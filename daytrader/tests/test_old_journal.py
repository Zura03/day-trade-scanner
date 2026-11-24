# Journal rows written before shorts existed have no side: with newer rows that do, they load as longs.
import sys
sys.path.insert(0, '.')
import json, tempfile
from pathlib import Path
import pandas as pd
import dtrader.performance as perf
from dtrader.markets import US, US_FUT

tmp = Path(tempfile.mkdtemp()) / "journal.jsonl"
perf.journal_path = lambda m=US: tmp
old = {"symbol": "AAA", "strategy": "orb", "entry_time": "2026-11-18 10:00:00-05:00", "entry": 10.0, "stop": 9.8,
       "target": 10.4, "initial_stop": 9.8, "shares": 10, "exit_price": 10.4, "r_multiple": 2.0}
cfg = {"market": US, "risk": {}, "broker": None, "slippage": {}, "active_strategy": "orb"}


def load(*rows, market=US, c=cfg):
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return perf.load_journal(market, c)


new = {**old, "symbol": "BBB", "side": 1, "pivot": None}
short = {**old, "symbol": "CCC", "side": -1, "pivot": 10.0, "stop": 10.2, "initial_stop": 10.2, "target": 9.6,
         "exit_price": 9.6}
j = load(old)                       # only old rows
assert j["side"].tolist() == [1]
r_old = j["r"].iloc[0]
j = load(old, new)                  # the mix that crashed the dashboard
assert j["side"].tolist() == [1, 1] and (j["r"] - r_old).abs().max() < 1e-9
j = load(old, new, short, {**old, "symbol": "DDD", "side": None})
assert j["side"].tolist() == [1, 1, -1, 1]
assert j["r"].gt(0).all()           # the short fell to its target: a win
h = perf.strategy_hint(pd.DataFrame(columns=["day", "strategy", "r"]), cfg)
assert h.table is not None and len(h.table)
print("ok")
