"""Render the BI agent's output as a self-contained HTML dashboard (Plotly).

Design rules (kept on purpose):
- one y-axis per chart, never dual axis
- one series = one colour (slot 1); several series = fixed colour order, legend shown
- colour follows the entity (sorted names), never its rank
- thin marks, rounded bar ends, hairline grid, hover tooltips on every chart
- KPI deltas use arrow + text + colour, never colour alone
"""
from __future__ import annotations

import html
import os
from datetime import datetime
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

# Reference palette (validated categorical order, light mode)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SURFACE, PAGE = "#fcfcfb", "#f9f9f7"
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
GOOD_TEXT, BAD_TEXT = "#006300", "#d03b3b"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

MAX_CATEGORIES = 25


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------

def compact(v, fmt: str = "number") -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if fmt == "percent":
        return f"{v * 100:.1f}%"
    if fmt == "decimal":
        return f"{v:,.2f}"
    a = abs(v)
    if a >= 1e9:
        s = f"{v / 1e9:.2f}B"
    elif a >= 1e6:
        s = f"{v / 1e6:.2f}M"
    elif a >= 1e4:
        s = f"{v / 1e3:.1f}K"
    else:
        s = f"{v:,.0f}" if a >= 100 else f"{v:,.2f}"
    return ("$" + s) if fmt == "currency" else s


def full(v, fmt: str = "number") -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if fmt == "percent":
        return f"{v * 100:.2f}%"
    if fmt == "currency":
        return f"${v:,.0f}"
    if fmt == "decimal":
        return f"{v:,.2f}"
    return f"{v:,.0f}" if float(v).is_integer() or abs(v) >= 100 else f"{v:,.2f}"


HOVER = {"currency": "$,.0f", "percent": ".1%", "decimal": ",.2f", "number": ",.0f"}


def axis_fmt(fmt: str) -> dict:
    if fmt == "percent":
        return {"tickformat": ".0%"}
    if fmt == "decimal":
        return {"tickformat": ",.2f"}
    out = {"exponentformat": "B", "separatethousands": True}
    if fmt == "currency":
        out["tickprefix"] = "$"
    return out


# ---------------------------------------------------------------------------
# Column helpers
# ---------------------------------------------------------------------------

def find_col(df: pd.DataFrame, name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    lookup = {c.lower(): c for c in df.columns}
    return lookup.get(str(name).lower()) or lookup.get(str(name).split(".")[-1].lower())


def numeric_cols(df):
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def is_time_like(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in ("year", "month", "quarter", "date", "week", "day"))


def colour_map(values) -> dict:
    names = sorted({str(v) for v in values})
    return {n: SERIES[i] for i, n in enumerate(names[: len(SERIES)])}


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        if df[c].dtype == object:
            conv = pd.to_numeric(df[c], errors="coerce")
            if conv.notna().all() and len(conv):
                df[c] = conv
    return df


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def base_layout(fig: go.Figure, fmt: str, legend: bool, horizontal: bool = False) -> go.Figure:
    value_axis = dict(gridcolor=GRID, gridwidth=1, zeroline=False, showline=False, automargin=True,
                      tickfont=dict(color=MUTED, size=11), **axis_fmt(fmt))
    cat_axis = dict(showgrid=False, linecolor=AXIS, linewidth=1, showline=True,
                    tickfont=dict(color=MUTED, size=11), automargin=True)
    fig.update_layout(
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family=FONT, color=INK_2, size=12),
        margin=dict(l=8, r=16, t=8, b=8), height=340,
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=11, color=INK_2)),
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=GRID, font=dict(family=FONT, color=INK, size=12)),
        bargap=0.35, bargroupgap=0.08,
    )
    if horizontal:
        fig.update_xaxes(**value_axis)
        fig.update_yaxes(**cat_axis)
    else:
        fig.update_yaxes(**value_axis)
        fig.update_xaxes(**cat_axis)
    return fig


def build_figure(spec: dict, df: pd.DataFrame) -> Optional[go.Figure]:
    """Return a Plotly figure, or None when the data suits a table better."""
    df = prepare(df)
    if df.empty:
        return None
    kind = (spec.get("type") or "bar").lower()
    fmt = spec.get("format") or "number"
    nums = numeric_cols(df)
    others = [c for c in df.columns if c not in nums]

    x = find_col(df, spec.get("x")) or (others[0] if others else df.columns[0])
    y = find_col(df, spec.get("y"))
    if y is None or y == x:
        y = next((c for c in nums if c != x), None)
    color = find_col(df, spec.get("color"))
    if color in (x, y):
        color = None
    if kind == "table" or y is None or len(df) < 2:
        return None

    hover_y = HOVER.get(fmt, ",.0f")

    if kind == "heatmap":
        dim2 = find_col(df, spec.get("y")) if spec.get("value") else None
        val = find_col(df, spec.get("value")) or y
        dim2 = dim2 if dim2 and dim2 != val else (others[1] if len(others) > 1 else None)
        if not dim2:
            kind = "bar"
        else:
            pv = df.pivot_table(index=dim2, columns=x, values=val, aggfunc="sum")
            fig = go.Figure(go.Heatmap(
                z=pv.values, x=[str(c) for c in pv.columns], y=[str(i) for i in pv.index],
                text=[[compact(v, fmt) for v in row] for row in pv.values],
                texttemplate="%{text}" if pv.size <= 80 else None, textfont=dict(size=11),
                colorscale=[[i / (len(SEQ_BLUE) - 1), c] for i, c in enumerate(SEQ_BLUE)],
                xgap=2, ygap=2,
                colorbar=dict(thickness=10, outlinewidth=0, tickfont=dict(color=MUTED, size=11),
                              tickprefix="$" if fmt == "currency" else "", exponentformat="B"),
                hovertemplate=f"{x}: %{{x}}<br>{dim2}: %{{y}}<br>{val}: %{{z:{hover_y}}}<extra></extra>",
            ))
            fig = base_layout(fig, "number", legend=False)
            fig.update_xaxes(type="category")
            return fig

    if kind == "scatter":
        xs = find_col(df, spec.get("x"))
        if xs in nums:
            fig = go.Figure()
            groups = [(None, df)] if not color else list(df.groupby(color))[:3]
            cmap = colour_map(df[color]) if color else {}
            label = next((c for c in others if c != color), None)
            for name, g in groups:
                fig.add_trace(go.Scatter(
                    x=g[xs], y=g[y], mode="markers", name=str(name) if name is not None else y,
                    marker=dict(size=9, color=cmap.get(str(name), SERIES[0]), line=dict(color=SURFACE, width=2)),
                    text=g[label] if label else None,
                    hovertemplate=(f"%{{text}}<br>" if label else "") + f"{xs}: %{{x:,}}<br>{y}: %{{y:{hover_y}}}<extra></extra>",
                ))
            fig = base_layout(fig, fmt, legend=bool(color))
            fig.update_xaxes(showgrid=True, gridcolor=GRID)
            return fig
        kind = "bar"

    time_x = is_time_like(x)
    if time_x:
        df = df.sort_values(x)
    if kind in ("line", "area") or (time_x and kind == "bar" and not color and len(df) > 12):
        fig = go.Figure()
        series = [(None, df)] if not color else list(df.groupby(color))[: len(SERIES)]
        cmap = colour_map(df[color]) if color else {}
        for i, (name, g) in enumerate(series):
            c = cmap.get(str(name), SERIES[0])
            g = g.reset_index(drop=True)
            labels = [""] * len(g)
            if len(g) and len(series) <= 3:
                labels[-1] = compact(g[y].iloc[-1], fmt)
                if not color:
                    peak = int(pd.to_numeric(g[y], errors="coerce").idxmax())
                    labels[peak] = compact(g[y].iloc[peak], fmt)
                    labels[0] = compact(g[y].iloc[0], fmt)
            fig.add_trace(go.Scatter(
                x=g[x].astype(str), y=g[y], name=str(name) if name is not None else y,
                text=labels, textposition="top center", textfont=dict(color=INK_2, size=11),
                mode=("lines+markers" if len(g) <= 24 else "lines") + "+text",
                line=dict(color=c, width=2), marker=dict(size=8, color=c, line=dict(color=SURFACE, width=2)),
                fill="tozeroy" if kind == "area" and not color else None,
                fillcolor=(c + "22") if kind == "area" and not color else None,
                hovertemplate=f"%{{y:{hover_y}}}",
            ))
        fig = base_layout(fig, fmt, legend=bool(color))
        fig.update_layout(hovermode="x unified", margin=dict(l=8, r=48, t=28, b=8))
        fig.update_traces(cliponaxis=False)
        fig.update_xaxes(type="category")
        return fig

    # bars
    horizontal = kind == "hbar" or (not time_x and not color and df[x].astype(str).str.len().max() > 12)
    if not color:
        if not time_x:
            df = df.sort_values(y, ascending=False)
        df = df.head(15 if horizontal else MAX_CATEGORIES)
        cats = df[x].astype(str)
        trace = go.Bar(
            x=df[y] if horizontal else cats, y=cats if horizontal else df[y],
            orientation="h" if horizontal else "v",
            marker=dict(color=SERIES[0], cornerradius=4, line=dict(width=0)),
            text=[compact(v, fmt) for v in df[y]], textposition="outside", cliponaxis=False,
            textfont=dict(color=INK_2, size=11),
            hovertemplate=f"%{{{'y' if horizontal else 'x'}}}<br>{y}: %{{{'x' if horizontal else 'y'}:{hover_y}}}<extra></extra>",
        )
        fig = base_layout(go.Figure(trace), fmt, legend=False, horizontal=horizontal)
        n = len(df)
        fig.update_layout(bargap=0.55 if n <= 6 else 0.35, margin=dict(l=8, r=64 if horizontal else 16, t=28, b=8))
        if horizontal:
            fig.update_layout(height=max(220, min(520, 34 * n + 70)))
            fig.update_yaxes(autorange="reversed", type="category")
        else:
            fig.update_xaxes(type="category")
        return fig

    # grouped / stacked by a colour column (fixed colour order, legend on)
    keep = df.groupby(color)[y].sum().sort_values(ascending=False).index[:6]
    df = df[df[color].isin(keep)]
    cmap = colour_map(keep)
    fig = go.Figure()
    for name in sorted(map(str, keep)):
        g = df[df[color].astype(str) == name]
        show_labels = len(df) <= (30 if kind == "stacked_bar" else 24)
        fig.add_trace(go.Bar(
            x=g[x].astype(str), y=g[y], name=name,
            marker=dict(color=cmap[name], cornerradius=4 if kind != "stacked_bar" else 0,
                        line=dict(color=SURFACE, width=2)),
            text=[compact(v, fmt) for v in g[y]] if show_labels else None,
            textposition="inside" if kind == "stacked_bar" else "outside",
            insidetextanchor="middle", cliponaxis=False,
            textfont=dict(size=10, color="#ffffff" if kind == "stacked_bar" else INK_2),
            hovertemplate=f"{name}: %{{y:{hover_y}}}<extra></extra>",
        ))
    fig = base_layout(fig, fmt, legend=True)
    fig.update_layout(barmode="stack" if kind == "stacked_bar" else "group", hovermode="x unified",
                      uniformtext=dict(minsize=9, mode="hide"), margin=dict(l=8, r=16, t=28, b=8))
    fig.update_xaxes(type="category")
    return fig


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def table_html(df: pd.DataFrame, formats: Optional[dict] = None, max_rows: int = 50) -> str:
    df = prepare(df).head(max_rows)
    nums = set(numeric_cols(df))
    head = "".join(f'<th class="{"num" if c in nums else ""}">{html.escape(str(c))}</th>' for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            if c in nums:
                cells.append(f'<td class="num">{html.escape(full(r[c], (formats or {}).get(c, "number")))}</td>')
            else:
                cells.append(f"<td>{html.escape(str(r[c]))}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def kpi_html(k: dict) -> str:
    fmt = k.get("format") or "number"
    value = compact(k.get("value"), fmt)
    delta = ""
    prior = k.get("prior")
    if prior not in (None, 0) and k.get("value") is not None:
        try:
            change = (float(k["value"]) - float(prior)) / abs(float(prior))
            if abs(change) < 0.0005:
                return (
                    f'<div class="card kpi"><div class="kpi-label">{html.escape(k.get("label", ""))}</div>'
                    f'<div class="kpi-value">{html.escape(value)}</div>'
                    f'<div class="delta" style="color:{INK_2}"><span aria-hidden="true">&#9644;</span> no change '
                    f'<span class="muted">{html.escape(k.get("prior_label") or "vs prior")} '
                    f'({html.escape(compact(prior, fmt))})</span></div></div>'
                )
            up = change >= 0
            good = up == bool(k.get("higher_is_better", True))
            arrow = "&#9650;" if up else "&#9660;"
            word = "up" if up else "down"
            delta = (
                f'<div class="delta" style="color:{GOOD_TEXT if good else BAD_TEXT}">'
                f'<span aria-hidden="true">{arrow}</span> {word} {abs(change) * 100:.1f}% '
                f'<span class="muted">{html.escape(k.get("prior_label") or "vs prior")} '
                f'({html.escape(compact(prior, fmt))})</span></div>'
            )
        except (TypeError, ValueError):
            pass
    err = f'<div class="muted small">{html.escape(k["error"][:120])}</div>' if k.get("error") else ""
    return (
        f'<div class="card kpi"><div class="kpi-label">{html.escape(k.get("label", ""))}</div>'
        f'<div class="kpi-value">{html.escape(value)}</div>{delta}{err}</div>'
    )


def md_to_html(text: str) -> str:
    lines, prev = [], ""
    for line in (text or "").splitlines():
        is_item = line.lstrip().startswith(("- ", "* "))
        if is_item and prev.strip() and not prev.lstrip().startswith(("- ", "* ")):
            lines.append("")
        lines.append(line)
        prev = line
    text = "\n".join(lines)
    try:
        import markdown

        return markdown.markdown(text)
    except ImportError:
        return "<p>" + html.escape(text or "").replace("\n", "<br>") + "</p>"


AGENTS = {
    "analyst": ("Analyst agent", SERIES[0]),
    "bi": ("BI agent", SERIES[6]),
    "summary": ("Summary agent", SERIES[2]),
}

CSS = f"""
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: {PAGE}; color: {INK}; font-family: {FONT}; }}
.wrap {{ max-width: 1280px; margin: 0 auto; padding: 28px 28px 48px; }}
header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; flex-wrap: wrap; margin-bottom: 20px; }}
h1 {{ font-size: 26px; font-weight: 650; margin: 0 0 6px; letter-spacing: -0.01em; }}
.question {{ color: {INK_2}; font-size: 15px; margin: 0; }}
.meta {{ color: {MUTED}; font-size: 12px; text-align: right; margin-top: 8px; }}
.toolbar {{ display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; align-items: center; }}
.agent-btn {{ font: inherit; font-size: 12.5px; padding: 6px 12px; border-radius: 999px; cursor: pointer;
             background: {SURFACE}; border: 1px solid rgba(11,11,11,0.12); color: {INK_2};
             display: inline-flex; align-items: center; gap: 6px; transition: all .15s ease; }}
.agent-btn:hover {{ border-color: rgba(11,11,11,0.30); color: {INK}; }}
.agent-btn.active {{ background: {INK}; color: #fff; border-color: {INK}; }}
.dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; }}
.switch {{ display: inline-flex; align-items: center; gap: 8px; font-size: 12.5px; color: {INK_2};
          margin-left: 8px; cursor: pointer; user-select: none; }}
.switch input {{ display: none; }}
.track {{ width: 32px; height: 18px; border-radius: 999px; background: {AXIS}; position: relative; transition: background .15s; }}
.track::after {{ content: ""; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%;
                background: #fff; transition: transform .15s; }}
.switch input:checked + .track {{ background: {SERIES[0]}; }}
.switch input:checked + .track::after {{ transform: translateX(14px); }}
.src {{ font-size: 11px; padding: 3px 9px; border-radius: 999px; border: 1px dashed {AXIS}; color: {MUTED}; }}
.grid-kpi {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; margin-bottom: 14px; }}
.grid-charts {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
.card {{ background: {SURFACE}; border: 1px solid rgba(11,11,11,0.10); border-radius: 14px; padding: 18px 20px;
        position: relative; animation: rise .45s ease both; }}
@keyframes rise {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: none; }} }}
.tag {{ position: absolute; top: 14px; right: 16px; font-size: 10.5px; color: {MUTED};
       display: inline-flex; align-items: center; gap: 5px; }}
.tag .dot {{ width: 6px; height: 6px; }}
.wide {{ grid-column: 1 / -1; }}
.kpi-label {{ font-size: 13px; color: {INK_2}; margin-bottom: 8px; padding-right: 70px; }}
.kpi-value {{ font-size: 30px; font-weight: 600; letter-spacing: -0.01em; }}
.delta {{ font-size: 12.5px; margin-top: 8px; font-weight: 500; }}
.muted {{ color: {MUTED}; font-weight: 400; }}
.small {{ font-size: 11px; margin-top: 6px; }}
h2 {{ font-size: 15px; font-weight: 600; margin: 0 0 2px; padding-right: 90px; }}
.sub {{ font-size: 12.5px; color: {MUTED}; margin: 0 0 12px; }}
.summary {{ margin-bottom: 14px; font-size: 14.5px; line-height: 1.55; }}
.summary p {{ margin: 0 0 10px; }} .summary ul {{ margin: 4px 0 10px; padding-left: 20px; }}
.summary strong {{ font-weight: 600; }}
.table-wrap {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th {{ text-align: left; font-weight: 600; color: {INK_2}; border-bottom: 1px solid {AXIS}; padding: 8px 10px; white-space: nowrap; }}
td {{ border-bottom: 1px solid {GRID}; padding: 7px 10px; white-space: nowrap; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
tbody tr:hover {{ background: #f4f3ef; }}
details {{ margin-top: 10px; }} summary {{ cursor: pointer; color: {INK_2}; font-size: 12.5px; }}
pre {{ background: #f4f3ef; border-radius: 8px; padding: 12px; font-size: 12px; overflow-x: auto; white-space: pre-wrap; margin: 8px 0; }}
.reasoning {{ display: none; border-style: dashed; }}
body.show-reasoning .reasoning {{ display: block; }}
.hidden {{ display: none !important; }}
.step {{ border-left: 2px solid {GRID}; padding: 2px 0 2px 14px; margin: 10px 0; }}
.step.ok {{ border-color: {GOOD_TEXT}; }} .step.bad {{ border-color: {BAD_TEXT}; }}
.badge {{ font-size: 11px; padding: 2px 8px; border-radius: 999px; font-weight: 500; }}
.badge.ok {{ background: #e6f2e6; color: {GOOD_TEXT}; }}
.badge.fix {{ background: #fff3d6; color: #7a5200; }}
.badge.bad {{ background: #fbe7e7; color: {BAD_TEXT}; }}
.err {{ color: {BAD_TEXT}; font-size: 12px; }}
footer {{ margin-top: 28px; color: {MUTED}; font-size: 12px; }}
@media (max-width: 900px) {{ .grid-charts {{ grid-template-columns: 1fr; }} }}
"""

JS = """
function showAgent(agent, btn) {
  document.querySelectorAll('[data-agent]').forEach(function (el) {
    el.classList.toggle('hidden', !(agent === 'all' || el.dataset.agent === agent));
  });
  document.querySelectorAll('.agent-btn').forEach(function (b) { b.classList.toggle('active', b === btn); });
  setTimeout(function () { window.dispatchEvent(new Event('resize')); }, 60);
}
function toggleReasoning(cb) {
  document.body.classList.toggle('show-reasoning', cb.checked);
  setTimeout(function () { window.dispatchEvent(new Event('resize')); }, 60);
}
document.addEventListener('DOMContentLoaded', function () {
  var cb = document.getElementById('reasoning-toggle');
  if (document.body.classList.contains('show-reasoning') && cb) { cb.checked = true; }
});
"""


def tag(agent: str) -> str:
    name, colour = AGENTS[agent]
    return f'<span class="tag"><span class="dot" style="background:{colour}"></span>{name}</span>'


def card(agent: str, inner: str, extra_class: str = "") -> str:
    return f'<section class="card {extra_class}" data-agent="{agent}">{tag(agent)}{inner}</section>'


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def analyst_reasoning(state: dict) -> str:
    steps = []
    for e in state.get("analyst_log") or []:
        ok = not e.get("error")
        status = (f'<span class="badge ok">ok, {e.get("rows", 0)} rows</span>' if ok
                  else '<span class="badge bad">failed, retried</span>')
        err = "" if ok else f'<div class="err">{esc(e["error"][:400])}</div>'
        steps.append(f'<div class="step {"ok" if ok else "bad"}"><b>Attempt {e.get("attempt")}</b> {status}'
                     f'<pre>{esc(e.get("sql"))}</pre>{err}</div>')
    semantic = "semantic view first, star schema SQL as fallback" if os.getenv("SEMANTIC_VIEW") else "star schema SQL"
    body = (f'<h2>Analyst agent reasoning</h2><p class="sub">Read the metric definitions and table docs, '
            f'wrote SQL ({semantic}), ran it on Snowflake, retried on errors (max 3).</p>' + "".join(steps))
    return card("analyst", body, "wide reasoning")


def bi_reasoning(bi: dict) -> str:
    repaired = {r["id"].replace("_prior", "") for r in bi.get("repairs", []) if r.get("fixed_sql") and not r.get("fixed_error")}
    rows = []
    for k in bi.get("kpis", []):
        status = "bad" if k.get("error") else ("fix" if k.get("id") in repaired else "ok")
        label = {"ok": "ok", "fix": "repaired", "bad": "skipped"}[status]
        rows.append(f'<tr><td>{esc(k.get("id"))}</td><td>KPI</td><td>{esc(k.get("label"))}</td>'
                    f'<td><span class="badge {status}">{label}</span></td></tr>')
    for c in bi.get("charts", []):
        status = "bad" if c.get("error") else ("fix" if c.get("id") in repaired else "ok")
        label = {"ok": "ok", "fix": "repaired", "bad": "skipped"}[status]
        rows.append(f'<tr><td>{esc(c.get("id"))}</td><td>{esc(c.get("type"))}</td><td>{esc(c.get("title"))}</td>'
                    f'<td><span class="badge {status}">{label}</span></td></tr>')
    plan = ('<div class="table-wrap"><table><thead><tr><th>Tile</th><th>Type</th><th>Title</th><th>Status</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    fixes = []
    for r in bi.get("repairs", []):
        outcome = "fixed" if r.get("fixed_sql") and not r.get("fixed_error") else "still failing, tile skipped"
        fixes.append(f'<div class="step {"ok" if outcome == "fixed" else "bad"}"><b>{esc(r.get("title") or r.get("id"))}</b> '
                     f'<span class="badge {"fix" if outcome == "fixed" else "bad"}">{outcome}</span>'
                     f'<div class="err">{esc(r.get("error"))}</div>'
                     f'<details><summary>Original SQL</summary><pre>{esc(r.get("original_sql"))}</pre></details>'
                     f'<details><summary>Fixed SQL</summary><pre>{esc(r.get("fixed_sql"))}</pre></details></div>')
    sql_blocks = []
    for k in bi.get("kpis", []):
        sql_blocks.append(f'<details><summary>KPI: {esc(k.get("label"))}</summary><pre>{esc(extract(k.get("sql")))}</pre>'
                          + (f'<pre>-- prior period\n{esc(extract(k.get("prior_sql")))}</pre>' if k.get("prior_sql") else "")
                          + "</details>")
    for c in bi.get("charts", []):
        sql_blocks.append(f'<details><summary>Chart: {esc(c.get("title"))}</summary><pre>{esc(c.get("sql_run"))}</pre></details>')
    plan_note = ""
    if bi and not bi.get("plan_ok", True):
        plan_note = (f'<div class="err">The dashboard plan could not be read after {bi.get("plan_attempts")} attempts, '
                     f'so only the Analyst chart is shown. End of the model reply:</div>'
                     f'<pre>{esc(bi.get("plan_raw_tail"))}</pre>')
    elif bi.get("plan_attempts", 1) > 1:
        plan_note = '<p class="sub">First plan reply was unreadable; the second attempt worked.</p>'
    body = (f'<h2>BI agent reasoning</h2>{plan_note}<p class="sub">Planned {len(bi.get("kpis", []))} KPIs and '
            f'{len(bi.get("charts", []))} charts, ran {bi.get("queries_run", 0)} queries, '
            f'repaired {len(repaired)}.</p>{plan}'
            + (f'<h2 style="margin-top:16px">Repairs</h2>{"".join(fixes)}' if fixes else "")
            + f'<h2 style="margin-top:16px">SQL behind each tile</h2>{"".join(sql_blocks)}')
    return card("bi", body, "wide reasoning")


def summary_reasoning(bi: dict, state: dict) -> str:
    k = len([x for x in bi.get("kpis", []) if not x.get("error")])
    c = len([x for x in bi.get("charts", []) if not x.get("error")])
    body = ('<h2>Summary agent reasoning</h2>'
            f'<p class="sub">Wrote the narrative from {k} KPI tiles and {c} charts. It may only use numbers '
            'present in those tiles, and is told not to over-read tiny differences.</p>')
    if state.get("error"):
        body += f'<div class="err">Analyst failed: {esc(state["error"][:300])}</div>'
    return card("summary", body, "wide reasoning")


def extract(sql) -> str:
    import re

    if not sql:
        return ""
    m = re.search(r"```(?:sql)?\s*(.*?)```", str(sql), re.DOTALL | re.IGNORECASE)
    return (m.group(1) if m else str(sql)).strip()


def render_dashboard(state: dict, path: str) -> str:
    bi = state.get("bi") or {}
    offline = os.getenv("DASHBOARD_OFFLINE", "").lower() in ("1", "true", "yes")
    plotly_loaded = False
    config = {"displaylogo": False, "responsive": True,
              "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]}

    kpis = "".join(
        kpi_html(k).replace('<div class="card kpi">', f'<div class="card kpi">{tag("bi")}', 1)
        for k in bi.get("kpis", [])
    )

    chart_cards = []
    for i, ch in enumerate(bi.get("charts", [])):
        if ch.get("error"):
            continue
        title = esc(ch.get("title", ""))
        sub = f'<p class="sub">{esc(ch["subtitle"])}</p>' if ch.get("subtitle") else '<p class="sub">&nbsp;</p>'
        df = pd.DataFrame(ch.get("data") or [], columns=ch.get("columns"))
        fig = build_figure(ch, df)
        if fig is not None:
            include = (True if offline else "cdn") if not plotly_loaded else False
            body = pio.to_html(fig, full_html=False, include_plotlyjs=include, config=config, default_width="100%")
            plotly_loaded = True
        else:
            fmt = ch.get("format") or "number"
            body = table_html(df, {c: fmt for c in df.columns})
        wide = "wide" if i == 0 or (fig is None and len(df.columns) > 4) else ""
        chart_cards.append(card("bi", f"<h2>{title}</h2>{sub}{body}", wide))

    main_df = pd.DataFrame(state.get("data") or [], columns=state.get("columns"))
    main_fmt = (bi.get("charts") or [{}])[0].get("format", "number")
    if state.get("error"):
        analyst_body = (f'<h2>Analyst result</h2><p class="sub">No data: the Analyst could not build a working '
                        f'query in {state.get("attempts")} attempts.</p><div class="err">{esc(state["error"][:400])}</div>')
    else:
        analyst_body = (f'<h2>Analyst result</h2><p class="sub">{len(main_df)} rows returned from Snowflake</p>'
                        f'{table_html(main_df, {c: main_fmt for c in main_df.columns})}'
                        f'<details><summary>Analyst SQL</summary><pre>{esc(state.get("sql", ""))}</pre></details>')

    buttons = '<button class="agent-btn active" onclick="showAgent(\'all\', this)">All agents</button>' + "".join(
        f'<button class="agent-btn" onclick="showAgent(\'{key}\', this)"><span class="dot" style="background:{colour}">'
        f'</span>{name}</button>'
        for key, (name, colour) in AGENTS.items()
    )
    sv = os.getenv("SEMANTIC_VIEW", "")
    title = bi.get("title") or state.get("question", "Dashboard")

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style><script>{JS}</script></head>
<body>
<div class="wrap">
<header>
  <div><h1>{esc(title)}</h1><p class="question">{esc(state.get("question", ""))}</p></div>
  <div>
    <div class="toolbar">{buttons}
      <label class="switch"><input type="checkbox" id="reasoning-toggle" onchange="toggleReasoning(this)">
      <span class="track"></span>Show reasoning</label></div>
    <div class="meta"><span class="src">Snowflake {esc(sv.split(".")[-1] if sv else "star schema")}</span>
    &nbsp; Generated {datetime.now().strftime("%d %b %Y, %H:%M")}</div>
  </div>
</header>
{card("bi", '<h2>Only a basic chart this time</h2><p class="sub">The BI agent could not produce a dashboard plan. '
            'Ask the question again; turn on Show reasoning to see why.</p>', "wide") if bi and not bi.get("plan_ok", True) else ""}
<div class="grid-kpi" data-agent="bi">{kpis}</div>
{card("summary", md_to_html(state.get("summary", "")), "summary")}
<div class="grid-charts">
{"".join(chart_cards)}
{card("analyst", analyst_body, "wide")}
{analyst_reasoning(state)}
{bi_reasoning(bi)}
{summary_reasoning(bi, state)}
</div>
<footer>LangGraph pipeline on Snowflake: Analyst agent (SQL) -> BI agent (dashboard) -> Summary agent (narrative).</footer>
</div></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path