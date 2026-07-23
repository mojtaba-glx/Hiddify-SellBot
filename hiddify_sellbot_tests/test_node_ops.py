import os
import unittest

from Shared import node_ops


class TestNodeOps(unittest.IsolatedAsyncioTestCase):
    def test_slugify(self):
        self.assertEqual(node_ops._slugify("Node Germany 1"), "node-germany-1")
        self.assertEqual(node_ops._slugify("  $$$  "), "node")

    def test_cloud_init_requires_template(self):
        old = os.environ.get("NODE_INSTALL_COMMAND_TEMPLATE")
        if "NODE_INSTALL_COMMAND_TEMPLATE" in os.environ:
            del os.environ["NODE_INSTALL_COMMAND_TEMPLATE"]
        try:
            with self.assertRaises(node_ops.NodeOpsError):
                node_ops._build_cloud_init("n1.example.com", "abc")
        finally:
            if old is not None:
                os.environ["NODE_INSTALL_COMMAND_TEMPLATE"] = old

    async def test_monitor_marks_down_and_recovers(self):
        calls = {"reboot": 0}

        async def fake_list_users(server):
            raise RuntimeError("down")

        async def fake_reboot(provider_server_id):
            calls["reboot"] += 1

        orig_servers = node_ops.database.get_servers
        orig_get_server = node_ops.database.get_server_by_id
        orig_update_server = node_ops.database.update_server
        orig_list_users = node_ops.hiddify_api.list_users
        orig_reboot = node_ops._hetzner_reboot

        old_auto = os.environ.get("NODE_AUTO_RECOVER_ENABLED")
        old_thr = os.environ.get("NODE_RECOVER_FAIL_THRESHOLD")
        os.environ["NODE_AUTO_RECOVER_ENABLED"] = "1"
        os.environ["NODE_RECOVER_FAIL_THRESHOLD"] = "1"

        updated_payloads = []
        try:
            node_ops.database.get_servers = lambda: [
                {
                    "id": 1,
                    "nodes": [
                        {
                            "id": 1,
                            "title": "n1",
                            "target_server_id": 2,
                            "provider": "hetzner",
                            "provider_server_id": "123",
                            "fail_count": 0,
                        }
                    ],
                }
            ]
            node_ops.database.get_server_by_id = lambda sid: {"id": sid, "infra": {"provider": "hetzner", "provider_server_id": "123"}} if sid == 2 else None
            node_ops.database.update_server = lambda sid, payload: updated_payloads.append((sid, payload))
            node_ops.hiddify_api.list_users = fake_list_users
            node_ops._hetzner_reboot = fake_reboot

            summary = await node_ops.monitor_and_recover_nodes()
        finally:
            node_ops.database.get_servers = orig_servers
            node_ops.database.get_server_by_id = orig_get_server
            node_ops.database.update_server = orig_update_server
            node_ops.hiddify_api.list_users = orig_list_users
            node_ops._hetzner_reboot = orig_reboot

            if old_auto is not None:
                os.environ["NODE_AUTO_RECOVER_ENABLED"] = old_auto
            elif "NODE_AUTO_RECOVER_ENABLED" in os.environ:
                del os.environ["NODE_AUTO_RECOVER_ENABLED"]
            if old_thr is not None:
                os.environ["NODE_RECOVER_FAIL_THRESHOLD"] = old_thr
            elif "NODE_RECOVER_FAIL_THRESHOLD" in os.environ:
                del os.environ["NODE_RECOVER_FAIL_THRESHOLD"]

        self.assertEqual(summary["nodes_scanned"], 1)
        self.assertEqual(summary["nodes_down"], 1)
        self.assertEqual(summary["recoveries"], 1)
        self.assertEqual(calls["reboot"], 1)
        self.assertTrue(updated_payloads)


if __name__ == "__main__":
    unittest.main()
