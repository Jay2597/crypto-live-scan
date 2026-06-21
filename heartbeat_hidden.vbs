' Launches the heartbeat watchdog with NO visible window (its stall balloon alert still shows).
CreateObject("WScript.Shell").Run "powershell -NoProfile -ExecutionPolicy Bypass -File ""C:\TradingApp\new_strategy\heartbeat_check.ps1""", 0, False
