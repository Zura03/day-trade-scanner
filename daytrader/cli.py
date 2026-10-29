#!/usr/bin/env python
"""Small-cap day-trade scanner.

  python cli.py strategies              list strategies (* = active)
  python cli.py switch bull_flag        change the active strategy
  python cli.py scan [--all]            rank today's movers for the active (or every) strategy
  python cli.py backtest --days 10      replay recent sessions for every strategy
  python cli.py hint                    should I switch strategies?
  python cli.py monitor                 live BUY / SELL alerts (runs until Ctrl-C)
  python cli.py auth                    log in to Schwab (first-time setup)
"""
from __future__ import annotations

import argparse
import sys
import warnings

warnings.filterwarnings("ignore")

import pandas as pd  # noqa: E402

from dtrader.config import load_config, set_active_strategy  # noqa: E402
from dtrader.strategies import REGISTRY, all_strategies, get_strategy  # noqa: E402

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_colwidth", 60)

DISCLAIMER = ("Educational tool, not financial advice. Small caps are volatile: "
              "always use a stop and size for the loss.")


def provider(cfg, args):
    from dtrader.data import get_provider

    p = get_provider(cfg, getattr(args, "source", None))
    print(f"[data: {p.name}]", file=sys.stderr)
    return p


def cmd_strategies(cfg, args):
    for name, cls in REGISTRY.items():
        mark = "*" if name == cfg.get("active_strategy") else " "
        print(f"{mark} {name:16} {cls.title}  [{cls.style}]\n    {cls.description}\n")


def cmd_switch(cfg, args):
    if args.name not in REGISTRY:
        sys.exit(f"Unknown strategy. Options: {', '.join(REGISTRY)}")
    set_active_strategy(args.name)
    print(f"Active strategy -> {args.name} ({REGISTRY[args.name].title})")


def cmd_scan(cfg, args):
    from dtrader.scanner import scan

    p = provider(cfg, args)
    strats = all_strategies(cfg) if args.all else [get_strategy(args.strategy or cfg["active_strategy"], cfg)]
    df, _ = scan(p, cfg, strats, log=lambda m: print(m, file=sys.stderr))
    if df.empty:
        print("No candidates passed the filters right now.")
        return
    if args.all:
        piv = df.pivot_table(index="symbol", columns="strategy", values="status", aggfunc="first")
        best = df.groupby("symbol")["score"].max().sort_values(ascending=False)
        print(piv.loc[best.index].fillna("").to_string())
        print()
        df = df[df["status"].isin(["BUY NOW", "SETTING UP"]) | df["status"].str.startswith("triggered")]
    cols = ["symbol", "strategy", "status", "score", "last", "gap_%", "day_%", "rel_vol", "float_M",
            "entry", "stop", "target", "shares", "why"]
    print(df[cols].head(args.top).to_string(index=False))
    print(f"\n{DISCLAIMER}")


def cmd_backtest(cfg, args):
    from dtrader.backtest import run_backtest, summarize

    p = provider(cfg, args)
    strats = all_strategies(cfg) if args.strategy in (None, "all") else [get_strategy(args.strategy, cfg)]
    trades = run_backtest(p, cfg, strats, days=args.days, symbols=args.symbols,
                          log=lambda m: print(m, file=sys.stderr))
    if trades.empty:
        print("No trades. Try more --days, add symbols to universe.watchlist, or run `scan` "
              "on a few days to build up mover history.")
        return
    print(summarize(trades, [s.name for s in strats]).to_string())
    if args.trades:
        print()
        print(trades.tail(args.trades).to_string(index=False))
    print("\nSaved all trades to data/backtest_trades.csv")


def cmd_hint(cfg, args):
    from dtrader.backtest import load_last_trades, run_backtest
    from dtrader.performance import strategy_hint

    trades = load_last_trades()
    if args.refresh or trades.empty:
        trades = run_backtest(provider(cfg, args), cfg, all_strategies(cfg),
                              days=cfg.get("switch_hint", {}).get("lookback_days", 10),
                              log=lambda m: print(m, file=sys.stderr))
    h = strategy_hint(trades, cfg, names=list(REGISTRY))
    print(h.table.to_string())
    print("\n" + h.message)
    if h.switch and h.recommended and args.apply:
        set_active_strategy(h.recommended)
        print(f"Switched active strategy -> {h.recommended}")
    elif h.switch:
        print(f"Run `python cli.py switch {h.recommended}` (or `hint --apply`) to switch.")


def cmd_monitor(cfg, args):
    from dtrader.alerts import run_monitor

    if args.strategy:
        set_active_strategy(args.strategy)
        cfg = load_config()
    p = provider(cfg, args)
    if cfg.get("switch_hint", {}).get("auto_switch"):
        from dtrader.backtest import run_backtest
        from dtrader.performance import strategy_hint

        t = run_backtest(p, cfg, all_strategies(cfg), days=cfg["switch_hint"].get("lookback_days", 10),
                         log=lambda m: print(m, file=sys.stderr))
        h = strategy_hint(t, cfg, names=list(REGISTRY))
        print(h.message)
        if h.switch and h.recommended:
            set_active_strategy(h.recommended)
            cfg = load_config()
    print(DISCLAIMER)
    try:
        run_monitor(p, cfg, once=args.once)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


def cmd_auth(cfg, args):
    from dtrader.data import get_provider

    p = get_provider(cfg, "schwab")
    r = p.client.get_quotes(["AAPL"])
    print("Schwab login OK" if r.status_code == 200 else f"Schwab returned {r.status_code}: {r.text[:200]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["auto", "alpaca", "schwab", "yfinance"], help="override data_source")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("strategies")
    s = sub.add_parser("switch"); s.add_argument("name")
    s = sub.add_parser("scan")
    s.add_argument("--strategy"); s.add_argument("--all", action="store_true", help="show every strategy")
    s.add_argument("--top", type=int, default=20)
    s = sub.add_parser("backtest")
    s.add_argument("--days", type=int, default=10); s.add_argument("--strategy", default="all")
    s.add_argument("--symbols", nargs="*"); s.add_argument("--trades", type=int, default=0, help="print last N trades")
    s = sub.add_parser("hint")
    s.add_argument("--refresh", action="store_true", help="re-run the backtest first")
    s.add_argument("--apply", action="store_true", help="switch automatically if suggested")
    s = sub.add_parser("monitor"); s.add_argument("--strategy"); s.add_argument("--once", action="store_true")
    sub.add_parser("auth")
    args = ap.parse_args()
    cfg = load_config()
    {"strategies": cmd_strategies, "switch": cmd_switch, "scan": cmd_scan, "backtest": cmd_backtest,
     "hint": cmd_hint, "monitor": cmd_monitor, "auth": cmd_auth}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
