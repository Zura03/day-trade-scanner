"""Is there an edge? Checks to run before trusting any strategy.

1. Reward-to-risk: replay every strategy with targets at several R multiples
   (1.5R, 2R, ...) on the same sessions.
2. Market conditions: tag each backtest trade with what the index was doing
   at the moment of entry (only bars before the entry, so nothing a trader
   couldn't have seen) and compare results by condition.
3. Walk-forward: every rule is judged on the earlier sessions and then
   checked on later sessions it was not chosen on. The strategy pick is
   re-made each day from the previous days only, exactly like the switch
   hint, so "following the hint" gets a real track record.

Small samples (a few hundred trades over a month) are noisy: a rule only
counts if it helps in both halves.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .data.base import CACHE_DIR, read_cache, to_tz_index
from .markets import Market

# index whose mood every long trade fights or rides; VIX-style fear gauge
INDEX = {"us": ("SPY", "^VIX"), "india": ("^NSEI", "^INDIAVIX")}

# market-condition rules: name -> (description, test on one trade's tags)
RULES = {
    "index_up_since_open": ("index above its opening price at entry",
                            lambda t: t["idx_since_open"] > 0),
    "index_green_day": ("index above yesterday's close at entry",
                        lambda t: t["idx_day"] > 0),
    "index_uptrend": ("index closed above its 20-day average yesterday",
                      lambda t: t["idx_above_sma20"]),
    "calm_vix": ("fear gauge (VIX) below its 20-day median yesterday",
                 lambda t: t["vix_calm"]),
    "up_since_open_and_uptrend": ("index above its open AND in a 20-day uptrend",
                                  lambda t: (t["idx_since_open"] > 0) & t["idx_above_sma20"]),
    "low_fees": ("fees at most FEE_CAP of the risk (stop not too tight for the costs)",
                 lambda t: t["fee_R"] <= FEE_CAP),
}
FEE_CAP = 0.15   # replaced by risk.max_fee_r when set


# ---- index data -------------------------------------------------------------
def _yf_daily(ticker: str, days: int) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(ticker, period=f"{int(days * 1.6) + 10}d", interval="1d", progress=False,
                     auto_adjust=False, multi_level_index=False)
    if not len(df):
        return pd.DataFrame()
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index).date
    return df


def _yf_intraday(ticker: str, day: date, market: Market) -> pd.DataFrame:
    """1-min index bars from Yahoo (last ~30 days only), cached per day."""
    import yfinance as yf

    path = CACHE_DIR / "index" / f"{ticker.strip('^')}_{day.isoformat()}.pkl"
    if (df := read_cache(path)) is not None:
        return df
    try:
        df = yf.download(ticker, start=day, end=day + timedelta(days=1), interval="1m",
                         progress=False, auto_adjust=False, multi_level_index=False)
    except Exception:
        df = pd.DataFrame()
    if not len(df):
        return df
    df.columns = [c.lower() for c in df.columns]
    df = to_tz_index(df[["open", "high", "low", "close"]].dropna(), market.tz)
    df = df[df.index.date == day]
    if len(df) and day < market.now().date():
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_pickle(path)
    return df


def _fyers_index(provider, days: list[date]) -> dict[date, pd.DataFrame]:
    """Nifty 50 1-min bars from Fyers (~100 days back, one call per 90 days),
    cached per day in the same place as Yahoo's."""
    out, missing = {}, []
    for d in days:
        b = read_cache(CACHE_DIR / "index" / f"NSEI_{d.isoformat()}.pkl")
        if b is not None:
            out[d] = b
        else:
            missing.append(d)
    if not missing:
        return out
    try:
        allb = provider._fetch_intraday("NSE:NIFTY50-INDEX", min(missing), max(missing))
    except Exception:
        return out
    for d in missing:
        b = allb[allb.index.date == d][["open", "high", "low", "close"]] if len(allb) else allb
        if len(b):
            out[d] = b
            if d < provider.market.now().date():
                path = CACHE_DIR / "index" / f"NSEI_{d.isoformat()}.pkl"
                path.parent.mkdir(parents=True, exist_ok=True)
                b.to_pickle(path)
    return out


def index_context(provider, days: list[date]) -> tuple[dict[date, pd.DataFrame], pd.DataFrame]:
    """(1-min index bars per session, daily table with the prior-day regime)."""
    m = provider.market
    idx, vix = INDEX[m.key]
    bars = {}
    fyers = _fyers_index(provider, days) if getattr(provider, "name", "") == "fyers" else {}
    for d in days:
        try:  # US: SPY through the normal data source (Alpaca reaches back further)
            b = provider.intraday(idx, d) if m.key == "us" else fyers.get(d)
            if b is None or not len(b):
                b = _yf_intraday(idx, d, m)
        except Exception:
            b = pd.DataFrame()
        if len(b):
            b = b[(b.index.time >= m.open) & (b.index.time < m.close)]
        bars[d] = b
    n = len(days) + 40
    daily = provider.daily(idx, n) if m.key == "us" else _yf_daily(idx, n)
    daily.index = pd.to_datetime(daily.index).date  # sources differ: Timestamp vs date
    v = _yf_daily(vix, n)
    t = pd.DataFrame(index=sorted(set(daily.index)))
    t["prev_close"] = daily["close"].shift(1)
    t["above_sma20"] = (daily["close"] > daily["close"].rolling(20).mean()).shift(1)
    if len(v):
        vc = v["close"].reindex(t.index).ffill()
        t["vix_calm"] = (vc < vc.rolling(20).median()).shift(1)
    else:
        t["vix_calm"] = np.nan
    return bars, t


def tag_trades(trades: pd.DataFrame, bars: dict[date, pd.DataFrame], daily: pd.DataFrame) -> pd.DataFrame:
    """Add the index state at each entry, from bars that closed before it."""
    out = trades.copy()
    since_open, day_chg, above, calm = [], [], [], []
    for _, r in out.iterrows():
        b = bars.get(r["day"])
        ts = pd.Timestamp(r["entry_time"])
        row = daily.loc[r["day"]] if r["day"] in daily.index else None
        if b is None or not len(b) or row is None or ts.floor("min") <= b.index[0]:
            since_open.append(np.nan)
            day_chg.append(np.nan)
        else:
            seen = b[b.index < ts.floor("min")]
            last = float(seen["close"].iloc[-1])
            since_open.append(last / float(b["open"].iloc[0]) - 1)
            day_chg.append(last / float(row["prev_close"]) - 1 if row["prev_close"] == row["prev_close"] else np.nan)
        above.append(bool(row["above_sma20"]) if row is not None and row["above_sma20"] == row["above_sma20"] else np.nan)
        calm.append(bool(row["vix_calm"]) if row is not None and row["vix_calm"] == row["vix_calm"] else np.nan)
    out["idx_since_open"], out["idx_day"] = since_open, day_chg
    out["idx_above_sma20"], out["vix_calm"] = above, calm
    return out


# ---- scoring ----------------------------------------------------------------
def _score(r: pd.Series) -> dict:
    r = r.astype(float)
    losses = -r[r < 0].sum()
    return {"trades": len(r), "avg_R": round(r.mean(), 3) if len(r) else None,
            "total_R": round(r.sum(), 1), "win_%": round((r > 0).mean() * 100) if len(r) else None,
            "pf": round(r[r > 0].sum() / losses, 2) if losses else None}


def split_days(trades: pd.DataFrame, train_frac: float = 0.6) -> tuple[list[date], list[date]]:
    days = sorted(trades["day"].unique())
    k = max(1, int(len(days) * train_frac))
    return days[:k], days[k:]


def rr_table(by_rr: dict[float, pd.DataFrame], train: list[date], test: list[date]) -> pd.DataFrame:
    """Per strategy and R multiple: avg R in the earlier and later sessions."""
    rows = []
    for rr, t in by_rr.items():
        for name, g in t.groupby("strategy"):
            a, b = g[g["day"].isin(train)], g[g["day"].isin(test)]
            rows.append({"strategy": name, "target": f"{rr:g}R", "trades": len(g),
                         "avg_R": round(g["r"].mean(), 3), "early_avg_R": round(a["r"].mean(), 3) if len(a) else None,
                         "late_avg_R": round(b["r"].mean(), 3) if len(b) else None,
                         "win_%": round((g["r"] > 0).mean() * 100), "pnl": round(g["pnl"].sum(), 2)})
        rows.append({"strategy": "ALL", "target": f"{rr:g}R", "trades": len(t), "avg_R": round(t["r"].mean(), 3),
                     "early_avg_R": round(t[t["day"].isin(train)]["r"].mean(), 3),
                     "late_avg_R": round(t[t["day"].isin(test)]["r"].mean(), 3),
                     "win_%": round((t["r"] > 0).mean() * 100), "pnl": round(t["pnl"].sum(), 2)})
    return pd.DataFrame(rows).set_index(["strategy", "target"]).sort_index()


@dataclass
class RuleResult:
    name: str
    description: str
    kept_early: dict
    dropped_early: dict
    kept_late: dict
    dropped_late: dict
    passes: bool


def rule_results(tagged: pd.DataFrame, train: list[date], test: list[date]) -> list[RuleResult]:
    """A rule passes if the trades it keeps beat the ones it drops in the
    earlier sessions AND in the later ones, and the kept ones make money late."""
    out = []
    for name, (desc, test_fn) in RULES.items():
        need = {"low_fees": ["fee_R"], "calm_vix": ["vix_calm"], "index_uptrend": ["idx_above_sma20"],
                "up_since_open_and_uptrend": ["idx_since_open", "idx_above_sma20"]}.get(name, ["idx_since_open", "idx_day"])
        tt = tagged.dropna(subset=need)
        desc = desc.replace("FEE_CAP", f"{FEE_CAP:g}R")
        keep = test_fn(tt).astype(bool)
        early, late = tt["day"].isin(train), tt["day"].isin(test)
        ke, de = _score(tt[keep & early]["r"]), _score(tt[~keep & early]["r"])
        kl, dl = _score(tt[keep & late]["r"]), _score(tt[~keep & late]["r"])
        ok = all(x["trades"] >= 10 for x in (ke, kl)) and \
            (ke["avg_R"] or 0) > (de["avg_R"] if de["trades"] else -math.inf) and \
            (kl["avg_R"] or 0) > (dl["avg_R"] if dl["trades"] else -math.inf) and (kl["avg_R"] or 0) > 0
        out.append(RuleResult(name, desc, ke, de, kl, dl, ok))
    return out


@dataclass
class WalkForward:
    picks: pd.DataFrame          # day, pick, trades, R
    follow: dict                 # result of trading the daily pick
    everything: dict             # every strategy's every trade, same days
    notes: list = field(default_factory=list)


def walk_forward(trades: pd.DataFrame, lookback: int = 10, min_trades: int = 10,
                 rule: str | None = None) -> WalkForward:
    """Each day, pick the strategy with the best avg R over the previous
    `lookback` sessions (min `min_trades` trades); sit out if none is
    positive. Then take that strategy's trades that day (optionally only
    the ones `rule` keeps). This is the switch hint, replayed honestly."""
    t = trades
    if rule:
        t = t[RULES[rule][1](t).fillna(False).astype(bool)]
    days = sorted(trades["day"].unique())
    rows, taken = [], []
    for i in range(lookback, len(days)):
        past = t[t["day"].isin(days[i - lookback:i])]
        stats = past.groupby("strategy")["r"].agg(["count", "mean"])
        stats = stats[stats["count"] >= min_trades]
        pick = stats["mean"].idxmax() if len(stats) and stats["mean"].max() > 0 else None
        today = t[(t["day"] == days[i]) & (t["strategy"] == pick)] if pick else t.iloc[0:0]
        rows.append({"day": days[i], "pick": pick or "(sit out)", "trades": len(today),
                     "R": round(today["r"].sum(), 2)})
        taken.append(today)
    picks = pd.DataFrame(rows)
    took = pd.concat(taken) if taken else t.iloc[0:0]
    later = trades[trades["day"].isin(days[lookback:])]
    return WalkForward(picks, _score(took["r"]), _score(later["r"]))


# ---- does the expected-R score pick better trades? -------------------------------
def score_trades(trades: pd.DataFrame, lookback: int = 10, stock_sessions: int = 20) -> pd.DataFrame:
    """Each trade's expected-R score (ranking.py) from earlier sessions only:
    the strategy's trades in the previous `lookback` sessions and this
    stock's trades with that strategy in the previous `stock_sessions`.
    Backtest fills are at the entry, so there's no chase term. Trades
    before `lookback` sessions of history get no score."""
    from .ranking import edge

    days = sorted(trades["day"].unique())
    out = []
    for i in range(lookback, len(days)):
        d = days[i]
        past = trades[trades["day"].isin(days[i - lookback:i])]
        strat = past.groupby("strategy")["r"].agg(["mean", "count"])
        spast = trades[trades["day"].isin(days[max(0, i - stock_sessions):i])]
        stock = spast.groupby(["symbol", "strategy"])["r"].agg(["mean", "count"])
        today = trades[trades["day"] == d].copy()
        sc = []
        for r in today.itertuples():
            a = strat.loc[r.strategy] if r.strategy in strat.index else None
            k = stock.loc[(r.symbol, r.strategy)] if (r.symbol, r.strategy) in stock.index else None
            sc.append(edge(a["mean"] if a is not None else None, int(a["count"]) if a is not None else 0,
                           k["mean"] if k is not None else None, int(k["count"]) if k is not None else 0))
        today["score"] = sc
        out.append(today)
    return pd.concat(out) if out else trades.iloc[0:0].assign(score=[])


def pick_one_at_a_time(scored: pd.DataFrame, max_per_day: int, use_score: bool = True) -> pd.DataFrame:
    """Replay a trader with one position at a time and `max_per_day` trades:
    whenever flat, take the best-scoring setup triggering at that minute
    (only if its score is positive), or with use_score=False simply the first
    setup to trigger. Returns the trades taken."""
    taken = []
    for _, day in scored.groupby("day"):
        day = day.assign(t=pd.to_datetime(day["entry_time"]), x=pd.to_datetime(day["exit_time"]))
        day = day.sort_values(["t", "score"], ascending=[True, False]) if use_score else \
            day.sort_values(["t", "symbol"])  # baseline: no peeking at the score
        busy, n = None, 0
        for r in day.itertuples():
            if n >= max_per_day:
                break
            if busy is not None and r.t < busy:
                continue
            if use_score and not r.score > 0:
                continue
            taken.append(r.Index)
            busy, n = r.x, n + 1
    return scored.loc[taken]


@dataclass
class ScoreCheck:
    rule: RuleResult             # score > 0 kept vs dropped, both halves
    picker: dict                 # best-score trader
    first: dict                  # first-setup trader (no score), same limits
    every: dict                  # every scored trade
    picker_early: dict
    picker_late: dict
    first_early: dict
    first_late: dict


def score_check(trades: pd.DataFrame, lookback: int, max_per_day: int, train: list[date],
                test: list[date]) -> ScoreCheck | None:
    s = score_trades(trades, lookback)
    if not len(s):
        return None
    keep = s["score"] > 0
    early, late = s["day"].isin(train), s["day"].isin(test)
    rule = RuleResult("score_positive", "expected-R score above zero",
                      _score(s[keep & early]["r"]), _score(s[~keep & early]["r"]),
                      _score(s[keep & late]["r"]), _score(s[~keep & late]["r"]), False)
    rule.passes = all(x["trades"] >= 10 for x in (rule.kept_early, rule.kept_late)) and \
        (rule.kept_early["avg_R"] or 0) > (rule.dropped_early["avg_R"] if rule.dropped_early["trades"] else -math.inf) and \
        (rule.kept_late["avg_R"] or 0) > (rule.dropped_late["avg_R"] if rule.dropped_late["trades"] else -math.inf) and \
        (rule.kept_late["avg_R"] or 0) > 0
    pk, fi = pick_one_at_a_time(s, max_per_day), pick_one_at_a_time(s, max_per_day, use_score=False)
    half = lambda t, d: _score(t[t["day"].isin(d)]["r"])  # noqa: E731
    return ScoreCheck(rule, _score(pk["r"]), _score(fi["r"]), _score(s["r"]),
                      half(pk, train), half(pk, test), half(fi, train), half(fi, test))


# ---- the whole study ----------------------------------------------------------
@dataclass
class Study:
    market: Market
    sessions: tuple[date, date, int]
    rr: pd.DataFrame
    rules: list[RuleResult]
    hint: WalkForward
    hint_with_rule: dict[str, WalkForward]
    score: ScoreCheck | None
    untagged: int
    verdict: list[str]


def run_study(provider, cfg: dict, days: int = 30, rrs: tuple[float, ...] = (1.5, 2.0, 3.0),
              log=print) -> Study:
    from .backtest import run_backtest
    from .strategies import all_strategies

    global FEE_CAP
    FEE_CAP = float(cfg.get("risk", {}).get("max_fee_r") or FEE_CAP)
    by_rr: dict[float, pd.DataFrame] = {}
    for rr in rrs:
        # fee filter off here: it is judged below as one of the rules
        c = {**cfg, "risk": {**cfg.get("risk", {}), "reward_risk": rr, "max_fee_r": None}}
        log(f"Backtest with targets at {rr:g}R...")
        by_rr[rr] = run_backtest(provider, c, all_strategies(c), days=days, log=log, save=False)
    base_rr = float(cfg.get("risk", {}).get("reward_risk", 2.0))
    base = by_rr.get(base_rr)
    if base is None or base.empty:
        base = next(iter(by_rr.values()))
    if base.empty:
        raise RuntimeError("the backtest produced no trades - try more days")
    session_days = sorted(base["day"].unique())
    train, test = split_days(base)
    log("Index data for market conditions...")
    bars, daily = index_context(provider, session_days)
    tagged = tag_trades(base, bars, daily)
    from .costs import round_trip

    broker = cfg.get("broker")
    tagged["fee_R"] = [round_trip(provider.market.key, broker, r.shares * r.entry).total
                       / max((r.entry - r.stop) * r.shares, 1e-9) for r in tagged.itertuples()]
    untagged = int(tagged["idx_since_open"].isna().sum())
    fee_avg = float(tagged["fee_R"].mean())
    rules = rule_results(tagged, train, test)
    sh = cfg.get("switch_hint", {})
    lb = int(sh.get("lookback_days", 10))
    lb = min(lb, max(3, len(session_days) // 3))
    mt = int(sh.get("min_trades", 5))
    hint = walk_forward(tagged, lb, mt)
    with_rule = {r.name: walk_forward(tagged, lb, mt, r.name) for r in rules if r.passes}
    # the score is checked on the trades you'd actually see: with the configured fee filter
    live = tagged if not cfg.get("risk", {}).get("max_fee_r") else tagged[tagged["fee_R"] <= FEE_CAP]
    per_day = int((cfg.get("risk_limits") or {}).get("max_trades_per_day") or 3)
    sc = score_check(live, lb, per_day, train, test)

    v = []
    rrt = rr_table(by_rr, train, test).loc["ALL"]
    best = rrt["avg_R"].astype(float).idxmax()
    consistent = all((rrt.loc[best, k] or -9) >= (rrt[k].astype(float).max() - 1e-9) for k in ("early_avg_R", "late_avg_R"))
    v.append(f"Targets: {best} has the best average across all strategies ({rrt.loc[best, 'avg_R']:+.3f}R)"
             + (", in both halves." if consistent else ", but not in both halves - treat as noise."))
    v.append(f"Fees and taxes: {fee_avg:.2f}R per trade on average at your position size.")
    good = [r for r in rules if r.passes]
    v.append("Market filter: " + (", ".join(f"'{r.name}' passes" for r in good) if good
                                   else "no rule helped in both halves."))
    f = hint.follow
    v.append(f"Following the switch hint day by day: {f['trades']} trades, "
             + (f"{f['avg_R']:+.3f}R per trade, {f['total_R']:+.1f}R total." if f["trades"] else "it sat out every day."))
    for name, wf in with_rule.items():
        g = wf.follow
        if g["trades"]:
            v.append(f"  ...with '{name}': {g['trades']} trades, {g['avg_R']:+.3f}R per trade, {g['total_R']:+.1f}R total.")
    if sc:
        p, f = sc.picker, sc.first
        fmt = lambda x: f"{x['trades']} trades, {x['avg_R']:+.3f}R per trade" if x["trades"] else "no trades"  # noqa: E731
        v.append(f"Expected-R score: {'PASSES' if sc.rule.passes else 'does not pass'} the both-halves test. "
                 f"Taking the best-scoring setup, one at a time, up to {per_day} a day: {fmt(p)}; "
                 f"taking the first setup instead: {fmt(f)}.")
    return Study(provider.market, (session_days[0], session_days[-1], len(session_days)),
                 rr_table(by_rr, train, test), rules, hint, with_rule, sc, untagged, v)


def report(s: Study) -> str:
    """Plain-text report for the terminal (also saved to research.txt)."""
    a, b, n = s.sessions
    lines = [f"Edge check, {s.market.name}: {n} sessions {a} to {b}", ""]
    lines += ["== Verdict =="] + s.verdict + [""]
    lines += ["== Targets: average R per trade by target (early = first 60% of sessions, late = the rest) ==",
              s.rr.to_string(), ""]
    lines += ["== Market conditions at entry (kept = trades the rule allows) =="]
    if s.untagged:
        lines.append(f"({s.untagged} trades had no index data and are left out here)")
    for r in s.rules:
        lines.append(f"{'PASS' if r.passes else 'fail'}  {r.name}: {r.description}")
        for label, k, d in (("early", r.kept_early, r.dropped_early), ("late ", r.kept_late, r.dropped_late)):
            fmt = lambda x: f"{x['trades']:>4} trades {x['avg_R']:+.3f}R" if x["trades"] else "   0 trades"  # noqa: E731
            lines.append(f"      {label}: kept {fmt(k)} | dropped {fmt(d)}")
    if s.score:
        c = s.score
        fmt = lambda x: f"{x['trades']:>4} trades {x['avg_R']:+.3f}R" if x["trades"] else "   0 trades"  # noqa: E731
        lines += ["", "== Expected-R score (from earlier sessions only), on the trades you'd see =="]
        r = c.rule
        lines.append(f"{'PASS' if r.passes else 'fail'}  score above zero")
        for label, k, d in (("early", r.kept_early, r.dropped_early), ("late ", r.kept_late, r.dropped_late)):
            lines.append(f"      {label}: kept {fmt(k)} | dropped {fmt(d)}")
        lines.append(f"Best-score trader (one at a time): early {fmt(c.picker_early)} | late {fmt(c.picker_late)} "
                     f"| all {fmt(c.picker)}")
        lines.append(f"First-setup trader (no score):     early {fmt(c.first_early)} | late {fmt(c.first_late)} "
                     f"| all {fmt(c.first)}")
        lines.append(f"Every scored trade: {fmt(c.every)}")
    lines += ["", "== Switch hint replayed day by day (pick from previous sessions only) =="]
    lines.append(s.hint.picks.to_string(index=False) if len(s.hint.picks) else "(not enough sessions)")
    e = s.hint.everything
    lines.append(f"Taking every strategy's every trade on those days instead: {e['trades']} trades, "
                 f"{(e['avg_R'] or 0):+.3f}R per trade")
    return "\n".join(lines)
