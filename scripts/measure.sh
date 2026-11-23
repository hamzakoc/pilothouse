#!/bin/bash
# Gauntlet measurement harness (functional signal).
# Hits the live dashboard API + inspects source for feature markers, writes a
# JSON report. Visual evidence (screenshots) is captured separately each round.
# Usage: scripts/measure.sh [round-label]  -> evidence/report-<label>.json
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="${1:-adhoc}"
OUT="$DIR/evidence/report-$LABEL.json"
BASE="http://127.0.0.1:7777"

jqget(){ python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }
has(){ grep -qi -- "$2" "$DIR/$1" && echo true || echo false; }

STATUS=$(curl -s -m 5 "$BASE/api/status")
INV=$(curl -s -m 8 "$BASE/api/inventory")
MET=$(curl -s -m 5 "$BASE/api/metrics")

svc_total=$(echo "$STATUS" | jqget "len(d['services'])"); : "${svc_total:=0}"
svc_up=$(echo "$STATUS" | jqget "sum(s['up'] for s in d['services'])"); : "${svc_up:=0}"
ag=$(echo "$INV" | jqget "len(d['agents'])"); : "${ag:=0}"
md=$(echo "$INV" | jqget "len(d['models'])"); : "${md:=0}"
rp=$(echo "$INV" | jqget "len(d['github']['repos'])"); : "${rp:=0}"
metrics_ep=$([ -n "$MET" ] && echo "$MET" | python3 -c "import sys,json;json.load(sys.stdin);print('true')" 2>/dev/null || echo false)
: "${metrics_ep:=false}"

html_bytes=$(wc -c < "$DIR/index.html" | tr -d ' ')
py_bytes=$(wc -c < "$DIR/server.py" | tr -d ' ')

cat > "$OUT" <<JSON
{
  "label": "$LABEL",
  "ts": $(date +%s),
  "api": {
    "status_ok": $([ -n "$STATUS" ] && echo true || echo false),
    "inventory_ok": $([ -n "$INV" ] && echo true || echo false),
    "metrics_endpoint": $metrics_ep,
    "services_total": $svc_total,
    "services_up": $svc_up,
    "agents": $ag,
    "models": $md,
    "repos": $rp
  },
  "backend_features": {
    "api_metrics": $(has server.py "/api/metrics"),
    "api_logs": $(has server.py "/api/logs"),
    "host_cpu_mem": $(has server.py "vm_stat"),
    "docker_stats": $(has server.py "docker_stats"),
    "history_buffer": $(has server.py "deque"),
    "process_stats": $(has server.py "%cpu")
  },
  "frontend_features": {
    "command_palette": $(has index.html "openPalette"),
    "charts_sparklines": $(has index.html "spark("),
    "canvas_or_svg_chart": $(has index.html "class=\"spark\""),
    "detail_drawer": $(has index.html "openDrawer"),
    "log_viewer": $(has index.html "logbox"),
    "theme_toggle": $(has index.html "data-theme"),
    "keyboard_shortcuts": $(has index.html "keydown"),
    "toasts": $(has index.html "toast"),
    "search_filter": $(has index.html "filter")
  },
  "size": { "html_bytes": $html_bytes, "server_bytes": $py_bytes }
}
JSON
echo "wrote $OUT"
cat "$OUT"
