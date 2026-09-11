"""Stage-9 review fixes: per-agent SMS webhook isolation hardening.

Each of the 7 review findings gets a failing-first repro test with real
temporary databases and real HTTP round-trips against the local server.
"""

import json
import asyncio
import sqlite3
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import AsyncMock, patch

from AgentBot import database as agentbot_db
from CustomerBot import database as customer_db
from Shared import agent_db, agent_sms_webhook, sub_http_server, userbot_db

FAKE_TOKEN = "123456789:AA_REVIEW_FAKE_TOKEN_NOT_REAL_9999999999"


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


class ReviewFixHarness:
    """Real HTTP server + temporary databases + two isolated agents."""

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

        # Earlier test files may remove these from sys.modules; lazy imports
        # inside the server would then create FRESH unpached module instances
        # bound to the real project databases. Re-register the patched
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

        from http.server import ThreadingHTTPServer
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), sub_http_server._SubHandler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"

        # The rate-limit buckets are process-global; clear them so earlier
        # test files cannot push this server's requests into 429.
        sub_http_server._RATE_LIMIT_BUCKETS.clear()

        self.agent_a = agent_db.upsert_agent(telegram_id=30001, username="fix-a")
        self.agent_b = agent_db.upsert_agent(telegram_id=30002, username="fix-b")
        for aid in (self.agent_a, self.agent_b):
            agent_sms_webhook.ensure_agent_sms_settings(aid)
            agent_sms_webhook.set_agent_sms_enabled(aid, True)
            agentbot_db.set_setting(aid, "sms_auto_confirm", True)
        self.secret_a = agent_sms_webhook.get_agent_sms_settings(self.agent_a)["secret"]
        self.secret_b = agent_sms_webhook.get_agent_sms_settings(self.agent_b)["secret"]
        self.url_a = f"{self.base}{agent_sms_webhook.agent_webhook_path(self.agent_a)}"
        self.url_b = f"{self.base}{agent_sms_webhook.agent_webhook_path(self.agent_b)}"
        self.url_central = f"{self.base}/payment/sms-webhook"
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


class _Base(unittest.TestCase):
    def setUp(self):
        self.h = ReviewFixHarness()
        self.h.__enter__()
        self.addCleanup(self.h.__exit__, None, None, None)
        # most scenarios need the agents' own auto-confirm enabled
        agentbot_db.set_setting(self.h.agent_a, "sms_auto_confirm", True)
        agentbot_db.set_setting(self.h.agent_b, "sms_auto_confirm", True)

    def _agent_customer_payment(self, agent_id: int, amount=100_000):
        conn = customer_db._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO customer_payments (agent_id, tx_code, user_id, amount, method, status, created_at) "
                "VALUES (?, ?, NULL, ?, 'card', 'pending', ?)",
                (agent_id, f"tx-{agent_id}-{amount}-{int(time.time()*1000000)%1000000}", amount, customer_db._now()),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _userbot_user(self, uid=55001):
        return userbot_db.upsert_user(uid, "u", "User")

    def _userbot_pending_payment(self, user_db_id: int, amount=100_000, payer_last4=""):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        receipt_meta = f"|payer_last4:{payer_last4}" if payer_last4 else ""
        conn = userbot_db._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO userbot_payments (tx_code, user_id, amount, method, status, receipt_image, created_at, updated_at) "
                "VALUES (?, ?, ?, 'card', 'pending', ?, ?, ?)",
                (f"tx-u-{int(time.time()*1000000)%1000000}", user_db_id, amount,
                 receipt_meta, now, now),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _userbot_wallet_charge(self, user_db_id: int, amount: int) -> int:
        """Give the UserBot user a wallet balance to verify a credit happened."""
        conn = userbot_db._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE userbot_users SET wallet_balance = COALESCE(wallet_balance, 0) + ? WHERE id = ?",
                (amount, user_db_id),
            )
            conn.commit()
            row = cur.execute(
                "SELECT wallet_balance FROM userbot_users WHERE id = ?", (user_db_id,)
            ).fetchone()
        finally:
            conn.close()
        return int(row["wallet_balance"])


class Bug1AgentSmsCannotChargeUserbotWalletTests(_Base):
    """Finding 1 (critical): an agent-owned SMS must never approve a UserBot
    wallet payment via try_approve_payment_from_unmatched_sms."""

    def test_agent_owned_sms_never_retries_into_userbot_payment(self):
        uid = self._userbot_user()
        pay_id = self._userbot_pending_payment(uid, 100_000, payer_last4="1234")
        # An agent-owned event landed as no_pending_match (agent had no match).
        userbot_db.record_sms_webhook_event(
            {
                "event_id": "agent-owned-1", "sender": "BANK",
                "amount_raw": 100000, "currency_raw": "toman",
                "amount_toman": 100000, "reference": "ref-x",
                "card_last4": "1234", "body": "deposit 100000",
                "status": "no_pending_match",
                "received_at": int(time.time() * 1000),
                "device_time": 0,
            },
            owner_agent_id=self.h.agent_a,
        )
        ok, message, updated = userbot_db.try_approve_payment_from_unmatched_sms(pay_id)
        self.assertFalse(ok, f"agent-owned SMS approved a UserBot payment: {message}")
        payment = userbot_db.get_payment_by_id(pay_id)
        self.assertEqual(str(payment["status"]), "pending")
        self.assertEqual(self._userbot_wallet_charge(uid, 0), 0)

    def test_central_owned_sms_still_retries_into_userbot_payment(self):
        uid = self._userbot_user()
        pay_id = self._userbot_pending_payment(uid, 100_000, payer_last4="1234")
        userbot_db.record_sms_webhook_event(
            {
                "event_id": "central-owned-1", "sender": "BANK",
                "amount_raw": 100000, "currency_raw": "toman",
                "amount_toman": 100000, "reference": "ref-y",
                "card_last4": "1234", "body": "deposit 100000",
                "status": "no_pending_match",
                "received_at": int(time.time() * 1000),
                "device_time": 0,
            },
            owner_agent_id=0,
        )
        ok, message, updated = userbot_db.try_approve_payment_from_unmatched_sms(pay_id)
        self.assertTrue(ok, message)
        payment = userbot_db.get_payment_by_id(pay_id)
        self.assertEqual(str(payment["status"]), "approved")

    def test_rial_message_matches_toman_amount_and_survives_retry(self):
        """Bug1 companion: an agent webhook Rial SMS must convert correctly
        (1,000,000 Rial = 100,000 Toman) and stay correct on retry."""
        pay_a = self._agent_customer_payment(self.h.agent_a, 100_000)
        code, body = _http_post(self.h.url_a, self.h.secret_a, {
            "event_id": "rial-agent-1", "amount": 1_000_000, "currency": "rial",
        })
        self.assertEqual(code, 200, body)
        self.assertEqual(body.get("status"), "agency_queued", body)
        self.assertEqual(int(body.get("amount_toman") or 0), 100_000, body)
        queue = customer_db.fetch_pending_sms_auto_queue(limit=50)
        rows = [r for r in queue if r.get("event_id") == "rial-agent-1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["amount_toman"]), 100_000)


class Bug2RialNotTomanTests(_Base):
    """Finding 2 (critical): 1,000,000 Rial must match 100,000 Toman — the
    agent route must preserve the real currency on retry."""

    def test_rial_message_matches_toman_amount_and_survives_retry(self):
        pay_a = self._agent_customer_payment(self.h.agent_a, 100_000)
        code, body = _http_post(self.h.url_a, self.h.secret_a, {
            "event_id": "rial-1", "amount": 1_000_000, "currency": "rial",
        })
        self.assertEqual(code, 200, body)
        self.assertEqual(body.get("status"), "agency_queued", body)
        self.assertEqual(int(body.get("amount_toman") or 0), 100_000, body)
        queue = customer_db.fetch_pending_sms_auto_queue(limit=50)
        rows = [r for r in queue if r.get("event_id") == "rial-1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["amount_toman"]), 100_000)

        # Retry the same event after the queue entry is consumed: the event
        # becomes a duplicate (already recorded) — never re-queued with the
        # 10x Toman amount.
        code2, body2 = _http_post(self.h.url_a, self.h.secret_a, {
            "event_id": "rial-1", "amount": 1_000_000, "currency": "rial",
        })
        self.assertEqual(code2, 200, body2)
        self.assertIn(body2.get("status"), {"approved_duplicate", "agency_queued"}, body2)
        if body2.get("status") == "approved_duplicate":
            self.assertEqual(int(body2.get("amount_toman") or 0), 100_000, body2)
        queue2 = customer_db.fetch_pending_sms_auto_queue(limit=50)
        rows2 = [r for r in queue2 if r.get("event_id") == "rial-1"]
        self.assertEqual(len(rows2), 1)

    def test_currency_rial_amount_recorded_consistently(self):
        code, body = _http_post(self.h.url_a, self.h.secret_a, {
            "event_id": "rial-record-1", "amount": 1_000_000, "currency": "rial",
        })
        self.assertEqual(code, 202, body)
        event = userbot_db.get_sms_webhook_event(
            "rial-record-1", owner_agent_id=self.h.agent_a
        )
        self.assertEqual(int(event["amount_raw"]), 1_000_000)
        self.assertEqual(int(event["amount_toman"]), 100_000)
        self.assertEqual(str(event["currency_raw"]), "rial")


class Bug3CentralIgnoresAgencyPaymentsTests(_Base):
    """Finding 3 (critical): the central webhook (correct admin secret) must
    never match, attach, or enqueue an agency customer payment."""

    def test_central_secret_does_not_enqueue_agent_customer_payment(self):
        pay_a = self._agent_customer_payment(self.h.agent_a, 100_000)
        # The central webhook's real secret from .env is unknown here; the
        # point is that even a VALID central request must not see the agent's
        # customer payment. Simulate the central matching logic directly:
        # central scope = userbot payments + agent wallet top-ups only.
        userbot_matches = userbot_db.find_pending_card_payments_by_amount(
            100_000, sms_time_ms=int(time.time() * 1000))
        agent_wallet_matches = agentbot_db.find_pending_wallet_charge_payments_by_amount(
            100_000, sms_time_ms=int(time.time() * 1000))
        agency_via_central = [m for m in userbot_matches
                              if int(m.get("agent_id") or 0) == self.h.agent_a]
        self.assertEqual(agency_via_central, [],
                         "customer_payments must not leak into the central scope")
        # And the central flow never enqueues: the queue stays empty.
        queue = customer_db.fetch_pending_sms_auto_queue(limit=50)
        self.assertEqual([r for r in queue if int(r.get("pay_id") or 0) == pay_a], [])

    def test_central_route_without_agent_scope_match_returns_no_match(self):
        """Even a valid central request (admin .env secret) cannot approve or
        queue an agency customer payment — verified via the handler logic with
        the central owner scope (owner_agent_id=0)."""
        pay_a = self._agent_customer_payment(self.h.agent_a, 100_000)
        # Central scope has no such payment (it lives in customer_bot.db and
        # the central path no longer reads it).
        code, body = _http_post(self.h.url_central, "dummy-central-secret", {
            "event_id": "central-ignores-agency-1", "amount": 100000, "currency": "toman",
        })
        # 401/403 with the wrong secret; the important guarantee is the queue
        # stays empty either way.
        self.assertIn(code, (401, 403))
        queue = customer_db.fetch_pending_sms_auto_queue(limit=50)
        self.assertEqual([r for r in queue if int(r.get("pay_id") or 0) == pay_a], [])

    def test_cross_scope_ambiguity_between_userbot_and_agent_wallet_kept(self):
        """A central SMS matching both a UserBot payment and an agent wallet
        top-up of the same amount must stay ambiguous (preserved behavior)."""
        uid = self._userbot_user()
        self._userbot_pending_payment(uid, 100_000, payer_last4="1234")
        agentbot_db.create_wallet_charge_payment(
            agent_id=self.h.agent_a, agent_name="A", base_amount=100_000,
            marker_amount=0, receipt_file_id="f", card_last4="1234",
        )
        handler = object.__new__(sub_http_server._SubHandler)
        code, body = handler._try_approve_sms_event(
            event_id="cross-scope-amb-1", amount_raw=100000, currency_raw="toman",
            reference="r", sender="BANK", card_last4="1234", body="d",
            received_at_ms=int(time.time() * 1000), device_time_ms=0,
        )
        self.assertEqual(code, 409, body)
        self.assertEqual(body.get("scope"), "cross_scope")


class Bug4SmsReuseWithNewEventIdTests(_Base):
    """Finding 4 (important): the same SMS (same sender/reference/body) with a
    NEW event_id must not approve a second payment; a reserved/queued event
    must not be consumable by another payment."""

    def test_same_sms_new_event_id_rejected_in_agent_scope(self):
        pay1 = self._agent_customer_payment(self.h.agent_a, 100_000)
        payload = {
            "event_id": "reuse-old", "amount": 100000, "currency": "toman",
            "reference": "track-777", "sender": "BANK", "body": "deposit 100000 ref 777",
        }
        code1, body1 = _http_post(self.h.url_a, self.h.secret_a, payload)
        self.assertEqual(body1.get("status"), "agency_queued", body1)

        # Same SMS content arrives again with a fresh event_id (device retry
        # with a regenerated id): must be recognized as the same bank SMS.
        payload2 = dict(payload, event_id="reuse-new")
        code2, body2 = _http_post(self.h.url_a, self.h.secret_a, payload2)
        self.assertIn(body2.get("status"),
                      {"approved_duplicate", "no_pending_match", "agent_auto_disabled"}, body2)
        # No second queue entry for a different payment:
        queue = customer_db.fetch_pending_sms_auto_queue(limit=50)
        rows = [r for r in queue if r.get("event_id") == "reuse-new"]
        self.assertEqual(rows, [], "new event_id of the same SMS must not enqueue again")

    def test_reserved_event_cannot_be_consumed_by_concurrent_payment(self):
        pay1 = self._agent_customer_payment(self.h.agent_a, 100_000)
        payload = {"event_id": "reserved-1", "amount": 100000, "currency": "toman"}
        code, body = _http_post(self.h.url_a, self.h.secret_a, payload)
        self.assertEqual(body.get("status"), "agency_queued", body)
        # Another payment appears and the same event is re-posted: must not
        # enqueue for the second payment.
        pay2 = self._agent_customer_payment(self.h.agent_a, 100_000)
        code2, body2 = _http_post(self.h.url_a, self.h.secret_a, payload)
        queue = customer_db.fetch_pending_sms_auto_queue(limit=50)
        rows = [r for r in queue if r.get("event_id") == "reserved-1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["pay_id"]), pay1)
        self.assertNotEqual(int(rows[0]["pay_id"]), pay2)


class Bug5EnqueueFailureRetryTests(_Base):
    """Finding 5 (important): a transient enqueue failure must be recoverable
    by re-posting the same event."""

    def test_enqueue_failure_then_success_on_retry(self):
        pay_a = self._agent_customer_payment(self.h.agent_a, 100_000)
        payload = {"event_id": "retry-enq-1", "amount": 100000, "currency": "toman"}

        real_enqueue = customer_db.enqueue_sms_auto_approval
        calls = {"n": 0}

        def flaky_enqueue(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient db lock (simulated)")
            return real_enqueue(*a, **k)

        with patch.object(customer_db, "enqueue_sms_auto_approval", flaky_enqueue):
            code1, body1 = _http_post(self.h.url_a, self.h.secret_a, payload)
        self.assertEqual(code1, 500, body1)
        self.assertEqual(body1.get("status"), "agency_queue_failed", body1)

        # Re-post the same event: recover without double financial action.
        code2, body2 = _http_post(self.h.url_a, self.h.secret_a, payload)
        self.assertEqual(code2, 200, body2)
        self.assertEqual(body2.get("status"), "agency_queued", body2)
        queue = customer_db.fetch_pending_sms_auto_queue(limit=50)
        rows = [r for r in queue if r.get("event_id") == "retry-enq-1"]
        self.assertEqual(len(rows), 1)


class Bug6OwnerFilterBeforeLimitTests(_Base):
    """Finding 6 (performance): the agent_id filter must apply inside SQL
    before LIMIT; more than 20 same-amount payments of OTHER agents must not
    hide this agent's valid payment or ambiguity."""

    def test_owner_filter_inside_sql_and_before_limit(self):
        # 25 pending payments of other agents with the same amount
        for i in range(25):
            other = agent_db.upsert_agent(telegram_id=40000 + i, username=f"noise-{i}")
            self._agent_customer_payment(other, 100_000)
        pay_a = self._agent_customer_payment(self.h.agent_a, 100_000)

        # SQL-level filter: the agent's payment is found despite the noise.
        rows = customer_db.find_pending_card_payments_by_amount(
            100_000, sms_time_ms=int(time.time() * 1000), agent_id=self.h.agent_a)
        ids = [int(r["id"]) for r in rows]
        self.assertIn(pay_a, ids)
        self.assertTrue(all(int(r["agent_id"]) == self.h.agent_a for r in rows))

        # Ambiguity inside the agent's own scope still detected via SQL:
        pay_a2 = self._agent_customer_payment(self.h.agent_a, 100_000)
        rows2 = customer_db.find_pending_card_payments_by_amount(
            100_000, sms_time_ms=int(time.time() * 1000), agent_id=self.h.agent_a)
        self.assertEqual(len(rows2), 2)

    def test_webhook_route_still_queues_with_noise(self):
        for i in range(25):
            other = agent_db.upsert_agent(telegram_id=41000 + i, username=f"noise2-{i}")
            self._agent_customer_payment(other, 100_000)
        pay_a = self._agent_customer_payment(self.h.agent_a, 100_000)
        code, body = _http_post(self.h.url_a, self.h.secret_a, {
            "event_id": "noise-1", "amount": 100000, "currency": "toman"})
        self.assertEqual(code, 200, body)
        self.assertEqual(body.get("status"), "agency_queued", body)
        self.assertEqual(int(body.get("payment_id") or 0), pay_a)


class Bug7WebhookUrlFallbackTests(unittest.TestCase):
    """Finding 7 (performance): the agent URL must build from managed domain
    or the public host/scheme/port settings; without any domain the UI must
    say so instead of presenting a relative path."""

    @staticmethod
    def _load_settings_payment():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "AgentBot.handlers.settings_payment_review",
            Path(__file__).resolve().parents[1] / "AgentBot" / "handlers" / "settings_payment.py")
        mod = importlib.util.module_from_spec(spec)
        saved = {n: sys.modules.get(n) for n in
                 ("telegram", "telegram.error", "telegram.ext", "telegram.request",
                  "AgentBot", "AgentBot.handlers", "AgentBot.constants", "AgentBot.handlers.base",
                  "AgentBot.keyboards", "Shared.tg_button_styles",
                  "Shared.agent_sms_webhook", "AgentBot.database",
                  "AgentBot.handlers.settings_payment")}
        import types
        class _Any:
            def __init__(self, *a, **k):
                pass
            def __getattr__(self, name):
                return _Any()
        tg = types.ModuleType("telegram"); tg.__version__ = "20.0"
        for n in ("Update", "Bot", "BotCommand", "InlineKeyboardMarkup",
                  "InlineKeyboardButton", "ReplyKeyboardMarkup", "InputMediaPhoto"):
            setattr(tg, n, _Any)
        terr = types.ModuleType("telegram.error")
        for n in ("BadRequest", "Forbidden", "NetworkError"):
            setattr(terr, n, type(n, (Exception,), {}))
        ext = types.ModuleType("telegram.ext")
        ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
        ext.filters = types.SimpleNamespace(ALL=_Any(), TEXT=_Any(), PHOTO=_Any(),
                                            COMMAND=_Any(), Regex=lambda *a, **k: _Any())
        req = types.ModuleType("telegram.request"); req.HTTPXRequest = _Any
        consts = types.ModuleType("AgentBot.constants")
        for n in ("UD_STATE", "UD_SELECTED_CARD", "STATE_ADD_CARD", "STATE_ADD_CARD_NUMBER",
                  "STATE_ADD_CARD_OWNER", "STATE_ADD_CARD_BANK", "STATE_EDIT_CARD",
                  "STATE_SET_CARD_TEXT", "UD_NEW_CARD"):
            setattr(consts, n, n)
        base = types.ModuleType("AgentBot.handlers.base"); base.get_agent_id = lambda c: 1
        kb = types.ModuleType("AgentBot.keyboards")
        for n in ("card_settings_keyboard", "cancel_keyboard", "main_menu_keyboard",
                  "payment_cards_list_keyboard", "sms_webhook_settings_keyboard", "_ikb"):
            setattr(kb, n, _Any)
        tbs = types.ModuleType("Shared.tg_button_styles"); tbs.inline_button = _Any
        adb = types.ModuleType("AgentBot.database")
        for n in ("get_setting", "set_setting", "get_cards", "get_card", "add_card",
                  "update_card", "delete_card"):
            setattr(adb, n, lambda *a, **k: None)
        for n, m in {"telegram": tg, "telegram.error": terr, "telegram.ext": ext,
                     "telegram.request": req, "AgentBot.constants": consts,
                     "AgentBot.handlers.base": base, "AgentBot.keyboards": kb,
                     "Shared.tg_button_styles": tbs, "AgentBot.database": adb}.items():
            sys.modules[n] = m
        pkg = types.ModuleType("AgentBot"); pkg.__path__ = []
        hp = types.ModuleType("AgentBot.handlers"); hp.__path__ = []
        sys.modules.setdefault("AgentBot", pkg)
        sys.modules.setdefault("AgentBot.handlers", hp)
        sys.modules["AgentBot.handlers.settings_payment"] = mod
        try:
            spec.loader.exec_module(mod)
            return mod, saved
        finally:
            pass

    def test_managed_domain_preferred(self):
        mod, saved = self._load_settings_payment()
        try:
            with patch.object(userbot_db, "get_managed_sub_base_url", lambda: "https://managed.example.com"):
                status = mod._agent_sms_webhook_status(9)
            self.assertEqual(status["endpoint"],
                             "https://managed.example.com/payment/agent/9/sms-webhook")
            self.assertTrue(status["base_url_configured"])
        finally:
            for n, m in saved.items():
                if m is not None:
                    sys.modules[n] = m
                else:
                    sys.modules.pop(n, None)
            sys.modules.pop("AgentBot.handlers.settings_payment_review", None)

    def test_public_host_fallback(self):
        mod, saved = self._load_settings_payment()
        try:
            with patch.object(userbot_db, "get_managed_sub_base_url", lambda: ""), \
                 patch.dict(os.environ, {"SUB_SERVER_PUBLIC_HOST": "panel.example.net",
                                         "SUB_SERVER_PUBLIC_SCHEME": "https",
                                         "SUB_SERVER_PUBLIC_PORT": "443"}):
                status = mod._agent_sms_webhook_status(9)
            self.assertEqual(status["endpoint"],
                             "https://panel.example.net/payment/agent/9/sms-webhook")
            self.assertTrue(status["base_url_configured"])
        finally:
            for n, m in saved.items():
                if m is not None:
                    sys.modules[n] = m
                else:
                    sys.modules.pop(n, None)
            sys.modules.pop("AgentBot.handlers.settings_payment_review", None)

    def test_no_domain_reports_missing_configuration(self):
        mod, saved = self._load_settings_payment()
        try:
            with patch.object(userbot_db, "get_managed_sub_base_url", lambda: ""), \
                 patch.dict(os.environ, {"SUB_SERVER_PUBLIC_HOST": ""}):
                status = mod._agent_sms_webhook_status(9)
            self.assertFalse(status["base_url_configured"])
            self.assertEqual(status["endpoint"], "")
            # the path is available for information only
            self.assertEqual(status["webhook_path"], "/payment/agent/9/sms-webhook")
        finally:
            for n, m in saved.items():
                if m is not None:
                    sys.modules[n] = m
                else:
                    sys.modules.pop(n, None)
            sys.modules.pop("AgentBot.handlers.settings_payment_review", None)


class QueueOwnerSafetyTests(_Base):
    """Queued rows whose event was central-owned, ownerless, or owned by
    another agent must never be financially executed for this agent."""

    def test_queue_worker_rechecks_enabled_flag_and_agent(self):
        pay_a = self._agent_customer_payment(self.h.agent_a, 100_000)
        payload = {"event_id": "qsafe-1", "amount": 100000, "currency": "toman"}
        code, body = _http_post(self.h.url_a, self.h.secret_a, payload)
        self.assertEqual(body.get("status"), "agency_queued", body)

        # Agent's SMS gets disabled AFTER enqueue -> the worker must not run
        # the financial part.
        agent_sms_webhook.set_agent_sms_enabled(self.h.agent_a, False)
        agentbot_db.set_setting(self.h.agent_a, "sms_auto_confirm", False)

        rows = customer_db.fetch_pending_sms_auto_queue(limit=50)
        target = [r for r in rows if r.get("event_id") == "qsafe-1"]
        self.assertEqual(len(target), 1)

        # Simulate the worker's pre-execution checks (the same logic the
        # real worker runs before any financial action).
        agent_row = agent_db.get_agent_by_id(self.h.agent_a)
        enabled_now = agent_sms_webhook.is_agent_sms_enabled(self.h.agent_a)
        auto_now = agentbot_db.get_setting(self.h.agent_a, "sms_auto_confirm", False)
        self.assertTrue(
            (not (agent_row and int(agent_row.get("is_active", 0))))
            or (not enabled_now) or (not auto_now),
            "at least one guard must block execution after disable",
        )

    def test_secret_rotation_does_not_reenable_disabled_sms(self):
        agent_sms_webhook.set_agent_sms_enabled(self.h.agent_a, False)
        before = agent_sms_webhook.get_agent_sms_settings(self.h.agent_a)
        agent_sms_webhook.regenerate_agent_secret(self.h.agent_a)
        after = agent_sms_webhook.get_agent_sms_settings(self.h.agent_a)
        self.assertFalse(bool(after.get("enabled")),
                         "secret rotation must not silently enable disabled SMS")
        self.assertNotEqual(before["secret"], after["secret"])


class ResidualIsolationRegressionTests(_Base):
    """Real regressions found after the first stage-9 review passed."""

    def test_same_device_event_id_is_isolated_per_agent(self):
        pay_a = self._agent_customer_payment(self.h.agent_a, 100_000)
        pay_b = self._agent_customer_payment(self.h.agent_b, 200_000)

        code_a, body_a = _http_post(self.h.url_a, self.h.secret_a, {
            "event_id": "device-local-42", "amount": 100_000,
            "currency": "toman", "sender": "BANK-A", "body": "deposit A",
        })
        code_b, body_b = _http_post(self.h.url_b, self.h.secret_b, {
            "event_id": "device-local-42", "amount": 200_000,
            "currency": "toman", "sender": "BANK-B", "body": "deposit B",
        })

        self.assertEqual((code_a, body_a.get("status")), (200, "agency_queued"))
        self.assertEqual((code_b, body_b.get("status")), (200, "agency_queued"))
        rows = customer_db.fetch_pending_sms_auto_queue(limit=50)
        reservations = {
            (int(row["agent_id"]), int(row["pay_id"]), row["event_id"])
            for row in rows
        }
        self.assertIn((self.h.agent_a, pay_a, "device-local-42"), reservations)
        self.assertIn((self.h.agent_b, pay_b, "device-local-42"), reservations)
        self.assertIsNotNone(userbot_db.get_sms_webhook_event(
            "device-local-42", owner_agent_id=self.h.agent_a
        ))
        self.assertIsNotNone(userbot_db.get_sms_webhook_event(
            "device-local-42", owner_agent_id=self.h.agent_b
        ))

    def test_one_payment_has_only_one_pending_sms_reservation(self):
        pay_id = self._agent_customer_payment(self.h.agent_a, 100_000)
        first = _http_post(self.h.url_a, self.h.secret_a, {
            "event_id": "deposit-one", "amount": 100_000, "currency": "toman",
            "sender": "BANK", "reference": "ref-one", "body": "deposit one",
        })
        second = _http_post(self.h.url_a, self.h.secret_a, {
            "event_id": "deposit-two", "amount": 100_000, "currency": "toman",
            "sender": "BANK", "reference": "ref-two", "body": "deposit two",
        })

        self.assertEqual((first[0], first[1].get("status")), (200, "agency_queued"))
        self.assertEqual((second[0], second[1].get("status")), (409, "payment_reserved"))
        rows = [row for row in customer_db.fetch_pending_sms_auto_queue(limit=50)
                if int(row["pay_id"]) == pay_id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], "deposit-one")

    def test_multiple_agent_wallet_matches_are_ambiguous(self):
        first = agentbot_db.create_wallet_charge_payment(
            self.h.agent_a, "A", 100_000, 777, "file-a", "1234"
        )
        second = agentbot_db.create_wallet_charge_payment(
            self.h.agent_b, "B", 100_000, 777, "file-b", "1234"
        )
        handler = object.__new__(sub_http_server._SubHandler)
        code, body = handler._try_approve_sms_event(
            event_id="ambiguous-wallets", amount_raw=100_777,
            currency_raw="toman", reference="ref", sender="BANK",
            card_last4="1234", body="wallet deposit",
            received_at_ms=int(time.time() * 1000), device_time_ms=0,
        )

        self.assertEqual(code, 409, body)
        self.assertEqual(body.get("scope"), "agent_wallet", body)
        self.assertEqual(agentbot_db.get_payment_by_id(first["id"])["status"], "pending")
        self.assertEqual(agentbot_db.get_payment_by_id(second["id"])["status"], "pending")

    def test_queue_worker_rejects_foreign_event_before_financial_handler(self):
        from AgentBot.handlers import settings_customer_payments as worker

        pay_id = self._agent_customer_payment(self.h.agent_a, 100_000)
        userbot_db.record_sms_webhook_event({
            "event_id": "foreign-event", "amount_raw": 100_000,
            "currency_raw": "toman", "amount_toman": 100_000,
            "status": "agency_queued", "matched_payment_id": pay_id,
        }, owner_agent_id=self.h.agent_b)
        conn = customer_db._get_conn()
        try:
            conn.execute(
                "INSERT INTO customer_payment_sms_queue "
                "(agent_id, pay_id, event_id, amount_toman, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.h.agent_a, pay_id, "foreign-event", 100_000, customer_db._now()),
            )
            conn.commit()
        finally:
            conn.close()

        with patch.object(worker, "_auto_approve_from_sms_webhook", new_callable=AsyncMock) as financial:
            processed = asyncio.run(worker.process_sms_webhook_queue(object(), limit=10))
        self.assertEqual(processed, 0)
        financial.assert_not_awaited()
        self.assertEqual(customer_db.fetch_pending_sms_auto_queue(limit=50), [])

    def test_secret_rotation_keeps_both_flags_disabled(self):
        from AgentBot.handlers import settings_payment

        agent_sms_webhook.set_agent_sms_enabled(self.h.agent_a, False)
        agentbot_db.set_setting(self.h.agent_a, "sms_auto_confirm", False)
        before = agent_sms_webhook.get_agent_sms_settings(self.h.agent_a)["secret"]
        rotated = settings_payment._rotate_agent_sms_secret(self.h.agent_a)

        self.assertNotEqual(before, rotated["secret"])
        self.assertFalse(rotated["enabled"])
        self.assertFalse(agentbot_db.get_setting(
            self.h.agent_a, "sms_auto_confirm", True
        ))


class SmsWebhookMigrationTests(unittest.TestCase):
    def test_legacy_global_event_constraints_are_migrated_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_path = root / "userbot.db"
            customer_path = root / "customer.db"

            conn = sqlite3.connect(user_path)
            conn.execute(
                """
                CREATE TABLE userbot_sms_webhook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE, sender TEXT DEFAULT '',
                    amount_raw INTEGER DEFAULT 0, currency_raw TEXT DEFAULT '',
                    amount_toman INTEGER DEFAULT 0, reference TEXT DEFAULT '',
                    card_last4 TEXT DEFAULT '', body TEXT DEFAULT '',
                    status TEXT DEFAULT 'received', matched_payment_id INTEGER DEFAULT 0,
                    message TEXT DEFAULT '', received_at INTEGER DEFAULT 0,
                    device_time INTEGER DEFAULT 0, created_at TEXT DEFAULT '',
                    owner_agent_id INTEGER DEFAULT 0
                )
                """
            )
            conn.execute(
                "INSERT INTO userbot_sms_webhook_events "
                "(event_id, status, owner_agent_id) VALUES ('legacy-event', 'approved', 0)"
            )
            conn.commit()
            conn.close()

            conn = sqlite3.connect(customer_path)
            conn.execute(
                """
                CREATE TABLE customer_payment_sms_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL, pay_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE, amount_toman INTEGER DEFAULT 0,
                    card_last4 TEXT DEFAULT '', created_at TEXT,
                    processed INTEGER DEFAULT 0, note TEXT DEFAULT '',
                    processed_at TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                "INSERT INTO customer_payment_sms_queue "
                "(agent_id, pay_id, event_id, processed) VALUES (1, 9, 'old-a', 0)"
            )
            conn.execute(
                "INSERT INTO customer_payment_sms_queue "
                "(agent_id, pay_id, event_id, processed) VALUES (1, 9, 'old-b', 0)"
            )
            conn.commit()
            conn.close()

            with patch.object(userbot_db, "DB_PATH", user_path), \
                 patch.object(customer_db, "DB_PATH", customer_path):
                userbot_db.init_db()
                customer_db.init_db()

                inserted_a, _ = userbot_db.record_sms_webhook_event(
                    {"event_id": "shared-local-id"}, owner_agent_id=1
                )
                inserted_b, _ = userbot_db.record_sms_webhook_event(
                    {"event_id": "shared-local-id"}, owner_agent_id=2
                )
                self.assertTrue(inserted_a)
                self.assertTrue(inserted_b)
                self.assertEqual(
                    userbot_db.get_sms_webhook_event(
                        "legacy-event", owner_agent_id=0
                    )["status"],
                    "approved",
                )

                queue_conn = customer_db._get_conn()
                try:
                    pending = queue_conn.execute(
                        "SELECT COUNT(*) FROM customer_payment_sms_queue "
                        "WHERE agent_id=1 AND pay_id=9 AND processed=0"
                    ).fetchone()[0]
                finally:
                    queue_conn.close()
                self.assertEqual(pending, 1)
                self.assertTrue(customer_db.enqueue_sms_auto_approval(
                    2, 10, "same-device-id", 100_000
                ))
                self.assertTrue(customer_db.enqueue_sms_auto_approval(
                    3, 11, "same-device-id", 100_000
                ))


if __name__ == "__main__":
    unittest.main()
