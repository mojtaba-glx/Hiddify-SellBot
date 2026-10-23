import unittest
from unittest.mock import AsyncMock, patch

from AgentBot.handlers import settings_customer_payments as payments


class CustomerOrderRenewalGuardTests(unittest.IsolatedAsyncioTestCase):
    def _service(self):
        return {
            "id": 1,
            "agent_id": 1,
            "customer_id": 5,
            "server_id": 1,
            "name": "customer-zero-day",
            "usage_limit": 10,
            "usage_current": 0,
            "days_left": 10,
            "last_payment_operation_key": "",
        }

    async def test_reset_zero_days_rejected_before_panel_patch(self):
        order = {"renew_service_id": 1, "days": 0, "volume_gb": 10}
        with patch.object(
            payments.agent_db, "get_service_by_id", return_value=self._service()
        ), patch.object(
            payments.agent_db,
            "get_customer_by_telegram_id",
            return_value={"id": 5},
        ), patch(
            "Shared.userbot_db.get_renew_modes", return_value=("reset", "reset")
        ), patch(
            "CustomerBot.services.get_service_panel_targets"
        ) as panel_targets, patch(
            "Shared.multi_panel.patch_user", new_callable=AsyncMock
        ) as patch_user:
            with self.assertRaisesRegex(RuntimeError, "invalid_renew_duration"):
                await payments._renew_subscription_from_order(
                    None, 1, 999, order
                )
            panel_targets.assert_not_called()
            patch_user.assert_not_called()

    async def test_reset_zero_volume_rejected_before_panel_patch(self):
        order = {"renew_service_id": 1, "days": 30, "volume_gb": 0}
        with patch.object(
            payments.agent_db, "get_service_by_id", return_value=self._service()
        ), patch.object(
            payments.agent_db,
            "get_customer_by_telegram_id",
            return_value={"id": 5},
        ), patch(
            "Shared.userbot_db.get_renew_modes", return_value=("reset", "reset")
        ), patch(
            "CustomerBot.services.get_service_panel_targets"
        ) as panel_targets, patch(
            "Shared.multi_panel.patch_user", new_callable=AsyncMock
        ) as patch_user:
            with self.assertRaisesRegex(RuntimeError, "invalid_renew_volume"):
                await payments._renew_subscription_from_order(
                    None, 1, 999, order
                )
            panel_targets.assert_not_called()
            patch_user.assert_not_called()


if __name__ == "__main__":
    unittest.main()
