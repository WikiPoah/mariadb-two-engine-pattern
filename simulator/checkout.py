"""Atomic InnoDB purchases; no behavioural event writes belong here."""

from datetime import datetime

from simulator.database import _utc_datetime


class CheckoutRejected(ValueError):
    """The requested cart cannot be purchased in full."""


class CheckoutUncertain(RuntimeError):
    """The caller must establish the purchase outcome before taking further action."""


def checkout(operational, session_id: str, campaign_id: int | None,
             purchased_at: datetime, cart: dict[int, int], *, recovery_connection) -> int:
    """recovery_connection is a factory returning a fresh connection context manager."""
    timestamp = _utc_datetime(purchased_at)
    if not cart:
        raise CheckoutRejected("Cart must not be empty")
    if any(type(product_id) is not int for product_id in cart):
        raise CheckoutRejected("Product IDs must be integers")
    if any(type(quantity) is not int or quantity <= 0 for quantity in cart.values()):
        raise CheckoutRejected("Quantities must be positive integers")

    try:
        operational.begin()
        with operational.cursor() as cursor:
            prices = {}
            # Individual primary-key reads make the actual lock acquisition order explicit.
            for product_id in sorted(cart):
                cursor.execute(
                    "SELECT product_id, unit_price, stock_quantity, is_active "
                    "FROM products WHERE product_id = %s FOR UPDATE", (product_id,),
                )
                product = cursor.fetchone()
                if product is None:
                    raise CheckoutRejected(f"Product {product_id} does not exist")
                _, price, stock, active = product
                if not active:
                    raise CheckoutRejected(f"Product {product_id} is inactive")
                if stock < cart[product_id]:
                    raise CheckoutRejected(f"Product {product_id} has insufficient stock")
                prices[product_id] = price

            cursor.execute(
                "INSERT INTO orders (session_id, campaign_id, purchased_at) VALUES (%s, %s, %s)",
                (session_id, campaign_id, timestamp),
            )
            order_id = cursor.lastrowid
            for product_id in sorted(cart):
                cursor.execute(
                    "INSERT INTO order_items (order_id, product_id, quantity, unit_price) "
                    "VALUES (%s, %s, %s, %s)",
                    (order_id, product_id, cart[product_id], prices[product_id]),
                )
                cursor.execute(
                    "UPDATE products SET stock_quantity = stock_quantity - %s "
                    "WHERE product_id = %s", (cart[product_id], product_id),
                )
    except Exception:
        try:
            operational.rollback()
        except Exception:
            # Preserve the original failure; the owning connection context closes on exit.
            pass
        raise

    try:
        operational.commit()
    except Exception as commit_error:
        # A failed acknowledgement is not proof of rollback. Never resubmit the order.
        try:
            operational.rollback()
        except Exception:
            pass
        try:
            with recovery_connection() as recovery:
                recovery.begin()
                try:
                    with recovery.cursor() as cursor:
                        # Wait for any unresolved write to this unique key before deciding absence.
                        cursor.execute(
                            "SELECT order_id FROM orders WHERE session_id = %s FOR UPDATE",
                            (session_id,),
                        )
                        found = cursor.fetchone()
                finally:
                    recovery.rollback()
        except Exception as recovery_error:
            raise CheckoutUncertain(
                f"Cannot establish checkout outcome for session {session_id}"
            ) from recovery_error
        if found is None:
            raise commit_error
        if found[0] != order_id:
            raise CheckoutUncertain(f"Unexpected order for session {session_id}") from commit_error
        return order_id
    return order_id
