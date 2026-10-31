from __future__ import annotations

from .base import Strategy


class GapDownBounce(Strategy):
    name = "gap_down_bounce"
    title = "Bagholder Bounce (gap-down)"
    style = "reversal"
    description = (
        "A stock gaps down hard (US 15%+, NSE 4%+). Wait for the flush to a new low, then "
        "buy the first real bounce: a green candle closing above the prior candle's high "
        "and back over the 9 EMA. Stop under the flush low. (r/Daytrading list)"
    )
    defaults = {"min_gap_down_pct": 15, "min_flush_pct": 3, "lookback": 10}

    def qualifies(self, ctx):
        return ctx.gap_pct <= -self.p["min_gap_down_pct"]

    def check(self, df, i, ctx):
        lb = int(self.p["lookback"])
        if i <= lb or not df["regular"].iat[i - lb]:
            return None
        lod = df["lod"].iat[i - 1]
        if lod > ctx.open_price * (1 - self.p["min_flush_pct"] / 100):
            return None  # no flush below the open yet
        if df["low"].iloc[i - lb:i].min() > lod:
            return None  # the low wasn't made recently
        o, c = df["open"].iat[i], df["close"].iat[i]
        if not (c > o and c > df["high"].iat[i - 1] and c > df["ema9"].iat[i]
                and df["close"].iat[i - 1] <= df["ema9"].iat[i - 1]):
            return None  # first green reclaim of the 9 EMA
        return self.make_signal(ctx, df.index[i], c, lod - 0.01,
                                f"{ctx.gap_pct:.0f}% gap-down flushed to {lod:.2f}, first real bounce")
