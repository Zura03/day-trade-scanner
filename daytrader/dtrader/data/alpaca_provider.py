"""Alpaca Market Data API (free "Basic" plan works).

On the free plan Alpaca serves full-market SIP data for history, but not the
most recent 15 minutes; live data is only from the IEX exchange (a small
slice of volume, so small-cap IEX bars are thin). With feed "sip_delayed"
(the default) we stitch the two together:

    SIP bars up to 16 minutes ago  +  IEX bars for the last 16 minutes

so history and volume are accurate and only the newest bars are IEX-only.
With a paid plan (Algo Trader Plus) set alpaca.feed to "sip" for real-time.
Alpaca's movers list only resets at the open, so before 9:30 ET we also pull
premarket gainers from Yahoo.
"""
from __future__ import annotations

import json
import time as _time
from datetime import date, datetime, time, timedelta, timezone

import httpx
import pandas as pd

from .base import BAR_COLS, CACHE_DIR, NY, DataProvider, empty_bars, finished_bars, now_ny, read_cache

DATA_URL = "https://data.alpaca.markets"
TRADING_URLS = ("https://paper-api.alpaca.markets", "https://api.alpaca.markets")
LISTED = {"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"}
SIP_DELAY = timedelta(minutes=16)


class AlpacaProvider(DataProvider):
    name = "alpaca"

    def __init__(self, key_id: str, secret: str, feed: str = "sip_delayed"):
        if feed not in ("sip_delayed", "sip", "iex"):
            raise ValueError("alpaca.feed must be sip_delayed, sip or iex")
        self.feed = feed
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self.http = httpx.Client(base_url=DATA_URL, timeout=30, headers=self._headers)
        self._yf = None

    # ---- HTTP -----------------------------------------------------------
    def _get(self, path: str, params: dict) -> dict:
        for attempt in range(5):
            r = self.http.get(path, params=params)
            if r.status_code == 429:  # rate limited (200 req/min on free)
                _time.sleep(2 + attempt * 3)
                continue
            if r.status_code in (401, 403):
                raise RuntimeError(f"Alpaca {r.status_code}: {r.text[:200]} (check keys / plan)")
            r.raise_for_status()
            return r.json()
        raise RuntimeError("Alpaca rate limit: too many retries")

    def _bars(self, symbols: list[str], timeframe: str, start: datetime, end: datetime,
              feed: str) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        if end <= start:
            return out
        params = {
            "symbols": ",".join(symbols), "timeframe": timeframe, "feed": feed,
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "limit": 10000, "adjustment": "split", "sort": "asc",
        }
        while True:
            j = self._get("/v2/stocks/bars", params)
            for sym, bars in (j.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            tok = j.get("next_page_token")
            if not tok:
                return out
            params["page_token"] = tok

    @staticmethod
    def _frame(bars: list[dict], daily: bool = False) -> pd.DataFrame:
        if not bars:
            return empty_bars() if not daily else pd.DataFrame(columns=BAR_COLS)
        df = pd.DataFrame(bars).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        idx = pd.to_datetime(df["t"], utc=True).dt.tz_convert(NY)
        df.index = idx.dt.normalize().dt.tz_localize(None) if daily else idx
        df.index.name = None
        df = df[BAR_COLS].astype(float)
        return df[~df.index.duplicated(keep="last")].sort_index()

    def _sip_end(self, end: datetime) -> datetime:
        return end if self.feed == "sip" else min(end, datetime.now(timezone.utc) - SIP_DELAY)

    def _minute_bars(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
        if self.feed == "iex":
            raw = self._bars(symbols, "1Min", start, end, "iex")
            return {s: self._frame(raw.get(s, [])) for s in symbols}
        sip_end = self._sip_end(end)
        sip = self._bars(symbols, "1Min", start, sip_end, "sip")
        iex = self._bars(symbols, "1Min", max(start, sip_end), end, "iex") if sip_end < end else {}
        out = {}
        for s in symbols:
            a, b = self._frame(sip.get(s, [])), self._frame(iex.get(s, []))
            if len(a) and len(b):
                b = b[b.index > a.index[-1]]
            out[s] = pd.concat([a, b]) if len(b) else a
        return out

    # ---- DataProvider ---------------------------------------------------
    def movers(self, limit: int = 50) -> list[str]:
        syms: list[str] = []
        try:
            j = self._get("/v1beta1/screener/stocks/movers", {"top": 50})
            syms += [m["symbol"] for m in j.get("gainers", [])]
            j = self._get("/v1beta1/screener/stocks/most-actives", {"by": "volume", "top": 100})
            syms += [m["symbol"] for m in j.get("most_actives", [])]
        except Exception:
            pass
        now = now_ny()
        if now.time() < time(9, 30) or not syms:  # Alpaca movers still show yesterday
            syms = self._yahoo().movers(limit=limit) + syms
        seen, out = set(), []
        for s in syms:
            if s not in seen and s.isalpha():
                seen.add(s)
                out.append(s)
        return out[:limit]

    def _yahoo(self):
        if self._yf is None:
            from .yf_provider import YFinanceProvider

            self._yf = YFinanceProvider()
        return self._yf

    def quotes(self, symbols: list[str]) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame(columns=["last", "prev_close", "volume", "avg_volume"])
        now = datetime.now(NY)
        start = now - timedelta(days=25)
        feed = "iex" if self.feed == "iex" else "sip"
        rows = []
        live: dict[str, float] = {}
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i + 100]
            daily = self._bars(chunk, "1Day", start, self._sip_end(now) if feed == "sip" else now, feed)
            try:  # latest price; IEX is real-time on the free plan
                snap_feed = "sip" if self.feed == "sip" else "iex"
                snaps = self._get("/v2/stocks/snapshots", {"symbols": ",".join(chunk), "feed": snap_feed})
                for s, d in snaps.items():
                    t = (d or {}).get("latestTrade") or {}
                    if t.get("p") and pd.Timestamp(t["t"]).tz_convert(NY).date() == now.date():
                        live[s] = float(t["p"])
            except Exception:
                pass
            if now.time() < time(9, 30) and self._yf is not None:
                # thin IEX premarket prints: prefer Yahoo's premarket quote
                yq = self._yf.quotes([s for s in chunk if s in self._yf._screen_quotes])
                for s, r in yq.iterrows():
                    live[s] = float(r["last"])
            today = now.date()
            for s in chunk:
                d = self._frame(daily.get(s, []), daily=True)
                if len(d) < 2:
                    continue
                prior = d[d.index.date < today]
                cur = d[d.index.date == today]
                if not len(prior):
                    continue
                rows.append({
                    "symbol": s,
                    "last": live.get(s) or float((cur if len(cur) else prior)["close"].iloc[-1]),
                    "prev_close": float(prior["close"].iloc[-1]),
                    "volume": float(cur["volume"].iloc[-1]) if len(cur) else 0.0,
                    "avg_volume": float(prior["volume"].tail(10).mean()),
                })
        return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame(
            columns=["last", "prev_close", "volume", "avg_volume"])

    def _fetch_intraday(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        s = datetime.combine(start, time(4, 0), NY)
        e = min(datetime.combine(end, time(20, 0), NY), datetime.now(NY))
        return self._minute_bars([symbol], s, e)[symbol]

    def daily(self, symbol: str, days: int = 60) -> pd.DataFrame:
        now = datetime.now(NY)
        feed = "iex" if self.feed == "iex" else "sip"
        end = self._sip_end(now) if feed == "sip" else now
        raw = self._bars([symbol], "1Day", now - timedelta(days=int(days * 1.6) + 5), end, feed)
        return self._frame(raw.get(symbol, []), daily=True).tail(days)

    def session_hours(self, day: date) -> tuple[datetime, datetime] | None:
        """Exchange calendar: None on holidays, early close on half days."""
        for url in TRADING_URLS:
            try:
                r = httpx.get(f"{url}/v2/calendar", headers=self._headers, timeout=20,
                              params={"start": day.isoformat(), "end": day.isoformat()})
            except httpx.HTTPError:
                continue
            if r.status_code == 200:
                rows = [c for c in r.json() if c.get("date") == day.isoformat()]
                if not rows:
                    return None
                o, c = rows[0]["open"], rows[0]["close"]  # "09:30", "16:00"
                mk = lambda hm: datetime.combine(day, time(int(hm[:2]), int(hm[3:5])), NY)
                return mk(o), mk(c)
        return super().session_hours(day)

    # ---- market-wide backtest universe ------------------------------------
    def all_symbols(self) -> list[str] | None:
        """Active, tradable stocks on the main exchanges (no OTC), cached daily."""
        path = CACHE_DIR / self.name / "assets.json"
        if path.exists() and path.stat().st_mtime > _time.time() - 86400:
            return json.loads(path.read_text())
        assets = None
        for url in TRADING_URLS:  # paper keys use paper-api, live keys api
            r = httpx.get(f"{url}/v2/assets", headers=self._headers, timeout=60,
                          params={"status": "active", "asset_class": "us_equity"})
            if r.status_code == 200:
                assets = r.json()
                break
        if assets is None:
            return None
        syms = sorted(a["symbol"] for a in assets
                      if a.get("tradable") and a.get("exchange") in LISTED and a["symbol"].isalpha())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(syms))
        return syms

    def daily_many(self, symbols: list[str], days: int = 60) -> dict[str, pd.DataFrame]:
        """Daily bars for thousands of symbols, 100 per request, cached daily."""
        path = CACHE_DIR / self.name / f"daily_many_{now_ny().date()}_{days}_{len(symbols)}.pkl"
        if (cached := read_cache(path)) is not None:
            return cached
        now = datetime.now(NY)
        feed = "iex" if self.feed == "iex" else "sip"
        end = self._sip_end(now) if feed == "sip" else now
        start = now - timedelta(days=int(days * 1.6) + 5)
        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(symbols), 100):
            raw = self._bars(symbols[i:i + 100], "1Day", start, end, feed)
            for s, bars in raw.items():
                out[s] = self._frame(bars, daily=True).tail(days)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(out, path)
        return out

    def intraday_many(self, symbols: list[str], day: date) -> dict[str, pd.DataFrame]:
        out, missing = {}, []
        today = now_ny().date()
        for s in symbols:
            p = CACHE_DIR / self.name / f"{s}_{day.isoformat()}.pkl"
            cached = read_cache(p) if day < today else None
            if cached is not None:
                out[s] = cached
            else:
                missing.append(s)
        start = datetime.combine(day, time(4, 0), NY)
        end = min(datetime.combine(day, time(20, 0), NY), datetime.now(NY))
        for i in range(0, len(missing), 50):
            chunk = missing[i:i + 50]
            got = self._minute_bars(chunk, start, end)
            for s in chunk:
                df = got.get(s, empty_bars())
                out[s] = finished_bars(df, self.market) if day == today else df
                if day < today and len(df):
                    p = CACHE_DIR / self.name / f"{s}_{day.isoformat()}.pkl"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    df.to_pickle(p)
        return out
