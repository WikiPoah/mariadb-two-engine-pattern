import unittest
from unittest.mock import MagicMock

from dashboard.app import create_app


class DashboardPageTests(unittest.TestCase):
    def test_page_sections_provenance_and_assets_without_database_connection(self):
        connect = MagicMock(side_effect=AssertionError("Page must not query the database"))
        client = create_app(connection_factory=connect).test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        html = response.get_data(as_text=True)
        for text in ("Commerce Overview", "Inventory", "Recent Activity", "Campaign Performance",
                     "InnoDB", "DuckDB", "InnoDB + DuckDB"):
            self.assertIn(text, html)
        for asset, mimetype in (("dashboard.css", "text/css"), ("dashboard.js", "javascript")):
            self.assertIn(f'/static/{asset}', html)
            asset_response = client.get(f"/static/{asset}")
            self.assertEqual(asset_response.status_code, 200)
            self.assertIn(mimetype, asset_response.mimetype)
            asset_response.close()
        connect.assert_not_called()
