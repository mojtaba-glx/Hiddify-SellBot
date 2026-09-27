import unittest

from Shared.subscription_reports import _customer_profile_button_title


class CustomerProfileButtonTitleTests(unittest.TestCase):
    def test_prefers_full_name(self):
        customer = {"full_name": "Ali Reza", "username": "ali_user", "telegram_id": 123}
        self.assertEqual(_customer_profile_button_title(customer, 123), "👤 Ali Reza")

    def test_falls_back_to_username(self):
        customer = {"full_name": "", "username": "@ali_user", "telegram_id": 123}
        self.assertEqual(_customer_profile_button_title(customer, 123), "👤 @ali_user")

    def test_falls_back_to_telegram_id(self):
        customer = {"full_name": "", "username": "", "telegram_id": 123}
        self.assertEqual(_customer_profile_button_title(customer, 123), "👤 123")

    def test_long_name_is_trimmed(self):
        customer = {"full_name": "A" * 60, "username": "", "telegram_id": 123}
        title = _customer_profile_button_title(customer, 123)
        self.assertTrue(title.startswith("👤 "))
        self.assertTrue(title.endswith("..."))
        self.assertLessEqual(len(title), 47)


if __name__ == "__main__":
    unittest.main()
