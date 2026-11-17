# Weekdays 09:15 KST (Task Scheduler): refresh KR regime/flows/news + KODEX 200 bars, then the KR paper trader.
# ASCII only on purpose: Task Scheduler runs Windows PowerShell 5.1, which misreads non-BOM UTF-8.
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$data = "$HOME\kis-bot-data"
New-Item -ItemType Directory -Force "$data\logs" | Out-Null
$log = "$data\logs\run_kr_$(Get-Date -Format yyyy-MM-dd).log"
$env:PYTHONIOENCODING = "utf-8"
Set-Location $PSScriptRoot

"=== $(Get-Date) market regime" | Out-File $log -Append -Encoding utf8
& $py -B regime.py 2>&1 | Out-File $log -Append -Encoding utf8
"=== $(Get-Date) kr trader" | Out-File $log -Append -Encoding utf8
& $py -B kr_trader.py run 2>&1 | Out-File $log -Append -Encoding utf8
exit $LASTEXITCODE
