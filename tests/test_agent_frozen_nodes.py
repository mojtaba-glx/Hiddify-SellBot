import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from Shared import agent_db, agent_enforcer


class AgentFrozenNodeAccountingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = agent_db.DB_PATH
        self.old_initialized = agent_db._db_initialized
        self.old_init_path = agent_db._init_db_path
        agent_db.DB_PATH = Path(self.tmp.name) / "agency.db"
        agent_db._db_initialized = False
        agent_db._init_db_path = ""
        agent_db.init_db()

        conn = agent_db._get_conn()
        try:
            conn.execute(
                "INSERT INTO agent_users (id, telegram_id, username, full_name, is_active, created_at, updated_at) "
                "VALUES (1, 1001, 'agent', 'Agent', 1, '', '')"
            )
            conn.execute(
                """
                INSERT INTO agent_services (
                    id, agent_id, customer_id, server_id, server_title, name,
                    panel_user_uuid, usage_current, usage_limit, days_left,
                    start_date, end_date, is_active, created_at, updated_at
                ) VALUES (1, 1, NULL, 1, 'Germany', 'Test', 'uuid-a',
                          0, 20, 30, '', '2099-01-01 00:00:00', 1, '', '')
                """
            )
            conn.commit()
        finally:
            conn.close()

        agent_db.add_service_node(
            1, 1, server_title="Germany", panel_user_uuid="uuid-a"
        )
        agent_db.add_service_node(
            1, 2, server_title="Turkey", panel_user_uuid="uuid-a"
        )

    def tearDown(self):
        agent_db.DB_PATH = self.old_path
        agent_db._db_initialized = self.old_initialized
        agent_db._init_db_path = self.old_init_path
        self.tmp.cleanup()

    async def test_outage_keeps_last_node_usage_and_freezes_after_threshold(self):
        agent_db.update_service_node_runtime(
            1, 2, "uuid-a",
            usage_current=7.0,
            fail_count=2,
            last_ok_at="2026-09-20 12:00:00",
        )
        svc = agent_db.get_service_by_id(1)

        async def get_user(server, _uuid):
            if int(server["id"]) == 1:
                return {"uuid": "uuid-a", "current_usage_GB": 3.0}
            raise TimeoutError("node offline")

        with patch.object(
            agent_enforcer.database,
            "get_server_by_id",
            side_effect=lambda sid: {"id": sid, "title": "Germany" if sid == 1 else "Turkey"},
        ), patch.object(
            agent_enforcer.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ):
            result = await agent_enforcer._process_service(svc)

        self.assertEqual(result["status"], "synced")
        updated = agent_db.get_service_by_id(1)
        self.assertAlmostEqual(float(updated["usage_current"]), 10.0)

        nodes = {int(n["server_id"]): n for n in agent_db.get_service_nodes(1)}
        self.assertAlmostEqual(float(nodes[2]["usage_current"]), 7.0)
        self.assertEqual(int(nodes[2]["fail_count"]), 3)
        self.assertEqual(int(nodes[2]["frozen"]), 1)
        self.assertEqual(nodes[2]["frozen_reason"], "network_error")
        self.assertTrue(str(nodes[2]["frozen_at"] or ""))

    async def test_recovered_node_unfreezes_and_refreshes_snapshot(self):
        agent_db.update_service_node_runtime(
            1, 2, "uuid-a",
            usage_current=7.0,
            frozen=1,
            fail_count=4,
            frozen_at="2026-09-20 12:00:00",
            frozen_reason="network_error",
        )
        svc = agent_db.get_service_by_id(1)

        async def get_user(server, _uuid):
            usage = 3.0 if int(server["id"]) == 1 else 8.0
            return {"uuid": "uuid-a", "current_usage_GB": usage}

        with patch.object(
            agent_enforcer.database,
            "get_server_by_id",
            side_effect=lambda sid: {"id": sid, "title": str(sid)},
        ), patch.object(
            agent_enforcer.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ):
            await agent_enforcer._process_service(svc)

        nodes = {int(n["server_id"]): n for n in agent_db.get_service_nodes(1)}
        self.assertEqual(int(nodes[2]["frozen"]), 0)
        self.assertEqual(int(nodes[2]["fail_count"]), 0)
        self.assertEqual(str(nodes[2]["frozen_reason"] or ""), "")
        self.assertEqual(str(nodes[2]["frozen_at"] or ""), "")
        self.assertAlmostEqual(float(nodes[2]["usage_current"]), 8.0)
        self.assertAlmostEqual(float(agent_db.get_service_by_id(1)["usage_current"]), 11.0)

    async def test_all_nodes_offline_never_drops_existing_total_usage(self):
        agent_db.update_service(1, {"usage_current": 12.5})
        svc = agent_db.get_service_by_id(1)

        with patch.object(
            agent_enforcer.database,
            "get_server_by_id",
            side_effect=lambda sid: {"id": sid, "title": str(sid)},
        ), patch.object(
            agent_enforcer.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=TimeoutError("all offline")),
        ):
            await agent_enforcer._process_service(svc)

        self.assertAlmostEqual(float(agent_db.get_service_by_id(1)["usage_current"]), 12.5)

    async def test_renewal_resets_frozen_runtime_snapshot(self):
        agent_db.update_service_node_runtime(
            1, 2, "uuid-a",
            usage_current=7.0,
            frozen=1,
            fail_count=3,
            frozen_at="2026-09-20 12:00:00",
            frozen_reason="network_error",
        )

        ok = agent_db.renew_service_with_policy(
            1, 30, 10, "add", "add", "renew:test"
        )
        self.assertTrue(ok)
        # Runtime reset is intentionally deferred until the panel renewal is
        # confirmed by the caller.
        agent_db.reset_service_nodes_on_renew(1)

        nodes = {int(n["server_id"]): n for n in agent_db.get_service_nodes(1)}
        self.assertAlmostEqual(float(nodes[2]["usage_current"]), 0.0)
        self.assertEqual(int(nodes[2]["frozen"]), 0)
        self.assertEqual(int(nodes[2]["fail_count"]), 0)
        self.assertEqual(str(nodes[2]["frozen_at"] or ""), "")
        self.assertEqual(str(nodes[2]["frozen_reason"] or ""), "")
        self.assertEqual(int(nodes[2]["is_active"]), 1)

    async def test_deleted_server_holds_usage_until_renewal(self):
        agent_db.update_service_node_runtime(
            1, 2, "uuid-a", usage_current=6.25, last_ok_at="2026-09-20 12:00:00"
        )
        held = agent_db.hold_deleted_server_nodes(2)
        self.assertEqual(held, [1])

        node = next(n for n in agent_db.get_service_nodes(1) if int(n["server_id"]) == 2)
        self.assertEqual(int(node["deleted"]), 1)
        self.assertEqual(int(node["frozen"]), 1)
        self.assertAlmostEqual(float(node["usage_current"]), 6.25)
        self.assertEqual(node["frozen_reason"], "server_deleted")

        ok = agent_db.renew_service_with_policy(
            1, 30, 10, "add", "add", "renew:deleted"
        )
        self.assertTrue(ok)
        agent_db.reset_service_nodes_on_renew(1)
        self.assertFalse(any(int(n["server_id"]) == 2 for n in agent_db.get_service_nodes(1)))


if __name__ == "__main__":
    unittest.main()
