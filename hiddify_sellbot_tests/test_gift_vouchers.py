import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from Shared import userbot_db
from AdminBot import userbot


class TestGiftVouchers(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = userbot_db.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        userbot_db.DB_PATH = Path(self._tmpdir.name) / "test.db"
        userbot_db.init_db()

    def tearDown(self):
        userbot_db.DB_PATH = self._orig_db_path
        self._tmpdir.cleanup()

    def test_deactivate_unusable_vouchers_disables_expired_and_full(self):
        expired_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        ).strftime("%Y-%m-%d %H:%M:%S")
        userbot_db.upsert_zarin_voucher(
            "OLDGIFT",
            50000,
            max_uses=10,
            expires_at=expired_at,
            is_active=1,
        )
        userbot_db.upsert_zarin_voucher("FULLGIFT", 50000, max_uses=1, is_active=1)
        with userbot_db._get_conn() as conn:
            conn.execute("UPDATE userbot_zarin_vouchers SET used_count = 1 WHERE code = 'FULLGIFT'")
            conn.commit()

        changed = userbot_db.deactivate_unusable_zarin_vouchers()

        self.assertEqual(changed, 2)
        self.assertEqual(int(userbot_db.get_zarin_voucher("OLDGIFT")["is_active"]), 0)
        self.assertEqual(int(userbot_db.get_zarin_voucher("FULLGIFT")["is_active"]), 0)

    def test_redemptions_report_rows_include_wallet_balance(self):
        user_id = userbot_db.upsert_user(123456789, "tester", "Test User")
        userbot_db.upsert_zarin_voucher("GIFT100", 100000, max_uses=3, is_active=1)

        ok, _message, amount = userbot_db.redeem_zarin_voucher("GIFT100", user_id)
        rows = userbot_db.list_zarin_voucher_redemptions(code="GIFT100")

        self.assertTrue(ok)
        self.assertEqual(amount, 100000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["telegram_id"]), 123456789)
        self.assertEqual(int(rows[0]["wallet_balance"]), 100000)

    def test_redemption_card_is_multiline_and_readable(self):
        card = userbot._format_gift_redemption_card(
            1,
            {
                "code": "WELCOME-GS5VGQ",
                "username": "mojtaba_glx",
                "telegram_id": 407882018,
                "amount_toman": 30000,
                "wallet_balance": 30000,
                "redeemed_at": "2026-06-08 03:46:02",
            },
        )

        self.assertIn("#1  🏷 WELCOME-GS5VGQ", card)
        self.assertIn("👤 کاربر: @mojtaba_glx", card)
        self.assertIn("🎁 هدیه: 30,000 تومان", card)
        self.assertNotIn(" | ", card)


if __name__ == "__main__":
    unittest.main()
