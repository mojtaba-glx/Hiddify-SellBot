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

    async def test_multi_inbound_duplicate_client_is_not_double_counted(self):
        uid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        shared = {
            "id": "c-shared",
            "uuid": uid,
            "username": "demo",
            "status": "active",
            "trafficLimitBytes": 20 * 1024**3,
            "trafficUsedBytes": 3 * 1024**3,
        }
        inbounds = [
            {"id": "in-1", "protocol": "VLESS", "clients": [dict(shared)]},
            {"id": "in-2", "protocol": "Hysteria2", "clients": [dict(shared)]},
        ]
        with patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=inbounds)
        ):
            user = await xnet_api.get_user_by_uuid(self.server, uid)
        self.assertEqual(user["current_usage_GB"], 3.0)

    def test_public_subscription_url_can_use_custom_domain(self):
        server = dict(self.server)
        server["xnet_sub_domain"] = "sub.example.com"
        self.assertEqual(
            xnet_api.get_subscription_url(server, "abc"),
            "https://sub.example.com/api/v1/sub/abc",
        )

    async def test_get_user_configs_decodes_default_base64_subscription(self):
        body = base64.b64encode(
            b"vless://one\nhysteria2://two\nnot-a-config"
        ).decode("ascii")
        with patch.object(
            xnet_api, "get_subscription_body", new=AsyncMock(return_value=body)
        ):
            configs = await xnet_api.get_user_configs(self.server, "abc")
        self.assertEqual([c["protocol"] for c in configs], ["vless", "hysteria2"])

    def test_parse_config_link_reuses_existing_parser(self):
        parsed = xnet_api.parse_config_link(
            "vless://11111111-2222-4333-8444-555555555555@example.com:8443"
            "?security=tls&type=httpupgrade&path=%2Fhu-speed&sni=example.com"
        )
        self.assertEqual(parsed["protocol"], "vless")
        self.assertEqual(parsed["port"], 8443)
        self.assertEqual(parsed["network"], "httpupgrade")
        self.assertEqual(parsed["path"], "/hu-speed")

    async def test_create_inbound_from_link_maps_vless_tls_to_local_xnet_cert(self):
        inbounds = [
            {
                "id": "in-existing",
                "protocol": "VLESS",
                "port": 558,
                "security": "TLS",
                "sni": "xnet.example.com",
                "certFile": "/etc/sing-box/certs/xnet.example.com.crt",
                "keyFile": "/etc/sing-box/certs/xnet.example.com.key",
                "clients": [],
            }
        ]
        request_mock = AsyncMock(
            return_value={
                "id": "in-new",
                "protocol": "VLESS",
                "port": 8443,
                "enabled": True,
            }
        )
        compatibility = {
            "protocols": {
                "vless": {
                    "transports": ["tcp", "ws", "httpupgrade", "grpc", "http2"],
                    "security": ["none", "tls", "reality"],
                }
            }
        }
        link = (
            "vless://11111111-2222-4333-8444-555555555555@source.example:8443"
            "?security=tls&type=httpupgrade&path=%2Fhu-speed"
            "&sni=source.example&alpn=http%2F1.1#Imported"
        )

        with patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=inbounds)
        ), patch.object(
            xnet_api,
            "get_panel_config",
            new=AsyncMock(return_value={"port": "8080", "subPort": "2096"}),
        ), patch.object(
            xnet_api,
            "get_singbox_compatibility",
            new=AsyncMock(return_value=compatibility),
        ), patch.object(
            xnet_api, "_request_json", new=request_mock
        ):
            result = await xnet_api.create_inbound_from_link(self.server, link)

        self.assertEqual(result["id"], "in-new")
        args = request_mock.await_args
        self.assertEqual(args.args[:2], ("POST", "/api/inbounds"))
        body = args.kwargs["json"]
        self.assertEqual(body["protocol"], "VLESS")
        self.assertEqual(body["port"], 8443)
        self.assertEqual(body["transport"], "HTTPUpgrade")
        self.assertEqual(body["security"], "TLS")
        self.assertEqual(body["httpUpgradePath"], "/hu-speed")
        self.assertEqual(body["sni"], "xnet.example.com")
        self.assertEqual(
            body["certFile"], "/etc/sing-box/certs/xnet.example.com.crt"
        )
        self.assertEqual(
            body["keyFile"], "/etc/sing-box/certs/xnet.example.com.key"
        )
        self.assertEqual(body["clients"], [])

    async def test_create_inbound_from_link_rejects_duplicate_port(self):
        inbounds = [
            {
                "id": "in-existing",
                "protocol": "VLESS",
                "port": 8443,
                "clients": [],
            }
        ]
        link = (
            "vless://11111111-2222-4333-8444-555555555555@example.com:8443"
            "?security=none&type=tcp"
        )
        with patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=inbounds)
        ), patch.object(
            xnet_api,
            "get_panel_config",
            new=AsyncMock(return_value={"port": "8080", "subPort": "2096"}),
        ):
            with self.assertRaises(xnet_api.XnetApiError) as ctx:
                await xnet_api.create_inbound_from_link(self.server, link)
        self.assertIn("8443", str(ctx.exception))
        self.assertIn("استفاده شده", str(ctx.exception))

    async def test_create_inbound_from_link_rejects_reality_without_private_key(self):
        link = (
            "vless://11111111-2222-4333-8444-555555555555@example.com:443"
            "?security=reality&type=tcp&pbk=public-only"
        )
        with self.assertRaises(xnet_api.XnetApiError) as ctx:
            await xnet_api.create_inbound_from_link(self.server, link)
        self.assertIn("REALITY", str(ctx.exception))
        self.assertIn("کلید خصوصی", str(ctx.exception))

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
