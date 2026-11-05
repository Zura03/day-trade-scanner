import json, os, sys, tempfile
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, os.getcwd())
tmp = Path(tempfile.mkdtemp())
from dtrader.markets import INDIA
INDIA.__class__.data_dir = property(lambda self: tmp)
from dtrader import broker_sync as bs, portfolio as pf
from dtrader.config import load_config
bs.SYNCED_PATH = tmp / "fyers_synced.json"
cfg = load_config("india")
now = INDIA.now().replace(hour=10, minute=0, second=0, microsecond=0)
def t(mins): return (now + timedelta(minutes=mins)).strftime("%d-%b-%Y %H:%M:%S")
def tr(order, sym, side, qty, px, mins, n):
    return {"orderNumber": order, "symbol": f"NSE:{sym}-EQ", "side": side, "tradedQty": qty, "tradePrice": px,
            "orderDateTime": t(mins), "tradeNumber": n}

# a buyable setup for TCS
import pandas as pd
pf.remember_buyable(INDIA, pd.DataFrame([{"symbol": "TCS", "strategy": "gap_and_go", "triggered_at": "09:58",
    "entry": 2500.0, "stop": 2480.0, "target": 2540.0, "shares": 2, "last": 2501.0}]))

book = [tr("A1", "TCS", 1, 1, 2500.0, 0, "1"), tr("A1", "TCS", 1, 1, 2502.0, 0, "2"),   # one order, 2 fills
        tr("B1", "INFY", 1, 3, 1185.0, 1, "3"),                                          # no setup -> default stop
        tr("X1", "NIFTY25NOVFUT", 1, 50, 1, 2, "4"),                                     # not -EQ: ignored
        tr("S9", "SBIN", -1, 5, 800.0, 3, "5")]                                          # short: ignored
book[3]["symbol"] = "NSE:NIFTY25NOVFUT"
for m in bs.apply_fills(bs.group_fills(book), cfg): print(" ", m)
pos = {r["symbol"]: r for r in pf.load_positions(INDIA)}
assert set(pos) == {"TCS", "INFY"}, pos
assert pos["TCS"]["shares"] == 2 and pos["TCS"]["entry"] == 2501.0 and pos["TCS"]["strategy"] == "gap_and_go" and pos["TCS"]["stop"] == 2480.0
assert pos["INFY"]["stop"] == round(1185 * 0.99, 2) and pos["INFY"]["target"] == round(1185 + 2 * (1185 - 1173.15), 2) and "note" in pos["INFY"]

# re-reading the same book: nothing new
assert bs.apply_fills(bs.group_fills(book), cfg) == []
# add to TCS with a second order, then partial sell, then full sell over two orders
book += [tr("A2", "TCS", 1, 2, 2510.0, 5, "6")]
book += [tr("S1", "TCS", -1, 3, 2530.0, 20, "7")]
for m in bs.apply_fills(bs.group_fills(book), cfg): print(" ", m)
tcs = next(r for r in pf.load_positions(INDIA) if r["symbol"] == "TCS")
assert tcs["shares"] == 1 and tcs["entry"] == 2505.5, tcs
book += [tr("S2", "TCS", -1, 1, 2540.0, 30, "8")]
for m in bs.apply_fills(bs.group_fills(book), cfg): print(" ", m)
assert not any(r["symbol"] == "TCS" for r in pf.load_positions(INDIA))
closed = pf.load_closed(INDIA); print(closed[["symbol", "shares", "entry", "exit", "pnl", "exit_reason"]].to_string())
assert closed["shares"].sum() == 4 and abs(closed["pnl"].sum() - (3 * 24.5 + 34.5)) < 0.01

# manual record then fyers fill -> linked, not duplicated
pf.add_buy(INDIA, "HDFCBANK", 4, 1700.0, 1690.0, 1720.0, "orb", now + timedelta(minutes=40))
book += [tr("H1", "HDFCBANK", 1, 4, 1700.4, 42, "9")]
for m in bs.apply_fills(bs.group_fills(book), cfg): print(" ", m)
h = [r for r in pf.load_positions(INDIA) if r["symbol"] == "HDFCBANK"]
assert len(h) == 1 and h[0]["entry"] == 1700.4 and h[0]["strategy"] == "orb", h

# risk trade count: TCS (2 orders averaged into one position) + INFY + HDFCBANK = 3
rs = pf.risk_status(INDIA, None, cfg, advice=[])
print("trades today:", rs.trades_today); assert rs.trades_today == 3
print("ALL OK")
