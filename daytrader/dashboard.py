"""Streamlit dashboard:  streamlit run dashboard.py"""
from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from dtrader.alerts import load_alerts  # noqa: E402
from dtrader.backtest import load_last_trades, run_backtest, summarize  # noqa: E402
from dtrader.config import load_config, load_state, set_active_strategy  # noqa: E402
from dtrader.markets import MARKETS  # noqa: E402
from dtrader.data import get_provider  # noqa: E402
from dtrader.performance import load_journal, strategy_hint  # noqa: E402
from dtrader.scanner import buyable_now, scan  # noqa: E402
from dtrader.strategies import REGISTRY, all_strategies, get_strategy  # noqa: E402
from dtrader import ui  # noqa: E402

st.set_page_config(page_title="Day Trade Scanner", page_icon=":material/candlestick_chart:", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)


def _dash_secret() -> bytes:
    """Random per-install secret for sign-in tokens. Delete data/dashboard_secret
    to sign out every device."""
    import secrets

    from dtrader.config import DATA_DIR

    p = DATA_DIR / "dashboard_secret"
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        p.chmod(0o600)
    return p.read_text().strip().encode()


TOKEN_DAYS = 30


def _token(pw: str, day: str) -> str:
    import hashlib
    import hmac

    return f"{day}." + hmac.new(_dash_secret(), f"{pw}|{day}".encode(), hashlib.sha256).hexdigest()[:32]


def _token_ok(pw: str, k: str) -> bool:
    import hmac
    from datetime import date, datetime

    day = k.split(".", 1)[0]
    try:
        issued = datetime.strptime(day, "%Y%m%d").date()
    except ValueError:
        return False
    if (date.today() - issued).days > TOKEN_DAYS:
        return False
    return hmac.compare_digest(k.encode(), _token(pw, day).encode())


def require_password() -> None:
    """Simple gate for phone access: set DASHBOARD_PASSWORD in .env. After
    logging in, the URL carries a token (valid 30 days) so a bookmark stays
    signed in. Plain HTTP on Wi-Fi: prefer the Tailscale address, which is
    encrypted."""
    import hmac
    import os
    from datetime import date

    pw = os.environ.get("DASHBOARD_PASSWORD")
    if not pw:
        return
    if st.session_state.get("authed") or _token_ok(pw, st.query_params.get("k", "")):
        st.session_state.authed = True
        if not _token_ok(pw, st.query_params.get("k", "")):
            st.query_params["k"] = _token(pw, date.today().strftime("%Y%m%d"))
        return
    st.markdown(ui.section("Day Trade Scanner"), unsafe_allow_html=True)
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered.encode(), pw.encode()):
        st.session_state.authed = True
        st.query_params["k"] = _token(pw, date.today().strftime("%Y%m%d"))
        st.rerun()
    elif entered:
        st.error("Wrong password")
    st.stop()


require_password()


def provider_for(market_key: str, source: str, broker_token: str | None):
    """One provider per browser session (providers keep caches and rate-limit
    timers that aren't thread-safe). Rebuilt after 10 minutes or when a new
    Groww/Zerodha login appears."""
    import time

    key = (market_key, source, broker_token)
    hit = st.session_state.get("_provider")
    if hit and hit[0] == key and hit[1] > time.time() - 600:
        return hit[2]
    with st.spinner("Connecting to data source..."):
        p = get_provider(load_config(market_key), source)
    st.session_state["_provider"] = (key, time.time(), p)
    return p


SOURCES = {"us": ["auto", "alpaca", "schwab", "yfinance"], "india": ["auto", "groww", "kite", "yfinance"]}

# ---------------- sidebar ------------------------------------------------
with st.sidebar:
    st.markdown(f'<div style="font-weight:650;font-size:1rem;margin:0.2rem 0 0.8rem">Day Trade Scanner</div>',
                unsafe_allow_html=True)
    if "market" not in st.session_state:  # open on whichever market is trading now
        live = [k for k, m in MARKETS.items()
                if (h := m.session_hours(m.now().date())) and m.now().time() >= m.day_start and m.now() < h[1]]
        st.session_state["market"] = live[0] if live else "us"
    mkey = st.segmented_control("Market", list(MARKETS), key="market", required=True,
                                format_func=lambda k: MARKETS[k].name, width="stretch")
    cfg = load_config(mkey)
    mk = cfg["market"]
    opts = SOURCES[mkey]
    source = st.selectbox("Data source", opts, key=f"source_{mkey}",
                          index=opts.index(cfg.get("data_source", "auto")) if cfg.get("data_source", "auto") in opts else 0)
    broker_tok = None
    if mkey == "india":
        from dtrader.data import india_login_hint, india_login_needed, india_token_state

        broker_tok = india_token_state()
    try:
        prov = provider_for(mkey, source, broker_tok)
    except Exception as e:
        st.error(f"Data source error: {e}")
        st.stop()
    if mkey == "india" and india_login_needed() and prov.name == "yfinance_in":
        st.warning(f"Not logged in to your broker today - using free Yahoo data. {india_login_hint()}")

    names = list(REGISTRY)
    # Per-market widget so a choice never carries over to the other market; and
    # resync when the CLI/monitor/hint changed it, so stale widget state can't
    # write an old choice back.
    skey, seen = f"strategy_{mkey}", f"strategy_seen_{mkey}"
    if st.session_state.get(seen) != cfg["active_strategy"]:
        st.session_state[skey] = cfg["active_strategy"]
        st.session_state[seen] = cfg["active_strategy"]
    active = st.selectbox("Active strategy", names, key=skey, format_func=lambda n: REGISTRY[n].title)
    if active != cfg["active_strategy"]:
        set_active_strategy(active, mk)
        st.session_state[seen] = active
        cfg = load_config(mk)
        st.toast(f"Switched to {REGISTRY[active].title}")
    st.caption(REGISTRY[active].description)
    st.divider()

    trades_cached = load_last_trades(mk)
    if len(trades_cached):
        h = strategy_hint(trades_cached, cfg, names=names)
        st.markdown(f'<div style="font-size:0.75rem;color:{ui.MUTED};text-transform:uppercase;'
                    f'letter-spacing:0.06em;margin-bottom:0.3rem">Strategy check</div>', unsafe_allow_html=True)
        (st.warning if h.switch else st.caption)(h.message)
        if h.switch and h.recommended and st.button(f"Switch to {REGISTRY[h.recommended].title}"):
            set_active_strategy(h.recommended, mk)
            st.rerun()
    else:
        st.info("Run a backtest to get strategy-switch hints.")

    st.divider()
    compare = st.toggle("Show all strategies in the scan table", value=False)
    auto = st.toggle("Auto-refresh every 60 s", value=False)
    st.caption("Educational tool, not financial advice. Always trade with a stop.")

strategy = get_strategy(cfg["active_strategy"], cfg)
_scan = st.session_state.get(f"scan_{mk.key}")
st.markdown(ui.header(mk, prov.name, strategy.title,
                      f"{_scan[2]:%H:%M} {mk.tz_label}" if _scan else None), unsafe_allow_html=True)
tab_picks, tab_mine, tab_bt, tab_hint, tab_alerts, tab_guide = st.tabs(
    ["Picks", "My positions", "Backtest", "Switch hint", "Alerts", "Strategies"],
    key="main_tab")  # keyed so the selected tab survives reruns (form inputs, buttons)


def my_advice() -> list[tuple[dict, object]]:
    from dtrader.portfolio import evaluate, load_positions

    out = []
    for row in load_positions(mk):
        try:
            out.append((row, evaluate(row, prov, cfg)))
        except Exception as e:
            out.append((row, type("A", (), {"action": "NO DATA", "reason": str(e)[:80], "last": None,
                                            "stop": row["stop"], "pnl": None, "r_now": None, "notes": []})()))
    return out


# ---------------- chart helper --------------------------------------------
def candle_chart(cand, sig=None, watch=None, title=""):
    df = cand.bars
    start = (pd.Timestamp.combine(pd.Timestamp.today(), mk.open) - pd.Timedelta(hours=2.5)).time() \
        if mk.premarket else mk.open
    df = df[(df.index.time >= start) & (df.index.time <= mk.close)]
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"],
                                 close=df["close"], name="Price",
                                 increasing=dict(line=dict(color=ui.GOOD, width=1), fillcolor=ui.GOOD),
                                 decreasing=dict(line=dict(color=ui.BAD, width=1), fillcolor=ui.BAD)))
    fig.add_trace(go.Scatter(x=df.index, y=df["vwap"], name="VWAP", line=dict(color=ui.SERIES[0], width=2)))
    fig.add_trace(go.Scatter(x=df.index, y=df["ema9"], name="EMA 9", line=dict(color=ui.SERIES[1], width=2)))
    ctx = cand.ctx
    lines = [(ctx.prev_close, "Prev close", ui.MUTED, "dot")]
    if mk.premarket:
        lines.append((ctx.premarket_high, "PM high", ui.SERIES[6], "dot"))
    if sig:
        lines += [(sig.entry, "Entry", ui.INK_2, "dash"), (sig.stop, "Stop", ui.BAD, "dash"),
                  (sig.target, "Target", ui.GOOD, "dash")]
        fig.add_trace(go.Scatter(x=[sig.time], y=[sig.entry], mode="markers", name="Signal",
                                 marker=dict(symbol="triangle-up", size=12, color=ui.INK,
                                             line=dict(color=ui.SURFACE, width=2))))
    if watch:
        lines.append((watch[0], "Trigger", ui.WARN, "dash"))
    for y, label, color, dash in lines:
        if y and y == y:
            fig.add_hline(y=y, line=dict(color=color, dash=dash, width=1),
                          annotation_text=f"{label} {y:.2f}", annotation_position="top left",
                          annotation_font=dict(color=ui.INK_2, size=11))
    ui.chart_layout(fig, height=480, title=title)
    fig.update_layout(xaxis_rangeslider_visible=False)
    hi = mk.close.hour + mk.close.minute / 60
    lo = start.hour + start.minute / 60
    fig.update_xaxes(rangebreaks=[dict(bounds=[hi, lo], pattern="hour")])
    return fig


# ---------------- buyable now ---------------------------------------------
def buyable_section(df: pd.DataFrame, cands: list) -> None:
    """Every setup that is buyable right now, across all strategies, with its odds."""
    recent = strategy_hint(load_last_trades(mk), cfg, names=names).table
    b = buyable_now(df, strategy.name, recent)
    st.markdown(ui.section("Buyable now", f"{len(b)} across all strategies"), unsafe_allow_html=True)
    if b.empty:
        closed = ui.market_state(mk)[0] == "Closed"
        st.markdown(ui.empty("Nothing is buyable right now"
                             + (" - the market is closed." if closed else
                                ". A setup shows here once it triggers, while its trade is open and price "
                                "is within 0.5R of the entry.")), unsafe_allow_html=True)
        return
    min_t = int(cfg.get("switch_hint", {}).get("min_trades", 5))

    def lately(r):
        if r.strategy_R != r.strategy_R or r.strategy_trades < min_t:
            return f"too few trades ({r.strategy_trades})"
        return f"{r.strategy_R:+.2f}R ({r.strategy_trades})"

    from dtrader.news import check as news_check
    from dtrader.odds import setup_odds

    by_sym = {c.symbol: c for c in cands}
    odds, news = [], []
    with st.spinner("Working out the odds and checking news for each setup..."):
        for r in b.itertuples():
            c = by_sym.get(r.symbol)
            sig = (c.results.get(r.strategy) or {}).get("signal") if c else None
            try:
                odds.append(setup_odds(prov, cfg, get_strategy(r.strategy, cfg), sig, c.bars, c.last) if sig else None)
            except Exception:
                odds.append(None)
            try:
                news.append(news_check(r.symbol, mk))
            except Exception:
                news.append(None)
    show = pd.DataFrame({
        "Symbol": b["symbol"],
        "Verdict": [o.verdict if o else "–" for o in odds],
        "Reach (target / stop / neither)": [f"{o.p_target:.0%} / {o.p_stop:.0%} / {o.p_open:.0%}" if o else "–"
                                            for o in odds],
        "Track record": [o.history_text() if o else "–" for o in odds],
        "Earnings": [n.earnings_text() if n else "–" for n in news],
        "News": [n.news_text() if n else "–" for n in news],
        "Strategy": [REGISTRY[n].title + ("  (active)" if a else "") for n, a in zip(b["strategy"], b["active"])],
        "Strategy lately": [lately(r) for r in b.itertuples()],
        "Triggered": b["triggered_at"],
        "Entry": b["entry"], "Last": b["last"], "Past entry": b["r_from_entry"],
        "Stop": b["stop"], "Target": b["target"], "Shares": b["shares"],
        "Setup": b["why"],
    })
    money = st.column_config.NumberColumn(format="%.2f")
    st.dataframe(show.style.map(ui.signed_cell, subset=["Strategy lately"]).map(ui.verdict_cell, subset=["Verdict"])
                 .map(ui.earnings_cell, subset=["Earnings"]),
                 width="stretch", hide_index=True,
                 column_config={"Entry": money, "Last": money, "Stop": money, "Target": money,
                                "Past entry": st.column_config.NumberColumn(format="%+.2fR"),
                                "Shares": st.column_config.NumberColumn(format="%d"),
                                "Setup": st.column_config.TextColumn(width="large"),
                                "News": st.column_config.TextColumn(width="large")})
    st.caption("Earnings today or next session can gap the stock through your stop. News counts headlines since "
               "the previous close - a gap with fresh news (a catalyst) tends to follow through better.")
    st.caption(f"Only your active strategy ({strategy.title}) sends alerts. Reach: from the current price, how "
               "often a replay of the rest of today (10,000 runs on this stock's own minute bars, scaled to "
               "today's volatility, no assumed trend) hits the target first, the stop first, or neither by the "
               "time exit. Track record: this rule on this stock over its last 20 sessions - the verdict is "
               "based on it (5+ trades needed). 'Strategy lately': the strategy's average R across all stocks.")


# ---------------- picks ---------------------------------------------------
@st.fragment(run_every=60 if auto else None)
def picks():
    strats = all_strategies(cfg)  # always analyse all: cheap once bars are fetched
    key = f"scan_{mk.key}"
    if st.button("Scan now", type="primary", icon=":material/refresh:") or key not in st.session_state or auto:
        with st.spinner("Scanning movers..."):
            logs = []
            df, cands = scan(prov, cfg, strats, log=logs.append)
            st.session_state[key] = (df, cands, mk.now(), logs)
            from dtrader.portfolio import remember_buyable

            remember_buyable(mk, buyable_now(df, strategy.name))
    df, cands, ts, logs = st.session_state[key]
    st.caption(f"Scanned {ts:%H:%M:%S} {mk.tz_label} · {logs[0] if logs else ''}")
    if df.empty:
        st.markdown(ui.empty("No stocks pass the filters right now (price, size of move, relative volume)."),
                    unsafe_allow_html=True)
        return
    view = df if compare else df[df["strategy"] == strategy.name]
    buyable_section(df, cands)
    st.markdown(ui.section(f"{strategy.title} scan" if not compare else "All strategies",
                           f"{view['symbol'].nunique()} candidates"), unsafe_allow_html=True)
    st.markdown(ui.tiles([
        ("Buy now", str(int((view["status"] == "BUY NOW").sum())), strategy.title if not compare else "all strategies"),
        ("Setting up", str(int((view["status"] == "SETTING UP").sum())), "within 5% of trigger"),
        ("Triggered today", str(int(view["status"].str.startswith("triggered").sum())), "no longer buyable"),
        ("Candidates", str(view["symbol"].nunique()), "passed the filters"),
    ]), unsafe_allow_html=True)
    table = view.rename(columns={"symbol": "Symbol", "status": "Status", "last": "Last", "gap_%": "Gap",
                                 "day_%": "Day", "rel_vol": "Rel vol", "float_M": "Float (M)", "entry": "Entry",
                                 "stop": "Stop", "target": "Target", "shares": "Shares", "why": "Setup"})
    table["Strategy"] = [REGISTRY[n].title for n in view["strategy"]]
    cols = ["Symbol", "Strategy", "Status", "Last", "Gap", "Day", "Rel vol"] + \
        (["Float (M)"] if mk.key == "us" else []) + ["Entry", "Stop", "Target", "Shares", "Setup"]
    if not compare:
        cols.remove("Strategy")
    money = st.column_config.NumberColumn(format="%.2f")
    pct = st.column_config.NumberColumn(format="%+.1f%%")
    styled = ui.dash_fill(table[cols], money=["Entry", "Stop", "Target"], whole=["Shares"]).style \
        .map(ui.status_cell, subset=["Status"]).map(ui.signed_cell, subset=["Day"])
    st.dataframe(styled,
                 width="stretch", hide_index=True, height=min(560, 38 + 35 * len(view)),
                 column_config={"Last": money, "Gap": pct, "Day": pct,
                                "Rel vol": st.column_config.NumberColumn(format="%.1fx"),
                                "Float (M)": st.column_config.NumberColumn(format="%.1f"),
                                "Setup": st.column_config.TextColumn(width="large")})

    st.markdown(ui.section("Chart"), unsafe_allow_html=True)
    syms = list(dict.fromkeys(view["symbol"]))
    pick = st.selectbox("Symbol", syms, key=f"chart_sym_{mk.key}")
    cand = next((c for c in cands if c.symbol == pick), None)
    if cand is None:  # stale pick from an earlier scan
        return
    sname = st.selectbox("Overlay strategy", [s.name for s in strats], key=f"chart_strat_{mk.key}",
                         format_func=lambda n: REGISTRY[n].title) if compare else strategy.name
    a = cand.results.get(sname, {})
    st.plotly_chart(candle_chart(cand, a.get("signal"), a.get("watch"),
                                 f"{pick} · {REGISTRY[sname].title} · {a.get('status', '')}"),
                    width="stretch", config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]})
    from dtrader.news import check as news_check

    try:
        ni = news_check(pick, mk)
        tail = f" · {ni.earnings_text()}" if ni.earnings_when else ""
        st.markdown(ui.section(f"{pick} headlines", f"{len(ni.fresh)} since the previous close{tail}"),
                    unsafe_allow_html=True)
        st.markdown(ui.headline_list(ni.headlines, mk.tz), unsafe_allow_html=True)
    except Exception as e:
        st.caption(f"Headlines unavailable: {str(e)[:80]}")


ADVICE = my_advice()  # once per page load: evaluating fetches prices
from dtrader.portfolio import risk_status  # noqa: E402

RISK = risk_status(mk, prov, cfg, advice=ADVICE)

with tab_picks:
    _sell = [(r, a) for r, a in ADVICE if a.action == "SELL NOW"]
    if _sell:
        st.markdown(ui.banner("<b>Sell now</b> · " + "; ".join(
            f"{r['shares']} {r['symbol']} - {a.reason}" for r, a in _sell) + ". Record the sale in My positions."),
            unsafe_allow_html=True)
    if RISK.blocked:
        st.markdown(ui.notice("<b>BUY alerts paused for today</b> · " + "; ".join(RISK.breaches)
                              + ". Sell alerts keep working. Limits: <code>risk_limits</code> in config.yaml."),
                    unsafe_allow_html=True)
    picks()

# ---------------- my positions --------------------------------------------
with tab_mine:
    from datetime import datetime as _dt

    from dtrader.portfolio import add_buy, delete_position, load_closed, record_sell, update_position

    cur = mk.currency
    advice = ADVICE
    st.markdown(ui.section("Today's limits", "BUY alerts pause when one is reached"), unsafe_allow_html=True)
    st.markdown(ui.tiles([
        ("P&L today", mk.fmt_money(RISK.pnl_today),
         f"limit -{cur}{RISK.max_daily_loss:,.0f} · {cur}{RISK.loss_left:,.0f} left" if RISK.max_daily_loss else "no limit"),
        ("Trades today", f"{RISK.trades_today} / {RISK.max_trades}" if RISK.max_trades else str(RISK.trades_today),
         "buys recorded today"),
        ("Open positions", f"{RISK.open_count} / {RISK.max_open}" if RISK.max_open else str(RISK.open_count), None),
        ("Status", "Paused" if RISK.blocked else "OK", RISK.breaches[0] if RISK.breaches else "within limits"),
    ]), unsafe_allow_html=True)
    st.markdown(ui.section("Open positions", str(len(advice))), unsafe_allow_html=True)
    if advice:
        live_pnl = sum(a.pnl or 0 for _, a in advice)
        st.markdown(ui.tiles([
            ("Open P&L", mk.fmt_money(live_pnl), "unrealized"),
            ("To sell", str(sum(a.action == "SELL NOW" for _, a in advice)), "exit rule triggered"),
            ("Holding", str(sum(a.action == "HOLD" for _, a in advice)), None),
        ]), unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{
            "Action": a.action, "Symbol": r["symbol"], "Shares": r["shares"], "Bought": r["entry"],
            "At": r["entry_time"][11:16], "Last": a.last, "P&L": a.pnl, "R": a.r_now,
            "Stop now": a.stop, "Target": r["target"], "Why": a.reason,
            "Strategy": REGISTRY[r["strategy"]].title if r["strategy"] in REGISTRY else "Manual",
        } for r, a in advice]).style.map(ui.action_cell, subset=["Action"]).map(ui.signed_cell, subset=["P&L", "R"]),
            width="stretch", hide_index=True,
            column_config={k: st.column_config.NumberColumn(format="%.2f")
                           for k in ("Bought", "Last", "P&L", "Stop now", "Target")}
            | {"R": st.column_config.NumberColumn(format="%+.2f")})
        st.caption("Exit rules (same as the backtest): stop; target; stop moves to your entry at +1R; after "
                   "+1R sell on a 1-min close below the 9 EMA; flat by "
                   f"{cfg.get('exits', {}).get('time_exit')}. The monitor pushes a SELL alert when one triggers.")
        for r, a in advice:
            with st.expander(f"{a.action} · {r['symbol']} · {r['shares']} sh @ {r['entry']:.2f}",
                             icon=":material/sell:" if a.action == "SELL NOW" else ":material/trending_up:"):
                c1, c2 = st.columns(2)
                with c1:
                    px = st.number_input("Sold at", value=float(a.last or r["entry"]), step=0.05,
                                         format="%.2f", key=f"sellpx_{r['id']}")
                    if st.button("Record sale", key=f"sell_{r['id']}", type="primary"):
                        done = record_sell(mk, r["id"], px, reason=a.reason if a.action == "SELL NOW" else "sold early")
                        st.toast(f"Recorded: {done['symbol']} {done['r_multiple']:+.2f}R, {mk.fmt_money(done['pnl'])}")
                        st.rerun()
                with c2:
                    ns = st.number_input("Stop", value=float(r["stop"]), step=0.05, format="%.2f", key=f"stop_{r['id']}")
                    nt = st.number_input("Target", value=float(r["target"]), step=0.05, format="%.2f", key=f"tgt_{r['id']}")
                    nsh = st.number_input("Shares", value=int(r["shares"]), min_value=1, step=1, key=f"sh_{r['id']}")
                    b1, b2 = st.columns(2)
                    if b1.button("Save changes", key=f"save_{r['id']}"):
                        update_position(mk, r["id"], stop=ns, target=nt, shares=int(nsh))
                        st.rerun()
                    if b2.button("Delete (entered by mistake)", key=f"del_{r['id']}"):
                        delete_position(mk, r["id"])
                        st.rerun()
    else:
        st.markdown(ui.empty("No open positions. Record a buy below and the tool will tell you when to sell."),
                    unsafe_allow_html=True)

    st.markdown(ui.section("Record a buy"), unsafe_allow_html=True)
    if RISK.blocked:
        st.warning("A daily risk limit is reached (" + "; ".join(RISK.breaches) + "). You can still record a "
                   "buy you've already made, but the plan says no new trades today.")
    from dtrader.portfolio import recent_buyable, setup_key

    scan_df = st.session_state.get(f"scan_{mk.key}", (pd.DataFrame(),))[0]
    setups = buyable_now(scan_df, strategy.name) if len(scan_df) else pd.DataFrame()
    current = {setup_key(r): r.to_dict() for _, r in setups.iterrows()}
    recent = {k: v for k, v in recent_buyable(mk, minutes=60).items() if k not in current}
    snap_key = f"buy_snap_{mk.key}"  # the setup you picked stays listed until you record or change it
    pinned = st.session_state.get(snap_key)
    choices = {"manual": None, **current, **recent}
    if pinned and pinned[0] not in choices:
        choices[pinned[0]] = pinned[1]

    def label(k):
        if k == "manual":
            return "Manual entry"
        r = choices[k]
        tail = "" if k in current else f" - was buyable until {r['last_seen'][11:16]}" if "last_seen" in r else " - no longer buyable"
        return f"{r['symbol']} - {REGISTRY[r['strategy']].title} (entry {r['entry']:.2f}, {r['triggered_at']}){tail}"

    src = st.selectbox("From a buyable setup (current, or buyable in the last hour)", list(choices),
                       key=f"buy_src_{mk.key}", format_func=label)
    # Prefill by rebuilding the inputs (new keys) with the setup's values:
    # more reliable than overwriting live widget state in the browser.
    if st.session_state.get(f"buy_src_seen_{mk.key}") != src:
        st.session_state[f"buy_src_seen_{mk.key}"] = src
        st.session_state[f"buy_form_n_{mk.key}"] = st.session_state.get(f"buy_form_n_{mk.key}", 0) + 1
        pref = {}
        if src != "manual":
            r = choices[src]
            st.session_state[snap_key] = (src, r)
            live = scan_df[scan_df["symbol"] == r["symbol"]]["last"] if len(scan_df) else []
            px = float(live.iloc[0]) if len(live) else float(r["last"])
            sh = r.get("shares")
            pref = {"sym": r["symbol"], "px": px, "stop": float(r["stop"]), "tgt": float(r["target"]),
                    "sh": int(sh) if sh == sh and sh else 1, "strat": r["strategy"]}
        else:
            st.session_state.pop(snap_key, None)
        st.session_state[f"buy_pref_{mk.key}"] = pref
    pref = st.session_state.get(f"buy_pref_{mk.key}", {})
    fk = f"{mk.key}_{st.session_state.get(f'buy_form_n_{mk.key}', 0)}"
    c1, c2, c3 = st.columns(3)
    sym = c1.text_input("Symbol", value=pref.get("sym", ""), key=f"b_sym_{fk}").upper().strip()
    shares = c2.number_input("Shares", min_value=1, step=1, value=pref.get("sh", 1), key=f"b_sh_{fk}")
    price = c3.number_input(f"Buy price ({cur})", min_value=0.0, step=0.05, format="%.2f",
                            value=pref.get("px", 0.0), key=f"b_px_{fk}")
    c4, c5, c6 = st.columns(3)
    stop = c4.number_input("Stop", min_value=0.0, step=0.05, format="%.2f", value=pref.get("stop", 0.0),
                           key=f"b_stop_{fk}")
    target = c5.number_input("Target", min_value=0.0, step=0.05, format="%.2f", value=pref.get("tgt", 0.0),
                             key=f"b_tgt_{fk}")
    strat_opts = ["manual"] + names
    strat = c6.selectbox("Strategy", strat_opts, index=strat_opts.index(pref.get("strat", "manual")),
                         key=f"b_strat_{fk}",
                         format_func=lambda n: REGISTRY[n].title if n in REGISTRY else "Manual")
    when = st.time_input(f"Bought at ({mk.tz_label})", value=mk.now().time().replace(second=0, microsecond=0),
                         key=f"b_time_{fk}")
    if price and not stop:
        st.caption(f"Tip: a stop is required. 2% below your price would be {price * 0.98:.2f}, "
                   f"and a 2R target {price * 1.04:.2f}.")
    if st.button("Record buy", type="primary", key=f"b_go_{mk.key}"):
        try:
            row = add_buy(mk, sym, int(shares), price, stop, target, strat,
                          when=_dt.combine(mk.now().date(), when, mk.tz))
            st.session_state.pop(snap_key, None)
            st.toast(f"Tracking {row['shares']} {row['symbol']} @ {row['entry']:.2f}")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

    closed = load_closed(mk)
    st.markdown(ui.section("Closed trades", str(len(closed))), unsafe_allow_html=True)
    if len(closed):
        st.markdown(ui.tiles([
            ("Realized P&L", mk.fmt_money(closed["pnl"].sum()), None),
            ("Win rate", f"{(closed['pnl'] > 0).mean() * 100:.0f}%", f"{len(closed)} trades"),
            ("Average R", f"{closed['r_multiple'].mean():+.2f}", None),
        ]), unsafe_allow_html=True)
        ct = closed.rename(columns={"symbol": "Symbol", "shares": "Shares", "entry": "Bought", "exit": "Sold",
                                    "entry_time": "In", "exit_time": "Out", "r_multiple": "R", "pnl": "P&L",
                                    "exit_reason": "Reason"})
        st.dataframe(ct[["Symbol", "Shares", "Bought", "Sold", "In", "Out", "R", "P&L", "Reason"]].iloc[::-1]
                     .style.map(ui.signed_cell, subset=["R", "P&L"]), width="stretch", hide_index=True)

# ---------------- backtest ------------------------------------------------
with tab_bt:
    c1, c2 = st.columns([1, 3])
    days = c1.slider("Sessions", 3, {"yfinance": 25, "yfinance_in": 25, "schwab": 45}.get(prov.name, 90), 10)
    extra = c2.text_input("Extra symbols (space separated, optional)")
    if st.button("Run backtest", type="primary"):
        logbox = st.empty()
        syms = None
        if extra.strip():
            from dtrader.scanner import history_symbols
            syms = sorted(set(history_symbols(days=days + 5, market=mk)) | set(extra.upper().split()))
        with st.spinner("Replaying sessions bar by bar..."):
            trades = run_backtest(prov, cfg, all_strategies(cfg), days=days, symbols=syms,
                                  log=lambda m: logbox.caption(m))
        st.session_state[f"bt_{mk.key}"] = trades
        st.rerun()
    trades = st.session_state.get(f"bt_{mk.key}", load_last_trades(mk))
    if len(trades):
        summ = summarize(trades, names)
        summ.index = [REGISTRY[n].title for n in summ.index]
        summ = summ.rename(columns={"trades": "Trades", "win_rate_%": "Win %", "avg_R": "Avg R",
                                    "total_R": "Total R", "profit_factor": "Profit factor",
                                    "max_dd_R": "Max drawdown (R)", "pnl": "P&L", "days_traded": "Days"})
        st.dataframe(summ.sort_values("Avg R", ascending=False).style.map(ui.signed_cell, subset=["Avg R", "Total R"]),
                     width="stretch",
                     column_config={"Avg R": st.column_config.NumberColumn(format="%+.3f"),
                                    "Total R": st.column_config.NumberColumn(format="%+.1f"),
                                    "Win %": st.column_config.NumberColumn(format="%.0f%%"),
                                    "Profit factor": st.column_config.NumberColumn(format="%.2f"),
                                    "Max drawdown (R)": st.column_config.NumberColumn(format="%.1f"),
                                    "P&L": st.column_config.NumberColumn(format="%,.0f")})
        # Equity curves: active strategy + the 3 best by total R (more lines stop being readable).
        totals = trades.groupby("strategy")["r"].sum().sort_values(ascending=False)
        default = list(dict.fromkeys([strategy.name] + list(totals.index[:3])))
        chosen = st.multiselect("Equity curves", names, default=[n for n in default if n in totals.index],
                                format_func=lambda n: REGISTRY[n].title, max_selections=4)
        fig = go.Figure()
        for n in chosen:
            t = trades[trades["strategy"] == n].sort_values("entry_time")
            color = ui.SERIES[names.index(n)] if names.index(n) < len(ui.SERIES) else ui.MUTED
            fig.add_trace(go.Scatter(x=list(range(1, len(t) + 1)), y=t["r"].cumsum(), name=REGISTRY[n].title,
                                     mode="lines", line=dict(color=color, width=2),
                                     hovertemplate="trade %{x}: %{y:+.1f}R<extra>" + REGISTRY[n].title + "</extra>"))
        fig.add_hline(y=0, line=dict(color=ui.AXIS, width=1))
        ui.chart_layout(fig, height=360, title="Cumulative R by trade")
        fig.update_layout(hovermode="closest")
        st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
        f = st.multiselect("Trades for", names, default=[], format_func=lambda n: REGISTRY[n].title)
        show = trades[trades["strategy"].isin(f)] if f else trades
        st.dataframe(show.sort_values("entry_time", ascending=False), width="stretch", hide_index=True)
        st.caption("Backtests use symbols that gapped at the open (or ran 20%+ the day before) from your "
                   "scan history, watchlist and current movers. Small sample + survivorship bias: use it to "
                   "compare strategies, not to forecast returns.")
    else:
        st.markdown(ui.empty("No backtest yet. Press Run backtest."), unsafe_allow_html=True)

# ---------------- hint ----------------------------------------------------
with tab_hint:
    t = st.session_state.get(f"bt_{mk.key}", load_last_trades(mk))
    h = strategy_hint(t, cfg, names=names)
    (st.warning if h.switch else st.success)(h.message)
    ht = h.table.copy()
    ht.index = [REGISTRY[n].title if n in REGISTRY else n for n in ht.index]
    ht = ht.rename(columns={"trades": "Trades", "live_trades": "Live", "expectancy_R": "Avg R",
                            "last5_avg_R": "Last 5 avg R", "win_rate_%": "Win %", "profit_factor": "Profit factor"})
    st.dataframe(ht.sort_values("Avg R", ascending=False).style.map(ui.signed_cell, subset=["Avg R", "Last 5 avg R"]),
                 width="stretch", column_config={"Avg R": st.column_config.NumberColumn(format="%+.2f"),
                                                 "Last 5 avg R": st.column_config.NumberColumn(format="%+.2f"),
                                                 "Win %": st.column_config.NumberColumn(format="%.0f%%"),
                                                 "Profit factor": st.column_config.NumberColumn(format="%.2f")})
    if h.switch and h.recommended:
        if st.button(f"Apply: switch to {REGISTRY[h.recommended].title}", key="apply_hint"):
            set_active_strategy(h.recommended, mk)
            st.rerun()
    sh = cfg.get("switch_hint", {})
    st.caption(f"Judged on the last {sh.get('lookback_days', 10)} sessions; a strategy needs "
               f"{sh.get('min_trades', 10)}+ trades. Live trades from the alert journal count double. "
               "Edit `switch_hint` in config.yaml to tune.")

# ---------------- alerts --------------------------------------------------
with tab_alerts:
    st.markdown(f"Live {mk.name} alerts come from the monitor, which the daily job starts "
                "automatically (`./scripts/autostart.sh status`). To run it by hand:")
    st.code("./run.sh monitor" if mk.key == "us" else f"./run.sh --market {mk.key} monitor", language="bash")
    pos = load_state(mk).get("positions", [])
    st.markdown(ui.section("Paper trades from alerts", str(len(pos))), unsafe_allow_html=True)
    if pos:
        st.dataframe(pd.DataFrame(pos)[["symbol", "strategy", "entry_time", "entry", "stop", "target", "shares"]],
                     width="stretch", hide_index=True)
    al = load_alerts(market=mk)
    st.markdown(ui.section("Alert log"), unsafe_allow_html=True)
    if len(al):
        st.dataframe(al.iloc[::-1], width="stretch", hide_index=True)
    else:
        st.caption("No alerts yet.")
    j = load_journal(mk)
    st.markdown(ui.section("Closed paper trades"), unsafe_allow_html=True)
    if len(j):
        st.dataframe(j[["day", "symbol", "strategy", "entry", "exit_price", "exit_reason", "r", "shares"]].iloc[::-1],
                     width="stretch", hide_index=True)
        st.metric("Live total R", f"{j['r'].sum():+.2f}")
    else:
        st.caption("No closed trades yet.")

# ---------------- guide ---------------------------------------------------
with tab_guide:
    cols = st.columns(2)
    for k, (n, cls) in enumerate(REGISTRY.items()):
        with cols[k % 2].container(border=True):
            badge = ui.pill("Active", ui.ACCENT) if n == cfg["active_strategy"] else ""
            st.markdown(f'<div style="display:flex;justify-content:space-between;align-items:center;gap:0.5rem">'
                        f'<span style="font-weight:600;color:{ui.INK}">{cls.title}</span>{badge}</div>'
                        f'<div style="font-size:0.75rem;color:{ui.MUTED};margin:0.15rem 0 0.4rem">'
                        f'{cls.style} · <code>{n}</code></div>', unsafe_allow_html=True)
            st.caption(cls.description)
            with st.popover("Parameters"):
                st.json({**cls.defaults, **((cfg.get('strategies') or {}).get(n) or {})})
    ex = cfg.get("exits", {})
    st.markdown(
        f"**Exits (all strategies):** stop loss; target at {cfg.get('risk', {}).get('reward_risk', 2)}R; "
        f"stop to breakeven at +{ex.get('breakeven_after_r')}R; after +{ex.get('trail_after_r')}R sell on a "
        f"1-min close below the 9 EMA; flat by {ex.get('time_exit')}. No new entries after "
        f"{ex.get('no_new_entries_after')}.")
