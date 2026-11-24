#!/bin/bash
# macOS: double-click to start Pilothouse and open it in your browser (if it already runs, this just opens it).
cd "$(dirname "$0")" || exit 1
command -v python3 >/dev/null 2>&1 || { echo "Python 3.8 or newer is required: xcode-select --install, or https://www.python.org/downloads/"; read -r _; exit 1; }
exec python3 server.py --open
