A project for area 20, "An application that proves the two-engine pattern" for the MariaDB student database projects, 2026-09.

# Real-Time Commerce & Campaign Analytics

A Constructor University project investigating when operational and analytical workloads benefit from different storage engines inside one MariaDB server.

The application includes five tables, deterministic commerce activity, a continuous session simulator, and a campaign conversion/revenue query combining DuckDB session history with InnoDB purchases. InnoDB owns products, campaigns, orders, and order items; DuckDB owns `activity_events`. Checkout stays entirely inside InnoDB.

The original foundation is preserved in Git at `v0.1.0`. The read-only Flask dashboard combines live operational and behavioural observations. Performance comparisons and cross-engine transaction/staleness experiments remain future work. Functionality has been demonstrated; performance superiority has not.

See [the schema guide](docs/schema.md) for relationships, integrity boundaries, and observed plugin limitations.

## Prerequisites

- Docker and Compose running Linux containers:
  - Apple Silicon Mac: Docker Desktop (`linux/arm64`).
  - Intel/AMD Linux: Docker Engine and Compose (`linux/amd64`).
  - Windows on Intel/AMD: Docker Desktop with WSL2 integration (`linux/amd64`).
  - Intel Mac: a supported Docker Desktop/macOS environment for Linux AMD64 containers.
- Internet access for image/package downloads and a local clone of this repository.
- No host MariaDB installation is needed.

The ARM64 foundation workflow and application schema/seed/verification have passed locally on Apple Silicon. The AMD64 image build passed under emulation, including checksum and package installation checks; AMD64 database runtime and SQL execution remain unverified.

Run shell commands in your **Mac terminal**, **Linux terminal**, or **WSL Linux shell on Windows**, from the repository root. SQL clients run inside the container. Use WSL rather than PowerShell for the input redirection shown below.

```sh
cd /path/to/mariadb-two-engine-pattern
docker version
docker compose version
```

Use your actual clone path, such as `~/Uni/mariadb-two-engine-pattern`. If Docker is missing from PATH on macOS, run `export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"` and repeat the checks. On Windows, enable Docker Desktop integration for your WSL distribution. Confirm that Docker reports both client and server.

## Fresh-storage setup

Only on first setup, copy the credential template:

```sh
cp .env.example .env
```

Set distinct, nonempty local development `MARIADB_ROOT_PASSWORD` and `MARIADB_DASHBOARD_PASSWORD` values in `.env`; a random alphanumeric value avoids interpolation issues. Keep your existing `.env` on subsequent runs. Git ignores it. Changing this value does not reset a password already initialized in a persistent volume.

For the transition from the old foundation or a deliberate clean rerun, the following command **deletes this Compose stack's database volume and all its data**. Skip it if no prior stack exists or you intend to keep your data:

```sh
docker compose down --volumes
```

There is no automatic migration or deletion of `engine_proof`. The current schema creates a separate database, `commerce_analytics`. The fresh-volume workflow removes old data only through the explicit reset above.

Build and start MariaDB:

```sh
docker compose up --build --wait --wait-timeout 120 mariadb
docker compose ps
```

Expect `healthy`. If startup fails, inspect `docker compose logs mariadb` before continuing. Check server and plugin availability:

```sh
docker compose exec -T mariadb sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb --user=root --protocol=socket --table --execute="SELECT VERSION(); SHOW ENGINES;"'
```

Expect `12.3.3-MariaDB-ubu2404` and `DUCKDB` support `YES`.

Run these commands **in order**, checking each result before continuing:

```sh
docker compose exec -T mariadb sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb --user=root --protocol=socket --default-character-set=utf8mb4 --show-warnings' < sql/01-schema.sql
```

```sh
docker compose exec -T mariadb sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb --user=root --protocol=socket --default-character-set=utf8mb4 --show-warnings' < sql/02-seed.sql
```

```sh
docker compose exec -T mariadb sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb --user=root --protocol=socket --default-character-set=utf8mb4 --table --show-warnings' < sql/03-verify.sql
```

Schema and seed are one-time scripts for an empty application database; they are not idempotent and do not run automatically at startup. Do not use client `--force` to continue after SQL errors. The seed prints product IDs from its locking reads. Verification is read-only and can be repeated; inspect its results, since unexpected counts do not automatically cause a nonzero client exit.

## Expected results

| Table | Engine | Seed rows |
| --- | --- | ---: |
| products | InnoDB | 3 |
| campaigns | InnoDB | 2 |
| orders | InnoDB | 3 |
| order_items | InnoDB | 4 |
| activity_events | DUCKDB | 11 |

Five sessions produce three purchases. Final stock is 98 notebooks, 95 pens, and 99 mugs. The notebook's catalog price is EUR 12.00 while its purchase-time price remains EUR 10.00. Total historical revenue is EUR 38.00. Engine/stock/price matches must be `1`, and logical-integrity violation counts must be `0`.

For session starts on 2026-09-13 in `[12:00, 12:05)` UTC, observing purchases through 12:10 UTC inclusive:

| Attribution | Current campaign state | Sessions | Purchasing sessions | Conversion (%) | Revenue (EUR) |
| --- | --- | ---: | ---: | ---: | ---: |
| Campus Launch | Active | 2 | 1 | 50.00 | 26.00 |
| Study Week | Paused | 1 | 1 | 100.00 | 8.00 |
| Organic | — | 2 | 1 | 50.00 | 4.00 |

## Run the dashboard

After loading schema and seed:

```sh
docker compose up --build -d dashboard
```

Open [http://localhost:5000/](http://localhost:5000/). The four read-only endpoints are `/api/overview`, `/api/inventory`, `/api/activity`, and `/api/campaigns`. Panels refresh five seconds after reads finish; they are independent observations, not an atomic snapshot. The dashboard works without the simulator.

The dashboard image uses the same pinned Python base as the simulator, installs `dashboard/requirements.txt`, and runs Gunicorn as container user `10001:10001`. Only HTTP port 5000 is published, bound to localhost. MariaDB remains internal. Inspect startup failures with `docker compose logs dashboard`.

On fresh storage, `docker/mariadb-init/01-dashboard-user.sh` creates `dashboard` with SELECT access only to `commerce_analytics`. It does not create the application database or load fixtures. The dashboard receives only its own password; simulator credentials remain unchanged.

For an existing volume created before dashboard provisioning, add `MARIADB_DASHBOARD_PASSWORD` to your existing `.env`, rebuild and recreate the MariaDB container to supply the variable and initialization hook, then provision the account once:

```sh
docker compose up --build --wait --wait-timeout 120 mariadb
docker compose exec -T mariadb sh /docker-entrypoint-initdb.d/01-dashboard-user.sh
docker compose up --build -d dashboard
```

Do not rerun that provisioning command if the account already exists: it deliberately fails rather than replacing an account or silently retaining different permissions. Changing `.env` alone does not change an existing database password. Fresh-storage reset instructions above remain available for a deliberate clean rerun.

## Run the simulator

After loading schema and seed, run the companion service from the repository root:

```sh
docker compose build simulator
docker compose run --rm simulator --seed 17 --sessions 20 --session-interval 0.1
```

For continuous sessions at the default three-second interval, omit `--sessions` and `--session-interval`. Stop with Ctrl+C. The `simulator` profile keeps it out of ordinary `docker compose up`; explicitly naming it with `run` enables it. MariaDB must be healthy before it starts, but health does not imply the schema has been loaded.

The simulator uses the internal `mariadb:3306` endpoint; no host port is published. Its separate Python image installs `PyMySQL==1.2.0` and copies the simulator source. Rebuild after source changes. It uses the existing local root credential from `.env` while the dashboard uses a separate SELECT-only account. Simulator privileges and schema constraints are unchanged. Passwords are passed at runtime, not baked into the image.

Simulation changes stock and adds events/orders, so the fixed counts in `03-verify.sql` apply only before simulation. To preserve a development fixture, use a separate project: add `-p commerce-simulator-demo` to **every** Compose setup, SQL, build, run, and cleanup command. That project receives its own database volume. When finished, `docker compose -p commerce-simulator-demo down --volumes` deletes only that project's data.

The behavioural seed reproduces choices against the same inputs; UUIDs and timestamps remain fresh. Inventory is finite and is not replenished automatically. Checkout remains entirely in InnoDB; event-write failures end the affected session without retrying it.

## Stop and resume

```sh
docker compose stop
```

The named volume retains data. Resume MariaDB with the earlier `up` command and the dashboard with `docker compose up -d dashboard`. Run **only `03-verify.sql`** against an existing deterministic seeded database. To start over, use the deliberate volume reset and all three SQL scripts.

## Runtime pins and storage

- Platforms: `linux/arm64` and `linux/amd64`; Compose uses the Docker host's native Linux architecture.
- Base image: `mariadb:12.3.3-noble@sha256:ab1c3dd381940233af12512b97d47b508fd3a0f17fbe3ba388739b7bc17cbc0b` (official MariaDB, Ubuntu 24.04).
- Plugin package: `mariadb-plugin-duckdb=1:12.3.3+maria~ubu2404` from the MariaDB 12.3.3 release archive.
- Downloaded package: `mariadb-plugin-duckdb_1%3a12.3.3+maria~ubu2404_<architecture>.deb`, selected using `dpkg --print-architecture`.
- Pinned ARM64 SHA256: `8860a23a0bcaaf7f3f85d8e296f75fda27026ec9a46e9e4acbe1d832643f72bb`.
- Pinned AMD64 SHA256: `f96044a1000b20fd9f95e2ed22e4ebcab7e29f9f653c33588c12d2294d4660cc`.
- Local image: `mariadb-two-engine:12.3.3`, built from `Dockerfile`.
- Startup options: `--plugin-maturity=alpha` and `--plugin-load-add=ha_duckdb.so`. The tested plugin reports `ACTIVE` and maturity `Gamma`.

The server and plugin are prebuilt binaries; no source compilation is needed. The base image and plugin are pinned, but additional system dependencies resolve from the configured repositories, so builds are not guaranteed to be byte-identical.

Compose persists database files at `/var/lib/mysql` in the named `mariadb-data` volume (normally `mariadb-two-engine-pattern_mariadb-data`). No MariaDB host port is published; use `docker compose exec` for database access. Dashboard HTTP is available only on `127.0.0.1:5000`.

The [MariaDB 12.3.3 engine source](https://github.com/MariaDB/server/tree/mariadb-12.3.3/storage/duckdb) and official [AMD64](https://archive.mariadb.org/mariadb-12.3.3/repo/ubuntu/dists/noble/main/binary-amd64/Packages.gz) and [ARM64 package indexes](https://archive.mariadb.org/mariadb-12.3.3/repo/ubuntu/dists/noble/main/binary-arm64/Packages.gz) identify the runtime used here.
