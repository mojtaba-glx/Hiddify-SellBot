import os
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from Shared import userbot_db

os.environ.setdefault("USER_BOT_TOKEN", "123456789:TEST_USER_BOT_TOKEN_FOR_UNIT_TESTS")
os.environ.setdefault("ADMIN_ID", "999")
os.environ.setdefault("ADMIN_BOT_TOKEN", "123456789:TEST_ADMIN_BOT_TOKEN_FOR_UNIT_TESTS")

if importlib.util.find_spec("telegram") is None:
    user_main = None
    _IMPORT_ERROR = "python-telegram-bot unavailable"
else:
    try:
        from UserBot import main as user_main
        _IMPORT_ERROR = None
    except BaseException as exc:  # UserBot/main.py may call sys.exit when config is invalid.
        user_main = None
        _IMPORT_ERROR = exc


class _FakeTelegramFile:
    async def download_as_bytearray(self):
        return bytearray(b"fake-receipt")


class _FakeUserBot:
    async def get_file(self, file_id):
        return _FakeTelegramFile()


class _FakeAdminBot:
    def __init__(self):
        self.sent = []
        self.deleted = []
        self.counter = 0

    async def send_photo(self, chat_id, photo, caption, reply_markup=None):
        self.counter += 1
        self.sent.append((chat_id, caption))
        return SimpleNamespace(
            message_id=self.counter,
            chat=SimpleNamespace(id=chat_id),
            photo=[SimpleNamespace(file_id=f"admin-photo-{self.counter}")],
        )

    async def send_message(self, chat_id, text, reply_markup=None):
        self.counter += 1
        self.sent.append((chat_id, text))
        return SimpleNamespace(
            message_id=self.counter,
            chat=SimpleNamespace(id=chat_id),
            photo=[],
        )

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        return None


@unittest.skipIf(user_main is None, f"UserBot dependencies unavailable: {_IMPORT_ERROR}")
class TestCardPaymentAdminReports(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_db_path = userbot_db.DB_PATH
        self._orig_admin_id = user_main.ADMIN_ID
        self._orig_admin_bot_token = user_main.ADMIN_BOT_TOKEN
        self._orig_bot = user_main.Bot
        self._orig_save_receipt = user_main._save_receipt_local_copy
        self._tmpdir = tempfile.TemporaryDirectory()
        userbot_db.DB_PATH = Path(self._tmpdir.name) / "test.db"
        userbot_db.init_db()

        self.admin_bot = _FakeAdminBot()
        user_main.ADMIN_ID = 999
        user_main.ADMIN_BOT_TOKEN = "admin-token"
        user_main.Bot = lambda token: self.admin_bot

        async def fake_save_receipt(context, photo_file_id, telegram_id):
            return "", f"code-{photo_file_id}"

        user_main._save_receipt_local_copy = fake_save_receipt
        self.update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123456, username="tester", full_name="Test User")
        )
        self.context = SimpleNamespace(bot=_FakeUserBot())

    def tearDown(self):
        userbot_db.DB_PATH = self._orig_db_path
        user_main.ADMIN_ID = self._orig_admin_id
        user_main.ADMIN_BOT_TOKEN = self._orig_admin_bot_token
        user_main.Bot = self._orig_bot
        user_main._save_receipt_local_copy = self._orig_save_receipt
        self._tmpdir.cleanup()

    async def test_back_to_back_distinct_receipts_create_distinct_admin_reports(self):
        result1 = await user_main._finalize_pending_card_payment(
            update=self.update,
            context=self.context,
            user_id=123456,
            amount=75573,
            photo_file_id="receipt-photo-1",
            flow="wallet_topup",
            extra_meta={"pay_flow": "wallet_topup", "base_amount": 75000, "tx_marker": 573},
        )
        result2 = await user_main._finalize_pending_card_payment(
            update=self.update,
            context=self.context,
            user_id=123456,
            amount=75573,
            photo_file_id="receipt-photo-2",
            flow="wallet_topup",
            extra_meta={"pay_flow": "wallet_topup", "base_amount": 75000, "tx_marker": 573},
        )

        self.assertEqual(result1, (False, "pending"))
        self.assertEqual(result2, (False, "pending"))
        self.assertEqual(len(self.admin_bot.sent), 2)
        with userbot_db._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM userbot_payments WHERE method='card' AND status='pending'")
            self.assertEqual(int(cur.fetchone()["c"]), 2)

    async def test_duplicate_same_pending_receipt_does_not_resend_admin_report(self):
        result1 = await user_main._finalize_pending_card_payment(
            update=self.update,
            context=self.context,
            user_id=123456,
            amount=75573,
            photo_file_id="same-receipt-photo",
            flow="wallet_topup",
            extra_meta={"pay_flow": "wallet_topup", "base_amount": 75000, "tx_marker": 573},
        )
        result2 = await user_main._finalize_pending_card_payment(
            update=self.update,
            context=self.context,
            user_id=123456,
            amount=75573,
            photo_file_id="same-receipt-photo",
            flow="wallet_topup",
            extra_meta={"pay_flow": "wallet_topup", "base_amount": 75000, "tx_marker": 573},
        )

        self.assertEqual(result1, (False, "pending"))
        self.assertEqual(result2, (False, "pending"))
        self.assertEqual(len(self.admin_bot.sent), 1)
        with userbot_db._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM userbot_payments WHERE method='card'")
            self.assertEqual(int(cur.fetchone()["c"]), 1)


if __name__ == "__main__":
    unittest.main()
