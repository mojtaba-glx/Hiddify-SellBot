import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from Shared import sub_aggregator, sub_http_server


class SubscriptionExpiryLockTests(unittest.TestCase):
    def test_sanaei_exact_expiry_beats_rounded_zero_days(self):
        expired = datetime.now(timezone.utc) - timedelta(minutes=5)
        user = {
            "remaining_days": 0,
            "expiryTime": int(expired.timestamp() * 1000),
            "expire": expired.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.assertEqual(sub_aggregator._days_left_from_panel_user(user), -1)
        self.assertTrue(sub_aggregator._panel_user_exactly_expired(user))

    def test_date_only_today_is_not_expired_early(self):
        today = datetime.now(timezone.utc).date().isoformat()
        user = {"remaining_days": 0, "expire_date": today}
        self.assertEqual(sub_aggregator._days_left_from_panel_user(user), 0)
        self.assertFalse(sub_aggregator._panel_user_exactly_expired(user))

    def test_agent_expired_smart_sub_returns_only_status_trojan(self):
        expired = datetime.now(timezone.utc) - timedelta(minutes=5)
        svc = {
            "id": 77,
            "agent_id": 3,
            "server_id": 1,
            "name": "expired-agent",
            "panel_user_uuid": "uuid-expired",
            "usage_current": 1,
            "usage_limit": 10,
            "days_left": 0,
            "end_date": expired.strftime("%Y-%m-%d %H:%M:%S"),
            "is_active": 1,
        }
        with patch.object(
            sub_aggregator,
            "_build_status_config_line",
            return_value="trojan://expired@status.invalid:443#subscription-expired",
        ), patch.object(
            sub_aggregator,
            "_fetch_subscription_lines",
            side_effect=AssertionError("real configs must not be fetched after expiry"),
        ), patch(
            "Shared.sub_links.get_service_panel_targets",
            side_effect=AssertionError("expired service must return before target fetch"),
        ):
            body, returned = sub_http_server._build_agent_subscription_body(
                svc, is_b64=False
            )

        self.assertEqual(returned["id"], 77)
        self.assertEqual(
            body,
            "trojan://expired@status.invalid:443#subscription-expired",
        )

    def test_panel_srv_agent_link_uses_agent_lock_path(self):
        server = {"id": 9, "title": "Primary"}
        svc = {
            "id": 88,
            "agent_id": 3,
            "server_id": 9,
            "panel_user_uuid": "uuid-shared",
        }
        expected = "trojan://expired@status.invalid:443#subscription-expired"

        with patch.object(
            sub_http_server.database,
            "get_server_by_id",
            return_value=server,
        ), patch.object(
            sub_http_server.agent_db,
            "get_service_by_uuid",
            return_value=svc,
        ), patch.object(
            sub_http_server,
            "_build_agent_subscription_body",
            return_value=(expected, svc),
        ) as delegated:
            body, returned = sub_http_server._build_panel_uuid_subscription_body(
                "panel-srv-9",
                "uuid-shared",
                False,
            )

        self.assertEqual(body, expected)
        self.assertEqual(returned["id"], 88)
        delegated.assert_called_once_with(svc, False)


if __name__ == "__main__":
    unittest.main()
