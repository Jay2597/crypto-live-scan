# CryptoHeartbeat - alerts if the crypto scanner has stalled.
# The scanner rewrites results\live_scan_status.txt every run, so that file's last-write time IS the
# last successful scan. If it's older than STALE_MIN minutes (>= 4 missed 15-min runs) we treat the
# scanner as stalled and: (1) write a flag file to the Desktop, (2) pop a Windows balloon alert,
# (3) log it, and (4) attempt ONE self-heal by re-running the scan task. Clears automatically when
# the scanner recovers. Registered in Task Scheduler to run every 30 min.
$dir = "C:\TradingApp\new_strategy"
Set-Location $dir
$status = Join-Path $dir "results\live_scan_status.txt"
$logDir = Join-Path $dir "_tasklogs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory $logDir | Out-Null }
$log  = Join-Path $logDir "heartbeat.log"
$flag = Join-Path ([Environment]::GetFolderPath("Desktop")) "CRYPTO_SCANNER_STALLED.txt"
$STALE_MIN = 60

$now   = (Get-Date).ToUniversalTime()
$tsNow = $now.ToString("yyyy-MM-ddTHH:mmZ")
if (Test-Path $status) {
    $last = (Get-Item $status).LastWriteTimeUtc
    $ageMin = [int]($now - $last).TotalMinutes
    $lastStr = $last.ToString("yyyy-MM-ddTHH:mmZ")
} else {
    $ageMin = 99999; $lastStr = "never"
}

if ($ageMin -gt $STALE_MIN) {
    $msg = "Crypto scanner STALLED: no scan in $ageMin min (threshold $STALE_MIN). Last scan $lastStr."
    "$tsNow STALL ($ageMin min, last $lastStr)" | Add-Content $log
    @("CRYPTO SCANNER STALLED",
      "Checked (UTC):     $tsNow",
      "Last scan (UTC):   $lastStr  ($ageMin min ago)",
      "Threshold:         $STALE_MIN min",
      "",
      "The CryptoScan15m task has not updated results in over $STALE_MIN minutes.",
      "A self-heal restart was attempted. If this file keeps reappearing, open Task",
      "Scheduler and check CryptoScan15m, or see _tasklogs\scan.log for errors."
     ) -join "`r`n" | Set-Content $flag
    # (4) self-heal: kick the scan task once
    schtasks /run /tn "CryptoScan15m" 2>&1 | Out-Null
    # (2) balloon alert (best effort; needs an interactive session)
    try {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $ni = New-Object System.Windows.Forms.NotifyIcon
        $ni.Icon = [System.Drawing.SystemIcons]::Warning
        $ni.Visible = $true
        $ni.ShowBalloonTip(15000, "Crypto Scanner Stalled", $msg, [System.Windows.Forms.ToolTipIcon]::Warning)
        Start-Sleep -Seconds 6
        $ni.Dispose()
    } catch {}
} else {
    "$tsNow ok ($ageMin min)" | Add-Content $log
    if (Test-Path $flag) { Remove-Item $flag -Force }   # auto-clear once healthy
}
