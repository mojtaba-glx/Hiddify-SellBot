import os
import unittest
from unittest.mock import patch

from AdminBot import servers


class TestAdminManagedSubLinks(unittest.TestCase):
    def tearDown(self):
        for key in (
            "SUB_SERVICE_BASE_URL",
            "SUB_SERVER_PUBLIC_HOST",
            "SUB_SERVER_PUBLIC_SCHEME",
            "SUB_SERVER_PUBLIC_PORT",
        ):
            os.environ.pop(key, None)

    def test_links_use_configured_managed_sub_base(self):
        server = {"panel_url": "https://panel.example.com", "user_proxy_path": "u"}
        user_uuid = "a24674c6-0391-42bd-9d1d-b153a3f609e0"

        with (
            patch.object(servers.userbot_db, "get_managed_sub_base_url", return_value="https://sell.example.com"),
            patch.object(servers.userbot_db, "get_service_owner_by_panel_uuid", return_value={"service_id": 12}),
        ):
            text_link, b64_link, owner = servers._build_admin_managed_sub_links(server, user_uuid)

        self.assertEqual(owner["service_id"], 12)
        self.assertEqual(text_link, f"https://sell.example.com/sub/{user_uuid}/all.txt")
        self.assertEqual(b64_link, f"https://sell.example.com/sub/{user_uuid}/all.txt?base64=1")

    def test_links_fallback_to_server_public_origin_without_hidybot_path(self):
        server = {
            "panel_url": "https://panel.example.com",
            "user_proxy_path": "user-path",
            "domains": [{"domain": "usser.example.com", "title": "user"}],
        }
        user_uuid = "a24674c6-0391-42bd-9d1d-b153a3f609e0"

        with (
            patch.object(servers.userbot_db, "get_managed_sub_base_url", return_value=""),
            patch.object(servers.userbot_db, "get_service_owner_by_panel_uuid", return_value={"service_id": 12}),
        ):
            text_link, b64_link, _owner = servers._build_admin_managed_sub_links(server, user_uuid)

        self.assertEqual(text_link, f"https://usser.example.com/sub/{user_uuid}/all.txt")
        self.assertEqual(b64_link, f"https://usser.example.com/sub/{user_uuid}/all.txt?base64=1")
        self.assertNotIn("hidybot.txt", text_link)
        self.assertNotIn("user-path", text_link)

    def test_links_are_empty_when_subscription_is_not_connected_to_userbot(self):
        server = {"panel_url": "https://panel.example.com", "user_proxy_path": "u"}
        user_uuid = "a24674c6-0391-42bd-9d1d-b153a3f609e0"

        with (
            patch.object(servers.userbot_db, "get_managed_sub_base_url", return_value="https://sell.example.com"),
            patch.object(servers.userbot_db, "get_service_owner_by_panel_uuid", return_value=None),
        ):
            text_link, b64_link, owner = servers._build_admin_managed_sub_links(server, user_uuid)

        self.assertEqual(text_link, "")
        self.assertEqual(b64_link, "")
        self.assertIsNone(owner)


if __name__ == "__main__":
    unittest.main()
