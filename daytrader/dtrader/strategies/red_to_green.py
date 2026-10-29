from __future__ import annotations

from .base import Strategy, bar_break


class RedToGreen(Strategy):
    name = "red_to_green"
    title = "Red-to-Green"
    style = "momentum"
    description = (
        "Stock trades red on the day (below yesterday's close), then pushes back "
        "through yesterday's close. Buy the cross; stop at the low of day. Good for "
        "day-2 continuation on yesterday's runners."
    )
    defaults = {"min_dip_pct": 2}

    def qualifies(self, ctx):
        return ctx.open_price < ctx.prev_close  # opened red

    def check(self, df, i, ctx):
        level = ctx.prev_close
        if not df["regular"].iat[i - 1]:
            return None
        if df["hod"].iat[i - 1] > level:
            return None  # already went green earlier
        lod = df["lod"].iat[i - 1]
        if lod > level * (1 - self.p["min_dip_pct"] / 100):
            return None
        entry = bar_break(df, i, level)
        if entry is None:
            return None
        return self.make_signal(ctx, df.index[i], entry, lod - 0.01,
                                f"Crossed yesterday's close {level:.2f} from a {100 - lod / level * 100:.1f}% dip")

    def watch_level(self, df, ctx):
        reg = df[df["regular"]]
        if not len(reg) or reg["high"].max() > ctx.prev_close:
            return None
        if reg["low"].min() > ctx.prev_close * (1 - self.p["min_dip_pct"] / 100):
            return None
        return ctx.prev_close, "cross back above yesterday's close"
