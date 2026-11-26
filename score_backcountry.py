"""Score curated backcountry lines daily, gated by the official avalanche forecast.

    python score_backcountry.py    # config/backcountry/<zone>.geojson (+ <zone>_lines.json settings) -> data/backcountry.json

Zones without an avalanche center (avalanche_center_id null) are permanently "no avalanche forecast".

SAFETY RULE (non-negotiable, see CLAUDE.md): this never says "go ski this". The best a line can get is
"looks in condition, pending the avalanche forecast", and the official danger rating + link always ride
along with every line. With no forecast available: "no avalanche forecast — conditions score only".
Danger Considerable (3) or higher is a hard gate: the line is never presented as in condition.

Conditions score (0–100) per line and day:
  fresh snow 35   forecast to 9 am + observed (NOHRSC), aged
  coverage   20   season-to-date snowfall: a rough proxy for how filled-in the terrain is
  wind       20   ridge-top wind vs the line's aspect: loaded / cross-loaded / scoured / light
  surface    25   rain, thaw-then-refreeze crust, warmth
Hazard flags (wind slab, icy surface, wet snow, steep pitch) are listed separately and never add points.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import requests
import xarray as xr

from fetch_nws import (GRID_CACHE, HEADERS, KMH_PER_MPH, MM_PER_IN, ROOT, TZ, accumulate, c_to_f,
                       get_json, in_window, parse_interval, rain_liquid, resolve_grid)

BC_DIR = ROOT / "config" / "backcountry"
RECENT = ROOT / "data" / "recent_snow.json"
NOHRSC = ROOT / "data" / "nohrsc"
OUT = ROOT / "data" / "backcountry.json"
AVY_API = "https://api.avalanche.org/v2/public/products/map-layer"
DANGER = {1: "Low", 2: "Moderate", 3: "Considerable", 4: "High", 5: "Extreme"}
GATE_LEVEL = 3
OPEN_H = 9
LOADING_MPH = 15
M_TO_IN = 39.3701


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def angdiff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


_AVY_LAYER: list | None = None


def avy_layer() -> list:
    """avalanche.org map layer, fetched once per run (empty list if unavailable)."""
    global _AVY_LAYER
    if _AVY_LAYER is None:
        try:
            r = requests.get(AVY_API, headers=HEADERS, timeout=30)
            r.raise_for_status()
            _AVY_LAYER = r.json()["features"]
        except Exception as e:
            print(f"  avalanche.org unavailable: {e}", file=sys.stderr)
            _AVY_LAYER = []
    return _AVY_LAYER


def avalanche(center_id: str | None, fallback_url: str | None, no_center_note: str | None = None) -> dict:
    if not center_id:
        return {"available": False, "level": None, "danger": None, "off_season": None, "center": None,
                "link": None, "no_center": True,
                "reason": no_center_note or "no avalanche center forecasts this area"}
    feats = [f for f in avy_layer() if f["properties"].get("center_id") == center_id]
    if not feats:
        return {"available": False, "level": None, "danger": None, "off_season": None,
                "link": fallback_url, "reason": "avalanche.org did not return this zone"}
    p = feats[0]["properties"]
    level = p.get("danger_level")
    available = (not p.get("off_season")) and isinstance(level, int) and level >= 1
    return {
        "available": available,
        "level": level if available else None,
        "danger": DANGER.get(level) if available else None,
        "off_season": p.get("off_season"),
        "zone": p.get("name"), "center": p.get("center"),
        "link": (p.get("link") or fallback_url).strip(),
        "travel_advice": p.get("travel_advice"),
        "color": p.get("color"),
        "start_date": p.get("start_date"), "end_date": p.get("end_date"),
        "reason": None if available else ("off season" if p.get("off_season") else "no rating issued"),
    }


def value_at(layer: dict, t: datetime):
    for v in layer.get("values", []):
        s, e = parse_interval(v["validTime"])
        if s <= t < e:
            return v["value"]
    return None


def loading_wind(props: dict, ws: datetime, we: datetime) -> dict:
    """Speed-weighted mean direction of hours with wind >= LOADING_MPH in [ws, we)."""
    vx = vy = 0.0
    hours, speeds = 0, []
    t = ws.replace(minute=0, second=0, microsecond=0)
    while t < we:
        spd, d = value_at(props.get("windSpeed", {}), t), value_at(props.get("windDirection", {}), t)
        if spd is not None and d is not None and spd / KMH_PER_MPH >= LOADING_MPH:
            mph = spd / KMH_PER_MPH
            vx += mph * math.sin(math.radians(d))
            vy += mph * math.cos(math.radians(d))
            hours += 1
            speeds.append(mph)
        t += timedelta(hours=1)
    if not hours:
        return {"hours": 0, "from_deg": None, "mean_mph": None}
    return {"hours": hours, "from_deg": round((math.degrees(math.atan2(vx, vy)) + 360) % 360),
            "mean_mph": round(sum(speeds) / hours)}


def nohrsc_points(points: dict[str, tuple[float, float]]) -> dict:
    """Observed daily + season-to-date snowfall at named points, from files fetch_recent.py cached."""
    if not RECENT.exists():
        return {}
    meta = json.loads(RECENT.read_text())
    names = list(points)
    lats = xr.DataArray([points[n][0] for n in names], dims="pt")
    lons = xr.DataArray([points[n][1] for n in names], dims="pt")

    def sample(fname):
        p = NOHRSC / fname
        if not fname or not p.exists():
            return None
        with xr.open_dataset(p, engine="h5netcdf") as ds:
            v = ds["Data"].sel(lat=lats, lon=lons, method="nearest").values.astype(float)
        return np.where(v < 0, 0.0, v * M_TO_IN)

    daily = [(datetime.strptime(f.split("_")[-1][:10], "%Y%m%d%H").replace(tzinfo=timezone.utc), sample(f))
             for f in meta.get("daily_files", [])]
    season = sample(meta.get("season_file"))
    return {n: {"daily": [(end, float(v[i])) for end, v in daily if v is not None],
                "season_in": None if season is None else round(float(season[i]), 1)}
            for i, n in enumerate(names)}


def day_conditions(props: dict, wind_props: dict, obs: dict, open_at: datetime, now: datetime) -> dict:
    snow, temp = props.get("snowfallAmount", {}), props.get("temperature", {})
    pre48, night = max(now, open_at - timedelta(hours=48)), max(now, open_at - timedelta(hours=15))
    fcst = accumulate(snow, now, max(now, open_at)) / MM_PER_IN
    fcst24 = accumulate(snow, max(now, open_at - timedelta(hours=24)), open_at) / MM_PER_IN
    obs_in = sum(v * 0.5 ** (max(0.0, (open_at - end).total_seconds() / 86400) / 3)
                 for end, v in obs.get("daily", []))
    t48, tn = in_window(temp, pre48, open_at), in_window(temp, night, open_at)
    tday = in_window(temp, open_at, open_at + timedelta(hours=6))
    return {
        "fresh_in": round(fcst + obs_in, 1), "fresh_forecast_in": round(fcst, 1), "fresh_observed_in": round(obs_in, 1),
        "snow_last_24h_in": round(fcst24, 1),
        "season_in": obs.get("season_in"),
        "rain_in": round(rain_liquid(props, now, open_at + timedelta(hours=6)) / MM_PER_IN, 2),
        "temp_prev48_max_f": round(c_to_f(max(t48))) if t48 else None,
        "temp_overnight_min_f": round(c_to_f(min(tn))) if tn else None,
        "temp_day_max_f": round(c_to_f(max(tday))) if tday else None,
        "wind": loading_wind(wind_props, pre48, open_at),
    }


def score_line(p: dict, c: dict, avy: dict) -> dict:
    flags = []
    fresh = 35 * clamp01(c["fresh_in"] / 12)
    coverage = 20 * clamp01((c["season_in"] or 0) / 80)

    w = c["wind"]
    if c["fresh_in"] < 1 or not w["hours"]:
        effect, wind = ("light wind" if not w["hours"] else "no new snow to move"), 12
    else:
        d = angdiff(p["aspect_deg"], (w["from_deg"] + 180) % 360)
        effect = "loaded" if d <= 45 else "scoured" if d >= 135 else "cross-loaded"
        wind = {"loaded": 20, "cross-loaded": 14, "scoured": 4}[effect]
        if effect != "scoured" and c["fresh_in"] >= 2 and (w["mean_mph"] or 0) >= 20:
            flags.append(f"wind slab likely ({effect} by {w['mean_mph']} mph winds)")

    surf, surface_label = 25.0, "Cold, dry"
    if c["rain_in"] >= 0.05:
        surf, surface_label = 25 * (1 - clamp01(c["rain_in"] / 0.4)), "Rain-affected"
        flags.append("rain on snow: wet-snow instability")
    if (c["temp_prev48_max_f"] or 0) >= 34 and (c["temp_overnight_min_f"] or 99) <= 28 and c["snow_last_24h_in"] < 2:
        surf, surface_label = min(surf, 5), "Refrozen / icy"
        flags.append("firm, icy surface: long sliding falls")
    elif (c["temp_day_max_f"] or 0) >= 40:
        surf, surface_label = min(surf, 12), "Warm / wet"
        flags.append("warming: wet snow, and in spring falling ice and undermined snow")
    if p["slope_max_deg"] >= 45:
        flags.append(f"sustained pitch to {p['slope_max_deg']:.0f}°")

    score = round(fresh + coverage + wind + surf)
    # Avalanche gate. Wording is fixed by the safety rule; never "go ski this".
    if not avy["available"]:
        status, verdict = "no_forecast", "No avalanche forecast — conditions score only"
    elif avy["level"] >= GATE_LEVEL:
        status = "gated"
        verdict = f"Avalanche danger {avy['danger']} ({avy['level']}) — not shown as in condition"
    elif score >= 65:
        status, verdict = "pending_forecast", "Looks in condition, pending the avalanche forecast"
    else:
        status, verdict = "pending_forecast", "Not in condition by the numbers — read the avalanche forecast"
    return {
        "score": score, "status": status, "verdict": verdict,
        "conditions_label": "Looks in condition" if score >= 65 else "Marginal" if score >= 40 else "Not in condition",
        "parts": {"fresh": round(fresh, 1), "coverage": round(coverage, 1), "wind": wind, "surface": round(surf, 1)},
        "wind_effect": effect, "surface": surface_label, "flags": flags,
    }


def main() -> None:
    now = datetime.now(timezone.utc)
    local = now.astimezone(TZ)
    first = local.date() if local.hour < 10 else local.date() + timedelta(days=1)
    dates = [first, first + timedelta(days=1)]
    day_meta = [{"date": d.isoformat(), "label": d.strftime("%a %b ") + str(d.day)} for d in dates]

    cache = json.loads(GRID_CACHE.read_text()) if GRID_CACHE.exists() else {}
    def grid(lat, lon):
        return get_json(resolve_grid({"lat": lat, "lon": lon}, cache))["properties"]

    zones, feats = [], []
    for cfg_path in sorted(BC_DIR.glob("*_lines.json")):
        gj_path = cfg_path.with_name(cfg_path.name.replace("_lines.json", ".geojson"))
        if not gj_path.exists():
            print(f"  {gj_path.name} missing: run build_backcountry.py", file=sys.stderr)
            continue
        zone = json.loads(cfg_path.read_text())["zone"]  # live settings; the GeoJSON only supplies geometry + terrain
        gj = json.loads(gj_path.read_text())
        area_props = {a: grid(*pt) for a, pt in zone["area_points"].items()}
        wind_by_area = {a: grid(*zone.get("area_wind_points", {}).get(a, zone["wind_point"])) for a in zone["area_points"]}
        obs = nohrsc_points({a: tuple(pt) for a, pt in zone["area_points"].items()})
        avy = avalanche(zone.get("avalanche_center_id"), zone.get("avalanche_forecast_url"), zone.get("no_forecast_note"))

        conds = {}
        for d in dates:
            open_at = datetime(d.year, d.month, d.day, OPEN_H, tzinfo=TZ)
            for a in zone["area_points"]:
                conds[(a, d)] = day_conditions(area_props[a], wind_by_area[a], obs.get(a, {}), open_at, now)

        n0 = len(feats)
        for f in gj["features"]:
            p = {**f["properties"], "zone": zone["key"]}
            days = [{**meta, "conditions": conds[(p["area"], d)], **score_line(p, conds[(p["area"], d)], avy)}
                    for d, meta in zip(dates, day_meta)]
            feats.append({"type": "Feature", "geometry": f["geometry"], "properties": {**p, "days": days}})
        zones.append({"key": zone["key"], "name": zone["name"], "state": zone.get("state"),
                      "avalanche": avy, "lines": len(feats) - n0})
        print(f"{zone['name']}: {len(feats) - n0} lines · avalanche: "
              f"{avy['danger'] or 'no forecast'} ({avy.get('reason') or 'level ' + str(avy['level'])})", file=sys.stderr)
        for f in feats[n0:]:
            p, d0 = f["properties"], f["properties"]["days"][0]
            print(f"  {p['name']:<24} {p['aspect']:<2} {d0['score']:>3} {d0['conditions_label']:<16} "
                  f"{d0['wind_effect']:<20} {d0['surface']:<14} {d0['verdict']}", file=sys.stderr)
    GRID_CACHE.write_text(json.dumps(cache, indent=2))

    OUT.write_text(json.dumps({
        "type": "FeatureCollection",
        "generated_at": now.isoformat(),
        "zones": zones,
        "days": day_meta,
        "attribution": "Line geometry (c) OpenStreetMap contributors, ODbL. Elevation: USGS 3DEP.",
        "safety": "Never a recommendation to ski. Always read the official avalanche forecast first.",
        "features": feats,
    }, indent=1))
    print(f"wrote {OUT.relative_to(ROOT)} ({len(feats)} lines in {len(zones)} zones)", file=sys.stderr)


if __name__ == "__main__":
    main()
