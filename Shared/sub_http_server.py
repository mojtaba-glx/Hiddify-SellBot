import base64
import json
import logging
import os
import re
import threading
import time
import hmac
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from Shared import sub_aggregator, userbot_db

logger = logging.getLogger(__name__)
BYTES_PER_GB = 1024 ** 3
SMS_WEBHOOK_SECRET_ENV = "SMS_WEBHOOK_SECRET"
SMS_WEBHOOK_ENABLED_ENV = "SMS_WEBHOOK_ENABLED"
SMS_WEBHOOK_MAX_PENDING_AGE_ENV = "SMS_WEBHOOK_MAX_PENDING_AGE_MINUTES"
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _dotenv_get(name: str, default: str = "") -> str:
    key = str(name or "").strip()
    if not key:
        return str(default or "")
    try:
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                env_key, value = raw.split("=", 1)
                if env_key.strip() != key:
                    continue
                value = value.strip()
                if (
                    len(value) >= 2
                    and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'"))
                ):
                    value = value[1:-1]
                return value
    except Exception as e:
        logger.warning("Failed reading env value %s from %s: %s", key, ENV_FILE, e)
    return str(os.getenv(key, default) or default or "")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(_dotenv_get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on", "enable", "enabled", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "disable", "disabled", "n"}:
        return False
    return bool(default)


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except Exception:
        return int(default)


def _normalize_sms_currency(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if text in {"rial", "irr", "ریال", "ريال"}:
        return "rial"
    if text in {"toman", "تومان"}:
        return "toman"
    return "unknown"


def _sms_amount_candidates_toman(amount_raw: int, currency_raw: str) -> list[int]:
    amount = int(amount_raw or 0)
    if amount <= 0:
        return []
    currency = _normalize_sms_currency(currency_raw)
    candidates: list[int] = []
    if currency == "rial":
        candidates.append(int(round(amount / 10)))
    elif currency == "toman":
        candidates.append(amount)
    else:
        candidates.append(amount)
        if amount >= 10:
            candidates.append(int(round(amount / 10)))
    out: list[int] = []
    for item in candidates:
        if item > 0 and item not in out:
            out.append(item)
    return out


def _sms_webhook_secret() -> str:
    return str(_dotenv_get(SMS_WEBHOOK_SECRET_ENV, "") or "").strip()


def _sms_webhook_max_pending_age_minutes() -> int:
    return max(5, _to_int(_dotenv_get(SMS_WEBHOOK_MAX_PENDING_AGE_ENV, "360"), 360))


def _parse_receipt_meta(raw: str) -> dict[str, str]:
    raw = str(raw or "").strip()
    if not raw:
        return {}
    if "|" not in raw and ":" not in raw:
        return {"admin_fid": raw}
    data: dict[str, str] = {}
    for part in raw.split("|"):
        part = part.strip()
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            data[key] = value
    return data


def _telegram_form_request(token: str, method: str, fields: dict, timeout: int = 8) -> None:
    data = urllib.parse.urlencode({str(k): str(v) for k, v in fields.items()}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read(512)


def _telegram_photo_file_request(token: str, fields: dict, file_path: str, timeout: int = 12) -> None:
    path = Path(str(file_path or ""))
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    boundary = f"----SellBotBoundary{int(time.time() * 1000)}"
    body = bytearray()
    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        b'Content-Disposition: form-data; name="photo"; filename="receipt.jpg"\r\n'
        b"Content-Type: image/jpeg\r\n\r\n"
    )
    body.extend(path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read(512)


def _send_admin_sms_payment_report(payment: dict, amount_toman: int, sender: str, reference: str) -> None:
    token = str(_dotenv_get("ADMIN_BOT_TOKEN", "") or "").strip()
    admin_id = _to_int(_dotenv_get("ADMIN_ID", "0"), 0)
    if not token or admin_id <= 0:
        return

    tx_code = str((payment or {}).get("tx_code") or "").strip()
    payment_id = int((payment or {}).get("id") or 0)
    username = str((payment or {}).get("username") or "").strip()
    full_name = str((payment or {}).get("full_name") or "").strip()
    telegram_id = str((payment or {}).get("telegram_id") or "").strip()
    user_label = full_name or (f"@{username}" if username else telegram_id) or "نامشخص"
    receipt_meta = _parse_receipt_meta(str((payment or {}).get("receipt_image") or ""))
    receipt_photo_id = str(receipt_meta.get("admin_fid") or "").strip()
    receipt_local_path = str(receipt_meta.get("local_path") or "").strip()
    has_receipt = bool(receipt_photo_id or receipt_local_path)
    _clear_pending_admin_payment_keyboard(token, admin_id, receipt_meta)

    text = (
        "✅ پرداخت کارت‌به‌کارت با SMS تایید شد.\n"
        f"👤 کاربر: {user_label}\n"
        f"💰 مبلغ: {int(amount_toman or 0):,} تومان\n"
        f"🧾 کد تراکنش: {tx_code or '-'}\n"
        f"🆔 شناسه پرداخت: {payment_id or '-'}\n"
        f"📨 سرشماره: {sender or '-'}\n"
        f"🔖 پیگیری SMS: {reference or '-'}\n"
        f"🖼 رسید کاربر: {'پیوست شد' if has_receipt else 'در دسترس نیست'}"
    )
    try:
        if receipt_photo_id:
            _telegram_form_request(
                token,
                "sendPhoto",
                {
                    "chat_id": str(admin_id),
                    "photo": receipt_photo_id,
                    "caption": text,
                },
            )
            return
        if receipt_local_path:
            _telegram_photo_file_request(
                token,
                {
                    "chat_id": str(admin_id),
                    "caption": text,
                },
                receipt_local_path,
            )
            return
        _telegram_form_request(
            token,
            "sendMessage",
            {
                "chat_id": str(admin_id),
                "text": text,
                "disable_web_page_preview": "true",
            },
        )
    except Exception as e:
        logger.warning("Failed sending SMS payment admin report with receipt payment_id=%s: %s", payment_id, e)
        try:
            _telegram_form_request(
                token,
                "sendMessage",
                {
                    "chat_id": str(admin_id),
                    "text": text,
                    "disable_web_page_preview": "true",
                },
            )
        except Exception as fallback_error:
            logger.warning("Failed sending fallback SMS payment admin report payment_id=%s: %s", payment_id, fallback_error)


def _clear_pending_admin_payment_keyboard(token: str, default_admin_id: int, receipt_meta: dict[str, str]) -> None:
    try:
        chat_id = _to_int((receipt_meta or {}).get("admin_chat_id"), default_admin_id)
        message_id = _to_int((receipt_meta or {}).get("admin_message_id"), 0)
        if not token or chat_id <= 0 or message_id <= 0:
            return
        deleted = False
        try:
            _telegram_form_request(
                token,
                "deleteMessage",
                {
                    "chat_id": str(chat_id),
                    "message_id": str(message_id),
                },
            )
            deleted = True
        except Exception:
            deleted = False
        if not deleted:
            _telegram_form_request(
                token,
                "editMessageReplyMarkup",
                {
                    "chat_id": str(chat_id),
                    "message_id": str(message_id),
                },
            )
    except Exception as e:
        logger.warning("Failed clearing pending admin payment keyboard: %s", e)


def _send_admin_sms_reused_report(
    *,
    prior_event: dict,
    amount_toman: int,
    sender: str,
    reference: str,
) -> None:
    token = str(_dotenv_get("ADMIN_BOT_TOKEN", "") or "").strip()
    admin_id = _to_int(_dotenv_get("ADMIN_ID", "0"), 0)
    if not token or admin_id <= 0:
        return
    prior_payment_id = int((prior_event or {}).get("matched_payment_id") or 0)
    prior_event_id = str((prior_event or {}).get("event_id") or "").strip()
    text = (
        "⚠️ SMS بانکی تکراری شناسایی شد و تایید خودکار انجام نشد.\n"
        f"💰 مبلغ: {int(amount_toman or 0):,} تومان\n"
        f"📨 سرشماره: {sender or '-'}\n"
        f"🔖 پیگیری SMS: {reference or '-'}\n"
        f"🆔 پرداخت تاییدشده قبلی: {prior_payment_id or '-'}\n"
        f"🔐 شناسه SMS قبلی: {prior_event_id[:18] + '...' if prior_event_id else '-'}\n"
        "لطفاً رسید جدید را دستی بررسی کنید."
    )
    try:
        _telegram_form_request(
            token,
            "sendMessage",
            {
                "chat_id": str(admin_id),
                "text": text,
                "disable_web_page_preview": "true",
            },
        )
    except Exception as e:
        logger.warning("Failed sending reused SMS admin report: %s", e)


def _query_requests_base64(query: str) -> bool:
    params = parse_qs(str(query or ""), keep_blank_values=True)
    for key, values in params.items():
        key_lower = str(key or "").strip().lower()
        value_set = {str(v or "").strip().lower() for v in (values or [])}
        if key_lower in {"base64", "b64"} and (
            not value_set or value_set & {"", "1", "true", "yes", "y", "on", "base64", "b64"}
        ):
            return True
        if key_lower in {"format", "type"} and value_set & {"base64", "b64"}:
            return True
    return False


def _detect_file_format(file_part: str, query: str = ""):
    file_lower = str(file_part or "").strip().lower()
    query_is_b64 = _query_requests_base64(query)
    if file_lower in {"all.txt", "hiddify.txt"}:
        return True if query_is_b64 else False
    if file_lower in {"all.b64", "hiddify.b64"}:
        return True
    return None


class _SubHandler(BaseHTTPRequestHandler):
    def _write(
        self,
        status: int,
        body: str,
        content_type: str = "text/plain; charset=utf-8",
        headers: dict | None = None,
    ) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            if key and value is not None:
                self.send_header(str(key), str(value))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_json(self, status: int, payload: dict) -> None:
        self._write(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    @staticmethod
    def _subscription_headers(service: dict, is_b64: bool) -> dict:
        title = str((service or {}).get("name") or "subscription").strip() or "subscription"
        encoded_title = base64.b64encode(title.encode("utf-8")).decode("ascii")
        ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("._-") or "subscription"
        ext = "b64" if is_b64 else "txt"
        utf8_filename = quote(f"{title}.{ext}")

        try:
            usage_current_gb = max(float((service or {}).get("usage_current") or 0), 0.0)
        except (TypeError, ValueError):
            usage_current_gb = 0.0
        try:
            usage_limit_gb = max(float((service or {}).get("usage_limit") or 0), 0.0)
        except (TypeError, ValueError):
            usage_limit_gb = 0.0
        try:
            days_left = int((service or {}).get("days_left"))
        except (TypeError, ValueError):
            days_left = None

        userinfo_parts = [
            "upload=0",
            f"download={int(usage_current_gb * BYTES_PER_GB)}",
            f"total={int(usage_limit_gb * BYTES_PER_GB)}",
        ]
        if days_left is not None:
            userinfo_parts.append(f"expire={int(time.time()) + max(days_left, 0) * 86400}")

        return {
            "profile-title": f"base64:{encoded_title}",
            "profile-update-interval": "24",
            "subscription-userinfo": "; ".join(userinfo_parts),
            "Content-Disposition": f"inline; filename=\"{ascii_name}.{ext}\"; filename*=UTF-8''{utf8_filename}",
        }

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            p = parsed.path.strip("/")
            query = parsed.query
            parts = p.split("/")
            if not parts or parts[0] != "sub":
                self._write(404, "not found")
                return

            token = ""
            uuid_hint = ""
            is_b64 = False

            # New formats:
            # /sub/{token}/{uuid}/all.txt
            # /sub/{token}/{uuid}/all.txt?base64=1
            # /sub/{token}/{uuid}/all.b64
            if len(parts) == 4:
                token = parts[1].strip()
                uuid_hint = parts[2].strip()
                if not token or not uuid_hint:
                    self._write(404, "not found")
                    return
                detected = _detect_file_format(parts[3], query)
                if detected is None:
                    self._write(404, "not found")
                    return
                is_b64 = detected
            # New formats:
            # /sub/{token}/all.txt | /sub/{token}/all.txt?base64=1 | /sub/{token}/all.b64
            # backward compat:
            # /sub/{token}/hiddify.txt | /sub/{token}/hiddify.b64
            elif len(parts) == 3:
                token = parts[1].strip()
                detected = _detect_file_format(parts[2], query)
                if detected is None:
                    self._write(404, "not found")
                    return
                is_b64 = detected
            # Backward-compatible format: /sub/{token}.txt | /sub/{token}.txt?base64=1 | /sub/{token}.b64
            elif len(parts) == 2:
                file_part = parts[1]
                if file_part.endswith(".txt"):
                    token = file_part[:-4]
                    is_b64 = _query_requests_base64(query)
                elif file_part.endswith(".b64"):
                    token = file_part[:-4]
                    is_b64 = True
                else:
                    self._write(404, "not found")
                    return
            else:
                self._write(404, "not found")
                return

            sid = userbot_db.get_service_id_by_sub_token(token)
            if not sid and uuid_hint:
                owner = userbot_db.get_service_owner_by_panel_uuid(uuid_hint)
                if owner and owner.get("service_id"):
                    sid = int(owner["service_id"])
            if not sid:
                owner = userbot_db.get_service_owner_by_panel_uuid(token)
                if owner and owner.get("service_id"):
                    sid = int(owner["service_id"])
            if not sid:
                self._write(404, "subscription token not found")
                return
            service = (
                sub_aggregator.sync_service_runtime_from_panels(int(sid))
                or userbot_db.get_service_by_id(int(sid))
                or {}
            )
            lock_reason = sub_aggregator.get_service_lock_reason(sid)
            if lock_reason:
                self._write(403, f"subscription is locked: {lock_reason}")
                return

            if is_b64:
                body = sub_aggregator.build_subscription_b64_for_service(sid)
            else:
                body = sub_aggregator.build_subscription_text_for_service(sid)
            if not body:
                self._write(404, "subscription is empty")
                return
            self._write(200, body, headers=self._subscription_headers(service, is_b64))
        except Exception as e:
            logger.exception("sub server request failed: %s", e)
            self._write(500, "internal error")

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path not in {"/payment/sms-webhook", "/sms-webhook"}:
                self._write_json(404, {"ok": False, "error": "not_found"})
                return
            self._handle_sms_webhook()
        except Exception as e:
            logger.exception("sms webhook request failed: %s", e)
            self._write_json(500, {"ok": False, "error": "internal_error"})

    def _handle_sms_webhook(self) -> None:
        if not _env_bool(SMS_WEBHOOK_ENABLED_ENV, False):
            self._write_json(403, {"ok": False, "error": "sms_webhook_disabled"})
            return

        expected_secret = _sms_webhook_secret()
        if not expected_secret:
            self._write_json(503, {"ok": False, "error": "sms_webhook_secret_not_configured"})
            return

        provided_secret = str(self.headers.get("X-SellBot-Sms-Secret") or "").strip()
        if not hmac.compare_digest(provided_secret, expected_secret):
            self._write_json(401, {"ok": False, "error": "invalid_secret"})
            return

        try:
            length = min(int(self.headers.get("Content-Length", "0") or "0"), 64 * 1024)
        except Exception:
            length = 0
        if length <= 0:
            self._write_json(400, {"ok": False, "error": "empty_body"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", errors="ignore"))
        except Exception:
            self._write_json(400, {"ok": False, "error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._write_json(400, {"ok": False, "error": "invalid_payload"})
            return

        event_id = str(payload.get("event_id") or self.headers.get("X-SellBot-Event-Id") or "").strip()
        amount_raw = _to_int(payload.get("amount"), 0)
        currency_raw = _normalize_sms_currency(str(payload.get("currency") or ""))
        reference = str(payload.get("reference") or "").strip()
        sender = str(payload.get("sender") or "").strip()
        card_last4 = re.sub(r"\D", "", str(payload.get("card_last4") or ""))[-4:]
        body = str(payload.get("body") or "")
        is_test = bool(payload.get("test"))

        if not event_id:
            self._write_json(400, {"ok": False, "error": "event_id_required"})
            return

        if is_test:
            inserted, existing = userbot_db.record_sms_webhook_event(
                {
                    "event_id": event_id,
                    "sender": sender,
                    "amount_raw": amount_raw,
                    "currency_raw": currency_raw,
                    "amount_toman": 0,
                    "reference": reference,
                    "card_last4": card_last4,
                    "body": body,
                    "status": "test_received",
                    "message": "test webhook received",
                    "received_at": _to_int(payload.get("received_at"), 0),
                    "device_time": _to_int(payload.get("device_time"), 0),
                }
            )
            self._write_json(200, {"ok": True, "test": True, "duplicate": not inserted, "event": existing or {}})
            return

        candidates = _sms_amount_candidates_toman(amount_raw, currency_raw)
        if not candidates:
            userbot_db.record_sms_webhook_event(
                {
                    "event_id": event_id,
                    "sender": sender,
                    "amount_raw": amount_raw,
                    "currency_raw": currency_raw,
                    "amount_toman": 0,
                    "reference": reference,
                    "card_last4": card_last4,
                    "body": body,
                    "status": "invalid_amount",
                    "message": "amount not found or invalid",
                    "received_at": _to_int(payload.get("received_at"), 0),
                    "device_time": _to_int(payload.get("device_time"), 0),
                }
            )
            self._write_json(422, {"ok": False, "error": "invalid_amount"})
            return

        inserted, existing = userbot_db.record_sms_webhook_event(
            {
                "event_id": event_id,
                "sender": sender,
                "amount_raw": amount_raw,
                "currency_raw": currency_raw,
                "amount_toman": candidates[0],
                "reference": reference,
                "card_last4": card_last4,
                "body": body,
                "status": "received",
                "message": "received",
                "received_at": _to_int(payload.get("received_at"), 0),
                "device_time": _to_int(payload.get("device_time"), 0),
            }
        )
        if not inserted:
            status = str((existing or {}).get("status") or "").strip().lower()
            if status in {"received", "no_pending_match", "ambiguous"}:
                retry_status, retry_payload = self._try_approve_sms_event(
                    event_id=event_id,
                    amount_raw=_to_int((existing or {}).get("amount_raw"), amount_raw),
                    currency_raw=str((existing or {}).get("currency_raw") or currency_raw),
                    reference=str((existing or {}).get("reference") or reference),
                    sender=str((existing or {}).get("sender") or sender),
                    card_last4=re.sub(r"\D", "", str((existing or {}).get("card_last4") or card_last4))[-4:],
                    body=str((existing or {}).get("body") or body),
                )
                retry_payload["duplicate"] = True
                retry_payload["retry"] = True
                retry_payload["previous_status"] = status
                self._write_json(retry_status, retry_payload)
                return
            self._write_json(
                200,
                {
                    "ok": True,
                    "duplicate": True,
                    "status": (existing or {}).get("status"),
                    "matched_payment_id": (existing or {}).get("matched_payment_id"),
                },
            )
            return

        status_code, response = self._try_approve_sms_event(
            event_id=event_id,
            amount_raw=amount_raw,
            currency_raw=currency_raw,
            reference=reference,
            sender=sender,
            card_last4=card_last4,
            body=body,
        )
        self._write_json(status_code, response)
        return

    def _try_approve_sms_event(
        self,
        *,
        event_id: str,
        amount_raw: int,
        currency_raw: str,
        reference: str,
        sender: str,
        card_last4: str,
        body: str = "",
    ) -> tuple[int, dict]:
        candidates = _sms_amount_candidates_toman(amount_raw, currency_raw)
        if not candidates:
            userbot_db.update_sms_webhook_event(
                event_id,
                status="invalid_amount",
                message="amount not found or invalid",
                amount_toman=0,
            )
            return 422, {"ok": False, "error": "invalid_amount"}

        prior_event = None
        prior_amount = int(candidates[0])
        for amount_toman in candidates:
            prior_event = userbot_db.find_prior_approved_sms_webhook_event(
                event_id=event_id,
                amount_raw=amount_raw,
                currency_raw=currency_raw,
                amount_toman=int(amount_toman),
                sender=sender,
                reference=reference,
                body=body,
            )
            if prior_event:
                prior_amount = int(amount_toman)
                break
        if prior_event:
            userbot_db.update_sms_webhook_event(
                event_id,
                status="sms_reused",
                matched_payment_id=int((prior_event or {}).get("matched_payment_id") or 0),
                message="same bank SMS was already used for another approved payment",
                amount_toman=prior_amount,
            )
            _send_admin_sms_reused_report(
                prior_event=prior_event,
                amount_toman=prior_amount,
                sender=sender,
                reference=reference,
            )
            return 202, {
                "ok": True,
                "matched": False,
                "status": "sms_reused",
                "message": "bank_sms_already_used",
                "amount_toman": prior_amount,
                "previous_payment_id": int((prior_event or {}).get("matched_payment_id") or 0),
            }

        matches: list[dict] = []
        matched_amount = 0
        for amount_toman in candidates:
            matches = userbot_db.find_pending_card_payments_by_amount(
                int(amount_toman),
                card_last4=card_last4,
                max_age_minutes=_sms_webhook_max_pending_age_minutes(),
            )
            if matches:
                matched_amount = int(amount_toman)
                break

        if not matches:
            userbot_db.update_sms_webhook_event(
                event_id,
                status="no_pending_match",
                message=f"no pending card payment for candidates={candidates}",
                amount_toman=candidates[0],
            )
            return 202, {
                "ok": True,
                "matched": False,
                "status": "no_pending_match",
                "amount_candidates_toman": candidates,
            }

        if len(matches) > 1:
            userbot_db.update_sms_webhook_event(
                event_id,
                status="ambiguous",
                message=f"multiple pending card payments matched amount={matched_amount}",
                amount_toman=matched_amount,
            )
            return 409, {
                "ok": False,
                "matched": False,
                "error": "ambiguous_pending_payments",
                "amount_toman": matched_amount,
                "count": len(matches),
            }

        payment = matches[0]
        payment_id = int(payment.get("id") or 0)
        ok, message, updated = userbot_db.approve_pending_card_payment_from_sms(
            payment_id,
            event_id=event_id,
            reference=reference,
            sender=sender,
            amount_raw=amount_raw,
            currency_raw=currency_raw,
        )
        userbot_db.update_sms_webhook_event(
            event_id,
            status="approved" if ok else "approve_failed",
            matched_payment_id=payment_id if ok else 0,
            message=message,
            amount_toman=matched_amount,
        )
        if ok:
            _send_admin_sms_payment_report(updated or payment, matched_amount, sender, reference)
        return 200 if ok else 500, {
            "ok": bool(ok),
            "matched": bool(ok),
            "status": "approved" if ok else "approve_failed",
            "payment_id": payment_id,
            "tx_code": (updated or {}).get("tx_code") if updated else payment.get("tx_code"),
            "amount_toman": matched_amount,
            "message": message,
        }

    def log_message(self, format: str, *args):  # noqa: A003
        return


_server_thread: threading.Thread | None = None


def start_sub_server(host: str, port: int) -> None:
    global _server_thread
    if _server_thread and _server_thread.is_alive():
        return

    def _run():
        httpd = ThreadingHTTPServer((host, int(port)), _SubHandler)
        logger.info("Subscription HTTP server started on %s:%s", host, port)
        httpd.serve_forever()

    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()
