import asyncio
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

    def test_rebind_legacy_node_uuid_replaces_wrong_mapping(self):
        conn = userbot_db._get_conn()
        try:
            conn.execute(
                """
                UPDATE userbot_service_nodes
                SET server_id = 2,
                    server_title = 'Sanaei',
                    panel_user_uuid = 'wrong-uuid',
                    usage_current = 7.5,
                    frozen = 1,
                    fail_count = 3,
                    deleted = 0,
                    is_active = 0
                WHERE service_id = 1
                """
            )
            conn.commit()
        finally:
            conn.close()

        ok = userbot_db.rebind_service_node_uuid(
            1,
            2,
            "wrong-uuid",
            "real-sanaei-uuid",
            server_title="Sanaei",
        )
        self.assertTrue(ok)
        rows = userbot_db.get_service_nodes(1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["panel_user_uuid"], "real-sanaei-uuid")
        self.assertEqual(int(rows[0]["is_active"]), 1)
        self.assertEqual(int(rows[0]["frozen"]), 0)
        self.assertEqual(int(rows[0]["fail_count"]), 0)

    def test_legacy_name_recovery_requires_one_exact_match(self):
        users = [
            {"uuid": "a", "name": "Kyc", "email": "Kyc"},
            {"uuid": "b", "name": "Other", "email": "other"},
        ]
        matches = service_enforcer._exact_named_panel_candidates(users, "Kyc")
        self.assertEqual([row["uuid"] for row in matches], ["a"])

        ambiguous = users + [{"uuid": "c", "name": "kyc", "email": "kyc"}]
        self.assertEqual(
            service_enforcer._exact_named_panel_candidates(ambiguous, "Kyc"),
            [users[0], ambiguous[-1]],
        )

    def test_partial_mapping_heals_configured_xui_child(self):
        primary = {
            "id": 1,
            "title": "Germany",
            "nodes": [{"target_server_id": 2}],
        }
        child = {"id": 2, "title": "Sanaei", "nodes": []}
        servers = {1: primary, 2: child}
        svc = userbot_db.get_service_by_id(1)

        with patch.object(
            service_enforcer.database,
            "get_server_by_id",
            side_effect=lambda sid: servers.get(int(sid)),
        ), patch.object(
            service_enforcer.database,
            "get_servers",
            return_value=[primary, child],
        ):
            mappings = service_enforcer._get_or_create_mappings_for_service(svc)

        self.assertEqual(
            {int(row["server_id"]) for row in mappings},
            {1, 2},
        )
        child_row = next(row for row in mappings if int(row["server_id"]) == 2)
        self.assertEqual(child_row["panel_user_uuid"], "uuid-a")


class ForceEnforcerClusterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = userbot_db.DB_PATH
        userbot_db.DB_PATH = Path(self.tmp.name) / "force-enforcer.db"
        userbot_db.init_db()
        service_enforcer._ENFORCER_FETCH_SEMAPHORE = None

        conn = userbot_db._get_conn()
        try:
            conn.execute(
                """
                INSERT INTO userbot_services
                (id, user_id, name, server_id, server_title,
                 usage_current, usage_limit, days_left, last_online, comment)
                VALUES (77, 0, 'Kyc', 1, 'Germany',
                        0, 100, 21, '', 'uuid:shared-uuid|admin:1')
                """
            )
            for sid, title in ((1, "Germany"), (2, "Turkey"), (3, "France")):
                conn.execute(
                    """
                    INSERT INTO userbot_service_nodes
                    (service_id, server_id, server_title, panel_user_uuid,
                     is_active, usage_current, frozen, fail_count, deleted,
                     created_at, updated_at)
                    VALUES (77, ?, ?, 'shared-uuid',
                            1, 0, 0, 0, 0, '', '')
                    """,
                    (sid, title),
                )
            conn.commit()
        finally:
            conn.close()

        self.servers = {
            1: {"id": 1, "title": "Germany", "nodes": [{"target_server_id": 2}, {"target_server_id": 3}]},
            2: {"id": 2, "title": "Turkey", "nodes": []},
            3: {"id": 3, "title": "France", "nodes": []},
        }

    def tearDown(self):
        userbot_db.DB_PATH = self.old_path
        service_enforcer._ENFORCER_FETCH_SEMAPHORE = None
        service_enforcer._enforcer_running = False
        self.tmp.cleanup()

    def _panel_user(self, sid: int, usage: float) -> dict:
        return {
            "uuid": "shared-uuid",
            "name": "Kyc",
            "current_usage_GB": usage,
            "usage_limit_GB": 100,
            "days_left": 21,
            "is_active": True,
        }

    def test_force_scan_reenables_only_bot_locked_nodes_when_live_total_is_below_limit(self):
        conn = userbot_db._get_conn()
        try:
            conn.execute(
                "UPDATE userbot_service_nodes SET is_active = 0 WHERE service_id = 77"
            )
            conn.execute(
                "UPDATE userbot_services SET usage_current = 100 WHERE id = 77"
            )
            conn.commit()
        finally:
            conn.close()

        usage_by_sid = {1: 76.38, 2: 22.83, 3: 0.42}

        async def get_user(server, uuid):
            return self._panel_user(int(server["id"]), usage_by_sid[int(server["id"])])

        enable = AsyncMock(return_value={"is_active": True})
        disable = AsyncMock()

        with patch.object(
            service_enforcer.database,
            "get_servers",
            return_value=list(self.servers.values()),
        ), patch.object(
            service_enforcer.database,
            "get_server_by_id",
            side_effect=lambda sid: self.servers.get(int(sid)),
        ), patch.object(
            service_enforcer.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(
            service_enforcer.hiddify_api,
            "enable_user",
            new=enable,
        ), patch.object(
            service_enforcer.hiddify_api,
            "disable_user",
            new=disable,
        ):
            summary = asyncio.run(
                service_enforcer._run_global_usage_enforcer_impl(scan_all=True)
            )

        self.assertAlmostEqual(
            float(userbot_db.get_service_by_id(77)["usage_current"]),
            99.63,
            places=2,
        )
        self.assertEqual(summary["services_disabled"], 0)
        self.assertEqual(summary["services_reenabled"], 1)
        self.assertEqual(summary["nodes_reenabled"], 3)
        self.assertEqual(summary["nodes_reenable_failed"], 0)
        self.assertEqual(enable.await_count, 3)
        self.assertEqual(disable.await_count, 0)
        self.assertTrue(
            all(int(row["is_active"]) == 1 for row in userbot_db.get_service_nodes(77))
        )

    def test_force_scan_disables_all_cluster_nodes_when_live_total_reaches_limit(self):
        usage_by_sid = {1: 76.38, 2: 22.83, 3: 0.79}

        async def get_user(server, uuid):
            return self._panel_user(int(server["id"]), usage_by_sid[int(server["id"])])

        enable = AsyncMock()
        disable = AsyncMock(return_value={"is_active": False})

        with patch.object(
            service_enforcer.database,
            "get_servers",
            return_value=list(self.servers.values()),
        ), patch.object(
            service_enforcer.database,
            "get_server_by_id",
            side_effect=lambda sid: self.servers.get(int(sid)),
        ), patch.object(
            service_enforcer.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(
            service_enforcer.hiddify_api,
            "enable_user",
            new=enable,
        ), patch.object(
            service_enforcer.hiddify_api,
            "disable_user",
            new=disable,
        ):
            summary = asyncio.run(
                service_enforcer._run_global_usage_enforcer_impl(scan_all=True)
            )

        self.assertAlmostEqual(
            float(userbot_db.get_service_by_id(77)["usage_current"]),
            100.0,
            places=2,
        )
        self.assertEqual(summary["services_disabled"], 1)
        self.assertEqual(summary["nodes_disabled"], 3)
        self.assertEqual(summary["nodes_disable_failed"], 0)
        self.assertEqual(disable.await_count, 3)
        self.assertEqual(enable.await_count, 0)
        self.assertTrue(
            all(int(row["is_active"]) == 0 for row in userbot_db.get_service_nodes(77))
        )


if __name__ == "__main__":
    unittest.main()
