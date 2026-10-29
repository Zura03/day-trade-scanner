from __future__ import annotations

from .base import Strategy


class OversoldBounce(Strategy):
    name = "oversold_bounce"
    title = "Oversold Bounce (mean reversion)"
    style = "mean-reversion"
    description = (
        "A mover flushes 8%+ off its high and RSI drops under 25. Buy the first green "
        "candle that closes above the prior candle's high, targeting a snap back to "
        "VWAP. Useful on choppy days when breakouts keep failing."
    )
    defaults = {"min_flush_pct": 8, "rsi_max": 25, "lookback": 30, "min_gap_pct": 5}

    def qualifies(self, ctx):
        return abs(ctx.gap_pct) >= self.p["min_gap_pct"]

    def check(self, df, i, ctx):
        lb = int(self.p["lookback"])
        if i < lb or not df["regular"].iat[i - lb]:
            return None
        o, c, v = df["open"].iat[i], df["close"].iat[i], df["vwap"].iat[i]
        if c <= o or c <= df["high"].iat[i - 1] or c >= v:
            return None
        hi = df["high"].iloc[i - lb:i].max()
        low = float(df["low"].iloc[i - 10:i + 1].min())
        if (hi - low) / hi * 100 < self.p["min_flush_pct"]:
            return None
        if df["rsi"].iloc[i - 3:i].min() > self.p["rsi_max"]:
            return None
        stop = low - 0.01
        if v - c < c - stop:  # need at least 1R of room to VWAP
            return None
        return self.make_signal(ctx, df.index[i], c, stop,
                                f"Reversal candle after {(hi - low) / hi * 100:.0f}% flush, RSI oversold",
                                target=v)
