import os
import unittest

from Shared import hiddify_api


class TestHiddifyCreateUserStabilization(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        os.environ.pop("HIDDIFY_CREATE_USER_STABILIZE_MODE", None)

    async def _run_create_user(self):
        server = {
            "panel_url": "https://panel.example.com",
            "admin_proxy_path": "admin-path",
            "admin_uuid": "admin-key",
        }
        payload = {
            "uuid": "11111111-1111-1111-1111-111111111111",
            "name": "test-user",
            "usage_limit_GB": 10,
            "package_days": 30,
            "is_active": True,
        }
        calls = []

        async def fake_request(method, url, server, **kwargs):
            calls.append((method, kwargs.get("json") or {}))
            if method == "POST":
                return {"uuid": payload["uuid"], "name": payload["name"]}
            return {"uuid": payload["uuid"], **(kwargs.get("json") or {})}

        original_request = hiddify_api._request
        hiddify_api._request = fake_request
        try:
            await hiddify_api.create_user(server, payload)
        finally:
            hiddify_api._request = original_request
        return calls

    async def test_toggle_mode_updates_then_refreshes_activation(self):
        os.environ["HIDDIFY_CREATE_USER_STABILIZE_MODE"] = "toggle"

        calls = await self._run_create_user()

        self.assertEqual([method for method, _ in calls], ["POST", "PATCH", "PATCH", "PATCH"])
        self.assertTrue(calls[1][1]["is_active"])
        self.assertFalse(calls[2][1]["is_active"])
        self.assertTrue(calls[3][1]["is_active"])
        self.assertEqual(calls[3][1]["mode"], "no_reset")

    async def test_update_mode_does_not_toggle_activation(self):
        os.environ["HIDDIFY_CREATE_USER_STABILIZE_MODE"] = "update"

        calls = await self._run_create_user()

        self.assertEqual([method for method, _ in calls], ["POST", "PATCH"])
        self.assertTrue(calls[1][1]["is_active"])

    async def test_off_mode_only_creates_user(self):
        os.environ["HIDDIFY_CREATE_USER_STABILIZE_MODE"] = "off"

        calls = await self._run_create_user()

        self.assertEqual([method for method, _ in calls], ["POST"])


if __name__ == "__main__":
    unittest.main()
