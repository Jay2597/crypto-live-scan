"""commodity_lab.py — mix-and-match strategy lab for MCX commodities on the SAME daily
continuous data as commodity_trend.py (data/commodities/daily_cont/, 9 majors, 2023-2026),
same cost conventions (~0.12% RT + one extra RT per 22 trading days held for rolls).

Families tested (all long-side fills at NEXT day's open, no lookahead):
  A. XSMOM  — cross-sectional momentum rotation: every 21d rank by 126d return, hold top-3
              (long-only), Rs1L notional/slot
  B. MACROSS— EMA20/100 crossover trend (in at golden cross, out at dead cross), Rs1L notional
  C. PULLBK — uptrend (EMA100+slope) + pullback touching EMA20 + close back above -> Turtle
              exit (20d-low trail, 2.5xATR initial stop), Rs1000 risk
  D. MEANREV— RSI14 < 30 dip-buy; exit RSI > 55 or 10d time stop; 2.5xATR stop; Rs1000 risk;
              variants: gated (only if close > EMA200) and naive (any regime)
  E. RATIO  — GOLD/SILVER ratio z-score(60) reversion: |z|>2 entry, |z|<0.5 exit, two legs
              Rs1L notional each
  F. ADXGATE— the validated Turtle long-only book, entries only when ADX14 >= 20

Run: python commodity_lab.py
"""
import glob
import os
import statistics as st
from collections import defaultdict

import commodity_trend as ct

FILES = sorted(glob.glob(os.path.join(ct.DDIR, "*_day.json")))
SYMS = [os.path.basename(f).replace("_day.json", "") for f in FILES]
DATA = {s: ct.prep(f) for s, f in zip(SYMS, FILES)}
NOTIONAL = 100_000.0


def rt_cost(notional, hold_days):
    return ct.COST_RT * notional * (1 + hold_days / ct.ROLL_DAYS)


def rsi(c, n=14):
    out = [None] * len(c)
    g = l = 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        up, dn = max(d, 0), max(-d, 0)
        if i <= n:
            g += up / n
            l += dn / n
        else:
            g = (g * (n - 1) + up) / n
            l = (l * (n - 1) + dn) / n
        if i >= n:
            out[i] = 100 - 100 / (1 + (g / l if l else 99))
    return out


def adx(P, n=14):
    h, l, c = P["h"], P["l"], P["c"]
    out = [None] * len(c)
    trs, pdm, ndm = [], [], []
    for i in range(1, len(c)):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        ndm.append(dn if dn > up and dn > 0 else 0.0)
    if len(trs) < 2 * n:
        return out
    atr_s = sum(trs[:n])
    p_s = sum(pdm[:n])
    n_s = sum(ndm[:n])
    dxs = []
    for i in range(n, len(trs)):
        atr_s = atr_s - atr_s / n + trs[i]
        p_s = p_s - p_s / n + pdm[i]
        n_s = n_s - n_s / n + ndm[i]
        pdi = 100 * p_s / atr_s if atr_s else 0
        ndi = 100 * n_s / atr_s if atr_s else 0
        dx = 100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0
        dxs.append(dx)
        if len(dxs) >= n:
            out[i + 1] = sum(dxs[-n:]) / n
    return out


def summarize(label, trades):
    if not trades:
        print(f"{label:<28} n=0")
        return
    w = [t for t in trades if t["net"] > 0]
    ls = [t for t in trades if t["net"] <= 0]
    pf = sum(t["net"] for t in w) / (-sum(t["net"] for t in ls)) if ls else 99
    tot = sum(t["net"] for t in trades)
    yr = defaultdict(float)
    for t in trades:
        yr[t["xout"][:4]] += t["net"]
    yrs = " ".join(f"{y[2:]}:{v:+,.0f}" for y, v in sorted(yr.items()))
    print(f"{label:<28} n={len(trades):3} win={100*len(w)/len(trades):4.1f}% PF={pf:5.2f} "
          f"net Rs{tot:+9,.0f} | {yrs}")


# ---------- A. XS momentum rotation ----------
def xsmom(top_n=3, lb=126, rebal=21, gated=True):
    cal = DATA["GOLD"]["d"]
    idx = {s: {d: i for i, d in enumerate(P["d"])} for s, P in DATA.items()}
    pos = {}
    trades = []
    for k in range(200, len(cal), rebal):
        day = cal[k]
        scored = []
        for s, P in DATA.items():
            i = idx[s].get(day)
            if i is None or i < lb + 1:
                continue
            if gated and ct.regime(P, i) != 1:
                continue
            scored.append((P["c"][i] / P["c"][i - lb] - 1, s))
        scored.sort(reverse=True)
        want = {s for m, s in scored[:top_n] if m > 0}
        # exits then entries at next bar open
        for s in list(pos):
            if s not in want:
                i = idx[s].get(day)
                if i is None or i + 1 >= len(DATA[s]["c"]):
                    continue
                p = pos.pop(s)
                px = DATA[s]["o"][i + 1]
                qty = NOTIONAL / p["entry"]
                hold = i + 1 - p["i0"]
                net = (px - p["entry"]) * qty - rt_cost(NOTIONAL, hold)
                trades.append(dict(sym=s, ein=p["ein"], xout=DATA[s]["d"][i + 1], net=net))
        for s in want:
            if s in pos:
                continue
            i = idx[s].get(day)
            if i is None or i + 1 >= len(DATA[s]["c"]):
                continue
            pos[s] = dict(entry=DATA[s]["o"][i + 1], ein=DATA[s]["d"][i + 1], i0=i + 1)
    for s, p in pos.items():  # mark last
        P = DATA[s]
        qty = NOTIONAL / p["entry"]
        net = (P["c"][-1] - p["entry"]) * qty - rt_cost(NOTIONAL, len(P["c"]) - p["i0"])
        trades.append(dict(sym=s, ein=p["ein"], xout=P["d"][-1], net=net))
    return trades


# ---------- B. EMA cross ----------
def macross(fast=20, slow=100):
    trades = []
    for s, P in DATA.items():
        ef, es = ct.ema(P["c"], fast), ct.ema(P["c"], slow)
        pos = None
        for i in range(slow + 1, len(P["c"]) - 1):
            if pos is None and ef[i] and es[i] and ef[i - 1] and es[i - 1]:
                if ef[i - 1] <= es[i - 1] and ef[i] > es[i]:
                    pos = dict(entry=P["o"][i + 1], ein=P["d"][i + 1], i0=i + 1)
            elif pos is not None and ef[i - 1] >= es[i - 1] and ef[i] < es[i]:
                px = P["o"][i + 1]
                qty = NOTIONAL / pos["entry"]
                hold = i + 1 - pos["i0"]
                net = (px - pos["entry"]) * qty - rt_cost(NOTIONAL, hold)
                trades.append(dict(sym=s, ein=pos["ein"], xout=P["d"][i + 1], net=net))
                pos = None
        if pos:
            qty = NOTIONAL / pos["entry"]
            net = (P["c"][-1] - pos["entry"]) * qty - rt_cost(NOTIONAL, len(P["c"]) - pos["i0"])
            trades.append(dict(sym=s, ein=pos["ein"], xout=P["d"][-1], net=net))
    return trades


# ---------- C. pullback-in-trend ----------
def pullback():
    trades = []
    for s, P in DATA.items():
        e20 = ct.ema(P["c"], 20)
        pos = None
        pend = False
        for i in range(121, len(P["c"]) - 1):
            if pend and pos is None:
                entry = P["o"][i]
                a = P["atr"][i - 1]
                pos = dict(entry=entry, stop=entry - 2.5 * a, qty=1000.0 / (2.5 * a),
                           ein=P["d"][i], i0=i)
            pend = False
            if pos:
                lvl = P["xlo"][i]
                if lvl is not None:
                    pos["stop"] = max(pos["stop"], lvl)
                if P["l"][i] <= pos["stop"]:
                    px = min(P["o"][i], pos["stop"]) if P["o"][i] < pos["stop"] else pos["stop"]
                    hold = i - pos["i0"]
                    net = (px - pos["entry"]) * pos["qty"] - rt_cost(
                        pos["entry"] * pos["qty"], hold)
                    trades.append(dict(sym=s, ein=pos["ein"], xout=P["d"][i], net=net))
                    pos = None
            if pos is None and ct.regime(P, i) == 1 and e20[i] is not None:
                if P["l"][i] <= e20[i] and P["c"][i] > e20[i]:
                    pend = True
        # leave open position unmarked (small effect), consistent across variants
    return trades


# ---------- D. RSI mean reversion ----------
def meanrev(gated=True):
    trades = []
    for s, P in DATA.items():
        r = rsi(P["c"])
        e200 = ct.ema(P["c"], 200)
        pos = None
        pend = False
        for i in range(221, len(P["c"]) - 1):
            if pend and pos is None:
                entry = P["o"][i]
                a = P["atr"][i - 1]
                pos = dict(entry=entry, stop=entry - 2.5 * a, qty=1000.0 / (2.5 * a),
                           ein=P["d"][i], i0=i)
            pend = False
            if pos:
                done = px = None
                if P["l"][i] <= pos["stop"]:
                    px = min(P["o"][i], pos["stop"]) if P["o"][i] < pos["stop"] else pos["stop"]
                elif r[i] is not None and r[i] > 55:
                    done = "rsi"
                elif i - pos["i0"] >= 10:
                    done = "time"
                if done:
                    px = P["c"][i]
                if px is not None:
                    hold = i - pos["i0"]
                    net = (px - pos["entry"]) * pos["qty"] - rt_cost(
                        pos["entry"] * pos["qty"], hold)
                    trades.append(dict(sym=s, ein=pos["ein"], xout=P["d"][i], net=net))
                    pos = None
            if pos is None and r[i] is not None and r[i] < 30:
                if not gated or (e200[i] is not None and P["c"][i] > e200[i]):
                    pend = True
    return trades


# ---------- E. gold/silver ratio z-score ----------
def ratio_pair(z_in=2.0, z_out=0.5, look=60):
    G, S = DATA["GOLD"], DATA["SILVER"]
    idx = {d: i for i, d in enumerate(S["d"])}
    days = [d for d in G["d"] if d in idx]
    ratio = [G["c"][G["d"].index(d)] / S["c"][idx[d]] for d in days]  # slow but fine (900)
    gi = {d: G["d"].index(d) for d in days}
    si = {d: idx[d] for d in days}
    trades = []
    pos = None
    for k in range(look, len(days) - 1):
        w = ratio[k - look:k]
        mu, sd = st.mean(w), st.pstdev(w) or 1e-9
        z = (ratio[k] - mu) / sd
        d_next = days[k + 1]
        if pos is None and abs(z) > z_in:
            side = -1 if z > 0 else 1  # z>0: ratio rich -> short gold/long silver
            pos = dict(side=side, k0=k + 1,
                       gpx=G["o"][gi[d_next]], spx=S["o"][si[d_next]], ein=d_next)
        elif pos is not None and abs(z) < z_out:
            gq = NOTIONAL / pos["gpx"]
            sq = NOTIONAL / pos["spx"]
            gpnl = pos["side"] * -1 * (G["o"][gi[d_next]] - pos["gpx"]) * gq
            spnl = pos["side"] * (S["o"][si[d_next]] - pos["spx"]) * sq
            hold = k + 1 - pos["k0"]
            net = gpnl + spnl - 2 * rt_cost(NOTIONAL, hold)
            trades.append(dict(sym="G/S", ein=pos["ein"], xout=d_next, net=net))
            pos = None
    return trades


# ---------- F. ADX gate on the Turtle book ----------
def turtle_adx(min_adx=20):
    trades = []
    for s, P in DATA.items():
        P2 = dict(P)
        ax = adx(P)
        t, _ = ct.run_symbol(s, P2)
        di = {d: i for i, d in enumerate(P["d"])}
        for x in t:
            if x["side"] != "L":
                continue
            i = di[x["ein"]]
            if ax[i - 1] is not None and ax[i - 1] >= min_adx:
                trades.append(dict(sym=s, ein=x["ein"], xout=x["xout"], net=x["net"]))
    return trades


if __name__ == "__main__":
    print("COMMODITY STRATEGY LAB | 9 MCX majors daily 2023-2026 | costs incl rolls\n")
    base = []
    for s, f in zip(SYMS, FILES):
        t, _ = ct.run_symbol(s, ct.prep(f))
        base += [dict(sym=x["sym"], ein=x["ein"], xout=x["xout"], net=x["net"])
                 for x in t if x["side"] == "L"]
    summarize("BASELINE Turtle long-only", base)
    summarize("A. XSMOM top3/126d (gated)", xsmom(3, 126, 21, True))
    summarize("A. XSMOM top3/63d  (gated)", xsmom(3, 63, 21, True))
    summarize("A. XSMOM top3/126d (naive)", xsmom(3, 126, 21, False))
    summarize("B. MACROSS ema20/100", macross(20, 100))
    summarize("B. MACROSS ema50/200", macross(50, 200))
    summarize("C. PULLBACK ema20-in-trend", pullback())
    summarize("D. MEANREV rsi30 (gated)", meanrev(True))
    summarize("D. MEANREV rsi30 (naive)", meanrev(False))
    summarize("E. RATIO G/S z2.0/0.5", ratio_pair(2.0, 0.5))
    summarize("E. RATIO G/S z1.5/0.5", ratio_pair(1.5, 0.5))
    summarize("F. Turtle-L + ADX>=20", turtle_adx(20))
    summarize("F. Turtle-L + ADX>=25", turtle_adx(25))
