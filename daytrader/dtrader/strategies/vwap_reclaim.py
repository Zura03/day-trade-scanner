from __future__ import annotations

import numpy as np

from .base import Strategy


class VwapReclaim(Strategy):
    name = "vwap_reclaim"
    title = "VWAP Reclaim"
    style = "momentum"
    description = (
        "A gapper sells off below VWAP for 3+ candles, then a candle closes back above "
        "VWAP on above-average volume (buyers back in control). Buy the close; stop "
        "under the low of the dip."
    )
    defaults = {"min_gap_pct": 8, "min_bars_below": 3, "vol_mult": 1.2}

    def qualifies(self, ctx):
        return ctx.gap_pct >= self.p["min_gap_pct"]

    def _bars_below(self, df, j):
        n = 0
        while j - n >= 0 and df["regular"].iat[j - n] and df["close"].iat[j - n] < df["vwap"].iat[j - n]:
            n += 1
        return n

    def check(self, df, i, ctx):
        c, v = df["close"].iat[i], df["vwap"].iat[i]
        if c <= v:
            return None
        n = self._bars_below(df, i - 1)
        if n < self.p["min_bars_below"]:
            return None
        avg = df["vol_avg20"].iat[i - 1]
        if np.isnan(avg) or df["volume"].iat[i] < self.p["vol_mult"] * avg:
            return None  # no volume baseline yet, or not enough volume
        stop = float(df["low"].iloc[i - n:i + 1].min()) - 0.01
        return self.make_signal(ctx, df.index[i], c, stop,
                                f"Reclaimed VWAP {v:.2f} after {n} bars below")

    def watch_level(self, df, ctx):
        if not self.qualifies(ctx) or not len(df):
            return None
        j = len(df) - 1
        if self._bars_below(df, j) >= self.p["min_bars_below"]:
            return float(df["vwap"].iat[j]), "close back above VWAP on volume"
        return None
