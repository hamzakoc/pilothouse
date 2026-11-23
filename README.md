# Local AI Control

A zero-config, auto-detecting dashboard for your local AI stack and dev services.
Clone it, run it, open it — it discovers whatever is running on **your** machine.
No config files, no accounts, no `pip install`.

![status](https://img.shields.io/badge/deps-none%20(stdlib)-brightgreen) ![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-blue)

## What it does (all auto-detected)

- **Services** — every Docker container and every listening host port, auto-named
  and auto-grouped (AI / LLM, Database, Automation, Web / Dev, Infra, Docker · _project_).
  Start / stop / restart Docker containers and brew services right from the UI.
- **Topology map** — an interactive graph (zoom / pan / drag / click) built from the
  discovered services, grouped into labeled boxes, colored by live status.
- **AI agents** — detects installed AI CLIs/apps (Claude Code, Codex, OpenClaw,
  Ollama, Aider, LM Studio, Open Interpreter, Goose, llm, Hermes, Paperclip, Jan, …)
  and shows which are running. Click one for details.
- **Models** — your downloaded LLMs (Ollama + local `.gguf` scan), which are loaded
  in memory, with one-click unload.
- **Repos** — git repositories under your common dev folders, with branch and a
  clickable GitHub link; plus your `gh` accounts.
- **Live host metrics** — CPU / memory / GPU / load / swap / disk / network with a
  rolling history chart, top processes, and per-container stats. macOS **and** Linux.
- Light / dark theme, keyboard shortcuts (⌘K palette, `/` filter), always-live.

## Run it

```bash
python3 server.py
```

Then open **http://localhost:7777**. That's it. (macOS: you can also double-click
`start.command`.)

Requirements: **Python 3.8+** only. Everything else is optional — it lights up more
when you also have `docker`, `ollama`, `gh`, or `brew` installed.

## Configuration (optional, env vars)

| Variable | Default | Purpose |
|----------|---------|---------|
| `DASH_PORT` | `7777` | Port to serve on |
| `DASH_REPOS` | — | Extra repo roots to scan, colon-separated (e.g. `/opt/work:/srv/code`) |
| `DASH_HIDE` | — | Extra process-name substrings to treat as noise, comma-separated |

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
Control commands are derived from what it discovered — the browser can only name a
service id, never send a command. No arbitrary code execution.

## License

MIT (add your `LICENSE` file).
