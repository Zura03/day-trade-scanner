from __future__ import annotations

from .base import Strategy, bar_break


class FirstHourTrend(Strategy):
    name = "first_hour_trend"
    title = "First-Hour Trend Lock"
    style = "momentum"
    description = (
        "Whatever the stock does in the first hour, stick with it. If it closed the first "
        "hour well up from the open and above VWAP, buy pullbacks to the 20 EMA (break of "
        "the pullback candle's high). Never counter-trade the trend. (r/Daytrading list)"
    )
    defaults = {"trend_minutes": 60, "min_trend_pct": 3}

    def check(self, df, i, ctx):
        rng = self.opening_range(df, int(self.p["trend_minutes"]))
        if not rng or i < rng[0] + 1:
            return None
        e = rng[0] - 1  # last bar of the first hour
        first = self.regular_rows(df)[0]
        day_open = df["open"].iat[first]
        if df["close"].iat[e] / day_open - 1 < self.p["min_trend_pct"] / 100 or df["close"].iat[e] < df["vwap"].iat[e]:
            return None  # first hour wasn't a clear uptrend
        j = i - 1  # pullback candle: a fresh tag of the 20 EMA that holds VWAP
        if not (df["low"].iat[j] <= df["ema20"].iat[j] and df["low"].iat[j - 1] > df["ema20"].iat[j - 1]
                and df["close"].iat[j] >= df["vwap"].iat[j]):
            return None
        entry = bar_break(df, i, df["high"].iat[j])
        if entry is None:
            return None
        stop = float(df["low"].iloc[i - 3:i].min()) - 0.01
        return self.make_signal(ctx, df.index[i], entry, stop,
                                f"First hour +{(df['close'].iat[e] / day_open - 1) * 100:.1f}%, bought the 20 EMA pullback")
