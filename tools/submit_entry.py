#!/usr/bin/env python3
"""Turn a submission issue into a catalog entry, and say plainly what happened.

  ISSUE_BODY="$(cat body.md)" ISSUE_NUMBER=12 ISSUE_USER=someone \\
    tools/submit_entry.py --out stage --report report.md --pr-body pr-body.md

  tools/submit_entry.py --resolve https://github.com/someone/app [v1.0.0]

Reads the issue from the environment rather than argv: the body is text a stranger wrote, and a
`${{ github.event.issue.body }}` interpolated into a workflow's `run:` is substituted before the
shell parses it, so `$(curl … | sh)` in a submission would execute. Environment variables are data.

What it does, in order: parse the form, canonicalise the repo URL, resolve the version to a commit,
pick the id, check it against the catalog, write tapps/<id>.json, validate it. Anything that goes
wrong is written to the report as a sentence a contributor can act on, not a stack trace.

Every git invocation gets a rebuilt URL, a refspec matched against entry.REF_LABEL_RE, an argv list
and no shell — `git ls-remote 'ext::sh -c id'` executes, and this is what keeps that unreachable.

Stdlib only, python3.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import entry  # noqa: E402
import issue_form  # noqa: E402

# Marks the bot's own comment so an edited submission updates it instead of stacking another one.
SENTINEL = '<!-- tapps-bot:submit -->'

# https only, and never the local or ext transports, whatever the URL manages to say.
GIT = ['git', '-c', 'credential.helper=',
       '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always']
LS_TIMEOUT = 60
FETCH_TIMEOUT = 300


class SubmitError(Exception):
    """Something the contributor can fix, phrased for them."""


def git_env():
    env = dict(os.environ)
    # Without this a private or misspelled repo blocks on a credential prompt until the job dies.
    env['GIT_TERMINAL_PROMPT'] = '0'
    return env


def ls_remote(repo, *patterns):
    """[(sha, refname)] for the refs matching `patterns`. Raises SubmitError if git cannot look."""
    try:
        proc = subprocess.run([*GIT, 'ls-remote', '--', repo, *patterns],
                              capture_output=True, text=True, env=git_env(), timeout=LS_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SubmitError(f"`{repo}` did not answer within {LS_TIMEOUT}s.")
    if proc.returncode != 0:
        raise SubmitError(f"I could not reach `{repo}` — is it public, and is the URL right?")
    refs = []
    for line in proc.stdout.splitlines():
        sha, _, name = line.partition('\t')
        if name.strip():
            refs.append((sha.strip(), name.strip()))
    return refs


def canonical_repo(url):
    """The rebuilt https URL, or SubmitError. Nothing else may be handed to git."""
    m = entry.REPO_RE.match((url or '').strip())
    if not m:
        raise SubmitError(
            f"`{url}` does not look like a GitHub repository. "
            "It should read https://github.com/owner/name")
    return f"https://github.com/{m.group(1)}/{m.group(2)}"


def commit_exists(repo, sha):
    """Confirm a bare sha is fetchable. ls-remote cannot see a commit that is not a ref tip."""
    with tempfile.TemporaryDirectory(prefix='tapp-resolve-') as d:
        for cmd in (['init', '-q'], ['remote', 'add', 'origin', repo]):
            subprocess.run([*GIT, *cmd], cwd=d, capture_output=True, env=git_env())
        try:
            proc = subprocess.run([*GIT, 'fetch', '-q', '--depth', '1', 'origin', sha],
                                  cwd=d, capture_output=True, text=True,
                                  env=git_env(), timeout=FETCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise SubmitError(f"`{repo}` did not answer within {FETCH_TIMEOUT}s.")
        return proc.returncode == 0


def known_tags(repo, limit=10):
    """A few tag names, for when the version given matches nothing."""
    try:
        refs = ls_remote(repo, 'refs/tags/*')
    except SubmitError:
        return []
    names = [n[len('refs/tags/'):] for _, n in refs if not n.endswith('^{}')]
    return names[-limit:]


def resolve(repo, version):
    """(ref, note) — what to write into the entry, and one line about how it got there.

    A tag is kept as the author typed it: it is what the site shows, and build_entry.py resolves it
    to a commit at build time, recording that commit in the build record. Anything else lands as a
    40-character sha, so a blank version field still produces a pinned entry.
    """
    version = (version or '').strip()

    if not version:
        head = ls_remote(repo, 'HEAD')
        if not head:
            raise SubmitError(f"`{repo}` has no commits yet.")
        return head[0][0], 'no version given, so this is the last commit on the default branch'

    # Guarded before it becomes a refspec pattern: `v*` would glob and report a false match.
    if not entry.REF_LABEL_RE.match(version):
        raise SubmitError(
            f"`{version}` is not a tag name or a commit sha. Give a release tag like `v1.2.0`, "
            "a 40-character sha, or leave the field blank for your last commit.")

    if entry.SHA_RE.match(version):
        if not commit_exists(repo, version):
            raise SubmitError(f"`{repo}` has no commit `{version[:8]}`.")
        return version, 'pinned to the commit you gave'

    if ls_remote(repo, f'refs/tags/{version}'):
        return version, f'tagged release `{version}`'

    branch = ls_remote(repo, f'refs/heads/{version}')
    if branch:
        return branch[0][0], (
            f'`{version}` is a branch, not a tag, so this is its last commit — a branch moves, and '
            'the entry has to name something that does not. Tag a release if you want a version '
            'number shown on the site.')

    tags = known_tags(repo)
    known = f" Tags I can see: {', '.join(f'`{t}`' for t in tags)}." if tags else \
            " That repo has no tags — leave the field blank to use your last commit."
    raise SubmitError(f"`{repo}` has no tag, branch or commit called `{version}`.{known}")


def choose_slug(given, name, catalog):
    """The entry's id: the Id field, else the name slugified. Must be usable as a filename."""
    slug = given or issue_form.slugify(name)
    if not slug:
        raise SubmitError(
            "I could not make an id out of that name. Fill in the **Id** field with something "
            "lowercase and dashed, like `drum-machine`.")
    if not entry.SLUG_RE.match(slug):
        raise SubmitError(
            f"`{slug}` cannot be an id. It has to be 2 to 32 characters of lowercase letters, "
            "digits and dashes, starting and ending with a letter or digit.")
    return slug, (catalog / f'{slug}.json')


def check_collision(path, slug, repo, author):
    """(verb, warning). An id already in the catalog is an update, unless it is someone else's."""
    if not path.is_file():
        return 'add', None
    try:
        existing, _ = entry.parse(path)
        current = canonical_repo(existing.get('repo', ''))
    except (entry.EntryError, SubmitError):
        return 'update', f'`{path}` is currently unreadable, and this submission replaces it.'

    if current != repo:
        raise SubmitError(
            f"The id `{slug}` already belongs to {existing.get('repo')}. "
            "Pick a different **Id** for yours.")

    # Not an error: maintainers change hands and co-maintainers exist. It goes in front of a human.
    owner = str(existing.get('author', '')).lstrip('@').lower()
    if author and owner and owner != author.lower():
        return 'update', (f'Submitted by @{author}, but the current entry credits `{owner}`. '
                          'Worth a look before merging.')
    return 'update', None


def check_confirmations(boxes):
    unchecked = [label for checked, label in boxes if not checked]
    if unchecked:
        raise SubmitError('Please tick every box under **Confirmations**: '
                          + '; '.join(unchecked) + '.')


def render_report(ok, slug=None, record=None, note=None, verb=None, problems=(), warnings=()):
    """The issue comment. One per submission, rewritten in place as the issue is edited."""
    out = [SENTINEL, '']
    if not ok:
        out += ['### This submission is not ready yet', '']
        out += [f'- {p}' for p in problems]
        out += ['', 'Edit the issue above and I will check it again — no need to open a new one.']
        return '\n'.join(out) + '\n'

    out += [f'### Ready: `{slug}`', '']
    out += [f'- source: {record["repo"]} @ `{record["ref"]}`',
            f'- resolved: {note}',
            f'- this is {"a new entry" if verb == "add" else "an update to an existing entry"}']
    out += [f'- ⚠ {w}' for w in warnings if w]
    out += ['', 'The entry it produces:', '', '```json',
            json.dumps(record, indent=2, ensure_ascii=False), '```', '',
            'A maintainer will start the build from here. CI compiles it with the current SDK, '
            'runs the device loader\'s checks, and screenshots it in the emulator.']
    return '\n'.join(out) + '\n'


def render_pr_body(slug, record, note, verb, issue, user, notes, warnings):
    """The pull request description. Written to a file and passed with --body-file, never a shell."""
    who = f'submitted by @{user} in #{issue}' if user else f'from #{issue}'
    out = [f'{"Adds" if verb == "add" else "Updates"} `{slug}`, {who}.', '']
    out += [f'- source: {record["repo"]} @ `{record["ref"]}`', f'- {note}']
    out += [f'- ⚠ {w}' for w in warnings if w]
    if notes:
        out += ['', '**From the submitter:**', '']
        # Quoted, so their markdown cannot restructure this description.
        out += [f'> {line}' for line in notes.splitlines()]
    out += ['', 'The build and the screenshot are attached to the checks below. '
            'Merging publishes it.', '', f'Closes #{issue}']
    return '\n'.join(out) + '\n'


def submit(body, user, issue, catalog, out_dir):
    """The whole pipeline. Returns (slug, record, note, verb, warnings, notes)."""
    raw, extras, warnings = issue_form.parse(body)
    if not raw.get('name'):
        raise SubmitError('The **Name** field is empty — I could not read the form at all. '
                          'If you edited the issue body by hand, the `###` headings have to stay.')
    check_confirmations(extras['confirmations'])

    # Canonicalised here rather than left to entry.validate(), which only normalises on the way out
    # to the catalog. The committed file is what a reviewer reads, so it should already be the
    # thing that gets published — no "Instrument" in the diff becoming "instrument" on the site.
    raw['repo'] = canonical_repo(raw.get('repo'))
    raw['author'] = raw.get('author', '').lstrip('@')
    raw['category'] = raw.get('category', '').lower()
    raw['tags'] = [t.lower() for t in raw.get('tags', [])]
    raw['ref'], note = resolve(raw['repo'], raw.get('ref'))

    slug, existing_path = choose_slug(extras['slug'], raw['name'], catalog)
    verb, warning = check_collision(existing_path, slug, raw['repo'], user)
    warnings = list(warnings) + ([warning] if warning else [])

    out_dir.mkdir(parents=True, exist_ok=True)
    written = out_dir / f'{slug}.json'
    entry.dump(raw, written)

    _, errors = entry.validate(written, *entry.parse(written))
    if errors:
        # Strip the `file:line:` prefix — the contributor never sees that file, only their fields.
        raise SubmitError('\n'.join(e.split(': ', 1)[-1] for e in errors))

    return slug, json.loads(written.read_text()), note, verb, warnings, extras['notes']


def set_output(**kv):
    """Hand results to the workflow through $GITHUB_OUTPUT, not stdout."""
    path = os.environ.get('GITHUB_OUTPUT')
    if not path:
        return
    with open(path, 'a', encoding='utf-8') as fh:
        for key, value in kv.items():
            fh.write(f'{key}={value}\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--catalog', type=Path, default=Path('tapps'),
                    help='existing entries, for id collisions (default: tapps)')
    ap.add_argument('--out', type=Path, help='where to write <id>.json (default: the catalog)')
    ap.add_argument('--report', type=Path, help='write the issue comment here')
    ap.add_argument('--pr-body', type=Path, help='write the pull request description here')
    ap.add_argument('--resolve', metavar='REPO', help='just resolve a version, and print it')
    ap.add_argument('version', nargs='?', help='with --resolve: the tag, sha or branch')
    args = ap.parse_args()

    if args.resolve:
        try:
            repo = canonical_repo(args.resolve)
            ref, note = resolve(repo, args.version)
        except SubmitError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"{repo}@{ref}\n{note}")
        return 0

    body = os.environ.get('ISSUE_BODY', '')
    user = os.environ.get('ISSUE_USER', '').lstrip('@')
    issue = os.environ.get('ISSUE_NUMBER', '')

    try:
        slug, record, note, verb, warnings, notes = submit(
            body, user, issue, args.catalog, args.out or args.catalog)
    except SubmitError as e:
        print(e, file=sys.stderr)
        if args.report:
            args.report.write_text(render_report(False, problems=str(e).split('\n')),
                                   encoding='utf-8')
        set_output(ok='false')
        return 1

    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"{verb} {slug}  {record['repo']}@{record.get('ref', '')}  ({note})")

    if args.report:
        args.report.write_text(
            render_report(True, slug, record, note, verb, warnings=warnings), encoding='utf-8')
    if args.pr_body:
        args.pr_body.write_text(
            render_pr_body(slug, record, note, verb, issue, user, notes, warnings),
            encoding='utf-8')
    set_output(ok='true', slug=slug, verb=verb)
    return 0


if __name__ == '__main__':
    sys.exit(main())
