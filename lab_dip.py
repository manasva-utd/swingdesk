"""
Strategy lab for "buy the dip in strong stocks" (mean reversion).
Tests rule variants on 5 years of daily data, after full Angel One delivery charges.
Reports: all years, 2021-24 (in-sample), 2025-26 (out-of-sample), and the last 60 sessions.
  python lab_dip.py
"""
from __future__ import annotations

import itertools
import sys

import numpy as np
import pandas as pd

import charges
import config
import data
import indicators as ind

SPLIT = pd.Timestamp("2025-01-01")


def prep(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c = d["Close"]
    d["EMA200"] = ind.ema(c, 200)
    d["EMA50"] = ind.ema(c, 50)
    d["SMA5"] = c.rolling(5).mean()
    d["RSI2"] = ind.rsi(c, 2)
    d["ATR"] = ind.atr(d, 14)
    d["TURN_CR"] = (c * d["Volume"]).rolling(20).mean() / 1e7
    d["DOWN3"] = (c.diff() < 0).rolling(3).sum() == 3
    d["DROP3"] = 100 * (c / c.shift(3) - 1)
    return d


def simulate(d: pd.DataFrame, nifty_ok: pd.Series | None, v: dict, pos_value: float) -> list[dict]:
    o, h, l, c = (d[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    rsi2, sma5, ema200, ema50, atr = (d[k].to_numpy() for k in ("RSI2", "SMA5", "EMA200", "EMA50", "ATR"))
    turn, drop3 = d["TURN_CR"].to_numpy(), d["DROP3"].to_numpy()
    idx = d.index
    nok = nifty_ok.reindex(idx).ffill().fillna(False).to_numpy() if nifty_ok is not None else None
    n = len(d)
    mask = (rsi2 < v["rsi"]) & (c > ema200) & (turn >= config.MIN_AVG_TURNOVER_CR) & (c >= config.MIN_PRICE) & (drop3 <= -v["min_drop"])
    if v["trend"] == "strong":
        mask &= ema50 > ema200
    if nok is not None and v["mkt"]:
        mask &= nok.astype(bool)
    mask[:210] = False
    mask[n - 2:] = False
    trades, free = [], 0
    for i in np.flatnonzero(mask):
        if i < free:
            continue
        # entry next session
        j = i + 1
        if v["entry"] == "open":
            px = o[j]
        else:  # limit below signal close
            lim = c[i] - v["entry"] * atr[i]
            if l[j] > lim:
                continue
            px = min(o[j], lim)
        stop = px - v["stop_atr"] * atr[i] if v["stop_atr"] else None
        exit_px, k, why = None, j, ""
        while k < n:
            if stop is not None:
                if k > j and o[k] <= stop:
                    exit_px, why = o[k], "gap stop"
                    break
                if l[k] <= stop:
                    exit_px, why = stop, "stop"
                    break
            if v["exit"] == "sma5" and c[k] > sma5[k] and k >= j:
                exit_px, why = c[k], "bounce (close > 5-day avg)"
                break
            if v["exit"] == "rsi" and rsi2[k] > 65 and k >= j:
                exit_px, why = c[k], "bounce (RSI2 > 65)"
                break
            if k - j + 1 >= v["hold"]:
                exit_px, why = c[k], "time"
                break
            k += 1
        if exit_px is None:
            break
        qty = int(pos_value // px)
        if qty < 1:
            continue
        ch = charges.round_trip(px, exit_px, qty)
        trades.append({"date": idx[j], "exit_date": idx[k], "days": k - j + 1, "entry": px, "exit": exit_px,
                       "ret_pct": 100 * (exit_px / px - 1), "net": ch["net_pnl"], "charges": ch["total_charges"],
                       "net_pct": 100 * ch["net_pnl"] / (px * qty), "why": why})
        free = k + 1
    return trades


def summarize(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"n": 0}
    w, lo = t[t["net"] > 0], t[t["net"] <= 0]
    return {"n": len(t), "win%": round(100 * len(w) / len(t), 1),
            "avg_net%": round(t["net_pct"].mean(), 3),
            "avg_win%": round(w["net_pct"].mean(), 2) if len(w) else 0,
            "avg_loss%": round(lo["net_pct"].mean(), 2) if len(lo) else 0,
            "worst%": round(t["net_pct"].min(), 2), "pf": round(w["net"].sum() / -lo["net"].sum(), 2) if len(lo) and lo["net"].sum() < 0 else 99}


GRID = {
    "rsi": [5, 10],
    "min_drop": [0, 3],
    "trend": ["any", "strong"],
    "mkt": [False, True],
    "entry": ["open", 0.5],
    "exit": ["sma5", "rsi"],
    "stop_atr": [None, 3.0],
    "hold": [5, 10],
}

if __name__ == "__main__":
    universe = sys.argv[1] if len(sys.argv) > 1 else "Nifty 200"
    pos_value = float(sys.argv[2]) if len(sys.argv) > 2 else 15000
    nifty = data.get_history(config.INDEX_SYMBOL, period="5y", max_age_hours=24)
    nn = prep(nifty)
    nifty_ok = nn["Close"] > nn["EMA200"]
    frames = {}
    for s in data.get_universe(universe):
        try:
            df = data.get_history(s, period="5y", max_age_hours=24)
            if len(df) > 400:
                frames[s] = prep(df)
        except Exception:
            pass
    last60 = nifty.index[-60]
    print("stocks", len(frames), "| last-60 window starts", last60.date())
    rows = []
    keys = list(GRID)
    for combo in itertools.product(*GRID.values()):
        v = dict(zip(keys, combo))
        tr = []
        for s, d in frames.items():
            for t in simulate(d, nifty_ok, v, pos_value):
                t["symbol"] = s
                tr.append(t)
        t = pd.DataFrame(tr)
        if t.empty:
            continue
        a = summarize(t)
        b = summarize(t[t["date"] < SPLIT])
        o = summarize(t[t["date"] >= SPLIT])
        r = summarize(t[t["date"] >= last60])
        rows.append({**{k: str(v[k]) for k in keys}, "N": a["n"], "win": a["win%"], "avg%": a["avg_net%"],
                     "pf": a["pf"], "worst": a["worst%"], "IS_win": b.get("win%"), "IS_avg": b.get("avg_net%"),
                     "OOS_n": o["n"], "OOS_win": o.get("win%"), "OOS_avg": o.get("avg_net%"),
                     "L60_n": r["n"], "L60_win": r.get("win%"), "L60_avg": r.get("avg_net%")})
    out = pd.DataFrame(rows)
    out.to_csv("lab_dip_results.csv", index=False)
    pd.set_option("display.width", 260)
    good = out[(out["IS_win"] >= 70) & (out["OOS_win"] >= 70) & (out["IS_avg"] > 0) & (out["OOS_avg"] > 0)]
    print(f"variants: {len(out)} | passing 70% win + profit in BOTH periods: {len(good)}")
    cols = keys + ["N", "win", "avg%", "pf", "worst", "IS_win", "IS_avg", "OOS_n", "OOS_win", "OOS_avg", "L60_n", "L60_win", "L60_avg"]
    print(good.sort_values(["OOS_avg"], ascending=False)[cols].head(20).to_string(index=False))
    print("\nTop by overall win rate (any profitability):")
    print(out.sort_values("win", ascending=False)[cols].head(8).to_string(index=False))
