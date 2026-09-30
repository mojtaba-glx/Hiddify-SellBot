import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from Shared import agent_db, userbot_db


FMT = "%Y-%m-%d %H:%M:%S"


class UserBotExpiredAgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = userbot_db.DB_PATH
        userbot_db.DB_PATH = Path(self.tmp.name) / "userbot.db"
        userbot_db.init_db()
        conn = userbot_db._get_conn()
        conn.execute(
            "INSERT INTO userbot_users (id, telegram_id, username, full_name, created_at) "
            "VALUES (1, 1001, 'tester', 'Tester', '')"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        userbot_db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _insert_service(self, sid, *, used, limit, days_left, last_online="", expired_at=""):
        conn = userbot_db._get_conn()
        conn.execute(
            "INSERT INTO userbot_services "
            "(id, user_id, name, server_id, server_title, usage_current, usage_limit, "
            "days_left, last_online, comment, expired_at) "
            "VALUES (?, 1, ?, 1, 'Test', ?, ?, ?, ?, '', ?)",
            (sid, f"svc-{sid}", used, limit, days_left, last_online, expired_at),
        )
        conn.commit()
        conn.close()

    def test_old_last_online_does_not_expire_active_service(self):
        old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)).strftime(FMT)
        self._insert_service(1, used=2, limit=10, days_left=20, last_online=old)
        self.assertEqual(userbot_db.get_expired_services(3), [])

    def test_legacy_volume_expired_may_use_last_online_only_after_limit_reached(self):
        old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=4, minutes=5)).strftime(FMT)
        self._insert_service(2, used=10.1, limit=10, days_left=20, last_online=old)
        rows = userbot_db.get_expired_services(3)
        self.assertEqual([int(r["id"]) for r in rows], [2])
        self.assertGreaterEqual(int(rows[0]["_expired_days"]), 3)

    def test_runtime_sets_expired_at_and_renew_reset_clears_it(self):
        self._insert_service(3, used=9, limit=10, days_left=20)
        userbot_db.update_service_runtime(3, usage_current=10.0)
        conn = userbot_db._get_conn()
        row = conn.execute("SELECT expired_at FROM userbot_services WHERE id = 3").fetchone()
        conn.close()
        self.assertTrue(str(row["expired_at"] or "").strip())

        userbot_db.reset_service_nodes_on_renew(3)
        conn = userbot_db._get_conn()
        row = conn.execute("SELECT expired_at FROM userbot_services WHERE id = 3").fetchone()
        conn.close()
        self.assertEqual(str(row["expired_at"] or ""), "")


class AgentExpiredAgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = agent_db.DB_PATH
        self.old_initialized = agent_db._db_initialized
        self.old_init_path = agent_db._init_db_path
        agent_db.DB_PATH = Path(self.tmp.name) / "agency.db"
        agent_db._db_initialized = False
        agent_db._init_db_path = ""
        agent_db.init_db()
        self.agent_id = agent_db.upsert_agent(2001, username="agent")

    def tearDown(self):
        agent_db.DB_PATH = self.old_path
        agent_db._db_initialized = self.old_initialized
        agent_db._init_db_path = self.old_init_path
        self.tmp.cleanup()

    def test_old_updated_at_does_not_expire_valid_service(self):
        svc = agent_db.create_service(
            agent_id=self.agent_id,
            customer_id=0,
            server_id=1,
            name="active",
            panel_user_uuid="uuid-active",
            usage_limit=10,
            days=30,
        )
        old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)).strftime(FMT)
        conn = agent_db._get_conn()
        conn.execute(
            "UPDATE agent_services SET usage_current=2, days_left=20, "
            "end_date=?, updated_at=?, expired_at='' WHERE id=?",
            ((datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=20)).strftime(FMT), old, int(svc["id"])),
        )
        conn.commit()
        conn.close()
        self.assertEqual(agent_db.get_all_expired_services(3), [])

    def test_volume_expired_uses_expired_at_for_bulk_age(self):
        svc = agent_db.create_service(
            agent_id=self.agent_id,
            customer_id=0,
            server_id=1,
            name="expired-volume",
            panel_user_uuid="uuid-expired",
            usage_limit=10,
            days=30,
        )
        old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=4, minutes=5)).strftime(FMT)
        agent_db.update_service(
            int(svc["id"]),
            {"usage_current": 10.2, "days_left": 20, "expired_at": old},
        )
        rows = agent_db.get_all_expired_services(3)
        self.assertEqual([int(r["id"]) for r in rows], [int(svc["id"])])


if __name__ == "__main__":
    unittest.main()
