# Futures: contracts and roll, lot sizing with the margin cap, futures costs, config sections.
import sys, tempfile
sys.path.insert(0,'.')
from datetime import date, datetime
from pathlib import Path
import pandas as pd
from dtrader.markets import INDIA, INDIA_FUT, US_FUT, MARKETS, get_market
for m in MARKETS.values():
    m.__class__.data_dir = property(lambda self, t=Path(tempfile.mkdtemp()): t)
import dtrader.futures as fu

def row(sym, root, lot, tick, exp):
    ts = int(datetime(exp.year, exp.month, exp.day, 15, 30, tzinfo=INDIA.tz).timestamp())
    return f"1,{root} FUT,11,{lot},{tick},,0915-1530,2026-11-19,{ts},NSE:{sym},10,11,1,{root},2,-1.0,XX,1,None,0,0.0"
csv = "\n".join([row("NIFTY26NOVFUT", "NIFTY", 65, 0.1, date(2026, 11, 24)),
                 row("NIFTY26DECFUT", "NIFTY", 65, 0.1, date(2026, 12, 29)),
                 row("BANKNIFTY26NOVFUT", "BANKNIFTY", 30, 0.2, date(2026, 11, 24)),
                 "1,NIFTY 25000 CE,14,65,0.05,,x,x,1795428600,NSE:NIFTY26NOV25000CE,10,11,1,NIFTY,2"])
rows = fu.parse_fyers_csv(csv)
assert [r["symbol"] for r in rows] == ["NSE:NIFTY26NOVFUT", "NSE:NIFTY26DECFUT", "NSE:BANKNIFTY26NOVFUT"]   # options left out
c = fu.india_contract("NIFTY", date(2026, 11, 20), rows)
assert c.symbol == "NSE:NIFTY26NOVFUT" and c.lot == 65 and c.expiry == date(2026, 11, 24)
assert fu.india_contract("NIFTY", date(2026, 11, 24), rows).symbol == "NSE:NIFTY26DECFUT"     # rolls on expiry day
assert fu.us_contract("MES").lot == 5 and fu.us_contract("MES").symbol == "/MES"

# sizing: whole lots, capped by margin; stocks unchanged
fu.contract = lambda m, root: fu.india_contract(root, date(2026, 11, 20), rows) if m.region == "india" else fu.us_contract(root)
from dtrader.strategies.base import Signal, Strategy
ts = pd.Timestamp("2026-11-20 10:00", tz=INDIA.tz)
risk = {"risk_per_trade": 2000, "account_size": 1_000_000, "max_position_pct": 100, "futures": {"margin_pct": 0.15}}
st = Strategy(risk=risk, market=INDIA_FUT)
s = Signal("NIFTY", "orb", ts, 25000.0, 24990.0, 25020.0, "x")       # 10 pts x 65 = 650 per lot
assert st.position_size(s) == 3 * 65                                  # 2000 // 650 = 3 lots (margin allows 4)...
st.risk_cfg = {**risk, "account_size": 400000}                        # ...margin 25000*65*0.15 = 243,750 per lot
assert st.position_size(s) == 65                                      # 400,000 covers 1 lot
assert st.size_text(s) == "1 lot (65)"
st.risk_cfg = {**risk, "account_size": 100000}
assert st.position_size(s) == 0 and "margin" in st.size_text(s)
wide = Signal("NIFTY", "orb", ts, 25000.0, 24900.0, 25200.0, "x")    # 100 pts x 65 = 6,500 > 2,000
st.risk_cfg = risk
assert st.position_size(wide) == 0 and "risks" in st.size_text(wide)
us = Strategy(risk={"risk_per_trade": 50, "account_size": 5000, "max_position_pct": 100,
                    "futures": {"margin_per_contract": {"MES": 2500}}}, market=US_FUT)
m = Signal("MES", "orb", ts, 7000.0, 6996.0, 7008.0, "x")             # 4 pts x $5 = $20 per contract
assert us.position_size(m) == 2 * 5 and us.size_text(m) == "2 contracts"   # 50 // 20 = 2, margin allows 2
stock = Strategy(risk={"risk_per_trade": 100, "account_size": 25000, "max_position_pct": 25})
assert stock.position_size(Signal("ABC", "orb", ts, 10.0, 9.5, 11.0, "x")) == 200

# costs
from dtrader.costs import round_trip
c = round_trip("us_fut", "schwab", 70000, contracts=2, symbol="MES")
assert abs(c.total - 2 * 2 * (2.25 + 0.37)) < 1e-9 and c.brokerage == 2 * 2 * 2.25
c = round_trip("india_fut", "fyers", 1_625_000)                       # one NIFTY lot at 25,000
stt = 1_625_000 * 0.0005
assert c.sell_side > stt and abs(c.brokerage - 40) < 1e-9             # Rs 20 per order cap
assert round_trip("india", "fyers", 1_625_000).total < c.total        # futures STT > stock intraday STT

# config: a futures market builds on its region
from dtrader.config import load_config
ic, uc = load_config("india_fut"), load_config("us_fut")
assert ic["broker"] == "fyers" and ic["exits"]["time_exit"] == "15:10"     # from india:
assert ic["universe"]["watchlist"] == ["NIFTY", "BANKNIFTY"] and ic["hide_from_buyable"] == []
assert uc["exits"]["time_exit"] == "15:50" and uc["universe"]["watchlist"] == ["MES", "MNQ"]
assert get_market("india-fut") is INDIA_FUT and INDIA_FUT.region == "india" and US_FUT.futures
# a stock setup as its future: levels moved by the basis, lots from the futures settings
rows.append({"symbol": "NSE:RELIANCE26NOVFUT", "root": "RELIANCE", "lot": 500, "tick": 0.1,
             "expiry": "2026-11-24", "name": "RELIANCE 24 Nov 26 FUT"})
class P:
    def futures_quotes(self, roots): return {"RELIANCE": 1302.0}
setups = pd.DataFrame([{"symbol": "RELIANCE", "strategy": "orb", "side": 1, "last": 1300.0, "entry": 1300.0,
                        "stop": 1298.0, "target": 1304.0},
                       {"symbol": "RELIANCE", "strategy": "orb_short", "side": -1, "last": 1300.0, "entry": 1300.0,
                        "stop": 1302.0, "target": 1296.0}])
fcfg = {"broker": "fyers", "risk": {"risk_per_trade": 2500, "account_size": 1_000_000, "max_position_pct": 100},
        "futures": {"margin_pct": 0.15}}
f = fu.stock_futures(setups, P(), fcfg)
assert list(f["basis"]) == [2.0, 2.0] and list(f["entry"]) == [1302.0, 1302.0] and f["stop"].iloc[0] == 1300.0
assert list(f["lots"]) == [2, 2] and f["size"].iloc[0] == "2 lots (1000)"   # 2 pts x 500 = 1000 per lot
assert f["stop"].iloc[1] == 1304.0 and f["target"].iloc[1] == 1298.0        # the short: stop above
f = fu.stock_futures(setups.head(1), P(), {**fcfg, "risk": {**fcfg["risk"], "risk_per_trade": 500}})
assert f["lots"].iloc[0] == 0 and f["size"].iloc[0].startswith("0: 1 lot risks")
print("ok")
