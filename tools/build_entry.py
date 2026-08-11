#!/usr/bin/env python3
"""Build one catalog entry: fetch the pinned source, compile it, verify it loads.

  tools/build_entry.py tapps/foo.json --out out/

Writes <out>/<slug>.tapp and <out>/<slug>.build.json (the entry plus the resolved commit, size and
sha256 — publish_web.py reads that rather than re-deriving anything).

The toolchain is the SDK's own tapp-build, so what CI produces is what `./tapp-build` produces
locally and what the in-browser builder produces: one compiler, one flag set, one artifact.

Stdlib only, python3.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from entry import EntryError, load  # noqa: E402

SDK_REPO = 'https://github.com/bedtime-llc/tape_sdk'
SHA_RE = re.compile(r'^[0-9a-f]{40}$')


def run(cmd, cwd=None, quiet=False):
    """Run a command, echoing it. Raises CalledProcessError on failure."""
    if not quiet:
        print(f"$ {' '.join(shlex.quote(str(c)) for c in cmd)}", flush=True)
    return subprocess.run([str(c) for c in cmd], cwd=cwd, check=True,
                          text=True, capture_output=quiet)


def clone_pinned(repo, ref, dest):
    """Shallow-fetch exactly `ref`. Returns the resolved 40-char commit sha.

    A tag can be fetched by name, a raw sha cannot (`--branch` only takes refs), so the sha case
    goes through init+fetch. Both stay shallow — some tapp repos carry large asset history.

    An empty `ref` takes the default branch. Submitted entries never reach that path — the bot
    resolves a blank version field to a sha before writing the file — but a hand-written entry may
    leave `ref` out, and the sha it resolved to is recorded in the build record either way.
    """
    dest.mkdir(parents=True, exist_ok=True)
    if not ref:
        run(['git', 'clone', '-q', '--depth', '1', repo, '.'], cwd=dest)
    elif SHA_RE.match(ref):
        run(['git', 'init', '-q'], cwd=dest)
        run(['git', 'remote', 'add', 'origin', repo], cwd=dest)
        run(['git', 'fetch', '-q', '--depth', '1', 'origin', ref], cwd=dest)
        run(['git', 'checkout', '-q', 'FETCH_HEAD'], cwd=dest)
    else:
        run(['git', 'clone', '-q', '--depth', '1', '--branch', ref, repo, '.'], cwd=dest)

    # Submodules a tapp needs for its own sources are fetched; a vendored `sdk/` is NOT, because
    # those are pinned to whatever SDK snapshot the author had and are routinely years stale — the
    # build must use the SDK this script resolved, or the ABI it compiles against is not the one on
    # the device.
    gitmodules = dest / '.gitmodules'
    if gitmodules.is_file():
        paths = re.findall(r'^\s*path\s*=\s*(.+?)\s*$', gitmodules.read_text(), re.M)
        wanted = [p for p in paths if p != 'sdk' and not p.endswith('/sdk')]
        for p in wanted:
            try:
                run(['git', 'submodule', 'update', '--init', '--depth', '1', '--', p], cwd=dest)
            except subprocess.CalledProcessError:
                print(f"warning: submodule {p} could not be fetched", file=sys.stderr)
        if len(paths) != len(wanted):
            print("note: skipped the vendored sdk/ submodule — building against the pinned SDK")

    sha = run(['git', 'rev-parse', 'HEAD'], cwd=dest, quiet=True).stdout.strip()
    return sha


def resolve_sdk(workdir, sdk_arg, sdk_ref):
    """A local SDK path if given, else a shallow clone of the public SDK."""
    if sdk_arg:
        sdk = Path(sdk_arg).resolve()
        if not (sdk / 'tapp-build').is_file():
            sys.exit(f"error: {sdk} has no tapp-build — not an SDK checkout")
        return sdk
    sdk = workdir / 'sdk'
    run(['git', 'clone', '-q', '--depth', '1', '--branch', sdk_ref, SDK_REPO, str(sdk)])
    return sdk


def build(entry, src_dir, sdk, out_tapp):
    """Invoke tapp-build. `build:` names sources explicitly when the default scan is not enough."""
    if entry['build']:
        extra = shlex.split(entry['build'])
        for a in extra:
            if a.startswith('-') or os.path.isabs(a) or '..' in Path(a).parts:
                sys.exit(f"error: build: may only name source files inside the repo, got {a!r}")
            if not (src_dir / a).is_file():
                sys.exit(f"error: build: names {a!r}, which is not in the repository")
        args = [str(src_dir / a) for a in extra]
    else:
        args = [str(src_dir)]
    run([str(sdk / 'tapp-build'), *args, str(out_tapp)])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('entry', type=Path)
    ap.add_argument('--out', type=Path, default=Path('out'))
    ap.add_argument('--sdk', help='local SDK checkout (default: clone the public one)')
    ap.add_argument('--sdk-ref', default='main', help='SDK ref to clone (default: main)')
    ap.add_argument('--keep', action='store_true', help='keep the source checkout for inspection')
    args = ap.parse_args()

    try:
        entry = load(args.entry)
    except EntryError as e:
        print(e, file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    out_tapp = args.out / f"{entry['slug']}.tapp"
    out_tapp.unlink(missing_ok=True)

    workdir = Path(tempfile.mkdtemp(prefix='tapp-build-'))
    try:
        sdk = resolve_sdk(workdir, args.sdk, args.sdk_ref)
        src = workdir / 'src'
        print(f"\n── fetching {entry['repo']} @ {entry['ref'] or 'HEAD'} ──", flush=True)
        sha = clone_pinned(entry['repo'], entry['ref'], src)

        print(f"\n── building {entry['slug']} ──", flush=True)
        build(entry, src, sdk, out_tapp)

        if not out_tapp.is_file():
            # tapp-build deletes its output when verification fails, so an exit 0 with no file
            # means the gates rejected it. Never report that as a success.
            print("error: tapp-build produced no artifact (verification failed)", file=sys.stderr)
            return 1

        blob = out_tapp.read_bytes()
        record = dict(entry)
        record.update({
            'commit': sha,
            'size': len(blob),
            'sha256': hashlib.sha256(blob).hexdigest(),
        })
        (args.out / f"{entry['slug']}.build.json").write_text(json.dumps(record, indent=2) + '\n')
        print(f"\n✓ {out_tapp} ({len(blob)} bytes, {entry['repo']}@{sha[:8]})")
        return 0
    except subprocess.CalledProcessError as e:
        print(f"\nerror: {' '.join(str(c) for c in e.cmd)} exited {e.returncode}", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"source kept at {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())