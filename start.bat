@echo off
rem Windows: double-click to start the dashboard, then open http://localhost:7777
cd /d "%~dp0"
where py >nul 2>nul && (start "" http://localhost:7777 & py -3 server.py & goto :eof)
where python >nul 2>nul && (start "" http://localhost:7777 & python server.py & goto :eof)
echo Python 3.8 or newer is required. Install it from https://www.python.org/downloads/ and run this again.
pause
