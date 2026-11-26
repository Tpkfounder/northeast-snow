#!/usr/bin/env bash
# Refresh all data, then re-rank the weekend.
#   NWS point forecast (required) -> WPC PPP ranges -> NOHRSC observed snow -> SNODAS snow depth
#   -> drive times (cached) -> score -> backcountry -> powder-alert feed
# Optional steps warn and continue; the map and score degrade gracefully without them.
# Every step's result goes into data/status.json so the site can say which source is missing.
set -u
cd "$(dirname "$0")"
PY="${PY:-.venv/bin/python}"   # the GitHub Action sets PY=python
mkdir -p data
STEPS=""

step() {  # step <name> <description of what's lost if it fails> <command...>
  local name="$1" lost="$2"; shift 2
  local ok=true
  if ! "$@"; then
    ok=false
    echo "warning: $name failed; $lost" >&2
  fi
  STEPS="${STEPS}${STEPS:+,}{\"name\":\"$name\",\"ok\":$ok,\"lost\":\"$lost\"}"
  $ok
}

write_status() {
  printf '{"finished_at":"%s","steps":[%s]}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$STEPS" > data/status.json
}

step "NWS forecast" "nothing can be updated" $PY fetch_nws.py || { write_status; exit 1; }
step "WPC snowfall ranges" "map shows NWS totals only" $PY fetch_ppp.py
step "NOHRSC observed snow" "scores ignore recent snowfall" $PY fetch_recent.py
step "SNODAS snow depth" "snow depth and backcountry coverage use season snowfall only" $PY fetch_depth.py
step "Drive times" "using previous drive times" $PY fetch_drive.py
step "Weekend ranking" "weekend view not updated" $PY score.py
step "Backcountry scoring" "backcountry view not updated" $PY score_backcountry.py
step "Powder alerts feed" "alerts feed not updated" $PY make_alerts.py
write_status
