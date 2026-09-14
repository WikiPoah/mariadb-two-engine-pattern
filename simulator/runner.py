"""Sequential session orchestration with independent event and purchase writes."""

from datetime import datetime, timedelta, timezone
from random import Random
from time import monotonic, sleep

import pymysql

from simulator.checkout import CheckoutRejected, CheckoutUncertain, checkout
from simulator.database import (
    active_campaign_ids,
    active_product_ids,
    insert_product_view,
    insert_session_start,
    open_connection,
    open_connections,
)
from simulator.workload import (
    choose_campaign,
    choose_cart,
    choose_views,
    new_session_id,
    wants_purchase,
)


def next_timestamp(previous=None):
    now = datetime.now(timezone.utc)
    # Equal readings or a backwards wall-clock adjustment must not reorder events.
    return now if previous is None else max(now, previous + timedelta(microseconds=1))


def run_session(operational, events, rng, recovery_connection):
    session_id = new_session_id()
    campaign = choose_campaign(active_campaign_ids(operational), rng)
    timestamp = next_timestamp()
    view_count = 0

    def report(outcome, detail=""):
        attribution = 'organic' if campaign is None else f'campaign={campaign}'
        print(f'{session_id} {attribution} views={view_count} {outcome} {detail}'.rstrip(), flush=True)
        return outcome

    try:
        insert_session_start(events, session_id, campaign, timestamp)
    except pymysql.MySQLError as error:
        return report('event_failure', f'session_start: {error}')

    views = choose_views(active_product_ids(operational), rng)
    for product_id in views:
        timestamp = next_timestamp(timestamp)
        try:
            insert_product_view(events, session_id, campaign, timestamp, product_id)
        except pymysql.MySQLError as error:
            return report('event_failure', f'product_view: {error}')
        view_count += 1

    if not wants_purchase(rng):
        return report('abandoned')
    cart = choose_cart(views, rng)
    if not cart:
        return report('abandoned')
    try:
        order_id = checkout(
            operational, session_id, campaign, next_timestamp(timestamp), cart,
            recovery_connection=recovery_connection,
        )
    except CheckoutRejected as error:
        return report('rejected', str(error))
    except CheckoutUncertain as error:
        report('uncertain', str(error))
        raise
    return report('purchased', f'order_id={order_id}')


def run(options, db_config) -> int:
    rng = Random(options.seed)
    counts = dict(attempted=0, abandoned=0, purchased=0, rejected=0, event_failure=0, uncertain=0)
    try:
        with open_connections(db_config) as (operational, events):
            while options.sessions is None or counts['attempted'] < options.sessions:
                started = monotonic()
                counts['attempted'] += 1
                outcome = run_session(
                    operational, events, rng, lambda: open_connection(db_config),
                )
                counts[outcome] += 1
                if outcome == 'event_failure':
                    # Check usability without silently reconnecting or retrying the failed write.
                    operational.ping(reconnect=False)
                    events.ping(reconnect=False)
                if options.sessions is not None and counts['attempted'] >= options.sessions:
                    break
                # Schedule from the actual start, never from accumulated missed deadlines.
                elapsed = monotonic() - started
                remaining = options.session_interval - elapsed
                if remaining > 0:
                    sleep(remaining)
    except CheckoutUncertain:
        counts['uncertain'] += 1
        return 1
    except KeyboardInterrupt:
        print('Interrupted; any in-flight purchase may require inspection.', flush=True)
        return 130
    except Exception as error:
        print(f'Simulator stopped: {type(error).__name__}: {error}', flush=True)
        return 1
    finally:
        print('Summary: ' + ' '.join(f'{key}={value}' for key, value in counts.items()), flush=True)
    return 0
