@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   RAG Document QA - 外网访问 (Tailscale Funnel)
echo ============================================
set "TS=C:\Program Files\Tailscale\tailscale.exe"
if not exist "%TS%" (
  echo [错误] 未找到 Tailscale，请先安装: winget install Tailscale.Tailscale
  pause
  exit /b 1
)

echo [1/3] 检查 Tailscale 登录状态...
"%TS%" status >nul 2>&1
if errorlevel 1 (
  echo 未登录，正在打开浏览器授权...
  start "" "https://login.tailscale.com/start"
  echo 请在浏览器完成登录后，重新运行本脚本。
  pause
  exit /b 1
)
echo      已登录。

echo [2/3] 检查 8000 端口服务...
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if errorlevel 1 (
  echo      服务未运行，正在启动...
  start "RAG-Server" cmd /k run_server.bat
  echo      等待 6 秒让服务就绪...
  timeout /t 6 /nobreak >nul
)
echo      服务运行中。

echo [3/3] 启用公网 Funnel(后台持久化)...
"%TS%" funnel --bg 8000
echo.
echo 完成。公网地址: https://desktop-upqltd0.tail22c770.ts.net/
echo 查看状态: "%TS%" funnel status    |    关闭外网: "%TS%" funnel off
echo 注意: 首次使用若提示未启用，访问 login.tailscale.com 授权一次即可。
pause
