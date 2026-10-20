"""
Scheduled paper-trading jobs (GitHub Actions runs these; you can run them by hand too).

  python run_paper.py morning   # ~10:45 IST: log today's 10:30 intraday picks
  python run_paper.py evening   # ~17:00 IST: settle intraday + swing trades, log new swing picks

Add --telegram to push a summary to your phone (needs TELEGRAM_* in .env / repo secrets).
"""
import argparse

import paper
import report


def morning_text(ideas):
    if not ideas:
        return "SwingDesk 10:30 - no intraday setups this morning."
    L = ["SwingDesk 10:30 intraday picks (paper):"]
    for i in ideas:
        L.append(f"{i.side} {i.symbol} @ {i.entry} | stop {i.stop} | target {i.target} | qty {i.qty}\n  {i.why}")
    L.append("Close everything by 3:15 PM.")
    return "\n".join(L)


def evening_text(df):
    perf = paper.performance(df)
    L = ["SwingDesk evening paper update"]
    today_closed = df[df["status"] == "CLOSED"].tail(6)
    for _, r in today_closed.iterrows():
        L.append(f"{r['strategy']} {r['side']} {r['symbol']}: {r['reason']} net Rs {r['net']:.0f} ({r['R']:+.2f}R)")
    pend = df[df["status"].isin(["PENDING", "OPEN"])]
    for _, r in pend.iterrows():
        L.append(f"{r['status']} {r['symbol']} entry {r['entry']} stop {r['stop']}")
    if not perf.empty:
        for _, r in perf[perf["regime"] == "ALL"].iterrows():
            L.append(f"{r['strategy']}: {int(r['trades'])} trades, win {r['win_pct']}%, avg {r['avg_R']:+.2f}R, net Rs {r['net_rs']:.0f}")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["morning", "evening"])
    ap.add_argument("--telegram", action="store_true")
    a = ap.parse_args()
    if a.job == "morning":
        text = morning_text(paper.morning())
    else:
        paper.morning()          # catch-up: logs today's 10:30 picks if the morning run was missed
        text = evening_text(paper.evening())
    print(text)
    if a.telegram:
        report.send_telegram(text)
