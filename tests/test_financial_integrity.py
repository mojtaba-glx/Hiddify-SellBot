import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from AgentBot import database as agentbot_db
from Shared import agent_db


class WalletIdempotencyTests(unittest.TestCase):
    def _seed_agency(self, path: Path) -> None:
        with patch.object(agent_db, "DB_PATH", path):
            agent_db.init_db()
            conn = agent_db._get_conn()
            try:
                conn.execute(
                    "INSERT INTO agent_users (id, telegram_id, created_at) VALUES (1, 1001, '')"
                )
                conn.execute(
                    "INSERT INTO agent_customers (id, agent_id, telegram_id, created_at) "
                    "VALUES (1, 1, 2001, '')"
                )
                conn.commit()
            finally:
                conn.close()

    def test_charge_deduct_and_refund_are_each_applied_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agency.db"
            self._seed_agency(path)
            with patch.object(agent_db, "DB_PATH", path):
                agent_db.charge_wallet_once(1, 100_000, "charge:1")
                agent_db.charge_wallet_once(1, 100_000, "charge:1")
                ok1, _ = agent_db.deduct_wallet_once(1, 40_000, "payment:1")
                ok2, _ = agent_db.deduct_wallet_once(1, 40_000, "payment:1")
                agent_db.refund_wallet_once(1, 40_000, "refund:payment:1")
                agent_db.refund_wallet_once(1, 40_000, "refund:payment:1")

                self.assertTrue(ok1)
                self.assertTrue(ok2)
                self.assertEqual(agent_db.get_wallet_balance(1), 100_000)
                conn = agent_db._get_conn()
                try:
                    rows = conn.execute(
                        "SELECT tx_type, COUNT(*) AS c FROM agent_transactions "
                        "GROUP BY tx_type ORDER BY tx_type"
                    ).fetchall()
                    self.assertEqual({r["tx_type"]: r["c"] for r in rows}, {
                        "charge": 1,
                        "purchase": 1,
                        "refund": 1,
                    })
                finally:
                    conn.close()

    def test_service_create_and_renew_operation_keys_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agency.db"
            self._seed_agency(path)
            with patch.object(agent_db, "DB_PATH", path):
                first = agent_db.create_service(
                    agent_id=1,
                    customer_id=1,
                    server_id=10,
                    name="vpn-test",
                    panel_user_uuid="uuid-1",
                    usage_limit=20,
                    days=30,
                    payment_operation_key="create:1",
                )
                second = agent_db.create_service(
                    agent_id=1,
                    customer_id=1,
                    server_id=10,
                    name="vpn-test-duplicate",
                    panel_user_uuid="uuid-2",
                    usage_limit=20,
                    days=30,
                    payment_operation_key="create:1",
                )
                self.assertEqual(first["id"], second["id"])

                self.assertTrue(agent_db.renew_service_with_policy(
                    first["id"], 30, 20, "add", "add", "renew:1"
                ))
                self.assertTrue(agent_db.renew_service_with_policy(
                    first["id"], 30, 20, "add", "add", "renew:1"
                ))
                renewed = agent_db.get_service_by_id(first["id"])
                self.assertEqual(renewed["days_left"], 60)
                self.assertEqual(renewed["usage_limit"], 40)


class PaymentClaimTests(unittest.TestCase):
    def test_agent_payment_claim_has_one_exclusive_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.db"
            with patch.object(agentbot_db, "DB_FILE", path):
                agentbot_db.init_db()
                conn = agentbot_db._conn()
                try:
                    conn.execute(
                        "INSERT INTO agent_payments "
                        "(id, agent_id, amount, status, description, created_at, updated_at) "
                        "VALUES (1, 7, 50000, 'pending', 'شارژ کیف پول نماینده', '', '')"
                    )
                    conn.commit()
                finally:
                    conn.close()

                self.assertTrue(agentbot_db.claim_payment_processing(1, 7, "approval:1"))
                self.assertFalse(agentbot_db.claim_payment_processing(1, 7, "approval:1"))
                self.assertFalse(agentbot_db.claim_payment_processing(1, 7, "approval:other"))
                self.assertTrue(agentbot_db.finish_payment_processing(
                    1, 7, "approval:1", "approved"
                ))
                self.assertEqual(agentbot_db.get_payment_by_id(1)["status"], "approved")

    def test_customer_payment_claim_clears_its_key_on_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "customer.db"
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE customer_payments ("
                "id INTEGER, agent_id INTEGER, status TEXT, processing_key TEXT, updated_at TEXT)"
            )
            conn.execute("INSERT INTO customer_payments VALUES (2, 7, 'pending', '', '')")
            conn.commit()
            conn.close()

            def connect_with_rows():
                db = sqlite3.connect(path)
                db.row_factory = sqlite3.Row
                return db

            with patch.object(agentbot_db, "_customer_conn", side_effect=connect_with_rows):
                self.assertTrue(agentbot_db.claim_customer_payment_processing(7, 2, "attempt:1"))
                self.assertFalse(agentbot_db.claim_customer_payment_processing(7, 2, "attempt:2"))
                self.assertTrue(agentbot_db.finish_customer_payment_processing(
                    7, 2, "attempt:1", "pending"
                ))
                db = connect_with_rows()
                try:
                    row = db.execute(
                        "SELECT status, processing_key FROM customer_payments WHERE id=2"
                    ).fetchone()
                    self.assertEqual(row["status"], "pending")
                    self.assertEqual(row["processing_key"], "")
                finally:
                    db.close()


if __name__ == "__main__":
    unittest.main()
