# Changelog

Notable changes. Versions follow semantic versioning; before 1.0 the HTTP API other
than `/api/health` and `/metrics` may change between minor versions.

## 0.2.0 - unreleased

### Added
- Proper package layout: `pilothouse/` (program), `pilothouse/web/` (page), `docs/`,
  `examples/`, `scripts/`. `python3 server.py` still works from a clone;
  `python3 -m pilothouse` and `pip install .` are new.
- Command line options: `--port`, `--open`, `--demo`, `--doctor`, `--version`.
- Demo mode: made-up sample data, nothing on the machine is read or changed.
- Doctor: prints what can and cannot be detected on this machine.
- `GET /api/health` and Prometheus `GET /metrics`.
- "Sign in this browser" button on the key screen: one click opens the signed-in
  page in your default browser, no key to copy.
- Access key, on by default: a random key in the data folder (or `DASH_TOKEN`) is
  required on every read and action. `--open` signs the browser in; `--link` prints
  the address again; `DASH_AUTH=off` turns it off.
- Security headers on every response (CSP, no framing, no sniffing), integrity
  hashes on the two CDN scripts.
- `scripts/hostile-data.js`, an escaping test for the page.

### Security
An independent review before the first public release found one critical, four
high, eight medium and eleven low issues. All are fixed in this version except the
ones listed under "What it does not protect against" in SECURITY.md. The critical
one: git commands were run inside every discovered repository, which executes that
repository's own config. Details and the full table: SECURITY.md.
- Documentation with diagrams: architecture, security model, configuration, API,
  install, troubleshooting. Contributing guide, issue and pull request templates,
  examples for login items and user additions.
- A friendly message when the port is already in use.

### Changed
- Renamed from "Local AI Control" to Pilothouse. The data folder moved from
  `~/.local-ai-control` to `~/.pilothouse`; an existing folder is renamed once.
- An entry in `agents.json` with a built-in id now replaces the built-in entry.
- Repositories whose own git config could start other programs show "status not
  read"; branch, remote and last commit are still shown, read from plain files.
- Unknown paths answer 404 instead of the page. A hung Docker daemon no longer
  stalls every refresh.

## 0.1.0

First version: services and control, memory by app, map of what talks to what,
Docker housekeeping, agents, models, repos, usage and cost with 5-hour windows,
live sessions and budgets, history in sqlite, macOS, Linux and Windows.
