USE commerce_analytics;
SET SESSION time_zone = '+00:00';

INSERT INTO products (product_id, name, unit_price, stock_quantity)
VALUES (1, 'Notebook', 10.00, 100),
       (2, 'Pen', 2.00, 100),
       (3, 'Mug', 8.00, 100);

INSERT INTO campaigns (campaign_id, name)
VALUES (1, 'Campus Launch'), (2, 'Study Week');

-- Sessions A through E use UUID suffixes 001 through 005.
-- Attribution is fixed at session start; NULL means organic traffic.
-- Omit event_id: let DuckDB allocate it; seed identity never depends on its values.
INSERT INTO activity_events
    (session_id, event_type, occurred_at, campaign_id, product_id)
VALUES
    ('123e4567-e89b-42d3-a456-426614174001', 'session_start', '2026-09-13 12:00:00.000000', 1, NULL),
    ('123e4567-e89b-42d3-a456-426614174001', 'product_view',  '2026-09-13 12:00:10.000000', 1, 1),
    ('123e4567-e89b-42d3-a456-426614174001', 'product_view',  '2026-09-13 12:00:20.000000', 1, 2),
    ('123e4567-e89b-42d3-a456-426614174002', 'session_start', '2026-09-13 12:01:00.000000', 1, NULL),
    ('123e4567-e89b-42d3-a456-426614174002', 'product_view',  '2026-09-13 12:01:10.000000', 1, 1),
    ('123e4567-e89b-42d3-a456-426614174003', 'session_start', '2026-09-13 12:02:00.000000', 2, NULL),
    ('123e4567-e89b-42d3-a456-426614174003', 'product_view',  '2026-09-13 12:02:10.000000', 2, 3),
    ('123e4567-e89b-42d3-a456-426614174004', 'session_start', '2026-09-13 12:03:00.000000', NULL, NULL),
    ('123e4567-e89b-42d3-a456-426614174004', 'product_view',  '2026-09-13 12:03:10.000000', NULL, 2),
    ('123e4567-e89b-42d3-a456-426614174005', 'session_start', '2026-09-13 12:04:00.000000', NULL, NULL),
    ('123e4567-e89b-42d3-a456-426614174005', 'product_view',  '2026-09-13 12:04:10.000000', NULL, 3);

-- Fixed order IDs make this fresh-database seed easy to inspect.
-- Explicit InnoDB transactions commit each order, its captured prices, and stock together.
START TRANSACTION;
SELECT product_id FROM products WHERE product_id IN (1, 2)
ORDER BY product_id FOR UPDATE;
INSERT INTO orders (order_id, session_id, campaign_id, purchased_at)
VALUES (1, '123e4567-e89b-42d3-a456-426614174001', 1, '2026-09-13 12:00:30.000000');
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT 1, product_id, 2, unit_price FROM products WHERE product_id = 1;
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT 1, product_id, 3, unit_price FROM products WHERE product_id = 2;
UPDATE products SET stock_quantity = stock_quantity - 2 WHERE product_id = 1;
UPDATE products SET stock_quantity = stock_quantity - 3 WHERE product_id = 2;
COMMIT;

-- Pause Study Week after its session was acquired. Attribution remains intact.
UPDATE campaigns SET is_active = FALSE WHERE campaign_id = 2;

START TRANSACTION;
SELECT product_id FROM products WHERE product_id = 3 FOR UPDATE;
INSERT INTO orders (order_id, session_id, campaign_id, purchased_at)
VALUES (2, '123e4567-e89b-42d3-a456-426614174003', 2, '2026-09-13 12:02:30.000000');
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT 2, product_id, 1, unit_price FROM products WHERE product_id = 3;
UPDATE products SET stock_quantity = stock_quantity - 1 WHERE product_id = 3;
COMMIT;

START TRANSACTION;
SELECT product_id FROM products WHERE product_id = 2 FOR UPDATE;
INSERT INTO orders (order_id, session_id, campaign_id, purchased_at)
VALUES (3, '123e4567-e89b-42d3-a456-426614174004', NULL, '2026-09-13 12:03:30.000000');
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT 3, product_id, 2, unit_price FROM products WHERE product_id = 2;
UPDATE products SET stock_quantity = stock_quantity - 2 WHERE product_id = 2;
COMMIT;

-- Catalog changes must not rewrite purchase-time prices.
UPDATE products SET unit_price = 12.00 WHERE product_id = 1;
