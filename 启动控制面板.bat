@echo off
rem Klei Panel launcher (run by double-click)
rem 优先无控制台方式启动(pythonw):程序以托盘图标常驻,右键图标退出
chcp 65001 >nul
cd /d "%~dp0"

set "PYW="
where pythonw >nul 2>nul && set "PYW=pythonw"
if not defined PYW (where pyw >nul 2>nul && set "PYW=pyw -3")

if defined PYW (
  start "" %PYW% -X utf8 klei_panel.py %*
  exit /b 0
)

rem 兜底:只有 python(控制台模式,黑框可见便于排错)
where python >nul 2>nul
if not errorlevel 1 (set "PY=python") else (set "PY=py -3")

title Chester · 饥荒礼物箱
echo ============================================================
echo   Chester 饥荒礼物箱(未找到 pythonw,退回控制台模式)
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
