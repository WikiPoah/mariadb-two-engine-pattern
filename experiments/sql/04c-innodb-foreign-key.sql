USE commerce_analytics;

-- Probe the real order_items-to-orders relationship with a missing parent.
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
VALUES (999999, 1, 1, 12.00);
