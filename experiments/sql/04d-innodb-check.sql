USE commerce_analytics;

-- Probe chk_product_stock while satisfying the other product constraints.
INSERT INTO products
    (product_id, name, unit_price, stock_quantity, is_active)
VALUES (999998, 'Invalid Stock Probe', 1.00, -1, TRUE);
