# Contributing

Thanks for looking. The most useful contributions are small: an AI tool that is not
recognised yet, a service that gets the wrong name, a report from Windows or a Linux
distribution nobody has tried.

## Ground rules

These keep the project what it is. A change that breaks one will not be merged.

1. **Standard library only.** No `pip install`, no build step, no bundler. The front
   end is one HTML file.
2. **Nothing about one machine.** No user names, home paths, host names or private
   ports in the code. Detection comes from general knowledge about software.
3. **Local only.** Binds to `127.0.0.1`. No telemetry. The only outbound request is
   the price-list download the user clicks.
4. **Never sudo, never another user's process.**
5. **The browser names things, the server builds commands.** A request carries an
   id, a pid or a name, never a command, a path or a flag.
6. **A probe may fail.** A missing tool gives an empty section, not a traceback.
7. **Nothing disappears.** Features stay visible by default; stopped things fold to
   the bottom, they are not hidden.

## Set up

```bash
git clone https://github.com/hamzakoc/pilothouse.git
```

```bash
cd pilothouse && python3 server.py --port 7778 --open
```

There is nothing to install. Edit, restart, reload.

## Check your change

```bash
python3 server.py --selftest
```

runs the parsers, guards and cost math on fixed inputs.

```bash
python3 scripts/smoke.py server.py
```

starts a fresh instance with its own data folder, lets it discover a test server and
stops that server through the API.

```bash
scripts/check.sh 7778
```

runs read-only checks against the instance on that port (macOS and Linux). It reads
the access key of the default data folder; for an instance with its own
`DASH_DATA` or `DASH_TOKEN`, export the same variable first.

CI runs the first two on macOS, Linux and Windows with Python 3.9 and 3.13.

If you touch the front end, also open the page with `--demo` in light and dark theme,
check the browser console is clean, and run the escaping test: paste
`scripts/hostile-data.js` into the console. It must report `fired: 0`, no sinks and
no bad handlers.

## Recipes

### Recognise a new AI tool

Add one line to `AGENT_DEFS` in `pilothouse/app.py`:

```python
("mytool", "My Tool", ["mytool"], ["~/.mytool"], r"(^|/)mytool( |$)", "what it is", None),
```

The fields are id, name, command names, data folders, a process pattern, a
description and an optional local port. The pattern must be anchored to a path or a
word boundary. Add a line to the agent-pattern checks in `selftest()` showing a
command line it must match and one it must not (a shell command that only mentions
the name is the classic false positive).

### Name a service correctly

Add a row to `KNOWN` in `pilothouse/app.py`: a pattern for the process or image
name, the display name, one line of what it is, and a group (`AI / LLM`, `Database`,
`Automation`, `Web / Dev`, `Infra`). More specific rows go above generic ones.

### Read usage from another tool

Write a `scan_<tool>_file(path, root, claim)` next to `scan_generic_file` that returns
the same structure, and add its folder to `usage_sources()`. Keep token counts,
model ids and timestamps; never keep message text. Add a fixed-input check to
`selftest()`.

### Support another service manager

Follow `systemd_unit` and the `systemd` branches in `discover_services` and
`do_action`: find which unit owns a pid, offer only the actions that work without
root, build the command on the server.

## Style

- Python: plain functions, short names, a docstring that says why. No classes
  unless the standard library needs one. No type-checking setup is required.
- JavaScript: no framework. Every value that came from the machine goes through
  `esc()` before it enters HTML text or an attribute, `jq()` before it enters an
  inline handler (`onclick="go(${jq(id)})"`), and `url()` before it becomes a link.
  `esc()` alone is not enough inside a handler.
- Python: anything that may contain a credential (a command line, a URL, a log line)
  goes through `redact()` before it is returned. Never run `git` in a discovered
  repository except through `git_run` after `git_trusted`.
- Words in the UI: plain, no jargon, no em dashes.
- One change per pull request. Say what you tested and on which system.

## Reporting a bug

Use the bug template and include `python3 server.py --doctor`. For a security
problem, do not open a public issue: see [SECURITY.md](SECURITY.md).

## License

By contributing you agree that your contribution is released under the MIT license
of this project.
