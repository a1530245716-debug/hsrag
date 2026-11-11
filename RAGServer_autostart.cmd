@echo off
rem 开机/登录自启: RAG 文档问答服务(最小化窗口常驻 8000 端口)
cd /d "E:\doc\示例项目\project\rag"
start "RAG-Server" /min cmd /c "run_server.bat"
