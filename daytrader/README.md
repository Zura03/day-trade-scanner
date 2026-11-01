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
[Every morning](#every-morning-one-command) ·
[The dashboard](#the-dashboard) ·
[Commands](#commands) ·
[How a stock becomes BUY NOW](#how-a-stock-becomes-buy-now) ·
[Setup odds](#setup-odds) ·
[News and earnings](#news-and-earnings) ·
[Strategies](#strategies) ·
[Exits and R](#exits-and-r) ·
[Your positions](#your-positions-and-when-to-sell) ·
[Daily capital and costs](#daily-capital-and-costs) ·
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
| India | Fyers (once logged in today), then Groww (once today's approval is done), then Zerodha Kite, then Yahoo NSE |

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

### Fyers (India, free)

1. Open a Fyers account, then go to the API dashboard
   (<https://fyers.in/web/api-dashboard/user-apps>) and click **Create
   non-trading app**. Give it any name and set its redirect URL to
   `http://127.0.0.1:5011/fyers`. A non-trading app gets quotes and history
   and needs no static IP; the trading app (for placing orders) needs one, and
   this tool never places orders.
2. Put the app's **App ID** (looks like `XXXXXXXX-100`) and **App Secret** in
   `.env` as `FYERS_APP_ID` and `FYERS_SECRET_KEY`.
3. **Each trading morning between 6:30 and 9:15**, run `./run.sh today` (or
   just the login: `./run.sh --market india auth`).
   A browser opens the Fyers login; sign in yourself. The tool receives only
   a day token, never your password, PIN or TOTP. Tokens expire at 6:30 every
   morning.

The free plan allows 5 data calls a second, 50 a minute and **5,000 a day**,
and going over the per-minute limit more than 3 times blocks you for the rest
of the day. The tool keeps well inside that:

- calls are paced to 3 a second and 40 a minute, shared between the dashboard
  and the monitor;
- it stops at 4,500 calls a day (`data/india/fyers_calls.json`) and switches
  to Yahoo until the next day;
- live quotes cost 1 call per 50 stocks, but 1-minute bars cost 1 call per
  stock, so today's bars are refetched only as often as the remaining budget
  allows: with 30 candidates, about every 2 minutes. A signal can show up to
  about 2 minutes later than with an unlimited feed;
- average volumes for the ~220 F&O stocks come from one Yahoo batch download
  instead of 220 Fyers calls, and backtest history before the open may not use
  more than 2,000 calls, keeping the rest for the session.

The official `fyers-apiv3` SDK isn't used: it pins old library versions that
break the Groww SDK, so the tool calls Fyers' REST API directly.

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

If no broker is ready when the India monitor starts, it pushes a
reminder and uses Yahoo until you log in or approve.

### Every morning: one command

```bash
./run.sh today
```

Run it any time between 6:30 and 9:15 IST on a trading day. Fyers expires
every login at 6:30, so a login made earlier would die before the open; if
you run it between 5:15 and 6:30 it waits for 6:30 and then logs in. It then:

- opens the Fyers login in your browser (skipped if today's login is done,
  or the market is closed);
- records any Fyers trades not yet in My positions;
- starts the dashboard if it isn't running, and the India monitor if the
  market is already open and it isn't running;
- checks phone alerts are set up;
- prints today's strategy and what the switch hint says, your capital, risk
  and loss limit, any positions left open, and the dashboard links.

The US market needs nothing daily: Alpaca keys don't expire.

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
| **Settings** | Daily capital; risk per trade, max position, loss limit, max share price, trades per day and open positions auto-calculated from it (each can be set manually); broker; cost per trade and a broker comparison. |

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
| `sync` | India: record today's Fyers buys and sells as positions. |
| `today` | India morning routine in one command: Fyers login (waits for 6:30 if it's earlier), trade sync, checks the dashboard, monitor and phone alerts, then shows today's plan. |
| `sell SYMBOL-or-id PRICE` | Record a sale. |
| `risk` | Today's P&L, trades and open positions against your limits. |
| `budget` | Daily capital and what it implies: risk per trade, max position, loss limit, price cap, cost per trade. |
| `auth` | Log in to Schwab (US) or Fyers / Groww / Zerodha (India). |

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

### Automatic recording from Fyers (India)

Once you've logged in to Fyers for the day, you don't need to record India
trades at all. The monitor (every minute) and the dashboard (every 30
seconds) read your Fyers tradebook and record each fill:

- **A buy** opens a position. If the stock was a buyable setup in the last 90
  minutes, its strategy, stop and target are used. Otherwise the stop is 1%
  below your price (`india.fyers.default_stop_pct`) with a 2R target, and
  the position is marked "check it", so adjust the stop in My positions.
- **Another buy** of a stock you hold from today adds to it at the average
  price. A buy you had already recorded by hand (same stock and shares, within
  10 minutes) is linked to that row instead of being added twice.
- **A sell** closes that stock's positions, oldest first, partly if you sold
  part.
- Futures and options, and sells with nothing to close (short selling), are
  ignored.

You get a phone push for each recorded trade. `./run.sh --market india sync`
does the same once from the terminal. The tradebook is a user-info API with
its own 100,000-calls-a-day limit, so it doesn't use the 5,000 data calls.
Turn it off with `india.fyers.sync_trades: false`.

Your positions are stored in `data/[india/]my_positions.json`, and closed
trades in `my_trades.jsonl`. These are separate from the monitor's paper
trades.

## Daily capital and costs

Set **one number, the amount you trade per day**, in the dashboard's
**Settings** tab. Every other money setting is **auto-calculated** from it and
shown as an editable value. Type over any of them to set it **manually**; a
manual value keeps your number when daily capital changes, and **Reset to
auto** brings back the rule. Settings are saved per market to
`data/[india/]settings.json`.

| Value | Auto rule | With ₹5,000 a day |
|---|---|---|
| Risk per trade | 1% of daily capital | ₹50 lost on a stop-out |
| Max position | 100% of daily capital | ₹5,000 in one trade |
| Daily loss limit | 3% of daily capital | ₹150, then BUY alerts pause |
| Max share price | max position ÷ 2 shares | ₹2,500 |
| Trades per day | daily loss limit ÷ risk per trade | 3 |
| Open positions | daily capital ÷ max position | 1 |

Each rule uses the final values above it: set risk per trade to ₹80 by hand
and trades per day becomes ₹150 ÷ ₹80 ≈ 2. The rule percentages themselves
(1%, 100%, 3%, 2 shares) live in `capital:` in `config.yaml`. The broker is
picked in the same tab; `./run.sh --market india budget` prints the current
values and marks each one auto or manual.

Share size is the lower of "risk per trade ÷ R" and "max position ÷ price".
Setups you couldn't buy the minimum number of shares of aren't sized, and the
backtest skips them.

**Costs:** the broker setting applies each broker's brokerage plus the NSE
statutory charges (STT, stamp duty, exchange, SEBI, IPFT, GST) to every
backtest trade at its real size. On small trades the broker matters a lot:

| Round trip | ₹2,500 trade | ₹5,000 trade | ₹25,000 trade |
|---|---|---|---|
| Groww (0.1%, ₹5 minimum per order) | ₹12.69 (0.51%) | ₹13.57 (0.27%) | ₹56.07 (0.22%) |
| Zerodha / Dhan / Fyers (0.03%) | ₹2.66 (0.11%) | ₹5.31 (0.11%) | ₹26.51 (0.11%) |

In a 15-session India backtest at ₹5,000 a day, Groww's fees cost about 0.25R
per trade. Gap & Go fell from +0.33R with Fyers fees to +0.07R with Groww's,
and ORB went from +0.05R to −0.15R. `./run.sh --market india budget` prints
your numbers.

The US market has no `capital` section by default. It uses a $25,000 account
and $100 risk per trade, and trades are commission-free; set a daily capital
in Settings to switch the US to the same percentage-based scheme.

## Daily risk limits

Set in `risk_limits` (per market) and checked against **your recorded
positions**. India's loss limit comes from daily capital (above):

| Limit | US | India |
|---|---|---|
| `max_daily_loss`: today's realized + open P&L | $300 | auto: 3% of daily capital (₹150) |
| `max_trades_per_day`: buys recorded today | 4 | auto: loss limit ÷ risk per trade (3) |
| `max_open_positions` | 3 | auto: daily capital ÷ max position (1) |

In daily-capital mode these come from Settings and can be set manually there.

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
backtest charges slippage per side (US 0.1%, India 0.05%), plus India's
broker and statutory fees at each trade's real size, and skips today until the
session has closed.

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

Logs are in `data/monitor.log` and `data/india/monitor.log`.

**You don't start the app yourself.** After `install`, the dashboard is always
on (it starts when you log in to the Mac and restarts if it stops) and each
monitor starts at its time on trading days. `./run.sh` starts a dashboard by
hand, but don't run it while the always-on one is running: both use port 8501.
`./run.sh today` doesn't start a second dashboard either; it only starts the
background one if it has stopped.

**Stopping it:**

| To stop | Command | Comes back |
|---|---|---|
| Everything (dashboard + both monitors) | `./scripts/autostart.sh uninstall` | after `./scripts/autostart.sh install` |
| Today's India monitor only | `launchctl kill TERM gui/$(id -u)/com.daytrader.monitor.india` | by itself at the next start time |
| Today's US monitor only | `launchctl kill TERM gui/$(id -u)/com.daytrader.monitor` | by itself at the next start time |

The dashboard can't be stopped on its own this way: it restarts whenever it
stops, so use `uninstall`. Settings, positions, trades and your Fyers login
are kept either way.

**Battery and sleep:**

- The monitors do a little work once a minute and the dashboard idles (well
  under 1% CPU) unless a browser has it open. The real cost is staying awake:
  while a monitor runs, `caffeinate -i` stops idle sleep (the screen still
  turns off), which costs roughly a few percent of battery an hour.
- The India monitor keeps the Mac awake about 09:00 to 15:35 IST, and the US
  one about 18:00 to 02:35 IST, overnight. If you only trade one market, turn
  the other off to save the most battery.
- **Closing the lid sleeps the Mac anyway,** on battery or not, unless it's
  in clamshell mode (plugged in, with an external display and keyboard). While
  it sleeps there are no BUY/SELL alerts, no dashboard on your phone and no
  Fyers trade recording. Nothing is lost when it wakes: the monitor carries on
  with fresh prices, sends any SELL whose stop or target was hit while it
  slept, and records Fyers trades made meanwhile. BUY setups that ran away
  while it slept are missed. If the Mac was asleep at a start time, that job
  runs when it wakes.
- **During market hours, keep the Mac plugged in with the lid open** (screen
  off is fine) if you want alerts while you're away from it.

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
- **Rupees:** sized from ₹5,000 of daily capital by default (₹50 risk per
  trade, stocks up to ₹2,500). Change it in Settings.
- **Separate everything:** strategy, backtest, positions and alerts live in
  `data/india/`.

## Configuration

Everything is in `config.yaml`. The top level is the US; the `india:` section
overrides any key for NSE.

| Section | Controls |
|---|---|
| `data_source`, `alpaca`, `schwab`, `india.fyers`, `india.kite` | where data comes from |
| `universe` | price range, minimum move, relative volume, float, number of candidates, watchlist |
| `capital`, `broker` | daily capital and the rules for its auto-calculated values (manual overrides from Settings live in `data/…/settings.json`); broker for exact fees |
| `risk` | account size, risk per trade, max position %, reward:risk (derived from `capital` when set) |
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
  history from 2020, Fyers 100 days per request). Fyers' free plan caps data
  calls at 5,000 a day, which slows how often today's bars refresh.
- **The Mac has to be awake.** With the lid closed (outside clamshell mode)
  the monitors pause and no alerts arrive until it wakes.
- **Backtests** are a ranking of strategies over recent sessions, not a
  forecast. Fills assume the trigger price plus slippage, and real fills on
  fast small caps can be worse.
- **Auto-switching** can still follow noise when samples are small. Raise
  `min_trades` or `margin_r` for steadier switching.
- **Setup odds** assume no trend and use simple stop, target and time exits.
- **News for India** is a name search, so it occasionally picks up an
  unrelated story.
- **Float** comes from Yahoo (US only) and is cached for a week.
