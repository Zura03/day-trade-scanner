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


def read_cache(path):
    """Load a cached pickle, or None if it's missing or unreadable (e.g. written
    by a different pandas version). Unreadable files are deleted so they get
    refetched instead of failing every run."""
    if not path.exists():
        return None
    try:
        return pd.read_pickle(path)
    except Exception:
        path.unlink(missing_ok=True)
        return None


def finished_bars(df: pd.DataFrame, market: Market) -> pd.DataFrame:
    """Drop the minute that is still forming. Live signals must use the same
    closed bars the backtest uses, or a mid-minute price can fire an entry
    that is gone when the bar closes."""
    if not len(df):
        return df
    cutoff = pd.Timestamp(market.now()) - pd.Timedelta(minutes=1)
    return df[df.index <= cutoff]


def rank_movers(q: pd.DataFrame, limit: int) -> list[str]:
    """Top gainers by % change, the biggest losers (gap-down setups), then the
    most unusual volume (quotes frame)."""
    if q.empty:
        return []
    q = q[(q["prev_close"] > 0) & (q["last"] > 0)].copy()
    q["chg"] = q["last"] / q["prev_close"] - 1
    q["rv"] = q["volume"] / q["avg_volume"].where(q["avg_volume"] > 0)
    top = list(q.sort_values("chg", ascending=False).index[:limit])
    down = [s for s in q[q["chg"] < 0].sort_values("chg").index[:limit // 4] if s not in top]
    active = [s for s in q.sort_values("rv", ascending=False).index[:limit // 2] if s not in top + down]
    return (top[: limit - len(down[:limit // 4])] + down + active)[:limit]


class DataProvider(ABC):
    name = "base"
    market: Market = US
    premarket_volume_known = True   # False when the source reports 0 for premarket volume

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
        if day < today and (cached := read_cache(path)) is not None:
            return cached
        df = self._fetch_intraday(symbol, day, day)
        df = df[df.index.date == day] if len(df) else df
        if day < today and len(df):
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(path)
        return finished_bars(df, self.market) if day == today else df

    def _cached_daily(self, symbol: str, days: int, fetch) -> pd.DataFrame:
        """Daily bars cached per symbol per day. A copy saved during today's
        session holds a partial bar for today, so after the close it is
        refetched once to pick up the finished day."""
        now = self.market.now()
        path = CACHE_DIR / self.name / "daily" / f"{symbol}_{now.date()}.pkl"
        if (d := read_cache(path)) is not None:
            hours = self.market.session_hours(now.date())
            saved = datetime.fromtimestamp(path.stat().st_mtime, self.market.tz)
            stale = hours is not None and saved < hours[1] <= now
            if not stale and len(d) >= min(days, 100):
                return d.tail(days)
        d = fetch(max(days, 60))
        if len(d):  # don't cache a failed fetch for the whole day
            path.parent.mkdir(parents=True, exist_ok=True)
            d.to_pickle(path)
        return d.tail(days)

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


def to_tz_index(df: pd.DataFrame, tz: ZoneInfo = NY) -> pd.DataFrame:
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(tz)
    return df

