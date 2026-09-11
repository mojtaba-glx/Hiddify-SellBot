"""Per-agent SMS webhook isolation tests (real HTTP round-trips).

Each test spins up the real ThreadingHTTPServer from Shared.sub_http_server
on an ephemeral port and talks to it over HTTP. Only temporary databases
and a fake token are used; the real .env and operational databases are
never touched.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from AgentBot import database as agentbot_db
from CustomerBot import database as customer_db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
from Shared import agent_db, agent_sms_webhook, sub_http_server, userbot_db

FAKE_TOKEN = "123456789:AA_SMS_WEBHOOK_FAKE_TOKEN_NOT_REAL_0000000000"


def _http_post(url: str, secret: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-SellBot-Sms-Secret", secret)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            data = {}
        return e.code, data


class _ServerHarness:
    """Runs the real HTTP server on an ephemeral port with temp databases."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patchers = [
            patch.object(agentbot_db, "DB_FILE", root / "agent_bot.db"),
            patch.object(agent_db, "DB_PATH", root / "agency.db"),
            patch.object(userbot_db, "DB_PATH", root / "userbot.db"),
            patch.object(customer_db, "DB_PATH", root / "customer_bot.db"),
        ]
        for item in self.patchers:
            item.start()
        agent_db._db_initialized = False
        agent_db._init_db_path = ""
        agent_db.init_db()
        agentbot_db.init_db()
        userbot_db.init_db()
        customer_db.init_db()

        # Earlier test modules may have removed these from sys.modules; lazy
        # imports inside the server would then create FRESH unpached module
        # instances that touch the real databases. Re-register the patched
        # instances so every lazy import resolves to the same objects.
        self._modules_registered = {}
        for name, mod in {
            "AgentBot.database": agentbot_db,
            "CustomerBot.database": customer_db,
            "Shared.agent_db": agent_db,
            "Shared.userbot_db": userbot_db,
        }.items():
            self._modules_registered[name] = sys.modules.get(name)
            sys.modules[name] = mod

        # start the real HTTP server
        from http.server import ThreadingHTTPServer
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), sub_http_server._SubHandler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"

        # The rate-limit buckets are process-global; clear them so earlier
        # test files cannot push this server's requests into 429.
        sub_http_server._RATE_LIMIT_BUCKETS.clear()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        for name, old in self._modules_registered.items():
            if old is not None:
                sys.modules[name] = old
            else:
                sys.modules.pop(name, None)
        for item in reversed(self.patchers):
            item.stop()
        agent_db._db_initialized = False
        agent_db._init_db_path = ""
        self.tmp.cleanup()


class AgentSmsWebhookIsolationTests(unittest.TestCase):
    def setUp(self):
        self.h = _ServerHarness()
        self.h.__enter__()
        self.addCleanup(self.h.__exit__, None, None, None)
        # two isolated agents
        self.agent_a = agent_db.upsert_agent(telegram_id=20001, username="agent-a")
        self.agent_b = agent_db.upsert_agent(telegram_id=20002, username="agent-b")
        settings_a = agent_sms_webhook.ensure_agent_sms_settings(self.agent_a)
        settings_b = agent_sms_webhook.ensure_agent_sms_settings(self.agent_b)
        agent_sms_webhook.set_agent_sms_enabled(self.agent_a, True)
        agent_sms_webhook.set_agent_sms_enabled(self.agent_b, True)
        # enable the agents' own auto-confirm (their personal per-agent flag)
        agentbot_db.set_setting(self.agent_a, "sms_auto_confirm", True)
        agentbot_db.set_setting(self.agent_b, "sms_auto_confirm", True)
        self.secret_a = settings_a["secret"]
        self.secret_b = settings_b["secret"]
        self.url_a = f"{self.h.base}{agent_sms_webhook.agent_webhook_path(self.agent_a)}"
        self.url_b = f"{self.h.base}{agent_sms_webhook.agent_webhook_path(self.agent_b)}"
        self.url_central = f"{self.h.base}/payment/sms-webhook"

    def _agent_customer_payment(self, agent_id: int, amount=100_000):
        """Create a pending customer card payment for this agent (direct DB)."""
        conn = customer_db._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO customer_payments (agent_id, tx_code, user_id, amount, method, status, created_at) "
                "VALUES (?, ?, NULL, ?, 'card', 'pending', ?)",
                (agent_id, f"tx-{agent_id}-{amount}-{int(time.time()*1000)%100000}", amount, customer_db._now()),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    # ---- 1. independent address/secret ----

    def test_agents_have_independent_paths_and_secrets(self):
        self.assertNotEqual(self.secret_a, self.secret_b)
        self.assertIn(f"/agent/{self.agent_a}/", self.url_a)
        self.assertIn(f"/agent/{self.agent_b}/", self.url_b)
        self.assertNotEqual(self.url_a, self.url_b)

    def test_secret_a_rejected_on_b_route_and_central(self):
        code_b, body_b = _http_post(self.url_b, self.secret_a, {
            "event_id": "iso-a-on-b", "amount": 100000, "currency": "toman",
        })
        self.assertEqual(code_b, 401, body_b)
        code_c, body_c = _http_post(self.url_central, self.secret_a, {
            "event_id": "iso-a-on-central", "amount": 100000, "currency": "toman",
        })
        self.assertIn(code_c, (401, 403), body_c)

    def test_central_secret_rejected_on_agent_route(self):
        from Shared import userbot_db as udb
        code, body = _http_post(self.url_a, "some-other-central-secret-value-0123456789abcdef", {
            "event_id": "iso-central-on-a", "amount": 100000, "currency": "toman",
        })
        self.assertIn(code, (401, 403), body)

    def test_agent_secret_cannot_credit_own_wholesale_wallet(self):
        """A valid agent secret can never approve the agent's own wallet top-up."""
        payment = agentbot_db.create_wallet_charge_payment(
            agent_id=self.agent_a,
            agent_name="Agent A",
            base_amount=100_000,
            marker_amount=77,
            receipt_file_id="fake",
            card_last4="1234",
        )
        total = int(payment["amount"])
        code, body = _http_post(self.url_a, self.secret_a, {
            "event_id": "iso-wallet-1", "amount": total, "currency": "toman",
            "card_last4": "1234",
        })
        self.assertIn(body.get("status"), {"no_pending_match", "agent_auto_disabled"}, body)
        self.assertEqual(agentbot_db.get_payment_by_id(payment["id"])["status"], "pending")
        self.assertEqual(agent_db.get_wallet_balance(self.agent_a), 0)

    def test_agent_customers_same_amount_not_cross_matched(self):
        pay_a = self._agent_customer_payment(self.agent_a, 100_000)
        pay_b = self._agent_customer_payment(self.agent_b, 100_000)
        # enable auto-confirm for both
        agentbot_db.set_setting(self.agent_a, "sms_auto_confirm", True)
        agentbot_db.set_setting(self.agent_b, "sms_auto_confirm", True)

        code, body = _http_post(self.url_a, self.secret_a, {
            "event_id": "cross-a-1", "amount": 100000, "currency": "toman",
        })
        self.assertEqual(code, 200, body)
        self.assertEqual(body.get("status"), "agency_queued", body)
        self.assertEqual(body.get("agent_id"), self.agent_a)

        # A's payment queued exactly once, B's untouched
        queue_a = customer_db.fetch_pending_sms_auto_queue(limit=50)
        matching = [r for r in queue_a if int(r.get("agent_id") or 0) == self.agent_a
                    and int(r.get("pay_id") or 0) == pay_a]
        b_rows = [r for r in queue_a if int(r.get("agent_id") or 0) == self.agent_b]
        self.assertEqual(len(matching), 1)
        self.assertEqual(b_rows, [])

        code2, body2 = _http_post(self.url_b, self.secret_b, {
            "event_id": "cross-b-1", "amount": 100000, "currency": "toman",
        })
        self.assertEqual(code2, 200, body2)
        self.assertEqual(body2.get("agent_id"), self.agent_b)

    def test_same_sms_replayed_across_scopes_cannot_double_match(self):
        """One SMS (same event_id) accepted on agent route must not also
        match/queue again on the central route or another agent's route."""
        pay_a = self._agent_customer_payment(self.agent_a, 100_000)
        agentbot_db.set_setting(self.agent_a, "sms_auto_confirm", True)
        payload = {"event_id": "replay-1", "amount": 100000, "currency": "toman"}
        code, body = _http_post(self.url_a, self.secret_a, payload)
        self.assertEqual(body.get("status"), "agency_queued", body)

        # replay same event_id on central route with the admin secret path:
        # central uses a different secret; with a wrong secret it is rejected
        code2, _ = _http_post(self.url_central, self.secret_a, payload)
        self.assertIn(code2, (401, 403))

        # replay on agent A's own route: duplicate must NOT enqueue twice
        code3, body3 = _http_post(self.url_a, self.secret_a, payload)
        self.assertEqual(code3, 200, body3)
        queue = customer_db.fetch_pending_sms_auto_queue(limit=50)
        rows = [r for r in queue if r.get("event_id") == "replay-1"]
        self.assertEqual(len(rows), 1, "duplicate SMS must not enqueue twice")

    def test_toggle_agent_a_does_not_affect_b_or_admin(self):
        agent_sms_webhook.set_agent_sms_enabled(self.agent_a, False)
        self.assertFalse(agent_sms_webhook.is_agent_sms_enabled(self.agent_a))
        self.assertTrue(agent_sms_webhook.is_agent_sms_enabled(self.agent_b))
        # A disabled -> its route rejects before any matching
        code_a, _ = _http_post(self.url_a, self.secret_a, {
            "event_id": "toggle-a", "amount": 100000, "currency": "toman"})
        self.assertEqual(code_a, 403)
        # B unaffected — accepts requests normally (no pending payment yet -> 202)
        code_b, body_b = _http_post(self.url_b, self.secret_b, {
            "event_id": "toggle-b", "amount": 100000, "currency": "toman"})
        self.assertEqual(code_b, 202, body_b)
        self.assertEqual(body_b.get("status"), "no_pending_match", body_b)
        # central admin .env keys are never written by agent toggles
        env_path = Path(PROJECT_ROOT) / ".env"
        before = env_path.read_bytes() if env_path.exists() else b""
        agent_sms_webhook.set_agent_sms_enabled(self.agent_a, True)
        after = env_path.read_bytes() if env_path.exists() else b""
        self.assertEqual(before, after, "agent toggle must not touch the central .env")
        # re-enabled A accepts again (auto-confirm on, no pending -> no match)
        code_a2, body_a2 = _http_post(self.url_a, self.secret_a, {
            "event_id": "toggle-a2", "amount": 100000, "currency": "toman"})
        self.assertEqual(code_a2, 202, body_a2)
        self.assertEqual(body_a2.get("status"), "no_pending_match", body_a2)

    def test_disabled_agent_route_rejected(self):
        agent_db.set_agent_active(self.agent_a, False)
        code, body = _http_post(self.url_a, self.secret_a, {
            "event_id": "disabled-a", "amount": 100000, "currency": "toman"})
        self.assertEqual(code, 403, body)
        self.assertEqual(body.get("error"), "agent_disabled")
        agent_db.set_agent_active(self.agent_a, True)

    def test_secret_rotation_isolated(self):
        old_a = self.secret_a
        agent_sms_webhook.regenerate_agent_secret(self.agent_a)
        new_a = agent_sms_webhook.get_agent_sms_settings(self.agent_a)["secret"]
        self.assertNotEqual(old_a, new_a)
        code_old, _ = _http_post(self.url_a, old_a, {
            "event_id": "rot-1", "amount": 100000, "currency": "toman"})
        self.assertEqual(code_old, 401)
        code_new, body_new = _http_post(self.url_a, new_a, {
            "event_id": "rot-2", "amount": 100000, "currency": "toman"})
        self.assertIn(code_new, (200, 202), body_new)
        # B unaffected
        code_b, body_b = _http_post(self.url_b, self.secret_b, {
            "event_id": "rot-b", "amount": 100000, "currency": "toman"})
        self.assertIn(code_b, (200, 202), body_b)

    def test_manual_recent_agent_approval_attaches_late_sms(self):
        pay_a = self._agent_customer_payment(self.agent_a, 100_000)
        customer_db.update_payment_status(self.agent_a, pay_a, "approved")
        code, body = _http_post(self.url_a, self.secret_a, {
            "event_id": "manual-late-agent", "amount": 100000, "currency": "toman",
        })
        self.assertEqual(code, 200, body)
        self.assertEqual(body.get("status"), "attached_manual_agent_customer", body)
        self.assertEqual(body.get("payment_id"), pay_a)
        # no queue entry for a manual attach
        queue = customer_db.fetch_pending_sms_auto_queue(limit=50)
        self.assertEqual([r for r in queue if int(r.get("pay_id") or 0) == pay_a], [])

    def test_agent_auto_disabled_stops_financial_processing(self):
        pay_a = self._agent_customer_payment(self.agent_a, 100_000)
        agentbot_db.set_setting(self.agent_a, "sms_auto_confirm", False)
        code, body = _http_post(self.url_a, self.secret_a, {
            "event_id": "autooff-1", "amount": 100000, "currency": "toman",
        })
        self.assertEqual(code, 202, body)
        self.assertEqual(body.get("status"), "agent_auto_disabled", body)
        # payment stays pending
        conn = customer_db._get_conn()
        try:
            row = conn.execute(
                "SELECT status FROM customer_payments WHERE id=?", (pay_a,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["status"], "pending")


if __name__ == "__main__":
    unittest.main()
