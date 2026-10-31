# Day Trade Scanner

Finds stocks to day trade in two markets, **US small caps** and **India (NSE
F&O stocks)**. For each one it gives a setup with entry, stop, target and share
size, sends BUY and SELL alerts to your phone, tracks the shares you buy, and
re-checks every morning which of 13 strategies has been working.

> Educational tool, not financial advice. It never places orders: you trade in
> your broker app and record what you did. Day trading is risky; always use a
> stop and size for the loss.

**Contents:**
[Start](#start) ·
[Data sources](#data-sources) ·
[The dashboard](#the-dashboard) ·
[Commands](#commands) ·
[How a stock becomes BUY NOW](#how-a-stock-becomes-buy-now) ·
[Setup odds](#setup-odds) ·
[News and earnings](#news-and-earnings) ·
[Strategies](#strategies) ·
[Exits and R](#exits-and-r) ·
[Your positions](#your-positions-and-when-to-sell) ·
[Risk limits](#daily-risk-limits) ·
[Strategy switching](#strategy-switching) ·
[Automation and alerts](#running-automatically-and-alerts) ·
[Phone access](#open-the-dashboard-from-your-phone) ·
[India](#india-whats-different) ·
[Configuration](#configuration) ·
[Limits to know](#limits-to-know)

## Start

```bash
./run.sh            # first run creates .venv (needs Python 3.10+), then opens http://localhost:8501
```

It works straight away on free Yahoo data. Copy `.env.example` to `.env` and
add keys for better data (next section). To have everything run by itself
every market day, see [Automation and alerts](#running-automatically-and-alerts).

## Data sources

With `data_source: auto` (the default), each market uses the best source you
have keys for:

| Market | Order of preference |
|---|---|
| US | Alpaca, then Schwab, then Yahoo |
| India | Groww (once today's approval is done), then Zerodha Kite (once logged in), then Yahoo NSE |

### Alpaca (US, recommended, free)

1. Sign up at <https://app.alpaca.markets>. A **paper** account is enough: no
   funding or approval needed.
2. In the Paper dashboard, open **API Keys** and press **Generate**.
3. Put the values in `.env` as `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`.

On the free plan the tool combines two Alpaca feeds:
- full-market (SIP) 1-minute bars up to 16 minutes ago, with years of history;
  the backtests use these;
- live bars from the IEX exchange for the newest minutes. IEX is a small share
  of volume, so the latest small-cap bars can be thin.

Before 9:30 ET, premarket gainers come from Yahoo. The Alpaca keys also
provide the US news feed. With a paid Algo Trader Plus plan, set
`alpaca.feed: sip` for fully real-time data.

### Schwab (US, optional)

1. At <https://developer.schwab.com>, create an app with **Market Data
   Production** and the callback URL `https://127.0.0.1:8182`. Wait for
   "Ready For Use"; approval can take a few days.
2. Put the key and secret in `.env` as `SCHWAB_APP_KEY` and `SCHWAB_APP_SECRET`.
3. Run `./run.sh auth` and log in to Schwab in the browser that opens. Repeat
   this every 7 days.

### Groww (India, about ₹499/month)

1. Subscribe to Groww's **Trading API**, then open **Groww Cloud → API Keys**.
2. Create an **API key + secret** and put them in `.env` as `GROWW_API_KEY` and
   `GROWW_API_SECRET`.
3. **Each trading morning, approve the key** on that page. The monitor picks
   up the approval within about 5 minutes and switches from Yahoo to Groww. To
   check right away, run `./run.sh --market india auth`.

Groww gives official live quotes and 1-minute history back to 2020. Its TOTP
option (`GROWW_TOTP_TOKEN` / `GROWW_TOTP_SECRET`) skips the daily approval,
but anyone with your `.env` could then trade on your account. Groww allows 150
token requests a day, so while approval is pending the tool retries at most
every 5 minutes.

### Zerodha Kite (India, alternative, ₹500/month)

1. Subscribe to the **Kite Connect** plan at <https://developers.kite.trade>.
   The free Personal plan has no market data.
2. Create an app with the redirect URL `http://127.0.0.1:5010/kite`, and put
   the key and secret in `.env` as `KITE_API_KEY` and `KITE_API_SECRET`.
3. **Each trading morning**, run `./run.sh --market india auth` and sign in to
   Zerodha yourself. The tool receives only a day token, never your password
   or TOTP.

If neither broker is ready when the India monitor starts, it pushes a
reminder and uses Yahoo until you log in or approve.

## The dashboard

Open `./run.sh` or http://localhost:8501. The sidebar switches between **US**
and **India**; each market keeps its own strategy, trades and settings. The
header shows whether the market is open, premarket or closed, and when it
next opens, plus the data source, active strategy and last scan time.

| Tab | What's there |
|---|---|
| **Picks** | **Buyable now** across all strategies, with odds, news and earnings. Below it, your active strategy's scan table (status, gap, relative volume, levels) and a candle chart with VWAP, EMA 9, levels and the stock's latest headlines. A red banner lists positions to sell; an amber one shows when a risk limit has paused BUY alerts. |
| **My positions** | Today's limits; your open positions (HOLD or SELL NOW, P&L, current stop) with sell, edit and delete; **Record a buy**, prefilled from a current or recent setup; closed trades with realized P&L, win rate and average R. |
| **Backtest** | Run a backtest over N sessions; a summary per strategy, sorted best-first; equity curves (your active strategy and the best 3, up to 4); every trade. |
| **Switch hint** | Which strategy is working lately, and a button to apply the suggestion. |
| **Alerts** | The monitor's paper trades, the alert log and closed paper trades. |
| **Strategies** | A card per strategy with its rules and parameters. |

Toggles in the sidebar: **Show all strategies in the scan table**, and
**Auto-refresh every 60 s**.

## Commands

Run them as `./run.sh <command>`, adding `--market india` for NSE. The
dashboard does the same things.

| Command | What it does |
|---|---|
| `scan` / `scan --all` / `scan --buyable` | Scan for your active strategy, for every strategy, or only what's buyable now (with odds, news and earnings). |
| `switch <strategy>` / `strategies` | Change or list strategies. |
| `backtest --days 30 [--strategy x] [--trades N]` | Replay recent sessions. |
| `hint [--refresh] [--apply]` | Which strategy is working lately; optionally switch. |
| `monitor [--once] [--session]` | Live alerts (`--session`: the daily job's mode). |
| `buy SYMBOL SHARES PRICE [--stop --target --strategy]` | Record a buy (default stop 2% below, target 2R above). |
| `positions` | Your positions: HOLD or SELL NOW and why. |
| `sell SYMBOL-or-id PRICE` | Record a sale. |
| `risk` | Today's P&L, trades and open positions against your limits. |
| `auth` | Log in to Schwab (US) or Groww / Zerodha (India). |

## How a stock becomes BUY NOW

1. **It's moving today (the universe).**
   - **US:** Alpaca's top gainers and losers plus the most-active stocks (with
     Yahoo's premarket gainers before 9:30 ET).
   - **India:** the ~220 NSE F&O stocks, ranked by % change and unusual volume.
   - **Both:** plus your watchlist and the last 3 sessions' movers.
2. **It passes the filters.**

   | | US | India |
   |---|---|---|
   | Price | $1–20 | ₹50–10,000 |
   | Move vs yesterday's close (either way) | ≥ 5% | ≥ 1.5% |
   | Volume vs 10-day average | ≥ 2× (not before the open) | ≥ 1.2× |
   | Float | ≤ 50M shares | not used |
   | Kept | top 25 | top 30 |

3. **A setup triggered.** Each strategy has a daily filter (for example, the
   gap size) and a minute-by-minute trigger, checked on **finished** minutes
   only. It sets the entry, a stop at the level that proves the setup wrong,
   and a 2R target. Setups with a stop more than 10% away are skipped, and so
   are entries after the cutoff (US 11:30 ET, India 13:00 IST).
4. **It's still buyable.** The trade is replayed from the trigger to now, with
   the same exits as the backtest. It's **BUY NOW** only while all of these
   hold:
   - the trade is still open;
   - price is above the stop;
   - price is **no more than 0.5R past the entry**;
   - it's before the cutoff.

Other statuses:
- **SETTING UP:** within 5% of the trigger.
- **triggered HH:MM [reason]:** no longer buyable, with what happened (stop,
  target, ran past entry, and so on).
- **qualifies:** passes the daily filter, but no setup yet.
- **no setup.**

**Buyable now**, at the top of Picks, lists every BUY NOW setup across all 13
strategies. Your active strategy comes first, then the strategies with the
best recent R per trade (losing ones in red), then the setups closest to their
entry. The BUY NOW count and the monitor's BUY alerts cover your **active
strategy only**.

## Setup odds

Each buyable setup gets a **Verdict** with two readings behind it.

- **Reach (target / stop / neither):** from the current price, the rest of
  today is replayed 10,000 times using the stock's own 1-minute moves at the
  same time of day over its last 20 sessions, scaled to how volatile it is
  today. The replays assume **no trend**: 20 sessions can't measure one, and
  the average move per minute is mostly noise. So Reach answers "can price
  realistically get to the target, or the stop, before the time exit?"
- **Track record:** the same rule re-run on this stock's last 20 sessions: how
  often it fired, how many hit the target, and the average R.
- **Verdict:**
  - **Target out of reach** if it's 50% or more likely to still be open at the
    time exit;
  - **No track record** if the rule fired fewer than 5 times on this stock;
  - otherwise **Favourable** (average R ≥ +0.2), **Unfavourable** (≤ −0.1) or
    **Coin flip**.

The idea comes from the "Time Machine" in
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill). The
odds are a sanity check, not a forecast.

## News and earnings

Each buyable setup shows:
- **Earnings:** *today* (red) or *next session* (amber), when results could gap
  the stock through your stop; or *last session*, when the results are often
  the catalyst behind the move.
- **News:** the number of headlines since the previous close, and the latest
  one. A gap with fresh news tends to follow through better than one without.

The chart section lists the selected stock's recent headlines, with links, and
BUY alerts include the earnings warning and the latest headline.

Sources:
- US headlines: Alpaca's news API (with your Alpaca keys), else Yahoo RSS.
- India headlines: Google News, searched by company name.
- Earnings dates: Yahoo.

## Strategies

| Name | Idea | Entry | Stop |
|---|---|---|---|
| `gap_and_go` | 10%+ gap on a catalyst (NSE 2%) | first break of the premarket high (NSE: 5-min opening high), above VWAP | low of last 3 candles |
| `bull_flag` | first pullback on a stock up 10%+ | first candle to a new high after a 1–5 bar, light-volume pullback | pullback low |
| `orb` | opening range breakout (US 5 min, NSE 15) | break of the range high, above VWAP | range low (mid if wide) |
| `vwap_reclaim` | gapper dips under VWAP | close back above VWAP on volume | low of the dip |
| `red_to_green` | red on the day | cross back above yesterday's close | low of day |
| `oversold_bounce` | 8%+ flush, RSI < 25 | reversal candle | flush low, target VWAP |
| `vwap_bounce` | trending above VWAP, pulls back to it | long lower wick through VWAP on 1.5× volume, closes above | wick low |
| `first_hour_trend` | first hour closed 3%+ up (NSE 1.2%), above VWAP | break of a fresh 20 EMA pullback candle | low of last 3 candles |
| `stop_hunt` | dips below yesterday's low | reclaim of yesterday's low | sweep low |
| `rip_and_dip` | gapper breaks the premarket high, dips back, in the first 15 min | reclaim of the premarket high | dip low |
| `gap_down_bounce` | gap-down 15%+ (NSE 4%+) flushes to a new low | first green candle back over the 9 EMA | flush low |
| `opening_reversal` | sold off 2%+ from the open (NSE 0.8%) | first break of the last 3 candles' high, 15–35 min after the open | low of day |
| `orb_retest` | close above the 5-min range high (NSE 15), retest holds | break of the retest candle's high | retest low |

The last seven come from the r/Daytrading "strategies that actually work" post,
long side only. Not added from that post:
- short trades;
- anything needing options, Level 2 or dark-pool data;
- news-event trades (FOMC fade, earnings reversal);
- premarket and after-hours setups.

In the first backtests, none of the seven beat the leaders (US Gap & Go; India
ORB and Gap & Go). They stay in so the switch hint keeps measuring them.

Every strategy was checked for lookahead: each signal reappears identically
when the strategy only sees data up to that minute.

**Add your own:** copy a file in `dtrader/strategies/`, implement `check()`
(optionally `qualifies()` and `watch_level()`), and register it in
`dtrader/strategies/__init__.py`. The scanner, backtest, odds, hint, alerts and
dashboard pick it up automatically.

## Exits and R

**R** is the risk per share: entry minus stop. Results are in R, so +1R means
you made what you risked. The target is entry + 2R (`risk.reward_risk`).
Shares are your risk per trade divided by R, capped at 25% of the account.

All strategies, in the backtest, the paper trades and your positions, exit the
same way:
- the stop is hit (including the entry minute closing below it), or the target;
- at +1R the stop moves to the entry (breakeven);
- after +1R, a 1-minute close below the 9 EMA sells (trailing exit);
- time exit: US 15:50 ET, India 15:10 IST;
- at most 2 trades per stock per day (`exits.max_trades_per_day`).

## Your positions and when to sell

Record each buy in **My positions → Record a buy** (pick a buyable setup to
prefill everything, then set your real fill price and time), or with
`./run.sh buy JSWENERGY 100 505.2 --stop 491 --target 532`.

The list includes setups that were buyable in the last hour, marked "was
buyable until HH:MM". The one you pick stays selected, with your typed values,
through refreshes.

Each position shows **HOLD** or **SELL NOW** with the reason, live P&L, R and
the current stop. It follows the exits above, and a position held overnight
is flagged.

The monitor pushes **"Sell your 100 JSWENERGY"** once when an exit triggers,
for any strategy, plus a note when your stop moves. When you've sold, press
**Record sale**, or run `./run.sh sell JSWENERGY 512`; the trade moves to
Closed trades.

Your positions are stored in `data/[india/]my_positions.json`, and closed
trades in `my_trades.jsonl`. These are separate from the monitor's paper
trades.

## Daily risk limits

Set in `risk_limits` (per market) and checked against **your recorded
positions**:

| Limit | US | India |
|---|---|---|
| `max_daily_loss`: today's realized + open P&L | $300 | ₹7,500 |
| `max_trades_per_day`: buys recorded today | 4 | 4 |
| `max_open_positions` | 3 | 3 |

When a limit is hit:
- BUY alerts pause, and the monitor pushes "Risk limit reached" once;
- sell alerts keep working;
- the dashboard shows an amber notice and a warning in the buy form.

BUY alerts resume by themselves when the limit lifts, for example after you
close a position. `./run.sh risk` shows the status. Set a limit to 0 to turn it
off.

## Strategy switching

The **switch hint** compares each strategy's average R per trade over the last
`lookback_days` (10) finished sessions, combining backtest trades with the
monitor's paper trades, which count double. A strategy needs `min_trades` (10)
trades to be judged, and it suggests a switch when your strategy loses money
or trails the best one by `margin_r` (0.15R). If everything is negative, it
says so.

**Each morning** the daily job re-runs the backtest, pushes a "Strategy check"
with the top strategies, and, because `auto_switch: true` by default,
**switches automatically** when the hint suggests it, pushing the reason. Set
`auto_switch: false` to switch by hand, or `refresh_before_session: false` to
skip the morning backtest.

**The backtest universe:** with Alpaca (US) or any India source, it replays
the strongest gappers each day (up to 60 US / 40 India) from the whole market
or the F&O list. A stock counts as a gapper using only what's known at the
open: it gapped by at least the minimum move, or ran 20%+ the day before. The
backtest charges slippage per side (US 0.1%, India 0.08% including charges)
and skips today until the session has closed.

## Running automatically and alerts

```bash
./scripts/autostart.sh install         # US monitor 18:00, India 09:00 (local, Mon–Fri), dashboard always on
./scripts/autostart.sh status          # what's scheduled and running, last log lines, phone URLs
./scripts/autostart.sh run-now india   # start today's India (or us) job now
./scripts/autostart.sh uninstall
```

Change the start times with, for example,
`START_TIME=17:30 START_TIME_INDIA=08:55 ./scripts/autostart.sh install`.

Each job:
1. runs the morning strategy check;
2. monitors every minute until 5 minutes after the close;
3. skips that market's holidays and knows its early closes, using the market's
   own timezone, so US daylight saving needs no edits;
4. keeps the Mac awake (`caffeinate`).

If the Mac was asleep at the start time, the job runs when it wakes. Closing
the lid on battery still sleeps the Mac. Logs are in `data/monitor.log` and
`data/india/monitor.log`.

**Phone alerts (ntfy):**
1. Install the free **ntfy** app.
2. Subscribe to the topic in `NTFY_TOPIC` in `.env`; keep it hard to guess.
3. Choose which alerts reach your phone with `alerts.ntfy_kinds`: BUY, SELL,
   WATCH, INFO.

Pushes for India start with `[India]`. macOS notifications and sounds also
fire on the Mac.

## Open the dashboard from your phone

The always-on dashboard is reachable from other devices only when
`DASHBOARD_PASSWORD` is set in `.env`. After you log in, the URL carries a
sign-in token valid for 30 days, so you can bookmark it. Delete
`data/dashboard_secret` to sign out every device.

- **Same Wi-Fi:** use the "Phone on same Wi-Fi" address from `autostart.sh
  status`. It's plain HTTP and the address can change.
- **Anywhere (recommended, encrypted):**
  1. Install Tailscale on the Mac and your phone, signed in to the same account.
  2. Restart the dashboard:
     `launchctl kickstart -k gui/$(id -u)/com.daytrader.dashboard`
  3. Use the "Phone anywhere (Tailscale)" address from `status`.

## India: what's different

- **Universe:** ~220 NSE F&O stocks: liquid, no fixed circuit limits, and
  intraday leverage. The list refreshes daily from NSE.
- **No premarket:** Gap & Go and Rip & Dip use the first 5 minutes' high
  instead of the premarket high.
- **Smaller moves:** thresholds are scaled for large caps (see `india:` in
  `config.yaml`).
- **Hours:** 9:15–15:30 IST, with no new entries after 13:00 and flat by
  15:10, before the brokers' intraday (MIS) square-off around 15:20.
- **Rupees:** ₹2,500 risk per trade on a ₹5 lakh account by default.
- **Separate everything:** strategy, backtest, positions and alerts live in
  `data/india/`.

## Configuration

Everything is in `config.yaml`. The top level is the US; the `india:` section
overrides any key for NSE.

| Section | Controls |
|---|---|
| `data_source`, `alpaca`, `schwab`, `india.kite` | where data comes from |
| `universe` | price range, minimum move, relative volume, float, number of candidates, watchlist |
| `risk` | account size, risk per trade, max position %, reward:risk |
| `risk_limits` | daily loss, trades per day, open positions |
| `exits` | breakeven and trailing thresholds, time exit, entry cutoff, trades per stock per day |
| `strategies` | per-strategy parameters |
| `switch_hint` | lookback, minimum trades, margin, morning refresh, auto-switch |
| `alerts` | poll interval, desktop notifications, sound, which kinds go to ntfy |
| `backtest` | slippage, universe mode, liquidity floor, stocks per day |

Keys and passwords go in `.env` (see `.env.example`). The theme is in
`.streamlit/config.toml`.

## Limits to know

- **Free data.** Alpaca's free plan shows the newest minutes from IEX only,
  which can be thin on small caps. Yahoo is unofficial, reports 0 premarket
  volume, and keeps only about 30 days of 1-minute history (Groww has
  history from 2020).
- **Backtests** are a ranking of strategies over recent sessions, not a
  forecast. Fills assume the trigger price plus slippage, and real fills on
  fast small caps can be worse.
- **Auto-switching** can still follow noise when samples are small. Raise
  `min_trades` or `margin_r` for steadier switching.
- **Setup odds** assume no trend and use simple stop, target and time exits.
- **News for India** is a name search, so it occasionally picks up an
  unrelated story.
- **Float** comes from Yahoo (US only) and is cached for a week.
