@echo off
title MySQL Console
REM 定位部署根:本脚本可在 scripts/(开发仓库) 或 发布包根(install 复制到根) 下运行。
if exist "%~dp0src\server.py" (
  set "ROOT=%~dp0"
) else if exist "%~dp0..\src\server.py" (
  set "ROOT=%~dp0.."
) else (
  echo [ERROR] Can't locate src\server.py. Please run install first.
  pause
  exit /b 1
)
cd /d "%ROOT%"
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
if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
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
  %PY% -m pip install -r "%ROOT%\requirements.txt"
)

%PY% "%ROOT%\src\server.py"
echo.
echo Server exited.
pause