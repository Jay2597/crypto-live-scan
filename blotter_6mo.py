"""blotter_6mo.py — last-6-months trade blotter for the validated equity strategies on the
point-in-time midcap universe (data/stocks/swing_pit/).

Runs both engines fresh from START (indicators use full history; portfolio starts flat):
  A. XS momentum rotation (swing_momentum_xs, top10 / mom231 / rebal 21d)  — exits = rotation
     or market gate (no per-trade stop by design)
  B. Donchian swing engine (swing_momentum, Donch50 entry / 20d-low trail / 2xATR stop) — has
     explicit per-trade stoploss

Prints every trade (entry/exit dates+prices, stop where applicable, qty, net Rs, %) plus open
positions at window end, and writes results/blotter_6mo_{xs,donchian}.csv.

Run: python blotter_6mo.py [START]      (default 2026-01-01)
"""
import csv
import os
import sys

START = sys.argv[1] if len(sys.argv) > 1 else "2026-01-01"
os.environ["SWING_DIR"] = os.path.join("data", "stocks", "swing_pit")
os.environ["SWING_START"] = START
os.makedirs("results", exist_ok=True)


def dump_csv(path, rows, fields):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def summarize(trades, open_pos, eq):
    closed_net = sum(t["net"] for t in trades)
    open_mtm = sum(p["mtm"] for p in open_pos.values())
    wins = [t for t in trades if t["net"] > 0]
    v = [x for _, x in eq]
    peak, dd = v[0], 0.0
    for x in v:
        peak = max(peak, x)
        dd = min(dd, x / peak - 1)
    print(f"  closed n={len(trades)} win={100*len(wins)/len(trades):.0f}% "
          f"closed P&L Rs{closed_net:+,.0f} | open MTM Rs{open_mtm:+,.0f} "
          f"| TOTAL Rs{closed_net+open_mtm:+,.0f} on Rs{v[0]:,.0f} "
          f"({100*(closed_net+open_mtm)/v[0]:+.2f}%) | maxDD {dd*100:.1f}%"
          if trades else
          f"  closed n=0 | open MTM Rs{open_mtm:+,.0f} | maxDD {dd*100:.1f}%")


# ---------- A. XS momentum rotation ----------
import swing_momentum_xs as xs  # noqa: E402

xs.WINDOW_START = START
xs.CFG["MOM_LB"] = 231
xs.CFG["TOP_N"] = 10
S, nifty = xs.build()
t_xs, eq_xs, open_xs = xs.run(S, nifty)

print(f"=== A. XS MOMENTUM ROTATION (top10/mom231) | {START} -> {eq_xs[-1][0]} | Rs10L ===")
print(f"{'SYM':<12} {'ENTRY':>10} {'@PX':>9} {'EXIT':>10} {'@PX':>9} {'QTY':>5} "
      f"{'NET Rs':>10} {'RET%':>7}  EXIT-WHY")
for t in t_xs:
    print(f"{t['sym']:<12} {t['ein']:>10} {t['entry']:>9.1f} {t['xout']:>10} {t['exit']:>9.1f} "
          f"{t['qty']:>5} {t['net']:>10,.0f} {t['retpct']:>+6.1f}%  {t['reason']}")
for s, p in open_xs.items():
    print(f"{s:<12} {p['edate']:>10} {p['entry']:>9.1f} {'OPEN':>10} {p['last']:>9.1f} "
          f"{p['qty']:>5} {p['mtm']:>10,.0f} {p['mtmpct']:>+6.1f}%  (marked to last close)")
summarize(t_xs, open_xs, eq_xs)
dump_csv(os.path.join("results", "blotter_6mo_xs.csv"),
         t_xs + [dict(sym=s, ein=p["edate"], entry=p["entry"], xout="OPEN", exit=p["last"],
                      qty=p["qty"], net=p["mtm"], retpct=p["mtmpct"], reason="OPEN")
                 for s, p in open_xs.items()],
         ["sym", "ein", "entry", "xout", "exit", "qty", "net", "retpct", "reason"])

# ---------- B. Donchian swing engine (explicit stoploss) ----------
import swing_momentum as sw  # noqa: E402

t_dc, eq_dc, nf, open_dc = sw.run()
print(f"\n=== B. DONCHIAN SWING (Donch{sw.CFG['DONCH_N']}, 2xATR stop, "
      f"{sw.CFG['EXIT_N']}d-low trail) | {START} -> {eq_dc[-1][0]} | Rs10L, max 5 pos ===")
print(f"{'SYM':<12} {'ENTRY':>10} {'@PX':>9} {'STOP0':>9} {'EXIT':>10} {'@PX':>9} {'QTY':>5} "
      f"{'NET Rs':>10} {'RET%':>7} {'R':>6} {'HOLD':>5}")
for t in t_dc:
    # initial stop reconstructed from recorded risk: stop0 = entry - riskRs/qty
    print(f"{t['sym']:<12} {t['ein']:>10} {t['entry']:>9.1f} "
          f"{t['entry'] - (t['net']/t['R']/t['qty'] if t['R'] else 0):>9.1f} "
          f"{t['xout']:>10} {t['exit']:>9.1f} {t['qty']:>5} {t['net']:>10,.0f} "
          f"{t['retpct']:>+6.1f}% {t['R']:>+5.2f} {t['hold']:>4}d")
for s, p in open_dc.items():
    print(f"{s:<12} {p['edate']:>10} {p['entry']:>9.1f} {p['stop']:>9.1f} {'OPEN':>10} "
          f"{p['last']:>9.1f} {p['qty']:>5} {p['mtm']:>10,.0f} {p['mtmpct']:>+6.1f}%  "
          f"(stop = current trail)")
summarize(t_dc, open_dc, eq_dc)
dump_csv(os.path.join("results", "blotter_6mo_donchian.csv"),
         t_dc + [dict(sym=s, ein=p["edate"], entry=p["entry"], xout="OPEN", exit=p["last"],
                      qty=p["qty"], net=p["mtm"], retpct=p["mtmpct"], hold=p["bars"], R="")
                 for s, p in open_dc.items()],
         ["sym", "ein", "entry", "xout", "exit", "qty", "net", "retpct", "R", "hold"])
print("\nCSVs: results/blotter_6mo_xs.csv, results/blotter_6mo_donchian.csv")
