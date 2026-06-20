import unittest

from AdminBot import plans
from Shared import plans_storage
from UserBot import main as user_main


class TestDynamicPlanTieredDiscounts(unittest.TestCase):
    def test_normalizes_tiers_by_threshold(self):
        tiers = plans_storage.normalize_discount_tiers(
            [
                {"gb": "100", "percent": "10"},
                {"gb": 50, "percent": 5},
                {"gb": 100, "percent": 12},
                {"gb": 0, "percent": 99},
            ]
        )

        self.assertEqual(tiers, [{"gb": 50, "percent": 5}, {"gb": 100, "percent": 12}])

    def test_parses_admin_tier_input(self):
        tiers = plans._parse_discount_tiers_text("50:5, 100:10, 200:15")

        self.assertEqual(
            tiers,
            [
                {"gb": 50, "percent": 5},
                {"gb": 100, "percent": 10},
                {"gb": 200, "percent": 15},
            ],
        )

    def test_parses_admin_tier_input_with_persian_digits(self):
        tiers = plans._parse_discount_tiers_text("۵۰:۵, ۱۰۰:۱۰, ۲۰۰:۱۵")

        self.assertEqual(
            tiers,
            [
                {"gb": 50, "percent": 5},
                {"gb": 100, "percent": 10},
                {"gb": 200, "percent": 15},
            ],
        )

    def test_tiered_discount_uses_highest_reached_threshold(self):
        settings = {
            "price_per_gb": 1000,
            "price_per_month": 0,
            "discount_step_gb": 50,
            "discount_percent_step": 50,
            "discount_percent_max": 50,
            "discount_tiers": [
                {"gb": 50, "percent": 5},
                {"gb": 100, "percent": 10},
                {"gb": 200, "percent": 20},
            ],
        }

        self.assertEqual(user_main._calc_dynamic_price(40, 0, settings), (40000, 0))
        self.assertEqual(user_main._calc_dynamic_price(120, 0, settings), (108000, 10))
        self.assertEqual(user_main._calc_dynamic_price(250, 0, settings), (200000, 20))

    def test_legacy_discount_still_works_without_tiers(self):
        settings = {
            "price_per_gb": 1000,
            "price_per_month": 0,
            "discount_step_gb": 50,
            "discount_percent_step": 5,
            "discount_percent_max": 15,
            "discount_tiers": [],
        }

        self.assertEqual(user_main._calc_dynamic_price(150, 0, settings), (127500, 15))


if __name__ == "__main__":
    unittest.main()
