@echo off
rem Windows: double-click to start Pilothouse and open http://localhost:7777
cd /d "%~dp0"
where py >nul 2>nul && (py -3 server.py --open %* & goto :eof)
where python >nul 2>nul && (python server.py --open %* & goto :eof)
echo Python 3.8 or newer is required. Install it from https://www.python.org/downloads/ and run this again.
pause
