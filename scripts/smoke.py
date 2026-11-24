"""Start the dashboard as a fresh user would, on whatever OS this runs on, and exercise it over HTTP.
Usage: python smoke.py /path/to/server.py   -> prints findings, exit 1 on any failure."""
import json, os, subprocess, sys, time, urllib.request

server = sys.argv[1]; port = 7791; base = "http://127.0.0.1:%d" % port
env = dict(os.environ, DASH_PORT=str(port), DASH_DATA=os.path.join(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp", "dash-smoke"))
fails = []


def get(u, t=60):
    return json.load(urllib.request.urlopen(base + u, timeout=t))


def post(u, body, headers=None):
    req = urllib.request.Request(base + u, json.dumps(body).encode(), dict({"Content-Type": "application/json"}, **(headers or {})))
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + str(detail) if detail and not ok else ""))
    if not ok:
        fails.append(name)


dummy = subprocess.Popen([sys.executable, "-m", "http.server", "8999", "--bind", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
srv = subprocess.Popen([sys.executable, server], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    for _ in range(60):
        try:
            get("/api/metrics", 3); break
        except Exception:
            time.sleep(1)
    time.sleep(12)   # two refresh cycles, so CPU deltas and history exist
    m = get("/api/metrics"); s = get("/api/status"); i = get("/api/inventory"); u = get("/api/usage?range=7d"); n = get("/api/usage/now")
    print("platform:", m["platform"], "| cores:", m.get("cores"), "| memory GB:", m["memUsedGB"], "/", m["memTotalGB"], "| cpu:", m["cpu"])
    print("processes:", len(m["procs"]), "| apps:", len(m["apps"]), "| services:", [(x["name"], x["port"], x["kind"]) for x in s["services"]][:8])
    print("agents:", [a["name"] for a in i["agents"]], "| models:", len(i["models"]), "| repos:", len(i["github"]["repos"]), "| tools:", i["tools"])
    check("metrics: memory total > 0", m["memTotalGB"] > 0, m["memTotalGB"])
    check("metrics: memory parts present", bool(m["memParts"]))
    check("metrics: process list not empty", len(m["procs"]) > 0)
    check("metrics: per-app list not empty", len(m["apps"]) > 0)
    check("metrics: disk reported", len(m["disk"]) > 0 and m["disk"][0]["totalGB"] > 0, m["disk"])
    check("metrics: CPU is a sane percentage", 0 <= m["cpu"] <= 100, m["cpu"])
    mine = [x for x in s["services"] if x.get("port") == 8999]
    check("discovery: finds a server listening on port 8999", bool(mine), [x["port"] for x in s["services"]])
    if mine:
        d = get("/api/inspect?id=" + mine[0]["id"])
        check("detail: command line of that server is known", "http.server" in (d.get("command") or ""), d.get("command"))
        check("detail: its memory is known", (mine[0].get("memMB") or 0) > 0, mine[0].get("memMB"))
        check("control: stop is offered for it", "stop" in mine[0]["actions"] or "kill" in mine[0]["actions"], mine[0]["actions"])
        r = post("/api/action", dict(id=mine[0]["id"], action="kill" if "kill" in mine[0]["actions"] else "stop"), {"X-Dash": "1"})
        check("control: stopping it through the API works", r.get("ok") is True, r)
        time.sleep(2)
        check("control: the process is really gone", dummy.poll() is not None)
    check("guard: POST without the header is refused", post("/api/proc", dict(pid=1)).get("msg") == "forbidden")
    check("guard: cannot signal pid 1 / system", post("/api/proc", dict(pid=1), {"X-Dash": "1"}).get("ok") is False)
    check("usage: report renders with no logs", u["sessionCount"] >= 0 and "totals" in u)
    check("usage: right-now panel renders", "budgets" in n and "live" in n)
    check("history: database is writable", s["persisted"] is True)
    page = urllib.request.urlopen(base + "/", timeout=10).read()
    check("page: served", b"Local AI Control" in page)
finally:
    srv.terminate(); dummy.poll() is None and dummy.terminate()
    out = (srv.communicate(timeout=10)[0] or b"").decode("utf-8", "replace")
    check("server log has no traceback", "Traceback" not in out, out[-600:])
print("RESULT:", "ok" if not fails else "%d failed: %s" % (len(fails), fails))
sys.exit(1 if fails else 0)
