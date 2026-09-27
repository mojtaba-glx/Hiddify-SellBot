import ast
from pathlib import Path
import unittest


class AgentCustomerServiceGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = (
            Path(__file__).resolve().parents[1]
            / "AgentBot"
            / "handlers"
            / "settings_users.py"
        )
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_service_list_uses_three_button_rows(self):
        self.assertIn("if len(current_row) == 3:", self.source)
        self.assertIn("rows.append(current_row)", self.source)

    def test_service_status_marker_depends_on_usage_not_trial(self):
        self.assertIn('elif usage_current > 0:', self.source)
        self.assertIn('status_emoji = "🟢"', self.source)
        self.assertIn('status_emoji = "🟡"', self.source)
        self.assertIn('status_emoji = "🔴"', self.source)

    def test_trial_is_separate_marker(self):
        self.assertIn('trial_mark = "🔥" if int(s.get("is_trial", 0) or 0) == 1 else ""', self.source)
        self.assertNotIn('emoji = "🟡" if int(s.get("is_trial"', self.source)


if __name__ == "__main__":
    unittest.main()
