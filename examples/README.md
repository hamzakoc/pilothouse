# Examples

Copy what you need. None of these files is required.

| File | Copy to | For |
|---|---|---|
| `agents.json` | next to `server.py`, or `~/.pilothouse/` | Teaching Pilothouse an AI tool it does not know |
| `pricing.json` | next to `server.py`, or `~/.pilothouse/` | Prices for models that have none |
| `usage/mytool.jsonl` | `~/.pilothouse/usage/` | Usage records of a tool Pilothouse cannot read |
| `usage/log_usage.py` | anywhere | A ten-line helper that appends such a record |
| `launchd/com.pilothouse.plist` | `~/Library/LaunchAgents/` | Start at login on macOS |
| `systemd/pilothouse.service` | `~/.config/systemd/user/` | Start at login on Linux |
| `windows/install-task.cmd` | run once | Start at login on Windows |
| `prometheus.yml` | your Prometheus config | Scraping `/metrics` |

The formats are explained in [docs/CONFIGURATION.md](../docs/CONFIGURATION.md) and
the login items in [docs/INSTALL.md](../docs/INSTALL.md#keep-it-running).
