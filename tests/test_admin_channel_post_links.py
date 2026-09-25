import ast
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse


SOURCE_PATH = Path(__file__).resolve().parents[1] / "AdminBot" / "channel_posts.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


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


if __name__ == "__main__":
    unittest.main()
