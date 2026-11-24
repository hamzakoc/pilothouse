#!/usr/bin/env python3
"""
Pilothouse - one local page to see and steer your AI stack. Zero-config, auto-detecting.

Clone it, run `python3 server.py` (or `python3 -m pilothouse`), open http://localhost:7777. It discovers the
services, AI agents, models and git repos ON WHATEVER HOST IT RUNS ON - nothing
is hardcoded to one machine. Python stdlib only (no pip install), binds to
127.0.0.1, never uses sudo.

  GET  /api/status           auto-discovered services (docker + listening ports)
  GET  /api/metrics          host telemetry + history (macOS & Linux)
  GET  /api/inventory        AI agents, local models, git repos (cached)
  GET  /api/logs?id=&n=200   logs for a service
  GET  /api/inspect?id=      detail for a service
  GET  /api/modelinfo?name=  details for an Ollama model
  POST /api/action {id,action}   start|stop|restart|pause|unpause|kill for a service
  POST /api/group  {group,action} same, for every container of a compose project
  POST /api/proc   {pid,force}    signal one of YOUR processes
  POST /api/app    {name}         quit every process of an app (yours only)
  POST /api/model  {name,action}  load|stop|delete an Ollama model
  POST /api/docker {action}       prune-images | prune-cache
POSTs need the X-Dash header and a local Origin/Host (CSRF / DNS-rebinding guard).

  GET  /api/history?range=1h|6h|24h|48h[&id=]   persisted history (host or one service)
  GET  /api/since?ts=        what happened since a moment (events, peaks)

Config (optional env): DASH_PORT, DASH_REPOS (colon-sep roots), DASH_HIDE (extra
process-name substrings to hide), DASH_DATA (history folder, default
~/.pilothouse), DASH_ALERT_MEM / DASH_ALERT_DISK / DASH_ALERT_CPU (percent).
"""
import http.server, socketserver, json, os, re, shutil, signal, sqlite3, subprocess, sys, time, threading
from collections import deque, defaultdict

IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")
PLATFORM = "macOS" if IS_MAC else "Windows" if IS_WIN else "Linux"
if not IS_WIN:   # services started by launchd/systemd get a bare PATH: add the usual tool folders
    os.environ["PATH"] = ":".join(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
                                   os.environ.get("PATH", "")] + [os.path.expanduser(d) for d in (
                                       "~/.local/bin", "~/bin", "~/.npm-global/bin", "~/.cargo/bin", "~/.bun/bin")])
PORT = int(os.environ.get("DASH_PORT", "7777"))
HERE = os.path.dirname(os.path.abspath(__file__))   # the package folder (holds web/index.html)
ROOT = os.path.dirname(HERE)                         # the clone: optional agents.json / pricing.json live here
HOME = os.path.expanduser("~")
UID = os.getuid() if hasattr(os, "getuid") else 0   # Windows has no numeric user id
# Where history, budgets and your own settings live. DASH_DATA overrides it. Installs from before the project was
# named Pilothouse used ~/.local-ai-control: that folder keeps working and is renamed once at startup (migrate_data).
_LEGACY_DATA = os.path.join(HOME, ".local-ai-control")
DATA_DIR = os.environ.get("DASH_DATA") or (
    _LEGACY_DATA if os.path.isdir(_LEGACY_DATA) and not os.path.isdir(os.path.join(HOME, ".pilothouse")) else os.path.join(HOME, ".pilothouse"))


_hung = {}   # program -> when it last timed out


def sh(cmd, t=6, force=False):
    """run a command, never raise: None when it is missing or times out (every caller treats that as 'no data').
    A docker CLI whose daemon is gone (the VM was quit) hangs instead of failing: after one timeout it is left
    alone for 30 s, so a dead Docker cannot stall every refresh."""
    prog = cmd[0] if isinstance(cmd, list) and cmd else None
    if prog == "docker" and not force and time.time() - _hung.get(prog, 0) < 30:
        return None
    try:
        return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=t, errors="replace",
                              encoding="utf-8" if IS_WIN else None,
                              creationflags=0x08000000 if IS_WIN else 0)   # Windows: no console window per call
    except subprocess.TimeoutExpired:
        _hung[prog] = time.time()
        return None
    except Exception:
        return None


def which(b):
    return bool(shutil.which(b))


def fmt_etime(sec):
    """seconds -> the [DD-]HH:MM:SS form `ps` prints, so every OS reports uptime the same way."""
    sec = max(0, int(sec)); d, r = divmod(sec, 86400); h, r = divmod(r, 3600); m, x = divmod(r, 60)
    return ("%02d-" % d if d else "") + ("%02d:" % h if d or h else "") + "%02d:%02d" % (m, x)


def walk(root, depth, skip=(), limit=300000):
    """bounded directory walk that works on every OS (replaces `find`): yields (path, entry, is_dir).
    Never follows links, never enters a folder named in `skip`, stops after `limit` entries."""
    stack, n = [(root, 0)], 0
    while stack:
        d, lv = stack.pop()
        try:
            ents = list(os.scandir(d))
        except OSError:
            continue
        for e in ents:
            n += 1
            if n > limit:
                return
            try:
                isdir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            yield e.path, e, isdir
            if isdir and lv + 1 < depth and e.name not in skip:
                stack.append((e.path, lv + 1))


def dir_size(path, limit=150000):
    """bytes under a folder (replaces `du`); links are not followed so shared blobs count once."""
    total = 0
    for _, e, isdir in walk(path, 12, limit=limit):
        if not isdir:
            try:
                total += e.stat(follow_symlinks=False).st_size
            except OSError:
                pass
    return total


# ---- recognition hints (generic, not per-machine) ----------------------------
PORT_HINTS = {
    5432: ("PostgreSQL", "Database"), 3306: ("MySQL", "Database"), 6379: ("Redis", "Database"),
    27017: ("MongoDB", "Database"), 5984: ("CouchDB", "Database"), 9200: ("Elasticsearch", "Database"),
    8086: ("InfluxDB", "Database"), 7000: ("Cassandra", "Database"), 11211: ("Memcached", "Database"),
    11434: ("Ollama", "AI / LLM"), 1234: ("LM Studio", "AI / LLM"), 8188: ("ComfyUI", "AI / LLM"),
    7860: ("Gradio / WebUI", "AI / LLM"), 8000: ("Web / API", "Web / Dev"), 8080: ("Web / API", "Web / Dev"),
    3000: ("Web app", "Web / Dev"), 5173: ("Vite dev", "Web / Dev"), 4200: ("Angular dev", "Web / Dev"),
    5678: ("n8n", "Automation"), 5679: ("n8n", "Automation"), 1880: ("Node-RED", "Automation"),
    9090: ("Prometheus", "Infra"), 3100: ("Service", "AI / LLM"), 58848: ("Hermes llama.cpp", "AI / LLM"), 8787: ("Hermes WebUI", "AI / LLM"),
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


# ---- probes ------------------------------------------------------------------
# macOS asks `ps` and `lsof`. Linux reads /proc directly (works in minimal images with no procps, lsof or ss).
# Windows asks PowerShell and netstat, which ship with the OS. All three end up in the same shapes.
_snap = dict(ts=0, rows=[], prev={})


def linux_procs():
    hz = os.sysconf("SC_CLK_TCK"); page = os.sysconf("SC_PAGE_SIZE")
    with open("/proc/uptime") as f:
        boot = time.time() - float(f.read().split()[0])
    out = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name) as f:
                st = f.read()
            r = st.rindex(")"); f2 = st[r + 2:].split()
            with open("/proc/%s/cmdline" % name, "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            try:
                exe = os.readlink("/proc/%s/exe" % name)
            except OSError:
                exe = cmd.split(" ")[0] if cmd else "[%s]" % st[st.index("(") + 1:r]
            swap = 0
            try:
                with open("/proc/%s/status" % name) as f:
                    for line in f:
                        if line.startswith("VmSwap"):
                            swap = int(line.split()[1]) / 1024; break
            except OSError:
                pass
            res = int(f2[21]) * page / 1048576
            out.append(dict(pid=int(name), ppid=int(f2[1]), uid=os.stat("/proc/" + name).st_uid, cput=(int(f2[11]) + int(f2[12])) / hz,
                            start=boot + int(f2[19]) / hz, res=res, rss=res + swap, comm=exe, cmd=cmd or exe))
        except (OSError, ValueError, IndexError):
            continue   # the process ended while we were reading it
    return out


WIN_PS = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,WorkingSetSize,KernelModeTime,UserModeTime,"
          "@{n='Start';e={if($_.CreationDate){[int64](($_.CreationDate.ToUniversalTime()-[datetime]'1970-01-01').TotalSeconds)}else{0}}},"
          "ExecutablePath,CommandLine | ConvertTo-Json -Compress")


def parse_win_procs(txt):
    try:
        j = json.loads(txt)
    except Exception:
        return []
    out = []
    for q in (j if isinstance(j, list) else [j]):
        try:
            out.append(dict(pid=int(q["ProcessId"]), ppid=int(q.get("ParentProcessId") or 0), uid=0,
                            cput=((q.get("KernelModeTime") or 0) + (q.get("UserModeTime") or 0)) / 1e7, start=q.get("Start") or 0,
                            res=(q.get("WorkingSetSize") or 0) / 1048576, rss=(q.get("WorkingSetSize") or 0) / 1048576,
                            comm=q.get("ExecutablePath") or q.get("Name") or "?", cmd=q.get("CommandLine") or q.get("Name") or ""))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def snapshot():
    """Linux/Windows process list with CPU% worked out from the CPU time each process gained since the last look."""
    now = time.time()
    if now - _snap["ts"] < (4 if IS_WIN else 1.5):
        return _snap["rows"]
    if IS_WIN:
        r = sh(["powershell", "-NoProfile", "-NonInteractive", "-Command", WIN_PS], 25)
        rows = parse_win_procs(r.stdout if r else "")
    else:
        rows = linux_procs()
    prev, cur = _snap["prev"], {}
    for q in rows:
        old = prev.get(q["pid"])
        q["cpu"] = round(max(0.0, (q["cput"] - old[0]) / max(0.2, now - old[1]) * 100), 1) if old and q["cput"] >= old[0] else 0.0
        q["etime"] = fmt_etime(now - q["start"]) if q["start"] else ""
        cur[q["pid"]] = (q["cput"], now)
    if rows:
        _snap.update(ts=now, rows=rows, prev=cur)
    return _snap["rows"]


def base_name(comm):
    n = os.path.basename(comm.replace("\\", "/"))
    return n[:-4] if n.lower().endswith(".exe") else n


_lsock = dict(ts=0, listen={}, est=[], pids={})
LOOPBACK = ("0100007F", "00000000000000000000000001000000", "0000000000000000FFFF00000100007F")


def linux_sockets():
    """listening ports and loopback connections from /proc/net/tcp{,6}; the owning pid comes from matching each
    socket's inode against /proc/<pid>/fd (only processes we are allowed to look into)."""
    if time.time() - _lsock["ts"] < 3:
        return _lsock
    listen, est = {}, []
    for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(fn) as f:
                lines = f.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f2 = line.split()
            if len(f2) < 10:
                continue
            lport = int(f2[1].rsplit(":", 1)[1], 16); rip, rport = f2[2].rsplit(":", 1)
            if f2[3] == "0A":
                listen[lport] = f2[9]
            elif f2[3] == "01" and rip in LOOPBACK:
                est.append((lport, int(rport, 16), f2[9]))
    want = set(listen.values()) | {e[2] for e in est}; pids = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            for fd in os.listdir("/proc/%s/fd" % name):
                try:
                    t = os.readlink("/proc/%s/fd/%s" % (name, fd))
                except OSError:
                    continue
                if t.startswith("socket:[") and t[8:-1] in want:
                    pids.setdefault(t[8:-1], int(name))
        except OSError:
            continue
    _lsock.update(ts=time.time(), listen=listen, est=est, pids=pids)
    return _lsock


def parse_netstat(txt):
    """Windows `netstat -ano`: -> ({listening port: pid}, [(pid, local port, remote port)] for loopback connections)."""
    listen, est = {}, []
    for line in (txt or "").splitlines():
        f2 = line.split()
        if len(f2) < 5 or f2[0].upper() != "TCP" or not f2[-1].isdigit():
            continue
        try:
            lport = int(f2[1].rsplit(":", 1)[1]); rhost, rport = f2[2].rsplit(":", 1)
        except (ValueError, IndexError):
            continue
        if rport in ("0", "*"):                       # a listener has no remote end (the state word is localized)
            listen.setdefault(lport, int(f2[-1]))
        elif rhost in ("127.0.0.1", "[::1]"):
            est.append((int(f2[-1]), lport, int(rport)))
    return listen, est


_wnet = dict(ts=0, listen={}, est=[])


def win_sockets():
    if time.time() - _wnet["ts"] > 3:
        r = sh(["netstat", "-ano", "-p", "TCP"], 15); r6 = sh(["netstat", "-ano", "-p", "TCPv6"], 15)
        listen, est = parse_netstat((r.stdout if r else "") + "\n" + (r6.stdout if r6 else ""))
        _wnet.update(ts=time.time(), listen=listen, est=est)
    return _wnet


def listening():
    """-> {port: (pid, process name)} on every OS."""
    out = {}
    if IS_MAC:
        r = sh(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])
        for line in (r.stdout.splitlines()[1:] if r and r.stdout else []):
            f = line.split()
            if len(f) >= 9:
                m = re.search(r":(\d+)$", f[8])
                if m:
                    out[int(m.group(1))] = (int(f[1]), f[0])
        return out
    names = {q["pid"]: base_name(q["comm"]) for q in snapshot()}
    if IS_WIN:
        for port, pid in win_sockets()["listen"].items():
            out[port] = (pid, names.get(pid, "?"))
    else:
        ls = linux_sockets()
        for port, inode in ls["listen"].items():
            pid = ls["pids"].get(inode, 0)
            if pid:   # a listener we cannot attribute (another user's, or the kernel's) cannot be described or controlled
                out[port] = (pid, names.get(pid, "?"))
    return out


_cmd = {}


def cmd_of(pid):
    """full command line of a process (cached 30s: it is asked for every service on every refresh)."""
    if not pid:
        return ""
    if not IS_MAC:
        return next((q["cmd"] for q in snapshot() if q["pid"] == pid), "")[:1500]
    hit = _cmd.get(pid)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    if len(_cmd) > 600:
        _cmd.clear()
    r = sh(["ps", "-o", "command=", "-p", str(pid)], 3)
    _cmd[pid] = (time.time(), (r.stdout.strip() if r else "")[:1500])
    return _cmd[pid][1]


def all_cmds():
    """{pid: full command line} for every process."""
    if not IS_MAC:
        return {q["pid"]: q["cmd"] for q in snapshot()}
    r = sh(["ps", "-axo", "pid=,command="]); out = {}
    for line in (r.stdout.splitlines() if r else []):
        f = line.split(None, 1)
        if len(f) == 2 and f[0].isdigit():
            out[int(f[0])] = f[1]
    return out


def pgrep(pat):
    return next((pid for pid, c in all_cmds().items() if re.search(pat, c)), None)


def docker_ps():
    r = sh(["docker", "ps", "-a", "--format", "{{json .}}"], 8)
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
            m = re.search(r"com\.docker\.compose\.service=([^,]+)", labels)
            csvc = m.group(1) if m else ""
            hps = sorted({int(x) for x in re.findall(r"(?:0\.0\.0\.0|127\.0\.0\.1|\[?::1?\]?):(\d+)->", j.get("Ports", "") or "")})
            hp = hps[0] if hps else None
            state = (j.get("State") or "").lower() or ("running" if j.get("Status", "").startswith("Up") else "exited")
            out.append(dict(name=j.get("Names", ""), image=j.get("Image", ""), status=j.get("Status", ""),
                            project=proj, service=csvc, port=hp, ports=hps, state=state, up=state in ("running", "paused", "restarting"),
                            paused=state == "paused"))
    return out


# ---- service discovery (auto) ------------------------------------------------
_brew = dict(ts=0, data={})


def brew_services():
    if not IS_MAC or not which("brew"):
        return {}
    if time.time() - _brew["ts"] < 120:  # `brew services list` boots ruby: expensive, and rarely changes
        return _brew["data"]
    r = sh("brew services list 2>/dev/null", 20)
    out = {}
    if r:
        for line in r.stdout.splitlines()[1:]:
            f = line.split()
            if len(f) >= 2:
                out[f[0]] = f[1]  # name -> status
    _brew.update(ts=time.time(), data=out)
    return out


_ps = dict(ts=0, rows=[])
ME = set()  # this server's pid + ancestors: never signalled
OWN = set()  # this server's own child probes: never shown


def footprint(pid):
    """bytes the OS charges to a process: resident + compressed + swapped (Activity Monitor's "Memory").
    A model that macOS has compressed has almost no resident size, so RSS alone hides the biggest consumers."""
    try:
        if IS_MAC:
            import ctypes
            buf = (ctypes.c_uint64 * 40)()   # rusage_info_v0: uuid(2 words), ..., phys_footprint is word 9
            return buf[9] if _libproc().proc_pid_rusage(pid, 0, ctypes.byref(buf)) == 0 else 0
        with open("/proc/%d/status" % pid) as f:   # linux: resident + swapped
            d = dict(l.split(":", 1) for l in f if l.startswith(("VmRSS", "VmSwap")))
        return sum(int(v.split()[0]) for v in d.values()) * 1024
    except Exception:
        return 0


_lp = []


def _libproc():
    if not _lp:
        import ctypes, ctypes.util
        _lp.append(ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"))
    return _lp[0]


def ps_table():
    """every process, one call: [{pid, ppid, uid, cpu, rss (MB, memory footprint), res (MB resident), etime, comm}] - cached."""
    if time.time() - _ps["ts"] < 1.5:
        return _ps["rows"]
    rows = []
    if IS_MAC:
        r = sh(["ps", "-axo", "pid=,ppid=,uid=,%cpu=,rss=,etime=,comm="])
        for line in (r.stdout.splitlines() if r else []):
            f = line.split(None, 6)
            if len(f) == 7:
                try:
                    rows.append(dict(pid=int(f[0]), ppid=int(f[1]), uid=int(f[2]), cpu=float(f[3]),
                                     rss=int(f[4]) / 1024, etime=f[5], comm=f[6].strip()))
                except ValueError:
                    pass
        for q in rows:   # other users' processes refuse the query: they keep their resident size
            q["res"] = q["rss"]; fp = footprint(q["pid"])
            if fp:
                q["rss"] = fp / 1048576
    else:
        rows = [dict(pid=q["pid"], ppid=q["ppid"], uid=q["uid"], cpu=q["cpu"], rss=q["rss"], res=q["res"], etime=q["etime"],
                     comm=q["comm"]) for q in snapshot()]
    byp = {p["pid"]: p for p in rows}
    x, me = os.getpid(), set()
    while x and x not in me:
        me.add(x); x = byp.get(x, {}).get("ppid", 0)
    own, grow = set(), True   # the probes this server spawns (ps, lsof, docker ...): not part of the picture
    while grow:
        grow = False
        for q in rows:
            if q["pid"] not in own and (q["ppid"] == os.getpid() or q["ppid"] in own):
                own.add(q["pid"]); grow = True
    # add first, drop stale after: a request thread reading these mid-update sees too much protected, never too little
    ME.update(me); ME.intersection_update(me); OWN.update(own); OWN.intersection_update(own)
    if rows:
        _ps.update(ts=time.time(), rows=rows)
    return _ps["rows"]


def tree_sum(pid, byp, kids, stop=()):
    """cpu%, rss MB, process count of pid + descendants (not descending into `stop` pids)."""
    cpu = rss = n = 0; stack = [pid]; seen = set()
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x); p = byp.get(x)
        if p:
            cpu += p["cpu"]; rss += p["rss"]; n += 1
        stack += [k for k in kids.get(x, []) if k not in stop]
    return round(cpu, 1), round(rss), n


def app_name(comm):
    """group helper processes under the app they belong to."""
    if "ollama" in comm.lower():
        return "Ollama"
    if "claude-code" in comm or "native-binary/claude" in comm or os.path.basename(comm) == "claude":
        return "Claude Code"
    if "Virtualization.VirtualMachine" in comm:
        return "Virtual machine (Docker / VM)"
    m = re.search(r"/([^/]+)\.app/", comm)
    return m.group(1) if m else (base_name(comm) or comm)


def mine(p):
    """may this server signal that process? same user, not init, not the dashboard itself."""
    return p["uid"] == UID and p["pid"] > (4 if IS_WIN else 1) and p["pid"] not in ME


_ld = dict(ts=0, data={}, loaded={})


def launchd_jobs():
    """pid -> launchd label for the user's running agents (macOS). Also fills _ld['loaded']: label -> last exit status."""
    if not IS_MAC:
        return {}
    if time.time() - _ld["ts"] < 20:
        return _ld["data"]
    r = sh(["launchctl", "list"]); out, loaded = {}, {}
    if r:
        for line in r.stdout.splitlines()[1:]:
            f = line.split(None, 2)
            if len(f) == 3 and not f[2].startswith(("application.", "com.apple.")):
                loaded[f[2].strip()] = f[1]
                if f[0].isdigit():
                    out[int(f[0])] = f[2].strip()
    _ld.update(ts=time.time(), data=out, loaded=loaded)
    return out


_la = dict(ts=0, data={})


def launch_agents():
    """label -> {plist, keep, args} for the agents in ~/Library/LaunchAgents: the ones we can load and unload."""
    if not IS_MAC:
        return {}
    if time.time() - _la["ts"] < 60:
        return _la["data"]
    import glob, plistlib
    out = {}
    for f in glob.glob(os.path.join(HOME, "Library/LaunchAgents/*.plist")):
        try:
            with open(f, "rb") as fh:
                pl = plistlib.load(fh)
            label = pl.get("Label")
            if label and not label.startswith("com.apple."):
                out[label] = dict(plist=f, keep=bool(pl.get("KeepAlive")),
                                  args=" ".join(map(str, pl.get("ProgramArguments") or [pl.get("Program", "")])))
        except Exception:
            pass
    _la.update(ts=time.time(), data=out)
    return out


ROLE = re.compile(r"gateway|bridge|web-?ui|worker|scheduler|proxy|embed(?!ded)|warm", re.I)
MODEL_ARG = re.compile(r"(?:--model|-m)\s+(\S+\.(?:gguf|bin|safetensors))")   # a model FILE, not `python -m x` or `--model name`
ROLE_DESC = {"gateway": "message gateway", "bridge": "bridge to another app", "webui": "web interface", "web-ui": "web interface",
             "embed": "embeddings", "worker": "background worker", "scheduler": "job scheduler", "proxy": "proxy",
             "runner": "task runner", "api": "API", "certs": "one-shot certificate setup"}
DULL = {"var", "bin", "tmp", "opt", "usr", "lib", "libexec", "local", "src", "app", "current"}


def role_token(text):
    """the word in a command line / label that says what this instance is for (flags don't count)."""
    for tok in re.split(r"[\s/=,;'\"()\[\]]+", text or ""):
        if tok and not tok.startswith("-") and ROLE.search(tok):
            return next(part for part in tok.strip("@").split(".") if ROLE.search(part))
    return ""


_cwd = {}


def cwd_of(pid):
    if pid not in _cwd:
        if len(_cwd) > 400:
            _cwd.clear()
        if IS_MAC:
            r = sh(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], 4)
            nm = [l[1:] for l in (r.stdout.splitlines() if r else []) if l.startswith("n")]
            _cwd[pid] = nm[0] if nm else ""
        elif IS_LINUX:
            try:
                _cwd[pid] = os.readlink("/proc/%d/cwd" % pid)
            except OSError:
                _cwd[pid] = ""
        else:
            _cwd[pid] = ""   # Windows does not expose another process's folder without extra tools
    return _cwd[pid]


def systemd_unit(pid):
    """(unit name, is a user unit) for a Linux process managed by systemd, read from its cgroup."""
    try:
        with open("/proc/%d/cgroup" % pid) as f:
            path = f.read().strip().splitlines()[-1].split(":", 2)[-1]
    except (OSError, IndexError):
        return None, False
    return parse_cgroup(path)


def parse_cgroup(path):
    units = [x for x in path.split("/") if x.endswith(".service")]
    if not units or units[-1].startswith(("user@", "session-")):
        return None, False
    return units[-1], any(x.startswith("user@") for x in units[:-1])


def qualifier(i):
    """what tells two same-named services apart: the model, the role, the project folder, else the port."""
    if i["kind"] == "docker":
        return i.get("qual") or i["group"].replace("Docker · ", "") or ":%s" % i["port"]
    if i.get("_model"):
        return i["_model"]
    m = MODEL_ARG.search(i.get("_cmd") or "")
    if m and not os.path.basename(m.group(1)).startswith("sha256"):
        return os.path.splitext(os.path.basename(m.group(1)))[0]
    role = role_token(i.get("_cmd")) or role_token(i.get("launchd"))
    if role:
        return role
    if i.get("_owner"):   # started by another service: say whose it is
        return "for " + i["_owner"]
    cwd = os.path.basename(cwd_of(i["pid"])) if i.get("pid") else ""
    if cwd and cwd not in DULL and not re.search(r"\d+\.\d+|darwin|linux|arm64|x86", cwd) and cwd_of(i["pid"]) != HOME:
        return cwd
    if not i.get("port"):   # not running, so no port: its service name tells them apart
        return (i.get("launchd") or i.get("brew") or "stopped").split(".")[-1]
    return ":%s" % i["port"]


def num(txt):
    m = re.search(r"[\d.]+", txt or "")
    return float(m.group()) if m else 0.0


def to_mb(txt):
    """'1.2GiB' / '512MiB' / '300kB' -> MB."""
    m = re.match(r"\s*([\d.]+)\s*([kKmMgGtT]?)i?[bB]?", txt or "")
    if not m or not m.group(1):
        return 0
    return round(float(m.group(1)) * {"": 1e-6, "k": 1e-3, "m": 1, "g": 1024, "t": 1048576}[m.group(2).lower()])


def table(txt):
    """fixed-width CLI table (ollama ps/list) -> [{header: cell}] using header column offsets."""
    lines = [l for l in (txt or "").splitlines() if l.strip()]
    if not lines:
        return []
    cols = [(m.start(), m.group().strip().lower()) for m in re.finditer(r"\S+(?: \S+)*\s*", lines[0])]
    return [{nm: l[st:(cols[i + 1][0] if i + 1 < len(cols) else None)].strip() for i, (st, nm) in enumerate(cols)}
            for l in lines[1:]]


GROUP_ORDER = ["AI / LLM", "Backend", "Database", "Automation", "Web / Dev", "Infra", "Other"]

# (pattern over image/process/command, friendly name, what it's for, category)
KNOWN = [
    # --- exporters / specific DB variants (checked before generic postgres/redis) ---
    (r"[\w-]+-exporter|node.?exporter", "Metrics exporter", "Prometheus exporter", "Infra"),
    (r"timescale", "TimescaleDB", "Time-series (Postgres)", "Database"),
    (r"cockroach", "CockroachDB", "Distributed SQL", "Database"),
    (r"citus", "Citus", "Distributed Postgres", "Database"),
    (r"pgbouncer", "PgBouncer", "Postgres connection pooler", "Database"),
    (r"pgvector", "pgvector", "Vector search in Postgres", "Database"),
    # --- vector / graph / other databases ---
    (r"qdrant", "Qdrant", "Vector database", "Database"),
    (r"weaviate", "Weaviate", "Vector database", "Database"),
    (r"\bchroma\b|chromadb", "Chroma", "Vector database", "Database"),
    (r"milvus", "Milvus", "Vector database", "Database"),
    (r"\bneo4j\b", "Neo4j", "Graph database", "Database"),
    (r"cassandra|scylla", "Cassandra / Scylla", "Wide-column database", "Database"),
    (r"\bcouchdb\b", "CouchDB", "Document database", "Database"),
    (r"\betcd\b", "etcd", "Key-value store", "Database"),
    (r"memcached", "Memcached", "In-memory cache", "Database"),
    (r"dragonfly", "Dragonfly", "Redis-compatible store", "Database"),
    # --- AI / LLM ---
    (r"litellm", "LiteLLM", "LLM API proxy / gateway", "AI / LLM"),
    (r"localai", "LocalAI", "OpenAI-compatible local API", "AI / LLM"),
    (r"tabbyapi|tabby-api", "TabbyAPI", "ExLlama model server", "AI / LLM"),
    (r"koboldcpp|kobold", "KoboldCpp", "Local model server", "AI / LLM"),
    (r"exllama", "ExLlamaV2", "Local model inference", "AI / LLM"),
    (r"llamafile", "llamafile", "Single-file model runner", "AI / LLM"),
    (r"langflow", "Langflow", "Visual LLM flow builder", "AI / LLM"),
    (r"flowise", "Flowise", "Visual LLM flow builder", "AI / LLM"),
    (r"\bdify\b", "Dify", "LLM app platform", "AI / LLM"),
    (r"anythingllm", "AnythingLLM", "Local RAG chat app", "AI / LLM"),
    (r"librechat", "LibreChat", "Multi-model chat UI", "AI / LLM"),
    (r"lobe-?chat", "Lobe Chat", "Chat UI for LLMs", "AI / LLM"),
    (r"big-?agi", "big-AGI", "Advanced chat UI", "AI / LLM"),
    (r"sillytavern", "SillyTavern", "LLM roleplay UI", "AI / LLM"),
    (r"langfuse", "Langfuse", "LLM observability", "AI / LLM"),
    (r"ragflow", "RAGFlow", "RAG engine", "AI / LLM"),
    (r"whisper", "Whisper", "Speech-to-text", "AI / LLM"),
    (r"invokeai", "InvokeAI", "Image generation", "AI / LLM"),
    (r"fooocus", "Fooocus", "Image generation", "AI / LLM"),
    # --- backend / BaaS / auth ---
    (r"pocketbase", "PocketBase", "Backend-in-one (SQLite)", "Backend"),
    (r"appwrite", "Appwrite", "Backend-as-a-service", "Backend"),
    (r"directus", "Directus", "Headless CMS / data API", "Backend"),
    (r"hasura", "Hasura", "Instant GraphQL API", "Backend"),
    (r"strapi", "Strapi", "Headless CMS", "Backend"),
    (r"keycloak", "Keycloak", "Identity & SSO", "Backend"),
    (r"authentik", "Authentik", "Identity provider", "Backend"),
    (r"authelia", "Authelia", "Auth / 2FA gateway", "Backend"),
    (r"oauth2-?proxy", "OAuth2 Proxy", "Auth reverse proxy", "Backend"),
    (r"vaultwarden|bitwarden", "Vaultwarden", "Password manager", "Backend"),
    # --- queues / messaging ---
    (r"\bnats\b", "NATS", "Messaging system", "Infra"),
    (r"mosquitto|\bmqtt\b", "Mosquitto", "MQTT broker", "Infra"),
    (r"zookeeper", "ZooKeeper", "Coordination service", "Infra"),
    # --- observability / infra ---
    (r"\bloki\b", "Loki", "Log aggregation", "Infra"),
    (r"\btempo\b", "Tempo", "Distributed tracing", "Infra"),
    (r"jaeger", "Jaeger", "Distributed tracing", "Infra"),
    (r"cadvisor", "cAdvisor", "Container metrics", "Infra"),
    (r"uptime-?kuma", "Uptime Kuma", "Uptime monitoring", "Infra"),
    (r"portainer", "Portainer", "Docker management UI", "Infra"),
    (r"watchtower", "Watchtower", "Auto image updates", "Infra"),
    (r"dozzle", "Dozzle", "Live container logs", "Infra"),
    (r"netdata", "Netdata", "Real-time monitoring", "Infra"),
    (r"victoriametrics", "VictoriaMetrics", "Metrics database", "Infra"),
    (r"cloudflared", "Cloudflare Tunnel", "Secure tunnel", "Infra"),
    (r"tailscale", "Tailscale", "Mesh VPN", "Infra"),
    (r"wireguard|wg-easy", "WireGuard", "VPN", "Infra"),
    (r"pi-?hole", "Pi-hole", "DNS ad-blocker", "Infra"),
    (r"adguard", "AdGuard Home", "DNS ad-blocker", "Infra"),
    (r"\bregistry\b", "Docker Registry", "Container image registry", "Infra"),
    # --- self-hosted apps / dev ---
    (r"home-?assistant", "Home Assistant", "Home automation", "Automation"),
    (r"nextcloud", "Nextcloud", "File sync & share", "Web / Dev"),
    (r"immich", "Immich", "Photo backup", "Web / Dev"),
    (r"paperless", "Paperless-ngx", "Document management", "Web / Dev"),
    (r"jellyfin", "Jellyfin", "Media server", "Web / Dev"),
    (r"\bplex\b", "Plex", "Media server", "Web / Dev"),
    (r"gitea", "Gitea", "Git hosting", "Web / Dev"),
    (r"gitlab", "GitLab", "Git & CI platform", "Web / Dev"),
    (r"jenkins", "Jenkins", "CI server", "Web / Dev"),
    (r"woodpecker|\bdrone\b", "Woodpecker / Drone", "CI server", "Web / Dev"),
    (r"verdaccio", "Verdaccio", "Private npm registry", "Web / Dev"),
    (r"sonarqube", "SonarQube", "Code quality", "Web / Dev"),
    (r"storybook", "Storybook", "UI component explorer", "Web / Dev"),
    # --- web servers / runtimes ---
    (r"httpd|apache", "Apache", "Web server", "Infra"),
    (r"tomcat", "Tomcat", "Java app server", "Web / Dev"),
    (r"gunicorn", "Gunicorn", "Python WSGI server", "Web / Dev"),
    (r"uvicorn|fastapi", "Uvicorn / FastAPI", "Python ASGI server", "Web / Dev"),
    (r"\bdjango\b", "Django", "Python web app", "Web / Dev"),
    (r"\brails\b|puma", "Rails", "Ruby web app", "Web / Dev"),
    (r"php-?fpm", "PHP-FPM", "PHP runtime", "Web / Dev"),
    (r"\bnuxt\b", "Nuxt", "Vue web app", "Web / Dev"),
    (r"gotrue|supabase.?auth", "Supabase Auth", "Authentication (GoTrue)", "Backend"),
    (r"supabase.?studio|/studio", "Supabase Studio", "Admin dashboard UI", "Backend"),
    (r"postgres.?meta|pg.?meta", "Postgres Meta", "Database metadata API", "Backend"),
    (r"postgrest|supabase.?rest", "PostgREST", "Auto REST API over Postgres", "Backend"),
    (r"storage.?api|supabase.?storage", "Supabase Storage", "File / object storage API", "Backend"),
    (r"supabase.?realtime|/realtime", "Supabase Realtime", "Realtime websockets", "Backend"),
    (r"logflare|supabase.?analytics", "Supabase Analytics", "Log analytics", "Backend"),
    (r"supavisor|pooler", "Supabase Pooler", "Postgres connection pooler", "Backend"),
    (r"\bkong\b", "Kong", "API gateway", "Backend"),
    (r"mailpit|inbucket|mailhog", "Mailpit", "Local email inbox (dev)", "Backend"),
    (r"searxng", "SearXNG", "Private metasearch engine", "Web / Dev"),
    (r"\bn8n\b", "n8n", "Workflow automation", "Automation"),
    (r"node.?red", "Node-RED", "Flow-based automation", "Automation"),
    (r"postgres", "PostgreSQL", "Relational database", "Database"),
    (r"mysqld|mariadb", "MySQL / MariaDB", "Relational database", "Database"),
    (r"redis", "Redis", "In-memory cache / store", "Database"),
    (r"mongo", "MongoDB", "Document database", "Database"),
    (r"elasticsearch|opensearch", "Elasticsearch", "Search & index engine", "Database"),
    (r"clickhouse", "ClickHouse", "Analytics database", "Database"),
    (r"influxdb", "InfluxDB", "Time-series database", "Database"),
    (r"qdrant|weaviate|chroma|milvus|pgvector", "Vector DB", "Embedding / vector store", "Database"),
    (r"ollama\S*/llama-server|ollama runner", "Ollama model runner", "Holds a loaded model in memory", "AI / LLM"),
    (r"ollama", "Ollama", "Local LLM runtime", "AI / LLM"),
    (r"claude-code|native-binary/claude|claude\.app/Contents/MacOS/claude", "Claude Code session",
     "AI coding agent session (local control port)", "AI / LLM"),
    (r"llama.?server|llama\.cpp|llamacpp", "llama.cpp server", "Local model inference server", "AI / LLM"),
    (r"\bvllm\b", "vLLM", "High-throughput LLM server", "AI / LLM"),
    (r"text.?generation|textgen", "Text-Gen WebUI", "LLM web interface", "AI / LLM"),
    (r"open.?webui", "Open WebUI", "Chat UI for local LLMs", "AI / LLM"),
    (r"comfyui", "ComfyUI", "Image-generation workflows", "AI / LLM"),
    (r"automatic1111|a1111|stable.?diffusion", "Stable Diffusion", "Image generation", "AI / LLM"),
    (r"hermes", "Hermes", "Local AI agent", "AI / LLM"),
    (r"paperclip", "Paperclip", "Local agent platform", "AI / LLM"),
    (r"whatsapp", "WhatsApp Bridge", "Chat messaging bridge", "AI / LLM"),
    (r"grafana", "Grafana", "Metrics dashboards", "Infra"),
    (r"prometheus", "Prometheus", "Metrics time-series DB", "Infra"),
    (r"\bnginx\b", "Nginx", "Web server / reverse proxy", "Infra"),
    (r"\bcaddy\b", "Caddy", "Web server / reverse proxy", "Infra"),
    (r"traefik", "Traefik", "Reverse proxy / ingress", "Infra"),
    (r"\bminio\b", "MinIO", "S3-compatible object storage", "Infra"),
    (r"rabbitmq", "RabbitMQ", "Message queue", "Infra"),
    (r"\bkafka\b", "Kafka", "Event streaming", "Infra"),
    (r"\bvault\b", "Vault", "Secrets manager", "Infra"),
    (r"jupyter", "Jupyter", "Notebook server", "Web / Dev"),
    (r"\bvite\b", "Vite", "Frontend dev server", "Web / Dev"),
]


def recognize(sig, pname="", port=None):
    """-> (friendly name, one-line description, category) from image/process signature."""
    s = (sig or "").lower()
    for pat, name, desc, grp in KNOWN:
        if re.search(pat, s):
            return name, desc, grp
    if port in PORT_HINTS:
        return PORT_HINTS[port][0], "", PORT_HINTS[port][1]
    p = (pname or "").lower()
    grp = ("AI / LLM" if any(k in s for k in AI_PROC) else "Database" if any(k in p for k in DB_PROC)
           else "Infra" if any(k in p for k in INFRA_PROC) else "Web / Dev" if any(k in p for k in WEB_PROC) else "Other")
    m = re.search(r"/([\w.-]+?)(?:\.(?:js|py|mjs))?(?:\s|$)", s)
    return (m.group(1) if m else (pname or "service")), "", grp


_web = {}


def web_kind(port):
    """'page' if http://localhost:port/ serves HTML a person can open, 'api' if it speaks HTTP but has no page
    (401/404/JSON), None if it is not HTTP at all. Cached 5 min; local requests answer in milliseconds."""
    hit = _web.get(port)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    import urllib.request, urllib.error, urllib.parse

    class Stay(urllib.request.HTTPRedirectHandler):   # follow a redirect only to the same local port, never elsewhere
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            u = urllib.parse.urlparse(newurl)
            ok = u.hostname in ("127.0.0.1", "localhost") and (u.port or 80) == port
            return urllib.request.HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl) if ok else None
    kind = None
    try:
        r = urllib.request.build_opener(Stay).open(urllib.request.Request("http://127.0.0.1:%d/" % port, headers={"Accept": "text/html"}), timeout=1.5)
        kind = "page" if "html" in (r.headers.get("Content-Type") or "").lower() else "api"
    except urllib.error.HTTPError:
        kind = "api"
    except Exception:
        kind = None
    if len(_web) > 500:
        _web.clear()
    _web[port] = (time.time(), kind)
    return kind


def discover_services():
    ports = listening()
    dps = docker_ps()
    brew = brew_services()
    rows = ps_table(); byp = {p["pid"]: p for p in rows}
    kids = defaultdict(list)
    for p in rows:
        kids[p["ppid"]].append(p["pid"])
    jobs = launchd_jobs(); agents = launch_agents()
    dstats = {x["name"]: x for x in docker_stats()}
    models = defaultdict(list)   # `ollama serve` pid -> names of the models it holds
    for m in llm_loaded():
        models[m["pid"]].append(m["name"])
    items = []
    # docker containers (identify from the image, which says what it actually is)
    for c in dps:
        grp = "Docker · " + (c["project"] or "containers")
        http = web_kind(c["port"]) if (c["port"] and c["up"]) else None
        url = "http://localhost:%d" % c["port"] if http == "page" else None
        nm, desc, _ = recognize(c["image"] + " " + c["name"], "", c["port"])
        st = dstats.get(c["name"], {})
        memu = st.get("mem", "").split("/")[0].strip() if st.get("mem") else None
        acts = (["unpause", "stop"] if c["paused"] else ["restart", "stop", "pause", "kill"]) if c["up"] else ["start", "remove"]
        items.append(dict(id="dk:" + c["name"], name=nm, desc=desc, group=grp, kind="docker", docker=c["name"],
                          url=url, http=http, where=c["image"], port=c["port"], ports=c["ports"], note=None, cpu=st.get("cpu"), mem=memu,
                          cpuN=num(st.get("cpu")), memMB=to_mb(memu), procs=None, state=c["state"], qual=c["service"],
                          paused=c["paused"], crashed=False, actions=acts, has_logs=True,
                          up=c["up"], pid=None, uptime=c["status"]))
    # host listening ports, grouped by pid
    bypid = defaultdict(list)
    for port, (pid, pname) in ports.items():
        bypid[(pid, pname)].append(port)
    svc_pids = {pid for (pid, _) in bypid}
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
        nm, desc, grp = recognize(pname + " " + cmd, pname, primary)
        # resources = the process plus its children (workers, model runners), not counting other services
        pcpu, prss, pn = tree_sum(pid, byp, kids, svc_pids - {pid})
        proc = byp.get(pid, {})
        held = models.get(proc.get("ppid")) if nm == "Ollama model runner" else None
        if held:
            desc = "Holds %s in memory" % ", ".join(held)
        # who manages it decides what control we can offer
        acts, brewname, label, x = [], None, None, pid
        for _ in range(4):  # the launchd job is the process itself or a near ancestor
            if x in jobs:
                label = jobs[x]; break
            x = byp.get(x, {}).get("ppid", 0)
            if x <= 1:
                break
        if label and label.startswith("homebrew.mxcl.") and label[14:] in brew:
            brewname, label, acts = label[14:], None, ["restart", "stop", "start"]
        elif proc and mine(proc):
            # restart = bounce the whole job; unload = stop it and keep it stopped (only for agents we have the plist of)
            acts = (["restart"] + (["unload"] if label in agents else []) if label else []) + ["stop", "kill"]
        unit, uunit = systemd_unit(pid) if IS_LINUX else (None, False)
        if unit and uunit and which("systemctl"):   # a user unit can be bounced without root; a system unit cannot
            acts = ["restart", "stop", "kill"]
        items.append(dict(id="host:%d" % primary, name=nm, desc=desc, group=grp,
                          kind="brew" if brewname else ("launchd" if label else "systemd" if unit else "host"), brew=brewname, launchd=label,
                          systemd=unit, sysUser=uunit,
                          jobRoot=bool(label) and pid in jobs,
                          url=("http://localhost:%d" % primary if web_kind(primary) == "page" else None), http=web_kind(primary),
                          where="%s · pid %d" % (pname, pid), port=primary, ports=sorted(plist), note=None,
                          cpu="%.1f%%" % pcpu, mem=(fmt_mb(prss) if prss else None), cpuN=pcpu, memMB=prss, procs=pn,
                          state="running", paused=False, crashed=False, _cmd=cmd, _model=", ".join(held or []),
                          _opid=next((a for a in (proc.get("ppid"), byp.get(proc.get("ppid"), {}).get("ppid")) if a in svc_pids), None),
                          actions=acts, has_logs=True, up=True, pid=pid, uptime=proc.get("etime", "")))
    # always-on services that are NOT running: a KeepAlive agent that is down, a brew service in error
    running = set(jobs.values())
    for label, a in agents.items():
        if not a["keep"] or label in running or label.startswith("homebrew.mxcl.") or ROOT in a["args"]:
            continue
        nm, desc, grp = recognize(a["args"] + " " + label, "", None)
        st = _ld["loaded"].get(label)   # None = unloaded on purpose; a status = loaded but its process is gone
        items.append(dict(id="ld:" + label, name=nm, desc=desc, group=grp, kind="launchd", brew=None, launchd=label, jobRoot=True,
                          url=None, where=a["plist"].replace(HOME, "~"), port=None, ports=[], note=None, cpu=None, mem=None,
                          cpuN=0, memMB=0, procs=0, state="stopped", paused=False, crashed=st is not None,
                          actions=["start"], has_logs=False, up=False, pid=None,
                          uptime="not loaded (stopped on purpose)" if st is None else "loaded but not running (last exit %s)" % st))
    for bn, st in brew.items():
        if st in ("error", "stopped"):
            nm, desc, grp = recognize(bn, bn, None)
            items.append(dict(id="brew:" + bn, name=nm, desc=desc, group=grp, kind="brew", brew=bn, launchd=None, jobRoot=True,
                              url=None, where="brew service " + bn, port=None, ports=[], note=None, cpu=None, mem=None, cpuN=0,
                              memMB=0, procs=0, state="stopped", paused=False, crashed=st == "error", actions=["start"],
                              has_logs=False, up=False, pid=None, uptime="brew status: " + st))
    # same name twice (two Ollama servers, three n8n containers): say which is which
    seen = defaultdict(int)
    for i in items:
        seen[i["name"]] += 1
    pidname = {i["pid"]: i["name"] for i in items if i.get("pid")}
    for i in items:
        own = pidname.get(i.pop("_opid", None))
        i["_owner"] = own if own and own != i["name"] else None
    for i in items:
        if seen[i["name"]] > 1:
            q = qualifier(i); i["name"] += " · " + q
            role = next((v for k, v in ROLE_DESC.items() if k in q.lower()), None)
            if i.get("_model"):
                pass                                    # already says which model it holds
            elif MODEL_ARG.search(i.get("_cmd") or ""):
                i["desc"] = "Serves the model %s" % q
            elif role and i.get("desc"):
                i["desc"] += ": " + role
        i.pop("_cmd", None); i.pop("_model", None); i.pop("_owner", None)
    seen = defaultdict(int)
    for i in items:
        seen[i["name"]] += 1
    for i in items:   # still the same (three sessions in one folder): the port settles it
        if seen[i["name"]] > 1 and i.get("port"):
            i["name"] += " :%s" % i["port"]
    # order groups: AI first, then known, then docker projects, then other
    def gkey(g):
        if g in GROUP_ORDER:
            return (GROUP_ORDER.index(g), g)
        if g.startswith("Docker"):
            return (len(GROUP_ORDER), g)
        return (len(GROUP_ORDER) + 1, g)
    items.sort(key=lambda s: (gkey(s["group"]), s["name"].lower()))
    return items


def fmt_mb(mb):
    return "%.1f GB" % (mb / 1024) if mb >= 1024 else "%d MB" % mb


BY_ID = {}


def established():
    """local TCP connections -> (pid, local_port, remote_port)."""
    if IS_WIN:
        return list(win_sockets()["est"])
    if IS_LINUX:
        ls = linux_sockets()
        return [(ls["pids"].get(inode, 0), lp, rp) for lp, rp, inode in ls["est"]]
    out = []
    r = sh(["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED"])
    for line in (r.stdout.splitlines() if r else []):
        m = re.search(r":(\d+)->(?:127\.0\.0\.1|\[::1\]|localhost):(\d+)", line)
        if m:
            f = line.split()
            out.append((int(f[1]) if len(f) > 1 and f[1].isdigit() else 0, int(m.group(1)), int(m.group(2))))
    return out


_ddet = dict(ts=0, data=[])


SECRET = re.compile(r"KEY|SECRET|TOKEN|PASS|PWD|JWT|CRYPT|AUTH|CRED|DSN|WEBHOOK|PRIVATE|COOKIE|SESSION|SIGNING|SALT|SEED|CONN|_PAT$", re.I)
_WORDS = r"api[-_]?key|access[-_]?key|token|password|passwd|secret|requirepass|credential|pwd"
_R_URL = re.compile(r"(://)[^/@\s]*@")                                       # scheme://user:pass@ , ://:pass@ , ://token@
_R_FLAG = re.compile(r"(?i)(--?[\w-]*(?:%s|auth|pass)[\w-]*)(\s+|=)(\S+)" % _WORDS)     # --api-key X , --password=X
_R_KV = re.compile(r"(?i)\b([\w.-]*(?:%s)[\w.-]*)(\s*[=:]\s*)([^\s&;,\"']+)" % _WORDS)  # password=X , "token": X
_R_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_R_SHORT = re.compile(r"\b(redis-cli|mysql|mysqladmin|mariadb|mongosh?|mysqldump)\b(.*?\s)(-a\s*|-p|--password[= ])(\S+)")
_R_KNOWN = re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}"
                      r"|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,})")


def redact(text):
    """mask what looks like a credential in a command line, URL or log line before it leaves this process.
    A plain number is left alone, so `--max-tokens 4096` and `n_tokens = 512` stay readable."""
    if not text:
        return text
    keep = lambda m: m.group(0) if re.fullmatch(r"[\d.]+", m.group(3)) else m.group(1) + m.group(2) + "***"
    t = _R_URL.sub(r"\1***@", str(text))
    t = _R_FLAG.sub(keep, t); t = _R_KV.sub(keep, t)
    t = _R_BEARER.sub(r"\1 ***", t); t = _R_SHORT.sub(r"\1\2\3***", t)
    return _R_KNOWN.sub("***", t)


def safe_env(env):
    """what may be shown of a container's environment: no secret-named variables, and values with credentials masked."""
    return [redact(e) for e in env if not SECRET.search(e.split("=", 1)[0])]


def docker_details():
    """per-container {name, nets, aliases, env, project, service, depends} - cached (these rarely change)."""
    if _ddet["data"] and time.time() - _ddet["ts"] < 30:
        return _ddet["data"]
    names = [c["name"] for c in docker_ps()]
    data = []
    if names:
        r = sh(["docker", "inspect"] + names, 12)
        if r and r.stdout:
            try:
                for j in json.loads(r.stdout):
                    nets = (j.get("NetworkSettings", {}).get("Networks") or {}); lab = j.get("Config", {}).get("Labels") or {}
                    al = {j["Name"].lstrip("/")}
                    for v in nets.values():
                        al.update(v.get("Aliases") or []); al.update(v.get("DNSNames") or [])
                    data.append(dict(name=j["Name"].lstrip("/"), nets=list(nets.keys()), aliases=al,
                                     env=j.get("Config", {}).get("Env") or [],   # stays on the server: used to find links only
                                     project=lab.get("com.docker.compose.project", ""), service=lab.get("com.docker.compose.service", ""),
                                     restart=(j.get("HostConfig", {}).get("RestartPolicy") or {}).get("Name", ""),
                                     depends=[d.split(":")[0] for d in lab.get("com.docker.compose.depends_on", "").split(",") if d]))
            except Exception:
                pass
    _ddet.update(ts=time.time(), data=data)
    return data


def discover_edges(svcs):
    """[src, dst, why] - why is the evidence, so the map can be checked instead of believed."""
    port2id, pid2id, name2id = {}, {}, {}
    for s in svcs:
        if s.get("pid"):
            pid2id[s["pid"]] = s["id"]
        if s.get("docker"):
            name2id[s["docker"]] = s["id"]
        for p in (s.get("ports") or ([s["port"]] if s.get("port") else [])):
            port2id.setdefault(p, s["id"])
    edges = {}

    def add(a, b, why):
        if a and b and a != b:
            edges.setdefault((a, b), why)
    # 1. live TCP connections between services on this host (the client side names the listening port)
    for pid, lport, rport in established():
        if rport in port2id:
            add(pid2id.get(pid) or port2id.get(lport), port2id[rport], "live connection to :%d" % rport)
    # 1b. a service started by another service (an Ollama server and the model runner it spawned)
    byp = {q["pid"]: q for q in ps_table()}
    for s in svcs:
        x = byp.get(s.get("pid") or 0)
        for _ in range(3):
            x = byp.get(x["ppid"]) if x else None
            if x and x["pid"] in pid2id:
                add(pid2id[x["pid"]], s["id"], "runs it (child process)"); break
    # 2. containers: what their configuration points at
    det = docker_details(); alias = defaultdict(list)
    for c in det:
        for a in c["aliases"]:
            alias[a].append(c)
    for c in det:
        sid = name2id.get(c["name"])
        if not sid:
            continue

        def resolve(host):   # another container reachable under that name on a shared network
            for x in alias.get(host, []):
                if x["name"] != c["name"] and set(x["nets"]) & set(c["nets"]):
                    return name2id.get(x["name"])
        for e in c["env"]:
            key, _, val = e.partition("=")
            for host, port in re.findall(r"(?://|@|\b)([A-Za-z0-9][\w.-]+):(\d+)", val):
                # inside a container "localhost" is the container itself: only host.docker.internal reaches the host
                add(sid, resolve(host) or (port2id.get(int(port)) if host == "host.docker.internal" else None), "config " + key)
            for host in re.findall(r"//([A-Za-z0-9][\w.-]+)", val):
                add(sid, resolve(host), "config " + key)
            if re.search(r"HOST|ADDR|SERVER|ENDPOINT|UPSTREAM", key, re.I):   # PG_META_DB_HOST=supabase_db_...
                for host in re.split(r"[,\s]+", val):
                    add(sid, resolve(host.split(":")[0]), "config " + key)
        for dep in c["depends"]:
            for x in det:
                if x["project"] == c["project"] and x["service"] == dep:
                    add(sid, name2id.get(x["name"]), "compose depends_on")
    # links only show while a connection is open; remember them for a day so the map does not flicker
    now = int(time.time()); live = [[a, b, why, 0] for (a, b), why in sorted(edges.items())]
    if DB is None:
        return live[:150]
    dbx("insert or replace into edge(src, dst, why, ts) values(?,?,?,?)", [(a, b, w, now) for a, b, w, _ in live], many=True)
    ids = {x["id"] for x in svcs}
    return [[a, b, w, now - ts] for a, b, w, ts in dbx("select src, dst, why, ts from edge where ts>=? order by src, dst", (now - 86400,))
            if a in ids and b in ids][:150]


STATE = dict(ts=0, services=[], edges=[], alerts=[], rules=[])
SVC_HIST = defaultdict(lambda: deque(maxlen=75))   # service id -> (cpu%, mem MB), one point per refresh
EVENTS = deque(maxlen=400)                         # newest last
_seq = [0]
_prev = {}       # service id -> (up, paused, name) at the last refresh
_alert_prev = {}
_wake = threading.Event()
_lock = threading.Lock()
ALERT_MEM, ALERT_DISK, ALERT_CPU = (int(os.environ.get("DASH_ALERT_" + k, d)) for k, d in
                                    (("MEM", 88), ("DISK", 90), ("CPU", 90)))   # percent


DATA = DATA_DIR
KEEP_H = 48          # hours of per-minute history kept on disk
DB = None
_dbl = threading.Lock()


def db_open():
    """per-minute history + the event log in one sqlite file, so a night is not lost on restart."""
    global DB
    try:
        os.makedirs(DATA, exist_ok=True)
        DB = sqlite3.connect(os.path.join(DATA, "history.db"), check_same_thread=False, timeout=5)
        if not IS_WIN:   # session titles, folders and events live here: for your eyes only
            os.chmod(DATA, 0o700); os.chmod(os.path.join(DATA, "history.db"), 0o600)
        DB.executescript("""
            create table if not exists metric(ts integer primary key, cpu real, mem real, gpu real, load real,
                                              disk real, net real, swap real);
            create table if not exists svc(ts integer, id text, cpu real, mem real);
            create table if not exists app(ts integer, name text, mem real);
            create index if not exists app_i on app(name, ts);
            create index if not exists svc_i on svc(id, ts);
            create table if not exists kv(k text primary key, v text);
            create table if not exists edge(src text, dst text, why text, ts integer, primary key(src, dst));
            create table if not exists event(n integer primary key, ts integer, kind text, msg text, id text);""")
        for n, ts, kind, msg, sid in reversed(DB.execute(
                "select n, ts, kind, msg, id from event order by n desc limit 400").fetchall()):
            EVENTS.append(dict(n=n, ts=ts, kind=kind, msg=msg, id=sid)); _seq[0] = max(_seq[0], n)
        row = DB.execute("select v from kv where k='budgets'").fetchone()
        if row:
            BUDGETS.update({k: float(v) for k, v in json.loads(row[0]).items() if k in BUDGETS})
        row = DB.execute("select v from kv where k='stopped'").fetchone()
        if row:
            USER_STOPPED.update(json.loads(row[0]))
        row = DB.execute("select v from kv where k='alerts'").fetchone()
        if row:   # alerts that were already firing before a restart are not news
            _alert_prev.update(json.loads(row[0]))
    except Exception:
        DB = None  # read-only home, etc: run without history rather than not at all


def dbx(sql, args=(), many=False):
    if DB is None:
        return []
    with _dbl:
        try:
            cur = DB.executemany(sql, args) if many else DB.execute(sql, args)
            rows = cur.fetchall(); DB.commit()
            return rows
        except Exception:
            return []


def event(kind, msg, sid=None):
    """kind: up | down | new | gone | action | alert | resolved | wait"""
    _seq[0] += 1
    e = dict(n=_seq[0], ts=int(time.time()), kind=kind, msg=msg, id=sid)
    EVENTS.append(e)
    dbx("insert or replace into event(n, ts, kind, msg, id) values(?,?,?,?,?)", (e["n"], e["ts"], kind, msg, sid))


RANGES = {"1h": 3600, "6h": 21600, "24h": 86400, "48h": 172800}


def history(rng, sid=None, app=None, points=240):
    """persisted per-minute series, averaged into at most `points` buckets."""
    rng = rng if rng in RANGES else "1h"
    secs = RANGES[rng]; t0 = int(time.time()) - secs
    if app:
        rows = dbx("select ts, mem from app where name=? and ts>=? order by ts", (app, t0)); keys = ("mem",)
    elif sid:
        rows = dbx("select ts, cpu, mem from svc where id=? and ts>=? order by ts", (sid, t0)); keys = ("cpu", "mem")
    else:
        rows = dbx("select ts, cpu, mem, gpu, load, disk, net, swap from metric where ts>=? order by ts", (t0,))
        keys = ("cpu", "mem", "gpu", "load", "disk", "net", "swap")
    step = max(60, secs // points); out = dict(ts=[], range=rng, step=step, **{k: [] for k in keys})
    bucket, cur = [], None
    for r in rows + [None]:
        b = None if r is None else r[0] // step
        if bucket and b != cur:
            out["ts"].append(cur * step)
            for i, k in enumerate(keys):
                out[k].append(round(sum(x[i + 1] or 0 for x in bucket) / len(bucket), 1))
            bucket = []
        if r is not None:
            bucket.append(r); cur = b
    return out


def since(ts):
    """the morning question: what happened while nobody was looking."""
    ts = max(int(ts or 0), int(time.time()) - KEEP_H * 3600)
    ev = [dict(n=n, ts=t, kind=k, msg=m, id=i) for n, t, k, m, i in dbx(
        "select n, ts, kind, msg, id from event where ts>=? order by n desc limit 200", (ts,))]
    peak = lambda col: (dbx("select %s, ts from metric where ts>=? order by %s desc limit 1" % (col, col), (ts,)) or [(None, None)])[0]
    span = (dbx("select min(ts), max(ts), count(*) from metric where ts>=?", (ts,)) or [(None, None, 0)])[0]
    cnt = defaultdict(int)
    for e in ev:
        cnt[e["kind"]] += 1
    return dict(since=ts, events=ev, counts=cnt, peakMem=peak("mem"), peakCpu=peak("cpu"), peakSwap=peak("swap"),
                first=span[0], last=span[1], minutes=span[2])


USER_STOPPED = set()   # containers the user stopped from here (persisted): down on purpose, not crashed


def mark_stopped(names, stopped):
    (USER_STOPPED.update if stopped else USER_STOPPED.difference_update)(names)
    dbx("insert or replace into kv(k, v) values('stopped', ?)", (json.dumps(sorted(USER_STOPPED)),))


def compute_alerts(svcs):
    a = []
    policy = {c["name"]: c.get("restart") for c in docker_details()}
    for s in svcs:
        if s["kind"] != "docker":
            continue
        st = (s.get("uptime") or "").lower()
        m = re.match(r"exited \((\d+)\)", st)
        if "unhealthy" in st:
            a.append(dict(key="unhealthy:" + s["id"], level="err", id=s["id"], msg="%s is unhealthy" % s["name"]))
        elif s.get("state") == "restarting":
            a.append(dict(key="restarting:" + s["id"], level="err", id=s["id"], msg="%s is restart-looping" % s["name"]))
        elif (m and m.group(1) not in ("0", "137", "143")            # 137/143 = stopped by a signal
              and s["docker"] not in USER_STOPPED                     # stopped from this dashboard
              and policy.get(s["docker"]) not in ("always", "unless-stopped")):  # Docker restarts those unless a person stopped them
            a.append(dict(key="exit:" + s["id"], level="err", id=s["id"],
                          msg="%s crashed (exit code %s)" % (s["name"], m.group(1))))
    recent = list(M["cpu"])[-15:]; cpu_hot = len(recent) == 15 and min(recent) >= ALERT_CPU
    pr = SNAP.get("pressure", 1)
    rules = [dict(name="Memory used", value="%s%%" % SNAP["memPct"], rule=">= %d%%" % ALERT_MEM, firing=SNAP["memPct"] >= ALERT_MEM),
             dict(name="CPU sustained", value="%s%%" % SNAP["cpu"], rule=">= %d%% for 30s" % ALERT_CPU, firing=cpu_hot)]
    if IS_MAC:
        rules.insert(1, dict(name="Memory pressure (macOS)", value={1: "normal", 2: "warning", 4: "critical"}.get(pr, str(pr)),
                             rule="warning or critical", firing=pr >= 2))
    for d in SNAP["disk"]:
        rules.append(dict(name="Disk " + d["mount"], value="%s%%" % d["pct"], rule=">= %d%%" % ALERT_DISK, firing=d["pct"] >= ALERT_DISK))
    bad = [x for x in a if x["level"] == "err"]
    rules.append(dict(name="Containers", value="%d with a problem" % len(bad) if bad else "all healthy",
                      rule="crashed, unhealthy or restart-looping", firing=bool(bad)))
    stopped = [s for s in svcs if s["kind"] in ("launchd", "brew") and not s["up"] and s.get("crashed")]
    rules.append(dict(name="Always-on services", value="%d not running" % len(stopped) if stopped else "all running",
                      rule="a KeepAlive agent or brew service is not running", firing=bool(stopped)))
    for s in stopped:
        a.append(dict(key="stopped:" + s["id"], level="warn", id=s["id"], msg="%s should be running but is not" % s["name"]))
    try:
        ba, br = budget_alerts()
        a += ba; rules += br
    except Exception:
        pass
    if SNAP["memPct"] >= ALERT_MEM:
        a.append(dict(key="mem", level="warn", id=None,
                      msg="Memory at %s%% (%s GB swap in use)" % (SNAP["memPct"], SNAP["swapGB"])))
    if pr >= 2:
        a.append(dict(key="pressure", level="err" if pr >= 4 else "warn", id=None,
                      msg="macOS memory pressure is %s" % ("critical" if pr >= 4 else "elevated")))
    for d in SNAP["disk"]:
        if d["pct"] >= ALERT_DISK:
            a.append(dict(key="disk:" + d["mount"], level="warn", id=None, msg="Disk %s at %s%%" % (d["mount"], d["pct"])))
    if cpu_hot:
        a.append(dict(key="cpu", level="warn", id=None, msg="CPU above %d%% for 30s" % ALERT_CPU))
    STATE["rules"] = rules
    return a


def refresh(edges=True):
    global BY_ID, _prev, _alert_prev
    with _lock:
        svcs = discover_services()
        BY_ID = {s["id"]: s for s in svcs}
        cur = {s["id"]: (s["up"], s.get("paused"), s["name"]) for s in svcs}
        for s in svcs:
            SVC_HIST[s["id"]].append((s["cpuN"] if s["up"] else 0, s["memMB"] if s["up"] else 0))
        if STATE["ts"]:  # not the first pass: report what changed
            for sid, (up, paused, name) in cur.items():
                p = _prev.get(sid)
                if p is None:
                    event("new", "%s appeared" % name, sid)
                elif p[0] != up:
                    event("up" if up else "down", "%s %s" % (name, "is up" if up else "went down"), sid)
                elif p[1] != paused and not any(e["id"] == sid and e["kind"] == "action" and time.time() - e["ts"] < 20
                                                for e in EVENTS):  # done from here: already logged as the action
                    event("action", "%s %s" % (name, "paused" if paused else "resumed"), sid)
            for sid, p in _prev.items():
                if sid not in cur:
                    event("gone", "%s is gone (process exited)" % p[2], sid)
        _prev = cur
        alerts = compute_alerts(svcs); akeys = {a["key"]: a for a in alerts}
        if SNAP["ts"]:   # (needs a first metrics sample; alerts already firing at start are announced too)
            for k, a in akeys.items():
                if k not in _alert_prev:
                    event("alert", a["msg"], a["id"])
            for k, a in _alert_prev.items():
                if k not in akeys:
                    event("resolved", "Resolved: " + a["msg"], a["id"])
        if SNAP["ts"]:
            if set(akeys) != set(_alert_prev):
                dbx("insert or replace into kv(k, v) values('alerts', ?)", (json.dumps(akeys),))
            _alert_prev = akeys
        STATE.update(ts=int(time.time()), services=svcs, alerts=alerts,
                     edges=discover_edges(svcs) if edges else STATE["edges"])


def watcher():
    n = 0; last_min = 0
    while True:
        try:
            refresh(edges=(n % 3 == 0))
            m = int(time.time()) // 60
            if m != last_min:  # one row per service per minute; prune old rows hourly
                last_min = m
                dbx("insert into svc(ts, id, cpu, mem) values(?,?,?,?)",
                    [(m * 60, s["id"], s["cpuN"], s["memMB"]) for s in STATE["services"] if s["up"]], many=True)
                if m % 60 == 0:
                    old = int(time.time()) - KEEP_H * 3600
                    dbx("delete from metric where ts<?", (old,)); dbx("delete from svc where ts<?", (old,))
                    dbx("delete from app where ts<?", (old,))
                    dbx("delete from event where ts<?", (int(time.time()) - 14 * 86400,))
        except Exception:
            pass
        n += 1
        _wake.wait(4); _wake.clear()


def status():
    if not STATE["ts"]:
        refresh()
    out = []
    for s in STATE["services"]:
        h = list(SVC_HIST[s["id"]])
        out.append(dict(s, hist=dict(cpu=[x[0] for x in h], mem=[x[1] for x in h])))
    return dict(ts=STATE["ts"], services=out, edges=STATE["edges"], alerts=STATE["alerts"], rules=STATE["rules"],
                events=list(EVENTS)[-250:][::-1], persisted=DB is not None, keepHours=KEEP_H)


# ---- metrics (cross-platform) ------------------------------------------------
HIST = 150   # ~5 minutes of 2s samples in memory; longer ranges come from disk
M = {k: deque(maxlen=HIST) for k in ("ts", "cpu", "mem", "gpu", "rx", "tx", "load", "disk")}
SNAP = dict(ts=0, cpu=0, cpuUser=0, cpuSys=0, load=[0, 0, 0], memUsedGB=0, memTotalGB=0, memPct=0,
            swapGB=0, gpu=0, rxKBs=0, txKBs=0, disk=[], procs=[], procsMem=[], apps=[], memParts={}, docker=[], llm=[])
_net = dict(ts=0, rx=0, tx=0)
_cpu_prev = None
_dock = dict(ts=0, data=[])


def mac_ticks():
    """host CPU ticks [user, system, idle, nice] straight from the kernel (no `top` process)."""
    import ctypes, ctypes.util
    lib = ctypes.CDLL(ctypes.util.find_library("c"))
    lib.mach_host_self.restype = ctypes.c_uint
    buf = (ctypes.c_uint * 4)(); n = ctypes.c_uint(4)
    return list(buf) if lib.host_statistics(lib.mach_host_self(), 3, buf, ctypes.byref(n)) == 0 else None


def cpu_stat():
    """-> total%, user%, sys% since the previous call."""
    global _cpu_prev
    try:
        if IS_MAC:
            t = mac_ticks(); cur = (t[0] + t[3], t[1], t[2])           # user(+nice), system, idle
        elif IS_WIN:
            import ctypes
            idle, kern, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
            cur = (user.value, kern.value - idle.value, idle.value)     # Windows counts idle inside kernel time
        else:
            with open("/proc/stat") as f:
                v = list(map(int, f.readline().split()[1:8]))
            cur = (v[0] + v[1], v[2] + v[5] + v[6], v[3] + v[4])
        prev, _cpu_prev = _cpu_prev, cur
        if not prev:
            return 0, 0, 0
        du, ds, di = ((c - q) & 0xFFFFFFFF for c, q in zip(cur, prev))   # mac counters are 32-bit and wrap
        tot = (du + ds + di) or 1
        return round((du + ds) / tot * 100, 1), round(du / tot * 100, 1), round(ds / tot * 100, 1)
    except Exception:
        return 0, 0, 0


GIB = 1024 ** 3   # memory is reported the way the OS does it: binary gigabytes, labelled GB


def win_mem():
    """(total, available, page file total, page file available) in bytes, from GlobalMemoryStatusEx."""
    import ctypes

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in (
            "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]
    m = MS(); m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullTotalPhys, m.ullAvailPhys, m.ullTotalPageFile, m.ullAvailPageFile


def mem_stat():
    """-> used GB, total GB, parts{app, wired, compressed, cached, free} (parts add up to total)."""
    if IS_WIN:
        try:
            tot, av, _, _ = win_mem()
            return (tot - av) / GIB, tot / GIB, dict(app=round((tot - av) / GIB, 1), free=round(av / GIB, 1))
        except Exception:
            return 0, 0, {}
    if IS_MAC:
        total = int((sh(["sysctl", "-n", "hw.memsize"]) or type("", (), {"stdout": "0"})).stdout or 0)
        r = sh(["vm_stat"])
        if not (r and total):
            return 0, total / GIB, {}
        mm = re.search(r"page size of (\d+)", r.stdout); pg = int(mm.group(1)) if mm else 4096
        d = {}
        for line in r.stdout.splitlines():
            g = re.match(r'"?([\w -]+?)"?:\s+(\d+)\.', line)   # keys contain spaces and hyphens ("File-backed pages")
            if g:
                d[g.group(1).strip()] = int(g.group(2)) * pg / GIB
        # Activity Monitor's definitions: app = anonymous - purgeable, cached = file-backed + purgeable
        parts = dict(app=d.get("Anonymous pages", 0) - d.get("Pages purgeable", 0), wired=d.get("Pages wired down", 0),
                     compressed=d.get("Pages occupied by compressor", 0),
                     cached=d.get("File-backed pages", 0) + d.get("Pages purgeable", 0))
        parts["free"] = d.get("Pages free", 0) + d.get("Pages speculative", 0)
        parts["other"] = max(0, total / GIB - sum(parts.values()))   # kernel + pages vm_stat does not itemise
        parts = {k: round(v, 1) for k, v in parts.items()}
        return parts["app"] + parts["wired"] + parts["compressed"], total / GIB, parts
    try:
        d = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":"); d[k] = int(v.split()[0]) * 1024
        total = d["MemTotal"]; avail = d.get("MemAvailable", d.get("MemFree", 0))
        cached = d.get("Buffers", 0) + d.get("Cached", 0) + d.get("SReclaimable", 0)
        parts = dict(app=round((total - d.get("MemFree", 0) - cached) / GIB, 1), cached=round(cached / GIB, 1),
                     free=round(d.get("MemFree", 0) / GIB, 1))
        return (total - avail) / GIB, total / GIB, parts
    except Exception:
        return 0, 0, {}


def mem_pressure():
    """macOS's own verdict: 1 normal, 2 warning, 4 critical."""
    r = sh(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]) if IS_MAC else None
    try:
        return int(r.stdout.strip())
    except Exception:
        return 1


def swap_gb():
    if IS_WIN:
        try:
            tot, av, ptot, pav = win_mem()   # the commit charge beyond physical memory is what sits in the page file
            return round(max(0, (ptot - pav) - (tot - av)) / GIB, 2)
        except Exception:
            return 0
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
    elif IS_WIN:
        r = sh(["netstat", "-e"], 8)
        for line in (r.stdout.splitlines() if r else []):   # first row with exactly two numbers is the byte counters
            f = line.split()
            if len(f) >= 3 and f[-1].isdigit() and f[-2].isdigit():
                rx, tx = int(f[-2]), int(f[-1]); break
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
    for mnt in (["/", "/System/Volumes/Data"] if IS_MAC else [os.environ.get("SystemDrive", "C:") + "\\"] if IS_WIN else ["/"]):
        try:
            u = shutil.disk_usage(mnt)
            if u.total not in seen:
                seen.add(u.total)
                out.append(dict(mount=mnt, pct=round(u.used / u.total * 100, 1), usedGB=round(u.used / 1e9), totalGB=round(u.total / 1e9)))
        except Exception:
            pass
    return out


_users = {}


def uname(uid):
    if IS_WIN:
        return ""   # the process list does not carry an owner there
    if uid not in _users:
        try:
            import pwd
            _users[uid] = pwd.getpwuid(uid).pw_name
        except Exception:
            _users[uid] = str(uid)
    return _users[uid]


def top_procs(key="cpu", n=14):
    rows = sorted((p for p in ps_table() if p["pid"] not in OWN), key=lambda p: p[key], reverse=True)[:n]
    return [dict(pid=p["pid"], name=base_name(p["comm"])[:34], app=app_name(p["comm"])[:34], cpu=p["cpu"],
                 rssMB=round(p["rss"]), user=uname(p["uid"]), up=p["etime"], mine=mine(p)) for p in rows]


APP_HIST = defaultdict(lambda: deque(maxlen=60))


RUNTIMES = {"node", "python", "python3", "python3.12", "python3.13", "python3.14", "Python", "bun", "deno", "java", "ruby",
            "llama-server", "uvicorn", "gunicorn", "php", "dotnet"}


def top_apps(n=18):
    """memory + CPU per application (helpers/renderers folded into their app), with its biggest processes.
    A bare runtime (node, python) is named after the service it runs, so 'node' reads 'Paperclip'."""
    rows = ps_table(); byp = {p["pid"]: p for p in rows}
    svcpid = {s["pid"]: s["name"].split(" · ")[0] for s in STATE["services"] if s.get("pid")}

    def owner(p):
        if p["pid"] == os.getpid():
            return "Pilothouse (this dashboard)"
        nm = app_name(p["comm"])
        if nm in RUNTIMES:
            x = p
            for _ in range(4):
                if x["pid"] in svcpid:
                    return svcpid[x["pid"]]
                x = byp.get(x["ppid"])
                if not x:
                    break
        return nm
    g = {}
    for p in rows:
        if p["pid"] in OWN:
            continue
        a = g.setdefault(owner(p), dict(memMB=0, resMB=0, cpu=0, n=0, mine=True, procs=[]))
        a["memMB"] += p["rss"]; a["resMB"] += p["res"]; a["cpu"] += p["cpu"]; a["n"] += 1
        a["mine"] = a["mine"] and mine(p); a["procs"].append(p)
    out = []
    for k, v in sorted(g.items(), key=lambda kv: kv[1]["memMB"], reverse=True)[:n]:
        APP_HIST[k].append(round(v["memMB"]))
        out.append(dict(name=k, memMB=round(v["memMB"]), swapMB=round(max(0, v["memMB"] - v["resMB"])), cpu=round(v["cpu"], 1),
                        n=v["n"], mine=v["mine"], hist=list(APP_HIST[k]),
                        procs=[dict(pid=p["pid"], name=os.path.basename(p["comm"])[:40], rssMB=round(p["rss"]),
                                    swapMB=round(max(0, p["rss"] - p["res"])), cpu=p["cpu"], up=p["etime"], mine=mine(p))
                               for p in sorted(v["procs"], key=lambda p: p["rss"], reverse=True)[:8]]))
    return out


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
                data.append(dict(name=j.get("Name", ""), cpu=j.get("CPUPerc", ""), mem=j.get("MemUsage", ""), memPct=j.get("MemPerc", ""),
                                 net=j.get("NetIO", ""), block=j.get("BlockIO", ""), pids=j.get("PIDs", "")))
            except Exception: pass
    _dock = dict(ts=time.time(), data=data)
    return data


_llm = dict(ts=0, data=[])


def http_json(url, body=None, t=2):
    import urllib.request
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, {"Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=t).read(8000001)
    if len(raw) > 8000000:
        raise ValueError("answer too large")
    return json.loads(raw or b"{}")


def ollama_servers():
    """[(port, pid)] for every `ollama serve` listening here (people run more than one)."""
    return sorted((port, pid) for port, (pid, pname) in listening().items()
                  if pname.lower().startswith("ollama") and "serve" in cmd_of(pid))


def until(iso):
    try:
        import datetime
        t = datetime.datetime.fromisoformat(re.sub(r"\.\d+", "", iso or ""))
        s = (t - datetime.datetime.now(t.tzinfo)).total_seconds()
        return "Forever" if s > 365 * 86400 else "%dm" % max(0, s // 60) if s < 7200 else "%dh" % (s // 3600)
    except Exception:
        return ""


def llm_loaded():
    if time.time() - _llm["ts"] < 5:
        return _llm["data"]
    out = []
    for port, pid in ollama_servers():
        try:
            for m in http_json("http://127.0.0.1:%d/api/ps" % port).get("models", []):
                size, vram = m.get("size", 0), m.get("size_vram", 0)
                det = m.get("details") or {}
                out.append(dict(name=m.get("name", ""), size=fmt_mb(size / 1048576), memMB=round(size / 1048576),
                                proc=("%d%% GPU" % round(vram / size * 100)) if size else "", ctx=str(m.get("context_length") or ""),
                                until=until(m.get("expires_at")), port=port, pid=pid,
                                params=det.get("parameter_size", ""), quant=det.get("quantization_level", "")))
        except Exception:
            pass
    _llm.update(ts=time.time(), data=out)
    return out


def sampler():
    acc, cur_min = [], int(time.time()) // 60
    cpu_stat(); time.sleep(1)   # prime the counters: the first reading has nothing to diff against
    while True:
        try:
            cpu, cu, cs = cpu_stat(); used, total, parts = mem_stat(); gpu = gpu_util(); rk, tk = net_rates()
            cores = os.cpu_count() or 1   # Windows has no load average: busy cores is the same idea
            ld = list(os.getloadavg()) if hasattr(os, "getloadavg") else [round(cpu / 100 * cores, 2)] * 3
            dsk = disk_use(); mp = round(used / total * 100, 1) if total else 0
            swap = swap_gb(); now = int(time.time())
            for k, v in (("ts", now), ("cpu", cpu), ("mem", mp), ("gpu", gpu), ("rx", rk), ("tx", tk),
                         ("load", round(ld[0], 2)), ("disk", dsk[0]["pct"] if dsk else 0)):
                M[k].append(v)
            SNAP.update(ts=now, cpu=cpu, cpuUser=cu, cpuSys=cs, load=ld, memUsedGB=round(used, 1),
                        memTotalGB=round(total, 1), memPct=mp, swapGB=swap, gpu=gpu, rxKBs=rk, txKBs=tk,
                        pressure=mem_pressure(), cores=os.cpu_count() or 1,
                        disk=dsk, procs=top_procs("cpu"), procsMem=top_procs("rss"), apps=top_apps(), memParts=parts,
                        docker=_dock["data"], llm=llm_loaded())   # docker stats are refreshed by the watcher (slow call)
            acc.append((cpu, mp, gpu, ld[0], dsk[0]["pct"] if dsk else 0, rk + tk, swap))
            if now // 60 != cur_min and acc:   # one averaged row per minute goes to disk
                avg = [round(sum(x[i] for x in acc) / len(acc), 2) for i in range(7)]
                avg[1] = max(x[1] for x in acc); avg[6] = max(x[6] for x in acc)   # memory + swap: keep the peak
                dbx("insert or replace into metric(ts, cpu, mem, gpu, load, disk, net, swap) values(?,?,?,?,?,?,?,?)",
                    [cur_min * 60] + avg)
                dbx("insert into app(ts, name, mem) values(?,?,?)", [(cur_min * 60, a["name"], a["memMB"]) for a in SNAP["apps"]], many=True)
                acc, cur_min = [], now // 60
        except Exception:
            pass
        time.sleep(2)


def metrics():
    d = dict(SNAP); d["hist"] = {k: list(v) for k, v in M.items()}; d["platform"] = PLATFORM
    return d


# ---- inventory (agents / models / repos) -------------------------------------
_inv = dict(ts=0, data=None)
# (id, name, bins, home dirs, pgrep pattern, description, default http port)
# (id, name, programs on PATH, data folders, process pattern, description, usual port)
# Folders may use ~ and %ENVVAR% so one list serves macOS, Linux and Windows.
_VSC = ["~/Library/Application Support/Code/User/globalStorage/", "~/.config/Code/User/globalStorage/", "%APPDATA%/Code/User/globalStorage/"]
AGENT_DEFS = [
    ("claude", "Claude Code", ["claude"], ["~/.claude"], r"claude-code/|native-binary/claude|\.local/bin/claude|/claude --|claude\.exe", "Anthropic coding agent (CLI sessions)", None),
    ("claude-desktop", "Claude desktop app", [], ["~/Library/Application Support/Claude", "%APPDATA%/Claude", "~/.config/Claude"], r"Claude\.app/Contents/MacOS/Claude|\\Claude\.exe", "Anthropic's desktop app", None),
    ("codex", "Codex (OpenAI)", ["codex"], ["~/.codex"], r"(^|/)codex( |$)|@openai/codex|codex\.exe", "OpenAI coding agent CLI", None),
    ("gemini", "Gemini CLI", ["gemini"], ["~/.gemini"], r"gemini-cli|/gemini( |$)", "Google coding agent CLI", None),
    ("copilot", "GitHub Copilot CLI", ["copilot"], ["~/.copilot"], r"/copilot( |$)|@github/copilot", "GitHub coding agent CLI", None),
    ("cursor", "Cursor", ["cursor", "cursor-agent"], ["~/.cursor"], r"Cursor Agent|Cursor\.app|cursor-agent|\\Cursor\.exe", "AI code editor", None),
    ("windsurf", "Windsurf", ["windsurf"], ["~/.codeium/windsurf", "~/.windsurf"], r"Windsurf", "AI code editor", None),
    ("zed", "Zed", ["zed"], ["~/.config/zed", "~/Library/Application Support/Zed"], r"Zed\.app|/zed( |$)", "Editor with built-in AI agent", None),
    ("opencode", "opencode", ["opencode"], ["~/.local/share/opencode", "~/.config/opencode"], r"(^|/)opencode( |$)|opencode-ai|opencode\.exe", "Open-source terminal coding agent", None),
    ("amp", "Amp", ["amp"], ["~/.config/amp", "~/.local/share/amp"], r"@sourcegraph/amp|/amp( |$)", "Sourcegraph coding agent", None),
    ("aider", "Aider", ["aider"], ["~/.aider", "~/.aider.conf.yml"], r"(^|/)aider( |$)|aider\.exe", "AI pair programmer", None),
    ("cline", "Cline", ["cline"], [d + "saoudrizwan.claude-dev" for d in _VSC] + ["~/.cline"], r"saoudrizwan\.claude-dev|/cline( |$)", "Coding agent for VS Code", None),
    ("roo", "Roo Code", [], [d + "rooveterinaryinc.roo-cline" for d in _VSC], r"rooveterinaryinc\.roo-cline", "Coding agent for VS Code", None),
    ("kilo", "Kilo Code", ["kilo", "kilocode"], [d + "kilocode.kilo-code" for d in _VSC] + ["~/.kilocode"], r"kilocode", "Coding agent for VS Code", None),
    ("continue", "Continue", ["cn"], ["~/.continue"], r"\.continue/|continue\.continue", "Open-source coding assistant", None),
    ("qwen", "Qwen Code", ["qwen"], ["~/.qwen"], r"qwen-code|/qwen( |$)", "Alibaba coding agent CLI", None),
    ("crush", "Crush", ["crush"], ["~/.local/share/crush", "~/.config/crush"], r"/crush( |$)", "Charm terminal coding agent", None),
    ("droid", "Factory Droid", ["droid"], ["~/.factory"], r"/droid( |$)|\.factory/", "Factory coding agent", None),
    ("kiro", "Kiro", ["kiro", "kiro-cli"], ["~/.kiro"], r"(^|/)kiro(-cli)?( |$)|Kiro\.app", "AWS agentic IDE and CLI", None),
    ("amazonq", "Amazon Q CLI", ["qchat"], ["~/.aws/amazonq"], r"qchat|amazonq", "AWS coding assistant CLI", None),
    ("goose", "Goose", ["goose"], ["~/.config/goose"], r"(^|/)goose( |$)|goose\.exe", "Block AI agent", None),
    ("interpreter", "Open Interpreter", ["interpreter"], ["~/.config/open-interpreter"], r"open.interpreter", "Natural-language code exec", None),
    ("openclaw", "OpenClaw", ["openclaw"], ["~/.openclaw"], r"openclaw", "Self-hosted AI assistant", None),
    ("hermes", "Hermes Agent", ["hermes"], ["~/.hermes"], r"\.hermes/|hermes_cli|hermes-webui", "Nous local agent + WebUI", 8787),
    ("paperclip", "Paperclip", [], ["~/.paperclip"], r"paperclip", "Local agent platform", 3100),
    ("llm", "llm (Datasette)", ["llm"], ["~/Library/Application Support/io.datasette.llm", "~/.config/io.datasette.llm"], r"/llm( |$)", "CLI for many LLMs", None),
    ("chatgpt", "ChatGPT desktop app", [], ["~/Library/Application Support/com.openai.chat", "%LOCALAPPDATA%/Packages/OpenAI.ChatGPT-Desktop_2p2nqsd0c76g0"], r"ChatGPT\.app|ChatGPT\.exe", "OpenAI's desktop app", None),
    # local model runtimes and UIs
    ("ollama", "Ollama", ["ollama"], ["~/.ollama"], r"(^|/)ollama( |$)|/ollama/|ollama\.exe|ollama app", "Local LLM runtime (servers + model runners)", 11434),
    ("lmstudio", "LM Studio", ["lms"], ["~/.lmstudio", "~/.cache/lm-studio"], r"LM Studio|lm-studio", "Local model desktop app", 1234),
    ("llamacpp", "llama.cpp", ["llama-server", "llama-cli"], [], r"llama-server|llama-cli", "Local model inference server", 8080),
    ("mlx", "MLX LM", ["mlx_lm.server", "mlx_lm.generate"], [], r"mlx_lm", "Apple-silicon model server", 8080),
    ("vllm", "vLLM", ["vllm"], [], r"(^|/)vllm( |$)|-m vllm", "High-throughput model server", 8000),
    ("localai", "LocalAI", ["local-ai"], ["~/.localai"], r"local-ai", "OpenAI-compatible local server", 8080),
    ("jan", "Jan", [], ["~/jan", "~/.jan", "~/Library/Application Support/Jan", "%APPDATA%/Jan"], r"Jan\.app|\\Jan\.exe|/jan( |$)", "Local AI desktop app", 1337),
    ("gpt4all", "GPT4All", ["gpt4all"], ["~/.cache/gpt4all", "~/Library/Application Support/nomic.ai/GPT4All", "%LOCALAPPDATA%/nomic.ai/GPT4All"], r"gpt4all", "Local model app", 4891),
    ("msty", "Msty", [], ["~/Library/Application Support/Msty", "~/.config/Msty", "%APPDATA%/Msty"], r"Msty", "Local and cloud chat app", None),
    ("anythingllm", "AnythingLLM", [], ["~/Library/Application Support/anythingllm-desktop", "~/.config/anythingllm-desktop", "%APPDATA%/anythingllm-desktop"], r"anythingllm", "Chat with documents, local or cloud", 3001),
    ("openwebui", "Open WebUI", ["open-webui"], ["~/.open-webui"], r"open-webui", "Chat UI for local LLMs", 8080),
    ("comfyui", "ComfyUI", [], ["~/ComfyUI", "~/Documents/ComfyUI"], r"ComfyUI", "Node-based image generation", 8188),
    ("textgen", "text-generation-webui", [], ["~/text-generation-webui"], r"text-generation-webui", "Web UI for local models", 7860),
]
for _pf in (os.path.join(ROOT, "agents.json"), os.path.join(DATA_DIR, "agents.json")):
    try:   # your own tools: [{"id","name","bins":[],"dirs":[],"process":"regex","desc","port"}]
        with open(_pf, encoding="utf-8") as _f:
            for a in json.load(_f):
                re.compile(a.get("process") or a["id"])
                AGENT_DEFS[:] = [d for d in AGENT_DEFS if d[0] != a["id"]]   # your entry replaces a built-in one
                AGENT_DEFS.append((a["id"], a.get("name") or a["id"], a.get("bins") or [], a.get("dirs") or [],
                                   a.get("process") or re.escape(a["id"]), a.get("desc") or "", a.get("port")))
    except Exception:
        pass


def xp(path):
    """~ and %VAR% / $VAR expanded, so one path list serves every OS."""
    return os.path.normpath(os.path.expandvars(os.path.expanduser(path)))


MODEL_DIRS = ["~/.ollama/models", "~/models", "~/.cache/lm-studio/models", "~/.lmstudio/models",
              "~/.cache/huggingface/hub", "~/.hermes/models", "~/.local/share/models"]
REPO_ROOTS = ["~/Desktop", "~/Documents", "~/Projects", "~/projects", "~/dev", "~/Developer", "~/source/repos", "~/work",
              "~/code", "~/src", "~/git", "~/repos", "~/workspace", "~/go/src"]
REPO_ROOTS += [p for p in os.environ.get("DASH_REPOS", "").split(os.pathsep) if p.strip()]


def web_url(remote):
    """the web page of a git remote, or None. Only a plain https://host/path survives: no credentials, no odd characters."""
    r = re.sub(r"^(https?://)[^/@]*@", r"\1", (remote or "").strip())   # drop user:token@
    m = re.match(r"^(?:ssh://)?git@([\w.-]+)[:/](.+)$", r)
    if m:
        r = "https://%s/%s" % (m.group(1), m.group(2))
    if r.endswith(".git"):
        r = r[:-4]
    return r if re.match(r"^https?://[\w.-]+(:\d+)?(/[\w.~/+-]*)?$", r) else None


_ver = {}


def version_of(binpath):
    if binpath not in _ver:
        r = sh([binpath, "--version"], 4)
        m = re.search(r"\d+\.\d+[\w.\-]*", (r.stdout + r.stderr) if r else "")
        _ver[binpath] = m.group() if m else ""
    return _ver[binpath]


AGENT_APP = {"claude": "Claude", "cursor": "Cursor", "lmstudio": "LM Studio", "jan": "Jan", "gpt4all": "GPT4All", "ollama": "Ollama",
             "hermes": "Hermes", "codex": "Codex"}
CONFIG_NAMES = ("settings.json", "config.json", "config.toml", "config.yaml", "config.yml", ".env.example", "openclaw.json", "mcp.json",
                "CLAUDE.md", "AGENTS.md")
_du = {}


def dir_facts(path):
    """size, last change and the notable things inside an agent's data folder. Measuring a big folder takes seconds,
    so it happens in the background: the first answer may be empty, later ones are cached for 10 minutes."""
    hit = _du.get(path)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    if path not in _du_busy:
        _du_busy.add(path)

        def work():
            try:
                _du[path] = (time.time(), measure_dir(path))
            finally:
                _du_busy.discard(path); _inv["ts"] = 0   # the next inventory request includes it
        threading.Thread(target=work, daemon=True).start()
    return hit[1] if hit else dict(size="", changed="", items=[], configs=[], pending=True)


_du_busy = set()


def measure_dir(path):
    full = xp(path); f = dict(size="", changed="", items=[], configs=[])
    if os.path.isdir(full):
        f["size"] = gb(dir_size(full, 60000))
        try:
            ents = sorted(os.scandir(full), key=lambda e: e.stat().st_mtime, reverse=True)
            f["changed"] = days_ago(ents[0].stat().st_mtime) if ents else ""
            f["items"] = [e.name + ("/" if e.is_dir() else "") for e in ents[:14]]
            f["configs"] = [e.name for e in ents if e.is_file() and e.name in CONFIG_NAMES]
            f["count"] = len(ents)
        except Exception:
            pass
    return f


def terminal_cmd():
    """the terminal program to open on Linux, as a command prefix."""
    t = next((shutil.which(x) for x in ("x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "xterm") if shutil.which(x)), None)
    return [t] if t else None


def how_installed(binpath):
    b = os.path.realpath(binpath) if binpath else ""
    return ("Homebrew" if "/homebrew/" in b or "/Cellar/" in b else "npm (global)" if "node_modules" in b else
            "its own installer" if "/.local/" in b or "/.claude/" in b else "pip / pipx" if "/pipx/" in b or "site-packages" in b else "")


def agent_procs(pat, cmds=None):
    """processes of an agent, matched on the full command line."""
    hit = {pid for pid, c in (cmds or all_cmds()).items() if re.search(pat, c)}
    return [p for p in ps_table() if p["pid"] in hit and p["pid"] not in OWN and p["pid"] not in ME]


def inv_agents():
    out, ports, cmds = [], listening(), all_cmds()   # one process listing for all the tools
    for aid, name, bins, homes, pat, desc, port in AGENT_DEFS:
        path = ""
        for b in bins:
            if shutil.which(b):
                path = shutil.which(b); break
        home = next((h for h in homes if "%" not in xp(h) and os.path.exists(xp(h))), "")
        if not path and not home:
            continue
        procs = agent_procs(pat, cmds); pids = {p["pid"] for p in procs}
        facts = dir_facts(home) if home else {}
        app = AGENT_APP.get(aid); app = app if app and os.path.isdir("/Applications/%s.app" % app) else None
        key = name.split(" ")[0].lower()
        out.append(dict(id=aid, name=name, desc=desc, home=home or (homes[0] if homes else ""), installed=True,
                        url="http://localhost:%d" % port if (port and port in ports and web_kind(port) == "page") else None, port=port,
                        bin=path.replace(HOME, "~"), version=version_of(path) if path else "",
                        running=bool(procs), procs=len(procs), memMB=round(sum(p["rss"] for p in procs)),
                        cpu=round(sum(p["cpu"] for p in procs), 1), canStop=any(mine(p) for p in procs),
                        top=[dict(pid=p["pid"], name=os.path.basename(p["comm"])[:34], rssMB=round(p["rss"]), cpu=p["cpu"])
                             for p in sorted(procs, key=lambda p: p["rss"], reverse=True)[:6]],
                        installedVia=how_installed(path), app=app, facts=facts, canLaunch=bool(path) and (IS_MAC or IS_WIN or bool(terminal_cmd())),
                        services=[dict(id=s["id"], name=s["name"], up=s["up"], start="start" in (s.get("actions") or []))
                                  for s in STATE["services"] if s.get("pid") in pids or (not s["up"] and s["name"].lower().startswith(key))]))
    return out


def agent_action(aid, act="stop"):
    """stop = SIGTERM every process of it that is yours; app = open its desktop app; launch = run its CLI in a new Terminal window."""
    d = next((d for d in AGENT_DEFS if d[0] == aid), None)
    if not d:
        return False, "unknown agent"
    if act == "app":
        app = AGENT_APP.get(aid)
        if not (IS_MAC and app and os.path.isdir("/Applications/%s.app" % app)):
            return False, "no desktop app found for it"
        r = sh(["open", "-a", app], 10)
        return bool(r) and r.returncode == 0, "opened " + app
    if act == "launch":
        binp = next((shutil.which(b) for b in d[2] if shutil.which(b)), "")
        if not binp:
            return False, "no command-line program found for it"
        if IS_MAC:
            os.makedirs(DATA, exist_ok=True)
            script = os.path.join(DATA, "launch-%s.command" % aid)   # Terminal runs a .command file: no AppleScript permission needed
            import shlex
            with open(os.open(script, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700), "w") as f:
                f.write("#!/bin/sh\ncd \"$HOME\"\nexec %s\n" % shlex.quote(binp))
            os.chmod(script, 0o700)
            r = sh(["open", "-a", "Terminal", script], 10)
        elif IS_WIN:
            r = None if re.search(r'[&|<>^%"!]', binp) else sh(["cmd", "/c", "start", "", "cmd", "/K", binp], 10)
        else:
            term = terminal_cmd()
            r = sh(term + ["-e", binp], 10) if term else None
            if not term:
                return False, "no terminal program found (looked for x-terminal-emulator, gnome-terminal, konsole, xfce4-terminal, xterm)"
        event("action", "Launched %s in Terminal" % d[1])
        return bool(r) and r.returncode == 0, "started %s in a new Terminal window" % d[1]
    if act != "stop":
        return False, "unsupported"
    _ps["ts"] = 0; n = 0
    for p in agent_procs(d[4]):
        if mine(p):
            n += 1 if kill_pid(p["pid"], IS_WIN)[0] else 0
    event("action", "Stopped agent %s (%d processes)" % (aid, n)); _inv["ts"] = 0; _ps["ts"] = 0
    return n > 0, "sent stop to %d process%s" % (n, "" if n == 1 else "es")


def gb(n):
    return "%.1f GB" % (n / 1e9) if n >= 1e9 else "%d MB" % round(n / 1e6)


def days_ago(ts):
    d = int((time.time() - ts) / 86400)
    return "today" if d < 1 else "%d day%s ago" % (d, "" if d == 1 else "s") if d < 60 else "%d months ago" % (d // 30)


def ollama_on_disk():
    """every model Ollama has downloaded, read from its manifest files - works while Ollama is stopped."""
    root = os.path.expanduser(os.environ.get("OLLAMA_MODELS") or "~/.ollama/models")
    out, base = [], os.path.join(root, "manifests")
    for dp, _, files in os.walk(base):
        for f in files:
            full = os.path.join(dp, f); parts = os.path.relpath(full, base).split(os.sep)
            if len(parts) < 4:
                continue
            host, ns, model, tag = parts[0], "/".join(parts[1:-2]), parts[-2], parts[-1]
            try:
                with open(full, encoding="utf-8") as fh:
                    man = json.load(fh)
                size = sum(l.get("size", 0) for l in man.get("layers", [])) + (man.get("config") or {}).get("size", 0)
            except Exception:
                continue
            name = ("" if ns == "library" else ns + "/") + model + ":" + tag
            out.append(dict(name=name, bytes=size, mtime=os.path.getmtime(full), path=full, official=host == "registry.ollama.ai" and ns == "library"))
    return out


_scan = dict(ts=0, files=[])
MODEL_EXT = (".gguf", ".safetensors", ".onnx", ".ckpt", ".mlmodel", ".pt", ".pth", ".bin")


def scan_model_files():
    """large model files anywhere under the home folder (bounded depth, slow): refreshed in the background."""
    skip = {"node_modules", ".git", "site-packages", ".Trash", ".ollama", "CloudStorage", "Containers", "Mobile Documents", "Caches",
            "AppData" if False else "$Recycle.Bin", "Windows", "Program Files"}
    ext = tuple(e for e in MODEL_EXT if e != ".bin")   # .bin is too generic to trust outside model folders
    found = []
    for path, e, isdir in walk(HOME, 7, skip, limit=600000):
        if isdir or not e.name.lower().endswith(ext) or "huggingface" in path:
            continue
        try:
            if e.stat(follow_symlinks=False).st_size > 40e6:
                found.append(path)
        except OSError:
            pass
        if len(found) >= 300:
            break
    _scan.update(ts=time.time(), files=found)


def model_scanner():
    while True:
        try:
            scan_model_files(); _inv["ts"] = 0
        except Exception:
            pass
        time.sleep(900)


def inv_models():
    out = []
    loaded = {m["name"]: m for m in llm_loaded()}
    up = bool(ollama_servers())
    for m in sorted(ollama_on_disk(), key=lambda m: -m["bytes"]):
        l = loaded.get(m["name"])
        out.append(dict(name=m["name"], size=gb(m["bytes"]), bytes=m["bytes"], loaded=bool(l), kind="ollama", ollama=True, ollamaUp=up,
                        runtime="Ollama", where="~/.ollama/models", modified=days_ago(m["mtime"]),
                        proc=l["proc"] if l else "", ctx=l["ctx"] if l else "", until=l["until"] if l else "",
                        link=("https://ollama.com/library/" + m["name"].split(":")[0]) if m["official"] else "https://ollama.com/search?q=" + m["name"].split(":")[0].split("/")[-1]))
    cmds = "\n".join(all_cmds().values())
    files, seen = list(_scan["files"]), set()
    for d in MODEL_DIRS:   # the usual places are checked every time (fast); the home-wide scan fills in the rest
        dd = os.path.expanduser(d)
        if os.path.isdir(dd) and ".ollama" not in dd and "huggingface" not in dd:
            files += [pth for pth, e, isdir in walk(dd, 5, {"node_modules", ".git"}, limit=40000)
                      if not isdir and e.name.lower().endswith((".gguf", ".safetensors"))][:80]
    for f in files:
        if f in seen or not os.path.isfile(f):
            continue
        seen.add(f)
        nm, sz = os.path.basename(f), os.path.getsize(f); held = f in cmds or nm in cmds
        out.append(dict(name=nm, size=gb(sz), bytes=sz, loaded=held, kind="file", ollama=False, ollamaUp=up,
                        runtime={"gguf": "GGUF (llama.cpp)", "onnx": "ONNX", "safetensors": "safetensors"}.get(nm.rsplit(".", 1)[-1].lower(), nm.rsplit(".", 1)[-1]),
                        where=os.path.dirname(f).replace(HOME, "~"), path=f, modified=days_ago(os.path.getmtime(f)),
                        proc="a running server" if held else "", ctx="", until="its process runs" if held else "", link=None))
    hub = os.path.expanduser(os.environ.get("HF_HOME", "~/.cache/huggingface") + "/hub")
    if os.path.isdir(hub):   # Hugging Face downloads (transformers, MLX, diffusers ...)
        for path in sorted(os.path.join(hub, x) for x in os.listdir(hub) if x.startswith("models--")):
            nbytes = dir_size(path, 20000)
            if nbytes > 1e6:
                nm = os.path.basename(path)[8:].replace("--", "/")
                out.append(dict(name=nm, size=gb(nbytes), bytes=nbytes, loaded=False, kind="hf", ollama=False, ollamaUp=up,
                                runtime="Hugging Face cache", where=path.replace(HOME, "~"), path=path, modified=days_ago(os.path.getmtime(path)),
                                proc="", ctx="", until="", link="https://huggingface.co/" + nm))
    return out


# Running git inside a folder runs whatever that folder's .git/config says (core.fsmonitor, filter.*.clean,
# gpg.program, hooks ...), so a hostile repository you only unpacked could run code as you. So:
#   1. branch, origin and last commit are read from plain files in .git - nothing is executed;
#   2. git itself (for uncommitted / ahead / behind) runs only when the repo's own config holds nothing but keys
#      from this allowlist, and then with hooks, fsmonitor and signature checks switched off on the command line.
GIT_SAFE = {"core": {"repositoryformatversion", "filemode", "bare", "logallrefupdates", "ignorecase", "precomposeunicode",
                     "symlinks", "autocrlf", "eol", "safecrlf", "quotepath", "longpaths",
                     "hookspath"},   # harmless here only because git_run overrides it on the command line
            "extensions": {"worktreeconfig", "objectformat"},   # config.worktree is then held to this same list
            "remote": {"url", "fetch", "pushurl", "push", "tagopt", "prune"},
            "branch": {"remote", "merge", "rebase", "pushremote", "description", "vscode-merge-base", "github-pr-owner-number"},
            "user": {"name", "email"}, "submodule": {"url", "active"}, "pull": {"rebase", "ff"},
            "push": {"default", "autosetupremote"}, "init": {"defaultbranch"}, "lfs": {"repositoryformatversion"}}


def git_config(gd, name="config"):
    """.git/config as [(section, subsection, key, value)]; None when any line is not understood for certain."""
    try:
        with open(os.path.join(gd, name), encoding="utf-8") as f:
            text = f.read(200000)
    except (OSError, UnicodeError):
        return None
    out, sec, sub = [], None, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#;":
            continue
        if line.endswith("\\"):   # a continued line: do not guess
            return None
        m = re.match(r'^\[([A-Za-z0-9-]+)(?:\s+"([^"\\]*)")?\]$', line)
        if m:
            sec, sub = m.group(1).lower(), m.group(2); continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*)\s*(?:=\s*(.*))?$", line)
        if not m or sec is None:
            return None
        out.append((sec, sub, m.group(1).lower(), (m.group(2) or "").strip().strip('"')))
    return out


def git_trusted(gd, cfg):
    """may git be run in this repository? only when its own config cannot make git start another program."""
    extra = git_config(gd, "config.worktree") if os.path.exists(os.path.join(gd, "config.worktree")) else []
    return (cfg is not None and extra is not None and os.path.isdir(gd) and not os.path.exists(os.path.join(gd, "commondir"))
            and all(sec in GIT_SAFE and key in GIT_SAFE[sec] for sec, _, key, _ in cfg + extra))


def git_run(d, *args):
    return sh(["git", "--no-optional-locks", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
               "-c", "log.showSignature=false", "-C", d] + list(args), 6)


def since_words(ts):
    s = max(0, int(time.time() - ts))
    for n, w in ((86400 * 365, "year"), (86400 * 30, "month"), (86400 * 7, "week"), (86400, "day"), (3600, "hour"), (60, "minute")):
        if s >= n:
            return "%d %s%s ago" % (s // n, w, "" if s // n == 1 else "s")
    return "just now"


def git_text(gd, cfg):
    """(branch, origin url, last activity time, last commit subject) from plain files inside .git."""
    def rd(*parts):
        try:
            with open(os.path.join(gd, *parts), encoding="utf-8", errors="replace") as f:
                return f.read(4000)
        except OSError:
            return ""
    head = rd("HEAD").strip()
    branch = head[16:] if head.startswith("ref: refs/heads/") else "detached"
    remote = next((v for sec, sub, k, v in (cfg or []) if sec == "remote" and sub == "origin" and k == "url"), "")
    ts, subj = 0, ""
    try:   # the reflog: one line per change of HEAD, newest last
        with open(os.path.join(gd, "logs", "HEAD"), "rb") as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 20000))
            lines = f.read().decode("utf-8", "replace").splitlines()
        for line in reversed(lines):
            m = re.match(r"^[0-9a-f]{40,64} [0-9a-f]{40,64} .*> (\d+) [+-]\d{4}\t(.*)$", line)
            if not m:
                continue
            ts = ts or int(m.group(1))
            if m.group(2).startswith("commit"):
                ts, subj = int(m.group(1)), m.group(2).split(": ", 1)[-1]; break
    except OSError:
        pass
    return re.sub(r"[^\w./+-]", "", branch)[:80] or "detached", remote, ts, subj


def inv_repos():
    repos, seen = [], set()
    roots = [os.path.expanduser(r) for r in REPO_ROOTS if os.path.isdir(os.path.expanduser(r))]
    gits, has_git = set(), which("git")
    for root in roots:
        gits.update(pth for pth, e, isdir in walk(root, 4, {"node_modules", ".git", "Library", "AppData"}, limit=200000)
                    if isdir and e.name == ".git")
    for gd in sorted(gits):
        d = os.path.dirname(gd)
        if d in seen or len(repos) >= 120:
            continue
        seen.add(d)
        cfg = git_config(gd); branch, remote, ts, subj = git_text(gd, cfg)
        r = dict(path=d.replace(HOME, "~"), branch=branch, remote=redact(remote)[:200], web=web_url(remote), dirty=0, ahead=0, behind=0,
                 tracking=False, lastTs=ts, last=since_words(ts) if ts else "", subject=redact(subj)[:80], checked=False,
                 note="" if has_git else "git is not installed")
        if has_git and git_trusted(gd, cfg):
            # one status call gives how far it is from its upstream and what is uncommitted
            st = git_run(d, "status", "--porcelain=v1", "-b")
            lines = st.stdout.splitlines() if st and st.returncode == 0 else []
            head = lines[0] if lines else ""
            num = lambda k: int((re.search(k + r" (\d+)", head) or [0, 0])[1])
            if lines:
                r.update(dirty=max(0, len(lines) - 1), ahead=num("ahead"), behind=num("behind"), tracking="..." in head, checked=True)
            lg = git_run(d, "log", "-1", "--no-show-signature", "--format=%ct|%s")
            if lg and lg.returncode == 0 and "|" in lg.stdout:
                ct, sj = lg.stdout.strip().split("|", 1)
                if ct.isdigit():
                    r.update(lastTs=int(ct), last=since_words(int(ct)), subject=redact(sj)[:80])
        elif has_git:
            r["note"] = "status not read: this repository's own git settings can start other programs, so git is not run in it"
        repos.append(r)
    return repos


ENGINES = ((r"\.colima/", "Colima"), (r"\.orbstack/", "OrbStack"), (r"\.rd/", "Rancher Desktop"), (r"podman", "Podman"),
           (r"\.docker/(run|desktop)/|dockerDesktop|docker_engine", "Docker Desktop"))


def engine_name(host):
    """which Docker engine a socket address belongs to."""
    return next((n for p, n in ENGINES if re.search(p, host or "")), None) or ("Docker Desktop" if IS_MAC or IS_WIN else "Docker Engine")


def docker_engine():
    """the engine the docker CLI points at -> (name, command that starts it without root or None, what to tell the user)."""
    r = sh(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], 5, force=True)   # reads a file, never hangs
    name = engine_name((r.stdout.strip() if r and r.returncode == 0 else "") or os.environ.get("DOCKER_HOST", ""))
    cmd = None
    if name == "Colima":
        hint = "colima start"
        if which("colima"):
            cmd = ["brew", "services", "restart", "colima"] if brew_services().get("colima") else ["colima", "start"]
    elif name == "Podman":
        hint = "podman machine start"
        if which("podman"): cmd = ["podman", "machine", "start"]
    elif IS_MAC:
        app = "Docker" if name == "Docker Desktop" else name
        hint = "open the %s app" % name
        if os.path.isdir("/Applications/%s.app" % app): cmd = ["open", "-a", app]
    elif IS_WIN:
        hint = "start %s from the Start menu" % name
    else:
        hint = "sudo systemctl start docker"   # needs root, so it is only shown
    return name, cmd, hint


def docker_down():
    """why the Docker tab is empty: None when docker is not installed, else which engine is silent and how to start it."""
    if not which("docker"):
        return None
    name, cmd, hint = docker_engine()
    return dict(engine=name, canStart=bool(cmd), hint=hint)


def start_engine():
    r = sh(["docker", "version", "--format", "{{.Server.Version}}"], 8, force=True)
    if r and r.returncode == 0 and r.stdout.strip():
        _inv["ts"] = 0
        return True, "Docker is already running"
    name, cmd, hint = docker_engine()
    if not cmd:
        return False, "Pilothouse cannot start %s by itself. To start it: %s" % (name, hint)

    def go():
        r = sh(cmd, 300)
        if not (r and r.returncode == 0):
            event("alert", "Could not start %s. Try: %s" % (name, hint))
        _hung.pop("docker", None); _inv["ts"] = 0; _wake.set()
    threading.Thread(target=go, daemon=True).start()
    event("action", "You started %s" % name)
    return True, "Starting %s. Containers come back in about a minute." % name


def inv_docker():
    """disk usage, containers, images and volumes (`docker system df`)."""
    r = sh(["docker", "system", "df", "-v", "--format", "{{json .}}"], 25) if which("docker") else None
    try:
        j = json.loads(r.stdout)
    except Exception:
        return None
    imgs = [dict(name="%s:%s" % (i.get("Repository"), i.get("Tag")), id=(i.get("ID") or "")[7:19], size=i.get("Size"),
                 unique=i.get("UniqueSize"), created=i.get("CreatedSince"), used=int(i.get("Containers") or 0),
                 mb=to_mb(i.get("Size")), umb=to_mb(i.get("UniqueSize")), dangling="<none>" in str(i.get("Repository")))
            for i in (j.get("Images") or [])]
    vols = [dict(name=v.get("Name"), size=v.get("Size"), used=int(v.get("Links") or 0), mb=to_mb(v.get("Size")))
            for v in (j.get("Volumes") or [])]
    cons = [dict(name=c.get("Names"), image=c.get("Image"), state=c.get("State"), status=c.get("Status"),
                 size=c.get("Size"), created=c.get("RunningFor") or c.get("CreatedAt"), ports=c.get("Ports"),
                 mounts=c.get("Mounts")) for c in (j.get("Containers") or [])]
    imgs.sort(key=lambda x: x["mb"], reverse=True); vols.sort(key=lambda x: x["mb"], reverse=True)
    tot = {}   # the summary de-duplicates shared layers; summing the rows would overstate
    r2 = sh(["docker", "system", "df", "--format", "{{json .}}"], 15)
    for line in (r2.stdout.splitlines() if r2 else []):
        try:
            t = json.loads(line); tot[t["Type"]] = dict(size=t.get("Size"), reclaim=t.get("Reclaimable"), n=t.get("TotalCount"), active=t.get("Active"))
        except Exception:
            pass
    return dict(images=imgs, volumes=vols, containers=cons, totals=tot,
                unusedImages=sum(1 for i in imgs if not i["used"]), dangling=sum(1 for i in imgs if i["dangling"]),
                stopped=sum(1 for c in cons if c["state"] != "running"))


EDITORS = [("VS Code", "code"), ("Cursor", "cursor"), ("VS Code Insiders", "code-insiders"), ("Zed", "zed"), ("Sublime Text", "subl")]


def open_path(kind, key, how="folder"):
    """open a folder or file in the file manager / editor / terminal. The browser names WHICH known thing
    (a discovered repo, an agent's data dir, a service's folder or log); the path itself comes from discovery."""
    path = None
    if kind == "repo":
        want = os.path.expanduser(str(key))
        path = next((os.path.expanduser(r["path"]) for r in (inventory().get("github", {}).get("repos") or [])
                     if os.path.expanduser(r["path"]) == want), None)
    elif kind == "agent":
        path = next((xp(a["home"]) for a in inventory().get("agents", []) if a["id"] == key and a.get("home")), None)
    elif kind == "usagedir":   # a folder some recorded session ran in
        want = os.path.expanduser(str(key))
        path = want if any(d.get("cwd") == want for d in list(USAGE.values())) else None
    elif kind == "model":
        path = next((m["path"] for m in inventory().get("models", []) if m.get("path") == key), None)
        path = os.path.dirname(path) if path and os.path.isfile(path) and how == "folder" else path
    elif kind == "agentcfg":   # key = "<agent id>/<file name>", the file must be one we listed
        aid, _, fn = str(key).partition("/")
        a = next((a for a in inventory().get("agents", []) if a["id"] == aid), None)
        if a and fn in (a.get("facts") or {}).get("configs", []):
            path = os.path.join(xp(a["home"]), fn); how = "editor" if any(which(b) for _, b in EDITORS) else "folder"
    elif kind in ("cwd", "log"):
        sv = svc(str(key))
        if sv and sv.get("pid"):
            path = cwd_of(sv["pid"]) if kind == "cwd" else host_log_file(sv["pid"])
    if not path or not os.path.exists(path) or (kind == "usagedir" and not os.path.isdir(path)):
        return False, "that location is not one this dashboard discovered"
    if IS_WIN and how in ("terminal", "editor") and re.search(r'[&|<>^%"!]', path):
        return False, "this path has characters the Windows command prompt would run as commands: open it by hand"
    if IS_WIN and how == "terminal":   # a new console that starts in the folder: the path never passes through cmd's parser
        try:
            subprocess.Popen(["cmd.exe"], cwd=path if os.path.isdir(path) else os.path.dirname(path), creationflags=0x10)
            return True, "opened a terminal in " + path.replace(HOME, "~")
        except OSError as e:
            return False, "could not open a terminal: %s" % e.strerror
    cmd = open_cmd(path, how)
    if not cmd:
        return False, "not available on this system"
    r = sh(cmd, 10)
    ok = bool(r) and (r.returncode == 0 or (IS_WIN and cmd[0] == "explorer"))   # explorer reports 1 even when it worked
    return ok, "opened " + path.replace(HOME, "~")


def open_cmd(path, how):
    """the command that opens a path in the file manager, an editor or a terminal on this OS."""
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if how == "editor":
        ed = next((shutil.which(b) for _, b in EDITORS if shutil.which(b)), None)
        return ((["cmd", "/c", ed, path] if IS_WIN else [ed, path]) if ed else None)   # editors ship as .cmd shims on Windows
    if how == "terminal":
        if IS_MAC:
            return ["open", "-a", "Terminal", folder]
        if IS_WIN:
            return ["cmd", "/c", "start", "", "/D", folder, "cmd"]
        t = terminal_cmd()
        return (t + (["--workdir", folder] if "konsole" in t[0] else ["--working-directory=" + folder])) if t and "xterm" not in t[0] else None
    if os.path.isfile(path):   # show the file selected in its folder
        return ["open", "-R", path] if IS_MAC else ["explorer", "/select," + path] if IS_WIN else ["xdg-open", folder]
    if IS_MAC:   # `open` on a folder that is a bundle (Something.app, .workflow ...) would run it: show it in Finder instead
        return ["open", "-R", path] if "." in os.path.basename(path.rstrip("/"))[1:] else ["open", path]
    return ["explorer", path] if IS_WIN else (["xdg-open", path] if shutil.which("xdg-open") else None)


_inv_lock = threading.Lock()


def inventory(force=False):
    if _inv["data"] and not force and time.time() - _inv["ts"] < 60:
        return _inv["data"]
    with _inv_lock:   # a scan takes seconds: callers that arrive meanwhile wait for it and share its result
        if _inv["data"] and time.time() - _inv["ts"] < (5 if force else 60):
            return _inv["data"]
        return _inventory()


def _inventory():
    accounts = []
    if which("gh"):
        r = sh("gh auth status")
        if r:
            for m in re.finditer(r"account (\S+).*?Active account: (true|false)", r.stdout + r.stderr, re.S):
                accounts.append(dict(login=m.group(1), active=m.group(2) == "true"))
    ed = next(((n, b) for n, b in EDITORS if which(b)), None)
    dk = inv_docker()
    data = dict(ts=int(time.time()), agents=inv_agents(), models=inv_models(), docker=dk, dockerDown=None if dk else docker_down(),
                tools=dict(editor=ed[0] if ed else None, opener=IS_MAC or IS_WIN or which("xdg-open"),
                           terminal=IS_MAC or IS_WIN or bool(terminal_cmd()), platform=PLATFORM),
                github=dict(accounts=accounts, repos=inv_repos()))
    _inv.update(ts=time.time(), data=data)
    return data


# ---- usage: tokens and cost per session, per agent ----------------------------
# List prices in US dollars per million tokens: (input, output, cache-read multiplier of the input price).
# Cache writes cost 1.25x input (5-minute) or 2x input (1-hour); batch requests are half price.
# Source: Anthropic's published price list as of 2026-11. Override or add models in pricing.json next to this file
# or in DASH_DATA: {"model-id-prefix": [input, output, cache_read_multiplier]}.
PRICES = {
    "claude-fable-5-2": (8, 40, 0.025), "claude-mythos-5-2": (8, 40, 0.025),
    "claude-fable-5-1": (10, 50, 0.025), "claude-mythos-5-1": (10, 50, 0.025), "claude-fable-5": (10, 50, 0.1),
    "claude-opus-5-5": (4, 20, 0.05), "claude-opus-5": (5, 25, 0.1), "claude-opus-4-8": (5, 25, 0.1),
    "claude-opus-4-7": (5, 25, 0.1), "claude-opus-4-6": (5, 25, 0.1),
    "claude-sonnet-5-5": (2, 10, 0.05), "claude-sonnet-5": (2, 10, 0.1), "claude-sonnet-4-6": (3, 15, 0.1),
    "claude-haiku-5-5": (0.10, 0.50, 0.1), "claude-haiku-4-5": (1, 5, 0.1),
}
FAST_PRICES = {"claude-opus-5-5": (8, 40), "claude-opus-5": (10, 50), "claude-opus-4-8": (10, 50),
               "claude-fable-5-2": (16, 80), "claude-mythos-5-2": (16, 80)}
for _pf in (os.path.join(ROOT, "pricing.json"), os.path.join(DATA_DIR, "pricing.json")):
    try:
        with open(_pf) as _f:
            PRICES.update({k: tuple(v) for k, v in json.load(_f).items() if isinstance(v, list) and len(v) == 3})
    except Exception:
        pass
_PKEYS = sorted(PRICES, key=len, reverse=True)
EXTRA_PRICES = {}   # filled from a public community price list, only when the user asks for it
PRICE_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"


def load_price_map(raw):
    """LiteLLM's price map (dollars per token) -> {model: (input $/M, output $/M, cache-read multiplier)}."""
    out = {}
    for name, v in (raw or {}).items():
        try:
            pin, pout = float(v["input_cost_per_token"]) * 1e6, float(v["output_cost_per_token"]) * 1e6
            cr = v.get("cache_read_input_token_cost")
            mult = (float(cr) * 1e6 / pin) if cr and pin else 0.1 if "claude" in name else 0.5
            if (pin <= 0 and pout <= 0) or not all(0 <= x < 1e6 for x in (pin, pout, mult)):   # also rejects nan and inf
                continue
            out[str(name).split("/")[-1].lower()[:120]] = (pin, pout, mult)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def price_file():
    return os.path.join(DATA_DIR, "prices-litellm.json")


try:
    with open(price_file(), encoding="utf-8") as _f:
        EXTRA_PRICES.update(load_price_map(json.load(_f)))
except Exception:
    pass


def update_prices():
    """download the public price list (no data about you is sent), keep it on disk, use it for models we have no price for."""
    import urllib.request
    try:
        body = urllib.request.urlopen(PRICE_URL, timeout=30).read(20000001)
        if len(body) > 20000000:
            return False, "the price list is larger than expected, kept the old one"
        raw = json.loads(body)
        got = load_price_map(raw)
        if len(got) < 50:
            return False, "the price list looked wrong, kept the old one"
        os.makedirs(os.path.dirname(price_file()), exist_ok=True)
        keep = ("input_cost_per_token", "output_cost_per_token", "cache_read_input_token_cost")
        with open(price_file(), "w", encoding="utf-8") as f:   # only the three numbers per model that are used
            json.dump({n: {k: v[k] for k in keep if k in v} for n, v in raw.items() if isinstance(v, dict)
                       and str(n).split("/")[-1].lower()[:120] in got}, f)
        EXTRA_PRICES.clear(); EXTRA_PRICES.update(got); _ucache.clear(); _nowc["ts"] = 0
        return True, "prices for %d models loaded" % len(got)
    except Exception as e:
        return False, "could not download the price list (%s)" % type(e).__name__
N = 8   # per bucket: messages, input, output, cache write 5m, cache write 1h, cache read, thinking (part of output), web searches


def price_for(model):
    m = model or ""
    return next((PRICES[k] for k in _PKEYS if m.startswith(k)), None) or EXTRA_PRICES.get(m.split("/")[-1].lower())


def cost_of(model, fast, batch, v):
    """dollars for one bucket of counts, or None when the model has no known price (local models cost nothing)."""
    pr = price_for(model)
    if not pr:
        return None
    pin, pout, crm = pr
    if fast:
        pin, pout = next((FAST_PRICES[k] for k in sorted(FAST_PRICES, key=len, reverse=True) if model.startswith(k)), (pin, pout))
    c = (v[1] * pin + v[2] * pout + v[3] * pin * 1.25 + v[4] * pin * 2 + v[5] * pin * crm) / 1e6
    return c * 0.5 if batch else c


USAGE = {}            # log file path -> what it holds (see scan_claude_file)
USCAN = dict(done=0, total=0, running=False, last=0, secs=0)
_ucache = {}


def claude_root():
    return os.path.join(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"), "projects")


def scan_claude_file(path, root, claim=None):
    """one Claude Code transcript -> token counts per hour and model. Only the usage numbers, the session title and
    the folder are read; message text is never kept. A message is streamed as several lines that repeat the same
    usage block, so messages are de-duplicated by id."""
    rel = os.path.relpath(path, root).split(os.sep)
    sub = len(rel) > 2
    out = dict(sid=rel[1] if sub else rel[-1][:-6], sub=sub, agent=rel[-1][:-6] if sub else "", slug=rel[0],
               cwd="", title="", branch="", entry="", first="", last="", b={})
    seen = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    if '"custom-title"' in line and len(line) < 2000:
                        try:
                            out["title"] = (json.loads(line).get("customTitle") or "")[:120]
                        except Exception:
                            pass
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                m = j.get("message")
                u = m.get("usage") if isinstance(m, dict) else None
                if not isinstance(u, dict):
                    continue
                ts = j.get("timestamp") or ""
                if not out["cwd"]:
                    out["cwd"], out["branch"], out["entry"] = j.get("cwd") or "", j.get("gitBranch") or "", j.get("entrypoint") or ""
                cc = u.get("cache_creation") or {}
                cw1 = cc.get("ephemeral_1h_input_tokens") or 0
                cw5 = (cc.get("ephemeral_5m_input_tokens") if cc else None)
                cw5 = (u.get("cache_creation_input_tokens") or 0) - cw1 if cw5 is None else cw5
                rec = (ts, m.get("model") or "?", u.get("speed") == "fast", u.get("service_tier") == "batch",
                       u.get("input_tokens") or 0, u.get("output_tokens") or 0, max(0, cw5), cw1, u.get("cache_read_input_tokens") or 0,
                       (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0,
                       (u.get("server_tool_use") or {}).get("web_search_requests") or 0)
                mid = m.get("id") or j.get("uuid")
                old = seen.get(mid)
                if old is None or rec[5] >= old[5]:   # the last line of a streamed message carries its final counts
                    seen[mid] = rec
    except Exception:
        return out
    import datetime
    taken = claim(list(seen)) if claim else ()   # ids another file already counted (a fork repeats its parent's history)
    for mid, (ts, model, fast, batch, i, o, c5, c1, cr, th, web) in seen.items():
        if model.startswith("<") or mid in taken:   # "<synthetic>" placeholders are not API calls
            continue
        try:
            hour = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H")
        except Exception:
            continue
        if not out["first"] or ts < out["first"]:
            out["first"] = ts
        if ts > out["last"]:
            out["last"] = ts
        k = "%s|%s|%d|%d" % (hour, str(model).replace("|", "/"), fast, batch)
        v = out["b"].setdefault(k, [0] * N)
        v[0] += 1; v[1] += i; v[2] += o; v[3] += c5; v[4] += c1; v[5] += cr; v[6] += th; v[7] += web
    return out


def scan_codex_file(path, root, claim=None):
    """OpenAI Codex CLI session log (rollout-*.jsonl). Written from the published log format: session_meta and
    turn_context carry the folder and model, token_count events carry usage. Not yet checked against real sessions."""
    out = dict(sid="codex:" + os.path.basename(path)[:-6], sub=False, agent="", slug="", cwd="", title="", branch="", entry="codex",
               first="", last="", b={}, tool="Codex")
    import datetime
    model, prev = "?", None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"token_count"' not in line and '"turn_context"' not in line and '"session_meta"' not in line:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                pl = j.get("payload") or {}
                if j.get("type") in ("session_meta", "turn_context"):
                    out["cwd"] = pl.get("cwd") or out["cwd"]; model = pl.get("model") or model
                    continue
                info = pl.get("info") if pl.get("type") == "token_count" else None
                if not isinstance(info, dict):
                    continue
                u = info.get("last_token_usage")
                tot = info.get("total_token_usage") or {}
                if not u and tot:   # only running totals: this request is the growth since the previous event
                    u = {k: (tot.get(k) or 0) - ((prev or {}).get(k) or 0) for k in tot}
                prev = tot or prev
                if not u:
                    continue
                ts = j.get("timestamp") or ""
                try:
                    hour = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H")
                except Exception:
                    continue
                cached = u.get("cached_input_tokens") or 0
                v = out["b"].setdefault("%s|%s|0|0" % (hour, str(model).replace("|", "/")), [0] * N)
                v[0] += 1; v[1] += max(0, (u.get("input_tokens") or 0) - cached); v[2] += u.get("output_tokens") or 0
                v[5] += cached; v[6] += u.get("reasoning_output_tokens") or 0
                out["first"] = out["first"] or ts; out["last"] = ts
    except OSError:
        pass
    return out


def scan_generic_file(path, root, claim=None):
    """Any other tool: drop a .jsonl file into <data folder>/usage/ with one line per request:
    {"ts": ISO time, "agent": name, "model": id, "input": n, "output": n, "cache_read": n, "cache_write": n,
     "session": id, "cwd": folder, "title": text, "cost": dollars (optional, overrides the price list)}"""
    import datetime
    sess = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    j = json.loads(line)
                    ts = str(j["ts"])
                    hour = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H")
                except Exception:
                    continue
                sid = "ext:%s:%s" % (os.path.basename(path)[:-6], j.get("session") or "default")
                d = sess.setdefault(sid, dict(sid=sid, sub=False, agent="", slug="", cwd=str(j.get("cwd") or ""), title=str(j.get("title") or "")[:120],
                                              branch="", entry="", first=ts, last=ts, b={}, fixed={}, tool=str(j.get("agent") or os.path.basename(path)[:-6])))
                k = "%s|%s|0|0" % (hour, str(j.get("model") or "?").replace("|", "/"))
                v = d["b"].setdefault(k, [0] * N)
                n = lambda x: int(j.get(x) or 0)
                v[0] += 1; v[1] += n("input"); v[2] += n("output"); v[3] += n("cache_write"); v[5] += n("cache_read")
                c = j.get("cost")
                d["fixed"][k] = d["fixed"].get(k, 0) + (float(c) if c is not None else (cost_of(str(j.get("model") or ""), False, False, [1, n("input"), n("output"), n("cache_write"), 0, n("cache_read"), 0, 0]) or 0))
                d["first"] = min(d["first"], ts); d["last"] = max(d["last"], ts)
    except OSError:
        pass
    return list(sess.values())


def usage_sources():
    """(folder, file test, reader) for every place session usage is kept on this machine."""
    data = DATA_DIR
    return [(claude_root(), lambda n: n.endswith(".jsonl"), scan_claude_file),
            (os.path.join(xp(os.environ.get("CODEX_HOME") or "~/.codex"), "sessions"), lambda n: n.startswith("rollout-") and n.endswith(".jsonl"), scan_codex_file),
            (os.path.join(data, "usage"), lambda n: n.endswith(".jsonl"), scan_generic_file)]


SCAN_V = "3"   # bump to force a full re-read when the counting rules change


def claim_ids(fid, mids):
    """A forked or resumed session starts with a copy of its parent's messages, same ids. Each message is billed once,
    so the first file to present an id owns it; this returns the ids `fid` must not count. Kept on disk, not in RAM."""
    import hashlib
    hs = {int.from_bytes(hashlib.blake2b(str(m).encode(), digest_size=7).digest(), "big"): m for m in mids}
    taken, keys = set(), list(hs)
    for i in range(0, len(keys), 800):
        chunk = keys[i:i + 800]
        for h, f in dbx("select h, fid from usage_msg where h in (%s)" % ",".join("?" * len(chunk)), chunk):
            if f != fid:
                taken.add(hs[h])
    dbx("insert or ignore into usage_msg(h, fid) values(?,?)", [(h, fid) for h, m in hs.items() if m not in taken], many=True)
    return taken


def usage_scanner():
    """keeps USAGE current: first pass reads every transcript once (cached in the history db by size + mtime),
    later passes only re-read files that changed."""
    if (dbx("select v from kv where k='usage_v'") or [("",)])[0][0] != SCAN_V:
        dbx("drop table if exists usage_file"); dbx("drop table if exists usage_msg")
        dbx("insert or replace into kv(k, v) values('usage_v', ?)", (SCAN_V,))
    dbx("create table if not exists usage_file(path text primary key, mtime real, size integer, data text)")
    dbx("create table if not exists usage_msg(h integer primary key, fid integer)")
    known, fids = {}, {}
    def put(path, res):   # one file is one session, except drop-in files, which may hold several
        for k in [k for k in USAGE if k == path or k.startswith(path + "#")]:
            USAGE.pop(k, None)
        for d in (res if isinstance(res, list) else [res]):
            for k in ("slug", "cwd", "branch", "entry"):   # thousands of sessions share these strings
                d[k] = sys.intern(d.get(k) or "")
            USAGE[path + "#" + d["sid"] if isinstance(res, list) else path] = d
    for fid, path, mt, sz, data in dbx("select rowid, path, mtime, size, data from usage_file"):
        fids[path] = fid
        try:
            d = json.loads(data)
            if isinstance(d, list) or d.get("sid"):
                put(path, d); known[path] = (mt, sz)
        except Exception:
            pass
    while True:
        t0 = time.time(); files = []; reader = {}
        for root, want, fn_scan in usage_sources():
            for dp, _, fn in (os.walk(root) if os.path.isdir(root) else []):
                for f in fn:
                    if want(f):
                        files.append(os.path.join(dp, f)); reader[files[-1]] = (fn_scan, root)
        todo = []
        for f in files:
            try:
                st = os.stat(f)
            except OSError:
                continue
            if known.get(f) != (st.st_mtime, st.st_size):
                todo.append((getattr(st, "st_birthtime", st.st_mtime), f, st.st_mtime, st.st_size))
        todo.sort()   # oldest file first, so the original session owns its messages and the fork does not
        USCAN.update(done=0, total=len(todo), running=bool(todo))
        batch = []
        for _, f, mt, sz in todo:
            if f not in fids and DB is not None:
                dbx("insert or ignore into usage_file(path, mtime, size, data) values(?,0,0,'{}')", (f,))
                fids[f] = (dbx("select rowid from usage_file where path=?", (f,)) or [(0,)])[0][0]
            fid = fids.get(f); fn_scan, root = reader[f]
            USCAN["done"] += 1; known[f] = (mt, sz)   # a file that cannot be read is left alone until it changes
            try:
                d = fn_scan(f, root, (lambda mids, fid=fid: claim_ids(fid, mids)) if fid else None)
                blob = json.dumps(d, allow_nan=False); put(f, d)
            except Exception:
                continue
            batch.append((mt, sz, blob, f))
            if len(batch) >= 400:
                dbx("update usage_file set mtime=?, size=?, data=? where path=?", batch, many=True); batch = []; _ucache.clear()
        if batch:
            dbx("update usage_file set mtime=?, size=?, data=? where path=?", batch, many=True)
        gone = {k for k in USAGE if k.split("#")[0] not in reader}
        for g in gone:
            USAGE.pop(g, None); known.pop(g.split("#")[0], None)
        if todo or gone:
            _ucache.clear()
        USCAN.update(running=False, last=int(time.time()), secs=round(time.time() - t0, 1), files=len(files))
        time.sleep(120)


def hermes_sessions():
    """Hermes keeps its own per-session totals (and its own cost estimate) in ~/.hermes/state.db."""
    dbp = os.path.expanduser("~/.hermes/state.db")
    if not os.path.isfile(dbp):
        return []
    out = []
    try:
        c = sqlite3.connect("file:%s?mode=ro" % dbp, uri=True, timeout=2)
        per = defaultdict(list)
        for r in c.execute("select session_id, model, api_call_count, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens,"
                           " reasoning_tokens, coalesce(nullif(actual_cost_usd,0), estimated_cost_usd, 0) from session_model_usage"):
            per[r[0]].append(r[1:])
        import datetime
        for sid, title, model, st, en, cwd, branch, msgs, src in c.execute(
                "select id, title, model, started_at, coalesce(ended_at, last_activity_at, started_at), cwd, git_branch, message_count, source from sessions"):
            if not st:
                continue
            hour = datetime.datetime.fromtimestamp(st).strftime("%Y-%m-%d %H")
            iso = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            b, fixed = {}, {}
            for model2, calls, i, o, cw, cr, th, cost in per.get(sid, []):
                k = "%s|%s|0|0" % (hour, str(model2 or model or "?").replace("|", "/"))
                b[k] = [calls or 0, i or 0, o or 0, cw or 0, 0, cr or 0, th or 0, 0]; fixed[k] = cost or 0
            out.append(dict(sid="hermes:" + str(sid), sub=False, agent="", slug="", cwd=cwd or "", title=(title or "")[:120], branch=branch or "",
                            entry=src or "", first=iso(st), last=iso(en or st), b=b, fixed=fixed, tool="Hermes", msgs=msgs or 0))
        c.close()
    except Exception:
        pass
    return out


RANGE_DAYS = {"today": 1, "7d": 7, "30d": 30, "90d": 90, "all": 100000}


def usage_report(rng="7d"):
    """everything the Usage tab shows for a period: totals, per day, per agent, per model, per project, per session."""
    rng = rng if rng in RANGE_DAYS else "7d"
    hit = _ucache.get(rng)
    if hit and time.time() - hit[0] < 45:
        return hit[1]
    import datetime
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=RANGE_DAYS[rng] - 1)).isoformat() if rng != "all" else ""
    Z = lambda: dict(msgs=0, inp=0, out=0, cw=0, cr=0, think=0, web=0, cost=0.0, unpriced=0)

    def add(t, v, c):
        t["msgs"] += v[0]; t["inp"] += v[1]; t["out"] += v[2]; t["cw"] += v[3] + v[4]; t["cr"] += v[5]; t["think"] += v[6]; t["web"] += v[7]
        if c is None:
            t["unpriced"] += v[1] + v[2] + v[3] + v[4] + v[5]
        else:
            t["cost"] += c
    tot, days, models, tools, projects, sess = Z(), defaultdict(Z), defaultdict(Z), defaultdict(Z), defaultdict(Z), {}
    parts = dict(inp=0.0, out=0.0, cw=0.0, cr=0.0)
    for d in list(USAGE.values()) + hermes_sessions():
        tool = d.get("tool") or "Claude Code"
        S = None
        for k, v in d["b"].items():
            day = k[:10]
            if day < since:
                continue
            _, model, fast, batch = k.split("|")
            c = d["fixed"][k] if "fixed" in d else cost_of(model, fast == "1", batch == "1", v)
            if c is None and not price_for(model):
                c = 0.0 if not model.startswith("claude") else None   # local and unknown-vendor models: no bill
            if S is None:
                S = sess.get(d["sid"])
                if S is None:
                    S = sess[d["sid"]] = dict(Z(), id=d["sid"], tool=tool, title="", cwd="", branch="", entry="", first="", last="",
                                              models={}, subs=0, subCost=0.0)
                if not d["sub"]:
                    S.update(title=d["title"] or S["title"], cwd=d["cwd"] or S["cwd"], branch=d["branch"], entry=d["entry"])
                elif not S["cwd"]:
                    S["cwd"] = d["cwd"]
                if d["sub"]:
                    S["subs"] += 1
            for t in (tot, days[day], models[model], tools[tool], S):
                add(t, v, c)
            add(projects[(d["cwd"] or S["cwd"] or d["slug"])], v, c)
            S["models"][model] = S["models"].get(model, 0) + (c or 0)
            if d["sub"]:
                S["subCost"] += c or 0
            pr = price_for(model)
            if pr and "fixed" not in d:
                f = 0.5 if batch == "1" else 1
                parts["inp"] += v[1] * pr[0] / 1e6 * f; parts["out"] += v[2] * pr[1] / 1e6 * f
                parts["cw"] += (v[3] * 1.25 + v[4] * 2) * pr[0] / 1e6 * f; parts["cr"] += v[5] * pr[0] * pr[2] / 1e6 * f
        if S is not None:
            if d["first"] and (not S["first"] or d["first"] < S["first"]):
                S["first"] = d["first"]
            if d["last"] > S["last"]:
                S["last"] = d["last"]
    rnd = lambda t: dict(t, cost=round(t["cost"], 4))
    lst = lambda dd, key, n=None: sorted((dict(rnd(v), **{key: k}) for k, v in dd.items()), key=lambda x: -x["cost"])[:n]
    sl = sorted(sess.values(), key=lambda x: -x["cost"])
    for x in sl:
        x["cost"] = round(x["cost"], 4); x["subCost"] = round(x["subCost"], 4)
        x["models"] = sorted(((m, round(c, 4)) for m, c in x["models"].items()), key=lambda mc: -mc[1])
        x["project"] = os.path.basename(x["cwd"].rstrip("/")) or "(no folder)"; x["cwd"] = x["cwd"].replace(HOME, "~")
    out = dict(range=rng, since=since, totals=rnd(tot), parts={k: round(v, 2) for k, v in parts.items()},
               days=sorted((dict(rnd(v), day=k) for k, v in days.items()), key=lambda x: x["day"]),
               models=lst(models, "model"), tools=lst(tools, "tool"),
               projects=[dict(p, name=os.path.basename(p["cwd"].rstrip("/")) or p["cwd"], cwd=p["cwd"].replace(HOME, "~")) for p in lst(projects, "cwd", 40)],
               sessions=sl[:1500], sessionCount=len(sl), scan=dict(USCAN), files=len(USAGE),
               extraPrices=len(EXTRA_PRICES),
               prices={k: dict(input=v[0], output=v[1], cacheRead=round(v[0] * v[2], 4), cacheWrite5m=round(v[0] * 1.25, 4), cacheWrite1h=v[0] * 2)
                       for k, v in PRICES.items()})
    _ucache[rng] = (time.time(), out)
    return out


def usage_session(sid):
    """one session in full: per model, per hour, and each sub-agent it spawned."""
    files = [d for d in list(USAGE.values()) if d["sid"] == sid] if not sid.startswith("hermes:") else [d for d in hermes_sessions() if d["sid"] == sid]
    if not files:
        return dict(error="not found")
    main = next((d for d in files if not d["sub"]), files[0])
    Z = lambda: dict(msgs=0, inp=0, out=0, cw5=0, cw1=0, cr=0, think=0, web=0, cost=0.0)
    models, hours, subs = defaultdict(Z), defaultdict(Z), []
    for d in files:
        sc = Z()
        for k, v in d["b"].items():
            hour, model, fast, batch = k.split("|")
            c = d["fixed"][k] if "fixed" in d else (cost_of(model, fast == "1", batch == "1", v) or 0)
            for t in (models[model + (" (fast)" if fast == "1" else "") + (" (batch)" if batch == "1" else "")], hours[hour], sc):
                t["msgs"] += v[0]; t["inp"] += v[1]; t["out"] += v[2]; t["cw5"] += v[3]; t["cw1"] += v[4]; t["cr"] += v[5]
                t["think"] += v[6]; t["web"] += v[7]; t["cost"] += c
        if d["sub"]:
            subs.append(dict(sc, agent=d["agent"], first=d["first"], last=d["last"], cost=round(sc["cost"], 4)))
    path = next((p for p, d in list(USAGE.items()) if d is main), "")
    r = lambda dd, key: sorted((dict(v, cost=round(v["cost"], 4), **{key: k}) for k, v in dd.items()), key=lambda x: x[key])
    return dict(id=sid, tool=main.get("tool") or "Claude Code", title=main["title"], cwd=main["cwd"].replace(HOME, "~"), branch=main["branch"],
                entry=main["entry"], first=min((d["first"] for d in files if d["first"]), default=""), last=max(d["last"] for d in files),
                models=sorted(r(models, "model"), key=lambda x: -x["cost"]), hours=r(hours, "hour"),
                subs=sorted(subs, key=lambda x: -x["cost"]), log=path.replace(HOME, "~"),
                priced={m.split(" (")[0]: bool(price_for(m)) for m in models})


# ---- usage right now: 5-hour windows, live sessions, budgets ------------------
BLOCK_H = 5   # Claude subscriptions meter usage in 5-hour windows that start with your first message
_nowc = dict(ts=0, data=None)


def usage_blocks(days=30):
    """Claude Code activity cut into 5-hour windows: a window opens at the hour of the first request after the
    previous window ended (or after 5 quiet hours) and closes 5 hours later."""
    import datetime
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    hours = {}
    for d in list(USAGE.values()):
        for k, v in d["b"].items():
            if k[:10] < since:
                continue
            hour, model, fast, batch = k.split("|")
            h = hours.setdefault(hour, [0.0, 0, 0, {}])
            c = cost_of(model, fast == "1", batch == "1", v) or 0
            h[0] += c; h[1] += v[1] + v[2] + v[3] + v[4] + v[5]; h[2] += v[0]; h[3][model] = h[3].get(model, 0) + c
    blocks, cur = [], None
    for hour in sorted(hours):
        t = datetime.datetime.strptime(hour, "%Y-%m-%d %H")
        if cur is None or t >= cur["start"] + datetime.timedelta(hours=BLOCK_H):
            cur = dict(start=t, last=t, cost=0.0, tok=0, msgs=0, models={}); blocks.append(cur)
        c, tok, msgs, models = hours[hour]
        cur["last"] = t; cur["cost"] += c; cur["tok"] += tok; cur["msgs"] += msgs
        for m, mc in models.items():
            cur["models"][m] = cur["models"].get(m, 0) + mc
    return blocks


BUDGETS = dict(day=0.0, week=0.0, session=0.0, block=0.0)   # dollars at list price; 0 = no budget


def set_budgets(b):
    for k in BUDGETS:
        if k in b:
            try:
                BUDGETS[k] = max(0.0, min(1e7, float(b[k] or 0)))
            except (TypeError, ValueError):
                return False, "budgets must be numbers"
    dbx("insert or replace into kv(k, v) values('budgets', ?)", (json.dumps(BUDGETS),))
    _nowc["ts"] = 0; _wake.set()
    return True, "budgets saved"


def usage_now():
    """what the top of the Usage tab shows: the open 5-hour window, the rolling week, budgets, live sessions."""
    if _nowc["data"] and time.time() - _nowc["ts"] < 20:
        return dict(_nowc["data"], live=live_list())
    import datetime
    now = datetime.datetime.now(); blocks = usage_blocks(30)
    out_b = lambda b: dict(start=b["start"].timestamp(), end=(b["start"] + datetime.timedelta(hours=BLOCK_H)).timestamp(),
                           last=b["last"].timestamp(), cost=round(b["cost"], 2), tok=b["tok"], msgs=b["msgs"],
                           models=sorted(((m, round(c, 2)) for m, c in b["models"].items()), key=lambda x: -x[1])[:4])
    done = [b for b in blocks if b["start"] + datetime.timedelta(hours=BLOCK_H) <= now]
    act = blocks[-1] if blocks and blocks[-1]["start"] + datetime.timedelta(hours=BLOCK_H) > now else None
    block = None
    if act:
        el = max(60.0, (now - act["start"]).total_seconds()); left = max(0.0, BLOCK_H * 3600 - el)
        burn = act["cost"] / el * 3600
        block = dict(out_b(act), elapsed=el, left=left, burn=round(burn, 2), tokPerMin=round(act["tok"] / el * 60),
                     projected=round(act["cost"] + burn * left / 3600, 2))
    costs = sorted(b["cost"] for b in done)
    day = lambda n: (datetime.date.today() - datetime.timedelta(days=n)).isoformat()
    per_day = {d["day"]: d["cost"] for d in usage_report("30d")["days"]}
    wk = sum(c for d, c in per_day.items() if d > day(7)); prev = sum(c for d, c in per_day.items() if day(14) < d <= day(7))
    top = max((x for x in usage_report("today")["sessions"]), key=lambda x: x["cost"], default=None)
    data = dict(block=block, blocks=[out_b(b) for b in blocks[-14:]][::-1],
                stats=dict(n=len(done), max=round(costs[-1], 2) if costs else 0, typical=round(costs[len(costs) // 2], 2) if costs else 0,
                           p90=round(costs[int(len(costs) * 0.9)], 2) if costs else 0),
                week=dict(cost=round(wk, 2), prev=round(prev, 2)), today=round(per_day.get(day(0), 0), 2),
                topSession=dict(id=top["id"], title=top["title"] or top["project"], cost=top["cost"]) if top else None,
                budgets=dict(BUDGETS), scanning=USCAN["running"])
    _nowc.update(ts=time.time(), data=data)
    return dict(data, live=live_list())


def budget_alerts():
    """spend against the budgets the user set (list price). 80% warns, 100% alerts."""
    if USCAN["running"] or not any(BUDGETS.values()):
        return [], []
    n = usage_now(); a, rules = [], []
    for key, label, spent, scope in (("day", "Spend today", n["today"], "today"), ("week", "Spend this week", n["week"]["cost"], "in the last 7 days"),
                                     ("block", "Spend this 5-hour window", (n["block"] or {}).get("cost", 0), "in the open 5-hour window"),
                                     ("session", "Most expensive session today", (n["topSession"] or {}).get("cost", 0), "")):
        lim = BUDGETS[key]
        if not lim:
            continue
        pct = spent / lim * 100
        rules.append(dict(name=label, value="$%.2f (%d%%)" % (spent, pct), rule="budget $%g, warns at 80%%" % lim, firing=pct >= 80))
        if pct >= 80:
            who = (" (%s)" % n["topSession"]["title"]) if key == "session" and n["topSession"] else ""
            a.append(dict(key="budget:" + key, level="err" if pct >= 100 else "warn", id=None,
                          msg="%s: $%.2f of your $%g budget %s%s" % ("Over budget" if pct >= 100 else "Close to budget", spent, lim, scope, who)))
    return a, rules


LIVE = {}         # transcript path -> state of a session that was active in the last half hour
_live_prev = {}
CTX_MAX = lambda model: 200000 if model.startswith(("claude-haiku-4-5", "claude-3")) else 1000000


def tail_state(path, size):
    """what a Claude Code session is doing, from the end of its transcript. Only record types, stop reasons, tool
    names and token counts are looked at; message text is not kept."""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - 500000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    if size > 500000:
        lines = lines[1:]
    last, ctx, model, tool = None, 0, "", ""
    for line in lines:
        if '"type":"assistant"' not in line and '"type":"user"' not in line:
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("type") not in ("assistant", "user") or j.get("isSidechain"):
            continue
        m = j.get("message") if isinstance(j.get("message"), dict) else {}
        c = m.get("content")
        kinds = [b.get("type") for b in c if isinstance(b, dict)] if isinstance(c, list) else ["text"]
        last = (j["type"], m.get("stop_reason"), kinds)
        if j["type"] == "assistant":
            u = m.get("usage") or {}
            n = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            if n:
                ctx, model = n, m.get("model") or model
            for b in (c if isinstance(c, list) else []):
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool = b.get("name") or ""
    if not last:
        return None
    typ, stop, kinds = last
    if typ == "user":
        state, what = "working", ("reading a tool result" if "tool_result" in kinds else "working on your message")
    elif stop == "tool_use":
        state, what = "working", "running %s" % (tool or "a tool")
    elif stop in ("end_turn", "stop_sequence"):
        state, what = "waiting", "finished its turn, waiting for you"
    elif stop is None:
        state, what = "working", "writing"
    else:
        state, what = "waiting", "stopped (%s)" % stop
    return dict(state=state, what=what, ctx=ctx, model=model)


def live_scanner():
    """every 10s: which Claude Code sessions changed recently and what are they doing. Cheap: it only looks at
    project folders whose listing changed and at files it already knows are recent."""
    cand, dir_m, seen = {}, {}, {}
    while True:
        try:
            now = time.time(); root = claude_root()
            for d in (os.scandir(root) if os.path.isdir(root) else []):
                if not d.is_dir():
                    continue
                m = d.stat().st_mtime
                if dir_m.get(d.path) != m or now - m < 30:   # a file was added (or the folder is busy): look inside
                    dir_m[d.path] = m
                    for e in os.scandir(d.path):
                        if e.name.endswith(".jsonl"):
                            st = e.stat()
                            if st.st_mtime > now - 1800:
                                cand[e.path] = None
            for path, d in list(USAGE.items()):   # the usage scanner knows every file; recent ones become candidates
                if not d["sub"] and d["last"] and path not in cand and d["last"][:10] >= time.strftime("%Y-%m-%d", time.gmtime(now - 86400)):
                    cand[path] = None
            for path in list(cand):
                try:
                    st = os.stat(path)
                except OSError:
                    cand.pop(path, None); LIVE.pop(path, None); continue
                if st.st_mtime < now - 1800:
                    LIVE.pop(path, None)
                    if st.st_mtime < now - 90000:
                        cand.pop(path, None)
                    continue
                if seen.get(path) != (st.st_mtime, st.st_size):
                    seen[path] = (st.st_mtime, st.st_size)
                    ts = tail_state(path, st.st_size)
                    if ts:
                        LIVE[path] = dict(ts, mtime=st.st_mtime, path=path)
            for path, L in list(LIVE.items()):   # tell the user when a real (not one-shot) session stops and waits
                d = USAGE.get(path) or {}
                msgs = sum(v[0] for v in d.get("b", {}).values())
                prev = _live_prev.get(path)
                if prev == "working" and L["state"] == "waiting" and msgs > 3 and d.get("title"):
                    event("wait", "%s: Claude Code finished and is waiting for you" % (d.get("title") or os.path.basename((d.get("cwd") or "").rstrip("/")) or "a session"))
                _live_prev[path] = L["state"]
            for path in set(_live_prev) - set(LIVE):
                _live_prev.pop(path, None)
        except Exception:
            pass
        time.sleep(10)


def live_list():
    import datetime
    now = time.time(); today = datetime.date.today().isoformat(); out = []
    sids = {}
    for path, L in list(LIVE.items()):
        d = USAGE.get(path)
        if d:
            sids[d["sid"]] = (path, L, d)
    spend = defaultdict(lambda: [0.0, 0, 0])   # per session today: cost, requests, sub-agents
    for d in list(USAGE.values()):
        if d["sid"] in sids:
            t = spend[d["sid"]]
            t[2] += 1 if d["sub"] else 0
            for k, v in d["b"].items():
                if k[:10] == today:
                    _, model, fast, batch = k.split("|")
                    t[0] += cost_of(model, fast == "1", batch == "1", v) or 0; t[1] += v[0]
    for sid, (path, L, d) in sids.items():
        idle = now - L["mtime"]; state = L["state"]; what = L["what"]
        if state == "working" and idle > 300:
            state, what = "stalled", "no activity for %dm while %s" % (idle // 60, L["what"])
        total = sum(v[0] for v in d["b"].values())
        out.append(dict(id=sid, title=d["title"] or os.path.basename((d["cwd"] or "").rstrip("/")) or sid[:8], cwd=(d["cwd"] or "").replace(HOME, "~"),
                        branch=d["branch"], entry=d["entry"], state=state, what=what, idle=int(idle), model=L["model"], ctx=L["ctx"],
                        ctxMax=CTX_MAX(L["model"] or ""), costToday=round(spend[sid][0], 2), reqToday=spend[sid][1], subs=spend[sid][2],
                        oneShot=total <= 3 or (not d["title"] and total <= 12)))   # hooks and one-off runs: short and never titled
    order = {"working": 0, "stalled": 1, "waiting": 2}
    out.sort(key=lambda x: (x["oneShot"], order.get(x["state"], 3), x["idle"]))
    return out


# ---- logs / inspect / action -------------------------------------------------
def svc(sid):
    if not STATE["ts"]:
        refresh()
    return BY_ID.get(sid)


def host_log_file(pid):
    """where a host process writes: its stdout/stderr file, else an open *.log (newest)."""
    names = []
    if IS_MAC:
        r = sh(["lsof", "-a", "-p", str(pid), "-d", "1,2", "-Fn"], 4)
        names = [l[1:] for l in (r.stdout.splitlines() if r else []) if l.startswith("n/") and not l.startswith("n/dev/")]
        if not names:
            r = sh(["lsof", "-a", "-p", str(pid), "-Fn"], 4)
            names = [l[1:] for l in (r.stdout.splitlines() if r else []) if l.startswith("n/") and l.endswith(".log")]
    elif IS_LINUX:
        try:
            fds = {fd: os.readlink("/proc/%d/fd/%s" % (pid, fd)) for fd in os.listdir("/proc/%d/fd" % pid)}
        except OSError:
            fds = {}
        names = [t for fd, t in fds.items() if fd in ("1", "2") and t.startswith("/") and not t.startswith("/dev/")] \
            or [t for t in fds.values() if t.endswith(".log")]
    names = [n for n in dict.fromkeys(names) if os.path.isfile(n)]
    return max(names, key=os.path.getmtime) if names else None


def tail(path, n):
    with open(path, "rb") as f:
        f.seek(0, 2); f.seek(max(0, f.tell() - 300000))
        return f.read().decode("utf-8", "replace").splitlines()[-n:]


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def logs(sid, n=200):
    d = _logs(sid, max(10, min(n, 2000)))
    d["lines"] = [redact(ANSI.sub("", l)) for l in d["lines"]]  # no terminal colour codes, credentials masked
    return d


def _logs(sid, n):
    s = svc(sid)
    if not s:
        return dict(id=sid, source="", lines=["(service not found)"])
    if s.get("docker"):
        r = sh(["docker", "logs", "--tail", str(n), s["docker"]], 10)
        txt = (r.stdout + r.stderr) if r else "(docker logs failed)"
        return dict(id=sid, name=s["name"], source="docker logs " + s["docker"], lines=txt.splitlines()[-n:])
    path = host_log_file(s["pid"]) if s.get("pid") else None
    if path:
        try:
            return dict(id=sid, name=s["name"], source=path.replace(HOME, "~"), lines=tail(path, n))
        except Exception as e:
            return dict(id=sid, name=s["name"], source=path, lines=["(cannot read %s: %s)" % (path, e)])
    return dict(id=sid, name=s["name"], source="", lines=[
        "No log file found for '%s'." % s["name"],
        "It writes to a terminal or the system log, not to a file this dashboard can read."])


def ago(iso):
    try:
        import datetime
        t = datetime.datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        s = int((datetime.datetime.now(datetime.timezone.utc) - t).total_seconds())
        if s < 60: return "%ds" % s
        if s < 3600: return "%dm" % (s // 60)
        if s < 86400: return "%dh %dm" % (s // 3600, (s % 3600) // 60)
        return "%dd %dh" % (s // 86400, (s % 86400) // 3600)
    except Exception:
        return ""


def inspect(sid):
    s = svc(sid)
    if not s:
        return dict(error="not found")
    d = dict(id=sid, name=s["name"], what=s.get("desc") or "", group=s["group"], url=s.get("url"), kind=s.get("kind"), up=s.get("up"))
    # relationships (what it depends on / what uses it)
    svcs, edges = STATE["services"], STATE["edges"]; id2 = {x["id"]: x["name"] for x in svcs}
    h = list(SVC_HIST[sid]); d["hist"] = dict(cpu=[x[0] for x in h], mem=[x[1] for x in h])
    d["actions"] = s.get("actions") or []; d["memMB"] = s.get("memMB"); d["cpuN"] = s.get("cpuN")
    d["events"] = [e for e in list(EVENTS)[::-1] if e["id"] == sid][:12]
    d["dependsOn"] = [dict(id=e[1], name=id2.get(e[1], e[1]), why=e[2]) for e in edges if e[0] == sid]
    d["usedBy"] = [dict(id=e[0], name=id2.get(e[0], e[0]), why=e[2]) for e in edges if e[1] == sid]
    if s.get("launchd"):
        d["jobMates"] = [x["name"] for x in svcs if x.get("launchd") == s["launchd"] and x["id"] != sid]
    if s.get("docker"):
        r = sh(["docker", "inspect", s["docker"]], 8)
        if r and r.stdout:
            try:
                j = json.loads(r.stdout)[0]
                st, cfg, ns = j["State"], j["Config"], j.get("NetworkSettings", {})
                ports = []
                for cp, binds in (ns.get("Ports") or {}).items():
                    ports += ["%s → %s" % (b.get("HostPort"), cp) for b in binds] if binds else [cp]
                stat = next((x for x in docker_stats() if x["name"] == s["docker"]), {}) or {}
                d.update(kind="docker", container=s["docker"], image=cfg.get("Image"),
                         state=st.get("Status"), health=(st.get("Health") or {}).get("Status"),
                         started=st.get("StartedAt", "")[:19], uptime=ago(st.get("StartedAt")),
                         restarts=j.get("RestartCount"), exitCode=st.get("ExitCode"),
                         restartPolicy=(j.get("HostConfig", {}).get("RestartPolicy") or {}).get("Name") or "no",
                         memLimit=fmt_mb(j["HostConfig"]["Memory"] / 1048576) if j.get("HostConfig", {}).get("Memory") else "none",
                         created=ago(j.get("Created")), imageId=(j.get("Image") or "")[7:19],
                         pidsInside=stat.get("pids"), blockIO=stat.get("block"), memPct=stat.get("memPct"),
                         command=redact(" ".join((cfg.get("Entrypoint") or []) + (cfg.get("Cmd") or [])))[:160] or None,
                         project=(cfg.get("Labels") or {}).get("com.docker.compose.project"),
                         service=(cfg.get("Labels") or {}).get("com.docker.compose.service"),
                         cpu=stat.get("cpu"), memUse=stat.get("mem"), netIO=stat.get("net"),
                         networks=["%s · %s" % (k, v.get("IPAddress") or "-") for k, v in (ns.get("Networks") or {}).items()],
                         mounts=["%s %s → %s%s" % (m.get("Type", ""), (m.get("Name") or m.get("Source", ""))[-34:], m.get("Destination"),
                                                  "" if m.get("RW", True) else " (read-only)") for m in (j.get("Mounts") or [])][:12],
                         ports=ports,
                         env=safe_env(cfg.get("Env") or [])[:40], envHidden=len(cfg.get("Env") or []) - len(safe_env(cfg.get("Env") or [])),
                         labels=len(cfg.get("Labels") or {}), workdir=cfg.get("WorkingDir") or None,
                         healthLog=[redact((h.get("Output") or "").strip())[:240] for h in ((st.get("Health") or {}).get("Log") or [])[-2:]],
                         healthCmd=redact(" ".join(((cfg.get("Healthcheck") or {}).get("Test") or [])[1:]))[:160] or None,
                         cpuLimit=("%.2f cores" % (j["HostConfig"]["NanoCpus"] / 1e9)) if j.get("HostConfig", {}).get("NanoCpus") else "none",
                         finished=ago(st.get("FinishedAt")) if not st.get("Running") else None, oom=st.get("OOMKilled") or None)
                if st.get("Running"):   # what runs inside
                    tp = sh(["docker", "top", s["docker"], "-o", "pid,pcpu,rss,args"], 6)
                    lines = (tp.stdout.splitlines() if tp and tp.returncode == 0 else [])[1:13]
                    d["inside"] = [dict(pid=f[0], cpu=f[1], rssMB=round(int(f[2]) / 1024) if f[2].isdigit() else 0, cmd=redact(f[3])[:120])
                                   for f in (l.split(None, 3) for l in lines) if len(f) == 4]
            except Exception as e:
                d["error"] = str(e)
    else:
        pid = s.get("pid")
        d.update(pid=pid, ports=s.get("ports"), uptime=s.get("uptime"), where=s.get("where"), brew=s.get("brew"),
                 launchd=s.get("launchd"), procs=s.get("procs"), treeMem=s.get("mem"), treeCpu=s.get("cpu"))
        if pid:
            rows = ps_table()
            d["children"] = [dict(pid=p["pid"], name=os.path.basename(p["comm"])[:30], rssMB=round(p["rss"]), cpu=p["cpu"])
                             for p in sorted(rows, key=lambda p: p["rss"], reverse=True)
                             if p["ppid"] == pid and "defunct" not in p["comm"]][:10]
            if IS_MAC:
                of = sh(["lsof", "-a", "-p", str(pid), "-Ff"], 4)
                d["openFiles"] = sum(1 for l in of.stdout.splitlines() if l.startswith("f")) if of else None
            elif IS_LINUX:
                try:
                    d["openFiles"] = len(os.listdir("/proc/%d/fd" % pid))
                except OSError:
                    d["openFiles"] = None
            lf = host_log_file(pid); d["logFile"] = lf.replace(HOME, "~") if lf else None
            me = next((p for p in rows if p["pid"] == pid), None)
            if me:
                d.update(ppid=me["ppid"], user=uname(me["uid"]), cpu="%.1f%%" % me["cpu"], rssMB=round(me["rss"]),
                         uptime=me["etime"], command=redact(cmd_of(pid))[:900])
            cw = cwd_of(pid)
            if cw:
                d["cwd"] = cw.replace(HOME, "~")
        d["systemd"] = s.get("systemd")
    return d


def done(ok, r, verb):
    return ok, ((r.stdout + r.stderr).strip() if r else "") or (verb if ok else "failed")


def model_info(name):
    if not which("ollama") or not re.fullmatch(r"\w[\w.:@/-]*", name or ""):
        return dict(error="unsupported")
    r = sh(["ollama", "show", name], 10)
    d, sec = dict(name=name, sections={}), None
    for line in (r.stdout.splitlines() if r else []):
        if line.strip() and not line.startswith("    "):
            sec = line.strip(); d["sections"][sec] = []
        elif line.strip() and sec:
            if sec != "License" and len(d["sections"][sec]) < 16:
                d["sections"][sec].append(re.split(r"\s{2,}", line.strip(), 1))
    return d


def model_action(name, act):
    if not re.fullmatch(r"\w[\w.:@/-]*", name or ""):
        return False, "unsupported"
    servers = ollama_servers()
    held = next((m for m in llm_loaded() if m["name"] == name), None)
    port = held["port"] if held else next((p for p, _ in servers if p == 11434), servers[0][0] if servers else None)
    if act in ("load", "stop"):
        if not port:
            return False, "no Ollama server is running"

        def call():   # keep_alive 0 unloads, anything else loads; big models take a while, so do it in the background
            try:
                http_json("http://127.0.0.1:%d/api/generate" % port, dict(model=name, keep_alive="30m" if act == "load" else 0), 300)
                event("action", "Model %s %s" % (name, "loaded" if act == "load" else "unloaded"))
            except Exception as e:
                event("alert", "Model %s failed to %s: %s" % (name, act, e))
            _inv["ts"] = 0; _llm["ts"] = 0
        threading.Thread(target=call, daemon=True).start()
        return True, ("loading %s (large models take a while)" if act == "load" else "unloading %s") % name
    if act != "delete" or not which("ollama"):
        return False, "unsupported"
    r = sh(["ollama", "rm", name], 60)
    ok = bool(r) and r.returncode == 0
    event("action", "Model %s deleted%s" % (name, "" if ok else " FAILED")); _inv["ts"] = 0
    return done(ok, r, "deleted " + name)


def win_owned(pid):
    """Windows: does this pid belong to the logged-in user? tasklist does the comparison, so no owner names are parsed
    (the process list there carries no owner). Untested on a real Windows machine."""
    user = (os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")).strip("\\")
    r = sh(["tasklist", "/FI", "PID eq %d" % pid, "/FI", "USERNAME eq " + user, "/FO", "CSV", "/NH"], 15)
    return bool(r) and ('"%d"' % pid) in (r.stdout or "")


def kill_pid(pid, force=False):
    """ask a process to quit (or make it): SIGTERM / SIGKILL, or taskkill on Windows. -> (ok, error text)"""
    if IS_WIN:
        if not win_owned(pid):
            return False, "not yours to stop (it belongs to another user or to Windows)"
        r = sh(["taskkill", "/PID", str(pid)] + (["/F", "/T"] if force else []), 20)
        ok = bool(r) and r.returncode == 0
        return ok, "" if ok else ((r.stdout + r.stderr).strip() if r else "taskkill failed") + ("" if force else " (try Force: background programs ignore a polite close)")
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM) if force else signal.SIGTERM)
        return True, ""
    except OSError as e:
        return False, str(e)


def signal_pid(pid, force=False):
    """signal one process, only if it is the current user's and not the dashboard."""
    _ps["ts"] = 0   # look again now: a pid from a list that is seconds old may belong to something else
    p = next((x for x in ps_table() if x["pid"] == pid), None)
    if not p:
        return False, "no such process"
    if not mine(p):
        return False, "not yours to signal (other user, system, or the dashboard itself)"
    ok, err = kill_pid(pid, force)
    if not ok:
        return False, err
    _ps["ts"] = 0; _snap["ts"] = 0
    return True, "%s %s (pid %d)" % ("killed" if force else "stopped", base_name(p["comm"]), pid)


def proc_action(pid, force=False):
    ok, msg = signal_pid(int(pid or 0), force)
    if ok:
        event("action", msg)
    return ok, msg


def app_action(name):
    """quit an app = SIGTERM every process the grouping puts under that name (yours only)."""
    _ps["ts"] = 0
    pids = [p["pid"] for p in ps_table() if app_name(p["comm"]) == name and mine(p)]
    if not pids:
        return False, "nothing of yours to quit under '%s'" % name
    n = 0
    for pid in pids:
        n += 1 if kill_pid(pid, IS_WIN)[0] else 0   # Windows background programs only respond to a forced close
    _ps["ts"] = 0
    event("action", "Quit %s (%d processes)" % (name, n))
    return n > 0, "sent quit to %d process%s of %s" % (n, "" if n == 1 else "es", name)


def do_action(sid, act):
    s = svc(sid)
    if not s or act not in (s.get("actions") or []):
        return False, "action not available"
    dom = "gui/%d" % UID
    if s.get("docker"):
        r = sh(["docker", "rm" if act == "remove" else act, s["docker"]], 90)
        if act in ("stop", "kill", "start", "restart", "remove"):
            mark_stopped([s["docker"]], act in ("stop", "kill"))
    elif s.get("brew"):
        r = sh(["brew", "services", act, s["brew"]], 90); _brew["ts"] = 0
    elif s.get("launchd") and act == "restart":
        r = sh(["launchctl", "kickstart", "-k", "%s/%s" % (dom, s["launchd"])], 30)
    elif s.get("launchd") and act == "unload":       # stop it and keep it stopped (KeepAlive would revive a plain kill)
        r = sh(["launchctl", "bootout", "%s/%s" % (dom, s["launchd"])], 30)
    elif s.get("launchd") and act == "start":
        plist = launch_agents().get(s["launchd"], {}).get("plist")
        r = (sh(["launchctl", "kickstart", "%s/%s" % (dom, s["launchd"])], 30) if s["launchd"] in _ld["loaded"]
             else sh(["launchctl", "bootstrap", dom, plist], 30) if plist else None)
    elif s.get("systemd") and s.get("sysUser") and act in ("restart", "stop"):
        r = sh(["systemctl", "--user", act, s["systemd"]], 60)
    elif s.get("pid") and act in ("stop", "kill"):
        ok, msg = signal_pid(s["pid"], act == "kill")
        event("action", "%s: %s" % (s["name"], msg), sid); _wake.set()
        return ok, msg
    else:
        return False, "no controller for this service"
    ok = bool(r) and r.returncode == 0
    event("action", "%s %s%s" % (act.capitalize(), s["name"], "" if ok else " FAILED"), sid)
    _dock["ts"] = 0; _ld["ts"] = 0; _ps["ts"] = 0; _wake.set()
    return done(ok, r, act + " ok")


def group_action(group, act):
    """one action for every container of a compose project (the names come from discovery)."""
    if act not in ("restart", "stop", "start"):
        return False, "unsupported"
    names = [s["docker"] for s in STATE["services"] if s["group"] == group and s.get("docker")
             and (not s["up"] if act == "start" else s["up"])]
    if not names:
        return False, "nothing to %s in %s" % (act, group)
    r = sh(["docker", act] + names, 180)
    mark_stopped(names, act == "stop")
    ok = bool(r) and r.returncode == 0
    event("action", "%s %d containers in %s%s" % (act.capitalize(), len(names), group, "" if ok else " FAILED"))
    _dock["ts"] = 0; _wake.set()
    return ok, "%s %d container%s" % (act, len(names), "" if len(names) == 1 else "s") if ok else (r.stderr.strip() if r else "failed")


def docker_action(act, target=""):
    """housekeeping. Targets are checked against what Docker itself reports as unused, never taken on trust."""
    fixed = {"prune-images": ["docker", "image", "prune", "-f"],            # dangling layers only
             "prune-unused": ["docker", "image", "prune", "-a", "-f"],     # every image no container uses
             "prune-cache": ["docker", "builder", "prune", "-f"],
             "prune-containers": ["docker", "container", "prune", "-f"]}  # stopped containers
    cmd = fixed.get(act)
    if act == "start-engine":
        return start_engine() if which("docker") else (False, "Docker is not installed")
    if act in ("rmi", "rmvol", "pull"):
        inv = inv_docker() or {}
        if act == "rmi" and any(i["name"] == target and not i["used"] for i in inv.get("images", [])):
            cmd = ["docker", "rmi", target]
        elif act == "rmvol" and any(v["name"] == target and not v["used"] for v in inv.get("volumes", [])):
            cmd = ["docker", "volume", "rm", target]
        elif act == "pull" and any(i["name"] == target for i in inv.get("images", [])):
            def pull():
                r = sh(["docker", "pull", target], 900)
                event("action" if r and r.returncode == 0 else "alert",
                      "Pulled %s: %s" % (target, (r.stdout.strip().splitlines() or ["failed"])[-1] if r else "failed")); _inv["ts"] = 0
            threading.Thread(target=pull, daemon=True).start()
            return True, "pulling %s in the background (restart the container to use it)" % target
    if not cmd:
        return False, "unsupported, or the target is in use"
    r = sh(cmd, 180)
    ok = bool(r) and r.returncode == 0
    last = ((r.stdout + r.stderr).strip().splitlines() or ["done"])[-1] if r else "failed"
    event("action", "Docker %s %s: %s" % (act, target, last)); _inv["ts"] = 0; _wake.set()
    return ok, last


# ---- http --------------------------------------------------------------------
LOCAL = {"127.0.0.1:%d" % PORT, "localhost:%d" % PORT, "[::1]:%d" % PORT}
POSTS = {
    "/api/action": lambda b: do_action(b.get("id", ""), b.get("action", "")),
    "/api/group": lambda b: group_action(b.get("group", ""), b.get("action", "")),
    "/api/proc": lambda b: proc_action(b.get("pid"), bool(b.get("force"))),
    "/api/app": lambda b: app_action(b.get("name", "")),
    "/api/model": lambda b: model_action(b.get("name", ""), b.get("action", "stop")),
    "/api/docker": lambda b: docker_action(b.get("action", ""), str(b.get("target", ""))),
    "/api/agent": lambda b: agent_action(b.get("id", ""), b.get("action", "stop")),
    "/api/budget": lambda b: set_budgets(b),
    "/api/prices": lambda b: update_prices() if b.get("action") == "update" else (False, "unsupported"),
    "/api/open": lambda b: open_path(b.get("kind", ""), b.get("key", ""), b.get("how", "folder")),
}


DEMO = False                                 # --demo: answer from pilothouse/demo.py, read and change nothing
TOKEN = ""                                   # set in main(): every /api and /metrics request must carry it


def load_token():
    """The access token. Anything on this machine can reach 127.0.0.1 - another user, or a sandboxed program with
    no access to your files - so reading and control need a secret only you can read: DASH_TOKEN if you set one,
    otherwise a random one kept in the data folder (mode 0600). DASH_AUTH=off turns it off."""
    if os.environ.get("DASH_AUTH", "").lower() in ("off", "0", "no", "false", "none"):
        return ""
    if os.environ.get("DASH_TOKEN"):
        return os.environ["DASH_TOKEN"]
    path = os.path.join(DATA_DIR, "token")
    try:
        with open(path, encoding="utf-8") as f:
            t = f.read().strip()
        if len(t) >= 20:
            return t
    except OSError:
        pass
    import secrets
    t = secrets.token_urlsafe(32)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        if not IS_WIN:
            os.chmod(DATA_DIR, 0o700)
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(t)
    except OSError:
        pass   # a read-only data folder: the token lives for this run only
    return t


def link(port=None):
    """the address to open: the token travels in the fragment, which browsers never send to a server or put in a Referer."""
    return "http://localhost:%d/%s" % (port or PORT, "#t=" + TOKEN if TOKEN else "")


_paired = [0.0]


def pair():
    """the sign-in screen's button: open the signed-in address in this user's own default browser.
    It needs no key because it gives none away: the caller only learns yes or no, the key goes to the
    browser of whoever runs Pilothouse.  At most once every 10 seconds."""
    if DEMO or not TOKEN:
        return False, "No key is needed here. Reload the page."
    if IS_LINUX and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False, "Pilothouse runs without a desktop here, so it cannot open a browser. Use the command below."
    if time.time() - _paired[0] < 10:
        return False, "Already opened a moment ago. Look for the new tab."
    _paired[0] = time.time()
    import webbrowser
    try: ok = webbrowser.open(link())
    except Exception: ok = False
    return (True, "Opened in your default browser. If that is another browser than this one, paste the address below.") if ok \
        else (False, "Could not open a browser. Use the command below.")


def jdump(o):
    """JSON the browser can parse: nan and infinity (a broken number in some log) become 0."""
    try:
        return json.dumps(o, allow_nan=False)
    except ValueError:
        fix = lambda v: ({k: fix(x) for k, x in v.items()} if isinstance(v, dict) else [fix(x) for x in v] if isinstance(v, (list, tuple))
                         else 0 if isinstance(v, float) and v != v or v in (float("inf"), float("-inf")) else v)
        return json.dumps(fix(o))
STARTED = time.time()
# The page is one file with inline script and style, so 'unsafe-inline' is needed; what this policy still buys:
# nothing may be loaded from or sent to anywhere but this server (and the one CDN the map library comes from),
# and no other site can put the page in a frame.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")
READS = dict(status=status, metrics=metrics, inventory=inventory, logs=logs, inspect=inspect, history=history, since=since,
             usage=usage_report, usage_now=usage_now, usage_session=usage_session, modelinfo=model_info)


def api(name, *a):
    """the data behind one read endpoint: the real machine, or the made-up stack in --demo."""
    if DEMO:
        from . import demo as _demo
        return getattr(_demo, name)(*a)
    return READS[name](*a)


def health():
    """GET /api/health: small and stable, for scripts and uptime checks."""
    from . import __version__
    ready = DEMO or bool(STATE["ts"])   # the first look at the machine can take a while: health answers at once regardless
    st = api("status") if ready else dict(services=[], alerts=[]); S = st["services"]
    bad = {a["id"] for a in st["alerts"] if a.get("id") and a["level"] == "err"}
    return dict(ok=True, version=__version__, platform=PLATFORM, demo=DEMO, ready=ready, uptime=int(time.time() - STARTED), services=len(S),
                running=sum(1 for x in S if x["up"]), down=len(bad), stopped=sum(1 for x in S if not x["up"] and x["id"] not in bad),
                alerts=len(st["alerts"]))


def prom():
    """GET /metrics: the headline numbers in Prometheus text format."""
    h, st, m, out = health(), api("status"), api("metrics"), []
    lab = lambda d: "{%s}" % ",".join('%s="%s"' % (k, str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " "))
                                      for k, v in d.items()) if d else ""

    def g(name, text, rows):
        out.append("# HELP pilothouse_%s %s" % (name, text)); out.append("# TYPE pilothouse_%s gauge" % name)
        out.extend("pilothouse_%s%s %s" % (name, lab(l), v) for l, v in rows)
    g("up", "1 while Pilothouse answers", [({}, 1)])
    g("cpu_percent", "Host CPU in use", [({}, m.get("cpu", 0))])
    g("memory_used_percent", "Host memory in use", [({}, m.get("memPct", 0))])
    g("memory_used_bytes", "Host memory in use", [({}, int((m.get("memUsedGB") or 0) * 2 ** 30))])
    g("swap_bytes", "Swap in use", [({}, int((m.get("swapGB") or 0) * 2 ** 30))])
    g("gpu_percent", "GPU in use (0 when it cannot be read)", [({}, m.get("gpu") or 0)])
    g("disk_used_percent", "Disk space in use", [({"mount": d["mount"]}, d["pct"]) for d in m.get("disk") or []])
    g("services", "Services by state", [({"state": k}, h[k]) for k in ("running", "down", "stopped")])
    g("alerts", "Alerts firing now", [({}, h["alerts"])])
    ids = lambda x: {"id": x["id"], "name": x["name"], "group": x["group"]}
    g("service_up", "1 when the service is running", [(ids(x), int(bool(x["up"]))) for x in st["services"]])
    g("service_memory_bytes", "Memory of a running service", [(ids(x), (x.get("memMB") or 0) * 1048576) for x in st["services"] if x["up"]])
    g("service_cpu_percent", "CPU of a running service", [(ids(x), x.get("cpuN") or 0) for x in st["services"] if x["up"]])
    try:
        g("usage_cost_usd_today", "Agent usage today at list price", [({}, api("usage_now").get("today") or 0)])
    except Exception:
        pass
    return "\n".join(out) + "\n"


def doctor(port):
    """--doctor: what Pilothouse can and cannot see on this machine. Tool names, versions and counts only,
    so the output is safe to paste into a bug report."""
    import platform, socket
    from . import __version__
    print("Pilothouse %s | Python %s | %s" % (__version__, platform.python_version(), platform.platform()))

    def row(ok, what, note=""):
        print("  [%s] %-20s %s" % ("ok" if ok else "--" if ok is False else "!!", what, note))

    def tool(name, adds, probe=None):
        if not which(name):
            return row(False, name, "not found: " + adds)
        try: note = probe() if probe else "found"
        except Exception as e: note = "found, but: %s" % e
        row(not str(note).startswith("found, but"), name, note)

    def ver(cmd, t=6):
        r = sh(cmd, t)
        return (r.stdout.strip().splitlines() or [""])[0][:60] if r and r.returncode == 0 and r.stdout.strip() else None

    print("\nBasics")
    row(sys.version_info >= (3, 8), "Python 3.8+", platform.python_version())
    d = DATA_DIR.replace(HOME, "~"); parent = DATA_DIR if os.path.isdir(DATA_DIR) else os.path.dirname(DATA_DIR)
    row(os.access(parent, os.W_OK), "data folder", "%s (%s)" % (d, "exists" if os.path.isdir(DATA_DIR) else "will be created"))
    try:
        with socket.socket() as so:
            so.bind(("127.0.0.1", port))
        row(True, "port %d" % port, "free")
    except OSError:
        try: r = http_json("http://127.0.0.1:%d/api/health" % port)
        except Exception: r = None
        mine_ = isinstance(r, dict) and r.get("ok")
        row(True if mine_ else None, "port %d" % port, "Pilothouse %s is already running here" % r.get("version") if mine_
            else "in use by something else: pick another with --port")
    off = os.environ.get("DASH_AUTH", "").lower() in ("off", "0", "no", "false", "none")
    row(None if off else True, "access token", "OFF (DASH_AUTH=off): anything on this machine can read and control Pilothouse" if off
        else "on (your own DASH_TOKEN)" if os.environ.get("DASH_TOKEN") else "on (kept in the data folder; `--link` prints the address with it)")

    print("\nTools (all optional)")
    tool("docker", "no containers, images, volumes or compose stacks",
         lambda: ("server " + ver(["docker", "version", "--format", "{{.Server.Version}}"])) if ver(["docker", "version", "--format", "{{.Server.Version}}"])
         else "found, but the Docker daemon does not answer: start Docker and check `docker ps`")
    tool("ollama", "no load/unload of models (downloaded models are still listed from disk)",
         lambda: "server running" if ollama_servers() else "installed, server not running")
    tool("git", "no Repos tab", lambda: ver(["git", "--version"]) or "found")
    tool("gh", "no GitHub accounts next to repos", lambda: "found")
    if IS_MAC:
        tool("lsof", "cannot tell which process owns a port")
        tool("brew", "no Homebrew services")
    elif IS_LINUX:
        tool("systemctl", "no restart for systemd user services")
        tool("nvidia-smi", "no GPU usage")
    elif IS_WIN:
        tool("powershell", "cannot list processes")

    print("\nWhat it sees right now")

    def count(what, fn):
        try: row(True, what, fn())
        except Exception as e: row(None, what, "failed: %s: %s" % (type(e).__name__, e))
    count("processes", lambda: "%d" % len(all_cmds()))
    count("listening ports", lambda: "%d" % len(listening()))

    def svcs():
        k = defaultdict(int)
        for x in discover_services(): k[x["kind"]] += 1
        return ", ".join("%d %s" % (n, kind) for kind, n in sorted(k.items())) or "none"
    count("services", svcs)
    count("AI tools installed", lambda: ", ".join(a["name"] for a in inv_agents() if a.get("installed")) or "none found (add yours to agents.json)")
    count("Ollama models on disk", lambda: "%d" % len(ollama_on_disk()))

    def usage():
        out = []
        for folder, test, _ in usage_sources():
            n = sum(1 for _, _, fs in os.walk(folder) for f in fs if test(f)) if os.path.isdir(folder) else 0
            out.append("%s: %d files" % (folder.replace(HOME, "~"), n))
        return "; ".join(out)
    count("usage records", usage)
    print("\nNothing here is required. Start it with: python3 server.py --open   (or --demo to look around first)")
    return 0


def _int(v, default, lo, hi):
    try: return max(lo, min(hi, int(float(v))))
    except (TypeError, ValueError, OverflowError): return default


class H(http.server.BaseHTTPRequestHandler):
    server_version = "Pilothouse"; sys_version = ""   # do not advertise the Python version
    timeout = 20                                       # a connection that sends nothing does not hold a thread for ever

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer"); self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

    def _local(self):
        """only this machine's browser, addressed as localhost (blocks DNS rebinding)."""
        return self.headers.get("Host", "") in LOCAL

    def _same_site(self):
        """browsers say where a request comes from: one that another site caused (an <img>, a form, a script) is refused
        before it can start any work. Tools like curl send no such header and pass on to the token check."""
        return self.headers.get("Sec-Fetch-Site") in (None, "same-origin", "none")

    def _authed(self):
        """with DASH_TOKEN set, the request must carry it. The page keeps it per origin and sends it as a header:
        a cookie would also be sent to every other program serving on localhost."""
        if not TOKEN:
            return True
        import hmac
        got = self.headers.get("Authorization") or ""
        return got.startswith("Bearer ") and hmac.compare_digest(got[7:].strip().encode(), TOKEN.encode())

    def do_GET(self):
        if not self._local():
            return self._send(403, b"forbidden", "text/plain")
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path); p = u.path.rstrip("/") or "/"
        q = parse_qs(u.query[:2000], keep_blank_values=True); one = lambda k, d="": q.get(k, [d])[0][:400]
        if p in ("/", "/index.html"):   # the page holds no data, so it needs no token: it asks the API with one
            try:
                with open(os.path.join(HERE, "web", "index.html"), "rb") as f: return self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError: return self._send(404, b"index.html not found", "text/plain")
        if not self._same_site():
            return self._send(403, json.dumps(dict(ok=False, msg="forbidden")))
        if p != "/api/health" and not self._authed():   # health is counts and a version: scripts may ask without the token
            return self._send(401, json.dumps(dict(ok=False, msg="unauthorized: this Pilothouse needs its access token")))
        try:
            if p == "/metrics": return self._send(200, prom(), "text/plain; version=0.0.4; charset=utf-8")
            if p == "/api/health": d = health()
            elif p == "/api/status": d = api("status")
            elif p == "/api/metrics": d = api("metrics")
            elif p == "/api/inventory": d = api("inventory", "force" in q)
            elif p == "/api/logs": d = api("logs", one("id"), _int(one("n", "200"), 200, 10, 2000))
            elif p == "/api/inspect": d = api("inspect", one("id"))
            elif p == "/api/history": d = api("history", one("range", "1h"), q.get("id", [None])[0], q.get("app", [None])[0])
            elif p == "/api/since": d = api("since", _int(one("ts", "0"), 0, 0, 2 ** 40))
            elif p == "/api/usage/now": d = api("usage_now")
            elif p == "/api/usage/session": d = api("usage_session", one("id"))
            elif p == "/api/usage": d = api("usage", one("range", "7d"))
            elif p == "/api/modelinfo": d = api("modelinfo", one("name"))
            else: return self._send(404, json.dumps(dict(ok=False, msg="not found")))
            self._send(200, jdump(d))
        except Exception as e:   # one bad probe must not take the page down with a dropped connection
            import traceback
            traceback.print_exc()   # the detail goes to the server's own log, not to whoever asked
            self._send(500, json.dumps(dict(ok=False, msg="error: %s" % type(e).__name__)))

    def do_POST(self):
        # a page on another site can fire a blind POST at localhost: require our own header + origin
        origin = self.headers.get("Origin")
        if (not self._local() or not self._same_site() or self.headers.get("X-Dash") != "1"
                or (origin and origin.split("//")[-1] not in LOCAL)):
            return self._send(403, json.dumps(dict(ok=False, msg="forbidden")))
        if self.path == "/api/pair":   # before the key check: this is how a browser gets the key
            ok, msg = pair()
            return self._send(200, json.dumps(dict(ok=ok, msg=msg)))
        if not self._authed():
            return self._send(401, json.dumps(dict(ok=False, msg="unauthorized: this Pilothouse needs its access token")))
        fn = POSTS.get(self.path.split("?")[0])
        if not fn:
            return self._send(404, json.dumps(dict(ok=False, msg="not found")))
        n = _int(self.headers.get("Content-Length"), 0, 0, 2 ** 31)
        if n > 65536:   # every real request is a few dozen bytes
            return self._send(413, json.dumps(dict(ok=False, msg="request too large")))
        if DEMO:
            self.rfile.read(n)
            return self._send(200, json.dumps(dict(ok=True, msg="Demo mode: nothing was changed")))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            ok, msg = fn(body if isinstance(body, dict) else {})
        except Exception as e:
            import traceback
            traceback.print_exc()
            ok, msg = False, "error: %s" % type(e).__name__
        self._send(200 if ok else 500, jdump(dict(ok=ok, msg=msg)))

    def log_message(self, *a): pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def migrate_data():
    """one-time move of the pre-rename data folder; if it cannot be moved it simply keeps being used."""
    global DATA, DATA_DIR
    if not os.environ.get("DASH_DATA") and DATA_DIR == _LEGACY_DATA:
        try:
            os.rename(_LEGACY_DATA, os.path.join(HOME, ".pilothouse")); DATA = DATA_DIR = os.path.join(HOME, ".pilothouse")
        except OSError:
            pass


def _test_engine():
    assert engine_name("unix:///Users/x/.colima/default/docker.sock") == "Colima"
    assert engine_name("unix:///Users/x/.orbstack/run/docker.sock") == "OrbStack"
    assert engine_name("unix:///Users/x/.docker/run/docker.sock") == "Docker Desktop"
    assert engine_name("npipe:////./pipe/dockerDesktopLinuxEngine") == "Docker Desktop"
    assert engine_name("unix:///run/user/1000/podman/podman.sock") == "Podman"
    assert engine_name("unix:///var/run/docker.sock") in ("Docker Desktop", "Docker Engine")


def selftest():
    _test_engine()
    """python3 server.py --selftest : the parsers and guards that everything else leans on."""
    # the per-OS parsers are checked on every OS, from captured output
    li, es = parse_netstat("  Proto  Local Address   Foreign Address  State  PID\n  TCP  0.0.0.0:135  0.0.0.0:0  LISTENING  1128\n"
                           "  TCP  127.0.0.1:5432  127.0.0.1:51000  ESTABLISHED  4321\n  TCP  [::]:8080  [::]:0  LISTENING  77\n  UDP  0.0.0.0:53  *:*  9\n")
    assert li == {135: 1128, 8080: 77} and es == [(4321, 5432, 51000)], (li, es)
    wp = parse_win_procs(json.dumps([
        dict(ProcessId=4, ParentProcessId=0, Name="System", WorkingSetSize=1048576, KernelModeTime=20000000, UserModeTime=0, Start=0,
             ExecutablePath=None, CommandLine=None),
        dict(ProcessId=900, ParentProcessId=4, Name="ollama.exe", WorkingSetSize=2097152, KernelModeTime=0, UserModeTime=10000000,
             Start=1700000000, ExecutablePath="C:\\Tools\\ollama.exe", CommandLine="ollama.exe serve")]))
    assert [w["pid"] for w in wp] == [4, 900] and wp[0]["cput"] == 2.0 and wp[1]["rss"] == 2.0 and wp[1]["cmd"] == "ollama.exe serve"
    assert parse_win_procs("not json") == [] and base_name("C:\\Tools\\ollama.exe") == "ollama" and base_name("/usr/bin/node") == "node"
    assert parse_cgroup("/user.slice/user-1000.slice/user@1000.service/app.slice/n8n.service") == ("n8n.service", True)
    assert parse_cgroup("/system.slice/docker.service") == ("docker.service", False) and parse_cgroup("/user.slice/session-3.scope") == (None, False)
    assert fmt_etime(59) == "00:59" and fmt_etime(3661) == "01:01:01" and fmt_etime(90061) == "01-01:01:01"
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "a", "repo", ".git")); os.makedirs(os.path.join(td, "node_modules", "x", ".git"))
        with open(os.path.join(td, "a", "f.bin"), "wb") as fh:
            fh.write(b"x" * 1000)
        found = [pth for pth, e, isdir in walk(td, 4, {"node_modules", ".git"}) if isdir and e.name == ".git"]
        assert len(found) == 1 and found[0].endswith(os.path.join("repo", ".git")), found
        assert dir_size(td) == 1000 and isinstance(open_cmd(td, "folder") or [], list)
    assert kill_pid(2 ** 22 + 12345)[0] is False   # no such process: an error, not a crash
    pm = load_price_map({"openai/gpt-x": dict(input_cost_per_token=2e-6, output_cost_per_token=8e-6, cache_read_input_token_cost=5e-7),
                         "free": dict(input_cost_per_token=0, output_cost_per_token=0), "bad": {}})
    assert pm == {"gpt-x": (2.0, 8.0, 0.25)}, pm
    with tempfile.TemporaryDirectory() as td:
        f1 = os.path.join(td, "rollout-1.jsonl")
        with open(f1, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(type="turn_context", payload=dict(model="gpt-x", cwd="/w"))) + "\n")
            fh.write(json.dumps(dict(timestamp="2026-01-02T03:04:05Z", type="event_msg", payload=dict(type="token_count", info=dict(
                last_token_usage=dict(input_tokens=1000, cached_input_tokens=400, output_tokens=50, reasoning_output_tokens=20))))) + "\n")
        c = scan_codex_file(f1, td); v = list(c["b"].values())[0]
        assert c["cwd"] == "/w" and v[:3] == [1, 600, 50] and v[5] == 400 and v[6] == 20 and list(c["b"])[0].split("|")[1] == "gpt-x", c
        f2 = os.path.join(td, "mytool.jsonl")
        with open(f2, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(ts="2026-01-02T03:04:05Z", agent="MyTool", model="m", input=10, output=5, session="a", cost=0.5)) + "\n")
            fh.write(json.dumps(dict(ts="2026-01-02T04:04:05Z", agent="MyTool", model="m", input=1, output=1, session="b")) + "\nnot json\n")
        g = scan_generic_file(f2, td)
        assert len(g) == 2 and g[0]["tool"] == "MyTool" and list(g[0]["fixed"].values()) == [0.5] and list(g[1]["b"].values())[0][:3] == [1, 1, 1]
    assert xp("~/x").endswith("x") and "~" not in xp("~/x")
    pat = {d[0]: d[4] for d in AGENT_DEFS}   # a command that only mentions a tool is not that tool running
    assert not re.search(pat["opencode"], "zsh -c echo opencode is nice") and re.search(pat["opencode"], "/usr/local/bin/opencode serve")
    assert not re.search(pat["codex"], "vim notes-about-codex.md") and re.search(pat["codex"], "/opt/homebrew/bin/codex exec")
    assert re.search(pat["ollama"], "/opt/homebrew/opt/ollama/bin/ollama serve") and re.search(pat["ollama"], "/x/lib/ollama/llama-server --model m")
    assert len({d[0] for d in AGENT_DEFS}) == len(AGENT_DEFS)
    assert to_mb("1.5GiB") == 1536 and to_mb("512MiB") == 512 and to_mb("") == 0
    assert num("12.5%") == 12.5 and fmt_mb(2048) == "2.0 GB"
    t = table("NAME      ID     SIZE     UNTIL\nqwen:7b   abc    26 GB    Forever\n")
    assert t == [dict(name="qwen:7b", id="abc", size="26 GB", until="Forever")], t
    assert app_name("/Applications/Google Chrome.app/Contents/x/Google Chrome Helper.app/Contents/MacOS/h") == "Google Chrome"
    ps_table()
    assert os.getpid() in ME and not mine(dict(uid=UID, pid=os.getpid())) and not mine(dict(uid=UID, pid=1))
    assert not mine(dict(uid=UID + 1, pid=99999)) and mine(dict(uid=UID, pid=99999))
    assert signal_pid(1)[0] is False and proc_action(os.getpid())[0] is False
    assert group_action("x", "rm")[0] is False and docker_action("system prune")[0] is False
    assert safe_env(["DB=postgres://u:pw@h:5432/x", "API_KEY=1", "A=b", "MYSQL_PWD=x", "SENTRY_DSN=x", "GH_PAT=x",
                     "R=redis://:hunter2@cache:6379/0", "G=https://ghp_abcdefghijklmnopqrstuvwx@github.com/o/r"]) == [
        "DB=postgres://***@h:5432/x", "A=b", "R=redis://***@cache:6379/0", "G=https://***@github.com/o/r"]
    # credentials are masked in command lines and log lines; plain numbers are left readable
    for raw, want in (("redis-server --requirepass hunter2 --port 6379", "redis-server --requirepass *** --port 6379"),
                      ("llm serve --api-key=sk-live-1234567890abcdef --max-tokens 4096", "llm serve --api-key=*** --max-tokens 4096"),
                      ("redis-cli -a hunter2 ping", "redis-cli -a *** ping"), ("n_tokens = 512, password=s3cret!", "n_tokens = 512, password=***"),
                      ("Authorization: Bearer abcdefgh12345678", "Authorization: Bearer ***"),
                      ("clone https://user:tok@github.com/o/r.git", "clone https://***@github.com/o/r.git"),
                      ("python3 -m http.server 8999 --bind 127.0.0.1", "python3 -m http.server 8999 --bind 127.0.0.1")):
        assert redact(raw) == want, (raw, redact(raw))
    # a git remote becomes a link only when it is a plain https address
    assert web_url("git@github.com:o/r.git") == "https://github.com/o/r" and web_url("https://u:t@github.com/o/r.git") == "https://github.com/o/r"
    assert web_url('https://example.invalid/"><img src=x onerror=1>/r.git') is None and web_url("javascript:alert(1)") is None
    # git runs only in a repository whose own config cannot start another program
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        gd = os.path.join(td, ".git"); os.makedirs(os.path.join(gd, "logs"))
        put = lambda name, text: open(os.path.join(gd, name), "w", encoding="utf-8").write(text)
        put("HEAD", "ref: refs/heads/main\n")
        put(os.path.join("logs", "HEAD"), "%s %s A <a@b> 1700000000 +0000\tcommit: First\n%s %s A <a@b> 1700000100 +0000\tcheckout: moving\n" % (("0" * 40, "1" * 40) * 2))
        plain = '[core]\n\trepositoryformatversion = 0\n\tbare = false\n[remote "origin"]\n\turl = git@github.com:o/r.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n[branch "main"]\n\tremote = origin\n'
        put("config", plain); cfg = git_config(gd)
        assert git_trusted(gd, cfg) and git_text(gd, cfg) == ("main", "git@github.com:o/r.git", 1700000000, "First"), git_text(gd, cfg)
        for bad in ("[core]\n\tfsmonitor = touch /tmp/x\n", '[filter "x"]\n\tclean = touch /tmp/x\n', "[gpg]\n\tprogram = /tmp/x\n",
                    "[include]\n\tpath = ../evil\n", "[core] hooksPath = x\n", "[core]\n\tsshCommand = x \\\n y\n", "[alias]\n\tst = !x\n"):
            put("config", plain + bad)
            assert not git_trusted(gd, git_config(gd)), bad
        put("config", plain + "[extensions]\n\tworktreeConfig = true\n"); assert git_trusted(gd, git_config(gd))
        put("config.worktree", "[core]\n\tfsmonitor = x\n"); assert not git_trusted(gd, git_config(gd))
    assert jdump(dict(a=float("nan"), b=[float("inf"), 1.5])) == '{"a": 0, "b": [0, 1.5]}'
    assert qualifier(dict(kind="host", _cmd="node /x/whatsapp-bridge/bridge.js --port 3000", port=3000)) == "whatsapp-bridge"
    assert qualifier(dict(kind="host", _cmd="llama-server --no-webui --model /m/qwen-35b.gguf", port=1)) == "qwen-35b"
    assert role_token("srv --no-webui --port 1") == "" and role_token("/x/@embedded-postgres/bin/postgres") == ""
    assert qualifier(dict(kind="host", _cmd="postgres -D /x", _owner="Paperclip", port=1, pid=None)) == "for Paperclip"
    assert qualifier(dict(kind="host", _cmd="python3 -m hermes_cli.main gateway run", port=1)) == "gateway"
    assert qualifier(dict(kind="host", launchd="local.ollama-embed", port=1, pid=None)) == "ollama-embed"
    assert agent_action("nope")[0] is False and agent_action("claude", "rm")[0] is False
    # 1M input + 1M output + 1M 5-minute cache write + 1M 1-hour cache write + 1M cache read on Opus 4.8 ($5 / $25)
    assert abs(cost_of("claude-opus-4-8", False, False, [1, 1e6, 1e6, 1e6, 1e6, 1e6, 0, 0]) - (5 + 25 + 6.25 + 10 + 0.5)) < 1e-9
    assert abs(cost_of("claude-fable-5-2", False, False, [1, 0, 0, 0, 0, 1e6, 0, 0]) - 0.20) < 1e-9          # cache read at 0.025x
    assert abs(cost_of("claude-haiku-4-5-20251001", False, True, [1, 1e6, 0, 0, 0, 0, 0, 0]) - 0.5) < 1e-9   # dated id, batch = half
    USAGE["t"] = dict(sid="t", sub=False, b={time.strftime("%Y-%m-%d %H") + "|claude-opus-4-8|0|0": [2, 1e6, 0, 0, 0, 0, 0, 0],
                                             "2020-01-01 09|claude-opus-4-8|0|0": [1, 1e6, 0, 0, 0, 0, 0, 0]})
    bl = usage_blocks(30); USAGE.pop("t")
    assert len(bl) == 1 and abs(bl[0]["cost"] - 5) < 1e-9 and bl[0]["msgs"] == 2      # old activity is outside the look-back
    assert set_budgets(dict(day="x"))[0] is False and CTX_MAX("claude-haiku-4-5-20251001") == 200000
    assert cost_of("claude-opus-5-5", True, False, [1, 1e6, 0, 0, 0, 0, 0, 0]) == 8 and cost_of("qwen3.6:35b", False, False, [1] * 8) is None
    assert open_path("repo", "/etc")[0] is False and open_path("x", "y")[0] is False
    assert model_action("a; id", "delete")[0] is False
    print("selftest ok")


def main(argv=None):
    """entry point for `python3 server.py`, `python3 -m pilothouse` and the `pilothouse` command."""
    global PORT, LOCAL, DEMO, TOKEN
    import argparse
    from . import __version__
    ap = argparse.ArgumentParser(prog="pilothouse", description="One local page to see and steer your AI stack. "
                                 "Serves on 127.0.0.1 only. Settings are environment variables: see docs/CONFIGURATION.md.")
    ap.add_argument("--port", type=int, default=PORT, help="port to serve on (default %(default)s, or DASH_PORT)")
    ap.add_argument("--open", action="store_true", help="open the page in your browser once it is up")
    ap.add_argument("--demo", action="store_true", help="show made-up sample data; reads and changes nothing on this machine")
    ap.add_argument("--doctor", action="store_true", help="print what Pilothouse can and cannot see on this machine, then exit")
    ap.add_argument("--link", action="store_true", help="print the address to open, including the access token, then exit")
    ap.add_argument("--selftest", action="store_true", help="run the built-in checks and exit")
    ap.add_argument("--version", action="version", version="pilothouse " + __version__)
    a = ap.parse_args(argv)
    if a.selftest:
        selftest(); return 0
    if a.doctor:
        return doctor(a.port)
    PORT = a.port; DEMO = a.demo
    LOCAL = {"127.0.0.1:%d" % PORT, "localhost:%d" % PORT, "[::1]:%d" % PORT}
    migrate_data()
    # the sample data is not secret, so --demo asks for a token only when you set one yourself
    TOKEN = (os.environ.get("DASH_TOKEN", "") if DEMO else load_token())
    if a.link:
        print(link()); return 0
    import webbrowser
    try:
        httpd = Server(("127.0.0.1", PORT), H)
    except OSError as e:
        try: running = http_json("http://127.0.0.1:%d/api/health" % PORT).get("version")
        except Exception: running = None
        if running:   # started twice (a login item plus a double-click): show the one that is already there
            print("Pilothouse %s is already running on port %d." % (running, PORT))
            if a.open: webbrowser.open(link())
            elif sys.stdout.isatty(): print("Open " + link())
            return 0
        print("Cannot listen on 127.0.0.1:%d (%s).\nSomething else is using that port. "
              "Pick another: python3 server.py --port %d" % (PORT, e.strerror or e, PORT + 1), file=sys.stderr)
        return 1
    if not DEMO:
        db_open()
        event("action", "Dashboard started (monitoring resumed)")
        for job in (sampler, watcher, model_scanner, usage_scanner, live_scanner):
            threading.Thread(target=job, daemon=True).start()
    print("Pilothouse %s (%s)%s" % (__version__, PLATFORM, " DEMO: sample data, nothing is read or changed" if DEMO else ""))
    # the link carries the token, so it is printed only to a person at a terminal, never into a log file
    print("  open  " + (link() if sys.stdout.isatty() or not TOKEN else
                        "http://localhost:%d  (first time in a browser: run `python3 server.py --link` and open what it prints)" % PORT), flush=True)
    if a.open:
        threading.Timer(0.5, webbrowser.open, (link(),)).start()
    with httpd:
        try: httpd.serve_forever()
        except KeyboardInterrupt: print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
