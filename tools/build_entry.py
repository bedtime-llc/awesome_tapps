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
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from entry import EntryError, load  # noqa: E402

SDK_REPO = 'https://github.com/bedtime-llc/tape_sdk'
SHA_RE = re.compile(r'^[0-9a-f]{40}$')

# The tapp's ROLE, as the firmware's tapp_app_type_t numbers it. This is the catalog's `category`:
# read off the artifact, never asked of the submitter, so a listing cannot disagree with the binary
# it links to.
APP_TYPE = {0: 'utility', 1: 'source', 2: 'standalone'}

MANIFEST_SECTION = b'.tapp_manifest'
MANIFEST_MAGIC = 0x50504154  # "TAPP"
MANIFEST_VERSION = 1
SHT_NOBITS = 8
SHDR_SIZE = 40  # sizeof(Elf32_Shdr)


class ManifestError(Exception):
    """The artifact compiled but its manifest will not read — a gate, not a crash.

    Its own type rather than EntryError: those are all `path:line: message` about a submitted
    file, and submit_entry.py strips that prefix off them. This is about a built binary.
    """


def _sections(blob):
    """Yield (name, sh_type, sh_offset, sh_size) for each section of an ELF32 LE image."""
    if len(blob) < 52 or blob[:4] != b'\x7fELF':
        raise ManifestError('not an ELF file')
    if blob[4] != 1 or blob[5] != 1:  # EI_CLASS=ELFCLASS32, EI_DATA=ELFDATA2LSB
        raise ManifestError('not a 32-bit little-endian ELF')
    e_shoff, = struct.unpack_from('<I', blob, 0x20)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', blob, 0x2e)
    if not e_shoff or not e_shnum or e_shentsize != SHDR_SIZE or e_shstrndx >= e_shnum:
        raise ManifestError('no usable section-header table')
    if e_shoff + e_shnum * e_shentsize > len(blob):
        raise ManifestError('section-header table runs past the end of the file')

    def hdr(i):  # sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size
        return struct.unpack_from('<IIIIII', blob, e_shoff + i * e_shentsize)

    _, _, _, _, str_off, str_size = hdr(e_shstrndx)
    strtab = blob[str_off:str_off + str_size]
    for i in range(e_shnum):
        sh_name, sh_type, _, _, sh_off, sh_size = hdr(i)
        if sh_name >= len(strtab):  # tapp_api.c guards its name lookup the same way
            continue
        end = strtab.find(b'\0', sh_name)
        yield strtab[sh_name:end if end >= 0 else None], sh_type, sh_off, sh_size


def app_role(blob):
    """The role declared in a built .tapp: 'utility', 'source' or 'standalone'.

    Located by SECTION NAME, the way both real readers do it — the device's tapp_read_manifest()
    and the emulator's loader each walk the section table for ".tapp_manifest". The de-facto file
    offset 0x42 holds only because tapp-build links the generated manifest object first, so the
    section lands right after the 52-byte ELF header: that is link order, not format. Scanning the
    bytes for the magic instead would also misfire on a tapp that happens to contain "TAPP" in its
    rodata ahead of the manifest.

    Layout from the section start: +0 magic, +4 manifest_version, +8 api_major, +10 api_minor,
    +12 hardware_target_id, +14 app_type.

    Raises rather than guessing. A manifest that will not read is one the firmware would refuse to
    load too (tapp.c rejects app_type > AppTypeStandalone), so publishing it as 'utility' would
    ship a download the hardware rejects — and a wrong badge, once published, is permanent.
    """
    for name, sh_type, off, size in _sections(blob):
        if name != MANIFEST_SECTION:
            continue
        if sh_type == SHT_NOBITS:
            raise ManifestError('.tapp_manifest has no bytes in the file (SHT_NOBITS)')
        if size < 16 or off + 16 > len(blob):
            raise ManifestError(f".tapp_manifest is {size} bytes, too short for a manifest header")
        magic, version = struct.unpack_from('<II', blob, off)
        if magic != MANIFEST_MAGIC:
            raise ManifestError(f".tapp_manifest magic is {magic:#010x}, expected "
                                f"{MANIFEST_MAGIC:#010x}")
        if version != MANIFEST_VERSION:
            raise ManifestError(f"manifest version {version}, this catalog reads {MANIFEST_VERSION}")
        ordinal, = struct.unpack_from('<H', blob, off + 14)
        if ordinal not in APP_TYPE:
            known = ', '.join(f"{k}={v}" for k, v in sorted(APP_TYPE.items()))
            raise ManifestError(
                f"manifest declares app_type {ordinal}; this catalog knows {known}. Built against "
                f"a newer SDK than the registry — the device would refuse it too")
        return APP_TYPE[ordinal]
    raise ManifestError('no .tapp_manifest section in the built artifact')


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
        try:
            category = app_role(blob)
        except ManifestError as e:
            # Same contract as the no-artifact branch above: a build that failed a gate leaves
            # nothing behind, or CI's upload-artifact step publishes a rejected .tapp.
            out_tapp.unlink(missing_ok=True)
            print(f"\nerror: {out_tapp.name}: {e}", file=sys.stderr)
            return 1

        record = dict(entry)
        record.update({
            'category': category,
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