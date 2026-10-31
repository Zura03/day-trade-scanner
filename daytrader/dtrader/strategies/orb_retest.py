from __future__ import annotations

import numpy as np

from .base import Strategy, bar_break


class OrbRetest(Strategy):
    name = "orb_retest"
    title = "ORB Retest"
    style = "momentum"
    description = (
        "Mark the first 5 minutes' high (NSE 15). Wait for a candle to close above it, then "
        "for price to come back and retest the level and hold. Buy the break of the retest "
        "candle's high; stop under the retest low; 2R target. (r/Daytrading comments)"
    )
    defaults = {"range_minutes": 5, "min_gap_pct": 5, "retest_tol_pct": 0.15, "max_bars_after_break": 15}

    def qualifies(self, ctx):
        return ctx.gap_pct >= self.p["min_gap_pct"]

    def check(self, df, i, ctx):
        rng = self.opening_range(df, int(self.p["range_minutes"]))
        if not rng or i < rng[0] + 2:
            return None
        start, hi, _ = rng
        closes = df["close"].iloc[start:i].to_numpy()
        breaks = np.flatnonzero(closes > hi)
        if not len(breaks):
            return None
        b = start + int(breaks[0])  # first close above the range
        t = i - 1                  # retest candle
        if t <= b or i - b > self.p["max_bars_after_break"]:
            return None
        tol = self.p["retest_tol_pct"] / 100
        if not (df["low"].iat[t] <= hi * (1 + tol) and df["close"].iat[t] >= hi * (1 - tol)):
            return None
        entry = bar_break(df, i, df["high"].iat[t])
        if entry is None:
            return None
        return self.make_signal(ctx, df.index[i], entry, df["low"].iat[t] - 0.01,
                                f"Broke range high {hi:.2f}, retested and held")
