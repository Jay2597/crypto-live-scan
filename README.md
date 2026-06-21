# Price-Action-First trading strategy

A long+short intraday strategy with a validated execution layer. One shared engine, two
asset runners (commodities, crypto). Indian equity is kept as data only (no edge found).

## Strategy (what each bar must pass)
1. **Setup (level):** price within 0.5% of the 20-bar rolling support (long) / resistance (short).
2. **Trigger (candles):** a candlestick pattern (`patterns.py` 10-detector lib) OR a hand-coded
   pin bar (lower wick >= 2x body) / engulfing.
3. **Confirm (indicators):** long: close > EMA200 and RSI14 in 40-60; short: the mirror.
4. **Score:** weighted strength score must clear `min_score` (2.5).
5. **Execution (validated in- & out-of-sample):** MARKET entry at the bar close; stop = 3.0xATR;
   take-profit = 2.5R (RR 2.5:1); one position at a time; conservative stop-before-target;
   fixed-Rs risk, continuous risk-normalized sizing; realistic asymmetric costs
   (entry taker, TP maker, stop taker+slip, +0.05% entry slippage).

Best results: **commodities GOLDM @ 60-min** (PF ~1.6, 6-month) and **crypto** (pooled PF ~1.35
with the trend-strength gate, Sharpe ~1.9, OOS-confirmed). 60-min is the best commodity
timeframe (15-min loses). Equity has no cost-surviving edge.

## Code (minimal)
| file | role |
|---|---|
| `features.py` | load cached OHLCV + price-action & indicator features (shared) |
| `patterns.py` | candlestick pattern library (shared) |
| `risk.py` | ATR-based stop/target levels (shared) |
| `strategy.py` | the Price-Action-First signal engine (shared) |
| `backtest.py` | canonical backtest + `run_dir()`; trade-log CSV & equity-curve PNG |
| `commodities.py` | commodity runner (MCX) |
| `crypto.py` | crypto runner (Binance); validated TREND-STRENGTH gate (ADX15 + EMA200-slope 0.5%) + RR 3.0. Off-by-default levers (tested): volume gate & ATR trailing (hurt), `--min-dvol` liquidity filter (marginal; use ~1e6 for live execution hygiene only) |

## Run
```
python commodities.py                      # default GOLDM+SILVER 6mo @60min
python commodities.py --dataset mcx_majors
python crypto.py                           # default 12 majors 10mo @1h
python crypto.py --dataset oos15 --csv results/crypto.csv --plot results/crypto.png
python backtest.py --data-dir data/stocks/nse_2026_6mo_60m --interval 60minute  # ad-hoc retest
```

## Data (raw, kept for retesting)
```
data/
  stocks/        nse_2024H1_60m, nse_2024H2_60m, nse_2025H2_60m, nse_2026_6mo_60m
  crypto/        binance_hist10mo_1h, binance_recent3mo_1h, binance_oos15_1h
  commodities/   mcx_majors_3mo_60m, mcx_extra_3mo_60m, mcx_daily_3y, mcx_6mo_multitf
```
File format: `<SYMBOL>_<interval>.json` = `[{date,open,high,low,close,volume}, ...]`.
`results/` holds generated trade logs and equity curves.
