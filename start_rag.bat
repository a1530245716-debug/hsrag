@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  RAG Document QA - starting...
echo  Port: 8000
echo  Env: anaconda rag
echo ============================================
start "RAG-Server" cmd /k run_server.bat
timeout /t 8 /nobreak >nul
start "" http://127.0.0.1:8000
echo Server started, opening browser...
echo Close server: press Ctrl+C in the RAG-Server window
pause