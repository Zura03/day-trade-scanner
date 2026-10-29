"""Live monitor: BUY alerts when the active strategy triggers, SELL alerts when
a tracked trade hits its stop/target/trailing exit.

Every BUY alert opens a *tracked* (paper) position; exits are replayed with the
same `Position.step` logic as the backtester. Closed trades go to the journal,
which feeds the strategy-switch hint. Nothing here places orders.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time as _time
from datetime import datetime, time

import pandas as pd

from .config import DATA_DIR, load_config, load_state, save_state
from .data.base import DataProvider, now_ny
from .indicators import add_indicators
from .performance import JOURNAL
from .scanner import analyse, gather_candidates, session_day
from .strategies import Position, Strategy, get_strategy

ALERTS = DATA_DIR / "alerts.jsonl"
COLORS = {"BUY": "\033[1;32m", "SELL": "\033[1;31m", "WATCH": "\033[1;33m", "INFO": "\033[1;36m"}


def notify(kind: str, title: str, body: str, cfg: dict) -> None:
    a = cfg.get("alerts", {})
    stamp = now_ny().strftime("%H:%M:%S")
    print(f"{COLORS.get(kind, '')}[{stamp}] {kind:5} {title}\033[0m  {body}", flush=True)
    with open(ALERTS, "a") as f:
        f.write(json.dumps({"time": now_ny().isoformat(), "kind": kind, "title": title, "body": body}) + "\n")
    if sys.platform == "darwin":
        if a.get("desktop_notifications", True):
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e",
                            f'display notification "{esc(body)}" with title "{esc(kind + ": " + title)}"'],
                           capture_output=True)
        if a.get("sound", True) and kind in ("BUY", "SELL"):
            snd = "Glass" if kind == "BUY" else "Basso"
            subprocess.Popen(["afplay", f"/System/Library/Sounds/{snd}.aiff"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print("\a", end="", flush=True)


def load_alerts(limit: int = 300) -> pd.DataFrame:
    if not ALERTS.exists():
        return pd.DataFrame(columns=["time", "kind", "title", "body"])
    lines = ALERTS.read_text().splitlines()[-limit:]
    return pd.DataFrame([json.loads(l) for l in lines if l.strip()])


class Monitor:
    def __init__(self, provider: DataProvider, cfg: dict, strategy: Strategy | None = None):
        self.p = provider
        self.cfg = cfg
        self.strategy = strategy or get_strategy(cfg["active_strategy"], cfg)
        self.state = load_state()
        self.state.setdefault("alerted", [])
        self.state.setdefault("positions", [])
        self.cands = []
        self.last_universe = 0.0

    # -- persistence ----------------------------------------------------
    def _save(self):
        st = load_state()  # keep fields others may have changed (active strategy)
        st["alerted"] = self.state["alerted"][-500:]
        st["positions"] = self.state["positions"]
        save_state(st)

    def _seen(self, key: str) -> bool:
        if key in self.state["alerted"]:
            return True
        self.state["alerted"].append(key)
        return False

    # -- one polling cycle ---------------------------------------------
    def tick(self) -> None:
        # pick up a strategy switch made from the dashboard / CLI
        active = load_config().get("active_strategy")
        if active and active != self.strategy.name:
            self.strategy = get_strategy(active, self.cfg)
            notify("INFO", "Strategy switched", f"Now scanning with {self.strategy.title}", self.cfg)

        day = session_day(self.p)
        if _time.time() - self.last_universe > 300 or not self.cands:
            self.cands = gather_candidates(self.p, self.cfg, day, log=lambda *_: None)
            self.last_universe = _time.time()
        else:  # refresh bars only
            for c in self.cands:
                bars = self.p.intraday(c.symbol, day)
                if len(bars):
                    c.bars = add_indicators(bars)
                    c.last = float(bars["close"].iloc[-1])

        for c in self.cands:
            a = analyse(self.strategy, c)
            s = a["signal"]
            if a["status"] == "BUY NOW" and s and not self._seen(f"buy|{s.symbol}|{s.strategy}|{s.time}"):
                sh = self.strategy.position_size(s)
                notify("BUY", f"{s.symbol} @ {s.entry:.2f}",
                       f"stop {s.stop:.2f} / target {s.target:.2f} / {sh} sh "
                       f"(risk ${s.risk * sh:.0f}) - {s.reason}", self.cfg)
                self.state["positions"].append(Position.from_signal(s, sh).to_dict())
            elif a["status"] == "SETTING UP" and a["watch"] and not self._seen(f"watch|{c.symbol}|{day}|{self.strategy.name}"):
                notify("WATCH", f"{c.symbol} near trigger {a['watch'][0]:.2f}",
                       f"last {c.last:.2f}, {a['watch'][1]} ({self.strategy.title})", self.cfg)

        self._manage_positions(day)
        self._save()

    def _manage_positions(self, day) -> None:
        still_open = []
        for d in self.state["positions"]:
            p = Position.from_dict(d)
            if p.entry_time.date() != day:  # left over from a prior session: drop it
                continue
            strat = get_strategy(p.strategy, self.cfg)
            bars = next((c.bars for c in self.cands if c.symbol == p.symbol), None)
            if bars is None:
                raw = self.p.intraday(p.symbol, day)
                bars = add_indicators(raw) if len(raw) else None
            if bars is None:
                still_open.append(d)
                continue
            # replay from a fresh copy of the original trade each time
            replay = Position(p.symbol, p.strategy, p.entry_time, p.entry, p.initial_stop,
                              p.target, p.initial_stop, p.shares)
            for ts, bar in bars[bars.index > p.entry_time].iterrows():
                if replay.step(ts, bar, strat.exits):
                    break
            for note in replay.notes[len(p.notes):]:
                if not self._seen(f"note|{p.symbol}|{p.entry_time}|{note}"):
                    notify("INFO", f"{p.symbol}: {note.split(' ', 1)[1]}",
                           f"new stop {replay.stop:.2f}", self.cfg)
            if replay.is_open:
                still_open.append(replay.to_dict())
                continue
            pnl = (replay.exit_price - replay.entry) * replay.shares
            if not self._seen(f"sell|{p.symbol}|{p.entry_time}"):
                notify("SELL", f"{p.symbol} @ {replay.exit_price:.2f} ({replay.exit_reason})",
                       f"{replay.r_multiple:+.2f}R, ${pnl:+.0f} on {replay.shares} sh "
                       f"(entry {replay.entry:.2f})", self.cfg)
            with open(JOURNAL, "a") as f:
                f.write(json.dumps(replay.to_dict()) + "\n")
        self.state["positions"] = still_open


def in_session(now: datetime) -> bool:
    return now.weekday() < 5 and time(4, 0) <= now.time() <= time(16, 5)


def run_monitor(provider: DataProvider, cfg: dict, once: bool = False) -> None:
    m = Monitor(provider, cfg)
    poll = int(cfg.get("alerts", {}).get("poll_seconds", 60))
    notify("INFO", "Monitor started", f"strategy={m.strategy.title}, data={provider.name}, every {poll}s", cfg)
    while True:
        now = now_ny()
        if once or in_session(now):
            try:
                m.tick()
            except Exception as e:
                print(f"tick error: {e}", file=sys.stderr)
        if once:
            return
        _time.sleep(poll if in_session(now) else 300)
