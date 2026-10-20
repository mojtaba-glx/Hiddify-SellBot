import unittest
from unittest.mock import patch

from AdminBot import nodes as admin_nodes
from AdminBot import servers as admin_servers
from Shared import plans_storage


class ServerDetailPanelInfoTests(unittest.TestCase):
    def _render(self, mode, *, version=14, panel_type="hiddify"):
        server = {
            "id": 1,
            "title": "server",
            "panel_url": "https://panel.example",
            "panel_type": panel_type,
            "version": version,
            "users_limit": 10,
            "priority": 0,
        }
        with patch.object(
            plans_storage,
            "get_plan_display_mode",
            return_value=mode,
        ):
            return admin_servers.build_server_detail_text(
                server,
                users_count_override=0,
            )

    def test_plan_mode_is_shown_instead_of_plan_count(self):
        cases = {
            "dynamic": "پویا",
            "fixed": "ثابت",
            "mixed": "ترکیبی",
        }
        for mode, label in cases.items():
            with self.subTest(mode=mode):
                text = self._render(mode)
                self.assertIn(f"📋 نوع پلن: {label}", text)
                self.assertIn("🧩 نوع پنل: Hiddify", text)
                self.assertNotIn("تعداد پلن", text)

    def test_hiddify_version_is_not_hardcoded(self):
        text = self._render("dynamic", version=14)
        self.assertIn("📦 نسخه: V14", text)
        self.assertNotIn("V11,12", text)

        text = self._render("dynamic", version=None)
        self.assertIn("📦 نسخه: نامشخص", text)
        self.assertNotIn("V11,12", text)

    def test_xui_and_xnet_keep_their_panel_names(self):
        xui_text = self._render("fixed", panel_type="xui")
        self.assertIn("🧩 نوع پنل: X-UI", xui_text)
        self.assertIn("📦 پنل: X-UI", xui_text)

        xnet_text = self._render("fixed", panel_type="xnet")
        self.assertIn("🧩 نوع پنل: X-NET", xnet_text)
        self.assertIn("📦 پنل: X-NET", xnet_text)

    def test_formatter_handles_hiddify_major_versions_11_to_14(self):
        for major in (11, 12, 13, 14):
            with self.subTest(major=major):
                self.assertEqual(
                    admin_servers.hiddify_api.format_panel_version({"major": major}),
                    f"V{major}",
                )
        self.assertEqual(
            admin_servers.hiddify_api.format_panel_version(
                {"version": "v14.0.2", "major": 14}
            ),
            "v14.0.2",
        )


class LiveServerDetailPanelInfoTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_detection_uses_real_panel_version(self):
        server = {
            "id": 2,
            "title": "server",
            "panel_url": "https://panel.example",
            "panel_type": "hiddify",
            "users_limit": 10,
            "priority": 0,
        }
        with (
            patch.object(
                admin_servers.hiddify_api,
                "list_users",
                return_value=[],
            ),
            patch.object(
                admin_servers.hiddify_api,
                "get_panel_version",
                return_value={
                    "version": "v14.0.2",
                    "major": 14,
                    "source": "panel-info",
                    "error": "",
                },
            ),
            patch.object(
                plans_storage,
                "get_plan_display_mode",
                return_value="mixed",
            ),
        ):
            text = await admin_servers.build_server_detail_text_live(server)

        self.assertIn("📦 نسخه: v14.0.2", text)
        self.assertIn("📋 نوع پلن: ترکیبی", text)
        self.assertIn("🧩 نوع پنل: Hiddify", text)
        self.assertNotIn("V11,12", text)


class NodeEditPanelInfoTests(unittest.TestCase):
    def test_node_uses_child_version_and_plan_mode(self):
        node = {"title": "node", "target_server_id": 3}
        child = {
            "id": 3,
            "title": "child",
            "panel_url": "https://child.example",
            "panel_type": "hiddify",
            "version": 13,
        }
        with (
            patch.object(
                admin_nodes.database,
                "get_server_by_id",
                return_value=child,
            ),
            patch.object(
                admin_nodes.plans_storage,
                "get_plan_display_mode",
                return_value="fixed",
            ),
        ):
            text = admin_nodes._build_node_edit_text(
                1,
                node,
                users_count_override=0,
            )

        self.assertIn("📦 نسخه: V13", text)
        self.assertIn("📋 نوع پلن: ثابت", text)
        self.assertIn("🧩 نوع پنل: Hiddify", text)
        self.assertNotIn("V11,12", text)


if __name__ == "__main__":
    unittest.main()
