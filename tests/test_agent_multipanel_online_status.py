import asyncio
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
        subscriptions._HIDDIFY_PRESENCE_CACHE.clear()
        subscriptions._HIDDIFY_PRESENCE_TASKS.clear()

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

    async def test_hiddify_list_presence_overrides_stale_direct_last_online(self):
        from datetime import datetime, timezone

        targets = [(self.primary, "shared-uuid", "")]
        fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        subscriptions._HIDDIFY_PRESENCE_CACHE.clear()
        subscriptions._HIDDIFY_PRESENCE_TASKS.clear()

        with patch(
            "Shared.sub_links.get_service_panel_targets",
            return_value=targets,
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(return_value={
                "uuid": "shared-uuid",
                "is_active": True,
                "last_online": "2020-01-01T00:00:00Z",
            }),
        ), patch.object(
            hiddify_api,
            "refresh_user_usage_snapshot",
            new=AsyncMock(return_value={"status": "success", "comments": []}),
        ), patch.object(
            hiddify_api,
            "list_users",
            new=AsyncMock(return_value=[{
                "uuid": "shared-uuid",
                "is_active": True,
                "last_online": fresh,
            }]),
        ) as list_users, patch.object(
            subscriptions.agent_db,
            "mark_service_seen",
        ):
            status = await subscriptions._panel_user_status(self.service)

        self.assertEqual(status, "online")
        list_users.assert_awaited_once()

    async def test_hiddify_presence_snapshot_is_shared_between_service_checks(self):
        from datetime import datetime, timezone

        service2 = dict(self.service)
        service2["id"] = 502
        service2["panel_user_uuid"] = "second-uuid"
        targets_by_uuid = {
            "shared-uuid": [(self.primary, "shared-uuid", "")],
            "second-uuid": [(self.primary, "second-uuid", "")],
        }
        fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        subscriptions._HIDDIFY_PRESENCE_CACHE.clear()
        subscriptions._HIDDIFY_PRESENCE_TASKS.clear()

        def target_side_effect(svc):
            return targets_by_uuid[str(svc["panel_user_uuid"])]

        async def get_user(server, uuid):
            return {
                "uuid": uuid,
                "is_active": True,
                "last_online": "2020-01-01T00:00:00Z",
            }

        with patch(
            "Shared.sub_links.get_service_panel_targets",
            side_effect=target_side_effect,
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(
            hiddify_api,
            "refresh_user_usage_snapshot",
            new=AsyncMock(return_value={"status": "success", "comments": []}),
        ), patch.object(
            hiddify_api,
            "list_users",
            new=AsyncMock(return_value=[
                {"uuid": "shared-uuid", "is_active": True, "last_online": fresh},
                {"uuid": "second-uuid", "is_active": True, "last_online": fresh},
            ]),
        ) as list_users, patch.object(
            subscriptions.agent_db,
            "mark_service_seen",
        ):
            statuses = await asyncio.gather(
                subscriptions._panel_user_status(self.service),
                subscriptions._panel_user_status(service2),
            )

        self.assertEqual(statuses, ["online", "online"])
        list_users.assert_awaited_once()

    async def test_hiddify_m5_last_online_is_still_online(self):
        from datetime import datetime, timedelta, timezone

        targets = [(self.primary, "shared-uuid", "")]
        recent = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()

        with patch(
            "Shared.sub_links.get_service_panel_targets",
            return_value=targets,
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(return_value={
                "uuid": "shared-uuid",
                "is_active": True,
                "last_online": recent,
            }),
        ), patch.object(
            hiddify_api,
            "refresh_user_usage_snapshot",
            new=AsyncMock(return_value={"status": "success", "comments": []}),
        ), patch.object(
            hiddify_api,
            "list_users",
            new=AsyncMock(return_value=[{
                "uuid": "shared-uuid",
                "is_active": True,
                "last_online": recent,
            }]),
        ), patch.object(
            subscriptions.agent_db,
            "mark_service_seen",
        ):
            status = await subscriptions._panel_user_status(self.service)

        self.assertEqual(status, "online")

    async def test_hiddify_small_clock_skew_does_not_force_offline(self):
        from datetime import datetime, timedelta, timezone

        targets = [(self.primary, "shared-uuid", "")]
        slightly_future = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()

        with patch(
            "Shared.sub_links.get_service_panel_targets",
            return_value=targets,
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(return_value={
                "uuid": "shared-uuid",
                "is_active": True,
                "last_online": slightly_future,
            }),
        ), patch.object(
            hiddify_api,
            "refresh_user_usage_snapshot",
            new=AsyncMock(return_value={"status": "success", "comments": []}),
        ), patch.object(
            hiddify_api,
            "list_users",
            new=AsyncMock(return_value=[{
                "uuid": "shared-uuid",
                "is_active": True,
                "last_online": slightly_future,
            }]),
        ), patch.object(
            subscriptions.agent_db,
            "mark_service_seen",
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
