"""Groww Trade API (needs Groww's Trading API subscription, ~₹499/month).

Two ways to authenticate, both set up on Groww Cloud's API Keys page:

  * API key + secret (recommended): GROWW_API_KEY / GROWW_API_SECRET in .env.
    Groww asks you to approve the key once a day on that page; after you tap
    approve, the tool fetches the day's access token itself.
  * TOTP: GROWW_TOTP_TOKEN / GROWW_TOTP_SECRET in .env. No daily approval,
    but anyone with your .env can then trade on your account - keep it safe.

The day's token is cached in data/india/groww_token.json. Groww allows only
150 token requests a day, so while approval is pending we retry at most every
5 minutes.
"""
from __future__ import annotations

import json
import os
import time as _time
from datetime import date, datetime, time, timedelta

import pandas as pd

from ..markets import INDIA, india_fo_symbols
from .base import BAR_COLS, CACHE_DIR, DataProvider, empty_bars

TOKEN_PATH = INDIA.path("groww_token.json")
RETRY_AFTER = 300          # seconds between token attempts while approval is pending
LIVE_GAP = 1 / 9           # live data: 10 requests/second
HIST_GAP = 0.25            # historical: limit not published, stay conservative
QUOTE_TTL = 30             # seconds a quote is reused (movers + scan ask twice)


def groww_credentials() -> dict | None:
    key, secret = os.environ.get("GROWW_API_KEY"), os.environ.get("GROWW_API_SECRET")
    if key and secret:
        return {"mode": "approval", "api_key": key, "secret": secret}
    tkey, tsecret = os.environ.get("GROWW_TOTP_TOKEN"), os.environ.get("GROWW_TOTP_SECRET")
    if tkey and tsecret:
        return {"mode": "totp", "api_key": tkey, "totp_secret": tsecret}
    return None


def _last_reset() -> datetime:
    now = INDIA.now()
    reset = datetime.combine(now.date(), time(6, 0), INDIA.tz)
    return reset if now >= reset else reset - timedelta(days=1)


def _read_cache() -> dict:
    if TOKEN_PATH.exists():
        try:
            return json.loads(TOKEN_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _write_cache(d: dict) -> None:
    TOKEN_PATH.write_text(json.dumps(d))
    TOKEN_PATH.chmod(0o600)


def cached_token() -> str | None:
    """Today's token if we already have one (no network call)."""
    d = _read_cache()
    created = datetime.fromisoformat(d["created"]) if d.get("created") else None
    return d.get("access_token") if created and created >= _last_reset() else None


def groww_token(force: bool = False) -> str | None:
    """Today's access token, fetching one if needed. None if Groww refused
    (usually: today's key approval hasn't been done yet)."""
    if not force and (tok := cached_token()):
        return tok
    creds = groww_credentials()
    if not creds:
        return None
    d = _read_cache()
    if not force and d.get("last_fail", 0) > _time.time() - RETRY_AFTER:
        return None
    from growwapi import GrowwAPI

    try:
        if creds["mode"] == "approval":
            tok = GrowwAPI.get_access_token(api_key=creds["api_key"], secret=creds["secret"])
        else:
            import pyotp

            tok = GrowwAPI.get_access_token(api_key=creds["api_key"],
                                            totp=pyotp.TOTP(creds["totp_secret"]).now())
    except Exception as e:
        _write_cache({"last_fail": _time.time(), "error": str(e)[:300]})
        return None
    if isinstance(tok, dict):  # older SDKs returned the whole payload
        tok = tok.get("token") or tok.get("access_token")
    _write_cache({"access_token": tok, "created": INDIA.now().isoformat(), "mode": creds["mode"]})
    return tok


def last_token_error() -> str | None:
    return _read_cache().get("error")


class GrowwProvider(DataProvider):
    name = "groww"
    market = INDIA

    def __init__(self, token: str):
        from growwapi import GrowwAPI

        self.g = GrowwAPI(token)
        self._last = {"live": 0.0, "hist": 0.0}
        self._quotes: dict[str, tuple[float, dict]] = {}

    # ---- helpers ----------------------------------------------------------
    def _call(self, kind: str, fn, *args, **kw):
        """Throttled call with retry on rate limits; re-auths once on 401."""
        from growwapi.groww.exceptions import GrowwAPIAuthenticationException, GrowwAPIRateLimitException

        gap = LIVE_GAP if kind == "live" else HIST_GAP
        for attempt in range(4):
            wait = gap - (_time.time() - self._last[kind])
            if wait > 0:
                _time.sleep(wait)
            self._last[kind] = _time.time()
            try:
                return fn(*args, **kw)
            except GrowwAPIRateLimitException:
                _time.sleep(1 + attempt * 2)
            except GrowwAPIAuthenticationException:
                tok = groww_token(force=True) if attempt == 0 else None
                if not tok:
                    raise
                from growwapi import GrowwAPI

                self.g = GrowwAPI(tok)
        raise RuntimeError("Groww rate limit: too many retries")

    def _candles(self, symbol: str, start: datetime, end: datetime, interval: str) -> list:
        r = self._call("hist", self.g.get_historical_candles,
                       exchange=self.g.EXCHANGE_NSE, segment=self.g.SEGMENT_CASH,
                       groww_symbol=f"NSE-{symbol}",
                       start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
                       end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
                       candle_interval=interval)
        return (r or {}).get("candles") or []

    @staticmethod
    def _frame(candles: list, daily: bool = False) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame(columns=BAR_COLS) if daily else empty_bars(INDIA.tz)
        df = pd.DataFrame([c[:6] for c in candles], columns=["t", *BAR_COLS])
        t = df["t"]
        idx = (pd.to_datetime(t, unit="s", utc=True).dt.tz_convert(INDIA.tz)
               if pd.api.types.is_numeric_dtype(t) else
               pd.to_datetime(t).dt.tz_localize(INDIA.tz) if pd.to_datetime(t).dt.tz is None
               else pd.to_datetime(t).dt.tz_convert(INDIA.tz))
        df.index = idx.dt.normalize().dt.tz_localize(None) if daily else idx
        df.index.name = None
        df = df[BAR_COLS].astype(float)
        return df[~df.index.duplicated(keep="last")].sort_index()

    def _quote(self, symbol: str) -> dict | None:
        hit = self._quotes.get(symbol)
        if hit and hit[0] > _time.time() - QUOTE_TTL:
            return hit[1]
        try:
            q = self._call("live", self.g.get_quote, trading_symbol=symbol,
                           exchange=self.g.EXCHANGE_NSE, segment=self.g.SEGMENT_CASH)
        except Exception:
            return None
        self._quotes[symbol] = (_time.time(), q or {})
        return q

    # ---- DataProvider -------------------------------------------------------
    def all_symbols(self) -> list[str]:
        return india_fo_symbols()

    def quotes(self, symbols: list[str]) -> pd.DataFrame:
        today = INDIA.now().date()
        rows = []
        for s in symbols:
            q = self._quote(s)
            if not q or not q.get("last_price"):
                continue
            last = float(q["last_price"])
            chg = q.get("day_change")
            prev = last - float(chg) if chg is not None else float((q.get("ohlc") or {}).get("close") or 0)
            d = self.daily(s, 15)
            prior = d[d.index.date < today]
            rows.append({
                "symbol": s, "last": last, "prev_close": prev,
                "volume": float(q.get("volume") or 0),
                "avg_volume": float(prior["volume"].tail(10).mean()) if len(prior) else 0.0,
            })
        return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame(
            columns=["last", "prev_close", "volume", "avg_volume"])

    def movers(self, limit: int = 50) -> list[str]:
        q = self.quotes(self.all_symbols())
        if q.empty:
            return []
        q = q[q["prev_close"] > 0]
        q["chg"] = q["last"] / q["prev_close"] - 1
        q["rv"] = q["volume"] / q["avg_volume"].where(q["avg_volume"] > 0)
        top = list(q.sort_values("chg", ascending=False).index[:limit])
        active = [s for s in q.sort_values("rv", ascending=False).index[:limit // 2] if s not in top]
        return (top + active)[:limit]

    def _fetch_intraday(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        frames, cur = [], start
        while cur <= end:  # 1-minute candles: max 30 days per request
            stop = min(cur + timedelta(days=28), end)
            c = self._candles(symbol, datetime.combine(cur, INDIA.open),
                              datetime.combine(stop, INDIA.close), self.g.CANDLE_INTERVAL_MIN_1)
            frames.append(self._frame(c))
            cur = stop + timedelta(days=1)
        frames = [f for f in frames if len(f)]
        return pd.concat(frames).sort_index() if frames else empty_bars(INDIA.tz)

    def daily(self, symbol: str, days: int = 60) -> pd.DataFrame:
        """Daily bars, cached per symbol for the day."""
        today = INDIA.now().date()
        path = CACHE_DIR / self.name / "daily" / f"{symbol}_{today}.pkl"
        if path.exists():
            d = pd.read_pickle(path)
            if len(d) >= days or len(d) >= 110:
                return d.tail(days)
        now = INDIA.now().replace(tzinfo=None)
        span = min(int(max(days, 60) * 1.6) + 5, 179)  # max 180 days per request
        try:
            c = self._candles(symbol, now - timedelta(days=span), now, self.g.CANDLE_INTERVAL_DAY)
        except Exception:
            c = []
        d = self._frame(c, daily=True)
        if len(d):  # don't cache a failed fetch for the whole day
            path.parent.mkdir(parents=True, exist_ok=True)
            d.to_pickle(path)
        return d.tail(days)

    def daily_many(self, symbols: list[str], days: int = 60) -> dict[str, pd.DataFrame]:
        out = {}
        for s in symbols:
            d = self.daily(s, days)
            if len(d):
                out[s] = d
        return out
