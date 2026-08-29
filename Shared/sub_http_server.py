import base64
import asyncio
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
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse

from Shared import agent_db, database, hiddify_api, sub_aggregator, userbot_db

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


_FR_TOKEN_RE = re.compile(r"(?:^|[^a-z])fr(?:[^a-z]|$)")


def _service_server_ids(service_id: int) -> list:
    """server_idهای متصل به یک سرویس (از mappings، با fallback به سرور اصلی)."""
    ids: list = []
    try:
        for m in (userbot_db.get_service_nodes(int(service_id)) or []):
            try:
                msid = int((m or {}).get("server_id") or 0)
            except (TypeError, ValueError):
                msid = 0
            if msid > 0 and msid not in ids:
                ids.append(msid)
    except Exception:
        pass
    if not ids:
        try:
            svc = userbot_db.get_service_by_id(int(service_id)) or {}
            psid = int(svc.get("server_id") or 0)
            if psid > 0:
                ids.append(psid)
        except (TypeError, ValueError):
            pass
    return ids


def _invalidate_xui_caches_for_service(service_id: int) -> None:
    """کش X-UI را فقط برای سرورهای همین سرویس باطل کن (بدون هاردکد server_id)."""
    try:
        from Shared import xui_api
    except Exception:
        return
    for sid in _service_server_ids(service_id):
        srv = database.get_server_by_id(sid)
        if not srv:
            continue
        try:
            if not xui_api.is_xui_server(srv):
                continue
        except Exception:
            continue
        try:
            from Shared.xui_sanaei import _invalidate_caches as _inv_sanaei
            _inv_sanaei(srv)
        except Exception:
            pass
        try:
            from Shared.xui_alireza import _invalidate_xui_inbounds_cache as _inv_alireza
            _inv_alireza(srv)
        except Exception:
            pass


def _service_has_france_targets(service_id: int) -> bool:
    """سرویس به سروری وصل است که نشانه فرانسه دارد؟ (توکن fr در نام سرور یا host پنل)"""
    for sid in _service_server_ids(service_id):
        srv = database.get_server_by_id(sid)
        if not srv:
            continue
        candidates = [str((srv or {}).get("name") or "")]
        try:
            candidates.append(urlparse(str((srv or {}).get("panel_url") or "")).hostname or "")
        except Exception:
            pass
        for raw in candidates:
            hay = str(raw or "").strip().lower()
            if hay and _FR_TOKEN_RE.search(hay):
                return True
    return False


def _sub_body_config_count(body: str, is_b64: bool) -> int:
    """تعداد خطوط کانفیگ واقعی در body (اگر b64 بود اول decode می‌کند)."""
    try:
        text = str(body or "")
        if not text:
            return 0
        if is_b64:
            text = base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-8", "ignore")
        return sum(1 for ln in text.splitlines() if "://" in ln)
    except Exception:
        return 0


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
        # واحد نامشخص: فقط همان مقدار — ضریب ۱۰ با env صریح فعال می‌شود
        # (fallback /10 می‌توانست پرداخت را ۱۰ برابر بیش از واریز واقعی تأیید کند)
        candidates.append(amount)
        if _env_bool("SMS_WEBHOOK_ALLOW_RIAL_FALLBACK", False) and amount >= 10:
            candidates.append(int(round(amount / 10)))
    out: list[int] = []
    for item in candidates:
        if item > 0 and item not in out:
            out.append(item)
    return out


def _sms_webhook_manual_window_minutes() -> int:
    return max(5, _to_int(_dotenv_get("SMS_WEBHOOK_MANUAL_APPROVAL_WINDOW_MINUTES", "120"), 120))


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


def _panel_server_id_from_token(token: str) -> int:
    """
    Resolve admin-panel fallback tokens like ``panel-srv-12``.

    These tokens are intentionally distinct from persisted UserBot subscription
    tokens. They let AdminBot-generated smart links serve a panel user by
    ``server_id + uuid`` even when that user was created only from AdminBot and
    has no local userbot_services row yet.
    """
    raw = str(token or "").strip().lower()
    match = re.fullmatch(r"(?:panel[-_])?(?:srv|server)[-_](\d+)", raw)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _build_panel_uuid_subscription_body(token: str, uuid_hint: str, is_b64: bool) -> tuple[str, dict]:
    """
    Build a managed subscription directly from a Hiddify panel user.

    This is a fallback for AdminBot-created users. Normal UserBot services still
    use the persisted service id/token path above, but AdminBot can now generate
    links in the form ``/sub/panel-srv-{server_id}/{uuid}/all.txt`` and this
    function serves configs without requiring a pre-existing UserBot mapping.

    بهبود: کانفیگ‌های همه نودهای متصل به سرور اصلی هم جمع می‌شود تا لینک هوشمند
    کامل باشد.
    """
    server_id = _panel_server_id_from_token(token)
    user_uuid = str(uuid_hint or "").strip()
    if server_id <= 0 or not user_uuid:
        return "", {}

    server = database.get_server_by_id(server_id)
    if not server:
        return "", {}

    service: dict = {
        "id": 0,
        "name": user_uuid,
        "server_id": server_id,
        "server_title": str(server.get("title") or f"server-{server_id}"),
        "usage_current": 0,
        "usage_limit": 0,
    }

    # ---- ساخت لیست سرورهای هدف (اصلی + نودها) ----
    target_servers: list[dict] = [server]
    seen_ids: set[int] = {server_id}
    for node in (server.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        try:
            target_sid = int(node.get("target_server_id") or 0)
        except (TypeError, ValueError):
            target_sid = 0
        if target_sid <= 0 or target_sid in seen_ids:
            continue
        child = database.get_server_by_id(target_sid)
        if not child:
            continue
        seen_ids.add(target_sid)
        target_servers.append(child)
    # اگر سرورِ توکن خودش نود است، parent و sibling نودها را هم اضافه کن
    for parent in database.get_servers() or []:
        is_parent = False
        for node in (parent.get("nodes") or []):
            if not isinstance(node, dict):
                continue
            try:
                cid = int(node.get("target_server_id") or 0)
            except (TypeError, ValueError):
                cid = 0
            if cid == server_id:
                is_parent = True
                break
        if not is_parent:
            continue
        try:
            pid = int(parent.get("id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0 or pid in seen_ids:
            continue
        seen_ids.add(pid)
        target_servers.insert(0, parent)  # parent را اول قرار بده تا primary_name از آن بیاید
        for node in (parent.get("nodes") or []):
            if not isinstance(node, dict):
                continue
            try:
                child_sid = int(node.get("target_server_id") or 0)
            except (TypeError, ValueError):
                child_sid = 0
            if child_sid <= 0 or child_sid in seen_ids:
                continue
            child = database.get_server_by_id(child_sid)
            if not child:
                continue
            seen_ids.add(child_sid)
            target_servers.append(child)

    # ---- جمع‌آوری اطلاعات و کانفیگ‌ها از همه سرورها ----
    total_usage = 0.0
    primary_limit: float | None = None
    min_days_left = None
    primary_name = ""
    primary_fetched = False

    lines: list[str] = []
    seen_lines: set[str] = set()

    for idx, srv in enumerate(target_servers):
        is_primary = idx == 0
        # اطلاعات کاربر از پنل
        try:
            panel_user = asyncio.run(hiddify_api.get_user_by_uuid(srv, user_uuid)) or {}
        except Exception as e:
            logger.warning(
                "Failed fetching panel user for managed admin sub server_id=%s uuid=%s: %s",
                srv.get("id"), user_uuid, e,
            )
            panel_user = {}

        if isinstance(panel_user, dict) and panel_user:
            total_usage += _to_float(panel_user.get("current_usage_GB"), 0.0)
            if is_primary:
                primary_name = (
                    str(panel_user.get("name") or panel_user.get("username") or user_uuid).strip()
                    or user_uuid
                )
                try:
                    primary_limit = sub_aggregator._usage_limit_from_panel_user(panel_user)
                except Exception:
                    primary_limit = _to_float(
                        panel_user.get("usage_limit_GB") or panel_user.get("usage_limit"), 0.0
                    )
                try:
                    days_left = sub_aggregator._days_left_from_panel_user(panel_user)
                    if days_left is not None:
                        min_days_left = int(days_left)
                except Exception:
                    pass
            else:
                try:
                    days_left = sub_aggregator._days_left_from_panel_user(panel_user)
                    if days_left is not None:
                        min_days_left = (
                            days_left if min_days_left is None else min(min_days_left, int(days_left))
                        )
                except Exception:
                    pass

        # کانفیگ‌های این سرور
        fetched: list[str] = []
        try:
            base_url = sub_aggregator._build_user_base_url(srv, user_uuid)
        except Exception:
            base_url = None
        if base_url:
            fetched = sub_aggregator._fetch_subscription_lines(base_url)
        if not fetched:
            fetched = sub_aggregator._fetch_lines_from_admin_api(srv, user_uuid)

        for line in fetched or []:
            ln = str(line or "").strip()
            if not ln:
                continue
            if not sub_aggregator._is_config_line(ln):
                continue
            if sub_aggregator._is_panel_status_config_line(ln):
                continue
            if ln in seen_lines:
                continue
            seen_lines.add(ln)
            lines.append(ln)

        if is_primary:
            primary_fetched = True

    # نرم‌سازی مقادیر سرویس
    service["name"] = primary_name or user_uuid
    service["usage_current"] = total_usage
    service["usage_limit"] = primary_limit if primary_limit is not None else 0.0
    if min_days_left is not None:
        service["days_left"] = int(min_days_left)

    if not lines:
        return "", service

    status_line = sub_aggregator._build_status_config_line(service)
    if status_line:
        lines.insert(0, status_line)

    body = "\n".join(lines)
    if is_b64:
        body = base64.b64encode(body.encode("utf-8")).decode("ascii")
    return body, service


_UUID_SERVER_CACHE: dict = {}
_UUID_SERVER_CACHE_TTL = 600


def _looks_like_panel_uuid(value: str, min_len: int = 32, max_len: int = 64) -> bool:
    """هیورستیک ساده برای تشخیص uuid پنل در لینک‌های قدیمی /sub/{uuid}/all.txt."""
    raw = str(value or "").strip()
    if not raw or len(raw) < min_len or len(raw) > max_len:
        return False
    return "-" in raw


def _build_panel_uuid_body_via_any_server(uuid: str, is_b64: bool) -> tuple[str, dict]:
    """برای لینک‌های قدیمی بدون پیشوند server، سروری که این uuid را دارد پیدا می‌کند.

    فقط وقتی فراخوانی می‌شود که uuid به هیچ سرویسی در دیتابیس‌های محلی وصل نباشد.
    نتیجه کوتاه‌مدت کش می‌شود تا بار اضافی روی پنل‌ها ایجاد نشود.
    """
    uuid = str(uuid or "").strip()
    if not uuid:
        return "", {}

    now = time.time()
    cached = _UUID_SERVER_CACHE.get(uuid)
    if cached and cached[0] > now:
        server_id = cached[1]
        if server_id:
            body, service = _build_panel_uuid_subscription_body(f"panel-srv-{server_id}", uuid, is_b64)
            if body:
                return body, service

    for srv in database.get_servers() or []:
        try:
            server_id = int(srv.get("id") or 0)
        except (TypeError, ValueError):
            server_id = 0
        if server_id <= 0:
            continue
        body, service = _build_panel_uuid_subscription_body(f"panel-srv-{server_id}", uuid, is_b64)
        if body:
            _UUID_SERVER_CACHE[uuid] = (now + _UUID_SERVER_CACHE_TTL, server_id)
            return body, service

    _UUID_SERVER_CACHE[uuid] = (now + _UUID_SERVER_CACHE_TTL, 0)
    return "", {}


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
                # سرویس‌های نمایندگی/مشتری در agent_services ذخیره می‌شوند نه
                # userbot_services؛ لینک هوشمند این ربات‌ها با panel_user_uuid به
                # عنوان token ساخته می‌شود پس باید ابتدا در agent_services جستجو شود.
                agent_svc = _resolve_agent_service_by_uuid(token, uuid_hint)
                if agent_svc:
                    body, a_service = _build_agent_subscription_body(agent_svc, is_b64)
                    if body:
                        self._write(200, body, headers=self._subscription_headers(a_service, is_b64))
                        return
                    self._write(404, "subscription is empty")
                    return
            if not sid:
                panel_body, panel_service = _build_panel_uuid_subscription_body(token, uuid_hint, is_b64)
                if panel_body:
                    self._write(200, panel_body, headers=self._subscription_headers(panel_service, is_b64))
                    return
                if _panel_server_id_from_token(token) and uuid_hint:
                    self._write(404, "subscription is empty")
                    return
                if not uuid_hint and _looks_like_panel_uuid(token):
                    probe_body, probe_service = _build_panel_uuid_body_via_any_server(token, is_b64)
                    if probe_body:
                        self._write(200, probe_body, headers=self._subscription_headers(probe_service, is_b64))
                        return
                self._write(404, "subscription token not found")
                return
            # کش پنل X-UI را برای سرورهای همین سرویس باطل کن تا بعد از
            # پاکسازی دستی DB (DELETE FROM clients) داده کهنه نماند
            try:
                _invalidate_xui_caches_for_service(int(sid))
            except Exception:
                pass
            service = (
                sub_aggregator.sync_service_runtime_from_panels(int(sid))
                or userbot_db.get_service_by_id(int(sid))
                or {}
            )

            if is_b64:
                body = sub_aggregator.build_subscription_b64_for_service(sid)
            else:
                body = sub_aggregator.build_subscription_text_for_service(sid)
            # اگر body کانفیگ واقعی نداشت و سرویس target فرانسه دارد، یک بار
            # دیگر بساز (فیکس sync-فراموش-کرده) — برای بقیه سرویس‌ها دو بار build نشود
            if _service_has_france_targets(int(sid)) and _sub_body_config_count(body, is_b64) == 0:
                try:
                    alt_body = sub_aggregator.build_subscription_text_for_service(int(sid))
                    if alt_body and _sub_body_config_count(alt_body, False) > _sub_body_config_count(body, is_b64):
                        body = alt_body
                except Exception:
                    pass
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
        received_at_ms = _to_int(payload.get("received_at"), 0)
        device_time_ms = _to_int(payload.get("device_time"), 0)
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
                    "received_at": received_at_ms,
                    "device_time": device_time_ms,
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
                    "received_at": received_at_ms,
                    "device_time": device_time_ms,
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
                "received_at": received_at_ms,
                "device_time": device_time_ms,
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
                    received_at_ms=_to_int((existing or {}).get("received_at"), received_at_ms),
                    device_time_ms=_to_int((existing or {}).get("device_time"), device_time_ms),
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
            received_at_ms=received_at_ms,
            device_time_ms=device_time_ms,
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
        received_at_ms: int = 0,
        device_time_ms: int = 0,
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

        already_approved_payment = None
        already_amount = int(candidates[0])
        for amount_toman in candidates:
            already_approved_payment = userbot_db.find_approved_card_payment_by_sms_event(
                event_id=event_id,
                amount_raw=amount_raw,
                currency_raw=currency_raw,
                amount_toman=int(amount_toman),
                sender=sender,
                reference=reference,
            )
            if already_approved_payment:
                already_amount = int(amount_toman)
                break
        if already_approved_payment:
            payment_id = int((already_approved_payment or {}).get("id") or 0)
            userbot_db.update_sms_webhook_event(
                event_id,
                status="approved",
                matched_payment_id=payment_id,
                message="payment already approved by this bank SMS",
                amount_toman=already_amount,
            )
            return 200, {
                "ok": True,
                "matched": True,
                "status": "approved",
                "payment_id": payment_id,
                "tx_code": (already_approved_payment or {}).get("tx_code"),
                "amount_toman": already_amount,
                "message": "payment already approved by this bank SMS",
            }

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
                status="approved_duplicate",
                matched_payment_id=int((prior_event or {}).get("matched_payment_id") or 0),
                message="same bank SMS was already approved before",
                amount_toman=prior_amount,
            )
            return 200, {
                "ok": True,
                "matched": True,
                "duplicate": True,
                "status": "approved_duplicate",
                "message": "bank_sms_already_approved",
                "amount_toman": prior_amount,
                "matched_payment_id": int((prior_event or {}).get("matched_payment_id") or 0),
            }

        # ضد cross-user replay: اگر ادمین به‌تازگی همین مبلغ را به‌صورت دستی
        # تأیید کرده، این پیامک متعلق به همان پرداخت است — هرگز پرداخت
        # در انتظار کاربر دیگری با این پیامک تأیید نشود
        manual_window = _sms_webhook_manual_window_minutes()
        sms_time_ms = int(received_at_ms or device_time_ms or 0)
        for amount_toman in candidates:
            manually_approved = userbot_db.find_recently_admin_approved_card_payments(
                int(amount_toman),
                max_age_minutes=manual_window,
                sms_time_ms=sms_time_ms,
            )
            if manually_approved:
                payment = manually_approved[0]
                payment_id = int((payment or {}).get("id") or 0)
                userbot_db.attach_sms_event_to_approved_payment(
                    payment_id,
                    event_id=event_id,
                    reference=reference,
                    sender=sender,
                    amount_raw=amount_raw,
                    currency_raw=currency_raw,
                )
                userbot_db.update_sms_webhook_event(
                    event_id,
                    status="approved",
                    matched_payment_id=payment_id,
                    message="bank SMS attached to admin-approved payment",
                    amount_toman=int(amount_toman),
                )
                return 200, {
                    "ok": True,
                    "matched": True,
                    "status": "attached_manual_approved",
                    "payment_id": payment_id,
                    "tx_code": (payment or {}).get("tx_code"),
                    "amount_toman": int(amount_toman),
                    "message": "bank SMS attached to admin-approved payment",
                }

        matches: list[dict] = []
        matched_amount = 0
        for amount_toman in candidates:
            matches = userbot_db.find_pending_card_payments_by_amount(
                int(amount_toman),
                card_last4=card_last4,
                max_age_minutes=_sms_webhook_max_pending_age_minutes(),
                sms_time_ms=sms_time_ms,
            )
            if matches:
                matched_amount = int(amount_toman)
                break

        if not matches:
            # اگر این SMS قبلاً برای پرداخت نمایندگی صف شده و آن پرداخت حالا تایید شده است
            # (خودکار یا دستی)، به اپ approved_duplicate برگردان تا رکوردش به «تایید شده» برود
            try:
                from CustomerBot.database import get_sms_auto_queue_by_event as _qrow
                from CustomerBot.database import get_payment_status_any_agent as _pstat
                qrow = _qrow(event_id)
                if qrow:
                    agency_pay_status = _pstat(int(qrow.get("pay_id") or 0))
                    if agency_pay_status == "approved":
                        approved_pay_id = int(qrow.get("pay_id") or 0)
                        userbot_db.update_sms_webhook_event(
                            event_id,
                            status="approved_duplicate",
                            matched_payment_id=approved_pay_id,
                            message="agency payment approved for this SMS",
                            amount_toman=int(qrow.get("amount_toman") or candidates[0] or 0),
                        )
                        return 200, {
                            "ok": True,
                            "matched": True,
                            "duplicate": True,
                            "status": "approved_duplicate",
                            "message": "bank_sms_already_approved",
                            "matched_payment_id": approved_pay_id,
                        }
            except Exception as exc:
                logger.warning("agency sms duplicate check failed: %s", exc)

            # تطبیق با پرداخت‌های مشتریانِ نمایندگی‌ها (دیتابیس CustomerBot) —
            # پرداخت صف می‌شود تا پروسه AgentBot سرویس را بسازد و تحویل دهد
            agency_matches: list = []
            agency_matched_amount = 0
            try:
                from CustomerBot.database import find_pending_card_payments_by_amount as _find_agency_pending
                for amount_toman in candidates:
                    agency_matches = _find_agency_pending(
                        int(amount_toman),
                        max_age_minutes=_sms_webhook_max_pending_age_minutes(),
                        sms_time_ms=sms_time_ms,
                    )
                    if agency_matches:
                        agency_matched_amount = int(amount_toman)
                        break
            except Exception as exc:
                logger.warning("agency sms match failed: %s", exc)

            if agency_matches:
                if len(agency_matches) > 1:
                    userbot_db.update_sms_webhook_event(
                        event_id,
                        status="ambiguous",
                        message=f"multiple agency pending card payments matched amount={agency_matched_amount}",
                        amount_toman=agency_matched_amount,
                    )
                    return 409, {
                        "ok": False,
                        "matched": False,
                        "error": "ambiguous_pending_payments",
                        "scope": "agency",
                        "amount_toman": agency_matched_amount,
                        "count": len(agency_matches),
                    }
                agency_pay = agency_matches[0]
                agency_id = int(agency_pay.get("agent_id") or 0)
                agency_pay_id = int(agency_pay.get("id") or 0)
                try:
                    from CustomerBot.database import enqueue_sms_auto_approval as _enqueue_agency
                    # اگر event قبلاً صف شده باشد (استعلام دوباره اپ) هم مشکلی نیست
                    _enqueue_agency(
                        agency_id,
                        agency_pay_id,
                        event_id,
                        agency_matched_amount,
                        card_last4=card_last4,
                    )
                    queued = True
                except Exception as exc:
                    queued = False
                    logger.warning("agency sms enqueue failed: %s", exc)
                userbot_db.update_sms_webhook_event(
                    event_id,
                    status="agency_queued" if queued else "approve_failed",
                    matched_payment_id=agency_pay_id if queued else 0,
                    message=(
                        f"agency payment queued for auto approval (agent_id={agency_id})"
                        if queued
                        else "agency match found but enqueue failed"
                    ),
                    amount_toman=agency_matched_amount,
                )
                if queued:
                    return 200, {
                        "ok": True,
                        "matched": True,
                        "status": "agency_queued",
                        "payment_id": agency_pay_id,
                        "agent_id": agency_id,
                        "amount_toman": agency_matched_amount,
                        "message": "agency payment queued for automatic approval",
                    }
                return 500, {
                    "ok": False,
                    "matched": True,
                    "status": "agency_queue_failed",
                    "payment_id": agency_pay_id,
                    "agent_id": agency_id,
                }

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


def _resolve_agent_service_by_uuid(token: str, uuid_hint: str) -> Optional[dict]:
    """پیدا کردن سرویس نمایندگی/مشتری در agent_services با uuid پنل."""
    candidate = str(uuid_hint or "").strip() or str(token or "").strip()
    if not candidate:
        return None
    for db in (userbot_db,):
        try:
            reserved = getattr(db, "_RESERVED_SUB_TOKENS", ())
            if candidate in reserved:
                return None
        except Exception:
            break
    try:
        return agent_db.get_service_by_uuid(candidate)
    except Exception as e:
        logger.warning("agent service uuid resolution failed %s: %s", candidate[:12], e)
        return None


def _build_agent_subscription_body(svc: dict, is_b64: bool) -> tuple[str, dict]:
    """ساخت بدنه اشتراک برای سرویس نمایندگی/مشتری از همه نودهای آن.

    برای هیدیفای: از sub-link واقعی (hiddify.txt / all.txt) fetch می‌شود تا همان
    تنظیمات «شامل در اشتراک» رعایت شود و fallback به API انجام نمی‌شود.
    برای X-UI: چون native sub نیاز به fallback API دارد (و شامل در اشتراک معنی ندارد)،
    اگر fetch خالی بود از API پنل (xui_api) خط‌های کانفیگ جمع می‌شود.
    """
    try:
        from Shared.sub_aggregator import (
            _is_config_line,
            _is_panel_status_config_line,
            _fetch_subscription_lines,
            _fetch_lines_from_admin_api,
            _build_status_config_line,
            _service_lock_reason,
        )
        from Shared.sub_links import get_service_user_base_urls, get_service_panel_targets

        lock_reason = _service_lock_reason(svc)
        if not lock_reason and int((svc or {}).get("is_active") or 0) != 1:
            lock_reason = "service_not_found"
        locked_status = ""
        if lock_reason:
            locked_status = _build_status_config_line(svc, lock_reason)

        lines: list[str] = []
        seen: set = set()
        # per-node: هر نود جداگانه HTTP و اگر X-UI و خالی بود API
        for srv, uuid, marzban_un in get_service_panel_targets(svc):
            try:
                from Shared.sub_links import _build_user_base_url as _build_ub
                base = _build_ub(srv, uuid)
            except Exception:
                base = None
            base = str(base or "").strip().rstrip("/")
            fetched: list[str] = []
            if base:
                fetched = _fetch_subscription_lines(base)
            # اگر X-UI و HTTP خالی بود، از API همان نود بگیر
            if not fetched:
                try:
                    from Shared import xui_api
                    if xui_api.is_xui_server(srv):
                        api_lines = _fetch_lines_from_admin_api(srv, uuid, marzban_username=marzban_un)
                        if api_lines:
                            fetched = api_lines
                except Exception:
                    pass
            for ln in fetched:
                raw = str(ln or "").strip()
                if not raw or raw in seen:
                    continue
                if not _is_config_line(raw) or _is_panel_status_config_line(raw):
                    continue
                seen.add(raw)
                lines.append(raw)

        # X-UI fallback کلی: اگر هنوز هیچی نبود و سرویس X-UI دارد، همه نودها از API (برای سازگاری قدیم)
        if not lines:
            try:
                from Shared import xui_api
                needs_xui_fallback = False
                # بررسی اینکه سرویس متعلق به پنل X-UI است
                for srv, _, _ in get_service_panel_targets(svc):
                    if xui_api.is_xui_server(srv):
                        needs_xui_fallback = True
                        break
                if not needs_xui_fallback:
                    # fallback check via server_id directly
                    try:
                        sid = int(svc.get("server_id") or 0)
                        srv = database.get_server_by_id(sid) if sid else None
                        if srv and xui_api.is_xui_server(srv):
                            needs_xui_fallback = True
                    except Exception:
                        pass
                if needs_xui_fallback:
                    for srv, uuid, marzban_un in get_service_panel_targets(svc):
                        if not srv or not uuid:
                            continue
                        try:
                            api_lines = _fetch_lines_from_admin_api(srv, uuid, marzban_username=marzban_un)
                            for ln in api_lines or []:
                                raw = str(ln or "").strip()
                                if not raw or raw in seen:
                                    continue
                                if not _is_config_line(raw) or _is_panel_status_config_line(raw):
                                    continue
                                seen.add(raw)
                                lines.append(raw)
                        except Exception:
                            continue
            except Exception:
                pass

        if lock_reason and not lines:
            if is_b64 and locked_status:
                try:
                    locked_status = base64.b64encode(locked_status.encode("utf-8")).decode("ascii")
                except Exception:
                    pass
            return locked_status or "", svc
    except Exception as e:
        logger.warning("agent sub build failed for uuid=%s: %s", str(svc.get("panel_user_uuid") or "")[:12], e)
        return "", {}

    status_line = locked_status or _build_status_config_line(svc)
    if status_line and lines:
        lines.insert(0, status_line)

    body = "\n".join(lines)
    if is_b64 and body:
        try:
            body = base64.b64encode(body.encode("utf-8")).decode("ascii")
        except Exception:
            pass
    return body, svc
