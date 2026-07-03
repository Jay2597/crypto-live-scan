"""xs_pnl_per1000.py — per-trade P&L statement for the XS momentum stock book, normalized to
Rs1,000 invested per trade (fractional qty = 1000/entry).

Costs itemized per trade at this scale (Zerodha CNC delivery):
  STT 0.1% buy + 0.1% sell | stamp 0.015% buy | exch txn 0.00297%/side | SEBI 0.0001%/side |
  GST 18% on (txn+SEBI) | slippage 0.05%/side.
The flat DP fee (Rs15.93/sell) is reported in a separate column and NOT deducted from the
normalized net (it is a fixed fee: 1.6% of a literal Rs1k trade but ~0.01% at real Rs1L+ size).

Output: printed statement + results/xs_pnl_per1000.csv
"""
import csv
import os

import swing_momentum_xs as xs

CAP = 1000.0
xs.CFG["MOM_LB"] = 231
xs.CFG["TOP_N"] = 10

S, nifty = xs.build()
trades, eq, open_pos = xs.run(S, nifty)

STT, STAMP, TXN, SEBI, GST, SLIP = (xs.CFG["STT"], xs.CFG["STAMP_BUY"], xs.CFG["TXN"],
                                    xs.CFG["SEBI"], xs.CFG["GST"], xs.CFG["SLIP"])
rows = []
for i, t in enumerate(trades, 1):
    qty = CAP / t["entry"]
    buy_val = CAP
    sell_val = qty * t["exit"]
    gross = sell_val - buy_val
    stt = STT * (buy_val + sell_val)
    stamp = STAMP * buy_val
    txn = (TXN + SEBI) * (buy_val + sell_val)
    gst = GST * txn
    slip = SLIP * (buy_val + sell_val)
    cost = stt + stamp + txn + gst + slip
    net = gross - cost
    rows.append(dict(no=i, sym=t["sym"], entry_date=t["ein"], entry_px=round(t["entry"], 2),
                     exit_date=t["xout"], exit_px=round(t["exit"], 2),
                     qty=round(qty, 4), buy_value=round(buy_val, 2),
                     sell_value=round(sell_val, 2), gross_pnl=round(gross, 2),
                     stt=round(stt, 2), stamp=round(stamp, 2),
                     exch_gst=round(txn + gst, 2), slippage=round(slip, 2),
                     total_cost=round(cost, 2), net_pnl=round(net, 2),
                     net_pct=round(net / CAP * 100, 2), dp_flat_note=15.93,
                     exit_reason=t["reason"]))

os.makedirs("results", exist_ok=True)
with open(os.path.join("results", "xs_pnl_per1000.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

print("P&L STATEMENT — XS MOMENTUM STOCK BOOK | Rs1,000 invested per trade | 2021-07 -> 2026-07")
print(f"{'#':>3} {'SYM':<11} {'ENTRY':>10} {'@PX':>9} {'EXIT':>10} {'@PX':>9} "
      f"{'GROSS':>8} {'COSTS':>6} {'NET Rs':>8} {'NET%':>7}")
for r in rows:
    print(f"{r['no']:>3} {r['sym']:<11} {r['entry_date']:>10} {r['entry_px']:>9.1f} "
          f"{r['exit_date']:>10} {r['exit_px']:>9.1f} {r['gross_pnl']:>+8.1f} "
          f"{r['total_cost']:>6.1f} {r['net_pnl']:>+8.1f} {r['net_pct']:>+6.1f}%")

tot_g = sum(r["gross_pnl"] for r in rows)
tot_c = sum(r["total_cost"] for r in rows)
tot_n = sum(r["net_pnl"] for r in rows)
wins = [r for r in rows if r["net_pnl"] > 0]
loss = [r for r in rows if r["net_pnl"] <= 0]
print("\n================ SUMMARY (per Rs1,000-per-trade basis) ================")
print(f" trades          : {len(rows)}   (wins {len(wins)} / losses {len(loss)}, "
      f"win rate {100*len(wins)/len(rows):.1f}%)")
print(f" capital deployed: Rs{CAP*len(rows):,.0f} total turnover (Rs1,000 x {len(rows)} trades)")
print(f" gross P&L       : Rs{tot_g:+,.2f}")
print(f" total costs     : Rs{tot_c:,.2f}  (avg Rs{tot_c/len(rows):.2f}/trade = "
      f"{100*tot_c/(CAP*len(rows)):.2f}% of turnover)")
print(f" NET P&L         : Rs{tot_n:+,.2f}  ({100*tot_n/(CAP*len(rows)):+.1f}% per rupee "
      f"deployed per trade)")
print(f" avg win         : Rs{sum(r['net_pnl'] for r in wins)/len(wins):+.2f}   "
      f"avg loss: Rs{sum(r['net_pnl'] for r in loss)/len(loss):+.2f}")
print(f" best trade      : {max(rows,key=lambda r:r['net_pnl'])['sym']} "
      f"Rs{max(r['net_pnl'] for r in rows):+,.2f}")
print(f" worst trade     : {min(rows,key=lambda r:r['net_pnl'])['sym']} "
      f"Rs{min(r['net_pnl'] for r in rows):+,.2f}")
print(f" NOTE: flat DP fee Rs15.93/sell excluded from normalized net (would be "
      f"Rs{15.93*len(rows):,.0f} total at literal Rs1k size; ~0.01% at real Rs1L+ size)")
print("\nCSV: results/xs_pnl_per1000.csv (full itemized costs per trade)")
