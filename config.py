"""
SwingDesk settings. Change numbers here, not inside the code.
Every value is a plain-English rule you can read and argue with.
"""

# ---------- Money & risk ----------
CAPITAL = 5000            # total trading capital in INR
RISK_PER_TRADE_PCT = 2.0  # max you can LOSE on one trade, as % of capital (2% of 5000 = Rs 100)
MAX_OPEN_POSITIONS = 1    # with Rs 5,000, one position at a time keeps charges sane
MAX_POSITION_PCT = 100.0  # max % of capital in a single stock
MIN_REWARD_RISK = 2.0     # only take trades where target-1 is >= 2x the risk
MAX_STOP_PCT = 7.0        # skip setups where the stop is more than 7% below entry
MAX_HOLD_DAYS = 20        # time stop: exit after 20 trading days (~4 weeks) if the trade never got going
EXIT_MODE = "trail"       # "trail" = no fixed exit; stop to entry at +1R, then trail under 20-day EMA (tested better)
                          # "target" = sell everything at Target 1
NEAR_HIGH_PCT = 8         # stock must be within 8% of its 52-week high
AMBER_TRADES = False      # AMBER market = no new buys (tested: AMBER-day trades lost money)
INTRADAY_EXPOSURE = 25000 # intraday (MIS) margin ~5x: Rs 5k can hold ~Rs 25k of stock for the day. Max loss per trade is still RISK_PER_TRADE_PCT of CAPITAL.
ENTRY_VALID_DAYS = 2      # cancel the buy order if not filled within 2 sessions

# ---------- Stock filters ----------
MIN_PRICE = 50            # avoid penny stocks
MIN_AVG_TURNOVER_CR = 25  # avg daily traded value >= Rs 25 crore (liquidity / easy exits)
# A stock must allow buying a sensible number of shares with the capital you have:
MAX_PRICE_FRACTION_OF_CAPITAL = 0.5  # share price <= 50% of capital (so >=2 shares)

# ---------- Market regime (the "should I trade at all" switch) ----------
INDEX_SYMBOL = "^NSEI"    # Nifty 50 on Yahoo
VIX_SYMBOL = "^INDIAVIX"  # India VIX on Yahoo
VIX_CAUTION = 18          # above this, halve position size
VIX_DANGER = 24           # above this, no new longs

# ---------- Charges: Angel One equity delivery (verify on angelone.in/pricing) ----------
# Revised schedule effective 17 Nov 2025: lower of Rs 20 or 0.1%, minimum Rs 5, per executed order.
BROKERAGE_PCT = 0.10
BROKERAGE_MAX = 20.0
BROKERAGE_MIN = 5.0
STT_PCT = 0.10            # delivery: 0.1% on buy AND sell
EXCH_TXN_PCT = 0.00297    # NSE transaction charge
SEBI_FEE_PCT = 0.0001     # Rs 10 per crore
STAMP_BUY_PCT = 0.015     # buy side only
GST_PCT = 18.0            # on brokerage + exchange + SEBI fee
DP_CHARGE_PER_SELL = 23.6 # per scrip per sell day, incl GST (non-BSDA). BSDA accounts: 59.0
STCG_TAX_PCT = 20.0       # short-term capital gains tax on profits (holding < 12 months)

# ---------- Data ----------
DATA_SOURCE = "yahoo"     # "yahoo" (free, no login) or "angel" (free, needs SmartAPI keys in .env)
HISTORY_PERIOD = "2y"
CACHE_DIR = "data_cache"
JOURNAL_FILE = "journal.csv"

# Fallback universe if the NSE index list can't be downloaded: liquid Nifty 100-200 names.
FALLBACK_UNIVERSE = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "ITC", "SBIN", "BHARTIARTL",
    "LT", "AXISBANK", "KOTAKBANK", "HINDUNILVR", "BAJFINANCE", "MARUTI", "SUNPHARMA",
    "NTPC", "POWERGRID", "ONGC", "COALINDIA", "TATASTEEL", "JSWSTEEL",
    "HCLTECH", "WIPRO", "TECHM", "ADANIPORTS", "ADANIENT", "M&M", "TITAN", "ULTRACEMCO",
    "ASIANPAINT", "NESTLEIND", "GRASIM", "HINDALCO", "BPCL", "IOC", "GAIL", "BEL", "HAL",
    "BANKBARODA", "PNB", "CANBK", "UNIONBANK", "IDFCFIRSTB", "FEDERALBNK", "INDUSINDBK",
    "DLF", "GODREJPROP", "LODHA", "TATAPOWER", "ADANIPOWER", "JINDALSTEL", "SAIL", "NMDC",
    "VEDL", "HINDZINC", "BHEL", "IRFC", "RVNL", "IRCTC", "PFC", "RECLTD", "NHPC", "SJVN",
    "ETERNAL", "PAYTM", "NYKAA", "POLICYBZR", "DMART", "TRENT", "VBL",
    "DABUR", "MARICO", "COLPAL", "GODREJCP", "TATACONSUM", "BRITANNIA", "CIPLA", "DRREDDY",
    "DIVISLAB", "LUPIN", "AUROPHARMA", "ZYDUSLIFE", "APOLLOHOSP", "MAXHEALTH", "BIOCON",
    "HEROMOTOCO", "BAJAJ-AUTO", "EICHERMOT", "TVSMOTOR", "ASHOKLEY", "MOTHERSON", "BOSCHLTD",
    "SIEMENS", "ABB", "CGPOWER", "POLYCAB", "HAVELLS", "DIXON", "PERSISTENT", "COFORGE",
    "LTIM", "MPHASIS", "NAUKRI", "INDIGO", "CHOLAFIN", "SHRIRAMFIN", "MUTHOOTFIN", "LICHSGFIN",
    "SBILIFE", "HDFCLIFE", "ICICIPRULI", "SBICARD", "IDEA", "YESBANK", "SUZLON", "INOXWIND",
]

NIFTY_LIST_URLS = {
    "Nifty 100": "https://archives.nseindia.com/content/indices/ind_nifty100list.csv",
    "Nifty 200": "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
    "Nifty 500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
}
