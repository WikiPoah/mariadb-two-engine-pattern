import os
import json
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

import pymysql

from experiments.analytical_benchmark import measurement
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


class StatisticsTests(unittest.TestCase):
    def test_median_min_max_mad_and_nearest_rank_p95(self):
        values = list(range(1, 21))
        self.assertEqual(measurement.median(values), Decimal("10.5"))
        self.assertEqual(measurement.minimum(values), 1)
        self.assertEqual(measurement.maximum(values), 20)
        self.assertEqual(measurement.median_absolute_deviation(values), Decimal("5"))
        self.assertEqual(measurement.nearest_rank_p95(values), 19)

    def test_statistics_reject_empty_samples(self):
        for function in (measurement.minimum, measurement.maximum, measurement.median,
                         measurement.median_absolute_deviation,
                         measurement.nearest_rank_p95):
            with self.subTest(function=function.__name__):
                with self.assertRaises(ValueError):
                    function([])


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self.rows = []
        self.one = None
        self.current_query = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, parameters=None):
        self.current_query = self._query_name(sql)
        self.connection.executions.append((self.current_query, sql, parameters))
        self.connection.events.append(f"execute:{self.current_query}")
        self.one = None
        self.rows = []
        if "SELECT @@session.time_zone" in sql:
            self.one = ("+00:00", "OFF")
        elif "SELECT VERSION()" in sql:
            self.one = ("12.3.3-MariaDB-ubu2404", "MariaDB Server", "aarch64", "Linux", self.connection.database)
        elif "information_schema.PLUGINS" in sql:
            self.description = [(name,) for name in
                                ("PLUGIN_NAME", "PLUGIN_VERSION", "PLUGIN_STATUS", "PLUGIN_LIBRARY")]
            self.rows = [("DUCKDB", "1.0", "ACTIVE", "ha_duckdb.so")]
        elif sql.startswith("SHOW "):
            self.description = [("Variable_name",), ("Value",)]
            self.rows = [("query_cache_type", "OFF")]
        elif sql.startswith("EXPLAIN "):
            self.description = [("id",), ("select_type",), ("table",)]
            self.rows = [(1, "SIMPLE", "activity_events")]
        else:
            self.description = [("value",)]
            self.rows = [(1,)]
        return 1

    def fetchone(self):
        self.connection.events.append(f"fetchone:{self.current_query}")
        return self.one

    def fetchall(self):
        self.connection.events.append(f"fetchall:{self.current_query}")
        return self.rows

    @staticmethod
    def _query_name(sql):
        if sql.startswith("EXPLAIN "):
            return "explain"
        if "SHOW INDEX" in sql:
            return "show_index"
        if "SELECT @@session.time_zone" in sql:
            return "session_settings"
        if "SELECT VERSION()" in sql:
            return "server_metadata"
        if "information_schema.PLUGINS" in sql:
            return "plugin_metadata"
        if "SHOW " in sql:
            return "variables"
        if "COUNT(DISTINCT CASE" in sql:
            return "overview_activity"
        if "LIMIT %s" in sql:
            return "recent_activity"
        if "WITH session_history" in sql:
            return "campaigns"
        return "session_setup"


class _FakeConnection:
    def __init__(self, database, events):
        self.database = database
        self.events = events
        self.executions = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True
        return False

    def cursor(self):
        return _FakeCursor(self)


class _FakeClock:
    def __init__(self, events):
        self.events = events
        self.value = 0
        self.calls = 0

    def __call__(self):
        self.events.append("clock")
        self.value += 10
        self.calls += 1
        return self.value


class MeasurementTests(unittest.TestCase):
    def test_pymysql_version_uses_installed_distribution_metadata(self):
        with patch.object(pymysql, "__version__", "2.2.8"), patch.object(
            measurement, "distribution_version", return_value="1.2.0"
        ) as distribution_version:
            self.assertEqual(
                measurement._installed_distribution_version("PyMySQL"), "1.2.0"
            )
        distribution_version.assert_called_once_with("PyMySQL")

    def test_missing_distribution_metadata_is_recorded_as_unavailable(self):
        with patch.object(
            measurement,
            "distribution_version",
            side_effect=PackageNotFoundError("PyMySQL"),
        ):
            self.assertIsNone(
                measurement._installed_distribution_version("PyMySQL")
            )

    def _run_fake(self, validator=None):
        connections = []
        configs = []
        events = []

        def connect(config):
            configs.append(config)
            connection = _FakeConnection(config["database"], events)
            connections.append(connection)
            return connection

        validations = []

        def validate(_config, *, scales):
            validations.append(scales)
            if validator:
                return validator(len(validations))
            return {"valid": True, "mismatches": []}

        clock = _FakeClock(events)
        evidence = measurement.measure_scale(
            {"host": "mariadb"}, 5_000, connection_factory=connect,
            validation_function=validate, clock_ns=clock,
        )
        return evidence, connections, configs, validations, clock, events

    def test_measurement_counts_warmups_samples_connections_and_clock_boundary(self):
        evidence, connections, configs, validations, clock, events = self._run_fake()
        self.assertEqual(len(connections), 2)
        self.assertEqual(len({id(connection) for connection in connections}), 2)
        self.assertTrue(all(connection.closed for connection in connections))
        self.assertEqual([config["database"] for config in configs], [
            "analytical_bench_5000_two_engine", "analytical_bench_5000_innodb_only",
        ])
        self.assertEqual(validations, [(5_000,), (5_000,)])
        self.assertEqual(len(evidence["samples"]), 120)
        self.assertEqual(clock.calls, 240)
        self.assertTrue(all(sample["elapsed_ns"] == 10 for sample in evidence["samples"]))
        self.assertTrue(all(sample["fetched_rows"] == 1 for sample in evidence["samples"]))

        executions = [execution for connection in connections
                      for execution in connection.executions]
        for connection in connections:
            for query in measurement.QUERY_FILES:
                self.assertEqual(sum(item[0] == query for item in connection.executions), 23)
        self.assertEqual(evidence["method"]["warmups_per_query_variant"], 3)
        self.assertEqual(evidence["method"]["measured_repetitions_per_query_variant"], 20)
        self.assertTrue(evidence["correctness"][
            "measured_result_digests_match_across_repetitions_and_variants"
        ])
        for query in measurement.QUERY_FILES:
            for variant in measurement.VARIANTS:
                self.assertEqual(evidence["summaries"][query][variant]["n"], 20)

        clock_positions = [index for index, event in enumerate(events) if event == "clock"]
        self.assertEqual(len(clock_positions), 240)
        for index in clock_positions[::2]:
            self.assertTrue(events[index + 1].startswith("execute:"))
            self.assertEqual(events[index + 2],
                             events[index + 1].replace("execute:", "fetchall:"))
            self.assertEqual(events[index + 3], "clock")
        for connection in connections:
            self.assertTrue(any("SET SESSION time_zone" in sql
                                for _, sql, _ in connection.executions))
            self.assertTrue(any("SET SESSION query_cache_type = OFF" in sql
                                for _, sql, _ in connection.executions))

    def test_order_is_deterministic_and_variant_first_alternates(self):
        self.assertEqual(measurement.measured_order(0), measurement.measured_order(0))
        self.assertNotEqual(measurement.measured_order(0)[1], measurement.measured_order(1)[1])
        self.assertEqual(measurement.measured_order(0)[0], ["two_engine", "innodb_only"])
        self.assertEqual(measurement.measured_order(1)[0], ["innodb_only", "two_engine"])
        evidence, *_ = self._run_fake()
        orders = evidence["method"]["ordering_policy"]["repetitions"]
        self.assertEqual(len(orders), 20)
        self.assertEqual(orders[0]["variant_order"], ["two_engine", "innodb_only"])
        self.assertEqual(orders[1]["variant_order"], ["innodb_only", "two_engine"])

    def test_pre_measurement_validation_failure_prevents_connections_and_timing(self):
        def connect(_):
            self.fail("connection must not open before validation")

        with self.assertRaisesRegex(RuntimeError, "before measurement"):
            measurement.measure_scale(
                {}, 5_000, connection_factory=connect,
                validation_function=lambda *_args, **_kwargs: {"valid": False},
                clock_ns=lambda: self.fail("timer must not run before validation"),
            )

    def test_post_measurement_validation_failure_is_surfaced(self):
        calls = []

        def validate(*_args, **_kwargs):
            calls.append(True)
            return {"valid": len(calls) == 1}

        with self.assertRaisesRegex(RuntimeError, "after measurement"):
            self._run_fake(validator=validate)
        self.assertEqual(len(calls), 2)

    def test_scale_rejection_and_result_json_serialization(self):
        with self.assertRaises(ValueError):
            measurement.measure_scale({}, 10_000)
        values = {
            "decimal": Decimal("1.250"),
            "naive_utc": datetime(2026, 9, 13, 12, 0, 0, 123456),
            "aware_utc": datetime(2026, 9, 13, 13, 0, 0, 123456,
                                   tzinfo=timezone.utc),
            "null": None,
        }
        encoded = json.dumps(values, default=measurement.json_value)
        self.assertIn('"decimal": "1.250"', encoded)
        self.assertIn("2026-09-13T12:00:00.123456+00:00", encoded)
        self.assertIn('"null": null', encoded)
        evidence, *_ = self._run_fake()
        evidence_json = json.dumps(evidence, default=measurement.json_value)
        self.assertEqual(len(json.loads(evidence_json)["samples"]), 120)

    def test_result_digest_normalizes_representations_but_preserves_values_and_order(self):
        naive = datetime(2026, 9, 13, 12, 0, 0, 123456)
        aware = naive.replace(tzinfo=timezone.utc)
        left = [(Decimal("10.0"), naive, None), (Decimal("2.50"), None, "row-2")]
        same_values = [(Decimal("10.00"), aware, None), (Decimal("2.500"), None, "row-2")]

        self.assertEqual(measurement.result_digest(left),
                         measurement.result_digest(same_values))
        changed_value = [(Decimal("10.01"), aware, None), *same_values[1:]]
        self.assertNotEqual(measurement.result_digest(left),
                            measurement.result_digest(changed_value))
        self.assertNotEqual(measurement.result_digest(left),
                            measurement.result_digest(list(reversed(left))))

    def test_measure_cli_rejects_unlocked_scale_before_database_access(self):
        from experiments.analytical_benchmark.__main__ import main

        with open(os.devnull, "w") as sink, redirect_stderr(sink):
            with self.assertRaises(SystemExit) as raised:
                main(["measure", "--scale", "10000"])
        self.assertEqual(raised.exception.code, 2)

    def test_measure_cli_rejects_existing_output_before_database_access(self):
        from experiments.analytical_benchmark.__main__ import main

        with TemporaryDirectory() as directory:
            output = Path(directory) / "existing.json"
            output.write_text("{}", encoding="utf-8")
            with patch("experiments.analytical_benchmark.__main__.database_config") as config, \
                    patch("experiments.analytical_benchmark.__main__.measure_scale") as runner, \
                    open(os.devnull, "w") as sink, redirect_stderr(sink):
                with self.assertRaises(SystemExit) as raised:
                    main(["measure", "--scale", "5000", "--output", str(output)])
            self.assertEqual(raised.exception.code, 2)
            config.assert_not_called()
            runner.assert_not_called()
            self.assertEqual(output.read_text(encoding="utf-8"), "{}")


if __name__ == "__main__":
    unittest.main()
