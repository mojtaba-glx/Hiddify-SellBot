import ast
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_SOURCE_PATH = ROOT / "AgentBot" / "handlers" / "settings_channel.py"
CHANNEL_SOURCE = CHANNEL_SOURCE_PATH.read_text(encoding="utf-8")
MAIN_MENU_SOURCE = (ROOT / "AgentBot" / "handlers" / "main_menu.py").read_text(encoding="utf-8")
KEYBOARDS_SOURCE = (ROOT / "AgentBot" / "keyboards.py").read_text(encoding="utf-8")
MAIN_SOURCE = (ROOT / "AgentBot" / "main.py").read_text(encoding="utf-8")
CONSTANTS_SOURCE = (ROOT / "AgentBot" / "constants.py").read_text(encoding="utf-8")
TREE = ast.parse(CHANNEL_SOURCE)


def _load_normalizer():
    node = next(
        n for n in TREE.body
        if isinstance(n, ast.FunctionDef) and n.name == "_normalize_button_url"
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {"re": re, "urlparse": urlparse}
    exec(compile(module, str(CHANNEL_SOURCE_PATH), "exec"), ns)
    return ns["_normalize_button_url"]


class AgentChannelManagerTests(unittest.TestCase):
    def test_channel_manager_is_exposed_in_representative_bot_settings(self):
        self.assertIn(
            'IButton("📢 مدیریت کانال", callback_data="agbot:set:channel")',
            KEYBOARDS_SOURCE,
        )
        self.assertIn('"channel": settings_channel', MAIN_MENU_SOURCE)

    def test_channel_manager_has_same_core_draft_actions_as_admin(self):
        for label in (
            "➕ ساخت پست جدید",
            "✏️ ویرایش پست",
            "🔘 افزودن دکمه",
            "👁 پیش‌نمایش",
            "🚀 انتشار در کانال",
            "🧹 پاک کردن دکمه‌ها",
            "📝 ویرایش متن / کپشن",
            "🔄 جایگزینی کامل پست",
        ):
            self.assertIn(label, CHANNEL_SOURCE)

    def test_representative_draft_is_bound_to_agent_id(self):
        self.assertIn('"agent_id": int(agent_id or 0)', CHANNEL_SOURCE)
        self.assertIn(
            'int(value.get("agent_id") or 0) != int(agent_id or 0)',
            CHANNEL_SOURCE,
        )

    def test_publish_uses_only_current_agents_customer_bot_and_channel(self):
        self.assertIn("get_active_customer_bot(int(agent_id or 0))", CHANNEL_SOURCE)
        self.assertIn("get_force_join_settings(int(agent_id or 0))", CHANNEL_SOURCE)
        self.assertIn("sender_bot = Bot(token=token)", CHANNEL_SOURCE)
        self.assertIn("_download_media(context.bot, file_id)", CHANNEL_SOURCE)

    def test_accepts_at_username_as_button_url(self):
        normalize = _load_normalizer()
        self.assertEqual(
            normalize("@user_speedl_bot"),
            "https://t.me/user_speedl_bot",
        )
        self.assertEqual(
            normalize("t.me/user_speedl_bot"),
            "https://t.me/user_speedl_bot",
        )
        self.assertEqual(
            normalize("tg://resolve?domain=user_speedl_bot"),
            "tg://resolve?domain=user_speedl_bot",
        )

    def test_text_and_media_edits_remain_independent(self):
        text_state = CHANNEL_SOURCE.split(
            "if state == STATE_CHANNEL_EDIT_TEXT:", 1
        )[1].split("if state == STATE_CHANNEL_EDIT_MEDIA:", 1)[0]
        self.assertIn('draft["text"] = new_text', text_state)
        self.assertNotIn('draft["file_id"]', text_state)
        self.assertNotIn('draft["buttons"]', text_state)

        media_state = CHANNEL_SOURCE.split(
            "if state == STATE_CHANNEL_EDIT_MEDIA:", 1
        )[1].split("if state == STATE_CHANNEL_BUTTON_TEXT:", 1)[0]
        self.assertIn('draft["file_id"] =', media_state)
        self.assertNotIn('draft["text"] =', media_state)
        self.assertNotIn('draft["buttons"]', media_state)

    def test_agentbot_accepts_video_updates_for_channel_posts(self):
        self.assertIn(
            "filters.TEXT | filters.PHOTO | filters.VIDEO",
            MAIN_SOURCE,
        )
        self.assertIn('STATE_CHANNEL_EDIT_MEDIA = "st:channel_edit_media"', CONSTANTS_SOURCE)
        self.assertIn("STATE_CHANNEL_EDIT_MEDIA: settings_channel.handle_text", MAIN_MENU_SOURCE)

    def test_cancel_returns_to_channel_manager_without_dropping_draft(self):
        self.assertIn("state in channel_states", MAIN_MENU_SOURCE)
        self.assertIn("await settings_channel.handle_text(update, context)", MAIN_MENU_SOURCE)
        cancel_section = CHANNEL_SOURCE.split("if plain_text in CANCEL_WORDS:", 1)[1].split(
            "draft = _draft", 1
        )[0]
        self.assertNotIn("clear_draft=True", cancel_section)


if __name__ == "__main__":
    unittest.main()
