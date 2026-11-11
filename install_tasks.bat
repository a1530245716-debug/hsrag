@echo off
chcp 65001 >nul
setlocal

set "PY=C:\Users\user\anaconda3\envs\rag\python.exe"
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"

echo ============================================================
echo   RAG 系统 - 定时任务安装
echo ============================================================
echo   脚本目录: %DIR%
echo   Python  : %PY%
echo.

:: 检查管理员权限
net session >nul 2>&1
if errorlevel 1 (
  echo [错误] 需要管理员权限。
  echo        请右键点击本文件 - "以管理员身份运行"。
  echo.
  pause
  exit /b 1
)

if not exist "%PY%" (
  echo [错误] 找不到 Python: %PY%
  echo        请修改本文件第 5 行的 PY 变量。
  pause
  exit /b 1
)

echo [1/3] 注册健康检查（每 10 分钟）...
schtasks /Create /F /SC MINUTE /MO 10 /TN "RAG-HealthCheck" /TR "\"%PY%\" \"%DIR%\healthcheck.py\" --quiet" >nul
if errorlevel 1 (echo   失败) else (echo   完成)

echo [2/3] 注册每日轻量备份（每天 12:00，只备用户/笔记，秒级）...
schtasks /Create /F /SC DAILY /ST 12:00 /TN "RAG-Backup-Light" /TR "\"%PY%\" \"%DIR%\backup.py\" --light" >nul
if errorlevel 1 (echo   失败) else (echo   完成)

echo [3/3] 注册每周全量备份（周日 03:00，含上传的原始文档）...
schtasks /Create /F /SC WEEKLY /D SUN /ST 03:00 /TN "RAG-Backup-Full" /TR "\"%PY%\" \"%DIR%\backup.py\"" >nul
if errorlevel 1 (echo   失败) else (echo   完成)

echo.
echo ============================================================
echo   已注册的任务
echo ============================================================
schtasks /Query /TN "RAG-HealthCheck"    2>nul | findstr /i "RAG-HealthCheck"
schtasks /Query /TN "RAG-Backup-Light"   2>nul | findstr /i "RAG-Backup-Light"
schtasks /Query /TN "RAG-Backup-Full"    2>nul | findstr /i "RAG-Backup-Full"

echo.
echo 常用命令:
echo   立即跑一次检查   schtasks /Run /TN "RAG-HealthCheck"
echo   立即备份一次     "%PY%" "%DIR%\backup.py" --light
echo   查看备份列表     "%PY%" "%DIR%\backup.py" --list
echo   卸载全部任务     schtasks /Delete /TN "RAG-HealthCheck" /F ^& schtasks /Delete /TN "RAG-Backup-Light" /F ^& schtasks /Delete /TN "RAG-Backup-Full" /F
echo.
pause
