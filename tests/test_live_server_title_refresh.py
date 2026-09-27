import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def function_source(rel_path: str, name: str) -> str:
    path = ROOT / rel_path
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            if segment:
                return segment
    raise AssertionError(f"{name} not found in {rel_path}")


class LiveServerTitleRegressionTests(unittest.TestCase):
    def test_agent_profile_resolves_live_server_title(self):
        src = function_source("AgentBot/handlers/subscriptions.py", "_service_detail_text")
        self.assertIn("_resolve_live_server_title(svc)", src)

    def test_admin_agency_profile_resolves_live_server_title(self):
        src = function_source("AdminBot/agencies.py", "_service_detail_text")
        self.assertIn("_live_service_server_title(svc)", src)

    def test_subscription_report_resolves_live_server_title(self):
        src = function_source("Shared/subscription_reports.py", "build_subscription_report_text")
        self.assertIn("_live_server_title(svc)", src)

    def test_successful_agent_renewal_refreshes_stored_titles(self):
        src = function_source("AgentBot/services/subscription_service.py", "renew_subscription")
        self.assertIn("refresh_service_server_titles(service_id)", src)


if __name__ == "__main__":
    unittest.main()
