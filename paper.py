"""
Automatic paper trading. Every trade is decided by fixed rules and settled with real
prices and real Angel One charges, so the results are an honest test of the rules.

Strategies logged:
  INTRADAY_ORB  - 10:30 AM opening-range ideas (long or short), closed by 3:15 PM. Top 2 a day.
  DIP_BUY       - the default, tested strategy (see dipbuy.py): limit buy next session, sell 50% at +1 ATR,
                  rest on the first close above the prior day's high, 10 days max. ALL signals are logged.
  SWING_5D      - (retired) best daily setups from the evening scan, entry next day(s) via buy-stop,
                  max 5 sessions, stop to entry at +1R, then trail under the 20-day EMA. Top 2 a day.
                  Taken EVEN when the market regime is RED (tagged), so we can measure the filter.

Storage: paper/paper_trades.csv (committed to GitHub by the scheduled job).
Every settle step is re-computed from raw prices, so running a job twice is harmless.
"""
from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd

import charges
import config
import data
import dipbuy
import indicators as ind
import intraday
import scanner
import strategy

PAPER_FILE = os.path.join("paper", "paper_trades.csv")
COLS = ["id", "strategy", "symbol", "side", "regime", "status", "signal_date", "entry_date", "plan_entry", "entry",
        "stop", "target", "qty", "exit_date", "exit", "reason", "gross", "charges", "net", "R",
        "R_gross", "score", "why"]
SWING_HOLD = 5
PER_DAY = 2


def load(path: str | None = None) -> pd.DataFrame:
    path = path or PAPER_FILE
    if not os.path.exists(path):
        return pd.DataFrame(columns=COLS)
    df = pd.read_csv(path, dtype=str).fillna("")
    for c in ["plan_entry", "entry", "stop", "target", "exit", "gross", "charges", "net", "R", "R_gross", "score"]:
        if c not in df.columns:
            df[c] = None
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0).astype(int)
    return df


def save(df: pd.DataFrame, path: str | None = None):
    path = path or PAPER_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df[COLS].to_csv(path, index=False)


def _next_id(df) -> str:
    return str(int(pd.to_numeric(df["id"]).max()) + 1) if len(df) else "1"


def _regime_label(day=None) -> str:
    try:
        nifty = data.get_history(config.INDEX_SYMBOL, max_age_hours=1)
        if day is not None:
            nifty = nifty[nifty.index.date <= day]
        return strategy.market_regime(nifty).label
    except Exception:
        return "?"


def _finish(row: dict, exit_px: float, exit_date: str, reason: str, intraday_trade: bool):
    qty, side = int(row["qty"]), row["side"]
    if intraday_trade:
        c = charges.round_trip_intraday(row["entry"], exit_px, qty, side)
    else:
        c = charges.round_trip(row["entry"], exit_px, qty)
    risk = abs(row["entry"] - row["stop"]) * qty
    row.update(status="CLOSED", exit=round(exit_px, 2), exit_date=exit_date, reason=reason,
               gross=c["gross_pnl"], charges=c["total_charges"], net=c["net_pnl"],
               R=round(c["net_pnl"] / risk, 2) if risk else None,
               R_gross=round(c["gross_pnl"] / risk, 2) if risk else None)


# ------------------------------------------------------------------ morning (10:30 IST)
def morning(day=None, universe: str = "Nifty 100") -> list:
    syms = data.get_universe(universe)
    ideas, _ = intraday.scan(syms, day=day, top=PER_DAY)
    if ideas and day is None:
        day = datetime.strptime(ideas[0].date, "%Y-%m-%d").date()
    df = load()
    already = set(df[(df["strategy"] == "INTRADAY_ORB")]["signal_date"]) if len(df) else set()
    if ideas and ideas[0].date in already:
        return ideas  # this morning's picks are already logged
    reg = _regime_label(day)
    rows = []
    for i in ideas:
        rows.append({"id": "", "strategy": "INTRADAY_ORB", "symbol": i.symbol, "side": i.side, "regime": reg,
                     "status": "OPEN", "signal_date": i.date, "entry_date": i.date, "entry": i.entry,
                     "stop": i.stop, "target": i.target, "qty": i.qty, "exit_date": "", "exit": None,
                     "reason": "", "gross": None, "charges": None, "net": None, "R": None,
                     "score": i.score, "why": i.why})
    for r in rows:
        r["id"] = _next_id(df)
        df = pd.concat([df, pd.DataFrame([r])], ignore_index=True)
    save(df)
    return ideas


# ------------------------------------------------------------------ evening (after 4:30 PM IST)
def settle_intraday(df: pd.DataFrame) -> pd.DataFrame:
    open_ = df[(df["strategy"] == "INTRADAY_ORB") & (df["status"] == "OPEN")]
    if open_.empty:
        return df
    frames = intraday.download_15m(sorted(set(open_["symbol"])), period="30d")
    for idx, r in open_.iterrows():
        f = frames.get(r["symbol"])
        day = datetime.strptime(r["signal_date"], "%Y-%m-%d").date()
        if f is None or not len(f[f.index.date == day]) or f.index.max().date() == day and f.index.max().time() < intraday.LAST_EXIT:
            continue  # session not finished yet
        px, reason, t = intraday.simulate_exit(f, day, r["side"], r["entry"], r["stop"], r["target"])
        row = r.to_dict()
        _finish(row, px, f"{r['signal_date']} {t}", reason, intraday_trade=True)
        for k, v in row.items():
            df.at[idx, k] = v
    return df


def _simulate_swing(d: pd.DataFrame, r: dict) -> dict:
    """Replay a swing paper trade from its signal date using daily bars."""
    sig = pd.Timestamp(r["signal_date"])
    after = d[d.index > sig]
    if after.empty:
        return r
    entry, stop0 = float(r["entry"]), float(r["stop"])
    fill_i = None
    for k in range(min(config.ENTRY_VALID_DAYS, len(after))):
        b = after.iloc[k]
        if b["High"] >= entry:
            fill_i, fill_px = k, max(float(b["Open"]), entry)
            break
    if fill_i is None:
        if len(after) >= config.ENTRY_VALID_DAYS:
            r.update(status="EXPIRED", reason="buy-stop never triggered")
        return r
    r.update(status="OPEN", entry_date=str(after.index[fill_i].date()), entry=round(fill_px, 2))
    risk = fill_px - stop0
    stop = stop0
    held = after.iloc[fill_i:]
    for n, (ts, b) in enumerate(held.iterrows()):
        if n > 0 and b["Open"] <= stop:
            _finish(r, float(b["Open"]), str(ts.date()), "gap below stop", False)
            return r
        if b["Low"] <= stop:
            _finish(r, stop, str(ts.date()), "stop" if stop < fill_px else "trailing stop", False)
            return r
        if n + 1 >= SWING_HOLD:
            _finish(r, float(b["Close"]), str(ts.date()), f"{SWING_HOLD}-day exit", False)
            return r
        if b["Close"] >= fill_px + risk:
            stop = max(stop, fill_px, float(b["EMA20"] - 0.25 * b["ATR"]))
    r["stop_now"] = round(stop, 2)
    return r


def settle_swing(df: pd.DataFrame) -> pd.DataFrame:
    live = df[(df["strategy"] == "SWING_5D") & (df["status"].isin(["PENDING", "OPEN"]))]
    for idx, r in live.iterrows():
        try:
            d = ind.add_all(data.get_history(r["symbol"], max_age_hours=1))
        except Exception:
            continue
        row = r.to_dict()
        if row["status"] == "OPEN":  # replay from scratch for idempotence
            row["status"] = "PENDING"
        row = _simulate_swing(d, row)
        for k in COLS:
            df.at[idx, k] = row.get(k, df.at[idx, k])
    return df


def new_swing_signals(df: pd.DataFrame, universe: str = "Nifty 200", day=None) -> pd.DataFrame:
    syms = data.get_universe(universe)
    regime, plans = scanner.scan(syms)
    today = plans[0].date if plans else str(date.today())
    if len(df) and today in set(df[df["strategy"] == "SWING_5D"]["signal_date"]):
        return df
    busy = set(df[(df["strategy"] == "SWING_5D") & (df["status"].isin(["PENDING", "OPEN"]))]["symbol"]) if len(df) else set()
    hard = ("Liquidity", "Risk", "Event")
    cands = [p for p in plans if p.setup != "NONE" and p.grade in ("A", "B") and p.qty > 0
             and p.symbol not in busy and not any((not c.passed) and c.group in hard for c in p.checks)]
    cands.sort(key=lambda p: ({"A": 0, "B": 1}[p.grade], -p.score))
    for p in cands[:PER_DAY]:
        row = {"id": _next_id(df), "strategy": "SWING_5D", "symbol": p.symbol, "side": "LONG",
               "regime": regime.label, "status": "PENDING", "signal_date": p.date, "entry_date": "",
               "entry": p.entry, "stop": p.stop, "target": p.target1, "qty": p.qty, "exit_date": "",
               "exit": None, "reason": "", "gross": None, "charges": None, "net": None, "R": None,
               "score": p.score, "why": f"{p.setup} grade {p.grade}"}
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    return df


def settle_dip(df: pd.DataFrame) -> pd.DataFrame:
    live = df[(df["strategy"] == "DIP_BUY") & (df["status"].isin(["PENDING", "OPEN"]))]
    for idx, r in live.iterrows():
        try:
            d = dipbuy.prep(data.get_history(r["symbol"], max_age_hours=1))
        except Exception:
            continue
        res = dipbuy.replay(d, r["signal_date"], float(r["entry"]) if r["status"] == "PENDING" else float(r["plan_entry"] if pd.notna(r["plan_entry"]) else r["entry"]),
                            float(r["stop"]), float(r["target"]), int(r["qty"]))
        if res["status"] == "PENDING":
            continue
        df.at[idx, "status"] = res["status"]
        for k in ("entry_date", "entry", "exit_date", "exit", "reason", "gross", "charges", "net"):
            if k in res:
                df.at[idx, k] = res[k]
        if res["status"] == "CLOSED":
            risk = (float(res["entry"]) - (float(res["stop"]))) * int(r["qty"])
            df.at[idx, "R"] = round(res["net"] / risk, 2) if risk else None
            df.at[idx, "R_gross"] = round(res["gross"] / risk, 2) if risk else None
            legs = "; ".join(f"{q}@{p} {t}" for p, q, t in res.get("legs", []))
            df.at[idx, "why"] = (str(r["why"]) + " | sold " + legs)[:300]
        elif res["status"] == "EXPIRED":
            df.at[idx, "reason"] = res.get("reason", "")
    return df


def new_dip_signals(df: pd.DataFrame, universe: str = "Nifty 200") -> pd.DataFrame:
    syms = data.get_universe(universe)
    frames = {}
    for s in syms:
        try:
            frames[s] = data.get_history(s, max_age_hours=6)
        except Exception:
            pass
    nifty = data.get_history(config.INDEX_SYMBOL, max_age_hours=6)
    buys, _ = dipbuy.scan(frames, nifty=nifty)
    reg = strategy.market_regime(nifty).label
    logged = set(zip(df["strategy"], df["symbol"], df["signal_date"])) if len(df) else set()
    for p in buys:
        if ("DIP_BUY", p.symbol, p.date) in logged:
            continue
        row = {"id": _next_id(df), "strategy": "DIP_BUY", "symbol": p.symbol, "side": "LONG", "regime": reg,
               "status": "PENDING", "signal_date": p.date, "entry_date": "", "plan_entry": p.entry, "entry": p.entry,
               "stop": p.stop, "target": p.half_target, "qty": p.qty, "exit_date": "", "exit": None, "reason": "", "gross": None,
               "charges": None, "net": None, "R": None, "R_gross": None, "score": p.rsi2,
               "why": f"dip {p.drop3:+.1f}% in 3d, RSI2 {p.rsi2}"}
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    return df


def evening():
    df = load()
    df = settle_intraday(df)
    df = settle_swing(df)      # finishes any old SWING_5D trades; no new ones are opened
    df = settle_dip(df)
    df = new_dip_signals(df)
    save(df)
    return df


# ------------------------------------------------------------------ performance
def performance(df: pd.DataFrame) -> pd.DataFrame:
    c = df[df["status"] == "CLOSED"].copy()
    if c.empty:
        return pd.DataFrame()
    g = c.groupby(["strategy", "regime"])
    out = g.agg(trades=("R", "size"), win_pct=("net", lambda s: round(100 * (s > 0).mean(), 1)),
                avg_R=("R", "mean"), avg_R_before_charges=("R_gross", "mean"),
                net_rs=("net", "sum"), charges_rs=("charges", "sum")).round(2)
    tot = c.groupby("strategy").agg(trades=("R", "size"), win_pct=("net", lambda s: round(100 * (s > 0).mean(), 1)),
                                    avg_R=("R", "mean"), avg_R_before_charges=("R_gross", "mean"),
                net_rs=("net", "sum"), charges_rs=("charges", "sum")).round(2)
    tot["regime"] = "ALL"
    return pd.concat([out.reset_index(), tot.reset_index()], ignore_index=True)
