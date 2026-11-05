import sys,json
sys.path.insert(0,'.')
import pandas as pd
import tempfile
from pathlib import Path
from dtrader.markets import US
tmp = Path(tempfile.mkdtemp()); US.__class__.data_dir = property(lambda self: tmp)
from dtrader.config import load_config
from dtrader.markets import US, get_market
from dtrader.portfolio import buyable_today
from dtrader.scanner import buyable_now
u=load_config('us'); i=load_config('india')
assert u['switch_hint']['auto_switch'] is False and i['switch_hint']['auto_switch'] is True
assert len(u['hide_from_buyable'])==9 and i['hide_from_buyable']==[]
assert i['switch_hint']['lookback_days']==u['switch_hint']['lookback_days']  # overlay keeps the rest
from dtrader.strategies import REGISTRY
assert set(u['hide_from_buyable'])<=set(REGISTRY)
today=US.now().date().isoformat()
def row(s,st,status='BUY NOW'): return dict(symbol=s,strategy=st,status=status,score=1,last=2.0,entry=1.9,stop=1.8,target=2.2,shares=1,triggered_at='09:40',trigger_day=today,outcome=None,r_from_entry=0.1)
df=pd.DataFrame([row('A','gap_and_go'),row('B','orb'),row('C','bull_flag'),row('D','stop_hunt')])
H=u['hide_from_buyable']
assert list(buyable_now(df,'gap_and_go',hidden=H).symbol)==['A','C'] or set(buyable_now(df,'gap_and_go',hidden=H).symbol)=={'A','C'}
assert set(buyable_now(df,'orb',hidden=H).symbol)=={'A','B','C'}    # active stays
assert len(buyable_now(df,'gap_and_go'))==4                         # default hides nothing
t=buyable_today('us',df,H); assert 'B' not in set(t.symbol) and {'A','C'}<=set(t.symbol)
assert 'B' in set(buyable_today('us',df).symbol)
print('ok',sorted(t.symbol))
