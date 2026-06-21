# CryptoPush3h - commits & pushes the local scan results to GitHub, batched.
# Registered in Windows Task Scheduler to run every 3 hours. Pulls --rebase first so it
# never conflicts with anything already on origin. No-ops cleanly when nothing changed.
Set-Location C:\TradingApp\new_strategy
if (-not (Test-Path _tasklogs)) { New-Item -ItemType Directory _tasklogs | Out-Null }
$ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mmZ")
$log = "_tasklogs\push.log"

git add results/ 2>&1 | Out-Null
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "local scan batch $ts" 2>&1 | Out-Null
    git pull --rebase --autostash origin main 2>&1 | Out-Null
    git push origin main 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { "$ts pushed" | Add-Content $log }
    else { "$ts PUSH FAILED (exit $LASTEXITCODE)" | Add-Content $log }
} else {
    "$ts no-change" | Add-Content $log
}
