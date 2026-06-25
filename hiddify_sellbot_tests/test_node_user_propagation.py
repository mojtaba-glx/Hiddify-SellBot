"""
Tests for node user propagation and managed-subscription aggregation.

These cover the two pieces that broke multi-node support:
1. ``_auto_propagate_user_to_nodes`` must CREATE a user on each connected node
   sharing the same UUID, but when the user already exists on a node it must
   PATCH instead of failing (idempotent re-sync / previously created users).
2. ``_build_panel_uuid_subscription_body`` (smart link) must aggregate configs
   from the main server *and* all connected nodes, and sum usage across them.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from AdminBot import servers as admin_servers
from Shared import database, sub_aggregator, sub_http_server


MAIN_SERVER = {
    "id": 1,
    "title": "Main",
    "panel_url": "https://main.example.com",
    "admin_proxy_path": "admin",
    "admin_uuid": "MAIN-KEY",
    "user_proxy_path": "sub",
    "domains": [{"id": 1, "title": "user.main.example.com", "domain": "user.main.example.com"}],
    "nodes": [
        {"id": 1, "title": "Node-NL", "host": "node-nl.example.com", "target_server_id": 2},
        {"id": 2, "title": "Node-FI", "host": "node-fi.example.com", "target_server_id": 3},
    ],
}
NODE_NL = {
    "id": 2,
    "title": "Node-NL",
    "panel_url": "https://node-nl.example.com",
    "admin_proxy_path": "admin",
    "admin_uuid": "NL-KEY",
    "user_proxy_path": "sub",
    "domains": [{"id": 1, "title": "user.node-nl.example.com", "domain": "user.node-nl.example.com"}],
    "nodes": [],
}
NODE_FI = {
    "id": 3,
    "title": "Node-FI",
    "panel_url": "https://node-fi.example.com",
    "admin_proxy_path": "admin",
    "admin_uuid": "FI-KEY",
    "user_proxy_path": "sub",
    "domains": [{"id": 1, "title": "user.node-fi.example.com", "domain": "user.node-fi.example.com"}],
    "nodes": [],
}
SERVERS_BY_ID = {1: MAIN_SERVER, 2: NODE_NL, 3: NODE_FI}


class TestAutoPropagateUserToNodes(unittest.IsolatedAsyncioTestCase):
    """Propagation should CREATE on every node using the shared UUID."""

    def setUp(self):
        # Redirect the JSON servers DB to an empty temp file so the module-level
        # functions only see what we monkey-patch below.
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db_path = database.DB_PATH
        database.DB_PATH = str(Path(self._tmpdir.name) / "servers.json")

        self._patches = [
            patch.object(database, "get_server_by_id", side_effect=lambda sid: SERVERS_BY_ID.get(int(sid))),
            patch.object(database, "get_servers", side_effect=lambda: list(SERVERS_BY_ID.values())),
            patch.object(admin_servers.userbot_db, "add_service_node"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        database.DB_PATH = self._orig_db_path
        self._tmpdir.cleanup()

    async def test_creates_user_on_all_nodes_with_shared_uuid(self):
        calls = []

        async def fake_create(server, payload):
            calls.append({"server_id": server["id"], "uuid": payload["uuid"]})
            return {"uuid": payload["uuid"], "id": 900 + server["id"]}

        async def fake_get(server, uuid):
            return None  # never exists -> always create path

        with patch.object(admin_servers.hiddify_api, "create_user", side_effect=fake_create), \
             patch.object(admin_servers.hiddify_api, "get_user_by_uuid", side_effect=fake_get), \
             patch.object(admin_servers.hiddify_api, "patch_user") as fake_patch:
            report = await admin_servers._auto_propagate_user_to_nodes(
                1, user_uuid="shared-uuid-1", user_name="Alice", usage_limit_GB=20, package_days=30,
            )

        created_on = sorted(c["server_id"] for c in calls)
        self.assertEqual(created_on, [2, 3], "should create on both nodes")
        self.assertTrue(all(c["uuid"] == "shared-uuid-1" for c in calls), "UUID must be shared")
        self.assertEqual(fake_patch.call_count, 0, "nothing to patch when user is new")
        self.assertIn("ساخته شد", report)

    async def test_patches_when_user_already_exists_on_node(self):
        """Idempotent: a user that already exists on a node is PATCHed, not an error."""
        calls = {"create": 0, "patch": 0, "get": 0}

        async def fake_get(server, uuid):
            calls["get"] += 1
            return {"uuid": uuid, "name": "Existing", "current_usage_GB": 3.0}

        async def fake_create(server, payload):
            calls["create"] += 1
            raise RuntimeError("HTTP 400: user with this uuid already exists")

        async def fake_patch(server, uuid, payload):
            calls["patch"] += 1
            return {"uuid": uuid, **payload}

        with patch.object(admin_servers.hiddify_api, "get_user_by_uuid", side_effect=fake_get), \
             patch.object(admin_servers.hiddify_api, "create_user", side_effect=fake_create), \
             patch.object(admin_servers.hiddify_api, "patch_user", side_effect=fake_patch):
            report = await admin_servers._auto_propagate_user_to_nodes(
                1, user_uuid="dup-uuid", user_name="Dup", usage_limit_GB=10, package_days=15,
            )

        self.assertEqual(calls["create"], 0, "must not attempt create when user exists")
        self.assertEqual(calls["patch"], 2, "both nodes should be patched")
        self.assertNotIn("❌", report, "existing user must not be reported as error")
        self.assertIn("به‌روز", report)

    async def test_no_nodes_returns_empty(self):
        async def fake_create(server, payload):
            self.fail("create must not be called when there are no nodes")

        with patch.object(admin_servers.hiddify_api, "create_user", side_effect=fake_create):
            report = await admin_servers._auto_propagate_user_to_nodes(
                2, user_uuid="x", user_name="x", usage_limit_GB=1, package_days=1,
            )
        self.assertEqual(report, "")


class TestCreateOrPatchFallback(unittest.IsolatedAsyncioTestCase):
    """Race fallback: if create fails with 'already exists', fall back to PATCH."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db_path = database.DB_PATH
        database.DB_PATH = str(Path(self._tmpdir.name) / "servers.json")
        self._patches = [
            patch.object(database, "get_server_by_id", side_effect=lambda sid: SERVERS_BY_ID.get(int(sid))),
            patch.object(database, "get_servers", side_effect=lambda: list(SERVERS_BY_ID.values())),
            patch.object(admin_servers.userbot_db, "add_service_node"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        database.DB_PATH = self._orig_db_path
        self._tmpdir.cleanup()

    async def test_falls_back_to_patch_on_race(self):
        async def fake_get(server, uuid):
            raise RuntimeError("404 not found")  # probe misses

        async def fake_create(server, payload):
            raise RuntimeError("user with this uuid already exists")  # race

        patched = []

        async def fake_patch(server, uuid, payload):
            patched.append(uuid)
            return {"uuid": uuid, **payload}

        with patch.object(admin_servers.hiddify_api, "get_user_by_uuid", side_effect=fake_get), \
             patch.object(admin_servers.hiddify_api, "create_user", side_effect=fake_create), \
             patch.object(admin_servers.hiddify_api, "patch_user", side_effect=fake_patch):
            report = await admin_servers._auto_propagate_user_to_nodes(
                1, user_uuid="race-uuid", user_name="Race", usage_limit_GB=5, package_days=7,
            )

        self.assertEqual(patched, ["race-uuid", "race-uuid"])
        self.assertNotIn("❌", report)


class TestPanelUuidSubscriptionAggregation(unittest.TestCase):
    """Smart link must merge configs from main + nodes and sum usage."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db_path = database.DB_PATH
        database.DB_PATH = str(Path(self._tmpdir.name) / "servers.json")

        self._patches = [
            patch.object(database, "get_server_by_id", side_effect=lambda sid: SERVERS_BY_ID.get(int(sid))),
            patch.object(database, "get_servers", side_effect=lambda: list(SERVERS_BY_ID.values())),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        database.DB_PATH = self._orig_db_path
        self._tmpdir.cleanup()

    def test_aggregates_configs_from_main_and_nodes(self):
        async def fake_get_user(server, uuid):
            return {
                "uuid": uuid, "name": "TestUser",
                "current_usage_GB": 5.0, "usage_limit_GB": 30, "package_days": 30,
            }

        def fake_fetch_lines(base_url):
            if "main.example" in base_url:
                return ["vless://main@1.1.1.1:443#Main"]
            if "node-nl.example" in base_url:
                return ["vless://nl@2.2.2.2:443#NL"]
            if "node-fi.example" in base_url:
                return ["vless://fi@3.3.3.3:443#FI"]
            return []

        with patch.object(sub_http_server.hiddify_api, "get_user_by_uuid", side_effect=fake_get_user), \
             patch.object(sub_aggregator, "_fetch_subscription_lines", side_effect=fake_fetch_lines), \
             patch.object(sub_aggregator, "_fetch_lines_from_admin_api", return_value=[]):
            body, service = sub_http_server._build_panel_uuid_subscription_body(
                "panel-srv-1", "test-uuid", False,
            )

        lines = (body or "").split("\n")
        # Strip the leading status/info line produced by _build_status_config_line,
        # then count real config lines.
        config_lines = [l for l in lines if "://" in l and "status.hiddify-sellbot.invalid" not in l]
        self.assertEqual(len(config_lines), 3, "main + 2 nodes")
        # usage summed across all three (5.0 * 3)
        self.assertAlmostEqual(service["usage_current"], 15.0)
        self.assertEqual(service["usage_limit"], 30)
        self.assertEqual(service["name"], "TestUser")


if __name__ == "__main__":
    unittest.main()
