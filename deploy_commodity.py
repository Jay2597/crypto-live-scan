"""deploy_commodity.py — whole-lot capital-adequacy sizing for the commodity trend book.

Maps each of the 9 book symbols to its SMALLEST liquid MCX contract, computes 1-lot risk
(2.5 x ATR14 x contract multiplier) and estimated margin from the live-ish data in
data/commodities/daily_cont/, then re-runs the Turtle long-only backtest with INTEGER lots for
a given capital: lots = floor(risk_budget / lot_risk), skip signal if 0 lots afford.

Contract specs = MCX standard (VERIFY with broker before live; margins are estimates —
SPAN+exposure move with volatility, silver/natgas especially).

Run: python deploy_commodity.py 100000
     python deploy_commodity.py 200000 --blotter
"""
import glob
import os
import sys
from collections import defaultdict

import commodity_trend as ct

# book symbol -> (tradeable contract, multiplier vs OUR quote series, est margin % of lot value)
# multiplier = Rs P&L per 1-point move of the continuous series we backtest on.
CONTRACTS = dict(
    GOLD=("GOLDPETAL", 0.1, 8),      # series per 10g; Petal = 1g -> 0.1/point (also GOLDM=10)
    SILVER=("SILVERMIC", 1, 14),     # series per kg; Micro = 1kg
    CRUDEOIL=("CRUDEOILM", 10, 18),  # per bbl; mini = 10 bbl
    NATURALGAS=("NATGASMINI", 250, 22),
    COPPER=("COPPER", 2500, 12),     # NO mini exists
    ZINC=("ZINCMINI", 1000, 12),
    ALUMINIUM=("ALUMINI", 1000, 12),
    LEAD=("LEADMINI", 1000, 12),
    NICKEL=("NICKEL", 1500, 14),     # NO mini exists
)
RISK_PCT = 0.02          # risk budget per trade
MARGIN_CAP = 0.60        # max fraction of capital in margin at once
FLAT_BROKERAGE_RT = 40.0  # Rs20/side


def spec_table():
    print(f"{'symbol':<11} {'contract':<11} {'lot value':>12} {'~margin':>10} "
          f"{'1-lot risk':>10}  {'risk% of 1L':>11} {'2L':>6}")
    specs = {}
    for sym, (cname, mult, mpct) in CONTRACTS.items():
        P = ct.prep(os.path.join(ct.DDIR, f"{sym}_day.json"))
        px, a = P["c"][-1], P["atr"][-1]
        lot_val = px * mult
        margin = lot_val * mpct / 100
        risk = 2.5 * a * mult
        specs[sym] = dict(cname=cname, mult=mult, margin=margin, risk=risk, lot_val=lot_val)
        print(f"{sym:<11} {cname:<11} {lot_val:>12,.0f} {margin:>10,.0f} {risk:>10,.0f}  "
              f"{100*risk/100000:>10.1f}% {100*risk/200000:>5.1f}%")
    return specs


def run_whole_lot(capital):
    budget = RISK_PCT * capital
    files = sorted(glob.glob(os.path.join(ct.DDIR, "*_day.json")))
    all_t, skipped = [], defaultdict(int)
    margin_used = {}
    for f in files:
        sym = os.path.basename(f).replace("_day.json", "")
        cname, mult, mpct = CONTRACTS[sym]
        t, _ = ct.run_symbol(sym, ct.prep(f))
        for x in t:
            if x["side"] != "L":
                continue
            # per-lot risk at THIS trade's entry-time ATR: stop distance = entry - stop0
            stop_dist = x["entry"] - x["stop0"]
            lot_risk = stop_dist * mult
            lots = int(budget // lot_risk)
            margin = x["entry"] * mult * mpct / 100
            if lots < 1:
                skipped[sym] += 1
                continue
            qty = lots * mult
            gross = (x["exit"] - x["entry"]) * qty
            notional = x["entry"] * qty
            cost = ct.COST_RT * notional * (1 + x["hold"] / ct.ROLL_DAYS) + FLAT_BROKERAGE_RT
            all_t.append(dict(sym=sym, contract=cname, lots=lots, ein=x["ein"], xout=x["xout"],
                              entry=x["entry"], stop0=x["stop0"], exit=x["exit"],
                              hold=x["hold"], margin=margin * lots,
                              net=gross - cost, riskRs=lots * lot_risk))
    all_t.sort(key=lambda t: t["ein"])
    return all_t, skipped


def main():
    capital = float(sys.argv[1]) if len(sys.argv) > 1 else 100000
    print(f"=== WHOLE-LOT SIZING | capital Rs{capital:,.0f} | risk/trade "
          f"{RISK_PCT*100:.0f}% = Rs{RISK_PCT*capital:,.0f} ===\n")
    print("Current per-contract economics (ATR as of last bar):")
    spec_table()
    t, skipped = run_whole_lot(capital)
    print(f"\n--- 3.5yr whole-lot backtest at Rs{capital:,.0f} ---")
    if "--blotter" in sys.argv and t:
        print(f"{'SYM':<10} {'CONTRACT':<11} {'LOTS':>4} {'ENTRY':>10} {'EXIT':>10} "
              f"{'MARGIN':>9} {'NET Rs':>8}")
        for x in t:
            print(f"{x['sym']:<10} {x['contract']:<11} {x['lots']:>4} {x['ein']:>10} "
                  f"{x['xout']:>10} {x['margin']:>9,.0f} {x['net']:>+8,.0f}")
    if t:
        w = [x for x in t if x["net"] > 0]
        ls = [x for x in t if x["net"] <= 0]
        pf = sum(x["net"] for x in w) / (-sum(x["net"] for x in ls)) if ls else 99
        tot = sum(x["net"] for x in t)
        peak_margin = max(x["margin"] for x in t)
        by_y = defaultdict(float)
        for x in t:
            by_y[x["xout"][:4]] += x["net"]
        print(f"taken n={len(t)} win={100*len(w)/len(t):.1f}% PF={pf:.2f} "
              f"NET Rs{tot:+,.0f} ({100*tot/capital:+.1f}% of capital over 3.5yr)")
        print("by year:", {y: f"{v:+,.0f}" for y, v in sorted(by_y.items())})
        print(f"largest single-trade margin: Rs{peak_margin:,.0f} "
              f"({100*peak_margin/capital:.0f}% of capital)")
    print(f"skipped (1 lot too big for budget): {dict(skipped)}")


if __name__ == "__main__":
    main()
