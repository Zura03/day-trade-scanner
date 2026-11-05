import json, os, sys, tempfile, time
from datetime import datetime, timedelta, date
from pathlib import Path
from urllib.parse import parse_qs, urlparse
sys.path.insert(0, os.getcwd())
import httpx
tmp = Path(tempfile.mkdtemp())
from dtrader.markets import INDIA
INDIA.__class__.data_dir = property(lambda self: tmp)
import dtrader.data.fyers_provider as fp
import dtrader.data.base as base
fp.TOKEN_PATH = tmp / "fyers_token.json"; fp.CALLS_PATH = tmp / "fyers_calls.json"
base.CACHE_DIR = tmp / "cache"; fp.CACHE_DIR = base.CACHE_DIR; fp.SHARED_DIR = tmp / "cache" / "fyers" / "shared"

calls = []
def handler(req):
    calls.append(req)
    assert req.headers["Authorization"] == "APP-100:TOK", req.headers
    q = parse_qs(req.url.query.decode())
    if req.url.path.endswith("/quotes"):
        syms = q["symbols"][0].split(",")
        assert len(syms) <= 50
        return httpx.Response(200, json={"s": "ok", "d": [
            {"n": s, "s": "ok", "v": {"lp": 105.0, "prev_close_price": 100.0, "volume": 5e5}} for s in syms]})
    if req.url.path.endswith("/history"):
        if q["symbol"][0] == "NSE:EXPIRED-EQ":
            return httpx.Response(200, json={"s": "error", "code": -16, "message": "token expired"})
        a, b = int(q["range_from"][0]), int(q["range_to"][0])
        step = 86400 if q["resolution"][0] == "D" else 60
        ts = list(range(a - a % step, b, step))[:500]
        return httpx.Response(200, json={"s": "ok", "candles": [[t, 100, 101, 99, 100.5, 1000] for t in ts]})
    return httpx.Response(404)

cl = httpx.Client(transport=httpx.MockTransport(handler))
p = fp.FyersProvider("APP-100", "TOK", client=cl)

# quotes: 120 symbols -> 3 quote calls; daily via Yahoo batch stubbed
p._yahoo_daily = lambda syms, days: {}
syms = [f"S{i}" for i in range(120)] + ["M&M"]
q = p.quotes(syms)
assert len(q) == 121 and q.loc["M&M", "last"] == 105.0 and q.loc["S0", "prev_close"] == 100.0, q.head()
assert sum(c.url.path.endswith("/quotes") for c in calls) == 3
print("quotes ok", len(q))

# history -> IST frame
d = p.daily("SBIN", 30)
assert len(d) > 10 and d.index.tz is None, d.tail()
yday = INDIA.sessions_back(3)[-2] if INDIA.sessions_back(3)[-1] == INDIA.now().date() else INDIA.sessions_back(3)[-1]
bars = p.intraday("SBIN", yday)
print("daily", len(d), "intraday", len(bars), bars.index[0], bars.index.tz)
assert str(bars.index.tz) == "Asia/Kolkata"

# counter persisted, under limits
c = json.loads(fp.CALLS_PATH.read_text()); print("calls counted", c["count"]); assert c["count"] == len(calls)

# budget exhaustion -> BudgetExhausted, daily falls back to yahoo
c["count"] = fp.DAILY_BUDGET; fp.CALLS_PATH.write_text(json.dumps(c))
try:
    p._get("/quotes", {"symbols": "NSE:X-EQ"}); raise SystemExit("no budget error")
except fp.BudgetExhausted as e: print("budget ok:", e)
c["count"] = 0; fp.CALLS_PATH.write_text(json.dumps(c))

# expired token -> marked invalid
fp.TOKEN_PATH.write_text(json.dumps({"access_token": "TOK", "created": INDIA.now().isoformat()}))
assert fp.token_valid_today() == "TOK"
try:
    p._history("EXPIRED", INDIA.now() - timedelta(days=2), INDIA.now(), "D"); raise SystemExit("no auth error")
except RuntimeError as e: print("auth ok:", e)
assert fp.token_valid_today() is None

# old token before 6:30 reset
fp.TOKEN_PATH.write_text(json.dumps({"access_token": "OLD", "created": (fp._last_reset() - timedelta(minutes=1)).isoformat()}))
assert fp.token_valid_today() is None
print("reset ok, last reset", fp._last_reset())

# throttle: per-minute cap
fp.PER_MINUTE, fp.PER_SECOND = 5, 100
c = fp._read_calls(); c["recent"] = [time.time() - 58.5] * 5; fp.CALLS_PATH.write_text(json.dumps(c))
t = time.time(); fp.take_call(); w = time.time() - t
print(f"waited {w:.1f}s for minute slot"); assert 1.0 < w < 3.5

# token exchange
def auth(req):
    body = json.loads(req.content)
    import hashlib
    assert body["appIdHash"] == hashlib.sha256(b"APP-100:SEC").hexdigest() and body["code"] == "CODE"
    return httpx.Response(200, json={"s": "ok", "access_token": "NEW"})
assert fp.exchange_code("APP-100", "SEC", "CODE", httpx.Client(transport=httpx.MockTransport(auth))) == "NEW"
u = urlparse(fp.login_url("APP-100", fp.DEFAULT_REDIRECT)); print("login url", u.netloc + u.path, parse_qs(u.query)["redirect_uri"])
print("ALL OK")
