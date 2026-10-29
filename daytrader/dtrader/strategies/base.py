"""Strategy interface, trade signals, and the shared exit manager.

A strategy only decides *where to get in* (entry, stop, target). Exits are
managed the same way for every strategy (stop / target / breakeven / trailing
9 EMA / time stop) by `Position.step`, used by both the backtester and the
live alert monitor so the two always agree.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time

import numpy as np
import pandas as pd

from ..indicators import DayContext
from ..markets import US, Market


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


# Don't buy a triggered setup once price has run this many R past the entry.
MAX_CHASE_R = 0.5


@dataclass
class Signal:
    symbol: str
    strategy: str
    time: pd.Timestamp
    entry: float
    stop: float
    target: float
    reason: str

    @property
    def risk(self) -> float:
        return self.entry - self.stop

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time"] = str(self.time)
        return d


@dataclass
class Position:
    symbol: str
    strategy: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    target: float
    initial_stop: float
    shares: int = 0
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    trailing: bool = False
    notes: list = field(default_factory=list)

    @classmethod
    def from_signal(cls, s: Signal, shares: int = 0) -> "Position":
        return cls(s.symbol, s.strategy, s.time, s.entry, s.stop, s.target, s.stop, shares)

    @property
    def risk(self) -> float:
        return max(self.entry - self.initial_stop, 1e-9)

    @property
    def r_multiple(self) -> float | None:
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry) / self.risk

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    def step(self, ts: pd.Timestamp, bar: pd.Series, exits: dict) -> str | None:
        """Advance one bar. Returns an exit reason if the position closed."""
        if not self.is_open:
            return None
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
        # 1) stop first (conservative when stop and target share a bar)
        if l <= self.stop:
            px = min(o, self.stop)
            label = "stop" if self.stop < self.entry else ("breakeven" if self.stop == self.entry else "trail stop")
            return self._close(ts, px, label)
        if h >= self.target:
            return self._close(ts, max(o, self.target), "target")
        # 2) manage stop using this bar's close
        best_r = (h - self.entry) / self.risk
        be = exits.get("breakeven_after_r")
        if be is not None and best_r >= be and self.stop < self.entry:
            self.stop = self.entry
            self.notes.append(f"{ts:%H:%M} stop -> breakeven")
        tr = exits.get("trail_after_r")
        if tr is not None and not self.trailing and (c - self.entry) / self.risk >= tr:
            self.trailing = True
            self.notes.append(f"{ts:%H:%M} trailing 9 EMA - sell on a close below it")
        ema = bar.get("ema9", np.nan)
        if self.trailing and not np.isnan(ema) and c < ema:
            return self._close(ts, c, "lost 9 EMA")
        # 3) time stop
        if ts.time() >= _t(exits.get("time_exit", "15:50")):
            return self._close(ts, c, "time exit")
        return None

    def replay(self, bars: pd.DataFrame, exits: dict) -> "Position":
        """Run the trade forward through `bars` (entry-bar stop check, then
        stop/target/trailing/time exits). Used by the scanner and the monitor."""
        if self.entry_time in bars.index:
            self.check_entry_bar(bars.loc[self.entry_time])
        for ts, bar in bars[bars.index > self.entry_time].iterrows():
            if not self.is_open or self.step(ts, bar, exits):
                break
        return self

    def check_entry_bar(self, bar: pd.Series) -> bool:
        """Breakout entries fill inside the entry bar; if that bar closed at or
        below the stop, count it as stopped there (backtest and live agree)."""
        if self.is_open and bar["close"] <= self.stop:
            self._close(self.entry_time, self.stop, "stop")
            return True
        return False

    def _close(self, ts, px, reason) -> str:
        self.exit_time, self.exit_price, self.exit_reason = ts, float(px), reason
        return reason

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("entry_time", "exit_time"):
            d[k] = str(d[k]) if d[k] is not None else None
        d["r_multiple"] = self.r_multiple
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        for k in ("entry_time", "exit_time"):
            if d.get(k):
                d[k] = pd.Timestamp(d[k])
        return cls(**d)


class Strategy:
    name = "base"
    title = "Base"
    style = ""          # "momentum" | "mean-reversion" | ...
    description = ""
    defaults: dict = {}
    max_risk_pct = 10.0  # skip setups whose stop is wider than this % of price

    def __init__(self, params: dict | None = None, exits: dict | None = None,
                 risk: dict | None = None, universe: dict | None = None, market: Market = US):
        self.market = market
        self.p = {**self.defaults, **(params or {})}
        self.exits = exits or {}
        self.risk_cfg = risk or {}
        self.universe = universe or {}
        self.rr = float(self.risk_cfg.get("reward_risk", 2.0))
        self.last_entry = _t(self.exits.get("no_new_entries_after", "11:30"))

    # ---- to implement --------------------------------------------------
    def qualifies(self, ctx: DayContext) -> bool:
        """Day-level filter (gap, float, ...). Called before bar scanning."""
        return True

    def check(self, df: pd.DataFrame, i: int, ctx: DayContext) -> Signal | None:
        """Is there an entry on bar i? Must only look at rows <= i."""
        raise NotImplementedError

    def watch_level(self, df: pd.DataFrame, ctx: DayContext) -> tuple[float, str] | None:
        """Price that would trigger an entry next (for 'setting up' status)."""
        return None

    # ---- shared helpers ------------------------------------------------
    def make_signal(self, ctx, ts, entry, stop, reason, target=None) -> Signal | None:
        if stop >= entry or entry <= 0:
            return None
        if (entry - stop) / entry * 100 > self.max_risk_pct:
            return None
        if target is None:
            target = entry + self.rr * (entry - stop)
        return Signal(ctx.symbol, self.name, ts, round(entry, 4), round(stop, 4),
                      round(target, 4), reason)

    def regular_rows(self, df: pd.DataFrame) -> np.ndarray:
        return np.flatnonzero(df["regular"].to_numpy())

    def opening_range(self, df: pd.DataFrame, minutes: int) -> tuple[int, float, float] | None:
        """(first row after the range, range high, range low) for the first
        `minutes` minutes after the market open - by clock time, so missing
        bars on thin stocks don't stretch the range."""
        reg = self.regular_rows(df)
        if not len(reg):
            return None
        day = df.index[reg[0]].date()
        end = pd.Timestamp(datetime.combine(day, self.market.open, self.market.tz)) + pd.Timedelta(minutes=minutes)
        inside = reg[df.index[reg] < end]
        after = reg[df.index[reg] >= end]
        if not len(inside) or not len(after):
            return None  # range not finished yet
        return int(after[0]), float(df["high"].iloc[inside].max()), float(df["low"].iloc[inside].min())

    def entries(self, df: pd.DataFrame, ctx: DayContext, after: pd.Timestamp | None = None) -> list[Signal]:
        """All entry signals in a session (bars with indicators added)."""
        if not self.qualifies(ctx):
            return []
        out = []
        for i in self.regular_rows(df):
            ts = df.index[i]
            if after is not None and ts <= after:
                continue
            if ts.time() > self.last_entry:
                break
            s = self.check(df, i, ctx)
            if s:
                out.append(s)
        return out

    @property
    def max_trades_per_day(self) -> int:
        """Trades per symbol per session (backtest and live monitor)."""
        return int(self.exits.get("max_trades_per_day", 2))

    def position_size(self, s: Signal) -> int:
        risk_dollars = float(self.risk_cfg.get("risk_per_trade", 100))
        acct = float(self.risk_cfg.get("account_size", 25000))
        cap = acct * float(self.risk_cfg.get("max_position_pct", 25)) / 100
        if s.risk <= 0:
            return 0
        return int(max(0, min(risk_dollars / s.risk, cap / s.entry)))


def bar_break(df: pd.DataFrame, i: int, level: float) -> float | None:
    """Entry price if bar i trades through `level` (buy-stop style)."""
    if level is None or np.isnan(level) or i == 0:
        return None
    if df["high"].iat[i] > level and df["close"].iat[i - 1] <= level:
        return max(df["open"].iat[i], level)
    return None
