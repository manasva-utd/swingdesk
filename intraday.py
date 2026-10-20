"""
Intraday "opening range" scanner, built for a 10:30 AM IST check.

Plain English:
  * The first hour (9:15-10:15) sets the day's "opening range" - the high and low
    that buyers and sellers fought over.
  * By 10:30, if a stock has broken ABOVE that range, is above its average traded
    price for the day (VWAP), is beating the Nifty, and is trading on heavier volume
    than usual -> long idea.  The mirror image -> short idea (allowed intraday only).
  * Stop: middle of the opening range (or 1.5x the typical 15-min move, whichever is
    tighter). Target: 2x the risk. Anything still open is closed at 3:15 PM.

Data: Yahoo 15-minute candles (delayed ~15 min, fine for paper trading).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from datetime import time

import numpy as np
import pandas as pd

import config

OR_END = time(10, 15)       # opening range = candles starting 9:15, 9:30, 9:45, 10:00
SCAN_CUTOFF = time(10, 30)  # use candles that have finished by 10:30
LAST_EXIT = time(15, 0)     # close at the end of the 15:00 candle (= 3:15 PM)


@dataclass
class IntradayIdea:
    symbol: str
    date: str
    side: str          # LONG / SHORT
    entry: float
    stop: float
    target: float
    qty: int
    score: float
    rs_pct: float      # stock % move minus Nifty % move since yesterday's close
    vol_ratio: float   # volume so far vs normal for this time of day
    or_high: float
    or_low: float
    vwap: float
    gap_pct: float
    why: str

    def to_dict(self):
        return asdict(self)


def download_15m(symbols: list[str], period: str = "30d") -> dict[str, pd.DataFrame]:
    import yfinance as yf

    out = {}
    tickers = [s if s.startswith("^") else f"{s}.NS" for s in symbols]
    for k in range(0, len(tickers), 40):
        chunk = tickers[k:k + 40]
        raw = yf.download(chunk, period=period, interval="15m", group_by="ticker",
                          auto_adjust=False, progress=False, threads=True)
        for t in chunk:
            try:
                df = raw[t] if len(chunk) > 1 else raw
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(-1)
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
                if len(df):
                    df.index = df.index.tz_convert("Asia/Kolkata")
                    out[t.replace(".NS", "")] = df
            except Exception:
                pass
    return out


def _session(df: pd.DataFrame, day) -> pd.DataFrame:
    return df[df.index.date == day]


def analyze(sym: str, df: pd.DataFrame, nifty: pd.DataFrame, day, cutoff: time = SCAN_CUTOFF,
            capital: float | None = None) -> IntradayIdea | None:
    capital = capital or config.CAPITAL
    exposure = capital * config.INTRADAY_MARGIN_X
    today = _session(df, day)
    prev_days = sorted({d for d in df.index.date if d < day})
    if len(today) < 5 or len(prev_days) < 10:
        return None
    seen = today[today.index.time < cutoff]
    if len(seen) < 5:
        return None
    opening = seen[seen.index.time < OR_END]
    or_hi, or_lo = float(opening["High"].max()), float(opening["Low"].min())
    last = float(seen["Close"].iloc[-1])
    prev_close = float(df[df.index.date == prev_days[-1]]["Close"].iloc[-1])
    tp = (seen["High"] + seen["Low"] + seen["Close"]) / 3
    vwap = float((tp * seen["Volume"]).sum() / max(seen["Volume"].sum(), 1))

    # normal volume for this same window on the previous 20 sessions
    vols = []
    for d in prev_days[-20:]:
        s = _session(df, d)
        vols.append(s[s.index.time < cutoff]["Volume"].sum())
    avg_vol = float(np.mean(vols)) if vols else 0
    vol_ratio = float(seen["Volume"].sum() / avg_vol) if avg_vol else 0.0

    # liquidity: average daily traded value over the previous sessions (Rs crore)
    hist = df[df.index.date < day]
    turnover_cr = float((hist["Close"] * hist["Volume"]).groupby(hist.index.date).sum().tail(20).mean() / 1e7)
    if turnover_cr < config.MIN_AVG_TURNOVER_CR or not (config.MIN_PRICE <= last <= exposure * config.MAX_PRICE_FRACTION_OF_CAPITAL):
        return None

    n_today = _session(nifty, day)
    n_prev = nifty[nifty.index.date == prev_days[-1]]
    n_seen = n_today[n_today.index.time < cutoff]
    if not len(n_seen) or not len(n_prev):
        return None
    nifty_chg = 100 * (float(n_seen["Close"].iloc[-1]) / float(n_prev["Close"].iloc[-1]) - 1)
    chg = 100 * (last / prev_close - 1)
    rs = chg - nifty_chg
    gap = 100 * (float(today["Open"].iloc[0]) / prev_close - 1)

    rng = (hist["High"] - hist["Low"]).tail(26 * 10)
    atr15 = float(rng.mean())
    mid = (or_hi + or_lo) / 2

    side = None
    if last > or_hi and last > vwap and rs > 0.3 and vol_ratio >= 1.2 and abs(gap) < 4:
        side = "LONG"
        stop = max(mid, last - 1.5 * atr15)
    elif last < or_lo and last < vwap and rs < -0.3 and vol_ratio >= 1.2 and abs(gap) < 4:
        side = "SHORT"
        stop = min(mid, last + 1.5 * atr15)
    if side is None:
        return None
    risk = abs(last - stop)
    if risk < last * 0.003:      # stop too tight to survive normal noise
        risk = last * 0.003
        stop = last - risk if side == "LONG" else last + risk
    if risk > last * 0.025:      # more than 2.5% away: not an intraday trade
        return None
    target = last + 2 * risk if side == "LONG" else last - 2 * risk
    qty = min(math.floor(capital * config.RISK_PER_TRADE_PCT / 100 / risk), math.floor(exposure / last))
    if qty < 1:
        return None
    score = abs(rs) * min(vol_ratio, 3)
    why = (f"{'Above' if side == 'LONG' else 'Below'} first-hour range {or_lo:.2f}-{or_hi:.2f} and VWAP {vwap:.2f}; "
           f"{chg:+.2f}% vs Nifty {nifty_chg:+.2f}%; volume {vol_ratio:.1f}x normal")
    r2 = lambda x: round(round(x / 0.05) * 0.05, 2)
    return IntradayIdea(sym, str(day), side, r2(last), r2(stop), r2(target), qty, round(score, 2),
                        round(rs, 2), round(vol_ratio, 2), round(or_hi, 2), round(or_lo, 2), round(vwap, 2),
                        round(gap, 2), why)


def scan(symbols: list[str], day=None, cutoff: time = SCAN_CUTOFF, top: int = 5,
         frames: dict | None = None) -> tuple[list[IntradayIdea], dict]:
    frames = frames or download_15m(list(symbols) + [config.INDEX_SYMBOL])
    nifty = frames.get(config.INDEX_SYMBOL)
    if nifty is None or nifty.empty:
        return [], frames
    day = day or max(nifty.index.date)
    ideas = []
    for s in symbols:
        df = frames.get(s)
        if df is None or df.empty:
            continue
        try:
            i = analyze(s, df, nifty, day, cutoff)
            if i:
                ideas.append(i)
        except Exception:
            continue
    ideas.sort(key=lambda x: -x.score)
    return ideas[:top], frames


def simulate_exit(df: pd.DataFrame, day, side: str, entry: float, stop: float, target: float,
                  start: time = SCAN_CUTOFF) -> tuple[float, str, str]:
    """Walk the 15-min candles after entry. Stop wins ties (pessimistic). Returns (price, reason, time)."""
    s = _session(df, day)
    s = s[(s.index.time >= start) & (s.index.time <= LAST_EXIT)]
    for ts, b in s.iterrows():
        if side == "LONG":
            if b["Low"] <= stop:
                return stop, "stop", ts.strftime("%H:%M")
            if b["High"] >= target:
                return target, "target", ts.strftime("%H:%M")
        else:
            if b["High"] >= stop:
                return stop, "stop", ts.strftime("%H:%M")
            if b["Low"] <= target:
                return target, "target", ts.strftime("%H:%M")
    if len(s):
        return float(s["Close"].iloc[-1]), "3:15 PM exit", s.index[-1].strftime("%H:%M")
    return entry, "no data", ""
