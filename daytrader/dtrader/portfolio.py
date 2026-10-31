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


# ---- recently buyable setups (for recording a buy after a setup expires) ----
def _recent_path(m: Market):
    return m.path("recent_buyable.json")


def setup_key(row) -> str:
    return f"{row['symbol']}|{row['strategy']}|{row['triggered_at']}"


def remember_buyable(market: str | Market, buyable: pd.DataFrame) -> None:
    """Record every BUY NOW setup seen today, with when it was last buyable,
    so it can still be picked for a few minutes after it stops being buyable."""
    m = get_market(market)
    today, now = m.now().date().isoformat(), m.now().isoformat(timespec="seconds")
    p = _recent_path(m)
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except json.JSONDecodeError:
        data = {}
    day = data.get(today, {})
    for _, r in buyable.iterrows():
        k = setup_key(r)
        prev = day.get(k, {})
        day[k] = {"symbol": r["symbol"], "strategy": r["strategy"], "triggered_at": r["triggered_at"],
                  "entry": float(r["entry"]), "stop": float(r["stop"]), "target": float(r["target"]),
                  "shares": int(r["shares"]) if r["shares"] == r["shares"] else 1,
                  "last": float(r["last"]), "first_seen": prev.get("first_seen", now), "last_seen": now}
    p.write_text(json.dumps({today: day}, indent=1))  # only today's setups are kept


def recent_buyable(market: str | Market, minutes: int = 60) -> dict[str, dict]:
    """Setups that were buyable today within the last `minutes`, newest first."""
    m = get_market(market)
    p = _recent_path(m)
    try:
        day = json.loads(p.read_text()).get(m.now().date().isoformat(), {}) if p.exists() else {}
    except json.JSONDecodeError:
        return {}
    cutoff = m.now() - pd.Timedelta(minutes=minutes)
    keep = {k: v for k, v in day.items() if datetime.fromisoformat(v["last_seen"]) >= cutoff}
    return dict(sorted(keep.items(), key=lambda kv: kv[1]["last_seen"], reverse=True))


# ---- daily risk limits --------------------------------------------------------
@dataclass
class RiskStatus:
    realized: float
    unrealized: float
    trades_today: int
    open_count: int
    max_daily_loss: float
    max_trades: int
    max_open: int
    breaches: list

    @property
    def pnl_today(self) -> float:
        return self.realized + self.unrealized

    @property
    def blocked(self) -> bool:
        return bool(self.breaches)

    @property
    def loss_left(self) -> float:
        return max(self.max_daily_loss + self.pnl_today, 0.0)


def risk_status(market: str | Market, provider, cfg: dict, advice: list | None = None) -> RiskStatus:
    """Today's P&L, trade count and open positions against config `risk_limits`.
    `advice` = [(row, Advice)] if already evaluated (saves refetching prices)."""
    m = get_market(market)
    lim = cfg.get("risk_limits", {})
    max_loss = float(lim.get("max_daily_loss", 0) or 0)
    max_trades = int(lim.get("max_trades_per_day", 0) or 0)
    max_open = int(lim.get("max_open_positions", 0) or 0)
    today = m.now().date().isoformat()

    open_rows = load_positions(m)
    if advice is None:
        advice = []
        for r in open_rows:
            try:
                advice.append((r, evaluate(r, provider, cfg)))
            except Exception:
                advice.append((r, None))
    unrealized = sum((a.pnl or 0) for _, a in advice if a is not None)
    closed = load_closed(m)
    closed_today = closed[closed["exit_time"].astype(str).str.startswith(today)] if len(closed) else closed
    realized = float(closed_today["pnl"].sum()) if len(closed_today) else 0.0
    bought_today = sum(r["entry_time"].startswith(today) for r in open_rows)
    if len(closed):
        bought_today += int(closed["entry_time"].astype(str).str.startswith(today).sum())

    breaches = []
    pnl = realized + unrealized
    if max_loss and pnl <= -max_loss:
        breaches.append(f"daily loss limit hit ({m.fmt_money(pnl)} of -{m.currency}{max_loss:,.0f})")
    if max_trades and bought_today >= max_trades:
        breaches.append(f"{bought_today} of {max_trades} trades used today")
    if max_open and len(open_rows) >= max_open:
        breaches.append(f"{len(open_rows)} of {max_open} positions already open")
    return RiskStatus(realized, unrealized, bought_today, len(open_rows), max_loss, max_trades, max_open, breaches)
