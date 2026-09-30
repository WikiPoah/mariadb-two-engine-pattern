CREATE TABLE IF NOT EXISTS activity_events_copy (
    source_event_id BIGINT PRIMARY KEY,
    session_id VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    campaign_id INTEGER,
    product_id INTEGER,
    first_visible_batch_id BIGINT NOT NULL,
    CHECK (event_type IN ('session_start', 'product_view')),
    CHECK (first_visible_batch_id > 0),
    CHECK (
        (event_type = 'session_start' AND product_id IS NULL)
        OR (event_type = 'product_view' AND product_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS order_facts (
    order_id BIGINT PRIMARY KEY,
    session_id VARCHAR NOT NULL,
    campaign_id INTEGER,
    purchased_at TIMESTAMP NOT NULL,
    revenue_eur DECIMAL(18,2) NOT NULL,
    units_sold BIGINT NOT NULL,
    CHECK (revenue_eur >= 0),
    CHECK (units_sold > 0)
);

CREATE TABLE IF NOT EXISTS campaigns_snapshot (
    campaign_id INTEGER PRIMARY KEY,
    campaign_name VARCHAR NOT NULL,
    is_active BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS feed_batches (
    batch_id BIGINT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    source_read_at TIMESTAMPTZ NOT NULL,
    commit_started_at TIMESTAMPTZ NOT NULL,
    destination_visible_at TIMESTAMPTZ,
    event_checkpoint BIGINT NOT NULL,
    order_checkpoint BIGINT NOT NULL,
    events_loaded BIGINT NOT NULL,
    orders_loaded BIGINT NOT NULL,
    campaigns_loaded BIGINT NOT NULL,
    CHECK (event_checkpoint >= 0),
    CHECK (order_checkpoint >= 0),
    CHECK (events_loaded >= 0),
    CHECK (orders_loaded >= 0),
    CHECK (campaigns_loaded >= 0)
);
