USE commerce_analytics;

-- This script is destructive by design and belongs only in a fresh disposable database.
SET @rollback_session_id = '00000000-0000-4000-8000-000000000097';
SET @commit_session_id = '00000000-0000-4000-8000-000000000096';
SET @notebook_id = (
    SELECT product_id
    FROM products
    WHERE name = 'Notebook'
);

SELECT
    'rollback_baseline_innodb' AS checkpoint,
    COUNT(*) AS orders,
    (SELECT COUNT(*) FROM order_items) AS order_items,
    (SELECT stock_quantity FROM products WHERE product_id = @notebook_id) AS notebook_stock,
    (SELECT COALESCE(SUM(quantity * unit_price), 0.00) FROM order_items) AS historical_revenue,
    SUM(session_id = @rollback_session_id) AS temporary_order_count
FROM orders;

SELECT
    'rollback_baseline_duckdb' AS checkpoint,
    COUNT(*) AS activity_events,
    SUM(session_id = @rollback_session_id) AS temporary_event_count
FROM activity_events;

START TRANSACTION;

INSERT INTO activity_events
    (session_id, event_type, occurred_at, campaign_id, product_id)
VALUES (
    @rollback_session_id,
    'product_view',
    '2026-09-13 12:23:00.000000',
    1,
    @notebook_id
);

SELECT product_id, unit_price, stock_quantity, is_active
FROM products
WHERE product_id = @notebook_id
FOR UPDATE;
SET @notebook_unit_price = (
    SELECT unit_price
    FROM products
    WHERE product_id = @notebook_id
);

INSERT INTO orders (session_id, campaign_id, purchased_at)
VALUES (@rollback_session_id, 1, '2026-09-13 12:23:01.000000');
SET @rollback_order_id = LAST_INSERT_ID();

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
VALUES (@rollback_order_id, @notebook_id, 1, @notebook_unit_price);

UPDATE products
SET stock_quantity = stock_quantity - 1
WHERE product_id = @notebook_id;

SELECT
    'rollback_inside_transaction_innodb' AS checkpoint,
    COUNT(*) AS orders,
    (SELECT COUNT(*) FROM order_items) AS order_items,
    (SELECT stock_quantity FROM products WHERE product_id = @notebook_id) AS notebook_stock,
    (SELECT COALESCE(SUM(quantity * unit_price), 0.00) FROM order_items) AS historical_revenue,
    SUM(session_id = @rollback_session_id) AS temporary_order_count
FROM orders;

SELECT
    'rollback_inside_transaction_duckdb' AS checkpoint,
    COUNT(*) AS activity_events,
    SUM(session_id = @rollback_session_id) AS temporary_event_count
FROM activity_events;

ROLLBACK;

SELECT
    'rollback_after_innodb' AS checkpoint,
    COUNT(*) AS orders,
    (SELECT COUNT(*) FROM order_items) AS order_items,
    (SELECT stock_quantity FROM products WHERE product_id = @notebook_id) AS notebook_stock,
    (SELECT COALESCE(SUM(quantity * unit_price), 0.00) FROM order_items) AS historical_revenue,
    SUM(session_id = @rollback_session_id) AS temporary_order_count
FROM orders;

SELECT
    'rollback_after_duckdb' AS checkpoint,
    COUNT(*) AS activity_events,
    SUM(session_id = @rollback_session_id) AS temporary_event_count
FROM activity_events;

SELECT
    'commit_baseline_innodb' AS checkpoint,
    COUNT(*) AS orders,
    (SELECT COUNT(*) FROM order_items) AS order_items,
    (SELECT stock_quantity FROM products WHERE product_id = @notebook_id) AS notebook_stock,
    (SELECT COALESCE(SUM(quantity * unit_price), 0.00) FROM order_items) AS historical_revenue,
    SUM(session_id = @commit_session_id) AS temporary_order_count
FROM orders;

SELECT
    'commit_baseline_duckdb' AS checkpoint,
    COUNT(*) AS activity_events,
    SUM(session_id = @commit_session_id) AS temporary_event_count
FROM activity_events;

START TRANSACTION;

INSERT INTO activity_events
    (session_id, event_type, occurred_at, campaign_id, product_id)
VALUES (
    @commit_session_id,
    'product_view',
    '2026-09-13 12:24:00.000000',
    1,
    @notebook_id
);

SELECT product_id, unit_price, stock_quantity, is_active
FROM products
WHERE product_id = @notebook_id
FOR UPDATE;
SET @notebook_unit_price = (
    SELECT unit_price
    FROM products
    WHERE product_id = @notebook_id
);

INSERT INTO orders (session_id, campaign_id, purchased_at)
VALUES (@commit_session_id, 1, '2026-09-13 12:24:01.000000');
SET @commit_order_id = LAST_INSERT_ID();

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
VALUES (@commit_order_id, @notebook_id, 1, @notebook_unit_price);

UPDATE products
SET stock_quantity = stock_quantity - 1
WHERE product_id = @notebook_id;

SELECT
    'commit_inside_transaction_innodb' AS checkpoint,
    COUNT(*) AS orders,
    (SELECT COUNT(*) FROM order_items) AS order_items,
    (SELECT stock_quantity FROM products WHERE product_id = @notebook_id) AS notebook_stock,
    (SELECT COALESCE(SUM(quantity * unit_price), 0.00) FROM order_items) AS historical_revenue,
    SUM(session_id = @commit_session_id) AS temporary_order_count
FROM orders;

SELECT
    'commit_inside_transaction_duckdb' AS checkpoint,
    COUNT(*) AS activity_events,
    SUM(session_id = @commit_session_id) AS temporary_event_count
FROM activity_events;

COMMIT;

SELECT
    'commit_after_innodb' AS checkpoint,
    COUNT(*) AS orders,
    (SELECT COUNT(*) FROM order_items) AS order_items,
    (SELECT stock_quantity FROM products WHERE product_id = @notebook_id) AS notebook_stock,
    (SELECT COALESCE(SUM(quantity * unit_price), 0.00) FROM order_items) AS historical_revenue,
    SUM(session_id = @commit_session_id) AS temporary_order_count
FROM orders;

SELECT
    'commit_after_duckdb' AS checkpoint,
    COUNT(*) AS activity_events,
    SUM(session_id = @commit_session_id) AS temporary_event_count
FROM activity_events;
