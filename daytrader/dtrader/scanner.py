"""Find today's movers and rank them for a strategy (US small caps / NSE F&O)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .data.base import DataProvider
from .indicators import DayContext, add_indicators, build_context
from .markets import US, Market
from .strategies import MAX_CHASE_R, Position, Signal, Strategy


@dataclass
class Candidate:
    symbol: str
    ctx: DayContext
    bars: pd.DataFrame            # with indicators
    last: float
    rel_volume: float
    source: str                   # "movers" | "watchlist" | "history"
    results: dict = field(default_factory=dict)   # strategy -> analysis dict


def session_day(provider: DataProvider) -> date:
    """Today once its session has started (premarket for the US, the open for
    India) and the exchange is open, else the last session."""
    m = provider.market
    days = m.sessions_back(1)
    return days[-1] if days else m.now().date() - timedelta(days=1)


def _history_path(market: Market):
    return market.path("candidate_history.json")


def _load_history(market: Market = US) -> dict:
    p = _history_path(market)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def record_history(day: date, symbols: list[str], market: Market = US) -> None:
    h = _load_history(market)
    h[day.isoformat()] = sorted(set(h.get(day.isoformat(), [])) | set(symbols))
    keep = sorted(h)[-90:]
    _history_path(market).write_text(json.dumps({k: h[k] for k in keep}, indent=1))


def history_symbols(before: date | None = None, days: int = 30, market: Market = US) -> list[str]:
    h = _load_history(market)
    keys = sorted(k for k in h if before is None or k < before.isoformat())[-days:]
    return sorted({s for k in keys for s in h[k]})


def prior_stats(provider: DataProvider, symbol: str, day: date) -> tuple[float, float, pd.DataFrame]:
    """(previous close, 10-day avg volume) as of `day`, from daily bars."""
    d = provider.daily(symbol, days=40)
    prior = d[d.index.date < day]
    if not len(prior):
        return np.nan, 0.0, d
    return float(prior["close"].iloc[-1]), float(prior["volume"].tail(10).mean()), d


def momentum_score(ctx: DayContext, rel_volume: float, last: float) -> float:
    s = min(max(ctx.gap_pct, 0), 100) / 10
    s += math.log2(max(rel_volume, 0) + 1) * 2
    if ctx.float_m is not None:
        s += 3 if ctx.float_m < 10 else 1.5 if ctx.float_m < 20 else 0
    if 2 <= last <= 10:
        s += 1
    return round(s, 2)


NO_SETUP = "no setup"


def buy_status(strategy: Strategy, s: Signal, bars: pd.DataFrame, last: float) -> tuple[str, str | None]:
    """BUY NOW while the setup is still buyable: the trade it started hasn't
    hit its stop/target/exit, price is above the stop and no more than
    MAX_CHASE_R past the entry, and it's still before the entry cutoff.
    Otherwise 'triggered HH:MM' plus what happened to it."""
    pos = Position.from_signal(s).replay(bars, strategy.exits)
    label = f"triggered {s.time:%H:%M}"
    if not pos.is_open:
        return label, pos.exit_reason
    if last <= s.stop:
        return label, "below stop"
    if (last - s.entry) / s.risk > MAX_CHASE_R:
        return label, "ran past entry"
    if bars.index[-1].time() > strategy.last_entry:
        return label, "past entry cutoff"
    return "BUY NOW", None


def analyse(strategy: Strategy, cand: Candidate) -> dict:
    df, ctx = cand.bars, cand.ctx
    sigs: list[Signal] = strategy.entries(df, ctx)
    out = {"status": NO_SETUP, "signal": None, "watch": None, "outcome": None, "qualifies": strategy.qualifies(ctx)}
    if len(df):
        if sigs:
            s = sigs[-1]
            out["signal"] = s
            out["status"], out["outcome"] = buy_status(strategy, s, df, cand.last)
        if not sigs or not out["status"].startswith("BUY"):
            w = strategy.watch_level(df, ctx)
            if w and cand.last < w[0] and (w[0] - cand.last) / w[0] <= 0.05:
                out["status"] = "SETTING UP" if not sigs else out["status"]
                out["watch"] = w
    if out["status"] == NO_SETUP and out["qualifies"]:
        out["status"] = "qualifies"
    bonus = {"BUY NOW": 10, "SETTING UP": 6, "qualifies": 3}.get(out["status"], 2 if sigs else 0)
    out["score"] = momentum_score(ctx, cand.rel_volume, cand.last) + bonus
    return out


def gather_candidates(provider: DataProvider, cfg: dict, day: date | None = None,
                      log=print) -> list[Candidate]:
    u = cfg.get("universe", {})
    m: Market = provider.market
    day = day or session_day(provider)
    is_today = day == m.now().date()

    sources: dict[str, str] = {}
    if is_today:
        for s in provider.movers(limit=80):
            sources.setdefault(s, "movers")
    for s in u.get("watchlist") or []:
        sources.setdefault(s.upper(), "watchlist")
    for s in history_symbols(before=day, days=3, market=m):
        sources.setdefault(s, "history")
    if not is_today and not any(v == "movers" for v in sources.values()):
        for s in history_symbols(days=1, market=m) or provider.movers(limit=80):
            sources.setdefault(s, "movers")
    log(f"Universe: {len(sources)} symbols for session {day}")

    # Fast filter on quotes first.
    q = provider.quotes(list(sources)) if sources else pd.DataFrame()
    rows = []
    for sym, r in q.iterrows():
        if not r["last"] or not r["prev_close"]:
            continue
        chg = (r["last"] / r["prev_close"] - 1) * 100
        rv = r["volume"] / r["avg_volume"] if r["avg_volume"] else 0
        if not (u.get("min_price", 1) <= r["last"] <= u.get("max_price", 20)):
            continue
        # Before the open the day's volume is ~0, so relative volume can't filter yet.
        premarket = m.now().time() < m.open
        weak_vol = rv < u.get("min_rel_volume", 2) and not premarket
        if sources[sym] == "movers" and is_today and (chg < u.get("min_gap_pct", 5) or weak_vol):
            continue
        rows.append((sym, chg, rv, r["last"]))
    rows.sort(key=lambda x: x[1] * math.log2(x[2] + 2), reverse=True)
    rows = rows[: int(u.get("max_candidates", 25))]

    floats = provider.float_shares([r[0] for r in rows])
    max_f = u.get("max_float_millions")
    out: list[Candidate] = []
    for sym, chg, rv, last in rows:
        fl = floats.get(sym)
        if max_f and fl and fl / 1e6 > max_f:
            continue
        try:
            prev_close, avg_vol, _ = prior_stats(provider, sym, day)
            bars = provider.intraday(sym, day)
        except Exception as e:  # one bad symbol shouldn't kill the scan
            log(f"  skip {sym}: {e}")
            continue
        if not len(bars) or np.isnan(prev_close):
            continue
        ctx = build_context(sym, day, bars, prev_close, avg_vol, fl, market=m,
                            premarket_volume_known=provider.premarket_volume_known)
        last = float(bars["close"].iloc[-1])
        rel = float(bars["volume"].sum() / avg_vol) if avg_vol else rv
        out.append(Candidate(sym, ctx, add_indicators(bars, m), last, rel, sources[sym]))
    record_history(day, [c.symbol for c in out if c.source == "movers"], m)
    return out


def scan(provider: DataProvider, cfg: dict, strategies: list[Strategy], day: date | None = None,
         log=print) -> tuple[pd.DataFrame, list[Candidate]]:
    cands = gather_candidates(provider, cfg, day, log=log)
    rows = []
    for c in cands:
        for st in strategies:
            a = analyse(st, c)
            c.results[st.name] = a
            sig: Signal | None = a["signal"]
            rows.append({
                "symbol": c.symbol,
                "strategy": st.name,
                "status": a["status"],
                "score": a["score"],
                "last": round(c.last, 2),
                "gap_%": round(c.ctx.gap_pct, 1),
                "day_%": round((c.last / c.ctx.prev_close - 1) * 100, 1),
                "rel_vol": round(c.rel_volume, 1),
                "float_M": round(c.ctx.float_m, 1) if c.ctx.float_m else None,
                "entry": sig.entry if sig else (round(a["watch"][0], 2) if a["watch"] else None),
                "stop": sig.stop if sig else None,
                "target": sig.target if sig else None,
                "shares": st.position_size(sig) if sig else None,
                "why": (f"[{a['outcome']}] " if a.get("outcome") else "") + sig.reason if sig
                       else (f"watch {a['watch'][1]}" if a["watch"] else ""),
                "source": c.source,
            })
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(["score"], ascending=False).reset_index(drop=True)
    return df, cands
