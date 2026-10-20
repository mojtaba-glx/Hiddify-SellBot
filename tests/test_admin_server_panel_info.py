import unittest
from pathlib import Path
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


class ServerListPanelInfoTests(unittest.TestCase):
    def test_list_button_shows_panel_version_and_plan_mode(self):
        server = {
            "id": 1,
            "title": "لوکیشن هلند",
            "panel_type": "hiddify",
            "version": 14,
        }
        with (
            patch.object(
                admin_servers.database,
                "get_servers",
                return_value=[server],
            ),
            patch.object(
                admin_servers,
                "_get_child_server_ids",
                return_value=set(),
            ),
            patch.object(
                plans_storage,
                "get_plan_display_mode",
                return_value="dynamic",
            ),
        ):
            keyboard = admin_servers.build_servers_inline_keyboard()

        self.assertEqual(
            keyboard.inline_keyboard[0][0].text,
            "لوکیشن هلند | Hiddify V14 | پویا",
        )

    def test_list_button_labels_xui_and_xnet(self):
        for panel_type, label in (("xui", "X-UI"), ("xnet", "X-NET")):
            with self.subTest(panel_type=panel_type):
                server = {
                    "id": 2,
                    "title": "server",
                    "panel_type": panel_type,
                }
                with patch.object(
                    plans_storage,
                    "get_plan_display_mode",
                    return_value="fixed",
                ):
                    text = admin_servers._server_list_button_text(server, 2)
                self.assertIn(f"{label} | ثابت", text)

    def test_list_button_respects_telegram_text_limit(self):
        server = {
            "id": 3,
            "title": "x" * 80,
            "panel_type": "hiddify",
            "version": 14,
        }
        with patch.object(
            plans_storage,
            "get_plan_display_mode",
            return_value="mixed",
        ):
            text = admin_servers._server_list_button_text(server, 3)

        self.assertLessEqual(len(text), 64)
        self.assertIn("Hiddify V14 | ترکیبی", text)

    def test_readme_lists_v14_support(self):
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Hiddify Manager v11 / v12 / v13 / v14", readme)


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
