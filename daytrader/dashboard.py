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
from dtrader.data import get_provider  # noqa: E402
from dtrader.performance import load_journal, strategy_hint  # noqa: E402
from dtrader.scanner import scan  # noqa: E402
from dtrader.strategies import REGISTRY, all_strategies, get_strategy  # noqa: E402

st.set_page_config(page_title="Day Trade Scanner", page_icon="📈", layout="wide")


def require_password() -> None:
    """Simple gate for phone/LAN access: set DASHBOARD_PASSWORD in .env.
    After logging in, the URL carries a token so a bookmark stays signed in."""
    import hashlib
    import hmac
    import os

    pw = os.environ.get("DASHBOARD_PASSWORD")
    if not pw:
        return
    token = hmac.new(pw.encode(), b"daytrader-dashboard", hashlib.sha256).hexdigest()[:24]
    if st.session_state.get("authed") or hmac.compare_digest(st.query_params.get("k", ""), token):
        st.session_state.authed = True
        st.query_params["k"] = token
        return
    st.title("📈 Day Trade Scanner")
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered, pw):
        st.session_state.authed = True
        st.query_params["k"] = token
        st.rerun()
    elif entered:
        st.error("Wrong password")
    st.stop()


require_password()


@st.cache_resource(show_spinner="Connecting to data source...")
def provider_for(source: str):
    return get_provider(load_config(), source)


cfg = load_config()

# ---------------- sidebar ------------------------------------------------
with st.sidebar:
    st.title("📈 Day Trade Scanner")
    source = st.selectbox("Data source", ["auto", "alpaca", "schwab", "yfinance"],
                          index=["auto", "alpaca", "schwab", "yfinance"].index(cfg.get("data_source", "auto")))
    try:
        prov = provider_for(source)
        st.caption(f"Using **{prov.name}** data")
    except Exception as e:
        st.error(f"Data source error: {e}")
        st.stop()

    names = list(REGISTRY)
    active = st.selectbox("Active strategy", names, index=names.index(cfg["active_strategy"]),
                          format_func=lambda n: REGISTRY[n].title)
    if active != cfg["active_strategy"]:
        set_active_strategy(active)
        cfg = load_config()
        st.toast(f"Switched to {REGISTRY[active].title}")
    st.caption(REGISTRY[active].description)

    trades_cached = load_last_trades()
    if len(trades_cached):
        h = strategy_hint(trades_cached, cfg, names=names)
        (st.warning if h.switch else st.info)(h.message)
        if h.switch and h.recommended and st.button(f"Switch to {REGISTRY[h.recommended].title}"):
            set_active_strategy(h.recommended)
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
    df = df[df.index.time >= pd.Timestamp("07:00").time()]
    df = df[df.index.time <= pd.Timestamp("16:00").time()]
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"],
                                 close=df["close"], name=cand.symbol,
                                 increasing_line_color="#26a69a", decreasing_line_color="#ef5350"))
    fig.add_trace(go.Scatter(x=df.index, y=df["vwap"], name="VWAP", line=dict(color="#ab47bc", width=1.5)))
    fig.add_trace(go.Scatter(x=df.index, y=df["ema9"], name="EMA 9", line=dict(color="#ffa726", width=1)))
    ctx = cand.ctx
    lines = [(ctx.prev_close, "prev close", "#90a4ae"), (ctx.premarket_high, "PM high", "#42a5f5")]
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
    fig.update_xaxes(rangebreaks=[dict(bounds=[16, 7], pattern="hour")])
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
    strats = all_strategies(cfg) if compare else [strategy]
    if st.button("🔄 Scan now", type="primary") or "scan" not in st.session_state or auto:
        with st.spinner("Scanning movers..."):
            logs = []
            df, cands = scan(prov, cfg, strats, log=logs.append)
            st.session_state.scan = (df, cands, pd.Timestamp.now(tz="America/New_York"), logs)
    df, cands, ts, logs = st.session_state.scan
    st.caption(f"Last scan {ts:%H:%M:%S} ET - {logs[0] if logs else ''}")
    if df.empty:
        st.info("No stocks pass the universe filters right now (price / gap / relative volume / float).")
        return
    view = df if compare else df[df["strategy"] == strategy.name]
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
                 use_container_width=True, hide_index=True, height=min(600, 40 + 35 * len(view)),
                 column_config={"score": pct, "last": money, "gap_%": pct, "day_%": pct, "rel_vol": pct,
                                "float_M": pct, "entry": money, "stop": money, "target": money,
                                "shares": st.column_config.NumberColumn(format="%d")})

    st.subheader("Chart")
    syms = list(dict.fromkeys(view["symbol"]))
    pick = st.selectbox("Symbol", syms, key="chart_sym")
    cand = next(c for c in cands if c.symbol == pick)
    sname = st.selectbox("Overlay strategy", [s.name for s in strats], key="chart_strat",
                         format_func=lambda n: REGISTRY[n].title) if compare else strategy.name
    a = cand.results.get(sname, {})
    st.plotly_chart(candle_chart(cand, a.get("signal"), a.get("watch"),
                                 f"{pick} - {REGISTRY[sname].title}: {a.get('status', '')}"),
                    use_container_width=True)


with tab_picks:
    picks()

# ---------------- backtest ------------------------------------------------
with tab_bt:
    c1, c2 = st.columns([1, 3])
    days = c1.slider("Sessions", 3, {"yfinance": 25, "schwab": 45}.get(prov.name, 90), 10)
    extra = c2.text_input("Extra symbols (space separated, optional)")
    if st.button("Run backtest", type="primary"):
        logbox = st.empty()
        syms = None
        if extra.strip():
            from dtrader.scanner import history_symbols
            syms = sorted(set(history_symbols(days=days + 5)) | set(extra.upper().split()))
        with st.spinner("Replaying sessions bar by bar..."):
            trades = run_backtest(prov, cfg, all_strategies(cfg), days=days, symbols=syms,
                                  log=lambda m: logbox.caption(m))
        st.session_state.bt = trades
        st.rerun()
    trades = st.session_state.get("bt", load_last_trades())
    if len(trades):
        st.dataframe(summarize(trades, names), use_container_width=True)
        fig = go.Figure()
        for n in names:
            t = trades[trades["strategy"] == n].sort_values("entry_time")
            if len(t):
                fig.add_trace(go.Scatter(x=list(range(1, len(t) + 1)), y=t["r"].cumsum(), name=REGISTRY[n].title,
                                         mode="lines+markers"))
        fig.update_layout(title="Cumulative R by trade", xaxis_title="trade #", yaxis_title="R",
                          height=380, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)
        f = st.multiselect("Filter strategy", names, default=[])
        show = trades[trades["strategy"].isin(f)] if f else trades
        st.dataframe(show.sort_values("entry_time", ascending=False), use_container_width=True, hide_index=True)
        st.caption("Backtests use symbols that gapped at the open (or ran 20%+ the day before) from your "
                   "scan history, watchlist and current movers. Small sample + survivorship bias: use it to "
                   "compare strategies, not to forecast returns.")
    else:
        st.info("No backtest yet. Click **Run backtest**.")

# ---------------- hint ----------------------------------------------------
with tab_hint:
    t = st.session_state.get("bt", load_last_trades())
    h = strategy_hint(t, cfg, names=names)
    (st.warning if h.switch else st.success)(h.message)
    st.dataframe(h.table, use_container_width=True)
    if h.switch and h.recommended:
        if st.button(f"Apply: switch to {REGISTRY[h.recommended].title}", key="apply_hint"):
            set_active_strategy(h.recommended)
            st.rerun()
    sh = cfg.get("switch_hint", {})
    st.caption(f"Judged on the last {sh.get('lookback_days', 10)} sessions; a strategy needs "
               f"{sh.get('min_trades', 5)}+ trades. Live trades from the alert journal count double. "
               "Edit `switch_hint` in config.yaml to tune.")

# ---------------- alerts --------------------------------------------------
with tab_alerts:
    st.markdown("Live alerts come from the monitor. Run it in a terminal during market hours:")
    st.code("python cli.py monitor", language="bash")
    pos = load_state().get("positions", [])
    st.subheader(f"Open tracked trades ({len(pos)})")
    if pos:
        st.dataframe(pd.DataFrame(pos)[["symbol", "strategy", "entry_time", "entry", "stop", "target", "shares"]],
                     use_container_width=True, hide_index=True)
    al = load_alerts()
    st.subheader("Alert log")
    if len(al):
        st.dataframe(al.iloc[::-1], use_container_width=True, hide_index=True)
    else:
        st.caption("No alerts yet.")
    j = load_journal()
    st.subheader("Closed trades (journal)")
    if len(j):
        st.dataframe(j[["day", "symbol", "strategy", "entry", "exit_price", "exit_reason", "r", "shares"]].iloc[::-1],
                     use_container_width=True, hide_index=True)
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
