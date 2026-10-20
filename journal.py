"""Trade journal: a plain CSV you own. Every trade you actually take goes here."""
from __future__ import annotations

import os

import pandas as pd

import charges
import config

COLS = ["id", "symbol", "setup", "entry_date", "entry", "stop", "target1", "qty",
        "status", "exit_date", "exit", "net_pnl", "R", "notes"]


def load() -> pd.DataFrame:
    if not os.path.exists(config.JOURNAL_FILE):
        return pd.DataFrame(columns=COLS)
    df = pd.read_csv(config.JOURNAL_FILE, dtype={"exit_date": str, "notes": str, "status": str})
    return df.astype({c: object for c in ["exit_date", "exit", "net_pnl", "R", "notes"]})


def save(df: pd.DataFrame):
    df[COLS].to_csv(config.JOURNAL_FILE, index=False)


def add(symbol, setup, entry_date, entry, stop, target1, qty, notes=""):
    df = load()
    nid = int(df["id"].max()) + 1 if len(df) else 1
    row = dict(id=nid, symbol=symbol.upper(), setup=setup, entry_date=str(entry_date), entry=float(entry),
               stop=float(stop), target1=float(target1), qty=int(qty), status="OPEN",
               exit_date="", exit=None, net_pnl=None, R=None, notes=notes)
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    save(df)
    return nid


def close(trade_id: int, exit_date, exit_price: float):
    df = load()
    m = df["id"] == trade_id
    r = df[m].iloc[0]
    c = charges.round_trip(float(r["entry"]), float(exit_price), int(r["qty"]))
    risk = (float(r["entry"]) - float(r["stop"])) * int(r["qty"])
    vals = {"status": "CLOSED", "exit_date": str(exit_date), "exit": float(exit_price),
            "net_pnl": c["net_pnl"], "R": round(c["net_pnl"] / risk, 2) if risk else None}
    for k, v in vals.items():
        df.loc[m, k] = v
    save(df)


def stats() -> dict:
    df = load()
    c = df[df["status"] == "CLOSED"].copy()
    if c.empty:
        return {"closed_trades": 0}
    c["net_pnl"] = c["net_pnl"].astype(float)
    return {"closed_trades": len(c), "win_rate_pct": round(100 * (c["net_pnl"] > 0).mean(), 1),
            "net_pnl_rs": round(c["net_pnl"].sum(), 2), "avg_R": round(c["R"].astype(float).mean(), 2)}
