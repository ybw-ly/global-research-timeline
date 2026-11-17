@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
  )
)
if not defined PYTHON_CMD (
  echo.
  echo [ERROR] Python 3.10 or newer was not found.
  echo.
  echo Install Python from:
  echo   https://www.python.org/downloads/
  echo During setup, tick "Add python.exe to PATH".
  echo.
  echo Or download the no-install Windows bundle from:
  echo   https://github.com/ybw-ly/global-research-timeline/releases
  echo.
  pause
  exit /b 1
)

%PYTHON_CMD% server\app.py
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [ERROR] Server exited with code %CODE%.
  echo Copy the message above when asking for help.
  echo.
  pause
)
endlocal
