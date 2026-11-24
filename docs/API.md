# HTTP API

The page uses this API and you can too. It listens on `127.0.0.1` only.

```bash
curl -s http://127.0.0.1:7777/api/health
```

```bash
TOKEN=$(python3 server.py --link | sed 's/.*#t=//')
```

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:7777/api/status
```

Rules for every request:

- The `Host` header must be `localhost:<port>`, `127.0.0.1:<port>` or `[::1]:<port>`.
- `POST` needs the header `X-Dash: 1`, a JSON body, and, if an `Origin` header is
  sent, a local one. A request without them gets `403`.
- `POST /api/pair` (the "Sign in this browser" button) needs no key and returns none: it
  makes Pilothouse open its signed-in address in the default browser of the user it runs as.
- Every other request except `GET /api/health` needs the access key as a header:
  `Authorization: Bearer <key>`. The key is in `~/.pilothouse/token` (or your own
  `DASH_TOKEN`); `python3 server.py --link` prints the address that contains it.
  Without it the answer is `401`. `DASH_AUTH=off` turns the key off.
- A request a browser marks as caused by another site (`Sec-Fetch-Site`) gets `403`.
- Answers are JSON. A `POST` answers `{"ok": true|false, "msg": "..."}`.

## Read

| Request | Returns |
|---|---|
| `GET /api/health` | `{ok, version, platform, demo, ready, uptime, services, running, down, stopped, alerts}`. `ready` is false for the first seconds after start, while the first look at the machine runs. Cheap, and the only request that needs no key; for scripts and uptime checks. |
| `GET /metrics` | The same facts in Prometheus text format (see below). |
| `GET /api/status` | `services` (every service with state, memory, CPU, ports, allowed `actions`), `edges` (`[from, to, why, age]`), `alerts`, `rules`, `events`. |
| `GET /api/metrics` | Host CPU, memory breakdown, swap, GPU, network, disks, top processes, memory per app, per-container stats, models in memory, short history. |
| `GET /api/inventory` | `agents`, `models`, `docker` (images, volumes, containers, totals), `github` (accounts, repos), `tools`. Cached; add `?force` to rescan. |
| `GET /api/inspect?id=<service id>` | Detail for one service: what manages it, image, restart policy, limits, mounts, networks, child processes, open files, environment (secrets hidden). |
| `GET /api/logs?id=<service id>&n=200` | Last `n` log lines of a service. |
| `GET /api/history?range=1h\|6h\|24h\|48h[&id=<service id>][&app=<app name>]` | Persisted history for the host, one service or one app. |
| `GET /api/since?ts=<unix seconds>` | What happened since then: events, counts, peak memory, CPU and swap. |
| `GET /api/usage?range=today\|7d\|30d\|90d\|all` | Tokens and cost: `totals`, `days`, `models`, `tools`, `projects`, `sessions`, `prices`. |
| `GET /api/usage/now` | The open 5-hour window, burn rate, this week against last, budgets, live sessions. |
| `GET /api/usage/session?id=<session id>` | One session by model, by hour and by sub-agent. |
| `GET /api/modelinfo?name=<model>` | Architecture, parameters, quantization and capabilities of an Ollama model. |

## Change

Every one of these does something to your machine. The page asks before the
destructive ones; the API does not.

| Request | Body | Does |
|---|---|---|
| `POST /api/action` | `{"id": "<service id>", "action": "start\|stop\|restart\|pause\|unpause\|kill\|remove\|unload"}` | One service. Only the actions listed in that service's `actions` are accepted. |
| `POST /api/group` | `{"group": "<compose project>", "action": "start\|stop\|restart"}` | Every container of a compose project. |
| `POST /api/proc` | `{"pid": 1234, "force": false}` | Stops one process. Yours only; never pid 1, never Pilothouse. |
| `POST /api/app` | `{"name": "<app>"}` | Quits every process of an app (yours only). |
| `POST /api/model` | `{"name": "<model>", "action": "load\|stop\|delete"}` | An Ollama model. |
| `POST /api/docker` | `{"action": "prune-images\|prune-unused\|prune-cache\|prune-containers\|rmi\|rmvol\|pull", "target": "<name>"}` | Docker housekeeping. Bulk prune never touches volumes. |
| `POST /api/agent` | `{"id": "<agent id>", "action": "stop\|app\|launch"}` | Stop an agent's processes, open its app, or start it in a terminal. |
| `POST /api/open` | `{"kind": "repo\|agent\|model\|usagedir\|agentcfg\|cwd\|log", "key": "<id>", "how": "folder\|editor\|terminal"}` | Opens a folder Pilothouse discovered. An arbitrary path is refused. |
| `POST /api/budget` | `{"day": 50, "week": 200, "block": 20, "session": 10}` | Budgets in dollars; `0` clears one. |
| `POST /api/prices` | `{"action": "update"}` | Downloads the public LiteLLM price list. The only request that goes to the internet. |

Example:

```bash
curl -s -X POST http://127.0.0.1:7777/api/action \
  -H 'X-Dash: 1' -H 'Content-Type: application/json' \
  -d '{"id": "dk:my-container", "action": "restart"}'
```

Service ids come from `/api/status`: `dk:<container name>` for containers,
`host:<port>` for a process listening on a port, `ld:<label>` for a launchd job and
`brew:<name>` for a Homebrew service that is not running.

## Prometheus

`GET /metrics` answers in the text exposition format, so Prometheus, Grafana Agent,
Netdata or a plain `curl` in cron can read it:

```
pilothouse_up 1
pilothouse_cpu_percent 23.4
pilothouse_memory_used_percent 71.2
pilothouse_memory_used_bytes 48640504627
pilothouse_swap_bytes 1503238553
pilothouse_gpu_percent 45
pilothouse_disk_used_percent{mount="/"} 61.0
pilothouse_services{state="running"} 13
pilothouse_services{state="down"} 1
pilothouse_services{state="stopped"} 2
pilothouse_alerts 1
pilothouse_service_up{id="dk:n8n",name="n8n",group="Docker · automation"} 1
pilothouse_service_memory_bytes{id="dk:n8n",name="n8n",group="Docker · automation"} 406847488
pilothouse_service_cpu_percent{id="dk:n8n",name="n8n",group="Docker · automation"} 0.9
pilothouse_usage_cost_usd_today 27.5
```

Scrape config:

```yaml
scrape_configs:
  - job_name: pilothouse
    authorization:
      credentials_file: /home/you/.pilothouse/token
    static_configs:
      - targets: ["127.0.0.1:7777"]
```

## Stability

The API is version 0.x. `/api/health` and `/metrics` are meant to stay stable. The
other shapes follow what the page needs and can change between releases; the
[changelog](../CHANGELOG.md) says when they do.
