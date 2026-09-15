SELECT (SELECT COUNT(*) FROM orders) AS orders,
       COALESCE(SUM(quantity * unit_price), 0) AS revenue,
       COALESCE(SUM(quantity), 0) AS units_sold
FROM order_items;
