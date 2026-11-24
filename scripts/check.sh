#!/bin/bash
# Behaviour check against a running dashboard. Read-only: it never changes a service.
# Usage: scripts/check.sh [port]   -> prints PASS/FAIL lines, exit 1 on any FAIL
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASE="http://127.0.0.1:${1:-7777}"
fail=0
ok(){ if [ "$2" = "$3" ]; then echo "PASS  $1"; else echo "FAIL  $1  (got: $2, want: $3)"; fail=1; fi; }
py(){ python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }
post(){ curl -s -m 5 -X POST "$@"; }

python3 "$DIR/server.py" --selftest >/dev/null 2>&1; ok "parser + guard self-test" "$?" "0"

S=$(curl -s -m 8 "$BASE/api/status"); M=$(curl -s -m 5 "$BASE/api/metrics"); I=$(curl -s -m 30 "$BASE/api/inventory")
ok "status lists services"            "$(echo "$S" | py "len(d['services'])>0")" "True"
ok "every service has numeric mem/cpu" "$(echo "$S" | py "all('memMB' in s and 'cpuN' in s for s in d['services'])")" "True"
ok "every service has a history series" "$(echo "$S" | py "all('hist' in s for s in d['services'])")" "True"
ok "status carries alerts + events"   "$(echo "$S" | py "'alerts' in d and 'events' in d")" "True"
ok "metrics has per-app memory"       "$(echo "$M" | py "len(d['apps'])>0 and 'memMB' in d['apps'][0]")" "True"
ok "memory parts add up to total"     "$(echo "$M" | py "abs(sum(d['memParts'].values())-d['memTotalGB'])<1.5")" "True"
ok "top processes by cpu and by mem"  "$(echo "$M" | py "len(d['procs'])>0 and len(d['procsMem'])>0")" "True"
ok "inventory has agents/models/repos" "$(echo "$I" | py "all(k in d for k in ('agents','models','github'))")" "True"

# guards: none of these may succeed
ok "POST without X-Dash is refused"   "$(post "$BASE/api/proc" -d '{"pid":1}' | py "d['msg']")" "forbidden"
ok "POST from a foreign Origin is refused" "$(post -H 'X-Dash: 1' -H 'Origin: https://evil.example' "$BASE/api/proc" -d '{"pid":1}' | py "d['msg']")" "forbidden"
ok "GET with a foreign Host is refused" "$(curl -s -m 5 -H 'Host: evil.example' "$BASE/api/status")" "forbidden"
ok "cannot signal pid 1"              "$(post -H 'X-Dash: 1' "$BASE/api/proc" -d '{"pid":1}' | py "d['ok']")" "False"
ok "unknown service action is refused" "$(post -H 'X-Dash: 1' "$BASE/api/action" -d '{"id":"nope","action":"rm"}' | py "d['ok']")" "False"
ok "unknown docker action is refused" "$(post -H 'X-Dash: 1' "$BASE/api/docker" -d '{"action":"system prune -a"}' | py "d['ok']")" "False"
ok "model name is validated"          "$(post -H 'X-Dash: 1' "$BASE/api/model" -d '{"name":"a; id","action":"delete"}' | py "d['ok']")" "False"
exit $fail
