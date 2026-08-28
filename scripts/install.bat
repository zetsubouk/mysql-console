@echo off
title MySQL Console - Install
REM 定位部署根:本脚本可在 scripts/(开发仓库) 或 发布包根 下运行。
if exist "%~dp0src\server.py" (
  set "ROOT=%~dp0"
) else if exist "%~dp0..\src\server.py" (
  set "ROOT=%~dp0.."
) else (
  echo [ERROR] Can't locate src\server.py. Please unzip the package first.
  pause
  exit /b 1
)
cd /d "%ROOT%"
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
if exist "%ROOT%\.venv\Scripts\python.exe" (
  echo   .venv already exists, reuse it.
) else (
  %PY% -m venv "%ROOT%\.venv"
  if errorlevel 1 (
    echo [ERROR] Failed to create .venv. Check disk space and permissions.
    pause
    exit /b 1
  )
)
set VPY="%ROOT%\.venv\Scripts\python.exe"

echo [3/3] Install dependencies into .venv ...
set PYTHONUTF8=1
%VPY% -m pip install --upgrade pip >nul 2>&1
%VPY% -m pip install -r "%ROOT%\requirements.txt"
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
       /tr "\"%ROOT%\.venv\Scripts\pythonw.exe\" \"%ROOT%\src\server.py\""
echo ============================================
pause