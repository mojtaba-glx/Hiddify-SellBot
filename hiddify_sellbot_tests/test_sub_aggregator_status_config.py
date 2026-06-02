import os
import unittest
from urllib.parse import unquote

from Shared import sub_aggregator


class TestSubAggregatorStatusConfig(unittest.TestCase):
    def tearDown(self):
        for key in (
            "SUB_STATUS_CONFIG_ENABLED",
            "SUB_STATUS_CONFIG_HOST",
            "SUB_STATUS_CONFIG_SNI",
            "SUB_STATUS_CONFIG_PORT",
        ):
            os.environ.pop(key, None)

    def test_status_config_line_contains_usage_and_days(self):
        service = {
            "name": "mojtaba",
            "usage_current": 0.093,
            "usage_limit": 15,
            "days_left": 30,
        }

        line = sub_aggregator._build_status_config_line(service)

        self.assertTrue(line.startswith("trojan://1@status.hiddify-sellbot.invalid:443"))
        self.assertIn("security=tls", line)
        label = unquote(line.split("#", 1)[1])
        self.assertEqual(label, "📊 mojtaba | ⏳ 0.093/15GB | 📅 30 روز")

    def test_status_config_line_can_be_disabled(self):
        os.environ["SUB_STATUS_CONFIG_ENABLED"] = "false"

        line = sub_aggregator._build_status_config_line({"name": "x"})

        self.assertEqual(line, "")


if __name__ == "__main__":
    unittest.main()
