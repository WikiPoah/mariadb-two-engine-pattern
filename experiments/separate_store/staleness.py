"""Controlled periodic-feed and event-staleness measurement harness."""

import argparse
import json
import math
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb

from experiments.separate_store.feed import FeedCycle, initialize_destination, run_feed_cycle
from experiments.separate_store.queries import validate_convergence
from simulator.config import database_config
from simulator.database import open_connection
from simulator.runner import run as run_simulator


DEFAULT_SEED = 17
DEFAULT_SESSIONS = 20
DEFAULT_SESSION_INTERVAL = 0.1
DEFAULT_FEED_INTERVAL = 2.0
MAX_CATCH_UP_CYCLES = 5


@dataclass(frozen=True)
class ScheduledCycle:
    cycle: FeedCycle
    scheduled_monotonic: float
    started_monotonic: float
    completed_monotonic: float
    cadence_overrun_ms: float
    missed_intervals: int
    workload_running_after_cycle: bool


def median(values) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float((ordered[middle - 1] + ordered[middle]) / 2)


def nearest_rank_percentile(values, percentile: float) -> float | None:
    """Return the nearest-rank percentile: sorted[ceil(p / 100 * n) - 1]."""
    if not 0 < percentile <= 100:
        raise ValueError("Percentile must be greater than zero and at most 100")
    ordered = sorted(values)
    if not ordered:
        return None
    rank = math.ceil(percentile / 100 * len(ordered))
    return float(ordered[rank - 1])


def run_scheduled_feed(
    feed_interval: float,
    workload_running,
    feed_once,
    caught_up,
    *,
    monotonic_clock=time.monotonic,
    sleeper=time.sleep,
    max_catch_up_cycles=MAX_CATCH_UP_CYCLES,
) -> list[ScheduledCycle]:
    """Run on an intended cadence, skipping missed slots after an overrun."""
    deadline = monotonic_clock() + feed_interval
    records = []
    catch_up_cycles = 0
    while True:
        remaining = deadline - monotonic_clock()
        if remaining > 0:
            sleeper(remaining)

        started = monotonic_clock()
        cycle = feed_once()
        completed = monotonic_clock()
        next_deadline = deadline + feed_interval
        cadence_overrun_ms = max(0.0, (completed - next_deadline) * 1000)
        missed_intervals = 0
        while next_deadline < completed:
            next_deadline += feed_interval
            missed_intervals += 1
        workload_running_after_cycle = workload_running()
        records.append(ScheduledCycle(
            cycle=cycle,
            scheduled_monotonic=deadline,
            started_monotonic=started,
            completed_monotonic=completed,
            cadence_overrun_ms=round(cadence_overrun_ms, 3),
            missed_intervals=missed_intervals,
            workload_running_after_cycle=workload_running_after_cycle,
        ))

        if not workload_running_after_cycle:
            catch_up_cycles += 1
            if caught_up():
                return records
            if catch_up_cycles >= max_catch_up_cycles:
                raise RuntimeError("Destination did not converge after the simulator stopped")
        deadline = next_deadline


def _utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _parsed_utc(timestamp: str | None) -> datetime | None:
    if timestamp is None:
        return None
    return _utc(datetime.fromisoformat(timestamp))


def event_observations(destination, after_event_id: int) -> list[dict]:
    rows = destination.execute(
        """
        SELECT e.source_event_id, e.session_id, e.event_type, e.occurred_at,
               e.first_visible_batch_id,
               CAST(b.destination_visible_at AS VARCHAR)
        FROM activity_events_copy AS e
        LEFT JOIN feed_batches AS b ON b.batch_id = e.first_visible_batch_id
        WHERE e.source_event_id > ?
        ORDER BY e.source_event_id
        """,
        (after_event_id,),
    ).fetchall()
    observations = []
    for event_id, session_id, event_type, occurred_at, batch_id, visible_at in rows:
        visible_at = _parsed_utc(visible_at)
        raw_staleness_ms = None
        invalid_reason = None
        if visible_at is None:
            invalid_reason = "missing_destination_visibility"
        else:
            raw_staleness_ms = round(
                (_utc(visible_at) - _utc(occurred_at)).total_seconds() * 1000,
                3,
            )
            if raw_staleness_ms < 0:
                invalid_reason = "negative_staleness"
        valid = invalid_reason is None
        observations.append({
            "source_event_id": int(event_id),
            "session_id": session_id,
            "event_type": event_type,
            "occurred_at": _utc(occurred_at),
            "first_visible_batch_id": int(batch_id),
            "destination_visible_at": visible_at,
            "measurement_valid": valid,
            "invalid_reason": invalid_reason,
            "raw_staleness_ms": raw_staleness_ms,
            "staleness_ms": raw_staleness_ms if valid else None,
        })
    return observations


def summarize_observations(observations: list[dict]) -> dict:
    values = [
        item["staleness_ms"] for item in observations
        if item["measurement_valid"]
    ]
    return {
        "measured_event_count": len(values),
        "invalid_event_count": len(observations) - len(values),
        "median_staleness_ms": median(values),
        "p95_staleness_ms": nearest_rank_percentile(values, 95),
        "maximum_staleness_ms": None if not values else float(max(values)),
    }


def _source_ids(source, sql: str, checkpoint: int) -> list[int]:
    with source.cursor() as cursor:
        cursor.execute(sql, (checkpoint,))
        return [int(row[0]) for row in cursor.fetchall()]


def _differences(expected: list[int], actual: list[int]) -> tuple[list[int], list[int]]:
    expected_counts = Counter(expected)
    actual_counts = Counter(actual)
    return (
        sorted((expected_counts - actual_counts).elements()),
        sorted((actual_counts - expected_counts).elements()),
    )


def reconcile_controlled_records(
    source,
    destination,
    *,
    baseline_event_id: int,
    baseline_order_id: int,
) -> dict:
    """Compare exact controlled-workload ID multisets; IDs are not general CDC."""
    source_events = _source_ids(
        source,
        "SELECT event_id FROM activity_events WHERE event_id > %s ORDER BY event_id",
        baseline_event_id,
    )
    source_orders = _source_ids(
        source,
        "SELECT order_id FROM orders AS o WHERE order_id > %s ORDER BY order_id",
        baseline_order_id,
    )
    destination_events = [
        int(row[0]) for row in destination.execute(
            "SELECT source_event_id FROM activity_events_copy "
            "WHERE source_event_id > ? ORDER BY source_event_id",
            (baseline_event_id,),
        ).fetchall()
    ]
    destination_orders = [
        int(row[0]) for row in destination.execute(
            "SELECT order_id FROM order_facts WHERE order_id > ? ORDER BY order_id",
            (baseline_order_id,),
        ).fetchall()
    ]
    event_missing, event_extra = _differences(source_events, destination_events)
    order_missing, order_extra = _differences(source_orders, destination_orders)
    event_checkpoint, order_checkpoint = destination.execute(
        "SELECT event_checkpoint, order_checkpoint FROM feed_batches "
        "ORDER BY batch_id DESC LIMIT 1"
    ).fetchone()
    source_event_max = max(source_events, default=baseline_event_id)
    source_order_max = max(source_orders, default=baseline_order_id)
    checkpoint_match = (
        int(event_checkpoint) == source_event_max
        and int(order_checkpoint) == source_order_max
    )
    matches = not any((event_missing, event_extra, order_missing, order_extra))
    return {
        "matches": matches and checkpoint_match,
        "source_event_count": len(source_events),
        "destination_event_count": len(destination_events),
        "missing_event_ids": event_missing,
        "extra_event_ids": event_extra,
        "source_order_count": len(source_orders),
        "destination_order_count": len(destination_orders),
        "missing_order_ids": order_missing,
        "extra_order_ids": order_extra,
        "event_checkpoint": int(event_checkpoint),
        "order_checkpoint": int(order_checkpoint),
        "source_event_max": source_event_max,
        "source_order_max": source_order_max,
        "checkpoint_match": checkpoint_match,
    }


def _cycle_record(destination, scheduled: ScheduledCycle) -> dict:
    row = destination.execute(
        """
        SELECT CAST(started_at AS VARCHAR), CAST(source_read_at AS VARCHAR),
               CAST(commit_started_at AS VARCHAR),
               CAST(destination_visible_at AS VARCHAR), events_loaded, orders_loaded,
               campaigns_loaded, event_checkpoint, order_checkpoint
        FROM feed_batches WHERE batch_id = ?
        """,
        (scheduled.cycle.batch_id,),
    ).fetchone()
    return {
        "batch_id": scheduled.cycle.batch_id,
        "started_at": _parsed_utc(row[0]),
        "source_read_at": _parsed_utc(row[1]),
        "commit_started_at": _parsed_utc(row[2]),
        "destination_visible_at": _parsed_utc(row[3]),
        "visibility_metadata_valid": row[3] is not None,
        "events_loaded": int(row[4]),
        "orders_loaded": int(row[5]),
        "campaigns_loaded": int(row[6]),
        "event_checkpoint": int(row[7]),
        "order_checkpoint": int(row[8]),
        "cycle_duration_ms": round(
            (scheduled.completed_monotonic - scheduled.started_monotonic) * 1000,
            3,
        ),
        "cadence_overrun_ms": scheduled.cadence_overrun_ms,
        "missed_intervals": scheduled.missed_intervals,
        "simulator_active_after_cycle": scheduled.workload_running_after_cycle,
    }


def run_experiment(
    db_config: dict,
    destination_path,
    *,
    seed=DEFAULT_SEED,
    sessions=DEFAULT_SESSIONS,
    session_interval=DEFAULT_SESSION_INTERVAL,
    feed_interval=DEFAULT_FEED_INTERVAL,
    utc_clock=lambda: datetime.now(timezone.utc),
    monotonic_clock=time.monotonic,
    sleeper=time.sleep,
) -> dict:
    destination_path = Path(destination_path)
    if destination_path.exists():
        raise FileExistsError(f"Destination already exists: {destination_path}")

    destination = duckdb.connect(str(destination_path))
    try:
        initialize_destination(destination)
        with open_connection(db_config) as source:
            baseline = run_feed_cycle(source, destination, clock=utc_clock)
            baseline_convergence = validate_convergence(source, destination)
            if not baseline_convergence.matches:
                raise RuntimeError(
                    f"Baseline did not converge: {baseline_convergence.mismatches}"
                )

            simulator_state = {"exit_code": None, "completed_at": None}
            options = SimpleNamespace(
                seed=seed, sessions=sessions, session_interval=session_interval,
            )

            def simulator_worker():
                simulator_state["exit_code"] = run_simulator(options, db_config)
                simulator_state["completed_at"] = utc_clock()

            run_started_at = utc_clock()
            simulator_thread = threading.Thread(
                target=simulator_worker,
                name="controlled-commerce-simulator",
            )
            simulator_thread.start()

            def caught_up():
                reconciliation = reconcile_controlled_records(
                    source,
                    destination,
                    baseline_event_id=baseline.event_checkpoint,
                    baseline_order_id=baseline.order_checkpoint,
                )
                convergence = validate_convergence(source, destination)
                return reconciliation["matches"] and convergence.matches

            try:
                scheduled_cycles = run_scheduled_feed(
                    feed_interval,
                    simulator_thread.is_alive,
                    lambda: run_feed_cycle(source, destination, clock=utc_clock),
                    caught_up,
                    monotonic_clock=monotonic_clock,
                    sleeper=sleeper,
                )
            finally:
                simulator_thread.join()

            if simulator_state["exit_code"] != 0:
                raise RuntimeError(
                    f"Simulator exited with status {simulator_state['exit_code']}"
                )
            final_reconciliation = reconcile_controlled_records(
                source,
                destination,
                baseline_event_id=baseline.event_checkpoint,
                baseline_order_id=baseline.order_checkpoint,
            )
            final_convergence = validate_convergence(source, destination)
            if not final_reconciliation["matches"] or not final_convergence.matches:
                raise RuntimeError("Final source/destination reconciliation failed")
            caught_up_at = utc_clock()

            observations = event_observations(
                destination, baseline.event_checkpoint,
            )
            summary = summarize_observations(observations)
            summary.update({
                "convergence_matches": final_convergence.matches,
                "convergence_mismatches": list(final_convergence.mismatches),
                "reconciliation_matches": final_reconciliation["matches"],
            })
            return {
                "configuration": {
                    "seed": seed,
                    "sessions": sessions,
                    "simulator_session_interval_seconds": session_interval,
                    "feed_interval_seconds": feed_interval,
                },
                "run_timing": {
                    "run_started_at": run_started_at,
                    "simulator_completed_at": simulator_state["completed_at"],
                    "caught_up_at": caught_up_at,
                },
                "baseline": {
                    "batch_id": baseline.batch_id,
                    "event_checkpoint": baseline.event_checkpoint,
                    "order_checkpoint": baseline.order_checkpoint,
                },
                "feed_cycles": [
                    _cycle_record(destination, item) for item in scheduled_cycles
                ],
                "event_observations": observations,
                "summary": summary,
                "reconciliation": final_reconciliation,
            }
    finally:
        destination.close()


def _json_value(value):
    if isinstance(value, datetime):
        return _utc(value).isoformat(timespec="microseconds")
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def serialize_result(result: dict) -> str:
    return json.dumps(result, default=_json_value, indent=2, sort_keys=True) + "\n"


def write_result(result: dict, output_path) -> None:
    Path(output_path).write_text(serialize_result(result), encoding="utf-8")


def _positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Measure periodic-feed staleness in a standalone DuckDB store",
    )
    parser.add_argument("--destination", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--sessions", type=_positive_int, default=DEFAULT_SESSIONS)
    parser.add_argument(
        "--session-interval", type=_positive_float,
        default=DEFAULT_SESSION_INTERVAL,
    )
    parser.add_argument(
        "--feed-interval", type=_positive_float, default=DEFAULT_FEED_INTERVAL,
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    options = parse_args(argv)
    result = run_experiment(
        database_config(),
        options.destination,
        seed=options.seed,
        sessions=options.sessions,
        session_interval=options.session_interval,
        feed_interval=options.feed_interval,
    )
    write_result(result, options.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
