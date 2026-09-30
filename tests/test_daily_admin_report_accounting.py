import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from Shared import daily_admin_report as report


class DailyAdminReportAccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.user_db = root / "user.db"
        self.customer_db = root / "customer.db"
        self.agency_db = root / "agency.db"
        self.agent_bot_db = root / "agent_bot.db"
        self.patchers = [
            patch.object(report, "USER_DB", self.user_db),
            patch.object(report, "CUSTOMER_DB", self.customer_db),
            patch.object(report, "AGENCY_DB", self.agency_db),
            patch.object(report, "AGENT_BOT_DB", self.agent_bot_db),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _exec(self, path, sql):
        conn = sqlite3.connect(path)
        try:
            conn.executescript(sql)
            conn.commit()
        finally:
            conn.close()

    def test_wallet_spend_is_not_new_cash_but_is_service_sale(self):
        self._exec(
            self.user_db,
            """
            CREATE TABLE userbot_payments (
                id INTEGER, amount INTEGER, method TEXT, status TEXT,
                receipt_image TEXT, created_at TEXT, updated_at TEXT
            );
            INSERT INTO userbot_payments VALUES
                (1, 100000, 'card', 'approved', 'sms_event_id:1', '2026-09-28 08:00:00', '2026-09-28 08:00:00'),
                (2, 80000, 'wallet', 'approved', '', '2026-09-28 09:00:00', '2026-09-28 09:00:00');

            CREATE TABLE userbot_orders (
                id INTEGER, price INTEGER, status TEXT, renew_service_id INTEGER, created_at TEXT
            );
            INSERT INTO userbot_orders VALUES
                (1, 80000, 'approved', 0, '2026-09-28 09:00:00');
            """,
        )
        cash = report._userbot_cash("2026-09-28 00:00:00", "2026-09-29 00:00:00")
        sales = report._userbot_sales("2026-09-28 00:00:00", "2026-09-29 00:00:00")
        self.assertEqual(cash["amount"], 100000)
        self.assertEqual(cash["approved"], 1)
        self.assertEqual(sales["buy_amount"], 80000)
        self.assertEqual(sales["buy_count"], 1)

    def test_customer_retail_and_wholesale_are_separate(self):
        self._exec(
            self.customer_db,
            """
            CREATE TABLE customer_orders (
                order_id INTEGER, price INTEGER, wholesale_price INTEGER,
                renew_service_id INTEGER, status TEXT, created_at TEXT, updated_at TEXT
            );
            INSERT INTO customer_orders VALUES
                (10, 120000, 50000, 0, 'approved', '2026-09-27 20:00:00', '2026-09-28 08:00:00'),
                (11, 150000, 60000, 9, 'approved', '2026-09-28 07:00:00', '2026-09-28 10:00:00'),
                (12, 999999, 999999, 0, 'pending', '2026-09-28 11:00:00', '2026-09-28 11:00:00');
            """,
        )
        self._exec(
            self.agency_db,
            "CREATE TABLE agent_transactions (amount INTEGER, description TEXT, tx_type TEXT, created_at TEXT);",
        )
        data = report._customer_sales("2026-09-28 00:00:00", "2026-09-29 00:00:00")
        self.assertEqual(data["buy_count"], 1)
        self.assertEqual(data["buy_retail"], 120000)
        self.assertEqual(data["buy_wholesale"], 50000)
        self.assertEqual(data["renew_count"], 1)
        self.assertEqual(data["renew_retail"], 150000)
        self.assertEqual(data["renew_wholesale"], 60000)

    def test_customer_wholesale_debit_is_not_direct_agent_purchase(self):
        self._exec(
            self.agency_db,
            """
            CREATE TABLE agent_transactions (
                id INTEGER, amount INTEGER, tx_type TEXT, description TEXT,
                service_id INTEGER, created_at TEXT
            );
            INSERT INTO agent_transactions VALUES
                (1, 50000, 'purchase', 'کسر عمده سفارش مشتری #123', 2, '2026-09-28 09:00:00'),
                (2, 70000, 'purchase', 'خرید سرویس: direct-user', 1, '2026-09-28 08:00:00'),
                (3, 30000, 'purchase', 'تمدید سرویس: direct-user', 1, '2026-09-28 10:00:00');
            """,
        )
        data = report._agent_activity("2026-09-28 00:00:00", "2026-09-29 00:00:00")
        self.assertEqual(data["buy_count"], 1)
        self.assertEqual(data["buy_wholesale"], 70000)
        self.assertEqual(data["renew_count"], 1)
        self.assertEqual(data["renew_wholesale"], 30000)


    def test_legacy_customer_order_can_recover_wholesale_from_wallet_debit(self):
        self._exec(
            self.customer_db,
            """
            CREATE TABLE customer_orders (
                order_id INTEGER, price INTEGER, wholesale_price INTEGER,
                renew_service_id INTEGER, status TEXT, created_at TEXT, updated_at TEXT
            );
            INSERT INTO customer_orders VALUES
                (777, 120000, 0, 0, 'approved', '2026-09-28 08:00:00', '2026-09-28 08:01:00');
            """,
        )
        self._exec(
            self.agency_db,
            """
            CREATE TABLE agent_transactions (
                amount INTEGER, description TEXT, tx_type TEXT, created_at TEXT
            );
            INSERT INTO agent_transactions VALUES
                (50000, 'کسر عمده سفارش مشتری #777', 'purchase', '2026-09-28 08:01:00');
            """,
        )
        data = report._customer_sales("2026-09-28 00:00:00", "2026-09-29 00:00:00")
        self.assertEqual(data["buy_wholesale"], 50000)

    def test_failed_direct_renewal_refund_is_excluded(self):
        self._exec(
            self.agency_db,
            """
            CREATE TABLE agent_transactions (
                id INTEGER, amount INTEGER, tx_type TEXT, description TEXT,
                service_id INTEGER, created_at TEXT
            );
            INSERT INTO agent_transactions VALUES
                (1, 40000, 'purchase', 'تمدید سرویس: failed', 5, '2026-09-28 10:00:00'),
                (2, 40000, 'refund', 'بازگشت وجه تمدید ناموفق سرویس #5', 5, '2026-09-28 10:05:00');
            """,
        )
        data = report._agent_activity("2026-09-28 00:00:00", "2026-09-29 00:00:00")
        self.assertEqual(data["renew_count"], 0)
        self.assertEqual(data["renew_wholesale"], 0)

    def test_report_labels_system_wholesale_separately(self):
        now = datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc)
        with patch.object(report, "_userbot_cash", return_value={"approved": 1, "amount": 100000, "auto": 1, "manual": 0, "failed": 0}), \
             patch.object(report, "_agent_wallet_cash", return_value={"approved": 1, "amount": 200000, "auto": 0, "manual": 1, "failed": 0}), \
             patch.object(report, "_userbot_sales", return_value={"buy_count": 1, "buy_amount": 100000, "renew_count": 0, "renew_amount": 0}), \
             patch.object(report, "_customer_sales", return_value={"buy_count": 1, "buy_retail": 120000, "buy_wholesale": 50000, "renew_count": 0, "renew_retail": 0, "renew_wholesale": 0}), \
             patch.object(report, "_agent_activity", return_value={"buy_count": 1, "buy_wholesale": 70000, "renew_count": 0, "renew_wholesale": 0}):
            text, _ = report.build_daily_report(now=now)
        self.assertIn("جمع ورودی نقدی: <b>2 پرداخت — 300,000 تومان</b>", text)
        self.assertIn("جمع فروش نمایندگان به مشتری: 120,000 تومان", text)
        self.assertIn("سهم عمده سیستم: <b>50,000 تومان</b>", text)
        self.assertIn("جمع درآمد سرویس: <b>220,000 تومان</b>", text)


if __name__ == "__main__":
    unittest.main()
