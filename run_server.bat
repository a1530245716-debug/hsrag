@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "RAG_PY=%USERPROFILE%\anaconda3\envs\rag\python.exe"
if not exist "%RAG_PY%" set "RAG_PY=python"
echo Using Python: %RAG_PY%
"%RAG_PY%" app\server.py