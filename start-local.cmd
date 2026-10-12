@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First-time setup is required. See docs\LOCAL_AI.md
  echo py -3.12 -m venv .venv
  echo .venv\Scripts\python -m pip install ".[local-ai]"
  echo Models, FFmpeg and Japanese fonts must be prepared separately.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m clipbeta serve --workspace clipbeta-work
if errorlevel 1 pause
