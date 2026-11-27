"""Powder alerts: an RSS feed (and JSON for the page) of mountains forecast to get a real dump.

    python make_alerts.py    # data/storm_totals.json + storm_ranges.json + weekend_rank.json -> data/alerts.{xml,json}

An alert fires when the likely 72 h snowfall (WPC 50th percentile, or the NWS total when there's no range)
reaches ALERT_IN. Each alert's id is stable for the mountain and the day, so a feed reader shows it once
per storm day instead of every hourly refresh. Backcountry alerts always carry the avalanche caveat:
a snow forecast is never a recommendation to ski avalanche terrain.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from fetch_nws import ROOT, TZ

DATA = ROOT / "data"
ALERT_IN = 6.0
SITE = os.environ.get("SITE_URL", "https://tpkfounder.github.io/northeast-snow/")


def load(name):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else None


def fmt(v: float) -> str:
    return f'{v:.0f}"'


def main() -> None:
    totals = load("storm_totals.json")
    ranges = {m["id"]: m for m in (load("storm_ranges.json") or {}).get("mountains", [])}
    wk = {r["id"]: r for r in (load("weekend_rank.json") or {}).get("resorts", [])}
    now = datetime.now(timezone.utc)
    day = now.astimezone(TZ).date().isoformat()

    alerts = []
    for m in totals["mountains"]:
        if m.get("error"):
            continue
        rng = ranges.get(m["id"])
        likely = rng["p50"] if rng and rng.get("p50") is not None else m["snow_72h_in"]
        if likely is None or likely < ALERT_IN:
            continue
        # Peak 6 h block, to say when it falls.
        peak = max(m.get("timeline") or [{"snow_in": 0}], key=lambda b: b["snow_in"])
        when = (datetime.fromisoformat(peak["start"]).astimezone(TZ).strftime("%a %-I %p")
                if peak.get("start") and peak["snow_in"] > 0 else None)
        rng_txt = f" (range {fmt(rng['p10'])}–{fmt(rng['p90'])})" if rng and rng.get("p10") is not None else ""
        r = wk.get(m["id"])
        bits = [f"{fmt(likely)} likely in the next 72 h{rng_txt}"]
        if when:
            bits.append(f"heaviest around {when}")
        if r and r.get("drive_h") is not None:
            bits.append(f"{int(r['drive_h'])}h{round(r['drive_h'] % 1 * 60):02d} drive")
        if r and r.get("season", {}).get("status") == "scheduled":
            bits.append(f"not open until {r['season']['date']}")
        if m["pass"] == "Backcountry":
            bits.append("Not a recommendation to ski: read the official avalanche forecast first "
                        "(some zones have none)")
        alerts.append({
            "id": f"{m['id']}-{day}", "mountain": m["id"], "name": m["name"], "pass": m["pass"],
            "likely_in": round(likely, 1), "title": f"{m['name']}: {fmt(likely)} likely",
            "text": "; ".join(bits) + ".",
        })
    alerts.sort(key=lambda a: -a["likely_in"])

    (DATA / "alerts.json").write_text(json.dumps({
        "generated_at": now.isoformat(), "threshold_in": ALERT_IN, "alerts": alerts}, indent=1))

    pub = format_datetime(now)
    items = "".join(
        f"""
    <item>
      <title>{escape(a['title'])}</title>
      <link>{escape(SITE)}</link>
      <guid isPermaLink="false">{escape(a['id'])}</guid>
      <pubDate>{pub}</pubDate>
      <description>{escape(a['text'])}</description>
    </item>""" for a in alerts)
    (DATA / "alerts.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Northeast Snow: powder alerts</title>
    <link>{escape(SITE)}</link>
    <description>Northeast ski areas and backcountry zones forecast to get {fmt(ALERT_IN)} or more in the next 72 hours. Forecasts, not guarantees; backcountry alerts are never a recommendation to ski avalanche terrain.</description>
    <language>en-us</language>
    <lastBuildDate>{pub}</lastBuildDate>
    <ttl>60</ttl>{items}
  </channel>
</rss>
""")
    print(f"wrote data/alerts.xml + alerts.json ({len(alerts)} alerts at {fmt(ALERT_IN)}+)", file=sys.stderr)
    for a in alerts[:10]:
        print(f"  {a['title']} — {a['text']}", file=sys.stderr)


if __name__ == "__main__":
    main()
