SELECT COUNT(DISTINCT CASE WHEN event_type = 'session_start' THEN session_id END) AS sessions,
       COALESCE(SUM(CASE WHEN event_type = 'product_view' THEN 1 ELSE 0 END), 0) AS product_views
FROM activity_events;
