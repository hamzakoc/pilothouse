@echo off
rem Start Pilothouse when you log in (Windows), without a console window.
rem 1. Replace the path below.   2. Run this file once.
rem Remove it again with:  schtasks /Delete /TN Pilothouse /F
schtasks /Create /TN Pilothouse /SC ONLOGON /RL LIMITED /F /TR "pythonw.exe C:\PATH\TO\pilothouse\server.py"
schtasks /Run /TN Pilothouse
