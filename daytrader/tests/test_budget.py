import os, sys, json, tempfile, time
from datetime import timedelta
from pathlib import Path
tmp = Path(tempfile.mkdtemp())
from dtrader.markets import INDIA
INDIA.__class__.data_dir = property(lambda self: tmp)
import dtrader.data.fyers_provider as fp
fp.CALLS_PATH = tmp / "fyers_calls.json"; fp.SHARED_DIR = tmp / "shared"
calls = []
class R:
    status_code = 200
    def __init__(s, d): s.d = d
    def json(s): return s.d
class H:
    headers = {}
    def get(s, url, params=None):
        calls.append(params.get("resolution") or "quotes")
        t = int(time.time()) - 600
        return R({"s": "ok", "candles": [[t, 1, 2, .5, 1.5, 100]]})
today = INDIA.now().date()
p = fp.FyersProvider("a", "b", client=H())
# session running: paced as before, a repeat inside the window reuses, an old file refetches
fp._bars_final_since = lambda: None
assert len(p.intraday("AAA", today)) == 1 and len(calls) == 1
p.intraday("AAA", today); assert len(calls) == 1, "reused within pace"
old = fp.SHARED_DIR / f"AAA_{today}.pkl"; os.utime(old, (time.time() - 4000,) * 2); p._today.clear()
p.intraday("AAA", today); assert len(calls) == 2, "refetched when stale"
# after the close: a file from before final is refetched once, then never again, by any process
fp._bars_final_since = lambda: time.time() - 60
os.utime(old, (time.time() - 120,) * 2); p._today.clear()
p.intraday("AAA", today); assert len(calls) == 3, "one fetch after close"
for _ in range(3): p.intraday("AAA", today)
other = fp.FyersProvider("a", "b", client=H())
os.utime(old, (time.time() - 50,) * 2)
other.intraday("AAA", today); assert len(calls) == 3, ("other process reuses", len(calls))
p._today.clear(); p.intraday("AAA", today); assert len(calls) == 3
# 2 hours later: still no fetch, though the pace window has long passed
os.utime(old, (time.time() - 50,) * 2); p._today["AAA"] = (time.time() - 50, p._today["AAA"][1]); p.intraday("AAA", today); assert len(calls) == 3
# holiday: fetched once today, then reused
fp._bars_final_since = lambda: 0.0
p.intraday("BBB", today); p.intraday("BBB", today); assert len(calls) == 4
# evening reserve in the pace
d = {"date": today.isoformat(), "count": 3990, "recent": []}; fp.CALLS_PATH.write_text(json.dumps(d))
fp._session_minutes_left = lambda: 100.0; p._asked = {"AAA": time.time()}
assert p._refresh_every() == 600.0, p._refresh_every()    # 10 left for 100 min, with the 500 held back
d["count"] = 3000; fp.CALLS_PATH.write_text(json.dumps(d))
assert p._refresh_every() == 55.0                          # 1000 left, one stock
# the hard stop is still 4,500, not 4,000
d["count"] = 4200; fp.CALLS_PATH.write_text(json.dumps(d)); fp.take_call()
d["count"] = 4500; fp.CALLS_PATH.write_text(json.dumps(d))
try: fp.take_call(); raise SystemExit("no stop")
except fp.BudgetExhausted: pass
# counted by source
fp.CALLS_PATH.unlink(); fp._bars_final_since = lambda: None
from datetime import datetime
q = fp.FyersProvider("a", "b", client=H())
q._fetch_intraday("CCC", today, today); q._fetch_intraday("CCC", today - timedelta(days=9), today - timedelta(days=9))
q._history("CCC", INDIA.now(), INDIA.now(), "D", source="daily")
by = fp.calls_by_source(); print(by); assert by == {"today": 1, "history": 1, "daily": 1} and fp.calls_today() == 3
# old file format with no "by"
fp.CALLS_PATH.write_text(json.dumps({"date": today.isoformat(), "count": 5, "recent": []})); assert fp.calls_by_source() == {}; fp.take_call(source="quotes"); assert fp.calls_by_source() == {"quotes": 1}
print("budget fix OK")
