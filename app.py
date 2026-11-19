"""Demo UI: streamlit run app.py

- Type a question and press Enter (or click an example)
- Watch the agents work (animated), optional live reasoning
- Every run is saved; reopen any past dashboard from the sidebar
"""
import html
import os
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from agents import graph, list_runs

st.set_page_config(page_title="Snowflake BI Agents", page_icon=":material/insights:", layout="wide")

EXAMPLES = [
    "Who are the top 10 customers by net revenue in 1997, and which segment and nation are they from?",
    "Compare 1997 vs 1996 net revenue by customer region",
    "How has the order status mix changed by year from 1992 to 1998?",
    "Which ship mode has the highest late delivery rate, and how has it changed over time?",
    "Top 10 brands by net revenue in 1997",
    "How has the return rate changed by order year, and which regions return the most?",
]
STEPS = [
    ("analyst", "Analyst agent", "Reading the semantic layer and writing SQL"),
    ("bi", "BI agent", "Planning KPIs and charts, running queries on Snowflake"),
    ("summary", "Summary agent", "Writing the story and rendering the dashboard"),
]

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
st.markdown(
    """
<style>
.block-container { padding-top: 2.2rem; max-width: 1320px; }
[data-testid="stSidebar"] { background: #f4f3ef; }
.hero h1 { font-size: 2.5rem; font-weight: 750; letter-spacing: -0.02em; margin: 0 0 .2rem;
  background: linear-gradient(90deg, #0b0b0b 0%, #2a78d6 45%, #6250d6 70%, #1baf7a 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.hero p { color: #52514e; margin: 0 0 1.2rem; font-size: 1.02rem; }
.flow { display: inline-flex; gap: .4rem; align-items: center; flex-wrap: wrap; margin-bottom: 1.2rem; }
.chip { font-size: .78rem; padding: .25rem .65rem; border-radius: 999px; background: #fff;
  border: 1px solid rgba(11,11,11,.10); color: #52514e; display: inline-flex; gap: .35rem; align-items: center; }
.chip i { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.arrow { color: #898781; font-size: .8rem; }
.viewing { font-size: .85rem; color: #52514e; margin: .4rem 0 .6rem; }
.viewing b { color: #0b0b0b; }

/* ---------- AI working animation ---------- */
.ai-wrap { position: relative; border-radius: 18px; padding: 2px; margin: .6rem 0 1rem;
  background: linear-gradient(90deg, #2a78d6, #6250d6, #e87ba4, #1baf7a, #2a78d6, #6250d6);
  background-size: 300% 100%; animation: flow 3s linear infinite;
  box-shadow: 0 6px 28px rgba(98,80,214,.18); }
.ai-wrap.done { animation: none; background: #bfe0bf; box-shadow: none; }
.ai-inner { position: relative; background: #fcfcfb; border-radius: 16px; padding: 22px 24px;
  display: flex; gap: 22px; align-items: center; overflow: hidden; }
.done .orb { animation: none; background: radial-gradient(circle at 30% 30%, #fff 0%, #bfe0bf 35%, #1baf7a 100%);
  box-shadow: none; } .done .orb::after { display: none; } .done .ai-title { animation: none; color: #006300; background: none; }
.orb { width: 64px; height: 64px; flex: 0 0 64px; border-radius: 50%; position: relative;
  background: radial-gradient(circle at 30% 30%, #ffffff 0%, #9ec5f4 25%, #6250d6 60%, #2a78d6 100%);
  box-shadow: 0 0 24px rgba(98,80,214,.45), 0 0 48px rgba(42,120,214,.25);
  animation: pulse 1.6s ease-in-out infinite; }
.orb::after { content: ""; position: absolute; inset: -8px; border-radius: 50%;
  border: 2px solid rgba(98,80,214,.35); border-top-color: transparent; animation: spin 1.2s linear infinite; }
.ai-title { font-size: 1.15rem; font-weight: 650; margin-bottom: 2px;
  background: linear-gradient(90deg, #0b0b0b 0%, #0b0b0b 35%, #9085e9 50%, #0b0b0b 65%, #0b0b0b 100%);
  background-size: 250% 100%; -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: shimmer 2.2s linear infinite; }
.ai-sub { color: #52514e; font-size: .9rem; }
.dots span { display: inline-block; width: 5px; height: 5px; margin-left: 3px; border-radius: 50%;
  background: #6250d6; animation: blink 1.2s infinite both; }
.dots span:nth-child(2) { animation-delay: .2s; } .dots span:nth-child(3) { animation-delay: .4s; }
.steps { display: flex; gap: .5rem; margin-top: 12px; flex-wrap: wrap; }
.step { font-size: .78rem; padding: .3rem .7rem; border-radius: 999px; border: 1px solid rgba(11,11,11,.12);
  color: #898781; background: #fff; display: inline-flex; gap: .4rem; align-items: center; }
.step.active { color: #fff; border-color: transparent; background: linear-gradient(90deg, #2a78d6, #6250d6);
  box-shadow: 0 0 14px rgba(98,80,214,.45); }
.step.done { color: #006300; border-color: #bfe0bf; background: #eef7ee; }
.bar { height: 3px; border-radius: 3px; margin-top: 14px; background: #e1e0d9; overflow: hidden; }
.bar::after { content: ""; display: block; height: 100%; width: 35%; border-radius: 3px;
  background: linear-gradient(90deg, transparent, #6250d6, #2a78d6, transparent); animation: slide 1.4s ease-in-out infinite; }
.sparkles { position: absolute; inset: 0; pointer-events: none; }
.sparkles b { position: absolute; width: 4px; height: 4px; border-radius: 50%; background: #9085e9; opacity: 0;
  animation: float 2.6s ease-in-out infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes flow { from { background-position: 0% 0; } to { background-position: 300% 0; } }
@keyframes pulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.08); } }
@keyframes shimmer { from { background-position: 100% 0; } to { background-position: -150% 0; } }
@keyframes blink { 0%,80%,100% { opacity: .2; } 40% { opacity: 1; } }
@keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(300%); } }
@keyframes float { 0% { opacity: 0; transform: translateY(10px); } 30% { opacity: .9; }
  100% { opacity: 0; transform: translateY(-30px); } }
</style>
""",
    unsafe_allow_html=True,
)


def ai_card(stage: int, detail: str = "") -> str:
    """Animated 'AI is working' card. stage = index of the running agent (3 = finished)."""
    if stage >= len(STEPS):
        title, sub = "Dashboard ready", detail or "All three agents finished"
    else:
        title, sub = f"{STEPS[stage][1]} is working", detail or STEPS[stage][2]
    chips = []
    for i, (_, name, _) in enumerate(STEPS):
        cls = "done" if i < stage else ("active" if i == stage else "")
        mark = "&#10003;" if i < stage else ("&#9679;" if i == stage else "&#9675;")
        chips.append(f'<span class="step {cls}">{mark} {name}</span>')
    sparkles = "".join(
        f'<b style="left:{(i * 37) % 100}%; top:{30 + (i * 23) % 60}%; animation-delay:{(i % 7) * 0.35}s"></b>'
        for i in range(14)
    )
    dots = '<span class="dots"><span></span><span></span><span></span></span>' if stage < len(STEPS) else ""
    done = " done" if stage >= len(STEPS) else ""
    return (
        f'<div class="ai-wrap{done}"><div class="ai-inner"><div class="sparkles">{sparkles}</div>'
        f'<div class="orb"></div><div style="flex:1">'
        f'<div class="ai-title">{html.escape(title)}{dots}</div>'
        f'<div class="ai-sub">{html.escape(sub)}</div>'
        f'<div class="steps">{"".join(chips)}</div>'
        f'{"<div class=bar></div>" if stage < len(STEPS) else ""}</div></div></div>'
    )


# ---------------------------------------------------------------------------
# State and callbacks
# ---------------------------------------------------------------------------
ss = st.session_state
ss.setdefault("question_input", EXAMPLES[0])
ss.setdefault("pending", None)
ss.setdefault("view_run", None)


def use_example(q: str):
    ss.question_input = q
    ss.pending = q


def open_run(run: dict):
    ss.view_run = run["run_id"]
    ss.question_input = run["question"]
    ss.pending = None


# ---------------------------------------------------------------------------
# Sidebar: settings now, history filled in at the end (so new runs show up)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Settings")
    show_reasoning = st.toggle("Show agent reasoning", value=False,
                               help="Show each agent's SQL, plan and repaired queries while it works, "
                                    "and open dashboards with the reasoning panels visible.")
    st.markdown("### History")
    history_box = st.container()


def draw_history(runs: list):
    with history_box:
        if not runs:
            st.caption("Your questions will appear here.")
        for r in runs[:40]:
            when = datetime.fromisoformat(r["created"]).strftime("%d %b, %H:%M") if r.get("created") else ""
            label = r["question"][:58] + ("..." if len(r["question"]) > 58 else "")
            active = r["run_id"] == ss.view_run
            st.button(label, key=f"run_{r['run_id']}", help=f"{when}  |  {r['question']}",
                      on_click=open_run, args=(r,), width="stretch", type="primary" if active else "secondary")
        if runs:
            st.caption(f"{len(runs)} saved dashboards in output/runs")


# ---------------------------------------------------------------------------
# Header + question box
# ---------------------------------------------------------------------------
st.markdown(
    '<div class="hero"><h1>Ask a question, get a dashboard</h1>'
    "<p>Three AI agents query your Snowflake semantic layer and build the dashboard for you.</p></div>"
    '<div class="flow"><span class="chip"><i style="background:#2a78d6"></i>Analyst agent writes SQL</span>'
    '<span class="arrow">&rarr;</span><span class="chip"><i style="background:#6250d6"></i>BI agent builds tiles</span>'
    '<span class="arrow">&rarr;</span><span class="chip"><i style="background:#1baf7a"></i>Summary agent tells the story</span>'
    '<span class="arrow">&middot;</span><span class="chip">Snowflake SALES_SV</span></div>',
    unsafe_allow_html=True,
)

with st.form("ask", border=False):
    c1, c2 = st.columns([6, 1], vertical_alignment="bottom")
    question = c1.text_input("Business question", key="question_input",
                             placeholder="e.g. Compare 1997 vs 1996 net revenue by region", label_visibility="collapsed")
    submitted = c2.form_submit_button("Build dashboard", type="primary", width="stretch")

st.caption("Try one:")
cols = st.columns(3)
for i, ex in enumerate(EXAMPLES):
    cols[i % 3].button(ex if len(ex) <= 70 else ex[:67] + "...", key=f"ex{i}", help=ex,
                       on_click=use_example, args=(ex,), width="stretch")

to_run = ss.pending or (question.strip() if submitted and question.strip() else None)
ss.pending = None

# ---------------------------------------------------------------------------
# Run the agents with the animation (and optional live reasoning)
# ---------------------------------------------------------------------------
if to_run:
    anim = st.empty()
    anim.markdown(ai_card(0), unsafe_allow_html=True)
    live = st.container() if show_reasoning else None
    state = {"question": to_run, "attempts": 0}
    stage = 0
    try:
        for update in graph.stream(state, stream_mode="updates"):
            for node, out in update.items():
                state.update(out)
                if node == "analyst_agent":
                    if out.get("error"):
                        detail = f"Attempt {out.get('attempts')} hit an error, rewriting the SQL"
                        stage = 0 if out.get("attempts", 0) < 3 else 2
                    else:
                        detail = f"Got {len(out.get('data') or [])} rows. Planning KPIs and charts"
                        stage = 1
                    if live:
                        with live.expander(f"Analyst agent - attempt {out.get('attempts')}", expanded=True):
                            st.code(out.get("sql", ""), language="sql", wrap_lines=True)
                            if out.get("error"):
                                st.error(out["error"][:500])
                            else:
                                st.caption(f"{len(out.get('data') or [])} rows returned")
                elif node == "bi_agent":
                    bi = out.get("bi", {})
                    ok = len([c for c in bi.get("charts", []) if not c.get("error")])
                    detail = (f"{len(bi.get('kpis', []))} KPIs and {ok} charts ready. Writing the story"
                              if bi.get("plan_ok", True) else "Dashboard plan failed, showing a basic chart")
                    stage = 2
                    if live:
                        with live.expander(f"BI agent - plan: {bi.get('title', '')}", expanded=True):
                            rows = [{"tile": k.get("id"), "type": "KPI", "title": k.get("label"),
                                     "status": "skipped" if k.get("error") else "ok"} for k in bi.get("kpis", [])]
                            rows += [{"tile": c.get("id"), "type": c.get("type"), "title": c.get("title"),
                                      "status": "skipped" if c.get("error") else "ok"} for c in bi.get("charts", [])]
                            st.dataframe(rows, hide_index=True, width="stretch")
                            for r in bi.get("repairs", []):
                                st.warning(f"Repaired {r.get('title') or r.get('id')}: {r.get('error', '')[:200]}")
                                st.code(r.get("fixed_sql") or "(no fix)", language="sql", wrap_lines=True)
                else:
                    detail, stage = "Done", 3
                anim.markdown(ai_card(stage, detail), unsafe_allow_html=True)
        ss.view_run = state.get("run_id")
    except Exception as exc:  # show the error instead of a blank page
        anim.empty()
        st.error(f"Something went wrong: {exc}")
        draw_history(list_runs())
        st.stop()

# ---------------------------------------------------------------------------
# Show the selected dashboard (latest run, or one picked from history)
# ---------------------------------------------------------------------------
runs = list_runs()
draw_history(runs)
current = next((r for r in runs if r["run_id"] == ss.view_run), None)
if current and Path(current["dashboard_path"]).exists():
    when = datetime.fromisoformat(current["created"]).strftime("%d %b %Y, %H:%M")
    st.markdown(f'<div class="viewing">Showing: <b>{html.escape(current["question"])}</b> &middot; {when}</div>',
                unsafe_allow_html=True)
    page = Path(current["dashboard_path"]).read_text(encoding="utf-8")
    if show_reasoning:
        page = page.replace("<body>", '<body class="show-reasoning">', 1)
    components.html(page, height=2600, scrolling=True)
    st.download_button("Download dashboard.html", page, file_name=f"{current['run_id']}.html", mime="text/html")
elif not to_run:
    st.info("Ask a question above, or pick one from the examples. Past dashboards appear in the sidebar.")