# 302 Waterways offline charts

The chart packs behind **Offline maps** in the 302 Waterways app (Plus): NOAA's current nautical
chart for each bay, sound and tidal river the app covers, Maine to Texas, one
[MBTiles](https://github.com/mapbox/mbtiles-spec) file per water, so the map still has the chart,
its depths and its channel markers with no signal.

- **What's built:** `areas.json`, the waters and the box and zoom levels of each pack. It comes from
  the app (`scripts/offline-packs/areas.mjs` there) and is updated here when the app's waters change.
- **How:** `.github/workflows/charts.yml`, on the 5th of each month (or by hand: Actions → Offline
  charts → Run workflow, optionally for only some packs). Six jobs fetch the chart from NOAA's
  Chart Display Service, the same service and layers the app's map draws live
  (`scripts/build.py`: 512 px tiles, zoom 8 to 15 or 16, inland tiles left out from zoom 12), and
  each water's buoys and beacons (`scripts/markers.py`).
- **Where:** each build is a release (`offline-charts-YYYYMMDD-HHMM`) holding the packs. The list
  the app reads, `offline-charts.json` (each pack's size, SHA-256, chart date and address), is the
  file of the `catalog` release, and is kept in this repository too. The release before the
  current one is kept, so a download that started on the old list still finishes.

Tests: `python3 -m unittest discover -s scripts -p 'test_*.py'`.

Chart data: NOAA Office of Coast Survey (public domain). Not for navigation: the app's charts
are an aid, not an official chart.
