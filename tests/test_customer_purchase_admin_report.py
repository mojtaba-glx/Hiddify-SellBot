import ast
from pathlib import Path
import unittest


class CustomerPurchaseAdminReportRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = (
            Path(__file__).resolve().parents[1]
            / "AgentBot"
            / "handlers"
            / "settings_customer_payments.py"
        )
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    @classmethod
    def _function_source(cls, name: str) -> str:
        for node in cls.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                segment = ast.get_source_segment(cls.source, node)
                if segment:
                    return segment
        raise AssertionError(f"{name} was not found")

    def test_purchase_creation_reports_to_admin(self):
        source = self._function_source("_create_subscription_from_order")
        self.assertIn("await _report_customer_purchase_to_admin(", source)

    def test_report_uses_central_delivery_report_and_partial_status(self):
        source = self._function_source("_report_customer_purchase_to_admin")
        self.assertIn("notify_admin_delivery_report", source)
        self.assertIn('action_title="خرید سرویس مشتری"', source)
        self.assertIn('status="partial" if pending_servers else "success"', source)
        self.assertIn("sync_primary_server_id=", source)
        self.assertIn("sale_amount=", source)
        self.assertIn("wholesale_amount=", source)

    def test_customer_renewal_report_includes_sale_and_wholesale_amounts(self):
        source = self._function_source("_renew_subscription_from_order")
        self.assertIn('action_title="تمدید سرویس مشتری"', source)
        self.assertIn("sale_amount=sale_amount", source)
        self.assertIn("wholesale_amount=wholesale_amount", source)

    def test_admin_report_has_separate_customer_sale_and_wholesale_labels(self):
        report_path = Path(__file__).resolve().parents[1] / "Shared" / "admin_reports.py"
        source = report_path.read_text(encoding="utf-8")
        self.assertIn("قیمت فروش به مشتری", source)
        self.assertIn("هزینه عمده نماینده", source)

    def test_manual_and_sms_purchase_paths_share_create_function(self):
        manual = self._function_source("_approve_payment")
        sms = self._function_source("_auto_approve_from_sms_webhook")
        self.assertIn("_create_subscription_from_order(", manual)
        self.assertIn("_create_subscription_from_order(", sms)


if __name__ == "__main__":
    unittest.main()
