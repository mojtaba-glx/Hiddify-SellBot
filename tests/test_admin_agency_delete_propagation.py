import unittest
from unittest.mock import AsyncMock, patch

from AdminBot import servers
from Shared import agent_db


class AdminAgencyDeletePropagationTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_server_delete_preserves_service_records(self):
        server = {"id": 20, "title": "France", "panel_type": "xui"}
        panel_user = {
            "uuid": "uuid-extra",
            "current_usage_GB": 2.5,
        }

        with patch.object(
            servers.database, "get_server_by_id", return_value=server
        ), patch.object(
            servers, "_get_panel_user_with_list_fallback",
            new=AsyncMock(return_value=panel_user),
        ), patch.object(
            servers.hiddify_api, "delete_user", new=AsyncMock(return_value=None)
        ) as delete_panel, patch.object(
            servers.userbot_db,
            "get_service_owner_by_panel_uuid",
            return_value={"service_id": 7},
        ), patch.object(
            servers.userbot_db, "update_service_node_runtime"
        ) as user_node_update, patch.object(
            agent_db, "get_service_by_uuid",
            return_value={"id": 9},
        ), patch.object(
            agent_db, "update_service_node_runtime"
        ) as agent_node_update, patch.object(
            agent_db, "hard_delete_service_by_uuid"
        ) as hard_delete:
            ok, error = await servers._delete_user_on_single_server(
                20, "uuid-extra"
            )

        self.assertTrue(ok)
        self.assertEqual(error, "")
        delete_panel.assert_awaited_once_with(server, "uuid-extra")
        hard_delete.assert_not_called()
        user_node_update.assert_called_once()
        agent_node_update.assert_called_once()
        self.assertEqual(user_node_update.call_args.kwargs["deleted"], 1)
        self.assertEqual(agent_node_update.call_args.kwargs["deleted"], 1)

    async def test_successful_admin_panel_delete_hard_purges_agency_service(self):
        server = {"id": 10, "title": "X-Net", "panel_type": "xnet"}
        delete_panel = AsyncMock(return_value=None)

        with patch.object(
            servers.database, "get_server_by_id", return_value=server
        ), patch.object(
            servers, "_get_related_server_targets", return_value=[server]
        ), patch.object(
            servers.hiddify_api, "delete_user", new=delete_panel
        ), patch.object(
            servers.userbot_db, "delete_services_by_panel_user", return_value=1
        ), patch.object(
            agent_db, "hard_delete_service_by_uuid", return_value=1
        ) as hard_delete:
            deleted_ids, failures = await servers._delete_user_across_related_servers(
                10, "uuid-customer"
            )

        self.assertEqual(deleted_ids, [10])
        self.assertEqual(failures, [])
        hard_delete.assert_called_once_with("uuid-customer")

    async def test_failed_admin_panel_delete_keeps_agency_service(self):
        server = {"id": 10, "title": "X-Net", "panel_type": "xnet"}

        with patch.object(
            servers.database, "get_server_by_id", return_value=server
        ), patch.object(
            servers, "_get_related_server_targets", return_value=[server]
        ), patch.object(
            servers.hiddify_api,
            "delete_user",
            new=AsyncMock(side_effect=RuntimeError("panel offline")),
        ), patch.object(
            agent_db, "hard_delete_service_by_uuid", return_value=1
        ) as hard_delete:
            deleted_ids, failures = await servers._delete_user_across_related_servers(
                10, "uuid-customer"
            )

        self.assertEqual(deleted_ids, [])
        self.assertEqual(len(failures), 1)
        hard_delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
