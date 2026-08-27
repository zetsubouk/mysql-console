@echo off
title MySQL Console - Stop
cd /d %~dp0
echo ============================================
echo   MySQL Console - Stop Service (port 8090)
echo ============================================
echo.

set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8090" ^| findstr LISTENING') do (
  set FOUND=1
  echo   Stop instance PID %%p
  taskkill /PID %%p /F >nul 2>&1
)

if "%FOUND%"=="0" (
  echo No running service found on port 8090.
  goto :end
)

ping -n 2 127.0.0.1 >nul
netstat -ano | findstr ":8090" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  echo Service stopped.
) else (
  echo WARNING: port 8090 still in use, please check manually.
)

:end
pause
