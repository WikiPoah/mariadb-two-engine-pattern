USE commerce_analytics;

SELECT
    TABLE_NAME,
    ENGINE
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'commerce_analytics'
  AND TABLE_NAME = 'activity_events';

SELECT
    'rollback_baseline' AS checkpoint,
    COUNT(*) AS activity_event_count
FROM activity_events;

START TRANSACTION;

INSERT INTO activity_events
    (session_id, event_type, occurred_at, campaign_id, product_id)
VALUES (
    '00000000-0000-4000-8000-000000000099',
    'product_view',
    '2026-09-13 12:21:00.000000',
    1,
    1
);

SELECT
    'rollback_inside_transaction' AS checkpoint,
    COUNT(*) AS activity_event_count,
    SUM(session_id = '00000000-0000-4000-8000-000000000099') AS temporary_event_count
FROM activity_events;

ROLLBACK;

SELECT
    'rollback_after' AS checkpoint,
    COUNT(*) AS activity_event_count,
    SUM(session_id = '00000000-0000-4000-8000-000000000099') AS temporary_event_count
FROM activity_events;

SELECT
    'commit_baseline' AS checkpoint,
    COUNT(*) AS activity_event_count
FROM activity_events;

START TRANSACTION;

INSERT INTO activity_events
    (session_id, event_type, occurred_at, campaign_id, product_id)
VALUES (
    '00000000-0000-4000-8000-000000000098',
    'product_view',
    '2026-09-13 12:22:00.000000',
    1,
    1
);

SELECT
    'commit_inside_transaction' AS checkpoint,
    COUNT(*) AS activity_event_count,
    SUM(session_id = '00000000-0000-4000-8000-000000000098') AS temporary_event_count
FROM activity_events;

COMMIT;

SELECT
    'commit_after' AS checkpoint,
    COUNT(*) AS activity_event_count,
    SUM(session_id = '00000000-0000-4000-8000-000000000098') AS temporary_event_count
FROM activity_events;
