"""backtest.py — CANONICAL backtest of the Price-Action-First strategy.

Execution model validated 2026-06-19 (in-sample + OOS + fresh crypto, see memory):
  * MARKET entry at the signal bar's close (taker) — limit entries miss ~40% of fills, and the
    missed ones are disproportionately winners (adverse selection), so we take the taker hit.
  * Stop = 3.0 x ATR, Take-profit = 2.5R (RR 2.5:1). The wide stop makes each R large, so the
    fixed %-of-notional cost is a small fraction of every R (cost-in-R).
  * Asymmetric realistic costs: entry = taker; TP exit = maker (resting limit); SL exit =
    taker + slippage (stop becomes a market order). Optional entry slippage.
One position at a time; on each forward bar the stop is checked before the target (conservative).
Open positions at series end are marked-to-last-close. Fixed-INR risk sizing.

    python backtest.py --data-dir data_mcx --interval 60minute [--slip 0.0005]
                       [--csv trade_log.csv] [--plot equity_curve.png]
"""
from __future__ import annotations
import argparse
import csv
import glob
import math
import os

import features
import risk
from strategy import PriceActionStrategy

# ---- canonical, validated execution parameters ----
WARMUP = 205
SL_MULT = 3.0          # stop = 3.0 x ATR
RR = 2.5               # take-profit = 2.5 x risk
MAKER = 0.0002         # 0.02%/side  (resting limit / take-profit)
TAKER = 0.0010         # 0.10%/side  (market: entry + stop-out)
SLIP = 0.0005          # extra slippage on the stop (market) exit
DEFAULT_SLIP_ENTRY = 0.0005   # realistic liquid-instrument entry slippage


def backtest_symbol(symbol, interval, strat, fixed_risk=1000.0,
                    sl_mult=SL_MULT, rr=RR, slip_entry=DEFAULT_SLIP_ENTRY, trail_mult=0.0):
    df = features.engineer(features.load_cached(symbol, interval))
    n = len(df)
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    T = df["timestamp"].values
    trades, busy = [], -1
    for k in range(WARMUP, n - 1):
        if k <= busy:
            continue
        sig = strat.evaluate(df.iloc[:k + 1])
        if not sig:
            continue
        side, atr = sig["side"], sig["atr"]
        # market entry at close, with entry slippage (buy higher / sell lower)
        entry = C[k] * (1 + slip_entry) if side == "long" else C[k] * (1 - slip_entry)
        lv = risk.compute_levels(entry, atr, side, sl_mult=sl_mult, tp_mult=sl_mult * rr)
        ru = lv.risk_per_unit
        if ru <= 0:
            continue
        ex_idx, ex_px, res = n - 1, C[-1], "open"
        if trail_mult > 0:
            # LET-WINNERS-RUN: trail the stop by trail_mult*ATR from the best price; no fixed TP.
            # No look-ahead: test the stop set by prior bars, then ratchet with this bar's extreme.
            stop, peak = lv.stop_loss, entry
            for j in range(k + 1, n):
                if side == "long":
                    if L[j] <= stop:
                        ex_idx, ex_px = j, stop; break
                    if H[j] > peak:
                        peak = H[j]; stop = max(stop, peak - trail_mult * atr)
                else:
                    if H[j] >= stop:
                        ex_idx, ex_px = j, stop; break
                    if L[j] < peak:
                        peak = L[j]; stop = min(stop, peak + trail_mult * atr)
            res = "win" if ((ex_px - entry) if side == "long" else (entry - ex_px)) > 0 else "loss"
        else:
            for j in range(k + 1, n):
                if side == "long":
                    if L[j] <= lv.stop_loss:
                        ex_idx, ex_px, res = j, lv.stop_loss, "loss"; break
                    if H[j] >= lv.take_profit:
                        ex_idx, ex_px, res = j, lv.take_profit, "win"; break
                else:
                    if H[j] >= lv.stop_loss:
                        ex_idx, ex_px, res = j, lv.stop_loss, "loss"; break
                    if L[j] <= lv.take_profit:
                        ex_idx, ex_px, res = j, lv.take_profit, "win"; break
        busy = ex_idx
        # Continuous RISK-NORMALIZED sizing: every trade risks exactly `fixed_risk`, so no
        # high-value contract (e.g. SILVER) gets floored to an oversized 1-lot position. This
        # only re-weights P&L; it does NOT touch signals/levels/trade selection. Real whole-lot
        # tradeability (needs contract multipliers) is a separate capital-adequacy question.
        qty = fixed_risk / ru
        directional = (ex_px - entry) if side == "long" else (entry - ex_px)
        gross = directional * qty
        # fixed: target=limit(maker)/stop=taker. trailing: every exit is a stop-out (taker).
        x_rate = MAKER if (res == "win" and trail_mult <= 0) else (TAKER + SLIP)
        cost = TAKER * entry * qty + x_rate * ex_px * qty      # entry always taker (market)
        pnl_inr = gross - cost
        trades.append({
            "entry_dt": str(T[k])[:16].replace("T", " "),
            "exit_dt": str(T[ex_idx])[:16].replace("T", " "),
            "symbol": symbol, "side": side, "result": res,
            "entry": round(entry, 2), "sl": round(lv.stop_loss, 2),
            "tp": round(lv.take_profit, 2), "exit": round(ex_px, 2),
            "qty": round(qty, 4), "bars": ex_idx - k,
            "pnl_r": round(directional / ru - cost / (ru * qty), 3),
            "pnl_inr": round(pnl_inr, 2),
        })
    return trades


def summarize(trades):
    n = len(trades)
    if not n:
        return dict(trades=0, win=0, avg_r=0, total_r=0, total_inr=0, pf=0, sharpe=0, max_dd=0)
    rs = [t["pnl_r"] for t in trades]
    dec = sum(1 for t in trades if t["result"] in ("win", "loss"))
    w = sum(1 for t in trades if t["result"] == "win")
    gp = sum(t["pnl_inr"] for t in trades if t["pnl_inr"] > 0)
    gl = -sum(t["pnl_inr"] for t in trades if t["pnl_inr"] < 0)
    mean = sum(rs) / n
    std = math.sqrt(sum((r - mean) ** 2 for r in rs) / n)
    # max drawdown on the (exit-time-ordered) equity curve
    eq, peak, mdd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x["exit_dt"]):
        eq += t["pnl_inr"]; peak = max(peak, eq); mdd = max(mdd, peak - eq)
    return dict(trades=n, win=round(100 * w / dec, 1) if dec else 0.0, avg_r=round(mean, 3),
                total_r=round(sum(rs), 2), total_inr=round(sum(t["pnl_inr"] for t in trades), 2),
                pf=round(gp / gl, 2) if gl else float("inf"),
                sharpe=round(mean / std * math.sqrt(n), 2) if std else 0.0,
                max_dd=round(mdd, 2))


def write_csv(trades, path):
    cols = ["entry_dt", "exit_dt", "symbol", "side", "result", "entry", "sl", "tp",
            "exit", "qty", "bars", "pnl_r", "pnl_inr"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols); wr.writeheader()
        for t in sorted(trades, key=lambda x: x["exit_dt"]):
            wr.writerow(t)


def write_plot(trades, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"(plot skipped — matplotlib unavailable: {e})"); return False
    ts = sorted(trades, key=lambda x: x["exit_dt"])
    eq, cum, dd = [], 0.0, []
    peak = 0.0
    for t in ts:
        cum += t["pnl_inr"]; eq.append(cum); peak = max(peak, cum); dd.append(cum - peak)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(range(len(eq)), eq, color="#1a7f37", lw=1.5)
    ax1.axhline(0, color="gray", lw=0.8)
    ax1.set_title("Equity curve — market entry / stop 3.0xATR / RR 2.5 (realistic costs)")
    ax1.set_ylabel("Cumulative P&L (Rs)"); ax1.grid(alpha=0.3)
    ax2.fill_between(range(len(dd)), dd, 0, color="#cf222e", alpha=0.5)
    ax2.set_ylabel("Drawdown (Rs)"); ax2.set_xlabel("trade #"); ax2.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)
    return True


def run_dir(data_dir, interval="60minute", min_score=2.5, risk=1000.0,
            sl_mult=SL_MULT, rr=RR, slip=DEFAULT_SLIP_ENTRY, csv=None, plot=None, strat=None,
            trail_mult=0.0, min_dvol=0.0):
    """Run the canonical strategy over every *_<interval>.json in data_dir. Returns the
    pooled trade list. Shared by the commodities.py and crypto.py asset runners. Pass a custom
    `strat` (e.g. crypto's volume-gated subclass) to override the default engine.
    min_dvol: universe filter — skip symbols whose avg bar turnover (close*volume) is below it."""
    features.DATA_DIR = os.path.abspath(data_dir)
    if strat is None:
        strat = PriceActionStrategy(min_score=min_score)
    files = sorted(glob.glob(os.path.join(features.DATA_DIR, f"*_{interval}.json")))
    if min_dvol > 0:
        kept = []
        for fp in files:
            df0 = features.load_cached(os.path.basename(fp)[:-len(f"_{interval}.json")], interval)
            if (df0["close"] * df0["volume"]).mean() >= min_dvol:
                kept.append(fp)
        print(f"[universe filter] {len(kept)}/{len(files)} symbols pass min turnover Rs{min_dvol:,.0f}/bar")
        files = kept
    print(f"{os.path.basename(features.DATA_DIR)} {interval} | market entry, stop {sl_mult}xATR, "
          f"RR {rr} | entry-slip {slip*100:.2f}% | risk=Rs{risk:.0f} | min_score={min_score}\n")
    hdr = (f"{'symbol':<12}{'n':>4}{'L/S':>8}{'win%':>7}{'avgR':>8}{'totR':>8}"
           f"{'totINR':>11}{'PF':>6}{'sharpe':>8}{'maxDD':>10}")
    print(hdr); print("-" * len(hdr))
    allt = []
    for fp in files:
        sym = os.path.basename(fp)[:-len(f"_{interval}.json")]
        t = backtest_symbol(sym, interval, strat, risk, sl_mult, rr, slip, trail_mult)
        allt += t
        s = summarize(t)
        ls = f"{sum(1 for x in t if x['side']=='long')}/{sum(1 for x in t if x['side']=='short')}"
        print(f"{sym:<12}{s['trades']:>4}{ls:>8}{s['win']:>7}{s['avg_r']:>8}{s['total_r']:>8}"
              f"{s['total_inr']:>11.0f}{s['pf']:>6}{s['sharpe']:>8}{s['max_dd']:>10.0f}")
    o = summarize(allt)
    ls = f"{sum(1 for x in allt if x['side']=='long')}/{sum(1 for x in allt if x['side']=='short')}"
    print("-" * len(hdr))
    print(f"{'PORTFOLIO':<12}{o['trades']:>4}{ls:>8}{o['win']:>7}{o['avg_r']:>8}{o['total_r']:>8}"
          f"{o['total_inr']:>11.0f}{o['pf']:>6}{o['sharpe']:>8}{o['max_dd']:>10.0f}")
    if csv:
        write_csv(allt, csv); print(f"\ntrade log -> {csv} ({len(allt)} trades)")
    if plot and allt and write_plot(allt, plot):
        print(f"equity curve -> {plot}")
    return allt


def main():
    ap = argparse.ArgumentParser(description="Canonical Price-Action-First backtest over a data dir.")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--interval", default="60minute")
    ap.add_argument("--risk", type=float, default=1000.0)
    ap.add_argument("--sl-mult", type=float, default=SL_MULT)
    ap.add_argument("--rr", type=float, default=RR)
    ap.add_argument("--slip", type=float, default=DEFAULT_SLIP_ENTRY, help="entry slippage/side")
    ap.add_argument("--min-score", type=float, default=2.5)
    ap.add_argument("--csv", default=None, help="write trade log CSV here")
    ap.add_argument("--plot", default=None, help="write equity-curve PNG here")
    a = ap.parse_args()
    run_dir(a.data_dir, a.interval, a.min_score, a.risk, a.sl_mult, a.rr, a.slip, a.csv, a.plot)


if __name__ == "__main__":
    main()
