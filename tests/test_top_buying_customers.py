import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from Shared import userbot_db


class TopBuyingCustomersTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "userbot.db"
        self.db_path_patch = patch.object(userbot_db, "DB_PATH", self.db_path)
        self.db_path_patch.start()
        self.addCleanup(self.db_path_patch.stop)
        userbot_db.init_db()

        conn = userbot_db._get_conn()
        try:
            conn.executemany(
                """
                INSERT INTO userbot_users (telegram_id, username, full_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (1001, "alice", "Alice Example", "2026-09-01 00:00:00"),
                    (2002, "bob", "Bob Example", "2026-09-01 00:00:00"),
                ],
            )
            conn.commit()
            rows = conn.execute(
                "SELECT id, telegram_id FROM userbot_users ORDER BY telegram_id"
            ).fetchall()
            self.alice_id = int(rows[0]["id"])
            self.bob_id = int(rows[1]["id"])
        finally:
            conn.close()

    @staticmethod
    def _created_at(days_ago: int, iso_separator: bool = False) -> str:
        value = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)
        fmt = "%Y-%m-%dT%H:%M:%S" if iso_separator else "%Y-%m-%d %H:%M:%S"
        return value.strftime(fmt)

    def _insert_order(
        self, order_id, user_id, telegram_id, username, full_name,
        price, status, days_ago, iso_separator=False,
    ):
        conn = userbot_db._get_conn()
        try:
            conn.execute(
                """
                INSERT INTO userbot_orders (
                    order_id, user_id, telegram_id, username, full_name,
                    created_at, volume_gb, days, price, plan_title,
                    server_location, status, renew_service_id
                ) VALUES (?, ?, ?, ?, ?, ?, 10, 30, ?, 'Test plan', 'Test server', ?, 0)
                """,
                (
                    order_id, user_id, telegram_id, username, full_name,
                    self._created_at(days_ago, iso_separator), price, status,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _seed_orders(self):
        # Alice has one recent approved order whose old order snapshot contains
        # a stale Telegram ID, plus an older order with the normal identifier.
        self._insert_order(1, self.alice_id, 9999, "", "", 60000, "approved", 5, True)
        self._insert_order(2, self.alice_id, 1001, "alice", "Alice Example", 40000, "approved", 45)
        self._insert_order(3, self.alice_id, 1001, "alice", "Alice Example", 500000, "pending", 2)
        self._insert_order(4, self.alice_id, 1001, "alice", "Alice Example", 900000, "rejected", 1)
        self._insert_order(5, self.bob_id, 2002, "bob", "Bob Example", 180000, "approved", 2)
        self._insert_order(6, self.bob_id, 2002, "bob", "Bob Example", 20000, "APPROVED", 20, True)

    def test_only_approved_orders_count_and_customers_are_grouped_by_current_telegram_id(self):
        self._seed_orders()

        customers = userbot_db.get_top_buying_customers()

        self.assertEqual([row["telegram_id"] for row in customers], [2002, 1001])
        self.assertEqual(customers[0]["orders_count"], 2)
        self.assertEqual(customers[0]["total_spent"], 200000)
        self.assertEqual(customers[1]["orders_count"], 2)
        self.assertEqual(customers[1]["total_spent"], 100000)
        self.assertEqual(customers[1]["username"], "alice")

    def test_30_day_filter_excludes_older_approved_orders_and_supports_iso_timestamps(self):
        self._seed_orders()

        customers = userbot_db.get_top_buying_customers(days=30)

        self.assertEqual([row["telegram_id"] for row in customers], [2002, 1001])
        self.assertEqual(customers[0]["total_spent"], 200000)
        self.assertEqual(customers[1]["total_spent"], 60000)
        self.assertEqual(customers[0]["orders_count"], 2)
        self.assertEqual(customers[1]["orders_count"], 1)

    def test_limit_is_applied_after_ranking(self):
        for index in range(12):
            user_id = 3000 + index
            conn = userbot_db._get_conn()
            try:
                conn.execute(
                    "INSERT INTO userbot_users (telegram_id, username, full_name, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, f"user{index:02d}", f"User {index}", self._created_at(1)),
                )
                conn.commit()
                internal_id = conn.execute(
                    "SELECT id FROM userbot_users WHERE telegram_id = ?", (user_id,)
                ).fetchone()["id"]
            finally:
                conn.close()
            self._insert_order(
                100 + index, int(internal_id), user_id, f"user{index:02d}",
                f"User {index}", (index + 1) * 1000, "approved", 1,
            )

        customers = userbot_db.get_top_buying_customers(limit=3)

        self.assertEqual(len(customers), 3)
        self.assertEqual([row["telegram_id"] for row in customers], [3011, 3010, 3009])
        self.assertEqual([row["total_spent"] for row in customers], [12000, 11000, 10000])


if __name__ == "__main__":
    unittest.main()
