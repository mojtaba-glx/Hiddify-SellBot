import tempfile
import unittest
from pathlib import Path

from AgentBot.services import subscription_service
from Shared import userbot_db


class AgencyRenewUserBotRuntimeSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = userbot_db.DB_PATH
        userbot_db.DB_PATH = Path(self.tmp.name) / "hiddify_sellbot.db"
        userbot_db.init_db()

        conn = userbot_db._get_conn()
        try:
            conn.execute(
                "INSERT INTO userbot_users "
                "(id, telegram_id, username, full_name, created_at) "
                "VALUES (1, 1001, 'agent-customer', 'Agent Customer', '')"
            )
            conn.execute(
                """
                INSERT INTO userbot_services
                (id, user_id, name, server_id, server_title,
                 usage_current, usage_limit, days_left, last_online,
                 comment, expired_at)
                VALUES
                (1, 1, 'agency-service', 1, 'Germany',
                 10, 10, -1, '', 'uuid:uuid-agency', '2026-10-08 20:00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO userbot_service_nodes
                (service_id, server_id, server_title, panel_user_uuid,
                 usage_current, usage_raw, usage_offset, days_left,
                 frozen, fail_count, is_active, frozen_reason,
                 created_at, updated_at)
                VALUES
                (1, 1, 'Germany', 'uuid-agency',
                 10, 10, 10, -1,
                 1, 3, 0, 'time_expired', '', '')
                """
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        userbot_db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_agency_renew_sync_clears_stale_userbot_expiry(self):
        synced = subscription_service._sync_userbot_runtime_after_agency_renew(
            "uuid-agency",
            usage_limit=20,
            days_left=30,
        )

        self.assertTrue(synced)
        service = userbot_db.get_service_by_id(1)
        self.assertAlmostEqual(float(service["usage_current"]), 0.0)
        self.assertAlmostEqual(float(service["usage_limit"]), 20.0)
        self.assertEqual(int(service["days_left"]), 30)
        self.assertEqual(str(service["expired_at"] or ""), "")

        nodes = userbot_db.get_service_nodes(1)
        self.assertEqual(len(nodes), 1)
        self.assertAlmostEqual(float(nodes[0]["usage_current"]), 0.0)
        self.assertAlmostEqual(float(nodes[0]["usage_raw"]), 0.0)
        self.assertAlmostEqual(float(nodes[0]["usage_offset"]), 0.0)
        self.assertEqual(int(nodes[0]["frozen"] or 0), 0)
        self.assertEqual(int(nodes[0]["fail_count"] or 0), 0)
        self.assertEqual(int(nodes[0]["is_active"]), 1)
        self.assertEqual(str(nodes[0]["frozen_reason"] or ""), "")

    def test_missing_userbot_service_is_not_a_renewal_failure(self):
        synced = subscription_service._sync_userbot_runtime_after_agency_renew(
            "uuid-not-in-userbot-db",
            usage_limit=20,
            days_left=30,
        )
        self.assertFalse(synced)


if __name__ == "__main__":
    unittest.main()
