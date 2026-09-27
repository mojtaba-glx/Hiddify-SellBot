import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _function_source(name: str) -> str:
    path = ROOT / "AdminBot" / "servers.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            if segment:
                return segment
    raise AssertionError(f"{name} not found")


class AdminServerTitleUiTests(unittest.TestCase):
    def test_management_list_uses_exact_saved_title(self):
        src = _function_source("build_servers_inline_keyboard")
        self.assertNotIn('if "ترکیه" in title', src)
        self.assertNotIn('🇹🇷', src)
        self.assertNotIn('f"لوکیشن', src)
        self.assertIn('s.get("title")', src)

    def test_server_formatter_does_not_invent_location_or_flag(self):
        src = _function_source("_format_server_location_title")
        self.assertNotIn("🇹🇷", src)
        self.assertNotIn("_location", src.lower())
        self.assertIn('str(title or "")', src)

    def test_status_list_does_not_auto_add_country_flag(self):
        src = _function_source("send_server_status")
        self.assertNotIn('if "ترکیه" in title', src)
        self.assertNotIn('🇹🇷', src)
        self.assertNotIn('f"لوکیشن', src)


if __name__ == "__main__":
    unittest.main()
