"""Config + small persistent state (active strategy, open tracked trades).

config.yaml's top level is the US setup; a market section (e.g. `india:`)
overrides any of it for that market. State lives per market
(data/state.json for the US, data/india/state.json for India).
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any

import yaml

from .markets import DATA_DIR, MARKETS, ROOT, Market, get_market  # noqa: F401


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def load_config(market: str | Market | None = None) -> dict[str, Any]:
    m = get_market(market)
    with open(ROOT / "config.yaml") as f:
        raw = yaml.safe_load(f) or {}
    base = {k: v for k, v in raw.items() if k not in MARKETS}
    cfg = _merge(base, raw.get(m.key) or {}) if m.key != "us" else base
    cfg["market"] = m
    state = load_state(m)
    if state.get("active_strategy"):
        cfg["active_strategy"] = state["active_strategy"]
    return cfg


def _state_path(market: str | Market | None):
    return get_market(market).path("state.json")


def load_state(market: str | Market | None = None) -> dict[str, Any]:
    p = _state_path(market)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict[str, Any], market: str | Market | None = None) -> None:
    p = _state_path(market)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(p)


def set_active_strategy(name: str, market: str | Market | None = None) -> None:
    state = load_state(market)
    state["active_strategy"] = name
    save_state(state, market)


def schwab_keys() -> tuple[str, str] | None:
    key, secret = os.environ.get("SCHWAB_APP_KEY"), os.environ.get("SCHWAB_APP_SECRET")
    return (key, secret) if key and secret else None


def alpaca_keys() -> tuple[str, str] | None:
    key, secret = os.environ.get("ALPACA_API_KEY_ID"), os.environ.get("ALPACA_API_SECRET_KEY")
    return (key, secret) if key and secret else None


def kite_keys() -> tuple[str, str] | None:
    key, secret = os.environ.get("KITE_API_KEY"), os.environ.get("KITE_API_SECRET")
    return (key, secret) if key and secret else None
