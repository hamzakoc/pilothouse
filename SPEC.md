# Local AI Control — Auto-detecting Local Ops Dashboard (open source)

Zero-config: clone, run `python3 server.py`, open http://localhost:7777. It must
**auto-detect everything on the host it runs on** — no hardcoded service names,
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
- **Control (generic & safe)**: Docker → start/stop/restart via `docker`;
  brew-managed services → via `brew services`; everything else is status-only.
- **Logs/inspect**: Docker via `docker logs`/`docker inspect`; others best-effort.

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

### Host metrics  (`GET /api/metrics`)  — cross-platform
CPU / mem / load / swap / disk / net / GPU + 90-sample rolling history, top
processes, docker stats, loaded LLMs. **macOS**: top/vm_stat/ioreg/netstat.
**Linux**: /proc/stat, /proc/meminfo, /proc/net/dev, nvidia-smi. Degrades
gracefully (missing source → 0, never crash).

## Config (optional, env)
`DASH_PORT` (default 7777) · `DASH_REPOS` (colon-separated extra repo roots) ·
`DASH_HIDE` (extra process-name substrings to treat as noise).

## Topology map
Built from discovered services at runtime (cytoscape + dagre): one node per
service, grouped into compound boxes by category, colored by live status, 🐳 for
Docker. No hardcoded nodes/edges.

## Safety
127.0.0.1 only · control commands derived from discovery, never from the browser
· destructive ops confirm-gated · no arbitrary exec.

## Acceptance
On a fresh clone on any macOS/Linux host: services, agents, models, repos, and
metrics populate with that host's real state, zero edits. No reference to any
specific machine remains in the code.
