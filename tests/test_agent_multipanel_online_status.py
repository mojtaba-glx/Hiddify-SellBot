import unittest
from unittest.mock import AsyncMock, patch

from AgentBot.handlers import subscriptions
from Shared import hiddify_api


class AgentMultiPanelOnlineStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = {
            "id": 501,
            "server_id": 1,
            "panel_user_uuid": "shared-uuid",
            "name": "vpn-0839886",
        }
        self.primary = {"id": 1, "title": "سرور اصلی", "panel_type": "hiddify"}
        self.xnet_node = {"id": 2, "title": "X-Net", "panel_type": "xnet"}

    async def test_xnet_node_online_makes_mixed_service_online(self):
        targets = [
            (self.primary, "shared-uuid", ""),
            (self.xnet_node, "shared-uuid", ""),
        ]

        async def get_user(server, uuid):
            if int(server["id"]) == 1:
                return {
                    "is_active": True,
                    "_source": "hiddify",
                    "last_online": "2020-01-01T00:00:00Z",
                }
            return {
                "is_active": True,
                "_source": "xnet",
                "_user_list_status": "online",
                "last_online": "2026-09-27T03:45:00Z",
            }

        with patch(
            "Shared.sub_links.get_service_panel_targets",
            return_value=targets,
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(
            subscriptions.agent_db,
            "mark_service_seen",
        ), patch.object(
            subscriptions.agent_db,
            "mark_service_missing",
        ):
            status = await subscriptions._panel_user_status(self.service)

        self.assertEqual(status, "online")

    async def test_all_reachable_active_targets_offline_stays_offline(self):
        targets = [
            (self.primary, "shared-uuid", ""),
            (self.xnet_node, "shared-uuid", ""),
        ]

        async def get_user(server, uuid):
            if int(server["id"]) == 1:
                return {
                    "is_active": True,
                    "_source": "hiddify",
                    "last_online": "2020-01-01T00:00:00Z",
                }
            return {
                "is_active": True,
                "_source": "xnet",
                "_user_list_status": "offline",
                "last_online": "2026-09-26T00:00:00Z",
            }

        with patch(
            "Shared.sub_links.get_service_panel_targets",
            return_value=targets,
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(
            subscriptions.agent_db,
            "mark_service_seen",
        ):
            status = await subscriptions._panel_user_status(self.service)

        self.assertEqual(status, "offline")

    async def test_primary_expired_remains_authoritative_when_no_target_online(self):
        targets = [
            (self.primary, "shared-uuid", ""),
            (self.xnet_node, "shared-uuid", ""),
        ]

        async def get_user(server, uuid):
            if int(server["id"]) == 1:
                return {
                    "is_active": False,
                    "_source": "hiddify",
                }
            return {
                "is_active": True,
                "_source": "xnet",
                "_user_list_status": "offline",
            }

        with patch(
            "Shared.sub_links.get_service_panel_targets",
            return_value=targets,
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(
            subscriptions.agent_db,
            "mark_service_seen",
        ):
            status = await subscriptions._panel_user_status(self.service)

        self.assertEqual(status, "expired")


if __name__ == "__main__":
    unittest.main()
