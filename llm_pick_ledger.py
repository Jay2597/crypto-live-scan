"""llm_pick_ledger.py — FORWARD paper ledger comparing LLM stock picks vs mechanical momentum.

Why forward-only: an LLM cannot be honestly backtested on 2021-2025 Indian equities — its training
data knows what happened (look-ahead contamination). So LLM picks are recorded LIVE and settled
later, next to a mechanical control group picked the same day under the same rules.

Both sources share the validated swing exit rules (swing_momentum.py finding, 2026-06-22):
  entry  = next trading day's OPEN after pick_date
  stop   = entry - 2.0 * ATR(14)   (initial, computed at entry day)
  trail  = 20-day-low (Turtle) ratcheting up; exit at stop if day's low touches it
  costs  = Zerodha CNC (~0.35% round trip incl slippage) applied at settle

Ledger: results/llm_picks.csv
Commands:
  python llm_pick_ledger.py mech                 # print today's mechanical top-5 momentum picks
  python llm_pick_ledger.py record <src> <SYM> "thesis..."   # record a pick (src = llm|mech)
  python llm_pick_ledger.py settle               # update all open picks from yfinance
  python llm_pick_ledger.py report               # scoreboard llm vs mech
"""
import csv
import json
import math
import os
import sys
from datetime import date

LEDGER = os.path.join("results", "llm_picks.csv")
FIELDS = ["pick_date", "source", "symbol", "thesis", "status", "entry_date", "entry", "stop",
          "exit_date", "exit", "ret_pct_net", "hold_days"]
COST_RT = 0.0035  # CNC round trip incl slippage (validated model, flat approximation)
ATR_N, ATR_STOP, TRAIL_N = 14, 2.0, 20
PIT_DIR = os.path.join("data", "stocks", "swing_pit")


def load_ledger():
    if not os.path.exists(LEDGER):
        return []
    with open(LEDGER, newline="") as f:
        return list(csv.DictReader(f))


def save_ledger(rows):
    os.makedirs("results", exist_ok=True)
    with open(LEDGER, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def fetch_daily(symbol, start):
    import yfinance as yf
    df = yf.download(f"{symbol}.NS", start=start, progress=False, auto_adjust=True,
                     multi_level_index=False)
    bars = []
    for ts, row in df.iterrows():
        if any(row[k] != row[k] for k in ("Open", "High", "Low", "Close")):
            continue
        bars.append(dict(date=str(ts.date()), o=float(row["Open"]), h=float(row["High"]),
                         l=float(row["Low"]), c=float(row["Close"])))
    return bars


def atr(bars, n=ATR_N):
    trs = []
    for i in range(1, len(bars)):
        trs.append(max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - bars[i - 1]["c"]),
                       abs(bars[i]["l"] - bars[i - 1]["c"])))
    if len(trs) < n:
        return None
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def cmd_mech():
    """Today's mechanical top-5: 126d momentum among regime-eligible PIT-universe names."""
    import glob
    scored = []
    for f in sorted(glob.glob(os.path.join(PIT_DIR, "*_day.json"))):
        sym = os.path.basename(f).replace("_day.json", "")
        if sym == "NIFTY":
            continue
        bars = json.load(open(f))
        c = [b["close"] for b in bars]
        if len(c) < 260:
            continue
        s200 = sum(c[-200:]) / 200
        s200_prior = sum(c[-220:-20]) / 200
        if not (c[-1] > s200 and s200 > s200_prior):
            continue
        mom = c[-6] / c[-132] - 1
        scored.append((mom, sym, c[-1]))
    scored.sort(reverse=True)
    nb = json.load(open(os.path.join(PIT_DIR, "NIFTY_day.json")))
    nc = [b["close"] for b in nb]
    mkt_ok = nc[-1] > sum(nc[-200:]) / 200
    print(f"market gate (NIFTY>SMA200): {'PASS' if mkt_ok else 'FAIL — no new entries'}")
    print(f"data through {nb[-1]['date']} | eligible names: {len(scored)}")
    for m, sym, px in scored[:10]:
        print(f"  {sym:<12} 6mo-mom {m*100:+6.1f}%  close {px:,.1f}")
    return [s for _, s, _ in scored[:5]] if mkt_ok else []


def cmd_record(source, symbol, thesis):
    rows = load_ledger()
    if any(r["symbol"] == symbol and r["status"] in ("pending", "open") for r in rows):
        print(f"{symbol} already pending/open — skipped")
        return
    rows.append(dict(pick_date=str(date.today()), source=source, symbol=symbol, thesis=thesis,
                     status="pending", entry_date="", entry="", stop="", exit_date="", exit="",
                     ret_pct_net="", hold_days=""))
    save_ledger(rows)
    print(f"recorded {source} pick: {symbol}")


def cmd_settle():
    rows = load_ledger()
    for r in rows:
        if r["status"] not in ("pending", "open"):
            continue
        bars = fetch_daily(r["symbol"], "2025-07-01")
        if not bars:
            print(f"{r['symbol']}: no data")
            continue
        idx = {b["date"]: i for i, b in enumerate(bars)}
        if r["status"] == "pending":
            entry_i = next((i for i, b in enumerate(bars) if b["date"] > r["pick_date"]), None)
            if entry_i is None:
                print(f"{r['symbol']}: waiting for first bar after {r['pick_date']}")
                continue
            a = atr(bars[:entry_i + 1])
            if a is None:
                print(f"{r['symbol']}: insufficient history for ATR")
                continue
            r["entry_date"] = bars[entry_i]["date"]
            r["entry"] = f"{bars[entry_i]['o']:.2f}"
            r["stop"] = f"{bars[entry_i]['o'] - ATR_STOP * a:.2f}"
            r["status"] = "open"
        # walk forward from entry, ratchet the 20-day-low trail, check stop
        ei = idx[r["entry_date"]]
        stop = float(r["stop"])
        for i in range(ei + 1, len(bars)):
            lo20 = min(b["l"] for b in bars[max(0, i - TRAIL_N):i])
            stop = max(stop, lo20)
            if bars[i]["l"] <= stop:
                exit_px = min(bars[i]["o"], stop) if bars[i]["o"] < stop else stop
                gross = exit_px / float(r["entry"]) - 1
                r.update(status="closed", exit_date=bars[i]["date"], exit=f"{exit_px:.2f}",
                         ret_pct_net=f"{(gross - COST_RT) * 100:.2f}",
                         hold_days=str(i - ei))
                break
        else:
            r["stop"] = f"{stop:.2f}"
            mtm = bars[-1]["c"] / float(r["entry"]) - 1
            print(f"  OPEN {r['symbol']:<12} entry {r['entry']} stop {stop:.2f} "
                  f"mtm {(mtm - COST_RT) * 100:+.1f}% ({r['source']})")
        if r["status"] == "closed":
            print(f"  CLOSED {r['symbol']:<12} {r['ret_pct_net']}% in {r['hold_days']}d "
                  f"({r['source']})")
    save_ledger(rows)


def cmd_report():
    rows = load_ledger()
    for src in ("llm", "mech"):
        closed = [r for r in rows if r["source"] == src and r["status"] == "closed"]
        openp = [r for r in rows if r["source"] == src and r["status"] in ("open", "pending")]
        if closed:
            rets = [float(r["ret_pct_net"]) for r in closed]
            wins = sum(1 for x in rets if x > 0)
            print(f"{src}: closed n={len(closed)} win={100*wins/len(closed):.0f}% "
                  f"avg {sum(rets)/len(rets):+.2f}% total {sum(rets):+.2f}% | open {len(openp)}")
        else:
            print(f"{src}: no closed trades yet | open/pending {len(openp)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "mech":
        cmd_mech()
    elif cmd == "record":
        cmd_record(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
    elif cmd == "settle":
        cmd_settle()
    else:
        cmd_report()
