import unittest
from unittest.mock import AsyncMock, patch

from AdminBot import servers
from Shared import agent_db


class AdminAgencyDeletePropagationTests(unittest.IsolatedAsyncioTestCase):
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
