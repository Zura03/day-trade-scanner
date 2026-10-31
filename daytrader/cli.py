#!/usr/bin/env python
"""Day-trade scanner for US small caps and NSE F&O stocks.

Add --market india before the command for the Indian market (default: us).

  python cli.py strategies              list strategies (* = active)
  python cli.py switch bull_flag        change the active strategy
  python cli.py scan [--all]            rank today's movers for the active (or every) strategy
  python cli.py scan --buyable          only setups buyable right now, across all strategies
  python cli.py backtest --days 10      replay recent sessions for every strategy
  python cli.py hint                    should I switch strategies?
  python cli.py monitor                 live BUY / SELL alerts (runs until Ctrl-C)
  python cli.py buy JSWENERGY 100 505.2 --stop 491   record shares you bought
  python cli.py positions               your positions: HOLD or SELL NOW (and why)
  python cli.py sell JSWENERGY 512      record that you sold
  python cli.py auth                    log in to Schwab (US) / Groww or Zerodha (India, daily)

  python cli.py --market india scan     same commands for NSE
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

DISCLAIMER = ("Educational tool, not financial advice. Day trading is risky: "
              "always use a stop and size for the loss.")


def provider(cfg, args):
    from dtrader.data import get_provider

    p = get_provider(cfg, getattr(args, "source", None))
    print(f"[{cfg['market'].name} market, data: {p.name}]", file=sys.stderr)
    return p


def cmd_strategies(cfg, args):
    for name, cls in REGISTRY.items():
        mark = "*" if name == cfg.get("active_strategy") else " "
        print(f"{mark} {name:16} {cls.title}  [{cls.style}]\n    {cls.description}\n")


def cmd_switch(cfg, args):
    if args.name not in REGISTRY:
        sys.exit(f"Unknown strategy. Options: {', '.join(REGISTRY)}")
    set_active_strategy(args.name, cfg["market"])
    print(f"{cfg['market'].name} active strategy -> {args.name} ({REGISTRY[args.name].title})")


def cmd_scan(cfg, args):
    from dtrader.scanner import scan

    p = provider(cfg, args)
    every = args.all or args.buyable
    strats = all_strategies(cfg) if every else [get_strategy(args.strategy or cfg["active_strategy"], cfg)]
    df, cands = scan(p, cfg, strats, log=lambda m: print(m, file=sys.stderr))
    if df.empty:
        print("No candidates passed the filters right now.")
        return
    if args.buyable:
        from dtrader.backtest import load_last_trades
        from dtrader.performance import strategy_hint
        from dtrader.scanner import buyable_now

        recent = strategy_hint(load_last_trades(cfg["market"]), cfg, names=list(REGISTRY)).table
        b = buyable_now(df, cfg["active_strategy"], recent)
        if b.empty:
            print("Nothing is buyable right now.")
            return
        from dtrader.odds import setup_odds

        by_sym = {c.symbol: c for c in cands}
        verdict, reach, record = [], [], []
        for r in b.itertuples():
            c = by_sym[r.symbol]
            o = setup_odds(p, cfg, get_strategy(r.strategy, cfg), c.results[r.strategy]["signal"], c.bars, c.last)
            verdict.append(o.verdict if o else "-")
            reach.append(f"{o.p_target:.0%}/{o.p_stop:.0%}/{o.p_open:.0%}" if o else "-")
            record.append(o.history_text() if o else "-")
        b["verdict"], b["reach_T/S/none"], b["track_record"] = verdict, reach, record
        b.insert(0, "*", ["*" if a else "" for a in b["active"]])
        b["strategy_R"] = b["strategy_R"].map(lambda v: "" if v != v else f"{v:+.2f}")
        cols = ["*", "symbol", "strategy", "verdict", "reach_T/S/none", "track_record", "strategy_R",
                "triggered_at", "entry", "last", "r_from_entry", "stop", "target", "shares"]
        print(b[cols].to_string(index=False))
        print(f"\n* = active strategy ({cfg['active_strategy']}), the only one the monitor alerts on. "
              f"strategy_R = its recent average R per trade.\n{DISCLAIMER}")
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
    from dtrader.backtest import trades_csv
    from dtrader.config import ROOT

    print(f"\nSaved all trades to {trades_csv(cfg['market']).relative_to(ROOT)}")


def cmd_hint(cfg, args):
    from dtrader.backtest import load_last_trades, run_backtest
    from dtrader.performance import strategy_hint

    trades = load_last_trades(cfg["market"])
    if args.refresh or trades.empty:
        trades = run_backtest(provider(cfg, args), cfg, all_strategies(cfg),
                              days=cfg.get("switch_hint", {}).get("lookback_days", 10),
                              log=lambda m: print(m, file=sys.stderr))
    h = strategy_hint(trades, cfg, names=list(REGISTRY))
    print(h.table.to_string())
    print("\n" + h.message)
    if h.switch and h.recommended and args.apply:
        set_active_strategy(h.recommended, cfg["market"])
        print(f"Switched active strategy -> {h.recommended}")
    elif h.switch:
        mk = "" if cfg["market"].key == "us" else f"--market {cfg['market'].key} "
        print(f"Run `python cli.py {mk}switch {h.recommended}` (or `hint --apply`) to switch.")


def cmd_monitor(cfg, args):
    from dtrader.alerts import run_monitor

    if args.strategy:
        set_active_strategy(args.strategy, cfg["market"])
        cfg = load_config(cfg["market"])
    p = provider(cfg, args)
    # Refresh the backtest + switch hint before the session (skipped on
    # holidays, after the close and for --once).
    if cfg.get("switch_hint", {}).get("refresh_before_session") and not args.once:
        from dtrader.alerts import session_pending, strategy_check

        if not args.session or session_pending(p, cfg):
            cfg = strategy_check(p, cfg, log=lambda m: print(m, file=sys.stderr))
    print(DISCLAIMER)
    try:
        run_monitor(p, cfg, once=args.once, session=args.session)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


def cmd_buy(cfg, args):
    from dtrader.portfolio import add_buy

    m = cfg["market"]
    stop = args.stop if args.stop else round(args.price * 0.98, 2)
    target = args.target if args.target else round(args.price + 2 * (args.price - stop), 2)
    row = add_buy(m, args.symbol, args.shares, args.price, stop, target, args.strategy or "manual")
    print(f"Tracking {row['shares']} {row['symbol']} @ {row['entry']:.2f} (stop {row['stop']:.2f}, "
          f"target {row['target']:.2f}), id {row['id']}. `positions` shows when to sell.")


def cmd_positions(cfg, args):
    from dtrader.portfolio import evaluate, load_closed, load_positions

    rows = load_positions(cfg["market"])
    if not rows:
        print("No open positions. Record one with:  buy SYMBOL SHARES PRICE [--stop X --target Y]")
    else:
        p = provider(cfg, args)
        for r in rows:
            a = evaluate(r, p, cfg)
            pnl = cfg["market"].fmt_money(a.pnl) if a.pnl is not None else "?"
            last = f"{a.last:.2f}" if a.last else "?"
            print(f"{a.action:9} {r['symbol']:10} {r['shares']:>6} sh  bought {r['entry']:.2f} at "
                  f"{r['entry_time'][11:16]}  last {last}  {pnl}  stop now {a.stop:.2f}  - {a.reason}  [id {r['id']}]")
    closed = load_closed(cfg["market"])
    if len(closed):
        print(f"\nClosed: {len(closed)} trades, {cfg['market'].fmt_money(closed['pnl'].sum())}, "
              f"avg {closed['r_multiple'].mean():+.2f}R")


def cmd_sell(cfg, args):
    from dtrader.portfolio import load_positions, record_sell

    rows = [r for r in load_positions(cfg["market"]) if args.id_or_symbol in (r["id"], r["symbol"])]
    if not rows:
        sys.exit("No open position with that symbol or id (see `positions`).")
    if len(rows) > 1:
        sys.exit("Several positions match that symbol - use the id from `positions`.")
    done = record_sell(cfg["market"], rows[0]["id"], args.price)
    print(f"Recorded sale of {done['shares']} {done['symbol']} @ {done['exit']:.2f}: "
          f"{done['r_multiple']:+.2f}R, {cfg['market'].fmt_money(done['pnl'])}")


def cmd_auth(cfg, args):
    from dtrader.data import get_provider

    if cfg["market"].key == "india":
        from dtrader.data import india_broker

        broker = india_broker()
        if broker == "groww":
            from dtrader.data.groww_provider import groww_token, last_token_error

            if not groww_token(force=True):
                sys.exit(f"Groww refused the login: {last_token_error()}\n"
                         "If you use API key + secret, approve today's key on Groww Cloud's "
                         "API Keys page, then run this again.")
            p = get_provider(cfg, "groww")
            q = p.quotes(["RELIANCE"])
            px = f", RELIANCE {q['last'].iloc[0]:.2f}" if len(q) else ""
            print(f"Groww login OK for today{px}.")
            return
        if broker == "kite":
            from dtrader.config import kite_keys
            from dtrader.data.kite_provider import kite_login

            keys = kite_keys()
            kite_login(keys[0], keys[1], (cfg.get("kite") or {}).get("redirect_url", "http://127.0.0.1:5010/kite"))
            p = get_provider(cfg, "kite")
            print(f"Zerodha login OK for today ({len(p.all_symbols())} F&O stocks available).")
            return
        sys.exit("No India broker in .env: add GROWW_API_KEY / GROWW_API_SECRET "
                 "(or KITE_API_KEY / KITE_API_SECRET).")
    p = get_provider(cfg, "schwab")
    r = p.client.get_quotes(["AAPL"])
    print("Schwab login OK" if r.status_code == 200 else f"Schwab returned {r.status_code}: {r.text[:200]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", default="us", choices=["us", "india"], help="market to work on (default us)")
    ap.add_argument("--source", choices=["auto", "alpaca", "schwab", "yfinance", "groww", "kite"], help="override data_source")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("strategies")
    s = sub.add_parser("switch"); s.add_argument("name")
    s = sub.add_parser("scan")
    s.add_argument("--strategy"); s.add_argument("--all", action="store_true", help="show every strategy")
    s.add_argument("--buyable", action="store_true", help="only setups buyable now, across all strategies")
    s.add_argument("--top", type=int, default=20)
    s = sub.add_parser("backtest")
    s.add_argument("--days", type=int, default=10); s.add_argument("--strategy", default="all")
    s.add_argument("--symbols", nargs="*"); s.add_argument("--trades", type=int, default=0, help="print last N trades")
    s = sub.add_parser("hint")
    s.add_argument("--refresh", action="store_true", help="re-run the backtest first")
    s.add_argument("--apply", action="store_true", help="switch automatically if suggested")
    s = sub.add_parser("monitor"); s.add_argument("--strategy"); s.add_argument("--once", action="store_true")
    s.add_argument("--session", action="store_true",
                   help="for the daily job: skip holidays, stop 5 min after the close")
    sub.add_parser("auth")
    s = sub.add_parser("buy", help="record shares you bought; the tool tells you when to sell")
    s.add_argument("symbol"); s.add_argument("shares", type=int); s.add_argument("price", type=float)
    s.add_argument("--stop", type=float, help="default 2%% below price")
    s.add_argument("--target", type=float, help="default 2R above price"); s.add_argument("--strategy")
    sub.add_parser("positions", help="your open positions: HOLD or SELL NOW")
    s = sub.add_parser("sell", help="record that you sold a position")
    s.add_argument("id_or_symbol"); s.add_argument("price", type=float)
    args = ap.parse_args()
    cfg = load_config(args.market)
    {"strategies": cmd_strategies, "switch": cmd_switch, "scan": cmd_scan, "backtest": cmd_backtest,
     "hint": cmd_hint, "monitor": cmd_monitor, "auth": cmd_auth, "buy": cmd_buy,
     "positions": cmd_positions, "sell": cmd_sell}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
