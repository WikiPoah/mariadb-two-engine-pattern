USE commerce_analytics;

-- Probe only the declared orders primary key; all other values are valid.
INSERT INTO orders (order_id, session_id, campaign_id, purchased_at)
VALUES (
    1,
    '00000000-0000-4000-8000-000000000095',
    1,
    '2026-09-13 12:25:00.000000'
);
