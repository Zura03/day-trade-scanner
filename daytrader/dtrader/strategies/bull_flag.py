from __future__ import annotations

import numpy as np

from .base import Strategy, bar_break


class BullFlag(Strategy):
    name = "bull_flag"
    title = "Bull Flag / First Pullback"
    style = "momentum"
    description = (
        "A stock already up 10%+ on the day makes a sharp push (the pole), then pulls "
        "back 1-5 candles on lighter volume while holding above VWAP. Buy the first "
        "candle that makes a new high; stop under the pullback low."
    )
    defaults = {"min_day_gain_pct": 10, "max_pullback_bars": 5, "max_retrace_pct": 50,
                "min_pole_pct": 4}

    def _setup(self, df, j, ctx):
        """Flag whose pullback ends on bar j. Returns (trigger, stop, note)."""
        mp = self.p["max_pullback_bars"]
        if j < mp + 12 or not df["regular"].iat[j]:
            return None
        if df["close"].iat[j] / ctx.prev_close - 1 < self.p["min_day_gain_pct"] / 100:
            return None
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        win = slice(j - mp, j + 1)
        t = (j - mp) + int(np.argmax(highs[win]))
        if t >= j:  # needs at least one pullback bar
            return None
        top = highs[t]
        pole_low = lows[t - 10:t + 1].min()
        if top / pole_low - 1 < self.p["min_pole_pct"] / 100:
            return None
        opens, closes = df["open"].to_numpy(), df["close"].to_numpy()
        if (closes[t - 4:t + 1] > opens[t - 4:t + 1]).sum() < 3:
            return None
        pb_low = lows[t + 1:j + 1].min()
        if (top - pb_low) / (top - pole_low) > self.p["max_retrace_pct"] / 100:
            return None
        vol = df["volume"].to_numpy()
        if vol[t + 1:j + 1].mean() >= vol[t - 4:t + 1].mean():
            return None
        if pb_low <= df["vwap"].iat[j]:
            return None
        return highs[j], pb_low - 0.01, f"flag after {top / pole_low * 100 - 100:.0f}% pole, pullback low {pb_low:.2f}"

    def check(self, df, i, ctx):
        s = self._setup(df, i - 1, ctx)
        if not s:
            return None
        entry = bar_break(df, i, s[0])
        if entry is None:
            return None
        return self.make_signal(ctx, df.index[i], entry, s[1], "First candle to new high - " + s[2])

    def watch_level(self, df, ctx):
        s = self._setup(df, len(df) - 1, ctx)
        return (s[0], "first candle to make a new high") if s else None
