@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run start_windows.bat once to set up Python and dependencies.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 reader_v2.py %*
if errorlevel 1 pause
