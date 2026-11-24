#!/bin/sh
# Linux / macOS: start the dashboard, then open http://localhost:7777
cd "$(dirname "$0")" || exit 1
command -v python3 >/dev/null 2>&1 || { echo "Python 3.8 or newer is required."; exit 1; }
(sleep 1; (command -v xdg-open >/dev/null 2>&1 && xdg-open http://localhost:7777) || (command -v open >/dev/null 2>&1 && open http://localhost:7777)) >/dev/null 2>&1 &
exec python3 server.py
