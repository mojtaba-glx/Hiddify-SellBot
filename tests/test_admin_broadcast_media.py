import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telegram.error import NetworkError

from AdminBot import userbot


class AdminBroadcastMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_photo_is_downloaded_then_reused_as_userbot_file_id(self):
        file_obj = SimpleNamespace(
            download_as_bytearray=AsyncMock(return_value=bytearray(b"image-data"))
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(get_file=AsyncMock(return_value=file_obj))
        )
        target_bot = SimpleNamespace(
            send_photo=AsyncMock(
                return_value=SimpleNamespace(
                    photo=[SimpleNamespace(file_id="userbot-owned-file-id")]
                )
            ),
            send_message=AsyncMock(),
        )

        with patch.object(userbot, "USER_BOT_TOKEN", "fake-user-token"), \
             patch.object(userbot, "Bot", return_value=target_bot):
            result = await userbot._send_broadcast_to_targets(
                context, [101, 102], "hello", "admin-owned-file-id"
            )

        self.assertEqual(result[:2], (2, 0))
        self.assertEqual(result[2]["recovered"], 0)
        context.bot.get_file.assert_awaited_once_with("admin-owned-file-id")
        self.assertEqual(target_bot.send_photo.await_count, 2)
        first_photo = target_bot.send_photo.await_args_list[0].kwargs["photo"]
        second_photo = target_bot.send_photo.await_args_list[1].kwargs["photo"]
        self.assertIsInstance(first_photo, BytesIO)
        self.assertEqual(first_photo.getvalue(), b"image-data")
        self.assertEqual(second_photo, "userbot-owned-file-id")

    async def test_failed_first_recipient_does_not_prevent_photo_upload(self):
        file_obj = SimpleNamespace(
            download_as_bytearray=AsyncMock(return_value=bytearray(b"image-data"))
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(get_file=AsyncMock(return_value=file_obj))
        )
        successful = SimpleNamespace(
            photo=[SimpleNamespace(file_id="userbot-owned-file-id")]
        )
        target_bot = SimpleNamespace(
            send_photo=AsyncMock(side_effect=[RuntimeError("blocked"), successful, successful]),
            send_message=AsyncMock(),
        )

        with patch.object(userbot, "USER_BOT_TOKEN", "fake-user-token"), \
             patch.object(userbot, "Bot", return_value=target_bot):
            result = await userbot._send_broadcast_to_targets(
                context, [101, 102, 103], "hello", "admin-owned-file-id"
            )

        self.assertEqual(result[:2], (2, 1))
        self.assertEqual(result[2]["other"], 1)
        self.assertIsInstance(
            target_bot.send_photo.await_args_list[0].kwargs["photo"], BytesIO
        )
        self.assertIsInstance(
            target_bot.send_photo.await_args_list[1].kwargs["photo"], BytesIO
        )
        self.assertEqual(
            target_bot.send_photo.await_args_list[2].kwargs["photo"],
            "userbot-owned-file-id",
        )

    async def test_text_only_broadcast_does_not_download_media(self):
        context = SimpleNamespace(
            bot=SimpleNamespace(get_file=AsyncMock())
        )
        target_bot = SimpleNamespace(
            send_photo=AsyncMock(),
            send_message=AsyncMock(),
        )

        with patch.object(userbot, "USER_BOT_TOKEN", "fake-user-token"), \
             patch.object(userbot, "Bot", return_value=target_bot):
            result = await userbot._send_broadcast_to_targets(
                context, [101, 102], "hello"
            )

        self.assertEqual(result[:2], (2, 0))
        self.assertEqual(result[2]["recovered"], 0)
        context.bot.get_file.assert_not_awaited()
        target_bot.send_photo.assert_not_awaited()
        self.assertEqual(target_bot.send_message.await_count, 2)

    def test_skip_button_text_accepts_telegram_variants(self):
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
                self.assertTrue(userbot._is_ticket_reply_skip_text(value))

    def test_admin_broadcast_preview_stage_is_removed(self):
        self.assertFalse(hasattr(userbot, "_send_broadcast_preview"))
        self.assertFalse(hasattr(userbot, "build_broadcast_preview_keyboard"))

    async def test_admin_skip_sends_text_directly(self):
        message = SimpleNamespace(
            text="⏩رد کردن",
            photo=[],
            document=None,
            chat_id=999,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                userbot.BROADCAST_SEND_STATE: {
                    "segment": "all",
                    "step": "wait_photo",
                    "text": "متن تست",
                }
            },
            bot=SimpleNamespace(),
        )
        details = {
            "unreachable": 0,
            "temporary": 0,
            "telegram": 0,
            "other": 0,
            "recovered": 0,
        }
        send_mock = AsyncMock(return_value=(2, 0, details))

        with patch.object(
            userbot.userbot_db,
            "get_broadcast_target_telegram_ids",
            return_value=[101, 102],
        ), patch.object(
            userbot,
            "_send_broadcast_to_targets",
            new=send_mock,
        ):
            await userbot.handle_admin_text_input(update, context)

        send_mock.assert_awaited_once_with(
            context,
            [101, 102],
            "متن تست",
            "",
        )
        self.assertNotIn(userbot.BROADCAST_SEND_STATE, context.user_data)
        self.assertIn("2", message.reply_text.await_args.args[0])

    async def test_admin_photo_sends_directly_without_preview(self):
        message = SimpleNamespace(
            text=None,
            photo=[
                SimpleNamespace(file_id="photo-small"),
                SimpleNamespace(file_id="photo-large"),
            ],
            document=None,
            chat_id=999,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                userbot.BROADCAST_SEND_STATE: {
                    "segment": "all",
                    "step": "wait_photo",
                    "text": "متن همراه عکس",
                }
            },
            bot=SimpleNamespace(),
        )
        details = {
            "unreachable": 0,
            "temporary": 0,
            "telegram": 0,
            "other": 0,
            "recovered": 0,
        }
        send_mock = AsyncMock(return_value=(1, 0, details))

        with patch.object(
            userbot.userbot_db,
            "get_broadcast_target_telegram_ids",
            return_value=[101],
        ), patch.object(
            userbot,
            "_send_broadcast_to_targets",
            new=send_mock,
        ):
            await userbot.handle_admin_text_input(update, context)

        send_mock.assert_awaited_once_with(
            context,
            [101],
            "متن همراه عکس",
            "photo-large",
        )
        self.assertNotIn(userbot.BROADCAST_SEND_STATE, context.user_data)

    async def test_admin_image_document_is_accepted_as_photo(self):
        message = SimpleNamespace(
            text=None,
            photo=[],
            document=SimpleNamespace(
                file_id="image-document-id",
                mime_type="image/jpeg",
            ),
            chat_id=999,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                userbot.BROADCAST_SEND_STATE: {
                    "segment": "all",
                    "step": "wait_photo",
                    "text": "متن همراه فایل عکس",
                }
            },
            bot=SimpleNamespace(),
        )
        details = {
            "unreachable": 0,
            "temporary": 0,
            "telegram": 0,
            "other": 0,
            "recovered": 0,
        }
        send_mock = AsyncMock(return_value=(1, 0, details))

        with patch.object(
            userbot.userbot_db,
            "get_broadcast_target_telegram_ids",
            return_value=[101],
        ), patch.object(
            userbot,
            "_send_broadcast_to_targets",
            new=send_mock,
        ):
            await userbot.handle_admin_text_input(update, context)

        send_mock.assert_awaited_once_with(
            context,
            [101],
            "متن همراه فایل عکس",
            "image-document-id",
        )

    async def test_stale_preview_state_recovers_after_update(self):
        message = SimpleNamespace(
            text="⏩رد کردن",
            photo=[],
            document=None,
            chat_id=999,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                userbot.BROADCAST_SEND_STATE: {
                    "segment": "all",
                    "step": "preview",
                    "text": "متن قدیمی",
                    "photo_file_id": "",
                }
            },
            bot=SimpleNamespace(),
        )

        await userbot.handle_admin_text_input(update, context)

        state = context.user_data[userbot.BROADCAST_SEND_STATE]
        self.assertEqual(state["step"], "wait_photo")
        self.assertIn("مسیر قدیمی پیش‌نمایش حذف شده", message.reply_text.await_args.args[0])

    async def test_transient_network_error_is_retried_and_recovered(self):
        context = SimpleNamespace(
            bot=SimpleNamespace(get_file=AsyncMock())
        )
        target_bot = SimpleNamespace(
            send_photo=AsyncMock(),
            send_message=AsyncMock(side_effect=[
                NetworkError("temporary"),
                SimpleNamespace(),
            ]),
        )

        with patch.object(userbot, "USER_BOT_TOKEN", "fake-user-token"), \
             patch.object(userbot, "Bot", return_value=target_bot), \
             patch.object(userbot.asyncio, "sleep", new=AsyncMock()):
            result = await userbot._send_broadcast_to_targets(
                context, [101], "hello"
            )

        self.assertEqual(result[:2], (1, 0))
        self.assertEqual(result[2]["recovered"], 1)
        self.assertEqual(target_bot.send_message.await_count, 2)

    def test_result_text_reports_real_delivery_counts(self):
        self.assertIn("3", userbot._broadcast_result_text(3, 0))
        self.assertIn("4", userbot._broadcast_result_text(0, 4))
        partial = userbot._broadcast_result_text(
            3,
            2,
            {"unreachable": 1, "temporary": 1},
        )
        self.assertIn("موفق: 3", partial)
        self.assertIn("ناموفق: 2", partial)
        self.assertIn("غیرقابل دسترس/مسدود: 1", partial)
        self.assertIn("خطای موقت پس از تلاش مجدد: 1", partial)
        self.assertIn("پیدا نشد", userbot._broadcast_result_text(0, 0))


if __name__ == "__main__":
    unittest.main()
