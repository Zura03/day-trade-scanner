"""Charles Schwab Trader API (real-time). Needs an app at developer.schwab.com.

`./run.sh auth` opens a browser so you can log in to Schwab and approve the app;
the token is then saved to data/schwab_token.json and refreshed automatically
(Schwab forces a fresh login every 7 days). Without that login this never opens
a browser, since the monitor and dashboard run unattended.

Schwab supplies today's quotes and bars. `history` (Alpaca, when its keys are
set) supplies past days and the market-wide backtest, so backtests and track
records don't change, and adds to the movers: Schwab lists only about 20.
Schwab's minute bars start at 7:00 ET, not 4:00.
"""
from __future__ import annotations

import fcntl
import json
import os
import time as _time
from datetime import date, datetime, time, timedelta

import pandas as pd

from ..config import ROOT
from ..markets import DATA_DIR
from .base import BAR_COLS, CACHE_DIR, NY, DataProvider, empty_bars, read_cache

LOGIN_DAYS = 7          # Schwab's limit
PER_MINUTE = 100        # of about 120 allowed, shared by the dashboard and monitor
TODAY_TTL = 55          # seconds today's bars for a stock are reused by either process
CALLS_PATH = DATA_DIR / "schwab_calls.json"


def login_seconds_left(token_path: str) -> float:
    """Time until the saved login expires; 0 when it's missing or expired."""
    for attempt in range(2):
        try:
            made = float(json.loads((ROOT / token_path).read_text()).get("creation_timestamp", 0))
            return max(made + LOGIN_DAYS * 86400 - _time.time(), 0.0)
        except OSError:
            return 0.0
        except ValueError:  # another process is rewriting a refreshed token
            _time.sleep(0.2)
    return 0.0


def take_slot() -> None:
    """Wait for a free slot in the shared per-minute limit."""
    while True:
        with open(CALLS_PATH.with_suffix(".lock"), "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            now = _time.time()
            try:
                recent = [t for t in json.loads(CALLS_PATH.read_text()) if t > now - 60]
            except (OSError, ValueError):
                recent = []
            if len(recent) < PER_MINUTE:
                CALLS_PATH.write_text(json.dumps(recent + [now]))
                return
            wait = recent[0] + 60 - now
        _time.sleep(max(0.05, min(wait, 2.0)))


class SchwabProvider(DataProvider):
    name = "schwab"

    def __init__(self, app_key: str, app_secret: str, callback_url: str, token_path: str,
                 history: DataProvider | None = None, login: bool = False):
        from schwab import auth

        path = str(ROOT / token_path)
        self.client = auth.easy_client(api_key=app_key, app_secret=app_secret, callback_url=callback_url,
                                       token_path=path) if login \
            else auth.client_from_token_file(path, app_key, app_secret)
        (ROOT / token_path).chmod(0o600)  # this login can reach your account
        self.history = history

    def _call(self, fn, *args, **kwargs):
        """One paced request. A refusal for going too fast waits and retries,
        so it can't look like a stock with no data."""
        for attempt in range(3):
            take_slot()
            r = fn(*args, **kwargs)
            if r.status_code != 429:
                return r
            _time.sleep(15 + 15 * attempt)
        raise RuntimeError("Schwab: rate limited")

    def movers(self, limit: int = 50) -> list[str]:
        from schwab.client import Client

        M = Client.Movers
        syms: list[str] = []
        for idx in (M.Index.NASDAQ, M.Index.NYSE, M.Index.EQUITY_ALL):
            for order in (M.SortOrder.PERCENT_CHANGE_UP, M.SortOrder.VOLUME):
                try:
                    r = self._call(self.client.get_movers, idx, sort_order=order)
                    r.raise_for_status()
                    syms += [x["symbol"] for x in r.json().get("screeners", [])]
                except Exception:
                    continue
        try:
            if self.history is not None:
                syms += self.history.movers(limit=limit)
            else:
                from .yf_provider import YFinanceProvider

                syms += YFinanceProvider().movers(limit=limit)
        except Exception:
            pass
        seen, out = set(), []
        for s in syms:
            if s not in seen and s.isalpha():
                seen.add(s)
                out.append(s)
        return out[:limit]

    def quotes(self, symbols: list[str]) -> pd.DataFrame:
        rows = []
        for i in range(0, len(symbols), 100):
            r = self._call(self.client.get_quotes, symbols[i : i + 100])
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

    def intraday(self, symbol: str, day: date) -> pd.DataFrame:
        today = self.market.now().date()
        if day < today:
            return self.history.intraday(symbol, day) if self.history is not None else super().intraday(symbol, day)
        folder = CACHE_DIR / self.name / "today"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{symbol}_{day}.pkl"
        with open(folder / f"{symbol}.lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)  # if the other process is fetching this stock, wait and reuse it
            if path.exists() and path.stat().st_mtime > _time.time() - TODAY_TTL:
                if (shared := read_cache(path)) is not None:
                    return shared
            bars = super().intraday(symbol, day)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            bars.to_pickle(tmp)
            tmp.replace(path)
        return bars

    def all_symbols(self) -> list[str] | None:
        return self.history.all_symbols() if self.history is not None else None

    def daily_many(self, symbols: list[str], days: int = 60) -> dict[str, pd.DataFrame]:
        return self.history.daily_many(symbols, days) if self.history is not None else super().daily_many(symbols, days)

    def intraday_many(self, symbols: list[str], day: date) -> dict[str, pd.DataFrame]:
        return self.history.intraday_many(symbols, day) if self.history is not None \
            else super().intraday_many(symbols, day)

    def _fetch_intraday(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        frames = []
        cur = start
        while cur <= end:  # Schwab caps minute requests at ~10 days
            stop = min(cur + timedelta(days=6), end)
            r = self._call(
                self.client.get_price_history_every_minute,
                symbol,
                start_datetime=datetime.combine(cur, time(4, 0), NY),
                end_datetime=datetime.combine(stop, time(20, 0), NY),
                need_extended_hours_data=True,
            )
            if r.status_code == 200:
                c = r.json().get("candles", [])
                if c:
                    frames.append(pd.DataFrame(c))
            elif r.status_code not in (400, 404):  # those two: no such symbol or no data
                raise RuntimeError(f"Schwab bars {symbol}: HTTP {r.status_code}")
            cur = stop + timedelta(days=1)
        if not frames:
            return empty_bars()
        df = pd.concat(frames)
        df.index = pd.to_datetime(df["datetime"], unit="ms", utc=True).dt.tz_convert(NY)
        df = df[BAR_COLS].astype(float)
        df = df[(df.index.date >= start) & (df.index.date <= end)]  # before 4:00 it returns yesterday's
        return df[~df.index.duplicated()].sort_index()

    def daily(self, symbol: str, days: int = 60) -> pd.DataFrame:
        def fetch(n: int) -> pd.DataFrame:
            r = self._call(self.client.get_price_history_every_day, symbol,
                           start_datetime=datetime.now(NY) - timedelta(days=int(n * 1.6) + 5))
            r.raise_for_status()
            c = r.json().get("candles", [])
            if not c:
                return pd.DataFrame(columns=BAR_COLS)
            df = pd.DataFrame(c)
            df.index = pd.to_datetime(df["datetime"], unit="ms", utc=True).dt.tz_convert(NY).dt.normalize().dt.tz_localize(None)
            return df[BAR_COLS].astype(float)

        return self._cached_daily(symbol, days, fetch)
