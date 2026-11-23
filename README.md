# 302 Waterways offline charts

The chart packs behind **Offline maps** in the 302 Waterways app (Plus): NOAA's current nautical
chart for the whole coast, Maine to Texas, and the Great Lakes, in regions, one
[MBTiles](https://github.com/mapbox/mbtiles-spec) file each, so the map still has the chart, its
depths and its channel markers with no signal.

- **The regions:** `regions.json`. Each is one zoom-10 map tile (about 30–40 km a side) with
  charted water and US shore in it, or one of the app's waters: the bays, sounds and rivers, the
  Intracoastal Waterway, the inlets, the Keys, the ocean near shore and the Great Lakes' US shores.
  Named after the app's waters in it, else its biggest town. The app makes the list
  (`scripts/offline-packs/regions.mjs` there, from NOAA's chart itself), and it's updated here when
  the app's waters change.
- **How:** `.github/workflows/charts.yml`, by hand: Actions → Offline charts → Run workflow,
  optionally for only some regions. (The monthly build on the 5th is paused for now.) Eight jobs fetch the chart from NOAA's
  Chart Display Service, the same service and layers the app's map draws live (`scripts/build.py`:
  512 px tiles, zoom 10 to 15; past zoom 14 only where there's water, and not the open sea's
  plain water, which the app enlarges from the tile above), and each region's buoys and beacons
  (`scripts/markers.py`).
- **Where:** each build is a release (`offline-charts-YYYYMMDD-HHMM`) holding the packs. The list
  the app reads, `offline-charts.json` (each pack's size, SHA-256, chart date and address), is the
  file of the `catalog` release, and is kept in this repository too. The release before the
  current one is kept, so a download that started on the old list still finishes.

Tests: `python3 -m unittest discover -s scripts -p 'test_*.py'`.

Chart data: NOAA Office of Coast Survey (public domain). Not for navigation: the app's charts
are an aid, not an official chart.
