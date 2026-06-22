@echo off
REM CryptoBroadSweepDaily - runs the wide top-N near-trigger sweep once a day.
REM Writes results\broad_scan_status.txt (new candidates tagged "NOT in live_scan").
REM Registered in Windows Task Scheduler to run daily.
cd /d C:\TradingApp\new_strategy
if not exist _tasklogs mkdir _tasklogs
"C:\Users\deeps\AppData\Local\Programs\Python\Python311\pythonw.exe" broad_scan.py 150 > _tasklogs\broad_sweep.log 2>&1
