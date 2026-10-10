# Analytical engine comparison

This experiment measures the same production analytical queries against two
schemas on one MariaDB server. In the two-engine schema, `activity_events` uses
the MariaDB DuckDB storage engine; in the InnoDB-only schema, it uses InnoDB.
`products`, `campaigns`, `orders`, and `order_items` use InnoDB in both. This is
a query-time storage-engine comparison, separate from the [periodic-feed
freshness experiment](../README.md#separate-analytical-store-freshness) with a
standalone DuckDB destination.

## Dataset and fairness controls

The locked generator uses seed `17`. It creates one canonical logical dataset
per scale and loads it into both variants on the same disposable server. Each
scale used fresh storage. Notebook purchases use EUR 10 through session ordinal
2,500 and EUR 12 from ordinal 2,501, so the 5,000-session history is an exact
prefix of the 50,000-session history.

| Sessions | Events | Orders | Order items | Products | Campaigns | Dataset SHA256 |
|---:|---:|---:|---:|---:|---:|---|
| 5,000 | 17,401 | 1,021 | 1,342 | 3 | 2 | `61c41a001a918ae38a13db384aa7788087a3c40ab8333635a81ed73ab3e840de` |
| 50,000 | 174,868 | 10,064 | 13,244 | 3 | 2 | `29629ac545659fa802f4557d414c2107797f11ad5d86231681fc48be499b01e5` |

The queries are the existing dashboard SQL: [activity overview](../../dashboard/sql/overview_activity.sql)
counts session starts and product views; [recent activity](../../dashboard/sql/recent_activity.sql)
returns the latest 50 events; and [campaign performance](../../dashboard/sql/campaigns.sql)
combines session history with InnoDB purchases and revenue. Query parameters
and SQL are identical across variants. InnoDB has a session lookup index and a
recent-activity ordering index; its secondary index includes the primary key
as the final ordering column. The pinned MariaDB DuckDB plugin has no secondary
event index in this comparison because useful execution of one was not
demonstrated. The actual index and `EXPLAIN` details are retained in the JSON.

## Method and validity

Measurements ran on an ARM64 Mac with Docker Linux/aarch64, MariaDB
`12.3.3-MariaDB-ubu2404`, active MariaDB DuckDB plugin version `1.0`, and
PyMySQL distribution version `1.2.0`. Both variants were on the same MariaDB
server within each scale's run; no simulator workload ran during measurement.

Each query/variant pair had three discarded warmups and 20 measured executions
(`n=20`; 120 measured samples per scale). Each variant kept one persistent UTC
connection with the MariaDB query cache disabled. Query order was shuffled
deterministically per repetition, and the variant-first position alternated.
These are warm-cache observations. Client-observed `perf_counter_ns` timing
starts immediately before `cursor.execute()` and ends after `cursor.fetchall()`
has materialized the full result. Connection setup, `EXPLAIN`, result hashing,
loading, and JSON encoding are outside the timer.

The median is the middle value (mean of the two middle values for an even
sample). MAD is the median absolute deviation from that median; p95 uses the
nearest rank, `ceil(0.95 × n)`. The ratio below is InnoDB-only median divided
by two-engine median. It is descriptive, not a significance test.

Before and after timing, validation checked each canonical table's contents,
engine placement, and all three analytical answers across variants. Measured
result digests stayed stable across repetitions and variants. The source table
contents and analytical answers were unchanged between the two checks.

## Results

All times are milliseconds, with `n=20` per query and variant.

| 5,000 sessions | Variant | Median | p95 | Min | Max | MAD |
|---|---|---:|---:|---:|---:|---:|
| Activity overview | Two-engine | 3.823 | 4.187 | 3.510 | 4.307 | 0.195 |
| Activity overview | InnoDB-only | 5.040 | 5.383 | 4.886 | 5.699 | 0.082 |
| Recent activity | Two-engine | 3.250 | 3.538 | 3.084 | 3.691 | 0.127 |
| Recent activity | InnoDB-only | 0.537 | 0.581 | 0.424 | 0.672 | 0.028 |
| Campaign performance | Two-engine | 10.595 | 11.502 | 9.655 | 12.159 | 0.285 |
| Campaign performance | InnoDB-only | 23.226 | 24.007 | 22.690 | 25.287 | 0.221 |

| 50,000 sessions | Variant | Median | p95 | Min | Max | MAD |
|---|---|---:|---:|---:|---:|---:|---:|
| Activity overview | Two-engine | 19.970 | 22.195 | 19.232 | 24.112 | 0.293 |
| Activity overview | InnoDB-only | 62.879 | 67.252 | 59.143 | 68.715 | 3.215 |
| Recent activity | Two-engine | 7.239 | 7.455 | 6.692 | 15.168 | 0.153 |
| Recent activity | InnoDB-only | 0.661 | 0.729 | 0.468 | 0.752 | 0.051 |
| Campaign performance | Two-engine | 48.819 | 51.094 | 46.670 | 51.879 | 0.815 |
| Campaign performance | InnoDB-only | 241.528 | 244.863 | 236.245 | 244.918 | 1.501 |

| Query | InnoDB/two-engine at 5k | At 50k | Two-engine 50k/5k median | InnoDB-only 50k/5k median |
|---|---:|---:|---:|---:|
| Activity overview | 1.318 | 3.149 | 5.224× | 12.477× |
| Recent activity | 0.165 | 0.091 | 2.227× | 1.231× |
| Campaign performance | 2.192 | 4.947 | 4.608× | 10.399× |

In these measured configurations, the two-engine variant had lower median
latency for activity aggregation and campaign analysis at both scales. Its
descriptive ratio rose from 1.318 to 3.149 for overview and from 2.192 to
4.947 for campaigns. InnoDB-only had a substantially lower median for the
indexed recent-activity lookup at both scales (ratios 0.165 and 0.091). The
choice depends on the query workload.

The unchanged raw samples, correctness reports, SQL hashes, environment
metadata, indexes, and plans are retained in:

| Evidence | SHA256 |
|---|---|
| [5,000 sessions](results/benchmark-5000.json) | `16f507c13571d936506b79baaef2ef822bcda5181432121f5a7cf45885753fa8` |
| [50,000 sessions](results/benchmark-50000.json) | `641183b39d9c88f001006d76778a1adb51dc2dda4f870c21d09427cd98fd53f9` |

## Reproduce

Run from the repository root with Docker Compose available. Choose one scale
per fresh disposable Compose project; repeat the sequence with `scale=50000`
for the other scale. Supply two distinct temporary alphanumeric passwords:

```sh
scale=5000
benchmark_project="analytical-bench-${scale}-$(date +%s)"
export MARIADB_ROOT_PASSWORD='replace-with-a-temporary-root-password'
export MARIADB_DASHBOARD_PASSWORD='replace-with-a-different-temporary-password'
docker compose -p "$benchmark_project" up --build --wait --wait-timeout 120 mariadb

docker compose -p "$benchmark_project" run --rm -T --no-deps \
  --volume "$PWD:/workspace:ro" --workdir /workspace \
  --env MARIADB_USER=root --env MARIADB_PASSWORD="$MARIADB_ROOT_PASSWORD" \
  --entrypoint python dashboard -m experiments.analytical_benchmark prepare \
  --scale "$scale"

docker compose -p "$benchmark_project" run --rm -T --no-deps \
  --volume "$PWD:/workspace:ro" --workdir /workspace \
  --env MARIADB_USER=root --env MARIADB_PASSWORD="$MARIADB_ROOT_PASSWORD" \
  --entrypoint python dashboard -m experiments.analytical_benchmark validate \
  --scale "$scale"
```

Measure only after `validate` reports `"valid": true`. Use a new output name;
the CLI creates it exclusively and will not replace retained evidence:

```sh
local_output="experiments/analytical_benchmark/results/local-${scale}-$(date +%s).json"
docker compose -p "$benchmark_project" run --rm -T --no-deps \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD:/workspace" --workdir /workspace \
  --env MARIADB_USER=root --env MARIADB_PASSWORD="$MARIADB_ROOT_PASSWORD" \
  --env BENCHMARK_HOST_ARCHITECTURE="$(uname -m)" \
  --entrypoint python dashboard -m experiments.analytical_benchmark measure \
  --scale "$scale" --output "$local_output"

docker compose -p "$benchmark_project" run --rm -T --no-deps \
  --volume "$PWD:/workspace:ro" --workdir /workspace \
  --entrypoint python dashboard -m experiments.analytical_benchmark summarize \
  --input "$local_output"

docker compose -p "$benchmark_project" down --volumes --remove-orphans
unset MARIADB_ROOT_PASSWORD MARIADB_DASHBOARD_PASSWORD
```

`prepare` refuses to reuse either target schema. Reset only the disposable
project's volume before a repeated run. The commands above do not start the
simulator or publish MariaDB's port.

Only two scales, warm-cache reads, these SQL queries, this index configuration,
and this hardware were measured. These client-observed latencies do not establish
statistical significance, cold-cache or concurrent performance, production
throughput, asymptotic complexity, or performance on other hardware and data.
The MariaDB DuckDB engine here is not a standalone DuckDB store; the separate
store experiment measures freshness and convergence rather than this same-server
query latency.
