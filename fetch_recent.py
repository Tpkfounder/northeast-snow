"""Observed snowfall over the last 7 days from the NOHRSC National Snowfall Analysis (v2).

    python fetch_recent.py         # writes data/recent_snow.json

Source (verified 2026-11-05):
    https://www.nohrsc.noaa.gov/snowfall_v2/data/{YYYYMM}/sfav2_CONUS_24h_{YYYYMMDD}12.nc
One file per day, each the 24h ending 12Z that day. ~0.04° lat/lon grid (≈4 km), meters,
-99999 = no data. The analysis blends station reports, Stage IV radar/gauge QPE and HRRR,
so it's an estimate, not a resort's own measurement; ridge-top totals can run low.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import requests
import xarray as xr

from fetch_nws import HEADERS, ROOT

CONFIG = ROOT / "config" / "mountains.json"
CACHE = ROOT / "data" / "nohrsc"
OUT = ROOT / "data" / "recent_snow.json"
BASE = "https://www.nohrsc.noaa.gov/snowfall_v2/data"
DAYS = 7
M_TO_IN = 39.3701


def day_file(end: datetime):
    name = f"sfav2_CONUS_24h_{end:%Y%m%d}12.nc"
    path = CACHE / name
    if not path.exists():
        r = requests.get(f"{BASE}/{end:%Y%m}/{name}", headers=HEADERS, timeout=60)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        path.write_bytes(r.content)
    return path


def main() -> None:
    cfg = json.loads(CONFIG.read_text())
    CACHE.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    latest = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now < latest + timedelta(hours=2):  # today's 12Z analysis usually posts within ~2h
        latest -= timedelta(days=1)

    ends, paths = [], []
    probe = latest
    while len(paths) < DAYS and probe > latest - timedelta(days=DAYS + 2):
        p = day_file(probe)
        if p is not None:
            ends.append(probe)
            paths.append(p)
        probe -= timedelta(days=1)
    if not paths:
        raise RuntimeError("no NOHRSC 24h analyses found")

    # Season-to-date total (NOHRSC seasons start Sep 30 12Z). Rough proxy for how filled-in terrain is.
    season_start = datetime(latest.year if latest.month >= 10 else latest.year - 1, 9, 30, 12, tzinfo=timezone.utc)
    season_name = f"sfav2_CONUS_{season_start:%Y%m%d%H}_to_{ends[0]:%Y%m%d}12.nc"
    season_path = CACHE / season_name
    if not season_path.exists():
        r = requests.get(f"{BASE}/{ends[0]:%Y%m}/{season_name}", headers=HEADERS, timeout=60)
        if r.ok:
            season_path.write_bytes(r.content)
        else:
            season_path = None
            print(f"  season-to-date file unavailable ({r.status_code})", file=sys.stderr)

    keep = {p.name for p in paths} | ({season_path.name} if season_path else set())
    for old in CACHE.glob("*.nc"):
        if old.name not in keep:
            old.unlink()

    lats = xr.DataArray([m["lat"] for m in cfg["mountains"]], dims="pt")
    lons = xr.DataArray([m["lon"] for m in cfg["mountains"]], dims="pt")
    daily = []  # one array per day, newest first
    for p in paths:
        with xr.open_dataset(p, engine="h5netcdf") as ds:
            v = ds["Data"].sel(lat=lats, lon=lons, method="nearest").values.astype(float)
        daily.append(np.where(v < 0, np.nan, v * M_TO_IN))

    season = None
    if season_path:
        with xr.open_dataset(season_path, engine="h5netcdf") as ds:
            v = ds["Data"].sel(lat=lats, lon=lons, method="nearest").values.astype(float)
        season = np.where(v < 0, np.nan, v * M_TO_IN)

    rows = {}
    for i, m in enumerate(cfg["mountains"]):
        vals = [None if np.isnan(d[i]) else round(float(d[i]), 1) for d in daily]
        known = [v or 0.0 for v in vals]
        rows[m["id"]] = {
            "daily": [{"end": e.isoformat(), "in": v} for e, v in zip(ends, vals)],
            "last_72h_in": round(sum(known[:3]), 1),
            "last_7d_in": round(sum(known), 1),
            "season_in": None if season is None or np.isnan(season[i]) else round(float(season[i]), 1),
        }

    OUT.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "source": "NOHRSC National Snowfall Analysis v2 (24h, ending 12Z)",
        "latest_end": ends[0].isoformat(),
        "days": len(paths),
        "season_start": season_start.isoformat(),
        "season_file": season_path.name if season_path else None,
        "daily_files": [p.name for p in paths],
        "mountains": rows,
    }, indent=2))
    print(f"wrote {OUT.relative_to(ROOT)} ({len(paths)} days, latest ending {ends[0]:%Y-%m-%d %HZ})", file=sys.stderr)


if __name__ == "__main__":
    main()
