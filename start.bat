@echo off
title MySQL Console
cd /d %~dp0
echo ============================================
echo   MySQL Console - Database Web Console
echo   URL: http://127.0.0.1:8090
echo ============================================
echo.

echo [1/3] Check and kill old instance on port 8090 ...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8090" ^| findstr LISTENING') do (
  echo   Kill old instance PID %%p
  taskkill /PID %%p /F >nul 2>&1
)
ping -n 2 127.0.0.1 >nul

echo [2/3] Detect Python interpreter (3.10+) ...
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

echo [3/3] Check dependencies ...
%PY% -c "import pymysql, cryptography" >nul 2>&1
if errorlevel 1 (
  echo   Missing deps, installing requirements.txt ...
  set PYTHONUTF8=1
  %PY% -m pip install -r requirements.txt
)

%PY% server.py
echo.
echo Server exited.
pause
