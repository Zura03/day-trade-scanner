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

It also reads the order book (same API family) to check your stop-losses:
a stock you bought today with no pending stop-loss sell order in Fyers, a
stop that covers only part of the shares, or a stop-loss still pending for a
stock you no longer hold (it could sell shares you don't have). Read-only:
the tool never places, changes or cancels an order.
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
ORDERS = "https://api-t1.fyers.in/api/v3/orders"
SYNCED_PATH = INDIA.path("fyers_synced.json")
SETUP_MINUTES = 90
STOP_GRACE_MINUTES = 2   # time to place the stop-loss after a buy fills
PENDING = {4, 6}         # order status: transit, pending
STOP_TYPES = {3, 4}      # order type: SL-M, SL-L


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


def _fetch(url: str, key: str, app_id: str, token: str, client: httpx.Client | None = None) -> list[dict]:
    r = (client or httpx).get(url, headers={"Authorization": f"{app_id}:{token}"}, timeout=20)
    d = r.json()
    if d.get("s") != "ok":
        raise RuntimeError(f"Fyers {key}: {d.get('message') or r.status_code}")
    return d.get(key) or []


def fetch_tradebook(app_id: str, token: str, client: httpx.Client | None = None) -> list[dict]:
    return _fetch(TRADEBOOK, "tradeBook", app_id, token, client)


def fetch_orderbook(app_id: str, token: str, client: httpx.Client | None = None) -> list[dict]:
    return _fetch(ORDERS, "orderBook", app_id, token, client)


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


# ---- stop-loss check ----------------------------------------------------------
@dataclass
class StopIssue:
    key: str        # stable per problem, so an alert is pushed once
    symbol: str
    title: str
    body: str


def _eq(sym: str) -> str | None:
    return sym[4:-3] if sym.startswith("NSE:") and sym.endswith("-EQ") else None


def stop_issues(fills: list[Fill], orders: list[dict], positions: list[dict], now: datetime) -> list[StopIssue]:
    """Compare what you hold today (from the tradebook) with your pending
    stop-loss sell orders. `positions` = the tool's open rows (for its stop)."""
    held: dict[str, int] = {}
    last_buy: dict[str, datetime] = {}
    for f in fills:
        held[f.symbol] = held.get(f.symbol, 0) + f.side * f.qty
        if f.side == 1:
            last_buy[f.symbol] = max(last_buy.get(f.symbol, f.time), f.time)
    stops: dict[str, dict] = {}
    for o in orders:
        sym = _eq(str(o.get("symbol", "")))
        if not sym or int(o.get("side", 0)) != -1 or int(o.get("status", 0)) not in PENDING \
                or int(o.get("type", 0)) not in STOP_TYPES:
            continue
        left = int(o.get("remainingQuantity") or (int(o.get("qty") or 0) - int(o.get("filledQty") or 0)))
        s = stops.setdefault(sym, {"qty": 0, "trigger": None, "ids": [], "products": set()})
        s["qty"] += left
        trig = float(o.get("stopPrice") or 0) or None
        if trig:
            s["trigger"] = max(s["trigger"] or 0, trig)
        s["ids"].append(str(o.get("id", "")))
        s["products"].add(str(o.get("productType", "")))
    tool_stop = {}
    for r in positions:
        tool_stop[r["symbol"]] = max(tool_stop.get(r["symbol"], 0), float(r["stop"]))

    day = now.date().isoformat()
    out = []
    for sym, qty in sorted(held.items()):
        if qty <= 0 or (now - last_buy.get(sym, now)).total_seconds() < STOP_GRACE_MINUTES * 60:
            continue
        s, ts = stops.get(sym), tool_stop.get(sym)
        at = f" at {ts:.2f}" if ts else ""
        if not s or s["qty"] <= 0:
            out.append(StopIssue(f"stop|{day}|{sym}|missing|{qty}", sym, f"No stop-loss in Fyers for your {qty} {sym}",
                                 f"Place an SL-M sell for {qty} shares{at} (Intraday). Without it nothing sells "
                                 "if the price drops while you're away."))
        elif s["qty"] < qty:
            out.append(StopIssue(f"stop|{day}|{sym}|partial|{qty}|{s['qty']}", sym,
                                 f"Stop-loss covers only {s['qty']} of your {qty} {sym}",
                                 f"Change the pending stop-loss to {qty} shares{at}."))
        elif ts and s["trigger"] and s["trigger"] < ts * 0.998:
            out.append(StopIssue(f"stop|{day}|{sym}|below|{s['trigger']}|{ts}", sym,
                                 f"Your {sym} stop-loss is below the plan",
                                 f"Fyers stop {s['trigger']:.2f}, the tool's stop {ts:.2f}. You'd lose more than "
                                 "planned if it's hit."))
    for sym, s in sorted(stops.items()):
        if held.get(sym, 0) <= 0 and s["products"] - {"CNC"}:
            out.append(StopIssue(f"stop|{day}|{sym}|orphan|{','.join(sorted(s['ids']))}", sym,
                                 f"Cancel the leftover {sym} stop-loss",
                                 f"A stop-loss sell for {s['qty']} {sym} is still pending but you hold none today. "
                                 "If it triggers it sells shares you don't have (a short)."))
    return out


def enabled(cfg: dict) -> bool:
    from .data.fyers_provider import fyers_keys

    return cfg["market"].key == "india" and bool(fyers_keys()) and \
        (cfg.get("fyers") or {}).get("sync_trades", True)


def sync(cfg: dict) -> list[str]:
    """Read today's tradebook and record new fills. Needs today's Fyers login."""
    return sync_and_check(cfg, check=False)[0]


def sync_and_check(cfg: dict, check: bool = True) -> tuple[list[str], list[StopIssue]]:
    """Record new fills, then (with `fyers.stop_check`) check your stop-losses.
    One tradebook read serves both. Needs today's Fyers login."""
    from .data.fyers_provider import fyers_keys, token_valid_today

    keys, token = fyers_keys(), token_valid_today()
    if not (keys and token):
        return [], []
    fills = group_fills(fetch_tradebook(keys[0], token))
    msgs = apply_fills(fills, cfg)
    if not (check and (cfg.get("fyers") or {}).get("stop_check", True)):
        return msgs, []
    orders = fetch_orderbook(keys[0], token)
    return msgs, stop_issues(fills, orders, portfolio.load_positions(INDIA), INDIA.now())
