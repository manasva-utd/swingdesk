"""
SwingDesk - personal swing-trading assistant for NSE stocks (you place the orders yourself).
Run:  streamlit run app.py
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import backtest
import charges
import charts
import config
import data
import dipbuy
import indicators as ind
import intraday
import journal
import news
import paper
import scanner
import strategy

st.set_page_config(page_title="SwingDesk", page_icon="📈", layout="wide")


# ------------------------------------------------------------------ password gate (hosted website)
def _password_ok() -> bool:
    try:
        pw = st.secrets.get("APP_PASSWORD")
    except Exception:
        pw = None
    if not pw or st.session_state.get("auth"):
        return True
    st.title("📈 SwingDesk")
    entered = st.text_input("Password", type="password")
    if entered:
        if entered == pw:
            st.session_state["auth"] = True
            st.rerun()
        st.error("Wrong password.")
    return False


if not _password_ok():
    st.stop()

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("📈 SwingDesk")
    st.caption("Finds dip-buy trades in strong NSE stocks. You place the orders in Angel One.")
    config.CAPITAL = st.number_input("Your trading capital (₹)", 5000, 10_000_000, config.CAPITAL, step=1000,
                                     help="The whole amount goes into one trade at a time. Testing showed that "
                                          "splitting ₹15k into two trades lets charges eat the small wins.")
    st.markdown(f"**One trade at a time · up to ₹{config.CAPITAL:,.0f} per trade**")
    st.divider()
    st.markdown("**Daily routine**")
    st.markdown("1. After **4:30 PM IST** open **🌙 Tonight's plan**\n"
                "2. If there's a BUY, follow the 5 steps\n"
                "3. If not, do nothing. That's part of the system")
    st.divider()
    with st.expander("Advanced"):
        DEMO = st.toggle("Demo mode (fake data)", value=False)
        config.DATA_SOURCE = st.radio("Data source", ["yahoo", "angel"], index=0, horizontal=True)
        if st.button("Refresh all data"):
            st.cache_data.clear()
    st.caption("Not investment advice. A tool that applies tested rules to public data.")


# ------------------------------------------------------------------ cached loaders
@st.cache_data(ttl=3 * 3600, show_spinner=False)
def load_all_daily(universe: str, demo: bool):
    syms = config.FALLBACK_UNIVERSE[:60] if demo else data.get_universe(universe)
    out = {}
    for s in syms:
        try:
            out[s] = data.synthetic_history(s, days=750) if demo else data.get_history(s)
        except Exception:
            pass
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def load_market(demo):
    return scanner.load_market(demo)


@st.cache_data(ttl=3600, show_spinner=False)
def load_hist(sym, demo, period=None):
    return data.synthetic_history(sym, days=750) if demo else data.get_history(sym, period=period)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_news(q):
    return news.fetch_headlines(q)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_results_date(sym):
    return data.get_upcoming_results_date(sym)


@st.cache_data(ttl=3 * 3600, show_spinner=False)
def dip_scan(universe, capital, demo, day_key):
    frames = load_all_daily(universe, demo)
    nifty = load_market(demo)[0]
    return dipbuy.scan(frames, capital, nifty)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def recent_signals(universe, capital, demo, sessions, day_key):
    """Every dip-buy signal of the last N sessions, and what happened next (same rules as the backtest)."""
    frames = load_all_daily(universe, demo)
    rows = []
    for s, df in frames.items():
        if df is None or len(df) < 260:
            continue
        d = dipbuy.prep(df)
        for k in range(len(d) - sessions, len(d) - 1):
            p = dipbuy.evaluate(d.iloc[:k + 1], s, capital)
            if p.action != "BUY":
                continue
            res = dipbuy.replay(d, p.date, p.entry, p.stop, p.half_target, p.qty)
            rows.append({"Signal date": p.date, "Stock": s, "Dip (3d)": f"{p.drop3:+.1f}%", "Buy limit": p.entry,
                         "Result": {"CLOSED": "Closed", "OPEN": "Still open", "EXPIRED": "Not filled",
                                    "PENDING": "Waiting"}[res["status"]],
                         "Exit": res.get("exit"), "Why it ended": res.get("reason", ""),
                         "Net ₹ (after charges)": res.get("net")})
    return pd.DataFrame(rows).sort_values("Signal date", ascending=False) if rows else pd.DataFrame()


# ------------------------------------------------------------------ UI pieces
def money(x):
    return f"₹{x:,.0f}" if x is not None else "-"


def dip_card(p: dipbuy.DipPlan, d: pd.DataFrame, key: str = ""):
    if p.action == "BUY":
        st.success(f"### 🟢 BUY {p.symbol} — dip-buy signal")
    elif p.action == "WATCH":
        st.info(f"### 👀 WATCH {p.symbol} — no trade today")
    else:
        st.error(f"### ⛔ AVOID {p.symbol} (for dip-buying)")
    st.write(p.headline)
    if p.size_note:
        st.caption("ℹ️ " + p.size_note)

    if p.action == "BUY":
        c = st.columns(4)
        c[0].metric("Buy at (limit)", f"₹{p.entry:,.2f}", f"{100*(p.entry/p.close-1):+.1f}% vs today's close", delta_color="off")
        c[1].metric("Stop loss", f"₹{p.stop:,.2f}", f"{100*(p.stop/p.entry-1):+.1f}%", delta_color="inverse")
        c[2].metric("Sell 50% at", f"₹{p.half_target:,.2f}", f"{100*(p.half_target/p.entry-1):+.1f}%")
        c[3].metric("Shares / money used", f"{p.qty} sh", money(p.position_value), delta_color="off")

        st.markdown("#### Your 5-step game plan")
        for i, s in enumerate(p.steps(), 1):
            st.markdown(f"{i}. {s}")

        st.markdown("#### What could happen (after all charges)")
        c = st.columns(3)
        c[0].metric("😞 Stop loss hit (rare, ~1 in 20 trades)", money(p.loss_if_stopped))
        c[1].metric("🙂 Sell 50%, rest at buy price", money(p.profit_half_then_flat))
        c[2].metric("😀 Full bounce plays out", money(p.profit_full_bounce))
        st.caption(f"In 5 years of testing, about 3 out of 4 of these trades ended in profit after charges. "
                   f"Charges on this trade ≈ {money(p.charges_est)}.")

    fig = charts.plan_chart(d, f"{p.symbol} — last 90 trading days", p.entry, p.stop, p.ladder(),
                            extra_lines=[(float(d['EMA200'].iloc[-1]), "200-day avg (uptrend line)", "#6b7280")],
                            rsi_col="RSI2", rsi_levels=(10, 50, 90))
    st.plotly_chart(fig, width="stretch", key=f"chart_{key}_{p.symbol}")
    st.caption("Bottom panel: 2-day RSI. Under 10 = very oversold, which is our buy zone. Shaded green = profit zone, "
               "shaded red = loss zone.")
    if p.watch:
        st.info("👀 " + p.watch)
    with st.expander("Why? See every rule this stock passed or failed"):
        st.dataframe(pd.DataFrame([{"": "✅" if c.passed else "❌", "Rule": c.name, "Value": c.value,
                                    "Why it matters": c.why} for c in p.checks]), hide_index=True, width="stretch")
    if p.action == "BUY":
        st.code(f"ANGEL ONE  ·  {p.symbol}  ·  NSE  ·  Product: DELIVERY\n"
                f"1) BUY {p.qty}  · Order type: LIMIT · Price ₹{p.entry} · Validity: DAY\n"
                f"2) After fill: STOP-LOSS SELL {p.qty} · Trigger ₹{p.stop}\n"
                f"3) SELL {p.half_qty} at ₹{p.half_target} (limit / GTT) → then move stop for the rest to ₹{p.entry}\n"
                f"4) SELL remaining {p.qty - p.half_qty} at the close on the first day it closes above the prior day's high\n"
                f"5) Max {dipbuy.PARAMS['max_hold']} trading days, then sell at the close", language="text")


def regime_box(reg):
    color = {"GREEN": "🟢", "AMBER": "🟠", "RED": "🔴"}[reg.label]
    st.subheader(f"{color} Nifty trend: {reg.label}")
    for r in reg.reasons:
        st.write("• " + r)


def trend_chart(d: pd.DataFrame, p=None, days=180, title=""):
    d = d.tail(days)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="Price"), 1, 1)
    for col, colr in [("EMA20", "#2563eb"), ("EMA50", "#f59e0b"), ("EMA200", "#6b7280")]:
        fig.add_trace(go.Scatter(x=d.index, y=d[col], name=col, line=dict(width=1.2, color=colr)), 1, 1)
    if p is not None and p.entry:
        for y, lab, c in [(p.entry, "Entry", "#2563eb"), (p.stop, "Stop", "#dc2626"), (p.target1, "T1", "#16a34a")]:
            fig.add_hline(y=y, line_dash="dash", line_color=c, annotation_text=f"{lab} {y}", row=1, col=1)
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"], name="Volume", marker_color="#94a3b8"), 2, 1)
    fig.update_layout(height=520, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10), title=title)
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


tabs = st.tabs(["🏠 Start here", "🌙 Tonight's plan", "🔎 Check a stock", "🧾 Paper trades",
                "📒 My trades", "🧪 Proof", "🧰 More tools"])

# ================================================================== START HERE
with tabs[0]:
    st.markdown("## How SwingDesk works")
    st.markdown(f"""
**One idea, tested on 5 years of data: buy short, sharp dips in strong stocks — and get out on the bounce.**

| Step | What happens | In plain English |
|---|---|---|
| 1 | Filter | Only stocks in a clear uptrend (above their 200-day average). |
| 2 | Wait | For a sharp 3-day drop that leaves the stock very oversold. |
| 3 | Buy | With a limit order a bit **below** today's close, for the next session only. |
| 4 | Take profit | Sell **half** on a small bounce, then make the rest risk-free (stop → buy price). |
| 5 | Exit | Sell the rest when the bounce is done, or after 10 days at most. |

**Tested results** (Nifty 200, Oct 2021 – Oct 2026, ₹15,000, one trade at a time, all Angel One charges included):

| Period | Trades | Winners after charges | Net profit |
|---|---|---|---|
| All 5 years | 160 (≈3 a month) | **78%** | +₹14,510 |
| 2025-26 (data the rules were not tuned on) | 64 | **75%** | +₹4,327 |
| Last 60 sessions | 7 | **71%** | ≈ ₹0 (flat) |

**What to expect:** most evenings there is **no** trade. That's normal — the rules are strict on purpose.
Losses are rarer but bigger than wins (average win ≈ +1.6%, average loss ≈ −2.8%, worst single trade ≈ −12.7%). A typical trade lasts about 3 trading days.
""")
    with st.expander("📖 Mini glossary"):
        st.markdown("""
- **Limit order** – buy only at this price or lower.
- **Stop loss** – an order that sells automatically if the price falls to a level, to cap your loss.
- **ATR** – "average true range": how much the stock normally moves in a day. Used to set levels that fit each stock.
- **2-day RSI** – a 0-100 gauge of the last two days. Under 10 = sellers are exhausted (oversold).
- **200-day average** – the average price of the last ~10 months. Above it = long-term uptrend.
- **Delivery** – you actually own the shares overnight (needed for multi-day trades).
- **Paper trade** – a pretend trade with real prices, to test without money.
""")

# ================================================================== TONIGHT
with tabs[1]:
    st.markdown("## 🌙 Tonight's plan")
    st.caption("Scans the Nifty 200 for dip-buy signals using today's closing prices. Best after 4:30 PM IST. "
               "First run takes 1-2 minutes, then it's cached.")
    if st.button("▶️ Find tonight's trade", type="primary"):
        with st.spinner("Scanning 200 stocks..."):
            st.session_state["dip"] = dip_scan("Nifty 200", config.CAPITAL, DEMO, str(date.today()))
    if "dip" in st.session_state:
        buys, watch = st.session_state["dip"]
        if buys:
            best = buys[0]
            st.markdown(f"### ✅ {len(buys)} signal(s) tonight. Best: **{best.symbol}** (most oversold)")
            d = dipbuy.prep(load_hist(best.symbol, DEMO))
            dip_card(best, d, "tonight")
            st.info("ℹ️ Most limit orders (roughly 4 in 5) do **not** fill, because the stock doesn't dip far enough the next "
                    "morning. That's by design: you only buy at a bargain. Not filled = no trade, nothing lost.")
            if len(buys) > 1:
                st.caption("Other signals tonight (take only one trade at a time): " +
                           ", ".join(f"{b.symbol} (RSI2 {b.rsi2})" for b in buys[1:]))
        else:
            st.markdown("### 😴 No trade tonight")
            st.write("No stock in the Nifty 200 passes every rule today. With strict rules, that's most days. "
                     "Doing nothing is part of why the win rate is high.")
        if watch:
            st.markdown("#### 👀 Closest to a signal (oversold, but missing a rule)")
            st.dataframe(pd.DataFrame([{"Stock": w.symbol, "Close": w.close, "2-day RSI": w.rsi2, "3-day move": f"{w.drop3:+.1f}%",
                                        "Missing": ", ".join(c.name.split(" (")[0] for c in w.checks if not c.passed)}
                                       for w in watch]), hide_index=True, width="stretch")
        st.markdown("#### 🗓️ Recent signals and what actually happened")
        with st.spinner("Replaying the last 20 sessions..."):
            rs = recent_signals("Nifty 200", config.CAPITAL, DEMO, 20, str(date.today()))
        if rs.empty:
            st.write("No signals in the last 20 sessions.")
        else:
            st.dataframe(rs, hide_index=True, width="stretch")
            closed = rs[rs["Result"] == "Closed"]
            if len(closed):
                st.caption(f"If you had taken every filled signal — closed: {len(closed)} · winners: {(closed['Net ₹ (after charges)'] > 0).sum()} · "
                           f"net {money(closed['Net ₹ (after charges)'].sum())} (one trade each, ₹{config.CAPITAL:,.0f})")

# ================================================================== CHECK A STOCK
with tabs[2]:
    st.markdown("## 🔎 Check any stock")
    st.caption("Type an NSE symbol (e.g. TATASTEEL, BEL, ICICIBANK). You'll get a clear yes/no for dip-buying, the levels, "
               "and the reasons.")
    sym = st.text_input("NSE symbol", "ICICIBANK").strip().upper()
    if st.button("Check", type="primary") and sym:
        with st.spinner(f"Loading {sym}..."):
            try:
                df = load_hist(sym, DEMO)
                nifty = load_market(DEMO)[0]
                ed = None if DEMO else load_results_date(sym)
                d = dipbuy.prep(df)
                p = dipbuy.evaluate(d, sym, config.CAPITAL, nifty, earnings_date=ed)
                st.caption(f"Prices up to {p.date} · last close ₹{p.close:,.2f}"
                           + (f" · next results: {ed}" if ed else " · results date unknown — check NSE announcements"))
                dip_card(p, d, "check")
                if not DEMO:
                    heads = load_news(f"{sym} share")
                    st.markdown(f"#### 📰 News, last 7 days — {news.news_verdict(heads)}")
                    for h in heads[:8]:
                        st.markdown(f"{'🚩 ' if h['red'] else '🟢 ' if h['green'] else '• '}[{h['title']}]({h['link']}) · {h['published']}")
                    st.caption("News can veto a trade (fraud, probe, results tomorrow). It should not be the reason to buy.")
                with st.expander("Trend-following check (older breakout/pullback rules — not profitable in testing)"):
                    reg = strategy.market_regime(nifty)
                    dd = ind.add_all(df, nifty["Close"])
                    tp = strategy.evaluate(dd, sym, regime=reg)
                    st.write(f"**{tp.action}** · setup {tp.setup} · grade {tp.grade} · score {tp.score}/100")
                    st.plotly_chart(trend_chart(dd, tp, title=sym), width="stretch", key="trend_check")
            except Exception as e:
                st.error(str(e))

# ================================================================== PAPER TRADES
@st.cache_data(ttl=600, show_spinner=False)
def load_paper():
    return paper.load()


with tabs[3]:
    st.markdown("## 🧾 Paper trades (automatic)")
    st.caption("Logged and settled automatically every trading day with real prices and real charges. "
               "**DIP_BUY** = the tested strategy (every signal is logged). **INTRADAY_ORB** = experimental 10:30 AM "
               "intraday picks (no edge found yet — kept running to collect evidence).")
    pdf = load_paper()
    if pdf.empty:
        st.info("No paper trades yet.")
    else:
        for strat, label in [("DIP_BUY", "Dip-buy (tested strategy)"), ("INTRADAY_ORB", "Intraday 10:30 (experimental)")]:
            sub = pdf[pdf["strategy"] == strat]
            closed = sub[sub["status"] == "CLOSED"]
            st.markdown(f"#### {label}")
            c = st.columns(5)
            c[0].metric("Closed trades", len(closed))
            c[1].metric("Winners", f"{100 * (closed['net'] > 0).mean():.0f}%" if len(closed) else "-")
            c[2].metric("Net P&L", money(closed["net"].sum()) if len(closed) else "-")
            c[3].metric("Charges paid", money(closed["charges"].sum()) if len(closed) else "-")
            c[4].metric("Open / waiting", int(sub["status"].isin(["OPEN", "PENDING"]).sum()))
        live = pdf[pdf["status"].isin(["OPEN", "PENDING"])]
        if len(live):
            st.markdown("#### Open & waiting")
            st.dataframe(live[["strategy", "symbol", "status", "signal_date", "entry", "stop", "target", "qty", "why"]],
                         hide_index=True, width="stretch")
        with st.expander("All paper trades"):
            st.dataframe(pdf.iloc[::-1], hide_index=True, width="stretch")
    st.divider()
    st.markdown("#### ⚡ Intraday scan right now (experimental)")
    latest = st.toggle("Use latest candles instead of the 10:30 snapshot", value=False)
    if st.button("Scan Nifty 100 intraday"):
        from datetime import time as dtime
        with st.spinner("Pulling 15-minute candles..."):
            ideas, _ = intraday.scan(data.get_universe("Nifty 100"), cutoff=dtime(15, 30) if latest else intraday.SCAN_CUTOFF, top=6)
        if not ideas:
            st.info("No stock passes the opening-range rules right now.")
        for i in ideas:
            st.markdown(f"**{'🟢 LONG' if i.side == 'LONG' else '🔴 SHORT'} {i.symbol}** @ ₹{i.entry} · stop ₹{i.stop} · "
                        f"target ₹{i.target} · qty {i.qty}  \n<small>{i.why} · data for {i.date}</small>", unsafe_allow_html=True)
        st.caption("Intraday rules lost money in testing (31% winners). Paper-trade only.")

# ================================================================== MY TRADES
with tabs[4]:
    st.markdown("## 📒 My real trades")
    st.caption("Log trades you actually place. Each evening this tells you what to do next. "
               "(On the free website this log can reset when the app restarts — keep a copy on your laptop.)")
    with st.expander("➕ Log a trade"):
        c = st.columns(4)
        js = c[0].text_input("Symbol")
        je = c[1].number_input("Buy price", value=0.0)
        jst = c[2].number_input("Stop loss", value=0.0)
        jt = c[3].number_input("First target (sell 50%)", value=0.0)
        c = st.columns(3)
        jq = c[0].number_input("Shares", value=1, step=1)
        jd = c[1].date_input("Buy date", date.today())
        jn = c[2].text_input("Notes")
        if st.button("Save trade") and js and je and jst:
            journal.add(js, "DIP_BUY", jd, je, jst, jt, jq, jn)
            st.success("Saved.")
    j = journal.load()
    opn = j[j["status"] == "OPEN"] if len(j) else j
    st.markdown(f"#### Open ({len(opn)})")
    for _, r in opn.iterrows():
        try:
            d = dipbuy.prep(load_hist(r["symbol"], DEMO))
            sold = st.checkbox("I already sold 50%", key=f"half{r['id']}")
            adv = dipbuy.exit_advice(d, float(r["entry"]), float(r["stop"]), float(r["target1"]), int(r["qty"]), sold, r["entry_date"])
            last = float(d["Close"].iloc[-1])
            pnl = charges.round_trip(float(r["entry"]), last, int(r["qty"]))["net_pnl"]
            st.markdown(f"**{r['symbol']}** · {r['qty']} sh @ ₹{r['entry']} · last ₹{last:,.2f} · if sold now ≈ {money(pnl)} → **{adv['action']}**")
            st.write("👉 " + adv["msg"])
            c = st.columns(3)
            xp = c[0].number_input("Exit price", value=last, key=f"x{r['id']}")
            xd = c[1].date_input("Exit date", date.today(), key=f"d{r['id']}")
            if c[2].button("Close trade", key=f"c{r['id']}"):
                journal.close(int(r["id"]), xd, xp)
                st.rerun()
        except Exception as e:
            st.error(f"{r['symbol']}: {e}")
    st.markdown("#### History")
    st.json(journal.stats())
    st.dataframe(j, hide_index=True, width="stretch")

# ================================================================== PROOF
with tabs[5]:
    st.markdown("## 🧪 Proof: how the rules were tested")
    st.markdown("""
- **Data:** daily prices, Nifty 200 stocks, Oct 2021 – Oct 2026 (Yahoo Finance).
- **Honest fills:** buy only if the next session actually traded at or below the limit; if it opened lower, you get the lower price.
  If a stop and a target are touched the same day, the test assumes the **stop** hit first.
- **All Angel One charges:** brokerage (₹5-20 per order), STT 0.1% both sides, stamp duty, exchange fees, GST,
  and ₹23.6 DP charge per sell day.
- **No peeking:** rules were tuned on 2021-24 only, then checked on 2025-26 they had never seen.
- **480 rule variations** were tested; the one used here was picked for being consistent across all periods, not for the best single number.
""")
    st.markdown("""
| What was tested | Winners after charges | Result |
|---|---|---|
| Old breakout/pullback rules (₹5k) | 34% | Lost money |
| Intraday 10:30 opening-range (60 days) | 31% | Lost money |
| **Dip-buy, ₹15k, one trade at a time — 5 years** | **78%** | **+₹14,510** |
| Dip-buy — 2025-26 only | 75% | +₹4,327 |
| Dip-buy split into 2 × ₹7.5k trades | 56-61% | Much worse — charges eat small wins |
""")
    st.caption("Past results don't guarantee future ones. That's why every live signal is also paper-traded automatically.")

# ================================================================== MORE TOOLS
with tabs[6]:
    sub = st.tabs(["🧭 Market trend", "📡 Trend scanner (old)", "⚙️ Old backtester", "💸 Charges calculator"])
    with sub[0]:
        try:
            nifty, vix = load_market(DEMO)
            reg = strategy.market_regime(nifty, vix)
            regime_box(reg)
            st.plotly_chart(trend_chart(ind.add_all(nifty), days=250, title="Nifty 50"), width="stretch", key="nifty")
            st.caption("For dip-buying this is context only: the rules still work in weak markets, just a bit less often.")
        except Exception as e:
            st.error(f"Could not load market data: {e}")
    with sub[1]:
        st.caption("The older breakout/pullback scanner. It lost money in testing; kept for reference.")
        if st.button("Run trend scan"):
            syms = config.FALLBACK_UNIVERSE[:60] if DEMO else data.get_universe("Nifty 200")
            bar = st.progress(0.0)
            reg, plans = scanner.scan(syms, demo=DEMO, progress=lambda x: bar.progress(x))
            bar.empty()
            r = scanner.rank(plans)
            st.dataframe(scanner.to_table(r["buy"] + r["blocked"] + r["near"]), hide_index=True, width="stretch")
    with sub[2]:
        st.caption("Backtester for the older trend rules.")
        if st.button("Run old backtest (40 stocks, 5y)"):
            with st.spinner("Running..."):
                nifty = data.get_history(config.INDEX_SYMBOL, period="5y")
                frames = {s: load_hist(s, DEMO, "5y") for s in config.FALLBACK_UNIVERSE[:40]}
                res = backtest.run_many(frames, nifty)
            st.json(res["stats"])
    with sub[3]:
        c = st.columns(3)
        bp = c[0].number_input("Buy price", value=500.0)
        sp = c[1].number_input("Sell price", value=510.0)
        q = c[2].number_input("Shares", value=30, step=1)
        rt = charges.round_trip(bp, sp, int(q))
        c = st.columns(3)
        c[0].metric("Total charges", money(rt["total_charges"]))
        c[1].metric("Profit after charges", money(rt["net_pnl"]))
        c[2].metric("Move needed to break even", f"+{charges.breakeven_move_pct(bp, int(q))}%")
        with st.expander("Full breakdown"):
            st.dataframe(pd.DataFrame([rt]).T.rename(columns={0: "₹"}), width="stretch")
