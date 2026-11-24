# Local AI Control

A zero-config, auto-detecting dashboard for your local AI stack and dev services.
Clone it, run it, open it — it discovers whatever is running on **your** machine.
No config files, no accounts, no `pip install`.

![status](https://img.shields.io/badge/deps-none%20(stdlib)-brightgreen) ![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-blue)

## What it does (all auto-detected)

- **Services** - every Docker container (running or stopped) and every listening host
  port, auto-named with a one-line "what it is", auto-grouped (AI / LLM, Database,
  Automation, Web / Dev, Infra, Docker · _project_). Each row shows live memory, CPU
  and a memory trend; sort by memory or CPU, filter to running / stopped / problems.
- **Control** - Docker: start / stop / restart / pause / resume / kill / remove, plus
  restart or stop a whole compose project. Homebrew services: start / stop / restart.
  launchd agents: restart, "stop and keep stopped" (unload), start again. A KeepAlive
  agent that is not running shows up as stopped, with a Start button. Any other
  process of yours: stop or force kill. Destructive actions ask first and say what
  else goes down with them.
- **Where memory goes** - Activity Monitor style breakdown (apps / wired / compressed /
  cached / free) and memory + CPU **per app** with helper processes folded into their
  app. Quit an app or stop a single process from the table.
- **Service detail** - live CPU and memory charts, what manages it (Docker, Homebrew,
  launchd label, or by hand), image, restart policy, limits, ports, networks, mounts,
  child processes, open files, dependencies, environment (secrets hidden).
- **Logs** - `docker logs` for containers and, for host processes, the file their
  stdout/stderr or `*.log` handle points at. Filter, follow, copy.
- **History that survives the night** - one sample per minute and every event are
  written to a small sqlite file (48 h of metrics, 14 days of events). The history
  chart and each service's charts switch between Live / 1h / 6h / 24h / 48h. When you
  come back after a while, a "While you were away" strip says what went down, what
  alerts fired and the peak memory / CPU / swap, with times.
- **Activity** - the timeline (up, down, appeared, vanished, every action you took),
  the active alerts, and the alert rules with their current value and threshold:
  memory, macOS memory pressure, sustained CPU, disk, crashed / unhealthy /
  restart-looping containers, always-on services that are not running. Optional
  desktop notifications.
- **Docker** - every container with state, CPU, memory, ports and one-click control;
  container detail down to health-check output, processes inside, mounts, limits and
  environment; images (update, remove unused) and volumes (remove unattached); true
  `docker system df` totals; prune dangling / unused / stopped / build cache.
- **Map** - what talks to what, drawn as a left-to-right flow. A link exists only with
  evidence, shown on hover: a live TCP connection, a container's configuration
  pointing at another container, or compose `depends_on`. Links are remembered for
  24 h (dashed when not live right now). Each connected set of services is its own
  colored, titled section; services with no link sit in a section per kind. Every
  card shows the name, one line of what it is for, and memory / CPU / port, with a
  menu of actions on the card and stack-level buttons on Docker sections.
- **Usage and cost** - tokens and cost per session, per agent, per model, per project
  and per day, read from each agent's own logs (Claude Code transcripts, Hermes'
  session database). Input, cache writes (5-minute and 1-hour), cache reads, output
  and thinking are shown separately; a session opens to a per-model, per-hour and
  per-sub-agent breakdown. Forked or resumed sessions are counted once. Prices are
  Anthropic's public list prices and can be edited in `pricing.json`; local models
  cost nothing. Nothing leaves the machine and message text is never stored.
- **Right now** - the open 5-hour usage window (cost so far, burn rate, projected total
  at reset, compared with your own busiest and typical windows), the rolling 7 days
  against the 7 before, and every agent session active in the last 30 minutes with
  what it is doing (working, waiting for you, stalled), how full its context window
  is and what it has cost today. A notification fires when a session finishes and
  waits for you. Short untitled automated runs are folded away.
- **Budgets** - per day, per 7 days, per 5-hour window and per session, in dollars at
  list price. 80% warns and 100% alerts, in the header, on the Overview and in the
  Activity timeline.
- **Memory is footprint, not resident size** - resident + compressed + swapped, the
  number Activity Monitor shows. A model the OS has compressed still shows as the
  25 GB it costs you, and each app says how much of it is compressed or swapped.
- **AI agents** — detects installed AI CLIs/apps (Claude Code, Codex, OpenClaw,
  Ollama, Aider, LM Studio, Open Interpreter, Goose, llm, Hermes, Paperclip, Jan, …)
  and shows which are running. Click one for details.
- **Models** - your downloaded LLMs (Ollama + local `.gguf` scan), which are loaded
  in memory and on what (GPU %, context, keep-alive). Load, unload or delete; click a
  model for architecture, parameters, quantization and capabilities.
- **Repos** — git repositories under your common dev folders, with branch and a
  clickable GitHub link; plus your `gh` accounts.
- **Live host metrics** — CPU / memory / GPU / load / swap / disk / network with a
  rolling history chart, top processes, and per-container stats. macOS **and** Linux.
- Light / dark theme, keyboard shortcuts (⌘K palette, `/` filter), always-live.

## Run it

```bash
python3 server.py
```

Then open **http://localhost:7777**. Or double-click a launcher: `start.command`
(macOS), `start.bat` (Windows), `start.sh` (Linux).

Requirements: **Python 3.8+** only, nothing to `pip install`. Everything else is
optional: it shows more when `docker`, `ollama`, `git` or `gh` are installed.

## Operating systems

| | macOS | Linux | Windows |
|---|---|---|---|
| Services, ports, processes, memory, CPU | `ps` + `lsof` | read from `/proc` (no extra tools) | PowerShell + `netstat` (built in) |
| Stop / kill a process | yes | yes | yes (`taskkill`) |
| Restart a managed service | launchd, Homebrew | systemd user units | not yet |
| Docker containers, images, volumes | yes | yes | yes |
| Map of what talks to what | yes | yes | yes |
| Agents, models, repos, usage and cost | yes | yes | yes |
| Open folder / editor / terminal | yes | yes | yes |
| Memory breakdown | apps / wired / compressed / cached | apps / cached / free | used / free |
| Tested by | the author, daily | automated run in a Linux container | automated run in CI only |

`.github/workflows/ci.yml` starts the dashboard on all three systems on every push,
has it discover a small test server, and stops that server through the API
(`scripts/smoke.py`). Windows has not been used by a person yet: treat it as new,
and please open an issue with what you see.

## AI tools it recognises

Coding agents: Claude Code, Claude desktop, Codex, Gemini CLI, GitHub Copilot CLI,
Cursor, Windsurf, Zed, opencode, Amp, Aider, Cline, Roo Code, Kilo Code, Continue,
Qwen Code, Crush, Factory Droid, Kiro, Amazon Q CLI, Goose, Open Interpreter,
OpenClaw, Hermes, Paperclip, llm, ChatGPT desktop.
Local runtimes and UIs: Ollama, LM Studio, llama.cpp, MLX LM, vLLM, LocalAI, Jan,
GPT4All, Msty, AnythingLLM, Open WebUI, ComfyUI, text-generation-webui.

Anything else: add it to `agents.json` next to `server.py` (or in the data folder):

```json
[{"id": "mytool", "name": "My Tool", "bins": ["mytool"], "dirs": ["~/.mytool"],
  "process": "mytool", "desc": "what it is", "port": 9000}]
```

A tool that is not in the list still shows up under Services whenever it listens on
a port, with its memory, CPU, logs and stop button.

**Usage and cost** is read from the agent's own records, so it exists only where
those records do: Claude Code (verified against Claude Code's own per-session cost)
and Hermes today; Codex is read from its documented log format but has not been
checked against real sessions. For any other tool, write one line per request into
`~/.local-ai-control/usage/<name>.jsonl`:

```json
{"ts": "2026-01-02T03:04:05Z", "agent": "My Tool", "model": "some-model", "input": 1200,
 "output": 300, "cache_read": 0, "cache_write": 0, "session": "abc", "cwd": "/path", "cost": 0.02}
```

Claude models are priced out of the box. "Get prices for other vendors" on the Usage
tab downloads a public community price list (LiteLLM's) so other models get a cost
too; it is off until you click it and sends nothing about you.

## Configuration (optional, env vars)

| Variable | Default | Purpose |
|----------|---------|---------|
| `DASH_PORT` | `7777` | Port to serve on |
| `DASH_REPOS` | — | Extra repo roots to scan, separated by `:` (`;` on Windows) |
| `DASH_HIDE` | — | Extra process-name substrings to treat as noise, comma-separated |
| `DASH_DATA` | `~/.local-ai-control` | Where the history database lives |
| `DASH_ALERT_MEM` / `_DISK` / `_CPU` | `88` / `90` / `90` | Alert thresholds in percent |

```bash
DASH_PORT=8899 DASH_REPOS=/opt/work python3 server.py
```

## Keep it always running

**macOS (launchd)** — create `~/Library/LaunchAgents/local-ai-control.plist`
pointing `ProgramArguments` at `python3 /path/to/server.py`, with `RunAtLoad` and
`KeepAlive` true, then `launchctl bootstrap gui/$(id -u) <plist>`.

**Linux (systemd user service)** — a unit with
`ExecStart=/usr/bin/python3 /path/to/server.py` + `systemctl --user enable --now`.

## How detection works

- Services: `docker ps` (containers, compose project, published ports) + listening
  TCP ports (`lsof` on macOS, `ss` on Linux) recognized by a generic port/process
  hint table — not a per-machine list. Noise (browsers, IDEs, OS daemons, the
  dashboard itself) is filtered.
- Metrics: macOS uses `top`/`vm_stat`/`ioreg`/`netstat`; Linux uses `/proc` +
  `nvidia-smi`. Missing sources degrade to `0`, never crash.
- Agents/models/repos: probe known tools, scan `ollama` + common model/gguf dirs,
  and walk common dev folders for `.git`.

## Safety

Binds to `127.0.0.1` only (never exposed to the network). Never uses `sudo`.
Control commands are derived from what it discovered - the browser can only name a
service id, a pid or an app, never send a command. No arbitrary code execution.

- It only signals processes owned by **you**, never pid 1 and never itself.
- Every state-changing request needs a custom header and a local `Origin`/`Host`, so
  another website open in your browser cannot fire actions at it (CSRF and DNS
  rebinding are refused).
- Secrets in container environments (`KEY`, `SECRET`, `TOKEN`, `PASS`, ...) are never
  sent to the page.
- Bulk prune never touches volumes. Removing a single image or volume is only
  possible when Docker itself reports it as unused, and it asks first.

Check a running instance with `scripts/check.sh` (read-only) and the parsers with
`python3 server.py --selftest`.

## Known limits

- Windows: stopping a program works; restarting Windows services is not wired, the
  owner of a process is not shown, and per-process memory is the working set.
- Linux: system-wide systemd units need root, so only user units get Restart; a
  listening port owned by another user cannot be attributed to a process.
- Restart-at-login is yours to set up (launchd, systemd or Task Scheduler).
- Nothing is recorded while the dashboard itself is not running; the summary says so
  when a period has gaps. Run it as a login service to cover the night.
- Map links between two containers come from their configuration, not from sniffing
  traffic, so a link nobody configured by name will not appear.
- Memory per app is resident size summed over its processes, so memory shared
  between processes is counted more than once (same as most process monitors).

## License

MIT, see `LICENSE`.
