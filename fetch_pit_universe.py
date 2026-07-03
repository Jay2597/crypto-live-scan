"""fetch_pit_universe.py — build the POINT-IN-TIME midcap universe for the survivorship-free
swing-momentum test.

Universe = Nifty Midcap 150 constituents as archived on 2019-02-01 (Wayback Machine snapshot of
niftyindices.com ind_niftymidcap150list.csv). The list PREDATES the 2021-2026 backtest window,
so it contains zero hindsight: every later loser/faller is included. Residual survivorship bias
= names delisted/merged since 2019 that yfinance can no longer serve; these are LOGGED so the
bias is quantified, not hidden.

Data: yfinance daily OHLC (auto-adjusted), 2020-06-01 -> today, saved per symbol to
data/stocks/swing_pit/<SYM>_day.json in the swing_momentum.py format
[{date, open, high, low, close}, ...]. NIFTY = ^NSEI.

Run: python fetch_pit_universe.py <constituents.csv>
"""
import csv
import json
import os
import sys
import time

import yfinance as yf

OUT_DIR = os.path.join("data", "stocks", "swing_pit")
START = "2020-06-01"  # ~150 trading days SMA200 warmup before the 2021 window start

# Known NSE symbol changes since Feb-2019 (old -> current yahoo symbol, no hindsight involved --
# pure identifier renames/mergers; mergers map to the surviving listed entity only when the
# holder would have received its shares).
RENAMES = {
    "MOTHERSUMI": "MOTHERSON",     # Motherson Sumi renamed Samvardhana Motherson
    "CADILAHC": "ZYDUSLIFE",       # Cadila Healthcare renamed Zydus Lifesciences
    "MINDTREE": "LTIM",            # Mindtree merged into LTIMindtree (share swap)
    "L&TFH": "LTF",                # L&T Finance Holdings renamed LTF
    "SRTRANSFIN": "SHRIRAMFIN",    # Shriram Transport renamed Shriram Finance
    "TATAGLOBAL": "TATACONSUM",    # Tata Global Beverages renamed Tata Consumer
    "CROMPTON": "CROMPTON",
}


def load_symbols(csv_path: str) -> list[str]:
    syms = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sym = (row.get("Symbol") or "").strip()
            if sym:
                syms.append(RENAMES.get(sym, sym))
    return syms


def save_bars(sym: str, df) -> int:
    bars = []
    for ts, row in df.iterrows():
        if any(row[k] != row[k] for k in ("Open", "High", "Low", "Close")):  # NaN guard
            continue
        bars.append(dict(date=str(ts.date()), open=float(row["Open"]), high=float(row["High"]),
                         low=float(row["Low"]), close=float(row["Close"])))
    if len(bars) < 300:  # not enough history to ever clear SMA200 warmup
        return 0
    with open(os.path.join(OUT_DIR, f"{sym}_day.json"), "w") as f:
        json.dump(bars, f)
    return len(bars)


def main() -> None:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "midcap150_2019.csv"
    os.makedirs(OUT_DIR, exist_ok=True)
    symbols = load_symbols(csv_path)
    print(f"universe: {len(symbols)} symbols from {csv_path}")

    ok, failed = [], []
    for i, sym in enumerate(symbols):
        try:
            df = yf.download(f"{sym}.NS", start=START, progress=False, auto_adjust=True,
                             multi_level_index=False)
            n = save_bars(sym, df) if df is not None and len(df) else 0
            if n:
                ok.append(sym)
                print(f"  [{i+1:3}/{len(symbols)}] {sym}: {n} bars")
            else:
                failed.append(sym)
                print(f"  [{i+1:3}/{len(symbols)}] {sym}: NO DATA")
        except Exception as e:  # noqa: BLE001 - log and continue, report at end
            failed.append(sym)
            print(f"  [{i+1:3}/{len(symbols)}] {sym}: ERROR {e}")
        time.sleep(0.3)

    # NIFTY benchmark/market filter
    ndf = yf.download("^NSEI", start=START, progress=False, auto_adjust=True,
                      multi_level_index=False)
    print(f"  NIFTY: {save_bars('NIFTY', ndf)} bars")

    with open(os.path.join(OUT_DIR, "_universe_report.json"), "w") as f:
        json.dump(dict(source=csv_path, fetched=ok, failed=failed,
                       residual_bias_note="failed = delisted/merged/renamed-unmapped since "
                       "Feb-2019; their absence understates losses (residual survivorship bias)"),
                  f, indent=1)
    print(f"\nDONE: {len(ok)} fetched, {len(failed)} FAILED -> {failed}")


if __name__ == "__main__":
    main()
