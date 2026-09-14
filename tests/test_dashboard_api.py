import unittest
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pymysql

from dashboard import queries
from dashboard.app import create_app


class DashboardAPITests(unittest.TestCase):
    def setUp(self):
        self.connections = []

        @contextmanager
        def connect():
            connection = MagicMock()
            self.connections.append(connection)
            try:
                yield connection
            finally:
                connection.close()

        self.app = create_app(connection_factory=connect)
        self.app.testing = True
        self.client = self.app.test_client()
        self.result = dict(
            data={"revenue": Decimal("38.00"), "active": True, "campaign_id": None},
            observed_at=datetime(2026, 9, 14, 12, 0, 0, 123456, tzinfo=timezone.utc),
            scope="all_time", currency="EUR", engines=["InnoDB", "DuckDB"],
        )

    def test_endpoints_dispatch_preserve_contract_and_close_connections(self):
        original = deepcopy(self.result)
        for endpoint, function in [
            ("overview", "overview"), ("inventory", "inventory"),
            ("activity", "recent_activity"), ("campaigns", "campaigns"),
        ]:
            with self.subTest(endpoint=endpoint), patch.object(
                queries, function, return_value=self.result,
            ) as query:
                response = self.client.get(f"/api/{endpoint}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "application/json")
                args = (50,) if endpoint == "activity" else ()
                query.assert_called_once_with(self.connections[-1], *args)
                self.connections[-1].close.assert_called_once_with()
                body = response.get_json()
                self.assertEqual(body["data"], {
                    "revenue": "38.00", "active": True, "campaign_id": None,
                })
                self.assertEqual(body["observed_at"], "2026-09-14T12:00:00.123456+00:00")
                for field in ("engines", "scope", "currency"):
                    self.assertEqual(body[field], self.result[field])
        self.assertEqual(len({id(c) for c in self.connections}), 4)
        self.assertEqual(self.result, original)

    def test_activity_custom_limit_and_nested_timestamp(self):
        self.result["limit"] = 3
        self.result["data"] = [{"occurred_at": datetime(
            2026, 9, 14, 14, tzinfo=timezone(timedelta(hours=2)),
        )}]
        with patch.object(queries, "recent_activity", return_value=self.result) as query:
            response = self.client.get("/api/activity?limit=3")
        query.assert_called_once_with(self.connections[0], 3)
        self.assertEqual(response.get_json()["limit"], 3)
        self.assertEqual(response.get_json()["data"][0]["occurred_at"],
                         "2026-09-14T12:00:00.000000+00:00")

    def test_invalid_limits_do_not_open_connections(self):
        for raw in ("abc", "1.5", "0", "-1", "51", "", "1e1", "1&limit=2", "999999999"):
            with self.subTest(raw=raw):
                response = self.client.get(f"/api/activity?limit={raw}")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json(), {
                    "error": "Activity limit must be an integer between 1 and 50",
                })
        self.assertEqual(self.connections, [])

    def test_query_failures_close_connections_and_hide_details(self):
        for error, message in [
            (queries.DashboardDataError("secret SQL /private/path"), "Analytical data is inconsistent"),
            (pymysql.OperationalError(2006, "password=secret SQL"), "Unable to read dashboard data"),
            (RuntimeError("/private/path secret"), "Unable to read dashboard data"),
        ]:
            with self.subTest(error=type(error)), patch.object(queries, "campaigns", side_effect=error):
                response = self.client.get("/api/campaigns")
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.get_json(), {"error": message})
                self.connections[-1].close.assert_called_once_with()

    def test_connection_failure_is_controlled(self):
        factory = MagicMock(side_effect=pymysql.OperationalError("secret"))
        client = create_app(connection_factory=factory).test_client()
        response = client.get("/api/overview")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "Unable to read dashboard data"})

    def test_default_factory_reuses_configuration_and_connection_helper(self):
        with patch("dashboard.app.database_config", return_value={"password": "secret"}) as config:
            app = create_app()
        config.assert_called_once_with()
        with patch("dashboard.app.open_connection") as connect, patch.object(
            queries, "overview", return_value=self.result,
        ):
            response = app.test_client().get("/api/overview")
        self.assertEqual(response.status_code, 200)
        connect.assert_called_once_with({"password": "secret"})
        connect.return_value.__exit__.assert_called_once()

    def test_naive_timestamp_is_not_silently_labeled_utc(self):
        self.result["observed_at"] = datetime(2026, 9, 14)
        with patch.object(queries, "overview", return_value=self.result):
            response = self.client.get("/api/overview")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "Unable to read dashboard data"})
        self.connections[0].close.assert_called_once_with()
