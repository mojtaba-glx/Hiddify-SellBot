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

    def test_panel_fake_status_line_is_filtered(self):
        fake_line = (
            "trojan://1@01.24--2026.06.03.time:900"
            "?security=tls&sni=fake_ip_for_sub_link&type=tcp"
            "#%E2%8F%B3%200.104%2F5GB%20%F0%9F%93%85%2029%20%D8%B1%D9%88%D8%B2"
        )
        sellbot_line = sub_aggregator._build_status_config_line(
            {"name": "vpn", "usage_current": 0.147, "usage_limit": 5, "days_left": 30}
        )

        self.assertTrue(sub_aggregator._is_panel_status_config_line(fake_line))
        self.assertFalse(sub_aggregator._is_panel_status_config_line(sellbot_line))

    def test_build_subscription_keeps_sellbot_status_and_removes_node_status(self):
        fake_line = (
            "trojan://1@01.24--2026.06.03.time:900"
            "?security=tls&sni=fake_ip_for_sub_link&type=tcp"
            "#%E2%8F%B3%200.104%2F5GB%20%F0%9F%93%85%2029%20%D8%B1%D9%88%D8%B2"
        )
        real_line = "vless://00000000-0000-4000-8000-000000000000@example.com:443#real"
        service = {
            "id": 1,
            "name": "vpn",
            "usage_current": 0.147,
            "usage_limit": 5,
            "days_left": 30,
        }

        with (
            patch.object(sub_aggregator.userbot_db, "get_service_by_id", return_value=service),
            patch.object(sub_aggregator, "_service_lock_reason", return_value=None),
            patch.object(sub_aggregator, "_service_targets", return_value=[{"base_url": "https://node.test/sub"}]),
            patch.object(sub_aggregator, "_fetch_subscription_lines", return_value=[fake_line, real_line]),
        ):
            text = sub_aggregator.build_subscription_text_for_service(1)

        self.assertIn("status.hiddify-sellbot.invalid", text)
        self.assertIn(real_line, text)
        self.assertNotIn("fake_ip_for_sub_link", text)


if __name__ == "__main__":
    unittest.main()
