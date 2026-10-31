"""Setup odds: how good is THIS trade on THIS stock, right now?

Two independent estimates for a triggered setup:

1. Reachability (simulation) - replay the rest of today's session many
   times (default 10,000). Each future 1-minute bar is drawn from this
   stock's own bars at the same time of day over its last N sessions, scaled
   to how volatile the stock is today (gap days move far more than normal
   days). The draws are drift-neutral: 20 sessions can't estimate a trend
   (the average move per minute is mostly noise, and summed over hours it
   becomes a fake one), so the simulation only answers "given how this
   stock moves today, can price realistically reach the target or the stop
   before the time exit?" It reports target-first / stop-first / neither.
2. Track record (history) - re-run the same strategy rule on this stock's
   last N sessions and count what happened each time it fired. This is where
   any edge shows up, so the verdict leans on it.

Exits in the simulation are stop / target / time exit only.
"""
from __future__ import annotations

import time as _time
import zlib
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from .indicators import add_indicators, build_context
from .strategies import Signal, Strategy

_CACHE: dict[tuple, tuple[float, "Odds"]] = {}
CACHE_TTL = 300


@dataclass
class Odds:
    p_target: float
    p_stop: float
    p_open: float
    exp_r: float
    paths: int
    vol_scale: float
    sessions: int
    entry_now: float
    hist_fired: int = 0
    hist_target: int = 0
    hist_stop: int = 0
    hist_other: int = 0
    hist_avg_r: float | None = None
    notes: list = field(default_factory=list)

    MIN_HISTORY = 5

    @property
    def verdict(self) -> str:
        if self.p_open >= 0.5:
            return "Target out of reach"     # likely still open at the time exit
        if self.hist_fired < self.MIN_HISTORY or self.hist_avg_r is None:
            return "No track record"
        if self.hist_avg_r >= 0.2:
            return "Favourable"
        if self.hist_avg_r <= -0.1:
            return "Unfavourable"
        return "Coin flip"

    def odds_text(self) -> str:
        return f"T {self.p_target:.0%} / S {self.p_stop:.0%}"

    def history_text(self) -> str:
        if not self.hist_fired:
            return f"never fired in {self.sessions}"
        avg = f", {self.hist_avg_r:+.2f}R avg" if self.hist_avg_r is not None else ""
        return f"{self.hist_target}/{self.hist_fired} hit target{avg}"

    def summary(self) -> str:
        return (f"{self.verdict}. Reach: target first {self.p_target:.0%}, stop first {self.p_stop:.0%}, "
                f"neither by exit {self.p_open:.0%}. Track record: {self.history_text()} "
                f"(last {self.sessions} sessions).")


def _session_bars(bars: pd.DataFrame, market) -> pd.DataFrame:
    t = bars.index.time
    return bars[(t >= market.open) & (t < market.close)]


def _bar_moves(reg: pd.DataFrame, market) -> pd.DataFrame:
    """Per-bar log moves relative to the previous bar's close, keyed by minute of session."""
    prev = reg["close"].shift(1)
    prev.iloc[0] = reg["open"].iloc[0]
    open_ts = pd.Timestamp(datetime.combine(reg.index[0].date(), market.open, market.tz))
    out = pd.DataFrame({
        "slot": ((reg.index - open_ts).total_seconds() // 60).astype(int),
        "c": np.log(reg["close"] / prev), "h": np.log(reg["high"] / prev), "l": np.log(reg["low"] / prev),
    })
    return out.replace([np.inf, -np.inf], np.nan).dropna()


def setup_odds(provider, cfg: dict, strategy: Strategy, sig: Signal, bars_today: pd.DataFrame, last: float,
               n_sessions: int = 20, paths: int = 10_000) -> Odds | None:
    m = cfg["market"]
    key = (m.key, sig.symbol, sig.strategy, str(sig.time), round(last, 2), n_sessions, paths)
    hit = _CACHE.get(key)
    if hit and hit[0] > _time.time() - CACHE_TTL:
        return hit[1]

    day = sig.time.date()
    sessions = [d for d in m.sessions_back(n_sessions + 2) if d < day][-n_sessions:]
    moves, past = [], {}
    for d in sessions:
        try:
            b = provider.intraday(sig.symbol, d)
        except Exception:
            continue
        reg = _session_bars(b, m)
        if len(reg) >= 30:
            past[d] = b
            moves.append(_bar_moves(reg, m))
    if len(moves) < 5:
        return None
    pool = pd.concat(moves)
    # drift-neutral: remove each minute's average move (noise with ~20 samples), keep its volatility
    slot_mean = pool.groupby("slot")["c"].transform("mean")
    for col in ("c", "h", "l"):
        pool[col] = pool[col] - slot_mean

    # --- volatility scaling: today's realised 1-min vol vs the same slots historically
    reg_today = _session_bars(bars_today, m)
    today_moves = _bar_moves(reg_today, m) if len(reg_today) >= 2 else pd.DataFrame(columns=["slot", "c"])
    scale = 1.0
    if len(today_moves) >= 10:
        hist_same = pool[pool["slot"].isin(today_moves["slot"])]["c"]
        if hist_same.std() > 0:
            scale = float(np.clip(today_moves["c"].std() / hist_same.std(), 0.5, 4.0))

    # --- slots to simulate: from the next minute to the time exit
    exit_t = datetime.strptime(strategy.exits.get("time_exit", "15:50"), "%H:%M").time()
    open_ts = pd.Timestamp(datetime.combine(day, m.open, m.tz))
    now_slot = int((reg_today.index[-1] - open_ts).total_seconds() // 60) + 1 if len(reg_today) else 0
    end_slot = int((pd.Timestamp(datetime.combine(day, exit_t, m.tz)) - open_ts).total_seconds() // 60)
    if end_slot <= now_slot:
        return None
    slots = np.arange(now_slot, end_slot)

    rng = np.random.default_rng(zlib.crc32(f"{sig.symbol}|{sig.time}".encode()))  # repeatable
    by_slot = {k: g[["c", "h", "l"]].to_numpy() for k, g in pool.groupby("slot")}
    allm = pool[["c", "h", "l"]].to_numpy()
    draws = np.empty((paths, len(slots), 3))
    for j, k in enumerate(slots):
        cand = [by_slot[s] for s in (k, k - 1, k + 1, k - 2, k + 2) if s in by_slot]
        src = np.concatenate(cand) if cand else allm
        draws[:, j, :] = src[rng.integers(0, len(src), paths)]
    draws *= scale

    closes = last * np.exp(np.cumsum(draws[:, :, 0], axis=1))
    prev = np.concatenate([np.full((paths, 1), last), closes[:, :-1]], axis=1)
    highs, lows = prev * np.exp(draws[:, :, 1]), prev * np.exp(draws[:, :, 2])
    hit_s, hit_t = lows <= sig.stop, highs >= sig.target
    first_s = np.where(hit_s.any(1), hit_s.argmax(1), len(slots))
    first_t = np.where(hit_t.any(1), hit_t.argmax(1), len(slots))
    stop_first = (first_s < len(slots)) & (first_s <= first_t)  # same bar: assume the stop (conservative)
    tgt_first = (first_t < len(slots)) & (first_t < first_s)
    open_end = ~(stop_first | tgt_first)
    risk = max(last - sig.stop, 1e-9)
    r = np.where(tgt_first, (sig.target - last) / risk,
                 np.where(stop_first, (sig.stop - last) / risk, (closes[:, -1] - last) / risk))
    odds = Odds(float(tgt_first.mean()), float(stop_first.mean()), float(open_end.mean()), float(r.mean()),
                paths, scale, len(moves), last)
    if last <= sig.stop:
        odds.notes.append("price is at or below the stop")

    # --- history: the same rule on this stock's previous sessions
    try:
        from .backtest import simulate_day

        daily = provider.daily(sig.symbol, n_sessions + 30)
        rs = []
        for d, b in past.items():
            prior = daily[daily.index.date < d]
            if not len(prior):
                continue
            ctx = build_context(sig.symbol, d, b, float(prior["close"].iloc[-1]),
                                float(prior["volume"].tail(10).mean()), market=m,
                                premarket_volume_known=provider.premarket_volume_known,
                                prev_high=float(prior["high"].iloc[-1]), prev_low=float(prior["low"].iloc[-1]))
            for p in simulate_day(strategy, add_indicators(b, m), ctx, slippage=0.0):
                rs.append(p.r_multiple)
                if p.exit_reason == "target":
                    odds.hist_target += 1
                elif p.exit_reason == "stop":
                    odds.hist_stop += 1
                else:
                    odds.hist_other += 1
        odds.hist_fired = len(rs)
        odds.hist_avg_r = float(np.mean(rs)) if rs else None
    except Exception as e:  # history is a bonus; never block the odds
        odds.notes.append(f"history unavailable: {str(e)[:60]}")

    _CACHE[key] = (_time.time(), odds)
    return odds
