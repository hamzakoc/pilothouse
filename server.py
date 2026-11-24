#!/usr/bin/env python3
"""Pilothouse launcher: `python3 server.py` from a clone, nothing to install.

The program itself is the `pilothouse` package next to this file. See README.md.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pilothouse.app import main

if __name__ == "__main__":
    sys.exit(main())
