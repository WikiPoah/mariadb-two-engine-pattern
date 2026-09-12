A project for area 20, "An application that proves the two-engine pattern" for the MariaDB student database projects, 2026-09.

# MariaDB two-engine pattern

A Constructor University project exploring operational and analytical workloads on different storage engines within one MariaDB server.

Phase 1 establishes the technical foundation: MariaDB 12.3.3 on Linux, an InnoDB `products` table, a DuckDB `sales` table, deterministic inserts, independent reads, and one cross-engine SQL JOIN. These tiny tables are a technical proof, not the final application schema.

InnoDB is the provisional operational engine because its transactions, row-level locking, and crash recovery suit frequent transactional writes. DuckDB provides the analytical storage engine inside the same server. Phase 1 proves functionality; it does not yet establish performance benefits or cross-engine transaction semantics.

## Prerequisites

- Docker with Compose, running Linux containers on one of these hosts:
  - Apple Silicon Mac: Docker Desktop (`linux/arm64`).
  - Intel/AMD Linux: Docker Engine and Compose (`linux/amd64`).
  - Windows on Intel/AMD: Docker Desktop with WSL2 integration (`linux/amd64`).
  - Intel Mac: a Docker Desktop/macOS combination that supports Linux AMD64 containers.
- Internet access for the initial image build and package downloads.
- This repository cloned locally. No host MariaDB installation is required.

The updated Dockerfile and complete ARM64 runtime workflow have been verified locally on Apple Silicon. The AMD64 image build has also passed under emulation, including the pinned plugin checksum, package installation, and image architecture check. AMD64 runtime execution has not yet been verified; plugin loading and the SQL proof still need to be tested on AMD64.

Run all shell commands below in your **Mac terminal**, **Linux terminal**, or **WSL Linux shell on Windows**, from the repository root. Use the WSL shell rather than PowerShell for the SQL input redirection. The commands invoke the MariaDB client inside the container.

```sh
cd /path/to/mariadb-two-engine-pattern
docker version
docker compose version
```

Use your actual clone path (for example, `~/Uni/mariadb-two-engine-pattern`). On macOS, if `docker` is not on PATH, run `export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"` and repeat the checks. On Windows, enable Docker Desktop integration for your WSL distribution. Check that `docker version` reports both a client and a running server.

## Start and run the proof

On first setup, copy the credential template:

```sh
cp .env.example .env
```

Edit `.env` and give `MARIADB_ROOT_PASSWORD` a nonempty local development password. A random alphanumeric password avoids Compose interpolation issues. `.env` is ignored by Git; keep the existing file on subsequent runs. The password initializes fresh database storage: changing `.env` later does not change an existing database's root password.

Build and start the service, waiting for its health check:

```sh
docker compose up --build --wait --wait-timeout 120
docker compose ps
```

The `mariadb` service should report `healthy`. If startup fails, inspect `docker compose logs mariadb` before proceeding. The health check confirms database readiness; verify DuckDB explicitly:

```sh
docker compose exec -T mariadb sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb --user=root --protocol=socket --table --execute="SELECT VERSION(); SHOW ENGINES;"'
```

Expect version `12.3.3-MariaDB-ubu2404` and `DUCKDB` support `YES`.

Run setup **once per fresh database volume**:

```sh
docker compose exec -T mariadb sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb --user=root --protocol=socket' < sql/01-foundation.sql
```

Setup creates `engine_proof`, its two tables, and sample rows. It deliberately fails if the database already exists. SQL is not run automatically at startup.

Run verification whenever needed:

```sh
docker compose exec -T mariadb sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb --user=root --protocol=socket --table' < sql/02-verify.sql
```

This reads both tables, checks their actual storage engines, and executes the JOIN. Expected engine mapping:

| Table | Engine |
| --- | --- |
| products | InnoDB |
| sales | DUCKDB |

Expected cross-engine JOIN result:

| sale_id | name | quantity |
| --- | --- | --- |
| 1 | Notebook | 2 |
| 2 | Pen | 5 |
| 3 | Notebook | 1 |

## Stop or reset

Stop the stack while preserving its data:

```sh
docker compose stop
```

Start it again with the earlier `up` command, then run verification directly; do not repeat setup on the existing database.

To deliberately reset, the following command **deletes the database volume and all proof data**:

```sh
docker compose down --volumes
```

After a reset, start the stack and run setup followed by verification again.

## Runtime pins and storage

- Platforms: `linux/arm64` and `linux/amd64`; Compose uses the Docker host's native Linux architecture.
- Base image: `mariadb:12.3.3-noble@sha256:ab1c3dd381940233af12512b97d47b508fd3a0f17fbe3ba388739b7bc17cbc0b` (official MariaDB, Ubuntu 24.04).
- Plugin package: `mariadb-plugin-duckdb=1:12.3.3+maria~ubu2404` from the MariaDB 12.3.3 release archive.
- Downloaded package: `mariadb-plugin-duckdb_1%3a12.3.3+maria~ubu2404_<architecture>.deb`, selected using `dpkg --print-architecture`.
- Pinned ARM64 SHA256: `8860a23a0bcaaf7f3f85d8e296f75fda27026ec9a46e9e4acbe1d832643f72bb`.
- Pinned AMD64 SHA256: `f96044a1000b20fd9f95e2ed22e4ebcab7e29f9f653c33588c12d2294d4660cc`.
- Local image: `mariadb-two-engine:phase1`, built from `Dockerfile`.
- Startup options: `--plugin-maturity=alpha` and `--plugin-load-add=ha_duckdb.so`. The tested plugin reports `ACTIVE` and maturity `Gamma`.

The server and plugin are prebuilt binaries; no source compilation is needed. The base image and plugin are pinned, but additional system dependencies resolve from the configured repositories, so builds are not guaranteed to be byte-identical.

Compose persists database files at `/var/lib/mysql` in the named `mariadb-data` volume (normally `mariadb-two-engine-pattern_mariadb-data`). No host port is published; use `docker compose exec` for access.

The [MariaDB 12.3.3 engine source](https://github.com/MariaDB/server/tree/mariadb-12.3.3/storage/duckdb) and official [AMD64](https://archive.mariadb.org/mariadb-12.3.3/repo/ubuntu/dists/noble/main/binary-amd64/Packages.gz) and [ARM64 package indexes](https://archive.mariadb.org/mariadb-12.3.3/repo/ubuntu/dists/noble/main/binary-arm64/Packages.gz) identify the runtime used here. There is no simulator, API, dashboard, or benchmark framework in Phase 1.
