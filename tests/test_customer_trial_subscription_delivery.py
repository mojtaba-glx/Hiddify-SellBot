import ast
from pathlib import Path
import unittest


class CustomerTrialDeliveryRegressionTests(unittest.TestCase):
    def _trial_function_source(self) -> str:
        path = Path(__file__).resolve().parents[1] / "CustomerBot" / "handlers" / "callback.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_build_trial_service":
                segment = ast.get_source_segment(source, node)
                self.assertIsNotNone(segment)
                return segment or ""
        self.fail("_build_trial_service was not found")

    def test_trial_uses_same_subscription_delivery_as_paid_purchase(self):
        source = self._trial_function_source()
        self.assertIn(
            "from AgentBot.handlers.settings_customer_payments import _send_subscription_delivery",
            source,
        )
        self.assertIn(
            "await _send_subscription_delivery(context, agent_id, user.id, int(svc[\"id\"]))",
            source,
        )

    def test_trial_persistence_failure_rolls_back_local_and_panel_state(self):
        source = self._trial_function_source()
        self.assertIn("delete_agent_service(", source)
        self.assertIn("await multi_panel.delete_user(", source)
        self.assertIn("customer trial persistence failed", source)

    def test_trial_used_flag_failure_also_rolls_back_service(self):
        source = self._trial_function_source()
        self.assertIn("marked = set_got_free_trial(agent_id, user.id)", source)
        self.assertIn("clear_got_free_trial(agent_id, user.id)", source)
        self.assertIn("customer trial mark used failed", source)
        self.assertIn("delete_agent_service(", source)


    def test_trial_no_longer_has_separate_status_only_delivery(self):
        source = self._trial_function_source()
        self.assertNotIn("subscription_status_keyboard(", source)
        self.assertNotIn("build_subscription_status_text(svc", source)


if __name__ == "__main__":
    unittest.main()
