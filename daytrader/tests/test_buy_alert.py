"""A BUY is saved as soon as it's sent, and not sent if the price has left the setup."""
import sys
import types

import pandas as pd

import dtrader.alerts as al
import dtrader.news
from dtrader.markets import US
from dtrader.strategies import get_strategy, Signal

sent, saved = [], []
al.notify = lambda kind, title, body, cfg: sent.append((kind, title, body))
al.load_state = lambda m: {"alerted": [], "positions": []}
al.save_state = lambda st, m: saved.append([d["symbol"] for d in st["positions"]])
dtrader.news.check = lambda sym, m: types.SimpleNamespace(earnings_when=None, fresh=[])
cfg = {"market": US, "active_strategy": "gap_and_go", "risk": {"risk_per_trade": 100, "reward_risk": 2}, "exits": {},
       "strategies": {}, "universe": {}}
day = US.now().date()
sig = lambda sym: Signal(sym, "gap_and_go", pd.Timestamp(f"{day} 09:30", tz=US.tz), 2.10, 1.96, 2.38, "test")


def run(name, price, provider_name="schwab"):
    def quotes(syms):
        if isinstance(price, Exception):
            raise price
        return pd.DataFrame({"last": [price]}, index=syms)

    mon = al.Monitor(types.SimpleNamespace(name=provider_name, quotes=quotes), cfg, get_strategy("gap_and_go", {}))
    sent.clear(), saved.clear()
    mon._open(sig(name), 2.05, day)
    return mon


# price still fine: sent with the live price, and saved straight away
m = run("OK", 2.05)
assert [s[0] for s in sent] == ["BUY"] and "last 2.05" in sent[0][2], sent
assert saved == [["OK"]] and len(m.state["positions"]) == 1, saved

# FTCI: below the stop by the time the odds and news were done
m = run("LATE", 1.90)
assert not sent and not saved and not m.state["positions"]

# ran too far past the entry meanwhile
m = run("RAN", 2.50)
assert not sent and not m.state["positions"]

# price unknown: still alert, so an outage can't silence every BUY
m = run("NOQUOTE", RuntimeError("down"))
assert [s[0] for s in sent] == ["BUY"] and "last" not in sent[0][2].split(" - ")[0]

# Alpaca's free quotes are 15 minutes old, so they can't cancel a BUY
m = run("DELAYED", 1.90, "alpaca")
assert [s[0] for s in sent] == ["BUY"]
print("buy alert OK")
