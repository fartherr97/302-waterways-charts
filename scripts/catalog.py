#!/usr/bin/env python3
"""The list of offline chart packs the app offers (offline-charts.json): each pack built this time
(the <file>.json build.py wrote beside it) with where it was published, and any built before
that wasn't rebuilt this time (a build of only some packs keeps the rest).

  python3 scripts/catalog.py --packs packs --tag offline-charts-20261205 \
      --repo owner/name --out offline-charts.json [--releases releases.txt]

Also prints the releases the list no longer points at (for the workflow to delete).
Standard library only: it runs in the job that can push.
"""
import argparse
import glob
import json
import os
from datetime import datetime, timezone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--packs', required=True)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--repo', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--releases', help='file with the offline-charts-* release tags there are, one a line')
    o = ap.parse_args()

    old = {}
    if os.path.exists(o.out):
        try:
            old = {p['id']: p for p in json.load(open(o.out)).get('packs', [])}
        except (ValueError, KeyError):
            old = {}
    base = f'https://github.com/{o.repo}/releases/download/{o.tag}/'
    new = {}
    for f in sorted(glob.glob(os.path.join(o.packs, '*.json'))):
        info = json.load(open(f))
        if not os.path.exists(os.path.join(o.packs, info['file'])):
            continue
        new[info['id']] = {**info, 'url': base + info['file']}
    packs = {**old, **new}
    out = {
        'about': 'Offline chart packs for 302 Waterways: the NOAA nautical chart for each water, built '
                 f'monthly by https://github.com/{o.repo}. Chart data: NOAA Office of Coast Survey (public domain).',
        'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'packs': [packs[k] for k in sorted(packs)],
    }
    os.makedirs(os.path.dirname(o.out) or '.', exist_ok=True)
    with open(o.out, 'w') as fh:
        json.dump(out, fh, separators=(',', ':'))
        fh.write('\n')
    print(f'{len(new)} built now, {len(packs)} in the list')
    if o.releases and os.path.exists(o.releases):
        used = {p['url'].split('/releases/download/')[1].split('/')[0] for p in packs.values()}
        for tag in open(o.releases).read().split():
            if tag.startswith('offline-charts-') and tag not in used and tag != o.tag:
                print('UNUSED', tag)


if __name__ == '__main__':
    main()
