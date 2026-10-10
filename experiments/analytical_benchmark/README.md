# Analytical engine comparison setup

This experiment prepares equal commerce and activity data for the same dashboard queries against two disposable MariaDB schemas. `analytical_bench_<sessions>_two_engine` keeps `activity_events` in MariaDB's DuckDB engine; `analytical_bench_<sessions>_innodb_only` stores it in InnoDB. The four commerce tables use InnoDB in both.

The locked generator uses seed `17` and supports 5,000 or 50,000 sessions. Each pair is generated once and loaded into both schemas. Notebook purchases use €10 through session ordinal 2,500 and €12 from ordinal 2,501 onward, regardless of the selected scale, so the smaller history is a prefix of the larger one. The InnoDB event table has a session lookup index and an ordering index matching the production recent-activity order; InnoDB's secondary index includes the primary key as its final tie-breaker. DuckDB index parity is not assumed.

Run only on a fresh, disposable Compose project. Export the same temporary root credentials used to start MariaDB; do not point this setup at the normal development volume. For example, after starting the project's `mariadb` service under a unique Compose project name, run the existing dashboard image as a transient Python client on that project's network:

```sh
docker compose -p "$BENCHMARK_PROJECT" run --rm \
  --volume "$PWD:/workspace:ro" --workdir /workspace \
  --env MARIADB_USER=root \
  --env MARIADB_PASSWORD="$MARIADB_ROOT_PASSWORD" \
  --entrypoint python dashboard -m experiments.analytical_benchmark prepare

docker compose -p "$BENCHMARK_PROJECT" run --rm \
  --volume "$PWD:/workspace:ro" --workdir /workspace \
  --env MARIADB_USER=root \
  --env MARIADB_PASSWORD="$MARIADB_ROOT_PASSWORD" \
  --entrypoint python dashboard -m experiments.analytical_benchmark validate
```

Use `--scale 5000` or `--scale 50000` to select one pair. `prepare` refuses to reuse any target schema; discard the disposable MariaDB volume and start fresh before repeating it. `validate` checks engine placement, every canonical table row, and the existing activity overview, recent activity (50 rows), and campaign analytics answers. It exits unsuccessfully on a mismatch. No query timings are collected by these commands.

## Measurement protocol

The measurement command accepts only a locked scale and runs the paired correctness validation before and after timing. It uses one persistent MariaDB connection per variant, sets each session to UTC, disables the MariaDB query cache, performs three discarded warmups and records 20 measured executions for each production query and variant. Query order is deterministically shuffled per repetition, and the variant-first position alternates. The interpretation is warm-cache only.

`elapsed_ns` is client-observed time from immediately before `cursor.execute()` until `cursor.fetchall()` has fully materialized the result. Connection setup, `EXPLAIN`, result digesting, dataset loading, and JSON serialization are outside that interval. Evidence retains each raw sample and result digest, SQL hashes, environment/session metadata, index and `EXPLAIN` diagnostics, correctness reports, and median, minimum, maximum, median absolute deviation, and nearest-rank p95 summaries. EXPLAIN output is diagnostic evidence; engine plan costs are not directly comparable.

After review authorizes measurements, capture stdout to a new host-side file. Shell noclobber prevents replacing existing evidence:

```sh
set -o noclobber
docker compose -p "$BENCHMARK_PROJECT" run --rm \
  --volume "$PWD:/workspace:ro" --workdir /workspace \
  --env MARIADB_USER=root \
  --env MARIADB_PASSWORD="$MARIADB_ROOT_PASSWORD" \
  --entrypoint python dashboard -m experiments.analytical_benchmark measure \
  --scale 5000 > experiments/analytical_benchmark/results-5000.json

docker compose -p "$BENCHMARK_PROJECT" run --rm \
  --volume "$PWD:/workspace:ro" --workdir /workspace \
  --entrypoint python dashboard -m experiments.analytical_benchmark summarize \
  --input experiments/analytical_benchmark/results-5000.json
```

No performance measurements or result files have been collected. Any future findings apply only to these queries, scales, indexes, warm-cache conditions, and recorded environment; median ratios are descriptive and do not establish significance or general engine superiority.
