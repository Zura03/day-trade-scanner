import errno,os,resource,sys,types
sys.path.insert(0,'.')
import dtrader.alerts as al
from dtrader.config import load_config
from dtrader.markets import get_market
cfg=load_config('india'); m=get_market('india')
# 1 limit raised
resource.setrlimit(resource.RLIMIT_NOFILE,(256,resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
al._raise_open_file_limit(); assert resource.getrlimit(resource.RLIMIT_NOFILE)[0]==4096
# 2 notify survives a failing log and still pushes
pushed=[]; real_push,real_path=al.push_ntfy,al.alerts_path
al.push_ntfy=lambda k,t,b,c: pushed.append(t) or True
def bad(_): raise OSError(errno.EMFILE,'Too many open files')
al.alerts_path=bad
al.notify('INFO','x','y',{**cfg,'alerts':{'desktop_notifications':False,'sound':False}})
assert pushed==['x']; al.alerts_path=real_path
# 3 warning after 5 failures, once, then recovery, and EMFILE triggers gc
sent=[]; al.notify=lambda k,t,b,c: sent.append(t)
seq=[OSError(errno.EMFILE,'Too many open files')]*7+[None,None]
class Stop(Exception): pass
class M:
    def __init__(s,p,c): s.p=p; s.state={'positions':[]}; s.cands=[]; s.strategy=types.SimpleNamespace(title='T')
    def tick(s):
        if not seq: raise SystemExit
        e=seq.pop(0)
        if e: raise e
al.Monitor=M; al.in_session=lambda *_:True; al._time=types.SimpleNamespace(time=__import__('time').time,sleep=lambda *_:None)
gcs=[]; al.gc=types.SimpleNamespace(collect=lambda: gcs.append(1))
p=types.SimpleNamespace(name='yfinance_in',session_hours=lambda d:None)
c={**cfg,'data_source':'yfinance'}
try: al.run_monitor(p,c)
except SystemExit: pass
print(sent); assert sent.count('Monitor checks failing')==1 and sent.count('Monitor checks working again')==1 and len(gcs)==7
assert sent.index('Monitor checks failing')<sent.index('Monitor checks working again')
print('monitor OK')
