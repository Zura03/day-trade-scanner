# A dashboard page left open across a code reload holds a Market of the old class.
import sys
sys.path.insert(0,'.')
from types import SimpleNamespace
from dtrader.markets import US, MARKETS, get_market

assert get_market(US) is US
assert get_market('india') is MARKETS['india'] and get_market(None) is US
assert get_market(SimpleNamespace(key='india')) is MARKETS['india']   # old-class instance
assert get_market(SimpleNamespace(key='us')) is US
print('ok')
