"""News and earnings check for a stock.

US headlines: Alpaca's news API (Benzinga, free with your Alpaca keys), else
Yahoo Finance's RSS feed. India: Google News RSS searched by company name.
Earnings dates: Yahoo (both markets). All cached so the dashboard and monitor
don't hammer the sources.

Why it matters for day trading: a gap with fresh news (the catalyst) tends to
follow through better than one without, and a stock reporting earnings today
or tomorrow can gap against you through the stop.
"""
from __future__ import annotations

import json
import re
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import httpx

from .config import alpaca_keys
from .markets import Market

UA = {"User-Agent": "Mozilla/5.0 (day-trade-scanner)"}
NEWS_TTL = 600
_NEWS: dict[tuple, tuple[float, list]] = {}


@dataclass
class Headline:
    time: datetime
    title: str
    source: str
    url: str


@dataclass
class NewsInfo:
    headlines: list = field(default_factory=list)   # newest first, last ~2 days
    fresh: list = field(default_factory=list)       # since the previous session's close
    earnings: date | None = None
    earnings_when: str | None = None                # "today" / "next session" / "last session"

    def news_text(self) -> str:
        if not self.fresh:
            return "no news today"
        return f"{len(self.fresh)} today: {self.fresh[0].title[:70]}"

    def earnings_text(self) -> str:
        return f"earnings {self.earnings_when}" if self.earnings_when else "-"


# ---- sources -----------------------------------------------------------------
def _alpaca_news(symbol: str) -> list[Headline] | None:
    keys = alpaca_keys()
    if not keys:
        return None
    start = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    r = httpx.get("https://data.alpaca.markets/v1beta1/news", timeout=15,
                  params={"symbols": symbol, "limit": 20, "start": start, "sort": "desc"},
                  headers={"APCA-API-KEY-ID": keys[0], "APCA-API-SECRET-KEY": keys[1]})
    if r.status_code != 200:
        return None
    return [Headline(datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")), n["headline"],
                     n.get("source") or "Benzinga", n.get("url") or "") for n in r.json().get("news", [])]


def _rss(url: str, params: dict | None = None, split_source: bool = False) -> list[Headline]:
    r = httpx.get(url, params=params, headers=UA, timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return []
    out = []
    for item in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        when = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
        link = re.search(r"<link>(.*?)</link>", item, re.S)
        src = re.search(r"<source[^>]*>(.*?)</source>", item, re.S)
        if not (title and when):
            continue
        t = unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", title.group(1))).strip()
        source = unescape(src.group(1)).strip() if src else ""
        if split_source and " - " in t:  # Google News: "Headline - Publisher"
            t, source = t.rsplit(" - ", 1)
        try:
            ts = parsedate_to_datetime(when.group(1).strip())
        except (TypeError, ValueError):
            continue
        out.append(Headline(ts, t, source or "Yahoo Finance", link.group(1).strip() if link else ""))
    return out


def _company_name(symbol: str, m: Market) -> str:
    """NSE company name for news search, cached on disk (e.g. 'JSW Energy')."""
    path = m.path("company_names.json")
    names = json.loads(path.read_text()) if path.exists() else {}
    if symbol not in names:
        name = symbol
        try:
            import yfinance as yf

            info = yf.Ticker(f"{symbol}.NS").get_info()
            name = info.get("longName") or info.get("shortName") or symbol
        except Exception:
            pass
        names[symbol] = re.sub(r"\s+(Limited|Ltd\.?|Ltd|Corporation|Company)$", "", name.strip(), flags=re.I)
        path.write_text(json.dumps(names, indent=1))
    return names[symbol]


def headlines(symbol: str, m: Market) -> list[Headline]:
    key = (m.key, symbol)
    hit = _NEWS.get(key)
    if hit and hit[0] > _time.time() - NEWS_TTL:
        return hit[1]
    items: list[Headline] = []
    try:
        if m.key == "us":
            items = _alpaca_news(symbol)
            if items is None:
                items = _rss("https://feeds.finance.yahoo.com/rss/2.0/headline",
                             {"s": symbol, "region": "US", "lang": "en-US"})
        else:
            name = _company_name(symbol, m)
            items = _rss("https://news.google.com/rss/search",
                         {"q": f'"{name}" share OR stock OR shares', "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
                         split_source=True)
    except httpx.HTTPError:
        items = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    items = sorted((h for h in items if h.time >= cutoff), key=lambda h: h.time, reverse=True)[:15]
    _NEWS[key] = (_time.time(), items)
    return items


def earnings_date(symbol: str, m: Market) -> date | None:
    """Next/most recent earnings date from Yahoo, cached per symbol per day."""
    path = m.path("earnings_cache.json")
    cache = json.loads(path.read_text()) if path.exists() else {}
    today = m.now().date().isoformat()
    hit = cache.get(symbol)
    if hit and hit.get("checked") == today:
        return date.fromisoformat(hit["date"]) if hit.get("date") else None
    d = None
    try:
        import yfinance as yf

        cal = yf.Ticker(symbol + (".NS" if m.key == "india" else "")).calendar or {}
        dates = cal.get("Earnings Date") or []
        d = dates[0] if dates else None
    except Exception:
        pass
    cache[symbol] = {"checked": today, "date": d.isoformat() if d else None}
    path.write_text(json.dumps(cache))
    return d


def check(symbol: str, m: Market) -> NewsInfo:
    """Headlines (fresh = since the previous session's close) + earnings timing."""
    info = NewsInfo(headlines=headlines(symbol, m))
    prev = [d for d in m.sessions_back(3) if d < m.now().date()]
    since = (datetime.combine(prev[-1], m.close, m.tz) if prev else m.now() - timedelta(hours=18))
    info.fresh = [h for h in info.headlines if h.time >= since]
    info.earnings = earnings_date(symbol, m)
    if info.earnings:
        today = m.now().date()
        nxt = next((today + timedelta(days=k) for k in range(1, 8) if m.session_hours(today + timedelta(days=k))), None)
        if info.earnings == today:
            info.earnings_when = "today"
        elif info.earnings == nxt:
            info.earnings_when = "next session"
        elif prev and info.earnings == prev[-1]:
            info.earnings_when = "last session"
    return info
