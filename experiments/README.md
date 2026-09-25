# Engine experiments

This directory contains small, repeatable experiments that examine how the
pinned MariaDB server behaves when InnoDB and DuckDB are used together.

Each experiment separates three kinds of statements:

- **Observed behaviour** is what the disposable MariaDB 12.3.3 environment
  actually returned.
- **Documented guarantees** are claims supported by MariaDB documentation.
- **Architectural conclusions** are decisions we draw for this application
  from the observed evidence and documented guarantees.

Run experiments only against a disposable Compose project with a fresh named
volume. Do not use the normal development volume.

## InnoDB rollback

`sql/01-innodb-rollback.sql` uses the seeded `commerce_analytics` database.
It records the seeded baseline, performs a temporary order, item, and stock
mutation inside one InnoDB transaction, shows that uncommitted state, then
rolls the transaction back and shows the restored state. The script does not
claim an outcome until it has been executed.

From the repository root, set temporary credentials outside the repository and
choose a unique Compose project name:

```sh
docker_bin=/Applications/Docker.app/Contents/Resources/bin/docker
project=innodb-rollback-$(date +%s)
export MARIADB_ROOT_PASSWORD='temporary-root-password'
export MARIADB_DASHBOARD_PASSWORD='temporary-dashboard-password'

"$docker_bin" compose -p "$project" up -d mariadb
until "$docker_bin" compose -p "$project" exec -T mariadb \
  healthcheck.sh --connect --innodb_initialized; do sleep 2; done

"$docker_bin" compose -p "$project" exec -T \
  -e MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb \
  mariadb --user=root < sql/01-schema.sql
"$docker_bin" compose -p "$project" exec -T \
  -e MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb \
  mariadb --user=root commerce_analytics < sql/02-seed.sql
"$docker_bin" compose -p "$project" exec -T \
  -e MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb \
  mariadb --user=root commerce_analytics < experiments/sql/01-innodb-rollback.sql
```

The SQL output is the experiment evidence. Clean up only the disposable
project when finished:

```sh
"$docker_bin" compose -p "$project" down --volumes --remove-orphans
unset MARIADB_ROOT_PASSWORD MARIADB_DASHBOARD_PASSWORD
```

## DuckDB-engine rollback

`sql/02-duckdb-rollback.sql` asks whether the MariaDB DuckDB storage engine
participates in transaction control for the existing `activity_events` table.
It contains a rollback case and a separate commit control using different
temporary events. The commit control is needed because an event that is
invisible before `ROLLBACK` does not by itself demonstrate rollback semantics.
The script reports each checkpoint and does not claim an outcome before
execution.

Choose a new project name before starting MariaDB and reuse the same `$project`
value for startup, health checks, schema/seed loading, this execution, fresh
connection verification, and cleanup. Repeat the same setup commands above.
After loading `sql/02-seed.sql`, execute:

```sh
"$docker_bin" compose -p "$project" exec -T \
  -e MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb \
  mariadb --user=root commerce_analytics < experiments/sql/02-duckdb-rollback.sql
```

Open a new MariaDB connection afterwards and query the total event count plus
both temporary session IDs again. Clean up this disposable project with the
same `down --volumes --remove-orphans` command shown above.

## Cross-engine transaction

`sql/03-cross-engine-transaction.sql` uses one MariaDB connection to modify
InnoDB commerce state and the DuckDB-backed event table within the same
transaction. Separate rollback and commit-control cases report each engine's
state before, during, and after transaction completion without assuming that
the engines have identical visibility or atomicity semantics.

Choose a new project name before starting MariaDB and reuse the same `$project`
value for startup, health checks, schema/seed loading, this execution, fresh
connection verification, and cleanup. Run this script only after loading the
unchanged schema and seed:

```sh
"$docker_bin" compose -p "$project" exec -T \
  -e MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb \
  mariadb --user=root commerce_analytics \
  < experiments/sql/03-cross-engine-transaction.sql
```

The commit control deliberately leaves its temporary order, item, stock
change, and event in the disposable database as evidence. Do not delete those
rows individually; destroy the disposable project and volume after recording
the fresh-connection result.

## Integrity enforcement comparison

**Question:** Which declared constraints relevant to this application are
actually enforced by InnoDB and the MariaDB DuckDB storage engine in the
pinned runtime?

**Procedure:** Five small scripts each attempt one violation against an existing
application table:

- `sql/04a-innodb-duplicate-pk.sql`
- `sql/04b-duckdb-duplicate-pk.sql`
- `sql/04c-innodb-foreign-key.sql`
- `sql/04d-innodb-check.sql`
- `sql/04e-duckdb-check.sql`

Run every script in its own new disposable Compose project and volume. Before
the mutation, use a separate MariaDB client to record the relevant table count,
key or probe-row count, and engine. Run the script in a second client and record
its exit status plus any error or warning. Regardless of that outcome, use a
new connection to inspect the durable state. Do not delete accepted probe rows;
destroy the case's disposable project and volume before starting the next one.
This separation prevents an unexpected engine or commit result from affecting
another case.

The real DuckDB-backed schema declares no foreign key. Its campaign and product
references are logical, so an equivalent DuckDB FK probe would test a schema
the application does not use.

**Observation:** Record the baseline, mutation diagnostic, and fresh-connection
state for each case. These instructions do not predict the outcomes.

**Interpretation:** Compare the observed runtime behavior with each table's
declared constraints. Treat the output as evidence for the pinned MariaDB
12.3.3 environment, not as a universal storage-engine guarantee.

For each case, choose a new unique project name before starting MariaDB and
reuse it for startup, health checks, schema/seed loading, baseline inspection,
the single mutation, fresh-connection verification, and cleanup. Confirm
MariaDB 12.3.3 and the active DuckDB plugin, then load the unchanged schema and
seed. Execute one case, for example:

```sh
"$docker_bin" compose -p "$project" exec -T \
  -e MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb \
  mariadb --user=root commerce_analytics --show-warnings \
  < experiments/sql/04a-innodb-duplicate-pk.sql
```

An expected rejection makes that client command nonzero; capture its status and
diagnostic before opening the fresh verification connection. Use the same
`down --volumes --remove-orphans` cleanup shown above for every case and verify
that its containers and volume are gone before continuing.
