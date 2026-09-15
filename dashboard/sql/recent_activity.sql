-- Secondary ordering stabilizes distinguishable ties; event_id is not a unique cursor.
SELECT event_id, session_id, event_type, occurred_at, campaign_id, product_id
FROM activity_events
ORDER BY occurred_at DESC, session_id, event_type, campaign_id, product_id, event_id
LIMIT %s;
