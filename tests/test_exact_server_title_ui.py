import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            if segment:
                return segment
    raise AssertionError(f"{name} not found in {path}")


class ExactServerTitleUiTests(unittest.TestCase):
    def test_userbot_location_buttons_do_not_auto_add_flag_or_location_prefix(self):
        path = ROOT / "UserBot" / "keyboards.py"
        for name in ("location_keyboard", "trial_location_keyboard"):
            src = _function_source(path, name)
            self.assertNotIn('flag =', src)
            self.assertNotIn('🇩🇪', src)
            self.assertNotIn('🇹🇷', src)
            self.assertNotIn('f"لوکیشن', src)

    def test_customerbot_location_buttons_do_not_auto_add_flag_or_location_prefix(self):
        path = ROOT / "CustomerBot" / "keyboards.py"
        for name in ("location_keyboard", "trial_location_keyboard"):
            src = _function_source(path, name)
            self.assertNotIn('flag =', src)
            self.assertNotIn('🇩🇪', src)
            self.assertNotIn('🇹🇷', src)
            self.assertNotIn('f"لوکیشن', src)

    def test_admin_service_title_formatter_preserves_raw_title(self):
        src = _function_source(ROOT / "AdminBot" / "userbot.py", "_format_server_location_title")
        self.assertNotIn("_location_flag_from_title", src)
        self.assertNotIn("لوکیشن", src.split('"""')[-1])

    def test_admin_agency_title_formatter_preserves_raw_title(self):
        src = _function_source(ROOT / "AdminBot" / "agencies.py", "_server_flag_title")
        self.assertNotIn("_LOCATION_FLAGS", src)
        self.assertIn("str(title or", src)


if __name__ == "__main__":
    unittest.main()
