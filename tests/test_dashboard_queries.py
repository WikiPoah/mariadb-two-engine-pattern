import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from dashboard import queries


class QueryTests(unittest.TestCase):
    def test_seed_overview_and_observation_contract(self):
        with patch.object(queries, '_rows', side_effect=[
            [dict(orders=3, revenue=Decimal('38.00'), units_sold=8)],
            [dict(sessions=5, product_views=6)],
        ]):
            result = queries.overview(object())
        self.assertEqual(result['data'], dict(orders=3, revenue=Decimal('38'),
                         units_sold=8, sessions=5, product_views=6))
        self.assertIsInstance(result['data']['revenue'], Decimal)
        self.assertEqual(result['observed_at'].tzinfo, timezone.utc)
        self.assertEqual(result['scope'], 'all_time')
        self.assertEqual(result['currency'], 'EUR')

    def test_empty_aggregates(self):
        with patch.object(queries, '_rows', side_effect=[
            [dict(orders=0, revenue=None, units_sold=None)],
            [dict(sessions=0, product_views=None)],
        ]):
            data = queries.overview(object())['data']
        self.assertTrue(all(value == 0 for value in data.values()))
        self.assertIsInstance(data['revenue'], Decimal)
        with patch.object(queries, '_rows', return_value=[]):
            self.assertEqual(queries.campaigns(object())['data'], [])
            self.assertEqual(queries.inventory(object())['data'], [])
            self.assertEqual(queries.recent_activity(object())['data'], [])

    def test_inventory_shaping(self):
        with patch.object(queries, '_rows', return_value=[dict(
            product_id=1, product_name='Notebook', current_price=Decimal('12'), stock=98, is_active=1,
        )]):
            result = queries.inventory(object())
        self.assertEqual(result['engines'], ['InnoDB'])
        self.assertIs(result['data'][0]['is_active'], True)
        self.assertEqual(result['data'][0]['stock'], 98)
        self.assertIsInstance(result['data'][0]['current_price'], Decimal)

    def test_activity_limit_order_and_raw_fields(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        columns = ['event_id', 'session_id', 'event_type', 'occurred_at', 'campaign_id', 'product_id']
        cursor.description = [(name,) for name in columns]
        cursor.fetchall.return_value = [(1, 'session', 'session_start', datetime(2026, 9, 14), None, None)]
        result = queries.recent_activity(connection)
        sql, args = cursor.execute.call_args.args
        self.assertEqual(args, (50,))
        self.assertIn('ORDER BY occurred_at DESC', sql)
        self.assertIn('LIMIT %s', sql)
        self.assertNotIn('JOIN', sql)
        self.assertEqual(result['data'][0]['occurred_at'].tzinfo, timezone.utc)
        self.assertIsNone(result['data'][0]['campaign_id'])
        queries.recent_activity(connection, 1)
        self.assertEqual(cursor.execute.call_args.args[1], (1,))
        for limit in (0, 51, -1, 1.5, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                queries.recent_activity(connection, limit)

    def test_campaign_seed_attribution_and_money(self):
        rows = [dict(campaign_id=cid, campaign_name=name, is_active=active,
                     sessions=sessions, purchasing_sessions=1, revenue=Decimal(revenue),
                     conversion_percent=Decimal(rate), invalid_sessions=0)
                for cid,name,active,sessions,revenue,rate in [
                    (1,'Campus Launch',1,2,'26','50'), (2,'Study Week',0,1,'8','100'),
                    (None,'Organic',None,2,'4','50')]]
        with patch.object(queries, '_rows', return_value=rows):
            data = queries.campaigns(object())['data']
        self.assertEqual([row['sessions'] for row in data], [2,1,2])
        self.assertEqual([row['revenue'] for row in data], [Decimal(26),Decimal(8),Decimal(4)])
        self.assertFalse(data[1]['is_active'])
        self.assertTrue(data[2]['is_organic'])
        self.assertIsNone(data[2]['is_active'])
        self.assertNotIn('invalid_sessions', data[0])

    def test_campaign_integrity_failure_is_not_published(self):
        with patch.object(queries, '_rows', return_value=[dict(invalid_sessions=1)]):
            with self.assertRaises(queries.DashboardDataError):
                queries.campaigns(object())

    def test_queries_use_supplied_connection_and_propagate_errors(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = RuntimeError('failed read')
        with self.assertRaises(RuntimeError):
            queries.inventory(connection)
        connection.close.assert_not_called()
        connection.commit.assert_not_called()
        connection.begin.assert_not_called()
