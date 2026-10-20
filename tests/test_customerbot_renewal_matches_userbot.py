import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CUSTOMER_RENEW_SOURCE = (
    ROOT / "AgentBot" / "handlers" / "settings_customer_payments.py"
).read_text(encoding="utf-8")
USERBOT_SOURCE = (ROOT / "UserBot" / "main.py").read_text(encoding="utf-8")
AGENT_SERVICE_SOURCE = (
    ROOT / "AgentBot" / "services" / "subscription_service.py"
).read_text(encoding="utf-8")
CUSTOMER_SERVICE_SOURCE = (
    ROOT / "CustomerBot" / "services.py"
).read_text(encoding="utf-8")
ADMIN_USERBOT_SOURCE = (ROOT / "AdminBot" / "userbot.py").read_text(encoding="utf-8")


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
        self.assertNotIn('"is_active": True', src)

    def test_userbot_does_not_retry_active_user_limit(self):
        src = _function_source(USERBOT_SOURCE, "_retry_enable_renewed_user")
        self.assertIn("HiddifyActiveUserLimitError", src)
        self.assertIn("break", src)

    def test_customerbot_renewal_marks_active_only_after_primary_enable(self):
        src = _function_source(
            CUSTOMER_RENEW_SOURCE, "_renew_subscription_from_order"
        )
        self.assertIn("agent_db.set_service_active(service_id, primary_enable_ok)", src)
        self.assertNotIn("agent_db.set_service_active(service_id, True)", src)
        self.assertNotIn('"is_active": True', src)
        self.assertIn("primary_enable_error", src)
        self.assertIn('report_status = "error"', src)

    def test_legacy_customer_renewal_marks_active_only_after_primary_enable(self):
        src = _function_source(CUSTOMER_SERVICE_SOURCE, "renew_service")
        self.assertIn("agent_db.set_service_active(service_id, primary_enable_ok)", src)
        self.assertNotIn("agent_db.set_service_active(service_id, True)", src)

    def test_agent_renewal_reports_primary_activation_failure_as_error(self):
        src = _function_source(AGENT_SERVICE_SOURCE, "renew_subscription")
        self.assertIn("primary_enable_error", src)
        self.assertIn("targets and not primary_enable_ok", src)

    def test_renewal_rollback_does_not_force_activation(self):
        src = _function_source(ADMIN_USERBOT_SOURCE, "_revert_approved_payment")
        self.assertNotIn('"is_active": True', src)

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

    def test_paid_customer_renewal_requires_real_primary_panel_target(self):
        src = _function_source(
            CUSTOMER_RENEW_SOURCE, "_renew_subscription_from_order"
        )
        self.assertIn('raise RuntimeError("panel_targets_not_found")', src)
        self.assertIn('raise RuntimeError("primary_panel_target_not_found")', src)
        self.assertIn("primary_target = next(", src)
        self.assertIn("None,", src)

    def test_legacy_customer_renewal_uses_new_expiry_and_real_primary(self):
        legacy_source = (
            ROOT / "CustomerBot" / "services.py"
        ).read_text(encoding="utf-8")
        src = _function_source(legacy_source, "renew_service")
        self.assertIn("primary_panel_target_not_found", src)
        self.assertIn("agent_db.renew_service(service_id, extra_days=extra_days)", src)
        self.assertIn('new_end = str(updated.get("end_date")', src)
        self.assertIn('patch = {"expire_date": new_end.split(" ")[0]}', src)
        self.assertNotIn("targets[0]", src)

    def test_legacy_customer_renewal_refunds_only_before_primary_commit(self):
        legacy_source = (
            ROOT / "CustomerBot" / "services.py"
        ).read_text(encoding="utf-8")
        src = _function_source(legacy_source, "renew_service")
        primary_commit = src.index("# Primary + local DB are committed.")
        self.assertNotIn("agent_db.refund_wallet(", src[primary_commit:])
        self.assertIn("agent_db.refund_wallet(", src[:primary_commit])
        self.assertIn('"pending_nodes": failed_nodes', src)

    def test_admin_report_shows_purchased_renewal_package(self):
        src = _function_source(
            CUSTOMER_RENEW_SOURCE, "_renew_subscription_from_order"
        )
        self.assertIn("volume_gb=float(extra_gb or 0)", src)
        self.assertIn("days=int(extra_days or 0)", src)
        self.assertNotIn("volume_gb=float(new_usage_limit or 0)", src)

    def test_customerbot_renewal_syncs_central_userbot_runtime(self):
        src = _function_source(
            CUSTOMER_RENEW_SOURCE, "_renew_subscription_from_order"
        )
        self.assertIn(
            "from AgentBot.services.subscription_service import _sync_userbot_runtime_after_agency_renew",
            src,
        )
        self.assertIn("usage_limit=new_usage_limit", src)
        self.assertIn("days_left=new_days_left", src)


if __name__ == "__main__":
    unittest.main()
