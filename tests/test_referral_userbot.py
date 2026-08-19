"""Tests for UserBot /start referral handling and invite UI helpers."""

import asyncio
import types

import pytest


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.sent = []
        import datetime as _dt

        self.date = _dt.datetime.now(_dt.timezone.utc)

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)
        return self

    async def reply_photo(self, **kwargs):
        self.sent.append("__photo__")
        return self


class FakeUser:
    def __init__(self, user_id, username="u", full_name="F"):
        self.id = user_id
        self.username = username
        self.full_name = full_name


class FakeUpdate:
    def __init__(self, user_id, text):
        self.message = FakeMessage(text)
        self.callback_query = None
        self.update_id = 1
        self.effective_user = FakeUser(user_id)


class FakeApplication:
    def __init__(self):
        self.bot_data = {"_user_bot_username": "test_bot"}
        self.user_data = None

    def create_task(self, coro):
        return coro


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.bot_data = {"_user_bot_username": "test_bot"}
        self.application = FakeApplication()
        self.bot = None


def test_handle_referral_start_payload_registers(fresh_db, userbot_main):
    db = fresh_db
    inviter = db.upsert_user(900001, "inv", "Inviter")
    invitee = db.upsert_user(900002, "inv2", "Invitee")
    code = db.get_or_create_user_referral_code(inviter)
    db.set_referral_settings({"referral_enabled": True})

    consumed = userbot_main._handle_referral_start_payload(f"ref_{code}", invitee)
    assert consumed is True
    ref = db.get_referral_by_invitee(invitee)
    assert ref is not None
    assert int(ref["inviter_id"]) == inviter


def test_handle_referral_start_payload_disabled(fresh_db, userbot_main):
    db = fresh_db
    inviter = db.upsert_user(900003, "inv", "Inviter")
    invitee = db.upsert_user(900004, "inv2", "Invitee")
    code = db.get_or_create_user_referral_code(inviter)
    db.set_referral_settings({"referral_enabled": False})

    consumed = userbot_main._handle_referral_start_payload(f"ref_{code}", invitee)
    assert consumed is False
    assert db.get_referral_by_invitee(invitee) is None


def test_handle_referral_start_payload_unknown_code_not_consumed(fresh_db, userbot_main):
    db = fresh_db
    invitee = db.upsert_user(900005, "x", "X")
    db.set_referral_settings({"referral_enabled": True})
    # looks like a referral payload but belongs to no user → must not be consumed
    consumed = userbot_main._handle_referral_start_payload("ref_zzzzzzzz", invitee)
    assert consumed is False


def test_handle_referral_start_payload_duplicate_consumed_silently(fresh_db, userbot_main):
    db = fresh_db
    inviter = db.upsert_user(900006, "inv", "Inviter")
    invitee = db.upsert_user(900007, "inv2", "Invitee")
    code = db.get_or_create_user_referral_code(inviter)
    db.set_referral_settings({"referral_enabled": True})

    assert userbot_main._handle_referral_start_payload(f"ref_{code}", invitee) is True
    # second start with same payload: consumed but no duplicate row
    assert userbot_main._handle_referral_start_payload(f"ref_{code}", invitee) is True
    refs, total = db.list_referrals(inviter_id=inviter)
    assert total == 1


def test_start_with_referral_payload(fresh_db, userbot_main):
    db = fresh_db
    inviter = db.upsert_user(900011, "inv", "Inviter")
    new_user_tg = 900012
    code = db.get_or_create_user_referral_code(inviter)
    db.set_referral_settings({"referral_enabled": True})

    update = FakeUpdate(new_user_tg, f"/start ref_{code}")
    context = FakeContext()
    asyncio.get_event_loop().run_until_complete(userbot_main.start(update, context))

    internal_id = db.upsert_user(new_user_tg, "u", "F")
    ref = db.get_referral_by_invitee(internal_id)
    assert ref is not None
    assert int(ref["inviter_id"]) == inviter
    # user got welcome message, no weird coupon error
    assert len(update.message.sent) >= 1


def test_start_normal_no_referral(fresh_db, userbot_main):
    db = fresh_db
    tg = 900013
    update = FakeUpdate(tg, "/start")
    context = FakeContext()
    asyncio.get_event_loop().run_until_complete(userbot_main.start(update, context))

    internal_id = db.upsert_user(tg, "u", "F")
    assert db.get_referral_by_invitee(internal_id) is None
    assert len(update.message.sent) >= 1


def test_start_referral_does_not_break_coupon_payload(fresh_db, userbot_main):
    """A real coupon code via /start must still redeem even with referral code present in DB."""
    db = fresh_db
    inviter = db.upsert_user(900014, "inv", "Inviter")
    code = db.get_or_create_user_referral_code(inviter)
    db.upsert_zarin_voucher("GIFT123", 5000)
    db.set_referral_settings({"referral_enabled": True})

    tg = 900015
    update = FakeUpdate(tg, "/start GIFT123")
    context = FakeContext()
    asyncio.get_event_loop().run_until_complete(userbot_main.start(update, context))

    internal_id = db.upsert_user(tg, "u", "F")
    wallet = db.get_user_by_id(internal_id)["wallet_balance"]
    assert wallet == 5000  # coupon still works


def test_invite_keyboards_exist(userbot_main):
    from UserBot.keyboards import invite_banner_keyboard

    kb = invite_banner_keyboard()
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "invite:get_banner" in flat
    assert "invite:rewards" in flat
    assert "invite:list" in flat
    assert "invite:stats" in flat
    assert "invite:history" in flat


def test_render_invite_home_text(fresh_db, userbot_main):
    db = fresh_db
    inviter = db.upsert_user(900021, "inv", "Inviter")
    db.set_referral_settings(
        {
            "referral_enabled": True,
            "trial_reward_amount": 3000,
            "purchase_reward_amount": 20000,
        }
    )

    context = FakeContext()
    text = asyncio.get_event_loop().run_until_complete(
        userbot_main._render_invite_home_text(context, inviter)
    )
    assert "ref_" in text
    assert "test_bot" in text
    assert "کل دعوت‌ها" in text


def test_render_invite_home_text_disabled(fresh_db, userbot_main):
    db = fresh_db
    inviter = db.upsert_user(900022, "inv", "Inviter")
    db.set_referral_settings({"referral_enabled": False})
    context = FakeContext()
    text = asyncio.get_event_loop().run_until_complete(
        userbot_main._render_invite_home_text(context, inviter)
    )
    assert "غیرفعال" in text
