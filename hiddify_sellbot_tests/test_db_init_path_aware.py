#!/usr/bin/env python3
"""
Test suite for the path-aware init_db in CustomerBot/database.py and
Shared/agent_db.py.

The previous module-level `_db_initialized` flag made init_db a no-op even
when the database path changed (e.g. in tests), causing
`sqlite3.OperationalError: no such table`. These tests verify:

1. init_db creates tables on the first call
2. init_db is idempotent when called again on the same path
3. Changing DB_PATH re-initializes the new database file
4. Data written to one database file is preserved after path switches
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from CustomerBot import database as customer_db
from Shared import agent_db


def _reset_module(db_module):
    """بازنشانی فلگ‌های داخلی ماژول دیتابیس به حالت اولیه."""
    db_module._db_initialized = False
    db_module._init_db_path = ""


class TestCustomerDbInitPathAware(unittest.TestCase):

    def setUp(self):
        self._orig_db_path = customer_db.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        customer_db.DB_PATH = Path(self._tmpdir.name) / "customer_bot.db"
        _reset_module(customer_db)

    def tearDown(self):
        customer_db.DB_PATH = self._orig_db_path
        _reset_module(customer_db)
        self._tmpdir.cleanup()

    def _table_exists(self, path, table="customer_users"):
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def test_first_init_creates_tables(self):
        customer_db.init_db()
        self.assertTrue(self._table_exists(customer_db.DB_PATH))

    def test_init_db_is_idempotent_on_same_path(self):
        customer_db.init_db()
        customer_db.init_db()  # فراخوانی دوم نباید خطا بدهد
        self.assertTrue(self._table_exists(customer_db.DB_PATH))

    def test_changing_path_reinitializes_new_db(self):
        customer_db.init_db()
        customer_db.upsert_user(1, 111, "u1", "User One")

        new_path = Path(self._tmpdir.name) / "customer_bot_2.db"
        customer_db.DB_PATH = new_path
        customer_db.init_db()

        self.assertEqual(customer_db._init_db_path, str(new_path))
        self.assertTrue(self._table_exists(new_path))
        # دیتابیس جدید جداول دارد ولی کاربر قبلی آنجا نیست
        self.assertIsNone(customer_db.get_user(1, 111))

    def test_data_persisted_across_idempotent_calls(self):
        customer_db.init_db()
        customer_db.upsert_user(1, 222, "u2", "User Two")
        customer_db.init_db()  # روی همان مسیر — داده‌ها باقی می‌مانند

        user = customer_db.get_user(1, 222)
        self.assertIsNotNone(user)
        self.assertEqual(user["telegram_id"], 222)
        self.assertEqual(user["username"], "u2")


class TestAgentDbInitPathAware(unittest.TestCase):

    def setUp(self):
        self._orig_db_path = agent_db.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        agent_db.DB_PATH = Path(self._tmpdir.name) / "agency.db"
        _reset_module(agent_db)

    def tearDown(self):
        agent_db.DB_PATH = self._orig_db_path
        _reset_module(agent_db)
        self._tmpdir.cleanup()

    def _table_exists(self, path, table):
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def test_first_init_creates_tables(self):
        agent_db.init_db()
        self.assertTrue(self._table_exists(agent_db.DB_PATH, "agent_users"))

    def test_init_db_is_idempotent_on_same_path(self):
        agent_db.init_db()
        agent_db.init_db()  # فراخوانی دوم نباید خطا بدهد
        self.assertTrue(self._table_exists(agent_db.DB_PATH, "agent_users"))

    def test_changing_path_reinitializes_new_db(self):
        agent_db.init_db()

        new_path = Path(self._tmpdir.name) / "agency_2.db"
        agent_db.DB_PATH = new_path
        agent_db.init_db()

        self.assertEqual(agent_db._init_db_path, str(new_path))
        self.assertTrue(self._table_exists(new_path, "agent_users"))


if __name__ == "__main__":
    unittest.main()
