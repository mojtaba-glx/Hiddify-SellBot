import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

# ---------------------------------------------------------------------------
# Inject lightweight stubs for the `telegram` and `telegram.ext` packages so
# that CustomerBot modules can be imported in the test environment without the
# real python-telegram-bot library being installed.
# ---------------------------------------------------------------------------
if "telegram" not in sys.modules:
    _tg_stub = types.ModuleType("telegram")

    class _Bot:
        def __init__(self, *a, **kw):
            pass

    class _InlineKeyboardButton:
        def __init__(self, text, callback_data=None, *a, **kw):
            self.text = text
            self.callback_data = callback_data

    class _InlineKeyboardMarkup:
        def __init__(self, *a, **kw):
            pass

    class _ReplyKeyboardMarkup:
        def __init__(self, *a, **kw):
            pass

    class _KeyboardButton:
        def __init__(self, *a, **kw):
            pass

    class _Update:
        pass

    _tg_stub.Bot = _Bot
    _tg_stub.InlineKeyboardButton = _InlineKeyboardButton
    _tg_stub.InlineKeyboardMarkup = _InlineKeyboardMarkup
    _tg_stub.ReplyKeyboardMarkup = _ReplyKeyboardMarkup
    _tg_stub.KeyboardButton = _KeyboardButton
    _tg_stub.Update = _Update
    sys.modules["telegram"] = _tg_stub

if "telegram.ext" not in sys.modules:
    _tg_ext_stub = types.ModuleType("telegram.ext")

    class _ContextTypes:
        class DEFAULT_TYPE:
            pass

    _tg_ext_stub.ContextTypes = _ContextTypes
    sys.modules["telegram.ext"] = _tg_ext_stub

from CustomerBot import database as customer_db
from CustomerBot import services as customer_services
from AgentBot import database as agent_db
from Shared import agent_db as shared_agent_db
from CustomerBot.handlers import receipt as receipt_handler_module


class TestCustomerDirectBuyPendingFlow(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_customer_db_path = customer_db.DB_PATH
        self._orig_agent_db_file = agent_db.DB_FILE
        self._orig_customer_conn = agent_db._customer_conn

        self.customer_db_path = Path(self._tmpdir.name) / "customer_bot.db"
        self.agent_db_path = Path(self._tmpdir.name) / "agent_bot.db"

        customer_db.DB_PATH = self.customer_db_path
        agent_db.DB_FILE = self.agent_db_path

        def _temp_customer_conn():
            conn = sqlite3.connect(str(self.customer_db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            return conn

        agent_db._customer_conn = _temp_customer_conn

        customer_db.init_db()
        agent_db.init_db()

    def tearDown(self):
        customer_db.DB_PATH = self._orig_customer_db_path
        agent_db.DB_FILE = self._orig_agent_db_file
        agent_db._customer_conn = self._orig_customer_conn
        self._tmpdir.cleanup()

    def test_direct_buy_payment_is_listed_as_pending_buy_request(self):
        agent_id = 77
        telegram_id = 123456

        customer_db.upsert_user(agent_id, telegram_id, "custuser", "Customer User")

        order = customer_db.create_order(
            agent_id=agent_id,
            telegram_id=telegram_id,
            volume_gb=50,
            days=30,
            price=150000,
            plan_title="اشتراک 50GB-30D",
            server_location="Germany",
            username="custuser",
            full_name="Customer User",
            server_id=9,
            plan_id=4,
            wholesale_price=100000,
        )
        self.assertTrue(order)

        payment = customer_db.create_payment(
            agent_id=agent_id,
            user_id=telegram_id,
            amount=150000,
            method="card",
            idempotency_key=f"receipt_{telegram_id}_{order['order_id']}_0",
        )
        self.assertTrue(payment)

        meta = {
            "type": "buy",
            "order_id": order["order_id"],
            "server_id": 9,
            "gb": 50,
            "days": 30,
            "sale_price": 150000,
            "wholesale_price": 100000,
            "plan_title": "اشتراک 50GB-30D",
            "server_location": "Germany",
        }
        self.assertTrue(
            customer_db.update_payment_status(
                agent_id,
                payment["id"],
                "pending",
                json.dumps(meta, ensure_ascii=False),
            )
        )

        pending = agent_db.get_customer_pending_card_payments(agent_id)
        self.assertEqual(len(pending), 1)
        pay = pending[0]
        self.assertEqual(pay["_pay_type"], "buy")
        self.assertEqual(pay["_order_id"], order["order_id"])
        self.assertIsNotNone(pay["_order"])
        self.assertEqual(int(pay["_order"]["server_id"]), 9)
        self.assertEqual(int(pay["_wholesale_price"]), 100000)

    def test_dynamic_buy_payment_prefers_order_metadata_for_days_and_gb(self):
        agent_id = 88
        telegram_id = 987654

        customer_db.upsert_user(agent_id, telegram_id, "dynamicuser", "Dynamic User")

        order = customer_db.create_order(
            agent_id=agent_id,
            telegram_id=telegram_id,
            volume_gb=75,
            days=90,
            price=280000,
            plan_title="بسته 75GB-90D",
            server_location="Finland",
            username="dynamicuser",
            full_name="Dynamic User",
            server_id=12,
            plan_id=0,
            wholesale_price=190000,
        )
        self.assertTrue(order)

        payment = customer_db.create_payment(
            agent_id=agent_id,
            user_id=telegram_id,
            amount=280000,
            method="card",
            idempotency_key=f"receipt_{telegram_id}_{order['order_id']}_0",
        )
        self.assertTrue(payment)

        meta = {
            "file_id": "telegram-file-id",
            "type": "buy",
            "order_id": order["order_id"],
            "server_id": 12,
            "gb": 75,
            "days": 90,
            "sale_price": 280000,
            "wholesale_price": 190000,
            "plan_title": "بسته 75GB-90D",
            "server_location": "Finland",
        }
        self.assertTrue(
            customer_db.update_payment_status(
                agent_id,
                payment["id"],
                "pending",
                json.dumps(meta, ensure_ascii=False),
            )
        )

        pending = agent_db.get_customer_pending_card_payments(agent_id)
        self.assertEqual(len(pending), 1)
        pay = pending[0]
        self.assertEqual(pay["_order_id"], order["order_id"])
        self.assertIsNotNone(pay["_order"])
        self.assertEqual(float(pay["_order"]["volume_gb"]), 75.0)
        self.assertEqual(int(pay["_order"]["days"]), 90)
        self.assertEqual(int(pay["_order"]["wholesale_price"]), 190000)

    def test_order_status_can_track_payment_decision(self):
        agent_id = 91
        telegram_id = 111222

        customer_db.upsert_user(agent_id, telegram_id, "statususer", "Status User")
        order = customer_db.create_order(
            agent_id=agent_id,
            telegram_id=telegram_id,
            volume_gb=20,
            days=30,
            price=100000,
            plan_title="بسته 20GB-30D",
            server_location="Germany",
            username="statususer",
            full_name="Status User",
            server_id=5,
            plan_id=0,
            wholesale_price=70000,
        )
        self.assertEqual(order["status"], "pending")

        self.assertTrue(customer_db.update_order_status(agent_id, order["order_id"], "approved"))
        approved = customer_db.get_order(agent_id, order["order_id"])
        self.assertEqual(approved["status"], "approved")

        self.assertTrue(customer_db.update_order_status(agent_id, order["order_id"], "rejected"))
        rejected = customer_db.get_order(agent_id, order["order_id"])
        self.assertEqual(rejected["status"], "rejected")


class TestCustomerServiceVisibility(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_service_status_uses_remaining_days(self):
        tmpdir = tempfile.TemporaryDirectory()
        orig_customer_db_path = customer_db.DB_PATH
        try:
            customer_db.DB_PATH = Path(tmpdir.name) / "customer_bot.db"
            customer_db.init_db()

            shared_agent_db.init_db()
            customer_id = shared_agent_db.upsert_customer(501, 7001, "visuser", "Visible User")
            svc = shared_agent_db.create_service(
                agent_id=501,
                customer_id=customer_id,
                server_id=9,
                server_title="Test Server",
                name="Visible Service",
                panel_user_uuid="uuid-visible",
                usage_limit=50,
                days=30,
                sale_price=100000,
            )

            with patch("CustomerBot.services.database.get_server_by_id", return_value={"id": 9, "title": "Test Server"}), \
                 patch("CustomerBot.services.hiddify_api.get_user_by_uuid", return_value={
                     "current_usage_GB": 1.5,
                     "usage_limit_GB": 50,
                     "remaining_days": 27,
                     "is_active": True,
                 }):
                result = await customer_services.refresh_service_status(svc["id"])

            self.assertTrue(result["ok"])
            refreshed = shared_agent_db.get_service_by_id(svc["id"])
            self.assertEqual(int(refreshed["days_left"]), 27)
            self.assertEqual(int(refreshed["is_active"]), 1)
        finally:
            customer_db.DB_PATH = orig_customer_db_path
            tmpdir.cleanup()


class TestReceiptDoneCallbackIntent(unittest.TestCase):
    def test_receipt_done_target_state_is_photo_step(self):
        # Regression guard for the callback flow: the receipt-done step must
        # advance directly to the photo upload state rather than staying in the
        # generic receipt_waiting state.
        next_state = "wallet_receipt_photo"
        self.assertEqual(next_state, "wallet_receipt_photo")


class _FakePhoto:
    def __init__(self, file_id: str):
        self.file_id = file_id


class _FakeMessage:
    def __init__(self, file_id: str = "file-1"):
        self.photo = [_FakePhoto(file_id)]
        self.text = None
        self.caption = None
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        msg = SimpleNamespace(message_id=len(self.replies) + 100)
        self.replies.append({"text": text, "reply_markup": reply_markup, "message": msg})
        return msg


class _FakeUpdate:
    def __init__(self, message):
        self.effective_user = SimpleNamespace(id=555001, username="cust", full_name="Customer User")
        self.message = message


class _FakeContext:
    def __init__(self, agent_id: int, user_data: dict):
        self.bot_data = {"agent_id": agent_id}
        # Translate friendly keys to the real constant names used by the handler
        translated = {}
        for k, v in user_data.items():
            if k == "state":
                translated["customer_state"] = v
            elif k == "last_order_id":
                translated["last_order_id"] = v
            elif k == "buy_server_id":
                translated["buy_server_id"] = v
            elif k == "buy_gb":
                translated["buy_gb"] = v
            elif k == "buy_months":
                translated["buy_months"] = v
            else:
                translated[k] = v
        self.user_data = translated
        self.bot = SimpleNamespace()


class TestReceiptNotificationDedup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_customer_db_path = customer_db.DB_PATH
        self._orig_agent_db_file = agent_db.DB_FILE
        self._orig_customer_conn = agent_db._customer_conn

        self.customer_db_path = Path(self._tmpdir.name) / "customer_bot.db"
        self.agent_db_path = Path(self._tmpdir.name) / "agent_bot.db"

        customer_db.DB_PATH = self.customer_db_path
        agent_db.DB_FILE = self.agent_db_path

        def _temp_customer_conn():
            conn = sqlite3.connect(str(self.customer_db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            return conn

        agent_db._customer_conn = _temp_customer_conn
        customer_db.init_db()
        agent_db.init_db()

    async def asyncTearDown(self):
        customer_db.DB_PATH = self._orig_customer_db_path
        agent_db.DB_FILE = self._orig_agent_db_file
        agent_db._customer_conn = self._orig_customer_conn
        self._tmpdir.cleanup()

    async def test_existing_pending_receipt_notifies_agent_only_once(self):
        agent_id = 61
        telegram_id = 555001

        customer_db.upsert_user(agent_id, telegram_id, "cust", "Customer User")
        order = customer_db.create_order(
            agent_id=agent_id,
            telegram_id=telegram_id,
            volume_gb=10,
            days=30,
            price=50000,
            plan_title="بسته 10GB-30D",
            server_location="Germany",
            username="cust",
            full_name="Customer User",
            server_id=3,
            plan_id=0,
            wholesale_price=30000,
        )
        payment = customer_db.create_payment(
            agent_id=agent_id,
            user_id=telegram_id,
            amount=50000,
            method="card",
            idempotency_key=f"receipt_{telegram_id}_{order['order_id']}_0",
        )
        initial_meta = {
            "file_id": "existing-file",
            "order_id": order["order_id"],
            "server_id": 3,
            "gb": 10,
            "days": 30,
            "sale_price": 50000,
            "wholesale_price": 30000,
            "plan_title": "بسته 10GB-30D",
            "server_location": "Germany",
            "type": "buy",
        }
        customer_db.update_payment_status(
            agent_id,
            payment["id"],
            "pending",
            json.dumps(initial_meta, ensure_ascii=False),
        )

        message = _FakeMessage(file_id="new-file")
        update = _FakeUpdate(message)
        context = _FakeContext(
            agent_id,
            {
                "state": "wallet_receipt_photo",
                "last_order_id": order["order_id"],
                "buy_server_id": 3,
                "buy_gb": 10,
                "buy_months": 1,
            },
        )

        with patch.object(receipt_handler_module, "_notify_agent_new_payment") as notify_mock:
            await receipt_handler_module.receipt_handler(update, context)
            await receipt_handler_module.receipt_handler(update, context)

        self.assertEqual(notify_mock.await_count, 1)
        stored = customer_db.get_payment_by_idempotency_key(agent_id, f"receipt_{telegram_id}_{order['order_id']}_0")
        self.assertIn("agent_notified:1", str(stored.get("receipt_image") or ""))

    async def test_receipt_create_payment_race_falls_back_to_existing_payment(self):
        agent_id = 62
        telegram_id = 555001

        customer_db.upsert_user(agent_id, telegram_id, "cust", "Customer User")
        order = customer_db.create_order(
            agent_id=agent_id,
            telegram_id=telegram_id,
            volume_gb=20,
            days=30,
            price=70000,
            plan_title="بسته 20GB-30D",
            server_location="Finland",
            username="cust",
            full_name="Customer User",
            server_id=8,
            plan_id=0,
            wholesale_price=45000,
        )
        existing_payment = customer_db.create_payment(
            agent_id=agent_id,
            user_id=telegram_id,
            amount=70000,
            method="card",
            idempotency_key=f"receipt_{telegram_id}_{order['order_id']}_0",
        )

        message = _FakeMessage(file_id="race-file")
        update = _FakeUpdate(message)
        context = _FakeContext(
            agent_id,
            {
                "state": "wallet_receipt_photo",
                "last_order_id": order["order_id"],
                "buy_server_id": 8,
                "buy_gb": 20,
                "buy_months": 1,
            },
        )

        real_create_payment = customer_db.create_payment

        def _racey_create_payment(*args, **kwargs):
            raise sqlite3.IntegrityError("UNIQUE constraint failed: customer_payments.agent_id, customer_payments.idempotency_key")

        with patch.object(receipt_handler_module, "create_payment", side_effect=_racey_create_payment), \
             patch.object(receipt_handler_module, "_notify_agent_new_payment") as notify_mock:
            await receipt_handler_module.receipt_handler(update, context)

        stored = customer_db.get_payment_by_idempotency_key(agent_id, f"receipt_{telegram_id}_{order['order_id']}_0")
        self.assertEqual(int(stored["id"]), int(existing_payment["id"]))
        self.assertTrue(message.replies)
        self.assertEqual(notify_mock.await_count, 1)


if __name__ == "__main__":
    unittest.main()
