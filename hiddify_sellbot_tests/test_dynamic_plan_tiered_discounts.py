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
            "discount_tiered_enabled": True,
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
            "discount_simple_enabled": True,
            "discount_step_gb": 50,
            "discount_percent_step": 5,
            "discount_percent_max": 15,
            "discount_tiers": [],
        }

        self.assertEqual(user_main._calc_dynamic_price(150, 0, settings), (127500, 15))

    def test_both_discounts_enabled_chooses_best(self):
        """When both discounts enabled, should apply the best (highest) discount."""
        settings = {
            "price_per_gb": 1000,
            "price_per_month": 0,
            "discount_tiered_enabled": True,
            "discount_simple_enabled": True,
            "discount_step_gb": 50,
            "discount_percent_step": 6,  # 6% per 50GB (for 50GB: 6%, for 100GB: 12%)
            "discount_percent_max": 20,
            "discount_tiers": [
                {"gb": 50, "percent": 5},   # 5% at 50GB
                {"gb": 100, "percent": 10}, # 10% at 100GB
                {"gb": 200, "percent": 30}, # 30% at 200GB
            ],
        }
        
        # For 50GB: tiered=5%, simple=6% → should use 6%
        self.assertEqual(user_main._calc_dynamic_price(50, 0, settings), (47000, 6))
        
        # For 100GB: tiered=10%, simple=12% → should use 12%
        self.assertEqual(user_main._calc_dynamic_price(100, 0, settings), (88000, 12))
        
        # For 200GB: tiered=30%, simple=24% (100GB*6%=24% max@20%=20%) → should use 30%
        self.assertEqual(user_main._calc_dynamic_price(200, 0, settings), (140000, 30))


if __name__ == "__main__":
    unittest.main()
