"""Free Yahoo Finance data (no key). Intraday may lag; 1-min history ~30 days."""
from __future__ import annotations

import logging
import time
import warnings
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from ..markets import INDIA, india_fo_symbols
from .base import BAR_COLS, CACHE_DIR, DataProvider, empty_bars, rank_movers, read_cache, to_tz_index

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", module="yfinance")
warnings.filterwarnings("ignore", message=".*inplace.*")


class YFinanceProvider(DataProvider):
    name = "yfinance"
    premarket_volume_known = False  # Yahoo reports 0 volume on premarket bars
    suffix = ""          # Yahoo ticker suffix (".NS" for NSE)
    prepost = True       # request extended-hours bars

    def _y(self, symbol: str) -> str:
        return symbol + self.suffix

    SCREEN_TTL = 120  # seconds a screener quote is trusted

    def __init__(self):
        self._screen_quotes: dict[str, tuple[float, dict]] = {}
        self._batch_cache: dict[tuple, tuple[float, dict]] = {}

    def movers(self, limit: int = 50) -> list[str]:
        syms: list[str] = []
        for q in ("small_cap_gainers", "day_gainers", "most_actives"):
            try:
                res = yf.screen(q, count=100)
            except Exception:
                continue
            for r in res.get("quotes", []):
                syms.append(r["symbol"])
                self._screen_quotes[r["symbol"]] = (time.time(), r)
        seen, out = set(), []
        for s in syms:
            if s not in seen and "." not in s and "^" not in s:
                seen.add(s)
                out.append(s)
        return out[:limit]

    def quotes(self, symbols: list[str]) -> pd.DataFrame:
        rows = []
        for s in symbols:
            ts, r = self._screen_quotes.get(s, (0.0, None))
            if r and r.get("regularMarketPrice") and ts > time.time() - self.SCREEN_TTL:
                pre = r.get("marketState") == "PRE" and r.get("preMarketPrice")
                last = pre or r["regularMarketPrice"]
                rows.append({
                    "symbol": s, "last": float(last),
                    "prev_close": float(r.get("regularMarketPreviousClose") or 0),
                    "volume": float(r.get("regularMarketVolume") or 0),
                    "avg_volume": float(r.get("averageDailyVolume10Day") or r.get("averageDailyVolume3Month") or 0),
                })
                continue
            try:
                fi = yf.Ticker(s).fast_info
                rows.append({
                    "symbol": s,
                    "last": float(fi.last_price),
                    "prev_close": float(fi.previous_close),
                    "volume": float(fi.last_volume or 0),
                    "avg_volume": float(fi.ten_day_average_volume or 0),
                })
            except Exception:
                continue
        return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame(
            columns=["last", "prev_close", "volume", "avg_volume"]
        )

    def _fetch_intraday(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        # Yahoo serves 1-min bars for the last ~30 days, max 8 days per call.
        frames = []
        cur = start
        while cur <= end:
            stop = min(cur + timedelta(days=7), end + timedelta(days=1))
            try:
                df = yf.download(
                    self._y(symbol), start=cur, end=stop, interval="1m", prepost=self.prepost,
                    progress=False, auto_adjust=False, multi_level_index=False,
                )
            except Exception:
                df = pd.DataFrame()
            if len(df):
                frames.append(df)
            cur = stop
        if not frames:
            return empty_bars(self.market.tz)
        df = pd.concat(frames)
        df.columns = [c.lower() for c in df.columns]
        df = to_tz_index(df[BAR_COLS].dropna(), self.market.tz)
        return df[~df.index.duplicated()].sort_index()

    def daily(self, symbol: str, days: int = 60) -> pd.DataFrame:
        df = yf.download(
            self._y(symbol), period=f"{max(days, 5)}d", interval="1d", progress=False,
            auto_adjust=False, multi_level_index=False,
        )
        if not len(df):
            return pd.DataFrame(columns=BAR_COLS)
        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df[BAR_COLS].dropna()


class YFinanceIndiaProvider(YFinanceProvider):
    """Free NSE data from Yahoo (SYMBOL.NS). Unofficial and may be delayed;
    about 30 days of 1-minute history. Universe = NSE F&O stocks."""

    name = "yfinance_in"
    market = INDIA
    suffix = ".NS"
    prepost = False  # NSE has no continuous premarket session

    def all_symbols(self) -> list[str]:
        return india_fo_symbols()

    def _daily_batch(self, symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
        """Batch daily download; reused for 60 s so movers() and the scan's
        quotes() in the same pass share one download."""
        key = (days,)
        ts, cached = self._batch_cache.get(key, (0.0, {}))
        if ts > time.time() - 60 and all(s in cached for s in symbols):
            return {s: cached[s] for s in symbols}
        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i + 100]
            try:
                df = yf.download([self._y(s) for s in chunk], period=f"{int(days * 1.6) + 5}d",
                                 interval="1d", group_by="ticker", progress=False,
                                 auto_adjust=False, threads=True)
            except Exception:
                continue
            for s in chunk:
                try:
                    d = df[self._y(s)].copy()
                except KeyError:
                    continue
                d.columns = [c.lower() for c in d.columns]
                d.index = pd.to_datetime(d.index).tz_localize(None)
                d = d[BAR_COLS].dropna()
                if len(d):
                    out[s] = d.tail(days)
        merged = {**(cached if ts > time.time() - 60 else {}), **out}
        self._batch_cache[key] = (time.time(), merged)
        return out

    def daily_many(self, symbols: list[str], days: int = 60) -> dict[str, pd.DataFrame]:
        path = CACHE_DIR / self.name / f"daily_many_{self.market.now().date()}_{days}_{len(symbols)}.pkl"
        if (cached := read_cache(path)) is not None:
            return cached
        out = self._daily_batch(symbols, days)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(out, path)
        return out

    def quotes(self, symbols: list[str]) -> pd.DataFrame:
        today = self.market.now().date()
        rows = []
        for s, d in self._daily_batch(symbols, 15).items():
            prior = d[d.index.date < today]
            cur = d[d.index.date == today]
            if not len(prior):
                continue
            rows.append({
                "symbol": s,
                "last": float((cur if len(cur) else prior)["close"].iloc[-1]),
                "prev_close": float(prior["close"].iloc[-1]),
                "volume": float(cur["volume"].iloc[-1]) if len(cur) else 0.0,
                "avg_volume": float(prior["volume"].tail(10).mean()),
            })
        return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame(
            columns=["last", "prev_close", "volume", "avg_volume"])

    def movers(self, limit: int = 50) -> list[str]:
        """No gainers screener for NSE: rank the F&O list by % change."""
        return rank_movers(self.quotes(self.all_symbols()), limit)
