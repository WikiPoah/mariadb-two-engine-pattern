# Two-Engine Experimental Results

## Environment

The experiments ran in fresh Docker Compose environments with the following
pinned application and database configuration:

| Component | Tested configuration |
|---|---|
| MariaDB | `12.3.3-MariaDB-ubu2404` |
| Analytical engine | `mariadb-plugin-duckdb=1:12.3.3+maria~ubu2404`, reported `ACTIVE` |
| Operational tables | `products`, `campaigns`, `orders`, and `order_items` on InnoDB |
| Behavioural table | `activity_events` on the MariaDB DuckDB storage engine |
| Character handling | `utf8mb4` with `utf8mb4_bin` |

Each disposable database was loaded from the unchanged deterministic schema and
seed. Its relevant baseline was:

- 3 orders;
- 4 order items;
- Notebook stock of 98;
- EUR 38.00 historical revenue;
- 11 activity events.

Fresh MariaDB connections were used for durable-state verification where the
experiment required it. Disposable containers and volumes were removed after
the evidence was recorded.

## Results at a glance

| Experiment | Question | Observed result | Architectural relevance |
|---|---|---|---|
| InnoDB rollback | Are related operational mutations restored by `ROLLBACK`? | Temporary order, item, and stock changes were visible inside the transaction and absent after rollback. | Checkout mutations fit within one InnoDB transaction. |
| DuckDB rollback and commit control | How does a DuckDB-backed insert behave inside explicit transaction control? | The inserted event was not visible inside either transaction. Rollback discarded it; commit made it visible. | DuckDB transaction visibility differed from InnoDB in the tested path and must be measured rather than assumed. |
| Cross-engine transaction | What happens when one MariaDB transaction touches both engines? | Normal rollback discarded both engines' changes; normal commit persisted both. | The observation is useful evidence, but it is not a guarantee of cross-engine atomicity under failures. |
| Integrity enforcement | Which declared constraints rejected the tested violations? | InnoDB rejected duplicate PK, FK, and `CHECK` violations. DuckDB accepted the tested duplicate declared PK and rejected the tested `CHECK` violation. | Authoritative integrity remains in InnoDB; DuckDB event IDs are not treated as uniqueness guarantees. |
| Application failure boundary | Does an event-write failure prevent the normal simulator purchase path from reaching checkout? | A failed product view returned `event_failure`, called checkout zero times, and left InnoDB unchanged; the successful control purchased once. | Independent event writes may remain persisted, while checkout remains an InnoDB-only authority boundary. |
| Separate analytical store freshness | How stale do analytical events become behind a periodic feed to a standalone store? | Five controlled runs observed a pooled median of 1100.525 ms and nearest-rank p95 of 2013.842 ms, with exact final reconciliation and convergence. | A separate analytical store introduces an explicit data movement and freshness boundary. |

## 1. InnoDB rollback

The experiment locked the seeded Notebook row, captured its current EUR 12.00
price, inserted a temporary order and item, and decremented stock inside one
explicit InnoDB transaction.

| Checkpoint | Orders | Order items | Notebook stock | Historical revenue |
|---|---:|---:|---:|---:|
| Baseline | 3 | 4 | 98 | EUR 38.00 |
| Inside transaction | 4 | 5 | 97 | EUR 50.00 |
| After `ROLLBACK` | 3 | 4 | 98 | EUR 38.00 |

A fresh connection confirmed that the temporary session and order were absent.

## 2. DuckDB rollback and commit control

Both cases inserted a valid event into the existing DuckDB-backed
`activity_events` table.

| Case and checkpoint | Activity events | Temporary event visible |
|---|---:|---:|
| Rollback baseline | 11 | No |
| Rollback: inside transaction | 11 | No |
| Rollback: after `ROLLBACK` | 11 | No |
| Commit-control baseline | 11 | No |
| Commit control: inside transaction | 11 | No |
| Commit control: after `COMMIT` | 12 | Yes |

A fresh connection confirmed that the rollback event was absent and the
commit-control event was present. This result describes insert visibility and
transaction completion in the pinned MariaDB DuckDB engine; it does not mean
that DuckDB lacks transactions.

## 3. Cross-engine transaction

One MariaDB connection and explicit transaction inserted a DuckDB event and
mutated the InnoDB order, item, and product tables. Rollback and commit were
tested separately.

### Rollback

| Checkpoint | Orders | Order items | Notebook stock | Revenue | Activity events | Temporary event visible |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 3 | 4 | 98 | EUR 38.00 | 11 | No |
| Inside transaction | 4 | 5 | 97 | EUR 50.00 | 11 | No |
| After `ROLLBACK` | 3 | 4 | 98 | EUR 38.00 | 11 | No |

Neither the temporary InnoDB mutations nor the DuckDB event persisted.

### Commit control

| Checkpoint | Orders | Order items | Notebook stock | Revenue | Activity events | Temporary event visible |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 3 | 4 | 98 | EUR 38.00 | 11 | No |
| Inside transaction | 4 | 5 | 97 | EUR 50.00 | 11 | No |
| After `COMMIT` | 4 | 5 | 97 | EUR 50.00 | 12 | Yes |

A fresh connection confirmed both committed mutations. These normal
rollback/commit observations do not establish guaranteed cross-engine atomicity
under crashes, engine errors, or uncertain commits.

## 4. Integrity enforcement

Each probe ran against a separately restored fixture. Rejected statements left
the inspected table unchanged; the accepted DuckDB row was inspected from a
fresh connection and removed only by destroying the disposable volume.

| Probe | Outcome | Diagnostic | Durable state |
|---|---|---|---|
| InnoDB duplicate primary key | Rejected | SQLSTATE `23000`, error `1062`, duplicate key `PRIMARY` | Orders remained 3; the probe session was absent. |
| DuckDB duplicate declared primary key | Accepted without warning or error | Client exit 0 | Events increased 11 to 12; `event_id=1` count increased 1 to 2; the probe row was present. |
| InnoDB foreign key | Rejected | SQLSTATE `23000`, error `1452`, `fk_item_order` | Order items remained 4; the invalid child was absent. |
| InnoDB `CHECK` | Rejected | SQLSTATE `23000`, error `4025`, `chk_product_stock` | Products remained 3; the invalid product was absent. |
| DuckDB `CHECK` | Rejected | SQLSTATE `23000`, error `4025`, `chk_event_product` | Events remained 11; the invalid event was absent. |

`activity_events.campaign_id` and `activity_events.product_id` are logical
references with no declared foreign key. No artificial DuckDB foreign-key
schema was created merely for symmetry. The duplicate-key observation applies
only to the declared key and pinned engine tested here; it does not establish
that every DuckDB primary key or constraint is unenforced.

## 5. Application failure boundary

The harness called the production `run_session()` function with real MariaDB
connections. It fixed the normally random path to campaign 1, one Notebook
view, and a one-Notebook cart. The control used the production event writer and
checkout implementation.

### Controlled product-view failure

| State | Orders | Order items | Notebook stock | Revenue | Activity events |
|---|---:|---:|---:|---:|---:|
| Baseline | 3 | 4 | 98 | EUR 38.00 | 11 |
| Fresh connection after failure | 3 | 4 | 98 | EUR 38.00 | 12 |

The real `session_start` succeeded and remained persisted. The later
`product_view` deliberately raised `pymysql.OperationalError` 9001.
`run_session()` returned `event_failure`, and the checkout observer recorded
`checkout_calls=0`. The failure session contained one `session_start`, zero
product views, and no order.

### Successful control

The control started from an independently restored deterministic fixture.

| State | Orders | Order items | Notebook stock | Revenue | Activity events |
|---|---:|---:|---:|---:|---:|
| Baseline | 3 | 4 | 98 | EUR 38.00 | 11 |
| Fresh connection after control | 4 | 5 | 97 | EUR 50.00 | 13 |

Both `session_start` and `product_view` succeeded. `run_session()` returned
`purchased`, and the checkout observer recorded `checkout_calls=1`.

The two cases show that a preceding event-write failure prevents the normal
simulator purchase path from reaching checkout, while an earlier independently
successful event write can remain persisted. This is an application control-flow
boundary, not cross-engine atomicity.

## 6. Separate analytical store freshness

### Purpose and configuration

The Area 20 alternative architecture used the existing MariaDB server as the
authoritative source, a periodic read-only feed, and a genuinely separate
standalone DuckDB analytical store:

```text
MariaDB 12.3.3 source -> periodic feed -> standalone DuckDB destination
```

The MariaDB DuckDB storage engine remained active on the source for
`activity_events`. Moving the analytical copy to another store introduced an
explicit data movement and freshness boundary.

Five independent runs started with fresh MariaDB state, the unchanged
deterministic fixture, and a fresh standalone destination. Every run used:

- simulator seed 17;
- 20 sessions at a 0.1-second session interval;
- a 2.0-second feed interval;
- baseline event checkpoint 11 and order checkpoint 3.

The retained evidence independently records 20 distinct measured
`session_start` sessions, 67 measured events, and 5 committed orders per run.

### Measurement and validity

The experiment defines application-event-to-confirmed-analytical-visibility
delay as:

```text
destination_visible_at(first_visible_batch) - activity_events.occurred_at
```

`activity_events.occurred_at` is generated in UTC by the application
immediately before source insertion. It is not a MariaDB commit timestamp.
`destination_visible_at` is captured immediately after the standalone DuckDB
commit returns, making it a conservative upper bound on confirmed first
visibility. This experiment does not measure commit-to-visibility latency,
replication latency, or database commit lag.

Every destination event retains its first-visible batch ID. Seed events at or
below the baseline checkpoint are excluded. Missing visibility metadata and
negative deltas are retained as invalid observations, with their reason and raw
value where available, and excluded from statistics. Each run ended with exact
controlled event/order reconciliation and convergence between the production
source analytical queries and their standalone DuckDB equivalents.

### Results

| Run | Measured events | Invalid | Committed orders | Feed cycles | Median (ms) | Nearest-rank p95 (ms) | Maximum (ms) | Convergence | Reconciliation |
|---:|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 01 | 67 | 0 | 5 | 2 | 1077.843 | 2010.052 | 2017.273 | Yes | Yes |
| 02 | 67 | 0 | 5 | 2 | 1103.486 | 2030.588 | 2037.122 | Yes | Yes |
| 03 | 67 | 0 | 5 | 2 | 1105.441 | 2034.297 | 2040.065 | Yes | Yes |
| 04 | 67 | 0 | 5 | 2 | 1100.389 | 2031.680 | 2038.503 | Yes | Yes |
| 05 | 67 | 0 | 5 | 2 | 1083.154 | 2010.604 | 2018.578 | Yes | Yes |

Pooling all 335 valid per-event observations produced:

- median: 1100.525 ms;
- nearest-rank p95: 2013.842 ms;
- maximum: 2040.065 ms;
- invalid observations: 0.

The pooled p95 is the nearest-rank percentile calculated across all 335 event
observations; it is not an average of the five per-run p95 values.

Across the five runs, the per-run ranges were:

| Statistic | Minimum (ms) | Maximum (ms) |
|---|---:|---:|
| Median | 1077.843 | 1105.441 |
| Nearest-rank p95 | 2010.052 | 2034.297 |
| Maximum | 2017.273 | 2040.065 |

### Timing nuance

In all five locked runs, both measured feed cycles completed after the
simulator completed. The 20 sessions at 0.1-second spacing placed workload
completion around the first two-second feed boundary: the simulator completed
while the first feed was executing. That feed copied 64 events and 5 orders;
the second scheduled feed copied the final 3 events and no orders. No cadence
overrun or missed interval occurred.

This timing does not change an event's measured delay to its first confirmed
destination visibility. Separately, the gated harness integration test
validated multiple feed cycles during an active simulator workload; its timing
values are not included in this final evidence.

### Interpretation and limits

Under this controlled two-second periodic-feed configuration, the separate
analytical store introduced an observed application-event-to-confirmed-
analytical-visibility delay of approximately 1.10 seconds at the median, 2.01
seconds at p95, and 2.04 seconds at maximum. All five runs eventually achieved
exact controlled-record reconciliation and analytical convergence.

A separate store can offer isolation and scaling benefits, but it requires a
data movement mechanism and makes freshness an explicit architectural concern.
These measurements do not establish production CDC performance, guaranteed
consistency, or values that generalize beyond this controlled setup. They also
do not establish that either architecture is universally faster or superior.

The retained JSON files record session, event, and committed-order counts plus
the complete visibility measurements, but not the simulator's internal outcome
categories. The permanent evidence therefore reports only workload facts that
can be independently recovered from those files.

The unchanged raw evidence is retained under
`experiments/separate_store/results/` as `staleness-run-01.json` through
`staleness-run-05.json`.

## Architectural findings

### Observed in the pinned environment

- InnoDB restored the complete tested checkout-shaped mutation after rollback.
- DuckDB event inserts were not visible inside the tested explicit transactions;
  rollback discarded the insert and commit made it visible.
- Normal rollback and commit produced aligned final states when one transaction
  touched both engines.
- The DuckDB engine accepted the tested duplicate declared primary-key value but
  rejected the tested event `CHECK` violation.
- The application stopped before checkout after the controlled product-view
  failure, while the earlier session-start event remained persisted.

### Architectural interpretation

- Authoritative mutable commerce state belongs in InnoDB, where order creation,
  item creation, price capture, and stock decrement share one transaction.
- DuckDB is suitable for accumulating behavioural history when the application
  does not treat its generated event ID as a uniqueness or deduplication
  guarantee.
- Event writes and checkout need explicit application ordering. The application
  accepts that an earlier behavioural event may persist even when a later event
  prevents checkout.
- The application should not depend on a cross-engine transaction as its
  checkout integrity boundary.

### Limitations

These experiments do not establish:

- crash consistency;
- guaranteed cross-engine atomicity;
- exactly-once delivery;
- recovery from arbitrary failures;
- universal behaviour across MariaDB or DuckDB storage-engine versions;
- performance superiority of either architecture.
