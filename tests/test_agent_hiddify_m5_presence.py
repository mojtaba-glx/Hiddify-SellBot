import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from AgentBot.handlers import subscriptions
from Shared import hiddify_api


class AgentHiddifyM5PresenceTests(unittest.IsolatedAsyncioTestCase):
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

    async def _status_for_age(self, age: timedelta) -> str | None:
        last_online = (
            datetime.now(timezone.utc) - age
        ).isoformat().replace("+00:00", "Z")

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
            "refresh_user_usage",
            new=AsyncMock(return_value=True),
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

    async def test_three_minutes_ago_is_online_like_hiddify_m5(self):
        self.assertEqual(subscriptions._HIDDIFY_ONLINE_WINDOW_SECONDS, 5 * 60)
        status = await self._status_for_age(timedelta(minutes=3))
        self.assertEqual(status, "online")

    async def test_six_minutes_ago_is_offline(self):
        status = await self._status_for_age(timedelta(minutes=6))
        self.assertEqual(status, "offline")


if __name__ == "__main__":
    unittest.main()
