"""swing_momentum.py — daily trend-filtered momentum SWING strategy (long-only), CNC delivery.

Thesis: at daily horizon a multi-day winner captures a 4-15% move, so a ~0.35% round-trip cost is
small (vs scalping where it was the whole move). Edge source: ride trends in names that are in a
confirmed uptrend; cut losers fast with a trailing stop. Long-only, market-regime gated.

RULES (long-only)
  Eligibility (regime): close > SMA200  AND  SMA200 rising (vs 20d ago)  AND  NIFTY > its SMA200
  Entry:  close makes a new DONCH_N-day high (Donchian breakout)         -> fill NEXT day open
  Stop:   initial = entry - ATR_STOP*ATR(14); then trail with Chandelier = HH(22) - 3*ATR(22)
          (ratchets up only). Exit intraday if low <= stop (gap-adjusted to open).
  One position per stock; portfolio holds up to MAXPOS concurrent, equal-weight, compounding.

COSTS — Zerodha CNC EQUITY DELIVERY (verify before live):
  brokerage 0 | STT 0.1% buy + 0.1% sell | stamp 0.015% buy | exch txn 0.00297%/side |
  SEBI 0.0001%/side | GST 18% on (txn+sebi) | DP Rs15.93 flat on sell | slippage 0.05%/side.

Run: python swing_momentum.py
"""
import json, glob, os, math, statistics as st
from collections import defaultdict

DDIR=os.environ.get("SWING_DIR", os.path.join("data","stocks","swing_daily"))
CFG=dict(
    DONCH_N=50, ATR_N=14, ATR_STOP=2.0, CH_N=22, CH_MULT=3.0, SMA_TREND=200, SMA_SLOPE_LB=20,
    EXIT_MODE="donchlow", EXIT_N=20,          # 'donchlow' (Turtle, lets winners run) or 'chandelier'
    USE_MARKET_FILTER=True,
    CAP0=1_000_000, MAXPOS=5,
    # CNC cost rates
    STT=0.001, STAMP_BUY=0.00015, TXN=0.0000297, SEBI=0.000001, GST=0.18,
    DP_FLAT=15.93, SLIP=0.0005,
)

def sma(x,n):
    out=[None]*len(x); s=0.0
    for i in range(len(x)):
        s+=x[i]
        if i>=n: s-=x[i-n]
        if i>=n-1: out[i]=s/n
    return out
def atr(h,l,c,n):
    tr=[h[0]-l[0]]
    for i in range(1,len(c)): tr.append(max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])))
    out=[tr[0]]*len(c)
    for i in range(1,len(c)): out[i]=(out[i-1]*(n-1)+tr[i])/n
    return out
def roll_max(x,n):
    out=[None]*len(x)
    for i in range(len(x)):
        if i>=n: out[i]=max(x[i-n:i])      # prior n bars (exclude current) for breakout test
    return out
def roll_hh(x,n):
    out=[None]*len(x)
    for i in range(len(x)):
        if i>=n-1: out[i]=max(x[i-n+1:i+1])
    return out
def roll_ll(x,n):
    out=[None]*len(x)
    for i in range(len(x)):
        if i>=n: out[i]=min(x[i-n:i])      # prior n lows (exclude current) = trailing exit level
    return out

def load(path):
    b=json.load(open(path))
    return ([x["open"] for x in b],[x["high"] for x in b],[x["low"] for x in b],
            [x["close"] for x in b],[x["date"][:10] for x in b])

def buy_cost(px,q):
    to=px*q
    return CFG["STT"]*to+CFG["STAMP_BUY"]*to+CFG["TXN"]*to+CFG["SEBI"]*to+CFG["GST"]*(CFG["TXN"]+CFG["SEBI"])*to+CFG["SLIP"]*to
def sell_cost(px,q):
    to=px*q
    return CFG["STT"]*to+CFG["TXN"]*to+CFG["SEBI"]*to+CFG["GST"]*(CFG["TXN"]+CFG["SEBI"])*to+CFG["DP_FLAT"]+CFG["SLIP"]*to

def prep(path):
    o,h,l,c,d=load(path)
    return dict(o=o,h=h,l=l,c=c,d=d,sma200=sma(c,CFG["SMA_TREND"]),
                donch=roll_max(h,CFG["DONCH_N"]),atr=atr(h,l,c,CFG["ATR_N"]),
                hh=roll_hh(h,CFG["CH_N"]),atrch=atr(h,l,c,CFG["CH_N"]),sma50=sma(c,50),
                exitll=roll_ll(l,CFG["EXIT_N"]))

def build():
    files=sorted(glob.glob(os.path.join(DDIR,"*_day.json")))
    S={}
    for f in files:
        s=os.path.basename(f).replace("_day.json","")
        if s=="NIFTY": continue
        S[s]=prep(f)
    nx=load(os.path.join(DDIR,"NIFTY_day.json"))
    nifty=dict(d=nx[4],c=nx[3],sma200=sma(nx[3],CFG["SMA_TREND"]))
    return S,nifty

def regime_ok(P,i,nifty,nidx):
    if i<CFG["SMA_TREND"]+CFG["SMA_SLOPE_LB"]: return False
    if P["sma200"][i] is None or P["sma200"][i-CFG["SMA_SLOPE_LB"]] is None: return False
    up = P["c"][i]>P["sma200"][i] and P["sma200"][i]>P["sma200"][i-CFG["SMA_SLOPE_LB"]]
    if not up: return False
    if CFG["USE_MARKET_FILTER"]:
        j=nidx.get(P["d"][i])
        if j is None or nifty["sma200"][j] is None or nifty["c"][j]<=nifty["sma200"][j]: return False
    return True

def run():
    S,nifty=build()
    nidx={dt:i for i,dt in enumerate(nifty["d"])}
    # generate raw entry signals per stock (no overlap handled in portfolio)
    cal=sorted(nidx)                       # trading calendar from NIFTY
    idxmap={s:{P["d"][i]:i for i in range(len(P["d"]))} for s,P in S.items()}
    # portfolio sim
    cash=CFG["CAP0"]; equity=CFG["CAP0"]; positions={}   # sym -> dict
    pending=[]                              # entries queued for next day
    trades=[]; eq_curve=[]
    for dt in cal:
        # ---- 1. update/exit open positions on day dt ----
        for s in list(positions.keys()):
            P=S[s]; i=idxmap[s].get(dt)
            if i is None: continue
            pos=positions[s]
            # trail stop up (ratchet) by chosen mode
            if CFG["EXIT_MODE"]=="chandelier":
                lvl=(P["hh"][i]-CFG["CH_MULT"]*P["atrch"][i]) if P["hh"][i] is not None else pos["stop"]
            else:                                   # donchlow (Turtle): trail at N-day low
                lvl=P["exitll"][i] if P["exitll"][i] is not None else pos["stop"]
            pos["stop"]=max(pos["stop"],lvl)
            exit_px=None
            if P["l"][i]<=pos["stop"]:
                exit_px=min(P["o"][i],pos["stop"]) if P["o"][i]<pos["stop"] else pos["stop"]
            if exit_px is not None:
                q=pos["qty"]; sc=sell_cost(exit_px,q)
                proceeds=exit_px*q-sc; cash+=proceeds
                net=(exit_px-pos["entry"])*q-pos["buycost"]-sc
                trades.append(dict(sym=s,ein=pos["edate"],xout=dt,entry=pos["entry"],exit=exit_px,
                    qty=q,net=net,retpct=net/(pos["entry"]*q)*100,hold=pos["bars"],
                    R=net/pos["riskRs"] if pos["riskRs"] else 0))
                del positions[s]
            else:
                pos["bars"]+=1
        # ---- 2. execute pending entries at today's open ----
        newpend=[]
        for s in pending:
            P=S[s]; i=idxmap[s].get(dt)
            if i is None or s in positions: continue
            if len(positions)>=CFG["MAXPOS"]: continue
            entry=P["o"][i]
            slot=equity/CFG["MAXPOS"]
            q=int(slot/entry)
            if q<1: continue
            bc=buy_cost(entry,q)
            if entry*q+bc>cash: continue
            cash-=entry*q+bc
            stop0=entry-CFG["ATR_STOP"]*P["atr"][i]
            positions[s]=dict(entry=entry,qty=q,stop=stop0,edate=dt,bars=0,buycost=bc,
                              riskRs=CFG["ATR_STOP"]*P["atr"][i]*q)
        pending=[]
        # ---- 3. scan for new signals on close of dt (fill next day) ----
        for s,P in S.items():
            i=idxmap[s].get(dt)
            if i is None or i<1 or s in positions: continue
            if P["donch"][i] is None: continue
            if P["c"][i]>P["donch"][i] and regime_ok(P,i,nifty,nidx):
                pending.append(s)
        # ---- 4. mark-to-market equity ----
        mtm=cash
        for s,pos in positions.items():
            i=idxmap[s].get(dt)
            px=S[s]["c"][i] if i is not None else pos["entry"]
            mtm+=px*pos["qty"]
        equity=mtm; eq_curve.append((dt,equity))
    return trades,eq_curve,nifty

def metrics(trades,eq,nifty):
    if not trades: print("no trades"); return
    n=len(trades); net=[t["net"] for t in trades]
    wins=[t for t in trades if t["net"]>0]; losses=[t for t in trades if t["net"]<=0]
    pf=sum(t["net"] for t in wins)/(-sum(t["net"] for t in losses)) if losses else 99
    tot=sum(net)
    eqv=[e for _,e in eq]; dd=0;peak=eqv[0]
    for v in eqv: peak=max(peak,v); dd=min(dd,(v/peak-1))
    years=(len(eq))/252
    cagr=(eqv[-1]/eqv[0])**(1/years)-1
    rets=[eqv[i]/eqv[i-1]-1 for i in range(1,len(eqv))]
    sharpe=(st.mean(rets)/ (st.pstdev(rets) or 1))*math.sqrt(252)
    # nifty buy&hold over same window
    nd=nifty["d"]; nc=nifty["c"]
    i0=nd.index(eq[0][0]) if eq[0][0] in nd else 0
    nbh=(nc[-1]/nc[i0])**(1/years)-1
    npeak=nc[i0];ndd=0
    for v in nc[i0:]: npeak=max(npeak,v); ndd=min(ndd,(v/npeak-1))
    print(f"\n=== PORTFOLIO (Rs{CFG['CAP0']:,}, max {CFG['MAXPOS']} pos, compounding, net of CNC costs) ===")
    print(f"  final Rs{eqv[-1]:,.0f}  | total return {(eqv[-1]/eqv[0]-1)*100:+.1f}%  CAGR {cagr*100:+.1f}%  "
          f"Sharpe {sharpe:.2f}  maxDD {dd*100:.1f}%")
    print(f"  BENCHMARK Nifty B&H: CAGR {nbh*100:+.1f}%  maxDD {ndd*100:.1f}%   (window {eq[0][0]} -> {eq[-1][0]})")
    print(f"\n=== TRADES ===")
    print(f"  n={n}  win={100*len(wins)/n:.1f}%  PF={pf:.2f}  avg {st.mean(t['retpct'] for t in trades):+.2f}%/trade  "
          f"avgR {st.mean(t['R'] for t in trades):+.2f}  avg hold {st.mean(t['hold'] for t in trades):.0f}d")
    print(f"  avg win {st.mean(t['retpct'] for t in wins):+.1f}%  avg loss {st.mean(t['retpct'] for t in losses):+.1f}%  "
          f"biggest win {max(t['retpct'] for t in trades):+.1f}%  biggest loss {min(t['retpct'] for t in trades):+.1f}%")
    # by year
    print("  by entry year:")
    yr=defaultdict(list)
    for t in trades: yr[t["ein"][:4]].append(t["net"])
    for y in sorted(yr):
        v=yr[y]; w=sum(1 for x in v if x>0)
        print(f"    {y}: n={len(v):3} win={100*w/len(v):4.1f}% NET Rs{sum(v):9,.0f}")

def quick(t,e,nf):
    if not t: return "no trades"
    eqv=[v for _,v in e]; peak=eqv[0];dd=0
    for v in eqv: peak=max(peak,v);dd=min(dd,v/peak-1)
    yrs=len(e)/252; cagr=(eqv[-1]/eqv[0])**(1/yrs)-1
    rets=[eqv[i]/eqv[i-1]-1 for i in range(1,len(eqv))]
    sh=(st.mean(rets)/(st.pstdev(rets) or 1))*math.sqrt(252)
    w=[x for x in t if x["net"]>0]; ls=[x for x in t if x["net"]<=0]
    pf=sum(x["net"] for x in w)/(-sum(x["net"] for x in ls)) if ls else 99
    aw=st.mean(x["retpct"] for x in w) if w else 0; al=st.mean(x["retpct"] for x in ls) if ls else 0
    return (f"n={len(t):3} win={100*len(w)/len(t):4.1f}% PF={pf:4.2f} CAGR={cagr*100:+5.1f}% "
            f"Sharpe={sh:+.2f} maxDD={dd*100:5.1f}% avgWin={aw:+.1f}% avgLoss={al:+.1f}%")

if __name__=="__main__":
    import sys
    if "--grid" in sys.argv:
        print("GRID: trend SMA200 + Nifty filter | 23 stocks 2021-2026 | net CNC costs\n")
        print(f"{'entryDonch':>10} {'exitMode':>11} {'exitN':>5} {'atrStop':>7}  result")
        for dn in (20,50):
            for mode,en in (("donchlow",10),("donchlow",20),("chandelier",22)):
                for ast in (2.0,3.0):
                    CFG["DONCH_N"]=dn; CFG["EXIT_MODE"]=mode; CFG["EXIT_N"]=en; CFG["ATR_STOP"]=ast
                    t,e,nf=run()
                    print(f"{dn:>10} {mode:>11} {en:>5} {ast:>7}  {quick(t,e,nf)}")
    else:
        print(f"DAILY SWING-MOMENTUM | Donchian{CFG['DONCH_N']} | exit {CFG['EXIT_MODE']}{CFG['EXIT_N']} "
              f"| trend SMA{CFG['SMA_TREND']}+Nifty | 23 stocks 2021-2026")
        t,e,nf=run(); metrics(t,e,nf)
