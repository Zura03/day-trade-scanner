# Fyers futures fills: a long, a sale that closes it and opens a short, a cover; stop checks by side.
import sys, tempfile
sys.path.insert(0,'.')
from datetime import datetime, timedelta
from pathlib import Path
from dtrader.markets import MARKETS, INDIA_FUT
TMP = Path(tempfile.mkdtemp())
def _dir(self):
    d = TMP / self.key
    d.mkdir(exist_ok=True)
    return d
MARKETS["us"].__class__.data_dir = property(_dir)
import dtrader.futures as fu
from dtrader.futures import Contract
fu.contract = lambda m, root: Contract(root, f"NSE:{root}26NOVFUT", f"{root} FUT", 65, 0.1, None)
from dtrader import broker_sync as bs, portfolio

t0 = datetime(2026, 11, 20, 10, 0, tzinfo=INDIA_FUT.tz)
def trade(no, side, qty, px, mins, sym="NSE:NIFTY26NOVFUT"):
    return {"symbol": sym, "orderNumber": no, "side": side, "tradedQty": qty, "tradePrice": px,
            "orderDateTime": (t0 + timedelta(minutes=mins)).strftime("%d-%b-%Y %H:%M:%S")}
book = [trade("1", 1, 65, 25000, 0), trade("2", -1, 130, 25050, 30), trade("3", 1, 65, 25000, 60),
        trade("9", 1, 10, 1300, 5, "NSE:RELIANCE-EQ")]                     # a stock fill: not futures
fills = bs.group_fut_fills(book)
assert [(f.symbol, f.side, f.qty) for f in fills] == [("NIFTY", 1, 65), ("NIFTY", -1, 130), ("NIFTY", 1, 65)]
cfg = {"market": INDIA_FUT, "risk": {"reward_risk": 2.0}, "fyers": {"default_stop_pct": 0.5}}
b = bs.FYERS_FUT
msgs = bs.apply_fills(fills[:1], cfg, b)
pos = portfolio.load_positions(INDIA_FUT)
assert len(pos) == 1 and pos[0]["shares"] == 65 and int(pos[0].get("side", 1)) == 1 and pos[0]["stop"] < 25000
msgs = bs.apply_fills(fills[:2], cfg, b)                                  # sells 130: closes 65, shorts 65
pos = portfolio.load_positions(INDIA_FUT)
closed = portfolio.load_closed(INDIA_FUT)
assert len(closed) == 1 and closed["pnl"].iloc[0] == 50 * 65                # +50 pts on the long
assert len(pos) == 1 and pos[0]["side"] == -1 and pos[0]["entry"] == 25050 and pos[0]["stop"] > 25050
assert "sale" in msgs[0] and "short" in msgs[0]
bs.apply_fills(fills, cfg, b)                                              # buys 65 back: covers
assert portfolio.load_positions(INDIA_FUT) == []
closed = portfolio.load_closed(INDIA_FUT)
assert len(closed) == 2 and closed["pnl"].iloc[1] == 50 * 65                # short 25050 -> 25000
assert bs.apply_fills(fills, cfg, b) == []                                  # re-reading changes nothing

# stops: a short needs a pending BUY stop; a SELL stop with nothing to close is a leftover
short = fills[:2]                                                          # net -65 after these
now = t0 + timedelta(minutes=40)
assert [i.title for i in bs.fut_stop_issues(short, [], [], now)] == ["No stop-loss in Fyers for your NIFTY future"]
buy_stop = {"symbol": "NSE:NIFTY26NOVFUT", "side": 1, "status": 6, "type": 3, "qty": 65, "filledQty": 0, "id": "a"}
assert bs.fut_stop_issues(short, [buy_stop], [], now) == []
sell_stop = {**buy_stop, "side": -1, "id": "b"}
iss = bs.fut_stop_issues(short, [buy_stop, sell_stop], [], now)
assert [i.title for i in iss] == ["Cancel the leftover NIFTY stop-loss"]
assert bs.broker_for({"market": MARKETS["us_fut"]}) is None                 # Schwab futures: not read
print("ok")
