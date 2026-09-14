"""Pure workload choices; callers supply currently eligible catalog IDs."""

from collections.abc import Sequence
from random import Random
from uuid import uuid4

CAMPAIGN_PROBABILITY = 0.60
PURCHASE_PROBABILITY = 0.20
MIN_PRODUCT_VIEWS = 1
MAX_PRODUCT_VIEWS = 4
MIN_CART_PRODUCTS = 1
MAX_CART_PRODUCTS = 2
MIN_QUANTITY = 1
MAX_QUANTITY = 2


def new_session_id() -> str:
    # UUIDs must stay fresh when a behavioural seed is reused against existing data.
    return str(uuid4())


def choose_campaign(active_campaign_ids: Sequence[int], rng: Random) -> int | None:
    """Call once at acquisition and retain the result for the entire session."""
    if not active_campaign_ids or rng.random() >= CAMPAIGN_PROBABILITY:
        return None
    return rng.choice(sorted(set(active_campaign_ids)))


def choose_views(active_product_ids: Sequence[int], rng: Random) -> list[int]:
    if not active_product_ids:
        return []
    product_ids = sorted(set(active_product_ids))
    return [
        rng.choice(product_ids)
        for _ in range(rng.randint(MIN_PRODUCT_VIEWS, MAX_PRODUCT_VIEWS))
    ]


def wants_purchase(rng: Random) -> bool:
    return rng.random() < PURCHASE_PROBABILITY


def choose_cart(viewed_product_ids: Sequence[int], rng: Random) -> dict[int, int]:
    """Return product quantities; availability must be rechecked at checkout."""
    product_ids = sorted(set(viewed_product_ids))
    if not product_ids:
        return {}
    size = rng.randint(MIN_CART_PRODUCTS, min(MAX_CART_PRODUCTS, len(product_ids)))
    selected = rng.sample(product_ids, size)
    return {
        product_id: rng.randint(MIN_QUANTITY, MAX_QUANTITY)
        for product_id in sorted(selected)
    }
