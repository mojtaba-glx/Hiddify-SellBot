import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from Shared import agent_db
from Shared.agent_financial_report import _bounds
from AgentBot import main as agent_main


class AgentDailyFinancialReportTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_report_day_uses_full_tehran_calendar_day(self):
        start, end = _bounds(0, "Asia/Tehran", report_day="2026-09-29")
        self.assertEqual(start, "2026-09-28 20:30:00")
        self.assertEqual(end, "2026-09-29 20:30:00")

    async def test_daily_dispatch_sends_previous_day_once_to_active_agents(self):
        bot = AsyncMock()
        now = datetime(2026, 9, 30, 0, 0, tzinfo=agent_main._agent_report_tz())

        agents = [
            {"id": 1, "telegram_id": 1001, "is_active": 1},
            {"id": 2, "telegram_id": 1002, "is_active": 1},
        ]
        sent = set()

        def was_sent(agent_id, report_day):
            return (agent_id, report_day) in sent

        def mark_sent(agent_id, report_day):
            sent.add((agent_id, report_day))

        with patch.object(agent_db, "get_all_active_agents", return_value=agents), \
             patch.object(agent_db, "was_agent_daily_report_sent", side_effect=was_sent), \
             patch.object(agent_db, "mark_agent_daily_report_sent", side_effect=mark_sent), \
             patch("AgentBot.handlers.finance.build_financial_report_text", return_value="REPORT"), \
             patch("AgentBot.main.asyncio.sleep", new=AsyncMock()):
            first = await agent_main._send_daily_agent_financial_reports(bot, now=now)
            second = await agent_main._send_daily_agent_financial_reports(bot, now=now)

        self.assertEqual(first["day"], "2026-09-29")
        self.assertEqual(first["sent"], 2)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(second["skipped"], 2)
        self.assertEqual(bot.send_message.await_count, 2)

    async def test_failed_send_is_not_marked_sent(self):
        bot = AsyncMock()
        bot.send_message.side_effect = RuntimeError("blocked")
        now = datetime(2026, 9, 30, 0, 0, tzinfo=agent_main._agent_report_tz())

        with patch.object(
            agent_db,
            "get_all_active_agents",
            return_value=[{"id": 7, "telegram_id": 7007, "is_active": 1}],
        ), patch.object(
            agent_db, "was_agent_daily_report_sent", return_value=False
        ), patch.object(
            agent_db, "mark_agent_daily_report_sent"
        ) as mark, patch(
            "AgentBot.handlers.finance.build_financial_report_text",
            return_value="REPORT",
        ), patch("AgentBot.main.asyncio.sleep", new=AsyncMock()):
            result = await agent_main._send_daily_agent_financial_reports(bot, now=now)

        self.assertEqual(result["failed"], 1)
        mark.assert_not_called()


class AgentDailyReportDeliveryDbTests(unittest.TestCase):
    def test_delivery_marker_is_persistent_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_path = agent_db.DB_PATH
            old_initialized = agent_db._db_initialized
            old_init_path = agent_db._init_db_path
            try:
                agent_db.DB_PATH = Path(tmp) / "agency.db"
                agent_db._db_initialized = False
                agent_db._init_db_path = ""
                agent_db.init_db()
                conn = agent_db._get_conn()
                conn.execute(
                    "INSERT INTO agent_users "
                    "(id, telegram_id, username, full_name, is_active, created_at, updated_at) "
                    "VALUES (1, 1001, 'agent', 'Agent', 1, '', '')"
                )
                conn.commit()
                conn.close()

                self.assertFalse(agent_db.was_agent_daily_report_sent(1, "2026-09-29"))
                agent_db.mark_agent_daily_report_sent(1, "2026-09-29")
                agent_db.mark_agent_daily_report_sent(1, "2026-09-29")
                self.assertTrue(agent_db.was_agent_daily_report_sent(1, "2026-09-29"))

                conn = agent_db._get_conn()
                count = conn.execute(
                    "SELECT COUNT(*) FROM agent_daily_report_delivery "
                    "WHERE agent_id=1 AND report_day='2026-09-29'"
                ).fetchone()[0]
                conn.close()
                self.assertEqual(count, 1)
            finally:
                agent_db.DB_PATH = old_path
                agent_db._db_initialized = old_initialized
                agent_db._init_db_path = old_init_path


if __name__ == "__main__":
    unittest.main()
