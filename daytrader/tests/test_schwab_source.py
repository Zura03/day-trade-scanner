import json, tempfile, time, os
from pathlib import Path
from datetime import date, timedelta
import pandas as pd
tmp = Path(tempfile.mkdtemp())
import dtrader.data.schwab_provider as sp, dtrader.data.base as base
sp.CALLS_PATH = tmp / "calls.json"; sp.CACHE_DIR = tmp; base.CACHE_DIR = tmp
from dtrader.markets import US
class R:
    def __init__(s, code, d=None): s.status_code, s.d, s.text = code, d or {}, ""
    def json(s): return s.d
    def raise_for_status(s):
        if s.status_code != 200: raise RuntimeError(s.status_code)
sleeps = []; real_sleep = time.sleep
sp._time.sleep = lambda n: sleeps.append(n)
class Hist:
    name = "alpaca"
    def intraday(s, sym, d): return "HIST-BARS"
    def all_symbols(s): return ["A", "B"]
    def daily_many(s, syms, days=60): return {"from": "hist"}
    def intraday_many(s, syms, d): return {"from": "hist"}
    def movers(s, limit=50): return ["ZZZ", "AAA", "BAD1"]
def provider(client, history=None):
    p = sp.SchwabProvider.__new__(sp.SchwabProvider); p.client = client; p.history = history; return p
today = US.now().date(); y = today - timedelta(days=1)
now_ms = int(pd.Timestamp(US.now()).replace(hour=10, minute=0, second=0, microsecond=0).timestamp() * 1000)
candle = {"datetime": now_ms, "open": 1, "high": 2, "low": .5, "close": 1.5, "volume": 10}
class C:
    n = 0; seq = []
    def get_price_history_every_minute(s, sym, **k):
        C.n += 1; return C.seq.pop(0) if C.seq else R(200, {"candles": [candle]})
    def get_movers(s, *a, **k): return R(200, {"screeners": [{"symbol": "AAA"}, {"symbol": "BBB"}]})
    def get_price_history_every_day(s, sym, **k):
        C.n += 1; return R(200, {"candles": [dict(candle, datetime=int((pd.Timestamp(US.now()) - pd.Timedelta(days=i)).timestamp() * 1000)) for i in range(3)]})
p = provider(C(), Hist())
# history comes from Alpaca, today from Schwab
assert p.intraday("X", y) == "HIST-BARS" and C.n == 0
assert p.all_symbols() == ["A", "B"] and p.daily_many(["X"]) == {"from": "hist"} and p.intraday_many(["X"], y) == {"from": "hist"}
# movers: Schwab first, then the others, no duplicates, letters only
assert p.movers(10) == ["AAA", "BBB", "ZZZ"], p.movers(10)
# today's bars shared for 55 s by another process, then refetched
b = p.intraday("X", today); assert C.n == 1; q = provider(C(), Hist()); q.intraday("X", today); assert C.n == 1
f = tmp / "schwab" / "today" / f"X_{today}.pkl"; os.utime(f, (time.time() - 60,) * 2); q.intraday("X", today); assert C.n == 2
# 429 waits and retries, not an empty result; 3 refusals raise
C.n = 0; C.seq = [R(429), R(200, {"candles": [candle]})]; sleeps.clear()
d = p._fetch_intraday("X", today, today); assert len(d) == 1 and C.n == 2 and sleeps[0] == 15
C.seq = [R(429), R(429), R(429)]
try: p._fetch_intraday("X", today, today); raise SystemExit("no raise")
except RuntimeError as e: assert "rate limited" in str(e)
# a server or login error raises, bad symbol is simply no bars
C.seq = [R(401)]
try: p._fetch_intraday("X", today, today); raise SystemExit("no raise")
except RuntimeError as e: assert "401" in str(e)
C.seq = [R(400)]; assert p._fetch_intraday("X", today, today).empty
# daily is cached for the day
C.n = 0; p.daily("D1", 2); p.daily("D1", 2); assert C.n == 1
# per-minute limit waits when full
sp.CALLS_PATH.write_text(json.dumps([time.time()] * 100)); sleeps.clear()
t = [time.time() + i for i in (0,)]; sp.PER_MINUTE = 100
orig = sp._time.time; calls = {"n": 0}
def fake_time():
    calls["n"] += 1; return orig() + (61 if calls["n"] > 1 else 0)
sp._time.time = fake_time; sp.take_slot(); sp._time.time = orig; assert sleeps, "waited"
# login state
tok = "x.json"; sp.ROOT = tmp
assert sp.login_seconds_left(tok) == 0
(tmp / tok).write_text(json.dumps({"creation_timestamp": time.time() - 86400})); assert 5.9 * 86400 < sp.login_seconds_left(tok) < 6.1 * 86400
(tmp / tok).write_text(json.dumps({"creation_timestamp": time.time() - 8 * 86400})); assert sp.login_seconds_left(tok) == 0
(tmp / tok).write_text("{bad"); assert sp.login_seconds_left(tok) == 0
print("provider OK")
# get_provider falls back and never opens a browser
import dtrader.data as D
cfg = {"market": US, "data_source": "schwab", "schwab": {"token_path": "nope.json"}, "alpaca": {}}
D.schwab_keys = lambda: ("k", "s"); D.alpaca_keys = lambda: ("a", "b")
print("expired, alpaca keys ->", D.get_provider(cfg, "schwab").name); assert D.get_provider(cfg, "schwab").name == "alpaca"
D.alpaca_keys = lambda: None
print("expired, no alpaca ->", D.get_provider(cfg, "schwab").name); assert D.get_provider(cfg, "schwab").name == "yfinance"
D.schwab_keys = lambda: None
try: D.get_provider(cfg, "schwab"); raise SystemExit("no raise")
except RuntimeError as e: print("no keys ->", str(e)[:40])
print("ALL OK")
