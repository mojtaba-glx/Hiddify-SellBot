import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from AgentBot.constants import STATE_BROADCAST_MESSAGE, UD_STATE
from AgentBot.handlers import settings_broadcast as broadcast


def _details():
    return {
        "unreachable": 0,
        "temporary": 0,
        "telegram": 0,
        "other": 0,
        "recovered": 0,
    }


class AgentBroadcastFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_skip_text_normalizes_telegram_variants(self):
        accepted = (
            "⏩رد کردن",
            "⏩ رد کردن",
            "⏩️رد کردن",
            "رد کردن",
            "رد\u200cکردن",
            "⏭️رد کردن",
            "▶️رد کردن",
        )
        for value in accepted:
            with self.subTest(value=value):
                self.assertTrue(broadcast._is_skip_text(value))
        self.assertFalse(broadcast._is_skip_text("ارسال"))
        self.assertFalse(broadcast._is_skip_text("لغو"))

    def test_agent_broadcast_has_no_preview_stage(self):
        self.assertFalse(hasattr(broadcast, "_send_broadcast_preview"))
        self.assertNotIn("preview", broadcast.handle_text.__code__.co_names)

    async def test_text_step_moves_to_photo_or_skip_step(self):
        message = SimpleNamespace(
            text="سلام کاربران",
            caption=None,
            photo=[],
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                UD_STATE: STATE_BROADCAST_MESSAGE,
                "broadcast_state": {
                    "segment": "all",
                    "step": "wait_text",
                    "text": "",
                    "photo_file_id": "",
                },
            }
        )

        with patch.object(broadcast, "get_agent_id", return_value=7):
            consumed = await broadcast.handle_text(update, context)

        self.assertTrue(consumed)
        state = context.user_data["broadcast_state"]
        self.assertEqual(state["text"], "سلام کاربران")
        self.assertEqual(state["step"], "wait_photo")
        message.reply_text.assert_awaited_once()

    async def test_skip_sends_text_directly_without_preview(self):
        message = SimpleNamespace(
            text="⏩رد کردن",
            caption=None,
            photo=[],
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                UD_STATE: STATE_BROADCAST_MESSAGE,
                "broadcast_state": {
                    "segment": "all",
                    "step": "wait_photo",
                    "text": "متن تست",
                    "photo_file_id": "",
                },
            }
        )

        send_mock = AsyncMock(return_value=(2, 0, _details()))
        restore_mock = AsyncMock()
        with patch.object(broadcast, "get_agent_id", return_value=7), \
             patch.object(broadcast, "get_active_customer_bot", return_value={"bot_token": "fake"}), \
             patch.object(broadcast, "get_broadcast_target_telegram_ids", return_value=[101, 102]), \
             patch.object(broadcast, "_send_broadcast_to_targets", new=send_mock), \
             patch.object(broadcast, "_restore_main_menu", new=restore_mock):
            consumed = await broadcast.handle_text(update, context)

        self.assertTrue(consumed)
        send_mock.assert_awaited_once_with(
            context,
            "fake",
            [101, 102],
            "متن تست",
            "",
        )
        self.assertNotIn("broadcast_state", context.user_data)
        self.assertNotIn(UD_STATE, context.user_data)
        self.assertIn("موفق: 2", message.reply_text.await_args.args[0])
        restore_mock.assert_awaited_once_with(message)

    async def test_photo_sends_directly_without_preview(self):
        message = SimpleNamespace(
            text=None,
            caption=None,
            photo=[SimpleNamespace(file_id="photo-small"), SimpleNamespace(file_id="photo-large")],
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                UD_STATE: STATE_BROADCAST_MESSAGE,
                "broadcast_state": {
                    "segment": "all",
                    "step": "wait_photo",
                    "text": "متن همراه عکس",
                    "photo_file_id": "",
                },
            }
        )

        send_mock = AsyncMock(return_value=(1, 0, _details()))
        restore_mock = AsyncMock()
        with patch.object(broadcast, "get_agent_id", return_value=7), \
             patch.object(broadcast, "get_active_customer_bot", return_value={"bot_token": "fake"}), \
             patch.object(broadcast, "get_broadcast_target_telegram_ids", return_value=[101]), \
             patch.object(broadcast, "_send_broadcast_to_targets", new=send_mock), \
             patch.object(broadcast, "_restore_main_menu", new=restore_mock):
            consumed = await broadcast.handle_text(update, context)

        self.assertTrue(consumed)
        send_mock.assert_awaited_once_with(
            context,
            "fake",
            [101],
            "متن همراه عکس",
            "photo-large",
        )
        self.assertNotIn("broadcast_state", context.user_data)
        restore_mock.assert_awaited_once_with(message)


if __name__ == "__main__":
    unittest.main()
