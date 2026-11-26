"""Fetch WPC Probabilistic Precipitation Portal (PPP) 72h snowfall percentiles.

    python fetch_ppp.py            # writes data/storm_ranges.json

Source layout (verified 2026-11-05, see WPC docs/PPP_GRIB2_and_Geotiff_Information.pdf):
    https://ftp-wpc.ncep.noaa.gov/prob_precip_portal/co/ppp_co_72hr_{cycle}/
        ndfd_co_pw72ip0p{NN}_{cycle}f{fhour}.grib2   snowfall percentile NN (05..95)
        ndfd_co_w72i_{cycle}f{fhour}.grib2           NDFD "expected" (official forecast) snowfall
Each file is one CONUS 2.5 km Lambert grid, values in meters, 9999 = missing.
The 72h window ends at cycle + fhour; WPC usually starts it a few hours before the cycle.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes
import requests

from fetch_nws import HEADERS, ROOT  # shared User-Agent + project root

BASE = "https://ftp-wpc.ncep.noaa.gov/prob_precip_portal/co/"
CONFIG = ROOT / "config" / "mountains.json"
DATA = ROOT / "data"
GRIB_DIR = DATA / "grib"
OUT = DATA / "storm_ranges.json"

PERCENTILES = (10, 25, 50, 75, 90)
M_TO_IN = 39.3701
MISSING = 9000  # anything at/above this is the 9999 missing flag


def listing(url: str) -> list[str]:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return sorted(set(re.findall(r'href="([^"?/][^"]*)"', r.text)))


def newest_complete_cycle() -> tuple[str, dict[str, str]]:
    """Newest ppp_co_72hr_* directory that has every file we need. Returns (cycle, {key: url})."""
    dirs = sorted({m for m in re.findall(r"ppp_co_72hr_(\d{10})", " ".join(listing(BASE)))}, reverse=True)
    for cycle in dirs[:4]:
        d = f"{BASE}ppp_co_72hr_{cycle}/"
        names = listing(d)
        want = {f"p{p}": rf"ndfd_co_pw72ip0p{p:02d}_{cycle}f\d{{3}}\.grib2" for p in PERCENTILES}
        want["expected"] = rf"ndfd_co_w72i_{cycle}f\d{{3}}\.grib2"
        found = {k: next((n for n in names if re.fullmatch(pat, n)), None) for k, pat in want.items()}
        if all(found.values()):
            return cycle, {k: d + n for k, n in found.items()}
        print(f"  cycle {cycle} incomplete, trying older", file=sys.stderr)
    raise RuntimeError("no complete 72h PPP cycle found")


def download(cycle: str, urls: dict[str, str]) -> dict[str, Path]:
    target = GRIB_DIR / cycle
    target.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, url in urls.items():
        p = target / url.rsplit("/", 1)[1]
        if not p.exists():
            tmp = p.with_suffix(".part")
            with requests.get(url, headers=HEADERS, timeout=120, stream=True) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    shutil.copyfileobj(r.raw, f)
            tmp.rename(p)
        paths[key] = p
    for old in GRIB_DIR.iterdir():  # keep only the current cycle on disk
        if old.is_dir() and old.name != cycle:
            shutil.rmtree(old)
    return paths


def sample(path: Path, points: list[tuple[float, float]]) -> tuple[list[float | None], dict]:
    """Nearest-gridpoint values (inches) at each (lat, lon), plus the message's time window."""
    with open(path, "rb") as f:
        h = eccodes.codes_grib_new_from_file(f)
        try:
            ref = datetime.strptime(f"{eccodes.codes_get(h, 'dataDate')}{eccodes.codes_get(h, 'dataTime'):04d}",
                                    "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            start_h, end_h = (int(x) for x in re.fullmatch(r"(-?\d+)-(\d+)", eccodes.codes_get(h, "stepRange")).groups())
            window = {"start": (ref + timedelta(hours=start_h)).isoformat(),
                      "end": (ref + timedelta(hours=end_h)).isoformat()}
            vals = []
            for lat, lon in points:
                v = eccodes.codes_grib_find_nearest(h, lat, lon)[0]["value"]
                vals.append(None if v >= MISSING else round(v * M_TO_IN, 1))
        finally:
            eccodes.codes_release(h)
    return vals, window


def main() -> None:
    cfg = json.loads(CONFIG.read_text())
    mountains = cfg["mountains"]
    pts = [(m["lat"], m["lon"]) for m in mountains]

    cycle, urls = newest_complete_cycle()
    print(f"PPP cycle {cycle}", file=sys.stderr)
    paths = download(cycle, urls)

    cols, window = {}, None
    for key, p in paths.items():
        cols[key], window = sample(p, pts)

    rows = []
    for i, m in enumerate(mountains):
        pct = {f"p{p}": cols[f"p{p}"][i] for p in PERCENTILES}
        # Percentiles come from separate grids; enforce non-decreasing order in case of rounding noise.
        run = None
        for k in pct:
            if pct[k] is not None:
                run = pct[k] if run is None else max(run, pct[k])
                pct[k] = run
        rows.append({"id": m["id"], **pct, "expected": cols["expected"][i]})

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "WPC Probabilistic Precipitation Portal (NBM-based), 72h snowfall percentiles",
        "cycle": cycle,
        "window": window,
        "percentiles": list(PERCENTILES),
        "units": "in",
        "mountains": rows,
    }, indent=2))
    print(f"wrote {OUT.relative_to(ROOT)} ({len(rows)} mountains, window {window['start']} → {window['end']})",
          file=sys.stderr)


if __name__ == "__main__":
    main()
