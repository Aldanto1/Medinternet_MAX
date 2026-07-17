# Запуск Medinternet_MAX вместе с HTTPS-туннелем cloudflared.
# Использование:  .\run.ps1
# Останавливается по Ctrl+C (туннель тоже закроется).

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root "venv\Scripts\python.exe"
$envFile = Join-Path $root "env\.env"
$port = 8080

# --- Находим cloudflared ---
$cloudflared = $null
$candidates = @(
    "C:\Program Files (x86)\cloudflared\cloudflared.exe",
    "C:\Program Files\cloudflared\cloudflared.exe"
)
foreach ($c in $candidates) { if (Test-Path $c) { $cloudflared = $c; break } }
if (-not $cloudflared) {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { $cloudflared = $cmd.Source }
}
if (-not $cloudflared) {
    Write-Host "cloudflared не найден. Установите: winget install Cloudflare.cloudflared" -ForegroundColor Red
    exit 1
}

# --- Запускаем туннель ---
Write-Host "Запуск туннеля cloudflared -> http://127.0.0.1:$port ..." -ForegroundColor Cyan
$log = Join-Path $root ".cloudflared.log"
$errLog = Join-Path $root ".cloudflared.err.log"
foreach ($f in @($log, $errLog)) {
    if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
}

$tunnel = Start-Process -FilePath $cloudflared `
    -ArgumentList "tunnel", "--url", "http://127.0.0.1:$port" `
    -RedirectStandardOutput $log -RedirectStandardError $errLog `
    -NoNewWindow -PassThru

# --- Ждём публичный HTTPS-адрес ---
$url = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    $content = (Get-Content $log, $errLog -ErrorAction SilentlyContinue) -join "`n"
    $m = [regex]::Match($content, "https://[a-z0-9-]+\.trycloudflare\.com")
    if ($m.Success) { $url = $m.Value; break }
}

if (-not $url) {
    Write-Host "Не удалось получить адрес туннеля. См. лог: $log" -ForegroundColor Red
    Stop-Process -Id $tunnel.Id -Force -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "Туннель готов: $url" -ForegroundColor Green

# --- Прописываем WEBAPP_URL в env/.env (без BOM) ---
$lines = @(Get-Content $envFile)
$found = $false
$out = foreach ($line in $lines) {
    if ($line -match '^\s*WEBAPP_URL=') { $found = $true; "WEBAPP_URL=$url" }
    else { $line }
}
if (-not $found) { $out += "WEBAPP_URL=$url" }
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($envFile, $out, $utf8NoBom)

# --- Запускаем бота ---
Write-Host "Запуск бота..." -ForegroundColor Cyan
try {
    & $python (Join-Path $root "max_bot.py")
}
finally {
    Write-Host "`nОстановка туннеля..." -ForegroundColor Yellow
    Stop-Process -Id $tunnel.Id -Force -ErrorAction SilentlyContinue
}
