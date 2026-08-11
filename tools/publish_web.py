#!/usr/bin/env python3
"""Stage built tapps into a bedtime_web checkout.

  tools/publish_web.py --web-root ../web out/tapeboy.build.json [more...]

Writes, for each entry:
  <web>/static/downloads/tapps/<slug>.tapp     the artifact CI built
  <web>/static/images/tape/tapps/<slug>.png    the emulator screenshot
  <web>/data/tapps.json                        the catalog the site renders

Nothing is committed or pushed here — publish.yml does that on a dev-tapps-<slug> branch and opens
a pull request, so a human sees the Cloudflare preview before it is live.

The binary is named for the tapp alone, with no version in the filename: the download URL stays
stable across updates and old builds do not pile up in the web repo. Which commit it came from
lives in the catalog entry instead.

Stdlib only, python3.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

DOWNLOADS = Path('static/downloads/tapps')
IMAGES = Path('static/images/tape/tapps')
CATALOG = Path('data/tapps.json')

# Only what the site renders. The build record also carries local paths and build settings, which
# have no business in a published catalog.
#
# `ref` is optional and is dropped below when empty, so the website's tapp card must not assume it.
# `commit` is always present — it is what was built, and it is what a reader should trust.
FIELDS = ('slug', 'name', 'author', 'description', 'category', 'tags',
          'license', 'repo', 'ref', 'commit', 'size', 'sha256')


def load_catalog(path):
    if not path.is_file():
        return {'tapps': []}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"error: {path} is not valid JSON ({e})")
    data.setdefault('tapps', [])
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--web-root', type=Path, required=True, help='bedtime_web checkout')
    ap.add_argument('records', nargs='+', type=Path, help='<slug>.build.json from build_entry.py')
    ap.add_argument('--date', help='publish date, YYYY-MM-DD (default: today, UTC)')
    args = ap.parse_args()

    web = args.web_root
    # Same guard as tape_manual's build_hugo.py: this script writes into a sibling repo, and a
    # mistyped --web-root would otherwise scatter files across whatever directory it named.
    if not (web / 'config.yml').is_file():
        sys.exit(f"error: {web} does not look like the bedtime_web repo")

    date = args.date or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    (web / DOWNLOADS).mkdir(parents=True, exist_ok=True)
    (web / IMAGES).mkdir(parents=True, exist_ok=True)

    catalog = load_catalog(web / CATALOG)
    by_slug = {t['slug']: t for t in catalog['tapps'] if 'slug' in t}

    for rec_path in args.records:
        rec = json.loads(rec_path.read_text())
        slug = rec['slug']
        built = rec_path.parent / f'{slug}.tapp'
        shot = rec_path.parent / f'{slug}.png'
        for f in (built, shot):
            if not f.is_file():
                sys.exit(f"error: {f} missing — run build_entry.py and screenshot.mjs first")

        shutil.copyfile(built, web / DOWNLOADS / f'{slug}.tapp')
        shutil.copyfile(shot, web / IMAGES / f'{slug}.png')

        entry = {k: rec[k] for k in FIELDS if rec.get(k) not in (None, '', [])}
        entry['tapp_url'] = f'/downloads/tapps/{slug}.tapp'
        entry['image'] = f'/images/tape/tapps/{slug}.png'
        # Preserve the date a tapp first appeared; only "updated" moves.
        entry['added'] = by_slug.get(slug, {}).get('added', date)
        entry['updated'] = date
        by_slug[slug] = entry
        print(f"staged {slug}  {rec['size']} bytes  {rec['repo']}@{rec['commit'][:8]}")

    catalog['tapps'] = sorted(by_slug.values(), key=lambda t: t['slug'])
    catalog['updated'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    (web / CATALOG).write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + '\n')
    print(f"\n{len(catalog['tapps'])} tapp(s) in {web / CATALOG}")
    return 0


if __name__ == '__main__':
    sys.exit(main())