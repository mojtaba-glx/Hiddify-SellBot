import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from AgentBot.services import subscription_service
from AgentBot.services.subscription_service import InvalidRenewalError
from Shared import agent_db


class RenewalPolicyGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = agent_db.DB_PATH
        self.old_initialized = agent_db._db_initialized
        self.old_init_path = agent_db._init_db_path
        agent_db.DB_PATH = Path(self.tmp.name) / "agency.db"
        agent_db._db_initialized = False
        agent_db._init_db_path = ""
        agent_db.init_db()

        agent_id = agent_db.upsert_agent(1001, username="agent")
        svc = agent_db.create_service(
            agent_id=agent_id,
            customer_id=0,
            server_id=1,
            server_title="Test",
            name="zero-day-test",
            panel_user_uuid="uuid-zero-day",
            usage_limit=10,
            days=30,
        )
        self.service_id = int(svc["id"])
        self.before = agent_db.get_service_by_id(self.service_id)

    def tearDown(self):
        agent_db.DB_PATH = self.old_path
        agent_db._db_initialized = self.old_initialized
        agent_db._init_db_path = self.old_init_path
        self.tmp.cleanup()

    def _assert_unchanged(self):
        after = agent_db.get_service_by_id(self.service_id)
        for key in ("days_left", "usage_limit", "usage_current", "start_date", "end_date", "expired_at", "is_active"):
            self.assertEqual(after[key], self.before[key], key)

    def test_zero_and_negative_days_cannot_set_end_date_to_now(self):
        for days in (0, -30):
            with self.subTest(days=days):
                self.assertFalse(
                    agent_db.renew_service_with_policy(
                        self.service_id, days, 10, "reset", "reset"
                    )
                )
                self._assert_unchanged()

    def test_negative_days_rejected_even_in_add_mode(self):
        self.assertFalse(
            agent_db.renew_service_with_policy(
                self.service_id, -5, 10, "add", "add"
            )
        )
        self._assert_unchanged()

    def test_reset_zero_volume_is_rejected(self):
        self.assertFalse(
            agent_db.renew_service_with_policy(
                self.service_id, 30, 0, "reset", "reset"
            )
        )
        self._assert_unchanged()

    def test_valid_renewal_still_succeeds(self):
        self.assertTrue(
            agent_db.renew_service_with_policy(
                self.service_id, 60, 20, "reset", "reset"
            )
        )
        after = agent_db.get_service_by_id(self.service_id)
        self.assertEqual(int(after["days_left"]), 60)
        self.assertAlmostEqual(float(after["usage_limit"]), 20.0)
        self.assertAlmostEqual(float(after["usage_current"]), 0.0)
        self.assertEqual(str(after["expired_at"] or ""), "")
        now_text = datetime.now(timezone.utc).replace(tzinfo=None).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self.assertGreater(str(after["end_date"]), now_text)


class RenewSubscriptionValidationTests(unittest.IsolatedAsyncioTestCase):
    def _service(self):
        return {
            "id": 7,
            "agent_id": 3,
            "customer_id": 0,
            "server_id": 1,
            "name": "zero-day-test",
        }

    async def test_invalid_renewal_is_rejected_before_wallet_deduction(self):
        cases = ((0, 10), (-1, 10), (30, 0), (30, -1), ("bad", 10), (30, "bad"))
        for days, gb in cases:
            with self.subTest(days=days, gb=gb):
                with patch.object(
                    subscription_service.agent_db,
                    "get_service_by_id",
                    return_value=self._service(),
                ), patch.object(
                    subscription_service.agent_db,
                    "deduct_wallet",
                ) as deduct_wallet:
                    with self.assertRaises(InvalidRenewalError):
                        await subscription_service.renew_subscription(
                            3, 7, days, extra_gb=gb
                        )
                    deduct_wallet.assert_not_called()


if __name__ == "__main__":
    unittest.main()
