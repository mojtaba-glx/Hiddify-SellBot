import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import ReplyKeyboardMarkup

from AgentBot.constants import STATE_CBOT_TOKEN, UD_AGENT_ID, UD_STATE
from AgentBot.handlers import customer_bot


FAKE_CORE_TOKEN = "123456789:AA_CORE_TOKEN_FOR_TESTING_123456789"
FAKE_CUSTOMER_TOKEN = "987654321:AA_CUSTOMER_TOKEN_FOR_TESTING_12345"


class CustomerBotTokenSafetyTests(unittest.IsolatedAsyncioTestCase):
    def _request(self, token: str, agent_id: int = 7):
        message = SimpleNamespace(text=token, reply_text=AsyncMock())
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={UD_AGENT_ID: agent_id, UD_STATE: STATE_CBOT_TOKEN}
        )
        return update, context

    async def test_core_bot_token_is_rejected_before_telegram_or_database(self):
        update, context = self._request(FAKE_CORE_TOKEN)
        with patch.dict(os.environ, {"ADMIN_BOT_TOKEN": FAKE_CORE_TOKEN}, clear=False), \
             patch.object(customer_bot.agent_db, "get_all_active_customer_bots") as get_bots, \
             patch.object(customer_bot.agent_db, "add_customer_bot") as add_bot:
            consumed = await customer_bot.handle_text(update, context)

        self.assertTrue(consumed)
        get_bots.assert_not_called()
        add_bot.assert_not_called()
        self.assertEqual(context.user_data[UD_STATE], STATE_CBOT_TOKEN)
        reply = update.message.reply_text.await_args
        self.assertNotIn(FAKE_CORE_TOKEN, reply.args[0])
        self.assertIsInstance(reply.kwargs["reply_markup"], ReplyKeyboardMarkup)

    async def test_token_registered_to_another_agent_is_rejected(self):
        update, context = self._request(FAKE_CUSTOMER_TOKEN, agent_id=7)
        clean_core_env = {
            "ADMIN_BOT_TOKEN": "",
            "USER_BOT_TOKEN": "",
            "AGENT_BOT_TOKEN": "",
        }
        rows = [{"agent_id": 8, "bot_token": FAKE_CUSTOMER_TOKEN}]
        with patch.dict(os.environ, clean_core_env, clear=False), \
             patch.object(customer_bot.agent_db, "get_all_active_customer_bots", return_value=rows), \
             patch.object(customer_bot.agent_db, "add_customer_bot") as add_bot:
            consumed = await customer_bot.handle_text(update, context)

        self.assertTrue(consumed)
        add_bot.assert_not_called()
        self.assertEqual(context.user_data[UD_STATE], STATE_CBOT_TOKEN)
        reply = update.message.reply_text.await_args
        self.assertNotIn(FAKE_CUSTOMER_TOKEN, reply.args[0])
        self.assertIsInstance(reply.kwargs["reply_markup"], ReplyKeyboardMarkup)


if __name__ == "__main__":
    unittest.main()
