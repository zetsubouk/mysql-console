@echo off
title MySQL Console - Install
REM ============================================================
REM  MySQL Console - Install (Windows)
REM  Runtime resolution, 3 tiers, same policy as
REM  src/runtime_resolver.py (keep in sync):
REM    1) bundled private runtime   runtime\python\python.exe
REM    2) system Python 3.10+       -> isolated .venv  (system untouched)
REM    3) download embedded Python  -> runtime\python   (mirror fallback)
REM  Your system Python is NEVER modified, upgraded or uninstalled.
REM  Usage:
REM    install.bat
REM    install.bat --yes                          skip all confirm prompts
REM    install.bat --runtime-zip path\to\zip      use local embedded zip
REM  NOTE: pure ASCII + CRLF only. Do not add Chinese text here.
REM ============================================================
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

set "ASSUME_YES="
set "ZIPSRC="
:parse_args
if "%~1"=="--yes" (set "ASSUME_YES=1" & shift & goto :parse_args)
if "%~1"=="--runtime-zip" (set "ZIPSRC=%~2" & shift & shift & goto :parse_args)
if not "%~1"=="" (shift & goto :parse_args)

echo ============================================
echo   MySQL Console - Install (Windows)
echo   Runtime: bundled / system venv / private download
echo   System Python is NEVER modified.
echo ============================================
echo.

echo [1/4] Resolve Python runtime ...
call "%~dp0_resolve_python.bat" "%ROOT%"
if defined PYEXE (
  echo   Existing isolated runtime found: %PYEXE%
  if "%PYEXE%"=="%ROOT%\runtime\python\python.exe" (set "RUNKIND=private") else set "RUNKIND=venv"
  goto :deps
)
if not defined PYCMD goto :no_good_py
echo   Found system Python 3.10+ via: %PYCMD%
echo.
echo   Option A: create isolated venv .venv from it. Recommended, no system change.
echo   Option B: download private runtime into runtime\. No system Python needed.
echo.
if defined ASSUME_YES (
  echo   --yes: using Option A, isolated venv.
  goto :make_venv
)
set "CHOICE="
set /p CHOICE=  Choose [A/B, default A]: 
if /i not "%CHOICE%"=="B" goto :make_venv
call :confirm_download
if errorlevel 1 goto :manual_hint
goto :download

:no_good_py
echo   No usable Python 3.10+ found on this machine.
call :detect_old_python
if defined OLDVER (
  echo   Detected existing Python %OLDVER% , but 3.10+ is required.
  echo   It will NOT be touched, upgraded or uninstalled.
) else (
  echo   No other Python 3.x detected.
)
echo   A private standalone runtime can be installed into runtime\ ,
echo   living only inside this folder. Delete runtime\ to remove it.
echo.
call :confirm_download
if errorlevel 1 goto :manual_hint
goto :download

:confirm_download
REM returns errorlevel 1 = declined
if defined ASSUME_YES (
  echo   --yes: download confirmed automatically.
  exit /b 0
)
set "CONFIRM="
set /p CONFIRM=  Download private runtime now? [y/N]: 
if /i "%CONFIRM%"=="Y" exit /b 0
exit /b 1

:detect_old_python
set "OLDVER="
for /f "usebackq tokens=*" %%v in (`py -3 -c "print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2^>nul`) do set "OLDVER=%%v"
if not defined OLDVER for /f "usebackq tokens=*" %%v in (`python -c "print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2^>nul`) do set "OLDVER=%%v"
exit /b 0

:manual_hint
echo.
echo   Install Python 3.10+ manually, then re-run install.bat:
echo     https://www.python.org/downloads/windows/
echo   Tip: check "Add python.exe to PATH" during setup.
pause
exit /b 1

:make_venv
echo.
echo [2/4] Create project virtualenv .venv ...
if exist "%ROOT%\.venv\Scripts\python.exe" (
  echo   .venv already exists, reuse it.
) else (
  %PYCMD% -m venv "%ROOT%\.venv"
  if errorlevel 1 (
    echo [ERROR] Failed to create .venv. Check disk space and permissions.
    pause
    exit /b 1
  )
)
set "PYEXE=%ROOT%\.venv\Scripts\python.exe"
set "RUNKIND=venv"
goto :deps

:download
set "EMBEDVER=3.12.10"
set "EMBEDFILE=python-%EMBEDVER%-embed-amd64.zip"
echo.
echo [2/4] Get private runtime: %EMBEDFILE% ...
if not exist "%ROOT%\runtime" mkdir "%ROOT%\runtime"
if defined ZIPSRC (
  if not exist "%ZIPSRC%" (
    echo [ERROR] Local zip not found: %ZIPSRC%
    pause
    exit /b 1
  )
  echo   Using local zip: %ZIPSRC%
  copy /y "%ZIPSRC%" "%ROOT%\runtime\_embed.zip" >nul
  goto :extract
)
call :geturl "https://www.python.org/ftp/python/%EMBEDVER%/%EMBEDFILE%" "%ROOT%\runtime\_embed.zip"
if errorlevel 1 (
  echo   python.org failed, trying mirrors.huaweicloud.com ...
  call :geturl "https://mirrors.huaweicloud.com/python/%EMBEDVER%/%EMBEDFILE%" "%ROOT%\runtime\_embed.zip"
)
if errorlevel 1 (
  echo   huaweicloud failed, trying registry.npmmirror.com ...
  call :geturl "https://registry.npmmirror.com/-/binary/python/%EMBEDVER%/%EMBEDFILE%" "%ROOT%\runtime\_embed.zip"
)
if errorlevel 1 (
  echo [ERROR] Download failed from all sources. Check network, or:
  echo   1. Download manually: https://www.python.org/ftp/python/%EMBEDVER%/%EMBEDFILE%
  echo   2. Re-run: install.bat --runtime-zip path\to\%EMBEDFILE%
  del /q "%ROOT%\runtime\_embed.zip" 2>nul
  pause
  exit /b 1
)
echo   Download OK.
:extract
echo.
echo [3/4] Extract to runtime\python ...
if exist "%ROOT%\runtime\python" rmdir /s /q "%ROOT%\runtime\python"
powershell -NoProfile -Command "Expand-Archive -Force -LiteralPath '%ROOT%\runtime\_embed.zip' -DestinationPath '%ROOT%\runtime\python'"
if errorlevel 1 (
  echo [ERROR] Extract failed. Delete runtime\_embed.zip and retry.
  pause
  exit /b 1
)
powershell -NoProfile -Command "$p = Get-ChildItem -LiteralPath '%ROOT%\runtime\python' -Filter 'python3*._pth' | Select-Object -First 1; if ($p) { (Get-Content -LiteralPath $p.FullName) -replace '^#import site','import site' | Set-Content -LiteralPath $p.FullName -Encoding ASCII }"
set "PYEXE=%ROOT%\runtime\python\python.exe"
if not exist "%PYEXE%" (
  echo [ERROR] runtime\python\python.exe missing after extract. Package broken?
  pause
  exit /b 1
)
set "RUNKIND=private"
del /q "%ROOT%\runtime\_embed.zip" 2>nul
echo   Private runtime ready: %PYEXE%

:deps
echo.
echo [3/4] Install dependencies ...
set PYTHONUTF8=1
"%PYEXE%" -c "import pymysql, cryptography" >nul 2>&1
if not errorlevel 1 (
  echo   Dependencies already present, skip.
  goto :done
)
if "%RUNKIND%"=="private" goto :deps_private
REM venv: offline wheels first, then online PyPI
if exist "%ROOT%\wheels" (
  echo   Installing from local wheels\ , offline mode ...
  "%PYEXE%" -m pip install --no-index --find-links "%ROOT%\wheels" -r "%ROOT%\requirements.txt"
  if not errorlevel 1 goto :done
)
echo   Installing from PyPI ...
"%PYEXE%" -m pip install --upgrade pip >nul 2>&1
"%PYEXE%" -m pip install -r "%ROOT%\requirements.txt"
if errorlevel 1 (
  echo   PyPI failed, retrying via Tsinghua mirror ...
  "%PYEXE%" -m pip install -r "%ROOT%\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
)
if errorlevel 1 goto :depfail
goto :done

:deps_private
if not exist "%ROOT%\wheels" (
  echo [ERROR] Private runtime needs dependencies but wheels\ folder is missing.
  echo         Use the full package, or a package that ships wheels\ .
  goto :depfail
)
set "PIPWHL="
for %%f in ("%ROOT%\wheels\pip-*.whl") do set "PIPWHL=%%~ff"
if not defined PIPWHL (
  echo [ERROR] pip wheel not found in wheels\ .
  goto :depfail
)
echo   Installing from local wheels\ into private runtime ...
"%PYEXE%" "%PIPWHL%\pip" install --no-index --find-links "%ROOT%\wheels" -r "%ROOT%\requirements.txt" --target "%ROOT%\runtime\python\Lib\site-packages" --upgrade
if errorlevel 1 goto :depfail
goto :done

:depfail
echo [ERROR] Dependency install failed. Check network / proxy / disk space.
pause
exit /b 1

:geturl
REM %1 = url, %2 = dest ; try curl then PowerShell ; verify zip size
del /q "%~2" 2>nul
curl -fsSL -o "%~2" "%~1" 2>nul
call :checkzip "%~2"
if not errorlevel 1 exit /b 0
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%~1' -OutFile '%~2' -UseBasicParsing } catch { exit 1 }"
call :checkzip "%~2"
if not errorlevel 1 exit /b 0
exit /b 1

:checkzip
set "ZIPLEN="
if exist "%~1" for %%A in ("%~1") do if %%~zA GTR 8000000 set "ZIPLEN=ok"
if defined ZIPLEN exit /b 0
exit /b 1

:done
if not exist "%ROOT%\runtime" mkdir "%ROOT%\runtime"
> "%ROOT%\runtime\resolved_python.txt" echo %PYEXE%
echo.
echo ============================================
echo   Install OK.  Runtime kind: %RUNKIND%
echo   Next:
echo     start.bat        - run the service
echo     Browser URL      - http://127.0.0.1:8090
echo     First open       - setup wizard will guide you
echo   Notes:
echo     - Nothing was installed into your system Python.
echo     - Scheduled backup tasks reuse this runtime automatically.
echo   Auto start on boot, example:
echo     schtasks /create /tn MySQLConsole /sc onstart /ru SYSTEM /tr "\"%PYEXE%\" \"%ROOT%src\server.py\""
echo ============================================
pause
