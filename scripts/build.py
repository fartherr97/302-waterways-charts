#!/usr/bin/env python3
"""Build the offline chart packs: NOAA's current nautical chart for each water, as the app's map
draws it (the same service and layers, named in areas.json), in one MBTiles file per water.

  python3 scripts/build.py --areas areas.json --markers build/markers --out build/packs \
      [--shard 0/6] [--only rehoboth-bay-de]

Each pack: the tiles around the water from zoom 8 to 15 or 16 (areas.json says, from the app's
src/lib/offlineCharts.js), drawn at twice the pixels for a phone's screen. From zoom 12 on, only
tiles inside a tile with water in it are fetched, so the inland parts aren't. The water's channel
markers go in too (markers.py). Then <file>.json beside each pack: its size, checksum and the
date it was built, for catalog.py.

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
MARGIN_KM = 1.5          # the app's src/lib/offlineCharts.js PACK_MARGIN_KM
PRUNE_FROM_Z = 12        # the app's src/lib/offlineCharts.js PRUNE_FROM_Z
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


def tiles_for(a, z):
    w, s, e, n = a['bbox']
    x0, x1 = math.floor(lng2x(w, z)), math.floor(lng2x(e, z))
    y0, y1 = math.floor(lat2y(n, z)), math.floor(lat2y(s, z))
    r, k = a['km'] + MARGIN_KM, math.cos(math.radians(a['lat']))
    out = []
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            lng_a, lng_b, lat_n, lat_s = x2lng(x, z), x2lng(x + 1, z), y2lat(y, z), y2lat(y + 1, z)
            nx, ny = max(lng_a, min(a['lng'], lng_b)), max(lat_s, min(a['lat'], lat_n))
            if math.hypot((ny - a['lat']) * KM_PER_DEG, (nx - a['lng']) * KM_PER_DEG * k) <= r:
                out.append((x, y))
    return out


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


# What's on a tile: (anything drawn, any water). Water on NOAA's chart is white (deep) or one of
# its blues; land is tan, built-up areas grey. A little white is in the labels' halos on land too,
# so white counts only past a few percent.
def look(png):
    a = np.asarray(Image.open(io.BytesIO(png)).convert('RGBA')).astype(np.int16)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    seen = al > 0
    if not seen.any():
        return False, False
    n = a.shape[0] * a.shape[1]
    blue = ((b - r) >= 15) & seen
    white = (r >= 250) & (g >= 250) & (b >= 250) & seen
    return True, bool(blue.sum() / n > 0.003 or white.sum() / n > 0.04)


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
    watery, asked, stored, failed = set(), 0, 0, 0
    with ThreadPoolExecutor(workers) as pool:
        for z in range(a['minz'], a['maxz'] + 1):
            cand = tiles_for(a, z)
            if z >= PRUNE_FROM_Z:
                cand = [(x, y) for x, y in cand if (z - 1, x // 2, y // 2) in watery]
            asked += len(cand)
            got = pool.map(lambda t: (t, fetch(tile_url(chart, t[0], t[1], z))), cand)
            for (x, y), png in got:
                if png is None:
                    failed += 1
                    continue
                drawn, water = look(png)
                if water:
                    watery.add((z, x, y))
                if drawn:
                    db.execute('INSERT INTO tiles VALUES (?,?,?,?)', (z, x, 2 ** z - 1 - y, png))
                    stored += 1
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
        'tilesize': '512', 'water': a['id'], 'built': built, 'markers': markers,
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
    info = {**{k: a[k] for k in ('id', 'file', 'name', 'st', 'lat', 'lng', 'km', 'bbox', 'minz', 'maxz')},
            'bytes': os.path.getsize(final), 'sha256': h.hexdigest(), 'tiles': stored,
            'markers': len(json.loads(markers)), 'built': built}
    with open(final.replace('.mbtiles', '.json'), 'w') as f:
        json.dump(info, f)
    print(f"  ✓ {a['name']}: {stored} tiles ({asked} asked, {failed} missing), "
          f"{info['bytes'] / 1e6:.1f} MB, {info['markers']} markers, {time.time() - t0:.0f} s", flush=True)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--areas', required=True)
    ap.add_argument('--markers')
    ap.add_argument('--out', required=True)
    ap.add_argument('--shard', default='0/1')
    ap.add_argument('--only', help='file names (without .mbtiles), comma separated')
    ap.add_argument('--workers', type=int, default=8)
    o = ap.parse_args()
    spec = json.load(open(o.areas))
    shard, shards = map(int, o.shard.split('/'))
    areas = [a for i, a in enumerate(spec['areas']) if i % shards == shard]
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
