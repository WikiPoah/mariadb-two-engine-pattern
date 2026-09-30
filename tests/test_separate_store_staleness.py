import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb

from experiments.separate_store import staleness
from experiments.separate_store.feed import initialize_destination, run_feed_cycle
from experiments.separate_store.staleness import (
    event_observations,
    median,
    nearest_rank_percentile,
    reconcile_controlled_records,
    run_experiment,
    run_scheduled_feed,
    serialize_result,
    summarize_observations,
    write_result,
)
from simulator.config import database_config
from tests.test_separate_store_feed import Clock, Source, event, order


NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)


class MonotonicClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds

    def advance(self, seconds):
        self.value += seconds


class StalenessTests(unittest.TestCase):
    def setUp(self):
        self.destination = duckdb.connect(":memory:")
        initialize_destination(self.destination)

    def tearDown(self):
        self.destination.close()

    def insert_batch(self, batch_id, visible_at):
        self.destination.execute(
            """
            INSERT INTO feed_batches
                (batch_id, started_at, source_read_at, commit_started_at,
                 destination_visible_at, event_checkpoint, order_checkpoint,
                 events_loaded, orders_loaded, campaigns_loaded)
            VALUES (?, ?, ?, ?, ?, 1, 0, 1, 0, 0)
            """,
            (batch_id, NOW, NOW, NOW, visible_at),
        )

    def insert_event(self, event_id, occurred_at, batch_id):
        self.destination.execute(
            """
            INSERT INTO activity_events_copy
                (source_event_id, session_id, event_type, occurred_at,
                 campaign_id, product_id, first_visible_batch_id)
            VALUES (?, 'session', 'session_start', ?, NULL, NULL, ?)
            """,
            (event_id, occurred_at, batch_id),
        )

    def test_event_uses_its_first_visible_batch_for_exact_staleness(self):
        self.insert_batch(4, NOW)
        self.insert_event(12, datetime(2026, 9, 30, 11, 59, 58, 500000), 4)

        observation = event_observations(self.destination, 11)[0]

        self.assertEqual(observation["first_visible_batch_id"], 4)
        self.assertEqual(observation["destination_visible_at"], NOW)
        self.assertEqual(observation["raw_staleness_ms"], 1500.0)
        self.assertEqual(observation["staleness_ms"], 1500.0)
        self.assertTrue(observation["measurement_valid"])

    def test_negative_staleness_is_auditable_invalid_and_excluded(self):
        self.insert_batch(1, NOW)
        self.insert_event(1, datetime(2026, 9, 30, 12, 0, 0, 500000), 1)

        observations = event_observations(self.destination, 0)
        summary = summarize_observations(observations)

        self.assertEqual(observations[0]["raw_staleness_ms"], -500.0)
        self.assertIsNone(observations[0]["staleness_ms"])
        self.assertFalse(observations[0]["measurement_valid"])
        self.assertEqual(observations[0]["invalid_reason"], "negative_staleness")
        self.assertEqual(summary["measured_event_count"], 0)
        self.assertEqual(summary["invalid_event_count"], 1)
        self.assertIsNone(summary["maximum_staleness_ms"])

    def test_visibility_failure_is_flagged_and_excluded(self):
        self.insert_batch(1, None)
        self.insert_event(1, datetime(2026, 9, 30, 11, 59, 59), 1)

        observations = event_observations(self.destination, 0)
        summary = summarize_observations(observations)

        self.assertFalse(observations[0]["measurement_valid"])
        self.assertEqual(
            observations[0]["invalid_reason"], "missing_destination_visibility",
        )
        self.assertIsNone(observations[0]["staleness_ms"])
        self.assertIsNone(observations[0]["raw_staleness_ms"])
        self.assertEqual(summary["measured_event_count"], 0)
        self.assertEqual(summary["invalid_event_count"], 1)
        self.assertIsNone(summary["median_staleness_ms"])

    def test_feed_records_first_visible_batch_provenance(self):
        source = Source(
            events=[event(1, "session-1", "session_start", 0)],
            orders=[],
            campaigns=[],
        )
        run_feed_cycle(source, self.destination, clock=Clock())
        source.events.append(event(2, "session-2", "session_start", 1))
        run_feed_cycle(source, self.destination, clock=Clock())

        self.assertEqual(
            self.destination.execute(
                "SELECT source_event_id, first_visible_batch_id "
                "FROM activity_events_copy ORDER BY source_event_id"
            ).fetchall(),
            [(1, 1), (2, 2)],
        )

    def test_median_and_nearest_rank_p95(self):
        self.assertEqual(median([40, 10, 30]), 30.0)
        self.assertEqual(median([40, 10, 30, 20]), 25.0)
        self.assertEqual(nearest_rank_percentile([10, 20, 30, 40, 50], 95), 50.0)
        self.assertIsNone(median([]))
        self.assertIsNone(nearest_rank_percentile([], 95))

    def test_reconciliation_detects_missing_event(self):
        source = Source(
            events=[event(1, "session-1", "session_start", 0)],
            orders=[],
            campaigns=[],
        )
        run_feed_cycle(source, self.destination, clock=Clock())
        source.events.append(event(2, "session-2", "session_start", 1))

        result = reconcile_controlled_records(
            source, self.destination, baseline_event_id=1, baseline_order_id=0,
        )

        self.assertFalse(result["matches"])
        self.assertEqual(result["missing_event_ids"], [2])
        self.assertEqual(result["extra_event_ids"], [])

    def test_reconciliation_detects_extra_event(self):
        source = Source(
            events=[event(1, "session-1", "session_start", 0)],
            orders=[],
            campaigns=[],
        )
        run_feed_cycle(source, self.destination, clock=Clock())
        self.destination.execute(
            """
            INSERT INTO activity_events_copy
                (source_event_id, session_id, event_type, occurred_at,
                 campaign_id, product_id, first_visible_batch_id)
            VALUES (2, 'extra', 'session_start', ?, NULL, NULL, 1)
            """,
            (NOW,),
        )

        result = reconcile_controlled_records(
            source, self.destination, baseline_event_id=1, baseline_order_id=0,
        )

        self.assertFalse(result["matches"])
        self.assertEqual(result["missing_event_ids"], [])
        self.assertEqual(result["extra_event_ids"], [2])

    def test_reconciliation_detects_missing_order(self):
        source = Source(
            events=[event(1, "session-1", "session_start", 0)],
            orders=[order(1, "session-1", None, 1, "12.00", 1)],
            campaigns=[],
        )
        run_feed_cycle(source, self.destination, clock=Clock())
        source.orders.append(order(2, "session-2", None, 2, "8.00", 1))

        result = reconcile_controlled_records(
            source, self.destination, baseline_event_id=1, baseline_order_id=1,
        )

        self.assertFalse(result["matches"])
        self.assertEqual(result["missing_order_ids"], [2])
        self.assertEqual(result["extra_order_ids"], [])

    def test_reconciliation_detects_extra_order(self):
        source = Source(
            events=[event(1, "session-1", "session_start", 0)],
            orders=[order(1, "session-1", None, 1, "12.00", 1)],
            campaigns=[],
        )
        run_feed_cycle(source, self.destination, clock=Clock())
        self.destination.execute(
            """
            INSERT INTO order_facts
                (order_id, session_id, campaign_id, purchased_at,
                 revenue_eur, units_sold)
            VALUES (2, 'extra', NULL, ?, 8.00, 1)
            """,
            (NOW,),
        )

        result = reconcile_controlled_records(
            source, self.destination, baseline_event_id=1, baseline_order_id=1,
        )

        self.assertFalse(result["matches"])
        self.assertEqual(result["missing_order_ids"], [])
        self.assertEqual(result["extra_order_ids"], [2])

    def test_reconciliation_detects_checkpoint_disagreement(self):
        source = Source(
            events=[event(1, "session-1", "session_start", 0)],
            orders=[],
            campaigns=[],
        )
        run_feed_cycle(source, self.destination, clock=Clock())
        self.destination.execute(
            "UPDATE feed_batches SET event_checkpoint = 0 WHERE batch_id = 1"
        )

        result = reconcile_controlled_records(
            source, self.destination, baseline_event_id=1, baseline_order_id=0,
        )

        self.assertFalse(result["matches"])
        self.assertFalse(result["checkpoint_match"])
        self.assertEqual(result["missing_event_ids"], [])
        self.assertEqual(result["extra_event_ids"], [])

    def run_two_cycle_schedule(self, first_duration):
        clock = MonotonicClock()
        durations = iter((first_duration, 0.0))
        running = iter((True, False))
        cycles = []

        def feed_once():
            cycle = SimpleNamespace(batch_id=len(cycles) + 1)
            cycles.append(cycle)
            clock.advance(next(durations))
            return cycle

        records = run_scheduled_feed(
            2.0,
            lambda: next(running),
            feed_once,
            lambda: True,
            monotonic_clock=clock,
            sleeper=clock.sleep,
        )
        return clock, records

    def test_scheduler_anchors_after_normal_short_cycle(self):
        clock, records = self.run_two_cycle_schedule(1.0)

        self.assertEqual(clock.sleeps, [2.0, 1.0])
        self.assertEqual(records[0].completed_monotonic, 3.0)
        self.assertEqual(records[0].missed_intervals, 0)
        self.assertEqual(records[0].cadence_overrun_ms, 0.0)

    def test_scheduler_runs_due_cycle_at_exact_deadline(self):
        clock, records = self.run_two_cycle_schedule(2.0)

        self.assertEqual(clock.sleeps, [2.0])
        self.assertEqual(records[0].completed_monotonic, 4.0)
        self.assertEqual(records[0].missed_intervals, 0)
        self.assertEqual(records[0].cadence_overrun_ms, 0.0)

    def test_scheduler_skips_one_genuinely_missed_deadline(self):
        clock, records = self.run_two_cycle_schedule(2.1)

        self.assertEqual(len(clock.sleeps), 2)
        self.assertAlmostEqual(clock.sleeps[1], 1.9)
        self.assertEqual(records[0].missed_intervals, 1)
        self.assertEqual(records[0].cadence_overrun_ms, 100.0)

    def test_scheduler_skips_multiple_missed_deadlines(self):
        clock, records = self.run_two_cycle_schedule(6.1)

        self.assertEqual(len(clock.sleeps), 2)
        self.assertAlmostEqual(clock.sleeps[1], 1.9)
        self.assertEqual(records[0].missed_intervals, 3)
        self.assertEqual(records[0].cadence_overrun_ms, 4100.0)

    def test_scheduler_continues_until_caught_up(self):
        clock = MonotonicClock()
        caught_up = iter((False, True))
        cycles = []

        records = run_scheduled_feed(
            1.0,
            lambda: False,
            lambda: cycles.append(len(cycles) + 1) or SimpleNamespace(
                batch_id=len(cycles)
            ),
            lambda: next(caught_up),
            monotonic_clock=clock,
            sleeper=clock.sleep,
        )

        self.assertEqual(len(records), 2)
        self.assertEqual(clock.sleeps, [1.0, 1.0])

    def test_structured_result_serialization(self):
        result = {
            "configuration": {"seed": 17},
            "run_timing": {"run_started_at": NOW},
            "summary": {"median_staleness_ms": Decimal("12.500")},
        }
        encoded = serialize_result(result)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["configuration"]["seed"], 17)
        self.assertEqual(
            decoded["run_timing"]["run_started_at"],
            "2026-09-30T12:00:00.000000+00:00",
        )
        self.assertEqual(decoded["summary"]["median_staleness_ms"], "12.500")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            write_result(result, output)
            self.assertEqual(json.loads(output.read_text()), decoded)

    def test_nonzero_simulator_outcome_fails_the_harness(self):
        source = Source(events=[], orders=[], campaigns=[])
        convergence = SimpleNamespace(matches=True, mismatches=())
        with tempfile.TemporaryDirectory() as directory, patch.object(
            staleness, "open_connection", return_value=nullcontext(source),
        ), patch.object(
            staleness, "run_simulator", return_value=1,
        ), patch.object(
            staleness, "validate_convergence", return_value=convergence,
        ):
            with self.assertRaisesRegex(RuntimeError, "Simulator exited with status 1"):
                run_experiment(
                    {},
                    Path(directory) / "analytics.duckdb",
                    sessions=1,
                    session_interval=0.001,
                    feed_interval=0.01,
                    utc_clock=Clock(),
                )

    @unittest.skipUnless(
        os.environ.get("MARIADB_INTEGRATION") == "1",
        "requires a disposable seeded MariaDB instance",
    )
    def test_live_simulator_feed_convergence_and_staleness(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_experiment(
                database_config(),
                Path(directory) / "analytics.duckdb",
                seed=17,
                sessions=10,
                session_interval=0.03,
                feed_interval=0.05,
            )

        self.assertTrue(result["summary"]["convergence_matches"])
        self.assertTrue(result["summary"]["reconciliation_matches"])
        self.assertGreaterEqual(result["summary"]["measured_event_count"], 10)
        self.assertEqual(result["summary"]["invalid_event_count"], 0)
        self.assertEqual(
            result["reconciliation"]["source_event_count"],
            result["reconciliation"]["destination_event_count"],
        )
        self.assertGreaterEqual(len(result["feed_cycles"]), 2)
        self.assertTrue(any(
            cycle["simulator_active_after_cycle"]
            for cycle in result["feed_cycles"]
        ))


if __name__ == "__main__":
    unittest.main()
