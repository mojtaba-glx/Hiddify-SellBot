import tempfile
import unittest
from pathlib import Path

from Shared import userbot_db


class TestTrialSpecFractional(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = userbot_db.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        userbot_db.DB_PATH = Path(self._tmpdir.name) / "hiddify_sellbot.db"
        userbot_db.init_db()

    def tearDown(self):
        userbot_db.DB_PATH = self._orig_db_path
        self._tmpdir.cleanup()

    def test_usage_gb_allows_fractional_values(self):
        userbot_db.set_trial_spec_settings(
            {
                "enabled": True,
                "announce_enabled": False,
                "usage_gb": 0.5,
                "days": 2,
            }
        )
        settings = userbot_db.get_trial_spec_settings()
        self.assertAlmostEqual(float(settings.get("usage_gb")), 0.5, places=6)
        self.assertEqual(int(settings.get("days")), 2)
        self.assertFalse(bool(settings.get("announce_enabled")))

        userbot_db.set_trial_spec_value("usage_gb", 0.3)
        updated = userbot_db.get_trial_spec_settings()
        self.assertAlmostEqual(float(updated.get("usage_gb")), 0.3, places=6)

    def test_usage_gb_minimum_is_point_one(self):
        userbot_db.set_trial_spec_value("usage_gb", 0.01)
        settings = userbot_db.get_trial_spec_settings()
        self.assertAlmostEqual(float(settings.get("usage_gb")), 0.1, places=6)


if __name__ == "__main__":
    unittest.main()
