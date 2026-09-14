"""Operational reads and independent browsing-event writes."""

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone

import pymysql


@contextmanager
def open_connection(config: dict):
    with pymysql.connect(
        **config, charset="utf8mb4", collation="utf8mb4_bin", autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION time_zone = '+00:00'")
        yield connection


@contextmanager
def open_connections(config: dict):
    # Separate sessions keep event writes outside future checkout transactions.
    # Autocommit prevents catalog reads retaining an old InnoDB snapshot.
    with ExitStack() as stack:
        operational = stack.enter_context(open_connection(config))
        events = stack.enter_context(open_connection(config))
        yield operational, events


def active_campaign_ids(operational) -> list[int]:
    with operational.cursor() as cursor:
        cursor.execute(
            "SELECT campaign_id FROM campaigns WHERE is_active = TRUE ORDER BY campaign_id"
        )
        return [row[0] for row in cursor.fetchall()]


def active_product_ids(operational) -> list[int]:
    # Stock limits purchases, not browsing eligibility.
    with operational.cursor() as cursor:
        cursor.execute(
            "SELECT product_id FROM products WHERE is_active = TRUE ORDER BY product_id"
        )
        return [row[0] for row in cursor.fetchall()]


def _utc_datetime(timestamp: datetime) -> datetime:
    # DATETIME has no timezone: normalize before the driver serializes its fields.
    if timestamp.utcoffset() is None:
        raise ValueError("Database timestamps must be timezone-aware")
    return timestamp.astimezone(timezone.utc).replace(tzinfo=None)


def insert_session_start(events, session_id: str, campaign_id: int | None,
                         occurred_at: datetime) -> None:
    with events.cursor() as cursor:
        cursor.execute(
            "INSERT INTO activity_events "
            "(session_id, event_type, occurred_at, campaign_id, product_id) "
            "VALUES (%s, 'session_start', %s, %s, NULL)",
            (session_id, _utc_datetime(occurred_at), campaign_id),
        )


def insert_product_view(events, session_id: str, campaign_id: int | None,
                        occurred_at: datetime, product_id: int) -> None:
    with events.cursor() as cursor:
        cursor.execute(
            "INSERT INTO activity_events "
            "(session_id, event_type, occurred_at, campaign_id, product_id) "
            "VALUES (%s, 'product_view', %s, %s, %s)",
            (session_id, _utc_datetime(occurred_at), campaign_id, product_id),
        )
