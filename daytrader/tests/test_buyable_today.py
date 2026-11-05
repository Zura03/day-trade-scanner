import tempfile, json
from pathlib import Path
import pandas as pd
from dtrader.markets import US
tmp = Path(tempfile.mkdtemp()); US.__class__.data_dir = property(lambda self: tmp)
from dtrader import portfolio as pf
today = US.now().date().isoformat()
assert pf.buyable_today(US, pd.DataFrame()).empty
def row(sym, st, status, t, out=None, day=today, last=5.5):
    return {"symbol": sym, "strategy": st, "status": status, "outcome": out, "triggered_at": t, "trigger_day": day, "entry": 5.0, "stop": 4.5, "target": 6.0, "last": last, "shares": 10}
scan = pd.DataFrame([row("AAA","orb","BUY NOW","09:40"), row("BBB","orb","triggered 09:35","09:35","stop",last=4.0),
  row("OLD","orb","triggered 15:00","15:00","stop",day="2020-01-01"), row("NOPE","orb","qualifies",None,day=None), row("RUN","orb","triggered 09:50","09:50","ran past entry",last=7)])
pf.remember_buyable(US, scan[scan.status=="BUY NOW"])
sv = json.loads(pf._recent_path(US).read_text()); sv[today]["GONE|orb|09:31"] = {**sv[today]["AAA|orb|09:40"], "symbol":"GONE", "triggered_at":"09:31", "last":5.2}
pf._recent_path(US).write_text(json.dumps(sv))
t = pf.buyable_today(US, scan); print(t.to_string())
assert list(t.symbol) == ["RUN","AAA","BBB","GONE"]
n = dict(zip(t.symbol,t.now)); assert n == {"RUN":"Ran past entry","AAA":"Buyable now","BBB":"Hit stop","GONE":"Left the scan"}
assert t.set_index("symbol").loc["BBB","seen"] == "–" and abs(t.set_index("symbol").loc["RUN","past_entry"]-4)<1e-9 and "–" in t.set_index("symbol").loc["AAA","seen"]
# saved only (no scan) and scan only
assert len(pf.buyable_today(US, pd.DataFrame()))==2
pf._recent_path(US).unlink(); assert len(pf.buyable_today(US, scan))==3
print("buyable today OK")
