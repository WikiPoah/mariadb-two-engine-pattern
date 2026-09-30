"""Analytical queries and source/destination convergence checks for the experiment."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from dashboard import queries as source_queries


ACTIVITY_OVERVIEW_SQL = """
SELECT COUNT(DISTINCT CASE WHEN event_type = 'session_start' THEN session_id END) AS sessions,
       COALESCE(SUM(CASE WHEN event_type = 'product_view' THEN 1 ELSE 0 END), 0) AS product_views
FROM activity_events_copy
"""

COMMERCE_OVERVIEW_SQL = """
SELECT COUNT(*) AS orders,
       COALESCE(SUM(revenue_eur), 0) AS revenue,
       COALESCE(SUM(units_sold), 0) AS units_sold
FROM order_facts
"""

RECENT_ACTIVITY_SQL = """
SELECT source_event_id AS event_id, session_id, event_type, occurred_at,
       campaign_id, product_id
FROM activity_events_copy
ORDER BY occurred_at DESC, session_id, event_type, campaign_id, product_id,
         source_event_id
LIMIT ?
"""

CAMPAIGNS_SQL = """
WITH session_history AS (
    SELECT session_id, MIN(campaign_id) AS campaign_id,
           MIN(CASE WHEN event_type = 'session_start' THEN occurred_at END) AS started_at,
           SUM(CASE WHEN event_type = 'session_start' THEN 1 ELSE 0 END) AS starts,
           COUNT(DISTINCT campaign_id) AS campaign_count,
           COUNT(campaign_id) AS attributed_events, COUNT(*) AS events
    FROM activity_events_copy
    GROUP BY session_id
)
SELECT s.campaign_id,
       CASE WHEN s.campaign_id IS NULL THEN 'Organic' ELSE c.campaign_name END AS campaign_name,
       c.is_active,
       COUNT(*) AS sessions,
       COUNT(o.order_id) AS purchasing_sessions,
       COALESCE(SUM(o.revenue_eur), 0) AS revenue,
       ROUND(100.0 * COUNT(o.order_id) / COUNT(*), 2) AS conversion_percent,
       SUM(CASE WHEN s.starts <> 1 OR s.campaign_count > 1
                     OR (s.attributed_events > 0 AND s.attributed_events < s.events)
                     OR (s.campaign_id IS NOT NULL AND c.campaign_id IS NULL)
                     OR (o.order_id IS NOT NULL AND
                         (o.campaign_id <> s.campaign_id
                          OR (o.campaign_id IS NULL AND s.campaign_id IS NOT NULL)
                          OR (o.campaign_id IS NOT NULL AND s.campaign_id IS NULL)
                          OR o.purchased_at < s.started_at))
                THEN 1 ELSE 0 END) AS invalid_sessions
FROM session_history AS s
LEFT JOIN campaigns_snapshot AS c ON c.campaign_id = s.campaign_id
LEFT JOIN order_facts AS o ON o.session_id = s.session_id
GROUP BY s.campaign_id, c.campaign_name, c.is_active
ORDER BY CASE WHEN s.campaign_id IS NULL THEN 1 ELSE 0 END, s.campaign_id
"""


def _rows(connection, sql, parameters=()):
    cursor = connection.execute(sql, parameters)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def activity_overview(destination) -> dict:
    return _rows(destination, ACTIVITY_OVERVIEW_SQL)[0]


def commerce_overview(destination) -> dict:
    return _rows(destination, COMMERCE_OVERVIEW_SQL)[0]


def recent_activity(destination, limit=50) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Activity limit must be an integer between 1 and 50")
    return _rows(destination, RECENT_ACTIVITY_SQL, (limit,))


def campaigns(destination) -> list[dict]:
    rows = _rows(destination, CAMPAIGNS_SQL)
    if any(row["invalid_sessions"] for row in rows):
        raise ValueError("Campaign metrics require consistent session attribution")
    for row in rows:
        del row["invalid_sessions"]
        row["is_organic"] = row["campaign_id"] is None
    return rows


@dataclass(frozen=True)
class ConvergenceReport:
    matches: bool
    mismatches: tuple[str, ...]


def _canonical(value):
    if isinstance(value, Decimal):
        # MariaDB and DuckDB may preserve different trailing-zero scales.
        return format(value.normalize(), "f")
    if isinstance(value, float):
        # DuckDB may return ROUND() results as floats while MariaDB returns Decimal.
        return format(Decimal(str(value)).normalize(), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _data(connection):
    source_overview = source_queries.overview(connection)["data"]
    source_activity = source_queries.recent_activity(connection)["data"]
    source_campaigns = source_queries.campaigns(connection)["data"]
    return {
        "activity_overview": {
            "sessions": source_overview["sessions"],
            "product_views": source_overview["product_views"],
        },
        "commerce_overview": {
            "orders": source_overview["orders"],
            "revenue": source_overview["revenue"],
            "units_sold": source_overview["units_sold"],
        },
        "recent_activity": source_activity,
        "campaigns": source_campaigns,
    }


def validate_convergence(source, destination, *, activity_limit=50) -> ConvergenceReport:
    expected = _data(source)
    actual = {
        "activity_overview": activity_overview(destination),
        "commerce_overview": commerce_overview(destination),
        "recent_activity": recent_activity(destination, activity_limit),
        "campaigns": campaigns(destination),
    }
    mismatches = []
    for name in expected:
        if _canonical(expected[name]) != _canonical(actual[name]):
            mismatches.append(name)
    return ConvergenceReport(not mismatches, tuple(mismatches))
