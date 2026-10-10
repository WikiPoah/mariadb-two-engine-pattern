"""Disposable paired-schema preparation, loading, and correctness validation."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dashboard import queries as dashboard_queries
from simulator.database import open_connection

from experiments.analytical_benchmark.dataset import SCALES, generate_dataset


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
VARIANTS = {
    "two_engine": ("DUCKDB", ""),
    "innodb_only": (
        "InnoDB",
        ",\n    KEY idx_activity_session (session_id),\n"
        "    KEY idx_activity_recent "
        "(occurred_at DESC, session_id, event_type, campaign_id, product_id)",
    ),
}
TABLE_COLUMNS = {
    "products": "product_id, name, unit_price, stock_quantity, is_active",
    "campaigns": "campaign_id, name, is_active",
    "orders": "order_id, session_id, campaign_id, purchased_at",
    "order_items": "order_id, product_id, quantity, unit_price",
    "activity_events": "event_id, session_id, event_type, occurred_at, campaign_id, product_id",
}
TABLE_ORDER = {
    "products": "product_id",
    "campaigns": "campaign_id",
    "orders": "order_id",
    "order_items": "order_id, product_id",
    "activity_events": "event_id",
}
BATCH_SIZE = 500


def database_name(scale: int, variant: str) -> str:
    if scale not in SCALES or variant not in VARIANTS:
        raise ValueError("unsupported benchmark scale or variant")
    return f"analytical_bench_{scale}_{variant}"


def _canonical(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return format(Decimal(str(value)).normalize(), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    return value


def rows_digest(rows) -> str:
    digest = hashlib.sha256()
    for row in rows:
        value = json.dumps([_canonical(item) for item in row],
                           ensure_ascii=False, separators=(",", ":"))
        digest.update(value.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def rows_match(expected, actual) -> bool:
    return len(expected) == len(actual) and rows_digest(expected) == rows_digest(actual)


def query_mismatches(left: dict, right: dict) -> list[str]:
    """Compare logical query answers while normalizing driver representations."""
    names = sorted(set(left) | set(right))
    return [name for name in names
            if name not in left or name not in right
            or _canonical_tree(left[name]) != _canonical_tree(right[name])]


def _canonical_tree(value):
    if isinstance(value, dict):
        return {key: _canonical_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_tree(item) for item in value]
    return _canonical(value)


def _chunks(rows):
    for offset in range(0, len(rows), BATCH_SIZE):
        yield rows[offset:offset + BATCH_SIZE]


def _insert_rows(connection, table: str, columns: str, rows) -> None:
    placeholders = ", ".join(["%s"] * len(columns.split(",")))
    sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
    with connection.cursor() as cursor:
        for batch in _chunks(rows):
            cursor.executemany(sql, batch)


def _create_variant(connection, scale: int, variant: str, dataset) -> None:
    name = database_name(scale, variant)
    engine, indexes = VARIANTS[variant]
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
        )
    connection.select_db(name)
    schema = SCHEMA_PATH.read_text().format(
        event_engine=engine, event_indexes=indexes,
    )
    with connection.cursor() as cursor:
        for statement in schema.split(";"):
            if statement.strip():
                cursor.execute(statement)
    _insert_rows(connection, "products", TABLE_COLUMNS["products"], dataset.products)
    _insert_rows(connection, "campaigns", TABLE_COLUMNS["campaigns"], dataset.campaigns)
    _insert_rows(connection, "activity_events", TABLE_COLUMNS["activity_events"], dataset.events)
    _insert_rows(connection, "orders", TABLE_COLUMNS["orders"], dataset.orders)
    _insert_rows(connection, "order_items", TABLE_COLUMNS["order_items"], dataset.order_items)


def prepare_pair(config: dict, scales=SCALES) -> list[dict]:
    """Create fresh variant databases and load each from one generated dataset."""
    scales = tuple(scales)
    if not scales or any(scale not in SCALES for scale in scales):
        raise ValueError(f"scales must be selected from {SCALES}")
    admin_config = {**config, "database": None}
    prepared = []
    with open_connection(admin_config) as connection:
        names = [database_name(scale, variant)
                 for scale in scales for variant in VARIANTS]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME IN ({})".format(",".join(["%s"] * len(names))),
                names,
            )
            existing = [row[0] for row in cursor.fetchall()]
        if existing:
            raise RuntimeError(
                "benchmark databases already exist; use a fresh disposable server: "
                + ", ".join(existing)
            )

        for scale in scales:
            dataset = generate_dataset(scale)
            for variant in VARIANTS:
                _create_variant(connection, scale, variant, dataset)
                prepared.append({
                    "scale": scale,
                    "variant": variant,
                    "database": database_name(scale, variant),
                    "sessions": dataset.session_count,
                    "events": len(dataset.events),
                    "orders": len(dataset.orders),
                    "order_items": len(dataset.order_items),
                })
    return prepared


def _table_rows(connection, table: str) -> list[tuple]:
    sql = (f"SELECT {TABLE_COLUMNS[table]} FROM {table} "
           f"ORDER BY {TABLE_ORDER[table]}")
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return list(cursor.fetchall())


def _analytics(connection) -> dict:
    overview = dashboard_queries.overview(connection)["data"]
    return {
        "overview": overview,
        "recent_activity": dashboard_queries.recent_activity(connection, 50)["data"],
        "campaigns": dashboard_queries.campaigns(connection)["data"],
    }


def _analytics_summary(answers: dict) -> dict:
    summary = {}
    for name, value in answers.items():
        encoded = json.dumps(_canonical_tree(value), ensure_ascii=False,
                             separators=(",", ":"), sort_keys=True)
        summary[name] = {
            "rows": len(value) if isinstance(value, list) else 1,
            "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        }
    return summary


def validate_pair(config: dict, scales=SCALES) -> dict:
    """Compare engines, every canonical table row, and the application queries."""
    scales = tuple(scales)
    if not scales or any(scale not in SCALES for scale in scales):
        raise ValueError(f"scales must be selected from {SCALES}")
    report = {"valid": True, "scales": {}, "mismatches": []}
    admin_config = {**config, "database": None}

    with open_connection(admin_config) as connection:
        for scale in scales:
            dataset = generate_dataset(scale)
            expected = dataset.table_rows()
            variant_results = {}
            scale_report = {"variants": {}, "query_mismatches": []}

            for variant, (event_engine, _) in VARIANTS.items():
                name = database_name(scale, variant)
                connection.select_db(name)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' "
                        "ORDER BY TABLE_NAME",
                        (name,),
                    )
                    engines = dict(cursor.fetchall())
                expected_engines = {
                    "products": "InnoDB", "campaigns": "InnoDB",
                    "orders": "InnoDB", "order_items": "InnoDB",
                    "activity_events": event_engine,
                }
                variant_mismatches = []
                if engines != expected_engines:
                    variant_mismatches.append("engine_mapping")

                content = {}
                for table, expected_rows in expected.items():
                    actual_rows = _table_rows(connection, table)
                    matches = rows_match(expected_rows, actual_rows)
                    content[table] = {
                        "expected_rows": len(expected_rows),
                        "actual_rows": len(actual_rows),
                        "expected_sha256": rows_digest(expected_rows),
                        "actual_sha256": rows_digest(actual_rows),
                        "matches": matches,
                    }
                    if not matches:
                        variant_mismatches.append(f"table:{table}")

                try:
                    analytics = _analytics(connection)
                except Exception as error:
                    analytics = None
                    variant_mismatches.append(f"analytical_queries:{type(error).__name__}")
                scale_report["variants"][variant] = {
                    "database": name,
                    "engines": engines,
                    "contents": content,
                    "analytics": None if analytics is None else _analytics_summary(analytics),
                    "mismatches": variant_mismatches,
                }
                variant_results[variant] = analytics
                report["mismatches"].extend(
                    f"{scale}:{variant}:{item}" for item in variant_mismatches
                )

            if all(value is not None for value in variant_results.values()):
                query_diff = query_mismatches(
                    variant_results["two_engine"], variant_results["innodb_only"]
                )
                scale_report["query_mismatches"] = query_diff
                report["mismatches"].extend(
                    f"{scale}:query:{item}" for item in query_diff
                )
            report["scales"][str(scale)] = scale_report

    report["valid"] = not report["mismatches"]
    return report
