import base64
import unittest
from unittest.mock import AsyncMock, patch

from Shared import hiddify_api, xnet_api


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
        ), patch.object(
            xnet_api, "_online_client_map", new=AsyncMock(return_value={})
        ), patch.object(
            xnet_api, "_last_seen_from_sessions", new=AsyncMock(return_value=None)
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

    async def test_sync_users_to_inbounds_adds_new_target_without_recreating_user(self):
        server = dict(self.server)
        server["xnet_inbound_id"] = "0"
        uid = "11111111-2222-4333-8444-555555555555"
        client = {
            "id": "c-1",
            "uuid": uid,
            "username": "demo",
            "status": "active",
            "trafficLimitBytes": 10 * 1024**3,
            "trafficUsedBytes": 2 * 1024**3,
            "expireDate": "2026-12-31T00:00:00Z",
        }
        initial = [
            {
                "id": "in-1",
                "enabled": True,
                "protocol": "VLESS",
                "clients": [dict(client)],
            },
            {
                "id": "in-2",
                "enabled": True,
                "protocol": "Hysteria2",
                "clients": [],
            },
        ]
        fresh = [
            {
                "id": "in-1",
                "enabled": True,
                "protocol": "VLESS",
                "clients": [dict(client)],
            },
            {
                "id": "in-2",
                "enabled": True,
                "protocol": "Hysteria2",
                "clients": [dict(client)],
            },
        ]
        get_inbounds = AsyncMock(side_effect=[initial, fresh])
        request_mock = AsyncMock(return_value={"success": True})

        with patch.object(
            xnet_api, "get_inbounds", new=get_inbounds
        ), patch.object(
            xnet_api, "_request_json", new=request_mock
        ):
            result = await xnet_api.sync_users_to_inbounds(server)

        self.assertTrue(result["ok"])
        self.assertEqual(result["total_users"], 1)
        self.assertEqual(result["target_inbounds"], 2)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["errors"], [])

        args = request_mock.await_args
        self.assertEqual(args.args[0], "PUT")
        self.assertEqual(args.args[1], "/api/inbounds/in-1/clients/c-1")
        body = args.kwargs["json"]
        self.assertEqual(body["uuid"], uid)
        self.assertEqual(body["username"], "demo")
        self.assertEqual(body["trafficLimitBytes"], 10 * 1024**3)
        self.assertEqual(body["expireDate"], "2026-12-31T00:00:00Z")
        self.assertEqual(body["extraInboundIds"], ["in-2"])

    async def test_sync_users_to_inbounds_is_idempotent_when_already_synced(self):
        server = dict(self.server)
        server["xnet_inbound_id"] = "0"
        uid = "11111111-2222-4333-8444-555555555555"
        shared = {
            "id": "c-1",
            "uuid": uid,
            "username": "demo",
            "status": "active",
        }
        inbounds = [
            {
                "id": "in-1",
                "enabled": True,
                "protocol": "VLESS",
                "clients": [dict(shared)],
            },
            {
                "id": "in-2",
                "enabled": True,
                "protocol": "Hysteria2",
                "clients": [dict(shared)],
            },
        ]
        request_mock = AsyncMock(return_value={"success": True})
        with patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=inbounds)
        ), patch.object(
            xnet_api, "_request_json", new=request_mock
        ):
            result = await xnet_api.sync_users_to_inbounds(server)

        self.assertTrue(result["ok"])
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 1)
        request_mock.assert_not_awaited()

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
        ), patch.object(
            xnet_api, "_online_client_map", new=AsyncMock(return_value={})
        ), patch.object(
            xnet_api, "_last_seen_from_sessions", new=AsyncMock(return_value=None)
        ):
            user = await xnet_api.get_user_by_uuid(self.server, uid)
        self.assertEqual(user["current_usage_GB"], 3.0)

    async def test_list_users_marks_online_from_xnet_live_endpoint(self):
        inbounds = [
            {
                "id": "in-1",
                "protocol": "VLESS",
                "port": 443,
                "clients": [
                    {
                        "id": "c-live",
                        "uuid": "live-uuid",
                        "username": "live-user",
                        "status": "active",
                        "trafficLimitBytes": 5 * 1024**3,
                        "trafficUsedBytes": 1024,
                    }
                ],
            }
        ]
        with patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=inbounds)
        ), patch.object(
            xnet_api,
            "_online_client_map",
            new=AsyncMock(return_value={
                "c-live": {
                    "clientId": "c-live",
                    "username": "live-user",
                    "devices": 2,
                }
            }),
        ):
            users = await xnet_api.list_users(self.server)

        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["_user_list_status"], "online")
        self.assertEqual(users[0]["activeSessions"], 2)
        self.assertTrue(users[0]["last_online"])

    async def test_get_user_by_uuid_uses_last_connection_at_when_offline(self):
        inbounds = [
            {
                "id": "in-1",
                "protocol": "VLESS",
                "port": 443,
                "clients": [
                    {
                        "id": "c-offline",
                        "uuid": "offline-uuid",
                        "username": "offline-user",
                        "status": "active",
                        "trafficLimitBytes": 5 * 1024**3,
                        "trafficUsedBytes": 1024,
                        "lastConnectionAt": "2026-09-24T10:15:00Z",
                    }
                ],
            }
        ]
        history = AsyncMock(return_value=None)
        with patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=inbounds)
        ), patch.object(
            xnet_api, "_online_client_map", new=AsyncMock(return_value={})
        ), patch.object(
            xnet_api, "_last_seen_from_sessions", new=history
        ):
            user = await xnet_api.get_user_by_uuid(self.server, "offline-uuid")

        self.assertEqual(user["_user_list_status"], "offline")
        self.assertEqual(user["last_online"], "2026-09-24T10:15:00Z")
        history.assert_not_awaited()

    async def test_get_user_by_uuid_falls_back_to_session_history(self):
        inbounds = [
            {
                "id": "in-1",
                "protocol": "VLESS",
                "port": 443,
                "clients": [
                    {
                        "id": "c-history",
                        "uuid": "history-uuid",
                        "username": "history-user",
                        "status": "active",
                    }
                ],
            }
        ]
        with patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=inbounds)
        ), patch.object(
            xnet_api, "_online_client_map", new=AsyncMock(return_value={})
        ), patch.object(
            xnet_api,
            "_last_seen_from_sessions",
            new=AsyncMock(return_value="2026-09-24T10:12:00Z"),
        ):
            user = await xnet_api.get_user_by_uuid(self.server, "history-uuid")

        self.assertEqual(user["_user_list_status"], "offline")
        self.assertEqual(user["last_online"], "2026-09-24T10:12:00Z")

    async def test_disabled_xnet_user_is_exposed_as_inactive_account(self):
        inbounds = [
            {
                "id": "in-1",
                "protocol": "VLESS",
                "port": 443,
                "clients": [
                    {
                        "id": "c-disabled",
                        "uuid": "disabled-uuid",
                        "username": "disabled-user",
                        "status": "disabled",
                    }
                ],
            }
        ]
        with patch.object(
            xnet_api, "get_inbounds", new=AsyncMock(return_value=inbounds)
        ), patch.object(
            xnet_api, "_online_client_map", new=AsyncMock(return_value={})
        ), patch.object(
            xnet_api, "_last_seen_from_sessions", new=AsyncMock(return_value=None)
        ):
            user = await xnet_api.get_user_by_uuid(self.server, "disabled-uuid")

        self.assertFalse(user["is_active"])
        self.assertEqual(user["status"], "disabled")
        self.assertEqual(user["_user_list_status"], "offline")

    def test_management_api_prefers_internal_url(self):
        server = dict(self.server)
        server["panel_url"] = "https://xnet.speedll.ir:8080"
        server["xnet_api_url"] = "http://127.0.0.1:8080"
        self.assertEqual(
            xnet_api._base_url(server),
            "http://127.0.0.1:8080",
        )

    def test_internal_api_url_never_leaks_into_public_subscription(self):
        server = dict(self.server)
        server["panel_url"] = "https://xnet.speedll.ir:8080"
        server["xnet_api_url"] = "http://127.0.0.1:8080"
        server.pop("xnet_sub_domain", None)
        server.pop("xnet_sub_host", None)

        self.assertEqual(
            xnet_api.get_subscription_url(server, "abc"),
            "https://xnet.speedll.ir/api/v1/sub/abc",
        )

    def test_admin_web_url_uses_hidden_panel_path_and_subscriptions_page(self):
        server = dict(self.server)
        server["panel_url"] = "https://xnet.speedll.ir:8080"
        server["xnet_web_base_path"] = "SecretPanelPath"

        self.assertEqual(
            xnet_api.get_admin_web_url(server, "#/subscriptions"),
            "https://xnet.speedll.ir:8080/SecretPanelPath/#/subscriptions",
        )

    def test_admin_web_url_is_separate_from_public_subscription_url(self):
        server = dict(self.server)
        server["panel_url"] = "https://xnet.speedll.ir:8080"
        server["xnet_web_base_path"] = "SecretPanelPath"
        server["xnet_sub_domain"] = "https://xnet.speedll.ir"

        self.assertEqual(
            xnet_api.get_admin_web_url(server, "#/subscriptions"),
            "https://xnet.speedll.ir:8080/SecretPanelPath/#/subscriptions",
        )
        self.assertEqual(
            xnet_api.get_subscription_url(server, "abc"),
            "https://xnet.speedll.ir/api/v1/sub/abc",
        )

    def test_public_subscription_url_can_use_custom_domain(self):
        server = dict(self.server)
        server["xnet_sub_domain"] = "sub.example.com"
        self.assertEqual(
            xnet_api.get_subscription_url(server, "abc"),
            "https://sub.example.com/api/v1/sub/abc",
        )

    def test_public_subscription_url_drops_xnet_management_port_for_dns_host(self):
        server = dict(self.server)
        server["panel_url"] = "https://xnet.speedll.ir:8080"
        server.pop("xnet_sub_domain", None)
        server.pop("xnet_sub_host", None)

        self.assertEqual(
            xnet_api.get_subscription_url(server, "abc"),
            "https://xnet.speedll.ir/api/v1/sub/abc",
        )

    def test_public_subscription_custom_domain_drops_management_port(self):
        server = dict(self.server)
        server["xnet_sub_domain"] = "https://xnet.speedll.ir:8080"

        self.assertEqual(
            xnet_api.get_subscription_url(server, "abc"),
            "https://xnet.speedll.ir/api/v1/sub/abc",
        )

    def test_public_subscription_keeps_management_port_for_raw_ip_fallback(self):
        server = dict(self.server)
        server["panel_url"] = "https://31.56.48.96:8080"
        server.pop("xnet_sub_domain", None)
        server.pop("xnet_sub_host", None)

        self.assertEqual(
            xnet_api.get_subscription_url(server, "abc"),
            "https://31.56.48.96:8080/api/v1/sub/abc",
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

    async def test_download_server_backup_creates_downloads_and_cleans_up(self):
        request_json = AsyncMock(
            side_effect=[
                {
                    "id": "bk-2",
                    "filename": "xnet-2026-09-24T2215.db",
                    "sizeBytes": 205000,
                },
                {"success": True},
            ]
        )
        request_bytes = AsyncMock(
            return_value=(
                b"XNET-BACKUP-ARCHIVE",
                {"content-type": "application/octet-stream"},
            )
        )

        with patch.object(xnet_api, "_request_json", new=request_json), patch.object(
            xnet_api, "_request_bytes", new=request_bytes
        ):
            result = await xnet_api.download_server_backup(self.server)

        self.assertEqual(result["filename"], "xnet-2026-09-24T2215.db")
        self.assertEqual(result["content"], b"XNET-BACKUP-ARCHIVE")
        self.assertEqual(
            result["source_url"],
            "http://127.0.0.1:8080/api/backups/bk-2/download",
        )

        self.assertEqual(request_json.await_args_list[0].args[:2], ("POST", "/api/backups"))
        self.assertEqual(
            request_bytes.await_args.args[:2],
            ("GET", "/api/backups/bk-2/download"),
        )
        self.assertEqual(
            request_json.await_args_list[1].args[:2],
            ("DELETE", "/api/backups/bk-2"),
        )

    async def test_download_server_backup_falls_back_to_newest_backup_list(self):
        request_json = AsyncMock(
            side_effect=[
                {"success": True},
                [
                    {
                        "id": "bk-old",
                        "filename": "old.db",
                        "createdAt": "2026-09-23T10:00:00Z",
                    },
                    {
                        "id": "bk-new",
                        "filename": "new.db",
                        "createdAt": "2026-09-24T10:00:00Z",
                    },
                ],
                {"success": True},
            ]
        )
        request_bytes = AsyncMock(return_value=(b"NEWEST", {}))

        with patch.object(xnet_api, "_request_json", new=request_json), patch.object(
            xnet_api, "_request_bytes", new=request_bytes
        ):
            result = await xnet_api.download_server_backup(self.server)

        self.assertEqual(result["filename"], "new.db")
        self.assertEqual(result["content"], b"NEWEST")
        self.assertEqual(
            request_bytes.await_args.args[1],
            "/api/backups/bk-new/download",
        )

    async def test_hiddify_backup_dispatcher_routes_xnet(self):
        expected = {
            "filename": "xnet.db",
            "content": b"backup",
            "source_url": "http://127.0.0.1:8080/api/backups/bk-1/download",
        }
        downloader = AsyncMock(return_value=expected)
        with patch.object(xnet_api, "download_server_backup", new=downloader):
            result = await hiddify_api.download_server_backup(self.server)

        self.assertEqual(result, expected)
        downloader.assert_awaited_once_with(self.server)

    async def test_get_server_stats_uses_real_xnet_windows_and_realtime_rates(self):
        gib = 1024 ** 3
        users = [
            {
                "uuid": "u-1",
                "xnet_client_id": "c-1",
                "_user_list_status": "online",
            },
            {
                "uuid": "u-2",
                "xnet_client_id": "c-2",
                "_user_list_status": "offline",
            },
        ]
        metrics = {
            "cpuUsage": 6.5,
            "cpuCores": 1,
            "ramUsage": {"used": 0.52, "total": 3.82},
            "storageUsage": {"used": 2.22, "total": 37.54},
            "onlineUsersCount": 1,
            "singBoxStatus": "running",
        }
        traffic = {
            "totalUpload": 1 * gib,
            "totalDownload": 2 * gib,
            "todayUpload": 128 * 1024**2,
            "todayDownload": 384 * 1024**2,
            "activeClients": 1,
        }
        month_analytics = {
            "windowHasData": True,
            "periodUpload": 1 * gib,
            "periodDownload": 2 * gib,
            "periodTotal": 3 * gib,
            "consumers": [
                {
                    "clientId": "c-1",
                    "kind": "vpn",
                    "periodTotal": 2 * gib,
                },
                {
                    "clientId": "c-2",
                    "kind": "vpn",
                    "periodTotal": 1 * gib,
                },
                {
                    "clientId": "ssh:ali",
                    "kind": "ssh",
                    "periodTotal": 9 * gib,
                },
            ],
        }

        with patch.object(
            xnet_api, "list_users", new=AsyncMock(return_value=users)
        ), patch.object(
            xnet_api, "get_traffic_summary", new=AsyncMock(return_value=traffic)
        ), patch.object(
            xnet_api,
            "_request_json",
            new=AsyncMock(return_value=metrics),
        ), patch.object(
            xnet_api,
            "_get_traffic_analytics",
            new=AsyncMock(return_value=month_analytics),
        ), patch.object(
            xnet_api,
            "_get_realtime_network_mb",
            new=AsyncMock(return_value=(8.4, 1.2)),
        ):
            stats = await xnet_api.get_server_stats(self.server)

        self.assertEqual(stats["users_total"], 2)
        self.assertEqual(stats["users_online"], 1)
        self.assertEqual(stats["users_today"], 1)
        self.assertEqual(stats["users_month"], 2)
        self.assertAlmostEqual(stats["usage_today_gb"], 0.5)
        self.assertAlmostEqual(stats["usage_30days_gb"], 3.0)
        self.assertAlmostEqual(stats["traffic_ul"], 1.0)
        self.assertAlmostEqual(stats["traffic_dl"], 2.0)
        self.assertAlmostEqual(stats["now_net_recv_mb"], 8.4)
        self.assertAlmostEqual(stats["now_net_sent_mb"], 1.2)

    async def test_get_server_stats_current_online_is_included_in_period_counts(self):
        users = [
            {
                "uuid": "u-1",
                "xnet_client_id": "c-1",
                "_user_list_status": "online",
            }
        ]
        with patch.object(
            xnet_api, "list_users", new=AsyncMock(return_value=users)
        ), patch.object(
            xnet_api,
            "get_traffic_summary",
            new=AsyncMock(return_value={}),
        ), patch.object(
            xnet_api,
            "_request_json",
            new=AsyncMock(return_value={"onlineUsersCount": 1}),
        ), patch.object(
            xnet_api,
            "_get_traffic_analytics",
            new=AsyncMock(return_value={"windowHasData": False, "consumers": []}),
        ), patch.object(
            xnet_api,
            "_get_realtime_network_mb",
            new=AsyncMock(return_value=(0.0, 0.0)),
        ):
            stats = await xnet_api.get_server_stats(self.server)

        self.assertEqual(stats["users_online"], 1)
        self.assertEqual(stats["users_today"], 1)
        self.assertEqual(stats["users_month"], 1)
        self.assertEqual(stats["usage_30days_gb"], 0.0)

    async def test_realtime_network_prefers_metrics_tick_mb_per_second(self):
        with patch.object(
            xnet_api,
            "_request_json",
            new=AsyncMock(
                return_value={"networkTraffic": {"up": 1.25, "down": 8.5}}
            ),
        ):
            down, up = await xnet_api._get_realtime_network_mb(self.server)

        self.assertAlmostEqual(down, 8.5)
        self.assertAlmostEqual(up, 1.25)

    async def test_health_probe_uses_only_public_ping(self):
        ping = AsyncMock(return_value={"status": "ok"})
        with patch.object(xnet_api, "ping", new=ping), patch.object(
            xnet_api, "get_inbounds", new=AsyncMock()
        ) as get_inbounds, patch.object(
            xnet_api, "list_users", new=AsyncMock()
        ) as list_users:
            result = await xnet_api.health_probe(self.server)

        self.assertEqual(result["status"], "ok")
        ping.assert_awaited_once_with(self.server)
        get_inbounds.assert_not_awaited()
        list_users.assert_not_awaited()

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
