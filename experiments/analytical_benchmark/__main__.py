"""Prepare and validate the paired analytical benchmark datasets."""

import argparse
import json
import sys
from pathlib import Path

from experiments.analytical_benchmark.database import prepare_pair, validate_pair
from experiments.analytical_benchmark.measurement import json_value, measure_scale
from simulator.config import database_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "validate", "measure", "summarize"))
    parser.add_argument(
        "--scale", choices=("all", "5000", "50000"),
        help="locked session count (prepare/validate default to both scales)",
    )
    parser.add_argument("--output", type=Path, help="new JSON evidence file for measure")
    parser.add_argument("--input", type=Path, help="existing evidence JSON for summarize")
    options = parser.parse_args(argv)

    if options.action == "measure":
        if options.scale not in {"5000", "50000"}:
            parser.error("measure requires --scale 5000 or --scale 50000")
        if options.input is not None:
            parser.error("--input is only valid with summarize")
        if options.output is not None and options.output.exists():
            parser.error(f"evidence output already exists: {options.output}")
    elif options.action == "summarize":
        if options.input is None:
            parser.error("summarize requires --input evidence.json")
        if options.scale is not None or options.output is not None:
            parser.error("--scale and --output are not valid with summarize")
        try:
            evidence = json.loads(options.input.read_text())
            result = {
                key: evidence[key]
                for key in ("metadata", "method", "correctness", "summaries")
            }
        except (OSError, ValueError, KeyError) as error:
            print(f"benchmark summarize failed: {error}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2, default=json_value, sort_keys=True))
        return 0
    elif options.input is not None or options.output is not None:
        parser.error("--input/--output are only valid with measure/summarize")

    scales = ((5_000, 50_000) if options.scale in (None, "all")
              else (int(options.scale),))

    try:
        if options.action == "prepare":
            result = prepare_pair(database_config(), scales)
        elif options.action == "validate":
            result = validate_pair(database_config(), scales)
        else:
            result = measure_scale(database_config(), int(options.scale))
    except Exception as error:
        print(f"benchmark {options.action} failed: {error}", file=sys.stderr)
        return 1

    encoded = json.dumps(result, indent=2, default=json_value, sort_keys=True)
    if options.action == "measure" and options.output is not None:
        try:
            with options.output.open("x", encoding="utf-8") as evidence_file:
                evidence_file.write(encoded + "\n")
        except OSError as error:
            print(f"benchmark evidence write failed: {error}", file=sys.stderr)
            return 1
    else:
        print(encoded)
    if options.action == "validate" and not result["valid"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
