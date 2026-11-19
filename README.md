# Snowflake multi-agent analyst (LangGraph)

Analyst Agent -> BI Agent -> Summary Agent, running on Snowflake Cortex.

1. Run setup.sql in Snowsight (warehouse, database, cross-region, model check)
1b. Run semantic_layer.sql (star schema, data quality checks, semantic view SALES_SV)
2. python -m venv .venv, activate it, then pip install -r requirements.txt
3. python gen_keys.py, then run the ALTER USER line it prints in Snowsight
4. Copy .env.example to .env and paste a free Gemini key from https://aistudio.google.com/apikey into GOOGLE_API_KEY
5. python run.py "Which 10 nations had the highest total order revenue in 1997?"
   It opens output/dashboard.html in your browser (interactive Plotly dashboard)
6. streamlit run app.py for the demo UI

Add .env, rsa_key.p8 and output/ to .gitignore before pushing.
