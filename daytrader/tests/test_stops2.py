import json, tempfile
from pathlib import Path
from dtrader import broker_sync, portfolio
from dtrader.markets import INDIA
import dtrader.data.fyers_provider as fp
from dtrader.config import load_config

tmp = Path(tempfile.mkdtemp())
INDIA_path = INDIA.path
INDIA.__class__.path = lambda self, n: tmp / n  # isolate data files
broker_sync.SYNCED_PATH = tmp / "fyers_synced.json"
fp.fyers_keys = lambda: ("APP-100", "x"); fp.token_valid_today = lambda: "tok"
today = INDIA.now().strftime("%d-%b-%Y")
trades = [{"symbol": "NSE:OIL-EQ", "orderNumber": "1", "side": 1, "tradedQty": 6, "tradePrice": 454.0,
           "orderDateTime": f"{today} 09:20:00"}]
calls = []
def fake(url, key, app, tok, client=None):
    calls.append(key)
    return trades if key == "tradeBook" else []
broker_sync._fetch = fake
cfg = load_config("india")
msgs, issues = broker_sync.sync_and_check(cfg)
print(msgs); print([i.title for i in issues]); print(calls)
assert calls == ["tradeBook", "orderBook"] and len(issues) == 1 and "No stop-loss" in issues[0].title
calls.clear(); assert broker_sync.sync(cfg) == [] and calls == ["tradeBook"]
cfg["fyers"]["stop_check"] = False; calls.clear()
assert broker_sync.sync_and_check(cfg)[1] == [] and calls == ["tradeBook"]
# monitor pushes each problem once
from dtrader import alerts
sent = []
alerts.notify = lambda kind, title, body, cfg: sent.append((kind, title))
class M: pass
m = M(); m.cfg = load_config("india"); m.state = {"alerted": []}
m._seen = alerts.Monitor._seen.__get__(m)
alerts.Monitor._sync_broker(m); alerts.Monitor._sync_broker(m)
print(sent); assert [k for k, _ in sent] == ["STOP"]
print("integration ok")
