-- Run this once in a Snowsight SQL worksheet (Projects > Workspaces > + SQL file)
-- as ACCOUNTADMIN.

USE ROLE ACCOUNTADMIN;

-- 1. Small warehouse that suspends itself after 60s so the trial credits last
CREATE WAREHOUSE IF NOT EXISTS AGENT_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;

-- 2. Database for anything the agents write (and later, your Airflow pipeline)
CREATE DATABASE IF NOT EXISTS AGENT_DB;
CREATE SCHEMA IF NOT EXISTS AGENT_DB.ANALYTICS;

-- 3. (Skip on a trial account: Cortex AI functions are blocked on trials.
--    The agents use Gemini via LLM_PROVIDER=google instead.)
-- ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';

-- 4. Check the sample data is there
SELECT COUNT(*) FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS;   -- ~1.5M rows

-- 5. Only on a paid account: find a Cortex model that works and set
--    LLM_PROVIDER=cortex and CORTEX_MODEL in .env.
-- SELECT AI_COMPLETE('claude-3-5-sonnet', 'Say hi in 3 words');

-- 6. Key-pair login for Python (MFA blocks plain passwords from scripts).
--    Run gen_keys.py first, then paste the contents of rsa_key.pub
--    (without the BEGIN/END lines) below.
-- ALTER USER RISHABH SET RSA_PUBLIC_KEY = 'MIIBIjANBgkqh...';
-- DESC USER RISHABH;   -- RSA_PUBLIC_KEY_FP should now be filled in
