"""Three-agent LangGraph pipeline on Snowflake.

    question -> Analyst Agent -> BI Agent -> Summary Agent -> report

Analyst Agent : reads the semantic view (metrics, dimensions) and the documented
                star schema, writes read-only SQL (SEMANTIC_VIEW() first, plain
                SQL as fallback), runs it, retries up to 3 times on error.
BI Agent      : plans a dashboard (KPIs with prior-period deltas, 3-5 charts), runs
                its own queries through the semantic view, repairs failed ones.
Summary Agent : writes the narrative from the tiles and renders dashboard.html.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, TypedDict

import pandas as pd
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

load_dotenv()

OUTPUT_DIR = Path("output")
RUNS_DIR = OUTPUT_DIR / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)


def slugify(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n] or "question"


def list_runs() -> list[dict]:
    """Saved runs, newest first (used by the Streamlit history sidebar)."""
    runs = []
    for meta in RUNS_DIR.glob("*/meta.json"):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            m["dashboard_path"] = str(meta.parent / "dashboard.html")
            runs.append(m)
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(runs, key=lambda m: m.get("run_id", ""), reverse=True)
MAX_SQL_ATTEMPTS = 3
ROW_LIMIT = 1000

# ---------------------------------------------------------------------------
# Snowflake session + LLM
# ---------------------------------------------------------------------------

_session = None


def get_session():
    """One Snowpark session, reused by every agent."""
    global _session
    if _session is None:
        from snowflake.snowpark import Session

        cfg = {
            "account": os.environ["SNOWFLAKE_ACCOUNT"],
            "user": os.environ["SNOWFLAKE_USER"],
            "role": os.getenv("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
            "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "AGENT_WH"),
            "database": os.getenv("AGENT_DATABASE", "AGENT_DB"),
            "schema": os.getenv("AGENT_SCHEMA", "ANALYTICS"),
        }
        if os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE"):
            cfg["private_key_file"] = os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"]
        else:
            # password or programmatic access token (PAT)
            cfg["password"] = os.environ["SNOWFLAKE_PASSWORD"]
        _session = Session.builder.configs(cfg).create()
    return _session


_llm = None


def get_llm():
    global _llm
    if _llm is None:
        provider = os.getenv("LLM_PROVIDER", "google").lower()
        if provider == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI

            # reads GOOGLE_API_KEY from .env; default temperature is what
            # Google recommends for current Gemini models
            _llm = ChatGoogleGenerativeAI(
                model=os.getenv("GOOGLE_MODEL", "gemini-flash-latest"),
                max_output_tokens=int(os.getenv("LLM_MAX_TOKENS", "32000")),
            )
        elif provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            _llm = ChatAnthropic(
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
                temperature=0,
                max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8000")),
            )
        else:
            from langchain_snowflake import ChatSnowflake

            _llm = ChatSnowflake(
                model=os.getenv("CORTEX_MODEL", "claude-3-5-sonnet"),
                session=get_session(),
                temperature=0,
                max_tokens=2000,
            )
    return _llm


def to_text(content) -> str:
    """Model replies can be a string or a list of blocks (Gemini returns
    text blocks plus signature blocks). Keep only the text."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def ask(system: str, user: str) -> str:
    resp = get_llm().invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return to_text(resp.content)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_schema_cache: Optional[str] = None
_semantic_cache: Optional[str] = None


def get_schema_text() -> str:
    """Star schema tables and columns, with the data dictionary comments."""
    global _schema_cache
    if _schema_cache is None:
        db = os.getenv("AGENT_DATABASE", "AGENT_DB")
        sch = os.getenv("AGENT_SCHEMA", "ANALYTICS")
        rows = get_session().sql(
            f"""
            SELECT c.table_name, c.column_name, c.data_type, c.comment, t.comment AS table_comment
            FROM {db}.INFORMATION_SCHEMA.COLUMNS c
            JOIN {db}.INFORMATION_SCHEMA.TABLES t
              ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            WHERE c.table_schema = '{sch}'
            ORDER BY c.table_name, c.ordinal_position
            """
        ).collect()
        tables: dict[str, dict] = {}
        for r in rows:
            t = tables.setdefault(r["TABLE_NAME"], {"comment": r["TABLE_COMMENT"], "cols": []})
            col = f'  {r["COLUMN_NAME"]} {r["DATA_TYPE"]}'
            if r["COMMENT"]:
                col += f'  -- {r["COMMENT"]}'
            t["cols"].append(col)
        parts = []
        for name, t in tables.items():
            header = f"{db}.{sch}.{name}" + (f"  -- {t['comment']}" if t["comment"] else "")
            parts.append(header + "\n" + "\n".join(t["cols"]))
        _schema_cache = "\n\n".join(parts)
        if not _schema_cache:
            raise RuntimeError(
                f"No tables found in {db}.{sch}. Run semantic_layer.sql in Snowsight "
                "first, then check AGENT_DATABASE / AGENT_SCHEMA in .env."
            )
    return _schema_cache


def get_semantic_text() -> str:
    """Dimensions and metrics of the semantic view, from DESCRIBE SEMANTIC VIEW.

    Returns "" if SEMANTIC_VIEW is not set or the view does not exist, so the
    Analyst falls back to plain SQL on the star schema.
    """
    global _semantic_cache
    if _semantic_cache is None:
        sv = os.getenv("SEMANTIC_VIEW", "")
        if not sv:
            _semantic_cache = ""
            return _semantic_cache
        try:
            rows = get_session().sql(f"DESCRIBE SEMANTIC VIEW {sv}").collect()
        except Exception:
            _semantic_cache = ""
            return _semantic_cache
        _semantic_cache = format_semantic(sv, [r.as_dict() for r in rows])
    return _semantic_cache


def format_semantic(sv: str, rows: list[dict]) -> str:
    items: dict[tuple, dict] = {}
    for raw in rows:
        r = {k.lower(): v for k, v in raw.items()}
        kind = r.get("object_kind")
        if kind not in ("DIMENSION", "METRIC", "DERIVED_METRIC", "FACT", "RELATIONSHIP"):
            continue
        key = (kind, r.get("parent_entity") or "", r.get("object_name"))
        items.setdefault(key, {})[r.get("property")] = r.get("property_value")

    out = [f"Semantic view: {sv}"]
    for section in ("METRIC", "DERIVED_METRIC", "DIMENSION", "FACT", "RELATIONSHIP"):
        lines = []
        for (kind, parent, name), props in items.items():
            if kind != section or props.get("ACCESS_MODIFIER") == "PRIVATE":
                continue
            ref = f"{parent}.{name}".lower() if parent else name.lower()
            line = f"  {ref}"
            if props.get("EXPRESSION"):
                line += f" = {props['EXPRESSION']}"
            if props.get("COMMENT"):
                line += f"  -- {props['COMMENT']}"
            if props.get("SYNONYMS"):
                line += f"  (synonyms: {props['SYNONYMS']})"
            if kind == "RELATIONSHIP":
                line = f"  {props.get('TABLE')}.{props.get('FOREIGN_KEY')} -> {props.get('REF_TABLE')}.{props.get('REF_KEY')}"
            lines.append(line)
        if lines:
            out.append(f"{section}S:\n" + "\n".join(lines))
    return "\n".join(out)


BLOCKED = re.compile(
    r"\b(insert|update|delete|merge|drop|create|alter|truncate|grant|revoke|call|put|copy)\b",
    re.IGNORECASE,
)


def extract_sql(text: str) -> str:
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    sql = (m.group(1) if m else text).strip().rstrip(";").strip()
    return sql


def check_sql(sql: str) -> Optional[str]:
    """Return an error message if the SQL is not a single read-only query."""
    if not re.match(r"^\s*(select|with)\b", sql, re.IGNORECASE):
        return "Query must start with SELECT or WITH."
    if ";" in sql:
        return "Only one statement is allowed."
    if BLOCKED.search(sql):
        return "Only read-only queries are allowed."
    return None


def extract_json(text: str) -> dict:
    """Parse a JSON object from a model reply: plain, fenced, or cut off mid-way."""
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(.*?)(?:```|$)", text, re.DOTALL | re.IGNORECASE)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find("{")
    if start >= 0:
        candidates.append(text[start:])
    for cand in candidates:
        cand = cand.strip()
        try:
            obj = json.loads(cand[: cand.rfind("}") + 1] if "}" in cand else cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        try:  # repairs truncated / slightly broken JSON
            import json_repair

            obj = json_repair.loads(cand)
            if isinstance(obj, dict) and obj:
                return obj
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------


class State(TypedDict, total=False):
    question: str
    sql: str
    error: str
    attempts: int
    data: list  # rows as records
    columns: list
    bi: dict
    analyst_log: list
    dashboard_path: str
    run_id: str
    summary: str
    report_path: str


# ---------------------------------------------------------------------------
# Agent 1: Analyst
# ---------------------------------------------------------------------------

ANALYST_SYSTEM = """You are a senior data analyst writing Snowflake SQL against a
governed semantic layer: a star schema plus a semantic view.

How to answer:
1. PREFER the semantic view. If the question can be answered with its metrics and
   dimensions, query it like this (names are table_alias.name, lower case):

   SELECT * FROM SEMANTIC_VIEW(
       <semantic_view_name>
       DIMENSIONS customers.customer_region, dates.order_year
       METRICS lines.net_revenue, lines.order_count
       WHERE dates.order_year = 1997
   )
   ORDER BY net_revenue DESC
   LIMIT 10

   - DIMENSIONS before METRICS. WHERE inside the call may only use dimensions/facts.
   - ORDER BY and LIMIT go outside the call, using unqualified column names.
   - Never put FACTS and METRICS in the same call.
2. If the semantic view cannot answer it (e.g. ranking within groups, custom ratios,
   time comparisons), write normal SQL on the star schema tables, fully qualified,
   using the same business definitions as the metrics (e.g. revenue = SUM(NET_AMOUNT)).

Rules:
- One read-only SELECT (CTEs allowed). No DDL or DML. No semicolons.
- Clear upper-case aliases in plain SQL (e.g. NET_REVENUE).
- At most 1000 rows; add ORDER BY and LIMIT where it makes sense.
- Reply with the SQL only, inside a ```sql block."""


def analyst_agent(state: State) -> State:
    attempts = state.get("attempts", 0) + 1
    semantic = get_semantic_text()
    prompt = (
        (f"{semantic}\n\n" if semantic else "No semantic view available; use the star schema.\n\n")
        + f"Star schema:\n{get_schema_text()}\n\nQuestion: {state['question']}"
    )
    if state.get("error"):
        prompt += (
            f"\n\nYour previous SQL failed.\nSQL:\n{state.get('sql', '')}\n"
            f"Error:\n{state['error']}\nFix it."
        )

    sql = extract_sql(ask(ANALYST_SYSTEM, prompt))
    records, cols, err = run_query(sql)
    log = list(state.get("analyst_log") or []) + [
        {"attempt": attempts, "sql": sql, "error": err, "rows": len(records or [])}
    ]
    if err:  # rejected by the safety check or Snowflake error -> retry
        return {"sql": sql, "error": err, "attempts": attempts, "analyst_log": log}
    return {"sql": sql, "error": "", "attempts": attempts, "columns": cols, "data": records,
            "analyst_log": log}


def route_after_analyst(state: State) -> str:
    if not state.get("error"):
        return "bi_agent"
    if state.get("attempts", 0) < MAX_SQL_ATTEMPTS:
        return "analyst_agent"
    return "summary_agent"  # give up, summary explains the failure


# ---------------------------------------------------------------------------
# Shared query runner (used by the Analyst and the BI agent)
# ---------------------------------------------------------------------------


def run_query(sql: str):
    """Run one read-only query. Returns (records, columns, error)."""
    sql = extract_sql(sql)
    problem = check_sql(sql)
    if problem:
        return None, None, problem
    try:
        df = get_session().sql(f"SELECT * FROM ({sql}) LIMIT {ROW_LIMIT}").to_pandas()
    except Exception as exc:
        return None, None, str(exc)[:1500]
    df = df.where(pd.notnull(df), None)
    return json.loads(df.to_json(orient="records", date_format="iso")), list(df.columns), ""


def first_number(records: list):
    if not records:
        return None
    for v in records[0].values():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# Agent 2: BI (plans a dashboard, runs its own queries, repairs failures)
# ---------------------------------------------------------------------------

BI_SYSTEM = """You are a senior BI developer. Design a one-page dashboard that answers the
business question, using the Analyst's result plus extra queries you write against the
same semantic layer. Reply with JSON only, no prose:

{
  "title": "Short dashboard title",
  "kpis": [
    {"id": "k1", "label": "Net revenue (1996)", "sql": "<query>", "prior_sql": "<query or null>",
     "prior_label": "vs 1995", "format": "currency", "higher_is_better": true}
  ],
  "charts": [
    {"id": "c1", "title": "...", "subtitle": "one line on what to look at", "sql": "MAIN",
     "type": "hbar", "x": "MARKET_SEGMENT", "y": "AVG_ORDER_VALUE", "color": null, "format": "currency"}
  ]
}

KPIs (3 or 4):
- Each "sql" returns ONE row; its first numeric column is the value. Use SEMANTIC_VIEW() metrics.
- Add "prior_sql" for the comparison period (usually the previous year) whenever there is a
  time filter, so the tile can show % change. Rates and averages are fine as KPIs.
- higher_is_better is false for things like late_delivery_rate, return_rate, total_discount.

Charts (3 to 5):
- c1 MUST be "sql": "MAIN" (the Analyst's result, columns listed below).
- The others add context the question implies: trend over time, breakdown by another
  dimension, top/bottom N, mix. Do not repeat c1.
- type: "line" or "area" for time on x (sort by time), "hbar" for ranking categories,
  "bar" for few categories or years, "grouped_bar" / "stacked_bar" when "color" is a second
  dimension with <= 6 values, "heatmap" for two dimensions x one metric (x = dim 1,
  y = dim 2, "value" = metric column), "table" for detail lists.
- ONE metric per chart (never two y axes). x, y, color, value are OUTPUT column names in
  upper case, unqualified (e.g. ORDER_YEAR, NET_REVENUE).
- format: currency | number | percent (rates are fractions 0-1) | decimal.

SQL rules:
- Prefer SEMANTIC_VIEW(): DIMENSIONS before METRICS; WHERE inside the call uses dimensions
  only; ORDER BY / LIMIT outside the call. Never FACTS and METRICS in one call.
- Plain SQL on the star schema only when the view cannot do it. Read-only, no semicolons."""

BI_REPAIR_SYSTEM = """These dashboard queries failed on Snowflake. Fix each one using the
semantic layer provided. Reply with JSON only: {"<id>": "<fixed sql>", ...}. Keep the same
output column names where possible. Read-only SELECT, no semicolons."""


def bi_agent(state: State) -> State:
    main_cols = state.get("columns") or []
    main_df = pd.DataFrame(state.get("data", []), columns=main_cols)
    semantic = get_semantic_text()
    prompt = (
        f"{semantic}\n\nStar schema:\n{get_schema_text()}\n\n"
        f"Question: {state['question']}\n\nAnalyst SQL (this is MAIN):\n{state['sql']}\n\n"
        f"MAIN columns: {', '.join(main_cols)}\n"
        f"MAIN first rows:\n{main_df.head(15).to_markdown(index=False)}"
    )
    raw = ask(BI_SYSTEM, prompt)
    plan = extract_json(raw)
    plan_attempts = 1
    if not (plan.get("kpis") or plan.get("charts")):
        plan_attempts = 2
        raw = ask(BI_SYSTEM, prompt + "\n\nIMPORTANT: your previous reply could not be parsed. Reply with "
                  "ONLY the JSON object, no explanation. Keep SQL short (3 KPIs, 4 charts).")
        plan = extract_json(raw)
    plan_ok = bool(plan.get("kpis") or plan.get("charts"))
    kpis = [k for k in (plan.get("kpis") or []) if isinstance(k, dict)]
    charts = [c for c in (plan.get("charts") or []) if isinstance(c, dict)]
    if not charts or str(charts[0].get("sql", "")).upper() != "MAIN":
        charts.insert(0, {"id": "c1", "title": state["question"], "sql": "MAIN", "type": "bar"})

    # 1. run everything once
    jobs = []  # (item, key) where key is "sql" or "prior_sql"
    for i, k in enumerate(kpis):
        k.setdefault("id", f"k{i + 1}")
        jobs.append((k, "sql"))
        if k.get("prior_sql"):
            jobs.append((k, "prior_sql"))
    for i, c in enumerate(charts):
        c.setdefault("id", f"c{i + 1}")
        if str(c.get("sql", "")).upper() == "MAIN":
            c.update(data=state.get("data", []), columns=main_cols, sql_run=state["sql"], error="")
        else:
            jobs.append((c, "sql"))

    def execute(item, key):
        item.setdefault("_res", {})[key] = run_query(item.get(key) or "")

    for item, key in jobs:
        execute(item, key)

    # 2. one repair pass for anything that failed
    repairs = []
    failed = [(item, key) for item, key in jobs if item["_res"][key][2]]
    if failed:
        listing = "\n\n".join(
            f"id: {item['id']}{'_prior' if key == 'prior_sql' else ''}\nSQL:\n{item.get(key)}\n"
            f"Error: {item['_res'][key][2][:600]}"
            for item, key in failed
        )
        fixes = extract_json(ask(BI_REPAIR_SYSTEM, f"{semantic}\n\nStar schema:\n{get_schema_text()}\n\n{listing}"))
        for item, key in failed:
            fid = item["id"] + ("_prior" if key == "prior_sql" else "")
            entry = {"id": fid, "title": item.get("title") or item.get("label"),
                     "original_sql": item.get(key), "error": item["_res"][key][2][:400]}
            if fixes.get(fid):
                item[key] = fixes[fid]
                execute(item, key)
                entry.update(fixed_sql=extract_sql(fixes[fid]), fixed_error=item["_res"][key][2][:400])
            else:
                entry.update(fixed_sql="", fixed_error="no fix returned")
            repairs.append(entry)

    # 3. turn results into tiles
    for k in kpis:
        res = k.pop("_res", {})
        records, _, err = res.get("sql", (None, None, "not run"))
        k["value"], k["error"] = first_number(records), err
        if "prior_sql" in res:
            precords, _, perr = res["prior_sql"]
            k["prior"] = None if perr else first_number(precords)
    for c in charts:
        res = c.pop("_res", {})
        if "sql" in res:
            records, cols, err = res["sql"]
            c.update(data=records or [], columns=cols or [], error=err, sql_run=extract_sql(c.get("sql") or ""))

    return {"bi": {"title": plan.get("title") or "", "kpis": kpis, "charts": charts,
                   "plan_ok": plan_ok, "plan_attempts": plan_attempts,
                   "plan_raw_tail": "" if plan_ok else (raw or "")[-1500:],
                   "repairs": repairs, "queries_run": len(jobs) + len([r for r in repairs if r.get("fixed_sql")])}}


# ---------------------------------------------------------------------------
# Agent 3: Summary
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM = """You write the narrative for a BI dashboard read by business stakeholders.
Plain English, under 170 words, using this structure:

**Answer:** 1-2 sentences that directly answer the question.

**What the dashboard shows**
- 3 or 4 bullets, each one finding with a number from the tiles

**What to do next**
- 1 or 2 practical actions

Formatting: money as $ with thousands separators or K/M/B (e.g. $145,634 or $2.1B);
rates as % with one decimal. Only use numbers present in the input; never invent or
average numbers yourself. If differences are tiny, say so plainly instead of
over-interpreting. If the analysis failed, say what failed and how to rephrase."""


def summary_agent(state: State) -> State:
    from dashboard import render_dashboard

    if state.get("error"):
        prompt = (
            f"Question: {state['question']}\n"
            f"The analysis failed after {state.get('attempts')} attempts.\nLast error: {state['error']}"
        )
    else:
        bi = state.get("bi") or {}
        kpi_lines = [
            f"- {k.get('label')}: value={k.get('value')} prior={k.get('prior')} "
            f"({k.get('prior_label', '')}) format={k.get('format')}"
            for k in bi.get("kpis", []) if not k.get("error")
        ]
        chart_lines = []
        for c in bi.get("charts", []):
            if c.get("error"):
                continue
            df = pd.DataFrame(c.get("data") or [], columns=c.get("columns"))
            chart_lines.append(f"### {c.get('title')} (format={c.get('format')})\n{df.head(12).to_markdown(index=False)}")
        prompt = (
            f"Question: {state['question']}\n\nKPI tiles:\n" + "\n".join(kpi_lines)
            + "\n\nChart data:\n" + "\n\n".join(chart_lines)
        )
    summary = ask(SUMMARY_SYSTEM, prompt)
    state_for_render = {**state, "summary": summary}

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + slugify(state["question"])
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    dashboard_path = str(run_dir / "dashboard.html")
    render_dashboard(state_for_render, dashboard_path)
    shutil.copyfile(dashboard_path, OUTPUT_DIR / "dashboard.html")  # latest run
    bi = state.get("bi") or {}
    meta = {
        "run_id": run_id, "question": state["question"], "title": bi.get("title") or "",
        "created": datetime.now().isoformat(timespec="seconds"),
        "ok": not state.get("error"), "attempts": state.get("attempts"),
        "kpis": len(bi.get("kpis", [])), "charts": len([c for c in bi.get("charts", []) if not c.get("error")]),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    report = [f"# {state['question']}\n", summary, "\n## Analyst SQL\n", f"```sql\n{state.get('sql', '')}\n```"]
    report_path = OUTPUT_DIR / "report.md"
    report_path.write_text("\n".join(report), encoding="utf-8")
    return {"summary": summary, "report_path": str(report_path), "dashboard_path": dashboard_path,
            "run_id": run_id}


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------


def build_graph():
    g = StateGraph(State)
    g.add_node("analyst_agent", analyst_agent)
    g.add_node("bi_agent", bi_agent)
    g.add_node("summary_agent", summary_agent)
    g.add_edge(START, "analyst_agent")
    g.add_conditional_edges(
        "analyst_agent",
        route_after_analyst,
        {"analyst_agent": "analyst_agent", "bi_agent": "bi_agent", "summary_agent": "summary_agent"},
    )
    g.add_edge("bi_agent", "summary_agent")
    g.add_edge("summary_agent", END)
    return g.compile()


graph = build_graph()


def run(question: str) -> State:
    return graph.invoke({"question": question, "attempts": 0})