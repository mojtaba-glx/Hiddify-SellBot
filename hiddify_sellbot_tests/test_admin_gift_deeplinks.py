import unittest

from AdminBot import userbot


class TestAdminGiftDeepLinks(unittest.TestCase):
    def test_build_telegram_start_link_normalizes_username(self):
        link = userbot._build_telegram_start_link("@sellbot_user_bot", "GIFT_123")

        self.assertEqual(link, "https://t.me/sellbot_user_bot?start=GIFT_123")

    def test_build_telegram_start_link_requires_username_and_payload(self):
        self.assertEqual(userbot._build_telegram_start_link("", "GIFT_123"), "")
        self.assertEqual(userbot._build_telegram_start_link("sellbot_user_bot", ""), "")


if __name__ == "__main__":
    unittest.main()
