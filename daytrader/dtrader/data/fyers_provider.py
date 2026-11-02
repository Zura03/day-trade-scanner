"""Fyers API v3 (free with a Fyers account) for NSE data.

Fyers access tokens expire every morning (~6:30 IST), so you log in once a
day with `./run.sh --market india auth`: a browser opens the Fyers login, you
sign in yourself, and Fyers redirects back to a small local page that saves
the day's token to data/india/fyers_token.json. The tool never sees or
stores your Fyers password, PIN or TOTP.

Calls the REST API directly with httpx: the official fyers-apiv3 SDK pins old
aiohttp/requests versions that break the Groww SDK.

Rate limits (free Standard plan, data APIs): 5/second, 50/minute, 5,000/day,
and going over the per-minute limit more than 3 times blocks you for the rest
of the day. So:
- calls are throttled to 3/second and 40/minute;
- calls are counted per day (data/india/fyers_calls.json) and stop at 4,500;
- today's 1-minute bars are refetched only as often as the remaining budget
  allows over the rest of the session (about every 2-3 minutes for 30 stocks);
- today's bars and live quotes are shared on disk between the dashboard and
  the monitor (separate processes), so a stock fetched by one is reused by the
  other and an open dashboard with auto-refresh adds almost no calls;
- bulk daily bars (average volume for ~220 F&O stocks) and past days' minute
  bars beyond the budget come from Yahoo instead.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import time as _time
import webbrowser
from datetime import date, datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pandas as pd

from ..markets import INDIA, india_fo_symbols
from .base import BAR_COLS, CACHE_DIR, DataProvider, empty_bars, rank_movers, read_cache

API = "https://api-t1.fyers.in/api/v3"
DATA_API = "https://api-t1.fyers.in/data"
TOKEN_PATH = INDIA.path("fyers_token.json")
DEFAULT_REDIRECT = "http://127.0.0.1:5011/fyers"
TOKEN_RESET = time(6, 30)
AUTH_ERRORS = {-8, -15, -16, -17}  # expired / invalid token
PER_SECOND, PER_MINUTE = 3, 40
DAILY_BUDGET = 4500        # of the 5,000/day allowed
LIVE_RESERVE = 2500        # past-day history may not eat into this before/during the session
MAX_LIVE_RATE = 30         # today's-bars refetches per minute, at most
CALLS_PATH = INDIA.path("fyers_calls.json")
QUOTE_TTL = 150            # seconds a shared quote snapshot is reused (movers don't need fresher)
SHARED_DIR = CACHE_DIR / "fyers" / "shared"


def fyers_keys() -> tuple[str, str] | None:
    app_id, secret = os.environ.get("FYERS_APP_ID"), os.environ.get("FYERS_SECRET_KEY")
    return (app_id, secret) if app_id and secret else None


def _last_reset() -> datetime:
    now = INDIA.now()
    reset = datetime.combine(now.date(), TOKEN_RESET, INDIA.tz)
    return reset if now >= reset else reset - timedelta(days=1)


def token_valid_today() -> str | None:
    """Saved access token if it was created after this morning's 6:30 IST expiry."""
    if not TOKEN_PATH.exists():
        return None
    try:
        d = json.loads(TOKEN_PATH.read_text())
    except json.JSONDecodeError:
        return None
    if d.get("invalid"):
        return None
    created = datetime.fromisoformat(d.get("created", "1970-01-01T00:00:00+05:30"))
    return d.get("access_token") if created >= _last_reset() else None


def _mark_invalid() -> None:
    """Fyers rejected the token (logged out elsewhere, revoked): ask for a new login."""
    try:
        d = json.loads(TOKEN_PATH.read_text())
        d["invalid"] = True
        TOKEN_PATH.write_text(json.dumps(d))
    except (OSError, json.JSONDecodeError):
        pass


def login_url(app_id: str, redirect_url: str, state: str = "daytrader") -> str:
    q = {"client_id": app_id, "redirect_uri": redirect_url, "response_type": "code", "state": state}
    return f"{API}/generate-authcode?{urlencode(q)}"


def exchange_code(app_id: str, secret: str, auth_code: str, client: httpx.Client | None = None) -> str:
    """Trade the login's auth_code for the day's access token."""
    body = {"grant_type": "authorization_code",
            "appIdHash": hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest(),
            "code": auth_code}
    r = (client or httpx).post(f"{API}/validate-authcode", json=body, timeout=20)
    d = r.json()
    if d.get("s") != "ok" or not d.get("access_token"):
        raise RuntimeError(f"Fyers token exchange failed: {d.get('message') or d}")
    return d["access_token"]


def fyers_login(app_id: str, secret: str, redirect_url: str, timeout: int = 300) -> str:
    """Open Fyers' login page and catch the redirect on 127.0.0.1."""
    u = urlparse(redirect_url)
    if u.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("fyers.redirect_url must point at 127.0.0.1 (set the same URL in your Fyers app)")
    got: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            ok = "auth_code" in q and q.get("s", ["ok"])[0] == "ok"
            if ok:
                got["auth_code"] = q["auth_code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Fyers login done. You can close this tab." if ok else "Login failed or cancelled."
            self.wfile.write(f"<h3 style='font-family:sans-serif'>{msg}</h3>".encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer((u.hostname, u.port or 80), Handler)
    srv.timeout = 2
    print("Opening the Fyers login page. Sign in there; this window waits for the redirect.")
    webbrowser.open(login_url(app_id, redirect_url))
    deadline = _time.time() + timeout
    while "auth_code" not in got and _time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if "auth_code" not in got:
        raise RuntimeError("No login received from Fyers (timed out).")
    token = exchange_code(app_id, secret, got["auth_code"])
    TOKEN_PATH.write_text(json.dumps({"access_token": token, "created": INDIA.now().isoformat()}))
    TOKEN_PATH.chmod(0o600)
    return token


class BudgetExhausted(RuntimeError):
    pass


_LOCK = threading.Lock()


def _read_calls() -> dict:
    try:
        d = json.loads(CALLS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        d = {}
    if d.get("date") != INDIA.now().date().isoformat():
        d = {"date": INDIA.now().date().isoformat(), "count": 0, "recent": []}
    return d


def calls_today() -> int:
    return int(_read_calls().get("count", 0))


def take_call(reserve: int = 0) -> None:
    """Wait for a free slot and count the call. The record lives in a file
    under an flock, so the dashboard and the monitor (separate processes)
    share one limit, as Fyers counts them together."""
    while True:
        with _LOCK, open(CALLS_PATH.with_suffix(".lock"), "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            d = _read_calls()
            if d["count"] + reserve >= DAILY_BUDGET:
                raise BudgetExhausted(f"Fyers daily call budget used ({d['count']}/{DAILY_BUDGET})")
            now = _time.time()
            recent = [t for t in d.get("recent", []) if t > now - 60]
            last_sec = sum(1 for t in recent if t > now - 1)
            if len(recent) < PER_MINUTE and last_sec < PER_SECOND:
                d["recent"] = recent + [now]
                d["count"] += 1
                CALLS_PATH.write_text(json.dumps(d))
                return
            wait = recent[0] + 60 - now if len(recent) >= PER_MINUTE else 0.3
        _time.sleep(max(0.05, min(wait, 2.0)))


def _session_minutes_left() -> float:
    now = INDIA.now()
    hours = INDIA.session_hours(now.date())
    if not hours or now >= hours[1]:
        return 0.0
    return (hours[1] - max(now, hours[0])).total_seconds() / 60


def fyers_symbol(s: str) -> str:
    return f"NSE:{s}-EQ"


class FyersProvider(DataProvider):
    name = "fyers"
    market = INDIA

    def __init__(self, app_id: str, access_token: str, client: httpx.Client | None = None):
        self.http = client or httpx.Client(timeout=20)
        self.http.headers.update({"Authorization": f"{app_id}:{access_token}", "version": "3"})
        self._yahoo = None
        self._today: dict[str, tuple[float, pd.DataFrame]] = {}   # symbol -> (fetched at, bars)
        self._asked: dict[str, float] = {}                         # symbol -> last asked for

    # ---- helpers ----------------------------------------------------------
    def _get(self, path: str, params: dict, reserve: int = 0) -> dict:
        for attempt in range(3):
            take_call(reserve)
            r = self.http.get(f"{DATA_API}{path}", params=params)
            try:
                d = r.json()
            except ValueError:
                d = {}
            code = d.get("code")
            if r.status_code == 401 or code in AUTH_ERRORS:
                _mark_invalid()
                raise RuntimeError("Fyers token expired or invalid: log in with  ./run.sh --market india auth")
            if r.status_code == 429 or code in (429, -429):
                _time.sleep(15 + 15 * attempt)
                continue
            if d.get("s") in ("ok", "no_data"):
                return d
            raise RuntimeError(f"Fyers {path}: {d.get('message') or r.status_code}")
        raise RuntimeError(f"Fyers {path}: rate limited")

    def _history(self, symbol: str, start: datetime, end: datetime, resolution: str, reserve: int = 0) -> list:
        d = self._get("/history", {"symbol": fyers_symbol(symbol), "resolution": resolution, "date_format": "0",
                                   "range_from": str(int(start.timestamp())), "range_to": str(int(end.timestamp())),
                                   "cont_flag": "1"}, reserve)
        return d.get("candles") or []

    @staticmethod
    def _frame(candles: list, daily: bool = False) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame(columns=BAR_COLS) if daily else empty_bars(INDIA.tz)
        df = pd.DataFrame(candles, columns=["t", *BAR_COLS])
        idx = pd.to_datetime(df["t"], unit="s", utc=True).dt.tz_convert(INDIA.tz)
        df.index = idx.dt.normalize().dt.tz_localize(None) if daily else idx
        df.index.name = None
        df = df[BAR_COLS].astype(float)
        return df[~df.index.duplicated(keep="last")].sort_index()

    def _yahoo_provider(self):
        if self._yahoo is None:
            from .yf_provider import YFinanceIndiaProvider

            self._yahoo = YFinanceIndiaProvider()
        return self._yahoo

    def _yahoo_daily(self, symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
        return self._yahoo_provider().daily_many(symbols, days)

    # ---- DataProvider -------------------------------------------------------
    def all_symbols(self) -> list[str]:
        return india_fo_symbols()

    def daily_many(self, symbols: list[str], days: int = 60) -> dict[str, pd.DataFrame]:
        """Symbols already cached today come from Fyers; a big uncached batch
        (the morning's first scan) comes from Yahoo's batch download rather
        than one rate-limited Fyers call per stock."""
        today = INDIA.now().date()
        cached = [s for s in symbols if (CACHE_DIR / self.name / "daily" / f"{s}_{today}.pkl").exists()]
        rest = [s for s in symbols if s not in set(cached)]
        out = super().daily_many(cached, days)
        if len(rest) > 20:
            out.update(self._yahoo_daily(rest, days))
        else:
            out.update(super().daily_many(rest, days))
        return out

    def quotes(self, symbols: list[str]) -> pd.DataFrame:
        cols = ["last", "prev_close", "volume", "avg_volume"]
        if not symbols:
            return pd.DataFrame(columns=cols)
        today = INDIA.now().date()
        avg = {}
        for s, d in self.daily_many(symbols, days=15).items():
            prior = d[d.index.date < today]  # today's bar is partial
            if len(prior):
                avg[s] = float(prior["volume"].tail(10).mean())
        snap = self._shared_quotes(symbols)
        rows = [{"symbol": s, "last": q[1], "prev_close": q[2], "volume": q[3], "avg_volume": avg.get(s, 0.0)}
                for s, q in snap.items()]
        if not rows:
            raise RuntimeError("Fyers returned no prices (token expired or API down)")
        return pd.DataFrame(rows).set_index("symbol")

    def _shared_quotes(self, symbols: list[str]) -> dict[str, list]:
        """{symbol: [fetched at, last, prev_close, volume]}, reusing quotes any
        process fetched in the last QUOTE_TTL seconds and fetching the rest."""
        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        path = SHARED_DIR / f"quotes_{INDIA.now().date()}.json"
        with open(SHARED_DIR / "quotes.lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)  # one process fetches; the other waits and reuses
            if not path.exists():  # first fetch of the day: clear earlier days' shared files
                for old in SHARED_DIR.iterdir():
                    if old.suffix in (".json", ".pkl") and str(INDIA.now().date()) not in old.name:
                        old.unlink(missing_ok=True)
            try:
                snap = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                snap = {}
            now = _time.time()
            stale = [s for s in symbols if s not in snap or snap[s][0] < now - QUOTE_TTL]
            try:
                for i in range(0, len(stale), 50):
                    d = self._get("/quotes", {"symbols": ",".join(fyers_symbol(s) for s in stale[i:i + 50])})
                    for q in d.get("d") or []:
                        v = q.get("v") or {}
                        if q.get("s") == "ok" and v.get("lp"):
                            snap[q["n"].split(":", 1)[1].removesuffix("-EQ")] = [
                                now, float(v["lp"]), float(v.get("prev_close_price") or 0),
                                float(v.get("volume") or 0)]
            except BudgetExhausted:
                pass  # serve the last quotes we have
            finally:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(snap))
                tmp.replace(path)
        return {s: snap[s] for s in symbols if s in snap}

    def movers(self, limit: int = 50) -> list[str]:
        return rank_movers(self.quotes(self.all_symbols()), limit)

    def _refresh_every(self) -> float:
        """Seconds between refetches of one stock's bars today, so the
        stocks being watched fit in the budget left for the session."""
        now = _time.time()
        watched = sum(1 for t in self._asked.values() if t > now - 300) or 1
        left = max(DAILY_BUDGET - calls_today(), 1)
        rate = min(MAX_LIVE_RATE, left / max(_session_minutes_left(), 1.0))
        return max(55.0, 60.0 * watched / rate)

    def intraday(self, symbol: str, day: date) -> pd.DataFrame:
        today = INDIA.now().date()
        if day < today:
            try:
                return super().intraday(symbol, day)
            except BudgetExhausted:
                return self._yahoo_provider().intraday(symbol, day)
        self._asked[symbol] = _time.time()
        every = self._refresh_every()
        hit = self._today.get(symbol)
        if hit and hit[0] > _time.time() - every:
            return hit[1]
        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        path = SHARED_DIR / f"{symbol}_{day}.pkl"  # today's bars fetched by any process
        with open(SHARED_DIR / f"{symbol}.lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)  # if the other process is fetching this stock, wait and reuse it
            if path.exists() and path.stat().st_mtime > _time.time() - every:
                if (shared := read_cache(path)) is not None:
                    self._today[symbol] = (path.stat().st_mtime, shared)
                    return shared
            try:
                bars = super().intraday(symbol, day)
            except BudgetExhausted:
                stale = hit[1] if hit else read_cache(path) if path.exists() else None
                if stale is not None:
                    return stale
                raise
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            bars.to_pickle(tmp)
            tmp.replace(path)
        self._today[symbol] = (_time.time(), bars)
        return bars

    def _fetch_intraday(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        now = INDIA.now()
        hours = INDIA.session_hours(now.date())
        live_ahead = hours is not None and now < hours[1]   # today's session still to come or running
        reserve = LIVE_RESERVE if start < now.date() and live_ahead else 0
        frames, cur = [], start
        while cur <= end:  # 1-minute candles: max 100 days per request
            stop = min(cur + timedelta(days=90), end)
            hi = min(datetime.combine(stop, INDIA.close, INDIA.tz), INDIA.now())
            c = self._history(symbol, datetime.combine(cur, INDIA.open, INDIA.tz), hi, "1", reserve)
            frames.append(self._frame(c))
            cur = stop + timedelta(days=1)
        frames = [f for f in frames if len(f)]
        return pd.concat(frames).sort_index() if frames else empty_bars(INDIA.tz)

    def daily(self, symbol: str, days: int = 60) -> pd.DataFrame:
        def fetch(n: int) -> pd.DataFrame:
            now = INDIA.now()
            start = now - timedelta(days=min(int(n * 1.6) + 5, 365))
            try:
                return self._frame(self._history(symbol, start, now, "D"), daily=True)
            except BudgetExhausted:
                return self._yahoo_provider().daily(symbol, n)

        return self._cached_daily(symbol, days, fetch)
