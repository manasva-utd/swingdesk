"""
DIP-BUY strategy (the default). Tested Oct 2021 - Oct 2026 on the Nifty 200 with full Angel One charges,
Rs 15,000 in one position at a time: 160 trades, 78% winners, +Rs 14.5k net; 2025-26 alone: 75% winners.

Plain English:
  1. Only strong stocks: price above its 200-day average, and the 50-day above the 200-day.
  2. Wait for a sharp, short dip: down 3%+ over 3 days and very oversold (2-day RSI under 10).
  3. BUY with a limit order about one "normal day's move" (ATR) below today's close. Valid for the next session only.
     If the price never comes down that far, there is no trade (that's fine - it keeps entries cheap).
  4. SELL 50% when price bounces one ATR above your buy price. Then move the stop up to your buy price,
     so the rest of the trade can no longer lose money (apart from charges).
  5. SELL the other 50% on the first day the stock CLOSES above the previous day's high (the bounce is done),
     or after 10 sessions at the latest.
  6. STOP LOSS: 3 ATRs below the buy price. Rarely hit, but it is what keeps one bad trade from hurting badly.

Why it wins often: strong stocks that dip sharply usually snap back within days. Why it can still lose:
a dip can be the start of real bad news. The stop loss and the trend filter are there for that.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

import charges
import config
import indicators as ind

PARAMS = dict(rsi_max=10, min_drop_pct=3.0, entry_atr=1.0, half_at_atr=1.0, stop_atr=3.0, max_hold=10)


def prep(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c = d["Close"]
    d["EMA20"] = ind.ema(c, 20)
    d["EMA50"] = ind.ema(c, 50)
    d["EMA200"] = ind.ema(c, 200)
    d["SMA5"] = c.rolling(5).mean()
    d["RSI2"] = ind.rsi(c, 2)
    d["RSI"] = ind.rsi(c, 14)
    d["ATR"] = ind.atr(d, 14)
    d["TURN_CR"] = (c * d["Volume"]).rolling(20).mean() / 1e7
    d["DROP3"] = 100 * (c / c.shift(3) - 1)
    return d


@dataclass
class Check:
    name: str
    passed: bool
    value: str
    why: str


@dataclass
class DipPlan:
    symbol: str
    date: str
    close: float
    signal: bool
    action: str                 # BUY / WATCH / AVOID
    headline: str
    entry: float | None = None
    stop: float | None = None
    half_target: float | None = None
    final_trigger: float | None = None   # first close above this (= today's high) sells the rest
    qty: int = 0
    half_qty: int = 0
    position_value: float = 0.0
    loss_if_stopped: float = 0.0
    profit_half_then_flat: float = 0.0
    profit_full_bounce: float = 0.0
    charges_est: float = 0.0
    atr: float = 0.0
    rsi2: float = 0.0
    drop3: float = 0.0
    size_note: str = ""
    checks: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    watch: str = ""

    def to_dict(self):
        d = asdict(self)
        return d

    def ladder(self) -> list[dict]:
        if not self.entry:
            return []
        rest = self.qty - self.half_qty
        return [
            {"price": self.half_target, "label": f"SELL 50% ({self.half_qty} sh), then stop → buy price", "color": "#16a34a"},
            {"price": self.final_trigger, "label": f"SELL last 50% ({rest} sh) on a daily CLOSE above", "color": "#15803d"},
        ]

    def steps(self) -> list[str]:
        if not self.entry:
            return []
        rest = self.qty - self.half_qty
        return [
            f"**Tonight / tomorrow before 9:15 AM** — place a **LIMIT BUY** for **{self.qty} shares at ₹{self.entry}** "
            f"(Delivery). Valid for tomorrow only. Not filled? No trade — cancel it.",
            f"**As soon as it fills** — place a **stop-loss SELL** for all {self.qty} shares at **₹{self.stop}**.",
            f"**First profit** — when price reaches **₹{self.half_target}**, **sell {self.half_qty} shares (50%)**. "
            f"Then move your stop-loss for the other {rest} shares **up to ₹{self.entry}** (your buy price).",
            f"**Final exit** — sell the last **{rest} shares** at the close of the first day the stock **closes above the "
            f"previous day's high** (tomorrow that means a close above **₹{self.final_trigger}**).",
            f"**Time limit** — if neither has happened after **{PARAMS['max_hold']} trading days**, sell everything at the close.",
        ]


def _r(x):
    return round(round(float(x) / 0.05) * 0.05, 2)


def evaluate(d: pd.DataFrame, symbol: str, capital: float | None = None, nifty: pd.DataFrame | None = None,
             earnings_date=None) -> DipPlan:
    """d: prep()-ed daily candles. Evaluates the latest candle."""
    capital = capital or config.CAPITAL
    r = d.iloc[-1]
    close, atr = float(r["Close"]), float(r["ATR"])
    C = []
    add = lambda n, ok, v, w: C.append(Check(n, bool(ok), v, w))
    add("Long-term uptrend (price above 200-day avg)", close > r["EMA200"], f"₹{close:,.1f} vs ₹{r['EMA200']:,.1f}",
        "We only buy dips in stocks that are going up overall.")
    add("Strong trend (50-day avg above 200-day avg)", r["EMA50"] > r["EMA200"], f"₹{r['EMA50']:,.1f} vs ₹{r['EMA200']:,.1f}",
        "Filters out stocks that are only just recovering.")
    add(f"Sharp dip (down {PARAMS['min_drop_pct']:.0f}%+ in 3 days)", r["DROP3"] <= -PARAMS["min_drop_pct"], f"{r['DROP3']:+.1f}%",
        "A quick, sharp drop is what usually snaps back.")
    add(f"Very oversold (2-day RSI under {PARAMS['rsi_max']})", r["RSI2"] < PARAMS["rsi_max"], f"{r['RSI2']:.1f}",
        "2-day RSI (0-100) measures how one-sided the last two days were. Under 10 = sellers are exhausted.")
    add(f"Easy to buy and sell (₹{config.MIN_AVG_TURNOVER_CR} Cr+ traded daily)", r["TURN_CR"] >= config.MIN_AVG_TURNOVER_CR,
        f"₹{r['TURN_CR']:,.0f} Cr", "You can get in and out at your price.")
    entry = _r(close - PARAMS["entry_atr"] * atr)
    qty = math.floor(capital / entry) if entry > 0 else 0
    add("Price fits your capital (at least 2 shares)", qty >= 2 and close >= config.MIN_PRICE, f"₹{close:,.1f} → {qty} shares",
        "You need 2+ shares to sell half at the first target.")
    if earnings_date is not None:
        days = (pd.Timestamp(earnings_date) - d.index[-1]).days
        add("No results announcement in the next 2 weeks", days > 14, f"results {earnings_date}",
            "Results can gap a stock 5-10% overnight. Not worth it for a few-day trade.")

    sig = all(c.passed for c in C)
    p = DipPlan(symbol, str(d.index[-1].date()), round(close, 2), sig, "WATCH", "", atr=round(atr, 2),
                rsi2=round(float(r["RSI2"]), 1), drop3=round(float(r["DROP3"]), 2), checks=C)

    # market context: trade either way, size note
    if nifty is not None and len(nifty) > 200:
        n200 = ind.ema(nifty["Close"], 200).iloc[-1]
        if nifty["Close"].iloc[-1] < n200:
            p.size_note = ("Nifty is below its 200-day average. In testing, dip-buys still won ~76% of the time in "
                           "this phase (42 trades) vs ~79% in strong markets (118 trades).")

    trend_ok = C[0].passed and C[1].passed
    if sig:
        stop = _r(entry - PARAMS["stop_atr"] * atr)
        half = _r(entry + PARAMS["half_at_atr"] * atr)
        final = _r(float(r["High"]))
        half_qty = qty // 2
        p.entry, p.stop, p.half_target, p.final_trigger = entry, stop, half, max(final, half)
        p.qty, p.half_qty, p.position_value = qty, half_qty, round(qty * entry, 2)
        rest = qty - half_qty
        lose = charges.round_trip(entry, stop, qty)["net_pnl"]
        flat = (charges.round_trip(entry, half, half_qty)["net_pnl"] +
                charges.round_trip(entry, entry, rest)["net_pnl"] + 4)  # second DP/brokerage roughly
        full = (charges.round_trip(entry, half, half_qty)["net_pnl"] +
                charges.round_trip(entry, p.final_trigger, rest)["net_pnl"])
        p.loss_if_stopped, p.profit_half_then_flat, p.profit_full_bounce = round(lose, 0), round(flat, 0), round(full, 0)
        p.charges_est = round(charges.round_trip(entry, half, qty)["total_charges"] + config.DP_CHARGE_PER_SELL, 0)
        p.action = "BUY"
        p.headline = (f"{symbol} dropped {abs(p.drop3):.1f}% in 3 days inside a strong uptrend and is very oversold "
                      f"(2-day RSI {p.rsi2}). Classic dip-buy setup.")
    elif trend_ok:
        need = _r(float(d["Close"].iloc[-3]) * (1 - PARAMS["min_drop_pct"] / 100)) if len(d) > 3 else None
        p.headline = f"{symbol} is in a strong uptrend, but there's no dip to buy yet."
        p.watch = (f"Becomes a dip-buy candidate if it falls sharply — roughly a close below ₹{need} "
                   f"with 2-day RSI under {PARAMS['rsi_max']} (now {p.rsi2}).") if need else ""
    else:
        p.action = "AVOID"
        p.headline = f"{symbol} is not in a strong uptrend. Dip-buying a falling stock is how you catch a falling knife."
    return p


# ---------------------------------------------------------------- replay (backtest / paper trading)
def replay(d: pd.DataFrame, signal_date: str, entry: float, stop: float, half: float, qty: int) -> dict:
    """Play a dip-buy forward from its signal day on daily bars. Same rules as the backtest."""
    sig = pd.Timestamp(signal_date)
    after = d[d.index > sig]
    out = {"status": "PENDING"}
    if after.empty:
        return out
    b0 = after.iloc[0]
    if b0["Low"] > entry:
        return {"status": "EXPIRED", "reason": "limit not reached next session"}
    px = min(float(b0["Open"]), entry)
    # if it opened below the limit, the levels move down with the actual fill (same as the backtest)
    stop, half = px - (entry - stop), px + (half - entry)
    out.update(status="OPEN", entry_date=str(after.index[0].date()), entry=round(px, 2),
               stop=round(stop, 2), half=round(half, 2))
    half_qty, rem, legs, cur_stop = qty // 2, qty, [], stop
    prev_high = float(d[d.index <= sig]["High"].iloc[-1])
    for n, (ts, b) in enumerate(after.iterrows()):
        if n > 0 and b["Open"] <= cur_stop:
            legs.append((float(b["Open"]), rem, ts)); rem = 0
            reason = "opened below stop" if cur_stop < px else "opened below buy price (after 50% sold)"
            break
        if b["Low"] <= cur_stop:
            legs.append((cur_stop, rem, ts)); rem = 0
            reason = "stop" if cur_stop < px else "stop at buy price (after 50% sold)"
            break
        if rem == qty and b["High"] >= half:
            legs.append((max(half, float(b["Open"])) if n > 0 else half, half_qty, ts)); rem -= half_qty
            cur_stop = max(cur_stop, px)
        if n > 0 and b["Close"] > prev_high:
            legs.append((float(b["Close"]), rem, ts)); rem = 0; reason = "bounce done (close above prior high)"; break
        if n + 1 >= PARAMS["max_hold"]:
            legs.append((float(b["Close"]), rem, ts)); rem = 0; reason = f"{PARAMS['max_hold']}-day limit"; break
        prev_high = float(b["High"])
    if rem > 0:
        out.update(open_qty=rem, stop_now=round(cur_stop, 2), legs=legs)
        return out
    gross = sum(p * q for p, q, _ in legs) - px * qty
    cost = charges.round_trip(px, px, qty)["total_charges"] + config.DP_CHARGE_PER_SELL * (len({t for _, _, t in legs}) - 1)
    exit_avg = sum(p * q for p, q, _ in legs) / qty
    out.update(status="CLOSED", exit=round(exit_avg, 2), exit_date=str(legs[-1][2].date()), reason=reason,
               gross=round(gross, 2), charges=round(cost, 2), net=round(gross - cost, 2),
               legs=[(round(p, 2), q, str(t.date())) for p, q, t in legs])
    return out


def exit_advice(d: pd.DataFrame, entry: float, stop: float, half: float, qty: int, sold_half: bool, entry_date: str) -> dict:
    """What to do tomorrow with an open dip-buy you hold."""
    r = d.iloc[-1]
    held = int((d.index >= pd.Timestamp(entry_date)).sum())
    prev_high = float(d["High"].iloc[-2])
    close = float(r["Close"])
    cur_stop = entry if sold_half else stop
    if close <= cur_stop:
        return {"action": "SELL ALL", "msg": f"Closed at/below your stop (₹{cur_stop}). Exit at tomorrow's open."}
    if close > prev_high and held > 1:
        return {"action": "SELL ALL", "msg": "Closed above the previous day's high: the bounce is done. Sell the rest."}
    if held >= PARAMS["max_hold"]:
        return {"action": "SELL ALL", "msg": f"{PARAMS['max_hold']}-day limit reached. Free the money."}
    if not sold_half and float(r["High"]) >= half:
        return {"action": "SELL 50%", "msg": f"Hit the first target ₹{half}. Sell half and move the stop to ₹{entry}."}
    return {"action": "HOLD", "msg": f"Keep the stop at ₹{cur_stop}. Sell the rest on a close above ₹{round(float(r['High']), 2)}."}


def scan(frames: dict[str, pd.DataFrame], capital: float | None = None, nifty=None) -> tuple[list[DipPlan], list[DipPlan]]:
    """frames: raw daily candles by symbol. Returns (buy signals sorted best-first, watchlist)."""
    buys, watch = [], []
    for s, df in frames.items():
        try:
            if df is None or len(df) < 220:
                continue
            p = evaluate(prep(df), s, capital, nifty)
            if p.action == "BUY":
                buys.append(p)
            elif p.action == "WATCH" and p.rsi2 < 30 and p.drop3 < 0:
                watch.append(p)
        except Exception:
            continue
    buys.sort(key=lambda p: (p.rsi2, p.drop3))
    watch.sort(key=lambda p: p.rsi2)
    return buys, watch[:10]
