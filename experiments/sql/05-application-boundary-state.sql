USE commerce_analytics;

SET @failure_session_id = '00000000-0000-4000-8000-000000000091';
SET @control_session_id = '00000000-0000-4000-8000-000000000092';

-- Purchase truth comes only from InnoDB orders, items, and stock.
SELECT
    COUNT(DISTINCT o.order_id) AS orders,
    COUNT(oi.product_id) AS order_items,
    (SELECT stock_quantity FROM products WHERE product_id = 1) AS notebook_stock,
    COALESCE(SUM(oi.quantity * oi.unit_price), 0.00) AS historical_revenue
FROM orders AS o
LEFT JOIN order_items AS oi ON oi.order_id = o.order_id;

SELECT
    COUNT(*) AS activity_events,
    SUM(session_id = @failure_session_id) AS failure_session_events,
    SUM(session_id = @control_session_id) AS control_session_events
FROM activity_events;

SELECT
    session_id,
    COUNT(*) AS events,
    SUM(event_type = 'session_start') AS session_starts,
    SUM(event_type = 'product_view') AS product_views
FROM activity_events
WHERE session_id IN (@failure_session_id, @control_session_id)
GROUP BY session_id
ORDER BY session_id;

SELECT order_id, session_id, campaign_id, purchased_at
FROM orders
WHERE session_id IN (@failure_session_id, @control_session_id)
ORDER BY session_id;

SELECT event_id, session_id, event_type, campaign_id, product_id, occurred_at
FROM activity_events
WHERE session_id IN (@failure_session_id, @control_session_id)
ORDER BY session_id, occurred_at;
