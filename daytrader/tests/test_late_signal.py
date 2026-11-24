"""A BUY/SHORT isn't sent when its signal is old: the Mac slept mid-check (BDRX, 35 min late) or the monitor started late."""
import sys
import types

import pandas as pd

import dtrader.alerts as al
import dtrader.news
import dtrader.odds
from dtrader.markets import US, Market
from dtrader.strategies import get_strategy, Signal

sent, saved, logged = [], [], []
al.notify = lambda kind, title, body, cfg: sent.append((kind, title))
al.load_state = lambda m: {"alerted": [], "positions": []}
al.save_state = lambda st, m: saved.append(len(st["positions"]))
dtrader.news.check = lambda sym, m: types.SimpleNamespace(earnings_when=None, fresh=[])
CFG = {"market": US, "active_strategy": "gap_and_go", "risk": {"risk_per_trade": 100, "reward_risk": 2}, "exits": {},
       "strategies": {}, "universe": {}}
T0 = pd.Timestamp("2026-11-24 10:30", tz=US.tz)
clock = [T0]
Market.now = lambda self: clock[0].to_pydatetime().astimezone(self.tz)


def run(minutes, provider="schwab", alerts=None, sleep_during=0, side=1):
    """A 10:30 signal acted on `minutes` after 10:30; `sleep_during` passes while the price is fetched."""
    clock[0] = T0 + pd.Timedelta(minutes=minutes)
    calls = []

    def quotes(syms):
        calls.append(1)
        clock[0] += pd.Timedelta(minutes=sleep_during)
        return pd.DataFrame({"last": [2.05]}, index=syms)

    mon = al.Monitor(types.SimpleNamespace(name=provider, quotes=quotes), {**CFG, "alerts": alerts or {}},
                     get_strategy("gap_and_go", {}))
    sent.clear(), saved.clear()
    stop, target = (1.96, 2.38) if side > 0 else (2.24, 1.82)
    mon._open(Signal("BDRX", "gap_and_go", T0, 2.10, stop, target, "t", side=side), 2.05, T0.date())
    return mon, calls


for ok in (1, 5, 11):               # bar closes 10:31, so 11 min after 10:30 is 10 min old
    m, _ = run(ok)
    assert [s[0] for s in sent] == ["BUY"] and len(m.state["positions"]) == 1, (ok, sent)

m, calls = run(36)                  # old before we start: nothing sent, no paper trade, no price call
assert not sent and not saved and not m.state["positions"] and not calls
assert not m.state["trades_today"]  # and it doesn't use up the day's trade

m, calls = run(2, sleep_during=34)  # fresh at the start, the Mac sleeps mid-check: BDRX
assert calls and not sent and not m.state["positions"]

run(36, side=-1)
assert not sent                     # shorts too
run(36, alerts={"max_signal_age_minutes": 0})
assert len(sent) == 1               # turned off
run(8, alerts={"max_signal_age_minutes": 5})
assert not sent
run(20, "alpaca")                   # 15 minutes behind by design
assert len(sent) == 1
run(36, "alpaca")
assert not sent
print("late signal OK")
