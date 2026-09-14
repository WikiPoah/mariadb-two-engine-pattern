"""Read-only JSON endpoints; each query owns a short-lived database connection."""

from datetime import datetime, timezone
from decimal import Decimal

from flask import Flask, jsonify, render_template, request
from flask.json.provider import DefaultJSONProvider

from dashboard import queries
from simulator.config import database_config
from simulator.database import open_connection


class DashboardJSONProvider(DefaultJSONProvider):
    @staticmethod
    def default(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime):
            if value.utcoffset() is None:
                raise ValueError("API timestamps must be timezone-aware")
            return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
        return DefaultJSONProvider.default(value)


def create_app(*, connection_factory=None):
    """An injected factory must return a connection context manager."""
    app = Flask(__name__)
    app.json = DashboardJSONProvider(app)
    if connection_factory is None:
        config = database_config()

        def connection_factory():
            return open_connection(config)

    def read(query, *args):
        try:
            with connection_factory() as connection:
                result = query(connection, *args)
            return jsonify(result)
        except queries.DashboardDataError:
            return jsonify(error="Analytical data is inconsistent"), 500
        except Exception:
            # Driver errors can contain SQL and connection details; keep them private.
            return jsonify(error="Unable to read dashboard data"), 500

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/overview")
    def overview():
        return read(queries.overview)

    @app.get("/api/inventory")
    def inventory():
        return read(queries.inventory)

    @app.get("/api/activity")
    def activity():
        values = request.args.getlist("limit")
        raw = values[0] if values else "50"
        digits = raw.lstrip("0") or "0"
        if (len(values) > 1 or not raw.isascii() or not raw.isdecimal()
                or len(digits) > 2 or not 1 <= int(digits) <= 50):
            return jsonify(error="Activity limit must be an integer between 1 and 50"), 400
        return read(queries.recent_activity, int(digits))

    @app.get("/api/campaigns")
    def campaigns():
        return read(queries.campaigns)

    return app
