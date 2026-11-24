# Troubleshooting

First, always:

```bash
python3 server.py --doctor
```

It prints what Pilothouse found on this machine and what it could not see. Paste its
output into a bug report; it contains tool names and counts, not your data.

## It does not start

| You see | Cause | Do |
|---|---|---|
| `Cannot listen on 127.0.0.1:7777` | Something already uses the port, usually a Pilothouse you started earlier. | Open http://localhost:7777, or start on another port: `python3 server.py --port 7778`. |
| `python3: command not found` | Python is not installed or not on `PATH`. | See [INSTALL.md](INSTALL.md#python-is-missing). On Windows try `py server.py`. |
| `SyntaxError` on start | Python older than 3.8. | `python3 --version`, then install a newer one. |
| `ModuleNotFoundError: pilothouse` | `server.py` was moved away from the `pilothouse` folder. | Keep them together, or `pip install .` and use the `pilothouse` command. |

## The page

| You see | Cause | Do |
|---|---|---|
| `forbidden` in the browser | The address is not `localhost` or `127.0.0.1` (for example a LAN IP or a custom hostname). This is the DNS-rebinding guard. | Open `http://localhost:<port>`. For another machine, use an SSH tunnel: `ssh -L 7777:127.0.0.1:7777 you@host`. |
| "This browser needs the access key" | Every browser has to be given the key once. It also appears after the key changed. | Press **Sign in this browser** on that screen. If it opens a different browser than the one you are in, run `python3 server.py --link` in the Pilothouse folder and paste the address into the box. |
| A script gets `401` | The API needs the key as a header. | `curl -H "Authorization: Bearer $(cat ~/.pilothouse/token)" ...`. Only `/api/health` is open. |
| The map is empty, the list works | The map library comes from a CDN and the machine is offline or blocks it. | Use the list view; everything else works offline. |
| Buttons answer `forbidden` | A browser extension or proxy strips the `X-Dash` header or rewrites `Origin`. | Try a private window without extensions. |
| Numbers stop moving | The process was stopped. | Look at its terminal, start it again. See "Keep it running" in [INSTALL.md](INSTALL.md#keep-it-running). |

## Something is missing

| Missing | Why | Do |
|---|---|---|
| Docker containers | The `docker` command is not on `PATH`, the daemon is not running, or your user cannot talk to it. | `docker ps` must work in the same terminal. On Linux add yourself to the `docker` group. |
| A service that is running | It does not listen on a TCP port, it belongs to another user (Linux cannot attribute those without root), or its process name is in the noise list. | Check `--doctor`. Processes still show under Overview, "Memory by app". |
| A link on the map | Links need evidence: a live connection, a container's configuration naming the other one, or compose `depends_on`. | See [ARCHITECTURE.md](ARCHITECTURE.md#how-the-map-decides-what-talks-to-what). |
| An AI tool | It is not in the built-in list. | Add it to `agents.json`: [CONFIGURATION.md](CONFIGURATION.md#agentsjson-teach-it-an-ai-tool-it-does-not-know). |
| A model | Only Ollama's store, `~/models`, the LM Studio and Hugging Face caches and large model files under your home folder are found (a full scan runs every 15 minutes). | Wait for the next scan, or put the file under `~/models`. |
| A repository | It is outside the common dev folders. | `DASH_REPOS=/path/one:/path/two python3 server.py`. |
| A repository says "status not read" | Its own git settings could start other programs (a filter, an fsmonitor, an include), so git is not run in it. Branch, remote and last commit are still shown. | Nothing to fix. See [SECURITY.md](../SECURITY.md#6-repositories-you-did-not-write). |
| Usage and cost for a tool | Only tools that keep readable records are counted (Claude Code, Hermes; Codex unverified). | Write records to `~/.pilothouse/usage/<name>.jsonl`: [CONFIGURATION.md](CONFIGURATION.md#usagenamejsonl-usage-of-a-tool-pilothouse-cannot-read). |
| A cost (tokens show, dollars do not) | The model has no price. | Add it to `pricing.json`, or click "Get prices for other vendors". |
| History for last night | Pilothouse was not running. | Start it at login. |

## Numbers look wrong

| Question | Answer |
|---|---|
| Memory is higher than `ps` or `top` shows | On macOS Pilothouse shows footprint (resident + compressed + swapped), the same number as Activity Monitor. A model the OS compressed still costs what it shows. |
| Memory per app adds up to more than the machine has | Memory shared between processes is counted in each one, as in most process monitors. |
| Cost is higher than my bill | Cost is tokens at list price. On a subscription plan it is what the usage would have cost, not what you pay. |
| Cost differs from the agent's own counter | A resumed session restarts some agents' counters; Pilothouse counts every message once across all files. |
| The 5-hour window does not match the vendor's reset | The window is worked out from your own activity, so its edges are approximate. |
| Context gauge seems off | It assumes the model's standard window (200K or 1M). |

## A button did nothing

| Case | Why |
|---|---|
| "Stop" on a service and it comes back | Something restarts it: launchd `KeepAlive`, systemd `Restart=`, Docker `restart: always`. Use "Stop and keep stopped" where offered, or change the policy. |
| Stop is not offered | The process belongs to another user or to root. Pilothouse never uses `sudo`. |
| Restart is not offered | Nothing manages the process, so Pilothouse does not know how to start it again. It can only stop it. |
| Windows: no restart for services | Windows services are not wired yet. |

## Reset

Stop Pilothouse and delete `~/.pilothouse`. That removes history, budgets and
remembered links. Your agents' own logs are never touched.

## Still stuck

Open an issue with the output of `python3 server.py --doctor`, your operating system
and Python version, and what you expected to see.
