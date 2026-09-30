import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import duckdb

from dashboard import queries as source_queries
from experiments.separate_store.feed import initialize_destination, run_feed_cycle
from experiments.separate_store.queries import (
    activity_overview,
    campaigns,
    commerce_overview,
    recent_activity,
    validate_convergence,
)
from simulator.checkout import checkout
from simulator.config import database_config
from simulator.database import (
    insert_product_view,
    insert_session_start,
    open_connection,
    open_connections,
)
from tests.test_separate_store_feed import Source, event, order


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.destination = duckdb.connect(":memory:")
        initialize_destination(self.destination)
        source = Source(
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
        run_feed_cycle(source, self.destination)

    def tearDown(self):
        self.destination.close()

    def test_activity_overview_matches_fixture(self):
        self.assertEqual(activity_overview(self.destination), {
            "sessions": 5, "product_views": 6,
        })

    def test_commerce_overview_uses_one_order_fact_per_order(self):
        self.assertEqual(commerce_overview(self.destination), {
            "orders": 3, "revenue": Decimal("38.00"), "units_sold": 8,
        })

    def test_recent_activity_preserves_fields_order_and_limit(self):
        rows = recent_activity(self.destination, 3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [(row["event_id"], row["event_type"]) for row in rows],
            [(11, "product_view"), (10, "session_start"), (9, "product_view")],
        )
        self.assertEqual(set(rows[0]), {
            "event_id", "session_id", "event_type", "occurred_at",
            "campaign_id", "product_id",
        })

    def test_campaigns_preserve_attribution_conversion_and_revenue(self):
        self.assertEqual(campaigns(self.destination), [
            {
                "campaign_id": 1, "campaign_name": "Campus Launch",
                "is_active": True, "sessions": 2,
                "purchasing_sessions": 1, "revenue": Decimal("26.00"),
                "conversion_percent": Decimal("50.00"), "is_organic": False,
            },
            {
                "campaign_id": 2, "campaign_name": "Study Week",
                "is_active": False, "sessions": 1,
                "purchasing_sessions": 1, "revenue": Decimal("8.00"),
                "conversion_percent": Decimal("100.00"), "is_organic": False,
            },
            {
                "campaign_id": None, "campaign_name": "Organic",
                "is_active": None, "sessions": 2,
                "purchasing_sessions": 1, "revenue": Decimal("4.00"),
                "conversion_percent": Decimal("50.00"), "is_organic": True,
            },
        ])

    def test_validator_detects_a_deliberate_mismatch(self):
        with patch(
            "experiments.separate_store.queries.source_queries.overview",
            return_value={
                "data": {
                    "orders": 99, "revenue": Decimal("38.00"),
                    "units_sold": 8, "sessions": 5, "product_views": 6,
                }
            },
        ), patch(
            "experiments.separate_store.queries.source_queries.recent_activity",
            return_value={"data": recent_activity(self.destination)},
        ), patch(
            "experiments.separate_store.queries.source_queries.campaigns",
            return_value={"data": campaigns(self.destination)},
        ):
            report = validate_convergence(self.destination, self.destination)
        self.assertFalse(report.matches)
        self.assertEqual(report.mismatches, ("commerce_overview",))

    def test_incremental_feed_converges_again_after_new_event_and_order(self):
        source = Source(
            events=[
                event(12, "session-6", "session_start", 300),
                event(13, "session-6", "product_view", 301, None, 1),
            ],
            orders=[order(4, "session-6", None, 302, "12.00", 1)],
            campaigns=[(1, "Campus Launch", True), (2, "Study Week", False)],
        )
        run_feed_cycle(source, self.destination)
        self.assertEqual(activity_overview(self.destination)["sessions"], 6)
        self.assertEqual(commerce_overview(self.destination)["orders"], 4)

    @unittest.skipUnless(
        os.environ.get("MARIADB_INTEGRATION") == "1",
        "requires a disposable seeded MariaDB instance",
    )
    def test_live_source_and_destination_converge_before_and_after_feed(self):
        from experiments.separate_store.feed import run_feed_cycle

        destination = duckdb.connect(":memory:")
        initialize_destination(destination)
        session_id = "00000000-0000-4000-8000-000000000301"
        with open_connection(database_config()) as source:
            run_feed_cycle(source, destination)
            baseline = validate_convergence(source, destination)
            self.assertTrue(baseline.matches, baseline.mismatches)

            occurred_at = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
            insert_session_start(source, session_id, None, occurred_at)
            insert_product_view(source, session_id, None, occurred_at, 1)
            run_feed_cycle(source, destination)
            after_increment = validate_convergence(source, destination)
            self.assertTrue(after_increment.matches, after_increment.mismatches)
        destination.close()

    @unittest.skipUnless(
        os.environ.get("MARIADB_INTEGRATION") == "1",
        "requires a disposable seeded MariaDB instance",
    )
    def test_live_incremental_purchase_converges(self):
        destination = duckdb.connect(":memory:")
        initialize_destination(destination)
        config = database_config()
        session_id = "00000000-0000-4000-8000-000000000302"
        campaign_id = 1
        started_at = datetime(2026, 9, 30, 13, tzinfo=timezone.utc)
        purchased_at = datetime(2026, 9, 30, 13, 0, 2, tzinfo=timezone.utc)

        with open_connections(config) as (operational, events):
            run_feed_cycle(operational, destination)
            baseline = validate_convergence(operational, destination)
            self.assertTrue(baseline.matches, baseline.mismatches)
            baseline_campaign = next(
                row for row in source_queries.campaigns(operational)["data"]
                if row["campaign_id"] == campaign_id
            )

            insert_session_start(events, session_id, campaign_id, started_at)
            insert_product_view(
                events, session_id, campaign_id,
                datetime(2026, 9, 30, 13, 0, 1, tzinfo=timezone.utc), 1,
            )
            order_id = checkout(
                operational, session_id, campaign_id, purchased_at, {1: 1},
                recovery_connection=lambda: open_connection(config),
            )

            with operational.cursor() as cursor:
                cursor.execute(
                    "SELECT session_id, campaign_id, purchased_at "
                    "FROM orders WHERE order_id = %s",
                    (order_id,),
                )
                source_order = cursor.fetchone()
                cursor.execute(
                    "SELECT product_id, quantity, unit_price "
                    "FROM order_items WHERE order_id = %s",
                    (order_id,),
                )
                source_items = cursor.fetchall()
            self.assertEqual(source_order[:2], (session_id, campaign_id))
            self.assertEqual(source_items, ((1, 1, Decimal("12.00")),))

            purchase_revenue = sum(
                quantity * unit_price
                for _, quantity, unit_price in source_items
            )
            run_feed_cycle(operational, destination)

            destination_fact = destination.execute(
                "SELECT order_id, session_id, campaign_id, purchased_at, "
                "revenue_eur, units_sold FROM order_facts WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            self.assertEqual(destination_fact, (
                order_id, session_id, campaign_id, source_order[2],
                purchase_revenue, 1,
            ))

            source_campaign = next(
                row for row in source_queries.campaigns(operational)["data"]
                if row["campaign_id"] == campaign_id
            )
            destination_campaign = next(
                row for row in campaigns(destination)
                if row["campaign_id"] == campaign_id
            )
            self.assertEqual(
                source_campaign["sessions"], baseline_campaign["sessions"] + 1,
            )
            self.assertEqual(
                source_campaign["purchasing_sessions"],
                baseline_campaign["purchasing_sessions"] + 1,
            )
            self.assertEqual(
                source_campaign["revenue"],
                baseline_campaign["revenue"] + purchase_revenue,
            )
            self.assertEqual(
                destination_campaign["sessions"], source_campaign["sessions"],
            )
            self.assertEqual(
                destination_campaign["purchasing_sessions"],
                source_campaign["purchasing_sessions"],
            )
            self.assertEqual(
                destination_campaign["revenue"], source_campaign["revenue"],
            )
            expected_conversion = (
                Decimal(source_campaign["purchasing_sessions"] * 100)
                / Decimal(source_campaign["sessions"])
            ).quantize(Decimal("0.01"))
            self.assertEqual(
                Decimal(str(source_campaign["conversion_percent"])),
                expected_conversion,
            )
            self.assertEqual(
                Decimal(str(destination_campaign["conversion_percent"])),
                expected_conversion,
            )

            converged = validate_convergence(operational, destination)
            self.assertTrue(converged.matches, converged.mismatches)
        destination.close()


if __name__ == "__main__":
    unittest.main()
