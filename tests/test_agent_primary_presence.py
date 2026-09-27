import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from Shared import sub_links
from AgentBot.services import subscription_service


class PrimaryTargetResolutionTests(unittest.TestCase):
    def test_primary_is_included_even_when_only_child_mapping_exists(self):
        svc = {
            "id": 77,
            "server_id": 1,
            "panel_user_uuid": "primary-uuid",
        }
        primary = {
            "id": 1,
            "title": "سرور ترکیه",
            "panel_type": "hiddify",
            "nodes": [{"target_server_id": 2}],
        }
        child = {
            "id": 2,
            "title": "X-Net",
            "panel_type": "xnet",
        }
        mappings = [
            {
                "service_id": 77,
                "server_id": 2,
                "panel_user_uuid": "primary-uuid",
                "marzban_username": "",
            }
        ]

        with patch.object(
            sub_links.agent_db, "get_service_nodes", return_value=mappings
        ), patch.object(
            sub_links.database,
            "get_server_by_id",
            side_effect=lambda sid: {1: primary, 2: child}.get(int(sid)),
        ), patch.object(
            sub_links.database, "get_servers", return_value=[primary, child]
        ):
            targets = sub_links.get_service_panel_targets(svc)

        self.assertEqual([int(t[0]["id"]) for t in targets], [1, 2])
        self.assertEqual(targets[0][1], "primary-uuid")


class AgentPrimaryLastOnlineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.primary = {"id": 1, "title": "سرور ترکیه", "panel_type": "hiddify"}
        self.child = {"id": 2, "title": "X-Net", "panel_type": "xnet"}
        self.svc = {
            "id": 77,
            "server_id": 1,
            "panel_user_uuid": "shared-uuid",
            "usage_current": 0.1,
            "usage_limit": 1,
        }
        self.targets = [
            (self.primary, "shared-uuid", ""),
            (self.child, "shared-uuid", ""),
        ]

    async def test_offline_card_uses_primary_time_not_fresher_xnet_history(self):
        now = datetime.now(timezone.utc)
        primary_last = (now - timedelta(minutes=3)).isoformat()
        child_last = (now - timedelta(seconds=5)).isoformat()

        async def get_user(server, uuid):
            if int(server["id"]) == 1:
                return {
                    "uuid": uuid,
                    "is_active": True,
                    "current_usage_GB": 0.05,
                    "usage_limit_GB": 1,
                    "last_online": primary_last,
                }
            return {
                "uuid": uuid,
                "is_active": True,
                "_source": "xnet",
                "_user_list_status": "offline",
                "current_usage_GB": 0.05,
                "usage_limit_GB": 1,
                "last_online": child_last,
            }

        with patch.object(
            subscription_service,
            "get_service_panel_targets",
            return_value=self.targets,
        ), patch.object(
            subscription_service.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(
            subscription_service.hiddify_api,
            "list_users",
            new=AsyncMock(return_value=[{
                "uuid": "shared-uuid",
                "last_online": primary_last,
            }]),
        ), patch.object(
            subscription_service.agent_db,
            "update_service",
            return_value=True,
        ):
            label = await subscription_service.get_service_last_online(dict(self.svc))

        self.assertIn("3 دقیقه", label)
        self.assertNotIn("چند ثانیه", label)
        self.assertNotEqual(label, "آنلاین")

    async def test_recent_primary_hiddify_activity_is_online(self):
        now = datetime.now(timezone.utc)
        primary_last = (now - timedelta(seconds=10)).isoformat()
        child_last = (now - timedelta(minutes=2)).isoformat()

        async def get_user(server, uuid):
            if int(server["id"]) == 1:
                return {
                    "uuid": uuid,
                    "is_active": True,
                    "last_online": primary_last,
                }
            return {
                "uuid": uuid,
                "is_active": True,
                "_source": "xnet",
                "_user_list_status": "offline",
                "last_online": child_last,
            }

        with patch.object(
            subscription_service,
            "get_service_panel_targets",
            return_value=self.targets,
        ), patch.object(
            subscription_service.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(
            subscription_service.hiddify_api,
            "list_users",
            new=AsyncMock(return_value=[{
                "uuid": "shared-uuid",
                "last_online": primary_last,
            }]),
        ), patch.object(
            subscription_service.agent_db,
            "update_service",
            return_value=True,
        ):
            label = await subscription_service.get_service_last_online(dict(self.svc))

        self.assertEqual(label, "آنلاین")


if __name__ == "__main__":
    unittest.main()
