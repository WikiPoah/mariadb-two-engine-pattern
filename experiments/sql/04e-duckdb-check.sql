USE commerce_analytics;

-- Probe chk_event_product: a product_view requires a product identifier.
INSERT INTO activity_events
    (session_id, event_type, occurred_at, campaign_id, product_id)
VALUES (
    '00000000-0000-4000-8000-000000000093',
    'product_view',
    '2026-09-13 12:27:00.000000',
    1,
    NULL
);
