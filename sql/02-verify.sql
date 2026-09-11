USE engine_proof;

SELECT * FROM products ORDER BY id;
SELECT * FROM sales ORDER BY id;

SELECT TABLE_NAME, ENGINE
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'engine_proof'
ORDER BY TABLE_NAME;

SELECT s.id AS sale_id, p.name, s.quantity
FROM products AS p
JOIN sales AS s ON s.product_id = p.id
ORDER BY s.id;
