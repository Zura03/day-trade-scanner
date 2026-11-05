from datetime import datetime, timedelta
from dtrader.broker_sync import Fill, stop_issues
from dtrader.markets import INDIA
now = datetime(2026, 11, 3, 10, 30, tzinfo=INDIA.tz)
buy = lambda sym, q, mins=10, o="1": Fill(o, sym, 1, q, 454.0, now - timedelta(minutes=mins))
sell = lambda sym, q, o="9": Fill(o, sym, -1, q, 460.0, now - timedelta(minutes=1))
sl = lambda sym, q, trig, prod="INTRADAY", status=6, typ=3, side=-1, filled=0: {
    "symbol": f"NSE:{sym}-EQ", "side": side, "status": status, "type": typ, "qty": q, "filledQty": filled,
    "remainingQuantity": q - filled, "stopPrice": trig, "productType": prod, "id": f"{sym}{trig}"}
pos = [{"symbol": "OIL", "stop": 446.0}]
kinds = lambda xs: [i.key.split("|")[3] for i in xs]

assert kinds(stop_issues([buy("OIL", 6)], [], pos, now)) == ["missing"]
assert stop_issues([buy("OIL", 6, mins=1)], [], pos, now) == []           # grace period
assert stop_issues([buy("OIL", 6)], [sl("OIL", 6, 446)], pos, now) == []  # covered
assert kinds(stop_issues([buy("OIL", 6)], [sl("OIL", 3, 446)], pos, now)) == ["partial"]
assert kinds(stop_issues([buy("OIL", 6)], [sl("OIL", 6, 440)], pos, now)) == ["below"]
assert kinds(stop_issues([buy("OIL", 6), sell("OIL", 6)], [sl("OIL", 6, 446)], pos, now)) == ["orphan"]
assert stop_issues([], [sl("ITC", 10, 400, prod="CNC")], [], now) == []   # delivery holding's stop: fine
assert kinds(stop_issues([buy("OIL", 6)], [sl("OIL", 6, 446, status=1)], pos, now)) == ["missing"]  # cancelled
assert kinds(stop_issues([buy("OIL", 6)], [sl("OIL", 6, 446, typ=1)], pos, now)) == ["missing"]     # limit, not stop
assert stop_issues([buy("OIL", 6)], [sl("OIL", 6, 446, typ=4)], pos, now) == []                      # SL-L counts
assert kinds(stop_issues([buy("OIL", 6), buy("OIL", 4, o="2")], [sl("OIL", 6, 446)], pos, now)) == ["partial"]
i = stop_issues([buy("OIL", 6)], [], pos, now)[0]
print(i.title, "|", i.body)
print("stop checks ok")
