-- Read-only verification of the deterministic seed; monetary values are EUR.
-- All timestamp literals represent UTC.

-- Expected: five base tables, each with its intended engine and matches = 1.
SELECT expected.table_name, actual.TABLE_TYPE AS table_type,
       expected.expected_engine, actual.ENGINE AS actual_engine,
       CASE WHEN actual.TABLE_TYPE = 'BASE TABLE'
                 AND UPPER(actual.ENGINE) = expected.expected_engine
            THEN 1 ELSE 0 END AS matches
FROM (
    SELECT 'products' AS table_name, 'INNODB' AS expected_engine
    UNION ALL SELECT 'campaigns', 'INNODB'
    UNION ALL SELECT 'orders', 'INNODB'
    UNION ALL SELECT 'order_items', 'INNODB'
    UNION ALL SELECT 'activity_events', 'DUCKDB'
) AS expected
LEFT JOIN information_schema.TABLES AS actual
  ON actual.TABLE_SCHEMA = 'commerce_analytics'
 AND actual.TABLE_NAME = expected.table_name
ORDER BY expected.table_name;

SELECT COUNT(*) AS actual_table_count, 5 AS expected_table_count
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'commerce_analytics' AND TABLE_TYPE = 'BASE TABLE';

SELECT 'products' AS table_name, COUNT(*) AS actual_rows, 3 AS expected_rows
FROM commerce_analytics.products
UNION ALL SELECT 'campaigns', COUNT(*), 2 FROM commerce_analytics.campaigns
UNION ALL SELECT 'orders', COUNT(*), 3 FROM commerce_analytics.orders
UNION ALL SELECT 'order_items', COUNT(*), 4 FROM commerce_analytics.order_items
UNION ALL SELECT 'activity_events', COUNT(*), 11 FROM commerce_analytics.activity_events;

SELECT p.product_id, p.name, p.stock_quantity AS actual_stock,
       expected.expected_stock,
       p.stock_quantity = expected.expected_stock AS matches
FROM (
    SELECT 1 AS product_id, 98 AS expected_stock
    UNION ALL SELECT 2, 95
    UNION ALL SELECT 3, 99
) AS expected
LEFT JOIN commerce_analytics.products AS p ON p.product_id = expected.product_id
ORDER BY expected.product_id;

-- Expected: one notebook purchase line, current price 12.00, captured price 10.00.
SELECT p.name, p.unit_price AS current_price, oi.order_id,
       oi.unit_price AS purchased_price,
       p.unit_price = 12.00 AND oi.unit_price = 10.00 AS matches
FROM commerce_analytics.products AS p
JOIN commerce_analytics.order_items AS oi ON oi.product_id = p.product_id
WHERE p.product_id = 1
ORDER BY oi.order_id;

SELECT SUM(quantity * unit_price) AS historical_revenue,
       CAST(38.00 AS DECIMAL(10,2)) AS expected_revenue
FROM commerce_analytics.order_items;

-- Logical cross-engine references: every violation count must be zero.
-- Aggregate missing matches: anti-join forms returned wrong counts without errors
-- on the pinned MariaDB 12.3.3 DuckDB path. These checks passed valid and invalid fixtures.
SELECT COUNT(CASE WHEN p.product_id IS NULL THEN 1 END) AS events_with_missing_product
FROM commerce_analytics.activity_events AS e
LEFT JOIN commerce_analytics.products AS p ON p.product_id = e.product_id
WHERE e.product_id IS NOT NULL;

SELECT COUNT(CASE WHEN c.campaign_id IS NULL THEN 1 END) AS events_with_missing_campaign
FROM commerce_analytics.activity_events AS e
LEFT JOIN commerce_analytics.campaigns AS c ON c.campaign_id = e.campaign_id
WHERE e.campaign_id IS NOT NULL;

-- COUNT(DISTINCT) ignores NULL: explicitly detect organic/campaign mixtures.
SELECT COUNT(*) AS sessions_with_inconsistent_event_attribution
FROM (
    SELECT session_id
    FROM commerce_analytics.activity_events
    GROUP BY session_id
    HAVING COUNT(DISTINCT campaign_id) > 1
        OR (COUNT(campaign_id) > 0 AND COUNT(campaign_id) < COUNT(*))
) AS inconsistent;

SELECT COUNT(*) AS sessions_without_exactly_one_start
FROM (
    SELECT session_id
    FROM commerce_analytics.activity_events
    GROUP BY session_id
    HAVING SUM(CASE WHEN event_type = 'session_start' THEN 1 ELSE 0 END) <> 1
) AS invalid_sessions;

SELECT COUNT(DISTINCT CASE WHEN e.session_id IS NULL THEN o.order_id END)
       AS orders_without_matching_session_attribution
FROM commerce_analytics.orders AS o
LEFT JOIN commerce_analytics.activity_events AS e
  ON e.event_type = 'session_start' AND e.session_id = o.session_id
 AND (e.campaign_id = o.campaign_id
      OR (e.campaign_id IS NULL AND o.campaign_id IS NULL))
 AND e.occurred_at <= o.purchased_at;

-- Cohort: session starts on 2026-09-13 from 12:00 inclusive to 12:05 exclusive.
-- Observe purchases through 12:10 inclusive, and never before session start.
-- Session starts exclude repeated views; per-order revenue prevents item fan-out.
-- Event IDs are not used. Paused campaigns retain historical attribution.
WITH session_cohort AS (
    SELECT session_id, campaign_id, occurred_at AS started_at
    FROM commerce_analytics.activity_events
    WHERE event_type = 'session_start'
      AND occurred_at >= '2026-09-13 12:00:00.000000'
      AND occurred_at < '2026-09-13 12:05:00.000000'
), order_revenue AS (
    SELECT order_id, SUM(quantity * unit_price) AS revenue
    FROM commerce_analytics.order_items
    GROUP BY order_id
)
SELECT
    CASE WHEN s.campaign_id IS NULL THEN 'Organic' ELSE c.name END AS attribution,
    CASE WHEN s.campaign_id IS NULL THEN '—'
         WHEN c.is_active = 1 THEN 'Active' ELSE 'Paused' END AS current_campaign_state,
    COUNT(*) AS sessions,
    COUNT(o.order_id) AS purchasing_sessions,
    ROUND(100.0 * COUNT(o.order_id) / COUNT(*), 2) AS conversion_percent,
    CAST(COALESCE(SUM(r.revenue), 0) AS DECIMAL(12,2)) AS revenue_eur
FROM session_cohort AS s
LEFT JOIN commerce_analytics.campaigns AS c ON c.campaign_id = s.campaign_id
LEFT JOIN commerce_analytics.orders AS o
  ON o.session_id = s.session_id
 AND o.purchased_at >= s.started_at
 AND o.purchased_at <= '2026-09-13 12:10:00.000000'
LEFT JOIN order_revenue AS r ON r.order_id = o.order_id
GROUP BY s.campaign_id, c.name, c.is_active
ORDER BY CASE WHEN s.campaign_id IS NULL THEN 1 ELSE 0 END, s.campaign_id;
