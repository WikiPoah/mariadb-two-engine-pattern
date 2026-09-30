"""One read-only extraction cycle into a standalone DuckDB database."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_PATH = Path(__file__).with_name("schema.sql")

EVENTS_SQL = """
SELECT event_id, session_id, event_type, occurred_at, campaign_id, product_id
FROM activity_events
WHERE event_id > %s
ORDER BY event_id
"""

ORDER_FACTS_SQL = """
SELECT o.order_id, o.session_id, o.campaign_id, o.purchased_at,
       CAST(SUM(oi.quantity * oi.unit_price) AS DECIMAL(18,2)) AS revenue_eur,
       SUM(oi.quantity) AS units_sold
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.order_id
WHERE o.order_id > %s
GROUP BY o.order_id, o.session_id, o.campaign_id, o.purchased_at
ORDER BY o.order_id
"""

CAMPAIGNS_SQL = """
SELECT campaign_id, name, is_active
FROM campaigns
ORDER BY campaign_id
"""


@dataclass(frozen=True)
class FeedCycle:
    batch_id: int
    events_loaded: int
    orders_loaded: int
    campaigns_loaded: int
    event_checkpoint: int
    order_checkpoint: int
    destination_visible_at: datetime
    visibility_recorded: bool


def initialize_destination(destination) -> None:
    destination.execute(SCHEMA_PATH.read_text())


def _checkpoint(destination) -> tuple[int, int, int]:
    row = destination.execute(
        """
        SELECT batch_id, event_checkpoint, order_checkpoint
        FROM feed_batches
        ORDER BY batch_id DESC
        LIMIT 1
        """
    ).fetchone()
    return (0, 0, 0) if row is None else tuple(int(value) for value in row)


def _source_rows(source, sql: str, parameters=None) -> list[tuple]:
    with source.cursor() as cursor:
        cursor.execute(sql, parameters)
        return list(cursor.fetchall())


def run_feed_cycle(source, destination, *, clock=None) -> FeedCycle:
    """Copy one incremental batch; callers own both database connections."""
    now = clock or (lambda: datetime.now(timezone.utc))
    started_at = now()
    previous_batch, event_checkpoint, order_checkpoint = _checkpoint(destination)

    events = _source_rows(source, EVENTS_SQL, (event_checkpoint,))
    order_facts = _source_rows(source, ORDER_FACTS_SQL, (order_checkpoint,))
    campaigns = _source_rows(source, CAMPAIGNS_SQL)
    source_read_at = now()

    next_event_checkpoint = max(
        (int(row[0]) for row in events), default=event_checkpoint
    )
    next_order_checkpoint = max(
        (int(row[0]) for row in order_facts), default=order_checkpoint
    )
    batch_id = previous_batch + 1

    destination.execute("BEGIN TRANSACTION")
    try:
        if events:
            destination.executemany(
                """
                INSERT INTO activity_events_copy
                    (source_event_id, session_id, event_type, occurred_at,
                     campaign_id, product_id, first_visible_batch_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [(*event, batch_id) for event in events],
            )
        if order_facts:
            destination.executemany(
                """
                INSERT INTO order_facts
                    (order_id, session_id, campaign_id, purchased_at,
                     revenue_eur, units_sold)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                order_facts,
            )

        # Campaigns have no modification timestamp, so each cycle replaces the snapshot.
        destination.execute("DELETE FROM campaigns_snapshot")
        if campaigns:
            destination.executemany(
                """
                INSERT INTO campaigns_snapshot
                    (campaign_id, campaign_name, is_active)
                VALUES (?, ?, ?)
                """,
                campaigns,
            )

        commit_started_at = now()
        destination.execute(
            """
            INSERT INTO feed_batches
                (batch_id, started_at, source_read_at, commit_started_at,
                 destination_visible_at, event_checkpoint, order_checkpoint,
                 events_loaded, orders_loaded, campaigns_loaded)
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                batch_id, started_at, source_read_at, commit_started_at,
                next_event_checkpoint, next_order_checkpoint,
                len(events), len(order_facts), len(campaigns),
            ),
        )
        destination.execute("COMMIT")
    except Exception:
        destination.execute("ROLLBACK")
        raise

    # Captured after COMMIT returns, this is an upper bound on first visibility.
    destination_visible_at = now()
    try:
        destination.execute(
            "UPDATE feed_batches SET destination_visible_at = ? WHERE batch_id = ?",
            (destination_visible_at, batch_id),
        )
    except Exception:
        # The feed is already committed. NULL marks this batch unsuitable for timing.
        visibility_recorded = False
    else:
        visibility_recorded = True
    return FeedCycle(
        batch_id=batch_id,
        events_loaded=len(events),
        orders_loaded=len(order_facts),
        campaigns_loaded=len(campaigns),
        event_checkpoint=next_event_checkpoint,
        order_checkpoint=next_order_checkpoint,
        destination_visible_at=destination_visible_at,
        visibility_recorded=visibility_recorded,
    )
