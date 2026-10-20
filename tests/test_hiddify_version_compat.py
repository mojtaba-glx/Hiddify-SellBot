import os
import unittest
from unittest.mock import AsyncMock, patch

from Shared import hiddify_api


class HiddifyVersionCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with hiddify_api._panel_version_cache_lock:
            hiddify_api._PANEL_VERSION_CACHE.clear()
        self.server = {
            "id": 77,
            "panel_type": "hiddify",
            "panel_url": "https://panel.example.com",
            "admin_proxy_path": "admin-secret",
            "user_proxy_path": "user-secret",
            "admin_uuid": "00000000-0000-4000-8000-000000000077",
        }

    async def test_detects_major_version_from_panel_info(self):
        req = AsyncMock(return_value={"version": "13.0.0b11"})
        with patch.object(hiddify_api, "_request", new=req):
            info = await hiddify_api.get_panel_version(self.server, force=True)

        self.assertEqual(info["version"], "13.0.0b11")
        self.assertEqual(info["major"], 13)
        self.assertEqual(info["source"], "panel-info")
        self.assertTrue(req.await_args.args[1].endswith("/admin-secret/api/v2/panel/info/"))

    async def test_version_detection_failure_is_non_blocking(self):
        with patch.object(
            hiddify_api, "_request", new=AsyncMock(side_effect=RuntimeError("old reverse proxy"))
        ):
            info = await hiddify_api.get_panel_version(self.server, force=True)

        self.assertEqual(info["major"], 0)
        self.assertEqual(info["source"], "unknown")

    async def test_v13_patch_translates_legacy_active_fields_to_enable(self):
        req = AsyncMock(return_value={"uuid": "u-1", "enable": False, "is_active": False})
        with patch.object(
            hiddify_api, "get_panel_version", new=AsyncMock(return_value={"version": "13.0.0", "major": 13})
        ), patch.object(hiddify_api, "_request", new=req):
            await hiddify_api.patch_user(
                self.server,
                "u-1",
                {"is_active": False, "mode": "disable", "status": "disable", "comment": "x"},
            )

        sent = req.await_args.kwargs["json"]
        self.assertEqual(sent["enable"], False)
        self.assertEqual(sent["mode"], "no_reset")
        self.assertEqual(sent["comment"], "x")
        self.assertNotIn("is_active", sent)
        self.assertNotIn("status", sent)

    async def test_v12_patch_keeps_legacy_payload_unchanged(self):
        payload = {"is_active": False, "mode": "disable", "comment": "legacy"}
        req = AsyncMock(return_value={"uuid": "u-2"})
        with patch.object(
            hiddify_api, "get_panel_version", new=AsyncMock(return_value={"version": "12.3.3", "major": 12})
        ), patch.object(hiddify_api, "_request", new=req):
            await hiddify_api.patch_user(self.server, "u-2", payload)

        self.assertEqual(req.await_args.kwargs["json"], payload)

    async def test_v13_create_uses_enable_instead_of_is_active(self):
        req = AsyncMock(return_value={"uuid": "u-3", "enable": True})
        with patch.object(
            hiddify_api, "get_panel_version", new=AsyncMock(return_value={"version": "13.0.0", "major": 13})
        ), patch.object(hiddify_api, "_request", new=req), patch.dict(
            os.environ, {hiddify_api.CREATE_USER_STABILIZE_ENV: "off"}
        ):
            await hiddify_api.create_user(
                self.server,
                {"name": "test", "usage_limit_GB": 10, "package_days": 30, "is_active": True},
            )

        sent = req.await_args.kwargs["json"]
        self.assertTrue(sent["enable"])
        self.assertNotIn("is_active", sent)

    async def test_live_usage_refresh_uses_official_admin_action(self):
        req = AsyncMock(return_value={"status": "success"})
        with patch.object(hiddify_api, "_request", new=req):
            ok = await hiddify_api.refresh_user_usage(self.server)

        self.assertTrue(ok)
        self.assertEqual(req.await_args.args[0], "GET")
        self.assertTrue(
            req.await_args.args[1].endswith(
                "/admin-secret/api/v2/admin/update_user_usage/"
            )
        )

    async def test_live_usage_refresh_is_best_effort(self):
        with patch.object(
            hiddify_api,
            "_request",
            new=AsyncMock(side_effect=RuntimeError("legacy panel")),
        ):
            ok = await hiddify_api.refresh_user_usage(self.server)

        self.assertFalse(ok)

    async def test_live_usage_refresh_skips_xnet_and_xui(self):
        req = AsyncMock()
        with patch.object(hiddify_api, "_request", new=req):
            self.assertFalse(
                await hiddify_api.refresh_user_usage(
                    {**self.server, "panel_type": "xnet"}
                )
            )
            self.assertFalse(
                await hiddify_api.refresh_user_usage(
                    {**self.server, "panel_type": "xui"}
                )
            )

        req.assert_not_awaited()

    async def test_backup_prefers_cross_version_backupfile_route(self):
        body = b'{"users": [], "admin_users": []}'
        headers = {"content-type": "application/json"}
        req = AsyncMock(return_value=(body, headers))
        with patch.object(hiddify_api, "_request_bytes", new=req):
            result = await hiddify_api.download_server_backup(self.server)

        self.assertTrue(result["source_url"].endswith("/admin/backup/backupfile"))
        self.assertEqual(result["content"], body)

    async def test_backup_skips_v14_state_json_and_uses_download_route(self):
        state = b'{"current": {"users": 2}, "files": [], "telegram": {}, "directory": ""}'
        good = b'{"users": [], "admin_users": [], "domains": [], "proxies": [], "hconfigs": []}'
        headers = {"content-type": "application/json"}
        urls = []

        async def fake_request_bytes(method, url, server):
            urls.append(url)
            if "/api/v2/admin/backup/download/" in url:
                return good, headers
            return state, headers

        with patch.object(hiddify_api, "_request_bytes", new=fake_request_bytes):
            result = await hiddify_api.download_server_backup(self.server)

        self.assertTrue(result["source_url"].endswith("/api/v2/admin/backup/download/"))
        self.assertEqual(result["content"], good)
        self.assertTrue(any("/admin/backup/backupfile" in u for u in urls))

    async def test_backup_html_fallback_rejects_state_json(self):
        state = b'{"current": {"users": 2}}'
        with patch.object(
            hiddify_api,
            "_request_bytes",
            new=AsyncMock(side_effect=hiddify_api.HiddifyApiError("offline")),
        ), patch.object(
            hiddify_api,
            "_download_server_backup_from_admin_html",
            new=AsyncMock(return_value={
                "filename": "state.json",
                "content": state,
                "source_url": "https://panel.example.com/admin/backup",
            }),
        ):
            with self.assertRaises(hiddify_api.HiddifyApiError) as ctx:
                await hiddify_api.download_server_backup(self.server)

        self.assertIn("admin-html-fallback", str(ctx.exception))
        self.assertIn("state/metadata", str(ctx.exception))

    async def test_unknown_version_patch_uses_cross_version_switch_fields(self):
        version = {"version": "", "major": 0, "source": "unknown", "error": "panel info unavailable"}
        req = AsyncMock(return_value={"uuid": "u-0"})
        with patch.object(
            hiddify_api,
            "get_panel_version",
            new=AsyncMock(return_value=version),
        ), patch.object(hiddify_api, "_request", new=req):
            await hiddify_api.patch_user(self.server, "u-0", {"is_active": True})

        sent = req.await_args.kwargs["json"]
        self.assertIs(sent["enable"], True)
        self.assertIs(sent["is_active"], True)

    async def test_unknown_version_write_error_includes_detection_hint(self):
        version = {"version": "", "major": 0, "source": "unknown", "error": "old reverse proxy"}
        with patch.object(
            hiddify_api,
            "get_panel_version",
            new=AsyncMock(return_value=version),
        ), patch.object(
            hiddify_api,
            "_request",
            new=AsyncMock(side_effect=hiddify_api.HiddifyApiError("HTTP 500: boom")),
        ):
            with self.assertRaises(hiddify_api.HiddifyApiError) as ctx:
                await hiddify_api.patch_user(self.server, "u-0", {"is_active": True})

        self.assertIn("نسخه پنل Hiddify تشخیص داده نشد", str(ctx.exception))
        self.assertIn("old reverse proxy", str(ctx.exception))

    async def test_v14_wrapped_create_response_is_unwrapped(self):
        req = AsyncMock(
            return_value={"user": {"uuid": "u-14", "enable": True, "name": "test"}}
        )
        with patch.object(
            hiddify_api,
            "get_panel_version",
            new=AsyncMock(return_value={"version": "14.0.2", "major": 14}),
        ), patch.object(hiddify_api, "_request", new=req), patch.dict(
            os.environ, {hiddify_api.CREATE_USER_STABILIZE_ENV: "off"}
        ):
            result = await hiddify_api.create_user(
                self.server,
                {
                    "name": "test",
                    "usage_limit_GB": 1,
                    "package_days": 1,
                    "is_active": True,
                },
            )

        self.assertEqual(result["uuid"], "u-14")
        self.assertEqual(result["name"], "test")

    async def test_create_user_cleans_up_on_active_user_limit_during_stabilization(self):
        req = AsyncMock(return_value={"uuid": "u-9"})
        deleted = []

        async def fake_patch(server, uuid, payload):
            raise hiddify_api.HiddifyActiveUserLimitError("active user limit reached")

        async def fake_delete(server, uuid, *args, **kwargs):
            deleted.append(uuid)

        with patch.object(
            hiddify_api,
            "get_panel_version",
            new=AsyncMock(return_value={"version": "14.0.2", "major": 14}),
        ), patch.object(hiddify_api, "_request", new=req), patch.object(
            hiddify_api, "patch_user", new=fake_patch
        ), patch.object(
            hiddify_api, "delete_user", new=fake_delete
        ), patch.dict(
            os.environ, {hiddify_api.CREATE_USER_STABILIZE_ENV: "toggle"}
        ):
            with self.assertRaises(hiddify_api.HiddifyUserStabilizationError) as ctx:
                await hiddify_api.create_user(
                    self.server,
                    {
                        "name": "test",
                        "usage_limit_GB": 1,
                        "package_days": 1,
                        "is_active": True,
                    },
                )

        self.assertEqual(ctx.exception.user_uuid, "u-9")
        self.assertIn("stabilize_update", str(ctx.exception))
        self.assertEqual(deleted, ["u-9"])

    async def test_create_user_raises_when_update_mismatch_is_verified(self):
        deleted = []

        async def fake_patch(server, uuid, payload):
            raise RuntimeError("panel write failed")

        async def fake_delete(server, uuid, *args, **kwargs):
            deleted.append(uuid)

        with patch.object(
            hiddify_api,
            "get_panel_version",
            new=AsyncMock(return_value={"version": "14.0.2", "major": 14}),
        ), patch.object(
            hiddify_api,
            "_request",
            new=AsyncMock(return_value={"uuid": "u-10"}),
        ), patch.object(
            hiddify_api, "patch_user", new=fake_patch
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(return_value={"uuid": "u-10", "enable": False}),
        ), patch.object(
            hiddify_api, "delete_user", new=fake_delete
        ), patch.dict(
            os.environ, {hiddify_api.CREATE_USER_STABILIZE_ENV: "update"}
        ):
            with self.assertRaises(hiddify_api.HiddifyUserStabilizationError) as ctx:
                await hiddify_api.create_user(
                    self.server,
                    {
                        "name": "test",
                        "usage_limit_GB": 1,
                        "package_days": 1,
                        "is_active": True,
                    },
                )

        self.assertEqual(ctx.exception.user_uuid, "u-10")
        self.assertIn("panel state enabled=False", str(ctx.exception))
        self.assertEqual(deleted, [])

    async def test_create_user_returns_data_when_final_state_is_unreadable(self):
        async def fake_patch(server, uuid, payload):
            raise RuntimeError("temporary panel write failure")

        with patch.object(
            hiddify_api,
            "get_panel_version",
            new=AsyncMock(return_value={"version": "14.0.2", "major": 14}),
        ), patch.object(
            hiddify_api,
            "_request",
            new=AsyncMock(return_value={"uuid": "u-11"}),
        ), patch.object(
            hiddify_api, "patch_user", new=fake_patch
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(side_effect=RuntimeError("panel offline")),
        ), patch.dict(
            os.environ, {hiddify_api.CREATE_USER_STABILIZE_ENV: "update"}
        ):
            result = await hiddify_api.create_user(
                self.server,
                {
                    "name": "test",
                    "usage_limit_GB": 1,
                    "package_days": 1,
                    "is_active": True,
                },
            )

        self.assertEqual(result["uuid"], "u-11")

    async def test_create_user_toggle_raises_when_enable_fails_and_stays_disabled(self):
        with patch.object(
            hiddify_api,
            "get_panel_version",
            new=AsyncMock(return_value={"version": "14.0.2", "major": 14}),
        ), patch.object(
            hiddify_api,
            "_request",
            new=AsyncMock(return_value={"uuid": "u-12"}),
        ), patch.object(
            hiddify_api, "patch_user", new=AsyncMock(return_value={"uuid": "u-12"})
        ), patch.object(
            hiddify_api, "disable_user", new=AsyncMock(return_value={"uuid": "u-12"})
        ), patch.object(
            hiddify_api,
            "enable_user",
            new=AsyncMock(side_effect=RuntimeError("enable failed")),
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(return_value={"uuid": "u-12", "enable": False}),
        ), patch.dict(
            os.environ, {hiddify_api.CREATE_USER_STABILIZE_ENV: "toggle"}
        ):
            with self.assertRaises(hiddify_api.HiddifyUserStabilizationError) as ctx:
                await hiddify_api.create_user(
                    self.server,
                    {
                        "name": "test",
                        "usage_limit_GB": 1,
                        "package_days": 1,
                        "is_active": True,
                    },
                )

        self.assertEqual(ctx.exception.user_uuid, "u-12")
        self.assertIn("enable_final", str(ctx.exception))

    async def test_create_user_does_not_force_enable_when_disabled_requested(self):
        patch_calls = []

        async def fake_patch(server, uuid, payload):
            patch_calls.append(dict(payload))
            return {"uuid": "u-13"}

        enable = AsyncMock(return_value={"uuid": "u-13"})
        with patch.object(
            hiddify_api,
            "get_panel_version",
            new=AsyncMock(return_value={"version": "14.0.2", "major": 14}),
        ), patch.object(
            hiddify_api,
            "_request",
            new=AsyncMock(return_value={"uuid": "u-13"}),
        ), patch.object(
            hiddify_api, "patch_user", new=fake_patch
        ), patch.object(
            hiddify_api, "disable_user", new=AsyncMock(return_value={"uuid": "u-13"})
        ), patch.object(
            hiddify_api, "enable_user", new=enable
        ), patch.object(
            hiddify_api,
            "get_user_by_uuid",
            new=AsyncMock(return_value={"uuid": "u-13", "enable": False}),
        ), patch.dict(
            os.environ, {hiddify_api.CREATE_USER_STABILIZE_ENV: "toggle"}
        ):
            result = await hiddify_api.create_user(
                self.server,
                {
                    "name": "test",
                    "usage_limit_GB": 1,
                    "package_days": 1,
                    "is_active": False,
                },
            )

        self.assertEqual(result["uuid"], "u-13")
        self.assertIs(patch_calls[0]["enable"], False)
        enable.assert_not_awaited()

    async def test_enable_user_stops_on_active_user_limit(self):
        calls = []

        async def fake_patch(*args, **kwargs):
            calls.append(args)
            raise hiddify_api.HiddifyActiveUserLimitError("active user limit reached")

        with patch.object(hiddify_api, "patch_user", new=fake_patch):
            with self.assertRaises(hiddify_api.HiddifyActiveUserLimitError):
                await hiddify_api.enable_user(self.server, "u-1")

        self.assertEqual(len(calls), 1)

    async def test_request_maps_active_user_limit_response(self):
        class FakeResp:
            status_code = 400
            text = '{"message": "Active user limit reached: disable another user or raise max active users"}'
            headers = {"content-type": "application/json"}

        class FakeClient:
            async def request(self, *args, **kwargs):
                return FakeResp()

        async def fake_get_client(server, verify):
            return FakeClient()

        with patch.object(hiddify_api, "_get_hiddify_client", new=fake_get_client):
            with self.assertRaises(hiddify_api.HiddifyActiveUserLimitError):
                await hiddify_api._request(
                    "PATCH",
                    "https://panel.example.com/api/v2/admin/user/u-1/",
                    self.server,
                )


if __name__ == "__main__":
    unittest.main()
