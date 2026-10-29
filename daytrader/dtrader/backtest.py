"""Replay recent sessions bar-by-bar and score every strategy the same way.

Universe per day = stocks that gapped >= universe.min_gap_pct at the open or
ran 20%+ the day before (both knowable pre-market). With Alpaca that is found
across every listed stock; other sources fall back to symbols the tool has
seen (scan history, watchlist, today's movers), which is a biased sample.
Treat results as relative (which strategy is working *lately*), not as a
promise of future returns.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .data.base import DataProvider, trading_days_back
from .indicators import add_indicators, build_context
from .markets import US, Market, get_market
from .scanner import history_symbols
from .strategies import Position, Strategy


def trades_csv(market: Market = US):
    return market.path("backtest_trades.csv")


def simulate_day(strategy: Strategy, df: pd.DataFrame, ctx, slippage: float = 0.001,
                 max_trades: int = 2) -> list[Position]:
    trades: list[Position] = []
    sigs = strategy.entries(df, ctx)
    if not sigs:
        return trades
    pos_of = {ts: i for i, ts in enumerate(df.index)}
    reg_last = np.flatnonzero(df["regular"].to_numpy())[-1]
    after = None
    for s in sigs:
        if len(trades) >= max_trades:
            break
        if after is not None and s.time <= after:
            continue
        i = pos_of[s.time]
        p = Position.from_signal(s, strategy.position_size(s))
        p.entry = s.entry * (1 + slippage)
        # Breakout entries fill intrabar: if that same bar closed below the
        # stop we assume it was stopped out there.
        if df["close"].iat[i] <= p.stop:
            p._close(s.time, p.stop, "stop")
        j = i + 1
        while p.is_open and j <= reg_last:
            p.step(df.index[j], df.iloc[j], strategy.exits)
            j += 1
        if p.is_open:
            p._close(df.index[reg_last], df["close"].iat[reg_last], "close")
        p.exit_price *= (1 - slippage)
        trades.append(p)
        after = p.exit_time
    return trades


def _mover_days(d: pd.DataFrame, sessions: list[date], u: dict, min_avg_vol: float) -> list[tuple[date, float]]:
    """Days this stock was in play, using only what is known at the open:
    it gapped >= min_gap_pct, or ran 20%+ the day before (day-2 play).
    Returns (day, rank score) pairs."""
    if len(d) < 3:
        return []
    d = d.copy()
    d["prev_close"] = d["close"].shift(1)
    d["avg_vol"] = d["volume"].shift(1).rolling(10, min_periods=3).mean()
    d["gap_pct"] = (d["open"] / d["prev_close"] - 1) * 100
    d["prev_day_pct"] = (d["close"] / d["prev_close"] - 1).shift(1) * 100
    out = []
    for ts, r in d.iterrows():
        dd = ts.date()
        if dd not in sessions or np.isnan(r["prev_close"]):
            continue
        if not (u.get("min_price", 1) <= r["prev_close"] <= u.get("max_price", 20)):
            continue
        if (r["avg_vol"] or 0) < min_avg_vol:
            continue
        gap, prev = abs(r["gap_pct"]), (r["prev_day_pct"] or 0)
        if gap >= u.get("min_gap_pct", 5) or prev >= 20:
            out.append((dd, max(gap, prev)))
    return out


def run_backtest(provider: DataProvider, cfg: dict, strategies: list[Strategy],
                 days: int = 10, symbols: list[str] | None = None, log=print) -> pd.DataFrame:
    """Universe modes (config backtest.universe):
      market - every listed stock that gapped that day (needs a source that can
               list all symbols, e.g. Alpaca). No bias toward today's movers.
      seen   - only symbols the tool has seen: scan history, watchlist, movers.
    """
    u = cfg.get("universe", {})
    bt = cfg.get("backtest") or {}
    slip = float(bt.get("slippage_pct", 0.1)) / 100
    max_per_day = int(bt.get("max_symbols_per_day", 60))
    sessions = trading_days_back(days + 1, provider)[-days:]

    universe = None
    if symbols is None and bt.get("universe", "market") == "market":
        universe = provider.all_symbols()
    if universe:
        min_avg_vol = float(bt.get("min_avg_volume", 100_000))
        log(f"Scanning daily bars of {len(universe)} listed stocks for gappers...")
        dailies = provider.daily_many(universe, days=days + 25)
    else:
        min_avg_vol = 0.0
        if symbols is None:
            symbols = sorted(set(history_symbols(days=days + 5, market=provider.market))
                             | {s.upper() for s in u.get("watchlist") or []})
            try:
                symbols = sorted(set(symbols) | set(provider.movers(limit=60)))
            except Exception:
                pass
        dailies = provider.daily_many(symbols, days=days + 25)

    # day -> [(symbol, rank)], keep the strongest movers per day
    by_day: dict[date, list[tuple[str, float]]] = {}
    for sym, d in dailies.items():
        for dd, score in _mover_days(d, sessions, u, min_avg_vol):
            by_day.setdefault(dd, []).append((sym, score))
    plan = {dd: [s for s, _ in sorted(v, key=lambda x: -x[1])[:max_per_day]] for dd, v in by_day.items()}
    total = sum(len(v) for v in plan.values())
    log(f"Backtesting {len(strategies)} strategies on {total} gapper-days over {len(sessions)} sessions")

    rows = []
    for n, dd in enumerate(sorted(plan), 1):
        try:
            bars_by_sym = provider.intraday_many(plan[dd], dd)
        except Exception as e:
            log(f"  {dd}: {e}")
            continue
        for sym in plan[dd]:
            bars = bars_by_sym.get(sym)
            if bars is None or not len(bars):
                continue
            d = dailies[sym]
            prior = d[d.index.date < dd]
            if not len(prior):
                continue
            ctx = build_context(sym, dd, bars, float(prior["close"].iloc[-1]),
                                float(prior["volume"].tail(10).mean()), market=provider.market)
            df = add_indicators(bars, provider.market)
            if not df["regular"].any():
                continue
            for st in strategies:
                for p in simulate_day(st, df, ctx, slip):
                    rows.append({
                        "day": dd, "symbol": sym, "strategy": st.name,
                        "entry_time": p.entry_time, "entry": round(p.entry, 4),
                        "stop": round(p.initial_stop, 4), "target": round(p.target, 4),
                        "exit_time": p.exit_time, "exit": round(p.exit_price, 4),
                        "reason": p.exit_reason, "r": round(p.r_multiple, 3),
                        "shares": p.shares, "pnl": round((p.exit_price - p.entry) * p.shares, 2),
                        "gap_pct": round(ctx.gap_pct, 1),
                    })
        log(f"  ...{n}/{len(plan)} sessions ({dd}: {len(plan[dd])} gappers)")
    trades = pd.DataFrame(rows)
    if len(trades):
        trades = trades.sort_values(["day", "entry_time"]).reset_index(drop=True)
        trades.to_csv(trades_csv(provider.market), index=False)
    return trades


def summarize(trades: pd.DataFrame, strategies: list[str] | None = None) -> pd.DataFrame:
    names = strategies or (sorted(trades["strategy"].unique()) if len(trades) else [])
    rows = []
    for name in names:
        t = trades[trades["strategy"] == name] if len(trades) else trades
        if not len(t):
            rows.append({"strategy": name, "trades": 0})
            continue
        r = t["r"]
        wins, losses = r[r > 0].sum(), -r[r < 0].sum()
        eq = r.cumsum()
        rows.append({
            "strategy": name,
            "trades": len(t),
            "win_rate_%": round((r > 0).mean() * 100, 1),
            "avg_R": round(r.mean(), 3),
            "total_R": round(r.sum(), 2),
            "profit_factor": round(wins / losses, 2) if losses else np.inf,
            "max_dd_R": round((eq.cummax() - eq).max(), 2),
            "pnl": round(t["pnl"].sum(), 2),
            "days_traded": t["day"].nunique(),
        })
    return pd.DataFrame(rows).set_index("strategy") if rows else pd.DataFrame()


def load_last_trades(market: str | Market | None = None) -> pd.DataFrame:
    path = trades_csv(get_market(market))
    if path.exists():
        t = pd.read_csv(path, parse_dates=["entry_time", "exit_time"])
        t["day"] = pd.to_datetime(t["day"]).dt.date
        return t
    return pd.DataFrame()
