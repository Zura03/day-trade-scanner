"""Read your Schwab orders and holdings for broker_sync. Read-only: the tool
never places, changes or cancels an order.

Fills come from the execution legs of each order, so only what filled today
counts. Stop orders are read from further back, since a good-till-cancel stop
can be months old, and from inside OCO and bracket orders.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .broker_sync import Fill
from .markets import ROOT, US

LOGIN_DAYS = 7           # Schwab's limit: the login must be repeated after this
ORDER_LOOKBACK_DAYS = 360
ASSETS = {"EQUITY"}      # not options, and not ETFs and funds (COLLECTIVE_INVESTMENT)
STOP_TYPES = {"STOP", "STOP_LIMIT", "TRAILING_STOP", "TRAILING_STOP_LIMIT"}
PENDING = {"WORKING", "QUEUED", "ACCEPTED", "NEW", "PENDING_ACTIVATION", "AWAITING_CONDITION",
           "AWAITING_STOP_CONDITION", "AWAITING_MANUAL_REVIEW", "AWAITING_UR_OUT",
           "AWAITING_RELEASE_TIME", "PENDING_ACKNOWLEDGEMENT", "PENDING_RECALL"}
# AWAITING_PARENT_ORDER isn't pending here: that bracket's buy hasn't filled yet


def token_path(cfg: dict) -> Path:
    return ROOT / (cfg.get("schwab") or {}).get("token_path", "data/schwab_token.json")


def client(cfg: dict):
    """A client from the saved login. Never opens a browser: the monitor and
    dashboard run unattended, so an expired login is an error here."""
    from schwab import auth

    from .config import schwab_keys

    keys, path = schwab_keys(), token_path(cfg)
    age = time.time() - float(json.loads(path.read_text()).get("creation_timestamp", 0))
    if age >= LOGIN_DAYS * 86400:
        raise RuntimeError("Schwab login expired, run ./run.sh auth")
    return auth.client_from_token_file(str(path), keys[0], keys[1])


def _json(r, what: str):
    if r.status_code != 200:
        raise RuntimeError(f"Schwab {what}: HTTP {r.status_code}")
    return r.json()


def fetch_orders(c) -> list[dict]:
    now = datetime.now(timezone.utc)
    return _json(c.get_orders_for_all_linked_accounts(
        from_entered_datetime=now - timedelta(days=ORDER_LOOKBACK_DAYS), to_entered_datetime=now), "orders")


def fetch_holdings(c) -> dict[str, int]:
    from schwab.client import Client

    return holdings(_json(c.get_accounts(fields=[Client.Account.Fields.POSITIONS]), "positions"))


def holdings(accounts: list[dict]) -> dict[str, int]:
    """Long shares per stock across all your accounts."""
    out: dict[str, int] = {}
    for a in accounts:
        for p in a.get("securitiesAccount", {}).get("positions", []):
            sym = p.get("instrument", {}).get("symbol")
            if sym:
                out[sym] = out.get(sym, 0) + int(p.get("longQuantity") or 0)
    return out


def _flatten(orders: list[dict]):
    for o in orders:
        yield o
        yield from _flatten(o.get("childOrderStrategies") or [])


def _leg(o: dict) -> tuple[str, str] | None:
    """(symbol, instruction) of a single-leg stock order, else None."""
    legs = o.get("orderLegCollection") or []
    if len(legs) != 1 or legs[0].get("instrument", {}).get("assetType") not in ASSETS:
        return None
    return legs[0]["instrument"]["symbol"], legs[0].get("instruction", "")


def _time(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").astimezone(US.tz)


def group_fills(orders: list[dict], today: date, ignore: frozenset | set = frozenset()) -> list[Fill]:
    """Today's stock fills, one per order (qty-weighted price), oldest first.
    Short sales and covers are skipped: the tool is long-only."""
    side = {"BUY": 1, "SELL": -1}
    fills = []
    for o in _flatten(orders):
        leg = _leg(o)
        if not leg or leg[1] not in side or leg[0] in ignore:
            continue
        qty, value, first = 0, 0.0, None
        for act in o.get("orderActivityCollection") or []:
            if act.get("activityType") != "EXECUTION":
                continue
            for x in act.get("executionLegs") or []:
                when = _time(x["time"])
                if when.date() != today:
                    continue
                qty += int(x.get("quantity") or 0)
                value += float(x.get("price") or 0) * int(x.get("quantity") or 0)
                first = min(first, when) if first else when
        if qty > 0:
            fills.append(Fill(str(o.get("orderId")), leg[0], side[leg[1]], qty, value / qty, first))
    return sorted(fills, key=lambda f: f.time)


def pending_stops(orders: list[dict], ignore: frozenset | set = frozenset()) -> dict[str, dict]:
    """Stop sell orders still pending, per stock."""
    stops: dict[str, dict] = {}
    for o in _flatten(orders):
        leg = _leg(o)
        if not leg or leg[1] != "SELL" or leg[0] in ignore or o.get("status") not in PENDING \
                or o.get("orderType") not in STOP_TYPES:
            continue
        s = stops.setdefault(leg[0], {"qty": 0, "trigger": None, "ids": [], "products": set()})
        s["qty"] += int(o.get("remainingQuantity") or 0)
        trig = float(o.get("stopPrice") or 0) or None
        if trig:
            s["trigger"] = max(s["trigger"] or 0, trig)
        s["ids"].append(str(o.get("orderId", "")))
    return stops
