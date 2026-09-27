import re
import unittest
from unittest.mock import patch

from Shared import xui_common


class SanaeiEmailUniquenessTests(unittest.TestCase):
    def test_unique_english_name_keeps_exact_value_even_if_bot_db_has_same_name(self):
        with patch.object(
            xui_common,
            "_bot_has_service_name",
            side_effect=AssertionError("bot DB must not participate in Sanaei email uniqueness"),
        ):
            result = xui_common._unique_xui_email(
                "vpn-0839886",
                set(),
                "12345678-1234-1234-1234-123456789abc",
            )
        self.assertEqual(result, "vpn-0839886")

    def test_real_panel_collision_still_gets_suffix(self):
        base = "vpn-0839886"
        result = xui_common._unique_xui_email(
            base,
            {base, base.lower()},
            "12345678-1234-1234-1234-123456789abc",
        )
        self.assertNotEqual(result, base)
        self.assertTrue(result.startswith(base))

    def test_existing_sanitizer_behavior_is_unchanged(self):
        self.assertEqual(
            xui_common._sanitize_xui_email("vpn-0839886", "fallback"),
            "vpn-0839886",
        )
        persian = xui_common._sanitize_xui_email("علی رضایی", "fallback")
        self.assertNotEqual(persian, "fallback")
        self.assertRegex(persian, r"^[A-Za-z0-9._-]+$")


if __name__ == "__main__":
    unittest.main()
