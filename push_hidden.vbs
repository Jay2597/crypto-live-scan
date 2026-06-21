' Launches the 3-hourly git push with NO visible window.
CreateObject("WScript.Shell").Run "powershell -NoProfile -ExecutionPolicy Bypass -File ""C:\TradingApp\new_strategy\task_push.ps1""", 0, False
