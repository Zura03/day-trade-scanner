"""Yahoo's threads leave its timezone cache open until a garbage collection."""
import pandas as pd

import dtrader.data.yf_provider as yp

ran = []
yp.gc.collect = lambda: ran.append(1)
yp.yf.download = lambda *a, **k: pd.DataFrame()
yp.YFinanceIndiaProvider()._daily_batch(["A", "B"], 15)
assert ran, "no garbage collection after a batch download"
print("yahoo open files OK")
