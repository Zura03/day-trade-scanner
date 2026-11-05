import tempfile, json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dtrader.markets import US
tmp = Path(tempfile.mkdtemp())
US.__class__.data_dir = property(lambda self: tmp) if US.key else None
from dtrader import broker_sync as bs, schwab_sync as ss, portfolio as pf
assert str(US.path("x")).startswith(str(tmp)), US.path("x")
cfg = {"market": US, "risk": {"reward_risk": 2.0}, "schwab": {"default_stop_pct": 1.0}}
now = US.now().replace(hour=10, minute=0, second=0, microsecond=0)
iso = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
def order(id, sym, ins, qty, px=None, when=now, status="FILLED", typ="MARKET", asset="EQUITY", **kw):
    o = {"orderId": id, "status": status, "orderType": typ, "quantity": qty, "filledQuantity": qty if px else 0,
         "remainingQuantity": 0 if px else qty, "enteredTime": iso(when),
         "orderLegCollection": [{"instrument": {"assetType": asset, "symbol": sym}, "instruction": ins, "quantity": qty}], **kw}
    if px: o["orderActivityCollection"] = [{"activityType": "EXECUTION", "quantity": qty,
             "executionLegs": [{"quantity": qty / 2, "price": px - .1, "time": iso(when)}, {"quantity": qty / 2, "price": px + .1, "time": iso(when + timedelta(seconds=5))}]}]
    return o
yday = now - timedelta(days=1)
orders = [
  order(1, "ABC", "BUY", 100, 5.0),
  order(2, "OLD", "BUY", 50, 9.0, when=yday),                     # yesterday: not counted
  order(3, "SPY", "BUY", 5, 500.0, asset="COLLECTIVE_INVESTMENT"),# ETF: skipped
  order(4, "SHRT", "SELL_SHORT", 10, 3.0),                        # short: skipped
  order(5, "KEEP", "BUY", 10, 20.0),                              # ignored
  order(6, "PEN", "BUY", 200, 0.1896),                            # sub-$1 price
  {"orderId": 7, "orderStrategyType": "OCO", "status": "FILLED", "enteredTime": iso(yday), "childOrderStrategies": [
      order(8, "HELD", "SELL", 30, status="WORKING", typ="STOP", stopPrice=40.0, when=yday),
      order(9, "HELD", "SELL", 30, status="WORKING", typ="LIMIT", when=yday)]},
  order(10, "GONE", "SELL", 7, status="PENDING_ACTIVATION", typ="STOP", stopPrice=11.0, when=yday),
  order(11, "BR", "SELL", 7, status="AWAITING_PARENT_ORDER", typ="STOP", stopPrice=11.0),
  order(12, "LIM", "SELL", 7, status="PENDING_ACTIVATION", typ="LIMIT", duration="GOOD_TILL_CANCEL", when=yday),
]
fills = ss.group_fills(orders, now.date(), {"KEEP"})
assert sorted(f.symbol for f in fills) == ["ABC", "PEN"], fills
abc = next(f for f in fills if f.symbol == "ABC"); assert abc.qty == 100 and abs(abc.price - 5.0) < 1e-9 and abc.side == 1
msgs = bs.apply_fills(fills, cfg, bs.SCHWAB); print(*msgs, sep="\n")
pos = {r["symbol"]: r for r in pf.load_positions(US)}
assert pos["ABC"]["source"] == "schwab" and pos["ABC"]["stop"] == 4.95 and pos["ABC"]["target"] == 5.1
assert pos["PEN"]["stop"] < pos["PEN"]["entry"] < pos["PEN"]["target"], pos["PEN"]
assert bs.apply_fills(fills, cfg, bs.SCHWAB) == []                # re-read: no double count
sell = order(13, "ABC", "SELL", 40, 5.5, when=now + timedelta(minutes=5))
print(*bs.apply_fills(ss.group_fills(orders + [sell], now.date(), {"KEEP"}), cfg, bs.SCHWAB), sep="\n")
assert next(r for r in pf.load_positions(US) if r["symbol"] == "ABC")["shares"] == 60
assert bs._sync_schwab.__name__
# stop check
stops = ss.pending_stops(orders)
assert set(stops) == {"HELD", "GONE"} and stops["HELD"]["trigger"] == 40.0 and stops["HELD"]["qty"] == 30
hold = ss.holdings([{"securitiesAccount": {"positions": [{"instrument": {"symbol": "ABC"}, "longQuantity": 100.0}, {"instrument": {"symbol": "HELD"}, "longQuantity": 30.0}]}}])
later = now + timedelta(minutes=30)
iss = bs.check_stops(fills[:1], stops, [], later, bs.SCHWAB, hold)
print(*[i.title + " / " + i.body for i in iss], sep="\n")
kinds = {i.key.split("|")[3] + ":" + i.symbol for i in iss}
assert kinds == {"missing:ABC", "orphan:GONE"}, kinds                     # HELD owned long-term: not a leftover
# partial and below plan
s2 = {"ABC": {"qty": 40, "trigger": 4.0, "ids": ["1"], "products": set()}}
assert bs.check_stops(fills[:1], s2, [], later, bs.SCHWAB, hold)[0].key.split("|")[3] == "partial"
s3 = {"ABC": {"qty": 100, "trigger": 4.0, "ids": ["1"], "products": set()}}
r = bs.check_stops(fills[:1], s3, [{"symbol": "ABC", "stop": 4.95}], later, bs.SCHWAB, hold)
assert r[0].key.split("|")[3] == "below" and "Schwab stop" in r[0].body
# grace period; account no longer holding it
assert [i for i in bs.check_stops(fills[:1], {}, [], abc.time + timedelta(seconds=30), bs.SCHWAB, hold)] == []
assert bs.check_stops(fills[:1], {}, [], later, bs.SCHWAB, {}) == []
# expired login is an error, never a browser prompt
tok = tmp / "tok.json"; c2 = {"schwab": {"token_path": str(tok)}}
tok.write_text(json.dumps({"creation_timestamp": 0, "token": {}}))
import dtrader.config as dc; dc.schwab_keys = lambda: ("k", "s")
try: ss.client(c2); raise SystemExit("no error")
except RuntimeError as e: print("expired:", e)
print("schwab sync OK")
