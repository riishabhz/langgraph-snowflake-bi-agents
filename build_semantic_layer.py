"""Run semantic_layer.sql from Python, one statement at a time, with progress.

    python build_semantic_layer.py

Stops at the first error and prints which statement failed.
"""
import sys
from io import StringIO

from snowflake.connector.util_text import split_statements

from agents import get_session

sql = open("semantic_layer.sql", encoding="utf-8").read()
statements = [s for s, _ in split_statements(StringIO(sql), remove_comments=True) if s.strip()]

conn = get_session().connection
cur = conn.cursor()

for i, stmt in enumerate(statements, 1):
    first_line = " ".join(stmt.split())[:80]
    print(f"[{i}/{len(statements)}] {first_line} ...", end=" ", flush=True)
    try:
        cur.execute(stmt)
        rows = cur.fetchall() if cur.description else []
        print("ok")
        # print the data quality check results and the final test query
        if stmt.lstrip().upper().startswith(("SELECT 'FACT_PK", "SELECT * FROM SEMANTIC_VIEW")):
            for r in rows[:20]:
                print("     ", r)
    except Exception as exc:
        print("FAILED")
        print(f"\nStatement {i} failed:\n{stmt}\n\nError: {exc}")
        sys.exit(1)

print("\nSemantic layer built.")
