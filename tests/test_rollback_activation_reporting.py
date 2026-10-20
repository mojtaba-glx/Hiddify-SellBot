import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

from AdminBot import userbot
from Shared import userbot_db
from UserBot import delivery


class PatchServiceActivationReportingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = {
            "id": 5,
            "server_id": 1,
            "comment": "uuid:uuid-main",
            "server_title": "main",
        }
        self.server = {"id": 1, "title": "main"}

    async def test_success_reports_no_failures(self):
        with patch.object(
            delivery, "get_service_targets", return_value=[(self.server, "uuid-main")]
        ), patch.object(
            delivery, "_marzban_username_for", return_value=""
        ), patch.object(
            delivery.multi_panel, "patch_user", new=AsyncMock(return_value={})
        ) as patch_mock, patch.object(
            delivery.multi_panel, "enable_user", new=AsyncMock(return_value={})
        ) as enable_mock, patch.object(
            userbot_db, "set_service_node_active"
        ) as node_mock:
            changed, failed, activation_failed = await delivery.patch_service_on_panels(
                self.service, {"usage_limit_GB": 10}
            )

        self.assertEqual(changed, 1)
        self.assertEqual(failed, [])
        self.assertEqual(activation_failed, [])
        patch_mock.assert_awaited_once()
        enable_mock.assert_awaited_once()
        node_mock.assert_not_called()

    async def test_patch_failure_is_reported_without_enable_attempt(self):
        with patch.object(
            delivery, "get_service_targets", return_value=[(self.server, "uuid-main")]
        ), patch.object(
            delivery, "_marzban_username_for", return_value=""
        ), patch.object(
            delivery.multi_panel,
            "patch_user",
            new=AsyncMock(side_effect=RuntimeError("offline")),
        ), patch.object(
            delivery.multi_panel, "enable_user", new=AsyncMock()
        ) as enable_mock:
            changed, failed, activation_failed = await delivery.patch_service_on_panels(
                self.service, {"usage_limit_GB": 10}
            )

        self.assertEqual(changed, 0)
        self.assertEqual(failed, ["main"])
        self.assertEqual(activation_failed, [])
        enable_mock.assert_not_awaited()

    async def test_activation_failure_is_reported_and_node_marked_inactive(self):
        with patch.object(
            delivery, "get_service_targets", return_value=[(self.server, "uuid-main")]
        ), patch.object(
            delivery, "_marzban_username_for", return_value=""
        ), patch.object(
            delivery.multi_panel, "patch_user", new=AsyncMock(return_value={})
        ), patch.object(
            delivery.multi_panel,
            "enable_user",
            new=AsyncMock(side_effect=RuntimeError("activation rejected")),
        ), patch.object(
            userbot_db, "set_service_node_active"
        ) as node_mock:
            changed, failed, activation_failed = await delivery.patch_service_on_panels(
                self.service, {"usage_limit_GB": 10}
            )

        self.assertEqual(changed, 1)
        self.assertEqual(failed, [])
        self.assertEqual(activation_failed, ["main"])
        node_mock.assert_called_once_with(5, 1, "uuid-main", 0)


class RevertApprovedPaymentReportingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        snapshot = base64.b64encode(
            json.dumps(
                {"usage_limit": 10.0, "usage_current": 3.0, "days_left": 12}
            ).encode()
        ).decode()
        self.pay = {
            "id": 42,
            "telegram_id": 123,
            "receipt_image": (
                "pay_flow:direct_buy|renew_service_id:5|"
                f"renew_snapshot:{snapshot}"
            ),
        }
        self.service = {
            "id": 5,
            "server_id": 1,
            "comment": "uuid:uuid-main",
        }

    async def test_revert_reports_activation_failure_but_keeps_rollback_success(self):
        with patch.object(
            userbot_db, "get_service_by_id", return_value=self.service
        ), patch.object(
            userbot_db, "update_service_runtime"
        ) as update_mock, patch.object(
            userbot_db, "_patch_payment_receipt_meta"
        ), patch.object(
            delivery,
            "patch_service_on_panels",
            new=AsyncMock(return_value=(1, [], ["main"])),
        ):
            ok, message, failed = await userbot._revert_approved_payment(self.pay)

        self.assertTrue(ok)
        self.assertEqual(failed, [])
        self.assertIn("فعال‌سازی", message)
        self.assertIn("main", message)
        update_mock.assert_called_once()

    async def test_revert_without_activation_failure_keeps_previous_message(self):
        with patch.object(
            userbot_db, "get_service_by_id", return_value=self.service
        ), patch.object(
            userbot_db, "update_service_runtime"
        ), patch.object(
            userbot_db, "_patch_payment_receipt_meta"
        ), patch.object(
            delivery,
            "patch_service_on_panels",
            new=AsyncMock(return_value=(1, [], [])),
        ):
            ok, message, failed = await userbot._revert_approved_payment(self.pay)

        self.assertTrue(ok)
        self.assertEqual(failed, [])
        self.assertEqual(message, "🔁 تمدید به حالت قبل بازگشت.")

    async def test_revert_still_fails_when_no_server_patched(self):
        with patch.object(
            userbot_db, "get_service_by_id", return_value=self.service
        ), patch.object(
            userbot_db, "update_service_runtime"
        ), patch.object(
            userbot_db, "_patch_payment_receipt_meta"
        ), patch.object(
            delivery,
            "patch_service_on_panels",
            new=AsyncMock(return_value=(0, ["main"], [])),
        ):
            ok, message, failed = await userbot._revert_approved_payment(self.pay)

        self.assertFalse(ok)
        self.assertEqual(failed, ["main"])
        self.assertIn("هیچ سروری", message)


if __name__ == "__main__":
    unittest.main()
