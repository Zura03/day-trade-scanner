"""Charles Schwab Trader API (real-time). Needs an app at developer.schwab.com.

First run opens a browser so you can log in to Schwab and approve the app;
the token is then saved to data/schwab_token.json and refreshed automatically
(Schwab forces a fresh login every 7 days).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd

from ..config import ROOT
from .base import BAR_COLS, NY, DataProvider, empty_bars


class SchwabProvider(DataProvider):
    name = "schwab"

    def __init__(self, app_key: str, app_secret: str, callback_url: str, token_path: str):
        from schwab import auth

        self.client = auth.easy_client(
            api_key=app_key,
            app_secret=app_secret,
            callback_url=callback_url,
            token_path=str(ROOT / token_path),
        )

    def movers(self, limit: int = 50) -> list[str]:
        from schwab.client import Client

        M = Client.Movers
        syms: list[str] = []
        for idx in (M.Index.NASDAQ, M.Index.NYSE, M.Index.EQUITY_ALL):
            for order in (M.SortOrder.PERCENT_CHANGE_UP, M.SortOrder.VOLUME):
                try:
                    r = self.client.get_movers(idx, sort_order=order)
                    r.raise_for_status()
                    syms += [x["symbol"] for x in r.json().get("screeners", [])]
                except Exception:
                    continue
        seen, out = set(), []
        for s in syms:
            if s not in seen and s.isalpha():
                seen.add(s)
                out.append(s)
        return out[:limit]

    def quotes(self, symbols: list[str]) -> pd.DataFrame:
        rows = []
        for i in range(0, len(symbols), 100):
            r = self.client.get_quotes(symbols[i : i + 100])
            r.raise_for_status()
            for sym, d in r.json().items():
                q, f = d.get("quote", {}), d.get("fundamental", {})
                if not q:
                    continue
                rows.append(
                    {
                        "symbol": sym,
                        "last": q.get("lastPrice") or q.get("mark"),
                        "prev_close": q.get("closePrice"),
                        "volume": q.get("totalVolume", 0),
                        "avg_volume": f.get("avg10DaysVolume") or f.get("avg1YearVolume") or 0,
                    }
                )
        return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame(
            columns=["last", "prev_close", "volume", "avg_volume"]
        )

    def _fetch_intraday(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        frames = []
        cur = start
        while cur <= end:  # Schwab caps minute requests at ~10 days
            stop = min(cur + timedelta(days=6), end)
            r = self.client.get_price_history_every_minute(
                symbol,
                start_datetime=datetime.combine(cur, time(4, 0), NY),
                end_datetime=datetime.combine(stop, time(20, 0), NY),
                need_extended_hours_data=True,
            )
            if r.status_code == 200:
                c = r.json().get("candles", [])
                if c:
                    frames.append(pd.DataFrame(c))
            cur = stop + timedelta(days=1)
        if not frames:
            return empty_bars()
        df = pd.concat(frames)
        df.index = pd.to_datetime(df["datetime"], unit="ms", utc=True).dt.tz_convert(NY)
        df = df[BAR_COLS].astype(float)
        return df[~df.index.duplicated()].sort_index()

    def daily(self, symbol: str, days: int = 60) -> pd.DataFrame:
        r = self.client.get_price_history_every_day(
            symbol, start_datetime=datetime.now(NY) - timedelta(days=int(days * 1.6) + 5)
        )
        r.raise_for_status()
        c = r.json().get("candles", [])
        if not c:
            return pd.DataFrame(columns=BAR_COLS)
        df = pd.DataFrame(c)
        df.index = pd.to_datetime(df["datetime"], unit="ms", utc=True).dt.tz_convert(NY).dt.normalize().dt.tz_localize(None)
        return df[BAR_COLS].astype(float).tail(days)
