import tempfile
import unittest
from pathlib import Path

from Shared import agent_db, userbot_db


class AgencyMonotonicUsageGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = agent_db.DB_PATH
        self.old_initialized = agent_db._db_initialized
        self.old_init_path = agent_db._init_db_path
        agent_db.DB_PATH = Path(self.tmp.name) / "agency.db"
        agent_db._db_initialized = False
        agent_db._init_db_path = ""
        agent_db.init_db()

        conn = agent_db._get_conn()
        try:
            conn.execute(
                "INSERT INTO agent_users (id, telegram_id, username, full_name, is_active, created_at, updated_at) "
                "VALUES (1, 1001, 'agent', 'Agent', 1, '', '')"
            )
            conn.execute(
                "INSERT INTO agent_services "
                "(id, agent_id, customer_id, server_id, server_title, name, panel_user_uuid, "
                "usage_current, usage_limit, days_left, is_active, created_at, updated_at) "
                "VALUES (1, 1, NULL, 1, 'Main', 'svc', 'uuid-a', 4.5, 10, 20, 1, '', '')"
            )
            conn.commit()
        finally:
            conn.close()
        agent_db.add_service_node(1, 1, server_title="Main", panel_user_uuid="uuid-a")
        agent_db.update_service_node_runtime(1, 1, "uuid-a", usage_current=4.5)

    def tearDown(self):
        agent_db.DB_PATH = self.old_path
        agent_db._db_initialized = self.old_initialized
        agent_db._init_db_path = self.old_init_path
        self.tmp.cleanup()

    def test_panel_reset_keeps_previous_usage_and_adds_new_traffic(self):
        reset = agent_db.record_monotonic_panel_usage(1, 1, "uuid-a", 0.0)
        self.assertTrue(reset["reset_detected"])
        self.assertAlmostEqual(reset["effective_usage"], 4.5, places=6)
        self.assertAlmostEqual(reset["usage_offset"], 4.5, places=6)

        later = agent_db.record_monotonic_panel_usage(1, 1, "uuid-a", 0.7)
        self.assertFalse(later["reset_detected"])
        self.assertAlmostEqual(later["effective_usage"], 5.2, places=6)
        self.assertAlmostEqual(later["usage_offset"], 4.5, places=6)

    def test_normal_growth_and_small_correction_never_double_count(self):
        up = agent_db.record_monotonic_panel_usage(1, 1, "uuid-a", 5.0)
        self.assertFalse(up["reset_detected"])
        self.assertAlmostEqual(up["effective_usage"], 5.0, places=6)

        tiny = agent_db.record_monotonic_panel_usage(1, 1, "uuid-a", 4.98)
        self.assertFalse(tiny["reset_detected"])
        self.assertAlmostEqual(tiny["effective_usage"], 5.0, places=6)
        self.assertAlmostEqual(tiny["usage_offset"], 0.0, places=6)

    def test_confirmed_renewal_clears_guard_generation(self):
        agent_db.record_monotonic_panel_usage(1, 1, "uuid-a", 0.0)
        agent_db.reset_service_nodes_on_renew(1, reset_usage=True, reset_time=True)
        row = agent_db.get_service_nodes(1)[0]
        self.assertAlmostEqual(float(row["usage_current"] or 0), 0.0, places=6)
        self.assertAlmostEqual(float(row["usage_raw"] or 0), 0.0, places=6)
        self.assertAlmostEqual(float(row["usage_offset"] or 0), 0.0, places=6)

        fresh = agent_db.record_monotonic_panel_usage(1, 1, "uuid-a", 0.4)
        self.assertFalse(fresh["reset_detected"])
        self.assertAlmostEqual(fresh["effective_usage"], 0.4, places=6)


class UserBotMonotonicUsageGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = userbot_db.DB_PATH
        userbot_db.DB_PATH = Path(self.tmp.name) / "hiddify_sellbot.db"
        userbot_db.init_db()

        conn = userbot_db._get_conn()
        try:
            conn.execute(
                "INSERT INTO userbot_users (id, telegram_id, username, full_name, created_at) "
                "VALUES (1, 2001, 'user', 'User', '')"
            )
            conn.execute(
                "INSERT INTO userbot_services "
                "(id, user_id, name, server_id, server_title, usage_current, usage_limit, days_left, last_online, comment) "
                "VALUES (1, 1, 'svc', 1, 'Main', 4.5, 10, 20, '', '')"
            )
            conn.commit()
        finally:
            conn.close()
        userbot_db.add_service_node(1, 1, "uuid-u", server_title="Main")
        userbot_db.update_service_node_runtime(1, 1, "uuid-u", usage_current=4.5)

    def tearDown(self):
        userbot_db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_panel_reset_is_carried_forward(self):
        reset = userbot_db.record_monotonic_panel_usage(1, 1, "uuid-u", 0.0)
        self.assertTrue(reset["reset_detected"])
        self.assertAlmostEqual(reset["effective_usage"], 4.5, places=6)

        later = userbot_db.record_monotonic_panel_usage(1, 1, "uuid-u", 0.7)
        self.assertAlmostEqual(later["effective_usage"], 5.2, places=6)

    def test_userbot_renewal_clears_offset(self):
        userbot_db.record_monotonic_panel_usage(1, 1, "uuid-u", 0.0)
        userbot_db.reset_service_nodes_on_renew(1)
        row = userbot_db.get_service_nodes(1)[0]
        self.assertAlmostEqual(float(row["usage_current"] or 0), 0.0, places=6)
        self.assertAlmostEqual(float(row["usage_raw"] or 0), 0.0, places=6)
        self.assertAlmostEqual(float(row["usage_offset"] or 0), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
