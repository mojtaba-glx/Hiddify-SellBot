import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CUSTOMER_RENEW_SOURCE = (
    ROOT / "AgentBot" / "handlers" / "settings_customer_payments.py"
).read_text(encoding="utf-8")
USERBOT_SOURCE = (ROOT / "UserBot" / "main.py").read_text(encoding="utf-8")


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    lines = source.splitlines()
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


class CustomerBotRenewalMatchesUserBotTests(unittest.TestCase):
    def test_userbot_renewal_starts_fresh_usage_period(self):
        src = _function_source(USERBOT_SOURCE, "_build_renew_patch_payload")
        self.assertIn('"current_usage_GB": 0', src)
        self.assertIn("remaining_gb = max(usage_limit_old - usage_current, 0.0)", src)
        self.assertIn("package_gb + remaining_gb", src)

    def test_customerbot_renewal_uses_remaining_volume_not_old_total(self):
        src = _function_source(
            CUSTOMER_RENEW_SOURCE, "_renew_subscription_from_order"
        )
        self.assertIn(
            "remaining_gb = max(old_usage_limit - old_usage_current, 0.0)",
            src,
        )
        self.assertIn("new_usage_limit = remaining_gb + extra_gb", src)
        self.assertIn('"current_usage_GB": 0', src)
        self.assertIn('"last_reset_time": now.strftime', src)
        self.assertIn("reset_usage=True", src)

    def test_admin_report_shows_purchased_renewal_package(self):
        src = _function_source(
            CUSTOMER_RENEW_SOURCE, "_renew_subscription_from_order"
        )
        self.assertIn("volume_gb=float(extra_gb or 0)", src)
        self.assertIn("days=int(extra_days or 0)", src)
        self.assertNotIn("volume_gb=float(new_usage_limit or 0)", src)


if __name__ == "__main__":
    unittest.main()
