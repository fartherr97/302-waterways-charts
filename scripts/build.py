#!/usr/bin/env python3
"""Build the offline chart packs: NOAA's current nautical chart for each region, as the app's map
draws it (the same service and layers, named in regions.json), in one MBTiles file per region.

  python3 scripts/build.py --regions regions.json --markers build/markers --out build/packs \
      [--shard 0/8] [--only z10-292-391,z10-293-391]

A region is one zoom-10 map tile (about 30–40 km a side; the app's scripts/offline-packs/
regions.mjs chose them: every square along the coast and the Great Lakes with charted water).
Its pack holds the chart from zoom 10 to 16, drawn at twice the pixels for a phone's screen,
but not all of it: past zoom 14, only the tiles inside a tile with water in it (not the inland
ones), and none inside a tile with nothing drawn on it but water (the open sea: the map
enlarges the one above). The region's channel markers go in too (markers.py).
Then <file>.json beside each pack: its size, checksum and the date it was built, for catalog.py.

NOAA's chart is public domain (NOAA Office of Coast Survey). A tile that won't come after a few
tries is left out; a pack missing more than 1% of its tiles isn't written at all.
"""
import argparse
import hashlib
import io
import json
import math
import os
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
from PIL import Image

KM_PER_DEG = 111.2
PRUNE_FROM_Z = 15        # from here, only tiles inside a tile with water (NOAA draws small
                         # rivers only close in, so not before)
PLAIN_FROM_Z = 14        # past here, none inside a tile of plain water
R = 20037508.342789244
UA = '302Waterways-offline-charts/1.0 (+https://302waterways.com)'


# ── tiles (as the app's src/lib/offlineCharts.js) ──
def lng2x(lng, z): return (lng + 180) / 360 * 2 ** z
def lat2y(lat, z):
    r = math.radians(lat)
    return (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z
def x2lng(x, z): return x / 2 ** z * 360 - 180
def y2lat(y, z):
    n = math.pi - 2 * math.pi * y / 2 ** z
    return math.degrees(math.atan(0.5 * (math.exp(n) - math.exp(-n))))


def tile_url(chart, x, y, z):
    s = 2 * R / 2 ** z
    bbox = ','.join(f'{v:.2f}' for v in (-R + x * s, R - (y + 1) * s, -R + (x + 1) * s, R - y * s))
    return (f"{chart['export']}?bbox={bbox}&bboxSR=3857&imageSR=3857&size=512,512&dpi=192"
            f"&format=png8&transparent=true&layers={chart['layers']}&f=image")


def fetch(url, tries=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
                if r.status == 200 and b[:8] == b'\x89PNG\r\n\x1a\n':
                    return b
        except Exception:
            pass
        time.sleep(2 * (i + 1))
    return None


# What's on a tile: (anything drawn, any water, nothing but one colour). Water on NOAA's chart is
# white (deep) or one of its blues; land is tan, built-up areas grey. A little white is in the
# labels' halos on land too, so white counts only past a few percent.
def look(png):
    im = Image.open(io.BytesIO(png)).convert('RGBA')
    a = np.asarray(im).astype(np.int16)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    seen = al > 0
    if not seen.any():
        return False, False, True
    n = a.shape[0] * a.shape[1]
    blue = ((b - r) >= 15) & seen
    white = (r >= 250) & (g >= 250) & (b >= 250) & seen
    colours = im.getcolors(16)
    plain = bool(colours) and max(c for c, _ in colours) >= n * 0.998
    return True, bool(blue.sum() / n > 0.003 or white.sum() / n > 0.04), plain


def build(a, chart, markers_dir, out_dir, workers):
    t0 = time.time()
    part = os.path.join(out_dir, a['file'] + '.part')
    if os.path.exists(part):
        os.remove(part)
    db = sqlite3.connect(part)
    db.executescript("""
      CREATE TABLE metadata (name TEXT, value TEXT);
      CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);
      CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row);
      CREATE UNIQUE INDEX name ON metadata (name);
    """)
    # the region's tile, then each level's: the children of the tiles worth going into
    nxt, asked, stored, failed = [(a['x'], a['y'])], 0, 0, 0
    with ThreadPoolExecutor(workers) as pool:
        for z in range(a['minz'], a['maxz'] + 1):
            cand, nxt = nxt, []
            asked += len(cand)
            got = pool.map(lambda t: (t, fetch(tile_url(chart, t[0], t[1], z))), cand)
            for (x, y), png in got:
                if png is None:
                    failed += 1
                    continue
                drawn, water, plain = look(png)
                if drawn:
                    db.execute('INSERT INTO tiles VALUES (?,?,?,?)', (z, x, 2 ** z - 1 - y, png))
                    stored += 1
                go = z + 1 < PRUNE_FROM_Z or (drawn and water and not (plain and z >= PLAIN_FROM_Z))
                if go:
                    nxt += [(2 * x + dx, 2 * y + dy) for dy in (0, 1) for dx in (0, 1)]
            db.commit()
    if asked and failed / asked > 0.01:
        db.close()
        os.remove(part)
        print(f"  ✗ {a['name']}: {failed} of {asked} tiles wouldn't come; not written", flush=True)
        return None
    mk = os.path.join(markers_dir, a['file'].replace('.mbtiles', '.json')) if markers_dir else None
    markers = open(mk).read() if mk and os.path.exists(mk) else '[]'
    built = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    w, s, e, n = a['bbox']
    meta = {
        'name': a['name'], 'type': 'baselayer', 'format': 'png', 'version': '1',
        'description': f"NOAA nautical chart, {a['name']} ({', '.join(a['st'])}), built {built}",
        'attribution': 'NOAA Office of Coast Survey', 'bounds': f'{w},{s},{e},{n}',
        'center': f"{a['lng']},{a['lat']},{min(13, a['maxz'])}", 'minzoom': str(a['minz']), 'maxzoom': str(a['maxz']),
        'tilesize': '512', 'region': a['id'], 'built': built, 'markers': markers,
    }
    db.executemany('INSERT INTO metadata VALUES (?,?)', meta.items())
    db.commit()
    db.execute('VACUUM')
    db.close()
    final = os.path.join(out_dir, a['file'])
    os.replace(part, final)
    h = hashlib.sha256()
    with open(final, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    info = {**{k: a[k] for k in ('id', 'file', 'name', 'st', 'towns', 'waters', 'lat', 'lng', 'bbox', 'minz', 'maxz')},
            'bytes': os.path.getsize(final), 'sha256': h.hexdigest(), 'tiles': stored,
            'markers': len(json.loads(markers)), 'built': built}
    with open(final.replace('.mbtiles', '.json'), 'w') as f:
        json.dump(info, f)
    print(f"  ✓ {a['name']}: {stored} tiles ({asked} asked, {failed} missing), "
          f"{info['bytes'] / 1e6:.1f} MB, {info['markers']} markers, {time.time() - t0:.0f} s", flush=True)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--regions', required=True)
    ap.add_argument('--markers')
    ap.add_argument('--out', required=True)
    ap.add_argument('--shard', default='0/1')
    ap.add_argument('--only', help='region ids (file names without .mbtiles), comma separated')
    ap.add_argument('--workers', type=int, default=8)
    o = ap.parse_args()
    with open(o.regions) as f:
        spec = json.load(f)
    shard, shards = map(int, o.shard.split('/'))
    areas = [a for i, a in enumerate(spec['regions']) if i % shards == shard]
    if o.only:
        # (each of a shard's: the shards still share them out)
        want = {x.strip() for x in o.only.split(',') if x.strip()}
        areas = [a for a in areas if a['file'].replace('.mbtiles', '') in want]
    os.makedirs(o.out, exist_ok=True)
    print(f'{len(areas)} packs', flush=True)
    bad = 0
    for a in areas:
        if build(a, spec['chart'], o.markers, o.out, o.workers) is None:
            bad += 1
    if bad:
        print(f'{bad} pack(s) not built', file=sys.stderr)
        # (a few that NOAA wouldn't draw: the rest still go out; most of them: something's wrong)
        if bad > max(2, len(areas) // 4):
            sys.exit(1)


if __name__ == '__main__':
    main()
