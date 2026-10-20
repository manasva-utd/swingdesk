"""
Nightly report from the command line (no dashboard needed).

  python report.py                       # scan Nifty 200, print + save report
  python report.py --symbol TATASTEEL    # deep-dive one stock (Option 1 workflow)
  python report.py --telegram            # also push the report to your Telegram
  python report.py --demo                # offline test with fake data

Run it any time after ~4:30 pm IST (end-of-day candles are final by then).
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime

import config
import data
import indicators as ind
import news
import scanner
import strategy


def fmt_plan(p) -> str:
    L = [f"*{p.symbol}* - {p.action} | {p.setup} | grade {p.grade} | score {p.score}/100 | close Rs {p.close}"]
    if p.entry:
        L.append(f"  Entry: {p.entry_type} -> Rs {p.entry}")
        L.append(f"  Stop: Rs {p.stop} ({p.risk_pct}% risk)   T1: Rs {p.target1}   T2: Rs {p.target2}   R:R {p.reward_risk}")
        if p.qty:
            L.append(f"  Qty {p.qty} = Rs {p.position_value:,.0f} | max loss Rs {p.max_loss_rs:,.0f} | "
                     f"net at T1 Rs {p.profit_t1_net_rs:,.0f} | charges Rs {p.charges_round_trip:.0f} | breakeven +{p.breakeven_pct}%")
    else:
        w = p.watch_levels
        L.append(f"  No setup. Breakout trigger > Rs {w['breakout_above']} | pullback zone {w['pullback_zone']} | trend dies < Rs {w['trend_dies_below']}")
    fails = [c.name for c in p.checks if not c.passed]
    if fails:
        L.append("  Failed: " + "; ".join(fails))
    for n in p.notes:
        L.append("  Note: " + n)
    if p.resistance:
        L.append(f"  Resistance above: {p.resistance} | Support below: {p.support}")
    return "\n".join(L)


def deep_dive(symbol: str, demo: bool = False) -> str:
    nifty, vix = scanner.load_market(demo)
    regime = strategy.market_regime(nifty, vix)
    df = data.synthetic_history(symbol) if demo else data.get_history(symbol)
    d = ind.add_all(df, nifty["Close"])
    ed = None if demo else data.get_upcoming_results_date(symbol)
    p = strategy.evaluate(d, symbol, regime=regime, earnings_date=ed)
    out = [f"MARKET: {regime.label} (size x{regime.size_multiplier})"] + ["  " + r for r in regime.reasons]
    out += ["", fmt_plan(p), "", "CHECKLIST:"]
    out += [f"  [{'PASS' if c.passed else 'FAIL'}] {c.group:9s} {c.name:40s} {c.value}" for c in p.checks]
    if not demo:
        heads = news.fetch_headlines(f"{symbol} share")
        out += ["", f"NEWS (7d): {news.news_verdict(heads)}"]
        out += [f"  {'!! ' if h['red'] else '+ ' if h['green'] else '  '}{h['published']} {h['title']}" for h in heads[:8]]
        s = news.llm_summary(symbol, symbol, heads, fmt_plan(p))
        if s:
            out += ["", "AI RISK CHECK:", s]
    return "\n".join(out)


def nightly(universe: str, demo: bool = False) -> str:
    syms = config.FALLBACK_UNIVERSE[:60] if demo else data.get_universe(universe)
    regime, plans = scanner.scan(syms, demo=demo)
    r = scanner.rank(plans)
    now = datetime.now().strftime("%a %d %b %Y %H:%M")
    out = [f"SWINGDESK REPORT - {now}", f"Universe: {universe} ({len(plans)} stocks scanned)",
           f"MARKET: {regime.label} (size x{regime.size_multiplier})"] + ["  " + x for x in regime.reasons]
    out += ["", f"=== BUY CANDIDATES ({len(r['buy'])}) ==="]
    out += [fmt_plan(p) + "\n" for p in r["buy"][:5]] or ["  None today. No trade is a valid trade."]
    out += ["", f"=== VALID SETUPS BLOCKED BY A RULE ({len(r['blocked'])}) ==="]
    out += [f"  {p.symbol}: {p.setup} grade {p.grade}, {('; '.join(p.notes) or 'failed: ' + ', '.join(c.name for c in p.checks if not c.passed))}" for p in r["blocked"][:8]]
    out += ["", "=== WATCHLIST: within 3% of a breakout ==="]
    out += [f"  {p.symbol}: close {p.close}, trigger > {p.watch_levels['breakout_above']}, score {p.score}" for p in r["near"][:10]]
    out += ["", "Rules: place the buy-stop for the next session, cancel if not filled in "
            f"{config.ENTRY_VALID_DAYS} sessions, put the stop-loss in the moment you are filled. Max "
            f"{config.MAX_OPEN_POSITIONS} open position(s)."]
    return "\n".join(out)


def send_telegram(text: str):
    import requests
    from dotenv import load_dotenv

    load_dotenv()
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        print("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env).")
        return
    for k in range(0, len(text), 3800):
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": text[k:k + 3800]}, timeout=20)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="Nifty 200", choices=["Nifty 100", "Nifty 200", "Nifty 500", "Fallback"])
    ap.add_argument("--symbol")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    a = ap.parse_args()
    text = deep_dive(a.symbol.upper(), a.demo) if a.symbol else nightly(a.universe, a.demo)
    print(text)
    os.makedirs("reports", exist_ok=True)
    fn = f"reports/{datetime.now():%Y-%m-%d}_{a.symbol or 'scan'}.txt"
    with open(fn, "w") as f:
        f.write(text)
    if a.telegram:
        send_telegram(text)
