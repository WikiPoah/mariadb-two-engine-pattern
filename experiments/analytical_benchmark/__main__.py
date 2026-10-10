"""Prepare and validate the paired analytical benchmark datasets."""

import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal

from experiments.analytical_benchmark.database import prepare_pair, validate_pair
from simulator.config import database_config


def _json_value(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot encode {type(value).__name__}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "validate"))
    parser.add_argument(
        "--scale", choices=("all", "5000", "50000"), default="all",
        help="session count to prepare or validate (default: both locked scales)",
    )
    options = parser.parse_args(argv)
    scales = (5_000, 50_000) if options.scale == "all" else (int(options.scale),)

    try:
        result = (prepare_pair(database_config(), scales)
                  if options.action == "prepare"
                  else validate_pair(database_config(), scales))
    except Exception as error:
        print(f"benchmark {options.action} failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, default=_json_value, sort_keys=True))
    if options.action == "validate" and not result["valid"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
