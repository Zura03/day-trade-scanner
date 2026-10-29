"""Market data provider interface + shared helpers (caching, float lookup)."""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from ..config import DATA_DIR
from ..markets import US, Market

NY = ZoneInfo("America/New_York")
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
FLOAT_CACHE = DATA_DIR / "float_cache.json"
BAR_COLS = ["open", "high", "low", "close", "volume"]


def now_ny() -> datetime:
    return datetime.now(NY)


def empty_bars(tz: ZoneInfo = NY) -> pd.DataFrame:
    return pd.DataFrame(columns=BAR_COLS, index=pd.DatetimeIndex([], tz=tz))


class DataProvider(ABC):
    name = "base"
    market: Market = US

    # ---- required -----------------------------------------------------
    @abstractmethod
    def movers(self, limit: int = 50) -> list[str]:
        """Symbols currently moving up the most (gainers / volume leaders)."""

    @abstractmethod
    def quotes(self, symbols: list[str]) -> pd.DataFrame:
        """Index=symbol; columns: last, prev_close, volume, avg_volume."""

    @abstractmethod
    def _fetch_intraday(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """1-minute bars (incl. pre/post market where it exists), in market tz."""

    @abstractmethod
    def daily(self, symbol: str, days: int = 60) -> pd.DataFrame:
        """Daily bars, index = date (naive), columns BAR_COLS."""

    # ---- shared -------------------------------------------------------
    def intraday(self, symbol: str, day: date) -> pd.DataFrame:
        """1-min bars for one session. Past days are cached."""
        today = self.market.now().date()
        path = CACHE_DIR / self.name / f"{symbol}_{day.isoformat()}.pkl"
        if day < today and path.exists():
            return pd.read_pickle(path)
        df = self._fetch_intraday(symbol, day, day)
        df = df[df.index.date == day] if len(df) else df
        if day < today and len(df):
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(path)
        return df

    def intraday_range(self, symbol: str, days: list[date]) -> dict[date, pd.DataFrame]:
        """Bars for several sessions, fetching uncached days in one request."""
        out: dict[date, pd.DataFrame] = {}
        missing = []
        today = self.market.now().date()
        for d in days:
            p = CACHE_DIR / self.name / f"{symbol}_{d.isoformat()}.pkl"
            if d < today and p.exists():
                out[d] = pd.read_pickle(p)
            else:
                missing.append(d)
        if missing:
            df = self._fetch_intraday(symbol, min(missing), max(missing))
            for d in missing:
                part = df[df.index.date == d] if len(df) else empty_bars(self.market.tz)
                out[d] = part
                if d < today and len(part):
                    p = CACHE_DIR / self.name / f"{symbol}_{d.isoformat()}.pkl"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    part.to_pickle(p)
        return out

    def session_hours(self, day: date) -> tuple[datetime, datetime] | None:
        """Regular-session open/close for `day` (market tz), None if closed.
        Uses the exchange calendar (holidays, half days)."""
        return self.market.session_hours(day)

    def all_symbols(self) -> list[str] | None:
        """Every tradable stock in the market's universe, if the source can list them (for
        market-wide backtests). None means "not supported"."""
        return None

    def daily_many(self, symbols: list[str], days: int = 60) -> dict[str, pd.DataFrame]:
        out = {}
        for s in symbols:
            try:
                d = self.daily(s, days)
            except Exception:
                continue
            if len(d):
                out[s] = d
        return out

    def intraday_many(self, symbols: list[str], day: date) -> dict[str, pd.DataFrame]:
        """1-min bars for many symbols on one session (cached like intraday)."""
        return {s: self.intraday(s, day) for s in symbols}

    def float_shares(self, symbols: list[str]) -> dict[str, float | None]:
        """Float in shares. Neither Schwab nor price data gives float, so we
        look it up via Yahoo once a week and cache it. US small caps only:
        the India universe (F&O stocks) is all large and liquid."""
        if self.market is not US:
            return {s: None for s in symbols}
        cache = {}
        if FLOAT_CACHE.exists():
            try:
                cache = json.loads(FLOAT_CACHE.read_text())
            except json.JSONDecodeError:
                cache = {}
        week = time.time() - 7 * 86400
        out, dirty = {}, False
        for s in symbols:
            hit = cache.get(s)
            if hit and hit.get("ts", 0) > week:
                out[s] = hit.get("float")
                continue
            val = None
            try:
                import yfinance as yf

                info = yf.Ticker(s).get_info()
                val = info.get("floatShares") or info.get("sharesOutstanding")
            except Exception:
                pass
            cache[s] = {"float": val, "ts": time.time()}
            out[s] = val
            dirty = True
        if dirty:
            FLOAT_CACHE.write_text(json.dumps(cache))
        return out


def trading_days_back(n: int, provider: DataProvider) -> list[date]:
    """Last n completed-or-current trading sessions (exchange calendar)."""
    return provider.market.sessions_back(n)


def to_tz_index(df: pd.DataFrame, tz: ZoneInfo = NY) -> pd.DataFrame:
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(tz)
    return df

