USE commerce_analytics;

-- Probe the declared event primary key with an otherwise valid event.
INSERT INTO activity_events
    (event_id, session_id, event_type, occurred_at, campaign_id, product_id)
VALUES (
    1,
    '00000000-0000-4000-8000-000000000094',
    'product_view',
    '2026-09-13 12:26:00.000000',
    1,
    1
);
