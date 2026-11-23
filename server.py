#!/usr/bin/env python3
"""
Local AI Control - a zero-config, auto-detecting local ops dashboard.

Clone it, run `python3 server.py`, open http://localhost:7777. It discovers the
services, AI agents, models and git repos ON WHATEVER HOST IT RUNS ON - nothing
is hardcoded to one machine. Python stdlib only (no pip install), binds to
127.0.0.1, never uses sudo.

  GET  /api/status           auto-discovered services (docker + listening ports)
  GET  /api/metrics          host telemetry + history (macOS & Linux)
  GET  /api/inventory        AI agents, local models, git repos (cached)
  GET  /api/logs?id=&n=200   logs for a service
  GET  /api/inspect?id=      detail for a service
  POST /api/action {id,action}   start|stop|restart (docker / brew only)

Config (optional env): DASH_PORT, DASH_REPOS (colon-sep roots), DASH_HIDE (extra
process-name substrings to hide).
"""
import http.server, socketserver, json, os, re, subprocess, sys, time, threading
from collections import deque, defaultdict

IS_MAC = sys.platform == "darwin"
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + os.environ.get("PATH", "")
PORT = int(os.environ.get("DASH_PORT", "7777"))
HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
UID = os.getuid()


def sh(cmd, t=6):
    try:
        return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=t)
    except Exception:
        return None


def which(b):
    r = sh(["command", "-v", b]) if False else sh("command -v " + b)
    return bool(r and r.stdout.strip())


# ---- recognition hints (generic, not per-machine) ----------------------------
PORT_HINTS = {
    5432: ("PostgreSQL", "Database"), 3306: ("MySQL", "Database"), 6379: ("Redis", "Database"),
    27017: ("MongoDB", "Database"), 5984: ("CouchDB", "Database"), 9200: ("Elasticsearch", "Database"),
    8086: ("InfluxDB", "Database"), 7000: ("Cassandra", "Database"), 11211: ("Memcached", "Database"),
    11434: ("Ollama", "AI / LLM"), 1234: ("LM Studio", "AI / LLM"), 8188: ("ComfyUI", "AI / LLM"),
    7860: ("Gradio / WebUI", "AI / LLM"), 8000: ("Web / API", "Web / Dev"), 8080: ("Web / API", "Web / Dev"),
    3000: ("Web app", "Web / Dev"), 5173: ("Vite dev", "Web / Dev"), 4200: ("Angular dev", "Web / Dev"),
    5678: ("n8n", "Automation"), 5679: ("n8n", "Automation"), 1880: ("Node-RED", "Automation"),
    9090: ("Prometheus", "Infra"), 3100: ("Service", "AI / LLM"), 8787: ("WebUI", "AI / LLM"),
}
AI_PROC = ("ollama", "llama-server", "llama.cpp", "llamacpp", "vllm", "text-generation", "exllama",
           "koboldcpp", "localai", "hermes", "paperclip", "open-webui", "lmstudio", "mlx")
DB_PROC = ("postgres", "mysqld", "mariadb", "redis-server", "mongod", "couchdb", "influxd", "memcached")
WEB_PROC = ("node", "python", "uvicorn", "gunicorn", "flask", "rails", "puma", "php", "deno", "bun", "next", "vite")
INFRA_PROC = ("nginx", "caddy", "traefik", "haproxy", "envoy")
NOISE = ["ssh", "limactl", "com.docker", "docker-pr", "vpnkit", "rapportd", "controlce", "ard",
         "identityservicesd", "systemstats", "sharingd", "code", "cursor", "chrome", "firefox",
         "safari", "edge", "spotify", "slack", "zoom", "onedrive", "dropbox", "google", "microsoft",
         "cloudd", "nsurlsessiond", "trustd", "mdns", "rapport", "python"]  # python handled specially below
NOISE += [x.strip().lower() for x in os.environ.get("DASH_HIDE", "").split(",") if x.strip()]


def classify(pname, cmd, port):
    p = (pname or "").lower()
    c = (cmd or "").lower()
    # process-name signals win (most specific), then AI by name/cmd, then port hint
    if any(k in p for k in DB_PROC):
        grp = "Database"
    elif any(k in p for k in INFRA_PROC):
        grp = "Infra"
    elif any(k in p or k in c for k in AI_PROC):
        grp = "AI / LLM"
    elif port in PORT_HINTS:
        grp = PORT_HINTS[port][1]
    elif any(k in p for k in WEB_PROC):
        grp = "Web / Dev"
    else:
        grp = "Other"
    nm = PORT_HINTS[port][0] if port in PORT_HINTS else None
    if not nm:
        m = re.search(r"/([\w.-]+?)(?:\.(?:js|py|mjs))?\s", c + " ")
        nm = (m.group(1) if m else (pname or "service"))
    return nm, grp


# ---- probes ------------------------------------------------------------------
def listening():
    """-> {port: (pid, pname)} across mac & linux."""
    out = {}
    r = sh(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]) if (IS_MAC or which("lsof")) else None
    if r and r.stdout:
        for line in r.stdout.splitlines()[1:]:
            f = line.split()
            if len(f) >= 9:
                m = re.search(r":(\d+)$", f[8])
                if m:
                    out[int(m.group(1))] = (int(f[1]), f[0])
        return out
    r = sh("ss -ltnpH 2>/dev/null")  # linux fallback
    if r and r.stdout:
        for line in r.stdout.splitlines():
            mp = re.search(r":(\d+)\s", line)
            mpid = re.search(r"pid=(\d+)", line)
            mnm = re.search(r'"([^"]+)"', line)
            if mp:
                out[int(mp.group(1))] = (int(mpid.group(1)) if mpid else 0, mnm.group(1) if mnm else "?")
    return out


def cmd_of(pid):
    if not pid:
        return ""
    r = sh(["ps", "-o", "command=", "-p", str(pid)], 3)
    return (r.stdout.strip() if r else "")[:220]


def pgrep(pat):
    r = sh(["pgrep", "-f", pat])
    return int(r.stdout.split()[0]) if r and r.stdout.split() else None


def uptime(pid):
    if not pid:
        return ""
    r = sh(["ps", "-o", "etime=", "-p", str(pid)], 3)
    return r.stdout.strip() if r else ""


def docker_ps():
    r = sh(["docker", "ps", "--format", "{{json .}}"], 8)
    out = []
    if r and r.stdout:
        for line in r.stdout.splitlines():
            try:
                j = json.loads(line)
            except Exception:
                continue
            labels = j.get("Labels", "")
            proj = ""
            m = re.search(r"com\.docker\.compose\.project=([^,]+)", labels)
            if m:
                proj = m.group(1)
            hp = None
            for seg in (j.get("Ports", "") or "").split(","):
                mm = re.search(r"0\.0\.0\.0:(\d+)->|127\.0\.0\.1:(\d+)->", seg)
                if mm:
                    hp = int(mm.group(1) or mm.group(2)); break
            out.append(dict(name=j.get("Names", ""), image=j.get("Image", ""), status=j.get("Status", ""),
                            project=proj, port=hp, up=not j.get("Status", "").lower().startswith("exited")))
    return out


# ---- service discovery (auto) ------------------------------------------------
def brew_services():
    if not IS_MAC or not which("brew"):
        return {}
    r = sh("brew services list 2>/dev/null")
    out = {}
    if r:
        for line in r.stdout.splitlines()[1:]:
            f = line.split()
            if len(f) >= 2:
                out[f[0]] = f[1]  # name -> status
    return out


GROUP_ORDER = ["AI / LLM", "Database", "Automation", "Web / Dev", "Infra", "Other"]


def discover_services():
    ports = listening()
    dps = docker_ps()
    brew = brew_services()
    items = []
    # docker containers
    for c in dps:
        grp = "Docker · " + (c["project"] or "containers")
        url = "http://localhost:%d" % c["port"] if c["port"] else None
        items.append(dict(id="dk:" + c["name"], name=c["name"], group=grp, kind="docker", docker=c["name"],
                          url=url, where=c["image"], port=c["port"], note=None,
                          actions=["restart", "stop", "start"], has_logs=True,
                          up=c["up"], pid=None, uptime=c["status"]))
    # host listening ports, grouped by pid
    bypid = defaultdict(list)
    for port, (pid, pname) in ports.items():
        bypid[(pid, pname)].append(port)
    for (pid, pname), plist in bypid.items():
        pl = pname.lower()
        if any(pl.startswith(n) or n in pl for n in NOISE if n != "python"):
            continue
        cmd = cmd_of(pid)
        if "python" in pl and not any(k in cmd.lower() for k in AI_PROC + DB_PROC + ("server", "uvicorn", "gunicorn", "flask", "http")):
            if PORT not in plist:  # keep our own dashboard out, keep real python servers
                continue
        if PORT in plist:  # the dashboard itself
            continue
        primary = sorted([p for p in plist if p in PORT_HINTS] or plist)[0]
        nm, grp = classify(pname, cmd, primary)
        # brew-managed? enable control
        acts, brewname = [], None
        for bn in brew:
            if bn.split("@")[0] in pl or pl in bn:
                brewname, acts = bn, ["restart", "stop", "start"]; break
        items.append(dict(id="host:%d" % primary, name=nm, group=grp, kind="brew" if brewname else "host",
                          brew=brewname, url=("http://localhost:%d" % primary if grp in ("AI / LLM", "Web / Dev", "Automation") else None),
                          where="%s · pid %d" % (pname, pid), port=primary, ports=sorted(plist), note=None,
                          actions=acts, has_logs=False, up=True, pid=pid, uptime=uptime(pid)))
    # order groups: AI first, then known, then docker projects, then other
    def gkey(g):
        if g in GROUP_ORDER:
            return (GROUP_ORDER.index(g), g)
        if g.startswith("Docker"):
            return (len(GROUP_ORDER), g)
        return (len(GROUP_ORDER) + 1, g)
    items.sort(key=lambda s: (gkey(s["group"]), s["name"].lower()))
    return items


BY_ID = {}


def established():
    """local TCP connections -> (pid, local_port, remote_port)."""
    out = []
    r = sh(["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED"]) if (IS_MAC or which("lsof")) else sh("ss -tnpH state established 2>/dev/null")
    if not r:
        return out
    for line in r.stdout.splitlines():
        if "127.0.0.1" not in line and "[::1]" not in line and "localhost" not in line:
            continue
        m = re.search(r":(\d+)->(?:127\.0\.0\.1|\[::1\]|localhost):(\d+)", line)
        if not m:
            continue
        pid = 0
        if IS_MAC:
            f = line.split()
            if len(f) > 1 and f[1].isdigit():
                pid = int(f[1])
        else:
            mp = re.search(r"pid=(\d+)", line)
            if mp:
                pid = int(mp.group(1))
        out.append((pid, int(m.group(1)), int(m.group(2))))
    return out


_ddet = dict(ts=0, data=[])


def docker_details():
    """per-container {name, networks, env} - cached (these rarely change)."""
    if _ddet["data"] and time.time() - _ddet["ts"] < 30:
        return _ddet["data"]
    names = [c["name"] for c in docker_ps()]
    data = []
    if names:
        r = sh(["docker", "inspect"] + names, 12)
        if r and r.stdout:
            try:
                for j in json.loads(r.stdout):
                    data.append(dict(
                        name=j["Name"].lstrip("/"),
                        networks=list((j.get("NetworkSettings", {}).get("Networks") or {}).keys()),
                        env=[e for e in (j.get("Config", {}).get("Env") or []) if not re.search(r"KEY|SECRET|TOKEN|PASS|JWT|CRYPT", e, re.I)]))
            except Exception:
                pass
    _ddet.update(ts=time.time(), data=data)
    return data


def discover_edges(svcs):
    port2id, pid2id, name2id = {}, {}, {}
    for s in svcs:
        if s.get("pid"):
            pid2id[s["pid"]] = s["id"]
        if s.get("docker"):
            name2id[s["docker"]] = s["id"]
        for p in (s.get("ports") or ([s["port"]] if s.get("port") else [])):
            port2id.setdefault(p, s["id"])
    edges = set()
    # 1. live established TCP connections between services
    for pid, lport, rport in established():
        if rport in port2id:
            src = pid2id.get(pid) or port2id.get(lport)
            dst = port2id[rport]
            if src and dst and src != dst:
                edges.add((src, dst))
    # 2. docker containers: references to other services in their env (DATABASE_URL, *_URL, host:port)
    for c in docker_details():
        sid = name2id.get(c["name"])
        if not sid:
            continue
        for e in c["env"]:
            val = e.split("=", 1)[1] if "=" in e else e
            for host, p in re.findall(r"(?://|@|\b)([A-Za-z0-9][\w.-]+):(\d+)", val):
                dst = name2id.get(host) or (port2id.get(int(p)) if host in ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal") else None)
                if dst and dst != sid:
                    edges.add((sid, dst))
            for host in re.findall(r"//([A-Za-z0-9][\w.-]+)", val):
                if host in name2id and name2id[host] != sid:
                    edges.add((sid, name2id[host]))
    return [list(e) for e in sorted(edges)][:80]


def status():
    svcs = discover_services()
    global BY_ID
    BY_ID = {s["id"]: s for s in svcs}
    return dict(ts=int(time.time()), services=svcs, edges=discover_edges(svcs))


# ---- metrics (cross-platform) ------------------------------------------------
HIST = 90
M = {k: deque(maxlen=HIST) for k in ("cpu", "mem", "gpu", "rx", "tx", "load", "disk")}
SNAP = dict(ts=0, cpu=0, cpuUser=0, cpuSys=0, load=[0, 0, 0], memUsedGB=0, memTotalGB=0, memPct=0,
            swapGB=0, gpu=0, rxKBs=0, txKBs=0, disk=[], procs=[], docker=[], llm=[])
_net = dict(ts=0, rx=0, tx=0)
_cpu_prev = None
_dock = dict(ts=0, data=[])


def cpu_stat():
    global _cpu_prev
    if IS_MAC:
        r = sh(["top", "-l", "2", "-n", "0"], 5)
        if r:
            m = re.search(r"CPU usage:\s*([\d.]+)% user,\s*([\d.]+)% sys", r.stdout.split("Processes:")[-1])
            if m:
                u, s = float(m.group(1)), float(m.group(2))
                return round(u + s, 1), round(u, 1), round(s, 1)
        return 0, 0, 0
    try:  # linux /proc/stat
        def snap():
            with open("/proc/stat") as f:
                p = f.readline().split()[1:]
            v = list(map(int, p[:7]))
            idle = v[3] + v[4]; total = sum(v); return total, idle, v[0] + v[1], v[2]
        t1, i1, u1, s1 = snap(); time.sleep(0.2); t2, i2, u2, s2 = snap()
        dt = (t2 - t1) or 1
        return round((1 - (i2 - i1) / dt) * 100, 1), round((u2 - u1) / dt * 100, 1), round((s2 - s1) / dt * 100, 1)
    except Exception:
        return 0, 0, 0


def mem_stat():
    if IS_MAC:
        total = int((sh(["sysctl", "-n", "hw.memsize"]) or type("", (), {"stdout": "0"})).stdout or 0)
        r = sh(["vm_stat"]); used = 0
        if r and total:
            pg = 4096
            mm = re.search(r"page size of (\d+)", r.stdout)
            if mm: pg = int(mm.group(1))
            d = {}
            for line in r.stdout.splitlines():
                g = re.match(r'"?([\w ]+?)"?:\s+(\d+)\.', line)
                if g: d[g.group(1).strip()] = int(g.group(2))
            used = (d.get("Pages active", 0) + d.get("Pages wired down", 0) + d.get("Pages occupied by compressor", 0)) * pg
        return used / 1e9, total / 1e9
    try:
        d = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":"); d[k] = int(v.split()[0]) * 1024
        total = d["MemTotal"]; avail = d.get("MemAvailable", d.get("MemFree", 0))
        return (total - avail) / 1e9, total / 1e9
    except Exception:
        return 0, 0


def swap_gb():
    if IS_MAC:
        r = sh(["sysctl", "-n", "vm.swapusage"])
        if r:
            m = re.search(r"used = ([\d.]+)M", r.stdout)
            if m: return round(float(m.group(1)) / 1024, 2)
    else:
        try:
            for line in open("/proc/meminfo"):
                if line.startswith("SwapTotal"): tot = int(line.split()[1])
                if line.startswith("SwapFree"): fr = int(line.split()[1])
            return round((tot - fr) / 1e6, 2)
        except Exception:
            pass
    return 0


def gpu_util():
    if IS_MAC:
        r = sh("ioreg -r -d 1 -c IOAccelerator 2>/dev/null | grep -i 'Device Utilization' | head -1")
        if r:
            m = re.search(r'Device Utilization %"?\s*=\s*(\d+)', r.stdout)
            if m: return int(m.group(1))
    elif which("nvidia-smi"):
        r = sh("nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null")
        if r and r.stdout.strip():
            try: return int(r.stdout.strip().splitlines()[0])
            except Exception: pass
    return 0


def net_rates():
    global _net
    rx = tx = 0
    if IS_MAC:
        r = sh(["netstat", "-ib"])
        if r:
            seen = set()
            for line in r.stdout.splitlines()[1:]:
                f = line.split()
                if len(f) >= 10 and f[0] not in seen and not f[0].startswith("lo"):
                    seen.add(f[0])
                    try: rx += int(f[6]); tx += int(f[9])
                    except Exception: pass
    else:
        try:
            for line in open("/proc/net/dev").readlines()[2:]:
                name, data = line.split(":");
                if "lo" in name: continue
                d = data.split(); rx += int(d[0]); tx += int(d[8])
        except Exception:
            pass
    now = time.time(); rk = tk = 0
    if _net["ts"]:
        dt = now - _net["ts"] or 1
        rk = max(0, (rx - _net["rx"]) / dt / 1024); tk = max(0, (tx - _net["tx"]) / dt / 1024)
    _net = dict(ts=now, rx=rx, tx=tx)
    return round(rk, 1), round(tk, 1)


def disk_use():
    import shutil
    out, seen = [], set()
    for mnt in (["/", "/System/Volumes/Data"] if IS_MAC else ["/"]):
        try:
            u = shutil.disk_usage(mnt)
            if u.total not in seen:
                seen.add(u.total)
                out.append(dict(mount=mnt, pct=round(u.used / u.total * 100, 1), usedGB=round(u.used / 1e9), totalGB=round(u.total / 1e9)))
        except Exception:
            pass
    return out


def top_procs():
    r = sh(["ps", "-axo", "pid,comm,%cpu,rss"] if IS_MAC else ["ps", "-eo", "pid,comm,%cpu,rss"])
    rows = []
    if r:
        for line in r.stdout.splitlines()[1:]:
            f = line.split(None, 3)
            if len(f) >= 4:
                try: rows.append(dict(pid=int(f[0]), name=os.path.basename(f[1])[:28], cpu=float(f[2]), rssMB=round(int(f[3]) / 1024)))
                except Exception: pass
    rows.sort(key=lambda x: x["cpu"], reverse=True)
    return rows[:12]


def docker_stats():
    global _dock
    if time.time() - _dock["ts"] < 6:
        return _dock["data"]
    r = sh(["docker", "stats", "--no-stream", "--format", "{{json .}}"], 10)
    data = []
    if r:
        for line in r.stdout.splitlines():
            try:
                j = json.loads(line)
                data.append(dict(name=j.get("Name", ""), cpu=j.get("CPUPerc", ""), mem=j.get("MemUsage", ""), memPct=j.get("MemPerc", ""), net=j.get("NetIO", "")))
            except Exception: pass
    _dock = dict(ts=time.time(), data=data)
    return data


def llm_loaded():
    if not which("ollama"):
        return []
    r = sh(["ollama", "ps"]); out = []
    if r:
        for line in r.stdout.splitlines()[1:]:
            f = line.split()
            if len(f) >= 3:
                out.append(dict(name=f[0], size=f[2] + (" " + f[3] if len(f) > 3 and f[3] in ("GB", "MB") else ""),
                                proc=" ".join(f[4:6]) if len(f) > 5 else "", until=" ".join(f[-2:])))
    return out


def sampler():
    while True:
        try:
            cpu, cu, cs = cpu_stat(); used, total = mem_stat(); gpu = gpu_util(); rk, tk = net_rates()
            ld = list(os.getloadavg()); dsk = disk_use(); mp = round(used / total * 100, 1) if total else 0
            M["cpu"].append(cpu); M["mem"].append(mp); M["gpu"].append(gpu); M["rx"].append(rk); M["tx"].append(tk)
            M["load"].append(round(ld[0], 2)); M["disk"].append(dsk[0]["pct"] if dsk else 0)
            SNAP.update(ts=int(time.time()), cpu=cpu, cpuUser=cu, cpuSys=cs, load=ld, memUsedGB=round(used, 1),
                        memTotalGB=round(total, 1), memPct=mp, swapGB=swap_gb(), gpu=gpu, rxKBs=rk, txKBs=tk,
                        disk=dsk, procs=top_procs(), docker=docker_stats(), llm=llm_loaded())
        except Exception:
            pass
        time.sleep(2)


def metrics():
    d = dict(SNAP); d["hist"] = {k: list(v) for k, v in M.items()}; d["platform"] = "macOS" if IS_MAC else "Linux"
    return d


# ---- inventory (agents / models / repos) -------------------------------------
_inv = dict(ts=0, data=None)
# (id, name, bins, home dirs, pgrep pattern, description, default http port)
AGENT_DEFS = [
    ("claude", "Claude Code", ["claude"], ["~/.claude"], r"claude-code|Claude\.app|claude --", "Anthropic agent CLI + desktop", None),
    ("codex", "Codex (OpenAI)", ["codex"], ["~/.codex"], r"\bcodex\b", "OpenAI coding agent CLI", None),
    ("openclaw", "OpenClaw", ["openclaw"], ["~/.openclaw"], r"openclaw", "Self-hosted AI assistant", None),
    ("aider", "Aider", ["aider"], ["~/.aider", "~/.aider.conf.yml"], r"\baider\b", "AI pair programmer", None),
    ("ollama", "Ollama", ["ollama"], ["~/.ollama"], r"ollama serve", "Local LLM runtime", 11434),
    ("llm", "llm (Datasette)", ["llm"], ["~/Library/Application Support/io.datasette.llm", "~/.config/io.datasette.llm"], r"\bllm\b", "CLI for many LLMs", None),
    ("interpreter", "Open Interpreter", ["interpreter"], ["~/.config/open-interpreter"], r"open.interpreter|interpreter", "Natural-language code exec", None),
    ("goose", "Goose", ["goose"], ["~/.config/goose"], r"\bgoose\b", "Block AI agent", None),
    ("hermes", "Hermes Agent", [], ["~/.hermes"], r"hermes_cli", "Nous local agent + WebUI", 8787),
    ("paperclip", "Paperclip", [], ["~/.paperclip"], r"paperclipai", "Local agent platform", 3100),
    ("lmstudio", "LM Studio", ["lms"], ["~/.lmstudio", "~/.cache/lm-studio"], r"LM Studio|lm-studio", "Local model desktop app", 1234),
    ("jan", "Jan", [], ["~/jan", "~/.jan"], r"\bjan\b", "Local AI desktop app", None),
    ("gpt4all", "GPT4All", ["gpt4all"], ["~/.cache/gpt4all"], r"gpt4all", "Local model app", None),
    ("cursor", "Cursor", [], ["~/.cursor"], r"Cursor Agent", "AI code editor", None),
]
MODEL_DIRS = ["~/.ollama/models", "~/models", "~/.cache/lm-studio/models", "~/.lmstudio/models",
              "~/.cache/huggingface/hub", "~/.hermes/models", "~/.local/share/models"]
REPO_ROOTS = ["~/Desktop", "~/Documents", "~/Projects", "~/projects", "~/dev", "~/Developer",
              "~/code", "~/src", "~/git", "~/repos", "~/workspace", "~/go/src"]
REPO_ROOTS += [p for p in os.environ.get("DASH_REPOS", "").split(":") if p.strip()]


def web_url(remote):
    if not remote:
        return None
    r = remote.strip()
    if r.startswith("git@"):
        r = "https://" + r[4:].replace(":", "/", 1)
    if r.endswith(".git"):
        r = r[:-4]
    return r if r.startswith("http") else None


def inv_agents():
    out = []
    for aid, name, bins, homes, pat, desc, port in AGENT_DEFS:
        installed = any(which(b) for b in bins) or any(os.path.exists(os.path.expanduser(h)) for h in homes)
        if not installed:
            continue
        home = next((h for h in homes if os.path.exists(os.path.expanduser(h))), homes[0] if homes else "")
        url = "http://localhost:%d" % port if (port and port in listening()) else None
        out.append(dict(id=aid, name=name, desc=desc, url=url, home=home, installed=True, running=pgrep(pat) is not None))
    return out


def inv_models():
    out, loaded = [], {m["name"] for m in llm_loaded()}
    if which("ollama"):
        r = sh(["ollama", "list"])
        if r:
            for line in r.stdout.splitlines()[1:]:
                f = line.split()
                if len(f) >= 3:
                    size = f[2] + " " + f[3] if len(f) > 3 and f[3] in ("GB", "MB", "KB") else f[2]
                    out.append(dict(name=f[0], size=size, loaded=f[0] in loaded, runtime="Ollama",
                                    link="https://ollama.com/library/" + f[0].split(":")[0]))
    seen = {m["name"] for m in out}
    for d in MODEL_DIRS:
        dd = os.path.expanduser(d)
        if not os.path.isdir(dd):
            continue
        r = sh('find "%s" -maxdepth 3 -iname "*.gguf" 2>/dev/null | head -40' % dd)
        for g in (r.stdout.split("\n") if r else []):
            if g.strip():
                nm = os.path.basename(g)
                if nm in seen: continue
                seen.add(nm)
                try: sz = "%.0f GB" % (os.path.getsize(g) / 1e9)
                except Exception: sz = ""
                out.append(dict(name=nm, size=sz, loaded=False, runtime="gguf · " + os.path.dirname(g).replace(HOME, "~"), link=None))
    return out


def inv_repos():
    repos, seen = [], set()
    roots = [os.path.expanduser(r) for r in REPO_ROOTS if os.path.isdir(os.path.expanduser(r))]
    if roots:
        r = sh('for b in %s; do find "$b" -maxdepth 4 -name .git -type d 2>/dev/null; done'
               % " ".join('"%s"' % x for x in roots), 20)
        for gd in sorted(set((r.stdout.split("\n") if r else []))):
            if not gd.strip():
                continue
            d = os.path.dirname(gd)
            if d in seen or len(repos) >= 120:
                continue
            seen.add(d)
            br = sh('git -C "%s" branch --show-current' % d, 4)
            rm = sh('git -C "%s" remote get-url origin' % d, 4)
            remote = rm.stdout.strip() if rm else ""
            repos.append(dict(path=d.replace(HOME, "~"), branch=(br.stdout.strip() if br else "") or "detached",
                              remote=remote, web=web_url(remote)))
    return repos


def inventory():
    if _inv["data"] and time.time() - _inv["ts"] < 60:
        return _inv["data"]
    accounts = []
    if which("gh"):
        r = sh("gh auth status")
        if r:
            for m in re.finditer(r"account (\S+).*?Active account: (true|false)", r.stdout + r.stderr, re.S):
                accounts.append(dict(login=m.group(1), active=m.group(2) == "true"))
    data = dict(ts=int(time.time()), agents=inv_agents(), models=inv_models(),
                github=dict(accounts=accounts, repos=inv_repos()))
    _inv.update(ts=time.time(), data=data)
    return data


# ---- logs / inspect / action -------------------------------------------------
def svc(sid):
    return BY_ID.get(sid) or next((s for s in discover_services() if s["id"] == sid), None)


def logs(sid, n=200):
    s = svc(sid)
    if not s:
        return dict(id=sid, lines=["(service not found)"])
    if s.get("docker"):
        r = sh(["docker", "logs", "--tail", str(n), s["docker"]], 10)
        txt = (r.stdout + r.stderr) if r else "(docker logs failed)"
        return dict(id=sid, name=s["name"], lines=txt.splitlines()[-n:])
    return dict(id=sid, name=s["name"], lines=["No log source for '%s'." % s["name"],
                "Docker containers expose logs; host processes log to their own files or the system log."])


def inspect(sid):
    s = svc(sid)
    if not s:
        return dict(error="not found")
    d = dict(id=sid, name=s["name"], group=s["group"], url=s.get("url"), where=s.get("where"), kind=s.get("kind"))
    if s.get("docker"):
        r = sh(["docker", "inspect", s["docker"]], 8)
        if r and r.stdout:
            try:
                j = json.loads(r.stdout)[0]
                d.update(image=j["Config"]["Image"], state=j["State"].get("Status"), started=j["State"].get("StartedAt", "")[:19],
                         restarts=j.get("RestartCount"), exitCode=j["State"].get("ExitCode"),
                         ports=list((j.get("NetworkSettings", {}).get("Ports") or {}).keys()),
                         env=[e for e in (j["Config"].get("Env") or []) if not re.search(r"(KEY|SECRET|TOKEN|PASS)", e, re.I)][:14])
            except Exception as e:
                d["error"] = str(e)
    else:
        d.update(pid=s.get("pid"), ports=s.get("ports"), uptime=s.get("uptime"))
        if s.get("pid"):
            r = sh(["ps", "-o", "etime=,%cpu=,%mem=,command=", "-p", str(s["pid"])], 4)
            if r: d["ps"] = r.stdout.strip()[:300]
    return d


def model_action(name, act):
    if act != "stop" or not which("ollama") or not re.match(r"^[\w.:@/-]+$", name or ""):
        return False, "unsupported"
    r = sh(["ollama", "stop", name], 30)
    return (r.returncode == 0, (r.stdout + r.stderr).strip() or "unloaded " + name) if r else (False, "failed")


def do_action(sid, act):
    s = svc(sid)
    if not s or act not in (s.get("actions") or []):
        return False, "action not available"
    if s.get("docker"):
        cmd = ["docker", act, s["docker"]]
    elif s.get("brew"):
        cmd = ["brew", "services", act, s["brew"]]
    else:
        return False, "no controller for this service"
    r = sh(cmd, 90)
    if r is None:
        return False, "command failed"
    ok = r.returncode == 0
    return ok, (r.stdout + r.stderr).strip() or (act + "ed" if ok else "failed")


# ---- http --------------------------------------------------------------------
class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

    def _q(self):
        from urllib.parse import urlparse, parse_qs
        return parse_qs(urlparse(self.path).query)

    def do_GET(self):
        p = self.path
        if p.startswith("/api/status"): self._send(200, json.dumps(status()))
        elif p.startswith("/api/metrics"): self._send(200, json.dumps(metrics()))
        elif p.startswith("/api/inventory"): self._send(200, json.dumps(inventory()))
        elif p.startswith("/api/logs"):
            q = self._q(); self._send(200, json.dumps(logs(q.get("id", [""])[0], int(q.get("n", ["200"])[0]))))
        elif p.startswith("/api/inspect"): self._send(200, json.dumps(inspect(self._q().get("id", [""])[0])))
        else:
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f: self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError: self._send(404, b"index.html not found")

    def do_POST(self):
        if self.path.startswith("/api/action"):
            n = int(self.headers.get("Content-Length", 0))
            try: body = json.loads(self.rfile.read(n) or b"{}")
            except Exception: body = {}
            ok, msg = do_action(body.get("id", ""), body.get("action", "restart"))
            self._send(200 if ok else 500, json.dumps(dict(ok=ok, msg=msg)))
        elif self.path.startswith("/api/model"):
            n = int(self.headers.get("Content-Length", 0))
            try: body = json.loads(self.rfile.read(n) or b"{}")
            except Exception: body = {}
            ok, msg = model_action(body.get("name", ""), body.get("action", "stop"))
            self._send(200 if ok else 500, json.dumps(dict(ok=ok, msg=msg)))
        else:
            self._send(404, b"not found")

    def log_message(self, *a): pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    threading.Thread(target=sampler, daemon=True).start()
    print("Local AI Control (%s) -> http://127.0.0.1:%d" % ("macOS" if IS_MAC else "Linux", PORT))
    with Server(("127.0.0.1", PORT), H) as httpd:
        try: httpd.serve_forever()
        except KeyboardInterrupt: print("\nbye")
