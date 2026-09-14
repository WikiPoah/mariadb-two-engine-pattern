"""Startup options for the commerce workload."""

import argparse
import math
import os
from collections.abc import Mapping, Sequence

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


def database_config(environ: Mapping[str, str] | None = None) -> dict:
    """Read process environment; Compose's .env file is not loaded by Python."""
    env = os.environ if environ is None else environ
    user = env.get("MARIADB_USER", "root")
    password = env.get("MARIADB_PASSWORD")
    if password is None and user == "root":
        password = env.get("MARIADB_ROOT_PASSWORD")
    if not password:
        raise ValueError("Set MARIADB_PASSWORD (or MARIADB_ROOT_PASSWORD for root)")
    port = int(env.get("MARIADB_PORT", "3306"))
    if not 1 <= port <= 65535:
        raise ValueError("MARIADB_PORT must be between 1 and 65535")
    config = {
        "host": env.get("MARIADB_HOST", "mariadb"),
        "port": port,
        "user": user,
        "password": password,
        "database": env.get("MARIADB_DATABASE", "commerce_analytics"),
    }
    for name in ("host", "user", "database"):
        if not config[name].strip():
            raise ValueError(f"MARIADB_{name.upper()} must not be blank")
    return config
