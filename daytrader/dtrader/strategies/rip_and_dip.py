from __future__ import annotations

import numpy as np

from .base import Strategy, bar_break


class RipAndDip(Strategy):
    name = "rip_and_dip"
    title = "1-Min Rip & Dip"
    style = "momentum"
    description = (
        "In the first minutes, a gapper breaks the premarket high (the rip), the next "
        "candles dip back below it, then it reclaims the level: buy the reclaim, stop "
        "under the dip. NSE uses the 5-min opening high. (r/Daytrading list)"
    )
    defaults = {"min_gap_pct": 10, "open_range_minutes": 5, "window_minutes": 15}

    def qualifies(self, ctx):
        return ctx.gap_pct >= self.p["min_gap_pct"]

    def _level(self, df, ctx):
        if not np.isnan(ctx.premarket_high):
            return ctx.premarket_high, self.regular_rows(df)[0] if len(self.regular_rows(df)) else None
        rng = self.opening_range(df, int(self.p["open_range_minutes"]))
        return (rng[1], rng[0]) if rng else (None, None)

    def check(self, df, i, ctx):
        if self.minutes_since_open(df.index[i]) > self.p["window_minutes"]:
            return None
        level, start = self._level(df, ctx)
        if level is None or start is None or i < start + 2:
            return None
        highs = df["high"].iloc[start:i].to_numpy()
        rips = np.flatnonzero(highs > level)
        if not len(rips):
            return None
        r = start + int(rips[0])  # the rip candle
        if r >= i - 1:
            return None
        dip_low = float(df["low"].iloc[r + 1:i].min())
        if dip_low >= level:
            return None  # never dipped back under the level
        entry = bar_break(df, i, level)
        if entry is None:
            return None
        return self.make_signal(ctx, df.index[i], entry, dip_low - 0.01,
                                f"Ripped through {level:.2f}, dipped to {dip_low:.2f}, reclaimed")
