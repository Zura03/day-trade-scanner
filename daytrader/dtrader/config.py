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
SETTINGS_KEYS = ("capital", "broker")


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
    """Replace the given top-level settings (capital, broker, ...) for a market."""
    cur = load_settings(market)
    cur.update({k: v for k, v in updates.items() if k in SETTINGS_KEYS})
    p = _settings_path(market)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=2))
    tmp.replace(p)


# Values derived from daily capital. Each is auto-calculated unless you set it
# manually (capital.manual.<key>); rules use the *final* values above them.
CAPITAL_FIELDS = ("risk_per_trade", "max_position", "max_daily_loss", "max_price",
                  "max_trades_per_day", "max_open_positions")


def capital_values(cap: dict[str, Any], price_ceiling: float | None = None) -> tuple[dict, dict, set]:
    """(final values, auto values, manually set keys) for a `capital` section."""
    daily = float(cap.get("daily") or 0)
    manual = {k: v for k, v in (cap.get("manual") or {}).items() if k in CAPITAL_FIELDS and v is not None}
    final, auto = {}, {}

    def pick(key, value):
        auto[key] = value
        final[key] = float(manual[key]) if key in manual else value

    pick("risk_per_trade", round(daily * float(cap.get("risk_per_trade_pct", 1.0)) / 100, 2))
    pick("max_position", round(daily * float(cap.get("max_position_pct", 100)) / 100, 2))
    pick("max_daily_loss", round(daily * float(cap.get("max_daily_loss_pct", 3.0)) / 100, 2))
    price = final["max_position"] / max(int(cap.get("min_shares", 2)), 1)
    pick("max_price", round(min(price, price_ceiling) if price_ceiling else price, 2))
    pick("max_trades_per_day", max(1, int(round(final["max_daily_loss"] / final["risk_per_trade"])))
         if final["risk_per_trade"] > 0 else 1)
    pick("max_open_positions", max(1, int(daily // final["max_position"])) if final["max_position"] > 0 else 1)
    for k in ("max_trades_per_day", "max_open_positions"):
        final[k] = int(final[k])
    return final, auto, set(manual)


def price_ceiling(market: str | Market | None = None) -> float | None:
    """universe.max_price from config.yaml (before daily-capital derivation)."""
    m = get_market(market)
    with open(ROOT / "config.yaml") as f:
        raw = yaml.safe_load(f) or {}
    u = {**(raw.get("universe") or {}), **(((raw.get(m.key) or {}) if m.key != "us" else {}).get("universe") or {})}
    return float(u["max_price"]) if u.get("max_price") else None


def apply_capital(cfg: dict[str, Any]) -> dict[str, Any]:
    """Derive the money settings from `capital.daily` (the amount traded per day).
    Risk per trade, max position, daily loss limit, max share price, trades per
    day and open positions are auto-calculated from it unless set manually in
    `capital.manual`. No `capital` section = unchanged."""
    cap = cfg.get("capital") or {}
    daily = float(cap.get("daily") or 0)
    if daily <= 0:
        return cfg
    u = dict(cfg.get("universe") or {})
    final, auto, manual = capital_values(cap, price_ceiling=float(u["max_price"]) if "max_price" in u else None)
    risk = dict(cfg.get("risk") or {})
    risk.update({"account_size": daily, "risk_per_trade": final["risk_per_trade"],
                 "max_position_pct": final["max_position"] / daily * 100})
    cfg["risk"] = risk
    lim = dict(cfg.get("risk_limits") or {})
    lim.update({k: final[k] for k in ("max_daily_loss", "max_trades_per_day", "max_open_positions")})
    cfg["risk_limits"] = lim
    u["max_price"] = final["max_price"]
    cfg["universe"] = u
    cfg["derived"] = {"daily_capital": daily, **final, "auto": auto, "manual": sorted(manual),
                      "min_shares": int(cap.get("min_shares", 2))}
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
