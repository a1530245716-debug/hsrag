# start-ngrok.ps1 —— 把本机的 RAG 服务暴露成公网 https 地址
#
# 用法:
#   .\start-ngrok.ps1                          # 用已保存的 token, 随机域名
#   .\start-ngrok.ps1 -AuthToken <你的token>    # 首次: 配置 token 并启动
#   .\start-ngrok.ps1 -Domain your.ngrok.app   # 付费版: 固定域名
#   .\start-ngrok.ps1 -Port 8000 -Stop         # 停止隧道
#
# 首次使用需要 ngrok 账号(免费): https://dashboard.ngrok.com/get-started/your-authtoken
# 免费版每次启动域名都会变, 关了窗口/机器重启就失效。

param(
    [string]$AuthToken = "",
    [int]$Port = 8000,
    [string]$Domain = "",
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $here "ngrok.exe"
$api = "http://127.0.0.1:4040/api/tunnels"

function Get-PublicUrl {
    try {
        $t = Invoke-RestMethod $api -TimeoutSec 3
        $u = ($t.tunnels | Where-Object { $_.public_url -like "https://*" } |
              Select-Object -First 1).public_url
        return $u
    } catch {
        return $null
    }
}

if (-not (Test-Path $exe)) {
    Write-Host "找不到 ngrok.exe：$exe" -ForegroundColor Red
    Write-Host "重新下载：https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip" -ForegroundColor Yellow
    exit 1
}

if ($Status) {
    $u = Get-PublicUrl
    if ($u) { Write-Host "运行中：$u" -ForegroundColor Green } else { Write-Host "没有运行中的隧道" -ForegroundColor Yellow }
    exit 0
}

if ($Stop) {
    Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Host "已停止 ngrok" -ForegroundColor Yellow
    exit 0
}

# ---- token ----
$cfgFile = Join-Path $env:LOCALAPPDATA "ngrok\ngrok.yml"
if ($AuthToken) {
    & $exe config add-authtoken $AuthToken | Out-Null
    Write-Host "authtoken 已保存" -ForegroundColor Green
} elseif (-not (Test-Path $cfgFile)) {
    if ($env:NGROK_AUTHTOKEN) {
        & $exe config add-authtoken $env:NGROK_AUTHTOKEN | Out-Null
        Write-Host "已从环境变量 NGROK_AUTHTOKEN 读取" -ForegroundColor Green
    } else {
        Write-Host "还没有配置 authtoken。" -ForegroundColor Yellow
        Write-Host "  1. 打开 https://dashboard.ngrok.com/get-started/your-authtoken 注册（免费）"
        Write-Host "  2. 复制那串 token"
        Write-Host "  3. 运行：.\start-ngrok.ps1 -AuthToken <你的token>"
        exit 1
    }
}

# ---- 已有隧道就用现成的 ----
$existing = Get-PublicUrl
if ($existing) {
    Write-Host "隧道已在运行：$existing" -ForegroundColor Green
    exit 0
}

# ---- 先确认本地服务通 ----
try {
    $h = Invoke-WebRequest "http://127.0.0.1:$Port/api/health" -TimeoutSec 5 -UseBasicParsing
    if ($h.StatusCode -ne 200) { throw "状态码 $($h.StatusCode)" }
    Write-Host "本地服务正常（端口 $Port）" -ForegroundColor Green
} catch {
    Write-Host "本地服务 http://127.0.0.1:$Port 不可用：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "先启动服务：run_server.bat" -ForegroundColor Yellow
    exit 1
}

# ---- 启动隧道 ----
$ngArgs = @("http", $Port, "--log", "stdout")
if ($Domain) { $ngArgs += @("--domain", $Domain) }
Write-Host "启动隧道：ngrok $($ngArgs -join ' ')" -ForegroundColor Cyan
Start-Process -FilePath $exe -ArgumentList $ngArgs -WindowStyle Hidden -RedirectStandardOutput "$here\ngrok.log"

for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Seconds 1
    $u = Get-PublicUrl
    if ($u) {
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  公网地址： $u" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "登录口令：用管理员或普通用户的 key（不需要用户名）"
        Write-Host "停止隧道：.\start-ngrok.ps1 -Stop"
        Write-Host ""
        exit 0
    }
}

Write-Host "隧道没起来，看日志：$here\ngrok.log" -ForegroundColor Red
if (Test-Path "$here\ngrok.log") { Get-Content "$here\ngrok.log" -Tail 15 }
exit 1
