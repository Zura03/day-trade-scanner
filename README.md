# Day Trade Scanner

A small-cap momentum scanner. It finds stocks worth day trading, ranks them for
the strategy you pick, gives entry / stop / target / share size, and sends
BUY and SELL alerts. A market-wide backtester scores every strategy on recent
sessions and tells you when to switch.

> Educational tool, not financial advice. It never places orders.

## Features

- **Six switchable strategies:** Gap & Go, Bull Flag, Opening Range Breakout,
  VWAP Reclaim, Red-to-Green and Oversold Bounce. Switch from the dashboard
  dropdown or with one command.
- **Ranked picks:** stocks priced $1–20, up 5%+ on 2× normal volume, float under
  50M. Each pick is marked BUY NOW, SETTING UP or triggered, with an entry,
  stop, target and share size based on your risk per trade.
- **Live alerts:** macOS notifications and sounds for buys, sells (stop, target
  or trailing stop) and stop moves. Every alert is tracked as a paper trade.
- **Market-wide backtest:** replays every listed stock that gapped each day,
  using only what's known at the open.
- **Strategy-switch hint:** compares recent expectancy across strategies and
  suggests when to switch or sit out.
- **Data sources:** Alpaca (free plan), Schwab, or Yahoo with no key.

## Quick start

```bash
git clone <this repo> && cd trade
./daytrader/run.sh          # builds the environment, then opens the dashboard at http://localhost:8501
```

For better data, add free Alpaca paper-account keys to `daytrader/.env`. See
the setup steps below.

```bash
./daytrader/run.sh monitor            # live BUY/SELL alerts
./daytrader/run.sh scan --all         # every candidate vs every strategy
./daytrader/run.sh backtest --days 30
./daytrader/run.sh hint               # should I switch strategies?
./daytrader/run.sh switch gap_and_go
```

## Documentation

Full setup (Alpaca and Schwab keys), strategy rules, exits, configuration
and known limits are in [daytrader/README.md](daytrader/README.md).

## Layout

```
daytrader/
  run.sh            launcher (creates .venv on first run)
  cli.py            command-line interface
  dashboard.py      Streamlit dashboard
  config.yaml       filters, risk, exits, strategy parameters
  dtrader/
    data/           Alpaca, Schwab and Yahoo providers
    strategies/     one file per strategy + registry
    scanner.py      finds and ranks today's movers
    backtest.py     market-wide replay of recent sessions
    performance.py  strategy-switch hint
    alerts.py       live monitor and notifications
```

API keys live in `daytrader/.env`, and cached data in `daytrader/data/`. Git
ignores both.
