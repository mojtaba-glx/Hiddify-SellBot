import unittest
from datetime import datetime, timedelta

from Shared.hiddify_api import _normalize_hiddify_payload


class HiddifyExpiryPayloadNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "usage_limit_GB": 15.0,
            "package_days": 30,
            "start_date": "2026-10-17",
            "current_usage_GB": 0,
            "is_active": True,
            "expire": "2026-11-16",
            "expire_date": "2026-11-16",
        }

    def test_v11_v12_remove_read_only_expiry_aliases(self):
        for major in (11, 12):
            with self.subTest(panel_major=major):
                normalized = _normalize_hiddify_payload(self.payload, major)
                self.assertNotIn("expire", normalized)
                self.assertNotIn("expire_date", normalized)
                self.assertEqual(normalized["package_days"], 30)
                self.assertEqual(normalized["start_date"], "2026-10-17")
                self.assertTrue(normalized["is_active"])

    def test_v13_removes_expiry_aliases_and_maps_active_switch(self):
        normalized = _normalize_hiddify_payload(self.payload, 13)
        self.assertNotIn("expire", normalized)
        self.assertNotIn("expire_date", normalized)
        self.assertNotIn("is_active", normalized)
        self.assertIs(normalized["enable"], True)
        self.assertEqual(normalized["package_days"], 30)
        self.assertEqual(normalized["start_date"], "2026-10-17")

    def test_unknown_version_uses_cross_version_switch_fields(self):
        normalized = _normalize_hiddify_payload(self.payload, 0)
        self.assertNotIn("expire", normalized)
        self.assertNotIn("expire_date", normalized)
        self.assertIs(normalized["is_active"], True)
        self.assertIs(normalized["enable"], True)
        self.assertEqual(normalized["package_days"], 30)

    def test_unknown_version_keeps_disable_intent(self):
        normalized = _normalize_hiddify_payload({"mode": "disable"}, 0)
        self.assertEqual(normalized["mode"], "no_reset")
        self.assertIs(normalized["is_active"], False)
        self.assertIs(normalized["enable"], False)

    def test_legacy_expiry_only_payload_is_translated_to_supported_fields(self):
        expiry_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        normalized = _normalize_hiddify_payload({"expire_date": expiry_date}, 12)

        self.assertNotIn("expire_date", normalized)
        self.assertIn("start_date", normalized)
        self.assertEqual(normalized["start_date"], datetime.now().strftime("%Y-%m-%d"))
        self.assertEqual(normalized["package_days"], 30)

    def test_normalization_does_not_mutate_the_callers_payload(self):
        original = dict(self.payload)
        _normalize_hiddify_payload(self.payload, 12)
        self.assertEqual(self.payload, original)


if __name__ == "__main__":
    unittest.main()
