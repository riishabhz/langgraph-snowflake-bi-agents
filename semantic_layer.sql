-- =============================================================================
-- Semantic layer for the agents
--   Layer 1: Kimball star schema in AGENT_DB.ANALYTICS (built from TPC-H sample)
--   Layer 2: Native Snowflake semantic view SALES_SV (joins, metrics, synonyms)
-- Run after setup.sql, top to bottom, in one Snowsight worksheet.
-- =============================================================================

USE ROLE ACCOUNTADMIN;
USE WAREHOUSE AGENT_WH;
USE SCHEMA AGENT_DB.ANALYTICS;

-- -----------------------------------------------------------------------------
-- LAYER 1: STAR SCHEMA
--
--                 DIM_DATE
--                    |
--   DIM_CUSTOMER - FACT_ORDER_LINE - DIM_PRODUCT
--                    |
--               DIM_SUPPLIER
-- -----------------------------------------------------------------------------

-- DIM_DATE: one row per calendar day, 1992-1998 (TPC-H order date range)
CREATE OR REPLACE TABLE DIM_DATE AS
WITH days AS (
    SELECT DATEADD(day, ROW_NUMBER() OVER (ORDER BY SEQ4()) - 1, '1992-01-01'::DATE) AS d
    FROM TABLE(GENERATOR(ROWCOUNT => 2557))
)
SELECT
    TO_NUMBER(TO_CHAR(d, 'YYYYMMDD'))          AS DATE_KEY,
    d                                          AS CALENDAR_DATE,
    YEAR(d)                                    AS CAL_YEAR,
    QUARTER(d)                                 AS CAL_QUARTER,
    YEAR(d) || '-Q' || QUARTER(d)              AS YEAR_QUARTER,
    MONTH(d)                                   AS CAL_MONTH,
    MONTHNAME(d)                               AS MONTH_NAME,
    TO_CHAR(d, 'YYYY-MM')                      AS YEAR_MONTH,
    DAYNAME(d)                                 AS DAY_NAME,
    IFF(DAYOFWEEKISO(d) IN (6, 7), TRUE, FALSE) AS IS_WEEKEND
FROM days;

-- DIM_CUSTOMER: customer with nation and region flattened in (no snowflaking)
CREATE OR REPLACE TABLE DIM_CUSTOMER AS
SELECT
    c.C_CUSTKEY     AS CUSTOMER_KEY,
    c.C_NAME        AS CUSTOMER_NAME,
    c.C_MKTSEGMENT  AS MARKET_SEGMENT,
    c.C_ACCTBAL     AS ACCOUNT_BALANCE,
    n.N_NAME        AS NATION,
    r.R_NAME        AS REGION
FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.CUSTOMER c
JOIN SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.NATION   n ON c.C_NATIONKEY = n.N_NATIONKEY
JOIN SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.REGION   r ON n.N_REGIONKEY = r.R_REGIONKEY;

-- DIM_PRODUCT: TPC-H "part"
CREATE OR REPLACE TABLE DIM_PRODUCT AS
SELECT
    P_PARTKEY                   AS PRODUCT_KEY,
    P_NAME                      AS PRODUCT_NAME,
    P_MFGR                      AS MANUFACTURER,
    P_BRAND                     AS BRAND,
    P_TYPE                      AS PRODUCT_TYPE,
    SPLIT_PART(P_TYPE, ' ', 3)  AS MATERIAL,
    P_SIZE                      AS PRODUCT_SIZE,
    P_CONTAINER                 AS CONTAINER,
    P_RETAILPRICE               AS RETAIL_PRICE
FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.PART;

-- DIM_SUPPLIER
CREATE OR REPLACE TABLE DIM_SUPPLIER AS
SELECT
    s.S_SUPPKEY  AS SUPPLIER_KEY,
    s.S_NAME     AS SUPPLIER_NAME,
    s.S_ACCTBAL  AS ACCOUNT_BALANCE,
    n.N_NAME     AS NATION,
    r.R_NAME     AS REGION
FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.SUPPLIER s
JOIN SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.NATION   n ON s.S_NATIONKEY = n.N_NATIONKEY
JOIN SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.REGION   r ON n.N_REGIONKEY = r.R_REGIONKEY;

-- FACT_ORDER_LINE: grain = one row per order line (~6M rows, ~1 min on XSMALL)
CREATE OR REPLACE TABLE FACT_ORDER_LINE AS
SELECT
    l.L_ORDERKEY                                        AS ORDER_KEY,
    l.L_LINENUMBER                                      AS LINE_NUMBER,
    o.O_CUSTKEY                                         AS CUSTOMER_KEY,
    l.L_PARTKEY                                         AS PRODUCT_KEY,
    l.L_SUPPKEY                                         AS SUPPLIER_KEY,
    TO_NUMBER(TO_CHAR(o.O_ORDERDATE, 'YYYYMMDD'))       AS ORDER_DATE_KEY,
    o.O_ORDERDATE                                       AS ORDER_DATE,
    l.L_SHIPDATE                                        AS SHIP_DATE,
    l.L_COMMITDATE                                      AS COMMIT_DATE,
    l.L_RECEIPTDATE                                     AS RECEIPT_DATE,
    o.O_ORDERSTATUS                                     AS ORDER_STATUS,
    o.O_ORDERPRIORITY                                   AS ORDER_PRIORITY,
    l.L_SHIPMODE                                        AS SHIP_MODE,
    l.L_RETURNFLAG                                      AS RETURN_FLAG,
    l.L_QUANTITY                                        AS QUANTITY,
    l.L_EXTENDEDPRICE                                   AS GROSS_AMOUNT,
    l.L_DISCOUNT                                        AS DISCOUNT_PCT,
    l.L_EXTENDEDPRICE * l.L_DISCOUNT                    AS DISCOUNT_AMOUNT,
    l.L_EXTENDEDPRICE * (1 - l.L_DISCOUNT)              AS NET_AMOUNT,
    l.L_EXTENDEDPRICE * (1 - l.L_DISCOUNT) * l.L_TAX    AS TAX_AMOUNT,
    DATEDIFF(day, o.O_ORDERDATE, l.L_SHIPDATE)          AS DAYS_TO_SHIP,
    IFF(l.L_RECEIPTDATE > l.L_COMMITDATE, 1, 0)         AS IS_LATE,
    IFF(l.L_RETURNFLAG = 'R', 1, 0)                     AS IS_RETURNED
FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.LINEITEM l
JOIN SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS   o ON l.L_ORDERKEY = o.O_ORDERKEY;

-- Keys (informational in Snowflake, but documented for people, BI tools and agents)
ALTER TABLE DIM_DATE        ADD PRIMARY KEY (DATE_KEY);
ALTER TABLE DIM_CUSTOMER    ADD PRIMARY KEY (CUSTOMER_KEY);
ALTER TABLE DIM_PRODUCT     ADD PRIMARY KEY (PRODUCT_KEY);
ALTER TABLE DIM_SUPPLIER    ADD PRIMARY KEY (SUPPLIER_KEY);
ALTER TABLE FACT_ORDER_LINE ADD PRIMARY KEY (ORDER_KEY, LINE_NUMBER);
ALTER TABLE FACT_ORDER_LINE ADD FOREIGN KEY (CUSTOMER_KEY)   REFERENCES DIM_CUSTOMER (CUSTOMER_KEY);
ALTER TABLE FACT_ORDER_LINE ADD FOREIGN KEY (PRODUCT_KEY)    REFERENCES DIM_PRODUCT (PRODUCT_KEY);
ALTER TABLE FACT_ORDER_LINE ADD FOREIGN KEY (SUPPLIER_KEY)   REFERENCES DIM_SUPPLIER (SUPPLIER_KEY);
ALTER TABLE FACT_ORDER_LINE ADD FOREIGN KEY (ORDER_DATE_KEY) REFERENCES DIM_DATE (DATE_KEY);

-- Data dictionary (the Analyst agent reads these comments)
COMMENT ON TABLE FACT_ORDER_LINE IS 'Sales fact. Grain: one row per order line. Join to dims on *_KEY.';
COMMENT ON TABLE DIM_CUSTOMER    IS 'Customers with nation and region.';
COMMENT ON TABLE DIM_PRODUCT     IS 'Products (TPC-H parts).';
COMMENT ON TABLE DIM_SUPPLIER    IS 'Suppliers with nation and region.';
COMMENT ON TABLE DIM_DATE        IS 'Calendar. FACT_ORDER_LINE.ORDER_DATE_KEY joins to DATE_KEY.';

COMMENT ON COLUMN FACT_ORDER_LINE.GROSS_AMOUNT    IS 'Line value before discount (quantity x unit price).';
COMMENT ON COLUMN FACT_ORDER_LINE.DISCOUNT_PCT    IS 'Discount as a fraction, 0.05 = 5%.';
COMMENT ON COLUMN FACT_ORDER_LINE.DISCOUNT_AMOUNT IS 'GROSS_AMOUNT x DISCOUNT_PCT.';
COMMENT ON COLUMN FACT_ORDER_LINE.NET_AMOUNT      IS 'Revenue after discount, before tax. Use this for revenue.';
COMMENT ON COLUMN FACT_ORDER_LINE.TAX_AMOUNT      IS 'Tax charged on NET_AMOUNT.';
COMMENT ON COLUMN FACT_ORDER_LINE.ORDER_STATUS    IS 'F = fulfilled, O = open, P = partially fulfilled.';
COMMENT ON COLUMN FACT_ORDER_LINE.RETURN_FLAG     IS 'R = returned, A = accepted, N = not yet received.';
COMMENT ON COLUMN FACT_ORDER_LINE.DAYS_TO_SHIP    IS 'Days from order date to ship date.';
COMMENT ON COLUMN FACT_ORDER_LINE.IS_LATE         IS '1 if received after the committed date, else 0.';
COMMENT ON COLUMN FACT_ORDER_LINE.IS_RETURNED     IS '1 if RETURN_FLAG = R, else 0.';
COMMENT ON COLUMN DIM_CUSTOMER.MARKET_SEGMENT     IS 'AUTOMOBILE, BUILDING, FURNITURE, HOUSEHOLD, MACHINERY.';
COMMENT ON COLUMN DIM_PRODUCT.MATERIAL            IS 'Last word of PRODUCT_TYPE: BRASS, COPPER, NICKEL, STEEL, TIN.';

-- -----------------------------------------------------------------------------
-- DATA QUALITY CHECKS: every query should return 0
-- -----------------------------------------------------------------------------
SELECT 'fact_pk_duplicates' AS check_name, COUNT(*) AS failures FROM (
    SELECT ORDER_KEY, LINE_NUMBER FROM FACT_ORDER_LINE GROUP BY 1, 2 HAVING COUNT(*) > 1)
UNION ALL
SELECT 'orphan_customer', COUNT(*) FROM FACT_ORDER_LINE f
    LEFT JOIN DIM_CUSTOMER d ON f.CUSTOMER_KEY = d.CUSTOMER_KEY WHERE d.CUSTOMER_KEY IS NULL
UNION ALL
SELECT 'orphan_product', COUNT(*) FROM FACT_ORDER_LINE f
    LEFT JOIN DIM_PRODUCT d ON f.PRODUCT_KEY = d.PRODUCT_KEY WHERE d.PRODUCT_KEY IS NULL
UNION ALL
SELECT 'orphan_supplier', COUNT(*) FROM FACT_ORDER_LINE f
    LEFT JOIN DIM_SUPPLIER d ON f.SUPPLIER_KEY = d.SUPPLIER_KEY WHERE d.SUPPLIER_KEY IS NULL
UNION ALL
SELECT 'orphan_date', COUNT(*) FROM FACT_ORDER_LINE f
    LEFT JOIN DIM_DATE d ON f.ORDER_DATE_KEY = d.DATE_KEY WHERE d.DATE_KEY IS NULL
UNION ALL
SELECT 'null_net_amount', COUNT(*) FROM FACT_ORDER_LINE WHERE NET_AMOUNT IS NULL
UNION ALL
SELECT 'row_count_vs_source',
    ABS((SELECT COUNT(*) FROM FACT_ORDER_LINE)
      - (SELECT COUNT(*) FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.LINEITEM));

-- -----------------------------------------------------------------------------
-- LAYER 2: SEMANTIC VIEW (business definitions live here, once)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE SEMANTIC VIEW AGENT_DB.ANALYTICS.SALES_SV
  TABLES (
    lines AS AGENT_DB.ANALYTICS.FACT_ORDER_LINE
      PRIMARY KEY (ORDER_KEY, LINE_NUMBER)
      WITH SYNONYMS = ('sales', 'order lines', 'transactions')
      COMMENT = 'Sales fact, one row per order line',
    customers AS AGENT_DB.ANALYTICS.DIM_CUSTOMER
      PRIMARY KEY (CUSTOMER_KEY)
      WITH SYNONYMS = ('clients', 'buyers', 'accounts')
      COMMENT = 'Customers',
    products AS AGENT_DB.ANALYTICS.DIM_PRODUCT
      PRIMARY KEY (PRODUCT_KEY)
      WITH SYNONYMS = ('parts', 'items', 'skus')
      COMMENT = 'Products',
    suppliers AS AGENT_DB.ANALYTICS.DIM_SUPPLIER
      PRIMARY KEY (SUPPLIER_KEY)
      WITH SYNONYMS = ('vendors')
      COMMENT = 'Suppliers',
    dates AS AGENT_DB.ANALYTICS.DIM_DATE
      PRIMARY KEY (DATE_KEY)
      COMMENT = 'Calendar on order date'
  )
  RELATIONSHIPS (
    lines_to_customers  AS lines (CUSTOMER_KEY)   REFERENCES customers,
    lines_to_products   AS lines (PRODUCT_KEY)    REFERENCES products,
    lines_to_suppliers  AS lines (SUPPLIER_KEY)   REFERENCES suppliers,
    lines_to_order_date AS lines (ORDER_DATE_KEY) REFERENCES dates
  )
  FACTS (
    lines.net_amount      AS lines.NET_AMOUNT      COMMENT = 'Line revenue after discount',
    lines.gross_amount    AS lines.GROSS_AMOUNT    COMMENT = 'Line value before discount',
    lines.discount_amount AS lines.DISCOUNT_AMOUNT COMMENT = 'Discount given on the line',
    lines.quantity        AS lines.QUANTITY        COMMENT = 'Units on the line',
    lines.days_to_ship    AS lines.DAYS_TO_SHIP    COMMENT = 'Days from order to ship'
  )
  DIMENSIONS (
    lines.order_date        AS lines.ORDER_DATE        COMMENT = 'Date the order was placed',
    lines.ship_date         AS lines.SHIP_DATE,
    lines.order_status      AS lines.ORDER_STATUS      COMMENT = 'F fulfilled, O open, P partial',
    lines.order_priority    AS lines.ORDER_PRIORITY,
    lines.ship_mode         AS lines.SHIP_MODE         WITH SYNONYMS = ('shipping method', 'delivery mode'),
    lines.return_flag       AS lines.RETURN_FLAG       COMMENT = 'R returned, A accepted, N not received',
    customers.customer_name   AS customers.CUSTOMER_NAME,
    customers.market_segment  AS customers.MARKET_SEGMENT  WITH SYNONYMS = ('segment', 'industry'),
    customers.customer_nation AS customers.NATION          WITH SYNONYMS = ('country', 'customer country'),
    customers.customer_region AS customers.REGION          WITH SYNONYMS = ('customer region', 'geography'),
    products.product_name   AS products.PRODUCT_NAME,
    products.brand          AS products.BRAND,
    products.manufacturer   AS products.MANUFACTURER,
    products.product_type   AS products.PRODUCT_TYPE,
    products.material       AS products.MATERIAL,
    products.container      AS products.CONTAINER,
    suppliers.supplier_name   AS suppliers.SUPPLIER_NAME,
    suppliers.supplier_nation AS suppliers.NATION     WITH SYNONYMS = ('supplier country'),
    suppliers.supplier_region AS suppliers.REGION,
    dates.order_year        AS dates.CAL_YEAR         WITH SYNONYMS = ('year'),
    dates.order_quarter     AS dates.YEAR_QUARTER     WITH SYNONYMS = ('quarter'),
    dates.order_month       AS dates.YEAR_MONTH       WITH SYNONYMS = ('month'),
    dates.order_month_name  AS dates.MONTH_NAME,
    dates.order_day_name    AS dates.DAY_NAME,
    dates.is_weekend        AS dates.IS_WEEKEND
  )
  METRICS (
    lines.net_revenue        AS SUM(lines.NET_AMOUNT)
      WITH SYNONYMS = ('revenue', 'sales', 'turnover') COMMENT = 'Revenue after discount, before tax',
    lines.gross_revenue      AS SUM(lines.GROSS_AMOUNT)    COMMENT = 'Revenue before discount',
    lines.total_discount     AS SUM(lines.DISCOUNT_AMOUNT) COMMENT = 'Total discount given',
    lines.total_tax          AS SUM(lines.TAX_AMOUNT),
    lines.units_sold         AS SUM(lines.QUANTITY)        WITH SYNONYMS = ('volume', 'quantity sold'),
    lines.order_count        AS COUNT(DISTINCT lines.ORDER_KEY) WITH SYNONYMS = ('number of orders'),
    lines.active_customers   AS COUNT(DISTINCT lines.CUSTOMER_KEY) COMMENT = 'Customers who ordered in the period',
    lines.avg_order_value    AS SUM(lines.NET_AMOUNT) / NULLIF(COUNT(DISTINCT lines.ORDER_KEY), 0)
      WITH SYNONYMS = ('aov', 'basket size') COMMENT = 'Net revenue per order',
    lines.avg_discount_pct   AS AVG(lines.DISCOUNT_PCT)    COMMENT = 'Average discount fraction',
    lines.late_delivery_rate AS AVG(lines.IS_LATE)         COMMENT = 'Share of lines received after commit date (0-1)',
    lines.return_rate        AS AVG(lines.IS_RETURNED)     COMMENT = 'Share of lines returned (0-1)',
    lines.avg_days_to_ship   AS AVG(lines.DAYS_TO_SHIP),
    customers.customer_count AS COUNT(customers.CUSTOMER_KEY) COMMENT = 'All customers on file',
    products.product_count   AS COUNT(products.PRODUCT_KEY)
  )
  COMMENT = 'Governed sales model for the Analyst, BI and Summary agents';

-- -----------------------------------------------------------------------------
-- TEST THE SEMANTIC VIEW
-- -----------------------------------------------------------------------------
DESCRIBE SEMANTIC VIEW AGENT_DB.ANALYTICS.SALES_SV;

SELECT * FROM SEMANTIC_VIEW(
    AGENT_DB.ANALYTICS.SALES_SV
    DIMENSIONS customers.customer_region, dates.order_year
    METRICS lines.net_revenue, lines.order_count, lines.avg_order_value
    WHERE dates.order_year = 1997
)
ORDER BY net_revenue DESC;
