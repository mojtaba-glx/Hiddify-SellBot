import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from AgentBot import database as agentbot_db
from CustomerBot import database as customer_db
from Shared import agent_db, sub_http_server, userbot_db
from Shared.agent_wallet_payments import (
    approve_wallet_charge_from_sms,
    try_approve_wallet_charge_from_unmatched_sms,
)


class AgentWalletSmsApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patchers = [
            patch.object(agentbot_db, "DB_FILE", root / "agent_bot.db"),
            patch.object(agent_db, "DB_PATH", root / "agency.db"),
            patch.object(userbot_db, "DB_PATH", root / "userbot.db"),
        ]
        for item in self.patchers:
            item.start()
        agent_db._db_initialized = False
        agent_db._init_db_path = ""
        agent_db.init_db()
        agentbot_db.init_db()
        userbot_db.init_db()
        self.agent_id = agent_db.upsert_agent(telegram_id=10001, username="reseller")

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        agent_db._db_initialized = False
        agent_db._init_db_path = ""
        self.tmp.cleanup()

    def _payment(self, amount=100_000, marker=731, last4="1234"):
        return agentbot_db.create_wallet_charge_payment(
            agent_id=self.agent_id,
            agent_name="Reseller",
            base_amount=amount,
            marker_amount=marker,
            receipt_file_id="fake-receipt",
            card_last4=last4,
        )

    def _event(self, event_id, amount, last4="1234", status="received"):
        userbot_db.record_sms_webhook_event(
            {
                "event_id": event_id,
                "sender": "BANK",
                "amount_raw": amount,
                "currency_raw": "toman",
                "amount_toman": amount,
                "reference": f"ref-{event_id}",
                "card_last4": last4,
                "body": f"deposit {amount}",
                "status": status,
                "received_at": int(time.time() * 1000),
                "device_time": int(time.time() * 1000),
            }
        )

    def test_live_webhook_approves_wallet_once(self):
        payment = self._payment()
        total = int(payment["amount"])
        self._event("wallet-live-1", total)
        handler = object.__new__(sub_http_server._SubHandler)

        with patch.object(customer_db, "find_pending_card_payments_by_amount", return_value=[]), patch.object(
            sub_http_server, "_send_agent_wallet_sms_payment_report"
        ) as report:
            code, response = handler._try_approve_sms_event(
                event_id="wallet-live-1",
                amount_raw=total,
                currency_raw="toman",
                reference="bank-ref-1",
                sender="BANK",
                card_last4="1234",
                body=f"deposit {total}",
                received_at_ms=int(time.time() * 1000),
                device_time_ms=0,
            )

        self.assertEqual(code, 200)
        self.assertEqual(response["scope"], "agent_wallet")
        self.assertEqual(agentbot_db.get_payment_by_id(payment["id"])["status"], "approved")
        self.assertEqual(agent_db.get_wallet_balance(self.agent_id), total)
        report.assert_called_once()

        ok, _, _, _, newly_approved = approve_wallet_charge_from_sms(
            payment["id"],
            event_id="wallet-live-1",
            reference="bank-ref-1",
            sender="BANK",
            amount_raw=total,
            currency_raw="toman",
        )
        self.assertTrue(ok)
        self.assertFalse(newly_approved)
        self.assertEqual(agent_db.get_wallet_balance(self.agent_id), total)

    def test_sms_arriving_before_receipt_is_retried_when_payment_is_created(self):
        total = 150_000 + 845
        self._event("wallet-early-1", total, status="no_pending_match")
        payment = self._payment(amount=150_000, marker=845)

        result, event = try_approve_wallet_charge_from_unmatched_sms(payment["id"])
        ok, _, updated, wallet, newly_approved = result

        self.assertTrue(ok)
        self.assertTrue(newly_approved)
        self.assertEqual(event["event_id"], "wallet-early-1")
        self.assertEqual(updated["status"], "approved")
        self.assertEqual(wallet["balance"], total)
        self.assertEqual(userbot_db.get_sms_webhook_event("wallet-early-1")["status"], "approved")

    def test_two_concurrent_bank_events_cannot_claim_one_wallet_payment(self):
        payment = self._payment()
        total = int(payment["amount"])

        def approve(event_id):
            return approve_wallet_charge_from_sms(
                payment["id"],
                event_id=event_id,
                reference=event_id,
                sender="BANK",
                amount_raw=total,
                currency_raw="toman",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(approve, ("concurrent-a", "concurrent-b")))

        self.assertEqual(sum(1 for result in results if result[0]), 1)
        self.assertEqual(agent_db.get_wallet_balance(self.agent_id), total)
        conn = agent_db._get_conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM agent_transactions WHERE tx_type='charge'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def test_wrong_payer_card_does_not_match(self):
        payment = self._payment(last4="1234")
        matches = agentbot_db.find_pending_wallet_charge_payments_by_amount(
            payment["amount"],
            card_last4="9999",
            sms_time_ms=int(time.time() * 1000),
        )
        self.assertEqual(matches, [])

    def test_late_sms_attaches_to_manual_approval_without_second_credit(self):
        payment = self._payment()
        total = int(payment["amount"])
        key = f"agent-wallet-payment:{payment['id']}"
        self.assertTrue(agentbot_db.claim_payment_processing(payment["id"], self.agent_id, key))
        agent_db.charge_wallet_once(self.agent_id, total, key)
        self.assertTrue(
            agentbot_db.finish_payment_processing(
                payment["id"], self.agent_id, key, "approved"
            )
        )
        self._event("wallet-after-manual-1", total)
        handler = object.__new__(sub_http_server._SubHandler)

        code, response = handler._try_approve_sms_event(
            event_id="wallet-after-manual-1",
            amount_raw=total,
            currency_raw="toman",
            reference="manual-late-ref",
            sender="BANK",
            card_last4="1234",
            body=f"deposit {total}",
            received_at_ms=int(time.time() * 1000),
            device_time_ms=0,
        )

        self.assertEqual(code, 200)
        self.assertEqual(response["status"], "attached_manual_agent_wallet")
        self.assertEqual(agent_db.get_wallet_balance(self.agent_id), total)
        updated = agentbot_db.get_payment_by_id(payment["id"])
        self.assertEqual(updated["sms_event_id"], "wallet-after-manual-1")

    def test_agent_and_customer_same_amount_is_ambiguous(self):
        payment = self._payment()
        total = int(payment["amount"])
        self._event("wallet-ambiguous-1", total)
        handler = object.__new__(sub_http_server._SubHandler)
        fake_customer = {"id": 77, "agent_id": self.agent_id, "amount": total}

        with patch.object(customer_db, "find_pending_card_payments_by_amount", return_value=[fake_customer]), patch.object(
            sub_http_server, "_send_agent_wallet_sms_payment_report"
        ) as report:
            code, response = handler._try_approve_sms_event(
                event_id="wallet-ambiguous-1",
                amount_raw=total,
                currency_raw="toman",
                reference="bank-ref-ambiguous",
                sender="BANK",
                card_last4="1234",
                body=f"deposit {total}",
                received_at_ms=int(time.time() * 1000),
                device_time_ms=0,
            )

        # The central webhook no longer touches agency customer payments at
        # all: the wallet top-up (admin scope) is approved cleanly and the
        # customer payment of the agency can only ever be matched through
        # that agent's own dedicated webhook route.
        self.assertEqual(code, 200)
        self.assertEqual(response["scope"], "agent_wallet")
        self.assertEqual(agentbot_db.get_payment_by_id(payment["id"])["status"], "approved")
        self.assertEqual(agent_db.get_wallet_balance(self.agent_id), total)
        report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
