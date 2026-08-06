#!/usr/bin/env python3
"""
Test suite for CustomerBot subscription status helpers.

Covers:
1. build_subscription_status_text — «📄اطلاعات اشتراک شما» format like UserBot
2. get_or_create_bot_sub_links — smart managed subscription links (QR targets)
3. Small helper parsers used by the above (comment parsing, config sanitize)
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from CustomerBot.services import (
    build_subscription_status_text,
    get_or_create_bot_sub_links,
    is_customer_service_visible,
    _parse_service_comment,
    _sanitize_config_text,
    _extract_config_link_from_line,
)
from CustomerBot.keyboards import plans_keyboard


def _sample_service(**overrides):
    """سرویس نمونه با server_id=0 تا به دیتابیس واقعی دست نزنیم."""
    svc = {
        "id": 42,
        "name": "پلن 30 گیگ - ترکیه",
        "server_id": 0,
        "server_title": "ترکیه",
        "usage_current": 5.5,
        "usage_limit": 30.0,
        "days_left": 12,
        "sale_price": 120000,
        "wholesale_price": 80000,
        "comment": "code:1234567",
    }
    svc.update(overrides)
    return svc


class TestBuildSubscriptionStatusText(unittest.TestCase):
    """تست فرمت «📄اطلاعات اشتراک شما» مطابق ربات کاربران."""

    def test_basic_format_matches_userbot(self):
        text = build_subscription_status_text(_sample_service(), {"show_username": True}, {})
        lines = text.splitlines()
        self.assertEqual(lines[0], "📄اطلاعات اشتراک شما")
        # کاراکتر «-» توسط escape_markdown به \- تبدیل می‌شود (رندر یکسان است)
        self.assertIn("👤نام: پلن 30 گیگ \\- ترکیه", lines)
        self.assertIn("📡سرور: ترکیه", lines)
        self.assertIn("📊میزان استفاده: 5.5 از 30.0 گیگ", lines)
        self.assertIn("⏳زمان باقی مانده: 12 روز", lines)
        self.assertIn("💰قیمت اشتراک: 120,000 تومان", lines)
        self.assertIn("🔑شناسه: `1234567`", lines)

    def test_no_code_falls_back_to_service_id(self):
        text = build_subscription_status_text(_sample_service(comment=""), {}, {})
        self.assertIn("🔑شناسه: `42`", text)

    def test_price_falls_back_to_wholesale_when_sale_price_missing(self):
        text = build_subscription_status_text(_sample_service(sale_price=0), {}, {})
        self.assertIn("💰قیمت اشتراک: 80,000 تومان", text)

    def test_show_username_false_hides_name_line(self):
        text = build_subscription_status_text(_sample_service(), {"show_username": False}, {})
        self.assertNotIn("👤نام:", text)

    def test_unlimited_volume_and_time(self):
        br = {
            "renew_unlimited_volume": True,
            "renew_unlimited_volume_from_gb": 1000,
            "renew_unlimited_time": True,
            "renew_unlimited_time_from_days": 365,
        }
        text = build_subscription_status_text(
            _sample_service(usage_limit=1000, days_left=400), {}, br
        )
        self.assertIn("📊میزان استفاده: 5.5 از نامحدود", text)
        self.assertIn("⏳زمان باقی مانده: نامحدود", text)

    def test_markdown_special_chars_are_escaped(self):
        text = build_subscription_status_text(
            _sample_service(name="پلن *30* گیگ", server_title="ترکیه_1"), {}, {}
        )
        self.assertIn("پلن \\*30\\* گیگ", text)
        self.assertIn("ترکیه\\_1", text)


class TestGetOrCreateBotSubLinks(unittest.TestCase):
    """تست ساخت لینک‌های اشتراک هوشمند (هدف QR)."""

    def setUp(self):
        self._saved = {}
        for key in (
            "SUB_SERVICE_BASE_URL",
            "SUB_SERVER_PUBLIC_HOST",
            "SUB_SERVER_PUBLIC_SCHEME",
            "SUB_SERVER_PUBLIC_PORT",
        ):
            self._saved[key] = os.environ.get(key)
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @patch("Shared.userbot_db.get_managed_sub_base_url", return_value="")
    def test_uses_sub_service_base_url_env(self, _mock_managed):
        os.environ["SUB_SERVICE_BASE_URL"] = "https://sub.example.com"
        svc = _sample_service(panel_user_uuid="uuid-123")
        link, link_b64 = get_or_create_bot_sub_links(svc)
        self.assertEqual(link, "https://sub.example.com/sub/uuid-123/all.txt")
        self.assertEqual(link_b64, "https://sub.example.com/sub/uuid-123/all.txt?base64=1")

    @patch("Shared.userbot_db.get_managed_sub_base_url", return_value="https://managed.example.com")
    def test_managed_base_url_has_priority_over_env(self, _mock_managed):
        os.environ["SUB_SERVICE_BASE_URL"] = "https://env.example.com"
        svc = _sample_service(panel_user_uuid="uuid-1")
        link, _ = get_or_create_bot_sub_links(svc)
        self.assertTrue(link.startswith("https://managed.example.com/"))

    @patch("Shared.userbot_db.get_managed_sub_base_url", return_value="")
    @patch("CustomerBot.services.get_service_node_base_urls")
    def test_falls_back_to_node_host_when_no_env(self, mock_node_urls, _mock_managed):
        os.environ["SUB_SERVER_PUBLIC_SCHEME"] = "https"
        os.environ["SUB_SERVER_PUBLIC_PORT"] = "443"
        mock_node_urls.return_value = ["https://node1.example.com/user/uuid-123"]
        svc = _sample_service(panel_user_uuid="uuid-123")
        link, link_b64 = get_or_create_bot_sub_links(svc)
        self.assertEqual(link, "https://node1.example.com/sub/uuid-123/all.txt")
        self.assertEqual(link_b64, "https://node1.example.com/sub/uuid-123/all.txt?base64=1")

    @patch("Shared.userbot_db.get_managed_sub_base_url", return_value="")
    def test_uses_service_id_as_token_when_no_uuid(self, _mock_managed):
        os.environ["SUB_SERVICE_BASE_URL"] = "https://sub.example.com"
        svc = _sample_service(panel_user_uuid="")
        link, link_b64 = get_or_create_bot_sub_links(svc)
        self.assertEqual(link, "https://sub.example.com/sub/42/all.txt")
        self.assertEqual(link_b64, "https://sub.example.com/sub/42/all.txt?base64=1")


class TestCustomerServiceVisibility(unittest.TestCase):
    """تست فیلترگذاری سرویس‌های نامعتبر/حذف‌شده در وضعیت اشتراک."""

    def test_deleted_or_missing_panel_uuid_is_hidden(self):
        self.assertFalse(is_customer_service_visible({"is_active": 1, "days_left": 25, "server_id": 5, "panel_user_uuid": ""}))
        self.assertFalse(is_customer_service_visible({"is_active": 1, "days_left": 25, "server_id": 0, "panel_user_uuid": "uuid-123"}))

    def test_valid_service_is_visible_even_if_days_left_is_negative_but_active(self):
        self.assertTrue(is_customer_service_visible({"is_active": 1, "days_left": -5, "server_id": 5, "panel_user_uuid": "uuid-123"}))

    def test_inactive_stale_service_is_hidden(self):
        self.assertFalse(is_customer_service_visible({"is_active": 0, "days_left": -10, "server_id": 5, "panel_user_uuid": "uuid-123"}))


class TestSubscriptionHelperParsers(unittest.TestCase):
    """تست توابع کمکی پارс مورد استفاده در وضعیت اشتراک."""

    def test_parse_service_comment(self):
        parsed = _parse_service_comment("uuid:abc|code:999|note:hi")
        self.assertEqual(parsed, {"uuid": "abc", "code": "999", "note": "hi"})

    def test_parse_service_comment_empty(self):
        self.assertEqual(_parse_service_comment(""), {})
        self.assertEqual(_parse_service_comment(None), {})

    def test_sanitize_config_text(self):
        self.assertEqual(_sanitize_config_text(" \u200f vless://x "), "vless://x")

    def test_extract_config_link_from_line(self):
        link = "vless://uuid@host:443?type=tcp"
        self.assertEqual(_extract_config_link_from_line(link), link)

    def test_extract_config_link_blocks_http_and_inline_text(self):
        self.assertEqual(_extract_config_link_from_line("https://x.com/all.txt"), "")
        self.assertEqual(_extract_config_link_from_line("text vless://x"), "")


class TestRenewPlanKeyboard(unittest.TestCase):
    def test_renew_plan_keyboard_uses_renew_prefix(self):
        kb = plans_keyboard([
            {"id": 7, "title": "30G/30D", "price": 250000, "gb": 30, "days": 30, "priority": 1},
        ], 12, 0, callback_prefix="renew")
        data = kb.inline_keyboard[0][0].callback_data
        self.assertEqual(data, "renew:plan:12:7")


if __name__ == "__main__":
    unittest.main()
