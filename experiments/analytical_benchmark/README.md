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
