import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from AdminBot import servers
from Shared import userbot_db


class AdminManualRenewRuntimeSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = userbot_db.DB_PATH
        userbot_db.DB_PATH = Path(self.tmp.name) / "manual-renew.db"
        userbot_db.init_db()

        conn = userbot_db._get_conn()
        try:
            conn.execute(
                "INSERT INTO userbot_users "
                "(id, telegram_id, username, full_name, created_at) "
                "VALUES (1, 1001, 'tester', 'Tester', '')"
            )
            conn.execute(
                """
                INSERT INTO userbot_services
                (id, user_id, name, server_id, server_title,
                 usage_current, usage_limit, days_left, last_online, comment, expired_at)
                VALUES
                (1, 1, 'mohammadhosein', 1, 'Germany',
                 10, 10, -1, '', 'uuid:uuid-a', '2026-10-02 20:00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO userbot_service_nodes
                (service_id, server_id, server_title, panel_user_uuid,
                 is_active, created_at, updated_at)
                VALUES (1, 1, 'Germany', 'uuid-a', 0, '', '')
                """
            )
            conn.commit()
        finally:
            conn.close()

        self.context = SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock())
        )

    async def asyncTearDown(self):
        userbot_db.DB_PATH = self.old_path
        self.tmp.cleanup()

    async def test_admin_dynamic_renew_clears_local_expiry_before_reenable(self):
        server = {"id": 1, "title": "Germany"}

        with patch.object(
            servers.database,
            "get_server_by_id",
            return_value=server,
        ), patch.object(
            servers,
            "_resolve_panel_user_uuid",
            new=AsyncMock(return_value="uuid-a"),
        ), patch.object(
            servers,
            "_patch_user_on_related_servers",
            new=AsyncMock(return_value=("uuid-a", 1, 1, [])),
        ) as patch_cluster, patch.object(
            servers,
            "_set_user_active_state_on_related_servers",
            new=AsyncMock(return_value=("uuid-a", 1, 1, [])),
        ) as enable_cluster, patch.object(
            servers,
            "send_user_detail",
            new=AsyncMock(),
        ):
            await servers._apply_dynamic_extend(
                1,
                "uuid-a",
                10,
                1,
                999,
                self.context,
            )

        patch_cluster.assert_awaited_once()
        enable_cluster.assert_awaited_once()

        svc = userbot_db.get_service_by_id(1)
        self.assertEqual(int(svc["days_left"]), 30)
        self.assertAlmostEqual(float(svc["usage_current"]), 0.0)
        self.assertAlmostEqual(float(svc["usage_limit"]), 10.0)
        self.assertEqual(str(svc["expired_at"] or ""), "")
        self.assertEqual(userbot_db.get_expired_services(0), [])

        nodes = userbot_db.get_service_nodes(1)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(int(nodes[0]["is_active"]), 1)
        self.assertEqual(int(nodes[0]["frozen"] or 0), 0)
        self.assertEqual(int(nodes[0]["fail_count"] or 0), 0)


if __name__ == "__main__":
    unittest.main()
