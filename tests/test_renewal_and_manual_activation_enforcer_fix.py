import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from AdminBot import servers
from Shared import agent_db, userbot_db, agent_enforcer, database


class RenewalAndManualActivationEnforcerFixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_userbot_path = userbot_db.DB_PATH
        userbot_db.DB_PATH = Path(self.tmp.name) / "test_userbot.db"
        userbot_db.init_db()

        self.old_agent_path = agent_db.DB_PATH
        self.old_agent_initialized = agent_db._db_initialized
        self.old_agent_init_path = agent_db._init_db_path
        agent_db.DB_PATH = Path(self.tmp.name) / "test_agent.db"
        agent_db._db_initialized = False
        agent_db._init_db_path = ""
        agent_db.init_db()

        agent_id = agent_db.upsert_agent(3001, username="test_agent")
        # Add wallet balance
        agent_db.charge_wallet(agent_id, 1_000_000, description="initial")

        # Create expired agency service
        svc = agent_db.create_service(
            agent_id=agent_id,
            customer_id=0,
            server_id=1,
            server_title="Primary Server",
            name="customer_sub",
            panel_user_uuid="agency-uuid-1",
            usage_limit=20.0,
            days=30,
        )
        self.agent_id = agent_id
        self.service_id = int(svc["id"])
        agent_db.add_service_node(
            self.service_id,
            1,
            server_title="Primary Server",
            panel_user_uuid="agency-uuid-1",
        )
        # Mark it expired & disabled
        agent_db.update_service(
            self.service_id,
            {
                "usage_current": 5.0,
                "days_left": -2,
                "end_date": "2026-10-15 12:00:00",
                "expired_at": "2026-10-15 12:00:00",
                "is_active": 0,
            },
        )
        agent_db.update_service_node_runtime(
            self.service_id,
            1,
            "agency-uuid-1",
            usage_current=5.0,
            days_left=-2,
            frozen=1,
            frozen_reason="expired_disabled_verified",
            is_active=0,
        )

        self.server1 = {
            "id": 1,
            "title": "Primary Server",
            "panel_type": "hiddify",
            "url": "https://server1.example.com",
        }

    async def asyncTearDown(self):
        userbot_db.DB_PATH = self.old_userbot_path
        agent_db.DB_PATH = self.old_agent_path
        agent_db._db_initialized = self.old_agent_initialized
        agent_db._init_db_path = self.old_agent_init_path
        self.tmp.cleanup()

    async def test_manual_admin_activation_syncs_agency_service_and_survives_enforcer_cycles(self):
        """Admin manually enables an agency subscription in AdminBot.

        Central agent_db must be synced with live panel info, and periodic agent_enforcer
        cycles must NOT disable it.
        """
        live_user = {
            "uuid": "agency-uuid-1",
            "name": "customer_sub",
            "is_active": True,
            "current_usage_GB": 1.5,
            "usage_limit_GB": 20.0,
            "days_left": 28,
            "start_date": "2026-10-17",
            "package_days": 30,
        }

        disable_mock = AsyncMock()
        enable_mock = AsyncMock()

        with patch.object(
            servers.database,
            "get_server_by_id",
            return_value=self.server1,
        ), patch.object(
            servers,
            "_get_related_server_targets",
            return_value=[self.server1],
        ), patch.object(
            servers,
            "_resolve_panel_user_uuid",
            new=AsyncMock(return_value="agency-uuid-1"),
        ), patch.object(
            servers.hiddify_api,
            "enable_user",
            new=enable_mock,
        ), patch.object(
            servers,
            "_get_panel_user_with_list_fallback",
            new=AsyncMock(return_value=live_user),
        ):
            resolved_uuid, changed, total, failed = (
                await servers._set_user_active_state_on_related_servers(
                    1, "agency-uuid-1", active=True
                )
            )

        self.assertEqual(resolved_uuid, "agency-uuid-1")
        self.assertEqual(changed, 1)
        self.assertEqual(total, 1)
        self.assertEqual(failed, [])

        # Verify agent_services was synced
        svc = agent_db.get_service_by_id(self.service_id)
        self.assertEqual(int(svc["is_active"]), 1)
        self.assertEqual(int(svc["days_left"]), 28)
        self.assertEqual(str(svc["expired_at"] or ""), "")

        # Verify agent_service_nodes was unfrozen and activated
        nodes = agent_db.get_service_nodes(self.service_id)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(int(nodes[0]["is_active"]), 1)
        self.assertEqual(int(nodes[0]["frozen"] or 0), 0)
        self.assertEqual(str(nodes[0]["frozen_reason"] or ""), "")

        # Now simulate 3 periodic agent_enforcer cycles
        with patch.object(
            database,
            "get_server_by_id",
            return_value=self.server1,
        ), patch.object(
            agent_enforcer,
            "_get_user_with_list_fallback",
            new=AsyncMock(return_value=live_user),
        ), patch.object(
            agent_enforcer.hiddify_api,
            "disable_user",
            new=disable_mock,
        ):
            for _ in range(3):
                summary = await agent_enforcer.run_agent_usage_enforcer(scan_all=True)
                self.assertEqual(summary["services_disabled"], 0)

        # Service must STILL be active
        svc_after = agent_db.get_service_by_id(self.service_id)
        self.assertEqual(int(svc_after["is_active"]), 1)
        disable_mock.assert_not_called()

    async def test_customerbot_renewal_survives_multiple_enforcer_cycles(self):
        """CustomerBot renewal with stale panel expire_date survives enforcer cycles."""
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        live_user_panel = {
            "uuid": "agency-uuid-1",
            "name": "customer_sub",
            "is_active": True,
            "current_usage_GB": 0.05,
            "usage_limit_GB": 20.0,
            "package_days": 30,
            "start_date": today,
            # Stale expire_date from previous cycle
            "expire_date": "2026-10-15",
        }

        # Apply renewal via agent_db.renew_service
        self.assertTrue(agent_db.renew_service(self.service_id, extra_days=30))
        svc = agent_db.get_service_by_id(self.service_id)
        self.assertEqual(int(svc["is_active"]), 1)
        self.assertGreater(int(svc["days_left"]), 0)
        self.assertEqual(str(svc["expired_at"] or ""), "")

        # Unfreeze nodes
        agent_db.reset_service_nodes_on_renew(self.service_id, reset_usage=True, reset_time=True)

        disable_mock = AsyncMock()
        with patch.object(
            database,
            "get_server_by_id",
            return_value=self.server1,
        ), patch.object(
            agent_enforcer,
            "_get_user_with_list_fallback",
            new=AsyncMock(return_value=live_user_panel),
        ), patch.object(
            agent_enforcer.hiddify_api,
            "disable_user",
            new=disable_mock,
        ):
            for _ in range(3):
                summary = await agent_enforcer.run_agent_usage_enforcer(scan_all=True)
                self.assertEqual(summary["services_disabled"], 0)

        svc_after = agent_db.get_service_by_id(self.service_id)
        self.assertEqual(int(svc_after["is_active"]), 1)
        disable_mock.assert_not_called()

    async def test_truly_expired_service_is_correctly_disabled(self):
        """Truly expired service (past end_date and negative days_left) is disabled by enforcer."""
        agent_db.update_service(
            self.service_id,
            {
                "usage_current": 1.0,
                "days_left": -1,
                "end_date": "2026-10-10 12:00:00",
                "is_active": 1,
            },
        )
        live_user_expired = {
            "uuid": "agency-uuid-1",
            "name": "customer_sub",
            "is_active": True,
            "current_usage_GB": 1.0,
            "usage_limit_GB": 20.0,
            "days_left": -1,
            "start_date": "2026-09-01",
            "package_days": 30,
        }

        disable_mock = AsyncMock()
        with patch.object(
            database,
            "get_server_by_id",
            return_value=self.server1,
        ), patch.object(
            agent_enforcer,
            "_get_user_with_list_fallback",
            new=AsyncMock(return_value=live_user_expired),
        ), patch.object(
            agent_enforcer.hiddify_api,
            "disable_user",
            new=disable_mock,
        ):
            summary = await agent_enforcer.run_agent_usage_enforcer(scan_all=True)
            self.assertEqual(summary["services_disabled"], 1)

        svc_after = agent_db.get_service_by_id(self.service_id)
        self.assertEqual(int(svc_after["is_active"]), 0)
        disable_mock.assert_called_once()

    def test_compute_package_info_handles_unstarted_package_days(self):
        """When start_date is None but package_days is set, days_left is package_days."""
        user = {
            "uuid": "uuid-test",
            "start_date": None,
            "package_days": 30,
        }
        start_dt, end_dt, days_left = servers._compute_package_info(user)
        self.assertIsNone(start_dt)
        self.assertIsNone(end_dt)
        self.assertEqual(days_left, 30)




    async def test_agency_renewal_across_cluster_nodes_with_different_uuids(self):
        """When renewing a service across a cluster, secondary node UUIDs are used correctly."""
        from AgentBot.services import subscription_service

        server2 = {
            "id": 2,
            "title": "Child Node X-UI",
            "panel_type": "x-ui",
            "url": "https://server2.example.com",
        }

        agent_db.add_service_node(
            self.service_id,
            2,
            server_title="Child Node X-UI",
            panel_user_uuid="child-xui-uuid-99",
        )

        patch_calls = []

        async def fake_patch_user(server, uuid, patch_data, marzban_username=None):
            patch_calls.append((server["id"], uuid, patch_data))
            return {"ok": True}

        enable_calls = []

        async def fake_enable_user(server, uuid, marzban_username=None):
            enable_calls.append((server["id"], uuid))
            return {"ok": True}

        with patch.object(
            subscription_service,
            "get_server_by_id",
            side_effect=lambda sid: self.server1 if sid == 1 else server2,
        ), patch.object(
            subscription_service.database,
            "get_server_by_id",
            side_effect=lambda sid: self.server1 if sid == 1 else server2,
        ), patch.object(
            subscription_service.multi_panel,
            "patch_user",
            new=AsyncMock(side_effect=fake_patch_user),
        ), patch.object(
            subscription_service.multi_panel,
            "enable_user",
            new=AsyncMock(side_effect=fake_enable_user),
        ), patch.object(
            subscription_service.agent_db,
            "is_wholesale_pricing_configured",
            return_value=True,
        ), patch.object(
            subscription_service.agent_db,
            "calculate_wholesale_price",
            return_value=10_000,
        ):
            updated = await subscription_service.renew_subscription(
                agent_id=self.agent_id,
                service_id=self.service_id,
                extra_days=30,
                extra_gb=15.0,
            )

        self.assertIsNotNone(updated)
        # Verify both targets were patched with their specific UUID
        self.assertEqual(len(patch_calls), 2)
        target_uuids_patched = {call[1] for call in patch_calls}
        self.assertIn("agency-uuid-1", target_uuids_patched)
        self.assertIn("child-xui-uuid-99", target_uuids_patched)

        # Verify expire_date is present in patch payload
        for _, _, pdata in patch_calls:
            self.assertIn("expire_date", pdata)

        # Verify both targets were enabled with their specific UUID
        target_uuids_enabled = {call[1] for call in enable_calls}
        self.assertIn("agency-uuid-1", target_uuids_enabled)
        self.assertIn("child-xui-uuid-99", target_uuids_enabled)


if __name__ == "__main__":
    unittest.main()