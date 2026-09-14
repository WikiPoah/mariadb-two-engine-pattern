import contextlib
import io
import unittest
from random import Random
from unittest.mock import Mock
from uuid import UUID

from simulator.config import parse_args
from simulator.workload import (
    choose_campaign,
    choose_cart,
    choose_views,
    new_session_id,
    wants_purchase,
)


class ConfigurationTests(unittest.TestCase):
    def test_defaults_and_explicit_options(self):
        defaults = parse_args([])
        self.assertEqual(defaults.session_interval, 3.0)
        self.assertIsNone(defaults.seed)
        self.assertIsNone(defaults.sessions)
        configured = parse_args([
            "--seed", "0", "--session-interval", "0.5", "--sessions", "10",
        ])
        self.assertEqual(vars(configured), {
            "seed": 0, "session_interval": 0.5, "sessions": 10,
        })

    def test_invalid_options_are_rejected(self):
        cases = [
            ("--session-interval", value)
            for value in ("0", "-1", "nan", "inf", "abc")
        ] + [("--sessions", value) for value in ("0", "-1", "1.5")]
        for option, value in cases:
            with self.subTest(option=option, value=value):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        parse_args([option, value])
                self.assertEqual(error.exception.code, 2)


class WorkloadTests(unittest.TestCase):
    def test_session_ids_are_canonical_uuid4(self):
        for _ in range(10):
            session_id = new_session_id()
            self.assertEqual(len(session_id), 36)
            self.assertEqual(str(UUID(session_id)), session_id)
            self.assertEqual(UUID(session_id).version, 4)

    def test_acquisition_threshold_and_organic_fallback(self):
        rng = Mock(spec=Random)
        rng.random.return_value = 0.599999
        rng.choice.return_value = 7
        self.assertEqual(choose_campaign([7], rng), 7)
        rng.random.return_value = 0.60
        self.assertIsNone(choose_campaign([7], rng))
        rng.reset_mock()
        self.assertIsNone(choose_campaign([], rng))
        rng.random.assert_not_called()
        rng.choice.assert_not_called()

    def test_purchase_threshold(self):
        rng = Mock(spec=Random)
        rng.random.return_value = 0.199999
        self.assertTrue(wants_purchase(rng))
        rng.random.return_value = 0.20
        self.assertFalse(wants_purchase(rng))

    def test_empty_catalog_and_repeated_views(self):
        rng = Random(42)
        self.assertEqual(choose_views([], rng), [])
        self.assertEqual(choose_cart([], rng), {})
        views = choose_views([7], rng)
        self.assertTrue(1 <= len(views) <= 4)
        self.assertEqual(set(views), {7})
        cart = choose_cart([7, 7, 7], rng)
        self.assertEqual(list(cart), [7])
        self.assertIn(cart[7], (1, 2))

    def test_choices_respect_available_products_and_cart_limits(self):
        rng = Random(42)
        for _ in range(100):
            views = choose_views([1, 2, 3], rng)
            self.assertTrue(1 <= len(views) <= 4)
            self.assertTrue(set(views) <= {1, 2, 3})
            cart = choose_cart(views, rng)
            self.assertTrue(1 <= len(cart) <= 2)
            self.assertTrue(set(cart) <= set(views))
            self.assertTrue(all(quantity in (1, 2) for quantity in cart.values()))

    def test_seed_reproduces_choices_independently_of_session_ids(self):
        first = Random(17)
        second = Random(17)
        for _ in range(10):
            new_session_id()
            self.assertEqual(choose_campaign([2, 1], first), choose_campaign([1, 2], second))
            views = choose_views([3, 1, 2], first)
            self.assertEqual(views, choose_views([1, 2, 3], second))
            self.assertEqual(wants_purchase(first), wants_purchase(second))
            self.assertEqual(choose_cart(views, first), choose_cart(views, second))


if __name__ == "__main__":
    unittest.main()
