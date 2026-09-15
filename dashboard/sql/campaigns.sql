-- One row per session prevents repeated views or duplicate starts multiplying purchases.
-- Validate within the same statement; do not depend on a separate preflight snapshot.
WITH session_history AS (
    SELECT session_id, MIN(campaign_id) AS campaign_id,
           MIN(CASE WHEN event_type = 'session_start' THEN occurred_at END) AS started_at,
           SUM(CASE WHEN event_type = 'session_start' THEN 1 ELSE 0 END) AS starts,
           COUNT(DISTINCT campaign_id) AS campaign_count,
           COUNT(campaign_id) AS attributed_events, COUNT(*) AS events
    FROM activity_events
    GROUP BY session_id
), order_revenue AS (
    SELECT order_id, SUM(quantity * unit_price) AS revenue
    FROM order_items
    GROUP BY order_id
)
SELECT s.campaign_id,
       CASE WHEN s.campaign_id IS NULL THEN 'Organic' ELSE c.name END AS campaign_name,
       c.is_active,
       COUNT(*) AS sessions,
       COUNT(o.order_id) AS purchasing_sessions,
       COALESCE(SUM(r.revenue), 0) AS revenue,
       ROUND(100.0 * COUNT(o.order_id) / COUNT(*), 2) AS conversion_percent,
       SUM(CASE WHEN s.starts <> 1 OR s.campaign_count > 1
                     OR (s.attributed_events > 0 AND s.attributed_events < s.events)
                     OR (s.campaign_id IS NOT NULL AND c.campaign_id IS NULL)
                     OR (o.order_id IS NOT NULL AND
                         (o.campaign_id <> s.campaign_id
                          OR (o.campaign_id IS NULL AND s.campaign_id IS NOT NULL)
                          OR (o.campaign_id IS NOT NULL AND s.campaign_id IS NULL)
                          OR o.purchased_at < s.started_at))
                THEN 1 ELSE 0 END) AS invalid_sessions
FROM session_history AS s
LEFT JOIN campaigns AS c ON c.campaign_id = s.campaign_id
LEFT JOIN orders AS o ON o.session_id = s.session_id
LEFT JOIN order_revenue AS r ON r.order_id = o.order_id
GROUP BY s.campaign_id, c.name, c.is_active
ORDER BY CASE WHEN s.campaign_id IS NULL THEN 1 ELSE 0 END, s.campaign_id;
