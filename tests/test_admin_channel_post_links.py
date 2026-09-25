import ast
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse


SOURCE_PATH = Path(__file__).resolve().parents[1] / "AdminBot" / "channel_posts.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
KEYBOARDS_SOURCE = (Path(__file__).resolve().parents[1] / "AdminBot" / "keyboards.py").read_text(encoding="utf-8")
USERBOT_ADMIN_SOURCE = (Path(__file__).resolve().parents[1] / "AdminBot" / "userbot.py").read_text(encoding="utf-8")


def _load_normalizer():
    node = next(
        n for n in TREE.body
        if isinstance(n, ast.FunctionDef) and n.name == "_normalize_button_url"
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {"re": re, "urlparse": urlparse}
    exec(compile(module, str(SOURCE_PATH), "exec"), ns)
    return ns["_normalize_button_url"]


class AdminChannelPostLinkTests(unittest.TestCase):
    def test_accepts_telegram_at_username(self):
        normalize = _load_normalizer()
        self.assertEqual(
            normalize("@user_speedl_bot"),
            "https://t.me/user_speedl_bot",
        )

    def test_accepts_full_telegram_link(self):
        normalize = _load_normalizer()
        self.assertEqual(
            normalize("https://t.me/user_speedl_bot"),
            "https://t.me/user_speedl_bot",
        )

    def test_accepts_tme_without_scheme(self):
        normalize = _load_normalizer()
        self.assertEqual(
            normalize("t.me/user_speedl_bot"),
            "https://t.me/user_speedl_bot",
        )

    def test_accepts_tg_deep_link(self):
        normalize = _load_normalizer()
        self.assertEqual(
            normalize("tg://resolve?domain=user_speedl_bot"),
            "tg://resolve?domain=user_speedl_bot",
        )

    def test_rejects_invalid_telegram_username(self):
        normalize = _load_normalizer()
        self.assertEqual(normalize("@bad-name"), "")

    def test_channel_management_lives_inside_userbot_admin_menu(self):
        self.assertIn(
            'InlineKeyboardButton("📢 مدیریت کانال", callback_data="channelpost:menu")',
            USERBOT_ADMIN_SOURCE,
        )
        self.assertNotIn(
            "[KeyboardButton(BTN_USERBOT), KeyboardButton(BTN_CHANNEL_POSTS)]",
            KEYBOARDS_SOURCE,
        )
        self.assertIn(
            'callback_data=CB + "back_userbot"',
            SOURCE,
        )

    def test_channel_draft_can_be_edited_without_resetting_buttons(self):
        self.assertIn(
            'InlineKeyboardButton("✏️ ویرایش پست", callback_data=CB + "edit"',
            SOURCE,
        )
        self.assertIn('if action == "edit":', SOURCE)
        self.assertIn("🔘 دکمه‌های فعلی پست حفظ می‌شوند.", SOURCE)
        edit_section = SOURCE.split('if action == "edit":', 1)[1].split(
            'if action == "button":', 1
        )[0]
        self.assertNotIn('context.user_data[DRAFT_KEY] =', edit_section)


if __name__ == "__main__":
    unittest.main()
