# yfinance caches its last 64 responses, each holding the connection handle of the
# thread that fetched it: release_yahoo must drop them, and the lookups must call it.
import sys
sys.path.insert(0,'.')
from yfinance.data import YfData
from dtrader.data.base import release_yahoo
from dtrader.markets import US

d = YfData()
d.get = lambda url, params=None, timeout=30: object()   # no network
d.cache_get('https://example.invalid/a')
d.cache_get('https://example.invalid/b')
assert d.cache_get.cache_info().currsize == 2
release_yahoo()
assert d.cache_get.cache_info().currsize == 0

# a failed lookup still releases
import tempfile, yfinance as yf
from pathlib import Path
tmp = Path(tempfile.mkdtemp()); US.__class__.data_dir = property(lambda self: tmp)
from dtrader import news
d.cache_get('https://example.invalid/c')
class Boom:
    def __init__(self, *a): pass
    @property
    def calendar(self): raise RuntimeError("no network")
yf.Ticker = Boom
assert news.earnings_date('ZZZZ', US) is None
assert d.cache_get.cache_info().currsize == 0
print('ok')
