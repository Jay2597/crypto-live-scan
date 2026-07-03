"""intraday_inplay.py — 'Stocks-in-Play Momentum' intraday backtest on the names that were in
the monthly top-10 momentum book (results/top10_history.json), 15-min bars, MIS costs.

The one intraday hypothesis prior research never tested: all 13 failed intraday campaigns used
STATIC baskets of efficient large-caps; this trades ONLY the rotating momentum names, only on
days they are 'in play'. Long-only (names are momentum leaders), same-day square-off.

VARIANTS (entry trigger):
  ORB   : 15-min close above the 30-min opening range high -> enter next bar open
  PDH   : 15-min close above the prior day's high            -> enter next bar open
  VWAP  : price pulls back to within 0.15% of session VWAP after being 0.5%+ above -> enter on
          next 15-min close back above VWAP

COMMON RULES
  in-play filter (optional): first-30-min volume >= IPV_MULT x 20-session avg first-30-min
  volume, OR |gap| >= 0.5% vs prev close
  stop   : ORB -> max(OR low, entry - ATR15) [nearer]; others -> entry - 1.0 x ATR15(14)
  target : RR x risk; exit stop-before-target intrabar (conservative); square-off last bar close
  risk   : Rs1,000/trade (R-normalized), notional capped Rs2.5L; max 2 concurrent, 3/day
  costs  : Zerodha MIS intraday per leg: brokerage min(Rs20, 0.03%), STT 0.025% sell, txn
           0.00297%, SEBI 0.0001%, GST 18% on (brok+txn+sebi), stamp 0.003% buy, slip 0.05%/side

Run: python intraday_inplay.py            (grid summary)
     python intraday_inplay.py --blotter ORB   (trade-by-trade for one variant, best config)
"""
import glob
import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

DDIR = os.path.join("data", "stocks", "intraday15_inplay")
TOP10 = json.load(open(os.path.join("results", "top10_history.json")))
FIXED_RISK = 1000.0
MAX_NOTIONAL = 250_000
MAX_CONCURRENT = 2
MAX_PER_DAY = 3
SLIP = 0.0005  # per side
IPV_MULT = 1.5
GAP_MIN = 0.005


def mis_cost(buy_val, sell_val):
    brok = min(20.0, 0.0003 * buy_val) + min(20.0, 0.0003 * sell_val)
    stt = 0.00025 * sell_val
    txn = 0.0000297 * (buy_val + sell_val)
    sebi = 0.000001 * (buy_val + sell_val)
    gst = 0.18 * (brok + txn + sebi)
    stamp = 0.00003 * buy_val
    slip = SLIP * (buy_val + sell_val)
    return brok + stt + txn + sebi + gst + stamp + slip


def load_sym(path):
    bars = json.load(open(path))
    sessions = defaultdict(list)
    for b in bars:
        sessions[b["date"][:10]].append(b)
    return dict(sessions)


def build():
    data = {}
    for f in sorted(glob.glob(os.path.join(DDIR, "*_15minute.json"))):
        sym = os.path.basename(f).replace("_15minute.json", "")
        data[sym] = load_sym(f)
    nifty = data.pop("NIFTY")
    return data, nifty


def active_list(day):
    dates = sorted(TOP10)
    cur = None
    for d in dates:
        if d <= day:
            cur = d
        else:
            break
    return TOP10.get(cur, []) if cur else []


def atr15(prev_bars, n=14):
    if len(prev_bars) < n + 1:
        return None
    trs = []
    for i in range(len(prev_bars) - n, len(prev_bars)):
        h, l, pc = prev_bars[i]["high"], prev_bars[i]["low"], prev_bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / n


def nifty_daily_gate():
    """NIFTY close vs SMA200 per day from the swing_pit daily file (no lookahead: prior day)."""
    nb = json.load(open(os.path.join("data", "stocks", "swing_pit", "NIFTY_day.json")))
    c = [b["close"] for b in nb]
    d = [b["date"] for b in nb]
    ok = {}
    for i in range(200, len(c)):
        ok[d[i]] = c[i - 1] > sum(c[i - 200:i]) / 200  # decided on yesterday's close
    return ok


def run(data, variant="ORB", inplay=True, rr=2.0, use_gate=False, collect=False):
    gate = nifty_daily_gate() if use_gate else None
    # per symbol: flat history of bars for ATR + 20-session first-30min volume avg
    all_days = sorted({day for s in data.values() for day in s})
    trades = []
    for day in all_days:
        book = active_list(day)
        if not book:
            continue
        if use_gate and not gate.get(day, False):
            continue
        day_trades = 0
        open_pos = []
        cands = []
        for sym in book:
            S = data.get(sym)
            if not S or day not in S:
                continue
            bars = S[day]
            if len(bars) < 6:
                continue
            days_prior = sorted(d for d in S if d < day)
            if len(days_prior) < 21:
                continue
            prev_day = S[days_prior[-1]]
            prev_close, prev_high = prev_day[-1]["close"], max(b["high"] for b in prev_day)
            # in-play check
            v30 = sum(b["volume"] for b in bars[:2])
            hist_v30 = [sum(S[d][:2][i]["volume"] for i in range(min(2, len(S[d]))))
                        for d in days_prior[-20:]]
            avg_v30 = st.mean(hist_v30) if hist_v30 else 0
            gap = abs(bars[0]["open"] / prev_close - 1)
            if inplay and not (v30 >= IPV_MULT * avg_v30 or gap >= GAP_MIN):
                continue
            hist_flat = [b for d in days_prior[-3:] for b in S[d]] + bars[:2]
            a = atr15(hist_flat)
            if not a:
                continue
            cands.append((sym, bars, prev_high, a))
        # simulate the day bar-by-bar across candidates (entries from bar index 2 on)
        for sym, bars, prev_high, a in cands:
            if day_trades >= MAX_PER_DAY or len(open_pos) >= MAX_CONCURRENT:
                break
            orh = max(bars[0]["high"], bars[1]["high"])
            orl = min(bars[0]["low"], bars[1]["low"])
            # find trigger bar
            trig_i = None
            was_above = False
            cum_pv = cum_v = 0.0
            for i, b in enumerate(bars):
                tp = (b["high"] + b["low"] + b["close"]) / 3
                cum_pv += tp * b["volume"]
                cum_v += b["volume"]
                vwap = cum_pv / cum_v if cum_v else b["close"]
                if i < 2 or i > len(bars) - 4:
                    continue
                if variant == "ORB" and b["close"] > orh:
                    trig_i = i
                    break
                if variant == "PDH" and b["close"] > prev_high:
                    trig_i = i
                    break
                if variant == "VWAP":
                    if b["close"] > vwap * 1.005:
                        was_above = True
                    if was_above and b["low"] <= vwap * 1.0015 and b["close"] > vwap:
                        trig_i = i
                        break
            if trig_i is None or trig_i + 1 >= len(bars) - 1:
                continue
            entry = bars[trig_i + 1]["open"]
            stop = max(orl, entry - a) if variant == "ORB" else entry - a
            risk_ps = entry - stop
            if risk_ps <= 0:
                continue
            qty = min(FIXED_RISK / risk_ps, MAX_NOTIONAL / entry)
            target = entry + rr * risk_ps
            exit_px, xreason = None, None
            for j in range(trig_i + 1, len(bars)):
                b = bars[j]
                if j == trig_i + 1:
                    lo = min(b["low"], entry)  # entered at open of this bar
                else:
                    lo = b["low"]
                if lo <= stop:
                    exit_px = min(b["open"], stop) if b["open"] < stop else stop
                    xreason = "STOP"
                    break
                if b["high"] >= target:
                    exit_px = target
                    xreason = "TARGET"
                    break
            if exit_px is None:
                exit_px = bars[-1]["close"]
                xreason = "EOD"
            gross = (exit_px - entry) * qty
            cost = mis_cost(entry * qty, exit_px * qty)
            net = gross - cost
            trades.append(dict(day=day, sym=sym, entry=entry, stop=stop, target=target,
                               exit=exit_px, qty=qty, gross=gross, cost=cost, net=net,
                               r=net / FIXED_RISK, reason=xreason,
                               etime=bars[trig_i + 1]["date"][11:16]))
            day_trades += 1
    return trades


def summary(trades):
    if not trades:
        return "n=0"
    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    pf = sum(t["net"] for t in wins) / (-sum(t["net"] for t in losses)) if losses else 99
    tot = sum(t["net"] for t in trades)
    totr = sum(t["r"] for t in trades)
    gross = sum(t["gross"] for t in trades)
    return (f"n={len(trades):4} win={100*len(wins)/len(trades):4.1f}% PF={pf:4.2f} "
            f"net Rs{tot:+9,.0f} ({totr:+6.1f}R) gross Rs{gross:+9,.0f}")


if __name__ == "__main__":
    data, nifty = build()
    if "--blotter" in sys.argv:
        variant = sys.argv[sys.argv.index("--blotter") + 1]
        trades = run(data, variant=variant, inplay=True, rr=2.0, use_gate=False)
        print(f"{'DAY':>10} {'SYM':<11} {'IN':>5} {'ENTRY':>8} {'STOP':>8} {'TGT':>8} "
              f"{'EXIT':>8} {'NET':>7} {'R':>6}  WHY")
        for t in trades:
            print(f"{t['day']:>10} {t['sym']:<11} {t['etime']:>5} {t['entry']:>8.1f} "
                  f"{t['stop']:>8.1f} {t['target']:>8.1f} {t['exit']:>8.1f} "
                  f"{t['net']:>7,.0f} {t['r']:>+5.2f}  {t['reason']}")
        print(summary(trades))
        by_m = defaultdict(list)
        for t in trades:
            by_m[t["day"][:7]].append(t["net"])
        for m in sorted(by_m):
            print(f"  {m}: n={len(by_m[m]):3} net Rs{sum(by_m[m]):+8,.0f}")
    else:
        print("GRID | in-play momentum names | 15-min MIS | Rs1000 risk/trade\n")
        print(f"{'variant':>7} {'inplay':>7} {'rr':>4} {'gate':>5}  result")
        for variant in ("ORB", "PDH", "VWAP"):
            for inplay in (True, False):
                for rr in (1.5, 2.0, 3.0):
                    for use_gate in (False, True):
                        t = run(data, variant=variant, inplay=inplay, rr=rr, use_gate=use_gate)
                        print(f"{variant:>7} {str(inplay):>7} {rr:>4} {str(use_gate):>5}  "
                              f"{summary(t)}")
