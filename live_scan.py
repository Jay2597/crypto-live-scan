"""live_scan.py — one-shot LIVE crypto scan that RECORDS fired signals to a log file.

Runs the validated CryptoStrategy (ADX>=15, EMA200-slope>=0.5%, min_score 2.5, RR3) on the
latest CLOSED 1h Binance bar for a universe of liquid majors. Designed to be called every
~15 min by a scheduler/loop: it appends any NEW fired signal to results/live_scan_log.csv
(de-duplicated by symbol+bar-timestamp so re-runs within the same hourly bar don't double-log),
and always writes the current watchlist + heartbeat to results/live_scan_status.txt.

    python live_scan.py
"""
import os, sys, csv, json, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccxt, pandas as pd
import features
import paper
from crypto import CryptoStrategy

# Data source: try exchanges in order, use the first that serves OHLCV. Binance is last
# because it returns HTTP 451 from US-hosted runners (GitHub Actions/Azure). KuCoin/MEXC/Bybit
# all serve 17/17 of our pairs and are reachable from most CI regions. Override with env
# SCAN_EXCHANGES="bybit,okx" if needed.
EXCHANGES = os.environ.get("SCAN_EXCHANGES", "kucoin,mexc,bybit,okx,binance").split(",")

# 12 majors (hist10mo universe) UNION the 16 top-liquidity pairs (top6mo universe).
# Extra from top16: APT, INJ, NEAR, RUNE, SUI. DOT kept from the majors. = 17 unique pairs.
# + UNI, AERO added 2026-06-22 from broad_scan.py (liquid, strong-trend near-trigger setups). = 19.
PAIRS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
         "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT", "DOT/USDT", "TRX/USDT",
         "APT/USDT", "INJ/USDT", "NEAR/USDT", "RUNE/USDT", "SUI/USDT",
         "UNI/USDT", "AERO/USDT"]

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
os.makedirs(RESULTS, exist_ok=True)
LOG = os.path.join(RESULTS, "live_scan_log.csv")
STATUS = os.path.join(RESULTS, "live_scan_status.txt")
SEEN = os.path.join(RESULTS, "live_scan_seen.json")

STRAT = CryptoStrategy(adx_min=15.0, slope_min=0.005, min_score=2.5)


def pick_exchange():
    """Return the first exchange in EXCHANGES that serves a BTC/USDT 1h candle."""
    last_err = None
    for name in EXCHANGES:
        name = name.strip()
        if not name or not hasattr(ccxt, name):
            continue
        try:
            ex = getattr(ccxt, name)({"enableRateLimit": True})
            ex.fetch_ohlcv("BTC/USDT", "1h", limit=3)  # reachability probe
            print(f"[data source] using {name}")
            return ex, name
        except Exception as e:
            last_err = f"{name}: {type(e).__name__}"
            print(f"[data source] {name} unavailable ({type(e).__name__}), trying next")
    raise RuntimeError(f"no exchange reachable (tried {EXCHANGES}); last={last_err}")


def get_df(ex, sym):
    o = ex.fetch_ohlcv(sym, "1h", limit=520)
    df = pd.DataFrame(o, columns=["ms", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["ms"], unit="ms", utc=True)
    df = df.drop(columns=["ms"]).iloc[:-1].reset_index(drop=True)  # drop in-progress bar
    return features.engineer(df)


def load_seen():
    if os.path.exists(SEEN):
        try:
            return set(json.load(open(SEEN)))
        except Exception:
            return set()
    return set()


def main():
    ex, ex_name = pick_exchange()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    seen = load_seen()
    fired, watch, bar_ts = [], [], None
    dfs = {}

    for sym in PAIRS:
        try:
            df = get_df(ex, sym)
        except Exception as e:
            print(f"  {sym}: fetch error {e}")
            continue
        dfs[sym] = df
        last = df.iloc[-1]
        bar_ts = str(df["date"].iloc[-1])
        sig = STRAT.evaluate(df)
        if sig:
            key = f"{sym}@{bar_ts}"
            sig_row = {"scanned_at": now, "source": ex_name, "bar_ts": bar_ts, "symbol": sym,
                       "side": sig["side"], "entry": sig["entry"], "atr": sig["atr"],
                       "score": sig["score"], "reasons": json.dumps(sig["reasons"])}
            fired.append((sym, sig, key, sig_row))
            continue
        c, ema, rsi = last["close"], last["ema200"], last["rsi14"]
        if pd.isna(ema) or pd.isna(rsi):
            continue
        e_prev = df["ema200"].iloc[-1 - 50]
        slope = (ema - e_prev) / e_prev * 100 if e_prev and not pd.isna(e_prev) else float("nan")
        adx = last["adx14"]
        d_res = (last["resistance"] - c) / c * 100
        d_sup = (c - last["support"]) / c * 100
        if c < ema and slope <= -0.5 and adx >= 15 and 40 <= rsi <= 60 and d_res <= 1.5:
            watch.append(f"SHORT {sym:10s} px {c:<11g} {d_res:.2f}% under res | RSI{rsi:.0f} ADX{adx:.0f} slope{slope:+.2f}%")
        if c > ema and slope >= 0.5 and adx >= 15 and 40 <= rsi <= 60 and d_sup <= 1.5:
            watch.append(f"LONG  {sym:10s} px {c:<11g} {d_sup:.2f}% above sup | RSI{rsi:.0f} ADX{adx:.0f} slope{slope:+.2f}%")
        time.sleep(0.1)

    # record NEW fired signals
    new = [f for f in fired if f[2] not in seen]
    if new:
        write_header = not os.path.exists(LOG)
        with open(LOG, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(new[0][3].keys()))
            if write_header:
                w.writeheader()
            for _, _, key, row in new:
                w.writerow(row)
                seen.add(key)
        json.dump(sorted(seen), open(SEEN, "w"))

    # --- PAPER TRADES: open new positions, close ones that hit stop/target ---
    openpos = paper.load_open()
    opened = []
    for sym, sig, key, _ in new:                 # only freshly-fired signals
        if sym in openpos:                       # one paper position per symbol
            continue
        t = paper.open_trade(sym, sig, str(dfs[sym]["date"].iloc[-1]), now)
        if t:
            openpos[sym] = t
            opened.append(sym)
    openpos, closed = paper.check_open(openpos, dfs)
    paper.append_ledger(closed)
    paper.save_open(openpos)
    paper_txt = paper.write_summary(openpos, dfs, now)

    # heartbeat / current picture
    lines = [f"LIVE SCAN heartbeat {now} | source {ex_name} | last closed 1h bar {bar_ts}",
             f"fired this scan: {len(fired)} (new logged: {len(new)}) | watchlist: {len(watch)}",
             ""]
    if fired:
        for sym, s, key, _ in fired:
            lines.append(f"  FIRED {s['side'].upper()} {sym} @ {s['entry']} score {s['score']}")
    else:
        lines.append("  no fired signals — FLAT")
    lines.append("")
    lines.append("WATCHLIST:")
    lines += [f"  {w}" for w in sorted(watch)] or ["  (empty)"]
    if opened:
        lines.append("")
        lines.append(f"PAPER: opened {len(opened)} position(s): {', '.join(opened)}")
    if closed:
        lines.append(f"PAPER: closed {len(closed)} position(s): " +
                     ", ".join(f"{c['symbol']}={c['result']}(Rs{c['pnl_inr']})" for c in closed))
    text = "\n".join(lines)
    open(STATUS, "w", encoding="utf-8").write(text + "\n")
    print(text)
    print("\n" + paper_txt)
    if new:
        print(f">>> {len(new)} NEW signal(s) appended to {LOG}")


if __name__ == "__main__":
    main()
