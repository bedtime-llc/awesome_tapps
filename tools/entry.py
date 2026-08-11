#!/usr/bin/env python3
"""Parse and validate a catalog entry.

An entry is a JSON object under tapps/, one file per tapp. Nobody types these by hand: a
contributor fills in the issue form and tools/submit_entry.py writes the file, so the format is
chosen for its writer and its readers rather than for a typist. Every consumer already speaks JSON
— <slug>.build.json here, data/tapps.json on the website — and json.JSONDecodeError reports a
line and a column, which is better than the hand-rolled `key: value` parser this replaces managed.

Hand-writing one is still supported and still diagnosed per key; see the README.

  tools/entry.py --check tapps/           validate (exit 1 on any error)
  tools/entry.py --json  tapps/foo.json   emit the parsed entry for the other tools

Stdlib only, so CI needs no pip install.
"""

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

CATEGORIES = ('instrument', 'effect', 'utility', 'game', 'toy')

REQUIRED = ('name', 'author', 'repo', 'category', 'description')
# `ref` is optional: leave it out and the build takes the repo's default branch. Submissions never
# do — submit_entry.py resolves a blank version field to the last commit's sha and writes that.
OPTIONAL = ('ref', 'tags', 'license', 'build', 'screenshot_frames')
KNOWN = REQUIRED + OPTIONAL
# The on-disk key order, so a bot write and a hand edit produce the same file and diffs stay small.
# Spelled out rather than derived from REQUIRED + OPTIONAL: `ref` reads as part of `repo`, and an
# entry is something a reviewer skims.
ORDER = ('name', 'author', 'repo', 'ref', 'category', 'description',
         'tags', 'license', 'build', 'screenshot_frames')

MAX_DESCRIPTION = 200
MAX_TAGS = 6
DEFAULT_SCREENSHOT_FRAMES = 90

SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$')
TAG_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')
REPO_RE = re.compile(r'^https://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$')
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
# Not a pinning rule — a sha, a tag and a branch name all match. It is a shape check, because this
# string is rendered on a public page and is interpolated into an `ls-remote` refspec pattern,
# where a `*` would silently glob.
REF_LABEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$')


class EntryError(Exception):
    pass


JSON_TYPE = {bool: 'a boolean', int: 'a number', float: 'a number', str: 'a string',
             list: 'an array', dict: 'an object', type(None): 'null'}


def _typename(v):
    """Name a value the way the file spells it, so the message matches what the author typed."""
    return JSON_TYPE.get(type(v), type(v).__name__)


def _key_lines(text, raw):
    """Best-effort line number per top-level key, so diagnostics stay editor-jumpable.

    Exact for the shape entries actually have: one flat object whose only container is `tags`, an
    array of strings with no keys of its own to collide with.
    """
    lines = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        m = re.match(r'\s*"([^"]+)"\s*:', line)
        if m and m.group(1) in raw and m.group(1) not in lines:
            lines[m.group(1)] = lineno
    return lines


def parse(path):
    """Read the JSON object. Returns (raw, lines). Raises EntryError with file:line:col."""
    text = Path(path).read_text(encoding='utf-8')
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise EntryError(f"{path}:{e.lineno}:{e.colno}: {e.msg}")
    if not isinstance(raw, dict):
        raise EntryError(f"{path}: expected a JSON object, got {_typename(raw)}")
    return raw, _key_lines(text, raw)


def dump(raw, path):
    """Write the canonical on-disk form: known keys in ORDER, empties omitted.

    Takes the raw dict rather than a validated entry on purpose — validate() materialises
    screenshot_frames, and baking that default into every file is noise that also freezes it.
    """
    body = {k: raw[k] for k in ORDER if raw.get(k) not in (None, '', [])}
    Path(path).write_text(json.dumps(body, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def _err(errors, path, lines, key, msg):
    where = f"{path}:{lines[key]}" if key in lines else str(path)
    errors.append(f"{where}: {msg}")


def validate(path, raw, lines=None):
    """Return (entry, errors). `entry` is usable only when errors is empty."""
    path = Path(path)
    lines = lines or {}
    errors = []
    entry = {}
    mistyped = set()

    def text(key):
        """A string field. JSON has real types, so `"name": 42` is an error, not a stringify."""
        v = raw.get(key)
        if v is None:
            return ''
        if not isinstance(v, str):
            _err(errors, path, lines, key, f"{key} must be a string, got {_typename(v)}")
            mistyped.add(key)
            return ''
        return v.strip()

    slug = path.stem
    if not SLUG_RE.match(slug):
        errors.append(
            f"{path}: filename must be lowercase letters, digits and dashes "
            f"(2-32 chars), e.g. tapps/my-app.json")
    entry['slug'] = slug

    if 'slug' in raw:
        _err(errors, path, lines, 'slug', "slug comes from the filename; remove it")

    for key in sorted(set(raw) - set(KNOWN) - {'slug'}):
        hint = difflib.get_close_matches(key, KNOWN, n=1, cutoff=0.6)
        suffix = f" (did you mean {hint[0]!r}?)" if hint else ''
        _err(errors, path, lines, key, f"unknown key {key!r}{suffix}")

    field = {k: text(k) for k in ('name', 'author', 'repo', 'ref', 'description',
                                  'license', 'build')}
    field['category'] = text('category').lower()

    for key in REQUIRED:
        # A mistyped key already has a better error than "missing" against its name.
        if not field[key] and key not in mistyped:
            errors.append(f"{path}: missing required key {key!r}")

    name = field['name']
    if name and len(name) > 48:
        _err(errors, path, lines, 'name', f"name is {len(name)} chars, max 48")
    entry['name'] = name

    author = field['author']
    if author and len(author) > 48:
        _err(errors, path, lines, 'author', f"author is {len(author)} chars, max 48")
    entry['author'] = author.lstrip('@')

    repo = field['repo']
    m = REPO_RE.match(repo) if repo else None
    if repo and not m:
        _err(errors, path, lines, 'repo',
             "repo must look like https://github.com/owner/name")
    entry['repo'] = f"https://github.com/{m.group(1)}/{m.group(2)}" if m else repo

    # No branch denylist: omitting `ref` builds the default branch, so rejecting a literal "main"
    # while allowing its equivalent would be theatre. Submissions arrive pinned to a sha anyway.
    ref = field['ref']
    if ref and not REF_LABEL_RE.match(ref):
        _err(errors, path, lines, 'ref',
             f"ref {ref!r} does not look like a tag (e.g. v1.2.0) or a 40-character commit sha")
    entry['ref'] = ref

    category = field['category']
    if category and category not in CATEGORIES:
        hint = difflib.get_close_matches(category, CATEGORIES, n=1, cutoff=0.5)
        suffix = f" (did you mean {hint[0]!r}?)" if hint else ''
        _err(errors, path, lines, 'category',
             f"category {category!r} is not one of {', '.join(CATEGORIES)}{suffix}")
    entry['category'] = category

    description = field['description']
    if len(description) > MAX_DESCRIPTION:
        _err(errors, path, lines, 'description',
             f"description is {len(description)} chars, max {MAX_DESCRIPTION}")
    entry['description'] = description

    tags = raw.get('tags', [])
    if isinstance(tags, str):
        tags = tags.split(',')  # leniency for a hand-written entry
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        _err(errors, path, lines, 'tags',
             'tags must be an array of strings, e.g. ["drums", "sequencer"]')
        tags = []
    tags = [t.strip().lower() for t in tags if t.strip()]
    if len(tags) > MAX_TAGS:
        _err(errors, path, lines, 'tags', f"{len(tags)} tags, max {MAX_TAGS}")
    for t in tags:
        if not TAG_RE.match(t):
            _err(errors, path, lines, 'tags',
                 f"tag {t!r} must be lowercase letters, digits and dashes")
    entry['tags'] = tags

    entry['license'] = field['license']
    entry['build'] = field['build']

    frames = raw.get('screenshot_frames')
    if isinstance(frames, str):
        frames = frames.strip()
    if frames in (None, ''):
        frames = DEFAULT_SCREENSHOT_FRAMES
    else:
        # isinstance(True, int) is True, so a JSON `true` would otherwise pass as 1 frame.
        n = frames if isinstance(frames, int) and not isinstance(frames, bool) else None
        if n is None and isinstance(frames, str) and frames.isdigit():
            n = int(frames)
        if n is None or not 1 <= n <= 600:
            _err(errors, path, lines, 'screenshot_frames',
                 f"screenshot_frames must be a number from 1 to 600, "
                 f"got {json.dumps(frames)}")
            n = DEFAULT_SCREENSHOT_FRAMES
        frames = n
    entry['screenshot_frames'] = frames

    return entry, errors


def load(path):
    """Parse + validate; raise EntryError listing every problem at once."""
    entry, errors = validate(path, *parse(path))
    if errors:
        raise EntryError('\n'.join(errors))
    return entry


def entry_files(paths):
    """Expand args to entry files: every .json in a directory, or the files named directly.

    Returns (files, scanned_a_dir) — an empty catalog is a normal state, but an explicit path that
    matched nothing is a mistake worth an error.
    """
    out, scanned = [], False
    for p in paths:
        p = Path(p)
        if p.is_dir():
            scanned = True
        for f in ([p] if p.is_file() else sorted(p.glob('*.json'))):
            if f.suffix == '.json':
                out.append(f)
    return out, scanned


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('paths', nargs='+', help='entry files, or a directory of them')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='validate only (default)')
    mode.add_argument('--json', action='store_true', help='print the parsed entry as JSON')
    args = ap.parse_args()

    files, scanned = entry_files(args.paths)
    if not files:
        if scanned and not args.json:
            print('no entries yet')
            return 0
        print(f"no entry files matched: {' '.join(str(p) for p in args.paths)}", file=sys.stderr)
        return 2

    failed = 0
    parsed = []
    for f in files:
        try:
            parsed.append(load(f))
        except EntryError as e:
            print(e, file=sys.stderr)
            failed += 1
        except OSError as e:
            print(f"{f}: {e.strerror}", file=sys.stderr)
            failed += 1

    if args.json:
        if failed:
            return 1
        print(json.dumps(parsed[0] if len(parsed) == 1 else parsed, indent=2))
        return 0

    for e in parsed:
        print(f"ok  {e['slug']:<24} {e['category']:<11} {e['repo']}@{e['ref'] or 'HEAD'}")
    if failed:
        print(f"\n{failed} of {len(files)} entries invalid", file=sys.stderr)
        return 1
    print(f"\n{len(files)} entr{'y' if len(files) == 1 else 'ies'} valid")
    return 0


if __name__ == '__main__':
    sys.exit(main())