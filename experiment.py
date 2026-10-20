"""
Strategy lab: test rule variants on real data, split into in-sample (tuning) and
out-of-sample (never used for tuning) periods. Reports R before AND after charges.
  python experiment.py
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

import charges
import config
import data
import indicators as ind
import strategy

SPLIT = pd.Timestamp("2025-01-01")


def regime_series(nifty: pd.DataFrame) -> pd.Series:
    n = ind.add_all(nifty)
    green = (n["Close"] > n["EMA50"]) & (n["EMA20"] > n["EMA50"])
    amber = (~green) & (n["Close"] > n["EMA200"])
    return pd.Series(np.where(green, "GREEN", np.where(amber, "AMBER", "RED")), index=n.index)


_SETUPS: dict = {}


def signals(d: pd.DataFrame, reg: pd.Series, v: dict) -> list[int]:
    out = []
    key = id(d)
    if key not in _SETUPS:
        _SETUPS[key] = [strategy.detect_setup(d, i) for i in range(len(d) - 2)]
    setups = _SETUPS[key]
    r = reg.reindex(d.index).ffill()
    for i in range(210, len(d) - 2):
        s = setups[i]
        if s == "NONE" or s not in v["setups"]:
            continue
        if r.iloc[i] not in v["regimes"]:
            continue
        row = d.iloc[i]
        if v["rs_min"] is not None and not (row["RS_3M"] > v["rs_min"]):
            continue
        if row["DIST_52W_HIGH_PCT"] > v["max_dist_high"]:
            continue
        if row["EMA50"] <= row["EMA200"] or row["TURNOVER_CR"] < config.MIN_AVG_TURNOVER_CR:
            continue
        out.append((i, s))
    return out


def simulate(d, i, setup, v, capital):
    r = d.iloc[i]
    atr = r["ATR"]
    entry = r["High"] * 1.002
    if setup == "BREAKOUT":
        stop = max(r["HIGH20"] - 0.5 * atr, entry - 2 * atr)
    else:
        stop = max(d["Low"].iloc[i - 4:i + 1].min() - 0.25 * atr, entry - 2.5 * atr)
    stop = min(stop, entry - atr)
    risk = entry - stop
    if risk / entry * 100 > config.MAX_STOP_PCT:
        return None
    fill = None
    for k in (1, 2):
        j = i + k
        if j < len(d) and d["High"].iloc[j] >= entry:
            fill = (j, max(d["Open"].iloc[j], entry))
            break
    if not fill:
        return None
    j0, px = fill
    tgt = entry + v["target_R"] * risk if v["target_R"] else None
    cur_stop = stop
    exit_px, exit_j = None, None
    for j in range(j0, min(j0 + v["hold"], len(d))):
        b = d.iloc[j]
        if j > j0 and b["Open"] <= cur_stop:
            exit_px, exit_j = b["Open"], j
            break
        if b["Low"] <= cur_stop:
            exit_px, exit_j = cur_stop, j
            break
        if tgt and b["High"] >= tgt:
            exit_px, exit_j = max(tgt, b["Open"]) if j > j0 else tgt, j
            break
        # trailing logic, decided on the close, applied from the next day
        if v["breakeven_at_1R"] and b["Close"] >= px + risk:
            cur_stop = max(cur_stop, px)
        if v["trail_ema20"] and b["Close"] >= px + v["trail_after_R"] * risk:
            cur_stop = max(cur_stop, b["EMA20"] - 0.25 * b["ATR"])
    if exit_px is None:
        exit_j = min(j0 + v["hold"] - 1, len(d) - 1)
        exit_px = d["Close"].iloc[exit_j]
    risk_rs_target = capital * config.RISK_PER_TRADE_PCT / 100
    qty = int(min(risk_rs_target / risk, capital / px))
    if qty < 1:
        return None
    c = charges.round_trip(px, exit_px, qty)
    rr = risk * qty
    return {"date": d.index[j0], "gross_R": (exit_px - px) * qty / rr, "net_R": c["net_pnl"] / rr,
            "days": exit_j - j0 + 1, "exit_j": exit_j}


def run_variant(frames, reg, v, capital):
    rows = []
    for sym, df in frames.items():
        d = df
        nxt = 0
        for i, s in signals(d, reg, v):
            if i < nxt:
                continue
            t = simulate(d, i, s, v, capital)
            if t:
                t["symbol"] = sym
                rows.append(t)
                nxt = t["exit_j"] + 1
    return pd.DataFrame(rows)


def summarize(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"n": 0}
    return {"n": len(t), "win%": round(100 * (t["net_R"] > 0).mean(), 1),
            "gross_R": round(t["gross_R"].mean(), 3), "net_R": round(t["net_R"].mean(), 3),
            "days": round(t["days"].mean(), 1)}


def load_frames(symbols, nifty):
    frames = {}
    for s in symbols:
        try:
            df = data.get_history(s, period="5y", max_age_hours=24)
            if len(df) > 300:
                frames[s] = ind.add_all(df, nifty["Close"])
        except Exception:
            pass
    return frames


GRID = {
    "regimes": [("GREEN",), ("GREEN", "AMBER")],
    "setups": [("BREAKOUT", "PULLBACK"), ("PULLBACK",), ("BREAKOUT",)],
    "rs_min": [None, 5],
    "max_dist_high": [15, 8],
    "target_R": [2.0, None],          # None = no fixed target, ride the trend
    "hold": [10, 20, 30],
    "breakeven_at_1R": [True],
    "trail_ema20": [True],
    "trail_after_R": [1.0],
}

if __name__ == "__main__":
    import sys

    nifty = data.get_history("^NSEI", period="5y", max_age_hours=24)
    reg = regime_series(nifty)
    syms = data.get_universe(sys.argv[1]) if len(sys.argv) > 1 else config.FALLBACK_UNIVERSE[:40]
    frames = load_frames(syms, nifty)
    print("stocks", len(frames))
    res = []
    keys = list(GRID)
    for combo in itertools.product(*GRID.values()):
        v = dict(zip(keys, combo))
        t = run_variant(frames, reg, v, capital=100000)  # large capital = isolate signal quality
        if t.empty:
            continue
        ins, oos = t[t["date"] < SPLIT], t[t["date"] >= SPLIT]
        a, b = summarize(ins), summarize(oos)
        res.append({**{k: str(v[k]) for k in keys[:6]}, "IS_n": a["n"], "IS_grossR": a.get("gross_R"),
                    "IS_netR": a.get("net_R"), "OOS_n": b["n"], "OOS_grossR": b.get("gross_R"),
                    "OOS_netR": b.get("net_R"), "days": a.get("days")})
    out = pd.DataFrame(res).sort_values("IS_netR", ascending=False)
    out.to_csv("experiment_results.csv", index=False)
    pd.set_option("display.width", 250)
    print(out.head(15).to_string(index=False))
    print("...")
    print(out.tail(5).to_string(index=False))
