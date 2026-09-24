import base64
import unittest
from unittest.mock import AsyncMock, patch

from Shared import xnet_api


class XnetApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = {
            "panel_type": "xnet",
            "panel_url": "http://127.0.0.1:8080",
            "xnet_api_token": "fake-token",
        }

    def test_is_xnet_server(self):
        self.assertTrue(xnet_api.is_xnet_server({"panel_type": "xnet"}))
        self.assertTrue(xnet_api.is_xnet_server({"panel_type": "X-NET"}))
        self.assertFalse(xnet_api.is_xnet_server({"panel_type": "hiddify"}))

    def test_subscriber_array(self):
        users = xnet_api._subscriber_array(
            {"subscribers": [{"uuid": "u1"}, {"uuid": "u2"}]}
        )
        self.assertEqual([u["uuid"] for u in users], ["u1", "u2"])

    async def test_get_user_by_uuid_uses_verified_list_endpoint(self):
        with patch.object(
            xnet_api,
            "list_users",
            new=AsyncMock(return_value=[{"uuid": "abc", "name": "demo"}]),
        ):
            user = await xnet_api.get_user_by_uuid(self.server, "abc")
        self.assertEqual(user["name"], "demo")

    async def test_get_user_configs_decodes_default_base64_subscription(self):
        body = base64.b64encode(
            b"vless://one\nhysteria2://two\nnot-a-config"
        ).decode("ascii")
        with patch.object(
            xnet_api, "get_subscription_body", new=AsyncMock(return_value=body)
        ):
            configs = await xnet_api.get_user_configs(self.server, "abc")
        self.assertEqual([c["protocol"] for c in configs], ["vless", "hysteria2"])

    async def test_test_connect_requires_ok_ping(self):
        with patch.object(
            xnet_api, "ping", new=AsyncMock(return_value={"status": "ok"})
        ), patch.object(
            xnet_api, "list_users", new=AsyncMock(return_value=[])
        ):
            self.assertEqual(await xnet_api.test_connect(self.server), [])


if __name__ == "__main__":
    unittest.main()
