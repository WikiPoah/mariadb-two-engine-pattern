SELECT product_id, name AS product_name, unit_price AS current_price,
       stock_quantity AS stock, is_active
FROM products
ORDER BY product_id;
