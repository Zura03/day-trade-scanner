"""Dashboard design tokens, CSS and small HTML components.

Palette follows a validated data-viz reference (dark mode): one accent,
recessive chrome, and status colours reserved for state - always shown with
a text label, never colour alone.
"""
from __future__ import annotations

from datetime import timedelta
from html import escape

# ---- tokens -----------------------------------------------------------------
PAGE = "#0d0d0d"
SURFACE = "#1a1a19"
SURFACE_2 = "#222220"
INK = "#f0efec"
INK_2 = "#c3c2b7"
MUTED = "#898781"
GRID = "#2c2c2a"
AXIS = "#383835"
BORDER = "rgba(255,255,255,0.10)"
ACCENT = "#3987e5"          # series 1 (blue)
SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
GOOD = "#0ca30c"
WARN = "#fab219"
SERIOUS = "#ec835a"
BAD = "#d03b3b"

CSS = f"""
<style>
:root {{ --ink:{INK}; --ink2:{INK_2}; --muted:{MUTED}; --surface:{SURFACE}; --border:{BORDER}; }}
.block-container {{ padding-top: 3.2rem; padding-bottom: 3rem; max-width: 1500px; }}
header[data-testid="stHeader"] {{ background: transparent; }}
h1, h2, h3 {{ letter-spacing: -0.01em; }}
h3 {{ font-size: 1.05rem !important; margin-top: 1.4rem !important; }}
[data-testid="stCaptionContainer"], .stCaption {{ color: var(--muted) !important; }}
[data-testid="stDataFrame"] {{ font-variant-numeric: tabular-nums; }}
/* tabs: quieter, underline style */
.stTabs [data-baseweb="tab-list"] {{ gap: 1.4rem; border-bottom: 1px solid {GRID}; }}
.stTabs [data-baseweb="tab"] {{ padding: 0.4rem 0; color: var(--ink2); font-weight: 500; }}
.stTabs [aria-selected="true"] {{ color: var(--ink); }}
/* header bar */
.dt-header {{ display:flex; align-items:center; gap:1rem; flex-wrap:wrap;
  padding: 0.7rem 1rem; background: var(--surface); border:1px solid var(--border);
  border-radius: 8px; margin-bottom: 0.9rem; }}
.dt-title {{ font-weight: 650; font-size: 1.05rem; color: var(--ink); margin-right: auto; }}
.dt-meta {{ display:flex; gap:1.4rem; flex-wrap:wrap; font-size: 0.82rem; color: var(--ink2); }}
.dt-meta b {{ color: var(--ink); font-weight: 600; }}
.dt-meta span.k {{ color: var(--muted); margin-right: 0.35rem; }}
/* pills */
.dt-pill {{ display:inline-flex; align-items:center; gap:0.4rem; padding: 0.15rem 0.55rem;
  border-radius: 999px; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.02em;
  border: 1px solid var(--border); white-space: nowrap; }}
.dt-dot {{ width:7px; height:7px; border-radius:50%; display:inline-block; }}
/* stat tiles */
.dt-tiles {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.6rem;
  margin: 0.4rem 0 0.8rem; }}
.dt-tile {{ background: var(--surface); border:1px solid var(--border); border-radius: 8px;
  padding: 0.65rem 0.85rem; }}
.dt-tile .l {{ font-size: 0.74rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }}
.dt-tile .v {{ font-size: 1.45rem; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums;
  margin-top: 0.1rem; }}
.dt-tile .s {{ font-size: 0.75rem; color: var(--ink2); margin-top: 0.1rem; }}
/* section heading with count */
.dt-section {{ display:flex; align-items:baseline; gap:0.6rem; margin: 1.3rem 0 0.4rem; }}
.dt-section .t {{ font-size: 1rem; font-weight: 600; color: var(--ink); }}
.dt-section .c {{ font-size: 0.8rem; color: var(--muted); }}
.dt-empty {{ padding: 0.9rem 1rem; border: 1px dashed {AXIS}; border-radius: 8px; color: var(--ink2);
  font-size: 0.86rem; background: transparent; }}
/* alert banner */
.dt-banner {{ padding: 0.7rem 1rem; border-radius: 8px; border: 1px solid {BAD};
  background: rgba(208,59,59,0.10); color: var(--ink); font-size: 0.9rem; margin-bottom: 0.8rem; }}
.dt-banner b {{ color: #ff8a8a; }}
section[data-testid="stSidebar"] h1 {{ font-size: 1.05rem; }}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{ font-size: 0.8rem; line-height: 1.45; }}
@media (max-width: 640px) {{
  .dt-meta {{ gap: 0.8rem; }}
  .dt-tile .v {{ font-size: 1.2rem; }}
}}
</style>
"""

STATUS_STYLE = {          # table cell styles for scan status (text, not filled blocks)
    "BUY NOW": f"color:{GOOD};font-weight:600",
    "SETTING UP": f"color:{WARN};font-weight:600",
    "qualifies": f"color:{INK_2}",
    "no setup": f"color:{MUTED}",
}


def status_cell(v) -> str:
    if isinstance(v, str) and v.startswith("triggered"):
        return f"color:{MUTED}"
    return STATUS_STYLE.get(v, "")


def action_cell(v) -> str:
    return {"SELL NOW": f"color:{BAD};font-weight:700", "HOLD": f"color:{GOOD};font-weight:600",
            "NO DATA": f"color:{MUTED}"}.get(v, "")


def verdict_cell(v) -> str:
    return {"Favourable": f"color:{GOOD};font-weight:600", "Unfavourable": f"color:{BAD};font-weight:600",
            "Target out of reach": f"color:{WARN}", "Coin flip": f"color:{INK_2}",
            "No track record": f"color:{MUTED}"}.get(v, "")


def signed_cell(v) -> str:
    """Green/red for signed values (numbers or '+0.22R' strings)."""
    try:
        x = float(str(v).replace("R", "").split()[0])
    except (ValueError, IndexError):
        return f"color:{MUTED}"
    return f"color:{GOOD}" if x > 0 else f"color:{BAD}" if x < 0 else ""


def dash_fill(df, money: list[str] = (), whole: list[str] = ()):
    """Render empty numeric cells as a quiet dash (the grid would print 'None').
    Turns those columns into text, so give them TextColumn configs."""
    df = df.copy()
    for c in money:
        df[c] = [f"{v:,.2f}" if v is not None and v == v else "–" for v in df[c]]
    for c in whole:
        df[c] = [f"{int(v):,}" if v is not None and v == v else "–" for v in df[c]]
    return df


def pill(text: str, color: str) -> str:
    return (f'<span class="dt-pill" style="color:{INK}"><span class="dt-dot" style="background:{color}">'
            f'</span>{escape(text)}</span>')


def market_state(m) -> tuple[str, str, str]:
    """(label, colour, detail) for a market right now."""
    now = m.now()
    hours = m.session_hours(now.date())
    if hours and hours[0] <= now < hours[1]:
        return "Open", GOOD, f"closes {hours[1]:%H:%M} {m.tz_label}"
    if hours and m.premarket and now.time() >= m.premarket and now < hours[0]:
        return "Premarket", WARN, f"opens {hours[0]:%H:%M} {m.tz_label}"
    nxt = next((d for d in [now.date() + timedelta(days=k) for k in range(0, 8)]
                if (h := m.session_hours(d)) and h[0] > now), None)
    if nxt:
        h = m.session_hours(nxt)
        when = f"{h[0]:%a %H:%M}" if nxt != now.date() else f"{h[0]:%H:%M}"
        return "Closed", MUTED, f"opens {when} {m.tz_label}"
    return "Closed", MUTED, ""


def header(m, source: str, strategy_title: str, last_scan: str | None) -> str:
    label, color, detail = market_state(m)
    meta = [("Data", source), ("Strategy", strategy_title)]
    if last_scan:
        meta.append(("Last scan", last_scan))
    items = "".join(f'<div><span class="k">{escape(k)}</span><b>{escape(v)}</b></div>' for k, v in meta)
    return (f'<div class="dt-header"><div class="dt-title">Day Trade Scanner'
            f'<span style="color:{MUTED};font-weight:500"> / {escape(m.name)}</span></div>'
            f'{pill(f"{label}" + (f" · {detail}" if detail else ""), color)}'
            f'<div class="dt-meta">{items}</div></div>')


def tiles(items: list[tuple[str, str, str | None]]) -> str:
    """Stat tiles: (label, value, sub)."""
    cells = "".join(
        f'<div class="dt-tile"><div class="l">{escape(l)}</div><div class="v">{escape(v)}</div>'
        + (f'<div class="s">{escape(s)}</div>' if s else "") + "</div>" for l, v, s in items)
    return f'<div class="dt-tiles">{cells}</div>'


def section(title: str, count: str | None = None) -> str:
    c = f'<span class="c">{escape(count)}</span>' if count else ""
    return f'<div class="dt-section"><span class="t">{escape(title)}</span>{c}</div>'


def empty(text: str) -> str:
    return f'<div class="dt-empty">{escape(text)}</div>'


def banner(html_text: str) -> str:
    return f'<div class="dt-banner">{html_text}</div>'


def notice(html_text: str) -> str:
    """Amber notice (state, not error): e.g. BUY alerts paused by a risk limit."""
    return (f'<div class="dt-banner" style="border-color:{WARN};background:rgba(250,178,25,0.08)">'
            f'{html_text}</div>')


def chart_layout(fig, height: int = 460, title: str | None = None):
    """Recessive chrome: surface background, hairline grid, muted axes."""
    fig.update_layout(
        height=height, template="plotly_dark", paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family="Inter, sans-serif", size=12, color=INK_2),
        title=dict(text=title, font=dict(size=14, color=INK), x=0.01, y=0.97) if title else None,
        margin=dict(l=12, r=12, t=44 if title else 16, b=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1,
                    font=dict(color=INK_2, size=11), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor=SURFACE_2, bordercolor=AXIS, font=dict(color=INK, size=12)),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor=GRID, linecolor=AXIS, zeroline=False, tickfont=dict(color=MUTED),
                     showspikes=True, spikecolor=AXIS, spikethickness=1, spikedash="solid")
    fig.update_yaxes(gridcolor=GRID, linecolor=AXIS, zeroline=False, tickfont=dict(color=MUTED), side="right",
                     hoverformat=".2f")
    return fig
