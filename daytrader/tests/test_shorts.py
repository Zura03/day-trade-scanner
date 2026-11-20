# Shorts by mirroring: exact indicators, the round trip, an opening-range breakdown end to end,
# short P&L sign, the odds and status on the short side.
import sys
sys.path.insert(0,'.')
from datetime import date
import numpy as np, pandas as pd
from dtrader.markets import US_FUT
from dtrader.indicators import add_indicators, build_context
from dtrader.mirror import mirror_bars, mirror_ctx, mirror_text
from dtrader.strategies import REGISTRY, get_strategy, strategy_names
from dtrader.strategies.base import Position

day = date(2026, 11, 18)
idx = pd.date_range(pd.Timestamp(f"{day} 08:00", tz=US_FUT.tz), periods=420, freq="1min")
rng = np.random.default_rng(1)
c = 7000 + np.cumsum(rng.normal(0, 1.5, len(idx)))
reg = (idx.time >= US_FUT.open)
c[reg] -= np.linspace(0, 60, reg.sum())               # sells off after the open
raw = pd.DataFrame({"open": c + rng.normal(0, .3, len(idx)), "close": c}, index=idx)
raw["high"] = raw[["open", "close"]].max(1) + 1.0
raw["low"] = raw[["open", "close"]].min(1) - 1.0
raw["volume"] = 1000.0
raw = raw[["open", "high", "low", "close", "volume"]]
pivot = 7010.0

# indicators of the mirrored chart = the mirrored indicators (exact, not approximate)
a = add_indicators(mirror_bars(raw, pivot), US_FUT)
b = mirror_bars(add_indicators(raw, US_FUT), pivot)
for col in ("open", "high", "low", "close", "vwap", "ema9", "ema20", "rsi", "hod", "lod"):
    skip = 14 if col == "rsi" else 0  # RSI reads 50 until the first down move: only its warm-up differs
    assert np.allclose(a[col].iloc[skip:].fillna(0), b[col].iloc[skip:].fillna(0)), col
assert np.allclose(mirror_bars(mirror_bars(raw, pivot), pivot), raw)   # round trip
assert mirror_text("break of premarket high") == "break of premarket low"

cfg = {"strategies": {"orb": {"range_minutes": 15, "min_gap_pct": 0}}, "exits": {"no_new_entries_after": "15:00",
       "time_exit": "15:50", "trail_after_r": 1.0, "breakeven_after_r": 1.0},
       "risk": {"risk_per_trade": 500, "account_size": 50000, "max_position_pct": 100},
       "market": US_FUT, "allow_short": True, "universe": {}}
assert "orb_short" in strategy_names(cfg) and "orb_short" not in strategy_names({**cfg, "allow_short": False})
short = get_strategy("orb_short", cfg)
assert short.p == {"range_minutes": 15, "min_gap_pct": 0}                 # the long's settings
ctx = build_context("MES", day, raw, pivot, 1000.0, market=US_FUT)
df = add_indicators(raw, US_FUT)
sigs = short.entries(df, ctx)
assert sigs, "a steady selloff should give an opening-range breakdown"
s = sigs[0]
first15 = df[(df.index.time >= US_FUT.open) & (df.index < pd.Timestamp(f"{day} 09:45", tz=US_FUT.tz))]
assert s.side == -1 and s.strategy == "orb_short" and s.stop > s.entry > s.target
assert s.entry <= first15["low"].min() + 1e-3 and s.risk > 0                # broke the range low
assert s.reason == f"Broke 15-min range low {first15['low'].min():.2f}", s.reason   # real price, not the mirror's
from dtrader.mirror import mirror_prices
assert mirror_prices("on a 3.20% gap, 1.50x volume, VWAP 7000.00", 7010) == "on a 3.20% gap, 1.50x volume, VWAP 7020.00"

# replay: a short that falls to its target makes money
p = Position.from_signal(s, 5).replay(df, short.exits)
assert p.side == -1 and p.exit_price is not None
assert (p.exit_price < p.entry) == (p.pnl > 0) and np.sign(p.r_multiple) == np.sign(p.pnl)

# backtest: the short replays as the long on the mirror, mapped back
from dtrader.backtest import simulate_day
t = simulate_day(short, df, ctx)
assert t and all(x.side == -1 and x.strategy == "orb_short" for x in t)
assert abs(t[0].entry - s.entry) / s.entry < 0.01

# status: a short past its stop (above it) is no longer enterable
from dtrader.scanner import buy_status
st, why = buy_status(short, s, df[df.index <= s.time], s.stop + 1)
assert st.startswith("triggered") and why == "above stop"
print("ok")
