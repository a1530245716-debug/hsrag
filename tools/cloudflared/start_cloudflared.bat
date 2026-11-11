@echo off
chcp 65001 >nul
rem ============================================================
rem  RAG 系统外网隧道开机自启脚本
rem  放在启动文件夹里，登录 Windows 后自动拉起 Cloudflare Tunnel
rem
rem  两个关键点：
rem   1) 等 90 秒 —— 让 VPN/代理先起来，否则隧道连不上 Cloudflare
rem   2) 先查进程 —— 避免重复启动
rem ============================================================

set "RAG_DIR=E:\doc\示例项目\project\rag\tools\cloudflared"
set "PROXY=http://127.0.0.1:7890"

rem 等 VPN / 代理就绪
timeout /t 90 /nobreak >nul

rem 已经有隧道在跑就不重复启动
tasklist /FI "IMAGENAME eq cloudflared.exe" 2>nul | find /I "cloudflared.exe" >nul
if not errorlevel 1 (
    echo [%date% %time%] cloudflared 已在运行，跳过 >> "%RAG_DIR%\autostart.log"
    exit /b 0
)

set "HTTPS_PROXY=%PROXY%"
set "HTTP_PROXY=%PROXY%"
cd /d "%RAG_DIR%"

echo [%date% %time%] 启动 cloudflared >> "%RAG_DIR%\autostart.log"
start "" /min "cloudflared.exe" --config "config.yml" tunnel run
