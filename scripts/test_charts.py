"""Tests for the chart packs' scripts (python3 -m unittest discover -s scripts -p 'test_*.py')."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import markers  # noqa: E402

HERE = os.path.dirname(__file__)


def result(kind, lat, lng, **attrs):
    return {'geometry': {'x': lng, 'y': lat}, 'attributes': {'objectType': kind, **attrs}}


class Markers(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(markers.kind_from('3', ''), 'red')
        self.assertEqual(markers.kind_from('4', ''), 'green')
        self.assertEqual(markers.kind_from('3,4,3', ''), 'junction')
        self.assertEqual(markers.kind_from('', '2'), 'red')
        self.assertEqual(markers.kind_from('yellow', ''), 'junction')
        self.assertEqual(markers.kind_from('13', ''), 'green')  # (13 isn't red)

    def test_numbers(self):
        self.assertEqual(markers.num_of('Beach Cove Channel Daybeacon 4'), '4')
        self.assertEqual(markers.num_of('Lighted Buoy 12A'), '12A')
        self.assertEqual(markers.num_of('Junction'), 'Jun')

    def test_lights(self):
        L = markers.enc_light({'LITCHR': 'flashing', 'COLOUR': '3', 'SIGPER': '4', 'SIGGRP': '(2)', 'VALNMR': '3'})
        self.assertEqual(L, {'desc': 'Fl(2) R 4s', 'colour': 'R', 'period': 4, 'range': 3})
        self.assertEqual(markers.enc_light({'LITCHR': 'quick', 'COLOUR': 'green'})['desc'], 'Q G')
        self.assertIsNone(markers.enc_light({}))

    def test_answer(self):
        box = (-75.1, 38.58, -75.05, 38.63)
        j = {'results': [
            result('BOYLAT', 38.6, -75.07, COLOUR='3', OBJNAM='Indian River Inlet Buoy 2'),
            result('BOYLAT', 38.6, -75.07, COLOUR='3', OBJNAM='Indian River Inlet Buoy 2'),  # (twice)
            result('LIGHTS', 38.60005, -75.07, LITCHR='flashing', COLOUR='3', SIGPER='4'),
            result('BCNLAT', 38.61, -75.06, COLOUR='4', OBJNAM='Daybeacon 3'),
            result('MORFAC', 38.62, -75.06),                    # (a mooring: not a marker)
            {'geometry': None, 'attributes': {'objectType': 'BOYLAT'}},
        ]}
        out = markers.atons(j, box)
        self.assertEqual([m['id'] for m in out], ['enc-38.60000--75.07000', 'enc-38.61000--75.06000'])
        self.assertEqual(out[0]['kind'], 'red')
        self.assertEqual(out[0]['num'], '2')
        self.assertEqual(out[0]['light']['desc'], 'Fl R 4s')
        self.assertIsNone(out[1]['light'])

    def test_url(self):
        u = markers.identify_url(-75.1, 38.58, -75.05, 38.63)
        self.assertIn('/identify?geometry=', u)
        self.assertIn('layers=all:6', u)
        self.assertIn('mapExtent=-75.1,38.58,-75.05,38.63', u)


def png(pixels):
    import io
    from PIL import Image
    im = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
    im.putdata(pixels)
    b = io.BytesIO()
    im.save(b, 'PNG')
    return b.getvalue()


class Tiles(unittest.TestCase):
    def setUp(self):
        try:
            import build  # noqa: F401 (needs numpy and pillow)
        except ImportError:
            self.skipTest('numpy and pillow')
        self.build = build

    def test_look(self):
        sea, land = (255, 255, 255, 255), (244, 232, 193, 255)
        self.assertEqual(self.build.look(png([sea] * 256)), (True, True, True))            # open sea: plain
        self.assertEqual(self.build.look(png([land] * 256)), (True, False, True))          # inland
        self.assertEqual(self.build.look(png([(0, 0, 0, 0)] * 256)), (False, False, True))  # off the charts
        shore = self.build.look(png([sea] * 128 + [land] * 128))
        self.assertEqual(shore, (True, True, False))

    def test_url(self):
        u = self.build.tile_url({'export': 'https://x/export', 'layers': 'show:1,2'}, 292, 391, 10)
        self.assertIn('size=512,512&dpi=192', u)
        self.assertIn('layers=show:1,2', u)


class Catalog(unittest.TestCase):
    def run_catalog(self, d, tag, releases=None):
        args = [sys.executable, os.path.join(HERE, 'catalog.py'), '--packs', os.path.join(d, 'packs'), '--tag', tag,
                '--repo', 'o/r', '--out', os.path.join(d, 'offline-charts.json')]
        if releases is not None:
            with open(os.path.join(d, 'releases.txt'), 'w') as f:
                f.write('\n'.join(releases))
            args += ['--releases', os.path.join(d, 'releases.txt')]
        return subprocess.run(args, capture_output=True, text=True, check=True).stdout

    def pack(self, d, name):
        os.makedirs(os.path.join(d, 'packs'), exist_ok=True)
        with open(os.path.join(d, 'packs', f'{name}.mbtiles'), 'wb') as f:
            f.write(b'x')
        with open(os.path.join(d, 'packs', f'{name}.json'), 'w') as f:
            json.dump({'id': f'bay:{name}', 'file': f'{name}.mbtiles', 'name': name, 'built': '2026-12-05'}, f)

    def test_merges_and_says_unused(self):
        with tempfile.TemporaryDirectory() as d:
            self.pack(d, 'a')
            self.pack(d, 'b')
            self.run_catalog(d, 'offline-charts-1')
            # a build of only b: a keeps its old one
            os.remove(os.path.join(d, 'packs', 'a.json'))
            out = self.run_catalog(d, 'offline-charts-2', ['offline-charts-0', 'offline-charts-1', 'offline-charts-2', 'catalog'])
            with open(os.path.join(d, 'offline-charts.json')) as f:
                cat = json.load(f)
            urls = {p['id']: p['url'] for p in cat['packs']}
            self.assertEqual(urls['bay:a'], 'https://github.com/o/r/releases/download/offline-charts-1/a.mbtiles')
            self.assertEqual(urls['bay:b'], 'https://github.com/o/r/releases/download/offline-charts-2/b.mbtiles')
            self.assertIn('UNUSED offline-charts-0', out)
            self.assertNotIn('UNUSED offline-charts-1', out)
            self.assertNotIn('catalog', out.replace('offline-charts', ''))


if __name__ == '__main__':
    unittest.main()
