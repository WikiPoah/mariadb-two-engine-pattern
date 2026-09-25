USE commerce_analytics;

-- Baseline comes from the deterministic seed loaded immediately before this script.
SELECT
    'baseline' AS checkpoint,
    (SELECT COUNT(*) FROM orders) AS orders,
    (SELECT COUNT(*) FROM order_items) AS order_items,
    (SELECT stock_quantity FROM products WHERE name = 'Notebook') AS notebook_stock,
    (SELECT COALESCE(SUM(quantity * unit_price), 0.00) FROM order_items) AS historical_revenue;

START TRANSACTION;

SET @notebook_id = (
    SELECT product_id
    FROM products
    WHERE name = 'Notebook'
);

-- Lock the authoritative product row before reading its current price or stock.
SELECT product_id, unit_price, stock_quantity, is_active
FROM products
WHERE product_id = @notebook_id
FOR UPDATE;
SET @notebook_unit_price = (
    SELECT unit_price
    FROM products
    WHERE product_id = @notebook_id
);
SELECT @notebook_unit_price AS captured_notebook_unit_price;

INSERT INTO orders (session_id, campaign_id, purchased_at)
VALUES (
    '00000000-0000-4000-8000-000000000099',
    1,
    '2026-09-13 12:20:00.000000'
);
SET @temporary_order_id = LAST_INSERT_ID();

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
VALUES (@temporary_order_id, @notebook_id, 1, @notebook_unit_price);

UPDATE products
SET stock_quantity = stock_quantity - 1
WHERE product_id = @notebook_id;

SELECT
    'inside_transaction' AS checkpoint,
    (SELECT COUNT(*) FROM orders) AS orders,
    (SELECT COUNT(*) FROM order_items) AS order_items,
    (SELECT stock_quantity FROM products WHERE product_id = @notebook_id) AS notebook_stock,
    (SELECT COALESCE(SUM(quantity * unit_price), 0.00) FROM order_items) AS historical_revenue;

ROLLBACK;

SELECT
    'after_rollback' AS checkpoint,
    (SELECT COUNT(*) FROM orders) AS orders,
    (SELECT COUNT(*) FROM order_items) AS order_items,
    (SELECT stock_quantity FROM products WHERE product_id = @notebook_id) AS notebook_stock,
    (SELECT COALESCE(SUM(quantity * unit_price), 0.00) FROM order_items) AS historical_revenue;
