# KC Runners autostart / self-heal
# Starts all three runners if not already running. Safe to run repeatedly (idempotent).
#   1. primary       run_kc_live.py                 (account1, experiment de5459e4)
#   2. sim shadow    scripts\shadow_slope_runner.py (sim fills, experiment 0994abfe)
#   3. live shadow   scripts\shadow_live_runner.py  (account2 + slope, real fills, 8903f018)
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

# --- Primary runner (account1) ---
# NB: pattern must NOT match the live shadow, which runs via shadow_live_runner.py
if (-not (Test-RunnerRunning 'run_kc_live\.py')) {
    Start-Process -FilePath 'py.exe' -ArgumentList '-3', 'run_kc_live.py' `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'primary_console.out') `
        -RedirectStandardError  (Join-Path $logs 'primary_console.err')
    Write-Host "started primary runner"
} else {
    Write-Host "primary runner already running"
}

# --- Sim shadow runner (slope filter, simulated fills) ---
if (-not (Test-RunnerRunning 'shadow_slope_runner\.py')) {
    Start-Process -FilePath 'py.exe' -ArgumentList '-3', 'scripts\shadow_slope_runner.py' `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'shadow_console.out') `
        -RedirectStandardError  (Join-Path $logs 'shadow_console.err2')
    Write-Host "started sim shadow runner"
} else {
    Write-Host "sim shadow runner already running"
}

# --- Live shadow runner (account2, slope filter, real fills) ---
if (-not (Test-RunnerRunning 'shadow_live_runner\.py')) {
    Start-Process -FilePath 'py.exe' -ArgumentList '-3', 'scripts\shadow_live_runner.py' `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'shadow_live_console.out') `
        -RedirectStandardError  (Join-Path $logs 'shadow_live_console.err')
    Write-Host "started live shadow runner (account2)"
} else {
    Write-Host "live shadow runner already running"
}
