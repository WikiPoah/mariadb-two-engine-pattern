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
  mariadb --user=root commerce_analytics < sql/01-schema.sql
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
