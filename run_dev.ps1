# 开发启动脚本 —— 按所在目录自动选端口，三个 worktree 共用同一份
#
# 为什么这样写：这份文件被 git 跟踪，而每个 worktree 需要的端口不同。
# 之前每个 worktree 各存一份「写死端口」的版本，每次合并都会在 8011/8012
# 之间来回冲突。改成从目录名推导端口后，一份文件适配所有 worktree，永不冲突。
#
# 目录 → 端口映射（与 AGENTS.md 第七节端口分工保持一致）：
#   <...>\project\rag          → 8000（生产，别用它跑开发）
#   <...>\project\rag-agent    → 8011（RAG-Agent）
#   <...>\project\rag-claude   → 8012（Claude Code）
#   其它 / 未知目录            → 8013（随便找个空位，避免撞上面几个）
#
# 用法：
#   .\run_dev.ps1        # 自动选端口
#   .\run_dev.ps1 8020   # 手动指定端口

param([int]$Port = 0)

$here = Split-Path -Parent $PSScriptRoot
$leaf = Split-Path -Leaf $PSScriptRoot

if (-not $Port) {
    $Port = switch ($leaf) {
        "rag"        { 8000 }
        "rag-agent"  { 8011 }
        "rag-claude" { 8012 }
        default      { 8013 }
    }
}

if ($Port -eq 8000) {
    Write-Host "警告：这是生产端口 8000，开发测试请用 8011/8012/8013" -ForegroundColor Yellow
}

$py = "C:\Users\hspcadmin\anaconda3\envs\rag\python.exe"
Set-Location $PSScriptRoot

# 端口被占用就先说清楚，别等到 uvicorn 报错才去找原因
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "端口 $Port 已被占用 (pid=$($busy.OwningProcess))，换一个：" -ForegroundColor Red
    Write-Host "   .\run_dev.ps1 8013"
    exit 1
}

Write-Host "启动 $leaf  →  http://127.0.0.1:$Port" -ForegroundColor Cyan
& $py app\server.py --port $Port
