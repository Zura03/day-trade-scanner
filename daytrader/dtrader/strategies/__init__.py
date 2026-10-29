"""Strategy registry. To add a strategy: subclass Strategy, add it below."""
from __future__ import annotations

from ..markets import US
from .base import MAX_CHASE_R, Position, Signal, Strategy
from .bull_flag import BullFlag
from .gap_and_go import GapAndGo
from .orb import OpeningRangeBreakout
from .oversold_bounce import OversoldBounce
from .red_to_green import RedToGreen
from .vwap_reclaim import VwapReclaim

REGISTRY: dict[str, type[Strategy]] = {
    c.name: c for c in (GapAndGo, BullFlag, OpeningRangeBreakout, VwapReclaim, RedToGreen, OversoldBounce)
}


def get_strategy(name: str, cfg: dict) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"Unknown strategy '{name}'. Options: {', '.join(REGISTRY)}")
    return REGISTRY[name](
        params=(cfg.get("strategies") or {}).get(name),
        exits=cfg.get("exits"),
        risk=cfg.get("risk"),
        universe=cfg.get("universe"),
        market=cfg.get("market") or US,
    )


def all_strategies(cfg: dict) -> list[Strategy]:
    return [get_strategy(n, cfg) for n in REGISTRY]


__all__ = ["MAX_CHASE_R", "REGISTRY", "Position", "Signal", "Strategy", "get_strategy", "all_strategies"]
