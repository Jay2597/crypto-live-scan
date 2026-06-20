"""paper.py — paper-trade tracker for the live scanner.

When live_scan fires a signal it OPENS a paper trade here (entry/stop/target sized exactly like
backtest.py: market entry + 0.05% slip, stop 3.0xATR, TP at RR 3.0, Rs1000 risk). On every later
scan, check_open() walks the new 1h bars and closes any trade that hit its stop or target
(conservative: stop checked before target within a bar), recording realized P&L with the same
asymmetric costs as the validated backtest (entry taker 0.10%; TP exit maker 0.02%; SL exit
taker+slip 0.15%).

Files (all under results/, committed back by the workflow so state persists across runs):
  paper_open.json    — currently open positions (one per symbol max)
  paper_trades.csv   — CLOSED trades, the trade+P&L ledger you asked for
  paper_summary.txt  — running totals (win%, total R, total Rs, PF, open MTM)
"""
import os, json, csv
import pandas as pd
import risk

# --- crypto execution params (match crypto.py defaults + backtest.py costs) ---
RR = 3.0
SL_MULT = 3.0
SLIP_ENTRY = 0.0005
MAKER = 0.0002
TAKER = 0.0010
SLIP = 0.0005
FIXED_RISK = 1000.0

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
OPEN_F = os.path.join(RESULTS, "paper_open.json")
LEDGER = os.path.join(RESULTS, "paper_trades.csv")
SUMMARY = os.path.join(RESULTS, "paper_summary.txt")

LEDGER_COLS = ["symbol", "side", "result", "entry_dt", "exit_dt", "entry", "sl", "tp",
               "exit", "qty", "atr", "bars", "pnl_r", "pnl_inr"]


def load_open():
    if os.path.exists(OPEN_F):
        try:
            return json.load(open(OPEN_F))
        except Exception:
            return {}
    return {}


def save_open(openpos):
    os.makedirs(RESULTS, exist_ok=True)
    json.dump(openpos, open(OPEN_F, "w"), indent=2)


def open_trade(symbol, sig, bar_ts, scanned_at):
    """Build a paper position from a fired signal (entry at the signal bar's close)."""
    side, atr, close = sig["side"], sig["atr"], sig["entry"]
    entry = close * (1 + SLIP_ENTRY) if side == "long" else close * (1 - SLIP_ENTRY)
    lv = risk.compute_levels(entry, atr, side, sl_mult=SL_MULT, tp_mult=SL_MULT * RR)
    if lv.risk_per_unit <= 0:
        return None
    return {
        "symbol": symbol, "side": side, "entry_dt": bar_ts, "entry_scanned": scanned_at,
        "entry": round(entry, 8), "sl": round(lv.stop_loss, 8), "tp": round(lv.take_profit, 8),
        "atr": round(atr, 8), "ru": lv.risk_per_unit, "qty": FIXED_RISK / lv.risk_per_unit,
    }


def _close(t, ex_px, result, exit_dt, bars):
    entry, qty, ru, side = t["entry"], t["qty"], t["ru"], t["side"]
    directional = (ex_px - entry) if side == "long" else (entry - ex_px)
    x_rate = MAKER if result == "win" else (TAKER + SLIP)
    cost = TAKER * entry * qty + x_rate * ex_px * qty
    pnl_inr = directional * qty - cost
    pnl_r = directional / ru - cost / (ru * qty)
    return {"symbol": t["symbol"], "side": side, "result": result,
            "entry_dt": t["entry_dt"], "exit_dt": exit_dt,
            "entry": round(entry, 6), "sl": round(t["sl"], 6), "tp": round(t["tp"], 6),
            "exit": round(ex_px, 6), "qty": round(qty, 6), "atr": round(t["atr"], 6),
            "bars": bars, "pnl_r": round(pnl_r, 3), "pnl_inr": round(pnl_inr, 2)}


def check_open(openpos, dfs):
    """Walk new bars for each open position; return (still_open, newly_closed_rows)."""
    closed = []
    still = {}
    for sym, t in openpos.items():
        df = dfs.get(sym)
        if df is None:
            still[sym] = t
            continue
        entry_ts = pd.Timestamp(t["entry_dt"])
        fwd = df[df["date"] > entry_ts]
        side = t["side"]
        done = False
        for i, (_, b) in enumerate(fwd.iterrows(), start=1):
            hi, lo = float(b["high"]), float(b["low"])
            xdt = str(b["date"])
            if side == "long":
                if lo <= t["sl"]:
                    closed.append(_close(t, t["sl"], "loss", xdt, i)); done = True; break
                if hi >= t["tp"]:
                    closed.append(_close(t, t["tp"], "win", xdt, i)); done = True; break
            else:
                if hi >= t["sl"]:
                    closed.append(_close(t, t["sl"], "loss", xdt, i)); done = True; break
                if lo <= t["tp"]:
                    closed.append(_close(t, t["tp"], "win", xdt, i)); done = True; break
        if not done:
            still[sym] = t
    return still, closed


def append_ledger(rows):
    if not rows:
        return
    os.makedirs(RESULTS, exist_ok=True)
    new_file = not os.path.exists(LEDGER)
    with open(LEDGER, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLS)
        if new_file:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def write_summary(openpos, dfs, scanned_at):
    """Recompute running totals from the closed-trade ledger + mark open positions to last close."""
    closed = []
    if os.path.exists(LEDGER):
        with open(LEDGER, newline="", encoding="utf-8") as f:
            closed = list(csv.DictReader(f))
    n = len(closed)
    wins = sum(1 for r in closed if r["result"] == "win")
    tot_r = sum(float(r["pnl_r"]) for r in closed)
    tot_inr = sum(float(r["pnl_inr"]) for r in closed)
    gp = sum(float(r["pnl_inr"]) for r in closed if float(r["pnl_inr"]) > 0)
    gl = -sum(float(r["pnl_inr"]) for r in closed if float(r["pnl_inr"]) < 0)
    pf = round(gp / gl, 2) if gl else (float("inf") if gp else 0.0)
    lines = [f"PAPER-TRADE SUMMARY  (updated {scanned_at})",
             f"closed trades: {n} | wins: {wins} | win%: {round(100*wins/n,1) if n else 0.0}",
             f"total R: {round(tot_r,2)} | total P&L: Rs {round(tot_inr,2)} | "
             f"profit factor: {pf}",
             f"open positions: {len(openpos)}", ""]
    if openpos:
        lines.append("OPEN (marked to last close):")
        for sym, t in openpos.items():
            df = dfs.get(sym)
            mtm = ""
            if df is not None and len(df):
                last = float(df["close"].iloc[-1])
                d = (last - t["entry"]) if t["side"] == "long" else (t["entry"] - last)
                u_inr = d * t["qty"] - TAKER * t["entry"] * t["qty"]
                mtm = f" | MTM Rs {round(u_inr,2)} ({round(d/t['ru'],2)}R)"
            lines.append(f"  {t['side'].upper()} {sym} entry {t['entry']} sl {t['sl']} "
                         f"tp {t['tp']} since {t['entry_dt']}{mtm}")
    txt = "\n".join(lines) + "\n"
    open(SUMMARY, "w", encoding="utf-8").write(txt)
    return txt
