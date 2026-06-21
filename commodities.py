"""commodities.py — COMMODITY strategy runner (MCX).

Strategy logic = the shared, validated Price-Action-First engine (features -> strategy ->
risk -> backtest). Execution config is the one validated in/out-of-sample: MARKET entry,
stop 3.0xATR, RR 2.5, realistic asymmetric costs (see backtest.py).

Best result to date: GOLDM @ 60-minute (6-month window). 60-min is the best timeframe;
15-min loses. Avoid SILVER (barely triggers) and CRUDEOIL (never sets up) here.

    python commodities.py                         # default: 6-month GOLDM+SILVER @ 60-min
    python commodities.py --dataset mcx_majors    # GOLD/SILVER/CRUDE/NATGAS/COPPER (3-mo)
    python commodities.py --interval 30minute     # other timeframes (where data exists)
    python commodities.py --csv results/comm.csv --plot results/comm.png
"""
import argparse
import backtest

DATASETS = {
    "mcx_6mo":    ("data/commodities/mcx_6mo_multitf",    "60minute"),  # GOLDM+SILVER, 6mo, 15/30/60m
    "mcx_majors": ("data/commodities/mcx_majors_3mo_60m", "60minute"),  # GOLD/SILVER/CRUDE/NATGAS/COPPER
    "mcx_extra":  ("data/commodities/mcx_extra_3mo_60m",  "60minute"),  # ALUMINIUM/NICKEL/GOLDM/CRUDEOILM
    "mcx_daily":  ("data/commodities/mcx_daily_3y",       "day"),       # 3.5y daily (note: ~0 signals)
}


def main():
    ap = argparse.ArgumentParser(description="Run the commodity strategy on MCX data.")
    ap.add_argument("--dataset", choices=list(DATASETS), default="mcx_6mo")
    ap.add_argument("--interval", default=None, help="override interval (e.g. 15minute/30minute)")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--plot", default=None)
    a = ap.parse_args()
    data_dir, interval = DATASETS[a.dataset]
    print(f"[COMMODITIES] dataset={a.dataset}")
    backtest.run_dir(data_dir, a.interval or interval, csv=a.csv, plot=a.plot)


if __name__ == "__main__":
    main()
