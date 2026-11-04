"""Which buyable setup is best right now? An expected-R score per setup.

Only evidence available at this moment goes in:

- the strategy's recent results, from the backtest plus the monitor's paper
  trades, net of slippage and fees (the switch-hint table);
- this stock's own track record with this strategy over its last 20 sessions
  (the odds history, same costs);
- how far the price has already run past the entry.

The two track records are pooled and shrunk toward zero: a strategy with 5
trades at +1R is mostly luck, one with 200 trades at +0.1R is evidence.

    edge      = (n_strategy * R_strategy + n_stock * R_stock) / (n_strategy + n_stock + PRIOR)
    expected  = (entry + edge * risk - last) / (last - stop)

The second line re-expresses the edge for buying at the current price: the
trade is expected to end at entry + edge x risk, so every bit the price has
already run is profit you no longer get, and your stop is further away.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

PRIOR = 20          # trades of "no edge" every estimate starts with
MIN_SCORE = 0.0     # below this a setup isn't worth taking


@dataclass
class Score:
    expected_r: float   # per R risked if you buy now, after costs
    edge_r: float       # pooled, shrunk track record (buying at the entry)
    reason: str


def edge(strategy_r: float | None, strategy_n: int, stock_r: float | None, stock_n: int,
         prior: int = PRIOR) -> float:
    total, n = 0.0, 0
    for r, k in ((strategy_r, strategy_n), (stock_r, stock_n)):
        if r is not None and k and not math.isnan(r):
            total += r * k
            n += k
    return total / (n + prior)


def expected_now(edge_r: float, entry: float, stop: float, last: float) -> float:
    risk_now = last - stop
    if risk_now <= 0:
        return -1.0  # at or below the stop
    return (entry + edge_r * (entry - stop) - last) / risk_now


def score_setup(strategy_r: float | None, strategy_n: int, stock_r: float | None, stock_n: int,
                entry: float, stop: float, last: float) -> Score:
    e = edge(strategy_r, strategy_n, stock_r, stock_n)
    x = expected_now(e, entry, stop, last)
    bits = [f"strategy {strategy_r:+.2f}R over {strategy_n}" if strategy_n and strategy_r == strategy_r
            else "strategy: no recent trades"]
    bits.append(f"this stock {stock_r:+.2f}R over {stock_n}" if stock_n and stock_r is not None
                else "this stock: no record")
    past = (last - entry) / (entry - stop) if entry > stop else 0.0
    if abs(past) >= 0.05:
        bits.append(f"price {past:+.2f}R from entry")
    return Score(x, e, ", ".join(bits))
