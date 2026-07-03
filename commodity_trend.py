"""commodity_trend.py — daily TWO-SIDED trend-following on continuous MCX futures ("commodity
book"), built to parallel the validated equity XS momentum book at the daily horizon.

Universe: 9 liquid MCX majors (GOLD SILVER CRUDEOIL NATURALGAS COPPER ZINC ALUMINIUM LEAD
NICKEL), continuous daily series in data/commodities/daily_cont/<SYM>_day.json.

RULES (Turtle-style, long AND short — commodities trend both ways)
  Regime : LONG only if close > EMA100 and EMA100 rising vs 20d ago; SHORT mirror.
  Entry  : close breaks the prior DONCH_N-day extreme in regime direction -> fill next day open.
  Stop   : initial 2.5 x ATR(14) beyond entry; then trail at the prior EXIT_N-day low/high
           (ratchets only). Exit intraday when touched (gap-adjusted to open).
  Sizing : fixed Rs1,000 risk per trade (fractional lots — see whole-lot note in report).
  Costs  : futures RT ~0.05% of notional (brokerage flat Rs20/side amortized, CTT 0.01% sell,
           exch+GST, slippage 0.03%/side) + ROLL cost: one extra RT charged per ~22 trading
           days held (continuous series hides monthly rolls).

Run: python commodity_trend.py           (portfolio + per-symbol summary)
     python commodity_trend.py --grid    (parameter grid)
     python commodity_trend.py --blotter (every trade: entry/stop/exit/P&L per Rs1000 risk)
"""
import glob
import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

DDIR = os.path.join("data", "commodities", "daily_cont")
FIXED_RISK = 1000.0
COST_RT = 0.0005 + 0.0001 + 0.0006  # brokerage-ish + CTT + slippage/exch ~= 0.12% RT total
ROLL_DAYS = 22

CFG = dict(DONCH_N=50, EXIT_N=20, ATR_N=14, ATR_STOP=2.5, EMA_N=100, SLOPE_LB=20)


def ema(x, n):
    out = [None] * len(x)
    k = 2 / (n + 1)
    s = x[0]
    for i, v in enumerate(x):
        s = v * k + s * (1 - k)
        out[i] = s if i >= n - 1 else None
    return out


def atr(h, l, c, n):
    tr = [h[0] - l[0]]
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    out = [tr[0]] * len(c)
    for i in range(1, len(c)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def roll_extreme(x, n, hi=True):
    out = [None] * len(x)
    for i in range(len(x)):
        if i >= n:
            w = x[i - n:i]
            out[i] = max(w) if hi else min(w)
    return out


def prep(path):
    b = json.load(open(path))
    o = [x["open"] for x in b]
    h = [x["high"] for x in b]
    l = [x["low"] for x in b]
    c = [x["close"] for x in b]
    d = [x["date"][:10] for x in b]
    return dict(o=o, h=h, l=l, c=c, d=d, ema=ema(c, CFG["EMA_N"]),
                atr=atr(h, l, c, CFG["ATR_N"]),
                dhi=roll_extreme(h, CFG["DONCH_N"], True),
                dlo=roll_extreme(l, CFG["DONCH_N"], False),
                xhi=roll_extreme(h, CFG["EXIT_N"], True),
                xlo=roll_extreme(l, CFG["EXIT_N"], False))


def regime(P, i):
    lb = CFG["SLOPE_LB"]
    if i < CFG["EMA_N"] + lb or P["ema"][i] is None or P["ema"][i - lb] is None:
        return 0
    if P["c"][i] > P["ema"][i] and P["ema"][i] > P["ema"][i - lb]:
        return 1
    if P["c"][i] < P["ema"][i] and P["ema"][i] < P["ema"][i - lb]:
        return -1
    return 0


def run_symbol(sym, P):
    trades = []
    pos = None
    pend = 0
    for i in range(1, len(P["c"])):
        # execute pending entry at today's open
        if pend and pos is None:
            entry = P["o"][i]
            a = P["atr"][i - 1]
            stop = entry - pend * CFG["ATR_STOP"] * a
            qty = FIXED_RISK / (CFG["ATR_STOP"] * a)
            pos = dict(side=pend, entry=entry, stop=stop, qty=qty, ein=P["d"][i], i0=i)
        pend = 0
        if pos:
            # trail
            lvl = P["xlo"][i] if pos["side"] == 1 else P["xhi"][i]
            if lvl is not None:
                pos["stop"] = max(pos["stop"], lvl) if pos["side"] == 1 else min(pos["stop"], lvl)
            hit = (P["l"][i] <= pos["stop"]) if pos["side"] == 1 else (P["h"][i] >= pos["stop"])
            if hit:
                px = pos["stop"]
                if pos["side"] == 1 and P["o"][i] < pos["stop"]:
                    px = P["o"][i]
                if pos["side"] == -1 and P["o"][i] > pos["stop"]:
                    px = P["o"][i]
                hold = i - pos["i0"]
                gross = pos["side"] * (px - pos["entry"]) * pos["qty"]
                notional = pos["entry"] * pos["qty"]
                cost = COST_RT * notional * (1 + hold / ROLL_DAYS)
                net = gross - cost
                trades.append(dict(sym=sym, side="L" if pos["side"] == 1 else "S",
                                   ein=pos["ein"], xout=P["d"][i],
                                   entry=pos["entry"], stop0=pos["entry"] - pos["side"] *
                                   CFG["ATR_STOP"] * P["atr"][pos["i0"] - 1],
                                   exit=px, hold=hold, gross=gross, cost=cost, net=net,
                                   r=net / FIXED_RISK))
                pos = None
        if pos is None:
            r = regime(P, i)
            if r == 1 and P["dhi"][i] is not None and P["c"][i] > P["dhi"][i]:
                pend = 1
            elif r == -1 and P["dlo"][i] is not None and P["c"][i] < P["dlo"][i]:
                pend = -1
    return trades, pos


def summary(trades, label=""):
    if not trades:
        return f"{label} n=0"
    w = [t for t in trades if t["net"] > 0]
    ls = [t for t in trades if t["net"] <= 0]
    pf = sum(t["net"] for t in w) / (-sum(t["net"] for t in ls)) if ls else 99
    tot = sum(t["net"] for t in trades)
    rr = [t["r"] for t in trades]
    sh = (st.mean(rr) / (st.pstdev(rr) or 1)) * math.sqrt(max(1, len(rr) / 3.5))  # ~per-yr scale
    return (f"{label} n={len(trades):3} win={100*len(w)/len(trades):4.1f}% PF={pf:4.2f} "
            f"net Rs{tot:+9,.0f} ({sum(rr):+6.1f}R) avgR {st.mean(rr):+.2f} "
            f"avg hold {st.mean(t['hold'] for t in trades):.0f}d")


def main():
    files = sorted(glob.glob(os.path.join(DDIR, "*_day.json")))
    if not files:
        print(f"NO DATA in {DDIR} — pull continuous daily MCX first")
        return
    all_t, open_pos = [], {}
    for f in files:
        sym = os.path.basename(f).replace("_day.json", "")
        t, pos = run_symbol(sym, prep(f))
        all_t += t
        if pos:
            open_pos[sym] = pos
    all_t.sort(key=lambda t: t["ein"])
    if "--blotter" in sys.argv:
        print(f"{'SYM':<11} {'SIDE':>4} {'ENTRY':>10} {'@PX':>9} {'STOP0':>9} {'EXIT':>10} "
              f"{'@PX':>9} {'HOLD':>5} {'NET Rs':>8} {'R':>6}")
        for t in all_t:
            print(f"{t['sym']:<11} {t['side']:>4} {t['ein']:>10} {t['entry']:>9.1f} "
                  f"{t['stop0']:>9.1f} {t['xout']:>10} {t['exit']:>9.1f} {t['hold']:>4}d "
                  f"{t['net']:>+8,.0f} {t['r']:>+5.2f}")
    print("\n=== COMMODITY TREND BOOK (daily, long+short, Rs1000 risk/trade, cost incl rolls) ===")
    print(summary(all_t, "ALL   "))
    for side in ("L", "S"):
        print(summary([t for t in all_t if t["side"] == side], f"{side}-only"))
    print("\nper symbol:")
    for sym in sorted({t["sym"] for t in all_t}):
        print(" ", summary([t for t in all_t if t["sym"] == sym], f"{sym:<11}"))
    by_y = defaultdict(list)
    for t in all_t:
        by_y[t["xout"][:4]].append(t["net"])
    print("by exit year:")
    for y in sorted(by_y):
        print(f"  {y}: n={len(by_y[y]):3} net Rs{sum(by_y[y]):+9,.0f}")
    if open_pos:
        print("OPEN:", {s: dict(side=p['side'], entry=round(p['entry'], 1),
                                stop=round(p['stop'], 1)) for s, p in open_pos.items()})
    if "--grid" in sys.argv:
        print("\nGRID donch x exit x atrstop:")
        for dn in (20, 50):
            for en in (10, 20):
                for ast in (2.0, 2.5, 3.0):
                    CFG["DONCH_N"], CFG["EXIT_N"], CFG["ATR_STOP"] = dn, en, ast
                    tt = []
                    for f in files:
                        t, _ = run_symbol(os.path.basename(f).replace("_day.json", ""), prep(f))
                        tt += t
                    print(f"  d{dn:<3} x{en:<3} s{ast}: {summary(tt)}")


if __name__ == "__main__":
    main()
