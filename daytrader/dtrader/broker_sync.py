"""Record the trades you make in the Fyers app as positions automatically.

Reads today's Fyers tradebook (a user-info API: 100,000 calls/day, separate
from the 5,000/day data budget) and turns new fills into My positions:

- a buy opens a position. If the stock was a buyable setup in the last 90
  minutes, its strategy, stop and target come along; otherwise the stop is
  `default_stop_pct` below your price with a 2R target, flagged for you to check;
- a buy of a stock you already hold from today adds to it (average price);
- a buy you already recorded by hand (same stock, same shares, within 10
  minutes) is linked to that row instead of added twice;
- a sell closes positions of that stock, oldest first, partly if needed.
  Sells with nothing to close (short selling) are ignored: the tool is long-only.

Fills are grouped per order (an order can fill in parts) and the quantity
already handled per order is remembered in data/india/fyers_synced.json, so
re-reading the tradebook never double-counts.
"""
from __future__ import annotations

import fcntl
import json
from dataclasses import dataclass
from datetime import datetime

import httpx

from .markets import INDIA
from . import portfolio

TRADEBOOK = "https://api-t1.fyers.in/api/v3/tradebook"
SYNCED_PATH = INDIA.path("fyers_synced.json")
SETUP_MINUTES = 90


@dataclass
class Fill:
    order: str
    symbol: str
    side: int          # 1 buy, -1 sell
    qty: int
    price: float       # average over the order's fills
    time: datetime


def _parse_time(s: str) -> datetime:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=INDIA.tz)
        except ValueError:
            continue
    return INDIA.now()


def fetch_tradebook(app_id: str, token: str, client: httpx.Client | None = None) -> list[dict]:
    r = (client or httpx).get(TRADEBOOK, headers={"Authorization": f"{app_id}:{token}"}, timeout=20)
    d = r.json()
    if d.get("s") != "ok":
        raise RuntimeError(f"Fyers tradebook: {d.get('message') or r.status_code}")
    return d.get("tradeBook") or []


def group_fills(rows: list[dict]) -> list[Fill]:
    """NSE equity fills, one per order (qty-weighted price), oldest first."""
    orders: dict[str, dict] = {}
    for t in rows:
        sym = str(t.get("symbol", ""))
        if not (sym.startswith("NSE:") and sym.endswith("-EQ")):
            continue
        o = orders.setdefault(str(t["orderNumber"]), {"symbol": sym[4:-3], "side": int(t["side"]),
                                                       "qty": 0, "value": 0.0, "time": None})
        qty = int(t.get("tradedQty") or 0)
        o["qty"] += qty
        o["value"] += float(t.get("tradePrice") or 0) * qty
        when = _parse_time(str(t.get("orderDateTime", "")))
        o["time"] = min(o["time"], when) if o["time"] else when
    fills = [Fill(k, o["symbol"], o["side"], o["qty"], o["value"] / o["qty"], o["time"])
             for k, o in orders.items() if o["qty"] > 0]
    return sorted(fills, key=lambda f: f.time)


def _load_synced() -> dict:
    today = INDIA.now().date().isoformat()
    try:
        d = json.loads(SYNCED_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        d = {}
    return d if d.get("date") == today else {"date": today, "orders": {}}


def _levels(symbol: str, price: float, cfg: dict) -> tuple[str, float, float, str | None]:
    """(strategy, stop, target, note) from a recent setup, else defaults."""
    rr = float(cfg.get("risk", {}).get("reward_risk", 2.0))
    setups = [v for v in portfolio.recent_buyable(INDIA, SETUP_MINUTES).values() if v["symbol"] == symbol]
    for s in setups:  # newest first
        if s["stop"] < price < s["target"]:
            return s["strategy"], s["stop"], s["target"], None
    pct = float((cfg.get("fyers") or {}).get("default_stop_pct", 1.0))
    stop = round(price * (1 - pct / 100), 2)
    return "manual", stop, round(price + rr * (price - stop), 2), \
        f"no matching setup: stop set {pct:g}% below, check it"


def apply_fills(fills: list[Fill], cfg: dict) -> list[str]:
    """Turn new fills into position changes. Returns one message per change."""
    msgs = []
    with open(SYNCED_PATH.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        synced = _load_synced()
        for f in fills:
            new = f.qty - int(synced["orders"].get(f.order, 0))
            if new <= 0:
                continue
            msg = _buy(f, new, cfg) if f.side == 1 else _sell(f, new)
            synced["orders"][f.order] = f.qty
            SYNCED_PATH.write_text(json.dumps(synced))
            if msg:
                msgs.append(msg)
    return msgs


def _buy(f: Fill, qty: int, cfg: dict) -> str:
    today = INDIA.now().date().isoformat()
    rows = [r for r in portfolio.load_positions(INDIA) if r["symbol"] == f.symbol]
    linked = next((r for r in rows if f.order in r.get("orders", [])), None)
    if linked is None:  # recorded by hand already?
        for r in rows:
            mins = abs((datetime.fromisoformat(r["entry_time"]).replace(tzinfo=INDIA.tz) - f.time).total_seconds()) / 60
            if r.get("source") != "fyers" and r["shares"] == f.qty and mins <= 10:
                portfolio.update_position(INDIA, r["id"], entry=round(f.price, 2), source="fyers", orders=[f.order])
                return f"Linked your Fyers buy of {f.qty} {f.symbol} @ {f.price:.2f} to the position you recorded"
        linked = next((r for r in rows if r.get("source") == "fyers" and r["entry_time"].startswith(today)), None)
    if linked is not None:  # add to today's position (or more fills of the same order)
        shares = linked["shares"] + qty
        entry = round((linked["entry"] * linked["shares"] + f.price * qty) / shares, 2)
        portfolio.update_position(INDIA, linked["id"], shares=shares, entry=entry,
                                  orders=sorted(set(linked.get("orders", []) + [f.order])))
        return f"Added {qty} {f.symbol} @ {f.price:.2f}: now {shares} shares, average {entry:.2f}"
    strategy, stop, target, note = _levels(f.symbol, f.price, cfg)
    extra = {"source": "fyers", "orders": [f.order]} | ({"note": note} if note else {})
    portfolio.add_buy(INDIA, f.symbol, qty, round(f.price, 2), stop, target, strategy, f.time, **extra)
    from .strategies import REGISTRY

    title = getattr(REGISTRY.get(strategy), "title", strategy)
    tail = f" ({note})" if note else f" ({title} setup)"
    return f"Recorded your buy: {qty} {f.symbol} @ {f.price:.2f}, stop {stop:.2f}, target {target:.2f}{tail}"


def _sell(f: Fill, qty: int) -> str | None:
    rows = sorted((r for r in portfolio.load_positions(INDIA) if r["symbol"] == f.symbol),
                  key=lambda r: r["entry_time"])
    if not rows:
        return None  # short sale or a position the tool never knew about
    left, pnl, closed = qty, 0.0, 0
    for r in rows:
        if left <= 0:
            break
        n = min(left, r["shares"])
        done = portfolio.record_sell(INDIA, r["id"], round(f.price, 2), f.time, "sold (Fyers)", n)
        pnl += done["pnl"]
        closed += n
        left -= n
    return f"Recorded your sale: {closed} {f.symbol} @ {f.price:.2f}, P&L {INDIA.fmt_money(pnl)}"


def enabled(cfg: dict) -> bool:
    from .data.fyers_provider import fyers_keys

    return cfg["market"].key == "india" and bool(fyers_keys()) and \
        (cfg.get("fyers") or {}).get("sync_trades", True)


def sync(cfg: dict) -> list[str]:
    """Read today's tradebook and record new fills. Needs today's Fyers login."""
    from .data.fyers_provider import fyers_keys, token_valid_today

    keys, token = fyers_keys(), token_valid_today()
    if not (keys and token):
        return []
    return apply_fills(group_fills(fetch_tradebook(keys[0], token)), cfg)
