"""Record the trades you make at your broker as positions automatically:
Fyers for India, Schwab for the US (schwab_sync.py reads its orders).

Reads today's Fyers tradebook (a user-info API: 100,000 calls/day, separate
from the 5,000/day data budget) or Schwab orders and turns new fills into My
positions:

- a buy opens a position. If the stock was a buyable setup in the last 90
  minutes, its strategy, stop and target come along; otherwise the stop is
  `default_stop_pct` below your price with a 2R target, flagged for you to check;
- a buy of a stock you already hold from today adds to it (average price);
- a buy you already recorded by hand (same stock, same shares, within 10
  minutes) is linked to that row instead of added twice;
- a sell closes positions of that stock, oldest first, partly if needed.
  Sells with nothing to close (short selling) are ignored: the tool is long-only.

Fills are grouped per order (an order can fill in parts) and the quantity
already handled per order is remembered in data/india/fyers_synced.json (or
data/schwab_synced.json), so re-reading the tradebook never double-counts.

It also reads the order book (same API family) to check your stop-losses:
a stock you bought today with no pending stop-loss sell order, a
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

from .markets import INDIA, US, Market
from . import portfolio

TRADEBOOK = "https://api-t1.fyers.in/api/v3/tradebook"
ORDERS = "https://api-t1.fyers.in/api/v3/orders"
SYNCED_PATH = INDIA.path("fyers_synced.json")
SETUP_MINUTES = 90
STOP_GRACE_MINUTES = 2   # time to place the stop-loss after a buy fills
PENDING = {4, 6}         # order status: transit, pending
STOP_TYPES = {3, 4}      # order type: SL-M, SL-L


@dataclass(frozen=True)
class Broker:
    key: str            # its config section, and the `source` of a synced position
    label: str
    market: Market
    stop_order: str     # the stop-loss order to place, as advice
    stop_tail: str = ""


FYERS = Broker("fyers", "Fyers", INDIA, "an SL-M sell", " (Intraday)")
SCHWAB = Broker("schwab", "Schwab", US, "a stop sell")


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


def _synced_path(b: Broker):
    return SYNCED_PATH if b is FYERS else b.market.path(f"{b.key}_synced.json")


def _load_synced(b: Broker = FYERS) -> dict:
    today = b.market.now().date().isoformat()
    try:
        d = json.loads(_synced_path(b).read_text())
    except (OSError, json.JSONDecodeError):
        d = {}
    return d if d.get("date") == today else {"date": today, "orders": {}}


def _px(x: float) -> float:
    return round(x, 4 if x < 1 else 2)  # US stocks under $1 trade in fractions of a cent


def _levels(symbol: str, price: float, cfg: dict, b: Broker = FYERS) -> tuple[str, float, float, str | None]:
    """(strategy, stop, target, note) from a recent setup, else defaults."""
    rr = float(cfg.get("risk", {}).get("reward_risk", 2.0))
    setups = [v for v in portfolio.recent_buyable(b.market, SETUP_MINUTES).values() if v["symbol"] == symbol]
    for s in setups:  # newest first
        if s["stop"] < price < s["target"]:
            return s["strategy"], s["stop"], s["target"], None
    pct = float((cfg.get(b.key) or {}).get("default_stop_pct", 1.0))
    stop = _px(price * (1 - pct / 100))
    return "manual", stop, _px(price + rr * (price - stop)), \
        f"no matching setup: stop set {pct:g}% below, check it"


def apply_fills(fills: list[Fill], cfg: dict, b: Broker = FYERS) -> list[str]:
    """Turn new fills into position changes. Returns one message per change."""
    msgs = []
    path = _synced_path(b)
    with open(path.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        synced = _load_synced(b)
        for f in fills:
            new = f.qty - int(synced["orders"].get(f.order, 0))
            if new <= 0:
                continue
            msg = _buy(f, new, cfg, b) if f.side == 1 else _sell(f, new, b)
            synced["orders"][f.order] = f.qty
            path.write_text(json.dumps(synced))
            if msg:
                msgs.append(msg)
    return msgs


def _buy(f: Fill, qty: int, cfg: dict, b: Broker = FYERS) -> str:
    m = b.market
    today = m.now().date().isoformat()
    rows = [r for r in portfolio.load_positions(m) if r["symbol"] == f.symbol]
    linked = next((r for r in rows if f.order in r.get("orders", [])), None)
    if linked is None:  # recorded by hand already?
        for r in rows:
            mins = abs((datetime.fromisoformat(r["entry_time"]).replace(tzinfo=m.tz) - f.time).total_seconds()) / 60
            if r.get("source") != b.key and r["shares"] == f.qty and mins <= 10:
                portfolio.update_position(m, r["id"], entry=_px(f.price), source=b.key, orders=[f.order])
                return f"Linked your {b.label} buy of {f.qty} {f.symbol} @ {f.price:.2f} to the position you recorded"
        linked = next((r for r in rows if r.get("source") == b.key and r["entry_time"].startswith(today)), None)
    if linked is not None:  # add to today's position (or more fills of the same order)
        shares = linked["shares"] + qty
        entry = _px((linked["entry"] * linked["shares"] + f.price * qty) / shares)
        portfolio.update_position(m, linked["id"], shares=shares, entry=entry,
                                  orders=sorted(set(linked.get("orders", []) + [f.order])))
        return f"Added {qty} {f.symbol} @ {f.price:.2f}: now {shares} shares, average {entry:.2f}"
    strategy, stop, target, note = _levels(f.symbol, f.price, cfg, b)
    extra = {"source": b.key, "orders": [f.order]} | ({"note": note} if note else {})
    portfolio.add_buy(m, f.symbol, qty, _px(f.price), stop, target, strategy, f.time, **extra)
    from .strategies import REGISTRY

    title = getattr(REGISTRY.get(strategy), "title", strategy)
    tail = f" ({note})" if note else f" ({title} setup)"
    return f"Recorded your buy: {qty} {f.symbol} @ {_px(f.price):g}, stop {stop:g}, target {target:g}{tail}"


def _sell(f: Fill, qty: int, b: Broker = FYERS) -> str | None:
    rows = sorted((r for r in portfolio.load_positions(b.market) if r["symbol"] == f.symbol),
                  key=lambda r: r["entry_time"])
    if not rows:
        return None  # short sale or a position the tool never knew about
    left, pnl, closed = qty, 0.0, 0
    for r in rows:
        if left <= 0:
            break
        n = min(left, r["shares"])
        done = portfolio.record_sell(b.market, r["id"], _px(f.price), f.time, f"sold ({b.label})", n)
        pnl += done["pnl"]
        closed += n
        left -= n
    return f"Recorded your sale: {closed} {f.symbol} @ {f.price:.2f}, P&L {b.market.fmt_money(pnl)}"


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
    """Fyers: compare today's tradebook with the pending stop-loss sell orders."""
    return check_stops(fills, _fyers_stops(orders), positions, now)


def _fyers_stops(orders: list[dict]) -> dict[str, dict]:
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
    return stops


def check_stops(fills: list[Fill], stops: dict[str, dict], positions: list[dict], now: datetime,
                b: Broker = FYERS, holdings: dict[str, int] | None = None) -> list[StopIssue]:
    """Compare what you bought today with your pending stop-loss sell orders.
    `positions` = the tool's open rows (for its stop). `holdings` = shares in
    the account where the broker reports them, so a stop on shares you've owned
    for months isn't called a leftover."""
    held: dict[str, int] = {}
    last_buy: dict[str, datetime] = {}
    for f in fills:
        held[f.symbol] = held.get(f.symbol, 0) + f.side * f.qty
        if f.side == 1:
            last_buy[f.symbol] = max(last_buy.get(f.symbol, f.time), f.time)
    if holdings is not None:
        held = {sym: min(qty, holdings.get(sym, 0)) for sym, qty in held.items()}
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
            out.append(StopIssue(f"stop|{day}|{sym}|missing|{qty}", sym, f"No stop-loss in {b.label} for your {qty} {sym}",
                                 f"Place {b.stop_order} for {qty} shares{at}{b.stop_tail}. Without it nothing sells "
                                 "if the price drops while you're away."))
        elif s["qty"] < qty:
            out.append(StopIssue(f"stop|{day}|{sym}|partial|{qty}|{s['qty']}", sym,
                                 f"Stop-loss covers only {s['qty']} of your {qty} {sym}",
                                 f"Change the pending stop-loss to {qty} shares{at}."))
        elif ts and s["trigger"] and s["trigger"] < ts * 0.998:
            out.append(StopIssue(f"stop|{day}|{sym}|below|{s['trigger']}|{ts}", sym,
                                 f"Your {sym} stop-loss is below the plan",
                                 f"{b.label} stop {s['trigger']:.2f}, the tool's stop {ts:.2f}. You'd lose more than "
                                 "planned if it's hit."))
    for sym, s in sorted(stops.items()):
        gone = holdings.get(sym, 0) <= 0 if holdings is not None else held.get(sym, 0) <= 0 and s["products"] - {"CNC"}
        if gone:
            out.append(StopIssue(f"stop|{day}|{sym}|orphan|{','.join(sorted(s['ids']))}", sym,
                                 f"Cancel the leftover {sym} stop-loss",
                                 f"A stop-loss sell for {s['qty']} {sym} is still pending but you hold none{' today' if holdings is None else ''}. "
                                 "If it triggers it sells shares you don't have (a short)."))
    return out


def broker_for(cfg: dict) -> Broker | None:
    """The broker to sync for this market. Schwab needs its login done once."""
    if cfg["market"].key == "india":
        from .data.fyers_provider import fyers_keys

        return FYERS if fyers_keys() and (cfg.get("fyers") or {}).get("sync_trades", True) else None
    from .config import schwab_keys
    from .schwab_sync import token_path

    return SCHWAB if schwab_keys() and token_path(cfg).exists() and \
        (cfg.get("schwab") or {}).get("sync_trades", True) else None


def enabled(cfg: dict) -> bool:
    return broker_for(cfg) is not None


def sync(cfg: dict) -> list[str]:
    """Read today's trades and record new fills. Needs the broker's login."""
    return sync_and_check(cfg, check=False)[0]


def sync_and_check(cfg: dict, check: bool = True) -> tuple[list[str], list[StopIssue]]:
    """Record new fills, then (with `stop_check`) check your stop-losses.
    One tradebook read serves both. Needs the broker's login."""
    if broker_for(cfg) is SCHWAB:
        return _sync_schwab(cfg, check)
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


def _sync_schwab(cfg: dict, check: bool) -> tuple[list[str], list[StopIssue]]:
    from . import schwab_sync

    b, sec = SCHWAB, cfg.get("schwab") or {}
    ignore = {str(s).upper() for s in sec.get("ignore_symbols") or []}
    client = schwab_sync.client(cfg)
    orders = schwab_sync.fetch_orders(client)
    fills = schwab_sync.group_fills(orders, b.market.now().date(), ignore)
    msgs = apply_fills(fills, cfg, b)
    if not (check and sec.get("stop_check", True)):
        return msgs, []
    return msgs, check_stops(fills, schwab_sync.pending_stops(orders, ignore), portfolio.load_positions(b.market),
                             b.market.now(), b, schwab_sync.fetch_holdings(client))
