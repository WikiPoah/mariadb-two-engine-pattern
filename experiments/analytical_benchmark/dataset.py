"""Deterministic commerce data shared by both MariaDB variants."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from random import Random
from uuid import UUID

from simulator.workload import choose_campaign, choose_cart, choose_views, wants_purchase


SEED = 17
SCALES = (5_000, 50_000)
INITIAL_STOCK = 100_000
PRODUCTS = ((1, "Notebook", Decimal("10.00")),
            (2, "Pen", Decimal("2.00")),
            (3, "Mug", Decimal("8.00")))
CAMPAIGN_NAMES = ((1, "Campus Launch"), (2, "Study Week"))
START_TIME = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
# Session ordinals 1–2,500 use €10; ordinal 2,501 onward uses €12.
PRICE_CHANGE_AFTER_SESSION = 2_500


@dataclass(frozen=True)
class BenchmarkDataset:
    session_count: int
    products: tuple[tuple, ...]
    campaigns: tuple[tuple, ...]
    events: tuple[tuple, ...]
    orders: tuple[tuple, ...]
    order_items: tuple[tuple, ...]

    def table_rows(self) -> dict[str, tuple[tuple, ...]]:
        return {
            "products": self.products,
            "campaigns": self.campaigns,
            "activity_events": self.events,
            "orders": self.orders,
            "order_items": self.order_items,
        }


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def generate_dataset(session_count: int, *, seed: int = SEED) -> BenchmarkDataset:
    """Generate one fixed-scale workload; DATETIME values are naive UTC fields."""
    if session_count not in SCALES:
        raise ValueError(f"session_count must be one of {SCALES}")
    if seed != SEED:
        raise ValueError(f"the benchmark seed is locked at {SEED}")

    behavior = Random(seed)
    identifiers = Random(seed ^ 0x5A17C0DE)
    active_campaign_ids = [campaign_id for campaign_id, _ in CAMPAIGN_NAMES]
    product_ids = [product_id for product_id, _, _ in PRODUCTS]
    session_ids = set()
    events, orders, order_items = [], [], []
    stock = {product_id: INITIAL_STOCK for product_id in product_ids}
    event_id = order_id = 0
    for session_number in range(session_count):
        session_id = str(UUID(int=identifiers.getrandbits(128), version=4))
        if session_id != session_id.lower() or str(UUID(session_id)) != session_id:
            raise ValueError("generated session ID is not a canonical lowercase UUID")
        if session_id in session_ids:
            raise ValueError("generated duplicate session ID")
        session_ids.add(session_id)

        campaign_id = choose_campaign(active_campaign_ids, behavior)
        started_at = _naive_utc(START_TIME + timedelta(seconds=session_number))
        event_id += 1
        events.append((event_id, session_id, "session_start", started_at,
                       campaign_id, None))

        viewed_products = choose_views(product_ids, behavior)
        for view_number, product_id in enumerate(viewed_products, start=1):
            event_id += 1
            events.append((event_id, session_id, "product_view",
                           started_at + timedelta(milliseconds=100 * view_number),
                           campaign_id, product_id))

        if wants_purchase(behavior):
            cart = choose_cart(viewed_products, behavior)
            order_id += 1
            purchased_at = started_at + timedelta(milliseconds=750)
            orders.append((order_id, session_id, campaign_id, purchased_at))
            for product_id, quantity in cart.items():
                catalog_price = next(price for pid, _, price in PRODUCTS if pid == product_id)
                if product_id == 1 and session_number >= PRICE_CHANGE_AFTER_SESSION:
                    catalog_price = Decimal("12.00")
                order_items.append((order_id, product_id, quantity, catalog_price))
                stock[product_id] -= quantity

    products = tuple(
        (product_id, name,
         Decimal("12.00") if product_id == 1 else original_price,
         stock[product_id], True)
        for product_id, name, original_price in PRODUCTS
    )
    # Study Week is paused after its historical acquisitions, as in the application fixture.
    campaigns = ((1, "Campus Launch", True), (2, "Study Week", False))
    dataset = BenchmarkDataset(
        session_count, products, campaigns, tuple(events), tuple(orders),
        tuple(order_items),
    )
    validate_dataset(dataset)
    return dataset


def validate_dataset(dataset: BenchmarkDataset) -> None:
    product_ids = {row[0] for row in dataset.products}
    campaign_ids = {row[0] for row in dataset.campaigns}
    sessions = {row[1] for row in dataset.events if row[2] == "session_start"}
    event_ids = [row[0] for row in dataset.events]
    order_ids = [row[0] for row in dataset.orders]
    order_sessions = [row[1] for row in dataset.orders]
    starts = {}
    attribution = {}
    session_events = {}

    if len(sessions) != dataset.session_count:
        raise ValueError("each generated session must have one session_start")
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("generated event IDs must be unique")
    if len(order_ids) != len(set(order_ids)):
        raise ValueError("generated order IDs must be unique")
    if len(order_sessions) != len(set(order_sessions)):
        raise ValueError("a session may have at most one purchase")

    for event in dataset.events:
        identifier, session_id, event_type, occurred_at, campaign_id, product_id = event
        if event_type not in {"session_start", "product_view"}:
            raise ValueError("invalid event type")
        if campaign_id is not None and campaign_id not in campaign_ids:
            raise ValueError("event references an unknown campaign")
        if event_type == "session_start":
            starts[session_id] = starts.get(session_id, 0) + 1
            if product_id is not None:
                raise ValueError("session_start must not reference a product")
        elif product_id not in product_ids:
            raise ValueError("product_view must reference a known product")
        session_events.setdefault(session_id, []).append(event)
        if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is not None:
            raise ValueError("event timestamps must be naive UTC DATETIME values")
        prior_campaign = attribution.setdefault(session_id, campaign_id)
        if prior_campaign != campaign_id:
            raise ValueError("campaign attribution must remain fixed within a session")
        if identifier <= 0:
            raise ValueError("event IDs must be positive")

    if any(starts.get(session_id) != 1 for session_id in sessions):
        raise ValueError("each session must have exactly one session_start")
    if set(session_events) != sessions:
        raise ValueError("every event must belong to a session with a start")
    for session_id, rows in session_events.items():
        start = next(row[3] for row in rows if row[2] == "session_start")
        views = [row for row in rows if row[2] == "product_view"]
        if not 1 <= len(views) <= 4:
            raise ValueError("each generated session must have one to four views")
        if any(row[3] <= start for row in views):
            raise ValueError("product views must occur after session start")
    order_id_set = set(order_ids)
    for _, session_id, campaign_id, purchased_at in dataset.orders:
        if session_id not in sessions or campaign_id not in campaign_ids | {None}:
            raise ValueError("order references an unknown session or campaign")
        if campaign_id != attribution[session_id]:
            raise ValueError("order attribution must match its session")
        if not isinstance(purchased_at, datetime) or purchased_at.tzinfo is not None:
            raise ValueError("purchase timestamps must be naive UTC DATETIME values")
        if purchased_at <= max(row[3] for row in session_events[session_id]):
            raise ValueError("purchase must occur after session activity")
    if any(item[0] not in order_id_set or item[1] not in product_ids
           or item[2] not in (1, 2) or item[3] < 0
           for item in dataset.order_items):
        raise ValueError("invalid order item reference, quantity, or price")
    if len({(item[0], item[1]) for item in dataset.order_items}) != len(dataset.order_items):
        raise ValueError("an order may contain each product only once")
    viewed_products = {
        session_id: {row[5] for row in rows if row[2] == "product_view"}
        for session_id, rows in session_events.items()
    }
    orders_by_id = {row[0]: row for row in dataset.orders}
    if any(item[1] not in viewed_products[orders_by_id[item[0]][1]]
           for item in dataset.order_items):
        raise ValueError("purchased products must have been viewed in that session")
    items_by_product = {product_id: 0 for product_id in product_ids}
    for item_order_id, product_id, quantity, unit_price in dataset.order_items:
        items_by_product[product_id] += quantity
        purchased_at = orders_by_id[item_order_id][3]
        expected_price = next(price for pid, _, price in PRODUCTS if pid == product_id)
        if product_id == 1 and purchased_at >= _naive_utc(
            START_TIME + timedelta(seconds=PRICE_CHANGE_AFTER_SESSION)
        ):
            expected_price = Decimal("12.00")
        if unit_price != expected_price:
            raise ValueError("order item must preserve the price at purchase time")
    for product_id, _, _, stock_quantity, is_active in dataset.products:
        if stock_quantity != INITIAL_STOCK - items_by_product[product_id]:
            raise ValueError("product stock must reflect all generated purchases")
        if stock_quantity < 0:
            raise ValueError("generated stock must not be negative")
        if not is_active:
            raise ValueError("benchmark products must remain active")
