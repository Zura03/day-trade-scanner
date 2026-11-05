import sys,tempfile
sys.path.insert(0,'.')
import numpy as np, pandas as pd
from datetime import date
from dtrader.indicators import add_indicators, build_context
from dtrader.markets import US
from dtrader.scanner import Candidate, analyse, refresh_bars
from dtrader.strategies import get_strategy
from dtrader.config import load_config
day=date(2026,11,4); NY='America/New_York'
def mk(rows):
    idx=[pd.Timestamp(f'{day} {t}',tz=NY) for t,*_ in rows]
    return pd.DataFrame([r[1:] for r in rows],index=idx,columns=['open','high','low','close','volume'],dtype=float)
R=[('09:25',1.95,2.04,1.94,2.035,7e5),('09:26',2.03,2.04,1.99,2.02,5e5),('09:27',2.01,2.09,1.98,2.06,7e5),('09:28',2.06,2.10,1.97,2.05,4e5),('09:29',2.06,2.13,2.04,2.10,6e5),('09:30',2.09,2.12,2.05,2.11,2e6)]
class P:
    market=US; premarket_volume_known=True
    def __init__(s,b): s.b=b
    def intraday(s,sym,d): return s.b
st=get_strategy('gap_and_go',load_config(US))
old=mk(R[:4]); c=Candidate('FTCI',build_context('FTCI',day,old,1.45,1e6,None,market=US,prev_high=1.5,prev_low=1.4),add_indicators(old,US),2.05,1,'movers')
assert c.ctx.premarket_high==2.10
p=P(mk(R))
# what the monitor did before: new bars, old context
stale=Candidate('FTCI',c.ctx,add_indicators(p.b,US),2.11,1,'movers')
assert analyse(st,stale)['signal'] is not None, 'old behaviour should show the false break'
refresh_bars(p,c,day)
assert c.ctx.premarket_high==2.13 and c.last==2.11 and c.ctx.float_shares is None and c.ctx.prev_high==1.5 and c.ctx.avg_volume==1e6
assert analyse(st,c)['signal'] is None
# a real break still fires
R2=R+[('09:31',2.09,2.20,2.08,2.18,2e6)]; p2=P(mk(R2)); refresh_bars(p2,c,day); s=analyse(st,c)['signal']
assert s is not None and s.entry>=2.13, s
# empty bars leave things alone
b4=c.bars; refresh_bars(P(mk(R2).iloc[0:0]),c,day); assert c.bars is b4
print('ok')
