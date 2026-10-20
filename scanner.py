"""
Scanner: runs the rules over a list of stocks and ranks what it finds.
Also used by the nightly report (report.py) and the dashboard.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import config
import data
import indicators as ind
import strategy


def load_market(demo: bool = False):
    if demo:
        nifty = data.synthetic_history("NIFTY", seed=7)
        nifty[["Open", "High", "Low", "Close"]] *= 22000 / nifty["Close"].iloc[-1]
        return nifty, None
    nifty = data.get_history(config.INDEX_SYMBOL)
    try:
        vix = data.get_history(config.VIX_SYMBOL)
    except Exception:
        vix = None
    return nifty, vix


def _one(sym: str, nifty_close: pd.Series, regime, demo: bool, capital: float):
    try:
        df = data.synthetic_history(sym) if demo else data.get_history(sym)
        if df is None or len(df) < 220:
            return None
        d = ind.add_all(df, nifty_close)
        return strategy.evaluate(d, sym, regime=regime, capital=capital)
    except Exception:
        return None


def scan(symbols: list[str], demo: bool = False, capital: float | None = None,
         workers: int = 6, progress=None):
    capital = capital or config.CAPITAL
    nifty, vix = load_market(demo)
    regime = strategy.market_regime(nifty, vix)
    plans = []
    with ThreadPoolExecutor(max_workers=1 if not demo and config.DATA_SOURCE == "angel" else workers) as ex:
        futs = [ex.submit(_one, s, nifty["Close"], regime, demo, capital) for s in symbols]
        for k, f in enumerate(futs):
            p = f.result()
            if p is not None:
                plans.append(p)
            if progress:
                progress((k + 1) / len(futs))
    return regime, plans


def rank(plans: list) -> dict:
    order = {"A": 0, "B": 1, "C": 2, "-": 3}
    buys = sorted([p for p in plans if p.action == "BUY"], key=lambda p: (order[p.grade], -p.score))
    setups_blocked = sorted([p for p in plans if p.setup != "NONE" and p.action != "BUY"],
                            key=lambda p: (order[p.grade], -p.score))
    # "Almost there": strong trend, within 3% of breakout level, no setup yet
    near = []
    for p in plans:
        fits = not any(c.name == "Price fits your capital" and not c.passed for c in p.checks)
        if p.setup == "NONE" and p.score >= 75 and fits:
            lvl = p.watch_levels.get("breakout_above") or 0
            if lvl and 0 < (lvl - p.close) / p.close <= 0.03:
                near.append(p)
    near.sort(key=lambda p: -p.score)
    return {"buy": buys, "blocked": setups_blocked, "near": near[:15]}


def to_table(plans: list) -> pd.DataFrame:
    rows = []
    for p in plans:
        rows.append({
            "Symbol": p.symbol, "Action": p.action, "Setup": p.setup, "Grade": p.grade, "Score": p.score,
            "Close": p.close, "Entry": p.entry, "Stop": p.stop, "Target1": p.target1, "Target2": p.target2,
            "Risk %": p.risk_pct, "R:R": p.reward_risk, "Qty": p.qty, "Position Rs": p.position_value,
            "Max loss Rs": p.max_loss_rs, "Net @T1 Rs": p.profit_t1_net_rs,
            "Breakout >": p.watch_levels.get("breakout_above"), "Notes": " | ".join(p.notes),
        })
    return pd.DataFrame(rows)
