from __future__ import annotations

from .base import Strategy, bar_break


class OpeningReversal(Strategy):
    name = "opening_reversal"
    title = "9:45 Reversal"
    style = "reversal"
    description = (
        "The first 15 minutes are noisy and often reverse around 9:45 (NSE: 9:30). If a "
        "stock sold off from the open, buy the break above the last 3 candles' high in the "
        "reversal window. Stop under the low of the day. (r/Daytrading list)"
    )
    defaults = {"reversal_minutes": 15, "window_minutes": 20, "min_drop_pct": 2}

    def check(self, df, i, ctx):
        mins = self.minutes_since_open(df.index[i])
        if not (self.p["reversal_minutes"] <= mins <= self.p["reversal_minutes"] + self.p["window_minutes"]):
            return None
        if i < 3 or not df["regular"].iat[i - 3]:
            return None
        day_open = df["open"].iat[self.regular_rows(df)[0]]
        low = df["lod"].iat[i - 1]
        if low > day_open * (1 - self.p["min_drop_pct"] / 100):
            return None  # the open wasn't a sell-off
        level = float(df["high"].iloc[i - 3:i].max())
        entry = bar_break(df, i, level)
        if entry is None:
            return None
        for j in range(i - 1, 2, -1):  # only the first break inside the window counts
            if self.minutes_since_open(df.index[j]) < self.p["reversal_minutes"]:
                break
            if bar_break(df, j, float(df["high"].iloc[j - 3:j].max())) is not None:
                return None
        return self.make_signal(ctx, df.index[i], entry, low - 0.01,
                                f"Sold off {(1 - low / day_open) * 100:.1f}% from the open, reversing at {df.index[i]:%H:%M}")
