import os
import unittest
from unittest.mock import patch
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

    def test_subscription_output_can_hide_status_config_for_hiddify(self):
        service = {
            "id": 1,
            "name": "mojtaba",
            "usage_current": 0.093,
            "usage_limit": 15,
            "days_left": 30,
        }
        with (
            patch.object(sub_aggregator.userbot_db, "get_service_by_id", return_value=service),
            patch.object(sub_aggregator, "_service_lock_reason", return_value=""),
            patch.object(sub_aggregator, "_service_targets", return_value=[{"base_url": "https://node.example.com"}]),
            patch.object(sub_aggregator, "_fetch_subscription_lines", return_value=["vless://real-config"]),
        ):
            with_status = sub_aggregator.build_subscription_text_for_service(1)
            without_status = sub_aggregator.build_subscription_text_for_service(
                1,
                include_status_config=False,
            )

        self.assertIn("status.hiddify-sellbot.invalid", with_status)
        self.assertEqual(without_status, "vless://real-config")


if __name__ == "__main__":
    unittest.main()
