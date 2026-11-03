# Bundled coastline underlay: provenance

`land-110m.geo.json` is the land-polygon dataset for the map's built-in coastline underlay: a fallback outline
drawn beneath the basemap tiles so the map never shows legs and places on an empty field when the tile servers
are unreachable. It is not the live basemap and is never presented as one.

## Source

- Dataset: Natural Earth, `land` polygons at 1:110,000,000 scale, via the `world-atlas` npm package 2.0.2
  (`land-110m.json`, TopoJSON, sha256 `ead5f68119c49a9250902e7da303bcb209341bbb8fefe7369a439b48b704658a`).
- Upstream: https://www.naturalearthdata.com. Natural Earth vector map data is in the public domain; it is
  credited here anyway.

## Transform

- The TopoJSON arcs are de-quantized and stitched into one `MultiPolygon` land feature (125 polygons).
- Coordinates are rounded to 3 decimal places; there is no further simplification.
- Internal country borders are not present: this is the dissolved landmass with coastlines, island outlines and
  lake holes only.
