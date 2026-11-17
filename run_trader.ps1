# Weekdays 17:50 KST (Task Scheduler): place today's US reservation orders (exits at open, new buys).
# If the morning core steps did not finish today, run them first so decisions use fresh closes.
# ASCII only on purpose: Task Scheduler runs Windows PowerShell 5.1, which misreads non-BOM UTF-8.
$env:PYTHONIOENCODING = "utf-8"
Set-Location $PSScriptRoot
$log = "$HOME\kis-bot-data\logs\run_$(Get-Date -Format yyyy-MM-dd).log"
if (-not ((Test-Path $log) -and (Select-String -Path $log -Pattern "core done" -Quiet))) {
    & "$PSScriptRoot\run_daily.ps1" -CoreOnly
}
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -B trader.py daily 2>&1 | Out-Null
exit $LASTEXITCODE
