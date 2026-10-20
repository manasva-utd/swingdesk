"""
Data layer: daily OHLCV candles for NSE stocks and the Nifty index.

Two free sources:
  * yahoo  - yfinance, no login. End-of-day data, good enough for swing trading.
             Unofficial: can break or rate-limit; that's why there is a cache.
  * angel  - Angel One SmartAPI (official, free with your Angel account).
             Needs keys in a .env file (see README). Use it when Yahoo misbehaves.
"""
from __future__ import annotations

import io
import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import config

os.makedirs(config.CACHE_DIR, exist_ok=True)


# ---------------------------------------------------------------- helpers
def _cache_path(symbol: str) -> str:
    safe = symbol.replace("^", "IDX_").replace("&", "and").replace("/", "_")
    return os.path.join(config.CACHE_DIR, f"{safe}.csv")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.title)[["Open", "High", "Low", "Close", "Volume"]]
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=["Close"])


def _fresh(path: str, max_age_hours: float) -> bool:
    return os.path.exists(path) and (time.time() - os.path.getmtime(path)) < max_age_hours * 3600


# ---------------------------------------------------------------- yahoo
def _yahoo(symbol: str, period: str) -> pd.DataFrame:
    import yfinance as yf

    ticker = symbol if symbol.startswith("^") else f"{symbol}.NS"
    df = yf.download(ticker, period=period, interval="1d", auto_adjust=True,
                     progress=False, threads=False)
    return _clean(df)


# ---------------------------------------------------------------- angel one
_ANGEL = {"client": None, "master": None}
_ANGEL_INDEX_TOKENS = {"^NSEI": "99926000", "^INDIAVIX": "99926017", "^NSEBANK": "99926009"}


def _angel_client():
    if _ANGEL["client"] is not None:
        return _ANGEL["client"]
    from dotenv import load_dotenv
    import pyotp
    from SmartApi import SmartConnect

    load_dotenv()
    obj = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
    totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
    sess = obj.generateSession(os.environ["ANGEL_CLIENT_CODE"], os.environ["ANGEL_PIN"], totp)
    if not sess or not sess.get("status"):
        raise RuntimeError(f"Angel One login failed: {sess}")
    _ANGEL["client"] = obj
    return obj


def _angel_token(symbol: str) -> str:
    if symbol in _ANGEL_INDEX_TOKENS:
        return _ANGEL_INDEX_TOKENS[symbol]
    if _ANGEL["master"] is None:
        import requests

        path = os.path.join(config.CACHE_DIR, "angel_scrip_master.json")
        if not _fresh(path, 24):
            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            with open(path, "wb") as f:
                f.write(requests.get(url, timeout=60).content)
        m = pd.read_json(path)
        m = m[(m["exch_seg"] == "NSE") & (m["symbol"].str.endswith("-EQ"))]
        _ANGEL["master"] = dict(zip(m["symbol"].str.replace("-EQ", "", regex=False), m["token"].astype(str)))
    return _ANGEL["master"][symbol]


def _angel(symbol: str, period: str) -> pd.DataFrame:
    days = {"6mo": 190, "1y": 370, "2y": 740, "3y": 1100, "5y": 1850}.get(period, 740)
    obj = _angel_client()
    to = datetime.now()
    frm = to - timedelta(days=days)
    res = obj.getCandleData({
        "exchange": "NSE", "symboltoken": _angel_token(symbol), "interval": "ONE_DAY",
        "fromdate": frm.strftime("%Y-%m-%d 09:15"), "todate": to.strftime("%Y-%m-%d 15:30"),
    })
    rows = (res or {}).get("data") or []
    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    df["Date"] = pd.to_datetime(df["Date"].str[:10])
    time.sleep(0.35)  # stay under SmartAPI rate limits
    return _clean(df.set_index("Date"))


# ---------------------------------------------------------------- public API
def get_history(symbol: str, period: str | None = None, source: str | None = None,
                max_age_hours: float = 6) -> pd.DataFrame:
    """Daily candles for an NSE symbol (e.g. 'TATAMOTORS') or index ('^NSEI')."""
    period = period or config.HISTORY_PERIOD
    source = source or config.DATA_SOURCE
    path = _cache_path(symbol)
    if _fresh(path, max_age_hours):
        return pd.read_csv(path, index_col=0, parse_dates=True)
    try:
        df = _angel(symbol, period) if source == "angel" else _yahoo(symbol, period)
    except Exception as e:  # network / rate-limit: fall back to stale cache if we have one
        if os.path.exists(path):
            return pd.read_csv(path, index_col=0, parse_dates=True)
        raise RuntimeError(f"Could not fetch {symbol} from {source}: {e}") from e
    if not df.empty:
        df.to_csv(path)
    return df


def get_universe(name: str = "Nifty 200") -> list[str]:
    """Symbols of an NSE index (downloaded from NSE archives), cached for a week."""
    if name not in config.NIFTY_LIST_URLS:
        return list(config.FALLBACK_UNIVERSE)
    path = os.path.join(config.CACHE_DIR, f"universe_{name.replace(' ', '_')}.csv")
    try:
        if not _fresh(path, 24 * 7):
            import requests

            r = requests.get(config.NIFTY_LIST_URLS[name], timeout=20,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            with open(path, "w") as f:
                f.write(r.text)
        syms = pd.read_csv(path)["Symbol"].dropna().astype(str).str.strip().tolist()
        return syms or list(config.FALLBACK_UNIVERSE)
    except Exception:
        return list(config.FALLBACK_UNIVERSE)


def get_upcoming_results_date(symbol: str):
    """Next earnings date if Yahoo knows it (None otherwise). Used to avoid holding through results."""
    try:
        import yfinance as yf

        cal = yf.Ticker(f"{symbol}.NS").calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if dates:
            d = pd.to_datetime(dates[0])
            return d.date() if d >= pd.Timestamp.today().normalize() else None
    except Exception:
        pass
    return None


def get_fundamentals(symbol: str) -> dict:
    """A few sanity-check numbers from Yahoo (may be missing for some stocks)."""
    keys = ["longName", "sector", "industry", "marketCap", "trailingPE", "priceToBook",
            "debtToEquity", "returnOnEquity", "revenueGrowth", "earningsGrowth",
            "heldPercentInsiders", "fiftyTwoWeekHigh", "fiftyTwoWeekLow"]
    try:
        import yfinance as yf

        info = yf.Ticker(f"{symbol}.NS").info or {}
        return {k: info.get(k) for k in keys}
    except Exception:
        return {}


# ---------------------------------------------------------------- offline demo data
def synthetic_history(symbol: str, days: int = 500, seed: int | None = None) -> pd.DataFrame:
    """Fake but realistic-looking candles. ONLY for testing the code offline."""
    rng = np.random.default_rng(seed if seed is not None else abs(hash(symbol)) % 2**32)
    drift = rng.normal(0.0004, 0.0008)
    vol = rng.uniform(0.012, 0.025)
    regime = np.sin(np.linspace(0, rng.uniform(2, 6) * np.pi, days)) * 0.002
    rets = rng.normal(drift + regime, vol)
    close = rng.uniform(150, 2000) * np.exp(np.cumsum(rets))
    open_ = close * (1 + rng.normal(0, vol / 3, days))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, days)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, days)))
    volume = rng.lognormal(14, 0.5, days) * (1 + 3 * (np.abs(rets) > 2 * vol))
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "Volume": volume.round()}, index=idx)
