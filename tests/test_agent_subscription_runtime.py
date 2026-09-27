import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from AgentBot.services import subscription_service


class SubscriptionTimeFormattingTests(unittest.TestCase):
    def test_panel_datetime_accepts_timezone_fraction_and_z(self):
        tehran = subscription_service._parse_panel_datetime(
            "2026-09-09T15:30:00.500000+03:30"
        )
        utc = subscription_service._parse_panel_datetime("2026-09-09T12:00:00.500000Z")
        self.assertEqual(tehran, utc)
        self.assertEqual(utc.tzinfo, timezone.utc)

    def test_panel_datetime_rejects_never_connected_placeholder(self):
        self.assertIsNone(
            subscription_service._parse_panel_datetime("0001-01-01T00:00:00Z")
        )
        self.assertIsNone(
            subscription_service._parse_panel_datetime("1970-01-01T00:00:01Z")
        )

    def test_expiry_uses_absolute_end_time_and_keeps_hours(self):
        now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        end = now + timedelta(days=2, hours=5, minutes=40)
        text = subscription_service.format_service_expiry(
            {"days_left": 30, "end_date": end.isoformat()}, now=now
        )
        self.assertEqual(text, "2 روز و 5 ساعت دیگر")

    def test_expired_time_is_not_rendered_as_future(self):
        now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        end = now - timedelta(days=1, hours=3)
        text = subscription_service.format_service_expiry(
            {"days_left": 30, "end_date": end.isoformat()}, now=now
        )
        self.assertEqual(text, "منقضی شده (1 روز و 3 ساعت پیش)")


class SubscriptionRuntimeRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_refresh_uses_panel_expiry_and_latest_node_connection(self):
        now = datetime.now(timezone.utc)
        primary = {"id": 10, "title": "primary"}
        node = {"id": 11, "title": "node"}
        service = {
            "id": 7,
            "server_id": 10,
            "panel_user_uuid": "uuid-1",
            "usage_current": 99,
            "usage_limit": 20,
            "days_left": 30,
            "end_date": (now + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
            "is_active": 1,
        }
        panel_users = {
            10: {
                "uuid": "uuid-1",
                "current_usage_GB": 3.25,
                "usage_limit_GB": 20,
                "expire_date": (now + timedelta(days=9, hours=7)).isoformat(),
                "last_online": (now - timedelta(days=2, hours=4)).isoformat(),
                "is_active": True,
            },
            11: {
                "uuid": "uuid-1",
                "current_usage_GB": 1.5,
                "usage_limit_GB": 20,
                "expire_date": (now + timedelta(days=9, hours=7)).isoformat(),
                "last_online": (now - timedelta(hours=3, minutes=25)).isoformat(),
                "is_active": True,
            },
        }

        async def get_user(server, _uuid):
            return panel_users[server["id"]]

        targets = [(primary, "uuid-1", ""), (node, "uuid-1", "")]
        with patch.object(
            subscription_service, "get_service_panel_targets", return_value=targets
        ), patch.object(
            subscription_service.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(subscription_service.agent_db, "update_service", return_value=True) as update:
            last_online = await subscription_service.get_service_last_online(service)

        self.assertTrue(last_online.startswith("3 ساعت و 25 دقیقه پیش"), last_online)
        self.assertAlmostEqual(service["usage_current"], 4.75)
        self.assertIn("9 روز و 6 ساعت دیگر", subscription_service.format_service_expiry(service))
        update.assert_called_once()
        saved = update.call_args.args[1]
        self.assertAlmostEqual(saved["usage_current"], 4.75)
        self.assertNotEqual(saved["days_left"], 30)

    async def test_never_connected_xnet_user_does_not_render_huge_days_ago(self):
        now = datetime.now(timezone.utc)
        server = {"id": 21, "title": "X-Net", "panel_type": "xnet"}
        service = {
            "id": 81,
            "server_id": 21,
            "panel_user_uuid": "fresh-xnet",
            "usage_current": 0,
            "usage_limit": 10,
            "days_left": 30,
            "is_active": 1,
        }
        panel_user = {
            "uuid": "fresh-xnet",
            "current_usage_GB": 0,
            "usage_limit_GB": 10,
            "expire_date": (now + timedelta(days=30)).isoformat(),
            "last_online": "0001-01-01T00:00:00Z",
            "is_active": True,
            "_source": "xnet",
            "_user_list_status": "offline",
        }
        with patch.object(
            subscription_service,
            "get_service_panel_targets",
            return_value=[(server, "fresh-xnet", "")],
        ), patch.object(
            subscription_service.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(return_value=panel_user),
        ), patch.object(
            subscription_service.agent_db, "update_service", return_value=True
        ):
            last_online = await subscription_service.get_service_last_online(service)

        self.assertEqual(last_online, "هنوز متصل نشده")

    async def test_hiddify_primary_with_xnet_node_never_connected(self):
        now = datetime.now(timezone.utc)
        primary = {"id": 31, "title": "Hiddify", "panel_type": "hiddify"}
        node = {"id": 32, "title": "X-Net", "panel_type": "xnet"}
        service = {
            "id": 91,
            "server_id": 31,
            "panel_user_uuid": "mixed-uuid",
            "usage_current": 0,
            "usage_limit": 10,
            "days_left": 30,
            "is_active": 1,
        }
        panel_users = {
            31: {
                "uuid": "mixed-uuid",
                "current_usage_GB": 0,
                "usage_limit_GB": 10,
                "expire_date": (now + timedelta(days=30)).isoformat(),
                "last_online": None,
                "is_active": True,
                "_source": "hiddify",
            },
            32: {
                "uuid": "mixed-uuid",
                "current_usage_GB": 0,
                "usage_limit_GB": 10,
                "expire_date": (now + timedelta(days=30)).isoformat(),
                "last_online": "0001-01-01T00:00:00Z",
                "is_active": True,
                "_source": "xnet",
                "_user_list_status": "offline",
            },
        }

        async def get_user(server, _uuid):
            return panel_users[server["id"]]

        with patch.object(
            subscription_service,
            "get_service_panel_targets",
            return_value=[
                (primary, "mixed-uuid", ""),
                (node, "mixed-uuid", ""),
            ],
        ), patch.object(
            subscription_service.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(
            subscription_service.agent_db, "update_service", return_value=True
        ):
            last_online = await subscription_service.get_service_last_online(service)

        self.assertEqual(last_online, "هنوز متصل نشده")

    async def test_hiddify_primary_with_xnet_node_uses_latest_real_connection(self):
        now = datetime.now(timezone.utc)
        primary = {"id": 41, "title": "Hiddify", "panel_type": "hiddify"}
        node = {"id": 42, "title": "X-Net", "panel_type": "xnet"}
        service = {
            "id": 92,
            "server_id": 41,
            "panel_user_uuid": "mixed-used-uuid",
            "usage_current": 0,
            "usage_limit": 10,
            "days_left": 30,
            "is_active": 1,
        }
        panel_users = {
            41: {
                "uuid": "mixed-used-uuid",
                "current_usage_GB": 0,
                "usage_limit_GB": 10,
                "expire_date": (now + timedelta(days=30)).isoformat(),
                "last_online": None,
                "is_active": True,
                "_source": "hiddify",
            },
            42: {
                "uuid": "mixed-used-uuid",
                "current_usage_GB": 0.25,
                "usage_limit_GB": 10,
                "expire_date": (now + timedelta(days=30)).isoformat(),
                "last_online": (now - timedelta(minutes=8)).isoformat(),
                "is_active": True,
                "_source": "xnet",
                "_user_list_status": "offline",
            },
        }

        async def get_user(server, _uuid):
            return panel_users[server["id"]]

        with patch.object(
            subscription_service,
            "get_service_panel_targets",
            return_value=[
                (primary, "mixed-used-uuid", ""),
                (node, "mixed-used-uuid", ""),
            ],
        ), patch.object(
            subscription_service.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(
            subscription_service.agent_db, "update_service", return_value=True
        ):
            last_online = await subscription_service.get_service_last_online(service)

        self.assertIn("8 دقیقه پیش", last_online)

    async def test_real_xnet_last_online_is_shown_after_first_connection(self):
        now = datetime.now(timezone.utc)
        server = {"id": 22, "title": "X-Net", "panel_type": "xnet"}
        service = {
            "id": 82,
            "server_id": 22,
            "panel_user_uuid": "used-xnet",
            "usage_current": 0.1,
            "usage_limit": 10,
            "days_left": 30,
            "is_active": 1,
        }
        panel_user = {
            "uuid": "used-xnet",
            "current_usage_GB": 0.1,
            "usage_limit_GB": 10,
            "expire_date": (now + timedelta(days=30)).isoformat(),
            "last_online": (now - timedelta(minutes=12)).isoformat(),
            "is_active": True,
            "_source": "xnet",
            "_user_list_status": "offline",
        }
        with patch.object(
            subscription_service,
            "get_service_panel_targets",
            return_value=[(server, "used-xnet", "")],
        ), patch.object(
            subscription_service.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(return_value=panel_user),
        ), patch.object(
            subscription_service.agent_db, "update_service", return_value=True
        ):
            last_online = await subscription_service.get_service_last_online(service)

        self.assertIn("دقیقه پیش", last_online)
        self.assertNotEqual(last_online, "هنوز متصل نشده")


if __name__ == "__main__":
    unittest.main()
