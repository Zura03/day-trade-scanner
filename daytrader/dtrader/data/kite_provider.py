"""Zerodha Kite Connect (needs the Connect plan, ₹500/month, for market data).

Kite access tokens expire every morning (~6:00 IST), so you log in once a
day with `./run.sh --market india auth`: a browser opens the Zerodha login,
you sign in yourself, and Zerodha redirects back to a small local page that
saves the day's token to data/india/kite_token.json. The tool never sees or
stores your Zerodha password or TOTP.
"""
from __future__ import annotations

import json
import time as _time
import webbrowser
from datetime import date, datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

from ..markets import INDIA, india_fo_symbols
from .base import BAR_COLS, CACHE_DIR, DataProvider, empty_bars, rank_movers

TOKEN_PATH = INDIA.path("kite_token.json")
HIST_GAP = 0.35  # historical API allows ~3 requests/second


def token_valid_today() -> str | None:
    """Saved access token if it was created after this morning's 6:00 IST reset."""
    if not TOKEN_PATH.exists():
        return None
    try:
        d = json.loads(TOKEN_PATH.read_text())
    except json.JSONDecodeError:
        return None
    reset = INDIA.token_reset()
    created = datetime.fromisoformat(d.get("created", "1970-01-01T00:00:00+05:30"))
    return d.get("access_token") if created >= reset else None


def kite_login(api_key: str, api_secret: str, redirect_url: str, timeout: int = 300) -> str:
    """Open Zerodha's login page and catch the redirect on 127.0.0.1."""
    from kiteconnect import KiteConnect

    u = urlparse(redirect_url)
    if u.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("kite.redirect_url must point at 127.0.0.1 (set the same URL in your Kite app)")
    got: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            ok = q.get("status", [""])[0] == "success" and "request_token" in q
            if ok:
                got["request_token"] = q["request_token"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Zerodha login done. You can close this tab." if ok else "Login failed or cancelled."
            self.wfile.write(f"<h3 style='font-family:sans-serif'>{msg}</h3>".encode())

        def log_message(self, *a):
            pass

    kite = KiteConnect(api_key=api_key)
    srv = HTTPServer((u.hostname, u.port or 80), Handler)
    srv.timeout = 2
    print("Opening the Zerodha login page. Sign in there; this window waits for the redirect.")
    webbrowser.open(kite.login_url())
    deadline = _time.time() + timeout
    while "request_token" not in got and _time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if "request_token" not in got:
        raise RuntimeError("No login received from Zerodha (timed out).")
    sess = kite.generate_session(got["request_token"], api_secret=api_secret)
    TOKEN_PATH.write_text(json.dumps({"access_token": sess["access_token"],
                                      "created": INDIA.now().isoformat(),
                                      "user": sess.get("user_id")}))
    TOKEN_PATH.chmod(0o600)
    return sess["access_token"]


class KiteProvider(DataProvider):
    name = "kite"
    market = INDIA

    def __init__(self, api_key: str, access_token: str):
        from kiteconnect import KiteConnect

        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self._tokens: dict[str, int] | None = None
        self._last_hist = 0.0

    # ---- helpers ----------------------------------------------------------
    def _instrument_tokens(self) -> dict[str, int]:
        if self._tokens is None:
            path = CACHE_DIR / self.name / f"instruments_{INDIA.now().date()}.json"
            if path.exists():
                self._tokens = json.loads(path.read_text())
            else:
                rows = self.kite.instruments("NSE")
                self._tokens = {r["tradingsymbol"]: r["instrument_token"] for r in rows
                                if r.get("instrument_type") == "EQ" and r.get("segment") == "NSE"}
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(self._tokens))
        return self._tokens

    def _hist(self, symbol: str, start: datetime, end: datetime, interval: str) -> list[dict]:
        tok = self._instrument_tokens().get(symbol)
        if tok is None:
            return []
        wait = HIST_GAP - (_time.time() - self._last_hist)
        if wait > 0:
            _time.sleep(wait)
        self._last_hist = _time.time()
        for attempt in range(3):
            try:
                return self.kite.historical_data(tok, start, end, interval)
            except Exception as e:  # rate limit / transient
                if "Too many requests" in str(e) and attempt < 2:
                    _time.sleep(1 + attempt)
                    continue
                raise
        return []

    @staticmethod
    def _frame(rows: list[dict], daily: bool = False) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=BAR_COLS) if daily else empty_bars(INDIA.tz)
        df = pd.DataFrame(rows)
        idx = pd.to_datetime(df["date"])
        idx = idx.dt.tz_localize(INDIA.tz) if idx.dt.tz is None else idx.dt.tz_convert(INDIA.tz)
        df.index = idx.dt.normalize().dt.tz_localize(None) if daily else idx
        df.index.name = None
        df = df[BAR_COLS].astype(float)
        return df[~df.index.duplicated(keep="last")].sort_index()

    # ---- DataProvider -------------------------------------------------------
    def all_symbols(self) -> list[str]:
        tokens = self._instrument_tokens()
        return [s for s in india_fo_symbols() if s in tokens]

    def quotes(self, symbols: list[str]) -> pd.DataFrame:
        rows = []
        today = INDIA.now().date()
        avg = {}
        for s, d in self.daily_many(symbols, days=15).items():
            prior = d[d.index.date < today]  # today's cached bar is partial
            if len(prior):
                avg[s] = float(prior["volume"].tail(10).mean())
        for i in range(0, len(symbols), 400):
            chunk = symbols[i:i + 400]
            q = self.kite.quote([f"NSE:{s}" for s in chunk])
            for key, d in q.items():
                s = key.split(":", 1)[1]
                rows.append({
                    "symbol": s,
                    "last": float(d.get("last_price") or 0),
                    "prev_close": float((d.get("ohlc") or {}).get("close") or 0),
                    "volume": float(d.get("volume") or 0),
                    "avg_volume": avg.get(s, 0.0),
                })
        return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame(
            columns=["last", "prev_close", "volume", "avg_volume"])

    def movers(self, limit: int = 50) -> list[str]:
        return rank_movers(self.quotes(self.all_symbols()), limit)

    def _fetch_intraday(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        frames, cur = [], start
        while cur <= end:  # Kite caps minute requests at 60 days
            stop = min(cur + timedelta(days=55), end)
            rows = self._hist(symbol, datetime.combine(cur, time(9, 0)),
                              datetime.combine(stop, time(15, 59)), "minute")
            frames.append(self._frame(rows))
            cur = stop + timedelta(days=1)
        frames = [f for f in frames if len(f)]
        return pd.concat(frames).sort_index() if frames else empty_bars(INDIA.tz)

    def daily(self, symbol: str, days: int = 60) -> pd.DataFrame:
        def fetch(n: int) -> pd.DataFrame:
            now = INDIA.now().replace(tzinfo=None)
            return self._frame(self._hist(symbol, now - timedelta(days=int(n * 1.6) + 5), now, "day"), daily=True)

        return self._cached_daily(symbol, days, fetch)
