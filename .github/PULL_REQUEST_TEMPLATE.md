**What this changes**


**Why**


**Tested**

- [ ] `python3 server.py --selftest`
- [ ] `python3 scripts/smoke.py server.py`
- [ ] Opened the page (`--demo` is fine) in light and dark theme, console clean
- System(s) tested on:

**Checklist**

- [ ] Standard library only, no build step
- [ ] Nothing specific to one machine (no user names, home paths, private ports)
- [ ] Values from the machine are escaped before they enter HTML
- [ ] The server builds every command; the request only names things
