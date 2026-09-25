"""Exercise the real session path with one controlled event-write failure."""

import argparse
from contextlib import ExitStack
from random import Random
from unittest.mock import patch

import pymysql

from simulator import runner
from simulator.config import database_config
from simulator.database import open_connection, open_connections


FAILURE_SESSION_ID = "00000000-0000-4000-8000-000000000091"
CONTROL_SESSION_ID = "00000000-0000-4000-8000-000000000092"
CAMPAIGN_ID = 1
PRODUCT_ID = 1


def _fixed_campaign(active_ids, _rng):
    if CAMPAIGN_ID not in active_ids:
        raise RuntimeError(f"Campaign {CAMPAIGN_ID} is not active")
    return CAMPAIGN_ID


def _fixed_views(active_ids, _rng):
    if PRODUCT_ID not in active_ids:
        raise RuntimeError(f"Product {PRODUCT_ID} is not active")
    return [PRODUCT_ID]


def _fixed_cart(views, _rng):
    if views != [PRODUCT_ID]:
        raise RuntimeError("The controlled view path changed unexpectedly")
    return {PRODUCT_ID: 1}


def _fail_product_view(*_args, **_kwargs):
    raise pymysql.OperationalError(
        9001, "Injected product_view failure for application-boundary experiment"
    )


def run_case(case: str, config: dict) -> str:
    session_id = FAILURE_SESSION_ID if case == "failure" else CONTROL_SESSION_ID
    checkout_calls = 0
    real_checkout = runner.checkout

    def observed_checkout(*args, **kwargs):
        nonlocal checkout_calls
        checkout_calls += 1
        return real_checkout(*args, **kwargs)

    substitutions = {
        "new_session_id": lambda: session_id,
        "choose_campaign": _fixed_campaign,
        "choose_views": _fixed_views,
        "wants_purchase": lambda _rng: True,
        "choose_cart": _fixed_cart,
        "checkout": observed_checkout,
    }
    if case == "failure":
        substitutions["insert_product_view"] = _fail_product_view

    with ExitStack() as stack:
        for name, replacement in substitutions.items():
            stack.enter_context(patch.object(runner, name, replacement))
        with open_connections(config) as (operational, events):
            outcome = runner.run_session(
                operational,
                events,
                Random(0),
                lambda: open_connection(config),
            )

    expected_outcome = "event_failure" if case == "failure" else "purchased"
    expected_checkout_calls = 0 if case == "failure" else 1
    print(
        f"Experiment case={case} session_id={session_id} outcome={outcome} "
        f"checkout_calls={checkout_calls}",
        flush=True,
    )
    if outcome != expected_outcome or checkout_calls != expected_checkout_calls:
        raise RuntimeError(
            f"Unexpected result: expected outcome={expected_outcome} "
            f"checkout_calls={expected_checkout_calls}"
        )
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description="Controlled application failure boundary")
    parser.add_argument("case", choices=("failure", "control"))
    args = parser.parse_args()
    run_case(args.case, database_config())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
