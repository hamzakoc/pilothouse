#!/usr/bin/env python3
"""Append one usage record that Pilothouse will pick up within two minutes.

    python3 log_usage.py mytool my-model 1200 300 [session] [title]
"""
import datetime, json, os, sys

name, model, inp, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
folder = os.path.join(os.environ.get("DASH_DATA") or os.path.expanduser("~/.pilothouse"), "usage")
os.makedirs(folder, exist_ok=True)
rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "agent": name, "model": model,
       "input": inp, "output": out, "session": sys.argv[5] if len(sys.argv) > 5 else "default",
       "cwd": os.getcwd(), "title": sys.argv[6] if len(sys.argv) > 6 else ""}
with open(os.path.join(folder, name + ".jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(rec) + "\n")
