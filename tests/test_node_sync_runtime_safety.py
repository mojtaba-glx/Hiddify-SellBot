import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from AdminBot import servers


class NodeSyncRuntimeSafetyTests(unittest.TestCase):
    def test_existing_sync_payload_preserves_identity_traffic_and_state(self):
        source = {
            "uuid": "11111111-1111-1111-1111-111111111111",
            "name": "vpn-123",
            "usage_limit_GB": 10,
            "package_days": 30,
            "start_date": "2026-09-01",
            "last_reset_time": "2026-09-02 12:00:00",
            "comment": "note",
            "is_active": False,
            "current_usage_GB": 7.25,
        }

        payload = servers._build_node_sync_payload(source, for_create=False)

        self.assertEqual(payload["usage_limit_GB"], 10.0)
        self.assertEqual(payload["package_days"], 30)
        self.assertEqual(payload["start_date"], "2026-09-01")
        for forbidden in (
            "name",
            "uuid",
            "current_usage_GB",
            "last_reset_time",
            "is_active",
            "comment",
        ):
            self.assertNotIn(forbidden, payload)

    def test_new_node_payload_still_has_initial_identity_and_state(self):
        source = {
            "uuid": "11111111-1111-1111-1111-111111111111",
            "name": "vpn-123",
            "usage_limit_GB": 10,
            "package_days": 30,
            "start_date": "2026-09-01",
            "last_reset_time": "2026-09-02 12:00:00",
            "comment": "note",
            "is_active": False,
        }

        payload = servers._build_node_sync_payload(source, for_create=True)

        self.assertEqual(payload["name"], "vpn-123")
        self.assertEqual(payload["current_usage_GB"], 0)
        self.assertEqual(payload["last_reset_time"], "2026-09-02 12:00:00")
        self.assertFalse(payload["is_active"])

    def test_status_sync_only_disables_target_without_patching_profile(self):
        source = {"id": 1, "title": "Germany"}
        target = {"id": 2, "title": "France"}
        user_uuid = "11111111-1111-1111-1111-111111111111"
        source_user = {
            "uuid": user_uuid,
            "name": "vpn-123",
            "is_active": False,
        }
        target_user = {
            "uuid": user_uuid,
            "name": "different-node-name",
            "is_active": True,
            "current_usage_GB": 4.5,
        }

        async def list_users(server):
            return [source_user] if int(server["id"]) == 1 else [target_user]

        disable = AsyncMock()
        enable = AsyncMock()

        with patch.object(
            servers,
            "_node_sync_targets",
            return_value=(source, [target], []),
        ), patch.object(
            servers,
            "_invalidate_node_target_caches",
        ), patch.object(
            servers.hiddify_api,
            "list_users",
            new=AsyncMock(side_effect=list_users),
        ), patch.object(
            servers.hiddify_api,
            "disable_user",
            new=disable,
        ), patch.object(
            servers.hiddify_api,
            "enable_user",
            new=enable,
        ), patch.object(
            servers.hiddify_api,
            "patch_user",
            new=AsyncMock(),
        ) as patch_user, patch.object(
            servers,
            "_record_node_sync_mapping",
            return_value=True,
        ):
            summary = asyncio.run(servers._run_node_status_sync(1))

        self.assertEqual(summary["disabled"], 1)
        self.assertEqual(summary["enabled"], 0)
        self.assertEqual(summary["status_changed"], 1)
        disable.assert_awaited_once_with(target, user_uuid)
        enable.assert_not_awaited()
        patch_user.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
