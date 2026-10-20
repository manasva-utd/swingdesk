# SwingDesk — personal NSE swing-trading signal dashboard

A free, local tool that tells you **whether** a stock is a swing trade right now, **at what price** to buy,
**where** to put the stop loss, **where** to take profit, **how many shares** to buy, and **what it costs** at Angel One.
You place the orders yourself. Nothing is automated, so the SEBI static-IP / algo-registration rules for API order
placement don't apply to you.

---

## 1. The honest numbers first

| Claim | Reality |
|---|---|
| "5–8% per week" | That compounds to 10–50x a year. Nobody sustains it. Use **4–8% as a per-trade target**, not a weekly account target. A realistic goal for year one: survive, follow rules, aim for **2–4% a month** in good markets and ~0% (cash) in bad ones. |
| Rs 5,000 capital | One round trip costs **~Rs 46 (0.93%)** at Angel One (brokerage min Rs 5 each side, STT 0.1% each side, DP charge Rs 23.6 on sell, stamp, GST). At Rs 25,000 it drops to ~0.5%, at Rs 50,000 ~0.36%. Treat Rs 5k as paid tuition to prove the process; scale only after 20–30 logged trades. |
| Win rate | Good trend-following swing systems win **35–55%** of trades. They make money because winners (2R+) are bigger than losers (1R). Expect 3–5 losses in a row regularly. |
| "Sell calls" | In Indian cash you can't hold a short overnight. SELL here = exit something you own. |
| Backtests | On random data this system loses (as it should — there is no edge in noise, and charges bleed you). **Run the backtest on real data before risking money.** If expectancy < +0.2R after charges, don't trade it. |

### Real-data backtest (run 20 Oct 2026; 40 liquid NSE stocks; Oct 2021 – Oct 2026; pessimistic fills; full Angel One charges)

"R" = the amount you risk on a trade. +0.1R per trade means you earn 10% of your risk on average.

| Version | Capital | Trades | Win % | Avg per trade (after charges) | 2021–24 | 2025–26 |
|---|---|---|---|---|---|---|
| v1 (fixed 2R target, 10-day hold, AMBER allowed) | Rs 5k | 1,021 | 34% | **−0.46R** | negative | negative |
| v2 = current default (trailing exit, 20-day hold, GREEN market only, within 8% of high) | Rs 5k | 709 | 34% | **−0.29R** | −0.18R | −0.63R |
| v2 | Rs 25k | 896 | 40% | **0.00R** (breakeven) | +0.12R | −0.32R |
| v2 | Rs 1L | 904 | 43% | **+0.08R** | +0.20R | −0.24R |

What this means:
- The signals have a small edge **before** costs. At Rs 5k, charges (Rs 29k over the test) wipe it out completely.
- The edge existed in the 2021–24 bull market and **disappeared in 2025–26** (Nifty fell ~12% in 2026, RED regime 64% of days). Every variant tested lost money on 2025–26.
- **Do not trade this with real money yet.** Paper-trade it, let the regime turn GREEN, and re-run `python experiment.py` monthly.

---

## 2. How the system decides (plain English)

1. **Market switch (Nifty + India VIX).** GREEN = trade normal size. AMBER = half size, A-grade only. RED = no new buys.
2. **Two setups only, both "buy strength in an uptrend":**
   - **Breakout** – closes above its prior 20-day high on ≥1.5x normal volume.
   - **Pullback** – stock in an uptrend dips to its 20-day average, then closes strong.
3. **Checklist (13–14 conditions)** across Trend, Momentum, Strength vs Nifty, Volume, Liquidity, Risk and Results date. Each one shows PASS/FAIL with the number and *why it matters*. Score → grade A/B/C.
4. **Trade plan.** Entry = buy-stop just above today's high (the stock must prove itself tomorrow). Stop = below the swing low / breakout level using ATR (normal daily range). Target 1 = 2× risk, Target 2 = 3× risk (used as guides; by default the exit is a trailing stop, see config.EXIT_MODE). Quantity = (2% of capital) ÷ (entry − stop).
5. **Exit rules.** Stop hit → out. Up 1R → stop to entry. T1 → book half. Trail under 20-day EMA. 20 sessions without progress → out. Never hold through quarterly results.
6. **News = veto only.** Google News headlines with red/green flag words; optional free LLM summary.

---

## 3. Setup (≈15 minutes, Rs 0)

```bash
# 1) Install Python 3.11+ from python.org (tick "Add to PATH" on Windows)
# 2) Unzip this folder, open a terminal inside it
python -m venv .venv
# Windows: .venv\Scripts\activate      Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 3) Sanity test (fake data, no internet needed)
python report.py --demo

# 4) Real data
python report.py --symbol BEL          # Option 1: one stock you like
python report.py                       # Option 2: scan Nifty 200

# 5) The dashboard
streamlit run app.py                   # opens http://localhost:8501
```

Every setting (capital, risk %, R:R, time stop, Angel One charges, VIX levels) lives in `config.py`, commented.

### Optional, still free
- **Angel One data** (if Yahoo breaks): create an app at smartapi.angelone.in, enable TOTP, fill `.env` from `.env.example`, set `DATA_SOURCE = "angel"` in config.py. Data-only use; you are not placing orders by API.
- **Telegram alerts**: make a bot via @BotFather, put token + chat id in `.env`, run `python report.py --telegram`.
- **Nightly scan in the cloud**: push the folder to a *private* GitHub repo, add the two Telegram secrets; `.github/workflows/nightly.yml` runs Mon–Fri 5:15 pm IST for free.
- **AI news check**: any OpenAI-compatible free tier (Groq, Gemini). Fill `LLM_*` in `.env`.

---

## 4. Your daily / weekly routine

**Evening (after 4:30 pm IST, ~20 min)**
1. Dashboard → **Market** tab. RED? Stop here. Cash is a position.
2. **Scanner** (or **Analyze** a stock you like). Shortlist max 2 BUY candidates.
3. Open each in **Analyze**: read the failed checks, the chart, the news. Check results date on NSE/BSE.
4. Cross-check on free tools: chart on TradingView, fundamentals on screener.in, shareholding/pledge on NSE.
5. Pick **one**. Copy the order ticket.

**Next morning (Angel One app)**
- Delivery, SL order: trigger = Entry, limit ≈ Entry + 0.5%. Not filled in 2 sessions → cancel.
- Filled → immediately set the stop-loss (GTT / Stop Loss & Target with OCO). Write it in the **Journal** tab.

**Each evening while holding:** Journal tab shows HOLD/SELL and the updated stop. Follow it.

**Sunday (30 min):** review the week's trades. Did you follow every rule? That's the only score that matters for the first 30 trades.

---

## 5. Roadmap (build on top, in this order)

1. **Weeks 1–2:** run the backtest on real data for the 40-stock basket, 5 years. Tune nothing yet; just learn what the numbers look like.
2. **Weeks 2–6:** paper-trade or trade Rs 5k live, one position at a time. Log every trade.
3. **After 20–30 trades:** compare live vs backtest win rate and R. If close and positive → add capital in steps (Rs 25k → 50k). If not → find which rule you broke or which setup loses.
4. **Later:** sector strength ranking, FII/DII flows, delivery % from NSE bhavcopy, weekly-timeframe confirmation, crypto module.

---

## 6. Files

| File | What it does |
|---|---|
| `config.py` | All settings and charge rates |
| `data.py` | Yahoo / Angel One candles, NSE index lists, results date, fundamentals, cache |
| `indicators.py` | EMA, RSI, MACD, ATR, ADX, relative strength, support/resistance |
| `strategy.py` | Market regime, setups, checklist, trade plan, sizing, exit advice |
| `charges.py` | Angel One delivery round-trip cost, breakeven move |
| `backtest.py` | Replays rules with pessimistic fills and full charges |
| `news.py` | Google News RSS + flag words + optional LLM check |
| `scanner.py` / `report.py` | Scan an index, nightly text report, Telegram |
| `journal.py` | Your trade log (journal.csv) |
| `app.py` | The Streamlit dashboard |

*Not investment advice. A tool that applies rules you chose to public data. Verify charges at angelone.in/pricing.*

---

## 7. Put it online (free website, works on your phone)

Netlify can't run Python, so we use **Streamlit Community Cloud** (free, made for this).

1. Push this folder to a **private** GitHub repo (`.gitignore` keeps `.env`, `journal.csv` and the cache out).
2. Go to share.streamlit.io → sign in with GitHub → **Create app** → pick the repo, branch `main`, file `app.py`.
3. **Advanced settings** → Python 3.11 or 3.12 → **Secrets**: `APP_PASSWORD = "choose-a-password"` → Deploy.
4. You get a link like `https://swingdesk-yourname.streamlit.app`. Open it, enter the password, tap **Run tonight's check**.

Know the limits:
- The free app sleeps after a few days without visits; opening it wakes it in ~30 seconds.
- `journal.csv` resets whenever the cloud app restarts. Keep your trade log on your laptop for now (next upgrade: store it in Supabase or Google Sheets).
- Every `git push` to the repo updates the website automatically.

---

## 8. Automatic paper trading (3-4 trades a day, zero effort)

Runs on GitHub Actions (`.github/workflows/paper.yml`), results show in the **🧾 Paper trades** tab.

| Time (IST, Mon-Fri) | What happens |
|---|---|
| ~10:45 AM | Logs the top 2 **intraday** picks from the 10:30 snapshot (long or short, Nifty 100) |
| ~5:00 PM | Closes intraday trades on real 15-min prices (stop / 2R target / 3:15 PM), settles **swing** trades on daily prices, logs 2 new swing picks (held max 5 days) |

- Every trade records result **after** and **before** charges (in R = amount risked). Before-charges tells you if the *idea* works; after-charges tells you if it works *at your size*.
- Swing picks are taken even in RED markets and tagged with the regime, so the scorecard shows whether the market filter actually helps.
- Intraday sizing: max loss Rs 100 per trade, position up to Rs 25,000 (≈5x intraday margin on Rs 5k).
- Starting evidence (replay of the last ~60 sessions, 68 intraday trades): 31% wins, -0.02R before charges, -0.53R after. No edge yet - which is exactly why we paper-trade first.
- Run manually: `python run_paper.py morning` / `python run_paper.py evening`, or GitHub → Actions → paper-trading → Run workflow.
