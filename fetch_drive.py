"""Drive times from home to each mountain's base, via the public Valhalla router (OSM data).

    python fetch_drive.py          # writes data/drive_times.json (skips if inputs unchanged)
    python fetch_drive.py --force

Times are free-flow (no live traffic). Weekend ski traffic on I-87 / I-91 / I-93 is often
20–40% worse, so treat these as best-case. Routes rarely change, so results are cached and
only recomputed when home or a base coordinate changes in config/mountains.json.
Valhalla public instance: https://valhalla1.openstreetmap.de (FOSSGIS); keep usage light.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone

import requests

from fetch_nws import HEADERS, ROOT

CONFIG = ROOT / "config" / "mountains.json"
OUT = ROOT / "data" / "drive_times.json"
VALHALLA = "https://valhalla1.openstreetmap.de/route"


def main(force: bool = False) -> None:
    cfg = json.loads(CONFIG.read_text())
    home = cfg["home"]
    targets = [(m["id"], *m["base"]) for m in cfg["mountains"]]
    key = hashlib.sha1(json.dumps([home, targets]).encode()).hexdigest()[:12]

    if not force and OUT.exists() and json.loads(OUT.read_text()).get("inputs_key") == key:
        print("drive times up to date (inputs unchanged)", file=sys.stderr)
        return

    # The public matrix endpoint caps trips at 150 km, so route each mountain individually.
    out = {}
    for mid, la, lo in targets:
        body = {
            "locations": [{"lat": home["lat"], "lon": home["lon"]}, {"lat": la, "lon": lo}],
            "costing": "auto", "units": "miles", "directions_type": "none",
        }
        try:
            r = requests.post(VALHALLA, json=body, headers=HEADERS, timeout=60)
            r.raise_for_status()
            s = r.json()["trip"]["summary"]
            out[mid] = {"hours": round(s["time"] / 3600, 2), "miles": round(s["length"])}
        except Exception as e:
            out[mid] = {"hours": None, "miles": None, "error": str(e)}
            print(f"  {mid}: {e}", file=sys.stderr)
        time.sleep(1)  # be polite to the free public instance

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Valhalla (valhalla1.openstreetmap.de), auto costing, free-flow",
        "home": home,
        "inputs_key": key,
        "mountains": out,
    }, indent=2))
    print(f"wrote {OUT.relative_to(ROOT)} ({sum(1 for v in out.values() if v['hours'])}/{len(out)} routed)",
          file=sys.stderr)


if __name__ == "__main__":
    main(force="--force" in sys.argv)
