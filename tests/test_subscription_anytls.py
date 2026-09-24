import unittest
from unittest.mock import patch

from Shared import sub_aggregator


class TestManagedSubscriptionAnyTLS(unittest.TestCase):
    def test_anytls_is_an_allowed_managed_subscription_scheme(self):
        self.assertTrue(
            sub_aggregator._is_config_line(
                "anytls://secret@example.com:443?sni=example.com#AnyTLS"
            )
        )
        self.assertTrue(
            sub_aggregator._is_config_line(
                "ANYTLS://secret@example.com:443?sni=example.com#AnyTLS"
            )
        )

    def test_anytls_dedup_ignores_display_fragment(self):
        first = (
            "anytls://secret@example.com:443?sni=example.com#Node-A"
        )
        second = (
            "anytls://secret@example.com:443?sni=example.com#Node-B"
        )
        self.assertEqual(
            sub_aggregator._dedup_key_for_line(first),
            sub_aggregator._dedup_key_for_line(second),
        )

    def test_managed_subscription_keeps_xnet_anytls_line(self):
        anytls = "anytls://secret@xnet.example.com:443?sni=xnet.example.com#AnyTLS"
        vless = "vless://uuid@xnet.example.com:8443?security=tls#VLESS"
        service = {"id": 91, "name": "demo"}

        target = {
            "server_id": 4,
            "server": {"id": 4, "panel_type": "xnet"},
            "uuid": "user-uuid",
            "base_url": "https://xnet.example.com/api/v1/sub/user-uuid",
            "marzban_username": "",
        }

        with patch.object(
            sub_aggregator.userbot_db,
            "get_service_by_id",
            return_value=service,
        ), patch.object(
            sub_aggregator,
            "_service_lock_reason",
            return_value=None,
        ), patch.object(
            sub_aggregator,
            "_service_targets",
            return_value=[target],
        ), patch.object(
            sub_aggregator,
            "_fetch_subscription_lines",
            return_value=[anytls, vless],
        ), patch.object(
            sub_aggregator,
            "_build_status_config_line",
            return_value="",
        ):
            body = sub_aggregator.build_subscription_text_for_service(91)

        self.assertIn(anytls, body.splitlines())
        self.assertIn(vless, body.splitlines())


if __name__ == "__main__":
    unittest.main()
