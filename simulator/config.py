"""Startup options for the commerce workload."""

import argparse
import math
from collections.abc import Sequence

DEFAULT_SESSION_INTERVAL = 3.0


def positive_interval(value: str) -> float:
    interval = float(value)
    if not math.isfinite(interval) or interval <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return interval


def positive_count(value: str) -> int:
    count = int(value)
    if count <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return count


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuous commerce simulator")
    parser.add_argument(
        "--seed", type=int, default=None,
        help="seed behavioural choices; session UUIDs remain fresh",
    )
    parser.add_argument(
        "--session-interval", type=positive_interval, default=DEFAULT_SESSION_INTERVAL,
        help="target seconds between session starts (default: 3)",
    )
    parser.add_argument(
        "--sessions", type=positive_count, default=None,
        help="stop after this many sessions; omitted means run until interrupted",
    )
    return parser.parse_args(argv)
