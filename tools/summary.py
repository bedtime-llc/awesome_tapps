#!/usr/bin/env python3
"""Render a build record as the markdown CI puts in its job summary.

  tools/summary.py out/foo.build.json >> "$GITHUB_STEP_SUMMARY"

A script rather than an inline heredoc: a `<<'PY'` block inside an indented YAML `run:` needs its
terminator at column zero, which no longer lines up with the surrounding block — and the python
body would arrive indented anyway.

Stdlib only, python3.
"""

import json
import sys
from pathlib import Path


def render(rec):
    # `ref` is optional — a tag when the author named one, absent when the bot pinned a bare sha.
    # The commit is what was actually built, so it is always the thing shown.
    source = f"- source: {rec['repo']} @ `{rec['commit'][:8]}`"
    if rec.get('ref') and rec['ref'] != rec['commit']:
        source = f"- source: {rec['repo']} @ `{rec['ref']}` (`{rec['commit'][:8]}`)"
    out = [
        f"**{rec['name']}** by {rec['author']} — {rec['category']}",
        '',
        f"> {rec['description']}",
        '',
        source,
        f"- size: {rec['size']:,} bytes",
        f"- sha256: `{rec['sha256']}`",
    ]
    if rec.get('tags'):
        out.append(f"- tags: {', '.join(rec['tags'])}")
    if rec.get('license'):
        out.append(f"- license: {rec['license']}")
    return '\n'.join(out)


def main():
    if len(sys.argv) != 2:
        print('usage: summary.py <slug>.build.json', file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    print(f"### `{path.name.removesuffix('.build.json')}`")
    print()
    if not path.is_file():
        print('Build failed — see the log above for the gate that rejected it.')
        return 0
    print(render(json.loads(path.read_text())))
    print()
    print('The built `.tapp` and the screenshot are attached to this run as artifacts.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
