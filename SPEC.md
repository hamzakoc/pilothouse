# Pilothouse: auto-detecting local dashboard for your AI stack (open source)

Zero-config: clone, run `python3 server.py`, open http://localhost:7777. It must
**auto-detect everything on the host it runs on** - no hardcoded service names,
ports, paths, or accounts. Stdlib Python backend (no deps), vanilla HTML/JS front
end (cytoscape + dagre from CDN for the map). Binds 127.0.0.1 only, no sudo.

## Auto-detection (the core contract)
### Services  (`GET /api/status`)
Discover live services with NO hardcoded list:
- **Docker**: every container from `docker ps` → name, image, status, published
  host port, compose project (group = "Docker · <project>"), health.
- **Host ports**: every listening TCP port (`lsof`/`ss`) not owned by Docker,
  grouped by pid, recognized into a friendly name + category from a *hint table*
  (ports + process-name patterns), not a per-machine list.
- **Categories (auto)**: AI / LLM, Database, Automation, Web / Dev, Infra,
  Docker · <project>, Other. Noise (the dashboard itself, browsers, IDEs, OS
  daemons) is filtered.
- **Control (generic & safe)** (`POST /api/action|group|proc|app|model|docker`):
  Docker -> start/stop/restart/pause/unpause/kill, and restart/stop/start for a whole
  compose project; Homebrew services (matched by their `homebrew.mxcl.*` launchd
  label) -> `brew services`; other launchd agents -> `launchctl kickstart -k`; any
  other process owned by the current user -> SIGTERM / SIGKILL. Never pid 1, never
  another user's process, never the dashboard itself.
- **Per-service resources**: numeric CPU % and memory (containers from `docker stats`,
  host services = the process plus its children) with a rolling history per service.
- **Events + alerts**: every refresh is diffed against the last one (up, down,
  appeared, gone) and every action is logged. Alerts: crashed / unhealthy /
  restart-looping container, memory, disk, sustained CPU.
- **Logs/inspect**: Docker via `docker logs`/`docker inspect`; host processes via the
  file behind their stdout/stderr or an open `*.log`.

### Agents / CLIs  (`GET /api/inventory` → agents)
Probe a table of known AI tools (Claude Code, Codex, OpenClaw, Ollama, Aider,
LM Studio, Open Interpreter, Goose, llm, Hermes, Paperclip, Jan, …) by binary on
PATH + known home dir; show only the ones actually present. running = pgrep.

### Models  (`GET /api/inventory` → models)
`ollama list` / `ollama ps` (loaded + size), plus a bounded scan for `.gguf`
files in common model dirs (LM Studio cache, ~/models, HF cache). No hardcoding.

### Repos  (`GET /api/inventory` → github)
Scan common dev locations that exist (~/Desktop, ~/Projects, ~/dev, ~/code,
~/src, ~/Developer, ~/git, ~/repos, ~/workspace, …; override `DASH_REPOS`) for
`.git` (bounded depth), report branch + remote + web URL. `gh auth status` for
accounts if gh is present.

### Host metrics  (`GET /api/metrics`)  - cross-platform
CPU / mem / load / swap / disk / net / GPU + ~5 min of 2 s samples in memory, top
processes, docker stats, loaded LLMs. **macOS**: top/vm_stat/ioreg/netstat.
**Linux**: /proc/stat, /proc/meminfo, /proc/net/dev, nvidia-smi. Degrades
gracefully (missing source → 0, never crash).

Also in `/api/metrics`: memory composition (apps / wired / compressed / cached /
free, same definition as Activity Monitor), memory + CPU **per app** (helper
processes folded into their app), top processes by CPU and by memory.
`/api/inventory` adds Docker storage (images, volumes, build cache, what is unused).

## History  (`GET /api/history`, `GET /api/since`)
Per-minute host metrics, per-minute per-service CPU/memory, every event and every
observed link are written to sqlite under `DASH_DATA` (default
`~/.pilothouse`): 48 h of metrics, 14 days of events, 24 h of links. Without a
writable data dir the app still runs, live only.

## Config (optional, env)
`DASH_PORT` (default 7777) · `DASH_REPOS` (colon-separated extra repo roots) ·
`DASH_HIDE` (extra process-name substrings to treat as noise).

## Map
Built from discovered services at runtime (cytoscape + dagre). One node per service
(name / purpose / memory, CPU, port). Edges are `[src, dst, why, ageSeconds]` and
need evidence: a live TCP connection to a listening port, a container variable that
names another container on a shared network (or `host.docker.internal:port`), or
compose `depends_on`. `localhost` inside a container is the container itself and is
never an edge. No hardcoded nodes or edges.

## UI rules (every screen follows these)
Sources: Docker Desktop containers/images views, NN/g on data tables, progressive
disclosure and indicators, Carbon and Elastic EUI table guidelines, Grafana dashboard
best practices, Refactoring UI ("labels are a last resort"), Stephen Few's dashboard
pitfalls.

1. **One row design.** A row is: status dot, human name, one muted line saying what it
   is, then right-aligned numbers, then at most two visible actions and a "more" menu.
   No raw IDs as the primary label.
2. **Every list has a titled section** with a one-line count/summary; sections that
   belong together share a color (a compose stack, a kind of service) and that color is
   the same on the map, in the Services list and in the Docker tab.
3. **Each screen opens with one sentence** in plain words saying what it shows.
4. **Two levels only:** list, then the detail drawer. The drawer shows key facts as
   chips and the actions first; long sections (environment, storage, network) are
   folded until asked for.
5. **Numbers:** right-aligned, tabular, one unit style ("12 MB", "1.6 GB"), durations
   as "1d 5h". Status is a dot plus a word, never color alone.
6. **Nothing is highlighted unless it needs action.** The Overview leads with one
   status sentence; alerts appear only when firing; long lists show the top few with
   "show all".
7. **Docker = three views** (Containers by stack, Images, Volumes), not three stacked
   tables. Models = "In memory now" and "On disk".
8. Destructive buttons are red, ask first, and say what else is affected.

## Safety
127.0.0.1 only · control commands derived from discovery, never from the browser
· destructive ops confirm-gated · no arbitrary exec · POSTs require the `X-Dash`
header and a local Origin, every request a local Host (CSRF / DNS rebinding).

## Acceptance
On a fresh clone on any macOS/Linux host: services, agents, models, repos, and
metrics populate with that host's real state, zero edits. No reference to any
specific machine remains in the code.

## Security contract  (enforced by `--selftest`, `scripts/smoke.py`, `scripts/check.sh`, `scripts/hostile-data.js`)
- Binds 127.0.0.1 only. `Host` must be local. A request another site caused (`Sec-Fetch-Site`) is refused.
- Every `/api/*` and `/metrics` request needs the access key (`Authorization: Bearer`), except `/api/health`.
  The key is random per install, 0600 in the data folder, handed to the page in the URL fragment.
- `POST` also needs `X-Dash: 1`, a local `Origin` and a body under 64 KB.
- The browser names things; the server builds every command as an argument list. Only the user's own
  processes are signalled, never pid 1, never Pilothouse.
- `git` is never run in a discovered repository unless its own config passes the allowlist (`git_trusted`),
  and then only through `git_run`.
- Everything that may hold a credential passes `redact()`; secret-named environment variables are dropped.
- The page escapes every value from the machine: `esc` (text, attributes), `jq` (inline handlers), `url` (links).
- Responses carry CSP, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, CORP `same-origin`. CDN scripts carry SRI.
- `--demo` reads nothing from the host, opens no database and changes nothing.
