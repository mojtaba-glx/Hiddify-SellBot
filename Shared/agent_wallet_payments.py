"""Safe cross-database approval operations for representative wallet top-ups."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from AgentBot import database as agentbot_db
from Shared import agent_db, userbot_db


ApprovalResult = Tuple[
    bool,
    str,
    Optional[Dict[str, Any]],
    Optional[Dict[str, Any]],
    bool,
]


def _operation_key(payment_id: int) -> str:
    return f"agent-wallet-payment:{int(payment_id)}"


def _parse_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except (TypeError, ValueError):
            continue
    return None


def _event_datetime(event: Dict[str, Any]) -> Optional[datetime]:
    for key in ("received_at", "device_time"):
        try:
            stamp = float(str((event or {}).get(key) or "").strip())
            if stamp <= 0:
                continue
            if stamp > 10_000_000_000:
                stamp /= 1000.0
            return datetime.fromtimestamp(stamp, timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OSError):
            continue
    return _parse_datetime((event or {}).get("created_at"))


def _valid_wallet_transaction(payment: Dict[str, Any], transaction: Dict[str, Any]) -> bool:
    return bool(transaction) and (
        int(transaction.get("agent_id") or 0) == int(payment.get("agent_id") or 0)
        and int(transaction.get("amount") or 0) == int(payment.get("amount") or 0)
        and str(transaction.get("tx_type") or "") == "charge"
    )


def approve_wallet_charge_from_sms(
    payment_id: int,
    *,
    event_id: str,
    reference: str = "",
    sender: str = "",
    amount_raw: int = 0,
    currency_raw: str = "",
) -> ApprovalResult:
    """Credit one representative wallet exactly once and attach its bank event."""
    pid = int(payment_id or 0)
    event_id = str(event_id or "").strip()[:160]
    if not event_id:
        return False, "bank SMS event id is required", None, None, False
    payment = agentbot_db.get_payment_by_id(pid)
    if not payment:
        return False, "agent wallet payment not found", None, None, False
    if (
        str(payment.get("method") or "") != "card_to_card"
        or str(payment.get("description") or "") != "شارژ کیف پول نماینده"
    ):
        return False, "payment is not a representative wallet top-up", payment, None, False

    agent_id = int(payment.get("agent_id") or 0)
    amount = int(payment.get("amount") or 0)
    if agent_id <= 0 or amount <= 0:
        return False, "invalid representative wallet payment", payment, None, False
    key = _operation_key(pid)
    status = str(payment.get("status") or "").strip().lower()
    transaction = agent_db.get_wallet_transaction_by_key(key)

    if status == "approved":
        if not _valid_wallet_transaction(payment, transaction or {}):
            return False, "approved payment has no matching wallet transaction", payment, None, False
        if not agentbot_db.attach_payment_sms_event(pid, event_id):
            return False, "payment is already linked to another bank SMS", payment, None, False
        wallet = agent_db.get_wallet(agent_id)
        agentbot_db.patch_payment_receipt_metadata(
            pid,
            {
                "sms_event_id": event_id,
                "sms_reference": reference,
                "sms_sender": sender,
                "sms_amount_raw": int(amount_raw or 0),
                "sms_currency": currency_raw,
            },
        )
        return True, "bank SMS attached to approved representative wallet payment", agentbot_db.get_payment_by_id(pid), wallet, False

    if status not in {"pending", "processing"}:
        return False, f"payment status is {status or 'unknown'}", payment, None, False
    if status == "processing":
        if str(payment.get("processing_key") or "") != key:
            return False, "payment is owned by another processor", payment, None, False
        if str(payment.get("sms_event_id") or "").strip() != str(event_id or "").strip():
            return False, "payment is already being processed by another approval", payment, None, False

    newly_approved = status == "pending"
    if status == "pending" and not agentbot_db.claim_payment_processing(
        pid, agent_id, key, sms_event_id=event_id
    ):
        payment = agentbot_db.get_payment_by_id(pid) or payment
        status = str(payment.get("status") or "").strip().lower()
        if status == "approved":
            return approve_wallet_charge_from_sms(
                pid,
                event_id=event_id,
                reference=reference,
                sender=sender,
                amount_raw=amount_raw,
                currency_raw=currency_raw,
            )
        if (
            status != "processing"
            or str(payment.get("processing_key") or "") != key
            or str(payment.get("sms_event_id") or "").strip() != str(event_id or "").strip()
        ):
            return False, "payment could not be claimed", payment, None, False
        newly_approved = False

    try:
        wallet = agent_db.charge_wallet_once(
            agent_id,
            amount,
            key,
            description=f"شارژ کارت به کارت نماینده - تراکنش {payment.get('ref_id')}",
        )
        if not agentbot_db.finish_payment_processing(pid, agent_id, key, "approved"):
            refreshed = agentbot_db.get_payment_by_id(pid) or {}
            if str(refreshed.get("status") or "") != "approved":
                if not agentbot_db.finish_payment_processing(pid, agent_id, key, "approved"):
                    raise RuntimeError("wallet credited but payment approval could not be finalized")
        agentbot_db.patch_payment_receipt_metadata(
            pid,
            {
                "sms_event_id": event_id,
                "sms_reference": reference,
                "sms_sender": sender,
                "sms_amount_raw": int(amount_raw or 0),
                "sms_currency": currency_raw,
            },
        )
        return True, "representative wallet payment approved by bank SMS", agentbot_db.get_payment_by_id(pid), wallet, newly_approved
    except Exception:
        if not agent_db.get_wallet_transaction_by_key(key):
            agentbot_db.finish_payment_processing(pid, agent_id, key, "pending")
        raise


def try_approve_wallet_charge_from_unmatched_sms(
    payment_id: int,
    *,
    max_age_minutes: int = 360,
    receipt_lookback_minutes: int = 30,
) -> Tuple[ApprovalResult, Optional[Dict[str, Any]]]:
    """Retry an SMS that arrived before the representative submitted a receipt."""
    payment = agentbot_db.get_payment_by_id(int(payment_id or 0))
    if not payment or str(payment.get("status") or "") != "pending":
        return (False, "payment is not pending", payment, None, False), None
    amount = int(payment.get("amount") or 0)
    marker = int(payment.get("marker_amount") or 0)
    payer_last4 = str(payment.get("card_last4") or "").strip()
    payment_dt = _parse_datetime(payment.get("created_at"))
    if amount <= 0 or payment_dt is None:
        return (False, "invalid payment amount or time", payment, None, False), None

    lookback = max(1, min(120, int(receipt_lookback_minutes or 30)))
    eligible = []
    # Unmatched-event retries are owner-scoped: the central webhook records
    # events with owner_agent_id=0, so only those may retry into a payment —
    # agent-owned events can never be reused here (and vice versa).
    for event in userbot_db.find_recent_unmatched_sms_webhook_events(
        amount, max_age_minutes=max_age_minutes, owner_agent_id=0
    ):
        event_dt = _event_datetime(event)
        if event_dt is None:
            continue
        if event_dt < payment_dt - timedelta(minutes=lookback):
            continue
        if event_dt > payment_dt + timedelta(minutes=5):
            continue
        event_last4 = str(event.get("card_last4") or "").strip()
        if event_last4:
            if not payer_last4 or event_last4 != payer_last4:
                continue
        elif not (100 <= marker <= 999):
            continue
        prior = userbot_db.find_prior_approved_sms_webhook_event(
            event_id=str(event.get("event_id") or ""),
            amount_raw=int(event.get("amount_raw") or 0),
            currency_raw=str(event.get("currency_raw") or ""),
            amount_toman=amount,
            sender=str(event.get("sender") or ""),
            reference=str(event.get("reference") or ""),
            body=str(event.get("body") or ""),
            owner_agent_id=0,
        )
        if prior:
            continue
        eligible.append(event)

    if len(eligible) != 1:
        message = "no eligible unmatched bank SMS" if not eligible else "multiple unmatched bank SMS events matched"
        return (False, message, payment, None, False), None

    event = eligible[0]
    event_id = str(event.get("event_id") or "")
    result = approve_wallet_charge_from_sms(
        int(payment_id),
        event_id=event_id,
        reference=str(event.get("reference") or ""),
        sender=str(event.get("sender") or ""),
        amount_raw=int(event.get("amount_raw") or 0),
        currency_raw=str(event.get("currency_raw") or ""),
    )
    ok, message, _, _, _ = result
    userbot_db.update_sms_webhook_event(
        event_id,
        status="approved" if ok else "approve_failed",
        matched_payment_id=int(payment_id) if ok else 0,
        message=("representative wallet payment approved from earlier bank SMS" if ok else message),
        amount_toman=amount,
    )
    return result, event
