import unittest

from Shared.top_buying_customers_report import format_top_buying_customers_report


class TopBuyingCustomersReportFormattingTests(unittest.TestCase):
    def test_report_includes_30_day_period_approved_filter_ids_and_manual_reward_note(self):
        report = format_top_buying_customers_report(
            [
                {
                    "username": "alice",
                    "full_name": "Alice Example",
                    "telegram_id": 123456,
                    "total_spent": 125000,
                    "orders_count": 3,
                }
            ],
            days=30,
        )

        self.assertIn("۳۰ روز اخیر", report)
        self.assertIn("approved", report)
        self.assertIn("@alice", report)
        self.assertIn("ID: 123456", report)
        self.assertIn("125,000", report)
        self.assertIn("3", report)
        self.assertIn("پاداش مشتریان به‌صورت دستی", report)

    def test_empty_report_is_clear_and_does_not_claim_a_total(self):
        report = format_top_buying_customers_report([], days=30)

        self.assertIn("خرید تأییدشده‌ای پیدا نشد", report)
        self.assertIn("پاداش مشتریان به‌صورت دستی", report)
        self.assertNotIn("مجموع خرید این", report)

    def test_customer_names_are_html_escaped(self):
        report = format_top_buying_customers_report(
            [
                {
                    "username": "<b>bad</b>",
                    "telegram_id": 123,
                    "total_spent": 1000,
                    "orders_count": 1,
                }
            ]
        )

        self.assertIn("&lt;b&gt;bad&lt;/b&gt;", report)
        self.assertNotIn("<pre>\n 1   | @<b>bad</b>", report)


if __name__ == "__main__":
    unittest.main()
