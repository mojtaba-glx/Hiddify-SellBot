import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from AgentBot.handlers import subscriptions
from Shared import hiddify_api


class AgentHiddifyPresenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = {
            "id": 901,
            "server_id": 1,
            "panel_user_uuid": "shared-uuid",
            "name": "vpn-0839886",
        }
        self.primary = {
            "id": 1,
            "title": "Hiddify primary",
            "panel_type": "hiddify",
        }
        subscriptions._HIDDIFY_PRESENCE_CACHE.clear()
        subscriptions._HIDDIFY_PRESENCE_TASKS.clear()

    async def _status_for_age(self, age: timedelta, *, active_snapshot: bool = False):
        last_online = (
            datetime.now(timezone.utc) - age
        ).isoformat().replace("+00:00", "Z")
        snapshot = {
            "status": "success",
            "comments": [{"uuid": "shared-uuid", "usage": 1234}] if active_snapshot else [],
        }

        with patch(
            "Shared.sub_links.get_service_panel_targets",
            return_value=[(self.primary, "shared-uuid", "")],
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(return_value={
                "uuid": "shared-uuid",
                "is_active": True,
                "last_online": last_online,
            }),
        ), patch.object(
            hiddify_api,
            "refresh_user_usage_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            hiddify_api,
            "list_users",
            new=AsyncMock(return_value=[{
                "uuid": "shared-uuid",
                "is_active": True,
                "last_online": last_online,
            }]),
        ), patch.object(
            subscriptions.agent_db,
            "mark_service_seen",
        ):
            return await subscriptions._panel_user_status(self.service)

    async def test_five_minute_window_matches_hiddify_m5_metric(self):
        self.assertEqual(subscriptions._HIDDIFY_ONLINE_WINDOW_SECONDS, 5 * 60)
        status = await self._status_for_age(timedelta(minutes=3))
        self.assertEqual(status, "online")

    async def test_six_minutes_without_activity_is_offline(self):
        status = await self._status_for_age(timedelta(minutes=6))
        self.assertEqual(status, "offline")

    async def test_live_usage_snapshot_overrides_stale_last_online(self):
        status = await self._status_for_age(timedelta(minutes=6), active_snapshot=True)
        self.assertEqual(status, "online")


    async def test_legacy_v11_v12_comments_dict_marks_user_online(self):
        stale = (
            datetime.now(timezone.utc) - timedelta(minutes=30)
        ).isoformat().replace("+00:00", "Z")
        snapshot = {
            "status": "success",
            "comments": {"shared-uuid": "0.123MB"},
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        with patch(
            "Shared.sub_links.get_service_panel_targets",
            return_value=[(self.primary, "shared-uuid", "")],
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(return_value={
                "uuid": "shared-uuid",
                "is_active": True,
                "last_online": stale,
            }),
        ), patch.object(
            hiddify_api,
            "refresh_user_usage_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            hiddify_api,
            "list_users",
            new=AsyncMock(return_value=[{
                "uuid": "shared-uuid",
                "is_active": True,
                "last_online": stale,
            }]),
        ), patch.object(
            subscriptions.agent_db,
            "mark_service_seen",
        ):
            status = await subscriptions._panel_user_status(self.service)

        self.assertEqual(status, "online")

    async def test_legacy_usage_snapshot_json_string_is_unwrapped(self):
        payload = {
            "status": "success",
            "comments": {"shared-uuid": "0.250MB"},
            "date": "2026-09-27 11:45:00",
        }
        with patch.object(
            hiddify_api,
            "_get_panel_url",
            return_value="https://panel.example",
        ), patch.object(
            hiddify_api,
            "_get_admin_proxy",
            return_value="admin-proxy",
        ), patch.object(
            hiddify_api,
            "_request",
            new=AsyncMock(return_value=json.dumps(payload)),
        ):
            result = await hiddify_api.refresh_user_usage_snapshot(self.primary)

        self.assertEqual(result, payload)
        self.assertEqual(result["comments"]["shared-uuid"], "0.250MB")


if __name__ == "__main__":
    unittest.main()
