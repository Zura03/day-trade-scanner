# Day Trade Scanner

A day-trading assistant for **US small caps** and **Indian NSE F&O stocks**.
It scans the day's movers, finds setups for 13 switchable strategies, and tells
you what's buyable right now, with entry, stop, target and share size. It
sends BUY and SELL alerts to your phone and tracks the shares you buy until
it's time to sell. Every morning it re-runs a market-wide backtest and
switches to the strategy that has been working best.

> Educational tool, not financial advice. It never places orders: you trade in
> your own broker app and record what you did.

## Features

- **13 strategies, switchable:** Gap & Go, Bull Flag, Opening Range Breakout
  (ORB), VWAP Reclaim, Red-to-Green, Oversold Bounce, and seven long setups
  from r/Daytrading (VWAP Bounce, First-Hour Trend, Stop-Hunt Reversal, Rip &
  Dip, Bagholder Bounce, 9:45 Reversal, ORB Retest).
- **Buyable now:** every setup that has triggered and can still be bought,
  across all strategies. Each one shows its entry, stop, 2R target and share
  size from your risk per trade, plus:
  - **setup odds:** a replay of the rest of the day, run 10,000 times on the
    stock's own price history, plus this rule's track record on the stock;
  - **news and earnings warnings.**
- **Alerts on your phone (ntfy):** BUY when your active strategy triggers, SELL
  when one of your positions hits its stop, target, trailing exit or time
  exit, and a note when a stop moves.
- **Position tracking:** record what you bought (prefilled from a setup), and
  each position shows HOLD or SELL NOW with the reason, plus live P&L.
- **Daily capital:** set the amount you trade per day, and risk per trade,
  position size, the loss limit and the affordable price range follow as
  percentages of it. Your broker's exact fees (Groww, Zerodha, Fyers and
  others) are applied to every backtest trade.
- **Daily risk limits:** a max daily loss, a max number of trades per day and
  a max number of open positions. When one is hit, BUY alerts pause.
- **Market-wide backtest and auto-switch:** it replays every listed stock that
  gapped, using only what's known at the open. Each morning it picks the
  strategy with the best recent results, and needs at least 10 trades before
  judging one.
- **Two markets with their own hours and calendars:** US (premarket gappers,
  ET) and India (NSE F&O, IST). Holidays and early closes are handled.
- **Runs by itself on a Mac:** the monitors start before each market opens,
  and the dashboard is always on and reachable from your phone through
  Tailscale.
- **Data sources:** Alpaca (free plan), Schwab or Yahoo for the US; Groww,
  Zerodha Kite or free Yahoo data for NSE.

## Quick start

```bash
git clone https://github.com/Zura03/day-trade-scanner.git trade && cd trade
./daytrader/run.sh            # builds the environment, opens the dashboard at http://localhost:8501
```

It works straight away on free Yahoo data. For better US data, add a free
Alpaca paper account's keys to `daytrader/.env` (copy `.env.example`). To run
everything automatically every market day:

```bash
./daytrader/scripts/autostart.sh install
```

Common commands (add `--market india` for NSE):

```bash
./daytrader/run.sh scan --buyable     # what's buyable right now, with odds and news
./daytrader/run.sh monitor            # live BUY/SELL alerts
./daytrader/run.sh positions          # your positions: HOLD or SELL NOW
./daytrader/run.sh risk               # today's P&L and trades vs your limits
./daytrader/run.sh budget             # daily capital -> risk per trade, position, costs
./daytrader/run.sh backtest --days 30
./daytrader/run.sh hint               # which strategy is working lately?
```

## Documentation

Setup for each data source, the dashboard, every strategy's rules, exits,
alerts, phone access, configuration and known limits are all in
**[daytrader/README.md](daytrader/README.md)**.

## Layout

```
daytrader/
  run.sh                launcher (creates .venv on first run)
  cli.py                command-line interface
  dashboard.py          Streamlit dashboard
  config.yaml           filters, risk, exits, limits, strategy parameters (US + india: section)
  .streamlit/           dashboard theme
  scripts/autostart.sh  macOS jobs: monitors before each open, always-on dashboard
  dtrader/
    markets/            US / India hours, exchange calendars, NSE F&O list
    data/               Alpaca, Schwab, Groww, Zerodha Kite and Yahoo providers
    strategies/         one file per strategy + registry
    scanner.py          finds and ranks today's movers, BUY NOW logic
    backtest.py         market-wide replay of recent sessions
    performance.py      strategy-switch hint
    odds.py             setup odds (reach simulation + track record)
    news.py             headlines and earnings dates
    portfolio.py        your positions, sell advice, daily risk limits
    costs.py            broker + statutory fees per trade
    alerts.py           live monitor, phone and desktop notifications
    ui.py               dashboard design tokens and components
```

API keys and passwords live in `daytrader/.env`; caches, trades and logs in
`daytrader/data/`. Git ignores both.
