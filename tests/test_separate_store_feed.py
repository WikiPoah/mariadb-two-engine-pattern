import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import duckdb

from experiments.separate_store.feed import (
    ORDER_FACTS_SQL,
    _source_rows,
    initialize_destination,
    run_feed_cycle,
)
from simulator.config import database_config
from simulator.database import open_connection


BASE_TIME = datetime(2026, 9, 13, 12)


class SourceCursor:
    def __init__(self, source):
        self.source = source
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, parameters=None):
        if "FROM activity_events" in sql:
            checkpoint = parameters[0]
            self.rows = [row for row in self.source.events if row[0] > checkpoint]
        elif "FROM orders AS o" in sql:
            checkpoint = parameters[0]
            self.rows = [row for row in self.source.orders if row[0] > checkpoint]
        elif "FROM campaigns" in sql:
            self.rows = list(self.source.campaigns)
        else:
            raise AssertionError(f"Unexpected source SQL: {sql}")

    def fetchall(self):
        return list(self.rows)


class Source:
    def __init__(self, events, orders, campaigns):
        self.events = events
        self.orders = orders
        self.campaigns = campaigns

    def cursor(self):
        return SourceCursor(self)


class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 14, tzinfo=timezone.utc)

    def __call__(self):
        result = self.value
        self.value += timedelta(milliseconds=1)
        return result


class FailingBatchDestination:
    """Fail after data and snapshot writes, before their checkpoint can commit."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=None):
        if "INSERT INTO feed_batches" in sql:
            raise RuntimeError("injected batch-metadata failure")
        return self.connection.execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self.connection.executemany(sql, parameters)


class ObservedDestination:
    def __init__(self, connection, *, fail_visibility=False):
        self.connection = connection
        self.committed = False
        self.fail_visibility = fail_visibility

    def execute(self, sql, parameters=None):
        if "UPDATE feed_batches SET destination_visible_at" in sql:
            if self.fail_visibility:
                raise RuntimeError("injected visibility-metadata failure")
            self.visibility_update_after_commit = self.committed
        result = self.connection.execute(sql, parameters)
        if sql == "COMMIT":
            self.committed = True
        return result

    def executemany(self, sql, parameters):
        return self.connection.executemany(sql, parameters)


class CommitAwareClock(Clock):
    def __init__(self, destination):
        super().__init__()
        self.destination = destination
        self.commit_states = []

    def __call__(self):
        self.commit_states.append(self.destination.committed)
        return super().__call__()


def event(event_id, session_id, event_type, seconds, campaign_id=None, product_id=None):
    return (event_id, session_id, event_type, BASE_TIME + timedelta(seconds=seconds),
            campaign_id, product_id)


def order(order_id, session_id, campaign_id, seconds, revenue, units):
    return (order_id, session_id, campaign_id, BASE_TIME + timedelta(seconds=seconds),
            Decimal(revenue), units)


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.destination = duckdb.connect(":memory:")
        initialize_destination(self.destination)
        self.source = Source(
            events=[
                event(1, "session-1", "session_start", 0, 1),
                event(2, "session-1", "product_view", 10, 1, 1),
                event(3, "session-1", "product_view", 20, 1, 2),
                event(4, "session-2", "session_start", 60, 1),
                event(5, "session-2", "product_view", 70, 1, 1),
                event(6, "session-3", "session_start", 120, 2),
                event(7, "session-3", "product_view", 130, 2, 3),
                event(8, "session-4", "session_start", 180),
                event(9, "session-4", "product_view", 190, None, 2),
                event(10, "session-5", "session_start", 240),
                event(11, "session-5", "product_view", 250, None, 3),
            ],
            orders=[
                order(1, "session-1", 1, 30, "26.00", 5),
                order(2, "session-3", 2, 150, "8.00", 1),
                order(3, "session-4", None, 210, "4.00", 2),
            ],
            campaigns=[(1, "Campus Launch", True), (2, "Study Week", False)],
        )

    def tearDown(self):
        self.destination.close()

    def count(self, table):
        return self.destination.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_destination_schema_and_initial_fixture_transfer(self):
        tables = {
            row[0] for row in self.destination.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
        }
        self.assertEqual(tables, {
            "activity_events_copy", "order_facts", "campaigns_snapshot", "feed_batches"
        })

        result = run_feed_cycle(self.source, self.destination, clock=Clock())

        self.assertEqual((result.events_loaded, result.orders_loaded,
                          result.campaigns_loaded), (11, 3, 2))
        self.assertEqual((result.event_checkpoint, result.order_checkpoint), (11, 3))
        self.assertEqual(self.count("activity_events_copy"), 11)
        self.assertEqual(
            self.destination.execute(
                "SELECT event_type, COUNT(*) FROM activity_events_copy "
                "GROUP BY event_type ORDER BY event_type"
            ).fetchall(),
            [("product_view", 6), ("session_start", 5)],
        )
        self.assertEqual(self.count("order_facts"), 3)
        self.assertEqual(self.count("campaigns_snapshot"), 2)
        totals = self.destination.execute(
            "SELECT SUM(revenue_eur), SUM(units_sold) FROM order_facts"
        ).fetchone()
        self.assertEqual(totals, (Decimal("38.00"), 8))
        batch = self.destination.execute(
            """
            SELECT event_checkpoint, order_checkpoint, events_loaded, orders_loaded,
                   campaigns_loaded, destination_visible_at IS NOT NULL
            FROM feed_batches
            """
        ).fetchone()
        self.assertEqual(batch, (11, 3, 11, 3, 2, True))
        self.assertTrue(result.visibility_recorded)

    def test_second_cycle_uses_watermarks_and_refreshes_campaigns(self):
        run_feed_cycle(self.source, self.destination, clock=Clock())
        self.source.events.extend([
            event(12, "session-12", "session_start", 300),
            event(13, "session-12", "product_view", 301, None, 1),
        ])
        self.source.orders.append(order(4, "session-12", None, 302, "12.00", 1))
        self.source.campaigns = [
            (1, "Campus Launch", False),
            (2, "Study Week", True),
            (3, "Autumn Fair", True),
        ]

        result = run_feed_cycle(self.source, self.destination, clock=Clock())

        self.assertEqual((result.events_loaded, result.orders_loaded,
                          result.campaigns_loaded), (2, 1, 3))
        self.assertEqual((result.event_checkpoint, result.order_checkpoint), (13, 4))
        self.assertEqual(self.count("activity_events_copy"), 13)
        self.assertEqual(self.count("order_facts"), 4)
        self.assertEqual(
            self.destination.execute(
                "SELECT campaign_id, is_active FROM campaigns_snapshot ORDER BY campaign_id"
            ).fetchall(),
            [(1, False), (2, True), (3, True)],
        )
        self.assertEqual(self.count("feed_batches"), 2)

    def test_load_failure_rolls_back_data_snapshot_and_checkpoint(self):
        run_feed_cycle(self.source, self.destination, clock=Clock())
        self.source.events.append(
            event(12, "session-12", "session_start", 300)
        )
        self.source.orders.append(
            order(4, "session-12", None, 302, "12.00", 1)
        )
        self.source.campaigns = [(1, "Changed", False)]

        with self.assertRaisesRegex(RuntimeError, "batch-metadata failure"):
            run_feed_cycle(
                self.source, FailingBatchDestination(self.destination), clock=Clock()
            )

        self.assertEqual(self.count("activity_events_copy"), 11)
        self.assertEqual(self.count("order_facts"), 3)
        self.assertEqual(
            self.destination.execute(
                "SELECT COUNT(*) FROM order_facts WHERE order_id = 4"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(self.count("feed_batches"), 1)
        self.assertEqual(
            self.destination.execute(
                "SELECT campaign_id, campaign_name, is_active "
                "FROM campaigns_snapshot ORDER BY campaign_id"
            ).fetchall(),
            [(1, "Campus Launch", True), (2, "Study Week", False)],
        )
        self.assertEqual(
            self.destination.execute(
                "SELECT event_checkpoint, order_checkpoint FROM feed_batches"
            ).fetchone(),
            (11, 3),
        )

    def test_empty_cycle_preserves_checkpoints_and_refreshes_campaigns(self):
        first = run_feed_cycle(self.source, self.destination, clock=Clock())
        self.source.campaigns = [(1, "Campus Launch", False)]

        second = run_feed_cycle(self.source, self.destination, clock=Clock())

        self.assertEqual((second.events_loaded, second.orders_loaded), (0, 0))
        self.assertEqual(
            (second.event_checkpoint, second.order_checkpoint),
            (first.event_checkpoint, first.order_checkpoint),
        )
        self.assertEqual(
            self.destination.execute(
                "SELECT campaign_id, campaign_name, is_active FROM campaigns_snapshot"
            ).fetchall(),
            [(1, "Campus Launch", False)],
        )
        self.assertEqual(self.count("feed_batches"), 2)

    def test_visibility_timestamp_is_captured_and_recorded_after_commit(self):
        observed = ObservedDestination(self.destination)
        clock = CommitAwareClock(observed)

        result = run_feed_cycle(self.source, observed, clock=clock)

        self.assertEqual(clock.commit_states, [False, False, False, True])
        self.assertTrue(observed.visibility_update_after_commit)
        self.assertTrue(result.visibility_recorded)
        timestamp_matches = self.destination.execute(
            """
            SELECT destination_visible_at = ?
            FROM feed_batches
            WHERE batch_id = 1
            """,
            (result.destination_visible_at,),
        ).fetchone()[0]
        self.assertTrue(timestamp_matches)

    def test_visibility_metadata_failure_does_not_undo_or_fail_committed_feed(self):
        observed = ObservedDestination(self.destination, fail_visibility=True)
        clock = CommitAwareClock(observed)

        result = run_feed_cycle(self.source, observed, clock=clock)

        self.assertTrue(observed.committed)
        self.assertFalse(result.visibility_recorded)
        self.assertEqual((self.count("activity_events_copy"),
                          self.count("order_facts"),
                          self.count("campaigns_snapshot")), (11, 3, 2))
        batch = self.destination.execute(
            """
            SELECT batch_id, event_checkpoint, order_checkpoint,
                   destination_visible_at
            FROM feed_batches
            """
        ).fetchone()
        self.assertEqual(batch, (1, 11, 3, None))

    def test_controlled_event_ids_advance_without_recopying_prior_rows(self):
        self.source.events = [
            event(1, "session-1", "session_start", 1),
            event(2, "session-1", "product_view", 2, None, 1),
        ]
        first = run_feed_cycle(self.source, self.destination, clock=Clock())
        self.source.events.extend([
            event(3, "session-2", "session_start", 3),
            event(4, "session-2", "product_view", 4, None, 2),
        ])

        second = run_feed_cycle(self.source, self.destination, clock=Clock())

        self.assertEqual((first.events_loaded, first.event_checkpoint), (2, 2))
        self.assertEqual((second.events_loaded, second.event_checkpoint), (2, 4))
        self.assertEqual(
            self.destination.execute(
                "SELECT source_event_id FROM activity_events_copy ORDER BY source_event_id"
            ).fetchall(),
            [(1,), (2,), (3,), (4,)],
        )

    @unittest.skipUnless(
        os.environ.get("MARIADB_INTEGRATION") == "1",
        "requires a disposable seeded MariaDB instance",
    )
    def test_live_order_query_aggregates_multiple_items_into_one_fact(self):
        with open_connection(database_config()) as source:
            rows = _source_rows(source, ORDER_FACTS_SQL, (0,))

        order_one = [row for row in rows if row[0] == 1]
        self.assertEqual(len(order_one), 1)
        self.assertEqual(order_one[0], (
            1,
            "123e4567-e89b-42d3-a456-426614174001",
            1,
            datetime(2026, 9, 13, 12, 0, 30),
            Decimal("26.00"),
            Decimal("5"),
        ))


if __name__ == "__main__":
    unittest.main()
