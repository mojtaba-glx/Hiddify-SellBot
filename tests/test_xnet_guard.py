import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from AdminBot import xnet_guard
from Shared import userbot_db, xnet_api


class XnetGuardSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = userbot_db.DB_PATH
        userbot_db.DB_PATH = Path(self.tmp.name) / "userbot.db"
        userbot_db.init_db()

    def tearDown(self):
        userbot_db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_recovery_baseline_accumulates_new_live_usage(self):
        userbot_db.upsert_xnet_guard_snapshot_users(
            7,
            [{
                "uuid": "u-1",
                "name": "demo",
                "usage_limit_GB": 30,
                "current_usage_GB": 10,
                "expireDate": "2026-10-30T00:00:00Z",
                "is_active": True,
            }],
        )
        userbot_db.set_xnet_guard_recovery_base(7, "u-1", 10)

        # Restored X-NET account starts from 0 and now consumed 5 GB more.
        userbot_db.upsert_xnet_guard_snapshot_users(
            7,
            [{
                "uuid": "u-1",
                "name": "demo",
                "usage_limit_GB": 20,
                "current_usage_GB": 5,
                "expireDate": "2026-10-30T00:00:00Z",
                "is_active": True,
            }],
        )

        snap = userbot_db.get_xnet_guard_snapshot(7, "u-1")
        self.assertIsNotNone(snap)
        self.assertAlmostEqual(float(snap["usage_limit_gb"]), 30.0)
        self.assertAlmostEqual(float(snap["usage_current_gb"]), 15.0)
        self.assertAlmostEqual(float(snap["recovery_base_gb"]), 10.0)

    def test_later_expiry_resets_recovery_baseline_as_new_period(self):
        userbot_db.upsert_xnet_guard_snapshot_users(
            7,
            [{
                "uuid": "u-1",
                "usage_limit_GB": 30,
                "current_usage_GB": 10,
                "expireDate": "2026-10-30T00:00:00Z",
                "is_active": True,
            }],
        )
        userbot_db.set_xnet_guard_recovery_base(7, "u-1", 10)
        userbot_db.upsert_xnet_guard_snapshot_users(
            7,
            [{
                "uuid": "u-1",
                "usage_limit_GB": 30,
                "current_usage_GB": 1,
                "expireDate": "2026-11-30T00:00:00Z",
                "is_active": True,
            }],
        )

        snap = userbot_db.get_xnet_guard_snapshot(7, "u-1")
        self.assertAlmostEqual(float(snap["usage_current_gb"]), 1.0)
        self.assertAlmostEqual(float(snap["recovery_base_gb"]), 0.0)


class XnetGuardRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_uses_frozen_usage_and_only_grants_remaining_quota(self):
        server = {
            "id": 7,
            "panel_type": "xnet",
            "panel_url": "http://127.0.0.1:8080",
            "xnet_inbound_id": "0",
        }
        snap = {
            "server_id": 7,
            "user_uuid": "11111111-2222-4333-8444-555555555555",
            "username": "demo",
            "usage_limit_gb": 30.0,
            "usage_current_gb": 8.0,
            "expire_date": "2026-12-31T00:00:00Z",
            "status": "active",
            "is_active": 1,
            "comment": "",
            "snapshot": {
                "uuid": "11111111-2222-4333-8444-555555555555",
                "name": "demo",
                "username": "demo",
                "email": "",
                "comment": "",
                "expireDate": "2026-12-31T00:00:00Z",
            },
        }
        state = {
            "inbounds": [
                {"id": "in-1", "enabled": True},
                {"id": "in-2", "enabled": True},
            ],
            "users": [],
            "snapshots": [snap],
            "missing": [snap],
        }
        create = AsyncMock(return_value={"uuid": snap["user_uuid"]})
        sync = AsyncMock(return_value={"ok": True})
        set_base = unittest.mock.Mock()

        with patch.object(
            xnet_guard, "_guard_state", new=AsyncMock(return_value=state)
        ), patch.object(
            xnet_guard, "_frozen_usage_for_uuid", return_value=10.0
        ), patch.object(
            xnet_api, "create_user", new=create
        ), patch.object(
            xnet_api, "sync_users_to_inbounds", new=sync
        ), patch.object(
            userbot_db, "set_xnet_guard_recovery_base", new=set_base
        ):
            result = await xnet_guard._recover_missing(server)

        self.assertEqual(result["restored"], 1)
        self.assertAlmostEqual(result["preserved_gb"], 10.0)
        payload = create.await_args.args[1]
        self.assertAlmostEqual(payload["usage_limit_GB"], 20.0)
        self.assertEqual(payload["uuid"], snap["user_uuid"])
        set_base.assert_called_once_with(7, snap["user_uuid"], 10.0)
        sync.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
