from __future__ import annotations

import numpy as np

from .base import Strategy


class VwapBounce(Strategy):
    name = "vwap_bounce"
    title = "VWAP Bounce"
    style = "momentum"
    description = (
        "A stock that has been holding above VWAP pulls back to it. Don't buy the first "
        "touch: buy the candle that wicks through VWAP on heavy volume and closes back "
        "above it (sellers tried and failed). Stop under the wick. (r/Daytrading list)"
    )
    defaults = {"min_gap_pct": 5, "min_bars_above": 10, "min_wick_ratio": 0.5, "vol_mult": 1.5,
                "touch_tol_pct": 0.1}

    def qualifies(self, ctx):
        return ctx.gap_pct >= self.p["min_gap_pct"]

    def check(self, df, i, ctx):
        lb = int(self.p["min_bars_above"])
        if i <= lb or not df["regular"].iat[i - lb]:
            return None
        win = slice(i - lb, i)
        if (df["close"].iloc[win] > df["vwap"].iloc[win]).mean() < 0.8:
            return None  # wasn't trending above VWAP
        o, h, l, c, v = (df[k].iat[i] for k in ("open", "high", "low", "close", "vwap"))
        if not (l <= v * (1 + self.p["touch_tol_pct"] / 100) and c > v):
            return None
        rng = h - l
        if rng <= 0 or (min(o, c) - l) / rng < self.p["min_wick_ratio"]:
            return None  # needs a long lower wick
        avg = df["vol_avg20"].iat[i - 1]
        if np.isnan(avg) or df["volume"].iat[i] < self.p["vol_mult"] * avg:
            return None
        return self.make_signal(ctx, df.index[i], c, l - 0.01,
                                f"Wicked through VWAP {v:.2f} on {df['volume'].iat[i] / avg:.1f}x volume, closed above")
