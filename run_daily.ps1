# Daily after US close: refresh daily bars -> regime -> pullback signals -> paper P&L snapshot ("core"),
# then the ranking-based momentum-burst screener report (reference only).
# -CoreOnly: core steps only (run_trader.ps1 calls this when the morning run did not finish).
# ASCII only on purpose: Task Scheduler runs Windows PowerShell 5.1, which misreads non-BOM UTF-8.
param([switch]$CoreOnly)
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$data = "$HOME\kis-bot-data"
New-Item -ItemType Directory -Force "$data\logs" | Out-Null
$log = "$data\logs\run_$(Get-Date -Format yyyy-MM-dd).log"
$env:PYTHONIOENCODING = "utf-8"
Set-Location $PSScriptRoot

"=== $(Get-Date) update history" | Out-File $log -Append -Encoding utf8
& $py -B update_history.py 2>&1 | Out-File $log -Append -Encoding utf8
if ($LASTEXITCODE -ne 0) { "update failed ($LASTEXITCODE), signals skipped" | Out-File $log -Append -Encoding utf8 }
else {
    "=== $(Get-Date) market regime" | Out-File $log -Append -Encoding utf8
    & $py -B regime.py 2>&1 | Out-File $log -Append -Encoding utf8
    "=== $(Get-Date) pullback signals" | Out-File $log -Append -Encoding utf8
    & $py -B trader.py signals 2>&1 | Out-File $log -Append -Encoding utf8
    "=== $(Get-Date) paper P&L snapshot" | Out-File $log -Append -Encoding utf8
    & $py -B trader.py snapshot 2>&1 | Out-File $log -Append -Encoding utf8
    if ($LASTEXITCODE -eq 0) { "=== $(Get-Date) core done" | Out-File $log -Append -Encoding utf8 }
}
if ($CoreOnly) { exit 0 }

"=== $(Get-Date) momentum export" | Out-File $log -Append -Encoding utf8
& $py -B export_daily.py 2>&1 | Out-File $log -Append -Encoding utf8
if ($LASTEXITCODE -ne 0) { "export failed ($LASTEXITCODE), screener skipped" | Out-File $log -Append -Encoding utf8; exit 1 }

"=== $(Get-Date) momentum screen" | Out-File $log -Append -Encoding utf8
& $py -B "$HOME\.agents\skills\stockbee-momentum-burst-screener\scripts\screen_momentum_burst.py" `
    --prices-json "$data\daily_ohlcv.json" --output-dir "$data\reports" 2>&1 | Out-File $log -Append -Encoding utf8
exit $LASTEXITCODE
