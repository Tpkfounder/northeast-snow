"""Snow depth on the ground from NOAA's SNODAS model (NSIDC G02158, ~1 km grid, daily).

    python fetch_depth.py    # -> data/snow_depth.json

Samples modeled snow depth at each mountain's upper-mountain point and base, and at every backcountry
area point. SNODAS is a model (assimilating stations and satellites), not a measured base depth: resort
snowmaking and grooming aren't in it, so treat it as natural snowpack.
"""
from __future__ import annotations

import gzip
import io
import json
import sys
import tarfile
from datetime import datetime, timedelta, timezone

import numpy as np
import requests

from fetch_nws import HEADERS, ROOT

URL = "https://noaadata.apps.nsidc.org/NOAA/G02158/masked/{y}/{m:02d}_{mon}/SNODAS_{ymd}.tar"
CACHE = ROOT / "data" / "snodas"
OUT = ROOT / "data" / "snow_depth.json"
# Masked CONUS grid (from the product header): 30 arc-second cells, cell centers.
NCOLS, NROWS = 6935, 3351
X0, Y0, RES = -124.729166666662, 52.8708333333312, 0.008333333333333
MM_PER_IN = 25.4


def download() -> tuple[str, bytes]:
    """Newest available day's snow-depth grid (product 1036), as raw big-endian int16 bytes."""
    today = datetime.now(timezone.utc).date()
    for back in range(4):
        d = today - timedelta(days=back)
        url = URL.format(y=d.year, m=d.month, mon=d.strftime("%b"), ymd=d.strftime("%Y%m%d"))
        cached = CACHE / f"depth_{d:%Y%m%d}.dat.gz"
        if cached.exists():
            return d.isoformat(), gzip.decompress(cached.read_bytes())
        r = requests.get(url, headers=HEADERS, timeout=180)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        with tarfile.open(fileobj=io.BytesIO(r.content)) as tar:
            member = next(m for m in tar.getmembers() if "1036" in m.name and m.name.endswith(".dat.gz"))
            raw = tar.extractfile(member).read()
        CACHE.mkdir(parents=True, exist_ok=True)
        for old in CACHE.glob("depth_*.dat.gz"):
            old.unlink()
        cached.write_bytes(raw)
        return d.isoformat(), gzip.decompress(raw)
    raise RuntimeError("no SNODAS file in the last 4 days")


def sampler(raw: bytes):
    grid = np.frombuffer(raw, dtype=">i2").reshape(NROWS, NCOLS)

    def depth_in(lat: float, lon: float) -> float | None:
        c, r = round((lon - X0) / RES), round((Y0 - lat) / RES)
        if not (0 <= r < NROWS and 0 <= c < NCOLS):
            return None
        v = int(grid[r, c])
        return None if v < 0 else round(v / MM_PER_IN, 1)
    return depth_in


def main() -> None:
    day, raw = download()
    depth_in = sampler(raw)
    mountains = json.loads((ROOT / "config" / "mountains.json").read_text())["mountains"]
    out_m = {m["id"]: {"top_in": depth_in(m["lat"], m["lon"]), "base_in": depth_in(*m["base"])} for m in mountains}

    areas = {}
    for cfg in sorted((ROOT / "config" / "backcountry").glob("*_lines.json")):
        z = json.loads(cfg.read_text())["zone"]
        for a, (lat, lon) in z["area_points"].items():
            areas[f"{z['key']}/{a}"] = depth_in(lat, lon)

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": day,
        "source": "NOAA NOHRSC SNODAS via NSIDC (G02158), modeled snow depth",
        "mountains": out_m,
        "areas": areas,
    }, indent=1))
    have = sum(v["top_in"] is not None for v in out_m.values())
    print(f"wrote {OUT.relative_to(ROOT)} (SNODAS {day}; {have}/{len(out_m)} mountains)", file=sys.stderr)
    for mid, v in sorted(out_m.items(), key=lambda kv: -(kv[1]["top_in"] or 0))[:8]:
        print(f"  {mid:<16} top {v['top_in']}\"  base {v['base_in']}\"", file=sys.stderr)


if __name__ == "__main__":
    main()
