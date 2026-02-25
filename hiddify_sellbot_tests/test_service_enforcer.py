import unittest

from Shared import service_enforcer


class TestServiceEnforcer(unittest.IsolatedAsyncioTestCase):
    async def test_disable_when_total_usage_reaches_limit(self):
        calls = {
            "update_runtime": [],
            "disable_calls": [],
            "set_active": [],
        }

        async def fake_get_user_by_uuid(server, user_uuid):
            if user_uuid == "u1":
                return {"current_usage_GB": 6, "start_date": "2026-02-01", "package_days": 30}
            return {"current_usage_GB": 5, "start_date": "2026-02-01", "package_days": 30}

        async def fake_patch_user(server, user_uuid, payload):
            calls["disable_calls"].append((server["id"], user_uuid, payload))
            return {}

        orig_get_services = service_enforcer.userbot_db.get_services_for_enforcement
        orig_get_mappings = service_enforcer.userbot_db.get_service_nodes
        orig_add_mapping = service_enforcer.userbot_db.add_service_node
        orig_update_runtime = service_enforcer.userbot_db.update_service_runtime
        orig_set_active = service_enforcer.userbot_db.set_service_nodes_active
        orig_get_server = service_enforcer.database.get_server_by_id
        orig_get_user = service_enforcer.hiddify_api.get_user_by_uuid
        orig_patch_user = service_enforcer.hiddify_api.patch_user

        try:
            service_enforcer.userbot_db.get_services_for_enforcement = lambda: [
                {"id": 1, "usage_limit": 10, "server_id": 1, "comment": "uuid:u1"}
            ]
            service_enforcer.userbot_db.get_service_nodes = lambda service_id: [
                {"service_id": 1, "server_id": 1, "panel_user_uuid": "u1"},
                {"service_id": 1, "server_id": 2, "panel_user_uuid": "u2"},
            ]
            service_enforcer.userbot_db.add_service_node = lambda **kwargs: None
            service_enforcer.userbot_db.update_service_runtime = (
                lambda **kwargs: calls["update_runtime"].append(kwargs)
            )
            service_enforcer.userbot_db.set_service_nodes_active = (
                lambda service_id, is_active: calls["set_active"].append((service_id, is_active))
            )
            service_enforcer.database.get_server_by_id = lambda sid: {"id": sid} if sid in {1, 2} else None
            service_enforcer.hiddify_api.get_user_by_uuid = fake_get_user_by_uuid
            service_enforcer.hiddify_api.patch_user = fake_patch_user

            summary = await service_enforcer.run_global_usage_enforcer()
        finally:
            service_enforcer.userbot_db.get_services_for_enforcement = orig_get_services
            service_enforcer.userbot_db.get_service_nodes = orig_get_mappings
            service_enforcer.userbot_db.add_service_node = orig_add_mapping
            service_enforcer.userbot_db.update_service_runtime = orig_update_runtime
            service_enforcer.userbot_db.set_service_nodes_active = orig_set_active
            service_enforcer.database.get_server_by_id = orig_get_server
            service_enforcer.hiddify_api.get_user_by_uuid = orig_get_user
            service_enforcer.hiddify_api.patch_user = orig_patch_user

        self.assertEqual(summary["services_scanned"], 1)
        self.assertEqual(summary["services_synced"], 1)
        self.assertEqual(summary["services_disabled"], 1)
        self.assertEqual(summary["nodes_disabled"], 2)
        self.assertEqual(summary["errors"], 0)

        self.assertTrue(calls["update_runtime"])
        self.assertAlmostEqual(calls["update_runtime"][0]["usage_current"], 11.0, places=3)
        self.assertEqual(len(calls["disable_calls"]), 2)
        self.assertEqual(calls["set_active"], [(1, 0)])


if __name__ == "__main__":
    unittest.main()
