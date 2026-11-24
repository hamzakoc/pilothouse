"""Made-up data for `--demo`: a small fictional AI stack, so the page can be tried, shown and
screenshotted without reading anything from the machine it runs on. Nothing here touches the host.

Each function returns the same shape as the real endpoint of the same name in app.py.
"""
import math, time

T0 = time.time()
GIB = 1024


def w(seed, base, amp, period=40.0, t=None):
    """a value that moves a little over time, the same for everyone at the same moment."""
    t = time.time() if t is None else t
    return base + amp * math.sin(t / period + seed) + amp * 0.4 * math.sin(t / (period / 3.7) + seed * 2.3)


def mb(n): return "%.1f GB" % (n / 1024.0) if n >= 1024 else "%d MB" % n


# id, name, desc, group, kind, port, http, where, memMB, cpu, state, extra
_S = [
    ("dk:rag-api", "RAG API", "FastAPI app answering questions over your documents", "Docker · rag-stack", "docker", 8000, "api", "rag-stack/api:latest", 412, 3.1, "running", dict(qual="api")),
    ("dk:rag-worker", "Ingest worker", "Splits and embeds new documents", "Docker · rag-stack", "docker", None, None, "rag-stack/worker:latest", 0, 0, "restarting", dict(qual="worker", crashed=True)),
    ("dk:qdrant", "Qdrant", "Vector database", "Docker · rag-stack", "docker", 6333, "page", "qdrant/qdrant:v1.12", 938, 1.2, "running", dict(qual="qdrant")),
    ("dk:rag-postgres", "PostgreSQL", "Relational database", "Docker · rag-stack", "docker", 5432, None, "postgres:17", 164, 0.4, "running", dict(qual="db")),
    ("dk:rag-redis", "Redis", "In-memory cache and queue", "Docker · rag-stack", "docker", 6379, None, "redis:7-alpine", 22, 0.2, "running", dict(qual="cache")),
    ("dk:n8n", "n8n", "Workflow automation", "Docker · automation", "docker", 5678, "page", "n8nio/n8n:latest", 388, 0.9, "running", dict(qual="n8n")),
    ("dk:n8n-db", "PostgreSQL", "Relational database", "Docker · automation", "docker", 5433, None, "postgres:16", 96, 0.1, "running", dict(qual="db")),
    ("dk:open-webui", "Open WebUI", "Chat interface for local models", "Docker · containers", "docker", 3000, "page", "ghcr.io/open-webui/open-webui:main", 742, 0.6, "running", {}),
    ("dk:searxng", "SearXNG", "Private metasearch engine", "Docker · containers", "docker", 8888, "page", "searxng/searxng:latest", 148, 0.1, "running", {}),
    ("dk:grafana", "Grafana", "Dashboards", "Docker · containers", "docker", 3001, None, "grafana/grafana:11.2", 0, 0, "exited", {}),
    ("ld:com.ollama.server", "Ollama", "Runs local models and serves them over an API", "AI / LLM", "launchd", 11434, "api", "ollama · pid 811", 21480, 38.0, "running", dict(launchd="com.ollama.server", pid=811, uptime="2-03:14:09", procs=2)),
    ("host:8080", "llama.cpp server", "Serves one GGUF model with an OpenAI-style API", "AI / LLM", "host", 8080, "page", "llama-server · pid 4410", 9320, 4.0, "running", dict(pid=4410, uptime="05:41:20", procs=1)),
    ("host:8889", "Jupyter", "Notebooks", "Web / Dev", "host", 8889, "page", "python3.12 · pid 5120", 310, 0.3, "running", dict(pid=5120, uptime="03:02:11", procs=3)),
    ("host:5173", "Vite dev server", "Front-end dev server with hot reload", "Web / Dev", "host", 5173, "page", "node · pid 6033", 268, 1.1, "running", dict(pid=6033, uptime="01:12:40", procs=2)),
    ("host:4000", "LiteLLM proxy", "One API in front of many model vendors", "AI / LLM", "host", 4000, "api", "litellm · pid 6200", 356, 0.5, "running", dict(pid=6200, uptime="05:40:02", procs=1)),
    ("brew:postgresql@16", "PostgreSQL (Homebrew)", "Relational database", "Database", "brew", 5434, None, "postgresql@16 · stopped", 0, 0, "stopped", dict(brew="postgresql@16")),
]
_E = [
    ("dk:rag-api", "dk:qdrant", "config QDRANT_URL", 0), ("dk:rag-api", "dk:rag-postgres", "config DATABASE_URL", 0),
    ("dk:rag-api", "dk:rag-redis", "config REDIS_URL", 0), ("dk:rag-api", "ld:com.ollama.server", "config OLLAMA_BASE_URL", 0),
    ("dk:rag-worker", "dk:rag-redis", "compose depends_on", 1300), ("dk:rag-worker", "dk:qdrant", "config QDRANT_URL", 1300),
    ("dk:n8n", "dk:n8n-db", "config DB_POSTGRESDB_HOST", 0), ("dk:n8n", "dk:rag-api", "live connection to :8000", 0),
    ("dk:open-webui", "ld:com.ollama.server", "config OLLAMA_BASE_URL", 0), ("dk:open-webui", "dk:searxng", "config SEARXNG_QUERY_URL", 0),
    ("host:5173", "dk:rag-api", "live connection to :8000", 0), ("host:4000", "host:8080", "live connection to :8080", 0),
    ("host:8889", "host:4000", "live connection to :4000", 420),
]


def _hist(seed, base, amp, n=40):
    now = time.time()
    return [max(0, round(w(seed, base, amp, t=now - (n - i) * 2), 1)) for i in range(n)]


def _services():
    out = []
    for i, (sid, name, desc, group, kind, port, http, where, mem, cpu, state, x) in enumerate(_S):
        up = state == "running"
        m = max(0, round(w(i, mem, mem * 0.04))) if up else 0
        c = max(0.0, round(w(i * 1.7, cpu, cpu * 0.5), 1)) if up else 0.0
        if kind == "docker":
            acts = ["restart", "stop", "pause", "kill"] if up else ["start", "remove"]
            upt = "Up 2 days" if up else ("Restarting (1) 14 seconds ago" if state == "restarting" else "Exited (0) 3 days ago")
        elif kind == "launchd": acts = ["restart", "stop", "unload", "kill"]; upt = x.get("uptime")
        elif kind == "brew": acts = ["start"]; upt = None
        else: acts = ["stop", "kill"]; upt = x.get("uptime")
        out.append(dict(id=sid, name=name, desc=desc, group=group, kind=kind, docker=sid[3:] if kind == "docker" else None,
                        brew=x.get("brew"), launchd=x.get("launchd"), systemd=None, sysUser=False, jobRoot=kind in ("launchd", "brew"),
                        url="http://localhost:%d" % port if http == "page" else None, http=http if up else None, where=where,
                        port=port, ports=[port] if port else [], note=None, cpu="%.1f%%" % c if up else None, mem=mb(m) if up else None,
                        cpuN=c, memMB=m, procs=x.get("procs"), state=state, qual=x.get("qual"), paused=False,
                        crashed=bool(x.get("crashed")), actions=acts, has_logs=True, up=up, pid=x.get("pid"), uptime=upt,
                        hist=dict(cpu=_hist(i * 1.7, cpu, cpu * 0.5, 24) if up else [], mem=[round(v) for v in _hist(i, mem, mem * 0.04, 24)] if up else [])))
    return out


def _events():
    now = int(time.time()); ev = [
        (20, "down", "Ingest worker is restarting again and again (exit code 1)", "dk:rag-worker"),
        (22, "alert", "Ingest worker keeps restarting", "dk:rag-worker"),
        (1260, "action", "You restarted RAG API", "dk:rag-api"), (1255, "up", "RAG API is up", "dk:rag-api"),
        (4200, "action", "You loaded model qwen3:32b", None), (7300, "wait", "Claude Code is waiting for you: Add retry to the ingest queue", None),
        (9100, "new", "LiteLLM proxy appeared on port 4000", "host:4000"), (15000, "resolved", "Memory used is back under 88%", None),
        (15900, "alert", "Memory used 91% (limit 88%)", None), (21000, "gone", "Grafana stopped (exit code 0)", "dk:grafana"),
        (30000, "action", "Dashboard started (monitoring resumed)", None)]
    return [dict(n=len(ev) - i, ts=now - age, kind=k, msg=m, id=sid) for i, (age, k, m, sid) in enumerate(ev)]


def status():
    S = _services(); m = metrics()
    return dict(ts=int(time.time()), services=S, edges=[list(e) for e in _E],
                alerts=[dict(key="restarting:dk:rag-worker", level="err", id="dk:rag-worker", msg="Ingest worker keeps restarting (exit code 1)")],
                rules=[dict(name="Memory used", value="%s%%" % m["memPct"], rule=">= 88%", firing=False),
                       dict(name="Memory pressure (macOS)", value="normal", rule="warning or critical", firing=False),
                       dict(name="CPU sustained", value="%s%%" % m["cpu"], rule=">= 90% for 30s", firing=False),
                       dict(name="Disk /", value="61.0%", rule=">= 90%", firing=False),
                       dict(name="Containers", value="1 with a problem", rule="crashed, unhealthy or restart loop", firing=True),
                       dict(name="Always-on services", value="all running", rule="KeepAlive job not running", firing=False)],
                events=_events(), persisted=True, keepHours=48, demo=True)


_APPS = [("Ollama", 21480, 38.0, 2), ("llama-server", 9320, 4.0, 1), ("Docker", 3900, 6.5, 9), ("Browser", 3150, 5.2, 14),
         ("Code editor", 2240, 3.1, 9), ("Claude Code", 1620, 2.4, 4), ("Terminal", 520, 0.4, 5), ("LiteLLM", 356, 0.5, 1),
         ("Jupyter", 310, 0.3, 3), ("node", 268, 1.1, 2)]


def metrics():
    now = time.time(); cpu = round(max(2, w(1, 31, 12)), 1); used = round(w(2, 44.6, 0.9), 1); total = 64.0
    apps, procs = [], []
    for i, (n, m, c, k) in enumerate(_APPS):
        mm = round(w(i + 3, m, m * 0.03)); cc = round(max(0, w(i * 2.1, c, c * 0.4)), 1)
        pp = [dict(pid=800 + i * 37 + j, name=n.lower().replace(" ", "-") + ("" if j == 0 else " helper"), rssMB=round(mm * (0.7 if j == 0 else 0.3 / max(1, k - 1))),
                   swapMB=0, cpu=round(cc * (0.8 if j == 0 else 0.2 / max(1, k - 1)), 1), up="05:%02d:11" % (10 + i), mine=True) for j in range(min(k, 3))]
        apps.append(dict(name=n, memMB=mm, swapMB=1200 if n == "Ollama" else 0, cpu=cc, n=k, mine=True, hist=[round(v) for v in _hist(i + 3, m, m * 0.03, 38)], procs=pp))
        procs.append(dict(pid=pp[0]["pid"], name=pp[0]["name"], app=n, cpu=cc, rssMB=pp[0]["rssMB"], user="you", up=pp[0]["up"], mine=True))
    H = 40; ts = [int(now - (H - i) * 2) for i in range(H)]
    return dict(ts=int(now), cpu=cpu, cpuUser=round(cpu * 0.7, 1), cpuSys=round(cpu * 0.3, 1), load=[round(w(4, 3.2, 0.6), 2), 3.4, 3.1],
                memUsedGB=used, memTotalGB=total, memPct=round(used / total * 100, 1), swapGB=1.4, gpu=round(max(0, w(5, 46, 22))),
                rxKBs=round(max(0, w(6, 180, 120)), 1), txKBs=round(max(0, w(7, 60, 40)), 1),
                disk=[dict(mount="/", pct=61.0, usedGB=562, totalGB=926)], procs=sorted(procs, key=lambda p: -p["cpu"]),
                procsMem=sorted(procs, key=lambda p: -p["rssMB"]), apps=apps,
                memParts=dict(app=round(used - 9.3, 1), wired=4.1, compressed=5.2, cached=12.8, free=round(total - used - 12.8, 1), other=0.0),
                docker=[dict(name=s["docker"], cpu=s["cpu"], mem="%s / 7.7GiB" % s["mem"], memPct="%.1f%%" % (s["memMB"] / 78.8), net="1.2MB / 840kB",
                             block="12MB / 4MB", pids="14") for s in _services() if s["kind"] == "docker" and s["up"]],
                llm=[dict(name="qwen3:32b", size="20.9 GB", memMB=21400, proc="100% GPU", ctx="32768", until="in 24 minutes", port=11434, pid=811, params="32.8B", quant="Q4_K_M")],
                pressure=1, cores=12,
                hist=dict(ts=ts, cpu=_hist(1, 31, 12, H), mem=[round(v / total * 100, 1) for v in _hist(2, 44.6, 0.9, H)], gpu=[round(v) for v in _hist(5, 46, 22, H)],
                          rx=[round(v) for v in _hist(6, 180, 120, H)], tx=[round(v) for v in _hist(7, 60, 40, H)], load=_hist(4, 3.2, 0.6, H), disk=[61.0] * H),
                platform="macOS")


def history(rng="1h", sid=None, app=None):
    secs = {"1h": 3600, "6h": 21600, "24h": 86400, "48h": 172800}.get(rng, 3600); n = 60; step = secs // n
    now = time.time(); ts = [int(now - (n - i) * step) for i in range(n)]
    f = lambda seed, base, amp: [round(max(0, w(seed, base, amp, period=secs / 9.0, t=t)), 1) for t in ts]
    if sid or app:
        s = next((x for x in _S if x[0] == sid), None); base = s[8] if s else next((a[1] for a in _APPS if a[0] == app), 400)
        return dict(ts=ts, range=rng, step=step, cpu=f(3, s[9] if s else 2, 1.5), mem=f(8, base, base * 0.06))
    return dict(ts=ts, range=rng, step=step, cpu=f(1, 31, 14), mem=f(2, 70, 6), gpu=f(5, 46, 25), load=f(4, 3.2, 0.8),
                disk=[61.0] * n, net=f(6, 240, 150), swap=f(9, 1.4, 0.3))


def since(ts=0):
    ev = [e for e in _events() if e["ts"] >= ts]; c = {}
    for e in ev: c[e["kind"]] = c.get(e["kind"], 0) + 1
    now = int(time.time())
    return dict(since=ts, events=ev, counts=c, peakMem=[91.0, now - 15900], peakCpu=[88.5, now - 9000], peakSwap=[2.1, now - 15800],
                first=now - 30000, last=now, minutes=500)


def logs(sid, n=200):
    s = next((x for x in _S if x[0] == sid), None)
    if not s: return dict(id=sid, source="", lines=["(service not found)"])
    if sid == "dk:rag-worker":
        L = ["worker starting", "connecting to redis://cache:6379", "connecting to qdrant at http://qdrant:6333",
             "Traceback (most recent call last):", '  File "/app/worker.py", line 41, in main', "    model = load_embedder(os.environ['EMBED_MODEL'])",
             "KeyError: 'EMBED_MODEL'", "worker exited with code 1"] * 3
    else:
        L = ["%s INFO  %s request %d handled in %d ms" % (time.strftime("%H:%M:%S", time.localtime(time.time() - (40 - i) * 7)), s[1], 1000 + i, 12 + (i * 7) % 90) for i in range(40)]
    return dict(id=sid, source="docker logs " + sid[3:] if s[4] == "docker" else "~/Library/Logs/%s.log" % s[1].split()[0].lower(), lines=L[-n:])


def inspect(sid):
    s = next((x for x in _services() if x["id"] == sid), None)
    if not s: return dict(error="not found")
    names = {x[0]: x[1] for x in _S}
    d = dict(id=sid, name=s["name"], what=s["desc"], group=s["group"], url=s["url"], kind=s["kind"], up=s["up"], hist=s["hist"], actions=s["actions"],
             memMB=s["memMB"], cpuN=s["cpuN"], events=[e for e in _events() if e["id"] == sid],
             dependsOn=[dict(id=b, name=names[b], why=y) for a, b, y, _ in _E if a == sid],
             usedBy=[dict(id=a, name=names[a], why=y) for a, b, y, _ in _E if b == sid])
    if s["kind"] == "docker":
        proj = s["group"].split(" · ")[1]
        d.update(container=s["docker"], image=s["where"], state=s["state"], health="healthy" if s["up"] else None, started="2026-01-02T08:00:00",
                 uptime="2 days" if s["up"] else "14 seconds", restarts=27 if s["crashed"] else 0, exitCode=1 if s["crashed"] else 0, restartPolicy="unless-stopped",
                 memLimit="none", created="9 days", imageId="3f1c9a7b2e10", pidsInside="14", blockIO="12MB / 4MB", memPct="5.2%" if s["up"] else None,
                 command="python -m app", project=None if proj == "containers" else proj, service=s["qual"], cpu=s["cpu"],
                 memUse=s["mem"], netIO="1.2MB / 840kB", networks=["%s_default · 172.20.0.%d" % (proj, 2 + len(sid))],
                 mounts=["volume %s_data → /data" % s["docker"], "bind ./config → /app/config (read-only)"],
                 ports=["%d → %d/tcp" % (s["port"], s["port"])] if s["port"] else [],
                 env=["LOG_LEVEL=info", "TZ=UTC", "QDRANT_URL=http://qdrant:6333", "DATABASE_URL=postgresql://app:•••@db:5432/app"], envHidden=3,
                 labels=6, workdir="/app", healthLog=["ok"] if s["up"] else [], healthCmd="curl -f http://localhost/health" if s["up"] else None,
                 cpuLimit="none", finished=None if s["up"] else "3 days", oom=None,
                 inside=[dict(pid="1", cpu="0.4", rssMB=round(s["memMB"] * 0.8), cmd="python -m app"), dict(pid="18", cpu="0.1", rssMB=round(s["memMB"] * 0.2), cmd="worker --queue default")] if s["up"] else [])
    else:
        d.update(pid=s["pid"], ports=s["ports"], uptime=s["uptime"], where=s["where"], brew=s["brew"], launchd=s["launchd"], procs=s["procs"],
                 treeMem=s["mem"], treeCpu=s["cpu"], systemd=None)
        if s["pid"]:
            d.update(children=[dict(pid=s["pid"] + 1, name="runner", rssMB=round(s["memMB"] * 0.6), cpu=round(s["cpuN"] * 0.7, 1))], openFiles=42,
                     logFile="~/Library/Logs/%s.log" % s["name"].split()[0].lower(), ppid=1, user="you", cpu=s["cpu"], rssMB=round(s["memMB"] * 0.4),
                     command="/usr/local/bin/%s serve --port %d" % (s["name"].split()[0].lower(), s["port"]), cwd="~/projects/demo")
    return d


def modelinfo(name):
    return dict(name=name, sections={"Model": [["architecture", "qwen3"], ["parameters", "32.8B"], ["context length", "32768"], ["quantization", "Q4_K_M"]],
                                     "Capabilities": [["completion"], ["tools"], ["thinking"]], "Parameters": [["temperature", "0.7"], ["top_p", "0.8"]]})


def inventory(force=False):
    A = lambda i, n, d, run, mem, via, ver, **k: dict(
        id=i, name=n, desc=d, home="~/." + i, installed=True, url=k.get("url"), port=k.get("port"), bin="/usr/local/bin/" + i, version=ver, running=run,
        procs=k.get("procs", 1 if run else 0), memMB=mem, cpu=round(w(len(i), 1.2, 0.6), 1) if run else 0, canStop=run,
        top=[dict(pid=7000 + len(i), name=i, rssMB=mem, cpu=1.0)] if run else [], installedVia=via, app=k.get("app", ""),
        facts=dict(size=k.get("size", "120 MB"), changed="2 hours ago", items=["sessions", "settings.json", "plugins"], configs=["settings.json"], count=3),
        canLaunch=True, services=k.get("services", []))
    agents = [A("claude", "Claude Code", "Anthropic coding agent (CLI sessions)", True, 1620, "npm", "2.4.1", procs=4, size="1.9 GB"),
              A("codex", "Codex (OpenAI)", "OpenAI coding agent CLI", True, 410, "Homebrew", "0.157.1", procs=1),
              A("ollama", "Ollama", "Local model runtime", True, 21480, "Homebrew", "0.12.3", port=11434, size="64.2 GB",
                services=[dict(id="ld:com.ollama.server", name="Ollama", up=True, start=False)]),
              A("aider", "Aider", "Pair-programming agent in the terminal", False, 0, "pipx", "0.86.0"),
              A("gemini", "Gemini CLI", "Google coding agent CLI", False, 0, "npm", "0.9.2"),
              A("cursor", "Cursor", "AI code editor", False, 0, "app", "1.7", app="Cursor"),
              A("lmstudio", "LM Studio", "Desktop app for local models", False, 0, "app", "0.3.30", app="LM Studio", size="38.0 GB"),
              A("open-webui", "Open WebUI", "Chat interface for local models", True, 742, "Docker", "0.6.34", port=3000, url="http://localhost:3000",
                services=[dict(id="dk:open-webui", name="Open WebUI", up=True, start=False)])]
    M = lambda n, g, kind, rt, where, loaded=False: dict(
        name=n, size="%.1f GB" % g, bytes=int(g * 1e9), loaded=loaded, kind=kind, ollama=kind == "ollama", ollamaUp=True, runtime=rt, where=where,
        modified="%d days ago" % (3 + len(n) % 40), proc="100% GPU" if loaded else "", ctx="32768" if loaded else "", until="24 minutes" if loaded else "", link="")
    models = [M("qwen3:32b", 20.9, "ollama", "Ollama", "~/.ollama/models", True), M("llama3.3:70b", 42.5, "ollama", "Ollama", "~/.ollama/models"),
              M("gemma3:12b", 8.1, "ollama", "Ollama", "~/.ollama/models"), M("nomic-embed-text:latest", 0.3, "ollama", "Ollama", "~/.ollama/models"),
              M("deepseek-r1:14b", 9.0, "ollama", "Ollama", "~/.ollama/models"), M("mistral-small-24b-Q5_K_M.gguf", 16.8, "file", "GGUF (llama.cpp)", "~/models"),
              M("phi-4-Q4_K_M.gguf", 9.1, "file", "GGUF (llama.cpp)", "~/models"), M("BAAI/bge-m3", 2.3, "hf", "Hugging Face cache", "~/.cache/huggingface/hub"),
              M("openai/whisper-large-v3", 3.1, "hf", "Hugging Face cache", "~/.cache/huggingface/hub")]
    imgs = [dict(name=s[7], id="sha256:%012x" % (i * 7919 + 4099), size="%d MB" % (180 + i * 97), unique="%d MB" % (60 + i * 31), created="%d days ago" % (4 + i * 3),
                 used=1, mb=180 + i * 97, umb=60 + i * 31, dangling=False) for i, s in enumerate(_S) if s[4] == "docker"]
    imgs += [dict(name="python:3.11-slim", id="sha256:0000000a11ce", size="155 MB", unique="155 MB", created="5 weeks ago", used=0, mb=155, umb=155, dangling=False),
             dict(name="<none>", id="sha256:0000000dead0", size="812 MB", unique="640 MB", created="2 weeks ago", used=0, mb=812, umb=640, dangling=True)]
    R = lambda p, br, dirty, ahead, behind, days, subj: dict(
        path="~/projects/" + p, branch=br, remote="git@github.com:you/%s.git" % p, web="https://github.com/you/" + p, dirty=dirty, ahead=ahead, behind=behind,
        tracking=True, lastTs=int(time.time() - days * 86400), last=("1 day ago" if days == 1 else "%d days ago" % days) if days >= 1 else "3 hours ago", subject=subj)
    return dict(ts=int(time.time()), agents=agents, models=models,
                docker=dict(images=imgs, volumes=[dict(name="rag-stack_qdrant_data", size="4.2 GB", used=1, mb=4300), dict(name="rag-stack_pg_data", size="610 MB", used=1, mb=610),
                                                  dict(name="automation_n8n_data", size="88 MB", used=1, mb=88), dict(name="old_experiment_data", size="1.9 GB", used=0, mb=1945)],
                            containers=[dict(name=s[0][3:], image=s[7], state=s[10], status="Up 2 days" if s[10] == "running" else "Exited (0) 3 days ago", size="12 MB",
                                             created="9 days ago", ports=str(s[5] or ""), mounts="1") for s in _S if s[4] == "docker"],
                            totals={"Images": dict(size="6.4GB", reclaim="967MB (15%)", n="12", active="10"), "Containers": dict(size="130MB", reclaim="12MB (9%)", n="10", active="8"),
                                    "Local Volumes": dict(size="6.8GB", reclaim="1.9GB (28%)", n="4", active="3"), "Build Cache": dict(size="2.1GB", reclaim="2.1GB", n="48", active="0")},
                            unusedImages=2, dangling=1, stopped=2),
                tools=dict(editor="VS Code", opener=True, terminal=True, platform="macOS"),
                github=dict(accounts=[dict(login="you", active=True)],
                            repos=[R("rag-stack", "main", 3, 1, 0, 0, "Add retry to the ingest queue"), R("pilothouse", "main", 0, 0, 0, 1, "Docs: architecture diagrams"),
                                   R("n8n-workflows", "feature/slack-digest", 1, 2, 0, 2, "Daily digest to Slack"), R("prompt-library", "main", 0, 0, 4, 6, "Tidy system prompts"),
                                   R("eval-harness", "main", 0, 0, 0, 12, "Score runs by rubric"), R("dotfiles", "main", 2, 0, 0, 30, "zsh: faster prompt")]))


_SESS = [("a1f0", "Claude Code", "Add retry to the ingest queue", "rag-stack", "main", "claude-fable-5-2", 212, 18.42, 0.2),
         ("b2e1", "Claude Code", "Why does the worker crash on start?", "rag-stack", "main", "claude-opus-5-5", 64, 6.10, 0.4),
         ("c3d2", "Claude Code", "Architecture diagrams for the docs", "pilothouse", "main", "claude-fable-5-2", 148, 12.77, 3.0),
         ("d4c3", "Codex", "Port the eval harness to pytest", "eval-harness", "main", "gpt-5.2-codex", 91, 4.36, 7.5),
         ("e5b4", "Claude Code", "Slack digest workflow", "n8n-workflows", "feature/slack-digest", "claude-sonnet-5-5", 77, 1.94, 26.0),
         ("f6a5", "Claude Code", "Tidy the system prompts", "prompt-library", "main", "claude-haiku-5-5", 310, 0.41, 50.0),
         ("0796", "Hermes", "Summarise yesterday's logs", "rag-stack", "main", "qwen3:32b", 44, 0.0, 28.0),
         ("1887", "Claude Code", "Review the retry change", "rag-stack", "main", "claude-opus-5-5", 39, 3.88, 74.0)]
_ZERO = dict(msgs=0, inp=0, out=0, cw=0, cr=0, think=0, web=0, cost=0.0, unpriced=0)


def _tok(msgs, cost):
    return dict(msgs=msgs, inp=msgs * 420, out=msgs * 1150, cw=msgs * 9800, cr=msgs * 96000, think=msgs * 310, web=msgs // 20, cost=round(cost, 2), unpriced=0)


def _add(a, b):
    return {k: (round(a[k] + b[k], 2) if k == "cost" else a[k] + b[k]) for k in _ZERO}


def usage(rng="7d"):
    import datetime
    days_n = {"today": 1, "7d": 7, "30d": 30, "90d": 90, "all": 120}.get(rng, 7); today = datetime.date.today()
    S = [s for s in _SESS if s[8] < days_n * 24]
    fmt = lambda h: (datetime.datetime.now() - datetime.timedelta(hours=h)).strftime("%Y-%m-%d %H:%M")
    sessions = [dict(_tok(s[6], s[7]), id="demo-" + s[0], tool=s[1], title=s[2], cwd="~/projects/" + s[3], branch=s[4], entry="cli", first=fmt(s[8] + 1.5),
                     last=fmt(s[8]), models=[[s[5], s[7]]], subs=2 if s[7] > 10 else 0, subCost=round(s[7] * 0.18, 2) if s[7] > 10 else 0.0, project=s[3]) for s in S]

    def group(key, label):
        g = {}
        for s, row in zip(S, sessions): g[key(s)] = _add(g.get(key(s), _ZERO), row)
        return sorted([dict(v, **{label: k}) for k, v in g.items()], key=lambda x: -x["cost"])
    days = []
    for i in range(min(days_n, 14) - 1, -1, -1):
        c = round(max(0.4, w(i * 1.3, 9, 6, period=1.0, t=i)), 2)
        days.append(dict(_tok(int(c * 14), c), day=(today - datetime.timedelta(days=i)).isoformat()))
    tot = _ZERO
    for d in days: tot = _add(tot, d)
    projects = [dict(p, cwd="~/projects/" + p["name"]) for p in group(lambda s: s[3], "name")]
    return dict(range=rng if rng in ("today", "7d", "30d", "90d", "all") else "7d", since=days[0]["day"], totals=tot,
                parts=dict(inp=round(tot["cost"] * 0.03, 2), out=round(tot["cost"] * 0.34, 2), cw=round(tot["cost"] * 0.38, 2), cr=round(tot["cost"] * 0.25, 2)),
                days=days, models=group(lambda s: s[5], "model"), tools=group(lambda s: s[1], "tool"), projects=projects, sessions=sessions,
                sessionCount=len(sessions), scan=dict(done=128, total=128, running=False, last=int(time.time()) - 40, secs=0.4, files=128), files=128, extraPrices=0,
                prices={"claude-fable-5-2": dict(input=8, output=40, cacheRead=0.2, cacheWrite5m=10.0, cacheWrite1h=16),
                        "claude-opus-5-5": dict(input=4, output=20, cacheRead=0.2, cacheWrite5m=5.0, cacheWrite1h=8),
                        "claude-sonnet-5-5": dict(input=2, output=10, cacheRead=0.1, cacheWrite5m=2.5, cacheWrite1h=4),
                        "claude-haiku-5-5": dict(input=0.1, output=0.5, cacheRead=0.01, cacheWrite5m=0.125, cacheWrite1h=0.2)})


def usage_session(sid):
    s = next((x for x in _SESS if "demo-" + x[0] == sid), None)
    if not s: return dict(error="not found")
    full = lambda m, c: dict(msgs=m, inp=m * 420, out=m * 1150, cw5=m * 2800, cw1=m * 7000, cr=m * 96000, think=m * 310, web=m // 20, cost=round(c, 2))
    hr = time.strftime("%Y-%m-%d %H", time.localtime(time.time() - s[8] * 3600))
    return dict(id=sid, tool=s[1], title=s[2], cwd="~/projects/" + s[3], branch=s[4], entry="cli", first=hr + ":02", last=hr + ":58",
                models=[dict(full(s[6], s[7]), model=s[5])], hours=[dict(full(s[6], s[7]), hour=hr)],
                subs=[dict(full(s[6] // 6, s[7] * 0.18), agent="Explore", first=hr + ":10", last=hr + ":21")] if s[7] > 10 else [],
                log="~/.claude/projects/demo/%s.jsonl" % s[0], priced={s[5]: s[7] > 0})


def usage_now():
    now = time.time(); start = now - 2.3 * 3600; cost = round(21.4 + (now - T0) * 0.002, 2)
    blk = lambda st, c: dict(start=st, end=st + 5 * 3600, last=st + 4.2 * 3600, cost=c, tok=int(c * 2.1e6), msgs=int(c * 11), models=[["claude-fable-5-2", round(c * 0.7, 2)], ["claude-opus-5-5", round(c * 0.3, 2)]])
    L = lambda i, t, proj, st, what, idle, model, ctx, mx, c: dict(
        id="demo-" + i, title=t, cwd="~/projects/" + proj, branch="main", entry="cli", state=st, what=what, idle=idle, model=model, ctx=ctx, ctxMax=mx,
        costToday=c, reqToday=int(c * 12), subs=0, oneShot=False)
    return dict(block=dict(blk(start, cost), last=now - 20, elapsed=2.3 * 3600, left=2.7 * 3600, burn=round(cost / 2.3, 2), tokPerMin=310000, projected=round(cost / 2.3 * 5, 2)),
                blocks=[blk(now - (i * 7 + 2.3) * 3600, round(max(2, w(i, 24, 16, period=1.0, t=i)), 2)) for i in range(1, 13)],
                stats=dict(n=31, max=58.2, typical=22.6, p90=41.0), week=dict(cost=118.4, prev=96.9), today=round(cost + 6.1, 2),
                topSession=dict(id="demo-a1f0", title="Add retry to the ingest queue", cost=18.42),
                budgets=dict(day=40.0, week=250.0, session=25.0, block=30.0), scanning=False,
                live=[L("a1f0", "Add retry to the ingest queue", "rag-stack", "working", "Running tests", 4, "claude-fable-5-2", 412000, 1000000, 18.42),
                      L("b2e1", "Why does the worker crash on start?", "rag-stack", "waiting", "Finished: waiting for you", 310, "claude-opus-5-5", 96000, 200000, 6.10),
                      L("c3d2", "Architecture diagrams for the docs", "pilothouse", "stalled", "No activity for 12 minutes", 720, "claude-fable-5-2", 655000, 1000000, 12.77)])
