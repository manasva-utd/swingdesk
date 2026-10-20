"""
Technical indicators, written out in plain pandas so you can read every formula.

  EMA   - exponential moving average: the trend line. Price above a rising EMA = uptrend.
  RSI   - relative strength index (0-100): momentum. 50-70 = healthy strength, >75 = stretched.
  MACD  - gap between a fast and slow EMA: is momentum speeding up or slowing down?
  ATR   - average true range: how much the stock normally moves in a day (in rupees).
          Used to place stops outside normal noise.
  ADX   - trend strength (0-100). Above ~20 = a real trend, below = choppy sideways market.
  RS    - relative strength vs Nifty: is this stock beating the index over ~3 months?
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["High"].diff()
    dn = -df["Low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0)


def add_all(df: pd.DataFrame, index_close: pd.Series | None = None) -> pd.DataFrame:
    """Return a copy of df with every indicator column the strategy uses."""
    df = df.copy()
    c = df["Close"]
    df["EMA20"] = ema(c, 20)
    df["EMA50"] = ema(c, 50)
    df["EMA200"] = ema(c, 200)
    df["RSI"] = rsi(c)
    macd = ema(c, 12) - ema(c, 26)
    df["MACD"] = macd
    df["MACD_SIG"] = ema(macd, 9)
    df["MACD_HIST"] = macd - df["MACD_SIG"]
    df["ATR"] = atr(df)
    df["ATR_PCT"] = 100 * df["ATR"] / c
    df["ADX"] = adx(df)
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()
    df["VOL_RATIO"] = df["Volume"] / df["VOL_AVG20"]
    df["TURNOVER_CR"] = (c * df["Volume"]).rolling(20).mean() / 1e7
    df["HIGH20"] = df["High"].rolling(20).max().shift(1)   # prior 20-day high (breakout level)
    df["LOW10"] = df["Low"].rolling(10).min()               # recent swing low (stop reference)
    df["HIGH52W"] = df["High"].rolling(250, min_periods=60).max()
    df["DIST_52W_HIGH_PCT"] = 100 * (df["HIGH52W"] - c) / df["HIGH52W"]
    df["RET_1M"] = 100 * c.pct_change(21)
    df["RET_3M"] = 100 * c.pct_change(63)
    if index_close is not None and len(index_close):
        idx = index_close.reindex(df.index).ffill()
        df["RS_3M"] = df["RET_3M"] - 100 * idx.pct_change(63)   # outperformance vs Nifty, in % points
    else:
        df["RS_3M"] = np.nan
    return df


def resistance_levels(df: pd.DataFrame, price: float, lookback: int = 250, n: int = 3) -> list[float]:
    """Nearest swing highs ABOVE the current price (where sellers showed up before)."""
    d = df.tail(lookback)
    h = d["High"]
    piv = h[(h == h.rolling(11, center=True).max())]
    lv = sorted({round(x, 2) for x in piv if x > price * 1.005})
    return lv[:n]


def support_levels(df: pd.DataFrame, price: float, lookback: int = 120, n: int = 3) -> list[float]:
    """Nearest swing lows BELOW the current price (where buyers showed up before)."""
    d = df.tail(lookback)
    lo = d["Low"]
    piv = lo[(lo == lo.rolling(11, center=True).min())]
    lv = sorted({round(x, 2) for x in piv if x < price * 0.995}, reverse=True)
    return lv[:n]
