<#
.SYNOPSIS
    Live watcher for 3-minute Dow candles (PowerShell version)

.DESCRIPTION
    Shows the latest candles from the most recent JSONL log file.
    Refreshes every 8 seconds.

.USAGE
    cd C:\Users\alexy\.openclaw\workspace\JuneKCTrading\scripts
    .\watch_candles.ps1
#>

$LogDir = Join-Path $PSScriptRoot "..\logs"
$RefreshSeconds = 8

function Get-LatestLogFile {
    Get-ChildItem -Path $LogDir -Filter "dow_3min_*.jsonl" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}

function Show-Candles {
    param($File)

    if (-not $File) {
        Write-Host "No log files found yet..." -ForegroundColor Yellow
        return
    }

    $lines = Get-Content $File.FullName -Tail 12 -ErrorAction SilentlyContinue
    if (-not $lines) {
        Write-Host "Log file is empty." -ForegroundColor Yellow
        return
    }

    Clear-Host
    Write-Host "=== Latest 3-min Candles ===" -ForegroundColor Cyan
    Write-Host "File: $($File.Name)" -ForegroundColor Gray
    Write-Host ""

    $lines | ForEach-Object {
        try {
            $c = $_ | ConvertFrom-Json
            $time = ([datetime]$c.timestamp_utc).ToString("HH:mm")
            $range = [math]::Round($c.high - $c.low, 2)
            Write-Host ("{0}  |  O:{1,-8} H:{2,-8} L:{3,-8} C:{4,-8}  Range:{5}" -f 
                $time, $c.open, $c.high, $c.low, $c.close, $range)
        } catch {}
    }

    Write-Host ""
    Write-Host "Press Ctrl+C to exit" -ForegroundColor DarkGray
}

# Main loop
while ($true) {
    $latest = Get-LatestLogFile
    Show-Candles -File $latest
    Start-Sleep -Seconds $RefreshSeconds
}
