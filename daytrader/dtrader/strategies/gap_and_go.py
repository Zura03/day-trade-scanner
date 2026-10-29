from __future__ import annotations

import numpy as np

from .base import Strategy, bar_break


class GapAndGo(Strategy):
    name = "gap_and_go"
    title = "Gap & Go"
    style = "momentum"
    description = (
        "Stock gaps up 10%+ premarket on heavy volume (news catalyst). Buy the first "
        "break of the premarket high after the open while price holds above VWAP. "
        "Stop under the last 3 candles' low."
    )
    defaults = {"min_gap_pct": 10, "min_premarket_volume": 100_000}

    def qualifies(self, ctx):
        # Yahoo reports 0 premarket volume, so 0 means "unknown", not "none".
        pv = ctx.premarket_volume
        return (ctx.gap_pct >= self.p["min_gap_pct"]
                and (pv == 0 or pv >= self.p["min_premarket_volume"])
                and not np.isnan(ctx.premarket_high))

    def check(self, df, i, ctx):
        level = ctx.premarket_high
        if i < 3 or (df["regular"].iat[i - 1] and df["hod"].iat[i - 1] > level):
            return None  # only the first break counts
        if df["close"].iat[i - 1] < df["vwap"].iat[i - 1]:
            return None
        entry = bar_break(df, i, level)
        if entry is None:
            return None
        stop = float(df["low"].iloc[i - 3:i].min()) - 0.01
        return self.make_signal(ctx, df.index[i], entry, stop,
                                f"Broke premarket high {level:.2f} on a {ctx.gap_pct:.0f}% gap")

    def watch_level(self, df, ctx):
        if not self.qualifies(ctx):
            return None
        reg = df[df["regular"]]
        if len(reg) and reg["high"].max() > ctx.premarket_high:
            return None
        return ctx.premarket_high, "break of premarket high"
