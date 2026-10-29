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
from dtrader.scanner import scan  # noqa: E402
from dtrader.strategies import REGISTRY, all_strategies, get_strategy  # noqa: E402

st.set_page_config(page_title="Day Trade Scanner", page_icon="📈", layout="wide")


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
    st.title("📈 Day Trade Scanner")
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
    st.title("📈 Day Trade Scanner")
    if "market" not in st.session_state:  # open on whichever market is trading now
        live = [k for k, m in MARKETS.items()
                if (h := m.session_hours(m.now().date())) and m.now().time() >= m.day_start and m.now() < h[1]]
        st.session_state["market"] = live[0] if live else "us"
    mkey = st.radio("Market", list(MARKETS), horizontal=True, key="market",
                    format_func=lambda k: f"{MARKETS[k].flag} {MARKETS[k].name}")
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
        st.caption(f"Using **{prov.name}** data")
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

    trades_cached = load_last_trades(mk)
    if len(trades_cached):
        h = strategy_hint(trades_cached, cfg, names=names)
        (st.warning if h.switch else st.info)(h.message)
        if h.switch and h.recommended and st.button(f"Switch to {REGISTRY[h.recommended].title}"):
            set_active_strategy(h.recommended, mk)
            st.rerun()
    else:
        st.info("Run a backtest to get strategy-switch hints.")

    compare = st.checkbox("Compare all strategies in scan", value=False)
    auto = st.checkbox("Auto-refresh scan every 60s", value=False)
    st.divider()
    st.caption("Educational tool, not financial advice. Always trade with a stop.")

strategy = get_strategy(cfg["active_strategy"], cfg)
tab_picks, tab_bt, tab_hint, tab_alerts, tab_guide = st.tabs(
    ["🎯 Picks", "🧪 Backtest", "🔀 Switch hint", "🔔 Alerts & trades", "📘 Strategies"])


# ---------------- chart helper --------------------------------------------
def candle_chart(cand, sig=None, watch=None, title=""):
    df = cand.bars
    start = (pd.Timestamp.combine(pd.Timestamp.today(), mk.open) - pd.Timedelta(hours=2.5)).time() \
        if mk.premarket else mk.open
    df = df[(df.index.time >= start) & (df.index.time <= mk.close)]
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"],
                                 close=df["close"], name=cand.symbol,
                                 increasing_line_color="#26a69a", decreasing_line_color="#ef5350"))
    fig.add_trace(go.Scatter(x=df.index, y=df["vwap"], name="VWAP", line=dict(color="#ab47bc", width=1.5)))
    fig.add_trace(go.Scatter(x=df.index, y=df["ema9"], name="EMA 9", line=dict(color="#ffa726", width=1)))
    ctx = cand.ctx
    lines = [(ctx.prev_close, "prev close", "#90a4ae")]
    if mk.premarket:
        lines.append((ctx.premarket_high, "PM high", "#42a5f5"))
    if sig:
        lines += [(sig.entry, "entry", "#26a69a"), (sig.stop, "stop", "#ef5350"), (sig.target, "target", "#66bb6a")]
        fig.add_trace(go.Scatter(x=[sig.time], y=[sig.entry], mode="markers", name="signal",
                                 marker=dict(symbol="triangle-up", size=14, color="#26a69a")))
    if watch:
        lines.append((watch[0], "trigger", "#ffee58"))
    for y, label, color in lines:
        if y and y == y:
            fig.add_hline(y=y, line=dict(color=color, dash="dot", width=1),
                          annotation_text=f"{label} {y:.2f}", annotation_position="top left",
                          annotation_font_color=color)
    fig.update_layout(height=520, title=title, xaxis_rangeslider_visible=False,
                      margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h"))
    hi = mk.close.hour + mk.close.minute / 60
    lo = start.hour + start.minute / 60
    fig.update_xaxes(rangebreaks=[dict(bounds=[hi, lo], pattern="hour")])
    return fig


STATUS_COLORS = {"BUY NOW": "background-color:#1b5e20;color:white",
                 "SETTING UP": "background-color:#f9a825;color:black"}


def style_status(v):
    if isinstance(v, str) and v.startswith("triggered"):
        return "background-color:#263238;color:#b0bec5"
    return STATUS_COLORS.get(v, "")


# ---------------- picks ---------------------------------------------------
@st.fragment(run_every=60 if auto else None)
def picks():
    strats = all_strategies(cfg)  # always analyse all: cheap once bars are fetched
    key = f"scan_{mk.key}"
    if st.button("🔄 Scan now", type="primary") or key not in st.session_state or auto:
        with st.spinner("Scanning movers..."):
            logs = []
            df, cands = scan(prov, cfg, strats, log=logs.append)
            st.session_state[key] = (df, cands, mk.now(), logs)
    df, cands, ts, logs = st.session_state[key]
    st.caption(f"{mk.flag} Last scan {ts:%H:%M:%S} {mk.tz_label} - {logs[0] if logs else ''}")
    if df.empty:
        st.info("No stocks pass the universe filters right now (price / gap / relative volume / float).")
        return
    view = df if compare else df[df["strategy"] == strategy.name]
    others = df[(df["status"] == "BUY NOW") & (df["strategy"] != strategy.name)]
    if not compare and len(others):
        tbl = strategy_hint(load_last_trades(mk), cfg, names=names).table

        def lately(n):
            r = tbl.loc[n] if n in tbl.index else None
            return f"{r['expectancy_R']:+.2f}R/trade lately" if r is not None and r["expectancy_R"] == r["expectancy_R"] else "untested lately"

        items = "; ".join(f"**{r.symbol}** ({REGISTRY[r.strategy].title}, {lately(r.strategy)})"
                          for r in others.itertuples())
        st.info(f"Buyable in other strategies (not {strategy.title}, so no alerts): {items}. "
                "Tick *Compare all strategies* to see them.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Candidates", view["symbol"].nunique())
    c2.metric("BUY NOW", int((view["status"] == "BUY NOW").sum()))
    c3.metric("Setting up", int((view["status"] == "SETTING UP").sum()))
    c4.metric("Triggered today", int(view["status"].str.startswith("triggered").sum()))
    cols = ["symbol", "strategy", "status", "score", "last", "gap_%", "day_%", "rel_vol", "float_M",
            "entry", "stop", "target", "shares", "why"]
    money = st.column_config.NumberColumn(format="%.2f")
    pct = st.column_config.NumberColumn(format="%.1f")
    st.dataframe(view[cols].style.map(style_status, subset=["status"]),
                 width="stretch", hide_index=True, height=min(600, 40 + 35 * len(view)),
                 column_config={"score": pct, "last": money, "gap_%": pct, "day_%": pct, "rel_vol": pct,
                                "float_M": pct, "entry": money, "stop": money, "target": money,
                                "shares": st.column_config.NumberColumn(format="%d")})

    st.subheader("Chart")
    syms = list(dict.fromkeys(view["symbol"]))
    pick = st.selectbox("Symbol", syms, key=f"chart_sym_{mk.key}")
    cand = next((c for c in cands if c.symbol == pick), None)
    if cand is None:  # stale pick from an earlier scan
        return
    sname = st.selectbox("Overlay strategy", [s.name for s in strats], key=f"chart_strat_{mk.key}",
                         format_func=lambda n: REGISTRY[n].title) if compare else strategy.name
    a = cand.results.get(sname, {})
    st.plotly_chart(candle_chart(cand, a.get("signal"), a.get("watch"),
                                 f"{pick} - {REGISTRY[sname].title}: {a.get('status', '')}"),
                    width="stretch")


with tab_picks:
    picks()

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
        st.dataframe(summarize(trades, names), width="stretch")
        fig = go.Figure()
        for n in names:
            t = trades[trades["strategy"] == n].sort_values("entry_time")
            if len(t):
                fig.add_trace(go.Scatter(x=list(range(1, len(t) + 1)), y=t["r"].cumsum(), name=REGISTRY[n].title,
                                         mode="lines+markers"))
        fig.update_layout(title="Cumulative R by trade", xaxis_title="trade #", yaxis_title="R",
                          height=380, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width="stretch")
        f = st.multiselect("Filter strategy", names, default=[])
        show = trades[trades["strategy"].isin(f)] if f else trades
        st.dataframe(show.sort_values("entry_time", ascending=False), width="stretch", hide_index=True)
        st.caption("Backtests use symbols that gapped at the open (or ran 20%+ the day before) from your "
                   "scan history, watchlist and current movers. Small sample + survivorship bias: use it to "
                   "compare strategies, not to forecast returns.")
    else:
        st.info("No backtest yet. Click **Run backtest**.")

# ---------------- hint ----------------------------------------------------
with tab_hint:
    t = st.session_state.get(f"bt_{mk.key}", load_last_trades(mk))
    h = strategy_hint(t, cfg, names=names)
    (st.warning if h.switch else st.success)(h.message)
    st.dataframe(h.table, width="stretch")
    if h.switch and h.recommended:
        if st.button(f"Apply: switch to {REGISTRY[h.recommended].title}", key="apply_hint"):
            set_active_strategy(h.recommended, mk)
            st.rerun()
    sh = cfg.get("switch_hint", {})
    st.caption(f"Judged on the last {sh.get('lookback_days', 10)} sessions; a strategy needs "
               f"{sh.get('min_trades', 5)}+ trades. Live trades from the alert journal count double. "
               "Edit `switch_hint` in config.yaml to tune.")

# ---------------- alerts --------------------------------------------------
with tab_alerts:
    st.markdown(f"Live {mk.name} alerts come from the monitor, which the daily job starts "
                "automatically (`./scripts/autostart.sh status`). To run it by hand:")
    st.code("./run.sh monitor" if mk.key == "us" else f"./run.sh --market {mk.key} monitor", language="bash")
    pos = load_state(mk).get("positions", [])
    st.subheader(f"Open tracked trades ({len(pos)})")
    if pos:
        st.dataframe(pd.DataFrame(pos)[["symbol", "strategy", "entry_time", "entry", "stop", "target", "shares"]],
                     width="stretch", hide_index=True)
    al = load_alerts(market=mk)
    st.subheader("Alert log")
    if len(al):
        st.dataframe(al.iloc[::-1], width="stretch", hide_index=True)
    else:
        st.caption("No alerts yet.")
    j = load_journal(mk)
    st.subheader("Closed trades (journal)")
    if len(j):
        st.dataframe(j[["day", "symbol", "strategy", "entry", "exit_price", "exit_reason", "r", "shares"]].iloc[::-1],
                     width="stretch", hide_index=True)
        st.metric("Live total R", f"{j['r'].sum():+.2f}")
    else:
        st.caption("No closed trades yet.")

# ---------------- guide ---------------------------------------------------
with tab_guide:
    for n, cls in REGISTRY.items():
        st.markdown(f"### {'⭐ ' if n == cfg['active_strategy'] else ''}{cls.title}  `{n}`")
        st.markdown(f"*{cls.style}*: {cls.description}")
        st.json({**cls.defaults, **((cfg.get('strategies') or {}).get(n) or {})}, expanded=False)
    ex = cfg.get("exits", {})
    st.markdown(
        f"**Exits (all strategies):** stop loss; target at {cfg.get('risk', {}).get('reward_risk', 2)}R; "
        f"stop to breakeven at +{ex.get('breakeven_after_r')}R; after +{ex.get('trail_after_r')}R sell on a "
        f"1-min close below the 9 EMA; flat by {ex.get('time_exit')}. No new entries after "
        f"{ex.get('no_new_entries_after')}.")
