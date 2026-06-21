"""crypto_subhour.py — backtest the validated crypto strategy on SUB-HOUR timeframes (15m, 30m).

We only ever tested crypto on 1h. Equity & commodities both LOST on sub-hour (cost-in-R balloons).
This fetches fresh 15m + 30m data (ccxt, no login) for the 12 majors and runs the SAME validated
config (CryptoStrategy adx>=15, EMA200-slope>=0.5%, min_score 2.5, market entry, 3xATR stop, RR3,
crypto maker/taker costs) so the result is directly comparable to the 1h baseline.

Note on "smaller capital": PF / win% are CAPITAL-INVARIANT (crypto fees are %-based, sizing is
fractional) — a smaller account just scales the rupee P&L linearly. So the edge question = the
timeframe question, which is what this tests.
"""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccxt
import backtest
from crypto import CryptoStrategy

PAIRS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
         "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT", "DOT/USDT", "TRX/USDT"]
HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = 3000   # bars per pair per timeframe (15m~31d, 30m~62d)


def fetch_paged(ex, sym, tf, target):
    step = ex.parse_timeframe(tf) * 1000
    since = ex.milliseconds() - target * step
    out, seen = [], set()
    while len(out) < target:
        o = ex.fetch_ohlcv(sym, tf, since=since, limit=1000)
        if not o:
            break
        for r in o:
            if r[0] not in seen:
                seen.add(r[0]); out.append(r)
        since = o[-1][0] + step
        if len(o) < 1000:
            break
        time.sleep(ex.rateLimit / 1000)
    out.sort(key=lambda r: r[0])
    return out[:-1]   # drop in-progress bar


def save(rows, path):
    import datetime as dt
    recs = [{"date": dt.datetime.utcfromtimestamp(r[0] / 1000).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
             "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in rows]
    json.dump(recs, open(path, "w"))
    return len(recs)


def main():
    ex = ccxt.kucoin({"enableRateLimit": True})
    strat = CryptoStrategy(adx_min=15.0, slope_min=0.005, min_score=2.5)
    for tf in ("15m", "30m"):
        d = os.path.join(HERE, "data", "crypto", f"binance_sub_{tf}")
        os.makedirs(d, exist_ok=True)
        print(f"\n=== fetching {tf} ({TARGET} bars x {len(PAIRS)} pairs) ===")
        for p in PAIRS:
            try:
                rows = fetch_paged(ex, p, tf, TARGET)
                n = save(rows, os.path.join(d, f"{p.replace('/', '')}_{tf}.json"))
                print(f"  {p:10s} {n} bars")
            except Exception as e:
                print(f"  {p:10s} fetch error {type(e).__name__}: {e}")
        print(f"\n=== BACKTEST {tf} (validated config: adx15/slope0.5%/RR3, cost-adjusted) ===")
        backtest.run_dir(f"data/crypto/binance_sub_{tf}", tf, rr=3.0, strat=strat)


if __name__ == "__main__":
    main()
