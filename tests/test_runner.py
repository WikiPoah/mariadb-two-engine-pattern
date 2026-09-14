import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pymysql

from simulator import runner
from simulator.checkout import CheckoutRejected, CheckoutUncertain


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.functions = {}
        for name in ('new_session_id', 'active_campaign_ids', 'choose_campaign',
                     'active_product_ids', 'choose_views', 'insert_session_start',
                     'insert_product_view', 'wants_purchase', 'choose_cart', 'checkout'):
            patcher = patch.object(runner, name)
            self.functions[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.functions['new_session_id'].return_value = 'session'
        self.functions['choose_campaign'].return_value = 2
        self.functions['choose_views'].return_value = [3, 1, 3]
        self.functions['wants_purchase'].return_value = True
        self.functions['choose_cart'].return_value = {3: 2}
        self.functions['checkout'].return_value = 42
        self.operational, self.events, self.rng, self.recovery = [MagicMock() for _ in range(4)]
        self.output = io.StringIO()

    def session(self):
        with redirect_stdout(self.output):
            return runner.run_session(self.operational, self.events, self.rng, self.recovery)

    def test_purchase_preserves_attribution_cart_connections_and_time_order(self):
        with patch.object(runner, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 9, 14, tzinfo=timezone.utc)
            self.assertEqual(self.session(), 'purchased')
        start = self.functions['insert_session_start'].call_args.args
        self.assertEqual(start[:3], (self.events, 'session', 2))
        times = [start[3]]
        for call in self.functions['insert_product_view'].call_args_list:
            self.assertEqual(call.args[:3], (self.events, 'session', 2))
            times.append(call.args[3])
        purchase = self.functions['checkout'].call_args
        self.assertEqual(purchase.args[:3], (self.operational, 'session', 2))
        self.assertEqual(purchase.args[4], {3: 2})
        self.assertIs(purchase.kwargs['recovery_connection'], self.recovery)
        times.append(purchase.args[3])
        self.assertTrue(all(left < right for left, right in zip(times, times[1:])))
        self.assertTrue(all(time.tzinfo == timezone.utc for time in times))
        self.functions['choose_cart'].assert_called_once_with([3, 1, 3], self.rng)
        self.functions['choose_campaign'].assert_called_once_with(
            self.functions['active_campaign_ids'].return_value, self.rng)
        self.assertIn('views=3 purchased order_id=42', self.output.getvalue())

    def test_failed_start_prevents_product_reads_views_and_checkout(self):
        self.functions['insert_session_start'].side_effect = pymysql.OperationalError('failed')
        self.assertEqual(self.session(), 'event_failure')
        for name in ('active_product_ids', 'insert_product_view', 'checkout'):
            self.functions[name].assert_not_called()
        self.functions['insert_session_start'].assert_called_once()

    def test_failed_view_stops_remaining_views_and_checkout(self):
        self.functions['insert_product_view'].side_effect = pymysql.OperationalError('failed')
        self.assertEqual(self.session(), 'event_failure')
        self.functions['insert_product_view'].assert_called_once()
        self.functions['checkout'].assert_not_called()

    def test_abandonment_and_empty_cart(self):
        self.functions['wants_purchase'].return_value = False
        self.assertEqual(self.session(), 'abandoned')
        self.functions['choose_cart'].assert_not_called()
        self.functions['wants_purchase'].return_value = True
        self.functions['choose_views'].return_value = []
        self.functions['choose_cart'].return_value = {}
        self.assertEqual(self.session(), 'abandoned')
        self.functions['checkout'].assert_not_called()

    def test_rejection_is_outcome_but_uncertainty_propagates(self):
        self.functions['checkout'].side_effect = CheckoutRejected('stock')
        self.assertEqual(self.session(), 'rejected')
        self.functions['checkout'].side_effect = CheckoutUncertain('lost commit')
        with self.assertRaises(CheckoutUncertain):
            self.session()
        self.assertIn('uncertain', self.output.getvalue())

    def test_clock_moving_backwards_still_advances(self):
        previous = datetime(2026, 9, 14, tzinfo=timezone.utc)
        with patch.object(runner, 'datetime') as clock:
            clock.now.return_value = previous - timedelta(seconds=5)
            self.assertEqual(runner.next_timestamp(previous), previous + timedelta(microseconds=1))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.mocks = {}
        for name in ('open_connections', 'open_connection', 'run_session', 'Random', 'monotonic', 'sleep'):
            patcher = patch.object(runner, name)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.operational, self.events = MagicMock(), MagicMock()
        self.mocks['open_connections'].return_value.__enter__.return_value = (
            self.operational, self.events)
        self.mocks['monotonic'].return_value = 0.0
        self.options = SimpleNamespace(seed=17, sessions=5, session_interval=3.0)
        self.output = io.StringIO()

    def run_simulator(self):
        with redirect_stdout(self.output):
            return runner.run(self.options, {'host': 'mariadb'})

    def test_finite_run_counters_single_rng_recovery_factory_and_pacing(self):
        self.mocks['run_session'].side_effect = [
            'abandoned', 'purchased', 'rejected', 'event_failure', 'purchased']
        self.assertEqual(self.run_simulator(), 0)
        self.assertEqual(self.mocks['run_session'].call_count, 5)
        self.mocks['Random'].assert_called_once_with(17)
        for call in self.mocks['run_session'].call_args_list:
            self.assertEqual(call.args[:3], (self.operational, self.events,
                                           self.mocks['Random'].return_value))
        factory = self.mocks['run_session'].call_args.args[3]
        factory()
        self.mocks['open_connection'].assert_called_once_with({'host': 'mariadb'})
        self.assertEqual(self.mocks['sleep'].call_count, 4)
        self.operational.ping.assert_called_once_with(reconnect=False)
        self.events.ping.assert_called_once_with(reconnect=False)
        self.assertIn('attempted=5 abandoned=1 purchased=2 rejected=1 event_failure=1 uncertain=0',
                      self.output.getvalue())
        self.mocks['open_connections'].return_value.__exit__.assert_called_once()

    def test_slow_session_does_not_create_catch_up_schedule(self):
        self.options.sessions = 3
        self.mocks['run_session'].return_value = 'abandoned'
        self.mocks['monotonic'].side_effect = [0, 5, 5, 6, 8]
        self.assertEqual(self.run_simulator(), 0)
        self.mocks['sleep'].assert_called_once_with(2)

    def test_uncertain_checkout_terminates_without_next_session(self):
        self.mocks['run_session'].side_effect = CheckoutUncertain('unknown')
        self.assertEqual(self.run_simulator(), 1)
        self.mocks['run_session'].assert_called_once()
        self.assertIn('uncertain=1', self.output.getvalue())

    def test_unusable_connection_after_event_failure_terminates(self):
        self.mocks['run_session'].return_value = 'event_failure'
        self.events.ping.side_effect = pymysql.OperationalError('disconnected')
        self.assertEqual(self.run_simulator(), 1)
        self.mocks['run_session'].assert_called_once()
        self.assertIn('event_failure=1', self.output.getvalue())
        self.assertIn('Simulator stopped:', self.output.getvalue())

    def test_unlimited_run_interrupt_prints_summary_and_closes_connections(self):
        self.options.sessions = None
        self.mocks['run_session'].side_effect = ['abandoned', KeyboardInterrupt()]
        self.assertEqual(self.run_simulator(), 130)
        self.assertIn('attempted=2 abandoned=1', self.output.getvalue())
        self.mocks['open_connections'].return_value.__exit__.assert_called_once()

    def test_unexpected_error_terminates(self):
        self.mocks['run_session'].side_effect = RuntimeError('unexpected')
        self.assertEqual(self.run_simulator(), 1)
        self.mocks['run_session'].assert_called_once()
        self.assertIn('RuntimeError: unexpected', self.output.getvalue())
