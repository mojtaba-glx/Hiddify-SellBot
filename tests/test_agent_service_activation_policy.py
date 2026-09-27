import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from AgentBot.handlers import subscriptions
from AgentBot.services import subscription_service


def _callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]


class AgentServiceActivationPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_customerbot_service_never_exposes_agent_renew(self):
        svc = {
            "id": 10,
            "agent_id": 1,
            "customer_id": 77,
            "is_active": 1,
            "usage_current": 1,
            "usage_limit": 20,
            "days_left": 12,
            "end_date": "2099-01-01 00:00:00",
        }
        callbacks = _callbacks(subscriptions._service_detail_keyboard_for(svc))
        self.assertNotIn("agbot:subs:renew:10", callbacks)
        self.assertIn("agbot:subs:disable:10", callbacks)

    def test_expired_customerbot_service_keeps_enable_but_no_agent_renew(self):
        svc = {
            "id": 11,
            "agent_id": 1,
            "customer_id": 77,
            "is_active": 0,
            "usage_current": 20,
            "usage_limit": 20,
            "days_left": -1,
            "end_date": "2020-01-01 00:00:00",
        }
        callbacks = _callbacks(subscriptions._service_detail_keyboard_for(svc))
        self.assertIn("agbot:subs:enable:11", callbacks)
        self.assertNotIn("agbot:subs:renew:11", callbacks)

    def test_expired_direct_agent_service_exposes_renew_and_enable(self):
        svc = {
            "id": 12,
            "agent_id": 1,
            "customer_id": None,
            "is_active": 0,
            "usage_current": 20,
            "usage_limit": 20,
            "days_left": -1,
            "end_date": "2020-01-01 00:00:00",
        }
        callbacks = _callbacks(subscriptions._service_detail_keyboard_for(svc))
        self.assertIn("agbot:subs:renew:12", callbacks)
        self.assertIn("agbot:subs:enable:12", callbacks)

    async def test_expired_service_cannot_be_enabled_in_backend(self):
        svc = {
            "id": 13,
            "agent_id": 1,
            "customer_id": None,
            "is_active": 0,
            "usage_current": 10,
            "usage_limit": 10,
            "days_left": 5,
            "end_date": "2099-01-01 00:00:00",
        }
        with patch.object(
            subscription_service.agent_db,
            "get_service_by_id",
            return_value=svc,
        ), patch.object(
            subscription_service,
            "_set_subscription_active_on_all_targets",
            new=AsyncMock(return_value=True),
        ) as setter:
            ok = await subscription_service.enable_subscription(1, 13)

        self.assertFalse(ok)
        setter.assert_not_awaited()

    async def test_valid_manually_disabled_service_can_be_enabled(self):
        svc = {
            "id": 14,
            "agent_id": 1,
            "customer_id": 77,
            "is_active": 0,
            "usage_current": 3,
            "usage_limit": 10,
            "days_left": 5,
            "end_date": "2099-01-01 00:00:00",
        }
        with patch.object(
            subscription_service.agent_db,
            "get_service_by_id",
            return_value=svc,
        ), patch.object(
            subscription_service,
            "_set_subscription_active_on_all_targets",
            new=AsyncMock(return_value=True),
        ) as setter:
            ok = await subscription_service.enable_subscription(1, 14)

        self.assertTrue(ok)
        setter.assert_awaited_once_with(svc, True)

    async def test_customerbot_owned_service_cannot_use_agent_renew_backend(self):
        svc = {
            "id": 15,
            "agent_id": 1,
            "customer_id": 77,
            "server_id": 1,
        }
        with patch.object(
            subscription_service.agent_db,
            "get_service_by_id",
            return_value=svc,
        ), patch.object(
            subscription_service.agent_db,
            "deduct_wallet",
        ) as deduct:
            result = await subscription_service.renew_subscription(
                1, 15, 30, extra_gb=10
            )

        self.assertIsNone(result)
        deduct.assert_not_called()

    async def test_expired_customer_enable_callback_shows_customerbot_warning(self):
        svc = {
            "id": 16,
            "agent_id": 1,
            "customer_id": 77,
            "is_active": 0,
            "usage_current": 10,
            "usage_limit": 10,
            "days_left": -1,
            "end_date": "2020-01-01 00:00:00",
        }
        query = AsyncMock()
        query.data = "agbot:subs:enable:16"
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})

        with patch.object(
            subscriptions,
            "get_agent_id",
            return_value=1,
        ), patch.object(
            subscriptions.agent_db,
            "get_service_by_id",
            return_value=svc,
        ), patch.object(
            subscriptions,
            "enable_subscription",
            new=AsyncMock(return_value=True),
        ) as enable:
            await subscriptions.handle_callback(update, context)

        enable.assert_not_awaited()
        query.edit_message_text.assert_not_awaited()
        args, kwargs = query.answer.await_args
        self.assertIn("ربات مشتری", args[0])
        self.assertTrue(kwargs.get("show_alert"))


if __name__ == "__main__":
    unittest.main()
