# Day Trade Scanner

Finds small-cap momentum stocks to day trade, ranks them for the strategy you
choose, gives entry / stop / target / share size, and fires BUY and SELL
alerts. A backtester scores every strategy on recent sessions and tells you
when to switch.

> Educational tool, not financial advice. It never places orders.

## Setup

```bash
cd daytrader
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

It works right away on free Yahoo data, which may be delayed.

### Alpaca (recommended, free)

1. Sign up at <https://app.alpaca.markets>. A paper account is enough: no
   funding or brokerage approval needed.
2. In the **Paper** dashboard, open **API Keys** → **Generate**.
3. Put both values in `.env` as `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`.

On the free plan the tool combines two Alpaca feeds:
- full-market (SIP) 1-minute bars up to 16 minutes ago, which is what the
  backtests use, with years of history;
- real-time bars from the IEX exchange for the newest minutes. IEX is only a
  small share of volume, so the latest small-cap bars can be thin.

Before 9:30 ET, premarket gainers come from Yahoo. Alpaca's movers list resets
at the open, and its IEX premarket prints on small caps are sparse. With a
paid Algo Trader Plus plan, set `alpaca.feed: sip` in `config.yaml` for fully
real-time data.

### Schwab (optional)

1. Sign in at <https://developer.schwab.com> with your Schwab login.
2. Create an app with the **Market Data Production** and **Accounts and Trading
   Production** products, and set the callback URL to `https://127.0.0.1:8182`.
3. Wait for the app to show **Ready For Use**. Approval can take a few days.
4. `cp .env.example .env` and paste in the App Key and Secret.
5. Run `.venv/bin/python cli.py auth`. A browser opens. Log in to Schwab and
   approve the app; your browser will warn about the local certificate, which
   is expected. The token is saved to `data/schwab_token.json`. Schwab makes
   you repeat this login every 7 days.

With `data_source: auto` in `config.yaml`, the tool uses Alpaca if its keys
are set, then Schwab, then Yahoo.

## Daily use

The launcher creates the virtualenv on first run:

```bash
./run.sh                 # dashboard at http://localhost:8501
./run.sh monitor         # live BUY/SELL alerts
./run.sh scan --all      # any cli.py command
```

Or call the tools directly:

```bash
.venv/bin/streamlit run dashboard.py      # dashboard: picks, charts, backtest, hint, alerts
.venv/bin/python cli.py monitor           # live BUY/SELL alerts (desktop notification + sound)
.venv/bin/python cli.py scan --all        # every candidate vs every strategy
.venv/bin/python cli.py switch bull_flag  # change strategy (dashboard dropdown does the same)
.venv/bin/python cli.py backtest --days 20
.venv/bin/python cli.py hint --refresh    # should I switch? (--apply to switch automatically)
```

Suggested routine: run `hint --refresh` before the open, start `monitor` at
around 7:00 ET (premarket gappers), and keep the dashboard open for charts.
Set `switch_hint.auto_switch: true` to let `monitor` pick the best recent
strategy on start.

## Strategies

| name | idea | entry | stop |
|---|---|---|---|
| `gap_and_go` | 10%+ premarket gap on a catalyst | first break of the premarket high, above VWAP | low of last 3 candles |
| `bull_flag` | first pullback on a stock up 10%+ | first candle making a new high after a 1-5 bar, light-volume pullback | pullback low |
| `orb` | opening range breakout (5 min default) | break of the range high, above VWAP | range low (mid if wide) |
| `vwap_reclaim` | gapper dips under VWAP | close back above VWAP on volume | low of the dip |
| `red_to_green` | red on the day, day-2 runners | cross back above yesterday's close | low of day |
| `oversold_bounce` | mean reversion after an 8%+ flush, RSI < 25 | reversal candle | flush low, target VWAP |

All strategies share the same exits: stop, 2R target, breakeven at +1R, and
after +1R a sell on a 1-min close below the 9 EMA. Everything is flat by
15:50, with no new entries after 11:30. The strategies are long-only.

**Add your own:** copy a file in `dtrader/strategies/`, implement `check()`
(and optionally `qualifies()` / `watch_level()`), then register it in
`dtrader/strategies/__init__.py`. The scanner, backtester, hint, alerts and
dashboard pick it up automatically.

## How the switch hint works

It looks at each strategy's expectancy (average R per trade) over the last
`lookback_days` sessions, combining backtest trades with live tracked trades
from `monitor`, which count double. It suggests a switch when your strategy
loses money or trails the best one by `margin_r`. If everything is negative,
it suggests sitting out.

## Limits to know

- **Backtest sample.** It only tests symbols the tool has seen: scan history,
  your watchlist and today's movers. Run the scanner or monitor daily so the
  history grows. Treat results as a ranking of strategies, not a forecast.
- **Yahoo data.** It reports 0 premarket volume, gives only about 30 days of
  1-minute history, and quotes may be delayed. Schwab gives real-time data and
  about 48 days of history.
- **Float** comes from Yahoo for both sources and is cached for a week.
- **Fills.** Breakout fills assume you get the trigger price plus 0.1%
  slippage. Real fills on fast small caps can be worse.
