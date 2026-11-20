"""Short setups by mirroring: a short is a long on the chart turned upside down.

Every price p becomes 2 x pivot - p, with the pivot at the previous close, so
highs become lows, a gap up becomes the same-sized gap down, the premarket
high becomes the premarket low and so on. The long strategies run unchanged on
the mirrored chart; their signals and trades are mirrored back to real prices.

Exact, not an approximation: VWAP and the EMAs are linear in price, so they
mirror the same way (2 x pivot - value); RSI becomes 100 - RSI; the session
high and low swap. Volume doesn't change. A trade's R and P&L per unit are the
same in both pictures, so the exit manager, the backtest and the odds need no
short-side code of their own.
"""
from __future__ import annotations

import copy
import re
from dataclasses import replace

import numpy as np
import pandas as pd

from .indicators import DayContext

LINEAR = ("vwap", "ema9", "ema20")


def mirror_bars(df: pd.DataFrame, pivot: float) -> pd.DataFrame:
    """1-min bars (with or without indicators) turned upside down around `pivot`."""
    out = df.copy()
    p2 = 2 * pivot
    out["open"], out["close"] = p2 - df["open"], p2 - df["close"]
    out["high"], out["low"] = p2 - df["low"], p2 - df["high"]
    for c in LINEAR:
        if c in df:
            out[c] = p2 - df[c]
    if "rsi" in df:
        out["rsi"] = 100 - df["rsi"]
    if "hod" in df and "lod" in df:
        out["hod"], out["lod"] = p2 - df["lod"], p2 - df["hod"]
    return out


def _m(x: float, pivot: float) -> float:
    return 2 * pivot - x if x == x else x  # NaN stays NaN


def mirror_ctx(ctx: DayContext, pivot: float | None = None) -> DayContext:
    p = ctx.prev_close if pivot is None else pivot
    return replace(ctx, prev_close=_m(ctx.prev_close, p),
                   premarket_high=_m(ctx.premarket_low, p), premarket_low=_m(ctx.premarket_high, p),
                   premarket_last=_m(ctx.premarket_last, p), open_price=_m(ctx.open_price, p),
                   prev_high=_m(ctx.prev_low, p), prev_low=_m(ctx.prev_high, p), extra=copy.deepcopy(ctx.extra))


_SWAP = {"high": "low", "low": "high", "above": "below", "below": "above", "up": "down", "down": "up",
         "higher": "lower", "lower": "higher", "highs": "lows", "lows": "highs", "green": "red", "red": "green",
         "buy": "short", "bought": "shorted", "sell": "cover", "reclaim": "rejection", "bounce": "drop",
         "oversold": "overbought", "flush": "spike", "gainers": "losers", "rally": "selloff"}
_SWAP |= {"sold off": "rallied", "rallied": "sold off", "selling off": "rallying", "gap-down": "gap-up",
          "gap-up": "gap-down", "dipped": "popped", "popped": "dipped", "ripped": "dropped", "dropped": "ripped",
          "dip": "pop", "pop": "dip"}
_WORD = re.compile(r"\b(" + "|".join(sorted(_SWAP, key=len, reverse=True)) + r")\b", re.I)


def mirror_text(text: str) -> str:
    """A long setup's description read the other way up ('break of premarket high' ->
    'break of premarket low'). Cosmetic: only used in reasons and watch labels."""
    def sw(mo):
        w = mo.group(0)
        r = _SWAP[w.lower()]
        return r[0].upper() + r[1:] if w[0].isupper() else r
    return _WORD.sub(sw, text or "")


_PRICE = re.compile(r"(?<![\d.])(\d+\.\d{2})(?![\d%x])")


def mirror_prices(text: str, pivot: float) -> str:
    """Prices in a reason (strategies write them with 2 decimals; percentages and
    volume multiples have 1 and a % or x) mirrored around `pivot`."""
    return _PRICE.sub(lambda mo: f"{_m(float(mo.group(1)), pivot):.2f}", text or "")


def mirror_signal(sig, pivot: float, name: str | None = None, side: int | None = None):
    """A Signal mirrored around `pivot` (back to real prices, or into the mirror)."""
    return replace(sig, entry=round(_m(sig.entry, pivot), 4), stop=round(_m(sig.stop, pivot), 4),
                   target=round(_m(sig.target, pivot), 4), reason=mirror_text(mirror_prices(sig.reason, pivot)),
                   strategy=name or sig.strategy, side=-sig.side if side is None else side, pivot=pivot)


def mirror_position(pos, pivot: float, name: str | None = None, side: int | None = None):
    """A Position mirrored around `pivot`; open or closed."""
    q = copy.copy(pos)
    q.entry, q.stop, q.target, q.initial_stop = (_m(pos.entry, pivot), _m(pos.stop, pivot),
                                                 _m(pos.target, pivot), _m(pos.initial_stop, pivot))
    if pos.exit_price is not None:
        q.exit_price = _m(pos.exit_price, pivot)
    q.notes = [mirror_text(n) for n in pos.notes]
    q.strategy = name or pos.strategy
    q.side = -pos.side if side is None else side
    q.pivot = pivot
    return q


def is_finite(x) -> bool:
    return x is not None and np.isfinite(x)
