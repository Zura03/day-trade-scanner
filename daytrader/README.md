# Day Trade Scanner

Finds stocks to day trade in two markets: **US small-cap momentum** and
**India (NSE F&O stocks)**. It ranks them for the strategy you choose, gives
entry / stop / target / share size, and fires BUY and SELL alerts. A backtester scores every strategy on recent sessions and tells you
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

### India: Groww (optional, about ₹499/month)

India works right away on free Yahoo NSE data, which was near real-time in
testing but is unofficial. If you trade with Groww, its Trade API gives
official live quotes and 1-minute history back to 2020:

1. Subscribe to Groww's **Trading API**, then open **Groww Cloud → API Keys**.
2. Create an **API key + secret** and put them in `.env` as `GROWW_API_KEY` and
   `GROWW_API_SECRET`.
3. **Each trading morning**, approve the key on that API Keys page. The
   monitor picks up the approval within 2 to 5 minutes and switches from Yahoo
   to Groww. To check right away, run `./run.sh --market india auth`.

Groww's other option, TOTP (`GROWW_TOTP_TOKEN` / `GROWW_TOTP_SECRET`), skips
the daily approval. But anyone with your `.env` could then trade on your
account, so only use it if the Mac is secure.

The day's token is cached in `data/india/groww_token.json`. Groww allows only
150 token requests a day, so while approval is pending the tool retries at
most every 5 minutes.

### India: Zerodha Kite (alternative, ₹500/month)

If you use Zerodha instead (Groww is preferred when both are set):

1. Subscribe to the **Kite Connect** plan at <https://developers.kite.trade>.
   Market data and historical candles need the paid Connect plan; the free
   Personal plan has none.
2. Create an app and set its redirect URL to `http://127.0.0.1:5010/kite`.
3. Put the API key and secret in `.env` as `KITE_API_KEY` and
   `KITE_API_SECRET`.
4. **Each trading morning**, run `./run.sh --market india auth`. Zerodha's login
   page opens; sign in yourself. The tool never sees your password or TOTP,
   and it saves the day's token to `data/india/kite_token.json`. Kite tokens
   expire around 6:00 IST every day.

If you haven't logged in or approved yet when the India monitor starts, it
pushes a reminder and uses Yahoo data. It switches to your broker shortly
after you do.

## Daily use

The launcher creates the virtualenv on first run:

```bash
./run.sh                 # dashboard at http://localhost:8501 (US/India switch in the sidebar)
./run.sh monitor         # live BUY/SELL alerts
./run.sh scan --all      # any cli.py command

./run.sh --market india scan --all     # every command works for India with --market india
./run.sh --market india backtest --days 20
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

### Run automatically every market day (macOS)

```bash
./scripts/autostart.sh install          # US monitor 18:00, India monitor 09:00, dashboard always on
./scripts/autostart.sh status           # what's scheduled / running, last log lines
./scripts/autostart.sh run-now india    # start today's India (or us) job immediately
./scripts/autostart.sh uninstall
```

Start times are local Mac time, Mon-Fri. Change them with, for example,
`START_TIME=17:30 START_TIME_INDIA=08:55 ./scripts/autostart.sh install`.

Each job runs `monitor --session`, which:
- reads that market's exchange calendar, exits right away on its holidays,
  and knows about early closes;
- uses the market's own timezone, so the US daylight-saving change needs no
  edits;
- stops 5 minutes after the close;
- runs under `caffeinate`, so the Mac doesn't idle-sleep during the session.

If the Mac is asleep at the start time, it runs as soon as the Mac wakes.
Closing the lid on battery still puts the Mac to sleep. Logs are in
`data/monitor.log` for the US and `data/india/monitor.log` for India.

### Open the dashboard from your phone

`autostart.sh install` also keeps the dashboard running in the background. It
starts at login and restarts if it crashes.

The dashboard is reachable from other devices only when `DASHBOARD_PASSWORD`
is set in `.env`. Without it, the dashboard binds to localhost only. After you
log in, the URL carries a sign-in token, so you can bookmark it. The token is
valid for 30 days, and anyone with that URL can get in until it expires, so
don't share it. To sign out every device at once, delete
`data/dashboard_secret`.

The Wi-Fi address is plain HTTP, so someone on the same network could read
the password or token. The Tailscale address is encrypted, so prefer it,
even at home.

**Same Wi-Fi:** run `./scripts/autostart.sh status`, then open the
"Phone on same Wi-Fi" address it prints, for example
`http://192.168.0.3:8501`. Your router can change that address, so check
`status` again if it stops working.

**From anywhere, with Tailscale (free, private, nothing exposed to the
internet):**
1. Install Tailscale on the Mac from <https://tailscale.com/download> and sign in.
2. Install the Tailscale app on your phone and sign in with the same account.
3. Restart the dashboard so it picks up Tailscale:
   `launchctl kickstart -k gui/$(id -u)/com.daytrader.dashboard`
4. `./scripts/autostart.sh status` now also prints a "Phone anywhere
   (Tailscale)" address, such as `http://your-mac.tailnet-name.ts.net:8501`.
   That address works whenever Tailscale is on, on both devices.

### Phone alerts (ntfy)

1. Install the free **ntfy** app (iOS or Android).
2. Subscribe to the topic named by `NTFY_TOPIC` in `.env`. Anyone who knows
   the topic name can read your alerts, so keep it hard to guess.
3. Choose which alerts reach your phone with `alerts.ntfy_kinds` in
   `config.yaml`: BUY, SELL, WATCH, and INFO (stop moves, start and finish).

Each daily job starts by re-running the backtest for its market and pushing
a "Strategy check" with the top strategies. With `switch_hint.auto_switch:
true`, the default, it also switches the active strategy when the hint
suggests it, and pushes "Switched to ..." with the reason. This is skipped on
holidays. If the check fails, the monitor keeps the current strategy and still
starts.

## India: what's different

- **Universe:** about 220 NSE F&O stocks, the most liquid names. They have no
  fixed circuit limits, tight spreads, and intraday leverage. The list
  refreshes daily from NSE.
- **No premarket:** NSE has only a short pre-open auction. Gap & Go uses the
  high of the first 5 minutes in place of the premarket high.
- **Smaller moves:** gaps and filters are scaled for large caps, for example a
  2% gap for Gap & Go and a 15-minute ORB.
- **Exits:** flat by 15:10, before brokers' intraday (MIS) square-off around
  15:20. No new entries after 13:00.
- **Costs:** the backtest charges 0.08% per side to cover slippage and
  STT/exchange fees.
- **Rupees:** risk is set in rupees in the `india:` section of `config.yaml`
  (default ₹2,500 per trade on a ₹5 lakh account).

Each market keeps its own active strategy, backtest, switch hint, tracked
trades and alerts. India data lives in `data/india/`. Phone pushes for India
are prefixed with `[India]`.

## Strategies

| name | idea | entry | stop |
|---|---|---|---|
| `gap_and_go` | 10%+ premarket gap on a catalyst | first break of the premarket high, above VWAP | low of last 3 candles |
| `bull_flag` | first pullback on a stock up 10%+ | first candle making a new high after a 1-5 bar, light-volume pullback | pullback low |
| `orb` | opening range breakout (5 min default) | break of the range high, above VWAP | range low (mid if wide) |
| `vwap_reclaim` | gapper dips under VWAP | close back above VWAP on volume | low of the dip |
| `red_to_green` | red on the day, day-2 runners | cross back above yesterday's close | low of day |
| `oversold_bounce` | mean reversion after an 8%+ flush, RSI < 25 | reversal candle | flush low, target VWAP |
| `vwap_bounce` | trending above VWAP, pulls back to it | long lower wick through VWAP on 1.5x volume, closes above | wick low |
| `first_hour_trend` | first hour closed 3%+ up (NSE 1.2%) and above VWAP | break of a fresh 20 EMA pullback candle | low of last 3 candles |
| `stop_hunt` | dips below yesterday's low (stop hunt) | reclaim of yesterday's low | sweep low |
| `rip_and_dip` | gapper breaks the premarket high in the first 15 min, dips back | reclaim of the premarket high | dip low |
| `gap_down_bounce` | bagholder bounce: gap-down 15%+ (NSE 4%+) flushes to a new low | first green candle back over the 9 EMA | flush low |
| `opening_reversal` | sold off 2%+ from the open (NSE 0.8%) | first break of the last 3 candles' high, 15-35 min after the open | low of day |
| `orb_retest` | close above the 5-min range high (NSE 15), retest holds | break of the retest candle's high | retest low |

The last seven come from the r/Daytrading "strategies that actually work" post,
long side only. The rest of that post wasn't added:
- short trades (broken-parabolic short, fake-halt short, merger-arb short);
- setups that need options, Level 2 or dark-pool data;
- news-event trades (FOMC fade, earnings reversal);
- premarket and after-hours setups.

In the first backtests (US over 30 sessions, India over 15), none of the seven
beat Gap & Go (US) or ORB and Gap & Go (India). Most were negative. They stay
in the list so the switch hint keeps measuring them as conditions change.

All strategies share the same exits: stop, 2R target, breakeven at +1R, and
after +1R a sell on a 1-min close below the 9 EMA. Everything is flat by
15:50, with no new entries after 11:30. The strategies are long-only.

**Add your own:** copy a file in `dtrader/strategies/`, implement `check()`
(and optionally `qualifies()` / `watch_level()`), then register it in
`dtrader/strategies/__init__.py`. The scanner, backtester, hint, alerts and
dashboard pick it up automatically.

## Tracking the shares you buy

The tool never places orders. When you buy, record it so it can tell you when
to sell.

- **Dashboard → 💼 My positions → Record a buy.** Pick one of the buyable
  setups to prefill the symbol, price, stop, target and size, or enter it by
  hand. Set the time you bought. The list also includes setups that were
  buyable in the last hour, marked "was buyable until HH:MM". Once you pick
  one, it stays selected, with your typed values, even if a refresh drops it
  from BUY NOW.
- **Terminal:** `./run.sh buy JSWENERGY 100 505.2 --stop 491 --target 532`.
  Add `--market india` for NSE. The stop defaults to 2% below your price and
  the target to 2R above.

Each position shows **HOLD** or **SELL NOW** with the reason, live P&L and the
current stop. It uses the same exit rules as the backtest:
- the stop or target is hit;
- at +1R the stop moves to your entry;
- after +1R, a 1-minute close below the 9 EMA is a sell;
- the time exit flattens it at the end of the day;
- a position held overnight is flagged, since these are day trades.

The monitor pushes **"SELL: Sell your 100 JSWENERGY"** once when an exit
triggers, and an INFO message when your stop moves. A red banner on the
Picks tab lists anything to sell.

When you've sold, press **Record sale** in the dashboard, or run
`./run.sh sell JSWENERGY 512`. The trade moves to *Closed trades*, which
shows realized P&L, win rate and average R. `./run.sh positions` prints
everything in the terminal. Your positions are stored in
`data/[india/]my_positions.json`, and closed trades in `my_trades.jsonl`.

## Setup odds (in Buyable now and BUY alerts)

Every buyable setup gets three extra readings, inspired by the "Time Machine"
in [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill):

- **Reach (target / stop / neither):** from the current price, the rest of
  today is replayed 10,000 times. Each future minute is drawn from this stock's
  own 1-minute bars at the same time of day over its last 20 sessions, scaled
  to how volatile the stock is today. The replays assume no trend: 20 sessions
  are too few to measure one, and the average move per minute is mostly noise
  that adds up to a fake trend over a few hours. So Reach tells you whether
  the target or the stop is realistically within range before the time exit,
  not whether the trade has an edge.
- **Track record:** the same strategy rule re-run on this stock's last 20
  sessions: how often it fired, how often it hit the target, and its average R.
- **Verdict:**
  - **Target out of reach** if it's likely to still be open at the time exit
    (50% or more);
  - **No track record** if the rule fired fewer than 5 times on this stock;
  - otherwise **Favourable** (average R +0.2 or better), **Unfavourable**
    (−0.1 or worse) or **Coin flip**.

`./run.sh scan --buyable` shows the same columns. The BUY push includes the
verdict and the reach odds. The odds are a sanity check, not a forecast.

## What the statuses mean

- **BUY NOW:** the setup triggered and is still buyable. The trade it started
  hasn't hit its stop, target or trailing exit, price is above the stop and
  no more than 0.5R past the entry, and it's before `no_new_entries_after`.
- **SETTING UP:** within 5% of the price that would trigger the setup.
- **triggered HH:MM [reason]:** it triggered but is no longer buyable. The
  reason is what happened to the trade: stop, target, lost 9 EMA, ran past
  entry, and so on.
- **qualifies:** passes the strategy's daily filters, but no setup yet.

The monitor sends one BUY alert per setup, at the current price.

The dashboard's **Buyable now - all strategies** section, at the top of the
Picks tab, lists every BUY NOW setup across all six strategies. Each row shows:
- the strategy's recent R per trade, in red if it has been losing money;
- how far price is past the entry, in R;
- the entry, stop, target and share size.

Your active strategy is starred; it's the only one the monitor alerts on. From
the terminal, run `./run.sh scan --buyable` (add `--market india` for NSE).

## How the switch hint works

It looks at each strategy's expectancy (average R per trade) over the last
`lookback_days` sessions, combining backtest trades with live tracked trades
from `monitor`, which count double. It suggests a switch when your strategy
loses money or trails the best one by `margin_r`. If everything is negative,
it suggests sitting out.

The hint changes only when a new backtest runs (or a live trade closes). The
daily jobs re-run it before each session; you can also refresh it with the
dashboard's **Run backtest** button or `hint --refresh`. To keep the
automatic refresh but switch by hand, set `auto_switch: false`. To turn the
morning refresh off entirely, set `refresh_before_session: false`.

Small samples can make auto-switching flip back and forth on noise. A
strategy needs `min_trades` (10) trades in the window to be judged, and must
beat the active one by `margin_r` (0.15R). Raise those for steadier
switching, especially for India, where there are fewer gappers per day.

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
