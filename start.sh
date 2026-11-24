#!/bin/sh
# Linux / macOS: start Pilothouse and open http://localhost:7777
cd "$(dirname "$0")" || exit 1
command -v python3 >/dev/null 2>&1 || { echo "Python 3.8 or newer is required."; exit 1; }
exec python3 server.py --open "$@"
