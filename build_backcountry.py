"""Build the backcountry lines GeoJSON: OSM geometry + USGS 3DEP terrain stats. Run after editing the line list.

    python build_backcountry.py              # every config/backcountry/<zone>_lines.json -> <zone>.geojson
    python build_backcountry.py mansfield    # just one zone

A line is one OSM way ("osm_way") or several joined end to end ("osm_ways", listed top to bottom or any order).

Per line it derives:
  top/bottom elevation (ft), vertical drop, length
  mean slope angle (top-to-bottom) and max sustained slope (steepest 30 m stretch along the line)
  aspect: direction the slope faces, from the 3DEP gradient around points in the middle of the line
Lines are oriented top -> bottom. The output is a curated asset: eyeball it before trusting it.
"""
from __future__ import annotations

import json
import math
import sys
import time

import numpy as np
import requests

from fetch_nws import HEADERS, ROOT

BC_DIR = ROOT / "config" / "backcountry"
OVERPASS = "https://overpass-api.de/api/interpreter"
DEP = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"

STEP_M = 10          # resample spacing along the line
WINDOW_M = 30        # "sustained" slope window
GRAD_M = 10          # half-spacing for the aspect gradient
M_PER_DEG_LAT = 111_320
FT = 3.28084
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def m_per_deg_lon(lat: float) -> float:
    return M_PER_DEG_LAT * math.cos(math.radians(lat))


def osm_ways(ids: list[int]) -> dict[int, list[tuple[float, float]]]:
    q = f"[out:json][timeout:60];way(id:{','.join(map(str, ids))});out geom tags;"
    for attempt in range(6):  # the public Overpass server often returns 502/504 under load
        try:
            r = requests.post(OVERPASS, data={"data": q}, headers=HEADERS, timeout=90)
            r.raise_for_status()
            return {e["id"]: [(p["lon"], p["lat"]) for p in e["geometry"]] for e in r.json()["elements"]}
        except Exception as e:
            if attempt == 5:
                raise
            print(f"  Overpass retry: {e}", file=sys.stderr)
            time.sleep(10 * (attempt + 1))


def way_ids(line: dict) -> list[int]:
    return line.get("osm_ways") or [line["osm_way"]]


def join_ways(parts: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    """Chain ways end to end, flipping each so it starts where the previous one ended."""
    d2 = lambda a, b: (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
    out = list(parts[0])
    for nxt in parts[1:]:
        if len(out) == len(parts[0]) and min(d2(out[0], nxt[0]), d2(out[0], nxt[-1])) < min(d2(out[-1], nxt[0]), d2(out[-1], nxt[-1])):
            out.reverse()  # the first way was listed backwards
        if d2(out[-1], nxt[-1]) < d2(out[-1], nxt[0]):
            nxt = nxt[::-1]
        out += nxt[1:] if d2(out[-1], nxt[0]) < 1e-12 else nxt
    return out


def elevations(pts: list[tuple[float, float]]) -> list[float]:
    """3DEP elevations (m) for (lon, lat) points, batched."""
    out = []
    for i in range(0, len(pts), 150):
        chunk = pts[i:i + 150]
        geom = json.dumps({"points": [list(p) for p in chunk], "spatialReference": {"wkid": 4326}})
        for attempt in range(4):
            try:
                r = requests.get(DEP, params={"geometry": geom, "geometryType": "esriGeometryMultipoint",
                                              "returnFirstValueOnly": "true", "f": "json"},
                                 headers=HEADERS, timeout=120)
                r.raise_for_status()
                samples = sorted(r.json()["samples"], key=lambda s: s["locationId"])
                out += [float(s["value"]) for s in samples]
                break
            except Exception as e:
                if attempt == 3:
                    raise
                print(f"  3DEP retry: {e}", file=sys.stderr)
                time.sleep(3 * (attempt + 1))
    return out


def resample(coords: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], list[float]]:
    """Points every STEP_M along the polyline, with cumulative distance (m)."""
    lat0 = coords[0][1]
    kx, ky = m_per_deg_lon(lat0), M_PER_DEG_LAT
    xy = np.array([((lo - coords[0][0]) * kx, (la - lat0) * ky) for lo, la in coords])
    seg = np.hypot(*np.diff(xy, axis=0).T)
    cum = np.concatenate([[0], np.cumsum(seg)])
    d = np.arange(0, cum[-1], STEP_M).tolist() + [cum[-1]]
    x, y = np.interp(d, cum, xy[:, 0]), np.interp(d, cum, xy[:, 1])
    return [(coords[0][0] + xi / kx, lat0 + yi / ky) for xi, yi in zip(x, y)], d


def aspect_at(pts: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], callable]:
    """Points needed for a central-difference gradient around each pt, and a function to turn elevations into aspects."""
    probe = []
    for lo, la in pts:
        dlon, dlat = GRAD_M / m_per_deg_lon(la), GRAD_M / M_PER_DEG_LAT
        probe += [(lo + dlon, la), (lo - dlon, la), (lo, la + dlat), (lo, la - dlat)]

    def to_vec(z):
        vx = vy = 0.0
        for i in range(0, len(z), 4):
            dzdx = (z[i] - z[i + 1]) / (2 * GRAD_M)   # + east
            dzdy = (z[i + 2] - z[i + 3]) / (2 * GRAD_M)  # + north
            # The slope faces downhill: opposite the gradient. Weight by steepness.
            vx -= dzdx
            vy -= dzdy
        return (math.degrees(math.atan2(vx, vy)) + 360) % 360
    return probe, to_vec


def build_zone(src_path) -> None:
    src = json.loads(src_path.read_text())
    out_path = src_path.with_name(src_path.name.replace("_lines.json", ".geojson"))
    lines = src["lines"]
    geoms = osm_ways([i for l in lines for i in way_ids(l)])
    print(f"{src['zone']['name']}:", file=sys.stderr)

    features = []
    for l in lines:
        parts = [geoms.get(i) for i in way_ids(l)]
        if any(not g or len(g) < 2 for g in parts):
            print(f"  {l['name']}: OSM way(s) {way_ids(l)} missing, skipped", file=sys.stderr)
            continue
        coords = join_ways(parts)
        pts, dist = resample(coords)
        n = len(pts)
        mid = pts[n // 4: max(n // 4 + 1, 3 * n // 4)]
        probe, to_aspect = aspect_at(mid[:: max(1, len(mid) // 6)])
        z = elevations(pts + probe)
        zl, zp = z[:n], z[n:]
        if zl[0] < zl[-1]:  # orient top -> bottom
            coords, pts, zl = coords[::-1], pts[::-1], zl[::-1]
            dist = [dist[-1] - d for d in dist[::-1]]
        length = dist[-1]
        drop = zl[0] - zl[-1]
        win = max(1, WINDOW_M // STEP_M)
        sustained = max((math.degrees(math.atan2(zl[i] - zl[i + win], dist[i + win] - dist[i]))
                         for i in range(n - win)), default=0.0)
        aspect = to_aspect(zp)
        props = {
            "key": l["key"], "name": l["name"], "area": l["area"],
            "zone": src["zone"]["key"],
            "osm_way": way_ids(l)[0], "osm_ways": way_ids(l),
            "top_ft": round(zl[0] * FT), "bottom_ft": round(zl[-1] * FT),
            "vert_ft": round(drop * FT), "length_ft": round(length * FT),
            "slope_mean_deg": round(math.degrees(math.atan2(drop, length)), 1),
            "slope_max_deg": round(sustained, 1),
            "aspect_deg": round(aspect),
            "aspect": COMPASS[round(aspect / 45) % 8],
            "hazards": src["area_hazards"].get(l["area"]),
        }
        features.append({"type": "Feature", "properties": props,
                         "geometry": {"type": "LineString", "coordinates": [[round(lo, 6), round(la, 6)] for lo, la in coords]}})
        print(f"  {l['name']:<18} {props['top_ft']:>5}->{props['bottom_ft']:>5} ft  {props['vert_ft']:>4} vert  "
              f"mean {props['slope_mean_deg']:>4}°  max30m {props['slope_max_deg']:>4}°  aspect {props['aspect']:<2} ({props['aspect_deg']}°)",
              file=sys.stderr)

    out_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "zone": src["zone"],
        "attribution": "Line geometry (c) OpenStreetMap contributors, ODbL. Elevation: USGS 3DEP.",
        "features": features,
    }, indent=1))
    print(f"wrote {out_path.relative_to(ROOT)} ({len(features)} lines)", file=sys.stderr)


def main() -> None:
    only = set(sys.argv[1:])
    for src_path in sorted(BC_DIR.glob("*_lines.json")):
        if not only or src_path.name.replace("_lines.json", "") in only:
            build_zone(src_path)


if __name__ == "__main__":
    main()
