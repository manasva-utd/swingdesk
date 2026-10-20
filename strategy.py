"""
The rules. Long-only swing trades in NSE cash (delivery).

Two setups, both "trade with the trend":
  BREAKOUT  - price closes above its prior 20-day high on strong volume.
  PULLBACK  - stock in an uptrend dips to its 20-day EMA, then bounces.

Every call comes with: entry trigger, stop loss, two targets, quantity, charges,
and a checklist so you can see exactly which conditions passed or failed.

Note: in the Indian cash market you cannot hold a short position overnight,
so "SELL" here always means "exit a stock you already hold".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import pandas as pd

import charges
import config
import indicators as ind


# ======================================================= market regime
@dataclass
class Regime:
    label: str              # GREEN / AMBER / RED
    size_multiplier: float  # 1.0 full size, 0.5 half, 0 = no new longs
    reasons: list[str] = field(default_factory=list)
    nifty_close: float | None = None
    vix: float | None = None


def market_regime(nifty: pd.DataFrame, vix: pd.DataFrame | None = None) -> Regime:
    n = ind.add_all(nifty)
    r = n.iloc[-1]
    ema20_up = r["EMA20"] > n["EMA20"].iloc[-6]
    reasons = []
    if r["Close"] > r["EMA50"] and r["EMA20"] > r["EMA50"]:
        label, mult = "GREEN", 1.0
        reasons.append("Nifty above its 50-day average and short-term trend above long-term: buyers in control.")
    elif r["Close"] > r["EMA200"] or (r["Close"] > r["EMA20"] and ema20_up):
        label, mult = "AMBER", (0.5 if config.AMBER_TRADES else 0.0)
        reasons.append("Mixed: Nifty not cleanly trending. " + ("Half size, A-grade only." if config.AMBER_TRADES
                       else "No new buys (backtest: trades taken in AMBER markets lost money)."))
    else:
        label, mult = "RED", 0.0
        reasons.append("Nifty below its 50- and 200-day averages: most breakouts fail in this phase. Cash is a position.")
    reasons.append(f"Nifty {r['Close']:.0f} | EMA20 {r['EMA20']:.0f} | EMA50 {r['EMA50']:.0f} | EMA200 {r['EMA200']:.0f} | RSI {r['RSI']:.0f}")
    vix_val = None
    if vix is not None and not vix.empty:
        vix_val = float(vix["Close"].iloc[-1])
        if vix_val >= config.VIX_DANGER:
            mult = 0.0
            reasons.append(f"India VIX {vix_val:.1f} >= {config.VIX_DANGER}: fear is high, gaps are violent. No new longs.")
        elif vix_val >= config.VIX_CAUTION:
            mult = min(mult, 0.5)
            reasons.append(f"India VIX {vix_val:.1f} is elevated: size halved.")
        else:
            reasons.append(f"India VIX {vix_val:.1f}: calm.")
    return Regime(label, mult, reasons, float(r["Close"]), vix_val)


# ======================================================= single-stock evaluation
@dataclass
class Check:
    group: str
    name: str
    passed: bool
    value: str
    why: str


@dataclass
class TradePlan:
    symbol: str
    date: str
    close: float
    setup: str                 # BREAKOUT / PULLBACK / NONE
    grade: str                 # A / B / C / -
    score: int
    action: str                # BUY / WATCH / AVOID
    entry: float | None = None
    entry_type: str = ""
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    risk_pct: float | None = None
    reward_risk: float | None = None
    qty: int = 0
    position_value: float = 0.0
    max_loss_rs: float = 0.0
    profit_t1_net_rs: float = 0.0
    charges_round_trip: float = 0.0
    breakeven_pct: float | None = None
    resistance: list = field(default_factory=list)
    support: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    watch_levels: dict = field(default_factory=dict)

    def to_dict(self):
        d = asdict(self)
        d["checks"] = [asdict(c) if not isinstance(c, dict) else c for c in self.checks]
        return d


def _round_tick(x: float) -> float:
    """NSE tick size is 5 paise for most stocks."""
    return round(round(x / 0.05) * 0.05, 2)


def detect_setup(d: pd.DataFrame, i: int) -> str:
    r, p = d.iloc[i], d.iloc[i - 1]
    uptrend = r["Close"] > r["EMA50"] and r["EMA50"] > r["EMA200"]
    if (r["Close"] > r["HIGH20"] and r["VOL_RATIO"] >= 1.5 and 55 <= r["RSI"] <= 78
            and r["Close"] > r["EMA50"]):
        return "BREAKOUT"
    recent = d.iloc[i - 3:i + 1]
    touched_ema20 = (recent["Low"] <= recent["EMA20"] + 0.5 * recent["ATR"]).any()
    bullish_close = r["Close"] > r["Open"] and r["Close"] > p["Close"] and \
        (r["Close"] - r["Low"]) >= 0.5 * (r["High"] - r["Low"])
    if uptrend and touched_ema20 and bullish_close and 40 <= r["RSI"] <= 62 and r["Close"] > r["EMA20"] * 0.98:
        return "PULLBACK"
    return "NONE"


def evaluate(d: pd.DataFrame, symbol: str, regime: Regime | None = None, i: int = -1,
             capital: float | None = None, earnings_date=None, light: bool = False) -> TradePlan:
    """
    d: dataframe that already has indicators (indicators.add_all).
    i: row to evaluate (default: latest candle).
    light=True skips support/resistance (used by the backtester for speed).
    """
    capital = capital or config.CAPITAL
    i = i if i >= 0 else len(d) + i
    r = d.iloc[i]
    close = float(r["Close"])
    atr = float(r["ATR"])
    setup = detect_setup(d, i)
    hist = d.iloc[: i + 1]
    res = [] if light else ind.resistance_levels(hist, close)
    sup = [] if light else ind.support_levels(hist, close)

    plan = TradePlan(symbol=symbol, date=str(d.index[i].date()), close=round(close, 2),
                     setup=setup, grade="-", score=0, action="WATCH", resistance=res, support=sup)

    # ---- entry / stop / targets
    if setup == "BREAKOUT":
        entry = _round_tick(r["High"] * 1.002)
        stop = max(r["HIGH20"] - 0.5 * atr, entry - 2.0 * atr)
        stop = min(stop, entry - 1.0 * atr)
        plan.entry_type = "Buy-stop (SL-M/SL-limit) just above today's high"
    elif setup == "PULLBACK":
        entry = _round_tick(r["High"] * 1.002)
        swing_low = float(d["Low"].iloc[i - 4:i + 1].min())
        stop = max(swing_low - 0.25 * atr, entry - 2.5 * atr)
        stop = min(stop, entry - 1.0 * atr)
        plan.entry_type = "Buy-stop just above today's high (confirms the bounce)"
    else:
        entry, stop = None, None

    if entry is not None:
        stop = _round_tick(stop)
        risk = entry - stop
        t1 = _round_tick(entry + config.MIN_REWARD_RISK * risk)
        t2 = _round_tick(entry + (config.MIN_REWARD_RISK + 1.0) * risk)
        plan.entry, plan.stop, plan.target1, plan.target2 = entry, stop, t1, t2
        plan.risk_pct = round(100 * risk / entry, 2)
        first_res = next((x for x in res if x > entry), None)
        plan.reward_risk = round((first_res - entry) / risk, 2) if first_res and first_res < t1 else round((t1 - entry) / risk, 2)

    # ---- checklist
    C = []
    add = lambda g, n, ok, v, w: C.append(Check(g, n, bool(ok), v, w))
    add("Trend", "Price above 50-day EMA", close > r["EMA50"], f"{close:.1f} vs {r['EMA50']:.1f}",
        "Only buy stocks already going up.")
    add("Trend", "50-day EMA above 200-day EMA", r["EMA50"] > r["EMA200"], f"{r['EMA50']:.1f} vs {r['EMA200']:.1f}",
        "Long-term trend is up too.")
    add("Trend", "20-day EMA rising", r["EMA20"] > d["EMA20"].iloc[i - 5], f"{r['EMA20']:.1f} vs {d['EMA20'].iloc[i-5]:.1f} (5d ago)",
        "Short-term direction is up.")
    add("Momentum", "RSI in healthy zone (45-75)", 45 <= r["RSI"] <= 75, f"{r['RSI']:.0f}",
        "Strong but not exhausted.")
    add("Momentum", "MACD histogram positive or rising", r["MACD_HIST"] > 0 or r["MACD_HIST"] > d["MACD_HIST"].iloc[i - 1],
        f"{r['MACD_HIST']:.2f}", "Momentum building, not fading.")
    add("Momentum", "ADX >= 20 (real trend)", r["ADX"] >= 20, f"{r['ADX']:.0f}", "Trend strong enough to carry the trade.")
    rs_ok = (not pd.isna(r["RS_3M"])) and r["RS_3M"] > 0
    add("Strength", "Beating Nifty over 3 months", rs_ok,
        "n/a" if pd.isna(r["RS_3M"]) else f"{r['RS_3M']:+.1f} pts", "Leaders keep leading.")
    add("Strength", f"Within {config.NEAR_HIGH_PCT}% of 52-week high", r["DIST_52W_HIGH_PCT"] <= config.NEAR_HIGH_PCT, f"{r['DIST_52W_HIGH_PCT']:.1f}% below",
        "Less overhead supply from trapped buyers.")
    if setup == "BREAKOUT":
        add("Volume", "Breakout volume >= 1.5x average", r["VOL_RATIO"] >= 1.5, f"{r['VOL_RATIO']:.1f}x",
            "Real money behind the move.")
    else:
        dry = d["Volume"].iloc[i - 3:i].mean() < r["VOL_AVG20"]
        add("Volume", "Pullback on lighter volume", dry, f"{d['Volume'].iloc[i-3:i].mean()/r['VOL_AVG20']:.2f}x avg",
            "Sellers are not aggressive.")
    add("Liquidity", f"Avg turnover >= Rs {config.MIN_AVG_TURNOVER_CR} Cr", r["TURNOVER_CR"] >= config.MIN_AVG_TURNOVER_CR,
        f"Rs {r['TURNOVER_CR']:.0f} Cr", "Easy to exit at your price.")
    price_ok = config.MIN_PRICE <= close <= capital * config.MAX_PRICE_FRACTION_OF_CAPITAL
    add("Liquidity", "Price fits your capital", price_ok, f"Rs {close:.0f}", "You can buy a sensible number of shares.")
    if entry is not None:
        add("Risk", f"Stop within {config.MAX_STOP_PCT}% of entry", plan.risk_pct <= config.MAX_STOP_PCT,
            f"{plan.risk_pct:.1f}%", "Wide stops = tiny position or big loss.")
        add("Risk", f"Room to target (R:R >= {config.MIN_REWARD_RISK})", plan.reward_risk >= config.MIN_REWARD_RISK,
            f"{plan.reward_risk:.1f}R" + (f" (resistance {res[0]})" if res and res[0] < (plan.target1 or 0) else ""),
            "No big ceiling right above you.")
    if earnings_date is not None:
        days = (pd.Timestamp(earnings_date) - d.index[i]).days
        add("Event", "No results inside the holding window", days > config.MAX_HOLD_DAYS * 1.5, f"results {earnings_date}",
            "Results = coin-flip gaps. Don't hold through them on a swing trade.")
    plan.checks = C

    weights = {"Trend": 3, "Momentum": 2, "Strength": 2, "Volume": 2, "Liquidity": 2, "Risk": 3, "Event": 3}
    tot = sum(weights[c.group] for c in C)
    got = sum(weights[c.group] for c in C if c.passed)
    plan.score = int(round(100 * got / tot)) if tot else 0

    hard_fail = [c for c in C if not c.passed and c.group in ("Liquidity", "Risk", "Event")]
    if setup == "NONE":
        plan.grade, plan.action = "-", "WATCH"
    else:
        plan.grade = "A" if plan.score >= 85 else "B" if plan.score >= 70 else "C"
        plan.action = "BUY" if (plan.grade in ("A", "B") and not hard_fail) else "AVOID"

    if regime is not None:
        if regime.size_multiplier == 0 and plan.action == "BUY":
            plan.action = "WATCH"
            plan.notes.append(f"Market regime {regime.label}: setup is valid but no new longs today.")
        elif regime.label == "AMBER" and plan.grade != "A" and plan.action == "BUY":
            plan.action = "WATCH"
            plan.notes.append("Market regime AMBER: only A-grade setups are taken.")

    # ---- position size & money
    if entry is not None:
        mult = regime.size_multiplier if regime is not None and regime.size_multiplier > 0 else 1.0
        risk_rs = capital * config.RISK_PER_TRADE_PCT / 100 * mult
        per_share = entry - plan.stop
        qty = math.floor(risk_rs / per_share) if per_share > 0 else 0
        qty = min(qty, math.floor(capital * config.MAX_POSITION_PCT / 100 / entry))
        plan.qty = max(qty, 0)
        if plan.qty == 0:
            plan.notes.append("Even 1 share risks more than your per-trade limit or exceeds capital. Skip.")
            if plan.action == "BUY":
                plan.action = "AVOID"
        else:
            plan.position_value = round(plan.qty * entry, 2)
            loss = charges.round_trip(entry, plan.stop, plan.qty)
            win = charges.round_trip(entry, plan.target1, plan.qty)
            plan.max_loss_rs = round(-loss["net_pnl"], 2)
            plan.profit_t1_net_rs = win["net_pnl"]
            plan.charges_round_trip = win["total_charges"]
            plan.breakeven_pct = charges.breakeven_move_pct(entry, plan.qty)
            if win["gross_pnl"] > 0 and win["total_charges"] / win["gross_pnl"] > 0.25:
                plan.notes.append(f"Charges eat {100*win['total_charges']/win['gross_pnl']:.0f}% of the target profit. "
                                  "Small position = expensive trade.")

    # ---- what to watch if there is no setup today
    plan.watch_levels = {
        "breakout_above": round(float(r["HIGH20"]), 2),
        "pullback_zone": f"{r['EMA20'] - 0.5*atr:.1f} - {r['EMA20'] + 0.25*atr:.1f}",
        "trend_dies_below": round(float(r["EMA50"]), 2),
        "atr_rs": round(atr, 2), "atr_pct": round(float(r["ATR_PCT"]), 2),
    }
    return plan


# ======================================================= exit rules for an open position
def exit_advice(d: pd.DataFrame, entry: float, stop: float, target1: float, entry_date: str) -> dict:
    """What to do tomorrow with a stock you already hold."""
    r = d.iloc[-1]
    held = int((d.index > pd.Timestamp(entry_date)).sum())
    close = float(r["Close"])
    risk = entry - stop
    new_stop = stop
    msgs = []
    if close <= stop:
        return {"action": "SELL", "stop": stop, "msg": ["Closed at/below stop. Exit at open tomorrow, no hoping."]}
    if close >= target1:
        msgs.append("Target 1 hit: book at least half; trail the rest.")
        new_stop = max(new_stop, entry)
    if close >= entry + risk:
        new_stop = max(new_stop, entry)
        msgs.append("Up 1R: move stop to entry (free trade).")
    trail = float(r["EMA20"]) - 0.25 * float(r["ATR"])
    if close >= entry + 1.5 * risk and trail > new_stop:
        new_stop = trail
        msgs.append("Trailing stop raised to just under the 20-day EMA.")
    if held >= config.MAX_HOLD_DAYS and close < target1:
        return {"action": "SELL", "stop": round(float(new_stop), 2),
                "msg": msgs + [f"Time stop: held {held} sessions without reaching target. Free the capital."]}
    if close < float(r["EMA20"]) and close < entry:
        msgs.append("Closed below 20-day EMA and below entry: momentum gone. Consider exiting.")
    return {"action": "HOLD", "stop": round(float(new_stop), 2), "held_days": held,
            "msg": msgs or ["Nothing to do. Keep the stop order in place."]}
