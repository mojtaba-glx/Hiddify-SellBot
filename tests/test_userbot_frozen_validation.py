import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from Shared import service_enforcer, userbot_db


class UserBotFrozenValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        service_enforcer._ENFORCER_FETCH_SEMAPHORE = None

    async def test_direct_not_found_uses_list_users_before_marking_missing(self):
        server = {"id": 1, "title": "Germany"}
        node = {"server_id": 1, "panel_user_uuid": "uuid-a"}

        with patch.object(
            service_enforcer.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=RuntimeError("HTTP 404 user not found")),
        ) as direct_mock, patch.object(
            service_enforcer.hiddify_api,
            "list_users",
            new=AsyncMock(
                return_value=[{"uuid": "uuid-a", "current_usage_GB": 2.5}]
            ),
        ) as list_mock:
            result = await service_enforcer._fetch_service_node_usage(
                service_id=1,
                node=node,
                servers_map={1: server},
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["not_found"])
        self.assertEqual(result["panel_user"]["uuid"], "uuid-a")
        direct_mock.assert_awaited_once_with(server, "uuid-a")
        list_mock.assert_awaited_once_with(server)

    async def test_list_failure_does_not_confirm_user_missing(self):
        server = {"id": 1, "title": "Germany"}
        node = {"server_id": 1, "panel_user_uuid": "uuid-a"}

        with patch.object(
            service_enforcer.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=RuntimeError("HTTP 404 user not found")),
        ), patch.object(
            service_enforcer.hiddify_api,
            "list_users",
            new=AsyncMock(side_effect=TimeoutError("panel offline")),
        ):
            result = await service_enforcer._fetch_service_node_usage(
                service_id=1,
                node=node,
                servers_map={1: server},
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["not_found"])


class UserBotFrozenReportDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = userbot_db.DB_PATH
        userbot_db.DB_PATH = Path(self.tmp.name) / "userbot.db"
        userbot_db.init_db()

        conn = userbot_db._get_conn()
        try:
            conn.execute(
                "INSERT INTO userbot_users "
                "(id, telegram_id, username, full_name, created_at) "
                "VALUES (1, 1001, 'tester', 'Tester', '')"
            )
            conn.execute(
                "INSERT INTO userbot_services "
                "(id, user_id, name, server_id, server_title, usage_current, usage_limit, days_left, last_online, comment) "
                "VALUES (1, 1, 'Test', 1, 'Germany', 0, 10, 30, '', 'uuid:uuid-a')"
            )
            conn.execute(
                """
                INSERT INTO userbot_service_nodes (
                    service_id, server_id, server_title, panel_user_uuid,
                    is_active, usage_current, frozen, fail_count,
                    frozen_at, frozen_reason, deleted, created_at, updated_at
                ) VALUES (1, 1, 'Germany', 'uuid-a',
                          0, 0, 1, 5, '2026-09-21 02:00:00',
                          'user_not_found', 0, '', '')
                """
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        userbot_db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_zero_usage_frozen_row_is_not_reported(self):
        self.assertEqual(userbot_db.get_frozen_nodes_report(), [])
        summary = userbot_db.get_frozen_nodes_summary()
        self.assertEqual(summary["frozen_nodes"], 0)

    def test_clear_frozen_snapshot_removes_held_usage_only(self):
        conn = userbot_db._get_conn()
        try:
            conn.execute(
                "UPDATE userbot_service_nodes SET usage_current = 0.012 WHERE service_id = 1"
            )
            conn.execute(
                "UPDATE userbot_services SET usage_current = 0.020 WHERE id = 1"
            )
            conn.commit()
        finally:
            conn.close()

        removed = userbot_db.clear_frozen_service_nodes(1)
        self.assertEqual(removed, 1)
        self.assertEqual(userbot_db.get_frozen_nodes_report(), [])
        self.assertEqual(userbot_db.get_service_nodes(1), [])
        service = userbot_db.get_service_by_id(1)
        self.assertAlmostEqual(float(service["usage_current"]), 0.008)

    def test_manual_thaw_can_restore_deleted_and_active_flags(self):
        conn = userbot_db._get_conn()
        try:
            conn.execute(
                """
                UPDATE userbot_service_nodes
                SET usage_current = 0.012, frozen = 1, fail_count = 4,
                    deleted = 1, is_active = 0,
                    frozen_at = '2026-09-21 02:00:00',
                    frozen_reason = 'server_deleted'
                WHERE service_id = 1
                """
            )
            conn.commit()
        finally:
            conn.close()

        userbot_db.update_service_node_runtime(
            1,
            1,
            "uuid-a",
            usage_current=0.020,
            frozen=0,
            fail_count=0,
            frozen_at="",
            frozen_reason="",
            deleted=0,
            is_active=1,
        )

        node = userbot_db.get_service_nodes(1)[0]
        self.assertAlmostEqual(float(node["usage_current"]), 0.020)
        self.assertEqual(int(node["frozen"]), 0)
        self.assertEqual(int(node["fail_count"]), 0)
        self.assertEqual(int(node["deleted"]), 0)
        self.assertEqual(int(node["is_active"]), 1)
        self.assertEqual(str(node["frozen_reason"] or ""), "")

    def test_positive_snapshot_is_reported(self):
        conn = userbot_db._get_conn()
        try:
            conn.execute(
                "UPDATE userbot_service_nodes SET usage_current = 0.005 WHERE service_id = 1"
            )
            conn.commit()
        finally:
            conn.close()

        rows = userbot_db.get_frozen_nodes_report()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(float(rows[0]["usage_current"]), 0.005)


if __name__ == "__main__":
    unittest.main()
