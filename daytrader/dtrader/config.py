"""Config + small persistent state (active strategy, open tracked trades)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
STATE_PATH = DATA_DIR / "state.json"


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


def load_config() -> dict[str, Any]:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f) or {}
    state = load_state()
    if state.get("active_strategy"):
        cfg["active_strategy"] = state["active_strategy"]
    return cfg


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict[str, Any]) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(STATE_PATH)


def set_active_strategy(name: str) -> None:
    state = load_state()
    state["active_strategy"] = name
    save_state(state)


def schwab_keys() -> tuple[str, str] | None:
    key, secret = os.environ.get("SCHWAB_APP_KEY"), os.environ.get("SCHWAB_APP_SECRET")
    return (key, secret) if key and secret else None


def alpaca_keys() -> tuple[str, str] | None:
    key, secret = os.environ.get("ALPACA_API_KEY_ID"), os.environ.get("ALPACA_API_SECRET_KEY")
    return (key, secret) if key and secret else None
