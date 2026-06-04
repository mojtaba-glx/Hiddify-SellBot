import tempfile
import unittest
from pathlib import Path

from Shared import userbot_db


class TestSmsWebhookPayments(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = userbot_db.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        userbot_db.DB_PATH = Path(self._tmpdir.name) / "test.db"
        userbot_db.init_db()

    def tearDown(self):
        userbot_db.DB_PATH = self._orig_db_path
        self._tmpdir.cleanup()

    def _create_pending_payment(self, *, amount: int = 100000, receipt_image: str = ""):
        internal_user_id = userbot_db.upsert_user(123456, "tester", "Test User")
        now = "2026-06-03 12:00:00"
        with userbot_db._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO userbot_payments
                (tx_code, user_id, amount, method, status, receipt_image, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("1234567", internal_user_id, amount, "card", "pending", receipt_image, now, now),
            )
            payment_id = int(cur.lastrowid)
        return internal_user_id, payment_id

    def test_find_pending_payment_by_toman_amount(self):
        _user_id, payment_id = self._create_pending_payment(amount=100000)

        rows = userbot_db.find_pending_card_payments_by_amount(100000, max_age_minutes=60 * 24 * 365)

        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["id"]), payment_id)

    def test_approve_payment_from_sms_updates_wallet_and_meta(self):
        internal_user_id, payment_id = self._create_pending_payment(amount=100000)

        ok, message, updated = userbot_db.approve_pending_card_payment_from_sms(
            payment_id,
            event_id="sms-event-1",
            reference="987654",
            sender="BANK",
            amount_raw=1_000_000,
            currency_raw="rial",
        )

        self.assertTrue(ok, message)
        self.assertEqual(updated["status"], "approved")
        user = userbot_db.get_user_by_id(internal_user_id)
        self.assertEqual(int(user["wallet_balance"]), 100000)
        payment = userbot_db.get_payment_by_id(payment_id)
        self.assertIn("sms_event_id:sms-event-1", payment["receipt_image"])
        self.assertIn("sms_amount_raw:1000000", payment["receipt_image"])

    def test_pending_payment_can_match_previous_unmatched_rial_sms(self):
        userbot_db.record_sms_webhook_event(
            {
                "event_id": "sms-before-receipt",
                "sender": "BANK",
                "amount_raw": 1_000_000,
                "currency_raw": "rial",
                "amount_toman": 100_000,
                "reference": "111222",
                "card_last4": "",
                "body": "واریز 1000000 ریال",
                "status": "no_pending_match",
                "message": "no pending yet",
                "received_at": 1780000000000,
                "device_time": 1780000001000,
            }
        )
        internal_user_id, payment_id = self._create_pending_payment(amount=100000)

        ok, message, updated = userbot_db.try_approve_payment_from_unmatched_sms(
            payment_id,
            max_age_minutes=60 * 24 * 365,
        )

        self.assertTrue(ok, message)
        self.assertEqual(updated["status"], "approved")
        user = userbot_db.get_user_by_id(internal_user_id)
        self.assertEqual(int(user["wallet_balance"]), 100000)
        payment = userbot_db.get_payment_by_id(payment_id)
        self.assertIn("sms_event_id:sms-before-receipt", payment["receipt_image"])

    def test_reused_approved_sms_does_not_auto_approve_new_payment(self):
        first_user_id, first_payment_id = self._create_pending_payment(amount=300000)
        ok, message, updated = userbot_db.approve_pending_card_payment_from_sms(
            first_payment_id,
            event_id="sms-used-once",
            reference="020000631300",
            sender="+989999987641",
            amount_raw=3_000_000,
            currency_raw="rial",
        )
        self.assertTrue(ok, message)
        self.assertEqual(int(userbot_db.get_user_by_id(first_user_id)["wallet_balance"]), 300000)
        userbot_db.record_sms_webhook_event(
            {
                "event_id": "sms-used-once",
                "sender": "+989999987641",
                "amount_raw": 3_000_000,
                "currency_raw": "rial",
                "amount_toman": 300_000,
                "reference": "020000631300",
                "card_last4": "",
                "body": "بلو\nواریز پول\nمجتبی عزیز، 3,000,000 ریال به حساب شما نشست.",
                "status": "approved",
                "matched_payment_id": first_payment_id,
                "message": "approved",
                "received_at": 1780000000000,
                "device_time": 1780000001000,
            }
        )

        inserted, _existing = userbot_db.record_sms_webhook_event(
            {
                "event_id": "sms-reused-copy",
                "sender": "+989999987641",
                "amount_raw": 3_000_000,
                "currency_raw": "rial",
                "amount_toman": 300_000,
                "reference": "020000631300",
                "card_last4": "",
                "body": "بلو\nواریز پول\nمجتبی عزیز، 3,000,000 ریال به حساب شما نشست.",
                "status": "no_pending_match",
                "message": "no pending yet",
                "received_at": 1780000000000,
                "device_time": 1780000001000,
            }
        )
        self.assertTrue(inserted)
        _second_user_id, second_payment_id = self._create_pending_payment(amount=300000)

        auto_ok, auto_message, auto_payment = userbot_db.try_approve_payment_from_unmatched_sms(
            second_payment_id,
            max_age_minutes=60 * 24 * 365,
        )

        self.assertFalse(auto_ok, auto_message)
        self.assertEqual(auto_payment["status"], "pending")
        self.assertIn("قبلاً", auto_message)
        self.assertEqual(userbot_db.get_payment_by_id(second_payment_id)["status"], "pending")
        event_rows = []
        with userbot_db._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT status, matched_payment_id FROM userbot_sms_webhook_events WHERE event_id = ?", ("sms-reused-copy",))
            event_rows = cur.fetchall()
        self.assertEqual(event_rows[0]["status"], "sms_reused")
        self.assertEqual(int(event_rows[0]["matched_payment_id"]), first_payment_id)


if __name__ == "__main__":
    unittest.main()
