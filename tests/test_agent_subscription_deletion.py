import unittest
from unittest.mock import AsyncMock, call, patch

from AgentBot.services import subscription_service
from Shared import sub_links


class AgentSubscriptionDeletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = {
            "id": 71,
            "agent_id": 9,
            "server_id": 10,
            "panel_user_uuid": "shared-user-uuid",
        }
        self.primary = {"id": 10, "title": "Hiddify primary", "panel_type": "hiddify"}
        self.sanaei = {"id": 12, "title": "Sanaei node", "panel_type": "xui"}
        self.targets = [
            (self.primary, "shared-user-uuid", ""),
            (self.sanaei, "xui-client-uuid", ""),
        ]

    async def test_deletes_primary_and_sanaei_node_using_resolved_uuid(self):
        delete_remote = AsyncMock(return_value=None)
        with patch.object(
            subscription_service.agent_db, "get_service_by_id", return_value=self.service
        ), patch.object(
            subscription_service, "get_service_panel_targets", return_value=self.targets
        ) as resolve, patch.object(
            subscription_service.multi_panel, "delete_user", new=delete_remote
        ), patch.object(
            subscription_service.agent_db, "delete_service_node", return_value=True
        ) as delete_mapping, patch.object(
            subscription_service.agent_db, "delete_service", return_value=True
        ) as delete_local:
            result = await subscription_service.delete_subscription(9, 71)

        self.assertTrue(result)
        resolve.assert_called_once_with(self.service)
        self.assertEqual(
            delete_remote.await_args_list,
            [
                call(self.primary, "shared-user-uuid", marzban_username=""),
                call(self.sanaei, "xui-client-uuid", marzban_username=""),
            ],
        )
        self.assertEqual(
            delete_mapping.call_args_list,
            [call(71, 10, "shared-user-uuid"), call(71, 12, "xui-client-uuid")],
        )
        delete_local.assert_called_once_with(71)

    async def test_legacy_service_without_sanaei_mapping_still_deletes_child_node(self):
        primary = dict(self.primary)
        primary["nodes"] = [{"target_server_id": 12}]
        servers = {10: primary, 12: self.sanaei}
        saved_mappings = [
            {
                "service_id": 71,
                "server_id": 10,
                "panel_user_uuid": "shared-user-uuid",
                "marzban_username": "",
            }
        ]
        delete_remote = AsyncMock(return_value=None)

        with patch.object(
            subscription_service.agent_db, "get_service_by_id", return_value=self.service
        ), patch.object(
            sub_links.agent_db, "get_service_nodes", return_value=saved_mappings
        ), patch.object(
            sub_links.database,
            "get_server_by_id",
            side_effect=lambda server_id: servers.get(server_id),
        ), patch.object(
            sub_links.database, "get_servers", return_value=list(servers.values())
        ), patch.object(
            subscription_service.multi_panel, "delete_user", new=delete_remote
        ), patch.object(
            subscription_service.agent_db, "delete_service_node", return_value=True
        ), patch.object(
            subscription_service.agent_db, "delete_service", return_value=True
        ):
            result = await subscription_service.delete_subscription(9, 71)

        self.assertTrue(result)
        self.assertEqual(
            delete_remote.await_args_list,
            [
                call(primary, "shared-user-uuid", marzban_username=""),
                call(self.sanaei, "shared-user-uuid", marzban_username=""),
            ],
        )

    async def test_remote_failure_keeps_local_service_for_retry(self):
        async def delete_remote(server, _uuid, **_kwargs):
            if server["id"] == 12:
                raise RuntimeError("Sanaei connection timeout")

        with patch.object(
            subscription_service.agent_db, "get_service_by_id", return_value=self.service
        ), patch.object(
            subscription_service, "get_service_panel_targets", return_value=self.targets
        ), patch.object(
            subscription_service.multi_panel,
            "delete_user",
            new=AsyncMock(side_effect=delete_remote),
        ), patch.object(
            subscription_service.agent_db, "delete_service_node", return_value=True
        ) as delete_mapping, patch.object(
            subscription_service.agent_db, "delete_service", return_value=True
        ) as delete_local:
            result = await subscription_service.delete_subscription(9, 71)

        self.assertFalse(result)
        delete_mapping.assert_called_once_with(71, 10, "shared-user-uuid")
        delete_local.assert_not_called()

    async def test_already_absent_node_is_an_idempotent_success(self):
        async def delete_remote(server, _uuid, **_kwargs):
            if server["id"] == 12:
                raise RuntimeError("user not found (uuid=xui-client-uuid)")

        with patch.object(
            subscription_service.agent_db, "get_service_by_id", return_value=self.service
        ), patch.object(
            subscription_service, "get_service_panel_targets", return_value=self.targets
        ), patch.object(
            subscription_service.multi_panel,
            "delete_user",
            new=AsyncMock(side_effect=delete_remote),
        ), patch.object(
            subscription_service.agent_db, "delete_service_node", return_value=True
        ) as delete_mapping, patch.object(
            subscription_service.agent_db, "delete_service", return_value=True
        ) as delete_local:
            result = await subscription_service.delete_subscription(9, 71)

        self.assertTrue(result)
        self.assertEqual(delete_mapping.call_count, 2)
        delete_local.assert_called_once_with(71)

    async def test_missing_targets_does_not_drop_local_service(self):
        with patch.object(
            subscription_service.agent_db, "get_service_by_id", return_value=self.service
        ), patch.object(
            subscription_service, "get_service_panel_targets", return_value=[]
        ), patch.object(
            subscription_service.agent_db, "delete_service", return_value=True
        ) as delete_local:
            result = await subscription_service.delete_subscription(9, 71)

        self.assertFalse(result)
        delete_local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
