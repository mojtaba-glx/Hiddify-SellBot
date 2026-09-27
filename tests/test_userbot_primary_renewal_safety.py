import ast
from pathlib import Path
import unittest


SOURCE_PATH = Path(__file__).resolve().parents[1] / "UserBot" / "main.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _fn(name: str) -> str:
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(SOURCE, node)
            if segment:
                return segment
    raise AssertionError(f"{name} not found")


class UserBotPrimaryRenewalSafetyTests(unittest.TestCase):
    def test_legacy_node_mapping_reconstructs_and_orders_primary(self):
        src = _fn("_get_service_targets_for_renew")
        self.assertIn("primary_sid", src)
        self.assertIn("_extract_uuid_from_comment", src)
        self.assertIn("targets.insert(0, (primary_srv, primary_uuid))", src)
        self.assertIn("targets.sort(", src)

    def test_paid_renewal_requires_primary_target(self):
        src = _fn("_apply_service_renewal_on_targets")
        self.assertIn("primary_target = next(", src)
        self.assertIn('raise RuntimeError("سرور اصلی سرویس برای تمدید پیدا نشد.")', src)

    def test_primary_patch_failure_aborts_instead_of_accepting_child_success(self):
        src = _fn("_apply_service_renewal_on_targets")
        self.assertIn('if int(srv.get("id") or 0) == primary_sid:', src)
        self.assertIn('"تمدید روی سرور اصلی انجام نشد:', src)


if __name__ == "__main__":
    unittest.main()
