# Configuration

Pilothouse needs no configuration. Everything here is optional.

## Command line

```bash
python3 server.py [--port N] [--open] [--link] [--demo] [--doctor] [--selftest] [--version]
```

| Option | Does |
|---|---|
| `--port N` | Serve on another port (default 7777). |
| `--open` | Open the page in your browser once it is up (or the one already running), signed in with the access key. |
| `--link` | Print the address to open, including the access key, and exit. Use it once in each new browser. |
| `--demo` | Show made-up sample data and change nothing on your machine. Good for a first look and for screenshots. |
| `--doctor` | Print what Pilothouse can and cannot see on this machine, then exit. Paste it into a bug report. |
| `--selftest` | Run the built-in checks (parsers, guards, cost math) and exit. |
| `--version` | Print the version. |

`python3 -m pilothouse` and, after `pip install .`, the `pilothouse` command take
the same options.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DASH_PORT` | `7777` | Port to serve on. `--port` wins. |
| `DASH_DATA` | `~/.pilothouse` | Folder for history, budgets and your own additions. |
| `DASH_TOKEN` | a random key in the data folder | Your own access key instead of the generated one. See [SECURITY.md](../SECURITY.md#3-other-users-and-programs-on-the-computer-need-the-key). |
| `DASH_AUTH` | on | `off` removes the access key: anything on the machine can then read and control Pilothouse. |
| `DASH_REPOS` | not set | Extra folders to search for git repositories, separated by `:` (`;` on Windows). |
| `DASH_HIDE` | not set | Extra process names to leave out of Services, comma separated. |
| `DASH_ALERT_MEM` | `88` | Alert when memory used reaches this percent. |
| `DASH_ALERT_DISK` | `90` | Alert when a disk reaches this percent. |
| `DASH_ALERT_CPU` | `90` | Alert when CPU stays at this percent for 30 seconds. |

```bash
DASH_PORT=8899 DASH_REPOS=/opt/work:/srv/code DASH_ALERT_MEM=92 python3 server.py
```

## Your own additions

Three optional files. Each can sit next to `server.py` or in the data folder
(`~/.pilothouse`). They are in `.gitignore`, so a `git pull` never touches them.
Working examples are in [`examples/`](../examples).

### `agents.json`: teach it an AI tool it does not know

```json
[
  {
    "id": "mytool",
    "name": "My Tool",
    "bins": ["mytool"],
    "dirs": ["~/.mytool", "%APPDATA%/MyTool"],
    "process": "mytool( |$)|MyTool\\.app",
    "desc": "what it is, in a few words",
    "port": 9000
  }
]
```

| Field | Meaning |
|---|---|
| `id` | Short unique id. Using the id of a built-in tool replaces that entry. |
| `name` | Shown in the Agents tab. |
| `bins` | Command names looked up on `PATH` to decide "installed". |
| `dirs` | Its settings or data folders. `~`, `$VAR` and `%VAR%` are expanded, so one list serves every OS. |
| `process` | A regular expression matched against running command lines to decide "running". Anchor it to a path or a word boundary, or it will match any command that merely mentions the name. |
| `desc` | One line under the name. |
| `port` | Optional. The port of its local web UI, if it has one. |

### `pricing.json`: prices for models that have none

```json
{
  "my-model-prefix": [3.0, 15.0, 0.1]
}
```

The key is a prefix of the model id. The values are dollars per million input
tokens, dollars per million output tokens, and the cache-read price as a fraction of
the input price. Your entries win over the built-in list and the downloaded one.

### `usage/<name>.jsonl`: usage of a tool Pilothouse cannot read

Write one line per request into `~/.pilothouse/usage/<name>.jsonl`:

```json
{"ts": "2026-01-02T03:04:05Z", "agent": "My Tool", "model": "some-model", "input": 1200, "output": 300, "cache_read": 0, "cache_write": 0, "session": "abc", "cwd": "/path/to/project", "title": "what the session was about", "cost": 0.02}
```

Only `ts`, `model`, `input` and `output` are required. `cost` is optional and wins
over the price list. The file is picked up within two minutes.

## Budgets and prices

Budgets are set on the Usage tab (per day, per 7 days, per 5-hour window, per
session) and stored in the data folder. Claude models are priced out of the box at
Anthropic's public list prices. "Get prices for other vendors" downloads LiteLLM's
public price list once; it is off until you click it.

## Run it at login

Ready-made files are in [`examples/`](../examples): a launchd agent for macOS, a
systemd user unit for Linux and a Task Scheduler command for Windows. See
[INSTALL.md](INSTALL.md#keep-it-running).
