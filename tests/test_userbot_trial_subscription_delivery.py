import ast
from pathlib import Path
import unittest


SOURCE_PATH = Path(__file__).resolve().parents[1] / "UserBot" / "main.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function_source(name: str) -> str:
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(SOURCE, node)
            if segment:
                return segment
    raise AssertionError(f"{name} not found")


class UserBotTrialDeliveryRegressionTests(unittest.TestCase):
    def test_trial_uses_paid_delivery_helper_for_link_and_qr(self):
        src = _function_source("receipt_handler")
        trial_pos = src.index('if step == "WAIT_TRIAL_SERVICE_NAME":')
        trial_src = src[trial_pos:]
        self.assertIn(
            "await _send_config_and_qr_after_delivery(",
            trial_src,
        )
        self.assertIn(
            "show_sub_link=settings.get(\"show_sub_link\", True)",
            trial_src,
        )

    def test_panel_creation_rollback_prefers_delete_then_disable_fallback(self):
        src = _function_source("_deactivate_created_users")
        self.assertIn("await multi_panel.delete_user(", src)
        self.assertIn("await multi_panel.disable_user(", src)
        self.assertLess(
            src.index("await multi_panel.delete_user("),
            src.index("await multi_panel.disable_user("),
        )

    def test_trial_db_failure_removes_partial_local_and_panel_state(self):
        src = _function_source("receipt_handler")
        trial_pos = src.index('if step == "WAIT_TRIAL_SERVICE_NAME":')
        trial_src = src[trial_pos:]
        marker = 'logger.exception("Failed persisting free trial'
        fail_pos = trial_src.index(marker)
        failure_slice = trial_src[fail_pos:fail_pos + 2600]
        self.assertIn("userbot_db.delete_service", failure_slice)
        self.assertIn("userbot_db.set_free_trial_used(internal_user_id, 0)", failure_slice)
        self.assertIn("await _deactivate_created_users(created_nodes)", failure_slice)


if __name__ == "__main__":
    unittest.main()
