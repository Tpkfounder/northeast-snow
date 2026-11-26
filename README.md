# Northeast Snow (TPK Labs)

A Northeast US snow map with three views, rebuilt from public weather data every few hours:

1. **Storm totals:** next-72h snowfall per mountain as a range (WPC 10th / 50th / 90th percentile) plus the NWS point forecast.
2. **Where to ski this weekend:** Ikon, Epic and Indy resorts ranked by snow conditions × wind-hold risk × drive time from Westchester, NY.
3. **Backcountry lines:** curated lines in five zones (Presidential Range, Mount Mansfield, Adirondack High Peaks, Katahdin, Granite Backcountry Alliance glades in Maine), scored daily. The Presidentials are gated by the official Mount Washington Avalanche Center rating; zones no avalanche center covers always read "no avalanche forecast — conditions score only". It never says "go ski this." Each zone can be toggled on and off.

## Data sources
| What | Source |
|---|---|
| Point forecast (snow, temp, wind) | NWS gridpoint API (api.weather.gov) |
| Snowfall percentiles | WPC Probabilistic Precipitation Portal (NBM-based GRIB2) |
| Observed snowfall, season to date | NOHRSC National Snowfall Analysis v2 |
| Snow depth on the ground (modeled) | NOAA NOHRSC SNODAS via NSIDC (G02158), daily ~1 km grid |
| Drive times (cached) | Valhalla on OpenStreetMap data (valhalla1.openstreetmap.de) |
| Avalanche danger | avalanche.org public API |
| Season opening dates (snapshot) | snowpack.report, which links each resort's own announcement |
| Resort websites / snow report links | Resort sites; Indy links from indyskipass.com resort pages |
| Backcountry line geometry | OpenStreetMap (ODbL) |
| Slope / aspect / elevation | USGS 3DEP lidar |

## Also
- **Storm timeline:** each mountain's popup charts snow and rain in 6-hour blocks for the next 72 h.
- **Snow on the ground:** SNODAS modeled depth (natural snow, not resort base depth) at each summit and base; backcountry coverage scores use it.
- **Powder alerts:** `data/alerts.xml` is an RSS feed of anything forecast 6"+ in 72 h (threshold in `make_alerts.py`). Subscribe in any feed reader, or pipe it to email with a service like Blogtrottr or IFTTT.
- **Stale-data warning:** `update.sh` records each step in `data/status.json`; the page warns when data is over 8 hours old or a source failed.
- **Opening dates:** a weekly scheduled Claude task (Mondays 8 am, runs while the Claude app is open) refreshes `config/resort_info.json` and commits it locally; push with GitHub Desktop.

## Run locally
```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
./update.sh                 # fetch everything, score, write data/*.json
python3 -m http.server 8765 # then open http://localhost:8765
```
Python 3.13 is required: the ecCodes GRIB library has no wheels for newer versions yet.

## Layout
- `config/mountains.json`: tracked mountains (summit point for weather, base point for driving).
- `config/scoring.json`: weekend-score weights. Tune, then run `python score.py`.
- `config/resort_info.json`: each resort's website, snow-report link and 2026-27 opening date (a snapshot with an `as_of` date, not live). A resort with an announced opening day after the weekend scores 0 and sorts last. Update it weekly in early season.
- `config/backcountry/<zone>_lines.json`: one file per backcountry zone (avalanche center, sample points, hazards, line list). After editing, run `python build_backcountry.py <zone>` to rebuild `<zone>.geojson`. A zone with `"avalanche_center_id": null` is permanently "no avalanche forecast".
- `index.html` + `app.js`: the page. No build step.
- `data/`: generated output. Only `drive_times.json` and `gridpoints.json` (slow-changing caches) are committed.

## Hosting
`.github/workflows/update.yml` runs `update.sh` on a schedule and publishes `index.html` + `data/*.json` to GitHub Pages.
In the repo settings, set Settings → Pages → Source to **GitHub Actions**. The schedule can also be triggered manually from the Actions tab.
Optional: set a repository variable `NWS_CONTACT` (e.g. a project email) to include in the NWS User-Agent.

## Security
- The site is static: no server, no logins, no cookies, no user data collected. GitHub Pages serves it over HTTPS only.
- `index.html` sets a Content-Security-Policy: scripts only from this site and the pinned Leaflet build on cdnjs, which is loaded with a Subresource Integrity hash (a modified copy won't run). No inline scripts.
- All data inserted into the page is HTML-escaped and links must be `https://`, so odd text coming back from an upstream API can't inject markup.
- The workflow has no permissions by default; each job gets the minimum. Actions are pinned to full commit SHAs, checkout doesn't keep git credentials, and pip installs prebuilt wheels only (no package build scripts run). Dependabot proposes updates monthly.
- No secrets are needed or stored. Keep it that way: never commit API keys or personal emails.

## License
Copyright (c) 2026 TPK Labs. All rights reserved; see [LICENSE](LICENSE). Third-party data and libraries keep their own terms (OpenStreetMap data is ODbL and must stay attributed).
