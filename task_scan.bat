@echo off
REM CryptoScan15m - runs the one-shot live scanner; updates results\ CSVs locally.
REM Registered in Windows Task Scheduler to run every 15 minutes.
cd /d C:\TradingApp\new_strategy
if not exist _tasklogs mkdir _tasklogs
"C:\Users\deeps\AppData\Local\Programs\Python\Python311\pythonw.exe" live_scan.py > _tasklogs\scan.log 2>&1
