@echo off
REM ============================================================
REM  Shared Python runtime resolution (Windows).
REM  Usage: call _resolve_python.bat ^<ROOT^>
REM    ROOT = deployment root (with or without trailing backslash)
REM  Sets:
REM    PYEXE  - absolute interpreter path, preferred (.venv / runtime)
REM    PYCMD  - command form fallback ("py -3" / "python")
REM  Order (same policy as src/runtime_resolver.py, keep in sync):
REM    .venv -> runtime\python -> runtime\resolved_python.txt
REM          -> py -3 -> python   (3.10+ verified by real execution)
REM  NOTE: pure ASCII + CRLF only. Do not add Chinese text here.
REM ============================================================
set "PYEXE="
set "PYCMD="
set "MCROOT=%~1"
if defined MCROOT if exist "%MCROOT%\.venv\Scripts\python.exe" set "PYEXE=%MCROOT%\.venv\Scripts\python.exe"
if not defined PYEXE if exist "%MCROOT%\runtime\python\python.exe" set "PYEXE=%MCROOT%\runtime\python\python.exe"
if not defined PYEXE if exist "%MCROOT%\runtime\resolved_python.txt" (
  for /f "usebackq delims=" %%p in ("%MCROOT%\runtime\resolved_python.txt") do if exist "%%p" set "PYEXE=%%p"
)
if not defined PYEXE (
  py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYCMD=py -3"
)
if not defined PYEXE if not defined PYCMD (
  python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYCMD=python"
)
exit /b 0
