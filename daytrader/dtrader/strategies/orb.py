from __future__ import annotations

from .base import Strategy, bar_break


class OpeningRangeBreakout(Strategy):
    name = "orb"
    title = "Opening Range Breakout"
    style = "momentum"
    description = (
        "Mark the high/low of the first N minutes (default 5). Buy the first break "
        "above the range high while above VWAP. Stop at the range low (or mid if the "
        "range is too wide). Works best on gappers with strong relative volume."
    )
    defaults = {"range_minutes": 5, "min_gap_pct": 5}

    def qualifies(self, ctx):
        return ctx.gap_pct >= self.p["min_gap_pct"]

    def _range(self, df):
        reg = self.regular_rows(df)
        n = int(self.p["range_minutes"])
        if len(reg) < n:
            return None
        r = reg[:n]
        return reg[0] + n, df["high"].iloc[r].max(), df["low"].iloc[r].min()

    def check(self, df, i, ctx):
        rng = self._range(df)
        if not rng or i < rng[0]:
            return None
        start, hi, lo = rng
        if i > start and df["high"].iloc[start:i].max() > hi:
            return None  # first break only
        if df["close"].iat[i - 1] < df["vwap"].iat[i - 1]:
            return None
        entry = bar_break(df, i, hi)
        if entry is None:
            return None
        stop = lo - 0.01
        if (entry - stop) / entry * 100 > self.max_risk_pct:
            stop = (hi + lo) / 2
        return self.make_signal(ctx, df.index[i], entry, stop,
                                f"Broke {self.p['range_minutes']}-min range high {hi:.2f}")

    def watch_level(self, df, ctx):
        rng = self._range(df)
        if not rng or not self.qualifies(ctx) or len(df) <= rng[0]:
            return None
        if df["high"].iloc[rng[0]:].max() > rng[1]:
            return None
        return rng[1], f"{self.p['range_minutes']}-min range high"
