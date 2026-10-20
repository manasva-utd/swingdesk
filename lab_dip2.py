"""
Round 2 of the dip lab: deeper limit entries, scaled exits (sell half early), faster exits.
  python lab_dip2.py [universe] [position_value]
"""
from __future__ import annotations

import itertools
import sys

import numpy as np
import pandas as pd

import config
import data
from lab_dip import prep, SPLIT


def _brk(v):
    return max(min(config.BROKERAGE_MAX, v * config.BROKERAGE_PCT / 100), config.BROKERAGE_MIN)


def leg_costs(buy_px, qty, sells):
    """sells: list of (price, qty, day_index). Returns total charges incl. one DP charge per sell day."""
    bv = buy_px * qty
    brok = _brk(bv)
    stt = bv * config.STT_PCT / 100
    exch = bv * config.EXCH_TXN_PCT / 100
    sebi = bv * config.SEBI_FEE_PCT / 100
    stamp = bv * config.STAMP_BUY_PCT / 100
    for px, q, _ in sells:
        sv = px * q
        brok += _brk(sv)
        stt += sv * config.STT_PCT / 100
        exch += sv * config.EXCH_TXN_PCT / 100
        sebi += sv * config.SEBI_FEE_PCT / 100
    gst = (brok + exch + sebi) * config.GST_PCT / 100
    dp = config.DP_CHARGE_PER_SELL * len({d for _, _, d in sells})
    return brok + stt + exch + sebi + stamp + gst + dp


def simulate(d, nifty_ok, v, pos_value):
    o, h, l, c = (d[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    rsi2, sma5, ema200, ema50, atr = (d[k].to_numpy() for k in ("RSI2", "SMA5", "EMA200", "EMA50", "ATR"))
    turn, drop3 = d["TURN_CR"].to_numpy(), d["DROP3"].to_numpy()
    idx = d.index
    n = len(d)
    mask = (rsi2 < v["rsi"]) & (c > ema200) & (ema50 > ema200) & (turn >= config.MIN_AVG_TURNOVER_CR) \
        & (c >= config.MIN_PRICE) & (drop3 <= -v["min_drop"])
    if v["mkt"]:
        mask &= nifty_ok.reindex(idx).ffill().fillna(False).to_numpy().astype(bool)
    mask[:210] = False
    mask[n - 2:] = False
    out, free = [], 0
    for i in np.flatnonzero(mask):
        if i < free:
            continue
        j = i + 1
        lim = c[i] - v["depth"] * atr[i]
        if l[j] > lim:
            continue
        px = min(o[j], lim)
        qty = int(pos_value // px)
        if qty < 2:
            continue
        a = atr[i]
        stop = px - v["stop_atr"] * a if v["stop_atr"] else None
        half_tgt = px + v["half_at"] * a if v["half_at"] else None
        rem, sells, k, why = qty, [], j, ""
        while k < n and rem > 0:
            if stop is not None and (l[k] <= stop or (k > j and o[k] <= stop)):
                spx = o[k] if (k > j and o[k] <= stop) else stop
                sells.append((spx, rem, k)); rem = 0; why = "stop"; break
            if half_tgt is not None and rem == qty and h[k] >= half_tgt and k > j - 1:
                q1 = qty // 2
                sells.append((max(half_tgt, o[k]) if k > j else half_tgt, q1, k)); rem -= q1
                if v["be_after_half"]:
                    stop = max(stop or 0, px)
            exit_now = False
            if v["exit"] == "rsi" and rsi2[k] > 65:
                exit_now, why = True, "bounce RSI2>65"
            elif v["exit"] == "sma5" and c[k] > sma5[k]:
                exit_now, why = True, "close > 5-day avg"
            elif v["exit"] == "upclose" and k > j and c[k] > h[k - 1]:
                exit_now, why = True, "close above prior high"
            if k - j + 1 >= v["hold"]:
                exit_now, why = True, "time"
            if exit_now and rem > 0:
                sells.append((c[k], rem, k)); rem = 0; break
            k += 1
        if rem > 0:
            break
        cost = leg_costs(px, qty, sells)
        gross = sum(p * q for p, q, _ in sells) - px * qty
        net = gross - cost
        out.append({"date": idx[j], "days": k - j + 1, "net": net, "charges": cost,
                    "net_pct": 100 * net / (px * qty), "gross_pct": 100 * gross / (px * qty), "why": why})
        free = k + 1
    return out


def summarize(t):
    if t.empty:
        return {"n": 0}
    w, lo = t[t["net"] > 0], t[t["net"] <= 0]
    return {"n": len(t), "win%": round(100 * len(w) / len(t), 1), "avg_net%": round(t["net_pct"].mean(), 3),
            "pf": round(w["net"].sum() / -lo["net"].sum(), 2) if len(lo) and lo["net"].sum() < 0 else 99,
            "worst%": round(t["net_pct"].min(), 2), "avg_win%": round(w["net_pct"].mean(), 2) if len(w) else 0,
            "avg_loss%": round(lo["net_pct"].mean(), 2) if len(lo) else 0}


GRID = {
    "rsi": [5, 10],
    "min_drop": [3, 5],
    "mkt": [True, False],
    "depth": [0.5, 1.0],
    "exit": ["rsi", "sma5", "upclose"],
    "half_at": [None, 0.5, 1.0],
    "be_after_half": [False, True],
    "stop_atr": [None, 3.0],
    "hold": [10],
}

if __name__ == "__main__":
    universe = sys.argv[1] if len(sys.argv) > 1 else "Nifty 200"
    pos_value = float(sys.argv[2]) if len(sys.argv) > 2 else 15000
    nifty = prep(data.get_history(config.INDEX_SYMBOL, period="5y", max_age_hours=48))
    nifty_ok = nifty["Close"] > nifty["EMA200"]
    frames = {}
    for s in data.get_universe(universe):
        try:
            df = data.get_history(s, period="5y", max_age_hours=48)
            if len(df) > 400:
                frames[s] = prep(df)
        except Exception:
            pass
    last60 = nifty.index[-60]
    rows, keys = [], list(GRID)
    for combo in itertools.product(*GRID.values()):
        v = dict(zip(keys, combo))
        if v["half_at"] is None and v["be_after_half"]:
            continue
        tr = [t for s, d in frames.items() for t in simulate(d, nifty_ok, v, pos_value)]
        t = pd.DataFrame(tr)
        if t.empty:
            continue
        a, b, o, r = summarize(t), summarize(t[t.date < SPLIT]), summarize(t[t.date >= SPLIT]), summarize(t[t.date >= last60])
        rows.append({**{k: str(v[k]) for k in keys}, "N": a["n"], "win": a["win%"], "avg%": a["avg_net%"], "pf": a["pf"],
                     "worst": a["worst%"], "avgW": a["avg_win%"], "avgL": a["avg_loss%"],
                     "IS_win": b.get("win%"), "IS_avg": b.get("avg_net%"), "OOS_n": o["n"], "OOS_win": o.get("win%"),
                     "OOS_avg": o.get("avg_net%"), "L60_n": r["n"], "L60_win": r.get("win%"), "L60_avg": r.get("avg_net%")})
    out = pd.DataFrame(rows)
    out.to_csv("lab_dip2_results.csv", index=False)
    pd.set_option("display.width", 280)
    good = out[(out.IS_win >= 70) & (out.OOS_win >= 70) & (out.IS_avg > 0) & (out.OOS_avg > 0)]
    print(f"variants {len(out)} | 70%+ win AND profitable in both periods: {len(good)}")
    print(good.sort_values("OOS_avg", ascending=False).head(15).to_string(index=False))
    print("\nBest win rates overall:")
    print(out.sort_values("win", ascending=False).head(10).to_string(index=False))
