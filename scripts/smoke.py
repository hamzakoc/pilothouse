"""Start the dashboard as a fresh user would, on whatever OS this runs on, and exercise it over HTTP.
Usage: python smoke.py /path/to/server.py   -> prints findings, exit 1 on any failure."""
import json, os, subprocess, sys, time, urllib.request

server = sys.argv[1]; port = 7791; base = "http://127.0.0.1:%d" % port
TOK = {"Authorization": "Bearer smoke-token"}
env = dict(os.environ, DASH_PORT=str(port), DASH_TOKEN="smoke-token", DASH_DATA=os.path.join(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp", "dash-smoke"))
fails = []


def get(u, t=60):
    return json.load(urllib.request.urlopen(urllib.request.Request(base + u, headers=TOK), timeout=t))


def post(u, body, headers=None):
    req = urllib.request.Request(base + u, json.dumps(body).encode(), dict({"Content-Type": "application/json"}, **dict(TOK, **(headers or {}))))
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


def code(u, headers=None, b=None):
    """HTTP status of a GET, whatever it is."""
    try:
        return urllib.request.urlopen(urllib.request.Request((b or base) + u, headers=TOK if headers is None else headers), timeout=20).status
    except urllib.error.HTTPError as e:
        return e.code


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
    check("guard: the sign-in button's request is refused without the page's header", post("/api/pair", {}).get("msg") == "forbidden")
    check("guard: cannot signal pid 1 / system", post("/api/proc", dict(pid=1), {"X-Dash": "1"}).get("ok") is False)
    check("usage: report renders with no logs", u["sessionCount"] >= 0 and "totals" in u)
    check("usage: right-now panel renders", "budgets" in n and "live" in n)
    check("history: database is writable", s["persisted"] is True)
    r = urllib.request.urlopen(base + "/", timeout=10); page = r.read()
    check("page: served", b"Pilothouse" in page)
    check("page: security headers are set", "frame-ancestors 'none'" in (r.headers.get("Content-Security-Policy") or "")
          and r.headers.get("X-Frame-Options") == "DENY" and r.headers.get("X-Content-Type-Options") == "nosniff", dict(r.headers))
    h = get("/api/health")
    check("health: answers ok with a version", h.get("ok") is True and bool(h.get("version")), h)
    prom = urllib.request.urlopen(urllib.request.Request(base + "/metrics", headers=TOK), timeout=20).read().decode()
    check("metrics: Prometheus text is served", "pilothouse_up 1" in prom and "pilothouse_memory_used_percent" in prom, prom[:200])
    check("guard: a foreign Host header is refused (DNS rebinding)", code("/api/status", dict(TOK, Host="evil.example:%d" % port)) == 403)
    check("guard: a request another website caused is refused", code("/api/inventory?force", dict(TOK, **{"Sec-Fetch-Site": "cross-site"})) == 403)
    check("guard: without the access key the API is closed, health is open", code("/api/status", {}) == 401 and code("/api/health", {}) == 200)
    check("guard: a POST from a foreign Origin is refused", post("/api/proc", dict(pid=1), {"X-Dash": "1", "Origin": "http://evil.example"}).get("msg") == "forbidden")
    check("guard: an oversized POST is refused", post("/api/budget", dict(pad="x" * 70000), {"X-Dash": "1"}).get("msg") == "request too large")
    check("unknown path is a 404, not the page", code("/api/nope") == 404 and code("/etc/passwd") == 404)
finally:
    srv.terminate(); dummy.poll() is None and dummy.terminate()
    out = (srv.communicate(timeout=10)[0] or b"").decode("utf-8", "replace")
    check("server log has no traceback", "Traceback" not in out, out[-600:])

# demo mode with an access token: sample data only, nothing changed, token enforced
dport = port + 2; dbase = "http://127.0.0.1:%d" % dport; tok = {"Authorization": "Bearer smoke-token"}
demo = subprocess.Popen([sys.executable, server, "--demo", "--port", str(dport)], env=dict(env, DASH_TOKEN="smoke-token"),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    for _ in range(30):
        try:
            urllib.request.urlopen(dbase + "/", timeout=2); break
        except Exception:
            time.sleep(0.5)
    check("token: the page itself needs no token", code("/", {}, dbase) == 200)
    check("token: the API refuses a request without it", code("/api/status", {}, dbase) == 401 and code("/metrics", {}, dbase) == 401)
    check("token: a wrong token is refused", code("/api/status", {"Authorization": "Bearer nope"}, dbase) == 401)
    d = json.load(urllib.request.urlopen(urllib.request.Request(dbase + "/api/status", headers=tok), timeout=10))
    check("demo: serves the sample stack", d.get("demo") is True and any(x["id"] == "dk:rag-api" for x in d["services"]))
    for ep in ("metrics", "inventory", "usage?range=7d", "usage/now", "usage/session?id=demo-a1f0", "history?range=6h", "since?ts=0",
               "logs?id=dk:rag-worker", "inspect?id=dk:rag-api", "inspect?id=host:8080", "modelinfo?name=qwen3:32b", "health"):
        check("demo: /api/%s answers" % ep, code("/api/" + ep, tok, dbase) == 200)
    req = urllib.request.Request(dbase + "/api/action", json.dumps(dict(id="dk:rag-api", action="stop")).encode(), dict(tok, **{"X-Dash": "1"}))
    check("demo: actions change nothing", "nothing was changed" in json.load(urllib.request.urlopen(req, timeout=10)).get("msg", ""))
finally:
    demo.terminate()
    out = (demo.communicate(timeout=10)[0] or b"").decode("utf-8", "replace")
    check("demo server log has no traceback", "Traceback" not in out, out[-600:])
print("RESULT:", "ok" if not fails else "%d failed: %s" % (len(fails), fails))
sys.exit(1 if fails else 0)
