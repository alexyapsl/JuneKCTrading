# KC Runners autostart / self-heal
# Starts the runners if not already running. Safe to run repeatedly (idempotent).
#   1. primary (TREATMENT)  scripts\primary_atr35_short_runner.py
#      (account1, dyn TP/SL + SHORT-only + ATR>=35 entry filter, since 2026-10-25)
#   2. live shadow (CONTROL) scripts\shadow_live_runner.py
#      (account2, dyn TP/SL, no entry filter = old primary behaviour, since 2026-10-25)
#   ~~ sim shadow scripts\shadow_slope_runner.py ~~ TEMPORARILY DISABLED 2026-08-26
#      (slope experiment set aside; re-enable block below to resume)
$ErrorActionPreference = 'SilentlyContinue'

$root = 'C:\Users\alexy\.openclaw\workspace\JuneKCTrading'
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null

# Small delay so boot-time network/IG login doesn't race
Start-Sleep -Seconds 45

function Test-RunnerRunning([string]$pattern) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='py.exe'"
    foreach ($p in $procs) {
        if ($p.CommandLine -and $p.CommandLine -match $pattern) { return $true }
    }
    return $false
}

# --- Primary runner (account1 TREATMENT: dyn + short-only + ATR>=35) ---
# NB: pattern must NOT match the live shadow, which runs via shadow_live_runner.py
if (-not (Test-RunnerRunning 'primary_atr35_short_runner\.py')) {
    Start-Process -FilePath 'py.exe' -ArgumentList '-3', 'scripts\primary_atr35_short_runner.py' `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'primary_console.out') `
        -RedirectStandardError  (Join-Path $logs 'primary_console.err')
    Write-Host "started primary runner (atr35 short-only)"
} else {
    Write-Host "primary runner already running"
}

# --- Sim shadow runner (slope filter, simulated fills) ---
# TEMPORARILY DISABLED 2026-08-26: slope experiment set aside for the
# static-vs-dynamic exit-management A/B. Uncomment to resume.
# if (-not (Test-RunnerRunning 'shadow_slope_runner\.py')) {
#     Start-Process -FilePath 'py.exe' -ArgumentList '-3', 'scripts\shadow_slope_runner.py' `
#         -WorkingDirectory $root -WindowStyle Hidden `
#         -RedirectStandardOutput (Join-Path $logs 'shadow_console.out') `
#         -RedirectStandardError  (Join-Path $logs 'shadow_console.err2')
#     Write-Host "started sim shadow runner"
# } else {
#     Write-Host "sim shadow runner already running"
# }

# --- Live chart web server (port 8787) ---
if (-not (Test-RunnerRunning 'live_chart_server\.py')) {
    Start-Process -FilePath 'py.exe' -ArgumentList '-3', 'scripts\live_chart_server.py', '--port', '8787' `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'live_chart_server.out') `
        -RedirectStandardError  (Join-Path $logs 'live_chart_server.err')
    Write-Host "started live chart server (port 8787)"
} else {
    Write-Host "live chart server already running"
}

# --- Live shadow runner (account2 CONTROL: static TP/SL, no slope, real fills) ---
if (-not (Test-RunnerRunning 'shadow_live_runner\.py')) {
    Start-Process -FilePath 'py.exe' -ArgumentList '-3', 'scripts\shadow_live_runner.py' `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'shadow_live_console.out') `
        -RedirectStandardError  (Join-Path $logs 'shadow_live_console.err')
    Write-Host "started live shadow runner (account2)"
} else {
    Write-Host "live shadow runner already running"
}
