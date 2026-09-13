import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

        self.assertEqual(result, (2, 0))
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

        self.assertEqual(result, (2, 1))
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

        self.assertEqual(result, (2, 0))
        context.bot.get_file.assert_not_awaited()
        target_bot.send_photo.assert_not_awaited()
        self.assertEqual(target_bot.send_message.await_count, 2)

    def test_result_text_reports_real_delivery_counts(self):
        self.assertIn("3", userbot._broadcast_result_text(3, 0))
        self.assertIn("4", userbot._broadcast_result_text(0, 4))
        partial = userbot._broadcast_result_text(3, 2)
        self.assertIn("موفق: 3", partial)
        self.assertIn("ناموفق: 2", partial)
        self.assertIn("پیدا نشد", userbot._broadcast_result_text(0, 0))


if __name__ == "__main__":
    unittest.main()
