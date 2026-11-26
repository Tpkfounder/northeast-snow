"""Fetch the next-72h NWS gridpoint forecast for each tracked mountain.

    python fetch_nws.py            # writes data/storm_totals.json
    NWS_CONTACT=you@example.com python fetch_nws.py   # optional contact in User-Agent

Flow: /points/{lat},{lon} -> forecastGridData URL (cached in data/gridpoints.json)
      -> /gridpoints/{wfo}/{x},{y} raw layers -> 72h sums / extremes per mountain.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config" / "mountains.json"
DATA = ROOT / "data"
GRID_CACHE = DATA / "gridpoints.json"
OUT = DATA / "storm_totals.json"

API = "https://api.weather.gov"
HORIZON_H = 72
contact = os.environ.get("NWS_CONTACT", "github.com/tpk-labs")
HEADERS = {
    "User-Agent": f"TPK Labs Northeast Snow/0.1 ({contact})",
    "Accept": "application/geo+json",
}

MM_PER_IN = 25.4
KMH_PER_MPH = 1.609344


def get_json(url: str, tries: int = 4) -> dict:
    """GET with retries; NWS gridpoint endpoints throw intermittent 5xx."""
    for attempt in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500 and r.status_code != 429:
                r.raise_for_status()
        except requests.RequestException as e:
            if attempt == tries - 1:
                raise
            print(f"  retry {attempt + 1} after {e}", file=sys.stderr)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"giving up on {url}")


def resolve_grid(m: dict, cache: dict) -> str:
    key = f"{m['lat']:.4f},{m['lon']:.4f}"
    if key not in cache:
        props = get_json(f"{API}/points/{key}")["properties"]
        cache[key] = {
            "forecastGridData": props["forecastGridData"],
            "wfo": props["gridId"],
            "x": props["gridX"],
            "y": props["gridY"],
        }
        time.sleep(0.5)
    return cache[key]["forecastGridData"]


_DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?")


def parse_interval(valid_time: str) -> tuple[datetime, datetime]:
    """'2026-11-05T12:00:00+00:00/PT6H' -> (start, end)."""
    start_s, dur_s = valid_time.split("/")
    start = datetime.fromisoformat(start_s)
    d, h, mnt = (int(g) if g else 0 for g in _DUR.fullmatch(dur_s).groups())
    return start, start + timedelta(days=d, hours=h, minutes=mnt)


def overlap_frac(s: datetime, e: datetime, ws: datetime, we: datetime) -> float:
    span = (e - s).total_seconds()
    ov = (min(e, we) - max(s, ws)).total_seconds()
    return max(0.0, ov) / span if span > 0 else 0.0


def accumulate(layer: dict, ws: datetime, we: datetime) -> float:
    """Sum an accumulation layer (mm), prorating intervals that straddle the window."""
    total = 0.0
    for v in layer.get("values", []):
        if v["value"] is None:
            continue
        s, e = parse_interval(v["validTime"])
        total += v["value"] * overlap_frac(s, e, ws, we)
    return total


def in_window(layer: dict, ws: datetime, we: datetime) -> list[float]:
    out = []
    for v in layer.get("values", []):
        if v["value"] is None:
            continue
        s, e = parse_interval(v["validTime"])
        if e > ws and s < we:
            out.append(v["value"])
    return out


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def rain_liquid(props: dict, ws: datetime, we: datetime) -> float:
    """Estimated liquid that fell as rain (mm): QPF minus the snow's water at ~10:1, per QPF interval."""
    snow, total = props.get("snowfallAmount", {}), 0.0
    for v in props.get("quantitativePrecipitation", {}).get("values", []):
        if v["value"] is None:
            continue
        s, e = parse_interval(v["validTime"])
        f = overlap_frac(s, e, ws, we)
        if f:
            total += max(0.0, v["value"] - accumulate(snow, s, e) / 10) * f
    return total


TZ = ZoneInfo("America/New_York")
LIFT_OPEN_H, LIFT_CLOSE_H = 9, 16


def weekend_days(props: dict, now: datetime) -> list[dict]:
    """Conditions for the coming Saturday and Sunday (or the rest of this weekend), lift hours 9–4 ET."""
    local = now.astimezone(TZ)
    days_to_sat = (5 - local.weekday()) % 7
    sat = (local + timedelta(days=days_to_sat)).date()
    if local.weekday() == 6:  # Sunday: only today is left
        dates = [local.date()]
    else:
        dates = [sat, sat + timedelta(days=1)]
    temp, wind, gust = (props.get(k, {}) for k in ("temperature", "windSpeed", "windGust"))
    snow = props.get("snowfallAmount", {})
    last_valid = max((parse_interval(v["validTime"])[1] for v in snow.get("values", [])), default=now)

    out = []
    for d in dates:
        open_ = datetime(d.year, d.month, d.day, LIFT_OPEN_H, tzinfo=TZ)
        close = datetime(d.year, d.month, d.day, LIFT_CLOSE_H, tzinfo=TZ)
        pre = max(now, open_ - timedelta(hours=48))  # freeze/thaw lookback, never before now
        night = max(now, open_ - timedelta(hours=15))  # ~6 pm the evening before
        lt, ow = in_window(temp, open_, close), in_window(temp, night, open_)
        pt = in_window(temp, pre, open_)
        lw, lg = in_window(wind, open_, close), in_window(gust, open_, close)
        out.append({
            "date": d.isoformat(),
            "label": d.strftime("%a %b ") + str(d.day),
            "complete": last_valid >= close,
            "snow_to_open_in": round(accumulate(snow, now, max(now, open_)) / MM_PER_IN, 1),
            "snow_during_in": round(accumulate(snow, max(now, open_), close) / MM_PER_IN, 1),
            "snow_last_24h_to_open_in": round(accumulate(snow, max(now, open_ - timedelta(hours=24)), open_) / MM_PER_IN, 1),
            "rain_to_open_in": round(rain_liquid(props, now, max(now, open_)) / MM_PER_IN, 2),
            "rain_during_in": round(rain_liquid(props, max(now, open_), close) / MM_PER_IN, 2),
            "temp_lift_min_f": round(c_to_f(min(lt))) if lt else None,
            "temp_lift_max_f": round(c_to_f(max(lt))) if lt else None,
            "temp_overnight_min_f": round(c_to_f(min(ow))) if ow else None,
            "temp_prev48_max_f": round(c_to_f(max(pt))) if pt else None,
            "wind_lift_max_mph": round(max(lw) / KMH_PER_MPH) if lw else None,
            "gust_lift_max_mph": round(max(lg) / KMH_PER_MPH) if lg else None,
        })
    return out


def summarize(props: dict, now: datetime) -> dict:
    we = now + timedelta(hours=HORIZON_H)
    snow = props.get("snowfallAmount", {})
    qpf = props.get("quantitativePrecipitation", {})

    days = []
    for i in range(HORIZON_H // 24):
        ds, de = now + timedelta(hours=24 * i), now + timedelta(hours=24 * (i + 1))
        days.append({
            "start": ds.isoformat(),
            "snow_in": round(accumulate(snow, ds, de) / MM_PER_IN, 1),
            "qpf_in": round(accumulate(qpf, ds, de) / MM_PER_IN, 2),
        })

    temps = in_window(props.get("temperature", {}), now, we)
    winds = in_window(props.get("windSpeed", {}), now, we)
    gusts = in_window(props.get("windGust", {}), now, we)
    dirs = in_window(props.get("windDirection", {}), now, we)

    snow_in = accumulate(snow, now, we) / MM_PER_IN
    qpf_in = accumulate(qpf, now, we) / MM_PER_IN
    return {
        "snow_72h_in": round(snow_in, 1),
        "qpf_72h_in": round(qpf_in, 2),
        # Snow-to-liquid ratio: ~10 is typical, 15+ is fluffy, <8 is dense / mixed precip.
        "slr": round(snow_in / qpf_in, 1) if qpf_in >= 0.05 and snow_in >= 0.5 else None,
        "temp_min_f": round(c_to_f(min(temps))) if temps else None,
        "temp_max_f": round(c_to_f(max(temps))) if temps else None,
        "wind_max_mph": round(max(winds) / KMH_PER_MPH) if winds else None,
        "gust_max_mph": round(max(gusts) / KMH_PER_MPH) if gusts else None,
        "wind_dir_mode_deg": max(set(round(d / 45) * 45 % 360 for d in dirs),
                                 key=[round(d / 45) * 45 % 360 for d in dirs].count) if dirs else None,
        "by_day": days,
        "weekend": weekend_days(props, now),
        "grid_update_time": props.get("updateTime"),
        "grid_elevation_ft": round(props["elevation"]["value"] * 3.28084)
        if props.get("elevation", {}).get("value") is not None else None,
    }


def main() -> None:
    cfg = json.loads(CONFIG.read_text())
    DATA.mkdir(exist_ok=True)
    cache = json.loads(GRID_CACHE.read_text()) if GRID_CACHE.exists() else {}

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    results, grids = [], {}
    for m in cfg["mountains"]:
        print(f"{m['name']}…", file=sys.stderr)
        try:
            url = resolve_grid(m, cache)
            if url not in grids:  # nearby points can share a grid cell
                grids[url] = get_json(url)["properties"]
            row = {**m, **summarize(grids[url], now), "grid_url": url, "error": None}
        except Exception as e:  # keep going; one bad mountain shouldn't kill the run
            row = {**m, "error": str(e)}
            print(f"  FAILED: {e}", file=sys.stderr)
        results.append(row)

    GRID_CACHE.write_text(json.dumps(cache, indent=2))
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_start": now.isoformat(),
        "horizon_hours": HORIZON_H,
        "source": "NWS gridpoint forecast (api.weather.gov)",
        "home": cfg.get("home"),
        "mountains": results,
    }, indent=2))
    ok = sum(1 for r in results if not r["error"])
    print(f"wrote {OUT.relative_to(ROOT)} ({ok}/{len(results)} ok)", file=sys.stderr)


if __name__ == "__main__":
    main()
