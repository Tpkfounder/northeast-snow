"""Rank resorts for the coming weekend.

    python score.py                # reads data/*.json + config/scoring.json, writes data/weekend_rank.json

Inputs: storm_totals.json (NWS, incl. weekend lift-hour blocks), recent_snow.json (NOHRSC observed),
drive_times.json (Valhalla), storm_ranges.json (WPC percentiles, shown for context only).
Backcountry entries are never ranked here; that view has its own avalanche-gated logic.
The score knows nothing about whether a resort is open, its base depth or trail count.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = DATA / "weekend_rank.json"


def load(name: str, required: bool = True):
    p = DATA / name
    if not p.exists():
        if required:
            sys.exit(f"missing {p.relative_to(ROOT)}; run update.sh first")
        return None
    return json.loads(p.read_text())


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def observed_fresh(recent: dict | None, mid: str, at: datetime, half_life_d: float) -> float:
    if not recent or mid not in recent["mountains"]:
        return 0.0
    total = 0.0
    for d in recent["mountains"][mid]["daily"]:
        if not d["in"]:
            continue
        age_d = (at - datetime.fromisoformat(d["end"])).total_seconds() / 86400
        total += d["in"] * 0.5 ** (max(0.0, age_d) / half_life_d)
    return total


def score_day(day: dict, mid: str, drive_h: float | None, recent: dict | None, cfg: dict) -> dict:
    W, F, S, Wd, D = cfg["weights"], cfg["fresh"], cfg["surface"], cfg["wind"], cfg["drive"]
    assert W["fresh"] + W["surface"] == 100, "fresh + surface weights must add to 100"
    open_at = datetime.fromisoformat(day["date"] + "T09:00:00-05:00")
    notes = []

    # Fresh snow
    obs = observed_fresh(recent, mid, open_at, F["observed_half_life_days"])
    fcst = day["snow_to_open_in"] + F["during_weight"] * day["snow_during_in"]
    fresh_in = fcst + obs
    fresh = W["fresh"] * clamp01(fresh_in / F["full_credit_in"])
    if day["snow_to_open_in"] >= 1:
        notes.append(f'{day["snow_to_open_in"]:.0f}" new by first chair')
    if day["snow_during_in"] >= 1:
        notes.append(f'{day["snow_during_in"]:.0f}" more during the day')
    if obs >= 1:
        notes.append(f'{obs:.0f}" recent (observed, aged)')

    # Surface quality
    surf_frac, label = 1.0, "Packed / groomed"
    lift_max, lift_min = day["temp_lift_max_f"], day["temp_lift_min_f"]
    rain = day["rain_to_open_in"] + day["rain_during_in"]
    if rain >= 0.05:
        pen = S["rain_max_penalty_frac"] * clamp01(rain / S["rain_full_penalty_in"])
        if day["snow_last_24h_to_open_in"] >= S["rain_buried_by_snow_in"]:
            pen /= 2
            notes.append("rain, then buried by new snow")
        else:
            notes.append(f'{rain:.2f}" of rain')
        surf_frac -= pen
        label = "Rain-affected"
    if (day["temp_prev48_max_f"] is not None and day["temp_overnight_min_f"] is not None
            and day["temp_prev48_max_f"] >= S["refreeze_thaw_above_f"]
            and day["temp_overnight_min_f"] <= S["refreeze_overnight_below_f"]
            and day["snow_last_24h_to_open_in"] < S["refreeze_reset_snow_in"]):
        surf_frac -= S["refreeze_penalty_frac"]
        label = "Refrozen / firm" if label != "Rain-affected" else "Rain, then refrozen"
        notes.append(f'thaw to {day["temp_prev48_max_f"]}°F then {day["temp_overnight_min_f"]}°F overnight')
    if lift_max is not None and lift_max > S["soft_start_f"]:
        surf_frac -= S["soft_penalty_frac"] * clamp01((lift_max - S["soft_start_f"]) / (S["soft_full_f"] - S["soft_start_f"]))
        if label == "Packed / groomed" and lift_max >= 45:
            label = "Soft / slushy"
        notes.append(f"warm, {lift_max}°F")
    if lift_max is not None and lift_max <= S["frigid_below_f"]:
        surf_frac -= S["frigid_penalty_frac"]
        notes.append(f"frigid, high of {lift_max}°F")
    if (fresh_in >= S["powder_min_in"] and lift_max is not None and lift_max <= S["powder_max_temp_f"]
            and rain < 0.05):
        label = "Powder"
    surface = W["surface"] * clamp01(surf_frac)

    # Wind-hold risk (summit gusts during lift hours)
    g = day["gust_lift_max_mph"] if day["gust_lift_max_mph"] is not None else day["wind_lift_max_mph"]
    if g is None:
        wind_f, risk = 1.0, "unknown"
    else:
        wind_f = 1 - (1 - Wd["min_factor"]) * clamp01((g - Wd["calm_gust_mph"]) / (Wd["hold_gust_mph"] - Wd["calm_gust_mph"]))
        risk = "high" if g >= Wd["high_risk_mph"] else "moderate" if g >= Wd["moderate_risk_mph"] else "low"
        if risk != "low":
            notes.append(f"gusts to {g} mph, {risk} wind-hold risk")

    # Drive
    if drive_h is None:
        drive_f = D["min_factor"]
    else:
        drive_f = 1 - (1 - D["min_factor"]) * clamp01((drive_h - D["easy_h"]) / (D["max_h"] - D["easy_h"]))

    conditions = fresh + surface
    parts = {"fresh": round(fresh, 1), "surface": round(surface, 1), "conditions": round(conditions, 1),
             "wind_factor": round(wind_f, 2), "drive_factor": round(drive_f, 2)}
    return {
        "date": day["date"], "label": day["label"], "complete": day["complete"],
        "score": round(conditions * wind_f * drive_f), "parts": parts,
        "fresh_in": round(fresh_in, 1), "fresh_forecast_in": round(fcst, 1), "fresh_observed_in": round(obs, 1),
        "surface": label, "rain_in": round(rain, 2),
        "temp_lift_min_f": lift_min, "temp_lift_max_f": lift_max,
        "gust_mph": g, "wind_risk": risk, "notes": notes,
    }


def season_status(info: dict | None) -> dict:
    """Season snapshot from config/resort_info.json: opened / opens on a date / no date announced / not tracked."""
    if info is None:
        return {"status": "untracked"}
    if info.get("opened"):
        return {"status": "opened", "date": info["opened"]}
    if "opens" in info:
        return {"status": "scheduled", "date": info["opens"]} if info["opens"] else {"status": "no_date"}
    return {"status": "untracked"}


def apply_season(day: dict, season: dict) -> dict:
    """A day before an announced opening date can't be skied: score 0, say why."""
    if season["status"] == "scheduled" and day["date"] < season["date"]:
        opens = datetime.fromisoformat(season["date"]).strftime("%b %-d")
        day = {**day, "score": 0, "closed": True, "notes": [f"not open yet (opens {opens})"] + day["notes"]}
    return day


def main() -> None:
    cfg = json.loads((ROOT / "config" / "scoring.json").read_text())
    info_cfg = json.loads((ROOT / "config" / "resort_info.json").read_text())
    infos = info_cfg["resorts"]
    totals = load("storm_totals.json")
    recent = load("recent_snow.json", required=False)
    drives = load("drive_times.json", required=False)
    ranges = load("storm_ranges.json", required=False)
    rng = {r["id"]: r for r in (ranges or {}).get("mountains", [])}

    resorts = []
    for m in totals["mountains"]:
        if m.get("error") or m["pass"] == "Backcountry" or not m.get("weekend"):
            continue
        drv = (drives or {}).get("mountains", {}).get(m["id"], {})
        season = season_status(infos.get(m["id"]))
        days = [apply_season(score_day(d, m["id"], drv.get("hours"), recent, cfg), season) for d in m["weekend"]]
        best = max(days, key=lambda d: d["score"])
        resorts.append({
            "id": m["id"], "name": m["name"], "state": m["state"], "pass": m["pass"],
            "lat": m["lat"], "lon": m["lon"],
            "score": best["score"], "best_day": best["label"],
            "drive_h": drv.get("hours"), "drive_mi": drv.get("miles"),
            "recent_7d_in": (recent or {}).get("mountains", {}).get(m["id"], {}).get("last_7d_in"),
            "storm_range": {k: rng[m["id"]].get(k) for k in ("p10", "p50", "p90")} if m["id"] in rng else None,
            "season": season,
            "site": infos.get(m["id"], {}).get("site"), "report": infos.get(m["id"], {}).get("report"),
            "days": days,
        })
    # Resorts not open on either day always sort below the open ones, whatever the weather.
    resorts.sort(key=lambda r: (all(d.get("closed") for d in r["days"]), -r["score"], r["drive_h"] or 99))
    for i, r in enumerate(resorts, 1):
        r["rank"] = i

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weekend": [d["label"] for d in resorts[0]["days"]] if resorts else [],
        "home": totals.get("home"),
        "weights": cfg["weights"],
        "inputs": {
            "nws": totals["generated_at"],
            "recent": recent and recent["latest_end"],
            "drive": drives and drives["generated_at"],
            "ppp_cycle": ranges and ranges["cycle"],
        },
        "season_info": {"as_of": info_cfg["as_of"], "source": info_cfg["source"]},
        "caveats": [
            f"Opening dates are a snapshot as of {info_cfg['as_of']}, not live lift status. Base depth and open trails aren't checked; use each resort's report link.",
            "Drive times are free-flow; weekend ski traffic is often 20–40% slower.",
            "Observed snowfall is the NOHRSC analysis (≈4 km grid), not resort-reported totals.",
        ],
        "resorts": resorts,
    }, indent=2))
    print(f"wrote {OUT.relative_to(ROOT)} ({len(resorts)} resorts)", file=sys.stderr)
    for r in resorts:
        d = next(x for x in r["days"] if x["label"] == r["best_day"])
        print(f'  {r["rank"]:>2}. {r["name"]:<14} {r["score"]:>3}  {r["best_day"]:<10} {d["parts"]}  {d["surface"]}',
              file=sys.stderr)


if __name__ == "__main__":
    main()
