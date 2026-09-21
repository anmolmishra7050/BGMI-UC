@echo off
rem Start the UC store. Extra arguments are forwarded to server.py,
rem e.g.   run.bat --port 9000 --open
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on PATH. Install Python 3.10+ first.
  pause
  exit /b 1
)
python server.py %*
pause
