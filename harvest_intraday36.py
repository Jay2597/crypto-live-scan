"""harvest_intraday36.py — move Kite MCP 15-min payloads (saved by the harness to
tool-results/*.txt) into data/stocks/intraday15_inplay/<SYM>_15minute.json.

Usage: python harvest_intraday36.py <file1>:<SYM1> <file2>:<SYM2> ...
       (file may be the bare timestamp id, e.g. 1783120604866:NIFTY)
"""
import json
import os
import sys

TR_DIR = r"C:\Users\deeps\.claude\projects\C--TradingApp\23cfc5f3-d64c-41e4-8754-d111a05ac462\tool-results"
OUT = os.path.join("data", "stocks", "intraday15_inplay")
os.makedirs(OUT, exist_ok=True)

for arg in sys.argv[1:]:
    fid, sym = arg.split(":")
    path = fid if os.path.exists(fid) else os.path.join(
        TR_DIR, f"mcp-kite-get_historical_data-{fid}.txt")
    bars = json.load(open(path, encoding="utf-8"))
    out = [dict(date=b["date"], open=b["open"], high=b["high"], low=b["low"],
                close=b["close"], volume=b.get("volume", 0)) for b in bars]
    with open(os.path.join(OUT, f"{sym}_15minute.json"), "w") as f:
        json.dump(out, f)
    print(f"{sym}: {len(out)} bars  {out[0]['date'][:10]} -> {out[-1]['date'][:10]}")
