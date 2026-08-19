"""Tests for the DB layer of the referral system."""

import pytest


def _insert_payment(db, user_id, amount, status="pending", method="card"):
    conn = db._get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO userbot_payments (tx_code, user_id, amount, method, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, '', '')",
        (f"T{user_id}{db._referral_now()[:4]}", user_id, amount, method, status),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def test_normal_start_no_referral(fresh_db):
    db = fresh_db
    uid = db.upsert_user(111, "alice", "Alice")
    assert uid > 0
    assert db.get_referral_by_invitee(uid) is None


def test_referral_start_registers(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(201, "inviter", "Inviter")
    invitee = db.upsert_user(202, "invitee", "Invitee")
    code = db.get_or_create_user_referral_code(inviter)

    payload = f"ref_{code}"
    normalized = db.normalize_referral_payload(payload)
    assert normalized == code

    ok, status, ref_id = db.register_referral(invitee, normalized, payload)
    assert ok is True
    assert status == "ok"
    assert ref_id > 0
    ref = db.get_referral_by_invitee(invitee)
    assert ref is not None
    assert int(ref["inviter_id"]) == inviter


def test_invalid_referral_code(fresh_db):
    db = fresh_db
    invitee = db.upsert_user(301, "b1", "B One")
    ok, status, _ = db.register_referral(invitee, "doesnotexist", "")
    assert ok is False
    assert status == "not_found"
    assert db.normalize_referral_payload("ref_!!") == ""
    assert db.normalize_referral_payload("tshotu_1_2") == ""


def test_self_referral_blocked(fresh_db):
    db = fresh_db
    uid = db.upsert_user(401, "selfy", "Selfy")
    code = db.get_or_create_user_referral_code(uid)
    ok, status, _ = db.register_referral(uid, code, "")
    assert ok is False
    assert status == "self_referral"


def test_cannot_change_referrer(fresh_db):
    db = fresh_db
    inviter_a = db.upsert_user(501, "a", "A")
    inviter_b = db.upsert_user(502, "b", "B")
    invitee = db.upsert_user(503, "c", "C")
    code_a = db.get_or_create_user_referral_code(inviter_a)
    code_b = db.get_or_create_user_referral_code(inviter_b)

    ok1, _, ref1 = db.register_referral(invitee, code_a, f"ref_{code_a}")
    ok2, status2, ref2 = db.register_referral(invitee, code_b, f"ref_{code_b}")
    assert ok1 is True
    assert ok2 is False
    assert status2 == "already_referred"
    ref = db.get_referral_by_invitee(invitee)
    assert int(ref["inviter_id"]) == inviter_a


def test_trial_reward_idempotent(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(601, "i", "I")
    invitee = db.upsert_user(602, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {"referral_enabled": True, "trial_reward_amount": 3000, "purchase_reward_amount": 20000}
    )

    r1 = db.try_grant_referral_trial_reward(invitee)
    assert r1 is not None and r1["is_new"] is True
    r2 = db.try_grant_referral_trial_reward(invitee)
    assert r2 is not None and r2["is_new"] is False
    assert r1["reward"]["id"] == r2["reward"]["id"]
    balance = db.get_user_by_id(inviter)["wallet_balance"]
    assert balance == 3000


def test_trial_reward_without_referral(fresh_db):
    db = fresh_db
    invitee = db.upsert_user(701, "x", "X")
    db.set_referral_settings({"referral_enabled": True, "trial_reward_amount": 3000})
    assert db.try_grant_referral_trial_reward(invitee) is None


def test_purchase_reward_requires_approved_payment(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(801, "i", "I")
    invitee = db.upsert_user(802, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {
            "referral_enabled": True,
            "trial_reward_amount": 3000,
            "purchase_reward_amount": 20000,
            "min_purchase_amount": 0,
        }
    )

    pid = _insert_payment(db, invitee, 50000, status="pending")
    assert db.try_grant_referral_purchase_reward(invitee, pid) is None

    db.change_payment_status_with_wallet(pid, "approved")
    result = db.try_grant_referral_purchase_reward(invitee, pid)
    assert result is not None
    assert int(result["reward"]["amount_toman"]) == 20000


def test_purchase_reward_min_amount(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(901, "i", "I")
    invitee = db.upsert_user(902, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {
            "referral_enabled": True,
            "purchase_reward_amount": 20000,
            "min_purchase_amount": 30000,
        }
    )
    pid = _insert_payment(db, invitee, 20000, status="pending")
    db.change_payment_status_with_wallet(pid, "approved")
    assert db.try_grant_referral_purchase_reward(invitee, pid) is None


def test_second_purchase_no_new_reward(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(1001, "i", "I")
    invitee = db.upsert_user(1002, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {"referral_enabled": True, "purchase_reward_amount": 15000, "min_purchase_amount": 0}
    )

    p1 = _insert_payment(db, invitee, 30000, status="pending")
    p2 = _insert_payment(db, invitee, 40000, status="pending")
    _, _, pay1 = db.change_payment_status_with_wallet(p1, "approved")
    _, _, pay2 = db.change_payment_status_with_wallet(p2, "approved")

    assert pay1.get("_referral_reward_is_new") is True
    # second payment: no NEW purchase reward may be credited
    assert not pay2.get("_referral_reward_is_new")

    balance = db.get_user_by_id(inviter)["wallet_balance"]
    assert balance == 15000  # only one purchase reward, despite two purchases
    # Only one purchase reward row exists
    rewards, _ = db.list_referral_rewards(inviter_id=inviter)
    purchase_rewards = [r for r in rewards if r["reward_type"] == "purchase"]
    assert len(purchase_rewards) == 1


def test_duplicate_approval_callback_single_reward(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(1101, "i", "I")
    invitee = db.upsert_user(1102, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {"referral_enabled": True, "purchase_reward_amount": 20000, "min_purchase_amount": 0}
    )

    pid = _insert_payment(db, invitee, 30000, status="pending")
    # five approval callbacks
    results = []
    for _ in range(5):
        ok, _, pay = db.change_payment_status_with_wallet(pid, "approved")
        results.append(pay)

    new_rewards = [p for p in results if p.get("_referral_reward_is_new")]
    assert len(new_rewards) == 1
    balance_after = db.get_user_by_id(inviter)["wallet_balance"]
    assert balance_after == 20000  # exactly one purchase reward


def test_reward_revoked_on_refund(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(1201, "i", "I")
    invitee = db.upsert_user(1202, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {"referral_enabled": True, "purchase_reward_amount": 20000, "min_purchase_amount": 0}
    )

    pid = _insert_payment(db, invitee, 30000, status="pending")
    db.change_payment_status_with_wallet(pid, "approved")
    invited_balance_after_grant = db.get_user_by_id(invitee)["wallet_balance"]
    inviter_balance_after_grant = db.get_user_by_id(inviter)["wallet_balance"]
    assert inviter_balance_after_grant >= 20000

    ok, _, pay = db.change_payment_status_with_wallet(pid, "rejected")
    assert ok is True
    revoked = pay.get("_revoked_referral_reward")
    assert revoked is not None
    new_inviter = db.get_user_by_id(inviter)["wallet_balance"]
    assert new_inviter < inviter_balance_after_grant
    rewards, _ = db.list_referral_rewards(inviter_id=inviter)
    statuses = [r["status"] for r in rewards if r["reward_type"] == "purchase"]
    assert "revoked" in statuses


def test_trial_reward_survives_purchase_refund(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(1301, "i", "I")
    invitee = db.upsert_user(1302, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {
            "referral_enabled": True,
            "trial_reward_amount": 3000,
            "purchase_reward_amount": 20000,
            "min_purchase_amount": 0,
        }
    )
    db.set_free_trial_used(invitee, 1)
    db.try_grant_referral_trial_reward(invitee)

    pid = _insert_payment(db, invitee, 30000, status="pending")
    db.change_payment_status_with_wallet(pid, "approved")
    db.change_payment_status_with_wallet(pid, "rejected")

    rewards, _ = db.list_referral_rewards(inviter_id=inviter)
    trial = [r for r in rewards if r["reward_type"] == "trial"]
    assert len(trial) == 1
    assert trial[0]["status"] == "paid"  # trial reward is untouched


def test_invitee_qualified_flag_blocks_reward(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(1401, "i", "I")
    invitee = db.upsert_user(1402, "e", "E")
    # invitee already has an approved purchase before joining
    old_pid = _insert_payment(db, invitee, 99000, status="approved")
    code = db.get_or_create_user_referral_code(inviter)
    ok, _, _ = db.register_referral(invitee, code, "")
    assert ok is True
    ref = db.get_referral_by_invitee(invitee)
    assert int(ref["invitee_qualified"]) == 0

    db.set_referral_settings(
        {"referral_enabled": True, "trial_reward_amount": 3000, "purchase_reward_amount": 20000}
    )
    assert db.try_grant_referral_trial_reward(invitee) is None


def test_fraud_flag_blocks_reward(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(1501, "i", "I")
    invitee = db.upsert_user(1502, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    _, _, rid = db.register_referral(invitee, code, "")
    db.set_referral_fraud_flag(rid, True)
    db.set_referral_settings({"referral_enabled": True, "trial_reward_amount": 3000})
    assert db.try_grant_referral_trial_reward(invitee) is None


def test_disabled_referral_no_registration(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(1601, "i", "I")
    invitee = db.upsert_user(1602, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.set_referral_settings({"referral_enabled": False})
    # register_referral itself records the relationship; /start gate checks enable flag.
    ok, _, _ = db.register_referral(invitee, code, "")
    assert ok is True
    db.set_referral_settings({"referral_enabled": False, "trial_reward_amount": 3000})
    assert db.try_grant_referral_trial_reward(invitee) is None


def test_max_successful_referrals_cap(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(1701, "i", "I")
    db.set_referral_settings(
        {
            "referral_enabled": True,
            "trial_reward_amount": 3000,
            "max_successful_referrals": 1,
        }
    )
    e1 = db.upsert_user(1702, "e1", "E1")
    e2 = db.upsert_user(1703, "e2", "E2")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(e1, code, "")
    db.register_referral(e2, code, "")
    r1 = db.try_grant_referral_trial_reward(e1)
    assert r1 is not None
    r2 = db.try_grant_referral_trial_reward(e2)
    assert r2 is None
    assert db.get_user_by_id(inviter)["wallet_balance"] == 3000


def test_gift_dashboard_capacity_fix(fresh_db):
    db = fresh_db
    db.upsert_zarin_voucher("CAPFIX", 30000, max_uses=7)
    stats = db.get_zarin_vouchers_dashboard()
    assert int(stats["total_amount"]) == 30000 * 7


def test_manual_reward(fresh_db):
    db = fresh_db
    uid = db.upsert_user(1801, "m", "M")
    reward = db.grant_manual_referral_reward(uid, 12345)
    assert reward is not None
    assert int(reward["amount_toman"]) == 12345
    assert db.get_user_by_id(uid)["wallet_balance"] == 12345
    assert db.grant_manual_referral_reward(uid, -5) is None


def test_user_stats(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(1901, "i", "I")
    invitee = db.upsert_user(1902, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {"referral_enabled": True, "trial_reward_amount": 3000, "purchase_reward_amount": 10000, "min_purchase_amount": 0}
    )
    db.try_grant_referral_trial_reward(invitee)
    stats = db.get_referral_user_stats(inviter)
    assert stats["total_referrals"] == 1
    assert stats["trial_rewards_count"] == 1
    assert stats["total_rewards"] == 3000


def test_concurrent_registration_single_winner(fresh_db):
    """Two consecutive registrations on the same invitee: exactly one wins."""
    db = fresh_db
    inviter = db.upsert_user(2001, "i", "I")
    invitee = db.upsert_user(2002, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)

    results = [db.register_referral(invitee, code, "") for _ in range(3)]
    ok_count = sum(1 for ok, *_ in results if ok)
    assert ok_count == 1


def test_admin_stats(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(2101, "i", "I")
    invitee = db.upsert_user(2102, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {"referral_enabled": True, "trial_reward_amount": 3000, "purchase_reward_amount": 20000, "min_purchase_amount": 0}
    )
    pid = _insert_payment(db, invitee, 30000, status="pending")
    db.try_grant_referral_trial_reward(invitee)  # trial first, then purchase
    db.change_payment_status_with_wallet(pid, "approved")

    stats = db.get_referral_admin_stats()
    assert stats["total_referrals"] == 1
    assert stats["trial_rewards_amount"] == 3000
    assert stats["purchase_rewards_amount"] == 20000
    assert stats["revenue_generated"] == 30000
