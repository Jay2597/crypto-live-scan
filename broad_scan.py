"""broad_scan.py — one-off WIDE crypto scan for near-trigger candidates.

Same validated CryptoStrategy + identical watchlist criteria as live_scan.py, but run over
the TOP-N most liquid USDT spot pairs pulled live from the exchange (by 24h quote turnover)
instead of the fixed 17-major universe. Read-only: prints fired signals + the near-trigger
watchlist, ranked by distance-to-level. Does NOT touch the paper book or seen/log files.

    python broad_scan.py            # top 120 USDT pairs
    python broad_scan.py 200        # top 200
"""
import os, sys, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccxt, pandas as pd
import features
from crypto import CryptoStrategy

EXCHANGES = os.environ.get("SCAN_EXCHANGES", "kucoin,mexc,bybit,okx,binance").split(",")
TOP_N = int(sys.argv[1]) if len(sys.argv) > 1 else 150
MIN_QUOTE_VOL = float(os.environ.get("BROAD_MIN_QV", 500_000))  # skip pairs below this $/24h turnover

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
os.makedirs(RESULTS, exist_ok=True)
STATUS = os.path.join(RESULTS, "broad_scan_status.txt")

# stablecoins / wrapped / leveraged tokens to exclude (no real trend signal)
SKIP = {"USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDD", "PYUSD", "EURT", "BUSD", "USDP"}
SKIP_SUBSTR = ("UP/", "DOWN/", "3L/", "3S/", "5L/", "5S/", "BULL/", "BEAR/")

STRAT = CryptoStrategy(adx_min=15.0, slope_min=0.005, min_score=2.5)


def pick_exchange():
    last_err = None
    for name in EXCHANGES:
        name = name.strip()
        if not name or not hasattr(ccxt, name):
            continue
        try:
            ex = getattr(ccxt, name)({"enableRateLimit": True})
            ex.fetch_ohlcv("BTC/USDT", "1h", limit=3)
            print(f"[data source] using {name}")
            return ex, name
        except Exception as e:
            last_err = f"{name}: {type(e).__name__}"
            print(f"[data source] {name} unavailable ({type(e).__name__}), trying next")
    raise RuntimeError(f"no exchange reachable; last={last_err}")


def top_pairs(ex, n):
    """Return the n most liquid spot USDT pairs by 24h quote volume."""
    ex.load_markets()
    tickers = ex.fetch_tickers()
    rows = []
    for sym, t in tickers.items():
        if not sym.endswith("/USDT"):
            continue
        base = sym.split("/")[0]
        if base in SKIP or any(s in sym for s in SKIP_SUBSTR):
            continue
        m = ex.markets.get(sym, {})
        if m.get("spot") is False:
            continue
        qv = t.get("quoteVolume") or 0
        if qv < MIN_QUOTE_VOL:
            continue
        rows.append((sym, qv))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in rows[:n]]


def get_df(ex, sym):
    o = ex.fetch_ohlcv(sym, "1h", limit=520)
    df = pd.DataFrame(o, columns=["ms", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["ms"], unit="ms", utc=True)
    df = df.drop(columns=["ms"]).iloc[:-1].reset_index(drop=True)
    return features.engineer(df)


def main():
    ex, ex_name = pick_exchange()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pairs = top_pairs(ex, TOP_N)
    print(f"[universe] {len(pairs)} liquid USDT pairs (>${MIN_QUOTE_VOL/1e6:.1f}M/24h), scanning...\n")

    fired, watch, bar_ts, errs = [], [], None, 0
    for i, sym in enumerate(pairs):
        try:
            df = get_df(ex, sym)
        except Exception:
            errs += 1
            continue
        if len(df) < 260:
            continue
        last = df.iloc[-1]
        bar_ts = str(df["date"].iloc[-1])
        sig = STRAT.evaluate(df)
        if sig:
            fired.append((sym, sig))
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
            watch.append((sym, d_res, f"SHORT {sym:14s} px {c:<12g} {d_res:.2f}% under res | RSI{rsi:.0f} ADX{adx:.0f} slope{slope:+.2f}%"))
        if c > ema and slope >= 0.5 and adx >= 15 and 40 <= rsi <= 60 and d_sup <= 1.5:
            watch.append((sym, d_sup, f"LONG  {sym:14s} px {c:<12g} {d_sup:.2f}% above sup | RSI{rsi:.0f} ADX{adx:.0f} slope{slope:+.2f}%"))
        time.sleep(0.03)

    # current live_scan universe — flag near-trigger names that are NOT already tracked,
    # since the whole point of the daily sweep is to surface new candidates to add.
    try:
        from live_scan import PAIRS as TRACKED
    except Exception:
        TRACKED = []
    tracked = set(TRACKED)

    lines = [f"BROAD SWEEP {now} | source {ex_name} | last closed 1h bar {bar_ts}",
             f"scanned {len(pairs)} pairs ({errs} fetch errors) | fired {len(fired)} | near-trigger {len(watch)}",
             ""]
    if fired:
        lines.append("FIRED SIGNALS:")
        for sym, s in fired:
            tag = "" if sym in tracked else "  <-- NOT in live_scan"
            lines.append(f"  FIRED {s['side'].upper():5s} {sym:14s} @ {s['entry']} score {s['score']}{tag}")
    else:
        lines.append("FIRED SIGNALS: none")
    lines.append("")
    lines.append("NEAR-TRIGGER WATCHLIST (sorted by distance to level; <-- = candidate to add):")
    if watch:
        for sym, _, line in sorted(watch, key=lambda r: r[1]):
            tag = "" if sym in tracked else "   <-- NOT in live_scan"
            lines.append(f"  {line}{tag}")
    else:
        lines.append("  (empty)")

    text = "\n".join(lines)
    open(STATUS, "w", encoding="utf-8").write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
