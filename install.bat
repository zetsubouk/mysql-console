@echo off
title MySQL Console - Install
cd /d %~dp0
echo ============================================
echo   MySQL Console - Install (Windows)
echo   1) Detect Python 3.10+
echo   2) Create project venv (.venv)
echo   3) Install dependencies from requirements.txt
echo ============================================
echo.

echo [1/3] Detect Python interpreter ...
set "PY="
py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3.10+ not found.
  echo         Download: https://www.python.org/downloads/windows/
  echo         Install with option "Add python.exe to PATH" checked.
  pause
  exit /b 1
)
for /f "tokens=*" %%v in ('%PY% -c "import sys;print(sys.version.split()[0])"') do set PYVER=%%v
echo   Found Python %PYVER% : %PY%

echo [2/3] Create project virtualenv (.venv) ...
if exist ".venv\Scripts\python.exe" (
  echo   .venv already exists, reuse it.
) else (
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create .venv. Check disk space and permissions.
    pause
    exit /b 1
  )
)

set VPY=.venv\Scripts\python.exe

echo [3/3] Install dependencies into .venv ...
set PYTHONUTF8=1
"%VPY%" -m pip install --upgrade pip >nul 2>&1
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Dependency install failed. Check network / proxy settings.
  pause
  exit /b 1
)

echo.
echo ============================================
echo   Install OK.
echo   Next:
echo     start.bat        - run the service
echo     Browser URL      - http://127.0.0.1:8090
echo     First open       - setup wizard will guide you
echo   Auto start on boot:
echo     schtasks /create /tn MySQLConsole /sc onstart /ru SYSTEM ^
       /tr "\"%~dp0.venv\Scripts\pythonw.exe\" \"%~dp0server.py\""
echo ============================================
pause
