"""commodity_scan.py — one-shot LIVE commodity (MCX) scan that RECORDS fired signals + paper trades.

Mirrors live_scan.py (crypto) but for MCX commodity futures, on the validated COMMODITY config:
base Price-Action engine, MARKET entry, stop 3.0xATR, RR 2.5, ~0.08% round-trip MCX cost.

Reads 60-min bars from data/commodities/live/<SYM>_60minute.json (refresh these before each run —
see the data-source note below), evaluates the LATEST CLOSED bar, appends any NEW fired signal to
results/commodity_scan_log.csv (de-duped by symbol+bar), opens/maintains paper positions, and writes
results/commodity_scan_status.txt (+ commodity_paper_*.{json,csv,txt}).

DATA SOURCE NOTE: unlike crypto (free ccxt feed), MCX 60-min data comes only from Kite, which needs a
daily login and (for unattended scripts) the PAID Kite Connect API. So the data files must be
refreshed by an authenticated pull before each scan; this script itself only consumes the local files.

    python commodity_scan.py
"""
import os, sys, csv, json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import features, paper
from strategy import PriceActionStrategy

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "commodities", "live")
RESULTS = os.path.join(HERE, "results")
os.makedirs(RESULTS, exist_ok=True)
LOG = os.path.join(RESULTS, "commodity_scan_log.csv")
STATUS = os.path.join(RESULTS, "commodity_scan_status.txt")
SEEN = os.path.join(RESULTS, "commodity_scan_seen.json")

SYMS = ["GOLD", "GOLDM", "SILVER", "CRUDEOIL", "NATURALGAS", "COPPER", "ALUMINIUM", "NICKEL"]

# --- retune the reused paper tracker to the validated COMMODITY config ---
paper.RR = 2.5
paper.SL_MULT = 3.0
paper.SLIP_ENTRY = 0.0005
paper.MAKER = 0.0004        # symmetric ~0.08% round-trip (MCX brokerage+STT+exch+GST+stamp)
paper.TAKER = 0.0004
paper.SLIP = 0.0
paper.FIXED_RISK = 1000.0
paper.OPEN_F = os.path.join(RESULTS, "commodity_paper_open.json")
paper.LEDGER = os.path.join(RESULTS, "commodity_paper_trades.csv")
paper.SUMMARY = os.path.join(RESULTS, "commodity_paper_summary.txt")

STRAT = PriceActionStrategy(min_score=2.5)


def load_df(sym):
    rows = json.load(open(os.path.join(DATA, f"{sym}_60minute.json")))
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return features.engineer(df)


def load_seen():
    if os.path.exists(SEEN):
        try:
            return set(json.load(open(SEEN)))
        except Exception:
            return set()
    return set()


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    seen = load_seen()
    fired, watch, bar_ts = [], [], None
    dfs = {}

    for sym in SYMS:
        path = os.path.join(DATA, f"{sym}_60minute.json")
        if not os.path.exists(path):
            print(f"  {sym}: no data file"); continue
        try:
            df = load_df(sym)
        except Exception as e:
            print(f"  {sym}: load error {e}"); continue
        dfs[sym] = df
        last = df.iloc[-1]
        bar_ts = str(df["date"].iloc[-1])
        sig = STRAT.evaluate(df)
        if sig:
            key = f"{sym}@{bar_ts}"
            row = {"scanned_at": now, "bar_ts": bar_ts, "symbol": sym, "side": sig["side"],
                   "entry": sig["entry"], "atr": sig["atr"], "score": sig["score"],
                   "reasons": json.dumps(sig["reasons"])}
            fired.append((sym, sig, key, row))
            continue
        c, ema, rsi = last["close"], last["ema200"], last["rsi14"]
        if pd.isna(ema) or pd.isna(rsi):
            continue
        d_res = (last["resistance"] - c) / c * 100
        d_sup = (c - last["support"]) / c * 100
        if c < ema and 40 <= rsi <= 60 and d_res <= 1.5:
            watch.append(f"SHORT {sym:11s} px {c:<10g} {d_res:.2f}% under res | RSI{rsi:.0f}")
        if c > ema and 40 <= rsi <= 60 and d_sup <= 1.5:
            watch.append(f"LONG  {sym:11s} px {c:<10g} {d_sup:.2f}% above sup | RSI{rsi:.0f}")

    # record NEW fired signals to the log
    new = [f for f in fired if f[2] not in seen]
    if new:
        write_header = not os.path.exists(LOG)
        with open(LOG, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(new[0][3].keys()))
            if write_header:
                w.writeheader()
            for _, _, key, row in new:
                w.writerow(row); seen.add(key)
        json.dump(sorted(seen), open(SEEN, "w"))

    # paper trades: open new, maintain/close existing
    openpos = paper.load_open()
    opened = []
    for sym, sig, key, _ in new:
        if sym in openpos:
            continue
        t = paper.open_trade(sym, sig, str(dfs[sym]["date"].iloc[-1]), now)
        if t:
            openpos[sym] = t; opened.append(sym)
    openpos, closed = paper.check_open(openpos, dfs)
    paper.append_ledger(closed)
    paper.save_open(openpos)
    paper_txt = paper.write_summary(openpos, dfs, now)

    lines = [f"COMMODITY SCAN heartbeat {now} | last closed 60m bar {bar_ts}",
             f"fired this scan: {len(fired)} (new logged: {len(new)}) | watchlist: {len(watch)}", ""]
    if fired:
        for sym, s, key, _ in fired:
            lines.append(f"  FIRED {s['side'].upper()} {sym} @ {s['entry']} score {s['score']}")
    else:
        lines.append("  no fired signals - FLAT")
    lines.append("")
    lines.append("WATCHLIST (near a level, awaiting trigger):")
    lines += [f"  {w}" for w in sorted(watch)] or ["  (empty)"]
    if opened:
        lines.append(f"\nPAPER: opened {len(opened)}: {', '.join(opened)}")
    if closed:
        lines.append("PAPER: closed " + ", ".join(f"{c['symbol']}={c['result']}(Rs{c['pnl_inr']})" for c in closed))
    text = "\n".join(lines)
    open(STATUS, "w", encoding="utf-8").write(text + "\n")
    print(text)
    print("\n" + paper_txt)


if __name__ == "__main__":
    main()
