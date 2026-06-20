# Run the crypto trigger scanner in the cloud (GitHub Actions) — no laptop needed

The workflow `.github/workflows/crypto-scan.yml` runs `live_scan.py` every 15 min on GitHub's
servers, scanning 17 pairs on the latest closed 1h bar with the validated CryptoStrategy, and
commits any fired signal to `results/live_scan_log.csv` so you can read it on github.com anytime.

Data source is KuCoin/MEXC/Bybit/OKX (fallback chain) because Binance returns HTTP 451 from
GitHub's US runners.

## Option A — GitHub web upload (no git needed locally)

1. Create a GitHub account if you don't have one.
2. New repo -> **Public** (important: public = unlimited Actions minutes; private only gives
   2,000 min/mo and this uses ~2,900/mo at 15-min cadence).
3. Upload the contents of `C:\TradingApp\new_strategy\` EXCEPT the big `data/` folder. The
   files that matter: `live_scan.py`, `features.py`, `crypto.py`, `strategy.py`, `patterns.py`,
   `risk.py`, `backtest.py`, `requirements.txt`, `.gitignore`, and the `.github/` folder.
   (Drag-drop in the "Add file -> Upload files" UI. The `.github` folder must keep that exact path.)
4. Go to the repo's **Actions** tab -> enable workflows -> open "crypto-live-scan" -> **Run workflow**
   once to confirm it works. After that it runs every 15 min on its own.

## Option B — git CLI

```
winget install Git.Git          # if git isn't installed; reopen the shell after
cd C:\TradingApp\new_strategy
git init -b main
git add live_scan.py features.py crypto.py strategy.py patterns.py risk.py backtest.py requirements.txt .gitignore .github
git commit -m "cloud crypto scanner"
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```
Then enable + run once from the Actions tab.

## Reading results
- `results/live_scan_log.csv` — fired triggers (empty = nothing triggered).
- `results/live_scan_status.txt` — heartbeat + current watchlist, refreshed every run.
- The commit history is itself a timeline of scans.

## Notes / caveats
- **Public repo recommended** for unlimited minutes; the data is just public crypto signals.
- GitHub **disables scheduled workflows after 60 days** of repo inactivity — the per-run commit
  keeps it active, but if you stop reading it for months it may pause.
- Scheduled cron can be **delayed** (sometimes 15-30 min) during GitHub peak load; fine for a
  1h-bar strategy.
- This duplicates the local Windows task `CryptoLiveScan15m`. Once the cloud one works, remove
  the local task: `Unregister-ScheduledTask -TaskName CryptoLiveScan15m -Confirm:$false`.
