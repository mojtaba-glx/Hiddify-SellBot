import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from AgentBot import database as agentbot_db
from Shared import agent_db, sub_http_server


class PaymentTransitionTests(unittest.TestCase):
    def test_agent_payment_status_requires_expected_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE agent_payments (id INTEGER, agent_id INTEGER, status TEXT, updated_at TEXT)")
            conn.execute("INSERT INTO agent_payments VALUES (1, 7, 'pending', '')")
            conn.commit()
            conn.close()
            with patch.object(agentbot_db, "init_db"), patch.object(
                agentbot_db, "_conn", side_effect=lambda: sqlite3.connect(path)
            ):
                self.assertTrue(agentbot_db.set_payment_status(1, 7, "processing", expected_status="pending"))
                self.assertFalse(agentbot_db.set_payment_status(1, 7, "approved", expected_status="pending"))

    def test_customer_payment_status_requires_expected_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "customer.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE customer_payments (id INTEGER, agent_id INTEGER, status TEXT, updated_at TEXT)")
            conn.execute("INSERT INTO customer_payments VALUES (2, 7, 'pending', '')")
            conn.commit()
            conn.close()
            def connect_with_rows():
                db = sqlite3.connect(path)
                db.row_factory = sqlite3.Row
                return db
            with patch.object(
                agentbot_db, "_customer_conn", side_effect=connect_with_rows
            ):
                self.assertTrue(
                    agentbot_db.update_customer_payment_status(7, 2, "processing", expected_status="pending")
                )
                self.assertFalse(
                    agentbot_db.update_customer_payment_status(7, 2, "approved", expected_status="pending")
                )


class EndpointSecurityTests(unittest.TestCase):
    def test_sms_amount_unknown_currency_does_not_divide_by_ten(self):
        self.assertEqual(sub_http_server._sms_amount_candidates_toman(70799, "unknown"), [70799])

    def test_rate_limit_is_enforced(self):
        with patch.object(sub_http_server, "_dotenv_get", return_value="2"):
            ip = "unit-test-rate-limit"
            self.assertTrue(sub_http_server._allow_request(ip, "get"))
            self.assertTrue(sub_http_server._allow_request(ip, "get"))
            self.assertFalse(sub_http_server._allow_request(ip, "get"))


class DatabaseIntegrityTests(unittest.TestCase):
    def test_agency_connections_enable_foreign_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(agent_db, "DB_PATH", Path(tmp) / "agency.db"):
                conn = agent_db._get_conn()
                try:
                    self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                finally:
                    conn.close()


if __name__ == "__main__":
    unittest.main()
