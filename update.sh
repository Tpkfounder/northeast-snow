#!/usr/bin/env bash
# Refresh all data, then re-rank the weekend.
#   NWS point forecast (required) -> WPC PPP ranges -> NOHRSC observed snow -> drive times (cached) -> score
# Optional steps warn and continue; the map and score degrade gracefully without them.
set -u
cd "$(dirname "$0")"
PY="${PY:-.venv/bin/python}"   # the GitHub Action sets PY=python
$PY fetch_nws.py || exit 1
$PY fetch_ppp.py    || echo "warning: PPP percentile fetch failed; map will show NWS totals only" >&2
$PY fetch_recent.py || echo "warning: NOHRSC observed snowfall fetch failed; score ignores recent snow" >&2
$PY fetch_drive.py  || echo "warning: drive-time fetch failed; using previous drive_times.json if present" >&2
$PY score.py
$PY score_backcountry.py || echo "warning: backcountry scoring failed; previous data/backcountry.json left in place" >&2
