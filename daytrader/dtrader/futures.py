"""Futures contracts: which one to trade today, its lot size and tick.

The app talks about a future by its root (NIFTY, ES); the data source needs
the contract. India: the front month from Fyers' public contract list
(NSE_FO.csv, fetched once a day), rolling to the next month on expiry day,
when the expiring one stops being worth a day trade. History comes from
Fyers' continuous series (`cont_flag=1`), so the roll doesn't break it.
US: fixed contract specs, and Schwab's root symbol (/ES) picks the front
month itself.

A position is counted in units (lots x lot size, contracts x multiplier), so
P&L, R and costs work the same as for shares; `lots()` turns units back.
"""
from __future__ import annotations

import csv
import io
import json
import time as _time
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from .markets import INDIA, INDIA_FUT, Market

FYERS_FO_CSV = "https://public.fyers.in/sym_details/NSE_FO.csv"


@dataclass(frozen=True)
class Contract:
    root: str            # NIFTY, ES
    symbol: str          # what the data source wants: NSE:NIFTY26NOVFUT, /ES
    name: str            # shown to you: NIFTY 24 Nov 26 FUT, /ES front month
    lot: int             # units per lot: NSE lot size, or the US point multiplier
    tick: float
    expiry: date | None  # None: the data source rolls for us


# US index futures: $ per point and tick (CME contract specs)
US_SPECS = {
    "ES": ("E-mini S&P 500", 50, 0.25), "NQ": ("E-mini Nasdaq-100", 20, 0.25),
    "MES": ("Micro E-mini S&P 500", 5, 0.25), "MNQ": ("Micro E-mini Nasdaq-100", 2, 0.25),
    "RTY": ("E-mini Russell 2000", 50, 0.1), "YM": ("E-mini Dow", 5, 1.0),
}


def _fyers_rows() -> list[dict]:
    """Futures rows of NSE_FO.csv, cached for the day (≈90k lines, one fetch)."""
    path = INDIA_FUT.path("nse_fo_futures.json")
    today = INDIA.now().date().isoformat()
    try:
        d = json.loads(path.read_text())
        if d.get("date") == today:
            return d["rows"]
    except (OSError, ValueError):
        d = {}
    try:
        import httpx

        r = httpx.get(FYERS_FO_CSV, timeout=30)
        r.raise_for_status()
        rows = parse_fyers_csv(r.text)
    except Exception:
        if d.get("rows"):
            return d["rows"]  # yesterday's list still has the live contracts
        raise
    path.write_text(json.dumps({"date": today, "rows": rows}))
    return rows


def parse_fyers_csv(text: str) -> list[dict]:
    """Futures contracts from Fyers' NSE_FO.csv: symbol, root, lot, tick, expiry, name."""
    out = []
    for f in csv.reader(io.StringIO(text)):
        if len(f) < 14 or not f[9].endswith("FUT"):
            continue
        try:
            expiry = datetime.fromtimestamp(int(f[8]), INDIA.tz).date()
            out.append({"symbol": f[9], "root": f[13], "lot": int(float(f[3])), "tick": float(f[4]),
                        "expiry": expiry.isoformat(), "name": f[1]})
        except (ValueError, IndexError):
            continue
    return out


def india_contract(root: str, day: date | None = None, rows: list[dict] | None = None) -> Contract:
    """Front-month future for an NSE root (NIFTY, BANKNIFTY, RELIANCE), rolling to
    the next month on expiry day."""
    day = day or INDIA.now().date()
    rows = rows if rows is not None else _fyers_rows()
    live = sorted((r for r in rows if r["root"] == root and r["expiry"] > day.isoformat()),
                  key=lambda r: r["expiry"])
    if not live:
        raise KeyError(f"No NSE future for {root} after {day}")
    r = live[0]
    return Contract(root, r["symbol"], r["name"], r["lot"], r["tick"], date.fromisoformat(r["expiry"]))


def us_contract(root: str) -> Contract:
    if root not in US_SPECS:
        raise KeyError(f"Unknown US future '{root}'. Options: {', '.join(US_SPECS)}")
    name, mult, tick = US_SPECS[root]
    return Contract(root, f"/{root}", f"/{root} {name}", mult, tick, None)


_CACHE: dict[tuple, tuple[float, Contract]] = {}


def contract(market: Market, root: str) -> Contract:
    """Today's contract for a root in a futures market (cached for an hour)."""
    key = (market.region, root)
    hit = _CACHE.get(key)
    if hit and hit[0] > _time.time() - 3600:
        return hit[1]
    c = india_contract(root) if market.region == "india" else us_contract(root)
    _CACHE[key] = (_time.time(), c)
    return c


def lot_units(market: Market, symbol: str) -> int:
    """Units in one lot/contract (1 for stocks)."""
    if not market.futures:
        return 1
    try:
        return contract(market, symbol).lot
    except Exception:
        return 0  # unknown contract: unsizeable rather than a wrong size


def lots(market: Market, symbol: str, units: float) -> float:
    lot = lot_units(market, symbol)
    return units / lot if lot else 0.0


def qty_text(market: Market, symbol: str, units: float) -> str:
    """'120 sh', '2 lots (130)' on NSE, '3 contracts' on CME."""
    units = int(units or 0)
    if not market.futures:
        return f"{units} sh"
    lot = lot_units(market, symbol)
    n = units // lot if lot else 0
    if market.region == "india":
        return f"{n} lot{'s' if n != 1 else ''} ({units})"
    return f"{n} contract{'s' if n != 1 else ''}"


def stock_futures(rows: pd.DataFrame, provider, cfg_fut: dict) -> pd.DataFrame:
    """NSE stock setups traded as the stock's front-month future: contract, futures
    price, basis (future - stock), the setup's levels shifted by the basis, lots for
    the risk and capital set for India futures, round-trip costs and estimated margin.
    `rows` are scan rows with a signal; the result has one row per setup it could price."""
    from .costs import round_trip

    if rows is None or rows.empty:
        return pd.DataFrame()
    roots = list(dict.fromkeys(rows["symbol"]))
    px = provider.futures_quotes(roots) if hasattr(provider, "futures_quotes") else {}
    risk = cfg_fut.get("risk") or {}
    budget = float(risk.get("risk_per_trade", 0))
    cap = float(risk.get("account_size", 0)) * float(risk.get("max_position_pct", 100)) / 100
    margin_pct = float((cfg_fut.get("futures") or {}).get("margin_pct") or 0)
    out = []
    for r in rows.itertuples():
        try:
            c = contract(INDIA_FUT, r.symbol)
        except Exception:
            continue
        fut = px.get(r.symbol)
        basis = fut - r.last if fut else None
        shift = basis or 0.0
        side = int(getattr(r, "side", 1) or 1)
        entry, stop, target = r.entry + shift, r.stop + shift, r.target + shift
        risk_pts = side * (entry - stop)
        margin = (fut or entry) * c.lot * margin_pct if margin_pct else None
        lots = int(budget // (risk_pts * c.lot)) if risk_pts > 0 and budget else 0
        if margin:
            lots = min(lots, int(cap // margin))
        why = "" if lots else (f"1 lot risks ₹{risk_pts * c.lot:,.0f}, over your ₹{budget:,.0f}"
                               if risk_pts * c.lot > budget else f"needs ~₹{margin or 0:,.0f} margin")
        cost = round_trip("india_fut", cfg_fut.get("broker"), max(lots, 1) * c.lot * entry)
        out.append({"symbol": r.symbol, "strategy": r.strategy, "side": side, "contract": c.name,
                    "fut_last": fut, "basis": round(basis, 2) if basis is not None else None,
                    "entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2),
                    "lot": c.lot, "lots": lots,
                    "size": f"{lots} lot{'s' if lots != 1 else ''} ({lots * c.lot})" if lots else f"0: {why}",
                    "cost_1lot": round(round_trip("india_fut", cfg_fut.get("broker"),
                                                                              c.lot * entry).total, 0),
                    "cost_r": round(cost.total / (max(lots, 1) * c.lot * risk_pts), 2) if risk_pts > 0 else None,
                    "margin_1lot": round(margin, 0) if margin else None})
    return pd.DataFrame(out)
