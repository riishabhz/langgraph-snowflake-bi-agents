"""CLI: python run.py "Which 10 nations had the highest revenue in 1997?"

Runs Analyst -> BI -> Summary and opens output/dashboard.html in your browser.
Add --no-open to skip opening the browser.
"""
import sys
import webbrowser
from pathlib import Path

from agents import get_schema_text, get_semantic_text, run
from dashboard import compact

args = [a for a in sys.argv[1:] if a != "--no-open"]
question = " ".join(args) or "Which 10 nations had the highest total net revenue in 1997?"

schema = get_schema_text()
semantic = get_semantic_text()
print(f"Star schema loaded: {schema.count(chr(10) + chr(10)) + 1} tables")
print(f"Semantic view loaded: {'yes' if semantic else 'NO (agents will use plain SQL)'}")

state = run(question)

print("\n=== Analyst Agent ===")
print(state.get("sql"))
print(f"(attempts: {state.get('attempts')}, rows: {len(state.get('data') or [])})")
if state.get("error"):
    print("Error:", state["error"])

bi = state.get("bi") or {}
print(f"\n=== BI Agent: {bi.get('title', '')} ===")
if not bi.get("plan_ok", True):
    print(f"  WARNING: plan could not be parsed after {bi.get('plan_attempts')} attempts. End of reply:")
    print("  " + (bi.get("plan_raw_tail") or "")[-400:])
for k in bi.get("kpis", []):
    status = f"ERROR {k['error'][:80]}" if k.get("error") else compact(k.get("value"), k.get("format") or "number")
    prior = f" (prior {compact(k['prior'], k.get('format') or 'number')})" if k.get("prior") is not None else ""
    print(f"  KPI   {k.get('label')}: {status}{prior}")
for c in bi.get("charts", []):
    status = f"ERROR {c['error'][:80]}" if c.get("error") else f"{len(c.get('data') or [])} rows"
    print(f"  Chart [{c.get('type')}] {c.get('title')}: {status}")

print("\n=== Summary Agent ===")
print(state.get("summary"))

path = state.get("dashboard_path")
if path:
    print(f"\nDashboard: {Path(path).resolve()}")
    if "--no-open" not in sys.argv:
        webbrowser.open(Path(path).resolve().as_uri())