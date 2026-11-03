"""Strategy-switch hint: is the active strategy still working lately?

Combines recent backtest trades with live trades from the alert journal
(live results count double, since they reflect what you could actually take).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .markets import US, Market, get_market


def journal_path(market: Market = US):
    return market.path("journal.jsonl")


def load_journal(market: str | Market | None = None) -> pd.DataFrame:
    path = journal_path(get_market(market))
    if not path.exists():
        return pd.DataFrame()
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if not rows:
        return pd.DataFrame()
    m = get_market(market)
    j = pd.DataFrame(rows)
    # a crash between journaling and saving state can replay a close: keep one
    j = j.drop_duplicates(subset=["symbol", "strategy", "entry_time"], keep="last")
    j["entry_time"] = m.parse_times(j["entry_time"])
    j["day"] = j["entry_time"].dt.date
    j["r"] = j["r_multiple"]
    j["source"] = "live"
    return j


@dataclass
class Hint:
    active: str
    recommended: str | None
    switch: bool
    message: str
    table: pd.DataFrame


def strategy_hint(backtest_trades: pd.DataFrame, cfg: dict, active: str | None = None,
                  names: list[str] | None = None) -> Hint:
    sh = cfg.get("switch_hint", {})
    lookback = int(sh.get("lookback_days", 10))
    min_trades = int(sh.get("min_trades", 5))
    margin = float(sh.get("margin_r", 0.15))
    active = active or cfg.get("active_strategy")

    bt = backtest_trades.copy()
    if len(bt):
        bt["source"] = "backtest"
    live = load_journal(cfg.get("market"))
    frames = [f[["day", "strategy", "r", "source"]] for f in (bt, live) if len(f)]
    allt = pd.concat(frames) if frames else pd.DataFrame(columns=["day", "strategy", "r", "source"])
    if len(allt):
        days = sorted(allt["day"].unique())[-lookback:]
        allt = allt[allt["day"].isin(days)]
    names = names or sorted(set(allt["strategy"]) | {active})

    rows = []
    for n in names:
        t = allt[allt["strategy"] == n]
        w = np.where(t["source"] == "live", 2.0, 1.0)
        r = t["r"].to_numpy(dtype=float)
        exp = float(np.average(r, weights=w)) if len(r) else np.nan
        recent = t.sort_values("day").tail(5)["r"].mean() if len(t) else np.nan
        losses = -r[r < 0].sum()
        rows.append({
            "strategy": n, "trades": len(t), "live_trades": int((t["source"] == "live").sum()),
            "expectancy_R": round(exp, 3) if len(r) else None,
            "last5_avg_R": round(recent, 3) if len(t) else None,
            "win_rate_%": round((r > 0).mean() * 100, 1) if len(r) else None,
            "profit_factor": round(r[r > 0].sum() / losses, 2) if losses else None,
        })
    table = pd.DataFrame(rows).set_index("strategy")
    eligible = table[(table["trades"] >= min_trades) & table["expectancy_R"].notna()]

    if eligible.empty:
        return Hint(active, None, False,
                    f"Not enough trades yet (need {min_trades}+ per strategy in the last "
                    f"{lookback} sessions). Run a longer backtest.", table)
    best = eligible["expectancy_R"].idxmax()
    best_exp = eligible.loc[best, "expectancy_R"]
    if best_exp <= 0:  # never switch into a strategy that is losing money
        return Hint(active, None, False,
                    f"No strategy with {min_trades}+ recent trades is making money (best: '{best}' at "
                    f"{best_exp:+.2f}R per trade). Staying with '{active}' - consider sitting out or trading smaller.",
                    table)
    if active not in eligible.index:
        return Hint(active, best, best != active,
                    f"'{active}' has too few recent trades to judge. Best lately: "
                    f"'{best}' ({best_exp:+.2f}R per trade).", table)
    act_exp = eligible.loc[active, "expectancy_R"]
    if best != active and (act_exp < 0 or best_exp - act_exp >= margin):
        why = "is losing money" if act_exp < 0 else "is lagging"
        return Hint(active, best, True,
                    f"SWITCH SUGGESTED: '{active}' {why} ({act_exp:+.2f}R/trade over the last "
                    f"{lookback} sessions). '{best}' is doing {best_exp:+.2f}R/trade.", table)
    if act_exp < 0:
        return Hint(active, None, False,
                    f"'{active}' is negative ({act_exp:+.2f}R) but so is everything else. "
                    "Consider sitting out or sizing down.", table)
    return Hint(active, active, False,
                f"Stay with '{active}': {act_exp:+.2f}R per trade lately"
                + (f" (best alternative '{best}' at {best_exp:+.2f}R)." if best != active else ", the best of all strategies."),
                table)


# ---- how trades actually end ----------------------------------------------------
EXIT_GROUPS = {"target": "target", "lost 9 EMA": "trailing", "trail stop": "trailing",
               "time exit": "time exit", "close": "time exit", "stop": "stop", "breakeven": "breakeven"}
EXIT_LABELS = {"target": "target hit", "trailing": "trailing exit", "time exit": "time exit",
               "stop": "stopped out", "breakeven": "breakeven"}


@dataclass
class ExitProfile:
    scope: str          # strategy name, or "all strategies" when it has too few trades
    trades: int
    table: pd.DataFrame  # index = exit group: share (0-1), avg_R

    def share(self, group: str) -> float:
        return float(self.table["share"].get(group, 0.0))

    def text(self) -> str:
        parts = [f"{EXIT_LABELS[g]} {r.share:.0%} (avg {r.avg_R:+.2f}R)" for g, r in self.table.iterrows()]
        return " · ".join(parts)


def exit_profile(trades: pd.DataFrame, strategy: str | None = None, min_trades: int = 10) -> ExitProfile | None:
    """Share of backtest trades ending each way (target, trailing, time, stop,
    breakeven) and their average R. Falls back to all strategies if `strategy`
    has fewer than `min_trades`."""
    if trades is None or not len(trades) or "reason" not in trades:
        return None
    scope, t = "all strategies", trades
    if strategy:
        s = trades[trades["strategy"] == strategy]
        if len(s) >= min_trades:
            scope, t = strategy, s
    g = t.assign(group=t["reason"].map(EXIT_GROUPS).fillna("time exit")).groupby("group")["r"]
    table = pd.DataFrame({"share": g.size() / len(t), "avg_R": g.mean()}).sort_values("share", ascending=False)
    return ExitProfile(scope, len(t), table)
