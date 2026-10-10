"""Warm-cache client latency measurements for the locked analytical queries."""

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import date, datetime, timezone
from decimal import Decimal
from importlib.metadata import (
    PackageNotFoundError,
    version as distribution_version,
)
from pathlib import Path

from experiments.analytical_benchmark.database import (
    SCHEMA_PATH,
    VARIANTS,
    database_name,
    rows_digest,
    validate_pair,
)
from experiments.analytical_benchmark.dataset import SEED, SCALES, generate_dataset
from simulator.database import open_connection


METHOD_SCHEMA_VERSION = "1.0"
WARMUP_COUNT = 3
MEASURED_REPETITIONS = 20
RECENT_ACTIVITY_LIMIT = 50
QUERY_FILES = {
    "overview_activity": "overview_activity.sql",
    "recent_activity": "recent_activity.sql",
    "campaigns": "campaigns.sql",
}
QUERY_PARAMETERS = {"overview_activity": None, "recent_activity": (RECENT_ACTIVITY_LIMIT,),
                    "campaigns": None}
SQL_DIRECTORY = Path(__file__).resolve().parents[2] / "dashboard" / "sql"


def _installed_distribution_version(distribution_name):
    try:
        return distribution_version(distribution_name)
    except PackageNotFoundError:
        return None


def median(values):
    """Return an exact Decimal median for integer nanosecond observations."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("median requires at least one value")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return Decimal(ordered[middle])
    return (Decimal(ordered[middle - 1]) + Decimal(ordered[middle])) / 2


def minimum(values):
    if not values:
        raise ValueError("minimum requires at least one value")
    return min(values)


def maximum(values):
    if not values:
        raise ValueError("maximum requires at least one value")
    return max(values)


def median_absolute_deviation(values):
    """Return median(abs(sample - median(sample))) without rounding half ns."""
    center = median(values)
    return median([abs(Decimal(value) - center) for value in values])


def nearest_rank_p95(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("p95 requires at least one value")
    rank = (95 * len(ordered) + 99) // 100
    return ordered[rank - 1]


def summarize_samples(samples):
    summary = {}
    for query_name in QUERY_FILES:
        summary[query_name] = {}
        for variant in VARIANTS:
            values = [sample["elapsed_ns"] for sample in samples
                      if sample["query"] == query_name and sample["variant"] == variant]
            if not values:
                raise ValueError(f"no samples for {query_name}/{variant}")
            summary[query_name][variant] = {
                "n": len(values),
                "median_ns": median(values),
                "min_ns": minimum(values),
                "max_ns": maximum(values),
                "mad_ns": median_absolute_deviation(values),
                "p95_ns": nearest_rank_p95(values),
            }
    return summary


def measured_order(repetition):
    """Build a deterministic query shuffle and alternating variant order."""
    import random

    query_order = list(QUERY_FILES)
    random.Random(SEED + repetition).shuffle(query_order)
    variant_order = list(VARIANTS)
    if repetition % 2:
        variant_order.reverse()
    return variant_order, query_order


def dataset_digest(dataset):
    digest = hashlib.sha256()
    for table, rows in sorted(dataset.table_rows().items()):
        digest.update(table.encode("utf-8"))
        digest.update(b"\0")
        digest.update(rows_digest(rows).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def result_digest(rows):
    """Digest complete ordered SQL rows using the repository's canonical encoding."""
    return rows_digest(rows)


def json_value(value):
    """Convert database and statistic values to lossless JSON representations."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"cannot encode {type(value).__name__}")


def _git_metadata():
    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, check=True,
            capture_output=True, text=True, timeout=2,
        ).stdout.strip())
        return {"commit": commit, "working_tree_dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "working_tree_dirty": None}


def _container_limits():
    def read(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None

    return {
        "cpu_max_cgroup_v2": read("/sys/fs/cgroup/cpu.max"),
        "memory_max_cgroup_v2": read("/sys/fs/cgroup/memory.max"),
    }


def _dict_rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _set_and_capture_session(connection):
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION time_zone = '+00:00'")
        cursor.execute("SET SESSION query_cache_type = OFF")
        cursor.execute("SELECT @@session.time_zone, @@session.query_cache_type")
        timezone_value, query_cache_type = cursor.fetchone()
        if timezone_value != "+00:00":
            raise RuntimeError(f"measurement connection timezone is {timezone_value!r}")
        if str(query_cache_type).upper() not in {"OFF", "0"}:
            raise RuntimeError("MariaDB query cache must be OFF for this protocol")
        cursor.execute(
            "SHOW SESSION VARIABLES WHERE Variable_name IN "
            "('time_zone', 'query_cache_type', 'optimizer_switch')"
        )
        session_variables = _dict_rows(cursor)
    return session_variables


def _capture_environment(connection, session_variables):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT VERSION(), @@version_comment, @@version_compile_machine, "
            "@@version_compile_os, DATABASE()"
        )
        version, comment, machine, operating_system, database = cursor.fetchone()
        cursor.execute(
            "SELECT PLUGIN_NAME, PLUGIN_VERSION, PLUGIN_STATUS, PLUGIN_LIBRARY "
            "FROM information_schema.PLUGINS WHERE PLUGIN_NAME IN "
            "('DUCKDB', 'RUN_IN_DUCKDB') ORDER BY PLUGIN_NAME"
        )
        plugins = _dict_rows(cursor)
        cursor.execute(
            "SHOW GLOBAL VARIABLES WHERE Variable_name IN "
            "('query_cache_type', 'query_cache_size', 'optimizer_switch', "
            "'innodb_buffer_pool_size')"
        )
        global_variables = _dict_rows(cursor)
    return {
        "database": database,
        "mariadb": {
            "version": version,
            "version_comment": comment,
            "compile_machine": machine,
            "compile_os": operating_system,
            "plugins": plugins,
            "global_variables": global_variables,
        },
        "session_variables": session_variables,
    }


def _load_queries():
    queries = {}
    for name, filename in QUERY_FILES.items():
        path = SQL_DIRECTORY / filename
        content = path.read_bytes()
        queries[name] = {
            "sql": content.decode("utf-8"),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    return queries


def _explain_sql(sql):
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return "EXPLAIN " + "\n".join(lines).strip().rstrip(";").strip()


def _diagnostics(connection, queries):
    with connection.cursor() as cursor:
        cursor.execute("SHOW INDEX FROM activity_events")
        indexes = _dict_rows(cursor)
        explains = {}
        for name, query in queries.items():
            parameters = QUERY_PARAMETERS[name]
            cursor.execute(_explain_sql(query["sql"]), parameters)
            explains[name] = _dict_rows(cursor)
    return {"indexes": indexes, "explain": explains}


def _run_query(connection, sql, parameters, clock_ns):
    # Cursor construction is outside the timing boundary; the clock brackets only
    # execute plus full fetchall materialization.
    with connection.cursor() as cursor:
        started = clock_ns()
        cursor.execute(sql, parameters)
        rows = cursor.fetchall()
        elapsed = clock_ns() - started
    return rows, elapsed


def _validation_ok(report):
    return isinstance(report, dict) and report.get("valid") is True


def measure_scale(config, scale, *, connection_factory=None,
                  validation_function=None, clock_ns=None):
    """Validate and measure one locked scale; callers save the returned evidence."""
    if type(scale) is not int or scale not in SCALES:
        raise ValueError(f"scale must be one of the locked scales: {SCALES}")
    connection_factory = connection_factory or open_connection
    validation_function = validation_function or validate_pair
    clock_ns = clock_ns or time.perf_counter_ns
    run_started_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    queries = _load_queries()
    dataset = generate_dataset(scale)

    pre_validation = validation_function(config, scales=(scale,))
    if not _validation_ok(pre_validation):
        raise RuntimeError("paired correctness validation failed before measurement")

    samples = []
    diagnostics = {}
    variant_environment = {}
    repetition_orders = []
    query_digests = {name: set() for name in QUERY_FILES}
    execution_sequence = 0

    with ExitStack() as stack:
        connections = {}
        for variant in VARIANTS:
            variant_config = {
                **config,
                "database": database_name(scale, variant),
            }
            connections[variant] = stack.enter_context(connection_factory(variant_config))

        for variant, connection in connections.items():
            session_variables = _set_and_capture_session(connection)
            variant_environment[variant] = _capture_environment(
                connection, session_variables
            )
            diagnostics[variant] = _diagnostics(connection, queries)

        # Warmups are separate, discarded executions and never enter samples/stats.
        for variant in VARIANTS:
            connection = connections[variant]
            for name, query in queries.items():
                for _ in range(WARMUP_COUNT):
                    with connection.cursor() as cursor:
                        cursor.execute(query["sql"], QUERY_PARAMETERS[name])
                        cursor.fetchall()

        for repetition in range(MEASURED_REPETITIONS):
            variant_order, query_order = measured_order(repetition)
            repetition_orders.append({
                "repetition": repetition + 1,
                "variant_order": variant_order,
                "query_order": query_order,
            })
            for variant_position, variant in enumerate(variant_order, start=1):
                connection = connections[variant]
                for query_position, name in enumerate(query_order, start=1):
                    execution_sequence += 1
                    rows, elapsed_ns = _run_query(
                        connection, queries[name]["sql"],
                        QUERY_PARAMETERS[name], clock_ns,
                    )
                    if elapsed_ns < 0:
                        raise RuntimeError("monotonic timer returned a negative duration")
                    result_hash = result_digest(rows)
                    query_digests[name].add(result_hash)
                    samples.append({
                        "scale": scale,
                        "repetition": repetition + 1,
                        "query": name,
                        "variant": variant,
                        "variant_position": variant_position,
                        "query_position": query_position,
                        "execution_sequence": execution_sequence,
                        "elapsed_ns": elapsed_ns,
                        "fetched_rows": len(rows),
                        "result_sha256": result_hash,
                    })

    post_validation = validation_function(config, scales=(scale,))
    unstable_queries = [name for name, hashes in query_digests.items() if len(hashes) != 1]
    if not _validation_ok(post_validation):
        raise RuntimeError("paired correctness validation failed after measurement")
    if unstable_queries:
        raise RuntimeError("query results changed during measurement: "
                           + ", ".join(unstable_queries))

    indexes = {
        variant: diagnostics[variant]["indexes"] for variant in VARIANTS
    }
    return {
        "metadata": {
            "method_schema_version": METHOD_SCHEMA_VERSION,
            "git": _git_metadata(),
            "benchmark_seed": SEED,
            "scale": scale,
            "generated_dataset_sha256": dataset_digest(dataset),
            "logical_row_counts": {
                table: len(rows) for table, rows in dataset.table_rows().items()
            },
            "run_started_at": run_started_at,
        },
        "environment": {
            "host_architecture": os.environ.get("BENCHMARK_HOST_ARCHITECTURE"),
            "runner_container_architecture": platform.machine(),
            "python_version": sys.version,
            "pymysql_version": _installed_distribution_version("PyMySQL"),
            "container_limits": _container_limits(),
            "variants": variant_environment,
        },
        "method": {
            "queries": {
                name: {"sql_file": QUERY_FILES[name], "sql_sha256": query["sha256"]}
                for name, query in queries.items()
            },
            "schema_template_sha256": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
            "warmups_per_query_variant": WARMUP_COUNT,
            "measured_repetitions_per_query_variant": MEASURED_REPETITIONS,
            "timing_definition": (
                "Client-observed nanoseconds from immediately before cursor.execute() "
                "through completion of cursor.fetchall(); connection setup, cursor "
                "creation, result digesting, EXPLAIN, and JSON encoding are excluded."
            ),
            "cache_interpretation": "warm-cache; MariaDB query cache disabled per session",
            "connection_policy": "one persistent connection per variant for this scale",
            "ordering_policy": {
                "query_shuffle": "random.Random(seed + zero_based_repetition).shuffle(query_names)",
                "variant_order": "two_engine first on odd-numbered repetitions; innodb_only first on even-numbered repetitions",
                "repetitions": repetition_orders,
            },
            "percentile_definition": "nearest rank: sorted sample at ceil(0.95 * n), one-based rank",
            "mad_definition": "median(abs(sample - median(sample)))",
            "schema_engines": {
                "two_engine": {"commerce": "InnoDB", "activity_events": "DuckDB"},
                "innodb_only": {"commerce": "InnoDB", "activity_events": "InnoDB"},
            },
            "indexes_by_variant": indexes,
            "warmup_samples_retained": False,
        },
        "correctness": {
            "pre_measurement": pre_validation,
            "post_measurement": post_validation,
            "measured_result_digests_match_across_repetitions_and_variants": True,
        },
        "diagnostics": diagnostics,
        "samples": samples,
        "summaries": summarize_samples(samples),
    }
