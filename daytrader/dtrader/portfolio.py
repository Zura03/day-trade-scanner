"""Your real positions: shares you actually bought, and when to sell them.

Separate from the monitor's paper trades. You record a buy (dashboard or
`cli.py buy`), the tool watches it with the same exit rules as the backtest
(stop, target, breakeven at +1R, trailing 9 EMA after +1R, time exit) and
says HOLD or SELL NOW with the reason; the monitor pushes a SELL alert. When
you sell, record it to move the position into your trade history.

Files (per market): data/[india/]my_positions.json (open; written only by
you via the dashboard/CLI) and my_trades.jsonl (closed).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .indicators import add_indicators
from .markets import US, Market, get_market
from .strategies import Position


def _open_path(m: Market):
    return m.path("my_positions.json")


def _closed_path(m: Market):
    return m.path("my_trades.jsonl")


def load_positions(market: str | Market | None = None) -> list[dict]:
    p = _open_path(get_market(market))
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return []


def _save_positions(rows: list[dict], m: Market) -> None:
    p = _open_path(m)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2))
    tmp.replace(p)


def add_buy(market: str | Market, symbol: str, shares: int, price: float, stop: float, target: float,
            strategy: str = "manual", when: datetime | None = None) -> dict:
    m = get_market(market)
    if shares <= 0 or price <= 0:
        raise ValueError("shares and price must be positive")
    if not stop < price < target:
        raise ValueError("need stop < buy price < target")
    row = {
        "id": uuid.uuid4().hex[:8], "symbol": symbol.upper().strip(), "shares": int(shares),
        "entry": float(price), "stop": float(stop), "target": float(target), "strategy": strategy,
        "entry_time": (when or m.now()).isoformat(timespec="minutes"),
    }
    rows = load_positions(m)
    rows.append(row)
    _save_positions(rows, m)
    return row


def update_position(market: str | Market, pid: str, **changes) -> None:
    m = get_market(market)
    rows = load_positions(m)
    for r in rows:
        if r["id"] == pid:
            r.update({k: v for k, v in changes.items() if k in ("stop", "target", "shares")})
    _save_positions(rows, m)


def delete_position(market: str | Market, pid: str) -> None:
    m = get_market(market)
    _save_positions([r for r in load_positions(m) if r["id"] != pid], m)


def record_sell(market: str | Market, pid: str, price: float, when: datetime | None = None,
                reason: str = "sold") -> dict | None:
    m = get_market(market)
    rows = load_positions(m)
    row = next((r for r in rows if r["id"] == pid), None)
    if row is None:
        return None
    risk = max(row["entry"] - row["stop"], 1e-9)
    done = {**row, "exit": float(price), "exit_time": (when or m.now()).isoformat(timespec="minutes"),
            "exit_reason": reason, "pnl": round((price - row["entry"]) * row["shares"], 2),
            "r_multiple": round((price - row["entry"]) / risk, 2)}
    with open(_closed_path(m), "a") as f:
        f.write(json.dumps(done) + "\n")
    _save_positions([r for r in rows if r["id"] != pid], m)
    return done


def load_closed(market: str | Market | None = None) -> pd.DataFrame:
    p = _closed_path(get_market(market))
    if not p.exists():
        return pd.DataFrame()
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return pd.DataFrame(rows)


@dataclass
class Advice:
    action: str          # "HOLD" | "SELL NOW" | "NO DATA"
    reason: str
    last: float | None
    stop: float          # current stop (after breakeven / trailing)
    pnl: float | None
    r_now: float | None
    notes: list


def evaluate(row: dict, provider, cfg: dict) -> Advice:
    """Replay the position from your buy time with the backtest's exit rules."""
    m: Market = cfg.get("market", US)
    entry_time = pd.Timestamp(row["entry_time"])
    if entry_time.tzinfo is None:
        entry_time = entry_time.tz_localize(m.tz)
    entry_time = entry_time.tz_convert(m.tz)
    today = m.now().date()
    pos = Position(row["symbol"], row.get("strategy", "manual"), entry_time, row["entry"], row["stop"],
                   row["target"], row["stop"], row["shares"])
    try:
        raw = provider.intraday(row["symbol"], entry_time.date())
    except Exception:
        raw = pd.DataFrame()
    if raw is None or not len(raw):
        return Advice("NO DATA", "no price data yet", None, row["stop"], None, None, [])
    bars = add_indicators(raw, m)
    bars = bars[bars["regular"]]
    if not len(bars):
        return Advice("NO DATA", "market hasn't opened yet", None, row["stop"], None, None, [])
    # bars from the minute you bought onwards
    bars = bars[bars.index >= entry_time.floor("min")]
    pos.replay(bars, cfg.get("exits", {}))
    last = float(raw["close"].iloc[-1])
    pnl = round((last - row["entry"]) * row["shares"], 2)
    r_now = round((last - row["entry"]) / max(row["entry"] - row["stop"], 1e-9), 2)
    notes = [n.split(" ", 1)[1] for n in pos.notes]
    if not pos.is_open:
        why = {"stop": f"stop {pos.stop:.2f} hit", "breakeven": "fell back to your entry (breakeven stop)",
               "trail stop": f"trailing stop {pos.stop:.2f} hit", "target": f"target {row['target']:.2f} reached",
               "lost 9 EMA": "closed below the 9 EMA after +1R (trailing exit)",
               "time exit": f"day-trade time exit ({cfg.get('exits', {}).get('time_exit')})"}
        return Advice("SELL NOW", f"{why.get(pos.exit_reason, pos.exit_reason)} at {pos.exit_time:%H:%M}",
                      last, pos.stop, pnl, r_now, notes)
    if entry_time.date() < today:
        return Advice("SELL NOW", "held overnight - this is a day trade", last, pos.stop, pnl, r_now, notes)
    hint = f"stop {pos.stop:.2f}" + (" (breakeven)" if pos.stop == row["entry"] else "")
    if pos.trailing:
        hint += ", trailing: sell on a 1-min close below the 9 EMA"
    return Advice("HOLD", hint, last, pos.stop, pnl, r_now, notes)
