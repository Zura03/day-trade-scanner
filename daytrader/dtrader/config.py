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
    cfg = _merge(cfg, load_settings(m))   # dashboard Settings tab overrides config.yaml
    cfg = apply_capital(cfg)
    cfg["market"] = m
    from .strategies import REGISTRY  # late import: strategies import config indirectly

    state = load_state(m)
    if state.get("active_strategy") in REGISTRY:  # ignore stale/renamed names
        cfg["active_strategy"] = state["active_strategy"]
    if cfg.get("active_strategy") not in REGISTRY:
        cfg["active_strategy"] = next(iter(REGISTRY))
    return cfg


# ---- settings edited in the dashboard (per market) ----------------------------
SETTINGS_KEYS = ("capital", "broker", "risk_limits")


def _settings_path(market: str | Market | None):
    return get_market(market).path("settings.json")


def load_settings(market: str | Market | None = None) -> dict[str, Any]:
    p = _settings_path(market)
    if p.exists():
        try:
            d = json.loads(p.read_text())
            return {k: v for k, v in d.items() if k in SETTINGS_KEYS}
        except json.JSONDecodeError:
            pass
    return {}


def save_settings(updates: dict[str, Any], market: str | Market | None = None) -> None:
    cur = load_settings(market)
    merged = _merge(cur, {k: v for k, v in updates.items() if k in SETTINGS_KEYS})
    p = _settings_path(market)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2))
    tmp.replace(p)


def apply_capital(cfg: dict[str, Any]) -> dict[str, Any]:
    """Derive the money settings from `capital.daily` (the amount traded per day):
    risk per trade, position cap, daily loss limit and the highest affordable
    share price are all percentages of it. No `capital` section = unchanged."""
    cap = cfg.get("capital") or {}
    daily = float(cap.get("daily") or 0)
    if daily <= 0:
        return cfg
    risk = dict(cfg.get("risk") or {})
    risk["account_size"] = daily
    risk["risk_per_trade"] = round(daily * float(cap.get("risk_per_trade_pct", 1.0)) / 100, 2)
    risk["max_position_pct"] = float(cap.get("max_position_pct", 100))
    cfg["risk"] = risk
    lim = dict(cfg.get("risk_limits") or {})
    lim["max_daily_loss"] = round(daily * float(cap.get("max_daily_loss_pct", 3.0)) / 100, 2)
    cfg["risk_limits"] = lim
    max_pos = daily * risk["max_position_pct"] / 100
    min_sh = max(int(cap.get("min_shares", 1)), 1)
    u = dict(cfg.get("universe") or {})
    price_cap = max_pos / min_sh
    u["max_price"] = min(float(u.get("max_price", price_cap)), price_cap)
    cfg["universe"] = u
    cfg["derived"] = {"daily_capital": daily, "risk_per_trade": risk["risk_per_trade"], "max_position": max_pos,
                      "max_daily_loss": lim["max_daily_loss"], "max_price": u["max_price"], "min_shares": min_sh}
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
