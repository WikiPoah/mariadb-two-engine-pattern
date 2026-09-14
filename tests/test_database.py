import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pymysql

from simulator.config import database_config
from simulator.database import (
    active_campaign_ids,
    active_product_ids,
    insert_product_view,
    insert_session_start,
    open_connections,
)


class DatabaseConfigurationTests(unittest.TestCase):
    def test_existing_root_environment_and_defaults(self):
        self.assertEqual(database_config({'MARIADB_ROOT_PASSWORD': 'test-only'}), {
            'host': 'mariadb', 'port': 3306, 'user': 'root',
            'password': 'test-only', 'database': 'commerce_analytics',
        })

    def test_explicit_connection_environment(self):
        config = database_config({
            'MARIADB_HOST': 'localhost', 'MARIADB_PORT': '3307',
            'MARIADB_USER': 'simulator', 'MARIADB_PASSWORD': 'test-only',
            'MARIADB_DATABASE': 'disposable',
        })
        self.assertEqual(config, {
            'host': 'localhost', 'port': 3307, 'user': 'simulator',
            'password': 'test-only', 'database': 'disposable',
        })

    def test_invalid_config_and_no_root_password_fallback_for_other_users(self):
        cases = [{}, {'MARIADB_USER': 'simulator', 'MARIADB_ROOT_PASSWORD': 'test-only'}]
        for key, values in {
            'MARIADB_PORT': ['0', '65536', 'abc'],
            'MARIADB_HOST': [' '], 'MARIADB_USER': [''],
            'MARIADB_DATABASE': [''], 'MARIADB_PASSWORD': [''],
        }.items():
            cases.extend({'MARIADB_PASSWORD': 'test-only', key: value} for value in values)
        for env in cases:
            with self.subTest(keys=list(env)):
                with self.assertRaises(ValueError):
                    database_config(env)


class ConnectionTests(unittest.TestCase):
    @patch('simulator.database.pymysql.connect')
    def test_two_autocommit_utc_connections_and_cleanup(self, connect):
        operational, events = MagicMock(), MagicMock()
        for connection in (operational, events):
            connection.__enter__.return_value = connection
        connect.side_effect = [operational, events]
        config = database_config({'MARIADB_ROOT_PASSWORD': 'test-only'})
        with open_connections(config) as pair:
            self.assertEqual(pair, (operational, events))
            self.assertEqual(connect.call_count, 2)
            for call in connect.call_args_list:
                self.assertEqual(call.kwargs, {
                    **config, 'charset': 'utf8mb4', 'collation': 'utf8mb4_bin',
                    'autocommit': True,
                })
            for connection in pair:
                connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
                    "SET SESSION time_zone = '+00:00'"
                )
        for connection in pair:
            connection.__exit__.assert_called_once()

    @patch('simulator.database.pymysql.connect')
    def test_initialization_failure_cleans_up_and_propagates(self, connect):
        for fail_at in ('second_connect', 'utc'):
            with self.subTest(fail_at=fail_at):
                first = MagicMock()
                first.__enter__.return_value = first
                error = pymysql.OperationalError('test failure')
                connect.side_effect = [first, error]
                if fail_at == 'utc':
                    first.cursor.return_value.__enter__.return_value.execute.side_effect = error
                with self.assertRaises(pymysql.OperationalError):
                    with open_connections({}):
                        self.fail('Initialization must not yield connections')
                first.__exit__.assert_called_once()


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.session_id = '123e4567-e89b-42d3-a456-426614174001'
        self.timestamp = datetime(2026, 9, 14, 12, 0, 0, 123456, tzinfo=timezone.utc)

    def test_active_reads_are_refreshed_and_do_not_filter_stock(self):
        for reader, id_column, table in (
            (active_campaign_ids, 'campaign_id', 'campaigns'),
            (active_product_ids, 'product_id', 'products'),
        ):
            with self.subTest(table=table):
                self.cursor.reset_mock()
                self.cursor.fetchall.side_effect = [[(1,), (3,)], []]
                self.assertEqual(reader(self.connection), [1, 3])
                self.assertEqual(reader(self.connection), [])
                self.assertEqual(self.cursor.execute.call_count, 2)
                self.cursor.execute.assert_called_with(
                    f'SELECT {id_column} FROM {table} WHERE is_active = TRUE ORDER BY {id_column}'
                )

    def test_event_insert_shapes_and_attribution(self):
        for campaign in (None, 2):
            for event_type, writer, extra in (
                ('session_start', insert_session_start, ()),
                ('product_view', insert_product_view, (3,)),
            ):
                with self.subTest(campaign=campaign, event_type=event_type):
                    self.cursor.reset_mock()
                    writer(self.connection, self.session_id, campaign, self.timestamp, *extra)
                    sql, params = self.cursor.execute.call_args.args
                    product_value = 'NULL' if event_type == 'session_start' else '%s'
                    self.assertEqual(sql,
                        'INSERT INTO activity_events '
                        '(session_id, event_type, occurred_at, campaign_id, product_id) '
                        f"VALUES (%s, '{event_type}', %s, %s, {product_value})")
                    self.assertNotIn('event_id', sql)
                    self.assertEqual(params, (
                        self.session_id, self.timestamp.replace(tzinfo=None), campaign, *extra,
                    ))
                    self.cursor.execute.assert_called_once()
        self.connection.commit.assert_not_called()
        self.connection.begin.assert_not_called()

    def test_timestamp_normalization_and_naive_rejection(self):
        local = self.timestamp.astimezone(timezone(timedelta(hours=2)))
        insert_session_start(self.connection, self.session_id, None, local)
        self.assertEqual(self.cursor.execute.call_args.args[1][1],
                         self.timestamp.replace(tzinfo=None))
        self.cursor.reset_mock()
        with self.assertRaises(ValueError):
            insert_session_start(self.connection, self.session_id, None,
                                 self.timestamp.replace(tzinfo=None))
        self.cursor.execute.assert_not_called()

    def test_event_failure_propagates_without_retry(self):
        for writer, extra in ((insert_session_start, ()), (insert_product_view, (3,))):
            self.cursor.reset_mock()
            self.cursor.execute.side_effect = pymysql.OperationalError('test failure')
            with self.assertRaises(pymysql.OperationalError):
                writer(self.connection, self.session_id, None, self.timestamp, *extra)
            self.cursor.execute.assert_called_once()
            self.connection.commit.assert_not_called()
