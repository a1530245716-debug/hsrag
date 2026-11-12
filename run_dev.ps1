# 本 worktree 专用启动脚本（端口 8011，不占用生产 8000）
# 用法: .\run_dev.ps1
$py = "C:\Users\hspcadmin\anaconda3\envs\rag\python.exe"
Set-Location $PSScriptRoot
Write-Host "启动 rag-agent  →  http://127.0.0.1:8011" -ForegroundColor Cyan
& $py app\server.py --port 8011
