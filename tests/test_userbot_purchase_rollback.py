import ast
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "UserBot" / "main.py"
SOURCE = PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

def fn_source(name: str) -> str:
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            out = ast.get_source_segment(SOURCE, node)
            if out:
                return out
    raise AssertionError(name)

class UserBotPurchaseRollbackTests(unittest.TestCase):
    def test_rollback_tracks_complete_cleanup(self):
        src = fn_source("_deactivate_created_users")
        self.assertIn("all_safe = True", src)
        self.assertIn("multi_panel.delete_user", src)
        self.assertIn("multi_panel.disable_user", src)
        self.assertIn("return all_safe", src)

    def test_new_purchase_cleanup_precedes_local_balance_restore(self):
        src = fn_source("_process_wallet_purchase")
        self.assertIn("rollback_ok = await _deactivate_created_users(created_nodes)", src)
        self.assertIn("if wallet_charged and rollback_ok:", src)
        self.assertIn("userbot_db.increase_user_wallet(internal_user_id, amount)", src)

    def test_renewal_commit_failure_uses_recovery_path(self):
        src = fn_source("_process_wallet_purchase")
        self.assertIn("Renewal already changed the authoritative panel", src)
        self.assertIn("recovery/manual reconciliation", src)

if __name__ == "__main__":
    unittest.main()
