"""All-time observations; callers own and close their database connection."""

from datetime import timezone, datetime
from decimal import Decimal
from pathlib import Path


class DashboardDataError(ValueError):
    """Analytical relationships are inconsistent; do not publish inflated metrics."""


def _rows(connection, name, parameters=None):
    sql = (Path(__file__).parent / 'sql' / f'{name}.sql').read_text()
    with connection.cursor() as cursor:
        cursor.execute(sql, parameters)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _observation(data, engines):
    # Completion time describes this read, not an atomic snapshot across panels/engines.
    return dict(data=data, observed_at=datetime.now(timezone.utc),
                scope='all_time', currency='EUR', engines=engines)


def overview(connection):
    commerce = _rows(connection, 'overview_commerce')[0]
    activity = _rows(connection, 'overview_activity')[0]
    data = dict(orders=int(commerce['orders']),
                revenue=Decimal(commerce['revenue'] or 0),
                units_sold=int(commerce['units_sold'] or 0),
                sessions=int(activity['sessions']),
                product_views=int(activity['product_views'] or 0))
    return _observation(data, ['InnoDB', 'DuckDB'])


def inventory(connection):
    rows = _rows(connection, 'inventory')
    for row in rows:
        row['current_price'] = Decimal(row['current_price'])
        row['is_active'] = bool(row['is_active'])
    return _observation(rows, ['InnoDB'])


def recent_activity(connection, limit=50):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Activity limit must be an integer between 1 and 50')
    rows = _rows(connection, 'recent_activity', (limit,))
    for row in rows:
        # MariaDB DATETIME fields hold UTC values without timezone metadata.
        row['occurred_at'] = row['occurred_at'].replace(tzinfo=timezone.utc)
    result = _observation(rows, ['DuckDB'])
    result['limit'] = limit
    return result


def campaigns(connection):
    rows = _rows(connection, 'campaigns')
    if any(row['invalid_sessions'] for row in rows):
        raise DashboardDataError('Campaign metrics require one session start and consistent attribution')
    for row in rows:
        del row['invalid_sessions']
        row['is_organic'] = row['campaign_id'] is None
        row['is_active'] = None if row['is_active'] is None else bool(row['is_active'])
        row['sessions'] = int(row['sessions'])
        row['purchasing_sessions'] = int(row['purchasing_sessions'])
        row['revenue'] = Decimal(row['revenue'] or 0)
        row['conversion_percent'] = Decimal(row['conversion_percent'] or 0)
    return _observation(rows, ['InnoDB', 'DuckDB'])
