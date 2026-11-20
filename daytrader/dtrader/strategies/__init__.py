"""Strategy registry. To add a strategy: subclass Strategy, add it below."""
from __future__ import annotations

from ..markets import US
from .base import MAX_CHASE_R, Position, Signal, Strategy
from .bull_flag import BullFlag
from .first_hour_trend import FirstHourTrend
from .gap_and_go import GapAndGo
from .gap_down_bounce import GapDownBounce
from .opening_reversal import OpeningReversal
from .orb import OpeningRangeBreakout
from .orb_retest import OrbRetest
from .oversold_bounce import OversoldBounce
from .red_to_green import RedToGreen
from .rip_and_dip import RipAndDip
from .short import ShortStrategy, short_of
from .stop_hunt import StopHuntReversal
from .vwap_bounce import VwapBounce
from .vwap_reclaim import VwapReclaim

LONG: dict[str, type[Strategy]] = {
    c.name: c for c in (
        GapAndGo, BullFlag, OpeningRangeBreakout, VwapReclaim, RedToGreen, OversoldBounce,
        # from the r/Daytrading "strategies that actually work" list (long side)
        VwapBounce, FirstHourTrend, StopHuntReversal, RipAndDip, GapDownBounce, OpeningReversal, OrbRetest,
    )
}
# every long strategy run upside down (markets with allow_short: true)
SHORT: dict[str, type[Strategy]] = {(s := short_of(c)).name: s for c in LONG.values()}
REGISTRY: dict[str, type[Strategy]] = {**LONG, **SHORT}


def strategy_names(cfg: dict) -> list[str]:
    """The strategies this market uses: the longs, plus the shorts where `allow_short`."""
    return list(REGISTRY) if cfg.get("allow_short") else list(LONG)


def get_strategy(name: str, cfg: dict) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"Unknown strategy '{name}'. Options: {', '.join(REGISTRY)}")
    cls = REGISTRY[name]
    params = cfg.get("strategies") or {}
    base = cls.base_cls.name if issubclass(cls, ShortStrategy) else name
    return cls(
        params=params.get(name) or params.get(base),  # a short uses its long's settings
        exits=cfg.get("exits"),
        risk={**(cfg.get("risk") or {}), "broker": cfg.get("broker"),  # broker: fee check
              "futures": cfg.get("futures")},                           # margin estimates
        universe=cfg.get("universe"),
        market=cfg.get("market") or US,
    )


def all_strategies(cfg: dict) -> list[Strategy]:
    return [get_strategy(n, cfg) for n in strategy_names(cfg)]


__all__ = ["MAX_CHASE_R", "LONG", "REGISTRY", "SHORT", "Position", "ShortStrategy", "Signal", "Strategy",
           "get_strategy", "all_strategies", "strategy_names"]
