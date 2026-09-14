import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pymysql

from simulator.checkout import CheckoutRejected, CheckoutUncertain, checkout


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.side_effect = [
            (1, Decimal('12.00'), 10, True), (3, Decimal('8.00'), 5, True),
        ]
        self.cursor.lastrowid = 42
        self.recovery = MagicMock()
        self.recovery_cursor = self.recovery.cursor.return_value.__enter__.return_value
        self.recovery_cursor.fetchone.return_value = (42,)
        self.recovery_factory_calls = 0
        self.timestamp = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)

    @contextmanager
    def recover(self):
        self.recovery_factory_calls += 1
        yield self.recovery

    def run_checkout(self, cart=None):
        return checkout(self.connection, 'session', None, self.timestamp,
                        {3: 1, 1: 2} if cart is None else cart,
                        recovery_connection=self.recover)

    def test_invalid_carts_do_not_start_transactions(self):
        for cart in ({}, {1: 0}, {1: -1}, {1: 1.5}, {1: True}, {'1': 1}):
            with self.subTest(cart=cart):
                with self.assertRaises(CheckoutRejected):
                    self.run_checkout(cart)
        self.connection.begin.assert_not_called()
        self.cursor.execute.assert_not_called()

    def test_naive_timestamp_rejected_before_transaction(self):
        self.timestamp = self.timestamp.replace(tzinfo=None)
        with self.assertRaises(ValueError):
            self.run_checkout()
        self.connection.begin.assert_not_called()

    def test_success_locks_prices_writes_and_commit_order(self):
        sequence = []
        self.connection.begin.side_effect = lambda: sequence.append('begin')
        self.cursor.execute.side_effect = lambda sql, params: sequence.append((sql, params))
        self.connection.commit.side_effect = lambda: sequence.append('commit')
        self.assertEqual(self.run_checkout(), 42)
        self.assertEqual(sequence[0], 'begin')
        self.assertEqual(sequence[-1], 'commit')
        statements = sequence[1:-1]
        self.assertEqual(len(statements), 7)
        for statement, product_id in zip(statements[:2], (1, 3)):
            self.assertEqual(statement, (
                'SELECT product_id, unit_price, stock_quantity, is_active '
                'FROM products WHERE product_id = %s FOR UPDATE', (product_id,),
            ))
        self.assertEqual(statements[2], (
            'INSERT INTO orders (session_id, campaign_id, purchased_at) VALUES (%s, %s, %s)',
            ('session', None, self.timestamp.replace(tzinfo=None)),
        ))
        for index, product_id, quantity, price in (
            (3, 1, 2, Decimal('12.00')), (5, 3, 1, Decimal('8.00')),
        ):
            self.assertEqual(statements[index], (
                'INSERT INTO order_items (order_id, product_id, quantity, unit_price) '
                'VALUES (%s, %s, %s, %s)', (42, product_id, quantity, price),
            ))
            self.assertEqual(statements[index + 1], (
                'UPDATE products SET stock_quantity = stock_quantity - %s WHERE product_id = %s',
                (quantity, product_id),
            ))
        self.assertTrue(all('activity_events' not in sql for sql, _ in statements))
        self.connection.rollback.assert_not_called()
        self.assertEqual(self.recovery_factory_calls, 0)

    def test_product_rejections_roll_back_without_writes(self):
        for product in (None, (1, Decimal('12'), 10, False), (1, Decimal('12'), 1, True)):
            with self.subTest(product=product):
                self.connection.reset_mock()
                self.cursor.fetchone.side_effect = [product]
                with self.assertRaises(CheckoutRejected):
                    self.run_checkout()
                self.connection.rollback.assert_called_once()
                self.connection.commit.assert_not_called()
                self.assertEqual(self.cursor.execute.call_count, 1)

    def test_database_failure_after_order_insert_rolls_back(self):
        self.cursor.execute.side_effect = [None, None, None, pymysql.OperationalError('failure')]
        with self.assertRaises(pymysql.OperationalError):
            self.run_checkout()
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()
        self.assertEqual(self.recovery_factory_calls, 0)

    def test_ambiguous_commit_resolves_existing_order_without_retry(self):
        self.connection.commit.side_effect = pymysql.OperationalError('lost acknowledgement')
        self.connection.rollback.side_effect = pymysql.OperationalError('connection lost')
        self.assertEqual(self.run_checkout(), 42)
        self.connection.begin.assert_called_once()
        self.connection.commit.assert_called_once()
        self.assertEqual(self.cursor.execute.call_count, 7)
        self.recovery_cursor.execute.assert_called_once_with(
            'SELECT order_id FROM orders WHERE session_id = %s FOR UPDATE', ('session',),
        )
        self.recovery.begin.assert_called_once()
        self.recovery.rollback.assert_called_once()

    def test_confirmed_absence_reports_original_failure(self):
        error = pymysql.OperationalError('commit failed')
        self.connection.commit.side_effect = error
        self.recovery_cursor.fetchone.return_value = None
        with self.assertRaises(pymysql.OperationalError) as raised:
            self.run_checkout()
        self.assertIs(raised.exception, error)
        self.connection.begin.assert_called_once()

    def test_failed_recovery_is_explicitly_uncertain(self):
        self.connection.commit.side_effect = pymysql.OperationalError('commit failed')
        self.recovery_cursor.execute.side_effect = pymysql.OperationalError('lookup failed')
        with self.assertRaises(CheckoutUncertain):
            self.run_checkout()
        self.connection.commit.assert_called_once()
        self.recovery.rollback.assert_called_once()

    def test_unexpected_recovered_order_is_not_reported_as_success(self):
        self.connection.commit.side_effect = pymysql.OperationalError('commit failed')
        self.recovery_cursor.fetchone.return_value = (99,)
        with self.assertRaises(CheckoutUncertain):
            self.run_checkout()
