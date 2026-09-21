import json
import unittest
from unittest.mock import AsyncMock, call, patch

from Shared import multi_panel, xui_alireza, xui_sanaei
from AgentBot.services import subscription_service


class StrictPanelUuidTests(unittest.IsolatedAsyncioTestCase):
    async def test_corrects_and_verifies_a_panel_generated_uuid(self):
        server = {"id": 7, "title": "node"}
        payload = {"name": "user", "uuid": "shared-user-uuid"}

        with patch.object(
            multi_panel,
            "create_user",
            new=AsyncMock(return_value={"uuid": "panel-generated-uuid"}),
        ) as create_mock, patch.object(
            multi_panel,
            "patch_user",
            new=AsyncMock(return_value={"uuid": "shared-user-uuid"}),
        ) as patch_mock, patch.object(
            multi_panel,
            "get_user_by_uuid",
            new=AsyncMock(return_value={"uuid": "shared-user-uuid", "name": "user"}),
        ) as get_mock, patch.object(
            multi_panel,
            "delete_user",
            new=AsyncMock(),
        ) as delete_mock:
            result = await multi_panel.create_user_with_uuid(server, payload)

        self.assertEqual(result["uuid"], "shared-user-uuid")
        create_mock.assert_awaited_once_with(server, payload)
        patch_mock.assert_awaited_once_with(
            server, "panel-generated-uuid", {"uuid": "shared-user-uuid"}
        )
        get_mock.assert_awaited_once_with(server, "shared-user-uuid")
        delete_mock.assert_not_awaited()

    async def test_timeout_after_create_recovers_existing_hiddify_user_without_second_post(self):
        server = {"id": 7, "title": "node"}
        payload = {"name": "user", "uuid": "shared-user-uuid"}
        timeout = RuntimeError("read timeout")

        with patch.object(
            multi_panel,
            "create_user",
            new=AsyncMock(side_effect=timeout),
        ) as create_mock, patch.object(
            multi_panel.hiddify_api,
            "_is_xui_server",
            return_value=False,
        ), patch.object(
            multi_panel,
            "get_user_by_uuid",
            new=AsyncMock(return_value={"uuid": "shared-user-uuid", "name": "user"}),
        ) as get_mock:
            result = await multi_panel.create_user_with_uuid(server, payload)

        self.assertEqual(result["uuid"], "shared-user-uuid")
        create_mock.assert_awaited_once_with(server, payload)
        get_mock.assert_awaited_once_with(server, "shared-user-uuid")

    async def test_timeout_without_persisted_user_fails_without_second_post(self):
        server = {"id": 7, "title": "node"}
        payload = {"name": "user", "uuid": "shared-user-uuid"}
        timeout = RuntimeError("read timeout")

        with patch.object(
            multi_panel,
            "create_user",
            new=AsyncMock(side_effect=timeout),
        ) as create_mock, patch.object(
            multi_panel.hiddify_api,
            "_is_xui_server",
            return_value=False,
        ), patch.object(
            multi_panel,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=RuntimeError("not found")),
        ) as get_mock, patch.object(
            multi_panel.asyncio,
            "sleep",
            new=AsyncMock(),
        ):
            with self.assertRaises(RuntimeError):
                await multi_panel.create_user_with_uuid(server, payload)

        create_mock.assert_awaited_once_with(server, payload)
        self.assertEqual(get_mock.await_count, 2)

    async def test_cluster_does_not_retry_create_after_timeout_failure(self):
        targets = [{"id": 1, "title": "main"}]
        payload = {"name": "user", "uuid": "shared-user-uuid"}
        create_mock = AsyncMock(side_effect=RuntimeError("read timeout"))

        with patch.object(
            subscription_service.multi_panel,
            "create_user_with_uuid",
            new=create_mock,
        ):
            with self.assertRaises(RuntimeError):
                await subscription_service._create_user_on_cluster(targets, payload)

        self.assertEqual(create_mock.await_count, 1)

    async def test_cluster_creation_rolls_back_when_one_node_fails(self):
        targets = [
            {"id": 1, "title": "main"},
            {"id": 2, "title": "node"},
        ]
        payload = {"name": "user", "uuid": "shared-user-uuid"}
        create_mock = AsyncMock(
            side_effect=[
                {"uuid": "shared-user-uuid"},
                RuntimeError("node rejected UUID"),
            ]
        )

        with patch.object(
            subscription_service.multi_panel,
            "create_user_with_uuid",
            new=create_mock,
        ), patch.object(
            subscription_service,
            "delete_user_on_panel",
            new=AsyncMock(return_value=True),
        ) as delete_mock:
            with self.assertRaises(RuntimeError):
                await subscription_service._create_user_on_cluster(targets, payload)

        delete_mock.assert_awaited_once_with(
            "shared-user-uuid", 1, marzban_username=""
        )


class AgentLinkUuidTests(unittest.IsolatedAsyncioTestCase):
    async def test_link_change_applies_one_uuid_to_primary_and_nodes(self):
        service = {
            "id": 41,
            "agent_id": 9,
            "server_id": 1,
            "panel_user_uuid": "old-main-uuid",
        }
        main = {"id": 1, "title": "main"}
        node = {"id": 2, "title": "node"}
        targets = [
            (main, "old-main-uuid", ""),
            (node, "old-node-uuid", ""),
        ]

        async def patch_user(_server, _old_uuid, payload):
            return {"uuid": payload["uuid"]}

        async def get_user(_server, user_uuid):
            return {"uuid": user_uuid}

        with patch.object(
            subscription_service.agent_db,
            "get_service_by_id",
            side_effect=[service, {**service, "panel_user_uuid": "new-shared-uuid"}],
        ), patch.object(
            subscription_service,
            "get_service_panel_targets",
            return_value=targets,
        ), patch.object(
            subscription_service.uuid,
            "uuid4",
            return_value="new-shared-uuid",
        ), patch.object(
            subscription_service.hiddify_api,
            "patch_user",
            new=AsyncMock(side_effect=patch_user),
        ) as panel_patch, patch.object(
            subscription_service.hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=get_user),
        ), patch.object(
            subscription_service.agent_db,
            "update_service",
            return_value=True,
        ) as update_service, patch.object(
            subscription_service.agent_db,
            "update_service_node_uuid",
            return_value=True,
        ) as update_node:
            result = await subscription_service.change_subscription_link(9, 41)

        self.assertEqual(result["panel_user_uuid"], "new-shared-uuid")
        self.assertEqual(
            panel_patch.await_args_list,
            [
                call(main, "old-main-uuid", {"uuid": "new-shared-uuid"}),
                call(node, "old-node-uuid", {"uuid": "new-shared-uuid"}),
            ],
        )
        update_service.assert_called_once_with(
            41, {"panel_user_uuid": "new-shared-uuid"}
        )
        self.assertEqual(update_node.call_count, 2)


class XuiUuidRotationTests(unittest.IsolatedAsyncioTestCase):
    OLD_UUID = "11111111-1111-4111-8111-111111111111"
    NEW_UUID = "22222222-2222-4222-8222-222222222222"

    async def test_alireza_patch_updates_xray_id_and_sub_id(self):
        inbound = {
            "id": 5,
            "protocol": "vless",
            "settings": json.dumps(
                {
                    "clients": [
                        {
                            "id": self.OLD_UUID,
                            "subId": self.OLD_UUID,
                            "email": "customer",
                            "enable": True,
                        }
                    ]
                }
            ),
        }
        requests = []

        class FakeContext:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def request(self, method, path, **kwargs):
                requests.append((method, path, kwargs))
                return {"success": True}

        with patch.object(
            xui_alireza,
            "_list_inbounds",
            new=AsyncMock(return_value=[inbound]),
        ), patch.object(
            xui_alireza,
            "_XuiContext",
            return_value=FakeContext(),
        ):
            result = await xui_alireza.patch_user(
                {"id": 2}, self.OLD_UUID, {"uuid": self.NEW_UUID}
            )

        self.assertEqual(result["uuid"], self.NEW_UUID)
        body = json.loads(requests[0][2]["json_body"]["settings"])
        self.assertEqual(body["clients"][0]["id"], self.NEW_UUID)
        self.assertEqual(body["clients"][0]["subId"], self.NEW_UUID)

    async def test_sanaei_patch_updates_uuid_id_and_sub_id(self):
        old_client = {
            "email": "customer",
            "uuid": self.OLD_UUID,
            "id": self.OLD_UUID,
            "subId": self.OLD_UUID,
            "enable": True,
        }
        new_client = {
            **old_client,
            "uuid": self.NEW_UUID,
            "id": self.NEW_UUID,
            "subId": self.NEW_UUID,
        }
        requests = []

        class FakeContext:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def request(self, method, path, **kwargs):
                requests.append((method, path, kwargs))
                return {"success": True}

        with patch.object(
            xui_sanaei,
            "_list_clients",
            new=AsyncMock(side_effect=[[old_client], [new_client]]),
        ), patch.object(
            xui_sanaei,
            "_XuiContext",
            return_value=FakeContext(),
        ):
            result = await xui_sanaei.patch_user(
                {"id": 3}, self.OLD_UUID, {"uuid": self.NEW_UUID}
            )

        self.assertEqual(result["uuid"], self.NEW_UUID)
        body = requests[0][2]["json_body"]
        self.assertEqual(body["uuid"], self.NEW_UUID)
        self.assertEqual(body["id"], self.NEW_UUID)
        self.assertEqual(body["subId"], self.NEW_UUID)


if __name__ == "__main__":
    unittest.main()
