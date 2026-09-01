@echo off
title MySQL Console
REM ============================================================
REM  MySQL Console - Start (Windows)
REM  Runtime resolution via _resolve_python.bat, same policy as
REM  src/runtime_resolver.py (keep in sync):
REM    .venv -> runtime\python -> cached -> py -3 / python
REM  Policy: system Python is never used for pip installs.
REM  If dependencies are missing, run install.bat to repair.
REM  NOTE: pure ASCII + CRLF only. Do not add Chinese text here.
REM ============================================================
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
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
echo ============================================
echo   MySQL Console - Database Web Console
echo   URL: http://127.0.0.1:8090
echo ============================================
echo.

echo [1/4] Check and kill old instance on port 8090 ...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8090" ^| findstr LISTENING') do (
  echo   Kill old instance PID %%p
  taskkill /PID %%p /F >nul 2>&1
)
ping -n 2 127.0.0.1 >nul

echo [2/4] Resolve Python runtime ...
call "%~dp0_resolve_python.bat" "%ROOT%"
if not defined PYEXE if not defined PYCMD (
  echo [ERROR] No Python runtime found. Run install.bat first.
  pause
  exit /b 1
)
if defined PYEXE (
  echo   Using interpreter: %PYEXE%
) else (
  echo   Using interpreter: %PYCMD%
)

echo [3/4] Check dependencies ...
if defined PYEXE (
  "%PYEXE%" -c "import pymysql, cryptography" >nul 2>&1
) else (
  %PYCMD% -c "import pymysql, cryptography" >nul 2>&1
)
if errorlevel 1 (
  echo [ERROR] Dependencies missing. System Python will NOT be touched.
  echo         Run install.bat first to set up or repair the runtime.
  pause
  exit /b 1
)

echo [4/4] Starting server ...
set PYTHONUTF8=1
if defined PYEXE (
  "%PYEXE%" "%ROOT%\src\server.py"
) else (
  %PYCMD% "%ROOT%\src\server.py"
)
echo.
echo Server exited.
pause
