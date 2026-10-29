from __future__ import annotations

import numpy as np

from .base import Strategy, bar_break


class GapAndGo(Strategy):
    name = "gap_and_go"
    title = "Gap & Go"
    style = "momentum"
    description = (
        "Stock gaps up on heavy volume (news catalyst). Buy the first break of the "
        "premarket high after the open while price holds above VWAP. Stop under the "
        "last 3 candles' low. Markets with no premarket (NSE) use the high of the "
        "first few minutes instead."
    )
    defaults = {"min_gap_pct": 10, "min_premarket_volume": 100_000, "open_range_minutes": 5}

    def qualifies(self, ctx):
        pv = ctx.premarket_volume  # None = the data source can't report it
        return ctx.gap_pct >= self.p["min_gap_pct"] and (pv is None or pv >= self.p["min_premarket_volume"])

    def _level(self, df, ctx, i=None):
        """(trigger level, first bar allowed to trigger, label)."""
        if not np.isnan(ctx.premarket_high):
            return ctx.premarket_high, 0, "premarket high"
        n = int(self.p["open_range_minutes"])
        rng = self.opening_range(df, n)
        if not rng:
            return None
        return rng[1], rng[0], f"{n}-min opening high"

    def check(self, df, i, ctx):
        lv = self._level(df, ctx)
        if not lv or i < max(3, lv[1]):
            return None
        level, start, label = lv
        if df["regular"].iat[i - 1] and i - 1 >= start and df["high"].iloc[start:i].max() > level:
            return None  # only the first break counts
        if df["close"].iat[i - 1] < df["vwap"].iat[i - 1]:
            return None
        entry = bar_break(df, i, level)
        if entry is None:
            return None
        stop = float(df["low"].iloc[i - 3:i].min()) - 0.01
        return self.make_signal(ctx, df.index[i], entry, stop,
                                f"Broke {label} {level:.2f} on a {ctx.gap_pct:.1f}% gap")

    def watch_level(self, df, ctx):
        if not self.qualifies(ctx):
            return None
        lv = self._level(df, ctx)
        if not lv:
            return None
        level, start, label = lv
        reg = df[df["regular"]]
        after = reg[reg.index >= df.index[start]] if start < len(df) else reg.iloc[0:0]
        if len(after) and after["high"].max() > level:
            return None
        return level, f"break of {label}"
