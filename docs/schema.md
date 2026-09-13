# Commerce and campaign schema

## Workload and scope

Real-Time Commerce & Campaign Analytics models a visitor arriving organically or through a campaign, viewing products, and either leaving or purchasing. The current implementation is a deterministic dataset and SQL analytics, not a running commerce website or continuous simulator.

The future simulator will keep carts in memory. There is no customer account or sessions table, payment integration, shipping, refund, cancellation, or order-status lifecycle. One session may produce at most one purchase. The workload motivates the engine split; its performance value still requires measurement.

## Tables and relationships

The executable definition is [01-schema.sql](../sql/01-schema.sql).

| Table | Purpose and engine rationale | Keys and relationships |
| --- | --- | --- |
| `products` | InnoDB holds mutable catalog prices, stock, and availability; stock changes must commit with purchases. | Explicit product ID primary key; nonnegative price/stock and boolean-state checks. |
| `campaigns` | InnoDB holds small, mutable acquisition controls. Pausing a campaign does not erase its identity. | Explicit campaign ID primary key; boolean-state check. |
| `orders` | InnoDB is the authoritative completed-purchase record. | Generated order ID primary key; unique session ID; nullable campaign foreign key. |
| `order_items` | InnoDB keeps purchase lines in the same transaction as orders and stock. | Composite `(order_id, product_id)` primary key; foreign keys to orders and products; positive quantity and nonnegative price checks. |
| `activity_events` | DuckDB holds accumulating session-start/product-view history for scans and aggregation. | Generated event ID with a declared primary key whose uniqueness was not enforced in our test; no cross-engine foreign keys or secondary indexes. |

All InnoDB foreign keys use `RESTRICT` for deletion and key updates. IDs are treated as immutable. Event product/campaign references and the relationship between order sessions and event sessions are logical: MariaDB foreign keys cannot cross this engine boundary. Products and campaigns should be retained even when only events reference them.

## Checkout and historical truth

Each seeded purchase locks its product rows and creates the order, its items, and stock decrements inside one explicit InnoDB transaction. Event inserts are outside those transactions. No successful checkout depends on committing a DuckDB write.

`order_items.unit_price` captures the catalog price at checkout. Revenue is derived from quantity times this captured price; there is no duplicated order total. The seed changes the notebook catalog price from EUR 10 to EUR 12 after purchases, while its purchased price stays EUR 10. Committed purchases must remain immutable through application policy; the schema does not prohibit all later updates.

The future checkout implementation must validate product availability and sufficient stock while holding locks, lock multiple products in consistent ID order, create at least one item, and roll back the entire purchase on failure. The deterministic seed assumes known fresh data; it is not a general checkout implementation. A nonnegative-stock check alone is not a concurrency-control strategy.

## Sessions, attribution, time, and currency

- Session IDs are canonical lowercase UUID strings, exactly 36 characters without spaces, validated before writing. `CHAR(36)` and its collation do not themselves validate UUID syntax.
- Each session has exactly one `session_start`. All events and any order retain the campaign chosen at acquisition; `NULL` means organic traffic.
- Only active campaigns acquire new sessions. Pausing a campaign does not prevent a previously acquired session from purchasing. Study Week is paused after its seeded session events are inserted and before its attributed purchase.
- Event product IDs are null for session starts and nonnull for product views. Event history contains no duplicate purchase fact.
- `DATETIME(6)` values represent UTC business occurrence time. Writers must use UTC connections and explicit UTC values; the type does not store a timezone. Purchase time is not an exact commit timestamp. Events and purchases must not precede session start.
- All prices use exact `DECIMAL(10,2)` amounts in EUR, with no currency conversion. General text and session fields use `utf8mb4` / `utf8mb4_bin`.

## Integrity: guarantees versus observations

**InnoDB enforcement:** primary and unique keys, foreign keys, required columns, and row checks enforce the declared relational constraints under normal enabled constraint enforcement. `orders.session_id` limits orders per canonical session. Transactions provide the boundary for atomic checkout writes. The current application schema definitions and valid seeds have run successfully; this is not an exhaustive negative test of every InnoDB constraint.

**Verified plugin behavior:** disposable tests on MariaDB `12.3.3-MariaDB-ubu2404`, package `mariadb-plugin-duckdb=1:12.3.3+maria~ubu2404`, on Linux/ARM64 accepted the event table and generated IDs. Both event checks rejected tested invalid inserts without storing rows, and a null `event_type` was rejected. Sample `DATETIME(6)` values and canonical session strings round-tripped correctly. Other required columns were declared `NOT NULL` but not individually probed with null inputs.

**Future simulator/application responsibilities:** canonical UUID validation, one session start, fixed attribution, chronological consistency, valid logical references, purchase immutability, and complete checkout error handling. Event IDs are convenient generated identifiers only: omit them on inserts and never use them as an enforced deduplication or integrity mechanism. Missing analytical history may affect metrics but must not compromise committed purchases.

Expected-failure constraint probes belong in a disposable environment, separate from [03-verify.sql](../sql/03-verify.sql). That file reads data and reports results; it neither mutates fixtures nor proves enforcement by attempting invalid writes.

## Observed limitations of the pinned environment

These are observations from this project's MariaDB 12.3.3 DuckDB-plugin experiments, not claims about every MariaDB version or standalone DuckDB.

| Observation | Design consequence |
| --- | --- |
| An explicit duplicate primary-key value was accepted and both rows remained stored. | Do not infer uniqueness from the primary-key declaration or metadata. Automatic IDs worked, with gaps, but are not a deduplication guarantee. |
| A session/time index was accepted and listed as `HASH`; the inspected plan used a scan and filesort. | Useful index execution was not demonstrated. Omit secondary indexes initially; this tiny test does not establish that all indexes are unusable. |
| Both tested comparison paths were case-sensitive, but a trailing-space variant compared equal in ordinary MariaDB and unequal on the DuckDB-table path. | Canonicalize UUIDs at the writer boundary; do not rely on cross-engine string comparisons for byte-exact validation. |
| `MICROSECOND()` returned `1654321` for a stored value ending in `01.654321`, while a MariaDB literal control returned `654321`. Direct timestamp comparison matched the inserted value. | Keep tested `DATETIME(6)` values and direct range comparisons; avoid this inconsistent function path. No general claim that every datetime function is incorrect. |
| Initial anti-join formulations returned incorrect results without errors. Valid product/campaign references were counted as missing; one `NOT EXISTS` check also missed an intentionally unmatched order. | Count unmatched rows within aggregates over left joins instead. Final checks passed both valid seeds and deliberately inconsistent disposable data. The internal cause has not been established. |

The plugin reported `ACTIVE` and maturity `Gamma`; startup retains the tested `--plugin-maturity=alpha` and `--plugin-load-add=ha_duckdb.so` options. Metadata and successful DDL alone are not proof of enforcement or query correctness.

## Deterministic evidence and analytics

[02-seed.sql](../sql/02-seed.sql) contains 3 products, 2 campaigns, 5 sessions, 11 events, 3 orders, and 4 items. UUID suffixes `001` through `005` identify sessions A through E. A purchases EUR 26, C purchases EUR 8, D purchases EUR 4; B and E do not purchase. Final notebook/pen/mug stock is 98/95/99.

The core query takes only session starts in `[2026-09-13 12:00, 12:05)` UTC and counts purchases no earlier than their session start and no later than 12:10 UTC inclusive. It aggregates item revenue per order before joining purchases to sessions. This prevents item fan-out, while excluding product views prevents repeated browsing from multiplying purchases. The query assumes one start per session; the preceding integrity check reports violations rather than silently repairing them.

Conversion is purchasing sessions divided by cohort sessions. Campaign state is current state, while attribution is historical; paused Study Week remains visible. Expected Campus Launch / Study Week / Organic results are respectively 2/1/2 sessions, 1/1/1 purchasing sessions, 50/100/50 percent conversion, and EUR 26/8/4 revenue.

Verification also inspects table engines, row counts, stock, price preservation, total revenue, and logical relationship violations. These results establish correctness for the tested fixtures, not continuous-write behavior, performance superiority, or cross-engine transaction guarantees. The next roadmap step is continuous simulation, after Phase 2 review.
