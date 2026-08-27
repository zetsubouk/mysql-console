@echo off
title MySQL Console - Init (Reset to Factory)
cd /d %~dp0
echo ============================================
echo   MySQL Console - One-click Initialize (Reset)
echo   This will DELETE all configs, system DB & backups.
echo ============================================
echo.

echo [1/4] Check and kill old instance on port 8090 ...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8090" ^| findstr LISTENING') do (
  echo   Kill old instance PID %%p
  taskkill /PID %%p /F >nul 2>&1
)
ping -n 2 127.0.0.1 >nul

echo [2/4] Detect Python interpreter (3.10+) ...
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
  py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3.10+ not found. Please install Python first.
  pause
  exit /b 1
)
echo   Using interpreter: %PY%

echo [3/4] Check dependencies ...
%PY% -c "import pymysql, cryptography" >nul 2>&1
if errorlevel 1 (
  echo   Missing deps, installing requirements.txt ...
  %PY% -m pip install -r requirements.txt
)

echo [4/4] Detect current environment ...
echo.
%PY% cli_init.py --check
echo.

echo ============================================
echo   WARNING: The above data will be PERMANENTLY destroyed.
echo ============================================
set /p CONFIRM="Type 'y' to confirm initialize, or any key to cancel: "
if /i not "%CONFIRM%"=="y" (
  echo.
  echo Cancelled. No changes made.
  pause
  exit /b 1
)

echo.
%PY% cli_init.py --do --force

echo.
echo Done. To re-run: start.bat    (opens fresh setup wizard at http://127.0.0.1:8090)
pause