import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Shared import userbot_db


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReferralForceJoinTests(unittest.TestCase):
    def test_start_persists_referral_before_force_join_can_return(self):
        tree = ast.parse((PROJECT_ROOT / "UserBot" / "main.py").read_text(encoding="utf-8"))
        start_node = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "start"
        )

        referral_calls = []
        force_join_calls = []
        for node in ast.walk(start_node):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_handle_referral_start_payload":
                referral_calls.append(node.lineno)
            if isinstance(func, ast.Name) and func.id == "_enforce_force_join":
                force_join_calls.append(node.lineno)

        self.assertTrue(referral_calls, "start() must process referral payloads")
        self.assertEqual(len(force_join_calls), 1)
        self.assertLess(
            min(referral_calls),
            force_join_calls[0],
            "the referral must be persisted before force-join can stop /start",
        )

    def test_registered_referral_still_rewards_after_force_join_interruption(self):
        with tempfile.TemporaryDirectory(prefix="referral-force-join-") as tmp:
            db_path = Path(tmp) / "userbot.db"
            with patch.object(userbot_db, "DB_PATH", db_path), patch.object(
                userbot_db, "_send_referral_reward_notice_background"
            ):
                userbot_db.init_db()
                userbot_db.set_referral_settings(
                    {
                        "referral_enabled": True,
                        "trial_reward_enabled": True,
                        "trial_reward_amount": 10,
                    }
                )
                inviter_id = userbot_db.upsert_user(10001, "inviter", "Inviter")
                invitee_id = userbot_db.upsert_user(10002, "invitee", "Invitee")
                code = userbot_db.get_or_create_user_referral_code(inviter_id)

                created, status, _ = userbot_db.register_referral(
                    invitee_id, code, f"ref_{code}"
                )
                self.assertTrue(created)
                self.assertEqual(status, "ok")

                first = userbot_db.try_grant_referral_trial_reward(invitee_id)
                duplicate = userbot_db.try_grant_referral_trial_reward(invitee_id)

                self.assertTrue(first and first.get("is_new"))
                self.assertFalse(duplicate and duplicate.get("is_new"))
                conn = sqlite3.connect(db_path)
                try:
                    balance = conn.execute(
                        "SELECT wallet_balance FROM userbot_users WHERE id = ?",
                        (inviter_id,),
                    ).fetchone()[0]
                    reward_count = conn.execute(
                        "SELECT COUNT(*) FROM userbot_referral_rewards WHERE inviter_id = ? AND reward_type = 'trial'",
                        (inviter_id,),
                    ).fetchone()[0]
                finally:
                    conn.close()

                self.assertEqual(balance, 10)
                self.assertEqual(reward_count, 1)


if __name__ == "__main__":
    unittest.main()
