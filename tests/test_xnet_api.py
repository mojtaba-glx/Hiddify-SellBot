import base64
import unittest
from unittest.mock import AsyncMock, patch

from Shared import xnet_api


class XnetApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = {
            "panel_type": "xnet",
            "panel_url": "http://127.0.0.1:8080",
            "xnet_username": "admin",
            "xnet_password": "secret",
        }

    def test_is_xnet_server(self):
        self.assertTrue(xnet_api.is_xnet_server({"panel_type": "xnet"}))
        self.assertTrue(xnet_api.is_xnet_server({"panel_type": "X-NET"}))
        self.assertFalse(xnet_api.is_xnet_server({"panel_type": "hiddify"}))

    async def test_get_user_by_uuid_reads_inbound_clients(self):
        inbounds = [
            {
                "id": "in-1",
                "protocol": "VLESS",
                "port": 443,
                "clients": [
                    {
                        "id": "c-1",
                        "uuid": "abc-def",
                        "username": "demo",
                        "status": "active",
                        "trafficLimitBytes": 10 * 1024**3,
                        "trafficUsedBytes": 2 * 1024**3,
                    }
                ],
            }
        ]
        with patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=inbounds)
        ):
            user = await xnet_api.get_user_by_uuid(self.server, "abc-def")
        self.assertEqual(user["name"], "demo")
        self.assertEqual(user["uuid"], "abc-def")
        self.assertEqual(user["usage_limit_GB"], 10.0)
        self.assertEqual(user["current_usage_GB"], 2.0)

    async def test_create_user_preserves_requested_uuid_across_selected_inbounds(self):
        server = dict(self.server)
        server["xnet_inbound_id"] = "0"
        inbounds = [
            {"id": "in-1", "enabled": True, "protocol": "VLESS", "clients": []},
            {"id": "in-2", "enabled": True, "protocol": "Hysteria2", "clients": []},
        ]
        requested = "11111111-2222-4333-8444-555555555555"
        request_mock = AsyncMock(
            return_value={"id": "c-1", "uuid": requested, "status": "active"}
        )
        expected = {"uuid": requested, "name": "test"}

        with patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=inbounds)
        ), patch.object(
            xnet_api, "_request_json", new=request_mock
        ), patch.object(
            xnet_api, "get_user_by_uuid", new=AsyncMock(return_value=expected)
        ):
            result = await xnet_api.create_user(
                server,
                {
                    "name": "test",
                    "uuid": requested,
                    "usage_limit_GB": 50,
                    "package_days": 30,
                },
            )

        self.assertEqual(result["uuid"], requested)
        args = request_mock.await_args
        self.assertEqual(args.args[0], "POST")
        self.assertEqual(args.args[1], "/api/inbounds/in-1/clients")
        body = args.kwargs["json"]
        self.assertEqual(body["uuid"], requested)
        self.assertEqual(body["extraInboundIds"], ["in-2"])
        self.assertEqual(body["trafficLimitBytes"], 50 * 1024**3)

    async def test_patch_user_can_rotate_uuid(self):
        old_uuid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        new_uuid = "11111111-2222-4333-8444-555555555555"
        inbounds = [
            {
                "id": "in-1",
                "protocol": "VLESS",
                "clients": [
                    {
                        "id": "c-1",
                        "uuid": old_uuid,
                        "username": "demo",
                        "status": "active",
                        "trafficLimitBytes": 10 * 1024**3,
                    }
                ],
            }
        ]
        request_mock = AsyncMock(return_value={"success": True})
        with patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=inbounds)
        ), patch.object(
            xnet_api, "_request_json", new=request_mock
        ), patch.object(
            xnet_api,
            "get_user_by_uuid",
            new=AsyncMock(return_value={"uuid": new_uuid, "name": "demo"}),
        ):
            result = await xnet_api.patch_user(
                self.server, old_uuid, {"uuid": new_uuid}
            )

        self.assertEqual(result["uuid"], new_uuid)
        body = request_mock.await_args.kwargs["json"]
        self.assertEqual(body["uuid"], new_uuid)

    async def test_get_user_configs_decodes_default_base64_subscription(self):
        body = base64.b64encode(
            b"vless://one\nhysteria2://two\nnot-a-config"
        ).decode("ascii")
        with patch.object(
            xnet_api, "get_subscription_body", new=AsyncMock(return_value=body)
        ):
            configs = await xnet_api.get_user_configs(self.server, "abc")
        self.assertEqual([c["protocol"] for c in configs], ["vless", "hysteria2"])

    async def test_test_connect_requires_ok_ping_and_management_api(self):
        with patch.object(
            xnet_api, "ping", new=AsyncMock(return_value={"status": "ok"})
        ), patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=[])
        ), patch.object(
            xnet_api, "list_users", new=AsyncMock(return_value=[])
        ):
            self.assertEqual(await xnet_api.test_connect(self.server), [])


if __name__ == "__main__":
    unittest.main()
