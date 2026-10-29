"""Markets the scanner supports: session hours, timezone, calendar, data folder.

Each market keeps its own settings, active strategy, scan history, backtest,
tracked trades and alerts. US files stay directly in data/ (as before); other
markets get a subfolder, e.g. data/india/.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class Market:
    key: str
    name: str
    tz: ZoneInfo
    open: time
    close: time
    premarket: time | None     # first extended-hours bar, None = no premarket trading
    currency: str
    calendar: str              # exchange_calendars code
    tz_label: str
    flag: str

    @property
    def data_dir(self) -> Path:
        d = DATA_DIR if self.key == "us" else DATA_DIR / self.key
        d.mkdir(parents=True, exist_ok=True)
        return d

    def path(self, name: str) -> Path:
        return self.data_dir / name

    def now(self) -> datetime:
        return datetime.now(self.tz)

    @property
    def day_start(self) -> time:
        """When a new session's data begins (premarket or the open)."""
        return self.premarket or self.open

    def session_hours(self, day: date) -> tuple[datetime, datetime] | None:
        """Regular-session open/close in local time, None on holidays/weekends."""
        cal = _calendar(self.calendar)
        if cal is None:
            if day.weekday() >= 5:
                return None
            return datetime.combine(day, self.open, self.tz), datetime.combine(day, self.close, self.tz)
        import pandas as pd

        ts = pd.Timestamp(day)
        if not cal.is_session(ts):
            return None
        row = cal.schedule.loc[ts]
        return (row["open"].tz_convert(self.tz).to_pydatetime(),
                row["close"].tz_convert(self.tz).to_pydatetime())

    def sessions_back(self, n: int, include_today: bool = True) -> list[date]:
        """Last n trading sessions up to today (today only once it has started)."""
        import pandas as pd

        cal = _calendar(self.calendar)
        today = self.now().date()
        start = pd.Timestamp(today) - pd.Timedelta(days=n * 2 + 15)
        if cal is None:
            days = [d.date() for d in pd.bdate_range(start, today)]
        else:
            days = [d.date() for d in cal.sessions_in_range(start, pd.Timestamp(today))]
        if days and days[-1] == today and not (include_today and self.now().time() >= self.day_start):
            days = days[:-1]
        return days[-n:]

    def fmt_money(self, v: float) -> str:
        return f"{'-' if v < 0 else '+'}{self.currency}{abs(v):,.0f}"


@lru_cache(maxsize=None)
def _calendar(code: str):
    try:
        import exchange_calendars as xc

        return xc.get_calendar(code)
    except Exception:
        return None


US = Market("us", "US", ZoneInfo("America/New_York"), time(9, 30), time(16, 0), time(4, 0),
            "$", "XNYS", "ET", "🇺🇸")
INDIA = Market("india", "India", ZoneInfo("Asia/Kolkata"), time(9, 15), time(15, 30), None,
               "₹", "XBOM", "IST", "🇮🇳")
MARKETS = {m.key: m for m in (US, INDIA)}


def get_market(key: str | Market | None) -> Market:
    if isinstance(key, Market):
        return key
    key = (key or "us").lower()
    if key in ("in", "nse", "indian"):
        key = "india"
    if key not in MARKETS:
        raise KeyError(f"Unknown market '{key}'. Options: {', '.join(MARKETS)}")
    return MARKETS[key]


def india_fo_symbols() -> list[str]:
    """NSE F&O stocks: fresh from NSE (cached daily), else the bundled list."""
    import json
    import time as _t

    cache = INDIA.path("fo_symbols.json")
    if cache.exists() and cache.stat().st_mtime > _t.time() - 86400:
        return json.loads(cache.read_text())
    syms: list[str] = []
    try:
        import csv
        import io

        import httpx

        r = httpx.get("https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv",
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            idx = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
            for row in list(csv.reader(io.StringIO(r.text)))[1:]:
                s = row[1].strip() if len(row) > 1 else ""
                if s and s.upper() not in idx and s != "Symbol":
                    syms.append(s)
    except Exception:
        pass
    if len(syms) < 100:  # NSE blocked or changed: use the bundled list
        bundled = Path(__file__).with_name("india_fo.txt")
        syms = [l.strip() for l in bundled.read_text().splitlines() if l.strip() and not l.startswith("#")]
    syms = sorted(set(syms))
    cache.write_text(json.dumps(syms))
    return syms
