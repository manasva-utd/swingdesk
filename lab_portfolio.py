"""Realistic money test: Rs 15,000, max N positions at once, take signals in date order."""
import sys
import pandas as pd
import config, data
from lab_dip import prep
import lab_dip2

V = dict(rsi=10, min_drop=3, mkt=False, depth=1.0, exit="upclose", half_at=1.0, be_after_half=True, stop_atr=3.0, hold=10)

def run(frames, nifty_ok, capital=15000, slots=1, start=None):
    per = capital / slots
    tr = []
    for s, d in frames.items():
        for t in lab_dip2.simulate(d, nifty_ok, V, per):
            t["symbol"] = s; t["exit_date"] = d.index[d.index.get_loc(t["date"]) + t["days"] - 1]
            tr.append(t)
    t = pd.DataFrame(tr).sort_values("date")
    if start is not None:
        t = t[t.date >= start]
    taken, busy = [], []
    for _, r in t.iterrows():
        busy = [e for e in busy if e >= r["date"]]
        if len(busy) < slots:
            taken.append(r); busy.append(r["exit_date"])
    k = pd.DataFrame(taken)
    eq = capital + k["net"].cumsum()
    yrs = (k.date.max() - k.date.min()).days / 365.25
    return {"trades": len(k), "per_year": round(len(k)/yrs, 1), "win%": round(100*(k.net>0).mean(), 1),
            "net_rs": round(k.net.sum()), "charges_rs": round(k.charges.sum()),
            "return_%": round(100*k.net.sum()/capital, 1), "per_year_%": round(100*k.net.sum()/capital/yrs, 1),
            "max_dd_rs": round((eq - eq.cummax()).min()), "worst_trade_rs": round(k.net.min())}, k

if __name__ == "__main__":
    nifty = prep(data.get_history(config.INDEX_SYMBOL, period="5y", max_age_hours=48)); ok = nifty["Close"] > nifty["EMA200"]
    frames = {s: prep(df) for s in data.get_universe("Nifty 200")
              for df in [data.get_history(s, period="5y", max_age_hours=48)] if len(df) > 400}
    for slots in (1, 2):
        for label, st in (("5y", None), ("2025-26", pd.Timestamp("2025-01-01")), ("last 60d", nifty.index[-60])):
            s, k = run(frames, ok, 15000, slots, st)
            print(f"slots={slots} {label:9s}", s)
