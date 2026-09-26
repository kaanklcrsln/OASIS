"""
One-time script: filters data/raw/altinordu.geojson to Akyazı neighbourhood bbox
and writes dashboard/data/akyazi_buildings.geojson.

Bbox (lat_min, lat_max, lon_min, lon_max):
  40.975135 – 40.985795  /  37.900106 – 37.934134
"""
import json
import os

ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT  = os.path.join(ROOT, 'data', 'raw', 'altinordu.geojson')
OUTPUT = os.path.join(ROOT, 'dashboard', 'data', 'akyazi_buildings.geojson')

LAT_MIN, LAT_MAX = 40.975135, 40.985795
LON_MIN, LON_MAX = 37.900106, 37.934134


def centroid(geometry):
    """Return (lon, lat) centroid of first outer ring of a MultiPolygon."""
    ring = geometry['coordinates'][0][0]
    n = len(ring)
    lon = sum(c[0] for c in ring) / n
    lat = sum(c[1] for c in ring) / n
    return lon, lat


def in_bbox(lon, lat):
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


with open(INPUT, encoding='utf-8') as f:
    data = json.load(f)

total = len(data['features'])
filtered = [feat for feat in data['features'] if in_bbox(*centroid(feat['geometry']))]

out = {'type': 'FeatureCollection', 'features': filtered}
with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

print(f"Done: {len(filtered)} / {total} features → {OUTPUT}")
