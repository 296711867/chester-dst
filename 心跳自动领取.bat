@echo off
rem Chester DST idle gift panel (run by double-click)
chcp 65001 >nul
title Chester Gift Panel
cd /d "%~dp0"

where python >nul 2>nul
if not errorlevel 1 (set "PY=python") else (set "PY=py -3")

echo ============================================================
echo   Chester - DST Idle Gift Panel
echo   - heartbeat (Tick) every 360s
echo   - auto claim gift when received
echo     (VerifyGiftingReceipt + SetItemOpened)
echo   - first run needs Steam client logon
echo   - Ctrl+C to exit
echo ============================================================

%PY% -X utf8 klei_heartbeat_auto.py %*
set RC=%errorlevel%

if not "%RC%"=="0" (
  echo.
  echo [!] script exit code %RC%, see hb_auto_console.log
)
echo.
pause