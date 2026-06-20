"""crypto.py — CRYPTO strategy runner (Binance USDT pairs, via ccxt-pulled data).

Currently runs the SAME validated Price-Action-First engine as commodities (it was confirmed
positive on crypto in- and out-of-sample: PF ~1.1-1.5 across up/down windows and 15 fresh
pairs at realistic cost). This module is the home for any crypto-SPECIFIC divergence later
(24/7 tape, exchange maker/taker tiers, funding, pair-specific tuning) — keep crypto changes
here so the commodity strategy stays untouched.

Execution config (market entry, stop 3.0xATR, RR 2.5, asymmetric maker/taker costs) lives in
backtest.py; the taker rate (0.10%/side) is already crypto-realistic.

    python crypto.py                       # default: 12 majors, 10-month window @ 1h
    python crypto.py --dataset oos15       # 15 fresh pairs (Jan-Jun 2026)
    python crypto.py --dataset recent3mo
    python crypto.py --csv results/crypto.csv --plot results/crypto.png

Data is fetched with ccxt (no login). To refresh/extend, re-pull OHLCV into
data/crypto/<name>/<SYMBOL>_1h.json (format: [{date,open,high,low,close,volume}, ...]).
"""
import argparse
import pandas as pd
import backtest
from strategy import PriceActionStrategy


class CryptoStrategy(PriceActionStrategy):
    """Price-Action engine + CRYPTO-specific gates (all off by default; tunable levers).

    TREND-STRENGTH gate (this is a trend-aligned pullback strategy, and crypto's edge dies in
    chop — so only take pullbacks inside a STRONG trend):
      * adx_min     : require ADX14 >= adx_min (0 = off).
      * slope_min   : require EMA200 slope over `slope_bars` to be >= slope_min in the trade
                      direction (rising for longs, falling for shorts); fraction, 0 = off.

    VOLUME gate (vol_mult): require trigger volume > vol_mult x 20-bar avg. TESTED 2026-06-19,
    it HURTS (this is mean-reversion into a level -> fading volume, not spikes). DEFAULT OFF.
    """

    def __init__(self, vol_mult: float = 0.0, adx_min: float = 0.0,
                 slope_bars: int = 50, slope_min: float = 0.0, **kw):
        super().__init__(**kw)
        self.vol_mult = vol_mult
        self.adx_min = adx_min
        self.slope_bars = slope_bars
        self.slope_min = slope_min

    def evaluate(self, df: pd.DataFrame):
        sig = super().evaluate(df)
        if not sig:
            return None
        row = df.iloc[-1]
        # --- volume gate (default off) ---
        if self.vol_mult > 0:
            vavg = row["vol_sma20"]
            if pd.isna(vavg) or vavg <= 0 or row["volume"] < self.vol_mult * vavg:
                return None
            sig["reasons"]["vol_x"] = round(float(row["volume"]) / float(vavg), 2)
        # --- trend-strength: ADX ---
        if self.adx_min > 0:
            adx = row["adx14"]
            if pd.isna(adx) or adx < self.adx_min:
                return None
            sig["reasons"]["adx"] = round(float(adx), 1)
        # --- trend-strength: EMA200 slope in the trade direction ---
        if self.slope_min > 0 and self.slope_bars > 0:
            if len(df) <= self.slope_bars:
                return None
            e_now = row["ema200"]
            e_prev = df["ema200"].iloc[-1 - self.slope_bars]
            if pd.isna(e_prev) or e_prev <= 0:
                return None
            slope = (e_now - e_prev) / e_prev
            if sig["side"] == "long" and slope < self.slope_min:
                return None
            if sig["side"] == "short" and slope > -self.slope_min:
                return None
            sig["reasons"]["ema_slope%"] = round(slope * 100, 2)
        return sig


DATASETS = {
    "hist10mo":  ("data/crypto/binance_hist10mo_1h",  "1h"),  # 12 majors, 2025-04..2026-03
    "recent3mo": ("data/crypto/binance_recent3mo_1h", "1h"),  # 12 majors, 2026-03..06 (down mkt)
    "oos15":     ("data/crypto/binance_oos15_1h",     "1h"),  # 15 fresh pairs, 2026-01..06
    "top6mo":    ("data/crypto/binance_top6mo_1h",     "1h"),  # 16 top pairs, full 6mo (Dec25-Jun26)
}


def main():
    ap = argparse.ArgumentParser(description="Run the crypto strategy on Binance data.")
    ap.add_argument("--dataset", choices=list(DATASETS), default="hist10mo")
    ap.add_argument("--interval", default=None)
    ap.add_argument("--vol-mult", type=float, default=0.0,
                    help="trigger volume must exceed this x 20-bar avg (0 = off; tested, doesn't help)")
    # validated trend-strength default: ADX15 + EMA200-slope 0.5% (best, most OOS-robust combo)
    ap.add_argument("--adx-min", type=float, default=15.0,
                    help="trend-strength gate: min ADX14 (DEFAULT 15; >20 over-filters)")
    ap.add_argument("--slope-min", type=float, default=0.005,
                    help="trend-strength gate: min EMA200 slope frac over 50 bars (DEFAULT 0.005)")
    # let-winners-run: RR 3.0 is the validated crypto default (modest, robust; RR>=4 breaks OOS,
    # ATR trailing was tested and is worse everywhere -> trail off).
    ap.add_argument("--rr", type=float, default=3.0, help="reward:risk (crypto default 3.0)")
    ap.add_argument("--trail-mult", type=float, default=0.0,
                    help="ATR trailing stop (0=off; tested, hurts -> keep off)")
    ap.add_argument("--min-dvol", type=float, default=0.0,
                    help="universe filter: skip pairs below this avg $-turnover/hr "
                         "(0=off; recommended ~1e6 for LIVE = liquid majors, higher PF/less slippage)")
    ap.add_argument("--min-score", type=float, default=2.5)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--plot", default=None)
    a = ap.parse_args()
    data_dir, interval = DATASETS[a.dataset]
    strat = CryptoStrategy(vol_mult=a.vol_mult, adx_min=a.adx_min, slope_min=a.slope_min,
                           min_score=a.min_score)
    print(f"[CRYPTO] dataset={a.dataset}  adx_min={a.adx_min} slope_min={a.slope_min} "
          f"rr={a.rr} trail={a.trail_mult}")
    backtest.run_dir(data_dir, a.interval or interval, rr=a.rr, csv=a.csv, plot=a.plot,
                     strat=strat, trail_mult=a.trail_mult, min_dvol=a.min_dvol)


if __name__ == "__main__":
    main()
