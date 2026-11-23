#!/usr/bin/env python3
"""The channel markers for each pack: NOAA's buoys, beacons and light floats in the water's box,
read as the app reads them live (302 Waterways src/data/atonSource.js, fetchNoaaAtons), so with
no signal every marker shows, not only the ones seen before. NOAA answers a box at a time (and
only so many in one), so each pack's is asked for in squares.

  python3 scripts/markers.py --areas areas.json --out build/markers [--shard 0/6] [--only a,b]

Writes <out>/<file without .mbtiles>.json: [{ id, kind, num, name, lat, lng, light }].
Standard library only.
"""
import argparse
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ENC_BASE = 'https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/ENCOnline/MapServer/exts/MaritimeChartService/MapServer'
UA = '302Waterways-offline-charts/1.0 (+https://302waterways.com)'
CELL = 0.05  # degrees

# Only real aids to navigation: buoys, beacons, light floats and light vessels
ATON_TYPES = re.compile(r'^(BOY(LAT|CAR|ISD|SAW|SPP)|BCN(LAT|CAR|ISD|SAW|SPP)|LITFLT|LITVES)$')
LIGHT_CHR = [('long', 'LFl'), ('isophase', 'Iso'), ('occult', 'Oc'), ('very quick', 'VQ'), ('quick', 'Q'),
             ('flash', 'Fl'), ('fixed', 'F'), ('morse', 'Mo'), ('alternating', 'Al')]


def code(n):
    return re.compile(r'(^|[^0-9])' + n + r'([^0-9]|$)')


RED, GREEN, YELLOW, WHITE = code('3'), code('4'), code('6'), code('1')


def text(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def field(p, keys):
    for k in keys:
        v = p.get(k)
        if v is not None and v != '' and v != 'null':
            return text(v)
    return ''


def num_of(name):
    m = re.search(r'\d+[A-Za-z]?', str(name))
    return (m.group(0) if m else str(name))[:3]


def kind_from(colour, cat):
    c = (colour or '').lower()
    red = bool(RED.search(c)) or bool(re.search(r'\bred\b', c)) or cat in ('2', 'starboard')
    green = bool(GREEN.search(c)) or bool(re.search(r'\bgreen\b', c)) or cat in ('1', 'port')
    if red and green:
        return 'junction'
    if red:
        return 'red'
    if green:
        return 'green'
    if re.search(r'yellow', c) or YELLOW.search(c):
        return 'junction'
    return 'green'


def number(s):
    try:
        v = float(s)
        return (int(v) if v.is_integer() else v) or None
    except (TypeError, ValueError):
        return None


def enc_light(p):
    t = field(p, ['LITCHR', 'litchr']).lower()
    chr_ = next((a for w, a in LIGHT_CHR if w in t), '')
    c = field(p, ['COLOUR', 'colour']).lower()
    colour = ('R' if re.search('red', c) or RED.search(c) else
              'G' if re.search('green', c) or GREEN.search(c) else
              'Y' if re.search('yellow', c) or YELLOW.search(c) else
              'W' if re.search('white', c) or WHITE.search(c) else None)
    per = number(field(p, ['SIGPER', 'sigper']))
    grp = re.sub(r'[()]', '', field(p, ['SIGGRP', 'siggrp'])).strip()
    desc = ' '.join(x for x in [chr_ + (f'({grp})' if chr_ and grp and grp != '1' else ''), colour or '',
                                f'{per}s' if per else ''] if x)
    return {'desc': desc, 'colour': colour, 'period': per, 'range': number(field(p, ['VALNMR', 'valnmr']))} if desc else None


def identify_url(w, s, e, n, group=6, tol=4):
    geom = json.dumps({'xmin': w, 'ymin': s, 'xmax': e, 'ymax': n, 'spatialReference': {'wkid': 4326}}, separators=(',', ':'))
    return (f'{ENC_BASE}/identify?geometry={urllib.parse.quote(geom)}&geometryType=esriGeometryEnvelope&sr=4326'
            f'&layers=all:{group}&tolerance={tol}&mapExtent={w},{s},{e},{n}&imageDisplay=1024,1024,96&returnGeometry=true&f=json')


def atons(j, box):
    """NOAA's identify answer → markers (as fetchNoaaAtons)."""
    w, s, e, n = box
    seen, out, lights = set(), [], []
    for r in j.get('results') or []:
        g = r.get('geometry') or {}
        lat, lng = g.get('y'), g.get('x')
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        p = r.get('attributes') or {}
        t = str(p.get('objectType') or '').upper()
        if t == 'LIGHTS':
            L = enc_light(p)
            if L:
                lights.append((lat, lng, L))
            continue
        if not ATON_TYPES.match(t):
            continue
        mid = f'enc-{lat:.5f}-{lng:.5f}'
        if mid in seen:
            continue
        seen.add(mid)
        out.append({'id': mid, 'kind': kind_from(field(p, ['COLOUR', 'colour', 'COLOR']), field(p, ['CATLAM', 'catlam'])),
                    'num': num_of(field(p, ['OBJNAM', 'objnam', 'OBJNAM_EN', 'NOBJNM', 'name'])),
                    'name': field(p, ['OBJNAM', 'objnam', 'name']), 'lat': lat, 'lng': lng, 'light': None})
    # the nearest light within ~40 m
    k = math.cos(math.radians((s + n) / 2))
    for m in out:
        best, best_d = None, 40
        for lat, lng, L in lights:
            d = math.hypot((lat - m['lat']) * 111132, (lng - m['lng']) * 111320 * k)
            if d < best_d:
                best_d, best = d, L
        if best:
            m['light'] = best
    return out[:600]


def cell_markers(box):
    for i in range(4):
        try:
            req = urllib.request.Request(identify_url(*box), headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return atons(json.load(r), box)
        except Exception:  # noqa: BLE001 (any trouble: try again, then go without)
            time.sleep(1.5 * (i + 1))
    print('  no answer for', ','.join(f'{v:.3f}' for v in box), flush=True)
    return []


def area_markers(a, pool):
    w, s, e, n = a['bbox']
    cells, x = [], w
    while x < e:
        y = s
        while y < n:
            cells.append((x, y, min(e, x + CELL), min(n, y + CELL)))
            y += CELL
        x += CELL
    by_id = {}
    for found in pool.map(cell_markers, cells):
        for m in found:
            if w <= m['lng'] <= e and s <= m['lat'] <= n:
                by_id[m['id']] = m
    return list(by_id.values()), len(cells)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--areas', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--shard', default='0/1')
    ap.add_argument('--only', default='')
    o = ap.parse_args()
    i, k = map(int, o.shard.split('/'))
    only = {x.strip() for x in o.only.split(',') if x.strip()}
    areas = [a for j, a in enumerate(json.load(open(o.areas))['areas']) if j % k == i]
    if only:
        areas = [a for a in areas if a['file'][:-len('.mbtiles')] in only]
    os.makedirs(o.out, exist_ok=True)
    with ThreadPoolExecutor(4) as pool:
        for a in areas:
            found, cells = area_markers(a, pool)
            with open(os.path.join(o.out, a['file'][:-len('.mbtiles')] + '.json'), 'w') as fh:
                json.dump(found, fh, separators=(',', ':'))
            print(f"{a['name']}: {len(found)} markers ({cells} squares)", flush=True)


if __name__ == '__main__':
    main()
