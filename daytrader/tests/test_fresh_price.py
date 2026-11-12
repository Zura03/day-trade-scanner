# Scan now wants today's prices: a quote newer than the bars sets `last`, an older one doesn't,
# and Fyers fetches quotes again when a scan asks for fresher ones than it would reuse.
import sys, tempfile, time
sys.path.insert(0,'.')
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np, pandas as pd
from dtrader.markets import INDIA
tmp = Path(tempfile.mkdtemp()); INDIA.__class__.data_dir = property(lambda self: tmp)
NOW = datetime(2026, 11, 12, 10, 30, tzinfo=INDIA.tz)
INDIA.__class__.now = lambda self: NOW
from dtrader.data.base import DataProvider, BAR_COLS
from dtrader.scanner import gather_candidates

idx = pd.date_range(NOW.replace(hour=9, minute=15), NOW - timedelta(minutes=3), freq="1min")  # bars end 10:27
bars = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 5000.0}, index=idx)[BAR_COLS]
didx = pd.date_range(end=pd.Timestamp(NOW).normalize() - timedelta(days=1), periods=20, freq="D")
daily = pd.DataFrame({"open": 95.0, "high": 96.0, "low": 94.0, "close": 95.0, "volume": 100000.0}, index=didx)

class Fake(DataProvider):
    name, market = "fake", INDIA
    def __init__(self, as_of): self.as_of = as_of
    def movers(self, limit=50): return ["AAA"]
    def quotes(self, symbols):
        return pd.DataFrame([{"symbol": s, "last": 103.0, "prev_close": 95.0, "volume": 5e5, "avg_volume": 1e5,
                              "as_of": self.as_of} for s in symbols]).set_index("symbol")
    def _fetch_intraday(self, symbol, start, end): return bars
    def intraday(self, symbol, day): return bars
    def daily(self, symbol, days=60): return daily

cfg = {"universe": {"min_price": 50, "max_price": 10000, "min_gap_pct": 1.5, "min_rel_volume": 1.2, "max_candidates": 30}}
c, = gather_candidates(Fake(NOW.timestamp() - 5), cfg, NOW.date(), log=lambda *a: None)
assert c.last == 103.0 and c.price_at == NOW.timestamp() - 5, (c.last, c.price_at)       # quote is newer
c, = gather_candidates(Fake(NOW.timestamp() - 600), cfg, NOW.date(), log=lambda *a: None)
assert c.last == 100.0 and c.price_at is None                                           # bars are newer

import dtrader.data.fyers_provider as fp
fp.SHARED_DIR = tmp / "shared"; fp.CALLS_PATH = tmp / "calls.json"
p = fp.FyersProvider.__new__(fp.FyersProvider)
calls = []
def fake_get(path, params, source="other"):
    calls.append(params["symbols"])
    return {"d": [{"s": "ok", "n": f"NSE:{s}-EQ", "v": {"lp": 101.0, "prev_close_price": 95.0, "volume": 1}} for s in ("AAA",)]}
p._get = fake_get
p.quote_max_age = None
real_time = fp._time.time
fp._time.time = lambda: real_time()
p._shared_quotes(["AAA"]); assert len(calls) == 1
fp._time.time = lambda: real_time() + 60      # a minute later: auto-refresh reuses it
p._shared_quotes(["AAA"]); assert len(calls) == 1
p.quote_max_age = 15                           # a click fetches again
p._shared_quotes(["AAA"]); assert len(calls) == 2
print("ok")
