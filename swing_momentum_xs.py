"""swing_momentum_xs.py — CROSS-SECTIONAL momentum rotation on the point-in-time midcap universe.

The survivorship-free test the 2026-06-22 swing-momentum finding asked for: universe = Nifty
Midcap 150 as of Feb-2019 (predates the window; includes all later losers), data in
data/stocks/swing_pit/ (fetch_pit_universe.py).

STRATEGY (long-only, monthly rotation, CNC delivery)
  Every REBAL_DAYS trading days, on that day's close:
    eligibility: close > SMA200 AND SMA200 rising vs 20d ago  (validated regime gate)
    market gate: NIFTY > its SMA200, else exit everything (stay in cash)
    score:       momentum = return over MOM_LB days, skipping the most recent MOM_SKIP days
    hold TOP_N by score, equal-weight; keep an existing holding if still in top BUFFER*TOP_N
  All buys/sells fill at the NEXT day's open. Zerodha CNC cost model from swing_momentum.py.

BENCHMARKS: NIFTY buy&hold + equal-weight buy&hold of the SAME point-in-time universe
(same symbols, same window) — the bar the biased midcap test failed to clear.

Run: python swing_momentum_xs.py            (single config)
     python swing_momentum_xs.py --grid     (small honest grid, report all cells)
"""
import glob
import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

DDIR = os.environ.get("XS_DIR", os.path.join("data", "stocks", "swing_pit"))
WINDOW_START = "2021-07-01"   # first decision date at/after this (universe list is Feb-2019)

CFG = dict(
    REBAL_DAYS=21, MOM_LB=126, MOM_SKIP=5, TOP_N=5, BUFFER=2.0,
    SMA_TREND=200, SMA_SLOPE_LB=20, USE_MARKET_FILTER=True,
    CAP0=1_000_000,
    STT=0.001, STAMP_BUY=0.00015, TXN=0.0000297, SEBI=0.000001, GST=0.18,
    DP_FLAT=15.93, SLIP=0.0005,
)


def sma(x, n):
    out = [None] * len(x)
    s = 0.0
    for i in range(len(x)):
        s += x[i]
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def load(path):
    bars = json.load(open(path))
    return ([b["open"] for b in bars], [b["high"] for b in bars],
            [b["low"] for b in bars], [b["close"] for b in bars],
            [b["date"][:10] for b in bars])


def buy_cost(px, q):
    to = px * q
    return (CFG["STT"] + CFG["STAMP_BUY"] + CFG["TXN"] + CFG["SEBI"]) * to \
        + CFG["GST"] * (CFG["TXN"] + CFG["SEBI"]) * to + CFG["SLIP"] * to


def sell_cost(px, q):
    to = px * q
    return (CFG["STT"] + CFG["TXN"] + CFG["SEBI"]) * to \
        + CFG["GST"] * (CFG["TXN"] + CFG["SEBI"]) * to + CFG["DP_FLAT"] + CFG["SLIP"] * to


def build():
    S = {}
    for f in sorted(glob.glob(os.path.join(DDIR, "*_day.json"))):
        sym = os.path.basename(f).replace("_day.json", "")
        if sym == "NIFTY":
            continue
        o, h, l, c, d = load(f)
        S[sym] = dict(o=o, h=h, l=l, c=c, d=d, sma200=sma(c, CFG["SMA_TREND"]),
                      idx={dt: i for i, dt in enumerate(d)})
    o, h, l, c, d = load(os.path.join(DDIR, "NIFTY_day.json"))
    nifty = dict(d=d, c=c, sma200=sma(c, CFG["SMA_TREND"]), idx={dt: i for i, dt in enumerate(d)})
    return S, nifty


def eligible(P, i):
    lb = CFG["SMA_SLOPE_LB"]
    if i < CFG["SMA_TREND"] + lb or P["sma200"][i] is None or P["sma200"][i - lb] is None:
        return False
    return P["c"][i] > P["sma200"][i] and P["sma200"][i] > P["sma200"][i - lb]


def momentum(P, i):
    j = i - CFG["MOM_SKIP"]
    k = j - CFG["MOM_LB"]
    if k < 0:
        return None
    return P["c"][j] / P["c"][k] - 1


def run(S, nifty):
    cal = [dt for dt in nifty["d"] if dt >= WINDOW_START]
    cash = CFG["CAP0"]
    positions = {}          # sym -> dict(qty, entry, edate, buycost)
    pending_buys, pending_sells = [], []
    trades, eq_curve = [], []
    for day_no, dt in enumerate(cal):
        # ---- 1. execute pending orders at today's open ----
        for sym in pending_sells:
            P = S[sym]
            i = P["idx"].get(dt)
            if i is None or sym not in positions:
                continue
            pos = positions.pop(sym)
            px = P["o"][i]
            sc = sell_cost(px, pos["qty"])
            cash += px * pos["qty"] - sc
            net = (px - pos["entry"]) * pos["qty"] - pos["buycost"] - sc
            trades.append(dict(sym=sym, ein=pos["edate"], xout=dt, entry=pos["entry"], exit=px,
                               qty=pos["qty"], net=net,
                               retpct=net / (pos["entry"] * pos["qty"]) * 100))
        if pending_buys:
            slot = (cash + sum(S[s]["c"][S[s]["idx"][dt]] * p["qty"]
                               for s, p in positions.items() if dt in S[s]["idx"])) / CFG["TOP_N"]
            for sym in pending_buys:
                P = S[sym]
                i = P["idx"].get(dt)
                if i is None or sym in positions or len(positions) >= CFG["TOP_N"]:
                    continue
                px = P["o"][i]
                q = int(min(slot, cash) / px)
                if q < 1:
                    continue
                bc = buy_cost(px, q)
                if px * q + bc > cash:
                    q = int((cash - bc) / px)
                    if q < 1:
                        continue
                    bc = buy_cost(px, q)
                cash -= px * q + bc
                positions[sym] = dict(qty=q, entry=px, edate=dt, buycost=bc)
        pending_buys, pending_sells = [], []

        # ---- 2. rebalance decision on close ----
        if day_no % CFG["REBAL_DAYS"] == 0:
            ni = nifty["idx"][dt]
            market_ok = (not CFG["USE_MARKET_FILTER"]) or (
                nifty["sma200"][ni] is not None and nifty["c"][ni] > nifty["sma200"][ni])
            if not market_ok:
                pending_sells = list(positions)
            else:
                scored = []
                for sym, P in S.items():
                    i = P["idx"].get(dt)
                    if i is None or not eligible(P, i):
                        continue
                    m = momentum(P, i)
                    if m is not None:
                        scored.append((m, sym))
                scored.sort(reverse=True)
                top = [sym for _, sym in scored[:CFG["TOP_N"]]]
                keep = {sym for _, sym in scored[:int(CFG["BUFFER"] * CFG["TOP_N"])]}
                pending_sells = [sym for sym in positions if sym not in keep]
                slots = CFG["TOP_N"] - (len(positions) - len(pending_sells))
                pending_buys = [sym for sym in top if sym not in positions][:max(0, slots)]

        # ---- 3. mark to market ----
        mtm = cash
        for sym, pos in positions.items():
            i = S[sym]["idx"].get(dt)
            mtm += (S[sym]["c"][i] if i is not None else pos["entry"]) * pos["qty"]
        eq_curve.append((dt, mtm))
    return trades, eq_curve


def perf(eq):
    v = [x for _, x in eq]
    yrs = len(eq) / 252
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    peak, dd = v[0], 0.0
    for x in v:
        peak = max(peak, x)
        dd = min(dd, x / peak - 1)
    rets = [v[i] / v[i - 1] - 1 for i in range(1, len(v))]
    sh = (st.mean(rets) / (st.pstdev(rets) or 1)) * math.sqrt(252)
    return cagr, dd, sh, v[-1]


def benchmarks(S, nifty):
    cal = [dt for dt in nifty["d"] if dt >= WINDOW_START]
    d0, d1 = cal[0], cal[-1]
    i0, i1 = nifty["idx"][d0], nifty["idx"][d1]
    yrs = (i1 - i0) / 252
    n_cagr = (nifty["c"][i1] / nifty["c"][i0]) ** (1 / yrs) - 1
    # equal-weight B&H of every universe name with data at d0 (same PIT set, incl later losers)
    rets = []
    for P in S.values():
        j0 = P["idx"].get(d0)
        if j0 is None:
            j0 = next((P["idx"][dt] for dt in cal if dt in P["idx"]), None)
        if j0 is None:
            continue
        rets.append(P["c"][-1] / P["c"][j0])
    ew_cagr = (st.mean(rets)) ** (1 / yrs) - 1 if rets else 0.0
    return n_cagr, ew_cagr, len(rets)


def report(trades, eq, S, nifty):
    cagr, dd, sh, final = perf(eq)
    n_cagr, ew_cagr, n_names = benchmarks(S, nifty)
    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    pf = sum(t["net"] for t in wins) / (-sum(t["net"] for t in losses)) if losses else 99
    print(f"\n=== XS MOMENTUM ROTATION (top{CFG['TOP_N']}, mom{CFG['MOM_LB']}-{CFG['MOM_SKIP']}, "
          f"rebal {CFG['REBAL_DAYS']}d, net CNC costs) ===")
    print(f"  window {eq[0][0]} -> {eq[-1][0]}  | universe {len(S)} PIT names")
    print(f"  final Rs{final:,.0f}  CAGR {cagr*100:+.1f}%  Sharpe {sh:.2f}  maxDD {dd*100:.1f}%")
    print(f"  BENCH: Nifty B&H {n_cagr*100:+.1f}% | equal-weight PIT universe B&H "
          f"{ew_cagr*100:+.1f}% ({n_names} names)")
    if trades:
        print(f"  trades n={len(trades)} win={100*len(wins)/len(trades):.1f}% PF={pf:.2f} "
              f"avg {st.mean(t['retpct'] for t in trades):+.2f}%/trade")
        yr = defaultdict(list)
        for t in trades:
            yr[t["xout"][:4]].append(t["net"])
        for y in sorted(yr):
            v = yr[y]
            print(f"    {y}: n={len(v):3} win={100*sum(1 for x in v if x>0)/len(v):4.1f}% "
                  f"NET Rs{sum(v):10,.0f}")


def main():
    S, nifty = build()
    if "--grid" in sys.argv:
        print(f"GRID | {len(S)} PIT names | window >= {WINDOW_START} | net CNC costs")
        print(f"{'momLB':>6} {'topN':>5} {'rebal':>6}  CAGR    Sharpe  maxDD   (Nifty / EW-univ)")
        n_c, ew_c, _ = benchmarks(S, nifty)
        for lb in (63, 126, 231):
            for tn in (5, 10):
                for rb in (21,):
                    CFG["MOM_LB"] = lb
                    CFG["TOP_N"] = tn
                    CFG["REBAL_DAYS"] = rb
                    t, e = run(S, nifty)
                    cagr, dd, sh, _ = perf(e)
                    print(f"{lb:>6} {tn:>5} {rb:>6}  {cagr*100:+5.1f}%  {sh:5.2f}  {dd*100:5.1f}%"
                          f"   ({n_c*100:+.1f}% / {ew_c*100:+.1f}%)")
    else:
        t, e = run(S, nifty)
        report(t, e, S, nifty)


if __name__ == "__main__":
    main()
