from __future__ import annotations

import numpy as np

from .base import Strategy, bar_break


class StopHuntReversal(Strategy):
    name = "stop_hunt"
    title = "Stop-Hunt Reversal (prior-day low)"
    style = "reversal"
    description = (
        "Retail stops sit under yesterday's low. Price makes a fake move below it (the "
        "stop hunt), then reverses: buy when it reclaims yesterday's low. Stop under the "
        "sweep low. (r/Daytrading list: stop-loss hunting reversal)"
    )
    defaults = {"min_sweep_pct": 0.2, "lookback": 20}

    def qualifies(self, ctx):
        return not np.isnan(ctx.prev_low) and ctx.open_price > ctx.prev_low

    def check(self, df, i, ctx):
        lb = int(self.p["lookback"])
        if i <= lb or not df["regular"].iat[i - 1]:
            return None
        level = ctx.prev_low
        sweep = float(df["low"].iloc[i - lb:i].min())
        if sweep > level * (1 - self.p["min_sweep_pct"] / 100):
            return None  # no real sweep below yesterday's low
        entry = bar_break(df, i, level)
        if entry is None:
            return None
        return self.make_signal(ctx, df.index[i], entry, sweep - 0.01,
                                f"Swept yesterday's low {level:.2f} to {sweep:.2f}, reclaimed it")
