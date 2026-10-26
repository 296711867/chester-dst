@echo off
rem Klei Panel launcher (run by double-click)
chcp 65001 >nul
title Chester · 饥荒礼物箱
cd /d "%~dp0"

where python >nul 2>nul
if not errorlevel 1 (set "PY=python") else (set "PY=py -3")

echo ============================================================
echo   Chester 饥荒礼物箱
echo   - 启动后浏览器自动打开 http://127.0.0.1:8377
echo   - 页面里填间隔、点开始,统计/礼物/历史一目了然
echo   - 教程见项目根目录 挂机指南.html
echo   - 与「心跳自动领取.bat」不要同时运行
echo   - Ctrl+C 退出
echo ============================================================

%PY% -X utf8 klei_panel.py %*
set RC=%errorlevel%

if not "%RC%"=="0" (
  echo.
  echo [!] 启动失败 exit code %RC%
)
echo.
pause
