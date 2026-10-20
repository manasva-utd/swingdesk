"""
Backtester: replays the exact same rules on past data, day by day, with no peeking ahead.

Fill rules (deliberately pessimistic):
  * Entry: buy-stop must trade the next 1-2 sessions; gap-ups fill at the open (worse price).
  * If stop and target are both touched on the same day, assume the STOP hit first.
  * Gap-downs through the stop exit at the open (worse than the stop).
  * Every trade pays full Angel One charges. Fixed capital per trade (no compounding).
"""
from __future__ import annotations

import pandas as pd

import charges
import config
import indicators as ind
import strategy


def run(df: pd.DataFrame, symbol: str, nifty: pd.DataFrame | None = None,
        capital: float | None = None, use_regime: bool = True, min_grade: str = "B") -> dict:
    capital = capital or config.CAPITAL
    idx_close = nifty["Close"] if nifty is not None else None
    d = ind.add_all(df, idx_close)
    n_ind = ind.add_all(nifty) if (nifty is not None and use_regime) else None
    grades_ok = {"A": ("A",), "B": ("A", "B"), "C": ("A", "B", "C")}[min_grade]

    trades = []
    i = 210
    while i < len(d) - 2:
        regime = None
        if n_ind is not None:
            day = d.index[i]
            nn = n_ind.loc[:day]
            if len(nn) > 60:
                rr = nn.iloc[-1]
                if rr["Close"] > rr["EMA50"] and rr["EMA20"] > rr["EMA50"]:
                    regime = strategy.Regime("GREEN", 1.0)
                elif rr["Close"] > rr["EMA200"]:
                    regime = strategy.Regime("AMBER", 0.5 if config.AMBER_TRADES else 0.0)
                else:
                    regime = strategy.Regime("RED", 0.0)
        if strategy.detect_setup(d, i) == "NONE":
            i += 1
            continue
        p = strategy.evaluate(d, symbol, regime=regime, i=i, capital=capital, light=True)
        if p.action != "BUY" or p.grade not in grades_ok or p.qty == 0:
            i += 1
            continue

        # ---- try to fill
        fill_i, fill_px = None, None
        for k in range(1, config.ENTRY_VALID_DAYS + 1):
            j = i + k
            if j >= len(d):
                break
            bar = d.iloc[j]
            if bar["High"] >= p.entry:
                fill_i, fill_px = j, max(float(bar["Open"]), p.entry)
                break
        if fill_i is None:
            i += 1
            continue

        # ---- manage the trade
        stop, t1 = p.stop, p.target1
        trail = config.EXIT_MODE == "trail"
        risk_ps = p.entry - p.stop
        exit_px, exit_i, reason = None, None, None
        for j in range(fill_i, min(fill_i + config.MAX_HOLD_DAYS, len(d))):
            bar = d.iloc[j]
            if j > fill_i and bar["Open"] <= stop:
                exit_px, exit_i, reason = float(bar["Open"]), j, "gap below stop"
                break
            if bar["Low"] <= stop:
                exit_px, exit_i = stop, j
                reason = "stop" if stop < fill_px else "trailing stop"
                break
            if not trail and bar["High"] >= t1:
                exit_px, exit_i, reason = max(t1, float(bar["Open"])) if j > fill_i else t1, j, "target"
                break
            if trail:  # decided on the close, active from the next session
                if bar["Close"] >= fill_px + risk_ps:
                    stop = max(stop, fill_px)
                    stop = max(stop, float(bar["EMA20"] - 0.25 * bar["ATR"]))
        if exit_px is None:
            exit_i = min(fill_i + config.MAX_HOLD_DAYS - 1, len(d) - 1)
            exit_px, reason = float(d["Close"].iloc[exit_i]), "time stop"

        qty = p.qty
        c = charges.round_trip(fill_px, exit_px, qty)
        risk_rs = (p.entry - p.stop) * qty
        trades.append({
            "signal_date": d.index[i].date(), "setup": p.setup, "grade": p.grade,
            "entry_date": d.index[fill_i].date(), "entry": round(fill_px, 2),
            "stop": p.stop, "target": t1, "exit_date": d.index[exit_i].date(),
            "exit": round(exit_px, 2), "reason": reason, "qty": qty,
            "days": exit_i - fill_i + 1, "gross_pnl": c["gross_pnl"], "charges": c["total_charges"],
            "net_pnl": c["net_pnl"], "R": round(c["net_pnl"] / risk_rs, 2) if risk_rs else 0,
            "ret_pct": round(100 * (exit_px / fill_px - 1), 2),
        })
        i = exit_i + 1  # one position at a time per stock

    return summarize(trades, capital)


def summarize(trades: list[dict], capital: float) -> dict:
    t = pd.DataFrame(trades)
    if t.empty:
        return {"trades": t, "stats": {"trades": 0}}
    wins = t[t["net_pnl"] > 0]
    losses = t[t["net_pnl"] <= 0]
    equity = capital + t["net_pnl"].cumsum()
    peak = equity.cummax()
    stats = {
        "trades": len(t),
        "win_rate_pct": round(100 * len(wins) / len(t), 1),
        "avg_win_rs": round(wins["net_pnl"].mean(), 2) if len(wins) else 0,
        "avg_loss_rs": round(losses["net_pnl"].mean(), 2) if len(losses) else 0,
        "expectancy_R": round(t["R"].mean(), 2),
        "total_net_rs": round(t["net_pnl"].sum(), 2),
        "total_charges_rs": round(t["charges"].sum(), 2),
        "return_on_capital_pct": round(100 * t["net_pnl"].sum() / capital, 2),
        "max_drawdown_rs": round((equity - peak).min(), 2),
        "avg_days_held": round(t["days"].mean(), 1),
        "profit_factor": round(wins["net_pnl"].sum() / -losses["net_pnl"].sum(), 2) if len(losses) and losses["net_pnl"].sum() < 0 else None,
    }
    return {"trades": t, "stats": stats}


def run_many(frames: dict[str, pd.DataFrame], nifty: pd.DataFrame | None = None, **kw) -> dict:
    """Backtest the rules over a basket of stocks and pool the trades."""
    all_trades = []
    for sym, df in frames.items():
        if df is None or len(df) < 260:
            continue
        res = run(df, sym, nifty, **kw)
        if len(res["trades"]):
            tt = res["trades"].copy()
            tt.insert(0, "symbol", sym)
            all_trades.append(tt)
    if not all_trades:
        return {"trades": pd.DataFrame(), "stats": {"trades": 0}}
    t = pd.concat(all_trades).sort_values("entry_date")
    out = summarize(t.to_dict("records"), kw.get("capital") or config.CAPITAL)
    out["trades"] = t
    return out
