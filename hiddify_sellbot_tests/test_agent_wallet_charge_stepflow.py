"""Tests for the AgentBot wallet charge step-by-step flow.

Verifies that:
  * each step deletes the previous prompt message and the user's input message,
  * a red (style="danger") back button is attached in the bottom reply keyboard at every step,
  * pressing the back button returns to the wallet menu,
  * the flow ends by creating a pending transaction and showing the final message.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from AgentBot.handlers import wallet as wallet_module
from AgentBot.constants import UD_STATE


def _make_message(message_id, chat_id=10, text=None, photo=None):
    msg = MagicMock()
    msg.message_id = message_id
    msg.chat_id = chat_id
    msg.text = text
    msg.caption = None
    msg.photo = photo
    msg.reply_text = AsyncMock()
    return msg


def _make_context(user_data=None):
    context = MagicMock()
    context.user_data = user_data if user_data is not None else {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    context.bot.delete_message = AsyncMock()
    return context


def _deleted_ids(context):
    calls = context.bot.delete_message.call_args_list
    return [c.kwargs.get("message_id") for c in calls]


class TestWalletChargeStepFlow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent_id = 5

    async def _start_charge(self, context):
        # Simulate the "charge" callback: wallet menu message deleted, amount prompt sent.
        query = MagicMock()
        query.data = "agbot:wallet:charge"
        query.message = _make_message(100)
        query.message.delete = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = SimpleNamespace(callback_query=query)
        sent = _make_message(101)
        context.bot.send_message.return_value = sent
        await wallet_module.handle_callback(update, context)
        return query

    async def test_full_flow_deletes_previous_step_messages(self):
        context = _make_context()

        # --- Step 1: charge callback -> amount prompt ---
        await self._start_charge(context)
        self.assertEqual(context.user_data.get(UD_STATE), wallet_module.STATE_WALLET_CHARGE_AMOUNT)
        self.assertEqual(context.user_data.get("charge_prompt_msg_id"), 101)

        # --- Step 2: user sends amount -> card info, delete prompt(101)+user msg(200) ---
        amount_msg = _make_message(200, text="200000")
        update = SimpleNamespace(message=amount_msg)
        card_sent = _make_message(300)
        back_kb_sent = _make_message(301)
        context.bot.send_message.side_effect = [card_sent, back_kb_sent]

        with patch.object(wallet_module, "get_agent_id", return_value=self.agent_id), \
             patch.object(wallet_module.shared_db, "get_random_card", return_value={"number": "6037", "owner": "Test"}):
            consumed = await wallet_module.handle_text(update, context)

        self.assertTrue(consumed)
        self.assertEqual(context.user_data.get(UD_STATE), wallet_module.STATE_WALLET_CHARGE_RECEIPT)
        self.assertEqual(context.user_data.get("charge_prompt_msg_id"), 300)
        self.assertEqual(context.user_data.get("charge_back_kb_msg_id"), 301)
        self.assertIn(101, _deleted_ids(context))  # amount prompt deleted
        self.assertIn(200, _deleted_ids(context))  # user's amount message deleted

        # --- Step 3: "paid" callback -> receipt prompt (edits card info msg 300) + sends back kb ---
        paid_query = MagicMock()
        paid_query.data = "agbot:wallet:paid"
        paid_query.message = _make_message(300)
        paid_query.edit_message_text = AsyncMock()
        back_kb_sent2 = _make_message(400)
        context.bot.send_message.side_effect = [back_kb_sent2]
        paid_update = SimpleNamespace(callback_query=paid_query)
        await wallet_module.handle_callback(paid_update, context)
        self.assertEqual(context.user_data.get(UD_STATE), wallet_module.STATE_WALLET_CHARGE_RECEIPT)
        self.assertEqual(context.user_data.get("charge_prompt_msg_id"), 300)

        # --- Step 4: user sends photo -> last4 prompt, delete receipt prompt(300)+photo(500)+back_kb(301) ---
        photo = SimpleNamespace(file_id="photo-file-id")
        photo_msg = _make_message(500, photo=[photo])
        update = SimpleNamespace(message=photo_msg)
        last4_prompt = _make_message(600)
        context.bot.send_message.side_effect = None
        context.bot.send_message.return_value = last4_prompt

        with patch.object(wallet_module, "get_agent_id", return_value=self.agent_id):
            consumed = await wallet_module.handle_text(update, context)

        self.assertTrue(consumed)
        self.assertEqual(context.user_data.get(UD_STATE), wallet_module.STATE_WALLET_CHARGE_LAST4)
        self.assertEqual(context.user_data.get("charge_receipt_id"), "photo-file-id")
        self.assertEqual(context.user_data.get("charge_prompt_msg_id"), 600)
        self.assertIn(300, _deleted_ids(context))  # receipt prompt deleted
        self.assertIn(500, _deleted_ids(context))  # user's photo message deleted
        self.assertIn(400, _deleted_ids(context))  # current back kb message deleted

        # --- Step 5: user sends last4 -> create payment, delete last4 prompt(600)+msg(700) ---
        last4_msg = _make_message(700, text="1234")
        update = SimpleNamespace(message=last4_msg)
        final_msg = _make_message(800)
        context.bot.send_message.side_effect = [final_msg]

        fake_payment = {"id": 1, "receipt_image": "{}", "amount": 200622, "card_last4": "1234", "ref_id": "123"}
        with patch.object(wallet_module, "get_agent_id", return_value=self.agent_id), \
             patch.object(wallet_module.agent_db, "get_agent_by_id", return_value={"full_name": "Agent"}), \
             patch.object(wallet_module.agentbot_db, "create_wallet_charge_payment", return_value=fake_payment) as create_mock, \
             patch.object(wallet_module, "_notify_admin_wallet_payment", AsyncMock()) as notify_mock:
            consumed = await wallet_module.handle_text(update, context)

        self.assertTrue(consumed)
        create_mock.assert_called_once()
        notify_mock.assert_awaited_once()
        self.assertIn(600, _deleted_ids(context))  # last4 prompt deleted
        self.assertIn(700, _deleted_ids(context))  # user's last4 message deleted
        # state cleared after finishing
        self.assertIsNone(context.user_data.get(UD_STATE))
        self.assertIsNone(context.user_data.get("charge_prompt_msg_id"))
        self.assertIsNone(context.user_data.get("charge_back_kb_msg_id"))

    async def test_back_button_is_red_in_bottom_reply_keyboard(self):
        kb = wallet_module._wallet_back_reply_keyboard()
        row = kb.keyboard[0]
        btn = row[0]
        self.assertEqual(btn.text, wallet_module.BTN_BACK_TEXT)
        self.assertEqual(btn.api_kwargs.get("style"), "danger")

    async def test_back_text_returns_to_wallet_menu(self):
        context = _make_context()
        await self._start_charge(context)
        self.assertEqual(context.user_data.get(UD_STATE), wallet_module.STATE_WALLET_CHARGE_AMOUNT)

        back_msg = _make_message(201, text=wallet_module.BTN_BACK_TEXT)
        update = SimpleNamespace(message=back_msg, callback_query=None)

        with patch.object(wallet_module, "get_agent_id", return_value=self.agent_id), \
             patch.object(wallet_module.agent_db, "get_wallet", return_value={"balance": 0}), \
             patch.object(wallet_module.agent_db, "get_agent_by_id", return_value={"is_active": 1}):
            consumed = await wallet_module.handle_text(update, context)

        self.assertTrue(consumed)
        # state cleared and wallet menu shown
        self.assertIsNone(context.user_data.get(UD_STATE))
        back_msg.reply_text.assert_awaited()


if __name__ == "__main__":
    unittest.main()