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

    async def test_backup_prefers_cross_version_backupfile_route(self):
        body = b'{"users": []}'
        headers = {"content-type": "application/json"}
        req = AsyncMock(return_value=(body, headers))
        with patch.object(hiddify_api, "_request_bytes", new=req):
            result = await hiddify_api.download_server_backup(self.server)

        self.assertTrue(result["source_url"].endswith("/admin/backup/backupfile"))
        self.assertEqual(result["content"], body)


if __name__ == "__main__":
    unittest.main()
