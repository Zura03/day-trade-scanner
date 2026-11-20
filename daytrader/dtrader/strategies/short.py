"""Short versions of the long strategies (dtrader.mirror explains the idea).

`short_of(OpeningRangeBreakout)` is a strategy named `orb_short` that runs the
ORB rules on the chart turned upside down around the previous close: an
opening-range breakdown. It has its own name, so the backtest, switch hint and
hide list judge it on its own record.
"""
from __future__ import annotations

import pandas as pd

from ..indicators import DayContext
from ..mirror import mirror_bars, mirror_ctx, mirror_signal, mirror_text
from .base import Signal, Strategy


class ShortStrategy(Strategy):
    base_cls: type[Strategy] = Strategy
    side = -1

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.base = self.base_cls(*a, **k)

    @staticmethod
    def _pivot(ctx: DayContext) -> float:
        return ctx.prev_close

    def qualifies(self, ctx: DayContext) -> bool:
        return self.base.qualifies(mirror_ctx(ctx))

    def entries(self, df: pd.DataFrame, ctx: DayContext, after: pd.Timestamp | None = None) -> list[Signal]:
        p = self._pivot(ctx)
        return [mirror_signal(s, p, self.name) for s in self.base.entries(mirror_bars(df, p), mirror_ctx(ctx), after)]

    def check(self, df: pd.DataFrame, i: int, ctx: DayContext) -> Signal | None:
        p = self._pivot(ctx)
        s = self.base.check(mirror_bars(df, p), i, mirror_ctx(ctx))
        return mirror_signal(s, p, self.name) if s else None

    def watch_level(self, df: pd.DataFrame, ctx: DayContext) -> tuple[float, str] | None:
        p = self._pivot(ctx)
        w = self.base.watch_level(mirror_bars(df, p), mirror_ctx(ctx))
        return (2 * p - w[0], mirror_text(w[1])) if w else None

    @property
    def max_trades_per_day(self) -> int:
        return self.base.max_trades_per_day


def short_of(cls: type[Strategy]) -> type[ShortStrategy]:
    return type(f"{cls.__name__}Short", (ShortStrategy,), {
        "base_cls": cls,
        "name": f"{cls.name}_short",
        "title": f"{cls.title} (short)",
        "style": cls.style,
        "defaults": cls.defaults,
        "description": f"Short side of {cls.title}, on the chart upside down: " + mirror_text(cls.description),
        "max_risk_pct": cls.max_risk_pct,
    })
