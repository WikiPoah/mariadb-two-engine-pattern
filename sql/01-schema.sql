CREATE DATABASE commerce_analytics
    CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
USE commerce_analytics;

-- InnoDB keeps mutable catalog/state and all purchase writes within one transactional engine.
CREATE TABLE products (
    product_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    unit_price DECIMAL(10,2) NOT NULL,
    stock_quantity INT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (product_id),
    CONSTRAINT chk_product_price CHECK (unit_price >= 0),
    CONSTRAINT chk_product_stock CHECK (stock_quantity >= 0),
    CONSTRAINT chk_product_active CHECK (is_active IN (0, 1))
) ENGINE=InnoDB
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE campaigns (
    campaign_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (campaign_id),
    CONSTRAINT chk_campaign_active CHECK (is_active IN (0, 1))
) ENGINE=InnoDB
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE orders (
    order_id BIGINT NOT NULL AUTO_INCREMENT,
    session_id CHAR(36) NOT NULL,
    campaign_id INT DEFAULT NULL,
    purchased_at DATETIME(6) NOT NULL,
    PRIMARY KEY (order_id),
    CONSTRAINT uq_order_session UNIQUE (session_id),
    CONSTRAINT fk_order_campaign FOREIGN KEY (campaign_id)
        REFERENCES campaigns (campaign_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- Capture checkout prices here so catalog updates cannot change historical revenue.
CREATE TABLE order_items (
    order_id BIGINT NOT NULL,
    product_id INT NOT NULL,
    quantity INT NOT NULL,
    unit_price DECIMAL(10,2) NOT NULL,
    PRIMARY KEY (order_id, product_id),
    CONSTRAINT fk_item_order FOREIGN KEY (order_id)
        REFERENCES orders (order_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_item_product FOREIGN KEY (product_id)
        REFERENCES products (product_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_item_quantity CHECK (quantity > 0),
    CONSTRAINT chk_item_price CHECK (unit_price >= 0)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- DuckDB holds browsing history for scans and aggregation, outside checkout transactions.
-- The pinned MariaDB 12.3.3 DuckDB plugin accepted explicit duplicate primary-key values.
-- event_id is a generated identifier, not a uniqueness or deduplication guarantee.
CREATE TABLE activity_events (
    event_id BIGINT NOT NULL AUTO_INCREMENT,
    session_id CHAR(36) NOT NULL,
    event_type VARCHAR(20) NOT NULL,
    occurred_at DATETIME(6) NOT NULL,
    campaign_id INT DEFAULT NULL,
    product_id INT DEFAULT NULL,
    PRIMARY KEY (event_id),
    CONSTRAINT chk_event_type
        CHECK (event_type IN ('session_start', 'product_view')),
    CONSTRAINT chk_event_product CHECK (
        (event_type = 'session_start' AND product_id IS NULL)
        OR (event_type = 'product_view' AND product_id IS NOT NULL)
    )
) ENGINE=DuckDB
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
