"""Edge-case and concurrency tests for referral rewards."""

import threading

import pytest


def _insert_payment(db, user_id, amount, status="pending", method="card", code="TX"):
    conn = db._get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO userbot_payments (tx_code, user_id, amount, method, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, '', '')",
        (code, user_id, amount, method, status),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def test_parcial_refund_does_not_double_revoke(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(3101, "i", "I")
    invitee = db.upsert_user(3102, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {"referral_enabled": True, "purchase_reward_amount": 20000, "min_purchase_amount": 0}
    )
    pid = _insert_payment(db, invitee, 30000, status="pending")
    db.change_payment_status_with_wallet(pid, "approved")

    # multiple refund callbacks
    withdraws = []
    for _ in range(4):
        ok, _, pay = db.change_payment_status_with_wallet(pid, "rejected")
        withdraws.append(pay.get("_revoked_referral_reward"))
    assert sum(1 for w in withdraws if w) == 1  # revoked exactly once


def test_concurrent_duplicate_callbacks_single_purchase_reward(fresh_db):
    """Simulate 5 simultaneous approval callbacks racing through the funnel."""
    db = fresh_db
    inviter = db.upsert_user(3201, "i", "I")
    invitee = db.upsert_user(3202, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {"referral_enabled": True, "purchase_reward_amount": 20000, "min_purchase_amount": 0}
    )
    pid = _insert_payment(db, invitee, 50000, status="pending")

    results = [None] * 5

    def worker(idx):
        try:
            _ok, _msg, pay = db.change_payment_status_with_wallet(pid, "approved")
            results[idx] = pay
        except Exception as e:
            results[idx] = {"error": str(e)}

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    new_rewards = [
        p for p in results if isinstance(p, dict) and p.get("_referral_reward_is_new")
    ]
    # exactly one thread performs the real pending→approved transition and grants the reward;
    # the rest are no-ops (already approved).
    assert len(new_rewards) == 1
    balance = db.get_user_by_id(inviter)["wallet_balance"]
    assert balance == 20000  # exactly one purchase reward despite race


def test_reward_creates_voucher_ledger(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(3301, "i", "I")
    invitee = db.upsert_user(3302, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {"referral_enabled": True, "trial_reward_amount": 3000}
    )
    result = db.try_grant_referral_trial_reward(invitee)
    voucher_code = result["reward"]["voucher_code"]
    voucher = db.get_zarin_voucher(voucher_code)
    assert voucher is not None
    assert int(voucher["amount_toman"]) == 3000
    # voucher consumed exactly once by the inviter
    redemptions = db.list_zarin_voucher_redemptions(code=voucher_code)
    assert len(redemptions) == 1
    assert int(redemptions[0]["user_id"]) == inviter


def test_reward_revocation_deactivates_voucher(fresh_db):
    db = fresh_db
    inviter = db.upsert_user(3401, "i", "I")
    invitee = db.upsert_user(3402, "e", "E")
    code = db.get_or_create_user_referral_code(inviter)
    db.register_referral(invitee, code, "")
    db.set_referral_settings(
        {"referral_enabled": True, "trial_reward_amount": 3000}
    )
    result = db.try_grant_referral_trial_reward(invitee)
    voucher_code = result["reward"]["voucher_code"]
    reward_id = int(result["reward"]["id"])
    revoked = db.revoke_referral_reward_by_id(reward_id)
    assert revoked is not None
    voucher = db.get_zarin_voucher(voucher_code)
    assert int(voucher["is_active"]) == 0
    ref = db.get_referral_by_invitee(invitee)
    assert int(ref["fraud_flag"]) == 1  # flagged for admin review


def test_settings_defaults(fresh_db):
    db = fresh_db
    settings = db.get_referral_settings()
    assert settings["referral_enabled"] is False  # off by default
    assert settings["trial_reward_amount"] == 0
    assert settings["purchase_reward_amount"] == 0
