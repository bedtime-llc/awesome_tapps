#!/usr/bin/env python3
"""Read a submission out of a rendered GitHub issue-form body.

GitHub does not expose issue-form answers as data. Not in the webhook payload, not in the REST API
— the only representation that exists anywhere is the markdown it renders into the issue body:

    ### Name

    Drum Machine

    ### Version (optional)

    _No response_

and that markdown carries the field's *label*, never its `id`. So this parser keys off label text,
which makes rewording a label in the form a breaking change. `--check-form` exists to make that
break loud: it asserts the form's labels and the LABELS table below still agree, and CI runs it.

Pure: no network, no filesystem except --check-form, nothing GitHub-specific beyond the format.
Stdlib only, python3.

  tools/issue_form.py --json                                  parse $ISSUE_BODY
  tools/issue_form.py --check-form .github/ISSUE_TEMPLATE/submit-a-tapp.yml
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

# Normalised label -> the key it carries. Everything the form asks for is here; a heading that is
# not in this table is treated as prose, not as a section boundary.
LABELS = {
    'name': 'name',
    'author': 'author',
    'repository': 'repo',
    'version': 'ref',
    'category': 'category',
    'description': 'description',
    'tags': 'tags',
    'license': 'license',
    'notes': 'notes',
    'id': 'slug',
    'build sources': 'build',
    'screenshot frames': 'screenshot_frames',
    'confirmations': 'confirmations',
}
KEYS = {v: k for k, v in LABELS.items()}

# Single-line entry keys, in the order they are asked for.
TEXT_KEYS = ('name', 'author', 'repo', 'ref', 'category', 'description', 'license', 'build')

HEADING_RE = re.compile(r'^#{1,6}\s+(.*?)\s*$')
CHECK_RE = re.compile(r'^\s*[-*]\s*\[([ xX])\]\s*(.*?)\s*$')
# A field's label sits at column 6 (body: -> `- type:` -> attributes: -> label:); a checkbox
# option's label sits at column 10. The indentation is the only thing telling them apart, which is
# why submit-a-tapp.yml says not to re-indent it.
FIELD_LABEL_RE = re.compile(r'^ {6}label:[ \t]*(.+?)[ \t]*$', re.M)
NO_RESPONSE = ('_no response_', '*no response*')
MAX_SLUG = 32


def normalize(label):
    """Fold a label to its lookup key: 'Version (optional)' -> 'version'."""
    s = re.sub(r'\([^)]*\)', ' ', label)
    s = re.sub(r'[^0-9a-z]+', ' ', s.lower())
    return s.strip()


def lines(body):
    """Split on real newlines only.

    Deliberately not str.splitlines(), which also breaks on U+2028, U+2029, \\x0b and \\x0c — a body
    could then smuggle a `### Category` into the middle of a line and move a section boundary.
    The webhook delivers CRLF, so both endings are normalised first.
    """
    return (body or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')


def blank(value):
    """Text, or '' — GitHub renders a field the author left empty as `_No response_`."""
    v = (value or '').strip()
    return '' if v.lower() in NO_RESPONSE else v


def one_line(value):
    """Collapse to a single line. A textarea keeps its newlines; nothing downstream wants them."""
    return ' '.join(blank(value).split())


def sections(body):
    """Split a rendered body into {normalised label: raw text}. Returns (found, duplicates).

    Two rules keep a contributor's own markdown from moving a section boundary:

      * a heading is a boundary only if this parser knows the label — `### Controls` is prose;
      * only the FIRST occurrence of a known label is a boundary — someone pasting `### Category`
        into their notes must not be able to truncate their own answer, or overwrite an earlier one.

    Anything before the first known heading is dropped, which is where GitHub puts nothing.
    """
    found, duplicates = {}, []
    current, buf = None, []

    def flush():
        if current is not None:
            found[current] = '\n'.join(buf).strip()

    for line in lines(body):
        m = HEADING_RE.match(line)
        key = normalize(m.group(1)) if m else None
        if key in LABELS:
            if key in found or key == current:
                duplicates.append(key)
                if current is not None:
                    buf.append(line)
                continue
            flush()
            current, buf = key, []
        elif current is not None:
            buf.append(line)
    flush()
    return found, duplicates


def checkboxes(text):
    """[(checked, label)] from a rendered task list. GitHub writes an uppercase X."""
    out = []
    for line in lines(text):
        m = CHECK_RE.match(line)
        if m:
            out.append((m.group(1).lower() == 'x', m.group(2)))
    return out


def slugify(text):
    """A URL id from free text: 'Drum Machine!' -> 'drum-machine'. May return ''."""
    s = unicodedata.normalize('NFKD', blank(text))
    s = s.encode('ascii', 'ignore').decode('ascii').lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:MAX_SLUG].strip('-')


def parse(body):
    """Rendered issue body -> (raw, extras, warnings).

    `raw` is shaped for entry.validate(): entry keys only, empties dropped. `extras` carries the
    answers that are not entry keys — the id, the notes, the confirmation boxes.
    """
    found, duplicates = sections(body)
    warnings = [f"a second {k!r} heading was read as text, not as a section" for k in sorted(set(duplicates))]

    def value(key):
        return one_line(found.get(KEYS[key], ''))

    raw = {k: value(k) for k in TEXT_KEYS}
    raw['tags'] = [t.strip() for t in value('tags').split(',') if t.strip()]

    # A digit string becomes a number so the entry file is canonical JSON; anything else is left
    # exactly as typed, so entry.validate() can quote it back at the author.
    frames = value('screenshot_frames')
    raw['screenshot_frames'] = int(frames) if frames.isdigit() else frames

    raw = {k: v for k, v in raw.items() if v not in ('', [])}

    extras = {
        'slug': value('slug'),
        'notes': blank(found.get('notes', '')),
        'confirmations': checkboxes(found.get('confirmations', '')),
    }
    return raw, extras, warnings


def check_form(path):
    """Assert the form's field labels and the LABELS table still agree. Returns a list of errors."""
    text = Path(path).read_text(encoding='utf-8')
    labelled = {normalize(m.strip('\'"')) for m in FIELD_LABEL_RE.findall(text)}
    errors = []
    for missing in sorted(set(LABELS) - labelled):
        errors.append(f"{path}: no field is labelled {missing!r}, but issue_form.LABELS reads one — "
                      f"a submission's {LABELS[missing]!r} would silently arrive empty")
    for extra in sorted(labelled - set(LABELS)):
        errors.append(f"{path}: the field labelled {extra!r} is not in issue_form.LABELS, "
                      f"so whatever a contributor types into it is discarded")
    return errors


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--json', action='store_true',
                      help='parse $ISSUE_BODY and print what was found')
    mode.add_argument('--check-form', metavar='YML',
                      help='verify the issue form matches this parser')
    args = ap.parse_args()

    if args.check_form:
        errors = check_form(args.check_form)
        for e in errors:
            print(e, file=sys.stderr)
        if errors:
            return 1
        print(f"ok  {args.check_form} matches issue_form.LABELS ({len(LABELS)} fields)")
        return 0

    raw, extras, warnings = parse(os.environ.get('ISSUE_BODY', ''))
    for w in warnings:
        print(w, file=sys.stderr)
    print(json.dumps({'raw': raw, **extras}, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
