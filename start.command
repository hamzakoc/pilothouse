#!/bin/bash
# Double-click to launch the Local Services dashboard.
# Starts the tiny status server (if not already up) and opens it in your browser.
cd "$(dirname "$0")" || exit 1

if curl -s -o /dev/null http://127.0.0.1:7777/api/status; then
  echo "Dashboard already running."
else
  echo "Starting dashboard server…"
  (sleep 1; open "http://127.0.0.1:7777") &
  exec /usr/bin/env python3 server.py
fi

open "http://127.0.0.1:7777"
