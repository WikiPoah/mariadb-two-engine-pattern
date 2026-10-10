import os
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from experiments.analytical_benchmark.database import query_mismatches, rows_digest, rows_match
from experiments.analytical_benchmark.dataset import (
    PRICE_CHANGE_AFTER_SESSION,
    SCALES,
    START_TIME,
    generate_dataset,
    validate_dataset,
)


class DatasetTests(unittest.TestCase):
    def test_seeded_generation_is_reproducible(self):
        for scale in SCALES:
            with self.subTest(scale=scale):
                self.assertEqual(generate_dataset(scale), generate_dataset(scale))

    def test_5000_sessions_are_an_exact_history_prefix_of_50000(self):
        small = generate_dataset(5_000)
        large = generate_dataset(50_000)

        self.assertEqual(small.events, large.events[:len(small.events)])
        self.assertEqual(small.orders, large.orders[:len(small.orders)])
        self.assertEqual(
            small.order_items, large.order_items[:len(small.order_items)]
        )
        self.assertEqual(small.campaigns, large.campaigns)

        # Mutable catalog stock and price are final-state values, so they grow/change
        # with the additional 45,000 sessions and are not part of this prefix check.
        small_sessions = {
            row[1] for row in small.events if row[2] == "session_start"
        }
        large_prefix_sessions = {
            row[1] for row in large.events[:len(small.events)]
            if row[2] == "session_start"
        }
        self.assertEqual(small_sessions, large_prefix_sessions)

    def test_locked_scales_have_expected_session_and_row_counts(self):
        small = generate_dataset(5_000)
        large = generate_dataset(50_000)
        self.assertEqual(SCALES, (5_000, 50_000))
        self.assertEqual((small.session_count, len(small.events)), (5_000, 17_401))
        self.assertEqual((len(small.orders), len(small.order_items)), (1_021, 1_342))
        self.assertEqual((large.session_count, len(large.events)), (50_000, 174_868))
        self.assertEqual((len(large.orders), len(large.order_items)), (10_064, 13_244))

    def test_identifiers_timestamps_and_workload_relationships_are_valid(self):
        dataset = generate_dataset(5_000)
        session_ids = {
            row[1] for row in dataset.events if row[2] == "session_start"
        }
        event_ids = [row[0] for row in dataset.events]
        order_ids = [row[0] for row in dataset.orders]
        self.assertEqual(len(session_ids), 5_000)
        self.assertEqual(len(event_ids), len(set(event_ids)))
        self.assertEqual(len(order_ids), len(set(order_ids)))
        self.assertTrue(all(str(UUID(value)) == value for value in session_ids))
        self.assertTrue(all(row[3].year == 2026 for row in dataset.events))
        validate_dataset(dataset)

    def test_catalog_price_change_preserves_purchase_time_prices(self):
        dataset = generate_dataset(50_000)
        notebook_prices = {
            item[3] for item in dataset.order_items if item[1] == 1
        }
        current_notebook = next(row[2] for row in dataset.products if row[0] == 1)
        self.assertEqual(notebook_prices, {10, 12})
        self.assertEqual(current_notebook, 12)

    def test_notebook_prices_change_after_fixed_session_boundary(self):
        dataset = generate_dataset(5_000)
        session_ordinals = {
            row[1]: int((row[3] - START_TIME.replace(tzinfo=None)).total_seconds()) + 1
            for row in dataset.events if row[2] == "session_start"
        }
        orders_by_id = {row[0]: row for row in dataset.orders}
        notebook_purchases = [
            (session_ordinals[orders_by_id[item[0]][1]],
             orders_by_id[item[0]][3], item[3])
            for item in dataset.order_items if item[1] == 1
        ]
        before = max(
            (purchase for purchase in notebook_purchases
             if purchase[0] <= PRICE_CHANGE_AFTER_SESSION),
            key=lambda purchase: purchase[0],
        )
        after = min(
            (purchase for purchase in notebook_purchases
             if purchase[0] > PRICE_CHANGE_AFTER_SESSION),
            key=lambda purchase: purchase[0],
        )
        transition_at = START_TIME.replace(tzinfo=None) + timedelta(
            seconds=PRICE_CHANGE_AFTER_SESSION
        )

        self.assertEqual(before[2], Decimal("10.00"))
        self.assertLess(before[1], transition_at)
        self.assertEqual(after[2], Decimal("12.00"))
        self.assertGreaterEqual(after[1], transition_at)

    def test_validator_rejects_duplicate_event_ids(self):
        dataset = generate_dataset(5_000)
        events = (dataset.events[0],) + dataset.events[1:]
        events = (events[0],) + (events[0],) + events[2:]
        with self.assertRaisesRegex(ValueError, "event IDs must be unique"):
            validate_dataset(replace(dataset, events=events))

    def test_comparison_helpers_detect_data_and_query_mismatches(self):
        self.assertNotEqual(rows_digest([(1, "same")]), rows_digest([(1, "changed")]))
        self.assertTrue(rows_match([(1, "same")], [(1, "same")]))
        self.assertFalse(rows_match([(1, "same")], [(1, "changed")]))
        self.assertEqual(
            query_mismatches({"revenue": 10.0}, {"revenue": Decimal("10.00")}), []
        )
        self.assertEqual(
            query_mismatches({"campaigns": [{"sessions": 2}]},
                             {"campaigns": [{"sessions": 3}]}),
            ["campaigns"],
        )

    @unittest.skipUnless(
        os.environ.get("MARIADB_INTEGRATION") == "1",
        "requires a fresh disposable MariaDB 12.3.3 server",
    )
    def test_live_paired_schema_load_and_analytical_validation(self):
        from experiments.analytical_benchmark.database import prepare_pair, validate_pair
        from simulator.config import database_config

        prepared = prepare_pair(database_config(), scales=(5_000,))
        self.assertEqual(len(prepared), 2)
        report = validate_pair(database_config(), scales=(5_000,))
        self.assertTrue(report["valid"], report["mismatches"])


if __name__ == "__main__":
    unittest.main()
