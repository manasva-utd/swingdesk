"""
News check. Free sources only:
  * Google News RSS search for the company (last ~7 days)
  * A simple red-flag / green-flag keyword scan (fast, dumb, transparent)
  * OPTIONAL: an LLM summary through any OpenAI-compatible free endpoint
    (Groq, Google Gemini's OpenAI-compatible endpoint, OpenRouter free models...).
    Set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL in .env to switch it on.

News is a VETO, not a signal: it can stop a trade (fraud, probe, results tomorrow),
it should not start one.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

RED_FLAGS = ["fraud", "probe", "raid", "sebi order", "sebi bans", "default", "downgrade", "resigns",
             "resignation", "pledge", "insolvency", "nclt", "penalty", "fire at", "shutdown",
             "block deal", "stake sale", "ofs", "misses estimates", "profit falls", "loss widens",
             "lawsuit", "investigation", "auditor", "suspension", "ban", "cut target", "sell rating"]
GREEN_FLAGS = ["order win", "bags order", "wins order", "contract", "upgrade", "beats estimates",
               "record profit", "profit jumps", "profit rises", "buyback", "bonus", "acquisition",
               "capacity expansion", "approval", "raises target", "buy rating", "outperform", "launch"]


def fetch_headlines(query: str, days: int = 7, limit: int = 15) -> list[dict]:
    import feedparser

    q = urllib.parse.quote(f"{query} when:{days}d")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    try:  # requests uses certifi, avoiding the macOS python.org SSL-certificate problem
        import requests

        raw = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}).content
        feed = feedparser.parse(raw)
    except Exception:
        feed = feedparser.parse(url)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for e in feed.entries[: limit * 2]:
        try:
            pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pub = None
        if pub and pub < cutoff:
            continue
        title = e.get("title", "")
        low = title.lower()
        out.append({
            "title": title, "link": e.get("link", ""),
            "source": (e.get("source") or {}).get("title", ""),
            "published": pub.strftime("%d %b %H:%M") if pub else "",
            "red": [w for w in RED_FLAGS if re.search(rf"\b{re.escape(w)}\b", low)],
            "green": [w for w in GREEN_FLAGS if re.search(rf"\b{re.escape(w)}\b", low)],
        })
        if len(out) >= limit:
            break
    return out


def news_verdict(headlines: list[dict]) -> str:
    reds = sum(1 for h in headlines if h["red"])
    greens = sum(1 for h in headlines if h["green"])
    if reds >= 2 or (reds and not greens):
        return "CAUTION - negative headlines found, read them before trading"
    if greens and not reds:
        return "SUPPORTIVE - positive headlines (still not a reason to buy on its own)"
    if not headlines:
        return "QUIET - no recent news"
    return "NEUTRAL"


def llm_summary(symbol: str, company: str, headlines: list[dict], plan_text: str) -> str | None:
    base, key, model = os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY"), os.getenv("LLM_MODEL")
    if not (base and key and model) or not headlines:
        return None
    import requests

    lines = "\n".join(f"- {h['published']} | {h['source']} | {h['title']}" for h in headlines)
    prompt = (
        f"You are a skeptical risk checker for a swing trade in {company} ({symbol}, NSE).\n"
        f"Technical plan:\n{plan_text}\n\nHeadlines from the last 7 days:\n{lines}\n\n"
        "In under 120 words, plain English: (1) any event risk in the next 2 weeks (results, "
        "regulatory action, block deals, promoter selling), (2) does the news contradict the "
        "technical setup, (3) verdict: PROCEED / REDUCE SIZE / SKIP. Do not invent facts that "
        "are not in the headlines; say 'unknown' instead."
    )
    try:
        r = requests.post(f"{base.rstrip('/')}/chat/completions", timeout=60,
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": model, "temperature": 0.2,
                                "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"(LLM call failed: {e})"
