import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import ReplyKeyboardMarkup

from AdminBot import plans


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.chat_id = 1234
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class TestAdminDynamicPlanReplyKeyboard(unittest.IsolatedAsyncioTestCase):
    def assert_admin_keyboard_restored(self, reply_markup):
        self.assertIsInstance(reply_markup, ReplyKeyboardMarkup)
        button_texts = [button.text for row in reply_markup.keyboard for button in row]
        self.assertIn("🖥️مدیریت سرورها", button_texts)
        self.assertNotIn("لغو❌", button_texts)

    async def test_dynamic_setting_save_restores_admin_keyboard(self):
        message = FakeMessage("8000")
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                "state": plans.PLANS_STATE_EDIT_DYNAMIC_FIELD,
                "plans_server_id": 1,
                "plans_dyn_action": "price_per_gb",
            }
        )

        with (
            patch.object(plans.plans_storage, "set_plan_dynamic_settings") as set_settings,
            patch.object(plans, "_send_dynamic_settings_menu", new=AsyncMock()) as send_menu,
        ):
            await plans.handle_plans_message(
                plans.PLANS_STATE_EDIT_DYNAMIC_FIELD,
                update,
                context,
            )

        set_settings.assert_called_once_with(1, price_per_gb=8000)
        send_menu.assert_awaited_once()
        self.assert_admin_keyboard_restored(message.replies[0][1].get("reply_markup"))
        self.assertNotIn("state", context.user_data)
        self.assertNotIn("plans_dyn_action", context.user_data)

    async def test_discount_save_restores_admin_keyboard(self):
        message = FakeMessage("25")
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                "state": plans.PLANS_STATE_EDIT_DYNAMIC_FIELD,
                "plans_server_id": 1,
                "plans_dyn_action": "discount",
                "plans_dyn_discount_phase": "percent",
                "plans_dyn_discount_threshold": 50,
            }
        )

        with (
            patch.object(plans.plans_storage, "set_plan_dynamic_settings") as set_settings,
            patch.object(plans, "_send_dynamic_settings_menu", new=AsyncMock()) as send_menu,
        ):
            await plans.handle_plans_message(
                plans.PLANS_STATE_EDIT_DYNAMIC_FIELD,
                update,
                context,
            )

        set_settings.assert_called_once_with(
            1,
            discount_step_gb=50,
            discount_percent_step=25,
            discount_percent_max=25,
            discount_tiers=[],
        )
        send_menu.assert_awaited_once()
        self.assert_admin_keyboard_restored(message.replies[0][1].get("reply_markup"))
        self.assertNotIn("state", context.user_data)
        self.assertNotIn("plans_dyn_action", context.user_data)

    async def test_discount_tiers_save_restores_admin_keyboard(self):
        message = FakeMessage("50:5, 100:10")
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                "state": plans.PLANS_STATE_EDIT_DYNAMIC_FIELD,
                "plans_server_id": 1,
                "plans_dyn_action": "discount_tiers",
            }
        )

        with (
            patch.object(plans.plans_storage, "set_plan_dynamic_settings") as set_settings,
            patch.object(plans, "_send_dynamic_settings_menu", new=AsyncMock()) as send_menu,
        ):
            await plans.handle_plans_message(
                plans.PLANS_STATE_EDIT_DYNAMIC_FIELD,
                update,
                context,
            )

        set_settings.assert_called_once_with(
            1,
            discount_tiers=[{"gb": 50, "percent": 5}, {"gb": 100, "percent": 10}],
            discount_step_gb=0,
            discount_percent_step=0,
            discount_percent_max=0,
        )
        send_menu.assert_awaited_once()
        self.assert_admin_keyboard_restored(message.replies[0][1].get("reply_markup"))
        self.assertNotIn("state", context.user_data)
        self.assertNotIn("plans_dyn_action", context.user_data)

    async def test_dynamic_setting_cancel_returns_to_dynamic_menu(self):
        message = FakeMessage("لغو❌")
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                "state": plans.PLANS_STATE_EDIT_DYNAMIC_FIELD,
                "plans_server_id": 1,
                "plans_dyn_action": "price_per_gb",
            }
        )

        with patch.object(plans, "_send_dynamic_settings_menu", new=AsyncMock()) as send_menu:
            await plans.handle_plans_message(
                plans.PLANS_STATE_EDIT_DYNAMIC_FIELD,
                update,
                context,
            )

        self.assertEqual(message.replies[0][0], "❌ عملیات لغو شد.")
        self.assert_admin_keyboard_restored(message.replies[0][1].get("reply_markup"))
        send_menu.assert_awaited_once_with(1, message.chat_id, context)
        self.assertNotIn("state", context.user_data)
        self.assertNotIn("plans_server_id", context.user_data)
        self.assertNotIn("plans_dyn_action", context.user_data)


if __name__ == "__main__":
    unittest.main()
