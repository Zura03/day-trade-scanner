"""Causal indicators on 1-min bars (value at row i uses only rows <= i)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

import numpy as np
import pandas as pd

OPEN = time(9, 30)
CLOSE = time(16, 0)


def add_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    t = df.index.time
    df["regular"] = (t >= OPEN) & (t < CLOSE)
    df["pre"] = t < OPEN
    tp = (df["high"] + df["low"] + df["close"]) / 3
    # Session VWAP: premarket VWAP before the open, resets at 9:30.
    seg = np.where(df["regular"], 1, np.where(df["pre"], 0, 2))
    pv = (tp * df["volume"]).groupby(seg).cumsum()
    vv = df["volume"].groupby(seg).cumsum().replace(0, np.nan)
    df["vwap"] = (pv / vv).fillna(df["close"])
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["rsi"] = rsi(df["close"], 14)
    df["vol_avg20"] = df["volume"].rolling(20, min_periods=5).mean()
    df["hod"] = df["high"].where(df["regular"]).cummax()
    df["lod"] = df["low"].where(df["regular"]).cummin()
    return df


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


@dataclass
class DayContext:
    """Everything a strategy needs to know about a symbol for one session."""

    symbol: str
    day: date
    prev_close: float
    avg_volume: float = 0.0
    float_shares: float | None = None
    premarket_high: float = np.nan
    premarket_low: float = np.nan
    premarket_volume: float = 0.0
    premarket_last: float = np.nan
    open_price: float = np.nan
    extra: dict = field(default_factory=dict)

    @property
    def gap_pct(self) -> float:
        ref = self.open_price if not np.isnan(self.open_price) else self.premarket_last
        if np.isnan(ref) or not self.prev_close:
            return 0.0
        return (ref / self.prev_close - 1) * 100

    @property
    def float_m(self) -> float | None:
        return self.float_shares / 1e6 if self.float_shares else None


def build_context(symbol: str, day: date, bars: pd.DataFrame, prev_close: float,
                  avg_volume: float = 0.0, float_shares: float | None = None) -> DayContext:
    pre = bars[bars.index.time < OPEN]
    reg = bars[(bars.index.time >= OPEN) & (bars.index.time < CLOSE)]
    ctx = DayContext(symbol=symbol, day=day, prev_close=prev_close, avg_volume=avg_volume,
                     float_shares=float_shares)
    if len(pre):
        ctx.premarket_high = float(pre["high"].max())
        ctx.premarket_low = float(pre["low"].min())
        ctx.premarket_volume = float(pre["volume"].sum())
        ctx.premarket_last = float(pre["close"].iloc[-1])
    if len(reg):
        ctx.open_price = float(reg["open"].iloc[0])
    return ctx
