"""Free Yahoo Finance data (no key). Intraday may lag; 1-min history ~30 days."""
from __future__ import annotations

import logging
import warnings
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from .base import BAR_COLS, DataProvider, empty_bars, to_ny_index

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", module="yfinance")
warnings.filterwarnings("ignore", message=".*inplace.*")


class YFinanceProvider(DataProvider):
    name = "yfinance"

    def __init__(self):
        self._screen_quotes: dict[str, dict] = {}

    def movers(self, limit: int = 50) -> list[str]:
        syms: list[str] = []
        for q in ("small_cap_gainers", "day_gainers", "most_actives"):
            try:
                res = yf.screen(q, count=100)
            except Exception:
                continue
            for r in res.get("quotes", []):
                syms.append(r["symbol"])
                self._screen_quotes[r["symbol"]] = r
        seen, out = set(), []
        for s in syms:
            if s not in seen and "." not in s and "^" not in s:
                seen.add(s)
                out.append(s)
        return out[:limit]

    def quotes(self, symbols: list[str]) -> pd.DataFrame:
        rows = []
        for s in symbols:
            r = self._screen_quotes.get(s)
            if r and r.get("regularMarketPrice"):
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
                    symbol, start=cur, end=stop, interval="1m", prepost=True,
                    progress=False, auto_adjust=False, multi_level_index=False,
                )
            except Exception:
                df = pd.DataFrame()
            if len(df):
                frames.append(df)
            cur = stop
        if not frames:
            return empty_bars()
        df = pd.concat(frames)
        df.columns = [c.lower() for c in df.columns]
        df = to_ny_index(df[BAR_COLS].dropna())
        return df[~df.index.duplicated()].sort_index()

    def daily(self, symbol: str, days: int = 60) -> pd.DataFrame:
        df = yf.download(
            symbol, period=f"{max(days, 5)}d", interval="1d", progress=False,
            auto_adjust=False, multi_level_index=False,
        )
        if not len(df):
            return pd.DataFrame(columns=BAR_COLS)
        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df[BAR_COLS].dropna()
