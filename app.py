"""
SwingDesk - personal swing-trading signal dashboard for NSE stocks.
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
import config
import data
import indicators as ind
import journal
import intraday
import news
import paper
import scanner
import strategy

st.set_page_config(page_title="SwingDesk", page_icon="📈", layout="wide")


# ------------------------------------------------------------------ password gate (for the hosted website)
def _password_ok() -> bool:
    try:
        pw = st.secrets.get("APP_PASSWORD")
    except Exception:
        pw = None
    if not pw:  # no password set (e.g. running on your laptop) -> open
        return True
    if st.session_state.get("auth"):
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
    st.caption("NSE swing signals - you place the orders")
    config.CAPITAL = st.number_input("Capital (Rs)", 1000, 10_000_000, config.CAPITAL, step=1000)
    config.RISK_PER_TRADE_PCT = st.slider("Max loss per trade (% of capital)", 0.5, 5.0, config.RISK_PER_TRADE_PCT, 0.5)
    config.MIN_REWARD_RISK = st.slider("Min reward : risk", 1.5, 3.0, config.MIN_REWARD_RISK, 0.5)
    config.MAX_HOLD_DAYS = st.slider("Time stop (trading days)", 5, 30, config.MAX_HOLD_DAYS)
    src = st.radio("Data source", ["yahoo", "angel"], index=0, horizontal=True,
                   help="Yahoo = no login. Angel = your SmartAPI keys in .env")
    config.DATA_SOURCE = src
    DEMO = st.toggle("Demo mode (fake data, offline test)", value=False)
    st.divider()
    st.caption(f"Risk per trade: **Rs {config.CAPITAL * config.RISK_PER_TRADE_PCT / 100:,.0f}**")
    st.caption("Signals use end-of-day candles. Run after 4:30 pm IST, place orders next morning.")
    if st.button("Clear data cache"):
        st.cache_data.clear()


# ------------------------------------------------------------------ cached loaders
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
def load_fund(sym):
    return data.get_fundamentals(sym)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_results_date(sym):
    return data.get_upcoming_results_date(sym)


def price_chart(d: pd.DataFrame, p=None, days=180, title=""):
    d = d.tail(days)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
                                 name="Price", increasing_line_color="#16a34a", decreasing_line_color="#dc2626"), 1, 1)
    for col, colr in [("EMA20", "#2563eb"), ("EMA50", "#f59e0b"), ("EMA200", "#6b7280")]:
        fig.add_trace(go.Scatter(x=d.index, y=d[col], name=col, line=dict(width=1.3, color=colr)), 1, 1)
    if p is not None and p.entry:
        for y, lab, c in [(p.entry, "Entry", "#2563eb"), (p.stop, "Stop", "#dc2626"),
                          (p.target1, "T1", "#16a34a"), (p.target2, "T2", "#15803d")]:
            fig.add_hline(y=y, line_dash="dash", line_color=c, annotation_text=f"{lab} {y}", row=1, col=1)
    elif p is not None:
        fig.add_hline(y=p.watch_levels["breakout_above"], line_dash="dot", line_color="#7c3aed",
                      annotation_text=f"Breakout > {p.watch_levels['breakout_above']}", row=1, col=1)
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"], name="Volume", marker_color="#94a3b8"), 2, 1)
    fig.add_trace(go.Scatter(x=d.index, y=d["VOL_AVG20"], name="Vol avg", line=dict(color="#0f172a", width=1)), 2, 1)
    fig.add_trace(go.Scatter(x=d.index, y=d["RSI"], name="RSI", line=dict(color="#7c3aed", width=1.3)), 3, 1)
    for y in (30, 50, 70):
        fig.add_hline(y=y, line_dash="dot", line_color="#cbd5e1", row=3, col=1)
    fig.update_layout(height=640, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10),
                      title=title, legend=dict(orientation="h", y=1.02))
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


def regime_box(reg):
    color = {"GREEN": "🟢", "AMBER": "🟠", "RED": "🔴"}[reg.label]
    st.subheader(f"{color} Market regime: {reg.label}  ·  position size x{reg.size_multiplier}")
    for r in reg.reasons:
        st.write("• " + r)


def plan_card(p):
    act_color = {"BUY": "green", "WATCH": "orange", "AVOID": "red"}[p.action]
    st.markdown(f"### :{act_color}[{p.action}] {p.symbol} — {p.setup} · grade {p.grade} · score {p.score}/100")
    if p.entry:
        c = st.columns(5)
        c[0].metric("Entry (buy-stop)", f"₹{p.entry}")
        c[1].metric("Stop loss", f"₹{p.stop}", f"-{p.risk_pct}%", delta_color="inverse")
        c[2].metric("Target 1", f"₹{p.target1}", f"+{100*(p.target1/p.entry-1):.1f}%")
        c[3].metric("Target 2", f"₹{p.target2}", f"+{100*(p.target2/p.entry-1):.1f}%")
        c[4].metric("Reward : risk", f"{p.reward_risk}R")
        c = st.columns(5)
        c[0].metric("Quantity", p.qty)
        c[1].metric("Position", f"₹{p.position_value:,.0f}")
        c[2].metric("Max loss (incl. charges)", f"₹{p.max_loss_rs:,.0f}")
        c[3].metric("Net profit at T1", f"₹{p.profit_t1_net_rs:,.0f}")
        c[4].metric("Charges / breakeven", f"₹{p.charges_round_trip:.0f}", f"needs +{p.breakeven_pct}% to break even", delta_color="off")
        if p.action == "BUY" and p.qty:
            st.code(
                f"ANGEL ONE ORDER TICKET  ({p.symbol}, NSE, Product: DELIVERY)\n"
                f"1) BUY {p.qty} - Order type: SL (stop-limit) - Trigger ₹{p.entry} - Limit ₹{round(p.entry*1.005,1)}\n"
                f"   Valid {config.ENTRY_VALID_DAYS} sessions (re-place next day if unfilled), then cancel.\n"
                f"2) After fill: GTT / stop-loss SELL {p.qty} at trigger ₹{p.stop}\n"
                f"3) Target: sell half (or all) at ₹{p.target1}; trail the rest under the 20-day EMA, max to ₹{p.target2}\n"
                f"4) Time stop: exit after {config.MAX_HOLD_DAYS} sessions if T1 not reached.", language="text")
    else:
        w = p.watch_levels
        st.info(f"No setup today. **Breakout trigger:** close above ₹{w['breakout_above']} on ≥1.5x volume · "
                f"**Pullback buy zone:** ₹{w['pullback_zone']} · **Trend breaks below:** ₹{w['trend_dies_below']} · "
                f"Daily range (ATR): ₹{w['atr_rs']} ({w['atr_pct']}%)")
    for n in p.notes:
        st.warning(n)
    if p.resistance or p.support:
        st.caption(f"Resistance above: {p.resistance or '—'}  ·  Support below: {p.support or '—'}")


def checklist_table(p):
    return pd.DataFrame([{"": "✅" if c.passed else "❌", "Group": c.group, "Condition": c.name,
                          "Value": c.value, "Why it matters": c.why} for c in p.checks])


@st.cache_data(ttl=3 * 3600, show_spinner=False)
def tonight_scan(universe, demo, capital, risk_pct, day):
    syms = config.FALLBACK_UNIVERSE[:60] if demo else data.get_universe(universe)
    return scanner.scan(syms, demo=demo, capital=capital)


tabs = st.tabs(["🌙 Tonight", "🧾 Paper trades", "🧭 Market", "🔎 Analyze a stock", "📡 Scanner", "🧪 Backtest",
                "📒 Journal & positions", "💸 Charges"])
TONIGHT, PAPER, tabs = tabs[0], tabs[1], tabs[2:]

# ------------------------------------------------------------------ Paper trading
@st.cache_data(ttl=600, show_spinner=False)
def load_paper():
    return paper.load()


@st.cache_data(ttl=600, show_spinner=False)
def live_intraday(day_key, latest):
    from datetime import datetime, time as dtime
    cutoff = dtime(15, 30) if latest else intraday.SCAN_CUTOFF
    ideas, _ = intraday.scan(data.get_universe("Nifty 100"), cutoff=cutoff, top=6)
    return ideas


with PAPER:
    st.markdown("## Paper trading scoreboard")
    st.caption("Trades are picked and settled automatically by fixed rules, using real prices and real Angel One "
               "charges. 10:30 AM intraday picks (closed by 3:15 PM) + evening swing picks (held up to 5 days). "
               "Swing picks are taken even in a RED market and tagged, so we can see if the market filter helps.")
    pdf = load_paper()
    if pdf.empty:
        st.info("No paper trades yet. The first ones are logged automatically at ~10:45 AM IST on the next trading day.")
    else:
        closed = pdf[pdf["status"] == "CLOSED"]
        c = st.columns(5)
        c[0].metric("Closed trades", len(closed))
        c[1].metric("Win rate", f"{100 * (closed['net'] > 0).mean():.0f}%" if len(closed) else "-")
        c[2].metric("Avg result (after charges)", f"{closed['R'].mean():+.2f}R" if len(closed) else "-")
        c[3].metric("Avg result (before charges)", f"{closed['R_gross'].mean():+.2f}R" if len(closed) else "-")
        c[4].metric("Net P&L", f"₹{closed['net'].sum():,.0f}" if len(closed) else "-")
        st.caption("R = the amount risked on a trade (₹100 at your settings). +0.20R means you earn ₹20 per trade on average. "
                   "If 'before charges' is positive but 'after charges' is negative, the idea works but the trade size is too small for the fees.")
        live = pdf[pdf["status"].isin(["OPEN", "PENDING"])]
        st.markdown(f"#### Open & pending ({len(live)})")
        if len(live):
            st.dataframe(live[["strategy", "symbol", "side", "status", "signal_date", "entry", "stop", "target", "qty", "why"]],
                         hide_index=True, width="stretch")
        perf = paper.performance(pdf)
        if not perf.empty:
            st.markdown("#### Scorecard by strategy and market regime")
            st.dataframe(perf, hide_index=True, width="stretch")
        if len(closed):
            cc = closed.copy()
            cc["equity"] = cc["net"].cumsum()
            st.line_chart(cc.reset_index(drop=True)["equity"])
        with st.expander("All paper trades"):
            st.dataframe(pdf.iloc[::-1], hide_index=True, width="stretch")
    st.divider()
    st.markdown("#### ⚡ Intraday scan right now")
    latest = st.toggle("Use the latest candles instead of the 10:30 snapshot", value=False)
    if st.button("Scan Nifty 100 intraday"):
        with st.spinner("Pulling 15-minute candles for 100 stocks..."):
            ideas = live_intraday(str(date.today()) + str(latest), latest)
        if not ideas:
            st.info("No stock passes the opening-range rules right now.")
        for i in ideas:
            st.markdown(f"**{'🟢 LONG' if i.side == 'LONG' else '🔴 SHORT'} {i.symbol}** @ ₹{i.entry} · stop ₹{i.stop} · "
                        f"target ₹{i.target} · qty {i.qty}  \n<small>{i.why} · data for {i.date}</small>", unsafe_allow_html=True)
        st.caption("Yahoo intraday data is ~15 minutes delayed. Shorts are intraday-only (MIS) and must be closed by 3:15 PM.")

# ------------------------------------------------------------------ Tonight (the one-button flow)
with TONIGHT:
    st.markdown("## What should I do tomorrow?")
    st.caption("One button. Checks the market, scans the Nifty 200, and gives you one answer. "
               "Best used after 4:30 pm IST. Takes 1-2 minutes the first time, then it's cached.")
    if st.button("▶️ Run tonight's check", type="primary"):
        with st.spinner("Checking the market and scanning 200 stocks..."):
            reg, plans = tonight_scan("Nifty 200", DEMO, config.CAPITAL, config.RISK_PER_TRADE_PCT, str(date.today()))
        st.session_state["tonight"] = (reg, plans)
    if "tonight" in st.session_state:
        reg, plans = st.session_state["tonight"]
        r = scanner.rank(plans)
        light = {"GREEN": "🟢", "AMBER": "🟠", "RED": "🔴"}[reg.label]
        if r["buy"]:
            p = r["buy"][0]
            st.success(f"### {light} Market {reg.label} — 1 trade idea: **{p.symbol}**")
            plan_card(p)
            with st.expander("Why? (checklist)"):
                st.dataframe(checklist_table(p), hide_index=True, width="stretch")
            if not DEMO:
                heads = load_news(f"{p.symbol} share")
                st.markdown(f"**News check:** {news.news_verdict(heads)}")
                for h in heads[:5]:
                    st.markdown(f"{'🚩 ' if h['red'] else '🟢 ' if h['green'] else ''}[{h['title']}]({h['link']})")
            st.warning("Before buying: check the results date on NSE and read the headlines. "
                       "Then place the order ticket above in Angel One.")
            if len(r["buy"]) > 1:
                st.caption("Other candidates: " + ", ".join(f"{x.symbol} ({x.grade})" for x in r["buy"][1:6]))
        else:
            st.error(f"### {light} No trade tomorrow")
            for x in reg.reasons[:2]:
                st.write("• " + x)
            st.write("Sitting in cash when the odds are bad is part of the system, not a failure of it.")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### ⭐ Best setups (blocked today)")
            if r["blocked"]:
                for p in r["blocked"][:5]:
                    why = "; ".join(p.notes) or "failed: " + ", ".join(c.name for c in p.checks if not c.passed)
                    st.markdown(f"**{p.symbol}** · {p.setup} · grade {p.grade}  \n<small>{why}</small>",
                                unsafe_allow_html=True)
            else:
                st.write("None.")
        with c2:
            st.markdown("#### 👀 Watchlist — close to a breakout")
            if r["near"]:
                for p in r["near"][:8]:
                    st.markdown(f"**{p.symbol}** — close ₹{p.close}, buy only if it closes above "
                                f"**₹{p.watch_levels['breakout_above']}**")
            else:
                st.write("None.")
        st.caption(f"Scanned {len(plans)} stocks · data up to {plans[0].date if plans else '-'}. "
                   "Tap a name into 'Analyze a stock' for the full chart and checklist.")

# ------------------------------------------------------------------ Market
with tabs[0]:
    try:
        nifty, vix = load_market(DEMO)
        reg = strategy.market_regime(nifty, vix)
        regime_box(reg)
        st.plotly_chart(price_chart(ind.add_all(nifty), days=250, title="Nifty 50"), width="stretch")
        st.caption("Why this matters: in a falling market, 60-70% of stock breakouts fail. The regime switch is the "
                   "single biggest drawdown saver in the system.")
    except Exception as e:
        st.error(f"Could not load market data: {e}")

# ------------------------------------------------------------------ Analyze
with tabs[1]:
    st.write("**Option 1 workflow:** type a stock you like. The system tells you if it's a trade, at what levels, and why.")
    sym = st.text_input("NSE symbol (e.g. TATASTEEL, BEL, ICICIBANK)", "BEL").strip().upper()
    if st.button("Analyze", type="primary") and sym:
        with st.spinner(f"Pulling data for {sym}..."):
            try:
                nifty, vix = load_market(DEMO)
                reg = strategy.market_regime(nifty, vix)
                df = load_hist(sym, DEMO)
                d = ind.add_all(df, nifty["Close"])
                ed = None if DEMO else load_results_date(sym)
                p = strategy.evaluate(d, sym, regime=reg, earnings_date=ed)
                st.session_state["last_plan"] = p
                plan_card(p)
                st.caption(f"Data up to {p.date} · market regime {reg.label}"
                           + (f" · next results: {ed}" if ed else " · results date: unknown, check NSE/BSE announcements"))
                c1, c2 = st.columns([3, 2])
                with c1:
                    st.plotly_chart(price_chart(d, p, title=sym), width="stretch")
                with c2:
                    st.dataframe(checklist_table(p), hide_index=True, width="stretch", height=520)
                if not DEMO:
                    f = load_fund(sym)
                    if f:
                        st.markdown("**Fundamentals sanity check** (Yahoo; verify on screener.in)")
                        fm = {k: v for k, v in f.items() if v is not None}
                        st.json(fm, expanded=False)
                    heads = load_news(f"{sym} share")
                    st.markdown(f"**News, last 7 days — {news.news_verdict(heads)}**")
                    for h in heads[:10]:
                        flag = "🚩 " if h["red"] else "🟢 " if h["green"] else ""
                        st.markdown(f"{flag}[{h['title']}]({h['link']}) · {h['published']}")
                    ai = news.llm_summary(sym, f.get("longName") or sym if f else sym, heads,
                                          f"{p.action} {p.setup} entry {p.entry} stop {p.stop} T1 {p.target1}")
                    if ai:
                        st.markdown("**AI risk check**")
                        st.write(ai)
            except Exception as e:
                st.error(f"{e}")

# ------------------------------------------------------------------ Scanner
with tabs[2]:
    st.write("**Option 2 workflow:** let the system find setups across an index.")
    uni = st.selectbox("Universe", ["Nifty 100", "Nifty 200", "Nifty 500", "Fallback (built-in list)"], index=1)
    if st.button("Run scan", type="primary"):
        syms = config.FALLBACK_UNIVERSE[:60] if DEMO else data.get_universe(uni.split(" (")[0])
        bar = st.progress(0.0, text=f"Scanning {len(syms)} stocks...")
        reg, plans = scanner.scan(syms, demo=DEMO, progress=lambda x: bar.progress(x))
        bar.empty()
        regime_box(reg)
        r = scanner.rank(plans)
        st.markdown(f"#### ✅ Buy candidates ({len(r['buy'])})")
        if r["buy"]:
            st.dataframe(scanner.to_table(r["buy"]), hide_index=True, width="stretch")
            st.caption("Take at most one. Open it in 'Analyze a stock' and read the news before placing the order.")
        else:
            st.info("Nothing passes every rule today. That's the system working, not failing.")
        st.markdown(f"#### ⚠️ Valid setups blocked by a rule ({len(r['blocked'])})")
        st.dataframe(scanner.to_table(r["blocked"]), hide_index=True, width="stretch")
        st.markdown("#### 👀 Watchlist — strong trend, within 3% of a breakout")
        st.dataframe(scanner.to_table(r["near"]), hide_index=True, width="stretch")
        st.caption(f"Scanned {len(plans)} stocks with data out of {len(syms)}.")

# ------------------------------------------------------------------ Backtest
with tabs[3]:
    st.write("Replays the same rules on past data with pessimistic fills and full Angel One charges. "
             "**If it doesn't make money here, it won't make money live.**")
    c1, c2, c3 = st.columns(3)
    bsyms = c1.text_input("Symbols (comma separated) or 'BASKET' for 40 liquid names", "BASKET")
    period = c2.selectbox("History", ["2y", "3y", "5y"], index=2)
    min_grade = c3.selectbox("Take grades", ["A", "B", "C"], index=1)
    use_reg = st.checkbox("Apply market-regime filter", True)
    if st.button("Run backtest", type="primary"):
        names = config.FALLBACK_UNIVERSE[:40] if bsyms.strip().upper() == "BASKET" else \
            [s.strip().upper() for s in bsyms.split(",") if s.strip()]
        with st.spinner(f"Backtesting {len(names)} stocks..."):
            nifty = load_market(DEMO)[0] if DEMO else data.get_history(config.INDEX_SYMBOL, period=period, max_age_hours=0)
            frames = {}
            for s in names:
                try:
                    frames[s] = load_hist(s, DEMO, period)
                except Exception:
                    pass
            res = backtest.run_many(frames, nifty, use_regime=use_reg, min_grade=min_grade)
        s = res["stats"]
        if not s.get("trades"):
            st.warning("No trades generated.")
        else:
            c = st.columns(6)
            c[0].metric("Trades", s["trades"])
            c[1].metric("Win rate", f"{s['win_rate_pct']}%")
            c[2].metric("Expectancy", f"{s['expectancy_R']}R / trade")
            c[3].metric("Profit factor", s["profit_factor"])
            c[4].metric("Net P&L", f"₹{s['total_net_rs']:,.0f}")
            c[5].metric("Charges paid", f"₹{s['total_charges_rs']:,.0f}")
            t = res["trades"].copy()
            t["equity"] = config.CAPITAL + t["net_pnl"].cumsum()
            st.line_chart(t.set_index("exit_date")["equity"])
            st.caption("Trades are pooled across stocks with fixed size per trade (no compounding). With Rs 5k you "
                       "can only hold one at a time, so treat this as 'quality of signals', not your exact P&L.")
            st.dataframe(t, hide_index=True, width="stretch")

# ------------------------------------------------------------------ Journal
with tabs[4]:
    st.write("Log every real trade. After 20-30 trades, compare your live numbers with the backtest.")
    with st.expander("➕ Log a new trade"):
        lp = st.session_state.get("last_plan")
        c = st.columns(4)
        js = c[0].text_input("Symbol", lp.symbol if lp else "")
        je = c[1].number_input("Fill price", value=float(lp.entry or 0) if lp else 0.0)
        jst = c[2].number_input("Stop", value=float(lp.stop or 0) if lp else 0.0)
        jt = c[3].number_input("Target 1", value=float(lp.target1 or 0) if lp else 0.0)
        c = st.columns(4)
        jq = c[0].number_input("Qty", value=int(lp.qty) if lp else 1, step=1)
        jd = c[1].date_input("Entry date", date.today())
        jsetup = c[2].selectbox("Setup", ["BREAKOUT", "PULLBACK", "OTHER"])
        jn = c[3].text_input("Notes")
        if st.button("Save trade") and js and je and jst:
            journal.add(js, jsetup, jd, je, jst, jt, jq, jn)
            st.success("Saved.")
    j = journal.load()
    opn = j[j["status"] == "OPEN"] if len(j) else j
    st.markdown(f"#### Open positions ({len(opn)})")
    for _, r in opn.iterrows():
        try:
            df = load_hist(r["symbol"], DEMO)
            d = ind.add_all(df)
            adv = strategy.exit_advice(d, float(r["entry"]), float(r["stop"]), float(r["target1"]), r["entry_date"])
            last = float(d["Close"].iloc[-1])
            pnl = charges.round_trip(float(r["entry"]), last, int(r["qty"]))["net_pnl"]
            st.markdown(f"**{r['symbol']}** · qty {r['qty']} @ ₹{r['entry']} · last ₹{last:.2f} · unrealised net ₹{pnl:,.0f} "
                        f"→ **{adv['action']}**, stop now ₹{adv['stop']}")
            for m in adv["msg"]:
                st.write("  • " + m)
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

# ------------------------------------------------------------------ Charges
with tabs[5]:
    st.write("What Angel One + the government take on one delivery round trip. Rates are in config.py.")
    c = st.columns(3)
    bp = c[0].number_input("Buy price", value=500.0)
    sp = c[1].number_input("Sell price", value=525.0)
    q = c[2].number_input("Quantity", value=10, step=1)
    rt = charges.round_trip(bp, sp, int(q))
    st.dataframe(pd.DataFrame([rt]).T.rename(columns={0: "Rs"}), width="stretch")
    st.metric("Move needed just to break even", f"+{charges.breakeven_move_pct(bp, int(q))}%")
    rows = []
    for size in (2000, 5000, 10000, 25000, 50000, 100000):
        qq = max(1, int(size / bp))
        rows.append({"Position Rs": size, "Round-trip charges Rs": charges.round_trip(bp, bp, qq)["total_charges"],
                     "Breakeven move %": charges.breakeven_move_pct(bp, qq)})
    st.markdown("**Why small accounts struggle:** the DP charge is a flat ~₹24 per sell.")
    st.dataframe(pd.DataFrame(rows), hide_index=True)
