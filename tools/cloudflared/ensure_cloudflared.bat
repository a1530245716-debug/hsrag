@echo off
chcp 65001 >nul
rem ============================================================
rem  隧道守护：被计划任务每 5 分钟调用一次
rem  进程不在就拉起来；在就什么都不做（轻量，无延迟）
rem
rem  背景：cloudflared 会因 DNS 解析超时等原因自己退出，
rem        没有守护的话外网就 530，而且没人知道。
rem ============================================================

set "RAG_DIR=E:\doc\示例项目\project\rag\tools\cloudflared"
set "PROXY=http://127.0.0.1:7890"

tasklist /FI "IMAGENAME eq cloudflared.exe" 2>nul | find /I "cloudflared.exe" >nul
if not errorlevel 1 exit /b 0

rem 代理没起来就别急着重启，等下一轮
powershell -NoProfile -Command "if (-not (Get-NetTCPConnection -LocalPort 7890 -State Listen -ErrorAction SilentlyContinue)) { exit 1 }" >nul 2>&1
if errorlevel 1 exit /b 0

set "HTTPS_PROXY=%PROXY%"
set "HTTP_PROXY=%PROXY%"
cd /d "%RAG_DIR%"

echo [%date% %time%] 发现隧道不在，重新拉起 >> "%RAG_DIR%\watchdog.log"
start "" /min "cloudflared.exe" --config "config.yml" tunnel run
exit /b 0
