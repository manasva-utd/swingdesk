"""
Exact-as-possible cost of one delivery round trip (buy, then sell later) at Angel One.
Rates live in config.py - re-check them on angelone.in/pricing every few months.
"""
from __future__ import annotations

import config


def _brokerage(value: float) -> float:
    b = min(config.BROKERAGE_MAX, value * config.BROKERAGE_PCT / 100)
    return max(b, config.BROKERAGE_MIN) if value > 0 else 0.0


def round_trip(buy_price: float, sell_price: float, qty: int) -> dict:
    buy_val = buy_price * qty
    sell_val = sell_price * qty
    brok = _brokerage(buy_val) + _brokerage(sell_val)
    stt = (buy_val + sell_val) * config.STT_PCT / 100
    exch = (buy_val + sell_val) * config.EXCH_TXN_PCT / 100
    sebi = (buy_val + sell_val) * config.SEBI_FEE_PCT / 100
    stamp = buy_val * config.STAMP_BUY_PCT / 100
    gst = (brok + exch + sebi) * config.GST_PCT / 100
    dp = config.DP_CHARGE_PER_SELL if qty > 0 else 0.0
    total = brok + stt + exch + sebi + stamp + gst + dp
    gross = sell_val - buy_val
    net_before_tax = gross - total
    tax = max(0.0, net_before_tax) * config.STCG_TAX_PCT / 100
    return {
        "buy_value": round(buy_val, 2), "sell_value": round(sell_val, 2),
        "brokerage": round(brok, 2), "stt": round(stt, 2), "exchange": round(exch, 2),
        "sebi": round(sebi, 4), "stamp": round(stamp, 2), "gst": round(gst, 2), "dp": round(dp, 2),
        "total_charges": round(total, 2),
        "charges_pct_of_buy": round(100 * total / buy_val, 3) if buy_val else 0.0,
        "gross_pnl": round(gross, 2), "net_pnl": round(net_before_tax, 2),
        "stcg_tax_est": round(tax, 2), "net_after_tax": round(net_before_tax - tax, 2),
    }


def breakeven_move_pct(price: float, qty: int) -> float:
    """How much the stock must rise (in %) just to cover charges."""
    if qty <= 0:
        return float("nan")
    lo, hi = price, price * 1.2
    for _ in range(60):  # bisection
        mid = (lo + hi) / 2
        if round_trip(price, mid, qty)["net_pnl"] < 0:
            lo = mid
        else:
            hi = mid
    return round(100 * (hi / price - 1), 3)
