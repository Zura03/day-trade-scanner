import dtrader.alerts as al
from dtrader.markets import US
sent = []; al.notify = lambda kind, title, body, cfg=None, **k: sent.append((kind, title, body[:70]))
class Hist: name = "alpaca"
class P:
    name = "schwab"; history = Hist(); market = US
ticks = {"n": 0}
class M:
    def __init__(s, p, cfg): s.p, s.state, s.cands, s.strategy, s.m = p, {"positions": {}}, [], type("S", (), {"title": "T"}), US
    def tick(s):
        ticks["n"] += 1
        if s.p.name == "schwab": raise RuntimeError("HTTP 401")
        raise KeyboardInterrupt
al.Monitor = M; al.in_session = lambda now, m: True
al._time.sleep = lambda n: None
import dtrader.data.schwab_provider as sp; sp.login_seconds_left = lambda t: 5 * 86400
try: al.run_monitor(P(), {"market": US, "data_source": "schwab", "alerts": {"poll_seconds": 1}})
except KeyboardInterrupt: pass
print(sent); assert any("failing - using alpaca" in x[1] for x in sent) and ticks["n"] == 3
sent.clear(); sp.login_seconds_left = lambda t: 3600 * 5
try: al.run_monitor(P(), {"market": US, "data_source": "schwab", "alerts": {"poll_seconds": 1}})
except KeyboardInterrupt: pass
assert any("expires soon" in x[1] for x in sent), sent
sent.clear(); P2 = type("P2", (), {"name": "alpaca", "market": US})
M.tick = lambda s: (_ for _ in ()).throw(KeyboardInterrupt())
try: al.run_monitor(P2(), {"market": US, "data_source": "schwab", "alerts": {"poll_seconds": 1}})
except KeyboardInterrupt: pass
assert any("login needed" in x[1] for x in sent), sent
sent.clear()
try: al.run_monitor(P2(), {"market": US, "data_source": "alpaca", "alerts": {"poll_seconds": 1}})
except KeyboardInterrupt: pass
assert not any("login" in x[1] for x in sent), sent
print("monitor OK")
