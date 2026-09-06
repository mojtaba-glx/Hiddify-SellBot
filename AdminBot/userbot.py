from __future__ import annotations

import base64
import math
import os
import logging
import re
import asyncio
import json
import secrets
import tempfile
import zipfile
import shutil
import sqlite3
from io import BytesIO
from html import escape as html_escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import qrcode
from telegram import (
    Update,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Bot 
)
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from dotenv import load_dotenv

from AdminBot.keyboards import admin_main_keyboard
from Shared.tg_button_styles import BUTTON_STYLE_THEMES, normalize_button_theme
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from Shared.tg_button_styles import keyboard_button as KeyboardButton
from Shared import userbot_db, database, hiddify_api

load_dotenv()
USER_BOT_TOKEN = os.getenv("USER_BOT_TOKEN")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

logger = logging.getLogger(__name__)

USERBOT_PAGE_SIZE = 21
BACKUP_RESTORE_LOCK = asyncio.Lock()

# ===============================
#   ثابت‌ها و تنظیمات
# ===============================
USER_SEARCH_STATE_KEY = "userbot_search_state"
ORDERS_SEARCH_STATE_KEY = "orders_search_state"
PAYMENT_SEARCH_STATE = "userbot_payment_search"

# تنظیمات اشتراک (منوی تنظیمات ربات کاربران)
SUBS_SETTINGS_KEY = "userbot_subscription_settings"
DEFAULT_SUBS_SETTINGS = {
    "show_user_page_link": True,
    "show_username": True,
    "shuffle_configs": True,
    "shuffle_server_layout": True,
    "shuffle_config_layout": True,
    "show_direct_config": True,
    "show_auto_sub_link": False,
    "show_sub_link": True,
    "show_sub_link_b64": False,
    "show_multi_server": False,
    "show_multi_server_b64": False,
}

# استیت‌های جدید برای عملیات پروفایل
WALLET_EDIT_STATE = "userbot_wallet_edit"
MESSAGE_SEND_STATE = "userbot_message_send"
SUB_REMINDER_EDIT_STATE = "userbot_sub_reminder_edit"
SUB_BASE_URL_EDIT_STATE = "userbot_sub_base_url_edit"
TRIAL_SPEC_EDIT_STATE = "userbot_trial_spec_edit"
RENEW_POLICY_EDIT_STATE = "userbot_renew_policy_edit"
EVENT_CHANNEL_EDIT_STATE = "userbot_event_channel_edit"
BACKUP_CHANNEL_EDIT_STATE = "userbot_backup_channel_edit"
BACKUP_RESTORE_STATE = "userbot_backup_restore_state"
TX_PLANS_EDIT_STATE = "userbot_tx_plans_edit"
TEXT_SETTINGS_EDIT_STATE = "userbot_text_settings_edit"
INVITE_BANNER_PHOTO_EDIT_STATE = "userbot_invite_banner_photo_edit"
MARKETING_EDIT_STATE = "userbot_marketing_edit"
FORCE_JOIN_EDIT_STATE = "userbot_force_join_edit"
PAYMENT_CHANNEL_EDIT_STATE = "userbot_payment_channel_edit"
PAYMENT_CARD_ADD_STATE = "userbot_payment_card_add"
PAYMENT_CARD_DELETE_STATE = "userbot_payment_card_delete"
PAYMENT_CARD_EDIT_STATE = "userbot_payment_card_edit"
ZARIN_COUPON_ADD_STATE = "userbot_zarin_coupon_add"
ZARIN_COUPON_DELETE_STATE = "userbot_zarin_coupon_delete"
ZARIN_COUPON_LINK_STATE = "userbot_zarin_coupon_link"
ZARIN_COUPON_AMOUNT_STATE = "userbot_zarin_coupon_amount"
ZARIN_COUPON_CODE_STATE = "userbot_zarin_coupon_code"
ZARIN_COUPON_LIMIT_STATE = "userbot_zarin_coupon_limit"
ZARIN_COUPON_EXP_STATE = "userbot_zarin_coupon_exp"
ZARIN_COUPON_BULK_STATE = "userbot_zarin_coupon_bulk"

# Referral (دعوت دوستان)
REFERRAL_VALUE_EDIT_STATE = "userbot_referral_value_edit"
REFERRAL_MANUAL_REWARD_STATE = "userbot_referral_manual_reward"

GIFT_CAMPAIGN_PRESETS: Dict[str, Dict[str, Any]] = {
    "welcome": {
        "title": "🎁 خوش‌آمدگویی",
        "prefix": "WELCOME",
        "amount": 30000,
        "max_uses": 100,
        "hours": 168,
        "note": "برای کاربران تازه و شروع کمپین‌های کوچک.",
    },
    "festival": {
        "title": "🔥 جشنواره فروش",
        "prefix": "FEST",
        "amount": 50000,
        "max_uses": 300,
        "hours": 48,
        "note": "هدیه محدود و فوری برای کانال یا گروه.",
    },
    "vip": {
        "title": "💎 مشتری وفادار",
        "prefix": "VIP",
        "amount": 100000,
        "max_uses": 50,
        "hours": 72,
        "note": "برای کاربران ارزشمند یا جبران نارضایتی.",
    },
    "winback": {
        "title": "🕒 بازگشت کاربر",
        "prefix": "BACK",
        "amount": 40000,
        "max_uses": 150,
        "hours": 120,
        "note": "برای برگرداندن کاربران غیرفعال.",
    },
}
SUB_TRACKING_STATE = "userbot_subscription_tracking"
TICKET_REPLY_STATE = "userbot_ticket_reply"
BROADCAST_SEND_STATE = "userbot_broadcast_send"
TICKETS_PAGE_SIZE = 21
TICKET_SHOT_START_PREFIX = "tshot"

# کلمات لغو
CANCEL_WORDS = {"❌لغو", "لغو❌", "لغو", "/cancel"}


# ===============================
#   Helper Functions
# ===============================

def _display_name(user: Dict[str, Any]) -> str:
    """نمایش نام کاربر (یوزرنیم یا نام یا شناسه)"""
    username = (user.get("username") or "").strip()
    full_name = (user.get("full_name") or "").strip()
    if username:
        return f"@{username}"
    if full_name:
        return full_name
    return str(user.get("telegram_id") or user.get("id") or _adm_t("ub_user"))


def _normalize_public_base_url(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text == "0":
        return ""
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").strip().lower()
    if scheme not in {"http", "https"}:
        return ""
    host = (parsed.hostname or "").strip()
    if not host:
        return ""
    port = parsed.port
    netloc = host
    if port:
        netloc = f"{host}:{port}"
    return f"{scheme}://{netloc}"


def _extract_host_only(raw_url: str) -> str:
    raw = str(raw_url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"//{raw}")
        host = (parsed.hostname or "").strip().lower()
        return host
    except Exception:
        return ""


def _guess_ssl_domain_hint() -> str:
    custom = _extract_host_only(userbot_db.get_managed_sub_base_url())
    if custom and "." in custom and custom != "localhost":
        return custom
    try:
        servers = database.get_servers() or []
    except Exception:
        servers = []
    for srv in servers:
        panel = _extract_host_only(srv.get("panel_url") or "")
        if panel and "." in panel and panel != "localhost":
            return panel
    return "site.example.com"


def _env_bool_value(raw: Any, default: bool = False) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on", "enable", "enabled", "y"}:
        return True
    if text in {"0", "false", "no", "off", "disable", "disabled", "n"}:
        return False
    return bool(default)


def _read_env_values() -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        if not ENV_FILE.exists():
            return values
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            key = key.strip()
            value = value.strip()
            if (
                len(value) >= 2
                and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'"))
            ):
                value = value[1:-1]
            values[key] = value
    except Exception as e:
        logger.warning("Failed reading env file %s: %s", ENV_FILE, e)
    return values


def _write_env_values(updates: Dict[str, Any]) -> None:
    clean_updates = {str(k): str(v) for k, v in (updates or {}).items() if str(k or "").strip()}
    if not clean_updates:
        return

    lines: List[str] = []
    seen: Set[str] = set()
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    out: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in clean_updates:
                out.append(f"{key}={clean_updates[key]}")
                seen.add(key)
                continue
        out.append(line)

    for key, value in clean_updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")

    for key, value in clean_updates.items():
        os.environ[key] = value
    load_dotenv(dotenv_path=ENV_FILE, override=True)


def _mask_secret(secret: str) -> str:
    text = str(secret or "").strip()
    if not text:
        return _adm_t("ub_not_configured")
    if len(text) <= 12:
        return text[:3] + "..." + text[-3:]
    return text[:8] + "..." + text[-6:]


def _sms_webhook_status() -> Dict[str, Any]:
    env = _read_env_values()
    enabled_raw = env.get("SMS_WEBHOOK_ENABLED", os.getenv("SMS_WEBHOOK_ENABLED", "false"))
    secret = env.get("SMS_WEBHOOK_SECRET", os.getenv("SMS_WEBHOOK_SECRET", ""))
    age = env.get("SMS_WEBHOOK_MAX_PENDING_AGE_MINUTES", os.getenv("SMS_WEBHOOK_MAX_PENDING_AGE_MINUTES", "360"))

    base_url = str(userbot_db.get_managed_sub_base_url() or "").strip().rstrip("/")
    if not base_url:
        host = env.get("SUB_SERVER_PUBLIC_HOST", os.getenv("SUB_SERVER_PUBLIC_HOST", "")).strip()
        scheme = env.get("SUB_SERVER_PUBLIC_SCHEME", os.getenv("SUB_SERVER_PUBLIC_SCHEME", "https")).strip() or "https"
        port = env.get("SUB_SERVER_PUBLIC_PORT", os.getenv("SUB_SERVER_PUBLIC_PORT", "443")).strip()
        if host:
            default_port = (scheme == "https" and port == "443") or (scheme == "http" and port == "80")
            base_url = f"{scheme}://{host}" if default_port else f"{scheme}://{host}:{port}"
    endpoint = f"{base_url}/payment/sms-webhook" if base_url else "https://YOUR_SUB_DOMAIN/payment/sms-webhook"
    return {
        "enabled": _env_bool_value(enabled_raw, False),
        "secret": str(secret or "").strip(),
        "age": str(age or "360").strip() or "360",
        "endpoint": endpoint,
        "env_file": str(ENV_FILE),
    }


def _format_gb(value: Any) -> str:
    if value is None:
        return _adm_t("ub_unknown")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v.is_integer():
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_toman(value: Any) -> str:
    """فرمت پول به تومان (3 رقم 3 رقم)"""
    if value is None:
        return "0"
    try:
        v = int(float(value))
        return f"{v:,}"
    except (TypeError, ValueError):
        return str(value)


def _generate_unique_gift_code(prefix: str, length: int = 6) -> str:
    clean_prefix = re.sub(r"[^A-Za-z0-9_-]", "", str(prefix or "GIFT").strip().upper()) or "GIFT"
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(1000):
        suffix = "".join(secrets.choice(alphabet) for _ in range(max(4, int(length or 6))))
        code = f"{clean_prefix}-{suffix}"
        if not userbot_db.get_zarin_voucher(code):
            return code
    return f"{clean_prefix}-{secrets.token_hex(5).upper()}"


def _parse_receipt_meta(raw: str) -> Dict[str, str]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    if "|" not in raw and ":" not in raw:
        return {"admin_fid": raw}
    data: Dict[str, str] = {}
    for part in raw.split("|"):
        part = part.strip()
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip()
        v = v.strip()
        if k and v:
            data[k] = v
    return data


def _payment_status_title(status: str) -> str:
    s = (status or "").strip().lower()
    if s == "approved":
        return _adm_t("ub_status_approved")
    if s == "rejected":
        return _adm_t("ub_status_rejected")
    return _adm_t("ub_status_pending")


def _build_payment_detail_text(pay: Dict[str, Any]) -> str:
    tx_code = str(pay.get("tx_code") or pay.get("id") or "-")
    username_raw = (pay.get("username") or "").strip()
    username = f"@{username_raw}" if username_raw else "-"
    full_name = (pay.get("full_name") or "-").strip()
    tg_id = str(pay.get("telegram_id") or "-")
    created_at = str(pay.get("created_at") or "-")
    amount = _format_toman(pay.get("amount") or 0)
    method = str(pay.get("method") or "card")
    status_title = _payment_status_title(str(pay.get("status") or "pending"))
    receipt_meta = _parse_receipt_meta(str(pay.get("receipt_image") or ""))
    payer_last4 = str(receipt_meta.get("payer_last4") or "").strip()
    payer_line = _adm_t("ub_payment_payer_last4", value=payer_last4) if payer_last4 else ""
    return (
        _adm_t("ub_payment_detail", tx_code=tx_code, full_name=full_name,
               username=username, tg_id=tg_id, created_at=created_at,
               amount=amount, status=status_title, method=method)
        + payer_line
    )


def _build_payment_approved_report_text(pay: Dict[str, Any]) -> str:
    tx_code = str(pay.get("tx_code") or pay.get("id") or "-")
    amount = _format_toman(pay.get("amount") or 0)
    method = str(pay.get("method") or "card")
    receipt_meta = _parse_receipt_meta(str(pay.get("receipt_image") or ""))
    pay_flow = str(receipt_meta.get("pay_flow") or "").strip().lower()
    is_direct_buy = pay_flow == "direct_buy"
    if method == "card" and is_direct_buy:
        method_title = _adm_t("ub_method_direct_card")
    else:
        method_title = _adm_t("ub_method_card") if method == "card" else method
    text = (
        _adm_t("ub_payment_approved_report", method=method_title,
               tx_code=tx_code, amount=amount)
    )
    # گزارش شارژ کیف پول: موجودی جدید کاربر بعد از تایید نمایش داده می‌شود
    if pay_flow == "wallet_topup":
        balance = 0
        try:
            u = None
            tg_id = int(pay.get("telegram_id") or 0)
            if tg_id > 0:
                u = userbot_db.get_user_by_telegram_id(tg_id)
            if not u:
                u = userbot_db.get_user_by_id(int(pay.get("user_id") or 0))
            balance = int((u or {}).get("wallet_balance") or 0)
        except Exception:
            balance = 0
        text += (
            _adm_t("ub_wallet_topped_up", balance=_format_toman(balance))
        )
    return text


def _ticket_status_title(status: str) -> str:
    s = str(status or "").strip().lower()
    if s == "open":
        return _adm_t("ub_ticket_open")
    if s == "closed":
        return _adm_t("ub_ticket_closed")
    return _adm_t("ub_ticket_pending")


def _ticket_user_label(ticket: Dict[str, Any]) -> str:
    full_name = str(ticket.get("full_name") or ticket.get("db_full_name") or "").strip()
    username = str(ticket.get("username") or ticket.get("db_username") or "").strip().lstrip("@")
    tg_id = str(ticket.get("telegram_id") or ticket.get("db_telegram_id") or "").strip()
    if full_name:
        return full_name
    if username:
        return username
    return tg_id or _adm_t("ub_user")


def _build_tickets_stats_text(stats: Dict[str, Any]) -> str:
    total = int(stats.get("total_count") or 0)
    pending = int(stats.get("pending_count") or 0)
    opened = int(stats.get("open_count") or 0)
    closed = int(stats.get("closed_count") or 0)
    feedback_total = int(stats.get("feedback_total") or 0)
    feedback_pos = int(stats.get("feedback_positive") or 0)
    feedback_neg = int(stats.get("feedback_negative") or 0)
    return (
        f"{_adm_t('ub_lit_95446193b2f4')}{total}{_adm_t('ub_lit_e9cbd2d2737c')}{opened}{_adm_t('ub_lit_464fef4b6241')}{closed}{_adm_t('ub_lit_d19d7a6fbd42')}{pending}{_adm_t('ub_lit_27351dc51444')}{feedback_total}{_adm_t('ub_lit_7b9ca13b8c44')}{feedback_pos}{_adm_t('ub_lit_fb8cdcb93919')}{feedback_neg}\n❖⬩--------------------------------⬩❖"
    )


def _broadcast_segment_label(segment: str) -> str:
    seg = str(segment or "").strip().lower()
    mapping = {
        "all": _adm_t('ub_lit_4e2322181a3c'),
        "expired_all": _adm_t('ub_lit_f0d81758cac5'),
        "no_order": _adm_t('ub_lit_c7d991fd8b86'),
        "expired_1w": _adm_t('ub_lit_a2e8cf10692c'),
        "expired_2w": _adm_t('ub_lit_e9f9a574e26e'),
        "expired_4w": _adm_t('ub_lit_817af6577e85'),
        "expired_8w": _adm_t('ub_lit_f33f3f99dccc'),
    }
    return mapping.get(seg, _adm_t('ub_lit_4e2322181a3c'))


def _build_broadcast_stats_text(stats: Dict[str, Any]) -> str:
    return (
        f"{_adm_t('ub_lit_b3d620fb2274')}{int(stats.get('total_users') or 0)}{_adm_t('ub_lit_1c7fb94307f9')}{int(stats.get('expired_users') or 0)}{_adm_t('ub_lit_83024d8eef9c')}{int(stats.get('no_order_users') or 0)}{_adm_t('ub_lit_baf77f76340e')}{int(stats.get('expired_1w_users') or 0)}{_adm_t('ub_lit_502540af517b')}{int(stats.get('expired_2w_users') or 0)}{_adm_t('ub_lit_f0268f20ccc5')}{int(stats.get('expired_4w_users') or 0)}{_adm_t('ub_lit_2b607a6b23bd')}{int(stats.get('expired_8w_users') or 0)}"
    )


def _normalize_admin_action_text(text: str) -> str:
    t = str(text or "").strip()
    for ch in ("\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u2066", "\u2067", "\u2068", "\u2069"):
        t = t.replace(ch, "")
    return " ".join(t.split())


def _is_ticket_reply_skip_text(text: str) -> bool:
    t = _normalize_admin_action_text(text).replace(" ", "")
    return t in {
        "ردکردن",
        "⏩ردکردن",
        "▶️ردکردن",
        "⏭️ردکردن",
    }


def _build_ticket_reply_preview_text(reply_text: str, has_photo: bool) -> str:
    body = str(reply_text or "").strip() or "-"
    shot_line = _adm_t('ub_lit_2601a58a4d58') if has_photo else _adm_t('ub_lit_42a1d894cd7d')
    return (
        f"{_adm_t('ub_lit_bc641fde7a5f')}{body}\n\n{shot_line}{_adm_t('ub_lit_a044227ca392')}"
    )


def _build_ticket_detail_text(
    ticket: Dict[str, Any],
    messages: List[Dict[str, Any]],
    screenshot_links: Optional[Dict[int, str]] = None,
) -> str:
    code = str(ticket.get("ticket_code") or "-")
    status = _ticket_status_title(str(ticket.get("status") or "pending"))
    created = str(ticket.get("created_at") or "-")
    admin_name = str(ticket.get("admin_name") or "").strip() or _adm_t('ub_lit_b5f50a9abc69')
    user_label = _ticket_user_label(ticket)
    username = str(ticket.get("username") or ticket.get("db_username") or "").strip().lstrip("@")
    tg_id = str(ticket.get("telegram_id") or ticket.get("db_telegram_id") or "-")

    lines = [
        f"{_adm_t('ub_lit_02f28a6324ce')}{html_escape(code)}",
        f"{_adm_t('ub_lit_0bfca2015957')}{html_escape(created)}",
        f"{_adm_t('ub_lit_821388487a16')}{html_escape(status)}",
        f"{_adm_t('ub_lit_9a3903dac8b0')}{html_escape(user_label)}",
        f"{_adm_t('ub_lit_0b59261c72b1')}{html_escape(username or '-')}",
        f"{_adm_t('ub_lit_6135467417d5')}{html_escape(tg_id)}",
        f"{_adm_t('ub_lit_29e351d99dc9')}{html_escape(admin_name)}",
        "❖⬩--------------------------------⬩❖",
    ]

    for idx, m in enumerate(messages, start=1):
        sender_type = str(m.get("sender_type") or "").strip().lower()
        sender_name = str(m.get("sender_name") or "").strip() or (_adm_t('ub_lit_883da9f030ce') if sender_type == "user" else _adm_t('ub_lit_65497ce4192c'))
        created_at = str(m.get("created_at") or "-")
        text = str(m.get("message_text") or "").strip()
        photo = str(m.get("photo_file_id") or "").strip()
        lines.append(f"{_adm_t('ub_lit_0bfca2015957')}{html_escape(created_at)} | #{idx}")
        lines.append(f"◈ {_adm_t('ub_lit_139606472968') if sender_type == 'user' else _adm_t('ub_lit_c121b28dc81d')}:")
        lines.append(html_escape(sender_name))
        if text:
            lines.append(html_escape(text))
        if photo:
            shot_link = str((screenshot_links or {}).get(idx) or "").strip()
            if shot_link:
                lines.append(f"🖼 <a href=\"{html_escape(shot_link, quote=True)}{_adm_t('ub_lit_d791a48e6b47')}{idx}</a>")
            else:
                lines.append(f"{_adm_t('ub_lit_86922f1334f7')}{idx}")
        lines.append("❖⬩------------------------------⬩❖")
    return "\n".join(lines)


def _collect_ticket_photo_refs(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    for idx, m in enumerate(messages, start=1):
        fid = str(m.get("photo_file_id") or "").strip()
        if not fid:
            continue
        refs.append(
            {
                "idx": idx,
                "message_id": int(m.get("id") or 0),
            }
        )
    return refs


def _build_ticket_shot_payload(ticket_code: int, message_id: int) -> str:
    return f"{TICKET_SHOT_START_PREFIX}_{int(ticket_code)}_{int(message_id)}"


def _parse_ticket_shot_payload(payload: str) -> Tuple[int, int]:
    raw = str(payload or "").strip()
    m = re.match(rf"^{re.escape(TICKET_SHOT_START_PREFIX)}_(\d+)_(\d+)$", raw)
    if not m:
        return 0, 0
    try:
        code = int(m.group(1))
        msg_id = int(m.group(2))
    except Exception:
        return 0, 0
    if code <= 0 or msg_id <= 0:
        return 0, 0
    return code, msg_id


async def _get_admin_bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    cached = str(context.bot_data.get("_admin_bot_username") or "").strip().lstrip("@")
    if cached:
        return cached
    try:
        me = await context.bot.get_me()
        username = str(getattr(me, "username", "") or "").strip().lstrip("@")
        if username:
            context.bot_data["_admin_bot_username"] = username
        return username
    except Exception:
        return ""


def _build_telegram_start_link(username: str, payload: str) -> str:
    bot_username = str(username or "").strip().lstrip("@")
    clean_payload = str(payload or "").strip()
    if not bot_username or not clean_payload:
        return ""
    return f"https://t.me/{bot_username}?start={clean_payload}"


def _make_qr_image(data: str, *, filename: str = "gift-deeplink-qr.png") -> BytesIO:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(str(data or ""))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    bio.name = filename
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


def _build_gift_campaign_copy(code: str, deep_link: str, amount_toman: int, *, title: str = "") -> str:
    campaign_title = str(title or _adm_t('ub_lit_af51f0048ada')).strip()
    link_line = str(deep_link or "").strip() or f"{_adm_t('ub_lit_5f9b2588a592')}{code}"
    return (
        f"🎁 {campaign_title}{_adm_t('ub_lit_574a9428f931')}{code}{_adm_t('ub_lit_d540a419275e')}{_format_toman(amount_toman)}{_adm_t('ub_lit_ee2315460a4b')}{link_line}{_adm_t('ub_lit_e5b13ceec4c3')}"
    )


async def _get_user_bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    cached = str(context.bot_data.get("_user_bot_username") or "").strip().lstrip("@")
    if cached:
        return cached

    env_name = str(os.getenv("SUB_BOT_USERNAME", "") or "").strip().lstrip("@")
    if env_name:
        context.bot_data["_user_bot_username"] = env_name
        return env_name

    token = str(os.getenv("USER_BOT_TOKEN", "") or USER_BOT_TOKEN or "").strip()
    if not token:
        return ""

    try:
        user_bot = Bot(token=token)
        me = await user_bot.get_me()
        username = str(getattr(me, "username", "") or "").strip().lstrip("@")
    except Exception as e:
        logger.warning("Failed resolving user bot username from USER_BOT_TOKEN: %s", e)
        return ""

    if username:
        context.bot_data["_user_bot_username"] = username
        try:
            _write_env_values({"SUB_BOT_USERNAME": username})
        except Exception as e:
            logger.warning("Failed caching SUB_BOT_USERNAME in .env: %s", e)
    return username


async def _build_ticket_screenshot_links(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_code: int,
    photo_refs: List[Dict[str, Any]],
) -> Dict[int, str]:
    username = await _get_admin_bot_username(context)
    if not username:
        return {}
    links: Dict[int, str] = {}
    for ref in photo_refs:
        idx = int(ref.get("idx") or 0)
        msg_id = int(ref.get("message_id") or 0)
        if idx <= 0 or msg_id <= 0:
            continue
        payload = _build_ticket_shot_payload(ticket_code, msg_id)
        links[idx] = f"https://t.me/{username}?start={payload}"
    return links


async def handle_ticket_screenshot_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    payload: str,
) -> bool:
    message = update.effective_message
    if not message:
        return False

    code, msg_id = _parse_ticket_shot_payload(payload)
    if code <= 0 or msg_id <= 0:
        return False

    ticket = userbot_db.get_ticket_by_code(code)
    if not ticket:
        await message.reply_text(_adm_t("ub_ticket_not_found"))
        return True

    messages = userbot_db.get_ticket_messages(code)
    photo_ref: Optional[Dict[str, Any]] = None
    for idx, item in enumerate(messages, start=1):
        if int(item.get("id") or 0) != msg_id:
            continue
        fid = str(item.get("photo_file_id") or "").strip()
        if not fid:
            break
        photo_ref = {
            "idx": idx,
            "photo_file_id": fid,
            "created_at": str(item.get("created_at") or "-"),
        }
        break

    if not photo_ref:
        await message.reply_text(_adm_t("ub_screenshot_not_found"))
        return True

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_adm_t("ub_back_to_ticket"), callback_data=f"userbot:ticket:detail:{code}:pending:1")]
    ])
    caption = (
        f"{_adm_t('ub_lit_096c2487df9b')}{code}{_adm_t('ub_lit_2740fc3ccb0e')}{int(photo_ref['idx'])}{_adm_t('ub_lit_7bf0af3d7455')}{photo_ref['created_at']}"
    )
    sent = await _send_ticket_photo_with_fallback(
        context=context,
        chat_id=message.chat_id,
        photo_id=str(photo_ref["photo_file_id"]),
        caption=caption,
        reply_markup=kb,
    )
    if not sent:
        await message.reply_text(_adm_t("ub_photo_send_failed"))
    return True


async def _send_ticket_photo_with_fallback(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    photo_id: str,
    caption: str = "",
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> bool:
    sent_id = await _send_ticket_photo_message_with_fallback(
        context=context,
        chat_id=chat_id,
        photo_id=photo_id,
        caption=caption,
        reply_markup=reply_markup,
    )
    return sent_id is not None


async def _send_ticket_photo_message_with_fallback(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    photo_id: str,
    caption: str = "",
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Optional[int]:
    fid = str(photo_id or "").strip()
    if not fid:
        return None
    try:
        sent = await context.bot.send_photo(
            chat_id=chat_id,
            photo=fid,
            caption=caption,
            reply_markup=reply_markup,
        )
        return int(sent.message_id)
    except Exception:
        pass

    if not USER_BOT_TOKEN:
        return None
    try:
        user_bot = Bot(token=USER_BOT_TOKEN)
        file_obj = await user_bot.get_file(fid)
        raw = await file_obj.download_as_bytearray()
        bio = BytesIO(raw)
        bio.name = "ticket_screenshot.jpg"
        bio.seek(0)
        sent = await context.bot.send_photo(
            chat_id=chat_id,
            photo=bio,
            caption=caption,
            reply_markup=reply_markup,
        )
        return int(sent.message_id)
    except Exception:
        return None


async def _send_auto_gift_message_if_needed(pay: Dict[str, Any]) -> None:
    # هدیه اتوماتیک ممکن است اعمال شود، اما پیام نوتیفیکیشن آن
    # طبق درخواست محصول به کاربر ارسال نمی‌شود.
    return


async def _send_payment_event_channel_report_if_enabled(pay: Dict[str, Any]) -> None:
    if not USER_BOT_TOKEN or not isinstance(pay, dict):
        return
    try:
        settings = userbot_db.get_payment_settings()
    except Exception:
        return
    if not bool(settings.get("event_channel_enabled", False)):
        return
    target = str(settings.get("event_channel_id") or "").strip()
    if not target:
        return
    try:
        chat_target: Any = int(target) if target.lstrip("-").isdigit() else target
        user_bot = Bot(token=USER_BOT_TOKEN)
        full_name = str(pay.get("full_name") or "-").strip()
        username_raw = str(pay.get("username") or "").strip()
        username = f"@{username_raw}" if username_raw else "-"
        tg_id = str(pay.get("telegram_id") or "-")
        amount = _format_toman(pay.get("amount") or 0)
        tx_code = str(pay.get("tx_code") or pay.get("id") or "-")
        text = (
            f"{_adm_t('ub_lit_af08ec76e495')}{full_name}{_adm_t('ub_lit_7b764c0838e5')}{username}{_adm_t('ub_lit_08c2e487ac8a')}{tg_id}{_adm_t('ub_lit_56260a9789c9')}{tx_code}{_adm_t('ub_lit_0835446733bd')}{amount}{_adm_t('ub_lit_5d262a4780cd')}"
        )
        await user_bot.send_message(chat_id=chat_target, text=text)
    except Exception as e:
        logger.warning("Failed sending payment event-channel report: %s", e)


def _build_payment_action_keyboard(payment_id: int, user_btn_title: str, uid: int, status: str = "") -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton(_adm_t("ub_change_payment_status"), callback_data=f"userbot:pay:chg:{payment_id}")])
    rows.append([InlineKeyboardButton(f"👤 {user_btn_title}", callback_data=f"userbot:user:{uid}")])
    return InlineKeyboardMarkup(rows)


def _build_payment_change_confirm_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_adm_t("ag_yes"), callback_data=f"userbot:pay:chg:yes:{payment_id}")],
        [InlineKeyboardButton(_adm_t("ag_no"), callback_data=f"userbot:pay:chg:no:{payment_id}")],
    ])


def _build_payment_change_options_keyboard(payment_id: int, current_status: str) -> InlineKeyboardMarkup:
    status = (current_status or "").strip().lower()
    rows = []
    if status != "approved":
        rows.append([InlineKeyboardButton(_adm_t("ub_status_approved"), callback_data=f"userbot:pay:set:{payment_id}:approved")])
    if status != "rejected":
        rows.append([InlineKeyboardButton(_adm_t("ub_status_rejected"), callback_data=f"userbot:pay:set:{payment_id}:rejected")])
    if status != "pending":
        rows.append([InlineKeyboardButton(_adm_t("ub_status_pending"), callback_data=f"userbot:pay:set:{payment_id}:pending")])
    rows.append([InlineKeyboardButton(_adm_t("back"), callback_data=f"userbot:pay:detail:{payment_id}")])
    return InlineKeyboardMarkup(rows)


def _decode_renew_snapshot(raw: str) -> Dict[str, Any]:
    try:
        decoded = base64.b64decode((raw or "").strip(), validate=False)
        data = json.loads(decoded.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def _notify_user_payment_status_change(pay: Dict[str, Any], new_status: str) -> None:
    """اطلاع‌رسانی تغییر وضعیت تراکنش به کاربر داخل ربات کاربران."""
    try:
        tg_id = pay.get("telegram_id")
        if not (tg_id and USER_BOT_TOKEN):
            return
        user_bot = Bot(token=USER_BOT_TOKEN)
        tx_code = str(pay.get("tx_code") or pay.get("id") or "-")
        _ulg = _user_lang_of(tg_id)
        if new_status == "rejected":
            text = _i18n_user_t(_ulg, "pay_status_rejected_support", code=tx_code)
        elif new_status == "pending":
            text = _i18n_user_t(_ulg, "pay_status_back_pending", code=tx_code)
        else:
            return
        await user_bot.send_message(chat_id=int(tg_id), text=text)
    except Exception as e:
        logger.warning("Failed to notify user about payment %s status change: %s", pay.get("id"), e)


async def _revert_approved_payment(pay: Dict[str, Any]) -> Tuple[bool, str, List[str]]:
    """
    بازگشت اثر تراکنش «خرید مستقیم» تاییدشده قبل از تغییر وضعیت:
    - تمدید: برگرداندن حجم/زمان به حالت قبل از تمدید (اسنپ‌شات)
    - خرید جدید: حذف اشتراک از سرور اصلی + نودها + دیتابیس
    خروجی: (ok, message, failed_servers)
    """
    meta = _parse_receipt_meta(str(pay.get("receipt_image") or ""))
    if str(meta.get("pay_flow") or "").strip().lower() != "direct_buy":
        return True, "", []

    from UserBot import delivery as delivery_ops

    pid = int(pay.get("id") or 0)
    delivered_service_id = int(float(str(meta.get("delivered_service_id") or 0) or 0))
    renew_service_id = int(float(str(meta.get("renew_service_id") or 0) or 0))
    snapshot = _decode_renew_snapshot(str(meta.get("renew_snapshot") or ""))
    failed: List[str] = []

    if renew_service_id > 0:
        if not snapshot:
            # اشتراک تمدیدی بدون اسنپ‌شات قابل حذف نیست؛ طبق قانون فقط وضعیت بازگشت می‌خورد.
            userbot_db._patch_payment_receipt_meta(pid, {"direct_done": "reverted"})
            return True, _adm_t('ub_lit_f752e9fc6dde'), failed
        service = userbot_db.get_service_by_id(renew_service_id)
        if not service:
            userbot_db._patch_payment_receipt_meta(pid, {"direct_done": "reverted", "renew_snapshot": None})
            return True, "", []
        usage_limit = float(snapshot.get("usage_limit") or 0.0)
        usage_current = float(snapshot.get("usage_current") or 0.0)
        days_left = int(snapshot.get("days_left") or 0)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        payload = {
            "usage_limit_GB": usage_limit,
            "package_days": days_left,
            "start_date": now.strftime("%Y-%m-%d"),
            "current_usage_GB": usage_current,
            "last_reset_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "is_active": True,
        }
        changed, failed = await delivery_ops.patch_service_on_panels(service, payload)
        if changed == 0:
            return False, _adm_t('ub_lit_784b7736e218'), failed
        userbot_db.update_service_runtime(
            renew_service_id,
            usage_current=usage_current,
            usage_limit=usage_limit,
            days_left=days_left,
        )
        userbot_db._patch_payment_receipt_meta(pid, {"direct_done": "reverted"})
        return True, _adm_t('ub_lit_317cea6e4027'), failed

    service = userbot_db.get_service_by_id(delivered_service_id) if delivered_service_id > 0 else None
    if not service:
        userbot_db._patch_payment_receipt_meta(
            pid,
            {"direct_done": "reverted", "delivered_service_id": None},
        )
        if delivered_service_id <= 0:
            return True, _adm_t('ub_lit_0f8a02885560'), failed
        return True, "", failed

    deleted, failed = await delivery_ops.delete_service_from_panels(service)
    if deleted == 0:
        return False, _adm_t('ub_lit_565b57318db3'), failed

    userbot_db.delete_service(delivered_service_id)
    try:
        from Shared import agent_db as _agn
        uuid = _extract_service_uuid(service)
        if uuid:
            _agn.soft_delete_service_by_uuid(uuid, int(service.get("server_id") or 0) or None)
    except Exception:
        pass
    userbot_db._patch_payment_receipt_meta(
        pid,
        {"direct_done": "reverted", "delivered_service_id": None},
    )
    return True, _adm_t('ub_lit_e2459030a92c'), failed


async def _notify_user_about_redelivery(pay: Dict[str, Any]) -> None:
    """اطلاع‌رسانی به کاربر درباره تایید مجدد تراکنش ردشده."""
    try:
        tg_id = pay.get("telegram_id")
        if not (tg_id and USER_BOT_TOKEN):
            return
        user_bot = Bot(token=USER_BOT_TOKEN)
        tx_code = str(pay.get("tx_code") or pay.get("id") or "-")
        await user_bot.send_message(
            chat_id=int(tg_id),
            text=_i18n_user_t(_user_lang_of(tg_id), "pay_reapproved_delivery", code=tx_code),
        )
    except Exception as e:
        logger.warning("Failed to notify user about re-approval of payment %s: %s", pay.get("id"), e)


def _reset_direct_delivery_meta(payment_id: int) -> None:
    """
    ریست وضعیت تحویل خرید مستقیم تا بعد از تایید مجدد تراکنش ردشده،
    حلقه تحویل در ربات کاربران، اشتراک را از نو بسازد و تحویل دهد.
    """
    try:
        userbot_db._patch_payment_receipt_meta(
            int(payment_id),
            {
                "direct_done": None,
                "direct_done_at": None,
                "direct_error": None,
                "direct_error_at": None,
                "direct_attempts": None,
                "delivered_service_id": None,
                "redelivered_at": None,
            },
        )
    except Exception as e:
        logger.warning("Failed to reset direct delivery meta for payment %s: %s", payment_id, e)


def _relative_last_online(last_online_raw: Optional[str]) -> str:
    dt = None
    if last_online_raw:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(last_online_raw, fmt)
                break
            except ValueError:
                continue
    
    if not dt:
        return _adm_t('ub_lit_437519ce5bf9')
    
    delta = datetime.now(timezone.utc).replace(tzinfo=None) - dt
    days = delta.days
    seconds = delta.seconds
    if days <= 0:
        if seconds < 60:
            rel = _adm_t('ub_lit_7890a7f0eb06')
        elif seconds < 3600:
            rel = f"{seconds // 60}{_adm_t('ub_lit_44d84b24c863')}"
        else:
            rel = f"{seconds // 3600}{_adm_t('ub_lit_43a26f3e010b')}"
    elif days < 30:
        rel = f"{days}{_adm_t('ub_lit_116b6a8f8f3e')}"
    elif days < 365:
        rel = f"{days // 30}{_adm_t('ub_lit_b6b254bf4dab')}"
    else:
        rel = f"{days // 365}{_adm_t('ub_lit_931b38d2d941')}"
    return f"{_adm_t('ub_lit_b69c033117ef')}{rel}"


def _parse_last_online_dt(last_online_raw: Optional[str]) -> Optional[datetime]:
    if not last_online_raw:
        return None
    last_online_raw = str(last_online_raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(last_online_raw, fmt)
        except ValueError:
            continue
    return None


def _service_last_online_line(last_online_raw: Optional[str]) -> str:
    dt = _parse_last_online_dt(last_online_raw)
    if not dt:
        return _adm_t('ub_lit_437519ce5bf9')
    # اگر اخیراً آنلاین بوده، خروجی ساده و شبیه UI مدنظر نشان بده.
    if (datetime.now(timezone.utc).replace(tzinfo=None) - dt).total_seconds() <= 4 * 3600:
        return _adm_t('ub_lit_0a2bf5861b7a')
    return _relative_last_online(last_online_raw)


def _display_safe_note(note_text: str) -> str:
    raw = str(note_text or "").strip()
    if not raw:
        return "-"
    fa_digits = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    # جلوگیری از لینک‌شدن خودکار شماره بعد از HiddifyBot:
    # مثال: HiddifyBot:123456789 -> HiddifyBot:۱۲۳۴۵۶۷۸۹
    raw = re.sub(
        r"(?i)\b(HiddifyBot:\s*[\u200c\u200d\u200e\u200f]*)([0-9]+)\b",
        lambda m: f"{m.group(1)}{m.group(2).translate(fa_digits)}",
        raw,
    )
    return raw


def _subscription_tracking_prompt_text() -> str:
    return _adm_t('ub_lit_b7b0bdd9bfa3')


def _location_flag_from_title(title: str) -> str:
    t = str(title or "")
    if "ترکیه" in t:
        return "🇹🇷"
    if "هلند" in t:
        return "🇳🇱"
    if "آلمان" in t:
        return "🇩🇪"
    if "فرانسه" in t:
        return "🇫🇷"
    if "امریک" in t or "آمریک" in t:
        return "🇺🇸"
    return "🏳️"


def _format_server_location_title(raw_title: str) -> str:
    title = str(raw_title or "").strip() or _adm_t('ub_lit_264f61d0e11d')
    flag = _location_flag_from_title(title)
    has_location_word = "لوکیشن" in title
    has_flag = flag != "🏳️" and flag in title
    if has_location_word:
        if has_flag or flag == "🏳️":
            return title
        return f"{title} {flag}"
    if flag == "🏳️":
        return f"{_adm_t('ub_lit_648eb893aa7a')}{title}"
    return f"{_adm_t('ub_lit_648eb893aa7a')}{flag} {title}"


def _resolve_live_server_title(service: Dict[str, Any], default: str = "") -> str:
    stored_title = str(service.get("server_title") or "").strip()
    try:
        sid = int(service.get("server_id") or 0)
    except (TypeError, ValueError):
        sid = 0

    if sid > 0:
        try:
            srv = database.get_server_by_id(sid)
        except Exception:
            srv = None
        if srv:
            live_title = str(srv.get("title") or "").strip()
            if live_title:
                return live_title
        if stored_title:
            return stored_title
        return f"{_adm_t('ub_lit_bb3a2e4773a8')}{sid}"

    return stored_title or default or _adm_t('ub_lit_bb3a2e4773a8')


def _resolve_all_server_titles(service: Dict[str, Any], default: str = "") -> str:
    titles = []
    seen_ids = set()
    try:
        sid = int(service.get("server_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    if sid > 0:
        seen_ids.add(sid)
        try:
            srv = database.get_server_by_id(sid)
        except Exception:
            srv = None
        if srv:
            t = str(srv.get("title") or "").strip()
            if t:
                titles.append(t)
        else:
            stored = str(service.get("server_title") or "").strip()
            titles.append(stored or f"{_adm_t('ub_lit_bb3a2e4773a8')}{sid}")
    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0
    if service_id > 0:
        try:
            nodes = userbot_db.get_service_nodes(service_id)
        except Exception:
            nodes = []
        for node in nodes:
            try:
                nid = int(node.get("server_id") or 0)
            except (TypeError, ValueError):
                nid = 0
            if nid <= 0 or nid in seen_ids:
                continue
            seen_ids.add(nid)
            try:
                nsrv = database.get_server_by_id(nid)
            except Exception:
                nsrv = None
            if nsrv:
                t = str(nsrv.get("title") or "").strip()
                if t:
                    titles.append(t)
            else:
                titles.append(f"{_adm_t('ub_lit_bb3a2e4773a8')}{nid}")
    if len(titles) == 1:
        return titles[0]
    if len(titles) > 1:
        return " + ".join(titles)
    return default or _adm_t('ub_lit_bb3a2e4773a8')


def _parse_service_comment_meta(raw_comment: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    raw = str(raw_comment or "").strip()
    if not raw:
        return parsed
    for part in raw.split("|"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        if k and v:
            parsed[k] = v
    return parsed


def build_subscription_tracking_keyboard(service: Dict[str, Any]) -> InlineKeyboardMarkup:
    service_id = int(service.get("id") or 0)
    user_id = int(service.get("user_id") or 0)
    service_name = str(service.get("name") or _adm_t('ub_lit_06097bf287e6')).strip() or _adm_t('ub_lit_06097bf287e6')
    user_btn_title = service_name
    if user_id > 0:
        user_row = userbot_db.get_user_by_id(user_id) or {}
        username = str(user_row.get("username") or "").strip()
        telegram_id = user_row.get("telegram_id")
        if username:
            user_btn_title = username
        elif telegram_id:
            user_btn_title = str(telegram_id)
        else:
            user_btn_title = _display_name(user_row)
    target_server_id, target_user_uuid = _service_primary_target(service)

    if target_server_id > 0 and target_user_uuid:
        cfg_cb = f"server:{target_server_id}:usercfg:{target_user_uuid}"
        edit_cb = f"server:{target_server_id}:useredit:{target_user_uuid}"
        del_cb = f"server:{target_server_id}:userdel:{target_user_uuid}"
    else:
        cfg_cb = f"userbot:svc:{service_id}:configs"
        edit_cb = f"userbot:svc:{service_id}:edit"
        del_cb = f"userbot:svc:{service_id}:delete"

    rows = [
        [InlineKeyboardButton(_adm_t("ub_configs"), callback_data=cfg_cb)],
        [InlineKeyboardButton(_adm_t("ub_edit_user"), callback_data=edit_cb)],
        [InlineKeyboardButton(_adm_t("ub_delete_user"), callback_data=del_cb)],
        [
            InlineKeyboardButton(
                user_btn_title,
                callback_data=(
                    f"userbot:user:{user_id}:from_subs:{service_id}"
                    if user_id > 0 and service_id > 0
                    else (
                        f"userbot:user:{user_id}:from_subs"
                        if user_id > 0
                        else "userbot:subs_menu"
                    )
                ),
            )
        ],
    ]
    return InlineKeyboardMarkup(rows)


def _service_public_note_text(service: Dict[str, Any]) -> str:
    note_override = str(service.get("note_text") or "").strip()
    if note_override:
        return note_override
    comment = str(service.get("comment") or "").strip()
    if not comment:
        return "-"
    meta = _parse_service_comment_meta(comment)
    if meta:
        for key in ("note", "memo", "desc", "comment"):
            value = str(meta.get(key) or "").strip()
            if value:
                return value
        return "-"
    lowered = comment.lower()
    if lowered in {"test", "linked", "connect", "source:connect"}:
        return "-"
    if "uuid:" in lowered or "code:" in lowered or "price:" in lowered:
        return "-"
    return comment


def _is_internal_panel_comment(raw: str) -> bool:
    text = str(raw or "").strip()
    if not text:
        return True
    tokens = [t.strip() for t in text.split("|") if t.strip()]
    if not tokens:
        return True
    for token in tokens:
        low = token.lower()
        if low in {"test", "trial", "linked", "connect", "source:connect", "-"}:
            continue
        return False
    return True


def _panel_comment_public_text(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return "-"
    # این الگو در بعضی پنل‌ها همان "یادداشت واقعی" کسب‌وکار است.
    if re.fullmatch(r"(?i)\s*hiddifybot:\d+\s*\|\s*test\s*", text):
        return text
    if _is_internal_panel_comment(text):
        return "-"

    tokens = [t.strip() for t in text.split("|") if t.strip()]
    if not tokens:
        return "-"

    visible_tokens: List[str] = []
    for token in tokens:
        low = token.lower()
        if re.fullmatch(r"hiddifybot:\d+", low):
            continue
        if low in {"test", "trial", "linked", "connect", "source:connect", "-"}:
            continue

        if ":" in token:
            k, v = token.split(":", 1)
            key = k.strip().lower()
            val = v.strip()
            if key in {"note", "memo", "desc", "comment"} and val:
                visible_tokens.append(val)
                continue
            if key in {"uuid", "code", "price"}:
                continue

        visible_tokens.append(token)

    if not visible_tokens:
        return "-"
    return " | ".join(visible_tokens)


def _extract_note_from_panel_user(user_data: Dict[str, Any]) -> str:
    if not isinstance(user_data, dict):
        return "-"
    for key in ("note", "memo", "desc", "description", "comment"):
        value = str(user_data.get(key) or "").strip()
        if not value:
            continue
        if key == "comment":
            note = _panel_comment_public_text(value)
            if note != "-":
                return note
            continue
        return value
    return "-"


def _extract_service_uuid(service: Dict[str, Any]) -> str:
    direct_uuid = str(service.get("user_uuid") or "").strip()
    if direct_uuid:
        return direct_uuid
    meta = _parse_service_comment_meta(str(service.get("comment") or ""))
    return str(meta.get("uuid") or "").strip()


def _to_int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _is_locally_deleted_service(service: Dict[str, Any]) -> bool:
    status = str(service.get("status") or "").strip().lower()
    return status in {"deleted", "removed"}


def _is_locally_expired_service(service: Dict[str, Any]) -> bool:
    status = str(service.get("status") or "").strip().lower()
    if status in {"inactive", "disabled", "expired"}:
        return True
    days_left = _to_int_or_none(service.get("days_left"))
    return days_left is not None and days_left <= 0


def _is_locally_active_service(service: Dict[str, Any]) -> bool:
    status = str(service.get("status") or "").strip().lower()
    if status in {"deleted", "removed", "inactive", "disabled", "expired"}:
        return False
    days_left = _to_int_or_none(service.get("days_left"))
    if days_left is not None and days_left <= 0:
        return False
    return True


def _is_display_expired_service(service: Dict[str, Any]) -> bool:
    return _is_locally_expired_service(service) or bool(service.get("_panel_expired"))


def _panel_user_is_active(user_data: Dict[str, Any]) -> bool:
    raw = user_data.get("is_active")
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in {"0", "false", "off", "inactive", "disabled"}


def _panel_user_is_expired_or_inactive(user_data: Dict[str, Any]) -> bool:
    if not _panel_user_is_active(user_data):
        return True

    for key in ("days_left", "remaining_days"):
        days_left = _to_int_or_none(user_data.get(key))
        if days_left is not None and days_left <= 0:
            return True

    start_dt = _parse_last_online_dt(user_data.get("start_date"))
    package_days = _to_int_or_none(user_data.get("package_days"))
    if start_dt and package_days is not None:
        end_dt = start_dt + timedelta(days=package_days)
        if (end_dt.date() - datetime.now(timezone.utc).replace(tzinfo=None).date()).days <= 0:
            return True

    try:
        usage_limit = float(user_data.get("usage_limit_GB"))
        usage_current = float(user_data.get("current_usage_GB"))
        if usage_limit > 0 and usage_current >= usage_limit:
            return True
    except Exception:
        pass

    for key in ("expire", "expire_date", "end_date", "expiration_date", "expires_at"):
        end_dt = _parse_last_online_dt(user_data.get(key))
        if end_dt and end_dt.date() <= datetime.now(timezone.utc).replace(tzinfo=None).date():
            return True

    return False


def _service_panel_targets(service: Dict[str, Any]) -> List[Tuple[int, str]]:
    targets: List[Tuple[int, str]] = []
    seen: Set[Tuple[int, str]] = set()

    sid = int(service.get("server_id") or 0)
    uuid = _extract_service_uuid(service)
    if sid > 0 and uuid:
        pair = (sid, uuid)
        seen.add(pair)
        targets.append(pair)

    service_id = int(service.get("id") or 0)
    if service_id > 0:
        for node in (userbot_db.get_service_nodes(service_id) or []):
            node_sid = int(node.get("server_id") or 0)
            node_uuid = str(node.get("panel_user_uuid") or "").strip()
            if node_sid <= 0 or not node_uuid:
                continue
            pair = (node_sid, node_uuid)
            if pair in seen:
                continue
            seen.add(pair)
            targets.append(pair)

    return targets


def _service_primary_target(service: Dict[str, Any]) -> Tuple[int, str]:
    """Best-effort resolve (server_id, panel_user_uuid) for service action buttons."""
    for sid, uuid in _service_panel_targets(service):
        if int(sid or 0) > 0 and str(uuid or "").strip():
            return int(sid), str(uuid).strip()
    sid = int(service.get("server_id") or 0)
    uuid = _extract_service_uuid(service)
    if sid > 0 and uuid:
        return sid, uuid
    return 0, ""


async def _service_exists_on_panel(service: Dict[str, Any]) -> bool:
    state = await _service_panel_state(service)
    return state in {"active", "unknown"}


async def _service_panel_state(service: Dict[str, Any]) -> str:
    targets = _service_panel_targets(service)
    if not targets:
        return "active"

    had_unknown_error = False
    saw_inactive_user = False
    tried = 0

    for sid, uuid in targets:
        server = database.get_server_by_id(sid)
        if not server:
            continue
        tried += 1
        try:
            panel_user = await asyncio.wait_for(
                hiddify_api.get_user_by_uuid(server, uuid),
                timeout=6,
            )
            if isinstance(panel_user, dict):
                if _panel_user_is_expired_or_inactive(panel_user):
                    saw_inactive_user = True
                    continue
                return "active"
            had_unknown_error = True
        except asyncio.TimeoutError:
            had_unknown_error = True
        except hiddify_api.HiddifyApiError as e:
            err = str(e)
            if "HTTP 404" in err or "HTTP 410" in err:
                continue
            had_unknown_error = True
        except Exception:
            had_unknown_error = True

    # در خطاهای شبکه/SSL تصمیم قطعی نگیریم که سرویس حذف شده است.
    if tried == 0 or had_unknown_error:
        return "unknown"
    if saw_inactive_user:
        return "expired"
    return "missing"


def _comment_has_flag(raw_comment: str, flag: str) -> bool:
    want = str(flag or "").strip().lower()
    if not want:
        return False
    parts = [p.strip().lower() for p in str(raw_comment or "").split("|") if p.strip()]
    return want in parts


def _synthetic_hiddify_note(service: Dict[str, Any]) -> str:
    user_id = int(service.get("user_id") or 0)
    if user_id <= 0:
        return "-"
    user = userbot_db.get_user_by_id(user_id) or {}
    telegram_id = int(user.get("telegram_id") or 0)
    if telegram_id <= 0:
        return "-"

    raw_comment = str(service.get("comment") or "")
    base_note = f"HiddifyBot:{telegram_id}"
    if _comment_has_flag(raw_comment, "test"):
        return f"{base_note}|test"
    return base_note


async def _resolve_service_note_text(service: Dict[str, Any]) -> str:
    local_note = _service_public_note_text(service)
    if local_note and local_note != "-":
        return local_note

    server_id = int(service.get("server_id") or 0)
    user_uuid = _extract_service_uuid(service)
    if server_id <= 0 or not user_uuid:
        return "-"

    tried_ids: List[int] = []
    if server_id > 0:
        tried_ids.append(server_id)
    for s in (database.get_servers() or []):
        sid = int(s.get("id") or 0)
        if sid > 0 and sid not in tried_ids:
            tried_ids.append(sid)

    for sid in tried_ids:
        server = database.get_server_by_id(sid)
        if not server:
            continue
        try:
            panel_user = await hiddify_api.get_user_by_uuid(server, user_uuid)
            panel_note = _extract_note_from_panel_user(panel_user or {})
            if panel_note != "-":
                return panel_note
        except Exception as e:
            logger.debug(
                "Failed to resolve panel note for service_id=%s sid=%s uuid=%s: %s",
                service.get("id"),
                sid,
                user_uuid,
                e,
            )

        # fallback از کش محلی users در servers.json
        try:
            for u in (database.get_users(sid) or []):
                u_uuid = str(u.get("uuid") or u.get("id") or "").strip()
                if not u_uuid or u_uuid != user_uuid:
                    continue
                cached_note = _extract_note_from_panel_user(u)
                if cached_note != "-":
                    return cached_note
        except Exception:
            pass

    return _synthetic_hiddify_note(service)


def build_subscription_tracking_detail_text(user: Dict[str, Any], service: Dict[str, Any]) -> str:
    user_name = str(service.get("name") or "").strip() or str(user.get("full_name") or "").strip() or _display_name(user)
    server_title = _format_server_location_title(_resolve_all_server_titles(service, default=_adm_t('ub_lit_9edd05da3834')))

    usage_current = _to_float(service.get("usage_current"))
    usage_limit = _to_float(service.get("usage_limit"))
    days_left = service.get("days_left")
    meta = _parse_service_comment_meta(str(service.get("comment") or ""))

    if usage_current is None and usage_limit is None:
        usage_line = _adm_t('ub_lit_24895ba533ce')
    elif usage_limit is None or usage_limit <= 0:
        usage_line = f"{_adm_t('ub_lit_2e6cd37f40c3')}{usage_current or 0.0:f'.1f'}{_adm_t('ub_lit_cbda8b44061e')}"
    else:
        usage_line = f"{_adm_t('ub_lit_2e6cd37f40c3')}{usage_current or 0.0:f'.1f'}{_adm_t('ub_lit_7138b458fc80')}{usage_limit:f'.1f'}{_adm_t('ub_lit_0f54de48930e')}"

    if days_left is None:
        expire_line = _adm_t('ub_lit_880029da193b')
    elif days_left < 0:
        expire_line = f"{_adm_t('ub_lit_680c6ea64d27')}{abs(int(days_left))}{_adm_t('ub_lit_39606a0be064')}"
    else:
        expire_line = f"{_adm_t('ub_lit_1e68e1a1af1c')}{int(days_left)}{_adm_t('ub_lit_6f274ee56123')}"

    price_line = _adm_t('ub_lit_19bc14069649')
    price_raw = str(meta.get("price") or "").strip()
    if price_raw.isdigit():
        price_line = f"{_adm_t('ub_lit_5fc63892f0e4')}{int(price_raw):f','}{_adm_t('ub_lit_f6ac3483a71a')}"

    lines = [
        f"{_adm_t('ub_lit_a4c85669f841')}{user_name}",
        "❖⬩╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍⬩❖",
        f"{_adm_t('ub_lit_d968434b916d')}{server_title}",
        usage_line,
        expire_line,
        price_line,
        f"{_adm_t('ub_lit_c489019f5c6c')}{_service_public_note_text(service)}",
    ]
    return "\n".join(lines)


def build_service_detail_text(user: Dict[str, Any], service: Dict[str, Any]) -> str:
    """متن جزئیات سرویس (برای منوی لیست سرویس‌ها)."""
    service_name = (service.get("name") or _adm_t('ub_lit_06097bf287e6')).strip()
    server_title = _format_server_location_title(_resolve_all_server_titles(service, default=_adm_t('ub_lit_9edd05da3834')))

    usage_current = _to_float(service.get("usage_current"))
    usage_limit = _to_float(service.get("usage_limit"))
    days_left = service.get("days_left")
    last_online_raw = service.get("last_online")
    comment = _service_public_note_text(service)
    if comment == "-":
        comment = _synthetic_hiddify_note(service)
    comment = _display_safe_note(comment)

    if usage_current is None:
        usage_line = _adm_t('ub_lit_f5a22d55ca6c')
    elif usage_limit is None:
        usage_line = f"{_adm_t('ub_lit_4a4dd5e2a0f6')}{usage_current:f'.2f'}{_adm_t('ub_lit_ab4b45a9ca94')}"
    else:
        usage_line = f"{_adm_t('ub_lit_4a4dd5e2a0f6')}{usage_current:f'.2f'}{_adm_t('ub_lit_7138b458fc80')}{usage_limit:f'.1f'}{_adm_t('ub_lit_7e0dcee1a10c')}"

    is_display_expired = _is_display_expired_service(service)
    if is_display_expired:
        expire_line = _adm_t('ub_lit_cad862180b19')
    elif days_left is None:
        expire_line = _adm_t('ub_lit_b5ec9e10c799')
    elif days_left < 0:
        expire_line = f"{_adm_t('ub_lit_3c55aa353829')}{abs(int(days_left))}{_adm_t('ub_lit_39606a0be064')}"
    else:
        expire_line = f"{_adm_t('ub_lit_def726e210a2')}{int(days_left)}{_adm_t('ub_lit_ca231220876d')}"

    if is_display_expired:
        last_online_line = _adm_t('ub_lit_3c3c3dbbf5b6')
    else:
        last_online_line = _service_last_online_line(last_online_raw)

    header_line = f"{_adm_t('ub_lit_a4c85669f841')}{service_name}"
    sep_line = "❖⬩╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍⬩❖"
    server_line = f"{_adm_t('ub_lit_332833b95ef3')}{server_title}"

    # بخش نودها: نمایش وضعیت یخ‌زده / حذف‌شده به‌تفکیک هر نود
    node_lines: List[str] = []
    frozen_count = 0
    deleted_count = 0
    try:
        _svc_id = service.get("id")
        _nodes = userbot_db.get_service_nodes(_svc_id) if _svc_id is not None else None
    except Exception:
        _nodes = None
    if _nodes:
        for _nd in _nodes:
            _sid = _nd.get("server_id")
            _srv = None
            try:
                if _sid is not None:
                    _srv = database.get_server_by_id(int(_sid))
            except Exception:
                _srv = None
            # fallback به server_title ذخیره‌شده در خود نود اگر سرور از servers.json حذف شده باشد
            _stored_title = str(_nd.get("server_title") or "").strip()
            if _srv and (_srv.get("name") or _srv.get("title")):
                _title = str(_srv.get("name") or _srv.get("title"))
            elif _stored_title:
                _title = _stored_title
            else:
                _title = f"{_adm_t('ub_lit_bb3a2e4773a8')}{_sid}" if _sid is not None else _adm_t('ub_lit_1789f5ad69dc')
            _is_deleted = bool(_nd.get("deleted"))
            _is_frozen = bool(_nd.get("frozen"))
            if _is_deleted:
                deleted_count += 1
                _status = _adm_t('ub_lit_7b7fadfb0acc')
            elif _is_frozen:
                frozen_count += 1
                _status = _adm_t('ub_lit_0290035a8413')
            else:
                _status = _adm_t('ub_lit_f1bc469f39f7')
            _nu = _to_float(_nd.get("usage_current"))
            _nu_s = f"{_nu:.2f}GB" if _nu is not None else "—"
            node_lines.append(f"  • {_title}: {_status} ({_nu_s})")

    lines = [
        header_line,
        sep_line,
        server_line,
        usage_line,
        expire_line,
        last_online_line,
        f"{_adm_t('ub_lit_c489019f5c6c')}{comment if str(comment).strip() else '—'}",
    ]
    if node_lines:
        lines.append(_adm_t('ub_lit_26e87b968446'))
        lines.extend(node_lines)
    if frozen_count or deleted_count:
        lines.append(
            f"⚠️ {frozen_count}{_adm_t('ub_lit_c883563059ef')}{deleted_count}{_adm_t('ub_lit_361e63d4664f')}"
        )
    return "\n".join(lines)


def userbot_cancel_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد لغو برای ویزاردها"""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(_adm_t("btn_cancel_inline"))]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ===============================
#   Keyboards (منوها)
# ===============================

def build_userbot_main_menu() -> InlineKeyboardMarkup:
    """منوی اصلی مدیریت ربات کاربران"""
    try:
        from Shared import userbot_db as _udb
        _lg = _udb.get_admin_language()
    except Exception:
        _lg = "fa"
    from Shared import i18n as _i18n
    _t = lambda k: _i18n.t(k, _lg)
    rows = [
        [
            InlineKeyboardButton(_t("adm_ub_users"), callback_data="userbot:users_menu")
        ],
        [
            InlineKeyboardButton(_t("adm_ub_tx"), callback_data="userbot:payments_menu"),
            InlineKeyboardButton(_t("adm_ub_orders"), callback_data="userbot:orders_menu"),
        ],
        [
            InlineKeyboardButton(_t("adm_ub_gifts"), callback_data="userbot:gifts_menu")
        ],
        [
            InlineKeyboardButton(_t("adm_ub_referral"), callback_data="userbot:referral_menu")
        ],
        [
            InlineKeyboardButton(_t("adm_ub_tickets"), callback_data="userbot:tickets_menu"),
            InlineKeyboardButton(_t("adm_ub_broadcast"), callback_data="userbot:broadcast_menu"),
        ],
        [
            InlineKeyboardButton(_t("adm_ub_settings"), callback_data="userbot:settings_menu")
        ],
    ]
    return InlineKeyboardMarkup(rows)


def build_payments_menu_keyboard() -> InlineKeyboardMarkup:
    """منوی مدیریت تراکنشات (طبق عکس)"""
    rows = [
        [InlineKeyboardButton(_adm_t("ub_payments_approved"), callback_data="userbot:payments:list:approved")],
        [InlineKeyboardButton(_adm_t("ub_payments_rejected"), callback_data="userbot:payments:list:rejected")],
        [InlineKeyboardButton(_adm_t("ub_payments_pending"), callback_data="userbot:payments:list:pending")],
        [InlineKeyboardButton(_adm_t("ub_payments_card"), callback_data="userbot:payments:list:card")],
        [InlineKeyboardButton(_adm_t("ub_search_payment"), callback_data="userbot:payments:search")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_users_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_adm_t("ub_user_list"), callback_data="userbot:users:1")],
        [InlineKeyboardButton(_adm_t("ub_search_users"), callback_data="userbot:users_search_menu")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_users_search_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_adm_t("ub_search_by_name"), callback_data="userbot:search:name")],
        [InlineKeyboardButton(_adm_t("ub_search_by_id"), callback_data="userbot:search:id")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:users_menu")],
    ]
    return InlineKeyboardMarkup(rows)


# ... (Keyboards قبلی) ...
def build_user_profile_keyboard(user_id: int, back_callback: str = "userbot:users_menu") -> InlineKeyboardMarkup:
    """کیبورد پروفایل"""
    rows = [
        [InlineKeyboardButton(_adm_t("ub_profile_services"), callback_data=f"userbot:user:{user_id}:services")],
        [InlineKeyboardButton(_adm_t("ub_profile_orders"), callback_data=f"userbot:user:{user_id}:orders")],
        [InlineKeyboardButton(_adm_t("ub_profile_payments"), callback_data=f"userbot:user:{user_id}:payments")],
        [InlineKeyboardButton(_adm_t("ub_profile_wallet"), callback_data=f"userbot:user:{user_id}:wallet")],
        [InlineKeyboardButton(_adm_t("ub_profile_reset_trial"), callback_data=f"userbot:user:{user_id}:reset_trial")],
        [InlineKeyboardButton(_adm_t("ub_profile_ban"), callback_data=f"userbot:user:{user_id}:ban")],
        [InlineKeyboardButton(_adm_t("ub_profile_message"), callback_data=f"userbot:user:{user_id}:message"),
         InlineKeyboardButton(_adm_t("ub_profile_tickets"), callback_data=f"userbot:user:{user_id}:tickets")],
        [InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data=back_callback)],
    ]
    return InlineKeyboardMarkup(rows)

def build_service_detail_keyboard(user_id: int, service_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_adm_t("ub_service_configs"), callback_data=f"userbot:svc:{service_id}:configs")],
        [InlineKeyboardButton(_adm_t("ub_service_edit"), callback_data=f"userbot:svc:{service_id}:edit")],
        [InlineKeyboardButton(_adm_t("ub_service_extend"), callback_data=f"userbot:svc:{service_id}:extend")],
        [InlineKeyboardButton(_adm_t("ub_service_delete"), callback_data=f"userbot:svc:{service_id}:delete")],
        [InlineKeyboardButton(_adm_t("ub_service_back"), callback_data=f"userbot:user:{user_id}:services")],
        [InlineKeyboardButton(_adm_t("ub_profile_back"), callback_data=f"userbot:user:{user_id}")],
    ]
    return InlineKeyboardMarkup(rows)

def build_orders_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
         [InlineKeyboardButton(_adm_t("ub_orders_list"), callback_data="userbot:orders:list:1")],
         [InlineKeyboardButton(_adm_t("ub_search_orders"), callback_data="userbot:orders:search")],
         [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:menu")],
    ])


def build_gifts_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_adm_t("ub_gifts_dashboard"), callback_data="userbot:gifts:dashboard")],
        [InlineKeyboardButton(_adm_t("ub_gifts_coupons"), callback_data="userbot:gifts:coupons")],
        [
            InlineKeyboardButton(_adm_t("ub_gifts_presets"), callback_data="userbot:gifts:presets"),
            InlineKeyboardButton(_adm_t("ub_gifts_bulk"), callback_data="userbot:gifts:bulk"),
        ],
        [InlineKeyboardButton(_adm_t("ub_gifts_redemptions"), callback_data="userbot:gifts:redemptions")],
        [
            InlineKeyboardButton(_adm_t("ub_gifts_campaign"), callback_data="userbot:gifts:campaign"),
            InlineKeyboardButton(_adm_t("ub_gifts_security"), callback_data="userbot:gifts:security"),
        ],
        [InlineKeyboardButton(_adm_t("ub_gifts_help"), callback_data="userbot:gifts:help")],
        [InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_gift_presets_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for key, preset in GIFT_CAMPAIGN_PRESETS.items():
        rows.append([
            InlineKeyboardButton(
                f"{preset['title']} | {_format_toman(preset['amount'])}{_adm_t('ub_lit_f6ac3483a71a')}",
                callback_data=f"userbot:gifts:preset:{key}",
            )
        ])
    rows.append([InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:gifts_menu")])
    return InlineKeyboardMarkup(rows)


def build_tickets_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_adm_t("ub_tickets_pending"), callback_data="userbot:tickets:list:pending:1")],
        [InlineKeyboardButton(_adm_t("ub_tickets_open"), callback_data="userbot:tickets:list:open:1")],
        [InlineKeyboardButton(_adm_t("ub_tickets_closed"), callback_data="userbot:tickets:list:closed:1")],
        [InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_broadcast_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_adm_t("ub_segment_all"), callback_data="userbot:broadcast:segment:all")],
        [InlineKeyboardButton(_adm_t("ub_segment_expired_all"), callback_data="userbot:broadcast:segment:expired_all")],
        [InlineKeyboardButton(_adm_t("ub_segment_no_order"), callback_data="userbot:broadcast:segment:no_order")],
        [InlineKeyboardButton(_adm_t("ub_segment_expired_1w"), callback_data="userbot:broadcast:segment:expired_1w")],
        [InlineKeyboardButton(_adm_t("ub_segment_expired_2w"), callback_data="userbot:broadcast:segment:expired_2w")],
        [InlineKeyboardButton(_adm_t("ub_segment_expired_4w"), callback_data="userbot:broadcast:segment:expired_4w")],
        [InlineKeyboardButton(_adm_t("ub_segment_expired_8w"), callback_data="userbot:broadcast:segment:expired_8w")],
        [InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def broadcast_skip_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(_adm_t("ub_skip"))],
            [KeyboardButton(_adm_t("btn_cancel_inline"))],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def build_tickets_list_keyboard(
    tickets: List[Dict[str, Any]],
    *,
    status: str,
    page: int,
    total_pages: int,
    from_user_id: int = 0,
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    current: List[InlineKeyboardButton] = []
    for item in tickets:
        code = str(item.get("ticket_code") or "").strip()
        if not code:
            continue
        if from_user_id > 0:
            cb = f"userbot:ticketu:detail:{code}:{from_user_id}:{page}"
        else:
            cb = f"userbot:ticket:detail:{code}:{status}:{page}"
        current.append(InlineKeyboardButton(code, callback_data=cb))
        if len(current) == 3:
            rows.append(current)
            current = []
    if current:
        rows.append(current)

    nav: List[InlineKeyboardButton] = []
    if page > 1:
        if from_user_id > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"userbot:user:{from_user_id}:tickets:{page-1}"))
        else:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"userbot:tickets:list:{status}:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{max(1, total_pages)}", callback_data="userbot:noop"))
    if page < total_pages:
        if from_user_id > 0:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"userbot:user:{from_user_id}:tickets:{page+1}"))
        else:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"userbot:tickets:list:{status}:{page+1}"))
    rows.append(nav)
    if from_user_id > 0:
        rows.append([InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data=f"userbot:user:{from_user_id}")])
    else:
        rows.append([InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:tickets_menu")])
    return InlineKeyboardMarkup(rows)


def build_ticket_detail_keyboard(
    ticket: Dict[str, Any],
    *,
    list_status: str = "pending",
    page: int = 1,
    from_user_id: int = 0,
) -> InlineKeyboardMarkup:
    user_id = int(ticket.get("user_id") or 0)
    code = int(ticket.get("ticket_code") or 0)
    user_btn = _ticket_user_label(ticket)
    if from_user_id > 0:
        reply_callback = f"userbot:ticketu:reply:{code}:{from_user_id}:{page}"
        status_callback_base = f"userbot:ticketu:status:{code}"
    else:
        list_status = str(list_status or "pending").strip().lower()
        reply_callback = f"userbot:ticket:reply:{code}:{list_status}:{page}"
        status_callback_base = f"userbot:ticket:status:{code}"

    current_status = str(ticket.get("status") or "pending").strip().lower()
    if current_status == "closed":
        status_title = _adm_t('ub_lit_3e481593296d')
        new_status = "open"
    else:
        status_title = _adm_t('ub_lit_1a7b2fd035f2')
        new_status = "closed"
    if from_user_id > 0:
        status_callback = f"{status_callback_base}:{new_status}:{from_user_id}:{page}"
    else:
        status_callback = f"{status_callback_base}:{new_status}:{list_status}:{page}"

    rows = []
    if user_id > 0:
        rows.append([InlineKeyboardButton(user_btn, callback_data=f"userbot:user:{user_id}")])
    rows.append([InlineKeyboardButton(_adm_t("btn_reply2"), callback_data=reply_callback)])
    rows.append([InlineKeyboardButton(status_title, callback_data=status_callback)])
    return InlineKeyboardMarkup(rows)


def build_ticket_reply_screenshot_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_adm_t("ub_skip"), callback_data="userbot:ticketreply:skip")],
        [InlineKeyboardButton(_adm_t("btn_cancel_inline"), callback_data="userbot:ticketreply:cancel")],
    ])


def build_ticket_reply_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_adm_t("btn_send"), callback_data="userbot:ticketreply:send"),
            InlineKeyboardButton(_adm_t("btn_edit"), callback_data="userbot:ticketreply:edit"),
        ],
        [InlineKeyboardButton(_adm_t("btn_cancel_inline"), callback_data="userbot:ticketreply:cancel")],
    ])


def _zarin_coupon_status(item: Dict[str, Any]) -> Tuple[str, str]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    exp_raw = str(item.get("expires_at") or "").strip()
    used = int(item.get("used_count") or 0)
    max_uses = max(1, int(item.get("max_uses") or 1))
    if exp_raw:
        try:
            if datetime.strptime(exp_raw, "%Y-%m-%d %H:%M:%S") <= now:
                return "⏰", _adm_t('ub_lit_8c8d3e14ff33')
        except Exception:
            pass
    if used >= max_uses:
        return "🔒", _adm_t('ub_lit_8470f065c5cc')
    if int(item.get("is_active") or 0) != 1:
        return "⚫", _adm_t('ub_lit_551b1db85bf7')
    return "🟢", _adm_t('ub_lit_25c499f43398')


def build_zarin_coupons_list_keyboard(coupons: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for item in coupons:
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        icon, status = _zarin_coupon_status(item)
        amount = _format_toman(item.get("amount_toman"))
        used = int(item.get("used_count") or 0)
        max_uses = int(item.get("max_uses") or 1)
        title = f"{icon} {code} | {amount}{_adm_t('ub_lit_7fa2e0e6b8aa')}{used}/{max_uses} | {status}"
        rows.append([InlineKeyboardButton(title, callback_data=f"userbot:gifts:coupon:{code}")])
    rows.append([InlineKeyboardButton(_adm_t("ub_add_coupon"), callback_data="userbot:gifts:coupons:add")])
    rows.append([InlineKeyboardButton(_adm_t("ub_bulk_gifts"), callback_data="userbot:gifts:bulk")])
    rows.append([InlineKeyboardButton(_adm_t("ub_delete_coupon"), callback_data="userbot:gifts:coupons:delete")])
    rows.append([InlineKeyboardButton(_adm_t("back"), callback_data="userbot:gifts_menu")])
    return InlineKeyboardMarkup(rows)


def build_zarin_coupon_detail_keyboard(code: str, item: Optional[Dict[str, Any]] = None) -> InlineKeyboardMarkup:
    c = str(code or "").strip()
    active = int((item or {}).get("is_active") or 0) == 1
    toggle_title = _adm_t('ub_lit_cd15491463d9') if active else _adm_t('ub_lit_d309e9df4422')
    rows = [
        [InlineKeyboardButton(_adm_t("ub_set_zarinpal_link"), callback_data=f"userbot:gifts:coupon:set_link:{c}")],
        [
            InlineKeyboardButton(_adm_t("ub_deeplink"), callback_data=f"userbot:gifts:coupon:deeplink:{c}"),
            InlineKeyboardButton(_adm_t("ub_qr_deeplink"), callback_data=f"userbot:gifts:coupon:qr:{c}"),
        ],
        [InlineKeyboardButton(_adm_t("ub_coupon_campaign"), callback_data=f"userbot:gifts:coupon:campaign:{c}")],
        [InlineKeyboardButton(toggle_title, callback_data=f"userbot:gifts:coupon:toggle:{c}")],
        [InlineKeyboardButton(_adm_t("ub_edit_coupon_code"), callback_data=f"userbot:gifts:coupon:set_code:{c}")],
        [InlineKeyboardButton(_adm_t("ub_edit_coupon_limit"), callback_data=f"userbot:gifts:coupon:set_limit:{c}")],
        [InlineKeyboardButton(_adm_t("ub_edit_coupon_expiry"), callback_data=f"userbot:gifts:coupon:set_exp:{c}")],
        [InlineKeyboardButton(_adm_t("ub_edit_coupon_gift"), callback_data=f"userbot:gifts:coupon:set_amount:{c}")],
        [InlineKeyboardButton(_adm_t("ub_coupon_redemptions"), callback_data=f"userbot:gifts:redemptions:{c}")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:gifts:coupons")],
    ]
    return InlineKeyboardMarkup(rows)


def _adm_lang() -> str:
    try:
        from Shared import userbot_db as _udb
        return _udb.get_admin_language()
    except Exception:
        return "fa"


def _adm_t(key: str, **kw) -> str:
    from Shared import i18n as _i18n
    return _i18n.t(key, _adm_lang(), **kw)


def _user_lang_of(tg_id: int) -> str:
    """زبان کاربرِ گیرنده (برای پیام‌هایی که ادمین از طریق ربات کاربران می‌فرستد)."""
    try:
        from Shared import i18n as _i18n
        return _i18n.get_user_lang(int(tg_id or 0))
    except Exception:
        return "fa"


def _i18n_user_t(lang: str, key: str, **kw) -> str:
    from Shared import i18n as _i18n
    return _i18n.t(key, lang, **kw)


def build_userbot_settings_menu_keyboard(ui_settings: Optional[Dict[str, Any]] = None) -> InlineKeyboardMarkup:
    theme = normalize_button_theme((ui_settings or {}).get("button_theme"))
    theme_title = BUTTON_STYLE_THEMES.get(theme, BUTTON_STYLE_THEMES["smart"])["title"]
    rows = [
        [InlineKeyboardButton(_adm_t("us_sub_settings"), callback_data="userbot:settings:subscription")],
        [InlineKeyboardButton(_adm_t("us_link_status"), callback_data="userbot:settings:sub_link_status")],
        [InlineKeyboardButton(f"{_adm_t('us_colored_buttons')} | {theme_title}", callback_data="userbot:settings:ui")],
        [InlineKeyboardButton(_adm_t("us_buy_renew_settings"), callback_data="userbot:settings:buy_renew")],
        [InlineKeyboardButton(_adm_t("us_tx_plans_settings"), callback_data="userbot:settings:tx_plans")],
        [InlineKeyboardButton(_adm_t("us_texts_settings"), callback_data="userbot:settings:texts")],
        [InlineKeyboardButton(_adm_t("us_marketing_settings"), callback_data="userbot:settings:marketing")],
        [InlineKeyboardButton(_adm_t("us_force_join_settings"), callback_data="userbot:settings:force_join")],
        [InlineKeyboardButton(_adm_t("us_payment_settings"), callback_data="userbot:settings:payment")],
        [InlineKeyboardButton(_adm_t("us_backup_settings"), callback_data="userbot:settings:backup_restore")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_colored_buttons_settings_keyboard(ui_settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    enabled = bool(ui_settings.get("colored_buttons", True))
    current_theme = normalize_button_theme(ui_settings.get("button_theme"))
    enabled_icon = "✅" if enabled else "❌"
    rows = [
        [InlineKeyboardButton(f"{_adm_t('us_colored_state')} | {enabled_icon}", callback_data="userbot:settings:ui:colored_buttons")],
        [InlineKeyboardButton(_adm_t("us_select_theme"), callback_data="userbot:noop")],
    ]
    for theme_key, meta in BUTTON_STYLE_THEMES.items():
        selected_icon = "✅" if theme_key == current_theme else "▫️"
        rows.append([
            InlineKeyboardButton(
                f"{selected_icon} {meta['title']}",
                callback_data=f"userbot:settings:ui:theme:{theme_key}",
            )
        ])
    rows.append([InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings_menu")])
    return InlineKeyboardMarkup(rows)


def build_subscription_settings_menu_keyboard(
    show_user_page_link: bool = True,
    show_username: bool = True,
    shuffle_configs: bool = True,
    shuffle_server_layout: bool = True,
    shuffle_config_layout: bool = True,
) -> InlineKeyboardMarkup:
    user_page_icon = "✅" if show_user_page_link else "❌"
    username_icon = "✅" if show_username else "❌"
    shuffle_icon = "✅" if shuffle_configs else "❌"
    shuffle_server_icon = "✅" if shuffle_server_layout else "❌"
    shuffle_config_icon = "✅" if shuffle_config_layout else "❌"
    rows = [
        [InlineKeyboardButton(f"{_adm_t('us_show_hiddify_page')} | {user_page_icon}", callback_data="userbot:settings:subscription:show_user_page_link")],
        [InlineKeyboardButton(f"{_adm_t('us_show_username')} | {username_icon}", callback_data="userbot:settings:subscription:show_username")],
        [InlineKeyboardButton(f"{_adm_t('us_shuffle_configs')} | {shuffle_icon}", callback_data="userbot:settings:subscription:shuffle_configs")],
        [InlineKeyboardButton(f"{_adm_t('us_shuffle_servers')} | {shuffle_server_icon}", callback_data="userbot:settings:subscription:shuffle_server_layout")],
        [InlineKeyboardButton(f"{_adm_t('us_shuffle_configs_layout')} | {shuffle_config_icon}", callback_data="userbot:settings:subscription:shuffle_config_layout")],
        [InlineKeyboardButton(_adm_t("us_sub_reminder"), callback_data="userbot:settings:subscription:sub_status_reminder")],
        [InlineKeyboardButton(_adm_t("us_trial_spec"), callback_data="userbot:settings:subscription:trial_spec")],
        [InlineKeyboardButton(_adm_t("us_reset_trial"), callback_data="userbot:settings:subscription:reset_free_trial")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)

def build_sub_link_status_menu_keyboard(settings: Dict[str, bool]) -> InlineKeyboardMarkup:
    direct_icon = "✅" if settings.get("show_direct_config", True) else "❌"
    auto_icon = "✅" if settings.get("show_auto_sub_link", False) else "❌"
    sub_icon = "✅" if settings.get("show_sub_link", True) else "❌"
    sub_b64_icon = "✅" if settings.get("show_sub_link_b64", False) else "❌"
    multi_icon = "✅" if settings.get("show_multi_server", False) else "❌"
    multi_b64_icon = "✅" if settings.get("show_multi_server_b64", False) else "❌"
    rows = [
        [InlineKeyboardButton(f"{_adm_t('config_direct')} | {direct_icon}", callback_data="userbot:settings:sub_link_status:show_direct_config")],
        [InlineKeyboardButton(f"{_adm_t('auto_sub_link_label')} | {auto_icon}", callback_data="userbot:settings:sub_link_status:show_auto_sub_link")],
        [InlineKeyboardButton(f"{_adm_t('config_sub_link')} | {sub_icon}", callback_data="userbot:settings:sub_link_status:show_sub_link")],
        [InlineKeyboardButton(f"{_adm_t('sub_b64_label')} | {sub_b64_icon}", callback_data="userbot:settings:sub_link_status:show_sub_link_b64")],
        [InlineKeyboardButton(f"{_adm_t('config_smart')} | {multi_icon}", callback_data="userbot:settings:sub_link_status:show_multi_server")],
        [InlineKeyboardButton(f"{_adm_t('smart_b64_label')} | {multi_b64_icon}", callback_data="userbot:settings:sub_link_status:show_multi_server_b64")],
        [InlineKeyboardButton(_adm_t("us_set_smart_domain"), callback_data="userbot:settings:sub_link_status:set_base_url")],
        [InlineKeyboardButton(_adm_t("us_ssl_help"), callback_data="userbot:settings:sub_link_status:ssl_help")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_sub_status_reminder_menu_keyboard(reminder: Dict[str, Any]) -> InlineKeyboardMarkup:
    enabled_icon = "✅" if reminder.get("enabled", True) else "❌"
    rows = [
        [InlineKeyboardButton(f"{_adm_t('us_sub_reminder')} | {enabled_icon}", callback_data="userbot:settings:sub_status_reminder:enabled")],
        [InlineKeyboardButton(_adm_t("us_reminder_usage"), callback_data="userbot:settings:sub_status_reminder:usage")],
        [InlineKeyboardButton(_adm_t("us_reminder_days"), callback_data="userbot:settings:sub_status_reminder:days")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings:subscription")],
    ]
    return InlineKeyboardMarkup(rows)


def build_trial_spec_menu_keyboard(spec: Dict[str, Any]) -> InlineKeyboardMarkup:
    enabled_icon = "✅" if spec.get("enabled", True) else "❌"
    announce_icon = "✅" if spec.get("announce_enabled", True) else "❌"
    rows = [
        [InlineKeyboardButton(f"{_adm_t('us_trial_status')} | {enabled_icon}", callback_data="userbot:settings:trial_spec:enabled")],
        [InlineKeyboardButton(f"{_adm_t('us_trial_announce')} | {announce_icon}", callback_data="userbot:settings:trial_spec:announce")],
        [InlineKeyboardButton(_adm_t("us_trial_usage"), callback_data="userbot:settings:trial_spec:usage")],
        [InlineKeyboardButton(_adm_t("us_trial_days"), callback_data="userbot:settings:trial_spec:days")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings:subscription")],
    ]
    return InlineKeyboardMarkup(rows)


def build_buy_renew_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    buy_icon = "✅" if settings.get("enable_buy", True) else "❌"
    renew_icon = "✅" if settings.get("enable_renew", True) else "❌"
    renew_btn_icon = "✅" if settings.get("show_renew_in_main_menu", True) else "❌"
    event_status = "✅" if settings.get("event_channel_enabled", False) else "❌"

    rows = [
        [InlineKeyboardButton(f"{_adm_t('us_enable_buy')} | {buy_icon}", callback_data="userbot:settings:buy_renew:enable_buy")],
        [InlineKeyboardButton(f"{_adm_t('us_enable_renew')} | {renew_icon}", callback_data="userbot:settings:buy_renew:enable_renew")],
        [InlineKeyboardButton(f"{_adm_t('us_renew_btn_menu')} | {renew_btn_icon}", callback_data="userbot:settings:buy_renew:show_renew_in_main_menu")],
        [InlineKeyboardButton(_adm_t("us_renew_mode"), callback_data="userbot:settings:buy_renew:renew_mode_info")],
        [
            InlineKeyboardButton(_adm_t("us_plan_columns"), callback_data="userbot:settings:buy_renew:plan_columns:menu"),
            InlineKeyboardButton(_adm_t("us_server_columns"), callback_data="userbot:settings:buy_renew:server_columns:menu"),
        ],
        [InlineKeyboardButton(f"{_adm_t('us_unlimited_volume')} | {'✅' if settings.get('renew_unlimited_volume', False) else '❌'}", callback_data="userbot:settings:buy_renew:renew_unlimited_volume")],
        [InlineKeyboardButton(f"{_adm_t('us_unlimited_time')} | {'✅' if settings.get('renew_unlimited_time', False) else '❌'}", callback_data="userbot:settings:buy_renew:renew_unlimited_time")],
        [
            InlineKeyboardButton(event_status, callback_data="userbot:settings:buy_renew:event_channel_enabled"),
            InlineKeyboardButton(_adm_t("ag_event_set"), callback_data="userbot:settings:buy_renew:event_channel_set"),
        ],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_tx_plans_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    random_icon = "✅" if settings.get("random_tx_spec", False) else "❌"
    rows = [
        [InlineKeyboardButton(f"{_adm_t('us_random_tx')} | {random_icon}", callback_data="userbot:settings:tx_plans:random_tx_spec")],
        [InlineKeyboardButton(_adm_t("us_min_tx"), callback_data="userbot:settings:tx_plans:min_tx")],
        [InlineKeyboardButton(_adm_t("us_plan_categories"), callback_data="userbot:settings:tx_plans:plan_categories_mode:menu")],
        [InlineKeyboardButton(_adm_t("us_plan_sort"), callback_data="userbot:settings:tx_plans:plan_sort_mode:menu")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_text_settings_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_adm_t("us_txt_welcome"), callback_data="userbot:settings:texts:edit:welcome_message")],
        [InlineKeyboardButton(_adm_t("us_txt_faq"), callback_data="userbot:settings:texts:edit:faq_text")],
        [InlineKeyboardButton(_adm_t("us_txt_guide"), callback_data="userbot:settings:texts:guide_menu")],
        [InlineKeyboardButton(_adm_t("us_txt_invite_banner"), callback_data="userbot:settings:texts:invite_menu")],
        [InlineKeyboardButton(_adm_t("us_txt_servers_list"), callback_data="userbot:settings:texts:edit:servers_list_text")],
        [InlineKeyboardButton(_adm_t("us_txt_plans_list"), callback_data="userbot:settings:texts:edit:plans_list_text")],
        [InlineKeyboardButton(_adm_t("us_txt_ticket_panel"), callback_data="userbot:settings:texts:edit:ticket_panel_text")],
        [InlineKeyboardButton(_adm_t("us_txt_zarinpal"), callback_data="userbot:settings:texts:edit:zarinpal_pro_text")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_guide_text_settings_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_adm_t("guide_android"), callback_data="userbot:settings:texts:edit:guide_android_text")],
        [InlineKeyboardButton(_adm_t("guide_ios"), callback_data="userbot:settings:texts:edit:guide_ios_text")],
        [InlineKeyboardButton(_adm_t("guide_windows"), callback_data="userbot:settings:texts:edit:guide_windows_text")],
        [InlineKeyboardButton(_adm_t("guide_mac"), callback_data="userbot:settings:texts:edit:guide_mac_text")],
        [InlineKeyboardButton(_adm_t("guide_linux"), callback_data="userbot:settings:texts:edit:guide_linux_text")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings:texts")],
    ]
    return InlineKeyboardMarkup(rows)


def build_invite_text_settings_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_adm_t("us_txt_invite_info"), callback_data="userbot:settings:texts:edit:invite_info_text")],
        [InlineKeyboardButton(_adm_t("us_txt_banner"), callback_data="userbot:settings:texts:edit:invite_banner_text")],
        [InlineKeyboardButton(_adm_t("us_add_photo"), callback_data="userbot:settings:texts:invite:add_photo")],
        [InlineKeyboardButton(_adm_t("us_remove_banner"), callback_data="userbot:settings:texts:invite:remove_photo")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings:texts")],
    ]
    return InlineKeyboardMarkup(rows)


def build_marketing_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    discount_icon = "✅" if settings.get("enable_discount_code", False) else "❌"
    increase_icon = "✅" if settings.get("enable_increase_code", False) else "❌"
    gift_btn_icon = "✅" if settings.get("show_gift_button", False) else "❌"
    user_status_icon = "✅" if settings.get("show_user_status", True) else "❌"
    instant_coupon_icon = "✅" if settings.get("instant_gift_coupon", False) else "❌"
    rows = [
        [InlineKeyboardButton(_adm_t("us_marketing_settings"), callback_data="userbot:noop")],
        [InlineKeyboardButton(f"{_adm_t('us_discount_code')} | {discount_icon}", callback_data="userbot:settings:marketing:toggle:enable_discount_code")],
        [InlineKeyboardButton(f"{_adm_t('us_increase_code')} | {increase_icon}", callback_data="userbot:settings:marketing:toggle:enable_increase_code")],
        [InlineKeyboardButton(f"{_adm_t('us_gift_btn')} | {gift_btn_icon}", callback_data="userbot:settings:marketing:toggle:show_gift_button")],
        [InlineKeyboardButton(f"{_adm_t('us_show_user_status')} | {user_status_icon}", callback_data="userbot:settings:marketing:toggle:show_user_status")],
        [InlineKeyboardButton(f"{_adm_t('us_instant_coupon')} | {instant_coupon_icon}", callback_data="userbot:settings:marketing:toggle:instant_gift_coupon")],
        [InlineKeyboardButton(_adm_t("us_auto_gift_text"), callback_data="userbot:settings:marketing:edit:auto_gift_text")],
        [InlineKeyboardButton(_adm_t("us_min_auto_gift"), callback_data="userbot:settings:marketing:edit:min_auto_gift_charge")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_force_join_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    enabled_icon = "✅" if settings.get("enabled", False) else "❌"
    rows = [
        [InlineKeyboardButton(_adm_t("us_fj_help"), callback_data="userbot:settings:force_join:help")],
        [InlineKeyboardButton(_adm_t("us_force_join") + " | " + enabled_icon, callback_data="userbot:settings:force_join:toggle")],
        [InlineKeyboardButton(_adm_t("us_set_support_channel"), callback_data="userbot:settings:force_join:set_channel")],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_payment_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    event_icon = "✅" if settings.get("event_channel_enabled", False) else "❌"
    rows = [
        [InlineKeyboardButton(_adm_t("us_card_settings"), callback_data="userbot:settings:payment:card")],
        [InlineKeyboardButton(_adm_t("us_zarinpal_settings"), callback_data="userbot:settings:payment:zarinpal")],
        [InlineKeyboardButton(_adm_t("us_perfect_settings"), callback_data="userbot:settings:payment:perfect")],
        [InlineKeyboardButton(_adm_t("us_crypto_settings"), callback_data="userbot:settings:payment:crypto")],
        [
            InlineKeyboardButton(event_icon, callback_data="userbot:settings:payment:event_channel_toggle"),
            InlineKeyboardButton(_adm_t("us_event_channel"), callback_data="userbot:settings:payment:event_channel_set"),
        ],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_backup_restore_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    auto_icon = "✅" if settings.get("auto_backup_enabled", True) else "❌"
    event_icon = "✅" if settings.get("event_channel_enabled", False) else "❌"
    rows = [
        [InlineKeyboardButton(f"{_adm_t('us_auto_backup')} | {auto_icon}", callback_data="userbot:settings:backup_restore:auto_toggle")],
        [
            InlineKeyboardButton(_adm_t("us_download_backup"), callback_data="userbot:settings:backup_restore:download"),
            InlineKeyboardButton(_adm_t("us_restore_backup"), callback_data="userbot:settings:backup_restore:restore"),
        ],
        [
            InlineKeyboardButton(event_icon, callback_data="userbot:settings:backup_restore:event_toggle"),
            InlineKeyboardButton(_adm_t("us_event_channel"), callback_data="userbot:settings:backup_restore:event_set"),
        ],
        [InlineKeyboardButton(_adm_t("back"), callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_payment_method_menu_keyboard(method: str, enabled: bool) -> InlineKeyboardMarkup:
    title_map = {
        "card": _adm_t('ub_lit_5e50de690dd4'),
        "zarinpal": _adm_t('ub_lit_746b989c930f'),
        "perfect": _adm_t('ub_lit_1da0ed43fb30'),
        "crypto": _adm_t('ub_lit_206483172632'),
    }
    title = title_map.get(method, _adm_t('ub_lit_6fe520977035'))
    icon = "✅" if enabled else "❌"
    if method == "card":
        tx_settings = _get_tx_plans_settings()
        random_icon = "✅" if bool(tx_settings.get("random_tx_spec", False)) else "❌"
        pay_settings = _get_payment_settings()
        last4_icon = "✅" if bool(pay_settings.get("require_last4_for_card_receipt", False)) else "❌"
        rows = [
            [InlineKeyboardButton(f"{_adm_t('ub_card_payment')} | {icon}", callback_data="userbot:settings:payment:toggle:card")],
            [InlineKeyboardButton(f"{_adm_t('ub_card_last4')} | {last4_icon}", callback_data="userbot:settings:payment:card:require_last4")],
            [InlineKeyboardButton(f"{_adm_t('ub_card_random_tx')} | {random_icon}", callback_data="userbot:settings:payment:card:random_tx_spec")],
            [InlineKeyboardButton(_adm_t("ub_card_sms"), callback_data="userbot:settings:payment:card:sms")],
            [InlineKeyboardButton(_adm_t("ub_card_list"), callback_data="userbot:settings:payment:card:list")],
            [InlineKeyboardButton(_adm_t("ub_card_text"), callback_data="userbot:settings:payment:card:text")],
            [InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:settings:payment")],
        ]
        return InlineKeyboardMarkup(rows)

    if method == "zarinpal":
        rows = [
            [InlineKeyboardButton(f"{icon} | {_adm_t('ub_zarinpal_gateway')}", callback_data="userbot:settings:payment:toggle:zarinpal")],
            [InlineKeyboardButton(_adm_t("ub_zarinpal_text"), callback_data="userbot:settings:texts:edit:zarinpal_pro_text")],
            [InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:settings:payment")],
        ]
        return InlineKeyboardMarkup(rows)

    rows = [[InlineKeyboardButton(f"{title} | {icon}", callback_data=f"userbot:settings:payment:toggle:{method}")]]
    rows.append([InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:settings:payment")])
    return InlineKeyboardMarkup(rows)


def build_payment_cards_list_keyboard(cards: List[Dict[str, str]]) -> InlineKeyboardMarkup:
    rows = []
    for c in cards:
        number = str(c.get("number") or "").strip()
        if not number:
            continue
        rows.append([InlineKeyboardButton(number, callback_data=f"userbot:settings:payment:card:item:{number}")])
    rows.append([InlineKeyboardButton(_adm_t("ub_card_add"), callback_data="userbot:settings:payment:card:add")])
    rows.append([InlineKeyboardButton(_adm_t("ub_card_delete"), callback_data="userbot:settings:payment:card:delete")])
    rows.append([InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:settings:payment:card")])
    return InlineKeyboardMarkup(rows)


def build_payment_card_item_keyboard(number: str) -> InlineKeyboardMarkup:
    n = str(number or "").strip()
    rows = [
        [InlineKeyboardButton(_adm_t("ub_card_edit_number"), callback_data=f"userbot:settings:payment:card:edit_number:{n}")],
        [InlineKeyboardButton(_adm_t("ub_card_edit_owner"), callback_data=f"userbot:settings:payment:card:edit_owner:{n}")],
        [InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:settings:payment:card:list")],
    ]
    return InlineKeyboardMarkup(rows)


def build_sms_webhook_settings_keyboard() -> InlineKeyboardMarkup:
    status = _sms_webhook_status()
    enabled_icon = "✅" if status.get("enabled") else "❌"
    rows = [
        [InlineKeyboardButton(f"{_adm_t('ub_sms_auto')} | {enabled_icon}", callback_data="userbot:settings:payment:card:sms:toggle")],
        [InlineKeyboardButton(_adm_t("ub_sms_secret"), callback_data="userbot:settings:payment:card:sms:regen")],
        [InlineKeyboardButton(_adm_t("ub_sms_show_secret"), callback_data="userbot:settings:payment:card:sms:show")],
        [InlineKeyboardButton(_adm_t("ub_sms_help"), callback_data="userbot:settings:payment:card:sms:help")],
        [InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:settings:payment:card")],
    ]
    return InlineKeyboardMarkup(rows)


def build_plan_categories_mode_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    categories_enabled = bool(settings.get("plan_categories_enabled", True))
    simple_icon = "✅" if not categories_enabled else "❌"
    categorized_icon = "✅" if categories_enabled else "❌"
    rows = [
        [
            InlineKeyboardButton(f"{simple_icon} | {_adm_t('ub_mode_simple')}", callback_data="userbot:settings:tx_plans:plan_categories_mode:set:simple"),
            InlineKeyboardButton(f"{categorized_icon} | {_adm_t('ub_mode_categorized')}", callback_data="userbot:settings:tx_plans:plan_categories_mode:set:categorized"),
        ],
        [InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:settings:tx_plans")],
    ]
    return InlineKeyboardMarkup(rows)


def build_plan_sort_mode_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    mode = str(settings.get("plan_sort_mode") or "price").strip().lower()
    desc = bool(settings.get("plan_sort_desc", False))
    price_icon = "✅" if mode == "price" else "❌"
    gb_icon = "✅" if mode == "gb" else "❌"
    days_icon = "✅" if mode == "days" else "❌"
    asc_icon = "✅" if not desc else "❌"
    desc_icon = "✅" if desc else "❌"
    rows = [
        [InlineKeyboardButton(f"{price_icon} | {_adm_t('ub_sort_price')}", callback_data="userbot:settings:tx_plans:plan_sort_mode:set:price")],
        [InlineKeyboardButton(f"{gb_icon} | {_adm_t('ub_sort_volume')}", callback_data="userbot:settings:tx_plans:plan_sort_mode:set:gb")],
        [InlineKeyboardButton(f"{days_icon} | {_adm_t('ub_sort_time')}", callback_data="userbot:settings:tx_plans:plan_sort_mode:set:days")],
        [
            InlineKeyboardButton(f"{desc_icon} | {_adm_t('ub_sort_desc')}", callback_data="userbot:settings:tx_plans:plan_sort_dir:set:desc"),
            InlineKeyboardButton(f"{asc_icon} | {_adm_t('ub_sort_asc')}", callback_data="userbot:settings:tx_plans:plan_sort_dir:set:asc"),
        ],
        [InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:settings:tx_plans")],
    ]
    return InlineKeyboardMarkup(rows)


def build_plan_columns_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    col = int(settings.get("plan_columns") or 1)
    one_icon = "✅" if col == 1 else "❌"
    two_icon = "✅" if col == 2 else "❌"
    rows = [
        [
            InlineKeyboardButton(f"{one_icon} | {_adm_t('ub_one')}", callback_data="userbot:settings:buy_renew:plan_columns:set:1"),
            InlineKeyboardButton(f"{two_icon} | {_adm_t('ub_two')}", callback_data="userbot:settings:buy_renew:plan_columns:set:2"),
        ],
        [InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:settings:buy_renew")],
    ]
    return InlineKeyboardMarkup(rows)


def build_server_columns_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    col = int(settings.get("server_columns") or 1)
    one_icon = "✅" if col == 1 else "❌"
    two_icon = "✅" if col == 2 else "❌"
    three_icon = "✅" if col == 3 else "❌"
    rows = [
        [
            InlineKeyboardButton(f"{one_icon} | {_adm_t('ub_one')}", callback_data="userbot:settings:buy_renew:server_columns:set:1"),
            InlineKeyboardButton(f"{two_icon} | {_adm_t('ub_two')}", callback_data="userbot:settings:buy_renew:server_columns:set:2"),
            InlineKeyboardButton(f"{three_icon} | {_adm_t('ub_three')}", callback_data="userbot:settings:buy_renew:server_columns:set:3"),
        ],
        [InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:settings:buy_renew")],
    ]
    return InlineKeyboardMarkup(rows)


def build_renew_policy_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    policy = str(settings.get("renew_policy") or "advanced").strip().lower()
    volume_mode = str(settings.get("renew_volume_mode") or "").strip().lower()
    time_mode = str(settings.get("renew_time_mode") or "").strip().lower()
    if volume_mode not in {"add", "reset"}:
        volume_mode = "add" if policy in {"default", "fair"} else "reset"
    if time_mode not in {"add", "reset"}:
        time_mode = "add" if policy == "fair" else "reset"
    fair_icon = "✅" if policy == "fair" else "❌"
    advanced_icon = "✅" if policy == "advanced" else "❌"
    default_icon = "✅" if policy == "default" else "❌"
    volume_add_icon = "✅" if volume_mode == "add" else "❌"
    volume_reset_icon = "✅" if volume_mode == "reset" else "❌"
    time_add_icon = "✅" if time_mode == "add" else "❌"
    time_reset_icon = "✅" if time_mode == "reset" else "❌"
    rows = [
        [
            InlineKeyboardButton(f"{_adm_t('ub_policy_default')} | {default_icon}", callback_data="userbot:settings:buy_renew:renew_policy:default"),
            InlineKeyboardButton(f"{_adm_t('ub_policy_advanced')} | {advanced_icon}", callback_data="userbot:settings:buy_renew:renew_policy:advanced"),
            InlineKeyboardButton(f"{_adm_t('ub_policy_fair')} | {fair_icon}", callback_data="userbot:settings:buy_renew:renew_policy:fair"),
        ],
        [
            InlineKeyboardButton(
                f"{_adm_t('ub_volume_add')} | {volume_add_icon}",
                callback_data="userbot:settings:buy_renew:renew_rollover:volume:add",
            ),
            InlineKeyboardButton(
                f"{_adm_t('ub_volume_reset')} | {volume_reset_icon}",
                callback_data="userbot:settings:buy_renew:renew_rollover:volume:reset",
            ),
        ],
        [
            InlineKeyboardButton(
                f"{_adm_t('ub_time_add')} | {time_add_icon}",
                callback_data="userbot:settings:buy_renew:renew_rollover:time:add",
            ),
            InlineKeyboardButton(
                f"{_adm_t('ub_time_reset')} | {time_reset_icon}",
                callback_data="userbot:settings:buy_renew:renew_rollover:time:reset",
            ),
        ],
        [InlineKeyboardButton(_adm_t("ub_renew_max_days"), callback_data="userbot:settings:buy_renew:renew_limit:days")],
        [InlineKeyboardButton(_adm_t("ub_renew_max_usage"), callback_data="userbot:settings:buy_renew:renew_limit:usage")],
        [InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:settings:buy_renew")],
    ]
    return InlineKeyboardMarkup(rows)


def build_reset_free_trial_confirm_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_adm_t("ub_confirm"), callback_data="userbot:settings:subscription:reset_free_trial_confirm")],
        [InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:settings:subscription")],
    ]
    return InlineKeyboardMarkup(rows)


def _get_subscription_settings(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, bool]:
    settings = dict(DEFAULT_SUBS_SETTINGS)
    try:
        db_settings = userbot_db.get_subscription_settings()
        if isinstance(db_settings, dict):
            for key in settings.keys():
                if key in db_settings:
                    settings[key] = bool(db_settings[key])
    except Exception as e:
        logger.warning(f"Failed to load subscription settings from DB: {e}")
    return settings


def _get_ui_settings() -> Dict[str, Any]:
    try:
        return userbot_db.get_ui_settings()
    except Exception as e:
        logger.warning(f"Failed to load UI settings from DB: {e}")
        return dict(userbot_db.DEFAULT_UI_SETTINGS)


def _toggle_subscription_setting(context: ContextTypes.DEFAULT_TYPE, key: str) -> Dict[str, bool]:
    if key in DEFAULT_SUBS_SETTINGS:
        try:
            return userbot_db.toggle_subscription_setting(key)
        except Exception as e:
            logger.warning(f"Failed to toggle subscription setting '{key}' in DB: {e}")
    return _get_subscription_settings(context)


def _get_sub_reminder_settings() -> Dict[str, Any]:
    try:
        return userbot_db.get_sub_reminder_settings()
    except Exception as e:
        logger.warning(f"Failed to load sub reminder settings from DB: {e}")
        return {"enabled": True, "usage_gb": 3, "days": 3}


def _get_trial_spec_settings() -> Dict[str, Any]:
    try:
        return userbot_db.get_trial_spec_settings()
    except Exception as e:
        logger.warning(f"Failed to load trial spec settings from DB: {e}")
        return {"enabled": True, "announce_enabled": True, "usage_gb": 1, "days": 1}


def _get_buy_renew_settings() -> Dict[str, Any]:
    try:
        return userbot_db.get_buy_renew_settings()
    except Exception as e:
        logger.warning(f"Failed to load buy/renew settings from DB: {e}")
        return {
            "enable_buy": True,
            "enable_renew": True,
            "show_renew_in_main_menu": True,
            "renew_mode": "plans",
            "plan_columns": 1,
            "server_columns": 1,
            "renew_policy": "advanced",
            "renew_volume_mode": "reset",
            "renew_time_mode": "reset",
            "renew_unlimited_volume": False,
            "renew_unlimited_time": False,
            "renew_unlimited_volume_from_gb": 1000,
            "renew_unlimited_time_from_days": 365,
            "event_channel_enabled": False,
            "event_channel_id": "",
        }


def _get_tx_plans_settings() -> Dict[str, Any]:
    try:
        return userbot_db.get_tx_plans_settings()
    except Exception as e:
        logger.warning(f"Failed to load tx/plans settings from DB: {e}")
        return {
            "random_tx_spec": False,
            "min_transaction_toman": 10000,
            "plan_categories_enabled": True,
            "plan_sort_by_priority": True,
        }


def _get_text_settings() -> Dict[str, str]:
    try:
        return userbot_db.get_text_settings()
    except Exception as e:
        logger.warning(f"Failed to load text settings from DB: {e}")
        return {
            "welcome_message": _adm_t('ub_lit_2544c118b6da'),
            "faq_text": (
                _adm_t('ub_lit_e79f4b498d61')
            ),
            "guide_text": _adm_t('ub_lit_6ba70f0fe183'),
            "guide_android_text": (
                _adm_t('ub_lit_c5833c3cac4d')
            ),
            "guide_ios_text": (
                _adm_t('ub_lit_7af978c10a78')
            ),
            "guide_windows_text": (
                _adm_t('ub_lit_efb35014c2d3')
            ),
            "guide_mac_text": (
                _adm_t('ub_lit_f94427f639a0')
            ),
            "guide_linux_text": (
                _adm_t('ub_lit_946da096d6f1')
            ),
            "invite_text": _adm_t('ub_lit_90b7f03e5d81'),
            "invite_info_text": _adm_t('ub_lit_7be80447d717'),
            "invite_banner_text": (
                _adm_t('ub_lit_232c70278dbb')
            ),
            "invite_banner_photo_id": "",
            "servers_list_text": _adm_t('ub_lit_fb2f43710743'),
            "plans_list_text": _adm_t('ub_lit_abfd4e6ea514'),
            "ticket_panel_text": _adm_t('ub_lit_5574bffb29fc'),
            "zarinpal_pro_text": "0",
            "card_to_card_text": "0",
        }


def _get_marketing_settings() -> Dict[str, Any]:
    try:
        return userbot_db.get_marketing_settings()
    except Exception as e:
        logger.warning(f"Failed to load marketing settings from DB: {e}")
        return {
            "enable_discount_code": False,
            "enable_increase_code": False,
            "show_gift_button": False,
            "show_user_status": True,
            "instant_gift_coupon": False,
            "auto_gift_text": _adm_t('ub_lit_ab667b0084a5'),
            "min_auto_gift_charge": 100000,
        }


def _get_force_join_settings() -> Dict[str, Any]:
    try:
        return userbot_db.get_force_join_settings()
    except Exception as e:
        logger.warning(f"Failed to load force-join settings from DB: {e}")
        return {
            "enabled": False,
            "channel_id": "",
            "channel_username": "",
            "channel_link": "",
            "guide_text": (
                _adm_t('ub_lit_bb1cd626c22c')
            ),
        }


def _get_payment_settings() -> Dict[str, Any]:
    try:
        return userbot_db.get_payment_settings()
    except Exception as e:
        logger.warning(f"Failed to load payment settings from DB: {e}")
        return {
            "enable_card_to_card": True,
            "require_last4_for_card_receipt": False,
            "enable_zarinpal": False,
            "enable_perfect_money": False,
            "enable_crypto": False,
            "event_channel_enabled": False,
            "event_channel_id": "",
        }


def _get_backup_restore_settings() -> Dict[str, Any]:
    try:
        return userbot_db.get_backup_restore_settings()
    except Exception as e:
        logger.warning(f"Failed to load backup/restore settings from DB: {e}")
        return {
            "auto_backup_enabled": True,
            "event_channel_enabled": False,
            "event_channel_id": "",
        }


def _project_root_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _backup_storage_dir() -> Path:
    backup_dir = _project_root_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        backup_dir.chmod(0o700)
    except OSError:
        pass
    return backup_dir


def _safe_backup_name(name: str, default: str = "backup.json") -> str:
    raw = str(name or "").strip().replace("\\", "/")
    raw = raw.split("/")[-1]
    raw = re.sub(r'[^A-Za-z0-9._\- @()\u0600-\u06FF]+', "_", raw).strip(" .")
    return raw or default


def _normalize_backup_member_name(raw_name: str) -> str:
    name = str(raw_name or "").replace("\\", "/").lstrip("/")
    if not name:
        return ""
    parts = [p for p in name.split("/") if p not in {"", "."}]
    if any(p == ".." for p in parts):
        return ""
    return "/".join(parts)


def _atomic_write_bytes(target_path: Path, payload: bytes) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = f".tmp_restore_{target_path.name}_{os.getpid()}_{int(datetime.now(timezone.utc).timestamp())}"
    tmp_path = target_path.parent / tmp_name
    with tmp_path.open("wb") as fh:
        fh.write(payload)
    tmp_path.replace(target_path)


def _normalize_plans_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(_adm_t('ub_lit_79793b18e804'))
    servers = payload.get("servers")
    if not isinstance(servers, dict):
        return payload

    for key, block in list(servers.items()):
        if not isinstance(block, dict):
            continue
        mode = str(block.get("display_mode") or block.get("mode") or "").strip().lower()
        if mode not in {"fixed", "dynamic", "mixed"}:
            mode = "dynamic"
        block["display_mode"] = mode
        block.setdefault("categories", [])
        block.setdefault("plans", [])
        block.setdefault("dynamic_settings", {})
        block.setdefault("next_category_id", 1)
        block.setdefault("next_plan_id", 1)
        servers[key] = block
    payload["servers"] = servers
    return payload


def _normalize_servers_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(_adm_t('ub_lit_bd23571afb0d'))
    servers = payload.get("servers")
    if not isinstance(servers, list):
        return payload

    normalized: List[Dict[str, Any]] = []
    for raw in servers:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)

        panel_url = str(row.get("panel_url") or row.get("url") or "").strip()
        if panel_url:
            row["panel_url"] = panel_url.rstrip("/")

        admin_proxy = str(row.get("admin_proxy_path") or row.get("proxy_path_admin") or "").strip().strip("/")
        if admin_proxy:
            row["admin_proxy_path"] = admin_proxy

        user_proxy = str(row.get("user_proxy_path") or row.get("proxy_path_user") or admin_proxy).strip().strip("/")
        if user_proxy:
            row["user_proxy_path"] = user_proxy

        admin_uuid = str(row.get("admin_uuid") or row.get("uuid_admin") or row.get("api_key") or "").strip()
        if admin_uuid:
            row["admin_uuid"] = admin_uuid

        row.setdefault("users", [])
        row.setdefault("plans", [])
        row.setdefault("domains", row.get("domains") or [])
        normalized.append(row)

    payload["servers"] = normalized
    return payload


def _restore_sqlite_db_from_file(src_db: Path, dst_db: Path) -> None:
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(src_db)) as src_conn:
        src_conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        check = src_conn.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).strip().lower() != "ok":
            raise sqlite3.DatabaseError(
                f"SQLite restore source integrity check failed for {src_db.name}"
            )
        with sqlite3.connect(str(dst_db), timeout=30) as dst_conn:
            src_conn.backup(dst_conn)
            dst_conn.commit()
    try:
        dst_db.chmod(0o600)
    except OSError:
        pass


def _create_sqlite_backup_snapshot(src_db: Path, dst_db: Path) -> None:
    """Create a consistent SQLite snapshot, including committed WAL data."""
    if not src_db.exists() or not src_db.is_file():
        raise FileNotFoundError(src_db)
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    sidecars = [
        dst_db,
        dst_db.with_name(dst_db.name + "-wal"),
        dst_db.with_name(dst_db.name + "-shm"),
    ]
    for candidate in sidecars:
        candidate.unlink(missing_ok=True)
    try:
        src_uri = f"{src_db.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(src_uri, uri=True, timeout=30) as src_conn:
            src_conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
            with sqlite3.connect(str(dst_db), timeout=30) as dst_conn:
                src_conn.backup(dst_conn)
                dst_conn.commit()
                check = dst_conn.execute("PRAGMA quick_check").fetchone()
                if not check or str(check[0]).strip().lower() != "ok":
                    raise sqlite3.DatabaseError(
                        f"SQLite backup integrity check failed for {src_db.name}"
                    )
        try:
            dst_db.chmod(0o600)
        except OSError:
            pass
    except BaseException:
        for candidate in sidecars:
            candidate.unlink(missing_ok=True)
        raise


LEGACY_BACKUP_CORE_KEYS = {
    "users",
    "orders",
    "payments",
    "servers",
    "plans",
    "str_config",
    "int_config",
    "bool_config",
}


def _legacy_is_nullish(value: Any) -> bool:
    if value is None:
        return True
    s = str(value).strip().lower()
    return s in {"", "none", "null", "nil", "n/a"}


def _legacy_clean_str(value: Any, default: str = "") -> str:
    if _legacy_is_nullish(value):
        return default
    return str(value).strip()


def _legacy_to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return int(default)


def _legacy_to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(str(value).replace(",", "").strip())
    except Exception:
        return float(default)


def _legacy_to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value) != 0
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on", "y", "t"}:
        return True
    if s in {"0", "false", "no", "off", "n", "f", ""}:
        return False
    return default


def _legacy_now_str() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _legacy_norm_dt(value: Any, fallback: str) -> str:
    raw = _legacy_clean_str(value, "")
    if not raw:
        return fallback
    # خروجی بکاپ قدیمی معمولا همین فرمت را دارد.
    return raw


def _legacy_cfg_to_map(items: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        out[key] = item.get("value")
    return out


def _legacy_is_payload(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    score = len(LEGACY_BACKUP_CORE_KEYS.intersection(set(data.keys())))
    return score >= 3


def _legacy_collect_payload_from_sqlite(db_path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [str(r["name"]) for r in cur.fetchall() if r and r["name"]]
        for table in tables:
            try:
                cur.execute(f"SELECT * FROM {table}")
                rows = [dict(r) for r in cur.fetchall()]
                out[table] = rows
            except Exception:
                continue
    return out


def _legacy_detect_money_scale(data: Dict[str, Any]) -> int:
    samples: List[int] = []

    for item in (data.get("payments") or []):
        if isinstance(item, dict):
            v = _legacy_to_int(item.get("payment_amount"), 0)
            if v > 0:
                samples.append(v)
            if len(samples) >= 500:
                break

    for item in (data.get("wallet") or []):
        if isinstance(item, dict):
            v = _legacy_to_int(item.get("balance"), 0)
            if v > 0:
                samples.append(v)
            if len(samples) >= 800:
                break

    for item in (data.get("plans") or []):
        if isinstance(item, dict):
            v = _legacy_to_int(item.get("price"), 0)
            if v > 0:
                samples.append(v)
            if len(samples) >= 1200:
                break

    if not samples:
        return 1

    samples = [abs(int(v)) for v in samples if int(v) > 0]
    if not samples:
        return 1

    div10_ratio = sum(1 for v in samples if v % 10 == 0) / float(len(samples))
    sorted_vals = sorted(samples)
    median = sorted_vals[len(sorted_vals) // 2]
    # بکاپ‌های قدیمی معمولا مبلغ‌ها را ریال ذخیره می‌کردند.
    if div10_ratio >= 0.7 and median >= 100000:
        return 10
    return 1


def _legacy_money_to_toman(value: Any, scale: int) -> int:
    amount = _legacy_to_int(value, 0)
    if scale <= 1:
        return amount
    return int(round(amount / float(scale)))


def _legacy_build_servers_json(data: Dict[str, Any]) -> Dict[str, Any]:
    servers_raw = data.get("servers") if isinstance(data.get("servers"), list) else []
    urls_raw = data.get("server_urls") if isinstance(data.get("server_urls"), list) else []
    cards_raw = data.get("cards") if isinstance(data.get("cards"), list) else []
    str_cfg = _legacy_cfg_to_map(data.get("str_config"))
    bool_cfg = _legacy_cfg_to_map(data.get("bool_config"))

    domains_by_server: Dict[int, List[Dict[str, Any]]] = {}
    for item in urls_raw:
        if not isinstance(item, dict):
            continue
        sid = _legacy_to_int(item.get("server_id"), 0)
        if sid <= 0:
            continue
        entry = {
            "id": 0,
            "title": _legacy_clean_str(item.get("title"), _adm_t('ub_lit_07b71d9606f0')),
            "domain": _legacy_clean_str(item.get("url")),
        }
        if not entry["domain"]:
            continue
        domains_by_server.setdefault(sid, []).append(entry)

    servers_out: List[Dict[str, Any]] = []
    for row in sorted(
        [s for s in servers_raw if isinstance(s, dict)],
        key=lambda x: _legacy_to_int(x.get("id"), 0),
    ):
        sid = _legacy_to_int(row.get("id"), 0)
        if sid <= 0:
            continue
        domains: List[Dict[str, Any]] = []
        for idx, d in enumerate(domains_by_server.get(sid, []), start=1):
            dom = _legacy_clean_str(d.get("domain"))
            if not dom:
                continue
            domains.append(
                {
                    "id": idx,
                    "title": _legacy_clean_str(d.get("title"), f"{idx}"),
                    "domain": dom,
                }
            )

        server_obj: Dict[str, Any] = {
            "id": sid,
            "title": _legacy_clean_str(row.get("title"), f"{_adm_t('ub_lit_bf1e2c70cb7e')}{sid}"),
            "panel_url": _legacy_clean_str(row.get("url")),
            "admin_proxy_path": _legacy_clean_str(row.get("proxy_path_admin")),
            "admin_uuid": _legacy_clean_str(row.get("uuid_admin")),
            "user_proxy_path": _legacy_clean_str(row.get("proxy_path_user")),
            "users_limit": max(0, _legacy_to_int(row.get("user_limit"), 0)),
            "priority": _legacy_to_int(row.get("priority_index"), 0),
            "version": _legacy_to_int(row.get("panel_version"), 0),
            "users": [],
            "plans": [],
            "domains": domains,
            "nodes": [],
        }
        servers_out.append(server_obj)

    cards: List[Dict[str, str]] = []
    seen_cards: Set[str] = set()
    for row in cards_raw:
        if not isinstance(row, dict):
            continue
        number = re.sub(r"\D+", "", _legacy_clean_str(row.get("card_number")))
        if len(number) < 10 or number in seen_cards:
            continue
        seen_cards.add(number)
        cards.append(
            {
                "number": number,
                "owner": _legacy_clean_str(row.get("card_holder")),
                "bank": _legacy_clean_str(row.get("bank")),
            }
        )

    cfg_card_num = re.sub(r"\D+", "", _legacy_clean_str(str_cfg.get("card_number")))
    cfg_card_holder = _legacy_clean_str(str_cfg.get("card_holder"))
    if len(cfg_card_num) >= 10 and cfg_card_num not in seen_cards:
        seen_cards.add(cfg_card_num)
        cards.append({"number": cfg_card_num, "owner": cfg_card_holder, "bank": ""})

    settings_block: Dict[str, Any] = {
        "cards": cards,
        "card_rr_index": 0,
        "card_active": _legacy_to_bool(bool_cfg.get("card_transfer_payment"), True),
        "gateway_active": _legacy_to_bool(bool_cfg.get("zarinpal_payment"), False),
    }

    out: Dict[str, Any] = {"servers": servers_out}
    out["settings"] = settings_block
    return out


def _legacy_default_dynamic_settings(data: Dict[str, Any], money_scale: int) -> Dict[str, Any]:
    default_settings = {
        "price_per_gb": 2000,
        "price_per_month": 30000,
        "min_gb": 20,
        "max_gb": 500,
        "step_gb": 10,
        "min_month": 1,
        "max_month": 12,
        "step_month": 1,
        "discount_step_gb": 50,
        "discount_percent_step": 5,
        "discount_percent_max": 50,
        "discount_tiers": [],
    }
    custom_list = data.get("custom_plans") if isinstance(data.get("custom_plans"), list) else []
    if not custom_list:
        return default_settings
    row = next((r for r in custom_list if isinstance(r, dict)), None)
    if not row:
        return default_settings
    return {
        "price_per_gb": max(1, _legacy_money_to_toman(row.get("per_gb_price"), money_scale)),
        "price_per_month": max(1, _legacy_money_to_toman(row.get("per_duration_price"), money_scale)),
        "min_gb": max(1, _legacy_to_int(row.get("init_volume"), 1)),
        "max_gb": max(1, _legacy_to_int(row.get("max_volume_per_duration"), 500)),
        "step_gb": 1,
        "min_month": max(1, _legacy_to_int(row.get("min_duration"), 1)),
        "max_month": max(1, _legacy_to_int(row.get("max_duration"), 12)),
        "step_month": 1,
        "discount_step_gb": max(1, _legacy_to_int(row.get("volume_discount_stage_size"), 50)),
        "discount_percent_step": max(0, _legacy_to_int(row.get("volume_discount_per_stage"), 0)),
        "discount_percent_max": max(0, _legacy_to_int(row.get("volume_max_discount"), 0)),
        "discount_tiers": [],
    }


def _legacy_build_plans_json(data: Dict[str, Any], money_scale: int) -> Dict[str, Any]:
    servers_raw = data.get("servers") if isinstance(data.get("servers"), list) else []
    categories_raw = data.get("plan_categories") if isinstance(data.get("plan_categories"), list) else []
    plans_raw = data.get("plans") if isinstance(data.get("plans"), list) else []

    known_server_ids: Set[int] = set()
    for row in servers_raw:
        if isinstance(row, dict):
            sid = _legacy_to_int(row.get("id"), 0)
            if sid > 0:
                known_server_ids.add(sid)
    if not known_server_ids:
        known_server_ids.add(1)

    default_sid = min(known_server_ids)

    def _target_sid(raw_sid: Any) -> int:
        sid = _legacy_to_int(raw_sid, 0)
        if sid <= 0:
            return default_sid
        return sid

    blocks: Dict[str, Dict[str, Any]] = {}
    dyn_default = _legacy_default_dynamic_settings(data, money_scale)

    for sid in sorted(known_server_ids):
        blocks[str(sid)] = {
            "display_mode": "dynamic",
            "categories": [],
            "plans": [],
            "dynamic_settings": dict(dyn_default),
            "next_category_id": 1,
            "next_plan_id": 1,
        }

    for row in categories_raw:
        if not isinstance(row, dict):
            continue
        sid = _target_sid(row.get("server_id"))
        key = str(sid)
        if key not in blocks:
            blocks[key] = {
                "display_mode": "dynamic",
                "categories": [],
                "plans": [],
                "dynamic_settings": dict(dyn_default),
                "next_category_id": 1,
                "next_plan_id": 1,
            }
        cat_id = _legacy_to_int(row.get("id"), 0)
        if cat_id <= 0:
            continue
        if any(_legacy_to_int(c.get("id"), 0) == cat_id for c in blocks[key]["categories"]):
            continue
        blocks[key]["categories"].append(
            {
                "id": cat_id,
                "title": _legacy_clean_str(row.get("title"), f"{_adm_t('ub_lit_b0cc1957de2b')}{cat_id}"),
                "priority": _legacy_to_int(row.get("priority_index"), 0),
            }
        )

    for row in plans_raw:
        if not isinstance(row, dict):
            continue
        status_raw = row.get("status")
        # در بکاپ قدیمی معمولا status=0 به معنی فعال است.
        if str(status_raw).strip() in {"1", "true", "True"}:
            continue

        sid = _target_sid(row.get("server_id"))
        key = str(sid)
        if key not in blocks:
            blocks[key] = {
                "display_mode": "dynamic",
                "categories": [],
                "plans": [],
                "dynamic_settings": dict(dyn_default),
                "next_category_id": 1,
                "next_plan_id": 1,
            }

        plan_id = _legacy_to_int(row.get("id"), 0)
        if plan_id <= 0:
            continue
        category_id = _legacy_to_int(row.get("category_id"), 0)
        category_id_val: Optional[int] = category_id if category_id > 0 else None
        if category_id_val is not None:
            exists_cat = any(_legacy_to_int(c.get("id"), 0) == category_id_val for c in blocks[key]["categories"])
            if not exists_cat:
                category_id_val = None

        gb = max(0.0, _legacy_to_float(row.get("size_gb"), 0.0))
        days = max(1, _legacy_to_int(row.get("days"), 30))
        price = max(0, _legacy_money_to_toman(row.get("price"), money_scale))
        title = _legacy_clean_str(row.get("description"))
        if not title:
            if gb > 0:
                title = f"{_format_gb(gb)}{_adm_t('ub_lit_7adb1e0c83a8')}{days}{_adm_t('ub_lit_6f274ee56123')}"
            else:
                title = f"{days}{_adm_t('ub_lit_3155e32642fd')}"

        blocks[key]["plans"].append(
            {
                "id": plan_id,
                "category_id": category_id_val,
                "title": title,
                "price": price,
                "days": days,
                "gb": gb,
                "priority": 0,
            }
        )

    for block in blocks.values():
        block["categories"] = sorted(
            block.get("categories") or [],
            key=lambda c: (_legacy_to_int(c.get("priority"), 0), _legacy_to_int(c.get("id"), 0)),
        )
        block["plans"] = sorted(
            block.get("plans") or [],
            key=lambda p: (_legacy_to_int(p.get("priority"), 0), _legacy_to_int(p.get("id"), 0)),
        )
        if block["plans"]:
            block["display_mode"] = "fixed"
        max_cat = max((_legacy_to_int(c.get("id"), 0) for c in block["categories"]), default=0)
        max_plan = max((_legacy_to_int(p.get("id"), 0) for p in block["plans"]), default=0)
        block["next_category_id"] = max_cat + 1
        block["next_plan_id"] = max_plan + 1

    return {"servers": blocks}


def _legacy_map_payment_method(raw: Any) -> str:
    method = _legacy_clean_str(raw, "").lower()
    if not method:
        return "card"
    if "card" in method:
        return "card"
    if "wallet" in method:
        return "wallet"
    if "zarin" in method:
        return "zarinpal"
    if "perfect" in method:
        return "perfect_money"
    if "crypto" in method or "now" in method:
        return "crypto"
    return method


def _legacy_build_userbot_settings(data: Dict[str, Any], money_scale: int) -> Dict[str, Any]:
    str_cfg = _legacy_cfg_to_map(data.get("str_config"))
    int_cfg = _legacy_cfg_to_map(data.get("int_config"))
    bool_cfg = _legacy_cfg_to_map(data.get("bool_config"))

    def b(key: str, default: bool = False) -> bool:
        return _legacy_to_bool(bool_cfg.get(key), default)

    def i(key: str, default: int = 0) -> int:
        return _legacy_to_int(int_cfg.get(key), default)

    def s(key: str, default: str = "") -> str:
        return _legacy_clean_str(str_cfg.get(key), default)

    sub_settings = dict(userbot_db.DEFAULT_SUBS_SETTINGS)
    servers_cfg_raw = s("servers_configs", "")
    servers_cfg: Dict[str, Any] = {}
    if servers_cfg_raw:
        try:
            parsed = json.loads(servers_cfg_raw)
            if isinstance(parsed, dict):
                servers_cfg = parsed
        except Exception:
            servers_cfg = {}
    randomize = _legacy_to_bool(servers_cfg.get("randomize"), sub_settings["shuffle_configs"])
    randomize_mode = _legacy_clean_str(servers_cfg.get("randomize_mode"), "servers").lower()
    sub_settings.update(
        {
            "show_user_page_link": b("visible_user_web_panle", sub_settings["show_user_page_link"]),
            "show_username": _legacy_to_bool(servers_cfg.get("username"), sub_settings["show_username"]),
            "shuffle_configs": randomize,
            "shuffle_server_layout": randomize and randomize_mode in {"server", "servers"},
            "shuffle_config_layout": randomize and randomize_mode not in {"server", "servers"},
            "show_direct_config": b("visible_conf_dir", sub_settings["show_direct_config"]),
            "show_auto_sub_link": b("visible_conf_sub_auto", sub_settings["show_auto_sub_link"]),
            "show_sub_link": b("visible_conf_sub_url", sub_settings["show_sub_link"]),
            "show_sub_link_b64": b("visible_conf_sub_url_b64", sub_settings["show_sub_link_b64"]),
            "show_multi_server": b("visible_conf_sub_multi_server", sub_settings["show_multi_server"]),
            "show_multi_server_b64": b("visible_conf_sub_multi_server_b64", sub_settings["show_multi_server_b64"]),
        }
    )

    buy_renew = dict(userbot_db.DEFAULT_BUY_RENEW_SETTINGS)
    renew_method_raw = i("renewal_method", 2)
    renew_policy = "advanced"
    if renew_method_raw == 1:
        renew_policy = "default"
    elif renew_method_raw == 3:
        renew_policy = "fair"
    buy_renew.update(
        {
            "enable_buy": b("buy_subscription_status", buy_renew["enable_buy"]),
            "enable_renew": b("renewal_subscription_status", buy_renew["enable_renew"]),
            "show_renew_in_main_menu": b(
                "visible_renewal_button_main_menu", buy_renew["show_renew_in_main_menu"]
            ),
            "renew_mode": "servers" if i("show_plans_method", 1) == 2 else "plans",
            "plan_columns": max(1, min(2, i("plans_columns", buy_renew["plan_columns"]))),
            "server_columns": max(1, min(3, i("servers_columns", buy_renew["server_columns"]))),
            "renew_policy": renew_policy,
            "renew_volume_mode": "add" if renew_policy in {"default", "fair"} else "reset",
            "renew_time_mode": "add" if renew_policy == "fair" else "reset",
            "renew_max_days": max(1, i("advanced_renewal_days", buy_renew["renew_max_days"])),
            "renew_max_remaining_gb": max(
                1, i("advanced_renewal_usage", buy_renew["renew_max_remaining_gb"])
            ),
            "event_channel_enabled": b("event_channel_subscription", buy_renew["event_channel_enabled"]),
            "event_channel_id": str(i("event_channel_id_subscription", 0))
            if b("event_channel_subscription", False) and i("event_channel_id_subscription", 0) != 0
            else "",
        }
    )

    tx_plans = dict(userbot_db.DEFAULT_TX_PLANS_SETTINGS)
    sort_mode_map = {1: "price", 2: "gb", 3: "days"}
    tx_plans.update(
        {
            "random_tx_spec": b("three_random_num_price", tx_plans["random_tx_spec"]),
            "plan_categories_enabled": i("plan_type", 1) in {1, 2},
            "plan_sort_by_priority": True,
            "plan_sort_mode": sort_mode_map.get(i("sort_plans_method", 1), "price"),
            "plan_sort_desc": b("sort_plans_method_desc", tx_plans["plan_sort_desc"]),
            "min_transaction_toman": max(
                1, _legacy_money_to_toman(i("min_deposit_amount", 100000), money_scale)
            ),
        }
    )

    marketing = dict(userbot_db.DEFAULT_MARKETING_SETTINGS)
    auto_gift_text = s("auto_gift_msg_explanation", marketing["auto_gift_text"])
    if _legacy_is_nullish(auto_gift_text):
        auto_gift_text = marketing["auto_gift_text"]
    marketing.update(
        {
            "enable_discount_code": b("visible_apply_discount", marketing["enable_discount_code"]),
            "enable_increase_code": b("visible_apply_surcharge", marketing["enable_increase_code"]),
            "show_gift_button": b("visible_gift_button", marketing["show_gift_button"]),
            "show_user_status": b("visible_user_conditions", marketing["show_user_status"]),
            "instant_gift_coupon": b("vouchers_instant_use", marketing["instant_gift_coupon"]),
            "auto_gift_text": auto_gift_text,
            "min_auto_gift_charge": max(
                0,
                _legacy_money_to_toman(
                    i("min_deposit_amount_auto_recharge_gift", 1000000),
                    money_scale,
                ),
            ),
        }
    )

    force_join = dict(userbot_db.DEFAULT_FORCE_JOIN_SETTINGS)
    channel_raw = s("channel_id", "")
    if channel_raw.startswith("@"):
        force_join["channel_username"] = channel_raw.lstrip("@")
        force_join["channel_id"] = ""
        force_join["channel_link"] = f"https://t.me/{force_join['channel_username']}"
    elif channel_raw.lstrip("-").isdigit():
        force_join["channel_id"] = channel_raw
        force_join["channel_username"] = ""
    elif channel_raw.startswith("http://") or channel_raw.startswith("https://"):
        force_join["channel_link"] = channel_raw
    force_join["enabled"] = b("force_join_channel", force_join["enabled"])

    payment = dict(userbot_db.DEFAULT_PAYMENT_SETTINGS)
    payment.update(
        {
            "enable_card_to_card": b("card_transfer_payment", payment["enable_card_to_card"]),
            "enable_zarinpal": b("zarinpal_payment", payment["enable_zarinpal"])
            or b("zarinpal_pro_payment", payment["enable_zarinpal"]),
            "enable_perfect_money": b("perfect_money_payment", payment["enable_perfect_money"]),
            "enable_crypto": b("nowpayments_crypto_payment", payment["enable_crypto"]),
            "event_channel_enabled": b("event_channel_payment", payment["event_channel_enabled"]),
            "event_channel_id": str(i("event_channel_id_payment", 0))
            if b("event_channel_payment", False) and i("event_channel_id_payment", 0) != 0
            else "",
        }
    )

    backup_restore = dict(userbot_db.DEFAULT_BACKUP_RESTORE_SETTINGS)
    backup_restore.update(
        {
            "auto_backup_enabled": b("bot_auto_backup", backup_restore["auto_backup_enabled"]),
            "event_channel_enabled": b(
                "event_channel_backup", backup_restore["event_channel_enabled"]
            ),
            "event_channel_id": str(i("event_channel_id_backup", 0))
            if b("event_channel_backup", False) and i("event_channel_id_backup", 0) != 0
            else "",
        }
    )

    sub_reminder = dict(userbot_db.DEFAULT_SUB_REMINDER_SETTINGS)
    sub_reminder.update(
        {
            "enabled": b("reminder_notification", sub_reminder["enabled"]),
            "usage_gb": max(0.1, float(i("reminder_notification_usage", 3))),
            "days": max(1, i("reminder_notification_days", 3)),
        }
    )

    trial_spec = dict(userbot_db.DEFAULT_TRIAL_SPEC_SETTINGS)
    trial_spec.update(
        {
            "enabled": b("test_subscription", trial_spec["enabled"]),
            "announce_enabled": b("test_subscription_alert", trial_spec["announce_enabled"]),
            "usage_gb": max(0.1, _legacy_to_float(int_cfg.get("test_sub_size_gb"), 1)),
            "days": max(1, i("test_sub_days", 1)),
        }
    )

    text_settings = dict(userbot_db.DEFAULT_TEXT_SETTINGS)

    def _set_text_if_any(dst_key: str, src_val: Any) -> None:
        txt = _legacy_clean_str(src_val, "")
        if not txt:
            return
        text_settings[dst_key] = txt

    _set_text_if_any("welcome_message", str_cfg.get("msg_user_start"))
    _set_text_if_any("faq_text", str_cfg.get("msg_faq"))
    _set_text_if_any("guide_android_text", str_cfg.get("msg_manual_android"))
    _set_text_if_any("guide_ios_text", str_cfg.get("msg_manual_ios"))
    _set_text_if_any("guide_windows_text", str_cfg.get("msg_manual_windows"))
    _set_text_if_any("guide_mac_text", str_cfg.get("msg_manual_mac"))
    _set_text_if_any("guide_linux_text", str_cfg.get("msg_manual_linux"))
    _set_text_if_any("invite_info_text", str_cfg.get("referral_msg_encouragement"))
    _set_text_if_any("invite_banner_text", str_cfg.get("referral_banner_caption"))
    _set_text_if_any("servers_list_text", str_cfg.get("msg_servers"))
    _set_text_if_any("plans_list_text", str_cfg.get("msg_plans"))
    _set_text_if_any("zarinpal_pro_text", str_cfg.get("zarinpal_pro_text"))
    _set_text_if_any("card_to_card_text", str_cfg.get("card_text"))

    ticket_menu = _legacy_clean_str(str_cfg.get("msg_ticket_menu"), "")
    if ticket_menu and not ticket_menu.startswith("/"):
        text_settings["ticket_panel_text"] = ticket_menu

    invite_photo = _legacy_clean_str(str_cfg.get("referral_banner_photo"), "")
    if invite_photo:
        text_settings["invite_banner_photo_id"] = invite_photo

    return {
        "subscription_settings": sub_settings,
        "buy_renew_settings": buy_renew,
        "tx_plans_settings": tx_plans,
        "text_settings": text_settings,
        "marketing_settings": marketing,
        "force_join_settings": force_join,
        "payment_settings": payment,
        "backup_restore_settings": backup_restore,
        "sub_reminder_settings": sub_reminder,
        "trial_spec_settings": trial_spec,
    }


def _restore_legacy_into_userbot_db(data: Dict[str, Any], money_scale: int) -> Dict[str, int]:
    root_dir = _project_root_dir()
    db_path = root_dir / "Shared" / "hiddify_sellbot.db"
    userbot_db.init_db()
    if not db_path.exists():
        raise ValueError(_adm_t('ub_lit_85f093f7bc00'))

    now = _legacy_now_str()
    temp_db = Path(tempfile.gettempdir()) / f"restore_legacy_db_{os.getpid()}_{int(datetime.now(timezone.utc).timestamp())}.db"
    shutil.copy2(db_path, temp_db)

    stats = {
        "users": 0,
        "orders": 0,
        "payments": 0,
        "services": 0,
        "tickets": 0,
        "ticket_messages": 0,
    }

    try:
        with sqlite3.connect(str(temp_db)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            tables = [
                "userbot_ticket_messages",
                "userbot_tickets",
                "userbot_service_nodes",
                "userbot_sub_tokens",
                "userbot_service_probe",
                "userbot_services",
                "userbot_orders",
                "userbot_payments",
                "userbot_zarin_voucher_redemptions",
                "userbot_zarin_vouchers",
                "userbot_users",
                "userbot_settings",
            ]

            table_cols: Dict[str, Set[str]] = {}
            for table in tables:
                try:
                    cur.execute(f"PRAGMA table_info({table})")
                    cols = {str(r["name"]) for r in cur.fetchall()}
                    if cols:
                        table_cols[table] = cols
                except Exception:
                    continue

            for table in tables:
                if table in table_cols:
                    cur.execute(f"DELETE FROM {table}")
            try:
                cur.execute("DELETE FROM sqlite_sequence WHERE name LIKE 'userbot_%'")
            except Exception:
                pass

            def _insert_row(table: str, row: Dict[str, Any]) -> int:
                cols = list(table_cols.get(table, set()))
                if not cols:
                    return 0
                payload = {k: v for k, v in row.items() if k in cols}
                if not payload:
                    return 0
                keys = list(payload.keys())
                placeholders = ", ".join("?" for _ in keys)
                sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})"
                cur.execute(sql, [payload[k] for k in keys])
                return int(cur.lastrowid or 0)

            users_raw = data.get("users") if isinstance(data.get("users"), list) else []
            wallet_raw = data.get("wallet") if isinstance(data.get("wallet"), list) else []
            orders_raw = data.get("orders") if isinstance(data.get("orders"), list) else []
            payments_raw = data.get("payments") if isinstance(data.get("payments"), list) else []
            plans_raw = data.get("plans") if isinstance(data.get("plans"), list) else []
            servers_raw = data.get("servers") if isinstance(data.get("servers"), list) else []
            order_subs_raw = data.get("order_subscriptions") if isinstance(data.get("order_subscriptions"), list) else []
            non_order_subs_raw = data.get("non_order_subscriptions") if isinstance(data.get("non_order_subscriptions"), list) else []
            tickets_raw = data.get("tickets") if isinstance(data.get("tickets"), list) else []
            ticket_messages_raw = data.get("ticket_messages") if isinstance(data.get("ticket_messages"), list) else []

            wallet_by_tg: Dict[int, int] = {}
            for row in wallet_raw:
                if not isinstance(row, dict):
                    continue
                tg = _legacy_to_int(row.get("telegram_id"), 0)
                if tg <= 0:
                    continue
                wallet_by_tg[tg] = _legacy_money_to_toman(row.get("balance"), money_scale)

            user_by_tg: Dict[int, Dict[str, Any]] = {}
            user_id_by_tg: Dict[int, int] = {}

            for row in users_raw:
                if not isinstance(row, dict):
                    continue
                tg = _legacy_to_int(row.get("telegram_id"), 0)
                if tg <= 0:
                    continue
                full_name = _legacy_clean_str(row.get("full_name"))
                username = _legacy_clean_str(row.get("username")).lstrip("@")
                created_at = _legacy_norm_dt(row.get("created_at"), now)
                wallet_amount = wallet_by_tg.get(tg, 0)
                parts = full_name.split()
                first_name = parts[0] if parts else ""
                last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
                insert_obj = {
                    "telegram_id": tg,
                    "username": username,
                    "full_name": full_name,
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_trial": 1 if _legacy_to_bool(row.get("test_subscription"), False) else 0,
                    "orders_count": 0,
                    "wallet_balance": wallet_amount,
                    "created_at": created_at,
                    "last_seen": created_at,
                    "is_banned": 1 if _legacy_to_bool(row.get("banned"), False) else 0,
                    "got_free_trial": 1 if _legacy_to_bool(row.get("test_subscription"), False) else 0,
                }
                uid = _insert_row("userbot_users", insert_obj)
                if uid <= 0:
                    continue
                user_id_by_tg[tg] = uid
                user_by_tg[tg] = {
                    "telegram_id": tg,
                    "username": username,
                    "full_name": full_name,
                    "id": uid,
                }
                stats["users"] += 1

            for tg, balance in wallet_by_tg.items():
                if tg in user_id_by_tg:
                    continue
                full_name = f"{_adm_t('ub_lit_c6f852ead8a7')}{tg}"
                created_at = now
                insert_obj = {
                    "telegram_id": tg,
                    "username": "",
                    "full_name": full_name,
                    "first_name": full_name,
                    "last_name": "",
                    "is_trial": 0,
                    "orders_count": 0,
                    "wallet_balance": balance,
                    "created_at": created_at,
                    "last_seen": created_at,
                    "is_banned": 0,
                    "got_free_trial": 0,
                }
                uid = _insert_row("userbot_users", insert_obj)
                if uid <= 0:
                    continue
                user_id_by_tg[tg] = uid
                user_by_tg[tg] = {
                    "telegram_id": tg,
                    "username": "",
                    "full_name": full_name,
                    "id": uid,
                }
                stats["users"] += 1

            plan_by_id: Dict[int, Dict[str, Any]] = {}
            for row in plans_raw:
                if not isinstance(row, dict):
                    continue
                pid = _legacy_to_int(row.get("id"), 0)
                if pid > 0:
                    plan_by_id[pid] = row

            server_title_by_id: Dict[int, str] = {}
            for row in servers_raw:
                if not isinstance(row, dict):
                    continue
                sid = _legacy_to_int(row.get("id"), 0)
                if sid <= 0:
                    continue
                server_title_by_id[sid] = _legacy_clean_str(row.get("title"), f"{_adm_t('ub_lit_bf1e2c70cb7e')}{sid}")

            orders_by_id: Dict[int, Dict[str, Any]] = {}
            orders_count_by_uid: Dict[int, int] = {}

            for row in orders_raw:
                if not isinstance(row, dict):
                    continue
                order_id = _legacy_to_int(row.get("id"), 0)
                tg = _legacy_to_int(row.get("telegram_id"), 0)
                if order_id <= 0 or tg <= 0:
                    continue
                uid = user_id_by_tg.get(tg)
                if not uid:
                    continue
                plan_id = _legacy_to_int(row.get("plan_id"), 0)
                plan = plan_by_id.get(plan_id) or {}
                volume_gb = max(0.0, _legacy_to_float(plan.get("size_gb"), 0.0))
                days = max(0, _legacy_to_int(plan.get("days"), 0))
                price = max(0, _legacy_money_to_toman(plan.get("price"), money_scale))
                server_id = _legacy_to_int(plan.get("server_id"), 0)
                insert_obj = {
                    "order_id": order_id,
                    "user_id": uid,
                    "telegram_id": tg,
                    "username": str((user_by_tg.get(tg) or {}).get("username") or ""),
                    "full_name": str((user_by_tg.get(tg) or {}).get("full_name") or ""),
                    "created_at": _legacy_norm_dt(row.get("created_at"), now),
                    "volume_gb": volume_gb,
                    "days": days,
                    "price": price,
                    "plan_title": _legacy_clean_str(plan.get("description"))
                    or _legacy_clean_str(row.get("user_name"))
                    or f"Plan {plan_id}",
                    "server_location": server_title_by_id.get(server_id, ""),
                    "status": "completed",
                }
                try:
                    rid = _insert_row("userbot_orders", insert_obj)
                except sqlite3.IntegrityError:
                    continue
                if rid <= 0:
                    continue
                stats["orders"] += 1
                orders_count_by_uid[uid] = orders_count_by_uid.get(uid, 0) + 1
                orders_by_id[order_id] = {
                    "order": row,
                    "plan": plan,
                    "user_id": uid,
                    "telegram_id": tg,
                    "created_at": insert_obj["created_at"],
                    "user_name": _legacy_clean_str(row.get("user_name"))
                    or str((user_by_tg.get(tg) or {}).get("full_name") or ""),
                }

            for row in payments_raw:
                if not isinstance(row, dict):
                    continue
                tg = _legacy_to_int(row.get("telegram_id"), 0)
                if tg <= 0:
                    continue
                uid = user_id_by_tg.get(tg)
                if not uid:
                    continue
                amount = max(0, _legacy_money_to_toman(row.get("payment_amount"), money_scale))
                approved = _legacy_to_bool(row.get("approved"), False)
                created_at = _legacy_norm_dt(row.get("created_at"), now)
                pay_id = _legacy_to_int(row.get("id"), 0)
                tx_code = f"{pay_id:07d}" if pay_id > 0 else ""
                insert_obj = {
                    "user_id": uid,
                    "amount": amount,
                    "method": _legacy_map_payment_method(row.get("payment_method")),
                    "status": "approved" if approved else "pending",
                    "receipt_image": _legacy_clean_str(row.get("payment_image")),
                    "created_at": created_at,
                    "updated_at": created_at,
                    "tx_code": tx_code,
                    "idempotency_key": None,
                }
                rid = _insert_row("userbot_payments", insert_obj)
                if rid > 0:
                    stats["payments"] += 1

            service_candidates: Dict[Tuple[int, str], Dict[str, Any]] = {}

            for row in order_subs_raw:
                if not isinstance(row, dict):
                    continue
                order_id = _legacy_to_int(row.get("order_id"), 0)
                server_id = _legacy_to_int(row.get("server_id"), 0)
                user_uuid = _legacy_clean_str(row.get("uuid"))
                if order_id <= 0 or server_id <= 0 or not user_uuid:
                    continue
                order_payload = orders_by_id.get(order_id)
                if not order_payload:
                    continue
                tg = _legacy_to_int(order_payload.get("telegram_id"), 0)
                uid = user_id_by_tg.get(tg)
                if not uid:
                    continue
                plan = order_payload.get("plan") if isinstance(order_payload.get("plan"), dict) else {}
                created_at = _legacy_norm_dt(order_payload.get("created_at"), now)
                usage_limit = max(0.0, _legacy_to_float(plan.get("size_gb"), 0.0))
                days_left = max(0, _legacy_to_int(plan.get("days"), 0))
                service_name = _legacy_clean_str(order_payload.get("user_name"))
                if not service_name:
                    service_name = str((user_by_tg.get(tg) or {}).get("full_name") or "")
                if not service_name:
                    service_name = f"vpn-{user_uuid[:6]}"
                key = (server_id, user_uuid)
                item = {
                    "user_id": uid,
                    "telegram_id": tg,
                    "server_id": server_id,
                    "server_title": server_title_by_id.get(server_id, f"{_adm_t('ub_lit_bf1e2c70cb7e')}{server_id}"),
                    "user_uuid": user_uuid,
                    "name": service_name,
                    "usage_limit": usage_limit,
                    "days_left": days_left,
                    "created_at": created_at,
                    "comment": f"uuid:{user_uuid}",
                }
                prev = service_candidates.get(key)
                if not prev or item["created_at"] >= prev.get("created_at", ""):
                    service_candidates[key] = item

            for row in non_order_subs_raw:
                if not isinstance(row, dict):
                    continue
                tg = _legacy_to_int(row.get("telegram_id"), 0)
                server_id = _legacy_to_int(row.get("server_id"), 0)
                user_uuid = _legacy_clean_str(row.get("uuid"))
                if tg <= 0 or server_id <= 0 or not user_uuid:
                    continue
                uid = user_id_by_tg.get(tg)
                if not uid:
                    continue
                user_meta = user_by_tg.get(tg) or {}
                service_name = _legacy_clean_str(user_meta.get("full_name")) or f"vpn-{user_uuid[:6]}"
                key = (server_id, user_uuid)
                item = {
                    "user_id": uid,
                    "telegram_id": tg,
                    "server_id": server_id,
                    "server_title": server_title_by_id.get(server_id, f"{_adm_t('ub_lit_bf1e2c70cb7e')}{server_id}"),
                    "user_uuid": user_uuid,
                    "name": service_name,
                    "usage_limit": 0.0,
                    "days_left": 0,
                    "created_at": now,
                    "comment": f"uuid:{user_uuid}",
                }
                if key not in service_candidates:
                    service_candidates[key] = item

            for item in sorted(
                service_candidates.values(),
                key=lambda x: (x.get("created_at") or "", _legacy_clean_str(x.get("name"))),
            ):
                created_at = _legacy_norm_dt(item.get("created_at"), now)
                service_row = {
                    "user_id": _legacy_to_int(item.get("user_id"), 0),
                    "server_id": _legacy_to_int(item.get("server_id"), 0),
                    "server_title": _legacy_clean_str(item.get("server_title")),
                    "name": _legacy_clean_str(item.get("name"), "vpn-user"),
                    "user_uuid": _legacy_clean_str(item.get("user_uuid")),
                    "usage_current": 0.0,
                    "usage_limit": max(0.0, _legacy_to_float(item.get("usage_limit"), 0.0)),
                    "days_left": max(0, _legacy_to_int(item.get("days_left"), 0)),
                    "last_online": _adm_t('ub_lit_264f61d0e11d'),
                    "comment": _legacy_clean_str(item.get("comment")),
                    "status": "active",
                    "created_at": created_at,
                    "updated_at": created_at,
                }
                sid_new = _insert_row("userbot_services", service_row)
                if sid_new <= 0:
                    continue
                node_row = {
                    "service_id": sid_new,
                    "server_id": _legacy_to_int(item.get("server_id"), 0),
                    "server_title": _legacy_clean_str(item.get("server_title")),
                    "panel_user_uuid": _legacy_clean_str(item.get("user_uuid")),
                    "panel_user_id": _legacy_clean_str(item.get("user_uuid")),
                    "is_active": 1,
                    "created_at": created_at,
                    "updated_at": created_at,
                }
                _insert_row("userbot_service_nodes", node_row)
                stats["services"] += 1

            ticket_code_by_legacy_id: Dict[int, int] = {}
            closed_ticket_codes: Set[int] = set()
            has_admin_reply: Set[int] = set()
            last_message_at: Dict[int, str] = {}
            used_ticket_codes: Set[int] = set()

            for row in tickets_raw:
                if not isinstance(row, dict):
                    continue
                legacy_tid = _legacy_to_int(row.get("id"), 0)
                tg = _legacy_to_int(row.get("telegram_id"), 0)
                if legacy_tid <= 0 or tg <= 0:
                    continue
                uid = user_id_by_tg.get(tg)
                if not uid:
                    continue
                code = legacy_tid
                while code in used_ticket_codes or code <= 0:
                    code = code + 1 if code > 0 else 1000000
                used_ticket_codes.add(code)
                created_at = _legacy_norm_dt(row.get("created_at"), now)
                user_meta = user_by_tg.get(tg) or {}
                username = _legacy_clean_str(user_meta.get("username"))
                full_name = _legacy_clean_str(user_meta.get("full_name"))
                status = "closed" if _legacy_to_bool(row.get("closed"), False) else "pending"
                if status == "closed":
                    closed_ticket_codes.add(code)
                ticket_row = {
                    "ticket_code": code,
                    "user_id": uid,
                    "telegram_id": tg,
                    "username": username,
                    "full_name": full_name,
                    "service_name": "",
                    "title": _legacy_clean_str(row.get("title")),
                    "question": _legacy_clean_str(row.get("question")),
                    "receipt_photo_id": _legacy_clean_str(row.get("attachment")),
                    "status": status,
                    "admin_name": "",
                    "admin_telegram_id": 0,
                    "created_at": created_at,
                    "updated_at": created_at,
                }
                rid = _insert_row("userbot_tickets", ticket_row)
                if rid <= 0:
                    continue
                ticket_code_by_legacy_id[legacy_tid] = code
                stats["tickets"] += 1

                msg_text = _legacy_clean_str(row.get("question"))
                msg_photo = _legacy_clean_str(row.get("attachment"))
                if msg_text or msg_photo:
                    message_row = {
                        "ticket_code": code,
                        "sender_type": "user",
                        "sender_name": full_name or username or str(tg),
                        "message_text": msg_text,
                        "photo_file_id": msg_photo,
                        "created_at": created_at,
                    }
                    _insert_row("userbot_ticket_messages", message_row)
                    stats["ticket_messages"] += 1
                    last_message_at[code] = created_at

            sorted_ticket_messages = sorted(
                [m for m in ticket_messages_raw if isinstance(m, dict)],
                key=lambda m: (_legacy_norm_dt(m.get("created_at"), now), _legacy_to_int(m.get("id"), 0)),
            )
            for row in sorted_ticket_messages:
                legacy_tid = _legacy_to_int(row.get("ticket_id"), 0)
                code = ticket_code_by_legacy_id.get(legacy_tid)
                if not code:
                    continue
                tg = _legacy_to_int(row.get("telegram_id"), 0)
                user_meta = user_by_tg.get(tg) or {}
                sender_type = "admin" if _legacy_to_bool(row.get("answer"), False) else "user"
                if sender_type == "admin":
                    has_admin_reply.add(code)
                message_text = _legacy_clean_str(row.get("message"))
                photo_id = _legacy_clean_str(row.get("attachment"))
                if not message_text and not photo_id:
                    continue
                created_at = _legacy_norm_dt(row.get("created_at"), now)
                message_row = {
                    "ticket_code": code,
                    "sender_type": sender_type,
                    "sender_name": _legacy_clean_str(user_meta.get("full_name"))
                    or _legacy_clean_str(user_meta.get("username"))
                    or (str(tg) if tg > 0 else (_adm_t('ub_lit_4efd4a96334a') if sender_type == "admin" else _adm_t('ub_lit_883da9f030ce'))),
                    "message_text": message_text,
                    "photo_file_id": photo_id,
                    "created_at": created_at,
                }
                _insert_row("userbot_ticket_messages", message_row)
                stats["ticket_messages"] += 1
                last_message_at[code] = created_at

            for code in list(ticket_code_by_legacy_id.values()):
                if code in closed_ticket_codes:
                    new_status = "closed"
                elif code in has_admin_reply:
                    new_status = "open"
                else:
                    new_status = "pending"
                updated_at = last_message_at.get(code, now)
                try:
                    cur.execute(
                        "UPDATE userbot_tickets SET status = ?, updated_at = ? WHERE ticket_code = ?",
                        (new_status, updated_at, code),
                    )
                except Exception:
                    pass

            settings_payload = _legacy_build_userbot_settings(data, money_scale)
            for key, value in settings_payload.items():
                payload = json.dumps(value, ensure_ascii=False)
                try:
                    cur.execute(
                        """
                        INSERT INTO userbot_settings (key, value) VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value
                        """,
                        (key, payload),
                    )
                except Exception:
                    continue

            if "orders_count" in table_cols.get("userbot_users", set()):
                for uid, count in orders_count_by_uid.items():
                    cur.execute(
                        "UPDATE userbot_users SET orders_count = ? WHERE id = ?",
                        (int(count), int(uid)),
                    )

            conn.commit()

        _restore_sqlite_db_from_file(temp_db, db_path)
        return stats
    finally:
        try:
            temp_db.unlink(missing_ok=True)
        except Exception:
            pass


def _restore_legacy_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    root_dir = _project_root_dir()
    shared_dir = root_dir / "Shared"
    money_scale = _legacy_detect_money_scale(data)

    servers_payload = _legacy_build_servers_json(data)
    plans_payload = _legacy_build_plans_json(data, money_scale)

    _atomic_write_bytes(
        shared_dir / "servers.json",
        json.dumps(servers_payload, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    _atomic_write_bytes(
        shared_dir / "plans.json",
        json.dumps(plans_payload, ensure_ascii=False, indent=2).encode("utf-8"),
    )

    stats = _restore_legacy_into_userbot_db(data, money_scale)
    return {
        "restored_files": [
            "Shared/hiddify_sellbot.db",
            "Shared/servers.json",
            "Shared/plans.json",
        ],
        "legacy_stats": stats,
    }


def _extract_legacy_payload_from_zip(
    zf: zipfile.ZipFile,
    members: Dict[str, zipfile.ZipInfo],
) -> Optional[Dict[str, Any]]:
    json_members = sorted([n for n in members.keys() if n.lower().endswith(".json")])
    for name in json_members:
        try:
            payload = zf.read(members[name])
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            continue
        if _legacy_is_payload(data):
            return data

    legacy_db_candidates = [
        "hidybot.db",
        "hiddify_bot.db",
        "hiddifybot.db",
        "data.db",
        "userbot.db",
    ]
    db_member_name = ""
    for name in members.keys():
        low = name.lower()
        if any(low == cand or low.endswith(f"/{cand}") for cand in legacy_db_candidates):
            db_member_name = name
            break
    if not db_member_name:
        return None

    tmp_db = Path(tempfile.gettempdir()) / f"legacy_restore_src_{os.getpid()}_{int(datetime.now(timezone.utc).timestamp())}.db"
    try:
        with zf.open(members[db_member_name], "r") as src, tmp_db.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        data = _legacy_collect_payload_from_sqlite(tmp_db)
        if _legacy_is_payload(data):
            return data
    except Exception:
        return None
    finally:
        try:
            tmp_db.unlink(missing_ok=True)
        except Exception:
            pass
    return None


def _restore_from_zip_backup(backup_file: Path) -> Dict[str, Any]:
    root_dir = _project_root_dir()
    shared_dir = root_dir / "Shared"
    restored_files: List[str] = []
    receipts_count = 0
    mode = "zip"
    legacy_stats: Dict[str, Any] = {}

    with zipfile.ZipFile(backup_file, mode="r") as zf:
        members: Dict[str, zipfile.ZipInfo] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            normalized = _normalize_backup_member_name(info.filename)
            if normalized:
                members[normalized] = info

        def _find_member(candidates: List[str]) -> str:
            for name in candidates:
                if name in members:
                    return name
            return ""

        db_member = _find_member([
            "Shared/hiddify_sellbot.db",
            "Shared/userbot.db",
            "hiddify_sellbot.db",
            "userbot.db",
        ])
        if db_member:
            tmp_db = Path(tempfile.gettempdir()) / f"restore_db_{os.getpid()}_{int(datetime.now(timezone.utc).timestamp())}.db"
            try:
                with zf.open(members[db_member], "r") as src, tmp_db.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                _restore_sqlite_db_from_file(tmp_db, shared_dir / "hiddify_sellbot.db")
                restored_files.append("Shared/hiddify_sellbot.db")
            finally:
                try:
                    tmp_db.unlink(missing_ok=True)
                except Exception:
                    pass

        servers_member = _find_member(["Shared/servers.json", "servers.json"])
        if servers_member:
            payload = zf.read(members[servers_member])
            servers_obj = _normalize_servers_payload(json.loads(payload.decode("utf-8")))
            _atomic_write_bytes(
                shared_dir / "servers.json",
                json.dumps(servers_obj, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            restored_files.append("Shared/servers.json")

        plans_member = _find_member(["Shared/plans.json", "plans.json"])
        if plans_member:
            payload = zf.read(members[plans_member])
            plans_obj = _normalize_plans_payload(json.loads(payload.decode("utf-8")))
            _atomic_write_bytes(
                shared_dir / "plans.json",
                json.dumps(plans_obj, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            restored_files.append("Shared/plans.json")

        # v4.0.0: Restore agency database
        agency_db_member = _find_member(["Shared/agency.db", "agency.db"])
        if agency_db_member:
            tmp_db = Path(tempfile.gettempdir()) / f"restore_agency_{os.getpid()}_{int(datetime.now(timezone.utc).timestamp())}.db"
            try:
                with zf.open(members[agency_db_member], "r") as src, tmp_db.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                _restore_sqlite_db_from_file(tmp_db, shared_dir / "agency.db")
                restored_files.append("Shared/agency.db")
            finally:
                try:
                    tmp_db.unlink(missing_ok=True)
                except Exception:
                    pass

        # v4.0.0: Restore customer bot database
        customer_db_member = _find_member(["customer_bot.db", "CustomerBot/customer_bot.db"])
        if customer_db_member:
            tmp_db = Path(tempfile.gettempdir()) / f"restore_customer_{os.getpid()}_{int(datetime.now(timezone.utc).timestamp())}.db"
            try:
                with zf.open(members[customer_db_member], "r") as src, tmp_db.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                _restore_sqlite_db_from_file(tmp_db, root_dir / "customer_bot.db")
                restored_files.append("customer_bot.db")
            finally:
                try:
                    tmp_db.unlink(missing_ok=True)
                except Exception:
                    pass

        # v4.0.0: Restore agent bot database
        agent_db_member = _find_member(["AgentBot/agent_bot.db", "agent_bot.db"])
        if agent_db_member:
            agent_dir = root_dir / "AgentBot"
            agent_dir.mkdir(parents=True, exist_ok=True)
            tmp_db = Path(tempfile.gettempdir()) / f"restore_agent_{os.getpid()}_{int(datetime.now(timezone.utc).timestamp())}.db"
            try:
                with zf.open(members[agent_db_member], "r") as src, tmp_db.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                _restore_sqlite_db_from_file(tmp_db, agent_dir / "agent_bot.db")
                restored_files.append("AgentBot/agent_bot.db")
            finally:
                try:
                    tmp_db.unlink(missing_ok=True)
                except Exception:
                    pass

        receipts_members = [
            name for name in members.keys()
            if name.startswith("Receiptions/") and len(name) > len("Receiptions/")
        ]
        if receipts_members:
            temp_root = Path(tempfile.mkdtemp(prefix="restore_receipts_"))
            try:
                extracted_root = temp_root / "Receiptions"
                for name in receipts_members:
                    rel = Path(name).relative_to("Receiptions")
                    dst_path = extracted_root / rel
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(members[name], "r") as src, dst_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    receipts_count += 1

                final_receipts_dir = root_dir / "Receiptions"
                if final_receipts_dir.exists():
                    shutil.rmtree(final_receipts_dir, ignore_errors=True)
                shutil.copytree(extracted_root, final_receipts_dir)
                restored_files.append("Receiptions/*")
            finally:
                shutil.rmtree(temp_root, ignore_errors=True)

        if not any(
            item in restored_files
            for item in {"Shared/hiddify_sellbot.db", "Shared/servers.json", "Shared/plans.json", "Shared/agency.db", "customer_bot.db", "AgentBot/agent_bot.db"}
        ):
            legacy_payload = _extract_legacy_payload_from_zip(zf, members)
            if legacy_payload:
                legacy_result = _restore_legacy_payload(legacy_payload)
                for item in legacy_result.get("restored_files") or []:
                    if item not in restored_files:
                        restored_files.append(item)
                legacy_stats = dict(legacy_result.get("legacy_stats") or {})
                mode = "zip-legacy"

    if not restored_files:
        raise ValueError(_adm_t('ub_lit_50ade149a68f'))

    result: Dict[str, Any] = {
        "mode": mode,
        "restored_files": restored_files,
        "receipts_count": receipts_count,
    }
    if legacy_stats:
        result["legacy_stats"] = legacy_stats
    return result


def _restore_from_json_backup(backup_file: Path) -> Dict[str, Any]:
    root_dir = _project_root_dir()
    shared_dir = root_dir / "Shared"
    restored_files: List[str] = []

    raw = backup_file.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(_adm_t('ub_lit_18fdcf71f040'))

    if "backup_type" in data and "files" in data:
        raise ValueError(_adm_t('ub_lit_7d50c8f383bd'))

    if _legacy_is_payload(data):
        legacy_result = _restore_legacy_payload(data)
        return {
            "mode": "json-legacy",
            "restored_files": legacy_result.get("restored_files") or [],
            "receipts_count": 0,
            "legacy_stats": legacy_result.get("legacy_stats") or {},
        }

    if "servers_json" in data:
        payload = data.get("servers_json")
        if not isinstance(payload, dict):
            raise ValueError(_adm_t('ub_lit_6414b7fddb01'))
        payload = _normalize_servers_payload(payload)
        _atomic_write_bytes(shared_dir / "servers.json", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        restored_files.append("Shared/servers.json")
    elif "servers" in data and "settings" in data:
        payload = _normalize_servers_payload(data)
        _atomic_write_bytes(shared_dir / "servers.json", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        restored_files.append("Shared/servers.json")

    if "plans_json" in data:
        payload = data.get("plans_json")
        if not isinstance(payload, dict):
            raise ValueError(_adm_t('ub_lit_99d08da32614'))
        payload = _normalize_plans_payload(payload)
        _atomic_write_bytes(shared_dir / "plans.json", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        restored_files.append("Shared/plans.json")
    elif "servers" in data and isinstance(data.get("servers"), dict) and "settings" not in data:
        payload = _normalize_plans_payload(data)
        _atomic_write_bytes(shared_dir / "plans.json", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        restored_files.append("Shared/plans.json")

    if not restored_files:
        raise ValueError(
            _adm_t('ub_lit_73bc194dad87')
        )

    return {
        "mode": "json",
        "restored_files": restored_files,
        "receipts_count": 0,
    }


def _restore_backup_file(backup_file: Path) -> Dict[str, Any]:
    suffix = backup_file.suffix.lower().strip()
    if suffix == ".zip":
        result = _restore_from_zip_backup(backup_file)
    elif suffix == ".json":
        result = _restore_from_json_backup(backup_file)
    else:
        raise ValueError(_adm_t('ub_lit_875bb3f45c68'))

    try:
        userbot_db.init_db()
    except Exception as e:
        logger.warning("Post-restore init_db warning: %s", e)

    return result


def _build_restore_result_text(result: Dict[str, Any]) -> str:
    restored_files = result.get("restored_files") or []
    receipts_count = int(result.get("receipts_count") or 0)
    mode = str(result.get("mode") or "").strip().upper() or "UNKNOWN"
    legacy_stats = result.get("legacy_stats") or {}
    lines = [
        _adm_t('ub_lit_9b109d98b35d'),
        f"{_adm_t('ub_lit_f1b32b8341f6')}{mode}",
        f"{_adm_t('ub_lit_08381a0c2d53')}{len(restored_files)}",
    ]
    if receipts_count > 0:
        lines.append(f"{_adm_t('ub_lit_6dc6ee580e61')}{receipts_count}")
    if restored_files:
        lines.append(_adm_t('ub_lit_4dd6e1fe542d'))
        for item in restored_files:
            lines.append(f"• {item}")
    if isinstance(legacy_stats, dict) and legacy_stats:
        lines.append(_adm_t('ub_lit_d7424e30ceec'))
        labels = [
            ("users", _adm_t('ub_lit_b0ab872b4a12')),
            ("orders", _adm_t('ub_lit_c6428e10990c')),
            ("payments", _adm_t('ub_lit_4ad10a7f11aa')),
            ("services", _adm_t('ub_lit_7dce24b8e835')),
            ("tickets", _adm_t('ub_lit_5a4f77d7b719')),
            ("ticket_messages", _adm_t('ub_lit_58029e6e5a8f')),
        ]
        for key, title in labels:
            if key in legacy_stats:
                try:
                    val = int(legacy_stats.get(key) or 0)
                except Exception:
                    val = 0
                lines.append(f"• {title}: {val}")
    return "\n".join(lines)


def _make_bot_backup_zip() -> Path:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ts = now.strftime("%d-%m-%Y_%H-%M-%S")
    backup_dir = _backup_storage_dir()
    out_path = backup_dir / f"Backup_Bot_{ts}.zip"
    if out_path.exists():
        suffix = 1
        while True:
            candidate = backup_dir / f"Backup_Bot_{ts}_{suffix}.zip"
            if not candidate.exists():
                out_path = candidate
                break
            suffix += 1
    root_dir = _project_root_dir()
    manifest_name = f"Backup_Bot_{ts}.json"

    files_to_add: List[Tuple[Path, str]] = [
        (root_dir / "Shared" / "hiddify_sellbot.db", "Shared/hiddify_sellbot.db"),
        # Backward compatibility with older installs/backups
        (root_dir / "Shared" / "userbot.db", "Shared/userbot.db"),
        (root_dir / "Shared" / "servers.json", "Shared/servers.json"),
        (root_dir / "Shared" / "plans.json", "Shared/plans.json"),
        # v4.0.0: Agency + Customer + Agent databases
        (root_dir / "Shared" / "agency.db", "Shared/agency.db"),
        (root_dir / "customer_bot.db", "customer_bot.db"),
        (root_dir / "AgentBot" / "agent_bot.db", "AgentBot/agent_bot.db"),
    ]

    added: List[Dict[str, Any]] = []
    snapshot_dir = Path(tempfile.mkdtemp(prefix="sellbot_backup_sqlite_"))
    try:
        with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for index, (src, arcname) in enumerate(files_to_add):
                if not src.exists() or not src.is_file():
                    continue
                archive_src = src
                if src.suffix.lower() == ".db":
                    archive_src = snapshot_dir / f"{index}_{src.name}"
                    _create_sqlite_backup_snapshot(src, archive_src)
                zf.write(archive_src, arcname=arcname)
                try:
                    fsize = int(archive_src.stat().st_size)
                except Exception:
                    fsize = 0
                added.append({"path": arcname, "size": fsize})

            receipts_dir = root_dir / "Receiptions"
            if receipts_dir.exists() and receipts_dir.is_dir():
                for item in receipts_dir.rglob("*"):
                    if not item.is_file():
                        continue
                    arc = str(item.relative_to(root_dir))
                    zf.write(item, arcname=arc)
                    try:
                        fsize = int(item.stat().st_size)
                    except Exception:
                        fsize = 0
                    added.append({"path": arc, "size": fsize})

            manifest = {
                "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "backup_type": "bot",
                "files_count": len(added),
                "files": added,
            }
            zf.writestr(manifest_name, json.dumps(manifest, ensure_ascii=False, indent=2))
    finally:
        shutil.rmtree(snapshot_dir, ignore_errors=True)

    try:
        out_path.chmod(0o600)
    except OSError:
        pass

    return out_path


def make_bot_backup_zip() -> Path:
    """
    Public wrapper for other admin modules.
    """
    return _make_bot_backup_zip()


def prune_full_backup_files(max_keep: int = 50) -> int:
    keep = max(1, int(max_keep or 50))
    backup_dir = _backup_storage_dir()
    files = sorted(
        backup_dir.glob("Backup_All_*.zip"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
        reverse=True,
    )
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink(missing_ok=True)
            removed += 1
        except Exception:
            pass
    return removed


def _build_full_backup_zip(
    bot_backup_path: Path,
    panel_backups: List[Dict[str, Any]],
    panel_errors: List[str],
) -> Path:
    ts = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")
    out_path = _backup_storage_dir() / f"Backup_All_{ts}.zip"

    if out_path.exists():
        idx = 1
        while True:
            candidate = _backup_storage_dir() / f"Backup_All_{ts}_{idx}.zip"
            if not candidate.exists():
                out_path = candidate
                break
            idx += 1

    used_names: set[str] = set()
    with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as out_zip:
        # Preserve bot backup structure for restore compatibility.
        with zipfile.ZipFile(bot_backup_path, mode="r") as bot_zip:
            for info in bot_zip.infolist():
                if info.is_dir():
                    continue
                out_zip.writestr(info.filename, bot_zip.read(info.filename))

        for item in panel_backups:
            server_id = int(item.get("server_id") or 0)
            server_name = _safe_backup_name(str(item.get("server_title") or f"server-{server_id}"), default=f"server-{server_id}")
            filename = _safe_backup_name(str(item.get("filename") or f"server-{server_id}.json"))

            base_arc = f"PanelBackups/{server_name}/{filename}"
            arcname = base_arc
            suffix = 1
            while arcname in used_names:
                stem, dot, ext = filename.rpartition(".")
                stem = stem or filename
                ext = f".{ext}" if dot else ""
                arcname = f"PanelBackups/{server_name}/{stem}_{suffix}{ext}"
                suffix += 1

            used_names.add(arcname)
            out_zip.writestr(arcname, item.get("content") or b"")

        manifest = {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "backup_type": "full",
            "bot_backup_file": bot_backup_path.name,
            "panel_backups_count": len(panel_backups),
            "panel_errors_count": len(panel_errors),
            "panel_backups": [
                {
                    "server_id": int(i.get("server_id") or 0),
                    "server_title": str(i.get("server_title") or ""),
                    "filename": str(i.get("filename") or ""),
                    "source_url": str(i.get("source_url") or ""),
                    "size": len(i.get("content") or b""),
                }
                for i in panel_backups
            ],
            "panel_errors": panel_errors,
        }
        out_zip.writestr(
            f"Backup_All_{ts}.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    try:
        out_path.chmod(0o600)
    except OSError:
        pass

    return out_path


async def _collect_panel_backups(
    only_xui: bool = False,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    servers = database.get_servers() or []
    if only_xui:
        servers = [s for s in servers if str(s.get("panel_type") or "").strip().lower() in {"xui", "x-ui"}]
    panel_backups: List[Dict[str, Any]] = []
    panel_errors: List[str] = []

    for server in servers:
        sid = int(server.get("id") or 0)
        stitle = str(server.get("title") or f"server-{sid}").strip() or f"server-{sid}"
        try:
            data = await hiddify_api.download_server_backup(server)
            panel_backups.append(
                {
                    "server_id": sid,
                    "server_title": stitle,
                    "filename": str(data.get("filename") or ""),
                    "content": data.get("content") or b"",
                    "source_url": str(data.get("source_url") or ""),
                }
            )
        except Exception as e:
            panel_errors.append(f"{stitle} (id={sid}): {e}")

    return panel_backups, panel_errors


async def _collect_xui_panel_backups() -> Tuple[List[Dict[str, Any]], List[str]]:
    return await _collect_panel_backups(only_xui=True)


async def _make_full_backup_zip(
    only_xui: bool = False,
) -> Tuple[Path, int, int, List[str]]:
    bot_backup_path = await asyncio.to_thread(_make_bot_backup_zip)
    try:
        panel_backups, panel_errors = await _collect_panel_backups(only_xui=only_xui)
        full_backup_path = await asyncio.to_thread(
            _build_full_backup_zip,
            bot_backup_path,
            panel_backups,
            panel_errors,
        )
        await asyncio.to_thread(prune_full_backup_files, 50)
        return full_backup_path, len(panel_backups), len(panel_errors), panel_errors
    finally:
        try:
            bot_backup_path.unlink(missing_ok=True)
        except Exception:
            pass


async def _make_xui_backup_zip() -> Tuple[Path, int, int, List[str]]:
    return await _make_full_backup_zip(only_xui=True)


def _should_report_auto_panel_backup_errors(context: ContextTypes.DEFAULT_TYPE, panel_err_count: int) -> bool:
    key = "_userbot_auto_backup_panel_error_streak"
    if panel_err_count <= 0:
        context.bot_data[key] = 0
        return False
    try:
        streak = int(context.bot_data.get(key) or 0) + 1
    except Exception:
        streak = 1
    context.bot_data[key] = streak
    return streak >= 2


async def run_userbot_auto_backup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _get_backup_restore_settings()
    if not bool(settings.get("auto_backup_enabled", True)):
        return

    now_local = datetime.now()
    # Allow 0-2 minute window to be resilient if job was delayed/hung at exact minute 0
    if now_local.minute not in {0, 1, 2} or now_local.hour not in {0, 6, 12, 18}:
        return

    # Use hour slot (not minute) so 18:00, 18:01, 18:02 all claim same slot
    slot_key = now_local.strftime("%Y-%m-%d %H:00")
    if str(context.bot_data.get("_userbot_auto_backup_slot") or "") == slot_key:
        return

    # Cross-process dedupe: if multiple AdminBot instances are running, only one should
    # claim and process this 6-hour slot.
    try:
        claimed = await asyncio.to_thread(userbot_db.claim_auto_backup_slot_once, slot_key)
    except Exception as e:
        logger.warning("Auto backup slot claim failed (fallback to memory guard): %s", e)
        claimed = True
    if not claimed:
        context.bot_data["_userbot_auto_backup_slot"] = slot_key
        return
    context.bot_data["_userbot_auto_backup_slot"] = slot_key

    admin_id = int(os.getenv("ADMIN_ID", "0") or "0")
    if admin_id <= 0:
        return

    try:
        backup_path, panel_ok_count, panel_err_count, panel_errors = await _make_full_backup_zip()
    except Exception as e:
        logger.warning("Auto full backup creation failed (will retry at next minute): %s", e)
        # Don't mark slot as done so 18:01/18:02 can retry
        return

    caption = (
        f"{_adm_t('ub_lit_c8ffdfa2fdfe')}{now_local.strftime('%Y-%m-%d %H:%M:%S')}{_adm_t('ub_lit_37bdced2ffa2')}{panel_ok_count}{_adm_t('ub_lit_1cb0523c4ddd')}{panel_err_count}{_adm_t('ub_lit_0806067ce85b')}"
    )

    try:
        with backup_path.open("rb") as fh:
            await context.bot.send_document(
                chat_id=admin_id,
                document=fh,
                filename=backup_path.name,
                caption=caption,
            )
    except Exception as e:
        logger.warning("Auto backup send to admin failed: %s", e)

    should_report_panel_errors = _should_report_auto_panel_backup_errors(context, panel_err_count)

    if panel_errors and should_report_panel_errors:
        preview = "\n".join(panel_errors[:5])
        extra = f"{_adm_t('ub_lit_6a765c44eb90')}{len(panel_errors) - 5}{_adm_t('ub_lit_315b0d6cee27')}" if len(panel_errors) > 5 else ""
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=_adm_t('ub_lit_0996fb054887') + preview + extra,
            )
        except Exception as e:
            logger.warning("Auto backup error report to admin failed: %s", e)
    elif panel_errors:
        logger.warning("Auto backup panel errors suppressed for transient failure: %s", panel_errors[:2])

    if bool(settings.get("event_channel_enabled", False)):
        target = str(settings.get("event_channel_id") or "").strip()
        if target:
            # Prevent duplicate sends if event channel is configured to the same chat as admin.
            if target == str(admin_id):
                target = ""
        if target:
            try:
                with backup_path.open("rb") as fh:
                    await context.bot.send_document(
                        chat_id=target,
                        document=fh,
                        filename=backup_path.name,
                        caption=caption,
                    )
            except Exception as e:
                logger.warning("Auto backup send to event channel failed: %s", e)
            if panel_errors and should_report_panel_errors:
                preview = "\n".join(panel_errors[:5])
                extra = f"{_adm_t('ub_lit_6a765c44eb90')}{len(panel_errors) - 5}{_adm_t('ub_lit_315b0d6cee27')}" if len(panel_errors) > 5 else ""
                try:
                    await context.bot.send_message(
                        chat_id=target,
                        text=_adm_t('ub_lit_0996fb054887') + preview + extra,
                    )
                except Exception as e:
                    logger.warning("Auto backup error report to event channel failed: %s", e)

    context.bot_data["_userbot_auto_backup_slot"] = slot_key

# ---------------------------------------------------------
# PART 2: SEND FUNCTIONS & DISPLAYS
# ---------------------------------------------------------

async def send_userbot_main_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    try:
        from Shared import userbot_db as _udb
        _lg = _udb.get_admin_language()
    except Exception:
        _lg = "fa"
    from Shared import i18n as _i18n
    text = (
        _i18n.t("adm_userbot_title", _lg) + "\n"
        + _i18n.t("adm_userbot_hint", _lg)
    )
    kb = build_userbot_main_menu()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_subscription_tracking_prompt(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    message=None,
) -> None:
    prompt = _subscription_tracking_prompt_text()
    if message:
        await message.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
    else:
        await context.bot.send_message(chat_id, prompt, reply_markup=userbot_cancel_keyboard())


async def send_subscription_tracking_detail(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    service: Dict[str, Any],
    message=None,
    edit: bool = False,
) -> None:
    service_view = dict(service or {})
    service_view["note_text"] = await _resolve_service_note_text(service_view)
    user_id = int(service_view.get("user_id") or 0)
    user = userbot_db.get_user_by_id(user_id) if user_id > 0 else {}
    detail_text = build_subscription_tracking_detail_text(user or {}, service_view)
    kb = build_subscription_tracking_keyboard(service_view)
    if message and edit:
        try:
            await message.edit_text(detail_text, reply_markup=kb)
            return
        except BadRequest:
            pass
    if message:
        await message.reply_text(detail_text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, detail_text, reply_markup=kb)


def _find_agent_service_by_code(service_code: str) -> Optional[Dict[str, Any]]:
    """جستجوی سرویس‌های نمایندگی (agency.db) با شناسه ۷ رقمی."""
    try:
        from Shared import agent_db as _agent_db
        svc = _agent_db.get_service_by_code(service_code)
        if svc:
            svc = dict(svc)
            svc["_source"] = "agent"
        return svc
    except Exception:
        return None


async def send_agent_service_tracking_detail(message, context, service: Dict[str, Any]) -> None:
    """نمایش جزئیات سرویس نمایندگی در ربات ادمین (جستجو با شناسه ۷ رقمی)."""
    from Shared import agent_db as _agent_db
    svc = dict(service or {})
    svc_code = ""
    for part in str(svc.get("comment") or "").split("|"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        if k.strip().lower() == "code":
            svc_code = str(v).strip()
            break
    agent = None
    if svc.get("agent_id"):
        try:
            agent = _agent_db.get_agent_by_id(int(svc["agent_id"]))
        except Exception:
            agent = None
    agent_label = ""
    if agent:
        agent_label = str(agent.get("full_name") or agent.get("username") or f"@{agent.get('telegram_id')}" or "")
    elif svc.get("agent_id"):
        agent_label = f"{_adm_t('ub_lit_d416fa44016d')}{svc.get('agent_id')}"
    text = (
        f"{_adm_t('ub_lit_dd6fe220562f')}{svc.get('name') or '—'}{_adm_t('ub_lit_0937d1718c21')}{html_escape(svc_code or '—')}{_adm_t('ub_lit_5ac851a18511')}{html_escape(agent_label or '—')}{_adm_t('ub_lit_f136270018c0')}{html_escape(str(svc.get('server_title') or '—'))}{_adm_t('ub_lit_b21917455747')}{svc.get('usage_limit') or 0}{_adm_t('ub_lit_ab26ad41f913')}{svc.get('usage_current') or 0}{_adm_t('ub_lit_cd7c42825a66')}{svc.get('days_left') or 0}{_adm_t('ub_lit_28fa328d388d')}{int(svc.get('sale_price') or 0):f','}{_adm_t('ub_lit_7f516e8e3dd8')}{int(svc.get('wholesale_price') or 0):f','}{_adm_t('ub_lit_7c1e128c33e0')}{svc.get('panel_user_uuid') or '—'}"
    )
    try:
        await message.reply_text(text, reply_markup=admin_main_keyboard(), parse_mode="HTML")
    except Exception:
        pass


# ===============================
#   بخش مدیریت تراکنشات (تکمیل شده)
# ===============================

async def send_payments_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_db8fa58a2fc1')
    kb = build_payments_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_payments_list(
    filter_type: str,  # 'approved', 'rejected', 'pending', 'card'
    page: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    if page < 1: page = 1

    # نگاشت فیلتر به پارامترهای دیتابیس
    status = None
    method = None
    header_title = ""

    if filter_type == 'approved':
        status = 'approved'
        header_title = _adm_t('ub_lit_fb35b4e24fbb')
    elif filter_type == 'rejected':
        status = 'rejected'
        header_title = _adm_t('ub_lit_e254bbde58ac')
    elif filter_type == 'pending':
        status = 'pending'
        header_title = _adm_t('ub_lit_ae42e2d73ef4')
    elif filter_type == 'card':
        method = 'card'
        header_title = _adm_t('ub_lit_c3ff8c18d61e')

    # 1. دریافت آمار (هدر)
    stats = userbot_db.get_payment_stats(status, method)

    # 2. دریافت لیست صفحه‌بندی شده (فقط برای دکمه‌ها)
    payments = userbot_db.get_payments_list_paginated(status, method, page, USERBOT_PAGE_SIZE)

    # محاسبه صفحات
    total_count = stats['total_count']
    total_pages = max(1, math.ceil(total_count / USERBOT_PAGE_SIZE))
    if page > total_pages: page = total_pages

    # ساخت متن با جداکننده‌ها
    text = (
        f"🔹 {header_title}{_adm_t('ub_lit_e6a4fd0222da')}{stats['total_count']}{_adm_t('ub_lit_9dc0422feb4d')}{_format_toman(stats['total_amount'])}{_adm_t('ub_lit_ccde1f8ca008')}{stats['last30_count']}{_adm_t('ub_lit_4cf4dd8b0dfc')}{_format_toman(stats['last30_amount'])}{_adm_t('ub_lit_71bf540116a3')}{stats['month_count']}{_adm_t('ub_lit_3160570278d0')}{_format_toman(stats['month_amount'])}{_adm_t('ub_lit_f6ac3483a71a')}"
    )

    # ساخت دکمه‌ها (گرید 3 ستونه)
    rows = []
    current_row = []
    for p in payments:
        label = str(p['id'])
        current_row.append(
            InlineKeyboardButton(label, callback_data=f"userbot:pay:detail:{p['id']}")
        )
        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    # نویگیشن
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f"userbot:payments:list:{filter_type}:{page-1}"))
    
    # دکمه وسط (نمایش صفحه)
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="userbot:noop"))

    if page < total_pages:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"userbot:payments:list:{filter_type}:{page+1}"))
    
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton(_adm_t("adm_btn_back_no_space"), callback_data="userbot:payments_menu")])

    kb = InlineKeyboardMarkup(rows)

    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_payment_detail(
    payment_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
    *,
    force_text_only: bool = False,
) -> None:
    pay = userbot_db.get_payment_by_id(payment_id)
    if not pay:
        await context.bot.send_message(chat_id, _adm_t("ub_payment_not_found"))
        return

    st = pay.get('status', 'pending')
    amount = _format_toman(pay.get('amount') or 0)
    uid = pay.get('user_id')
    receipt_raw = pay.get('receipt_image') or ""
    receipt_meta = _parse_receipt_meta(receipt_raw)
    receipt_admin_fid = receipt_meta.get("admin_fid")
    receipt_local_path = receipt_meta.get("local_path")
    receipt_legacy = receipt_raw if receipt_raw and ":" not in receipt_raw and "|" not in receipt_raw else ""

    caption = _build_payment_detail_text(pay)

    user_btn_title = (pay.get('full_name') or pay.get('username') or str(uid)).strip()
    kb = _build_payment_action_keyboard(payment_id, user_btn_title, uid, status=str(pay.get('status') or ""))

    # اگر قرار است متن بدون عکس نمایش داده شود و پیام فعلی عکس‌دار است،
    # پیام جدید می‌فرستیم و قبلی را حذف می‌کنیم تا چت شلوغ نشود.
    if force_text_only and message and getattr(message, "photo", None):
        await context.bot.send_message(chat_id, caption, reply_markup=kb)
        try:
            await message.delete()
        except Exception:
            pass
        return

    # ارسال (اگر عکس دارد با عکس، اگر نه متن)
    receipt_to_send = receipt_admin_fid or receipt_legacy
    if receipt_to_send and not force_text_only:
        if message:
            try:
                await message.edit_caption(caption=caption, reply_markup=kb)
                return
            except Exception:
                # اگر پیام قابل ویرایش نبود (یا کپشن نداشت)، جایگزین می‌کنیم
                try:
                    await context.bot.send_photo(chat_id, receipt_to_send, caption=caption, reply_markup=kb)
                finally:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                return

        try:
            await context.bot.send_photo(chat_id, receipt_to_send, caption=caption, reply_markup=kb)
        except Exception:
            # اگر فایل آیدی عکس نامعتبر بود، از فایل محلی استفاده می‌کنیم
            if receipt_local_path and os.path.exists(receipt_local_path):
                try:
                    with open(receipt_local_path, "rb") as f:
                        await context.bot.send_photo(chat_id, f, caption=caption, reply_markup=kb)
                    return
                except Exception:
                    pass
            await context.bot.send_message(chat_id, caption + _adm_t('ub_lit_ee8b68ab1d83'), reply_markup=kb)
    elif receipt_local_path and os.path.exists(receipt_local_path) and not force_text_only:
        try:
            with open(receipt_local_path, "rb") as f:
                await context.bot.send_photo(chat_id, f, caption=caption, reply_markup=kb)
            return
        except Exception:
            await context.bot.send_message(chat_id, caption + _adm_t('ub_lit_f972bd5eaf56'), reply_markup=kb)
    else:
        if message:
            try:
                await message.edit_text(caption, reply_markup=kb)
            except BadRequest:
                await context.bot.send_message(chat_id, caption, reply_markup=kb)
        else:
            await context.bot.send_message(chat_id, caption, reply_markup=kb)


# ===============================
#   بخش مدیریت کاربران
# ===============================

async def send_users_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_e446c2832f7b')
    kb = build_users_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_users_page(page: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    if page < 1: page = 1
    users, total_count = userbot_db.get_users_page(page, USERBOT_PAGE_SIZE)
    total_pages = max(1, math.ceil(total_count / USERBOT_PAGE_SIZE))
    if page > total_pages: page = total_pages; users, _ = userbot_db.get_users_page(page, USERBOT_PAGE_SIZE)

    lines = [
        _adm_t('ub_lit_5d7e8874bb47'),
        f"{_adm_t('ub_lit_dc659b6ef7d6')}{total_count}",
        f"{_adm_t('ub_lit_38a59cdd248c')}{page}/{total_pages}",
        ""
    ]
    rows = []
    row = []
    for u in users:
        label = f"🔵 {_display_name(u)}"
        row.append(InlineKeyboardButton(label, callback_data=f"userbot:user:{u['id']}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"userbot:users:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="userbot:noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"userbot:users:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:users_menu")])

    kb = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_user_profile(
    user_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
    back_callback: str = "userbot:users_menu",
) -> None:
    user = userbot_db.get_user_by_id(user_id)
    if not user:
        await context.bot.send_message(chat_id, _adm_t('ub_lit_c940c4b3979b'))
        return

    stats = userbot_db.get_full_user_stats(user_id)
    wallet = _format_toman(user.get("wallet_balance", 0))
    
    # چک کردن وضعیت بن و تست
    is_banned = user.get('is_banned', 0)
    got_trial = user.get('got_free_trial', 0)
    
    trial_icon = "✅" if got_trial else _adm_t('ub_lit_6e820d8c2bbb')
    ban_status = _adm_t('ub_lit_d31f21153610') if is_banned else _adm_t('ub_lit_0c90fe92316c')

    # متن پیام طبق عکس
    text = (
        f"{_adm_t('ub_lit_9a3903dac8b0')}{_display_name(user)}{_adm_t('ub_lit_82d8992ae2cf')}{user.get('username', '-')}{_adm_t('ub_lit_79f266413e33')}{user['telegram_id']}{_adm_t('ub_lit_298b95c83074')}{trial_icon}{_adm_t('ub_lit_f7ac0f0f591c')}{wallet}{_adm_t('ub_lit_47dd851a531e')}{ban_status}{_adm_t('ub_lit_ad0d1cc2cf24')}{stats['subs_bought']}{_adm_t('ub_lit_ad3814570384')}{stats['subs_connected']}{_adm_t('ub_lit_e6a4fd0222da')}{stats['tx_total']}{_adm_t('ub_lit_d5ea26a72b8f')}{stats['tx_approved']}{_adm_t('ub_lit_ac8fb63ca4b3')}{stats['orders_count']}{_adm_t('ub_lit_ccabb6312338')}{stats['orders_gb']}{_adm_t('ub_lit_10863b8ea7a1')}{_format_toman(stats['orders_price'])}{_adm_t('ub_lit_9e29f6087438')}"
    )
    
    kb = build_user_profile_keyboard(user_id, back_callback=back_callback)
    if message:
        try: await message.edit_text(text, reply_markup=kb)
        except BadRequest: await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)

# ================== HANDLERS برای ویزاردهای جدید ==================

async def handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت ورودی‌های متنی ادمین (کیف پول، پیام و...)"""
    msg = update.message
    text = (msg.text or "").strip()

    if context.user_data.get(PAYMENT_SEARCH_STATE):
        await handle_payment_search_input(update, context)
        return

    if context.user_data.get(ORDERS_SEARCH_STATE_KEY):
        await handle_orders_search_input(update, context)
        return

    if context.user_data.get(SUB_TRACKING_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(SUB_TRACKING_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            return

        sub_code = str(text or "").strip()
        if not sub_code:
            await msg.reply_text(_subscription_tracking_prompt_text(), reply_markup=userbot_cancel_keyboard())
            return

        service = userbot_db.get_service_by_code(sub_code)
        if not service:
            service = _find_agent_service_by_code(sub_code)
            if not service:
                await msg.reply_text(_adm_t('ub_lit_694c899bddd8'))
                await msg.reply_text(_subscription_tracking_prompt_text(), reply_markup=userbot_cancel_keyboard())
                return

        context.user_data.pop(SUB_TRACKING_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_4090f864d3eb'), reply_markup=admin_main_keyboard())
        if str(service.get("_source") or "") == "agent":
            await send_agent_service_tracking_detail(msg, context, service)
        else:
            await send_subscription_tracking_detail(
                msg.chat_id,
                context,
                service=service,
                message=msg,
            )
        return

    if context.user_data.get(BACKUP_RESTORE_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(BACKUP_RESTORE_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            return

        doc = getattr(msg, "document", None)
        if not doc:
            await msg.reply_text(
                _adm_t('ub_lit_50c69b95b9bd'),
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        file_name = str(getattr(doc, "file_name", "") or "").strip()
        low_name = file_name.lower()
        if not (low_name.endswith(".zip") or low_name.endswith(".json")):
            await msg.reply_text(
                _adm_t('ub_lit_399bdd6fe0c1'),
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        try:
            fobj = await context.bot.get_file(doc.file_id)
            base_name = Path(file_name).name if file_name else ""
            if not base_name:
                base_name = f"backup_restore_{int(datetime.now(timezone.utc).timestamp())}.bin"
            save_name = f"restore_{int(datetime.now(timezone.utc).timestamp())}_{base_name}"
            save_path = Path(tempfile.gettempdir()) / save_name
            await fobj.download_to_drive(custom_path=str(save_path))
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_07319186c9a2')}{e}", reply_markup=userbot_cancel_keyboard())
            return

        context.user_data.pop(BACKUP_RESTORE_STATE, None)
        if BACKUP_RESTORE_LOCK.locked():
            try:
                save_path.unlink(missing_ok=True)
            except Exception:
                pass
            await msg.reply_text(_adm_t('ub_lit_bb0b135e4377'), reply_markup=admin_main_keyboard())
            await send_backup_restore_settings_menu(msg.chat_id, context)
            return

        await msg.reply_text(_adm_t('ub_lit_5d49d7ecc0d2'), reply_markup=admin_main_keyboard())
        try:
            async with BACKUP_RESTORE_LOCK:
                result = await asyncio.to_thread(_restore_backup_file, save_path)
        except Exception as e:
            logger.exception("Backup restore failed: %s", e)
            await msg.reply_text(f"{_adm_t('ub_lit_b05c1a719494')}{e}", reply_markup=admin_main_keyboard())
            await send_backup_restore_settings_menu(msg.chat_id, context)
            return
        finally:
            try:
                save_path.unlink(missing_ok=True)
            except Exception:
                pass

        await msg.reply_text(_build_restore_result_text(result), reply_markup=admin_main_keyboard())

        settings = _get_backup_restore_settings()
        if bool(settings.get("event_channel_enabled", False)):
            target = str(settings.get("event_channel_id") or "").strip()
            if target:
                try:
                    await context.bot.send_message(
                        chat_id=target,
                        text=(
                            f"{_adm_t('ub_lit_c72dce7e257f')}{msg.from_user.id if msg.from_user else '-'}{_adm_t('ub_lit_fc8d6845db5e')}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        ),
                    )
                except Exception as e:
                    logger.warning("Send restore event to channel failed: %s", e)

        await send_backup_restore_settings_menu(msg.chat_id, context)
        return

    if context.user_data.get(BACKUP_CHANNEL_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(BACKUP_CHANNEL_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_backup_restore_settings_menu(msg.chat_id, context)
            return

        channel_target = ""
        try:
            fchat = getattr(msg, "forward_from_chat", None)
            if fchat and str(getattr(fchat, "type", "")) in {"channel", "supergroup"}:
                uname = str(getattr(fchat, "username", "") or "").strip()
                cid = str(getattr(fchat, "id", "") or "").strip()
                channel_target = f"@{uname}" if uname else cid
        except Exception:
            pass
        if not channel_target:
            raw = text.strip()
            if raw.startswith("@") and len(raw) > 1:
                channel_target = raw
            elif raw.lstrip("-").isdigit():
                channel_target = raw
            else:
                await msg.reply_text(
                    _adm_t('ub_lit_599d5c19e2a2'),
                    reply_markup=userbot_cancel_keyboard(),
                )
                return

        try:
            settings = _get_backup_restore_settings()
            settings["event_channel_id"] = channel_target
            userbot_db.set_backup_restore_settings(settings)
        except Exception as e:
            context.user_data.pop(BACKUP_CHANNEL_EDIT_STATE, None)
            await msg.reply_text(f"{_adm_t('ub_lit_fb6b9ccfa1f2')}{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(BACKUP_CHANNEL_EDIT_STATE, None)
        await msg.reply_text(f"{_adm_t('ub_lit_4bf85bbf9e3d')}{channel_target}", reply_markup=admin_main_keyboard())
        await send_backup_restore_settings_menu(msg.chat_id, context)
        return

    if context.user_data.get(BROADCAST_SEND_STATE):
        st = context.user_data.get(BROADCAST_SEND_STATE)
        if not isinstance(st, dict):
            context.user_data.pop(BROADCAST_SEND_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_610ffd0c3c21'), reply_markup=admin_main_keyboard())
            return

        segment = str(st.get("segment") or "all").strip().lower()
        step = str(st.get("step") or "wait_text").strip().lower()

        if text in CANCEL_WORDS:
            context.user_data.pop(BROADCAST_SEND_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            return

        if step == "wait_text":
            body_text = str(text or "").strip()
            if not body_text:
                await msg.reply_text(
                    _adm_t('ub_lit_0cf8c6e42c06'),
                    reply_markup=userbot_cancel_keyboard(),
                )
                return
            st["text"] = body_text
            st["step"] = "wait_photo"
            context.user_data[BROADCAST_SEND_STATE] = st
            await msg.reply_text(
                _adm_t('ub_lit_d1ab4a9c9a1d'),
                reply_markup=broadcast_skip_cancel_keyboard(),
            )
            return

        if step == "wait_photo":
            photo_file_id = ""
            if getattr(msg, "photo", None):
                photo_file_id = msg.photo[-1].file_id
            elif _is_ticket_reply_skip_text(text):
                photo_file_id = ""
            else:
                await msg.reply_text(
                    _adm_t('ub_lit_4ca4944eb907'),
                    reply_markup=broadcast_skip_cancel_keyboard(),
                )
                return

            body_text = str(st.get("text") or "").strip()
            if not body_text:
                st["step"] = "wait_text"
                context.user_data[BROADCAST_SEND_STATE] = st
                await msg.reply_text(_adm_t('ub_lit_cb6daf64f796'), reply_markup=userbot_cancel_keyboard())
                return

            target_ids = userbot_db.get_broadcast_target_telegram_ids(segment)
            try:
                await _send_broadcast_to_targets(context, target_ids, body_text, photo_file_id)
            except Exception as e:
                await msg.reply_text(f"{_adm_t('ub_lit_049a7c1fad77')}{e}", reply_markup=admin_main_keyboard())
                context.user_data.pop(BROADCAST_SEND_STATE, None)
                return

            context.user_data.pop(BROADCAST_SEND_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_d98f9f5fa5e7'), reply_markup=admin_main_keyboard())
            return

        # fallback
        st["step"] = "wait_text"
        context.user_data[BROADCAST_SEND_STATE] = st
        await msg.reply_text(
            f"{_adm_t('ub_lit_16a2b34bd26a')}{_broadcast_segment_label(segment)}{_adm_t('ub_lit_6e15e865cb28')}",
            reply_markup=userbot_cancel_keyboard(),
        )
        return

    if context.user_data.get(TICKET_REPLY_STATE):
        st = context.user_data.get(TICKET_REPLY_STATE)
        if not isinstance(st, dict):
            context.user_data.pop(TICKET_REPLY_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_e67bbca145fd'), reply_markup=admin_main_keyboard())
            return

        ticket_code = int(st.get("ticket_code") or 0)
        list_status = str(st.get("list_status") or "pending").strip().lower()
        page = max(1, int(st.get("page") or 1))
        from_user_id = int(st.get("from_user_id") or 0)
        step = str(st.get("step") or "wait_text").strip().lower()

        if text in CANCEL_WORDS:
            context.user_data.pop(TICKET_REPLY_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_ticket_detail(
                ticket_code,
                msg.chat_id,
                context,
                list_status=list_status,
                page=page,
                from_user_id=from_user_id,
            )
            return

        ticket = userbot_db.get_ticket_by_code(ticket_code)
        if not ticket:
            context.user_data.pop(TICKET_REPLY_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_6b02b8271e76'), reply_markup=admin_main_keyboard())
            return

        if step == "wait_text":
            body_text = str(text or "").strip()
            if not body_text:
                await msg.reply_text(_adm_t('ub_lit_9c6851bd6e22'), reply_markup=userbot_cancel_keyboard())
                return
            st["reply_text"] = body_text
            st["photo_file_id"] = ""
            st["step"] = "wait_screenshot"
            context.user_data[TICKET_REPLY_STATE] = st
            await msg.reply_text(
                _adm_t('ub_lit_febf12ffd68d'),
                reply_markup=build_ticket_reply_screenshot_keyboard(),
            )
            return

        if step == "wait_screenshot":
            photo_file_id = ""
            if getattr(msg, "photo", None):
                photo_file_id = msg.photo[-1].file_id
            elif _is_ticket_reply_skip_text(text):
                photo_file_id = ""
            else:
                await msg.reply_text(
                    _adm_t('ub_lit_4ca4944eb907'),
                    reply_markup=build_ticket_reply_screenshot_keyboard(),
                )
                return

            st["photo_file_id"] = photo_file_id
            st["step"] = "wait_confirm"
            context.user_data[TICKET_REPLY_STATE] = st
            preview_text = _build_ticket_reply_preview_text(
                str(st.get("reply_text") or ""),
                bool(photo_file_id),
            )
            if photo_file_id:
                try:
                    await context.bot.send_photo(
                        chat_id=msg.chat_id,
                        photo=photo_file_id,
                        caption=preview_text,
                        reply_markup=build_ticket_reply_confirm_keyboard(),
                    )
                except Exception:
                    await msg.reply_text(preview_text, reply_markup=build_ticket_reply_confirm_keyboard())
            else:
                await msg.reply_text(preview_text, reply_markup=build_ticket_reply_confirm_keyboard())
            return

        if step == "wait_confirm":
            await msg.reply_text(
                _adm_t('ub_lit_474338f5faed'),
                reply_markup=build_ticket_reply_confirm_keyboard(),
            )
            return
        # fallback
        st["step"] = "wait_text"
        context.user_data[TICKET_REPLY_STATE] = st
        await msg.reply_text(_adm_t('ub_lit_b36f472ee726'), reply_markup=userbot_cancel_keyboard())
        return

    if context.user_data.get(PAYMENT_CARD_ADD_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(PAYMENT_CARD_ADD_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_payment_cards_list_menu(msg.chat_id, context)
            return
        add_state = context.user_data.get(PAYMENT_CARD_ADD_STATE)
        if not isinstance(add_state, dict):
            add_state = {"step": "number"}
            context.user_data[PAYMENT_CARD_ADD_STATE] = add_state

        step = str(add_state.get("step") or "number").strip().lower()
        if step == "number":
            number = re.sub(r"\D", "", text)
            if len(number) != 16:
                await msg.reply_text(_adm_t('ub_lit_35660c5728c1'), reply_markup=userbot_cancel_keyboard())
                return
            add_state["step"] = "owner"
            add_state["number"] = number
            context.user_data[PAYMENT_CARD_ADD_STATE] = add_state
            await msg.reply_text(_adm_t('ub_lit_73dc47115c7b'), reply_markup=userbot_cancel_keyboard())
            return

        if step == "owner":
            owner = text.strip()
            number = re.sub(r"\D", "", str(add_state.get("number") or ""))
            if not owner:
                await msg.reply_text(_adm_t('ub_lit_74021069a830'), reply_markup=userbot_cancel_keyboard())
                return
            if len(number) != 16:
                context.user_data[PAYMENT_CARD_ADD_STATE] = {"step": "number"}
                await msg.reply_text(_adm_t('ub_lit_a681baa1c845'), reply_markup=userbot_cancel_keyboard())
                return
            add_state["step"] = "bank"
            add_state["owner"] = owner
            context.user_data[PAYMENT_CARD_ADD_STATE] = add_state
            await msg.reply_text(
                _adm_t('ub_lit_456f2348f2c9'),
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        if step == "bank":
            number = re.sub(r"\D", "", str(add_state.get("number") or ""))
            owner = str(add_state.get("owner") or "").strip()
            bank = text.strip()
            if bank == "0":
                bank = ""
            if len(number) != 16:
                context.user_data[PAYMENT_CARD_ADD_STATE] = {"step": "number"}
                await msg.reply_text(_adm_t('ub_lit_a681baa1c845'), reply_markup=userbot_cancel_keyboard())
                return
            if not owner:
                context.user_data[PAYMENT_CARD_ADD_STATE] = {"step": "owner", "number": number}
                await msg.reply_text(_adm_t('ub_lit_b11dd69cfe9f'), reply_markup=userbot_cancel_keyboard())
                return
            try:
                database.add_or_update_card(owner=owner, number=number, bank_name=bank)
            except Exception as e:
                await msg.reply_text(f"{_adm_t('ub_lit_4b0e35409dbd')}{e}", reply_markup=userbot_cancel_keyboard())
                return
            context.user_data.pop(PAYMENT_CARD_ADD_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_2e1937659375'), reply_markup=admin_main_keyboard())
            await send_payment_card_item_menu(msg.chat_id, context, number=number)
            return

        context.user_data[PAYMENT_CARD_ADD_STATE] = {"step": "number"}
        await msg.reply_text(_adm_t('ub_lit_3d68f9744af8'), reply_markup=userbot_cancel_keyboard())
        return

    if context.user_data.get(PAYMENT_CARD_EDIT_STATE):
        edit_state = context.user_data.get(PAYMENT_CARD_EDIT_STATE)
        if not isinstance(edit_state, dict):
            context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_162e59238e3a'), reply_markup=admin_main_keyboard())
            await send_payment_cards_list_menu(msg.chat_id, context)
            return

        target_number = re.sub(r"\D", "", str(edit_state.get("number") or ""))
        mode = str(edit_state.get("mode") or "").strip().lower()

        if text in CANCEL_WORDS:
            context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            if target_number:
                await send_payment_card_item_menu(msg.chat_id, context, number=target_number)
            else:
                await send_payment_cards_list_menu(msg.chat_id, context)
            return

        if mode == "number":
            new_number = re.sub(r"\D", "", text)
            if len(new_number) != 16:
                await msg.reply_text(_adm_t('ub_lit_f398aa65e2f8'), reply_markup=userbot_cancel_keyboard())
                return
            if new_number != target_number and database.get_card(new_number):
                await msg.reply_text(_adm_t('ub_lit_204b37404152'), reply_markup=userbot_cancel_keyboard())
                return
            ok = database.update_card_number(target_number, new_number)
            if not ok:
                context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
                await msg.reply_text(_adm_t('ub_lit_f990ba573889'), reply_markup=admin_main_keyboard())
                await send_payment_cards_list_menu(msg.chat_id, context)
                return
            context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_7298fac36975'), reply_markup=admin_main_keyboard())
            await send_payment_card_item_menu(msg.chat_id, context, number=new_number)
            return

        if mode == "owner":
            new_owner = text.strip()
            if not new_owner:
                await msg.reply_text(_adm_t('ub_lit_0e6506afda04'), reply_markup=userbot_cancel_keyboard())
                return
            ok = database.update_card_owner(target_number, new_owner)
            if not ok:
                context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
                await msg.reply_text(_adm_t('ub_lit_f990ba573889'), reply_markup=admin_main_keyboard())
                await send_payment_cards_list_menu(msg.chat_id, context)
                return
            context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_ef8440175cbb'), reply_markup=admin_main_keyboard())
            await send_payment_card_item_menu(msg.chat_id, context, number=target_number)
            return

        context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_877e28d82974'), reply_markup=admin_main_keyboard())
        await send_payment_cards_list_menu(msg.chat_id, context)
        return

    if context.user_data.get(PAYMENT_CARD_DELETE_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(PAYMENT_CARD_DELETE_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_payment_cards_list_menu(msg.chat_id, context)
            return
        number = re.sub(r"\D", "", text)
        if len(number) < 16:
            await msg.reply_text(_adm_t('ub_lit_410801d2f0ef'), reply_markup=userbot_cancel_keyboard())
            return
        ok = False
        try:
            ok = database.delete_card(number)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_573fa548ec88')}{e}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(PAYMENT_CARD_DELETE_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_aab1f54f4333') if ok else _adm_t('ub_lit_a40143a344e2'), reply_markup=admin_main_keyboard())
        await send_payment_cards_list_menu(msg.chat_id, context)
        return

    if context.user_data.get(ZARIN_COUPON_BULK_STATE):
        bulk_state = context.user_data.get(ZARIN_COUPON_BULK_STATE)
        if text in CANCEL_WORDS:
            context.user_data.pop(ZARIN_COUPON_BULK_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_e20a14ad242e'), reply_markup=admin_main_keyboard())
            await send_gifts_menu(msg.chat_id, context)
            return
        if not isinstance(bulk_state, dict):
            bulk_state = {"step": "prefix"}
            context.user_data[ZARIN_COUPON_BULK_STATE] = bulk_state
        step = str(bulk_state.get("step") or "prefix").strip().lower()
        if step == "prefix":
            prefix = str(text or "").strip().upper()
            if not re.fullmatch(r"[A-Za-z0-9_-]{2,24}", prefix):
                await msg.reply_text(
                    _adm_t('ub_lit_1ba71caae945'),
                    reply_markup=userbot_cancel_keyboard(),
                    parse_mode="Markdown",
                )
                return
            bulk_state.update({"step": "count", "prefix": prefix})
            context.user_data[ZARIN_COUPON_BULK_STATE] = bulk_state
            await msg.reply_text(_adm_t('ub_lit_57b2f72d1579'), reply_markup=userbot_cancel_keyboard())
            return
        if step == "count":
            try:
                count = int(str(text).replace(",", ""))
                if count <= 0 or count > 200:
                    raise ValueError
            except Exception:
                await msg.reply_text(_adm_t('ub_lit_4e55347762ca'), reply_markup=userbot_cancel_keyboard())
                return
            bulk_state.update({"step": "amount", "count": count})
            context.user_data[ZARIN_COUPON_BULK_STATE] = bulk_state
            await msg.reply_text(_adm_t('ub_lit_02084124c5ef'), reply_markup=userbot_cancel_keyboard())
            return
        if step == "amount":
            try:
                amount = int(str(text).replace(",", ""))
                if amount <= 0:
                    raise ValueError
            except Exception:
                await msg.reply_text(_adm_t('ub_lit_abc8ee33bc88'), reply_markup=userbot_cancel_keyboard())
                return
            bulk_state.update({"step": "expire", "amount": amount})
            context.user_data[ZARIN_COUPON_BULK_STATE] = bulk_state
            await msg.reply_text(
                _adm_t('ub_lit_f00926cd6a8e'),
                reply_markup=userbot_cancel_keyboard(),
            )
            return
        if step == "expire":
            try:
                hours = int(str(text).replace(",", ""))
                if hours < 0:
                    raise ValueError
            except Exception:
                await msg.reply_text(_adm_t('ub_lit_d635df7582c1'), reply_markup=userbot_cancel_keyboard())
                return
            prefix = str(bulk_state.get("prefix") or "GIFT").strip()
            count = max(1, min(200, int(bulk_state.get("count") or 1)))
            amount = max(1, int(bulk_state.get("amount") or 1))
            expires_at = ""
            if hours > 0:
                expires_at = (
                    datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=hours)
                ).strftime("%Y-%m-%d %H:%M:%S")
            codes: List[str] = []
            try:
                for _ in range(count):
                    code = _generate_unique_gift_code(prefix)
                    userbot_db.upsert_zarin_voucher(
                        code,
                        amount,
                        max_uses=1,
                        expires_at=expires_at,
                        is_active=1,
                    )
                    codes.append(code)
            except Exception as e:
                await msg.reply_text(f"{_adm_t('ub_lit_0443255bee50')}{e}", reply_markup=userbot_cancel_keyboard())
                return
            context.user_data.pop(ZARIN_COUPON_BULK_STATE, None)
            bot_username = await _get_user_bot_username(context)
            lines = [
                _adm_t('ub_lit_2f403ad9c699'),
                f"{_adm_t('ub_lit_caf8b4c732b7')}{len(codes)}",
                f"{_adm_t('ub_lit_cce613b19867')}{_format_toman(amount)}{_adm_t('ub_lit_f6ac3483a71a')}",
                f"{_adm_t('ub_lit_5b54e79c2658')}{_adm_t('ub_lit_2613293fcf88') if hours == 0 else _adm_t('ub_lit_d84a18536faa', h=hours)}",
                "",
                _adm_t('ub_lit_438ce8ced88d'),
            ]
            for code in codes:
                deep_link = _build_telegram_start_link(bot_username, code)
                if deep_link:
                    lines.append(f"{code} | {deep_link}")
                else:
                    lines.append(code)
            output = "\n".join(lines)
            for i in range(0, len(output), 3900):
                await msg.reply_text(output[i:i + 3900], disable_web_page_preview=True)
            await send_zarin_coupons_menu(msg.chat_id, context)
            return

    if context.user_data.get(ZARIN_COUPON_ADD_STATE):
        add_state = context.user_data.get(ZARIN_COUPON_ADD_STATE)
        if text in CANCEL_WORDS:
            context.user_data.pop(ZARIN_COUPON_ADD_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        if not isinstance(add_state, dict):
            add_state = {"step": "code"}
            context.user_data[ZARIN_COUPON_ADD_STATE] = add_state
        step = str(add_state.get("step") or "code").strip().lower()
        if step == "code":
            code = str(text or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", code):
                await msg.reply_text(
                    _adm_t('ub_lit_c57c0c573216'),
                    reply_markup=userbot_cancel_keyboard(),
                    parse_mode="Markdown",
                )
                return
            if userbot_db.get_zarin_voucher(code):
                await msg.reply_text(_adm_t('ub_lit_501a231d73f2'), reply_markup=userbot_cancel_keyboard())
                return
            add_state["step"] = "amount"
            add_state["code"] = code
            context.user_data[ZARIN_COUPON_ADD_STATE] = add_state
            await msg.reply_text(_adm_t('ub_lit_48651659a6a6'), reply_markup=userbot_cancel_keyboard())
            return
        if step == "amount":
            code = str(add_state.get("code") or "").strip()
            try:
                amount = int(str(text).replace(",", ""))
                if amount <= 0:
                    raise ValueError
            except Exception:
                await msg.reply_text(_adm_t('ub_lit_abc8ee33bc88'), reply_markup=userbot_cancel_keyboard())
                return
            try:
                userbot_db.upsert_zarin_voucher(code, amount, max_uses=1, is_active=1)
            except Exception as e:
                await msg.reply_text(f"{_adm_t('ub_lit_cb4c53563f62')}{e}", reply_markup=userbot_cancel_keyboard())
                return
            context.user_data.pop(ZARIN_COUPON_ADD_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_394538939248'), reply_markup=admin_main_keyboard())
            await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            return

    if context.user_data.get(ZARIN_COUPON_DELETE_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(ZARIN_COUPON_DELETE_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        code = str(text or "").strip()
        ok = userbot_db.delete_zarin_voucher(code)
        context.user_data.pop(ZARIN_COUPON_DELETE_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_2195f24993dc') if ok else _adm_t('ub_lit_ba49ec3eb391'), reply_markup=admin_main_keyboard())
        await send_zarin_coupons_menu(msg.chat_id, context)
        return

    if context.user_data.get(ZARIN_COUPON_LINK_STATE):
        st = context.user_data.get(ZARIN_COUPON_LINK_STATE)
        if text in CANCEL_WORDS:
            code = str((st or {}).get("code") or "").strip() if isinstance(st, dict) else ""
            context.user_data.pop(ZARIN_COUPON_LINK_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            if code:
                await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            else:
                await send_zarin_coupons_menu(msg.chat_id, context)
            return
        if not isinstance(st, dict):
            context.user_data.pop(ZARIN_COUPON_LINK_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_b56eb5171903'), reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        code = str(st.get("code") or "").strip()
        link = str(text or "").strip()
        if not (link.startswith("http://") or link.startswith("https://")):
            await msg.reply_text(_adm_t('ub_lit_72d0206c7dac'), reply_markup=userbot_cancel_keyboard())
            return
        try:
            userbot_db.set_zarin_voucher_link(code, link)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_3c03a9d8f9a2')}{e}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(ZARIN_COUPON_LINK_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_f1deb9706840'), reply_markup=admin_main_keyboard())
        await send_zarin_coupon_detail(msg.chat_id, context, code=code)
        return

    if context.user_data.get(ZARIN_COUPON_AMOUNT_STATE):
        st = context.user_data.get(ZARIN_COUPON_AMOUNT_STATE)
        if text in CANCEL_WORDS:
            code = str((st or {}).get("code") or "").strip() if isinstance(st, dict) else ""
            context.user_data.pop(ZARIN_COUPON_AMOUNT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            if code:
                await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            else:
                await send_zarin_coupons_menu(msg.chat_id, context)
            return
        if not isinstance(st, dict):
            context.user_data.pop(ZARIN_COUPON_AMOUNT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_1cff7f0fa06c'), reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        code = str(st.get("code") or "").strip()
        try:
            amount = int(str(text).replace(",", ""))
            if amount <= 0:
                raise ValueError
        except Exception:
            await msg.reply_text(_adm_t('ub_lit_abc8ee33bc88'), reply_markup=userbot_cancel_keyboard())
            return
        try:
            userbot_db.set_zarin_voucher_amount(code, amount)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_8a10aa2cd37b')}{e}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(ZARIN_COUPON_AMOUNT_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_c9295a8e43c6'), reply_markup=admin_main_keyboard())
        await send_zarin_coupon_detail(msg.chat_id, context, code=code)
        return

    if context.user_data.get(ZARIN_COUPON_CODE_STATE):
        st = context.user_data.get(ZARIN_COUPON_CODE_STATE)
        if text in CANCEL_WORDS:
            code = str((st or {}).get("code") or "").strip() if isinstance(st, dict) else ""
            context.user_data.pop(ZARIN_COUPON_CODE_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            if code:
                await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            else:
                await send_zarin_coupons_menu(msg.chat_id, context)
            return
        if not isinstance(st, dict):
            context.user_data.pop(ZARIN_COUPON_CODE_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3a11581a4250'), reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        old_code = str(st.get("code") or "").strip()
        new_code = str(text or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", new_code):
            await msg.reply_text(
                _adm_t('ub_lit_c57c0c573216'),
                reply_markup=userbot_cancel_keyboard(),
                parse_mode="Markdown",
            )
            return
        ok, result = userbot_db.rename_zarin_voucher(old_code, new_code)
        if not ok:
            await msg.reply_text(f"❌ {result}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(ZARIN_COUPON_CODE_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_9a6f070ad821'), reply_markup=admin_main_keyboard())
        await send_zarin_coupon_detail(msg.chat_id, context, code=new_code)
        return

    if context.user_data.get(ZARIN_COUPON_LIMIT_STATE):
        st = context.user_data.get(ZARIN_COUPON_LIMIT_STATE)
        if text in CANCEL_WORDS:
            code = str((st or {}).get("code") or "").strip() if isinstance(st, dict) else ""
            context.user_data.pop(ZARIN_COUPON_LIMIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            if code:
                await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            else:
                await send_zarin_coupons_menu(msg.chat_id, context)
            return
        if not isinstance(st, dict):
            context.user_data.pop(ZARIN_COUPON_LIMIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_1e5e2dfbeb9b'), reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        code = str(st.get("code") or "").strip()
        try:
            limit = int(str(text).replace(",", ""))
            if limit <= 0:
                raise ValueError
        except Exception:
            await msg.reply_text(_adm_t('ub_lit_a21ea86d5bb2'), reply_markup=userbot_cancel_keyboard())
            return
        try:
            userbot_db.set_zarin_voucher_max_uses(code, limit)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_38a4c64e7449')}{e}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(ZARIN_COUPON_LIMIT_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_e5cc5fcaada6'), reply_markup=admin_main_keyboard())
        await send_zarin_coupon_detail(msg.chat_id, context, code=code)
        return

    if context.user_data.get(ZARIN_COUPON_EXP_STATE):
        st = context.user_data.get(ZARIN_COUPON_EXP_STATE)
        if text in CANCEL_WORDS:
            code = str((st or {}).get("code") or "").strip() if isinstance(st, dict) else ""
            context.user_data.pop(ZARIN_COUPON_EXP_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            if code:
                await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            else:
                await send_zarin_coupons_menu(msg.chat_id, context)
            return
        if not isinstance(st, dict):
            context.user_data.pop(ZARIN_COUPON_EXP_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_afd62e26cfb1'), reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        code = str(st.get("code") or "").strip()
        try:
            hours = int(str(text).replace(",", ""))
            if hours < 0:
                raise ValueError
        except Exception:
            await msg.reply_text(_adm_t('ub_lit_f885235fc0db'), reply_markup=userbot_cancel_keyboard())
            return
        try:
            userbot_db.set_zarin_voucher_expire_hours(code, hours)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_1a0fea657e8f')}{e}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(ZARIN_COUPON_EXP_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_c0d511b14e20'), reply_markup=admin_main_keyboard())
        await send_zarin_coupon_detail(msg.chat_id, context, code=code)
        return

    if context.user_data.get(PAYMENT_CHANNEL_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(PAYMENT_CHANNEL_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_payment_settings_menu(msg.chat_id, context)
            return

        channel_target = ""
        try:
            fchat = getattr(msg, "forward_from_chat", None)
            if fchat and str(getattr(fchat, "type", "")) in {"channel", "supergroup"}:
                uname = str(getattr(fchat, "username", "") or "").strip()
                cid = str(getattr(fchat, "id", "") or "").strip()
                channel_target = f"@{uname}" if uname else cid
        except Exception:
            pass
        if not channel_target:
            raw = text.strip()
            if raw.startswith("@") and len(raw) > 1:
                channel_target = raw
            elif raw.lstrip("-").isdigit():
                channel_target = raw
            else:
                await msg.reply_text(
                    _adm_t('ub_lit_599d5c19e2a2'),
                    reply_markup=userbot_cancel_keyboard(),
                )
                return

        try:
            s = _get_payment_settings()
            s["event_channel_id"] = channel_target
            userbot_db.set_payment_settings(s)
        except Exception as e:
            context.user_data.pop(PAYMENT_CHANNEL_EDIT_STATE, None)
            await msg.reply_text(f"{_adm_t('ub_lit_e58474f3c8fe')}{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(PAYMENT_CHANNEL_EDIT_STATE, None)
        await msg.reply_text(f"{_adm_t('ub_lit_2770eb216b9a')}{channel_target}", reply_markup=admin_main_keyboard())
        await send_payment_settings_menu(msg.chat_id, context)
        return

    if context.user_data.get(FORCE_JOIN_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(FORCE_JOIN_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_force_join_settings_menu(msg.chat_id, context)
            return

        channel_target = ""
        channel_link = ""
        try:
            fchat = getattr(msg, "forward_from_chat", None)
            if fchat and str(getattr(fchat, "type", "")) in {"channel", "supergroup"}:
                uname = str(getattr(fchat, "username", "") or "").strip()
                cid = str(getattr(fchat, "id", "") or "").strip()
                if uname:
                    channel_target = f"@{uname}"
                    channel_link = f"https://t.me/{uname}"
                elif cid:
                    channel_target = cid
        except Exception:
            pass

        if not channel_target:
            raw = text.strip()
            if raw.startswith("@") and len(raw) > 1:
                channel_target = raw
                channel_link = f"https://t.me/{raw.lstrip('@')}"
            elif raw.lstrip("-").isdigit():
                channel_target = raw
            else:
                await msg.reply_text(
                    _adm_t('ub_lit_599d5c19e2a2'),
                    reply_markup=userbot_cancel_keyboard(),
                )
                return

        try:
            userbot_db.set_force_join_channel(channel_target, channel_link)
        except Exception as e:
            context.user_data.pop(FORCE_JOIN_EDIT_STATE, None)
            await msg.reply_text(f"{_adm_t('ub_lit_582bd646a9f3')}{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(FORCE_JOIN_EDIT_STATE, None)
        await msg.reply_text(f"{_adm_t('ub_lit_7560bbfc6c2a')}{channel_target}", reply_markup=admin_main_keyboard())
        await send_force_join_settings_menu(msg.chat_id, context)
        return

    if context.user_data.get(MARKETING_EDIT_STATE):
        edit_type = str(context.user_data.get(MARKETING_EDIT_STATE) or "").strip()
        if text in CANCEL_WORDS:
            context.user_data.pop(MARKETING_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_marketing_settings_menu(msg.chat_id, context)
            return

        try:
            if edit_type == "auto_gift_text":
                userbot_db.set_marketing_value("auto_gift_text", text)
                done_text = _adm_t('ub_lit_0443310f5f13')
            elif edit_type == "min_auto_gift_charge":
                value = int(text.replace(",", ""))
                if value < 0:
                    raise ValueError
                userbot_db.set_marketing_value("min_auto_gift_charge", value)
                done_text = f"{_adm_t('ub_lit_43e2b7c4f62c')}{value:f','}{_adm_t('ub_lit_94916658645d')}"
            else:
                raise ValueError("invalid marketing edit state")
        except Exception:
            if edit_type == "min_auto_gift_charge":
                await msg.reply_text(_adm_t('ub_lit_5d528a4feadf'), reply_markup=userbot_cancel_keyboard())
            else:
                await msg.reply_text(_adm_t('ub_lit_d4bcf57a25ff'), reply_markup=userbot_cancel_keyboard())
            return

        context.user_data.pop(MARKETING_EDIT_STATE, None)
        await msg.reply_text(done_text, reply_markup=admin_main_keyboard())
        await send_marketing_settings_menu(msg.chat_id, context)
        return

    if context.user_data.get(REFERRAL_VALUE_EDIT_STATE):
        edit_state = context.user_data.get(REFERRAL_VALUE_EDIT_STATE) or {}
        edit_name = str(edit_state.get("name") or "").strip()
        raw_text = (text or "").strip()
        if raw_text in CANCEL_WORDS:
            context.user_data.pop(REFERRAL_VALUE_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_referral_admin_settings(msg.chat_id, context)
            return
        if edit_name == "invite_intro_text":
            context.user_data.pop(REFERRAL_VALUE_EDIT_STATE, None)
            if not raw_text:
                await msg.reply_text(_adm_t('ub_lit_4946cbeadf96'), reply_markup=admin_main_keyboard())
                return
            try:
                userbot_db.set_referral_value("invite_intro_text", raw_text)
            except Exception as e:
                await msg.reply_text(f"{_adm_t('ub_lit_3e448ada69d3')}{e}", reply_markup=admin_main_keyboard())
                return
            await msg.reply_text(_adm_t('ub_lit_54bcac6988a7'), reply_markup=admin_main_keyboard())
            await send_referral_admin_settings(msg.chat_id, context)
            return
        try:
            value = int(raw_text.replace(",", "").replace("٬", ""))
        except Exception:
            await msg.reply_text(_adm_t('ub_lit_101cc2220c54'), reply_markup=userbot_cancel_keyboard())
            return
        try:
            userbot_db.set_referral_value(edit_name, value)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_3e448ada69d3')}{e}", reply_markup=admin_main_keyboard())
            return
        context.user_data.pop(REFERRAL_VALUE_EDIT_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_e88fbb5b2933'), reply_markup=admin_main_keyboard())
        await send_referral_admin_settings(msg.chat_id, context)
        return

    if context.user_data.get(REFERRAL_MANUAL_REWARD_STATE):
        raw_text = (text or "").strip()
        if raw_text in CANCEL_WORDS:
            context.user_data.pop(REFERRAL_MANUAL_REWARD_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_referral_admin_menu(msg.chat_id, context)
            return
        parts = raw_text.replace("٬", "").replace(",", "").split()
        if len(parts) != 2:
            await msg.reply_text(
                _adm_t('ub_lit_3fc3254f3678'),
                reply_markup=userbot_cancel_keyboard(),
            )
            return
        try:
            target_user_id = int(parts[0])
            amount = int(parts[1])
        except Exception:
            await msg.reply_text(_adm_t('ub_lit_7b7eae4c5cd0'), reply_markup=userbot_cancel_keyboard())
            return
        try:
            reward = userbot_db.grant_manual_referral_reward(target_user_id, amount)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_9d3e3e9ed574')}{e}", reply_markup=admin_main_keyboard())
            return
        context.user_data.pop(REFERRAL_MANUAL_REWARD_STATE, None)
        if not reward:
            await msg.reply_text(_adm_t('ub_lit_ab99955d41c0'), reply_markup=admin_main_keyboard())
            return
        await msg.reply_text(
            f"{_adm_t('ub_lit_bc9d01e34bfb')}{target_user_id}{_adm_t('ub_lit_0835446733bd')}{amount:f','}{_adm_t('ub_lit_6dfb7790bd57')}{reward.get('id')}",
            reply_markup=admin_main_keyboard(),
        )
        return

    if context.user_data.get(INVITE_BANNER_PHOTO_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(INVITE_BANNER_PHOTO_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_invite_text_settings_menu(msg.chat_id, context)
            return

        if not getattr(msg, "photo", None):
            await msg.reply_text(
                _adm_t('ub_lit_4fd19e9d65f3'),
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        try:
            file_id = msg.photo[-1].file_id
            userbot_db.set_text_setting("invite_banner_photo_id", file_id)
        except Exception as e:
            context.user_data.pop(INVITE_BANNER_PHOTO_EDIT_STATE, None)
            await msg.reply_text(f"{_adm_t('ub_lit_0ec3e6bcf8b8')}{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(INVITE_BANNER_PHOTO_EDIT_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_75b9f3967d9d'), reply_markup=admin_main_keyboard())
        await send_invite_text_settings_menu(msg.chat_id, context)
        return

    # بررسی ویزارد تنظیم کانال رویداد
    if context.user_data.get(EVENT_CHANNEL_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(EVENT_CHANNEL_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            return

        channel_target: str = ""
        channel_title: str = ""

        # 1) فوروارد پیام از کانال (روش پیشنهادی)
        try:
            fchat = getattr(msg, "forward_from_chat", None)
            if fchat and str(getattr(fchat, "type", "")) in {"channel", "supergroup"}:
                channel_target = str(getattr(fchat, "id", "") or "").strip()
                channel_title = str(getattr(fchat, "title", "") or "").strip()
        except Exception:
            pass

        # 2) Telegram PTB v20+: forward_origin
        if not channel_target:
            try:
                origin = getattr(msg, "forward_origin", None)
                ochat = getattr(origin, "chat", None) if origin else None
                if ochat and str(getattr(ochat, "type", "")) in {"channel", "supergroup"}:
                    channel_target = str(getattr(ochat, "id", "") or "").strip()
                    channel_title = str(getattr(ochat, "title", "") or "").strip()
            except Exception:
                pass

        # 3) ورود دستی @channel یا -100...
        if not channel_target:
            t = text.strip()
            if t.startswith("@") and len(t) > 1:
                channel_target = t
            elif t.lstrip("-").isdigit():
                channel_target = t

        if not channel_target:
            await msg.reply_text(
                _adm_t('ub_lit_a3435a3f2044'),
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        try:
            settings = _get_buy_renew_settings()
            settings["event_channel_id"] = channel_target
            userbot_db.set_buy_renew_settings(settings)
        except Exception as e:
            context.user_data.pop(EVENT_CHANNEL_EDIT_STATE, None)
            await msg.reply_text(f"{_adm_t('ub_lit_04623a2f0eec')}{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(EVENT_CHANNEL_EDIT_STATE, None)
        title_part = f" ({channel_title})" if channel_title else ""
        await msg.reply_text(
            f"{_adm_t('ub_lit_3736887a37be')}{channel_target}{title_part}",
            reply_markup=admin_main_keyboard(),
        )
        await send_buy_renew_settings_menu(msg.chat_id, context)
        return

    # بررسی ویزارد یادآور وضعیت اشتراک
    if context.user_data.get(SUB_REMINDER_EDIT_STATE):
        edit_type = context.user_data.get(SUB_REMINDER_EDIT_STATE)
        if text in CANCEL_WORDS:
            context.user_data.pop(SUB_REMINDER_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            return

        try:
            value = int(text.replace(",", ""))
            if value <= 0:
                raise ValueError
        except ValueError:
            unit = _adm_t('ub_lit_ca85caa98a61') if edit_type == "usage_gb" else _adm_t('ub_lit_6702edb75e90')
            await msg.reply_text(f"{_adm_t('ub_lit_7754a8d49afc')}{unit}).", reply_markup=userbot_cancel_keyboard())
            return

        try:
            userbot_db.set_sub_reminder_value(edit_type, value)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_2a6dd7353081')}{e}", reply_markup=admin_main_keyboard())
            context.user_data.pop(SUB_REMINDER_EDIT_STATE, None)
            return

        context.user_data.pop(SUB_REMINDER_EDIT_STATE, None)
        reminder = _get_sub_reminder_settings()
        if edit_type == "usage_gb":
            await msg.reply_text(
                f"{_adm_t('ub_lit_5386426d159e')}{reminder.get('usage_gb', value)}{_adm_t('ub_lit_d2b3c92f5bd8')}",
                reply_markup=admin_main_keyboard(),
            )
        else:
            await msg.reply_text(
                f"{_adm_t('ub_lit_8e906a2586a2')}{reminder.get('days', value)}{_adm_t('ub_lit_cb26b74de850')}",
                reply_markup=admin_main_keyboard(),
            )
        await send_sub_status_reminder_menu(msg.chat_id, context)
        return

    # بررسی ویزارد تنظیم دامنه Multi Server
    if context.user_data.get(SUB_BASE_URL_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(SUB_BASE_URL_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            return

        normalized = _normalize_public_base_url(text)
        if text.strip() != "0" and not normalized:
            await msg.reply_text(
                _adm_t('ub_lit_2055a5e5f76b'),
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        try:
            stored = userbot_db.set_managed_sub_base_url(normalized)
        except Exception as e:
            context.user_data.pop(SUB_BASE_URL_EDIT_STATE, None)
            await msg.reply_text(f"{_adm_t('ub_lit_cbe7997b4446')}{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(SUB_BASE_URL_EDIT_STATE, None)
        if stored:
            ssl_hint = ""
            host_hint = _extract_host_only(stored)
            if host_hint:
                ssl_hint = (
                    f"{_adm_t('ub_lit_d5cc4438fd32')}{host_hint} your-email@example.com"
                )
            await msg.reply_text(
                f"{_adm_t('ub_lit_20ef85d41c70')}{stored}{ssl_hint}",
                reply_markup=admin_main_keyboard(),
            )
        else:
            await msg.reply_text(
                _adm_t('ub_lit_5d1748a5b670'),
                reply_markup=admin_main_keyboard(),
            )
        await send_sub_link_status_menu(msg.chat_id, context)
        return

    # بررسی ویزارد مشخصات اشتراک تستی
    if context.user_data.get(TRIAL_SPEC_EDIT_STATE):
        edit_type = context.user_data.get(TRIAL_SPEC_EDIT_STATE)
        if text in CANCEL_WORDS:
            context.user_data.pop(TRIAL_SPEC_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            return

        try:
            if edit_type == "usage_gb":
                value = float(text.replace(",", "."))
                if value <= 0:
                    raise ValueError
            else:
                value = int(text.replace(",", ""))
                if value <= 0:
                    raise ValueError
        except ValueError:
            if edit_type == "usage_gb":
                await msg.reply_text(_adm_t('ub_lit_e10093dcd1b0'), reply_markup=userbot_cancel_keyboard())
            else:
                await msg.reply_text(_adm_t('ub_lit_dd83e44eea5e'), reply_markup=userbot_cancel_keyboard())
            return

        try:
            userbot_db.set_trial_spec_value(edit_type, value)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_d7e705e7c205')}{e}", reply_markup=admin_main_keyboard())
            context.user_data.pop(TRIAL_SPEC_EDIT_STATE, None)
            return

        context.user_data.pop(TRIAL_SPEC_EDIT_STATE, None)
        spec = _get_trial_spec_settings()
        if edit_type == "usage_gb":
            usage_val = float(spec.get("usage_gb", value))
            usage_txt = f"{usage_val:g}"
            await msg.reply_text(
                f"{_adm_t('ub_lit_7f0c3714a503')}{usage_txt}{_adm_t('ub_lit_d2b3c92f5bd8')}",
                reply_markup=admin_main_keyboard(),
            )
        else:
            await msg.reply_text(
                f"{_adm_t('ub_lit_fce18c0fa844')}{spec.get('days', value)}{_adm_t('ub_lit_cb26b74de850')}",
                reply_markup=admin_main_keyboard(),
            )
        await send_trial_spec_menu(msg.chat_id, context)
        return

    # بررسی ویزارد کیف پول
    if context.user_data.get(RENEW_POLICY_EDIT_STATE):
        edit_type = context.user_data.get(RENEW_POLICY_EDIT_STATE)
        if text in CANCEL_WORDS:
            context.user_data.pop(RENEW_POLICY_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            return

        try:
            value = int(text.replace(",", ""))
            if value <= 0:
                raise ValueError
        except ValueError:
            await msg.reply_text(_adm_t('ub_lit_50674c97b118'), reply_markup=userbot_cancel_keyboard())
            return

        try:
            userbot_db.set_buy_renew_limit(edit_type, value)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_ca6224a863ec')}{e}", reply_markup=admin_main_keyboard())
            context.user_data.pop(RENEW_POLICY_EDIT_STATE, None)
            return

        context.user_data.pop(RENEW_POLICY_EDIT_STATE, None)
        if edit_type == "renew_max_days":
            await msg.reply_text(
                f"{_adm_t('ub_lit_5ea3f64978cf')}{value}{_adm_t('ub_lit_cb26b74de850')}",
                reply_markup=admin_main_keyboard(),
            )
        elif edit_type == "renew_unlimited_volume_from_gb":
            await msg.reply_text(
                f"{_adm_t('ub_lit_e28c806532fe')}{value}{_adm_t('ub_lit_d2b3c92f5bd8')}",
                reply_markup=admin_main_keyboard(),
            )
            await send_buy_renew_settings_menu(msg.chat_id, context)
            return
        elif edit_type == "renew_unlimited_time_from_days":
            await msg.reply_text(
                f"{_adm_t('ub_lit_72ccfa7bc5b3')}{value}{_adm_t('ub_lit_cb26b74de850')}",
                reply_markup=admin_main_keyboard(),
            )
            await send_buy_renew_settings_menu(msg.chat_id, context)
            return
        else:
            await msg.reply_text(
                f"{_adm_t('ub_lit_000bcaff51c0')}{value}{_adm_t('ub_lit_d2b3c92f5bd8')}",
                reply_markup=admin_main_keyboard(),
            )
        await send_renew_policy_menu(msg.chat_id, context)
        return

    # بررسی ویزارد تنظیمات تراکنش/پلن
    if context.user_data.get(TX_PLANS_EDIT_STATE):
        edit_type = context.user_data.get(TX_PLANS_EDIT_STATE)
        if text in CANCEL_WORDS:
            context.user_data.pop(TX_PLANS_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            return

        try:
            value = int(text.replace(",", ""))
            if value <= 0:
                raise ValueError
        except ValueError:
            await msg.reply_text(_adm_t('ub_lit_0666a0bc2724'), reply_markup=userbot_cancel_keyboard())
            return

        try:
            if edit_type == "min_transaction_toman":
                userbot_db.set_tx_plans_min_transaction(value)
            else:
                raise ValueError("invalid edit state")
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_7ae6cfe1b810')}{e}", reply_markup=admin_main_keyboard())
            context.user_data.pop(TX_PLANS_EDIT_STATE, None)
            return

        context.user_data.pop(TX_PLANS_EDIT_STATE, None)
        await msg.reply_text(
            f"{_adm_t('ub_lit_faac0ada8bac')}{value:f','}{_adm_t('ub_lit_94916658645d')}",
            reply_markup=admin_main_keyboard(),
        )
        await send_tx_plans_settings_menu(msg.chat_id, context)
        return

    # بررسی ویزارد تنظیمات متون
    if context.user_data.get(TEXT_SETTINGS_EDIT_STATE):
        field_name = str(context.user_data.get(TEXT_SETTINGS_EDIT_STATE) or "").strip()
        guide_fields = {
            "guide_text",
            "guide_android_text",
            "guide_ios_text",
            "guide_windows_text",
            "guide_mac_text",
            "guide_linux_text",
        }
        invite_fields = {"invite_info_text", "invite_banner_text", "invite_text"}
        payment_text_fields = {"card_to_card_text"}
        if text in CANCEL_WORDS:
            context.user_data.pop(TEXT_SETTINGS_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            if field_name in guide_fields:
                await send_guide_text_settings_menu(msg.chat_id, context)
                return
            if field_name in invite_fields:
                await send_invite_text_settings_menu(msg.chat_id, context)
                return
            if field_name in payment_text_fields:
                await send_payment_method_menu(msg.chat_id, context, method="card")
                return
            return
        try:
            userbot_db.set_text_setting(field_name, text)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_969b02f6f1f7')}{e}", reply_markup=admin_main_keyboard())
            context.user_data.pop(TEXT_SETTINGS_EDIT_STATE, None)
            return

        context.user_data.pop(TEXT_SETTINGS_EDIT_STATE, None)
        await msg.reply_text(_adm_t('ub_lit_522712817c45'), reply_markup=admin_main_keyboard())
        if field_name in guide_fields:
            await send_guide_text_settings_menu(msg.chat_id, context)
            return
        if field_name in invite_fields:
            await send_invite_text_settings_menu(msg.chat_id, context)
            return
        if field_name in payment_text_fields:
            await send_payment_method_menu(msg.chat_id, context, method="card")
            return
        await send_text_settings_menu(msg.chat_id, context)
        return

    # بررسی ویزارد کیف پول
    if context.user_data.get(WALLET_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(WALLET_EDIT_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            return
        
        # مقدار باید عدد باشد
        try:
            amount = int(text.replace(",", ""))
        except ValueError:
            await msg.reply_text(_adm_t('ub_lit_550b38ae74e5'))
            return
        
        user_id = context.user_data.pop(WALLET_EDIT_STATE) # گرفتن ID و پاک کردن استیت
        
        # 1. آپدیت دیتابیس
        userbot_db.set_user_wallet(user_id, amount)
        
        # 2. دریافت اطلاعات کاربر برای ارسال نوتیفیکیشن
        user = userbot_db.get_user_by_id(user_id)
        
        await msg.reply_text(f"{_adm_t('ub_lit_045bd25dfbaf')}{_format_toman(amount)}{_adm_t('ub_lit_06d051a09b21')}", reply_markup=admin_main_keyboard())
        await send_user_profile(user_id, msg.chat_id, context)

        # 3. ارسال پیام به ربات کاربر
        if user and user.get('telegram_id') and USER_BOT_TOKEN:
            try:
                user_bot = Bot(token=USER_BOT_TOKEN)
                notify_text = _i18n_user_t(_user_lang_of(user['telegram_id']), "admin_wallet_set_notify", amount=_format_toman(amount))
                await user_bot.send_message(chat_id=user['telegram_id'], text=notify_text)
            except Exception as e:
                await msg.reply_text(f"{_adm_t('ub_lit_97f9a7ad298d')}{e}")
        return

    # بررسی ویزارد ارسال پیام
    if context.user_data.get(MESSAGE_SEND_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(MESSAGE_SEND_STATE, None)
            await msg.reply_text(_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            return

        raw_state = context.user_data.pop(MESSAGE_SEND_STATE)
        if isinstance(raw_state, dict):
            user_id = int(raw_state.get("user_id") or 0)
        else:
            try:
                user_id = int(raw_state or 0)
            except Exception:
                user_id = 0
        if user_id <= 0:
            await msg.reply_text(_adm_t('ub_lit_9024444be650'), reply_markup=admin_main_keyboard())
            return
        user = userbot_db.get_user_by_id(user_id)

        if user and user.get('telegram_id') and USER_BOT_TOKEN:
            try:
                user_bot = Bot(token=USER_BOT_TOKEN)
                _ulg = _user_lang_of(user['telegram_id'])
                final_msg = _i18n_user_t(_ulg, "admin_new_msg_notify", text=text)
                kb = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(_i18n_user_t(_ulg, "btn_reply2"), callback_data="support:adminmsg:reply")]]
                )
                await user_bot.send_message(
                    chat_id=user['telegram_id'],
                    text=final_msg,
                    reply_markup=kb,
                )
                await msg.reply_text(_adm_t('ub_lit_c6214e61f91d'), reply_markup=admin_main_keyboard())
            except Exception as e:
                await msg.reply_text(f"{_adm_t('ub_lit_fc8767e62053')}{e}", reply_markup=admin_main_keyboard())
        else:
            await msg.reply_text(_adm_t('ub_lit_cf78cec0ca70'), reply_markup=admin_main_keyboard())

        return


async def send_user_services_list(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    raw_services = userbot_db.get_services_for_user(user_id)
    candidate_services = [s for s in raw_services if not _is_locally_deleted_service(s)]
    local_active_services = [s for s in candidate_services if _is_locally_active_service(s)]
    expired_services = [s for s in candidate_services if _is_locally_expired_service(s)]

    services: List[Dict[str, Any]] = []
    if local_active_services:
        sem = asyncio.Semaphore(5)

        async def _check_visible(service: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
            async with sem:
                state = await _service_panel_state(service)
                return state, service

        checked = await asyncio.gather(*[_check_visible(s) for s in local_active_services])
        for state, service in checked:
            if state in {"active", "unknown"}:
                services.append(service)
            elif state == "expired":
                expired_service = dict(service)
                expired_service["_panel_expired"] = True
                expired_services.append(expired_service)

    visible_services = services + expired_services
    active_count = len(services)
    expired_count = len(expired_services)

    if not visible_services:
        text = (
            f"{_adm_t('ub_lit_2e0758df0152')}{len(raw_services)}{_adm_t('ub_lit_49fc823d5462')}"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data=f"userbot:user:{user_id}")]])
        if message:
            try: await message.edit_text(text, reply_markup=kb)
            except BadRequest: await context.bot.send_message(chat_id, text, reply_markup=kb)
        else: await context.bot.send_message(chat_id, text, reply_markup=kb)
        return

    rows: List[List[InlineKeyboardButton]] = []
    service_buttons: List[InlineKeyboardButton] = []
    for s in visible_services:
        name = s.get("name") or f"Service #{s['id']}"
        if _is_display_expired_service(s):
            emoji = "🔴"
        elif _comment_has_flag(str(s.get("comment") or ""), "test"):
            emoji = "🟡"
        else:
            emoji = "🔵"
        service_buttons.append(
            InlineKeyboardButton(
                f"{emoji} |{name}",
                callback_data=f"userbot:svc:{s['id']}",
            )
        )

    for i in range(0, len(service_buttons), 3):
        chunk = service_buttons[i:i + 3]
        rows.append(list(reversed(chunk)))

    rows.append([InlineKeyboardButton(_adm_t('ub_lit_95e3957c1b69'), callback_data=f"userbot:user:{user_id}")])
    
    kb = InlineKeyboardMarkup(rows)
    text = (
        f"{_adm_t('ub_lit_2e0758df0152')}{len(raw_services)}{_adm_t('ub_lit_f72e1335fbc5')}{active_count}{_adm_t('ub_lit_8158134befd3')}{expired_count}"
    )
    if message:
        try: await message.edit_text(text, reply_markup=kb)
        except BadRequest: await context.bot.send_message(chat_id, text, reply_markup=kb)
    else: await context.bot.send_message(chat_id, text, reply_markup=kb)
async def send_user_orders_list(user_id: int, page: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    if page < 1: page = 1
    
    # آمار و لیست سفارشات مختص کاربر
    stats = userbot_db.get_user_orders_stats_full(user_id)
    orders = userbot_db.get_user_orders_paginated(user_id, page, USERBOT_PAGE_SIZE)
    
    total_count = stats['total_count']
    total_pages = max(1, math.ceil(total_count / USERBOT_PAGE_SIZE))
    if page > total_pages: page = total_pages

    def fmt(val): return f"{int(val):,}"

    # متن پیام طبق عکس دوم
    text = (
        f"{_adm_t('ub_lit_e3a832b4ca5c')}{stats['total_count']}{_adm_t('ub_lit_ccabb6312338')}{fmt(stats['total_gb'])}{_adm_t('ub_lit_10863b8ea7a1')}{fmt(stats['total_price'])}{_adm_t('ub_lit_075fa5003b8c')}{stats['last30_count']}{_adm_t('ub_lit_46f5a090b943')}{fmt(stats['last30_gb'])}{_adm_t('ub_lit_4d5374d31fa8')}{fmt(stats['last30_price'])}{_adm_t('ub_lit_cb2ff5873dcb')}{stats['month_count']}{_adm_t('ub_lit_2d99a88d8227')}{fmt(stats['month_gb'])}{_adm_t('ub_lit_99a97b8e2595')}{fmt(stats['month_price'])}{_adm_t('ub_lit_9e29f6087438')}"
    )

    rows = []
    current_row = []
    for o in orders:
        oid = str(o.get('order_id') or o.get('id'))
        # با کلیک روی دکمه، جزئیات سفارش باز میشه
        current_row.append(InlineKeyboardButton(oid, callback_data=f"userbot:order:{oid}"))
        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []
    if current_row: rows.append(current_row)

    # نویگیشن
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"userbot:user:{user_id}:orders:list:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="userbot:noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"userbot:user:{user_id}:orders:list:{page+1}"))
    rows.append(nav)

    # دکمه بازگشت به پروفایل همان کاربر
    rows.append([InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data=f"userbot:user:{user_id}")])

    kb = InlineKeyboardMarkup(rows)
    
    if message:
        try: await message.edit_text(text, reply_markup=kb)
        except BadRequest: await context.bot.send_message(chat_id, text, reply_markup=kb)
    else: await context.bot.send_message(chat_id, text, reply_markup=kb)

async def send_user_payments_list(user_id: int, page: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    if page < 1: page = 1
    
    stats = userbot_db.get_user_payments_stats(user_id)
    payments = userbot_db.get_user_payments_paginated(user_id, page, USERBOT_PAGE_SIZE)
    
    total_count = stats['total_count']
    total_pages = max(1, math.ceil(total_count / USERBOT_PAGE_SIZE))
    if page > total_pages: page = total_pages

    def fmt(val): return f"{int(val):,}"

    # متن پیام طبق عکس سوم
    text = (
        f"{_adm_t('ub_lit_1ac60b3bc3e0')}{stats['total_count']}{_adm_t('ub_lit_9dc0422feb4d')}{fmt(stats['total_amount'])}{_adm_t('ub_lit_4c78c73fdcce')}{stats['last30_count']}{_adm_t('ub_lit_4cf4dd8b0dfc')}{fmt(stats['last30_amount'])}{_adm_t('ub_lit_3a85cb5ba8c9')}{stats['month_count']}{_adm_t('ub_lit_3160570278d0')}{fmt(stats['month_amount'])}{_adm_t('ub_lit_9e29f6087438')}"
    )

    rows = []
    current_row = []
    for p in payments:
        label = str(p['id'])
        current_row.append(InlineKeyboardButton(label, callback_data=f"userbot:pay:detail:{p['id']}"))
        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []
    if current_row: rows.append(current_row)

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"userbot:user:{user_id}:payments:list:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="userbot:noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"userbot:user:{user_id}:payments:list:{page+1}"))
    rows.append(nav)

    # بازگشت به پروفایل
    rows.append([InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data=f"userbot:user:{user_id}")])

    kb = InlineKeyboardMarkup(rows)
    
    if message:
        try: await message.edit_text(text, reply_markup=kb)
        except BadRequest: await context.bot.send_message(chat_id, text, reply_markup=kb)
    else: await context.bot.send_message(chat_id, text, reply_markup=kb)

async def send_service_detail(service_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    svc = userbot_db.get_service_by_id(service_id)
    if not svc:
        return
    is_expired = _is_locally_expired_service(svc)
    panel_state = "expired" if is_expired else await _service_panel_state(svc)
    if panel_state == "expired":
        svc = dict(svc)
        svc["_panel_expired"] = True
        is_expired = True
    if _is_locally_deleted_service(svc) or (not is_expired and panel_state == "missing"):
        text = _adm_t('ub_lit_8c4a2b05a781')
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data=f"userbot:user:{svc.get('user_id')}")]])
        if message:
            try:
                await message.edit_text(text, reply_markup=kb)
            except BadRequest:
                await context.bot.send_message(chat_id, text, reply_markup=kb)
        else:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
        return
    user = userbot_db.get_user_by_id(svc['user_id']) or {}
    
    text = build_service_detail_text(user, svc)
    kb = build_service_detail_keyboard(svc['user_id'], service_id)
    
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


# ===============================
#   بخش مدیریت سفارشات
# ===============================

async def send_orders_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_f95f745c9e5b')
    kb = build_orders_menu_keyboard()
    if message:
        try: await message.edit_text(text, reply_markup=kb)
        except BadRequest: await context.bot.send_message(chat_id, text, reply_markup=kb)
    else: await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_gifts_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    deactivated = 0
    try:
        deactivated = userbot_db.deactivate_unusable_zarin_vouchers()
    except Exception:
        deactivated = 0
    stats = userbot_db.get_zarin_vouchers_dashboard()
    text = (
        f"{_adm_t('ub_lit_7939af78c979')}{int(stats.get('active') or 0)}{_adm_t('ub_lit_f3ab0ecfcb48')}{int(stats.get('total') or 0)}{_adm_t('ub_lit_4ed63fda8474')}{int(stats.get('redemptions') or 0)}{_adm_t('ub_lit_e7774420341a')}{_format_toman(stats.get('redeemed_amount'))}{_adm_t('ub_lit_5a9315cc17f6')}{deactivated}{_adm_t('ub_lit_300e6c16f0bc')}"
    )
    kb = build_gifts_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_gifts_dashboard(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    deactivated = 0
    try:
        deactivated = userbot_db.deactivate_unusable_zarin_vouchers()
    except Exception:
        deactivated = 0
    stats = userbot_db.get_zarin_vouchers_dashboard()
    used = int(stats.get("used_count") or 0)
    max_uses = int(stats.get("max_uses") or 0)
    percent = int((used / max_uses) * 100) if max_uses > 0 else 0
    text = (
        f"{_adm_t('ub_lit_d4d3b360be5c')}{int(stats.get('total') or 0)}{_adm_t('ub_lit_61149b538c4f')}{int(stats.get('active') or 0)}{_adm_t('ub_lit_bd2f8d084f58')}{int(stats.get('inactive') or 0)}{_adm_t('ub_lit_0076b2c06f4f')}{int(stats.get('expired') or 0)}{_adm_t('ub_lit_51aed5c864fc')}{int(stats.get('full') or 0)}{_adm_t('ub_lit_80ab8a9e6ab4')}{used}{_adm_t('ub_lit_7138b458fc80')}{max_uses} ({percent}{_adm_t('ub_lit_5d082cae8dab')}{_format_toman(stats.get('total_amount'))}{_adm_t('ub_lit_97fd3e927175')}{_format_toman(stats.get('redeemed_amount'))}{_adm_t('ub_lit_43608a5133af')}{deactivated}{_adm_t('ub_lit_3a27474a1805')}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_adm_t('ub_lit_260de9a11858'), callback_data="userbot:gifts:coupons")],
        [InlineKeyboardButton(_adm_t('ub_lit_b89bf356fe97'), callback_data="userbot:gifts:presets")],
        [InlineKeyboardButton(_adm_t('ub_lit_7b83d4e6526c'), callback_data="userbot:gifts:bulk")],
        [InlineKeyboardButton(_adm_t('ub_lit_4a39cbe28b8a'), callback_data="userbot:gifts:redemptions")],
        [InlineKeyboardButton(_adm_t('ub_lit_c5cd51abdb6e'), callback_data="userbot:gifts:security")],
        [InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:gifts_menu")],
    ])
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_zarin_coupons_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    try:
        userbot_db.deactivate_unusable_zarin_vouchers()
    except Exception:
        pass
    coupons = userbot_db.list_zarin_vouchers(limit=300)
    total = len(coupons)
    active = int((userbot_db.get_zarin_vouchers_dashboard() or {}).get("active") or 0)
    text = (
        f"{_adm_t('ub_lit_d5e82963223f')}{total}{_adm_t('ub_lit_287dc0bc6067')}{active}{_adm_t('ub_lit_38cbb34f3729')}{max(0, total - active)}{_adm_t('ub_lit_47ed1e8d0578')}"
    )
    kb = build_zarin_coupons_list_keyboard(coupons)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_zarin_coupon_detail(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    code: str,
    message=None,
) -> None:
    try:
        userbot_db.deactivate_unusable_zarin_vouchers()
    except Exception:
        pass
    item = userbot_db.get_zarin_voucher(code)
    if not item:
        await context.bot.send_message(chat_id, _adm_t('ub_lit_50ffbd21b0e4'))
        await send_zarin_coupons_menu(chat_id, context)
        return
    c = str(item.get("code") or "").strip()
    amount = int(item.get("amount_toman") or 0)
    used = int(item.get("used_count") or 0)
    max_uses = int(item.get("max_uses") or 1)
    left = max(0, max_uses - used)
    exp_raw = str(item.get("expires_at") or "").strip()
    exp = exp_raw or _adm_t('ub_lit_2613293fcf88')
    remain = _adm_t('ub_lit_2613293fcf88')
    if exp_raw:
        try:
            exp_dt = datetime.strptime(exp_raw, "%Y-%m-%d %H:%M:%S")
            delta = exp_dt - datetime.now(timezone.utc).replace(tzinfo=None)
            if delta.total_seconds() <= 0:
                remain = _adm_t('ub_lit_1ec0363365b4')
            else:
                total = int(delta.total_seconds())
                h = total // 3600
                m = (total % 3600) // 60
                s = total % 60
                remain = f"{h:02d}:{m:02d}:{s:02d}"
        except Exception:
            remain = _adm_t('ub_lit_264f61d0e11d')
    link = str(item.get("zarinpal_link") or "").strip() or _adm_t('ub_lit_cdd8f534031a')
    bot_username = await _get_user_bot_username(context)
    deep_link = _build_telegram_start_link(bot_username, c) or _adm_t('ub_lit_b9bc1d35285e')
    status = _adm_t('ub_lit_25c499f43398') if int(item.get("is_active") or 0) == 1 else _adm_t('ub_lit_7fdadc73ac3c')
    if remain == "منقضی شده":
        status = _adm_t('ub_lit_1ec0363365b4')
    elif left <= 0:
        status = _adm_t('ub_lit_cd7d40cac442')
    text = (
        f"{_adm_t('ub_lit_2327f0d76d01')}{c}{_adm_t('ub_lit_0e95a15655c0')}{status}{_adm_t('ub_lit_2b00906ffd5d')}{used}{_adm_t('ub_lit_7138b458fc80')}{max_uses}{_adm_t('ub_lit_bbba6fbed6f5')}{left}{_adm_t('ub_lit_39b8e3414d62')}{amount:f','}{_adm_t('ub_lit_292ba1087f33')}{exp}{_adm_t('ub_lit_3414489d9d46')}{remain}{_adm_t('ub_lit_044747247e0f')}{link}{_adm_t('ub_lit_eecafadfaa06')}{deep_link}{_adm_t('ub_lit_87a29817849e')}"
    )
    kb = build_zarin_coupon_detail_keyboard(c, item)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)


async def send_gift_presets_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = (
        _adm_t('ub_lit_d2c52fc7ed8d')
    )
    for preset in GIFT_CAMPAIGN_PRESETS.values():
        hours = int(preset.get("hours") or 0)
        text += (
            f"{preset['title']}{_adm_t('ub_lit_5cad3416ea89')}{_format_toman(preset['amount'])}{_adm_t('ub_lit_15d3784337b7')}{int(preset['max_uses'])}{_adm_t('ub_lit_dab2ec9b2f1d')}{_adm_t('ub_lit_2613293fcf88') if hours <= 0 else _adm_t('ub_lit_d84a18536faa', h=hours)}{_adm_t('ub_lit_3c7839827d7a')}{preset['note']}\n\n"
        )
    kb = build_gift_presets_keyboard()
    if message:
        try:
            await message.edit_text(text.strip(), reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text.strip(), reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text.strip(), reply_markup=kb)


def _create_gift_preset_coupon(preset_key: str) -> Dict[str, Any]:
    preset = GIFT_CAMPAIGN_PRESETS.get(str(preset_key or "").strip().lower())
    if not preset:
        raise ValueError("preset not found")
    hours = int(preset.get("hours") or 0)
    expires_at = ""
    if hours > 0:
        expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
    code = _generate_unique_gift_code(str(preset.get("prefix") or "GIFT"))
    item = userbot_db.upsert_zarin_voucher(
        code,
        int(preset.get("amount") or 0),
        max_uses=int(preset.get("max_uses") or 1),
        expires_at=expires_at,
        is_active=1,
    )
    item["_preset_title"] = str(preset.get("title") or "")
    return item


async def send_gifts_security_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    deactivated = 0
    try:
        deactivated = userbot_db.deactivate_unusable_zarin_vouchers()
    except Exception:
        deactivated = 0
    stats = userbot_db.get_zarin_vouchers_dashboard()
    text = (
        f"{_adm_t('ub_lit_e9d1a83e577a')}{deactivated}{_adm_t('ub_lit_8b880ef6076b')}{int(stats.get('active') or 0)}{_adm_t('ub_lit_0076b2c06f4f')}{int(stats.get('expired') or 0)}{_adm_t('ub_lit_51aed5c864fc')}{int(stats.get('full') or 0)}{_adm_t('ub_lit_76d4260295c9')}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_adm_t('ub_lit_fb71ed8d102d'), callback_data="userbot:gifts:auto_off")],
        [InlineKeyboardButton(_adm_t('ub_lit_ab2cd3394549'), callback_data="userbot:gifts:help")],
        [InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:gifts_menu")],
    ])
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_coupon_campaign_text(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    code: str,
    message=None,
) -> None:
    item = userbot_db.get_zarin_voucher(code)
    if not item:
        await context.bot.send_message(chat_id, _adm_t('ub_lit_50ffbd21b0e4'))
        return
    c = str(item.get("code") or "").strip()
    bot_username = await _get_user_bot_username(context)
    deep_link = _build_telegram_start_link(bot_username, c)
    text = _build_gift_campaign_copy(c, deep_link, int(item.get("amount_toman") or 0))
    rows: List[List[InlineKeyboardButton]] = []
    if deep_link:
        rows.append([InlineKeyboardButton(_adm_t('ub_lit_76a467ee4372'), url=deep_link)])
    rows.append([InlineKeyboardButton(_adm_t('ub_lit_e75d30bfd8a5'), callback_data=f"userbot:gifts:coupon:qr:{c}")])
    rows.append([InlineKeyboardButton(_adm_t('ub_lit_030adba70185'), callback_data=f"userbot:gifts:coupon:{c}")])
    kb = InlineKeyboardMarkup(rows)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)


async def send_coupon_deeplink_qr(chat_id: int, context: ContextTypes.DEFAULT_TYPE, *, code: str) -> None:
    item = userbot_db.get_zarin_voucher(code)
    if not item:
        await context.bot.send_message(chat_id, _adm_t('ub_lit_50ffbd21b0e4'))
        return
    c = str(item.get("code") or "").strip()
    bot_username = await _get_user_bot_username(context)
    deep_link = _build_telegram_start_link(bot_username, c)
    if not deep_link:
        await context.bot.send_message(
            chat_id,
            _adm_t('ub_lit_06fd906729d8'),
        )
        return
    qr_image = _make_qr_image(deep_link)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_adm_t('ub_lit_76a467ee4372'), url=deep_link)],
        [InlineKeyboardButton(_adm_t('ub_lit_030adba70185'), callback_data=f"userbot:gifts:coupon:{c}")],
    ])
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=qr_image,
        caption=f"{_adm_t('ub_lit_6b621ab1fc3b')}{c}\n{deep_link}",
        reply_markup=kb,
    )


def _format_redemption_user(row: Dict[str, Any]) -> str:
    username = str(row.get("username") or "").strip()
    full_name = str(row.get("full_name") or "").strip()
    telegram_id = str(row.get("telegram_id") or "").strip()
    if username:
        return f"@{username}"
    if full_name:
        return full_name
    return telegram_id or f"user_id:{row.get('user_id')}"


def _format_gift_redemption_card(idx: int, row: Dict[str, Any]) -> str:
    code = str(row.get("code") or "-").strip()
    user_label = _format_redemption_user(row)
    telegram_id = str(row.get("telegram_id") or "-").strip()
    amount = int(row.get("amount_toman") or 0)
    wallet = _format_toman(row.get("wallet_balance"))
    redeemed_at = str(row.get("redeemed_at") or "-").strip()
    amount_text = f"{_format_toman(amount)}{_adm_t('ub_lit_f6ac3483a71a')}" if amount > 0 else _adm_t('ub_lit_4e2ba4db8557')

    return "\n".join(
        [
            f"#{idx}  🏷 {code}",
            f"{_adm_t('ub_lit_9a3903dac8b0')}{user_label}",
            f"{_adm_t('ub_lit_18386e72b90a')}{telegram_id}",
            f"{_adm_t('ub_lit_bf97eb94d5db')}{amount_text}",
            f"{_adm_t('ub_lit_f5e260100077')}{wallet}{_adm_t('ub_lit_f6ac3483a71a')}",
            f"{_adm_t('ub_lit_9c7cb5396b78')}{redeemed_at}",
        ]
    )


async def send_zarin_redemptions_report(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    code: str = "",
    message=None,
) -> None:
    rows = userbot_db.list_zarin_voucher_redemptions(limit=50, code=code)
    title = f"{_adm_t('ub_lit_b770a63e3be8')}{code}" if code else _adm_t('ub_lit_e625de0deb16')
    total_amount = sum(int(row.get("amount_toman") or 0) for row in rows)
    unique_users = len({int(row.get("user_id") or 0) for row in rows if int(row.get("user_id") or 0) > 0})
    lines = [
        title,
        "❖ ◈━━━━━━━━━━━━━━━━━━━━◈ ❖",
        f"{_adm_t('ub_lit_79a6ae50ad3b')}{len(rows)}",
        f"{_adm_t('ub_lit_91130bd47e98')}{unique_users}",
        f"{_adm_t('ub_lit_c26f77c5c483')}{_format_toman(total_amount)}{_adm_t('ub_lit_f6ac3483a71a')}",
        "",
    ]
    if not rows:
        lines.append(_adm_t('ub_lit_c0adfec4e172'))
    else:
        lines.append(_adm_t('ub_lit_3448b1b634f3'))
        lines.append("━━━━━━━━━━━━━━━━")
        for idx, row in enumerate(rows, 1):
            lines.append(_format_gift_redemption_card(idx, row))
            if idx != len(rows):
                lines.append("────────────")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_adm_t('ub_lit_98dc8fa1c6d4'), callback_data="userbot:gifts:coupons")],
        [InlineKeyboardButton(_adm_t('ub_lit_c5cd51abdb6e'), callback_data="userbot:gifts:security")],
        [InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:gifts_menu")],
    ])
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3800] + _adm_t('ub_lit_ccb230ef029d')
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_gifts_campaign_text(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    coupons = userbot_db.list_zarin_vouchers(limit=20)
    active_codes: List[str] = []
    for item in coupons:
        icon, _ = _zarin_coupon_status(item)
        if icon == "🟢":
            active_codes.append(str(item.get("code") or "").strip())
        if len(active_codes) >= 5:
            break
    sample_code = active_codes[0] if active_codes else "GIFT-CODE"
    bot_username = await _get_user_bot_username(context)
    deep_link = _build_telegram_start_link(bot_username, sample_code) or f"{_adm_t('ub_lit_5f9b2588a592')}{sample_code}"
    amount = 0
    if active_codes:
        item = userbot_db.get_zarin_voucher(sample_code) or {}
        amount = int(item.get("amount_toman") or 0)
    text = (
        f"{_adm_t('ub_lit_d2cf91badde9')}{_build_gift_campaign_copy(sample_code, deep_link, amount or 0)}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_adm_t('ub_lit_80c0569c75f6'), callback_data="userbot:gifts:presets")],
        [InlineKeyboardButton(_adm_t('ub_lit_956f451107e4'), callback_data="userbot:gifts:bulk")],
        [InlineKeyboardButton(_adm_t('ub_lit_260de9a11858'), callback_data="userbot:gifts:coupons")],
        [InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:gifts_menu")],
    ])
    if message:
        try:
            await message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)


async def send_gifts_help(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = (
        _adm_t('ub_lit_94cdf892a63d')
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_adm_t('ub_lit_5159299b55e3'), callback_data="userbot:gifts:dashboard")],
        [InlineKeyboardButton(_adm_t('ub_lit_b89bf356fe97'), callback_data="userbot:gifts:presets")],
        [InlineKeyboardButton(_adm_t('ub_lit_c5cd51abdb6e'), callback_data="userbot:gifts:security")],
        [InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:gifts_menu")],
    ])
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


# ===============================
#   بخش رفرال (دعوت دوستان)
# ===============================

def build_referral_admin_menu_keyboard() -> InlineKeyboardMarkup:
    try:
        settings = userbot_db.get_referral_settings()
    except Exception:
        settings = {}
    enabled_icon = "✅" if bool(settings.get("referral_enabled", False)) else "❌"
    rows = [
        [InlineKeyboardButton(_adm_t('ub_lit_74ffc25b9e16'), callback_data="userbot:referral:dashboard")],
        [
            InlineKeyboardButton(_adm_t('ub_lit_7a1e1a74a212'), callback_data="userbot:referral:settings"),
            InlineKeyboardButton(f"{_adm_t('ub_lit_b2508137d51c')}{enabled_icon}", callback_data="userbot:referral:toggle"),
        ],
        [
            InlineKeyboardButton(_adm_t('ub_lit_f7d7a040a5d6'), callback_data="userbot:referral:list:1"),
            InlineKeyboardButton(_adm_t('ub_lit_0b624017a44d'), callback_data="userbot:referral:rewards:1"),
        ],
        [InlineKeyboardButton(_adm_t('ub_lit_26403bb1b929'), callback_data="userbot:referral:manual")],
        [InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_referral_settings_keyboard() -> InlineKeyboardMarkup:
    try:
        settings = userbot_db.get_referral_settings()
    except Exception:
        settings = {}
    trial_icon = "✅" if bool(settings.get("trial_reward_enabled", True)) else "❌"
    purchase_icon = "✅" if bool(settings.get("purchase_reward_enabled", True)) else "❌"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{_adm_t('ub_lit_0613ad29d6f6')}{trial_icon}", callback_data="userbot:referral:toggle:trial_reward_enabled")],
        [InlineKeyboardButton(f"{_adm_t('ub_lit_925691336f67')}{purchase_icon}", callback_data="userbot:referral:toggle:purchase_reward_enabled")],
        [InlineKeyboardButton(_adm_t('ub_lit_018fddd73811'), callback_data="userbot:referral:edit:trial_reward_amount")],
        [InlineKeyboardButton(_adm_t('ub_lit_3bcd12c46741'), callback_data="userbot:referral:edit:purchase_reward_amount")],
        [InlineKeyboardButton(_adm_t('ub_lit_db24b18922a5'), callback_data="userbot:referral:edit:max_successful_referrals")],
        [InlineKeyboardButton(_adm_t('ub_lit_2bc44f60e2e5'), callback_data="userbot:referral:edit:min_purchase_amount")],
        [InlineKeyboardButton(_adm_t('ub_lit_47b5cf054f58'), callback_data="userbot:referral:edit:invite_intro_text")],
        [InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:referral_menu")],
    ])


def _referral_setting_value_label(name: str, value: Any) -> str:
    if name == "trial_reward_amount":
        return f"{_adm_t('ub_lit_dd879789c45b')}{int(value or 0):f','}"
    if name == "purchase_reward_amount":
        return f"{_adm_t('ub_lit_4b2ee92eacf2')}{int(value or 0):f','}"
    if name == "max_successful_referrals":
        return f"{_adm_t('ub_lit_db7ec6dd678e')}{int(value or 0)}"
    if name == "min_purchase_amount":
        return f"{_adm_t('ub_lit_d3762f432a9a')}{int(value or 0):f','}"
    return str(value)


async def send_referral_admin_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    try:
        stats = userbot_db.get_referral_admin_stats()
    except Exception:
        stats = {}
    text = (
        f"{_adm_t('ub_lit_aa2ecac8669c')}{int(stats.get('total_referrals') or 0)}{_adm_t('ub_lit_61149b538c4f')}{int(stats.get('active_referrals') or 0)}{_adm_t('ub_lit_f1c49c01b785')}{int(stats.get('rejected_referrals') or 0)}{_adm_t('ub_lit_8b3afcb8b187')}{int(stats.get('fraud_flagged') or 0)}{_adm_t('ub_lit_7e8b983ca3eb')}{int(stats.get('trial_rewards_count') or 0)}{_adm_t('ub_lit_9abf728ed96c')}{int(stats.get('purchase_rewards_count') or 0)}{_adm_t('ub_lit_4812f4807c61')}{int(stats.get('total_reward_cost') or 0):f','}{_adm_t('ub_lit_dd1c8dc14d9d')}{int(stats.get('revenue_generated') or 0):f','}{_adm_t('ub_lit_e7ce4334a0c5')}{stats.get('conversion_rate') or 0}{_adm_t('ub_lit_20ca1dcf1005')}"
    )
    kb = build_referral_admin_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_referral_admin_settings(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    try:
        settings = userbot_db.get_referral_settings()
    except Exception:
        settings = {}
    enabled = _adm_t('ub_lit_f1bc469f39f7') if bool(settings.get("referral_enabled", False)) else _adm_t('ub_lit_fcc2f9a81e87')
    text = (
        f"{_adm_t('ub_lit_b6e1d8a89c88')}{enabled}\n{_referral_setting_value_label('trial_reward_amount', settings.get('trial_reward_amount'))}\n{_referral_setting_value_label('purchase_reward_amount', settings.get('purchase_reward_amount'))}\n{_referral_setting_value_label('max_successful_referrals', settings.get('max_successful_referrals'))}\n{_referral_setting_value_label('min_purchase_amount', settings.get('min_purchase_amount'))}\n"
    )
    kb = build_referral_settings_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_referral_list(chat_id: int, context: ContextTypes.DEFAULT_TYPE, page: int = 1, message=None) -> None:
    page = max(1, int(page or 1))
    page_size = 10
    try:
        refs, total = userbot_db.list_referrals(limit=page_size, offset=(page - 1) * page_size)
    except Exception:
        refs, total = [], 0
    if not refs:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:referral_menu")]])
        text = _adm_t('ub_lit_ab21edd10538')
        if message:
            try:
                await message.edit_text(text, reply_markup=kb)
            except BadRequest:
                await context.bot.send_message(chat_id, text, reply_markup=kb)
        else:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
        return

    total_pages = max(1, math.ceil(total / page_size))
    lines = [f"{_adm_t('ub_lit_8f4daa9d91db')}{total}{_adm_t('ub_lit_0de506e0ad95')}{page}/{total_pages})", "❖ ◈━━━━━━━━━━━━━━━━━━━━◈ ❖"]
    for ref in refs:
        inviter = str(ref.get("inviter_full_name") or ref.get("inviter_username") or ref.get("inviter_telegram_id") or "—")
        invitee = str(ref.get("invitee_full_name") or ref.get("invitee_username") or ref.get("invitee_telegram_id") or "—")
        status_icon = "🟢" if str(ref.get("status") or "") == "active" else "🔴"
        fraud_icon = "🚩" if int(ref.get("fraud_flag") or 0) else ""
        qualified = "" if int(ref.get("invitee_qualified", 1)) else _adm_t('ub_lit_86d03fa2f66e')
        lines.append(f"#{ref.get('id')} | {inviter} ⟵ {invitee} {status_icon}{fraud_icon}{qualified}")
    text = "\n".join(lines)

    rows = []
    row: List[Any] = []
    for ref in refs:
        row.append(InlineKeyboardButton(f"#{ref.get('id')}", callback_data=f"userbot:referral:detail:{ref.get('id')}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(_adm_t('ub_lit_aa428b81d720'), callback_data=f"userbot:referral:list:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(_adm_t('ub_lit_24d05894b963'), callback_data=f"userbot:referral:list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:referral_menu")])
    kb = InlineKeyboardMarkup(rows)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_referral_detail(referral_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    ref = userbot_db.get_referral_by_id(int(referral_id or 0))
    if not ref:
        await context.bot.send_message(chat_id, _adm_t('ub_lit_a496c88ae82b'))
        return
    try:
        rewards, _ = userbot_db.list_referral_rewards(limit=20, inviter_id=int(ref.get("inviter_id") or 0))
    except Exception:
        rewards = []
    related = [r for r in rewards if int(r.get("referral_id") or 0) == int(ref.get("id") or 0)]

    inviter_db = userbot_db.get_user_by_id(int(ref.get("inviter_id") or 0)) or {}
    invitee_db = userbot_db.get_user_by_id(int(ref.get("invitee_id") or 0)) or {}
    labels = userbot_db.REFERRAL_REWARD_LABELS

    reward_lines = []
    for rw in related:
        label = labels.get(str(rw.get("reward_type") or ""), str(rw.get("reward_type") or ""))
        amount = int(rw.get("amount_toman") or 0)
        status_icon = "✅" if str(rw.get("status") or "") == "paid" else "🔻"
        reward_lines.append(f"• {label}: {amount:f','}{_adm_t('ub_lit_8180043839cb')}{status_icon}")

    text = (
        f"{_adm_t('ub_lit_0827005e8e3f')}{ref.get('id')}{_adm_t('ub_lit_55c13a9592a8')}{inviter_db.get('full_name') or inviter_db.get('username') or '—'} (ID: {ref.get('inviter_id')}{_adm_t('ub_lit_0c627bb0d35d')}{invitee_db.get('full_name') or invitee_db.get('username') or '—'} (ID: {ref.get('invitee_id')}{_adm_t('ub_lit_f40be1ec7928')}{ref.get('status')}{_adm_t('ub_lit_d91719d49012')}{_adm_t('ub_lit_5e8fdd3b40a3') if int(ref.get('fraud_flag') or 0) else _adm_t('ub_lit_36f5ebadb70f')}{_adm_t('ub_lit_96d3de206fba')}{_adm_t('ub_lit_5e8fdd3b40a3') if int(ref.get('invitee_qualified', 1)) else _adm_t('ub_lit_36f5ebadb70f')}{_adm_t('ub_lit_f6b275411746')}{ref.get('created_at') or '—'}\n"
    )
    if reward_lines:
        text += _adm_t('ub_lit_d4382309d428') + "\n".join(reward_lines)
    if str(ref.get("rejection_reason") or "").strip():
        text += f"{_adm_t('ub_lit_e4e6bd03d8c6')}{ref.get('rejection_reason')}"

    status = str(ref.get("status") or "").lower()
    fraud = int(ref.get("fraud_flag") or 0)
    rows = []
    if status == "active":
        rows.append([InlineKeyboardButton(_adm_t('ub_lit_b8a25bac25d9'), callback_data=f"userbot:referral:reject:{ref.get('id')}")])
    else:
        rows.append([InlineKeyboardButton(_adm_t('ub_lit_02740672858f'), callback_data=f"userbot:referral:activate:{ref.get('id')}")])
    fraud_label = _adm_t('ub_lit_f4e5a43e6c4c') if fraud else _adm_t('ub_lit_f2597d4e671b')
    rows.append([InlineKeyboardButton(fraud_label, callback_data=f"userbot:referral:fraud:{ref.get('id')}")])
    for rw in related:
        if str(rw.get("status") or "") == "paid":
            rows.append([
                InlineKeyboardButton(
                    f"{_adm_t('ub_lit_94dfe2215003')}{rw.get('id')}",
                    callback_data=f"userbot:referral:revoke_reward:{rw.get('id')}",
                )
            ])
    rows.append([InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:referral:list:1")])
    kb = InlineKeyboardMarkup(rows)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_referral_rewards_list(chat_id: int, context: ContextTypes.DEFAULT_TYPE, page: int = 1, message=None) -> None:
    page = max(1, int(page or 1))
    page_size = 10
    try:
        rewards, total = userbot_db.list_referral_rewards(limit=page_size, offset=(page - 1) * page_size)
    except Exception:
        rewards, total = [], 0
    if not rewards:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:referral_menu")]])
        text = _adm_t('ub_lit_d5bfa4163bc3')
        if message:
            try:
                await message.edit_text(text, reply_markup=kb)
            except BadRequest:
                await context.bot.send_message(chat_id, text, reply_markup=kb)
        else:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
        return

    total_pages = max(1, math.ceil(total / page_size))
    labels = userbot_db.REFERRAL_REWARD_LABELS
    lines = [f"{_adm_t('ub_lit_b5801a8790de')}{total}{_adm_t('ub_lit_0de506e0ad95')}{page}/{total_pages})", "❖ ◈━━━━━━━━━━━━━━━━━━━━◈ ❖"]
    for rw in rewards:
        label = labels.get(str(rw.get("reward_type") or ""), str(rw.get("reward_type") or ""))
        inviter = str(rw.get("inviter_full_name") or rw.get("inviter_username") or rw.get("inviter_id") or "—")
        amount = int(rw.get("amount_toman") or 0)
        status_icon = "✅" if str(rw.get("status") or "") == "paid" else "🔻"
        lines.append(f"#{rw.get('id')} | {label} | {inviter} | {amount:f','}{_adm_t('ub_lit_8180043839cb')}{status_icon}")
    text = "\n".join(lines)

    rows = []
    row: List[Any] = []
    for rw in rewards:
        status_icon = "✅" if str(rw.get("status") or "") == "paid" else "🔻"
        row.append(InlineKeyboardButton(f"#{rw.get('id')}{status_icon}", callback_data=f"userbot:referral:reward:{rw.get('id')}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(_adm_t('ub_lit_aa428b81d720'), callback_data=f"userbot:referral:rewards:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(_adm_t('ub_lit_24d05894b963'), callback_data=f"userbot:referral:rewards:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:referral_menu")])
    kb = InlineKeyboardMarkup(rows)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_referral_reward_detail(reward_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    rw = userbot_db.get_referral_reward(int(reward_id or 0))
    if not rw:
        await context.bot.send_message(chat_id, _adm_t('ub_lit_a8e733bbe018'))
        return
    labels = userbot_db.REFERRAL_REWARD_LABELS
    inviter_db = userbot_db.get_user_by_id(int(rw.get("inviter_id") or 0)) or {}
    invitee_db = userbot_db.get_user_by_id(int(rw.get("invitee_id") or 0)) or {}
    invitee_label = (
        f"{invitee_db.get('full_name') or invitee_db.get('username') or '—'} (ID: {rw.get('invitee_id')})"
        if invitee_db else "—"
    )
    text = (
        f"{_adm_t('ub_lit_d97228543d20')}{rw.get('id')}{_adm_t('ub_lit_ef4d4bf79f16')}{labels.get(str(rw.get('reward_type') or ''), str(rw.get('reward_type') or ''))}{_adm_t('ub_lit_d2df61e90759')}{rw.get('reward_source')}{_adm_t('ub_lit_cc328a6eb822')}{inviter_db.get('full_name') or inviter_db.get('username') or '—'} (ID: {rw.get('inviter_id')}{_adm_t('ub_lit_0c627bb0d35d')}{invitee_label}{_adm_t('ub_lit_0835446733bd')}{int(rw.get('amount_toman') or 0):f','}{_adm_t('ub_lit_6ec3b003fad1')}{rw.get('voucher_code') or '—'}{_adm_t('ub_lit_82d2d76ffde9')}{int(rw.get('payment_id') or 0) or '—'}{_adm_t('ub_lit_18db1bfd572c')}{rw.get('status')}{_adm_t('ub_lit_f6b275411746')}{rw.get('created_at') or '—'}\n"
    )
    if str(rw.get("revoked_at") or "").strip():
        text += f"{_adm_t('ub_lit_23b07572db63')}{rw.get('revoked_at')}"
    rows = []
    if str(rw.get("status") or "") == "paid":
        rows.append([InlineKeyboardButton(_adm_t('ub_lit_5e8daac876a1'), callback_data=f"userbot:referral:revoke_reward:{rw.get('id')}")])
    rows.append([InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:referral:rewards:1")])
    kb = InlineKeyboardMarkup(rows)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_userbot_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_02a4a16af7a9')
    kb = build_userbot_settings_menu_keyboard(_get_ui_settings())
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_colored_buttons_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    settings = _get_ui_settings()
    enabled = bool(settings.get("colored_buttons", True))
    theme = normalize_button_theme(settings.get("button_theme"))
    theme_meta = BUTTON_STYLE_THEMES.get(theme, BUTTON_STYLE_THEMES["smart"])
    status = _adm_t('ub_lit_cc1ff85dd8e6') if enabled else _adm_t('ub_lit_08b951f23eae')
    descriptions = "\n".join(
        f"{'✅' if key == theme else '▫️'} {meta['title']}: {meta['description']}"
        for key, meta in BUTTON_STYLE_THEMES.items()
    )
    text = (
        f"{_adm_t('ub_lit_29493b91852f')}{status}{_adm_t('ub_lit_920651a02ff8')}{theme_meta['title']}\n\n{descriptions}{_adm_t('ub_lit_b064865eb22b')}"
    )
    kb = build_colored_buttons_settings_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_subscription_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_687c62e630e9')
    settings = _get_subscription_settings(context)
    kb = build_subscription_settings_menu_keyboard(
        show_user_page_link=settings["show_user_page_link"],
        show_username=settings["show_username"],
        shuffle_configs=settings["shuffle_configs"],
        shuffle_server_layout=settings["shuffle_server_layout"],
        shuffle_config_layout=settings["shuffle_config_layout"],
    )
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)

async def send_sub_link_status_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    current_base = userbot_db.get_managed_sub_base_url()
    current_base_text = current_base if current_base else _adm_t('ub_lit_ae46fa38dea8')
    text = (
        f"{_adm_t('ub_lit_3a84fa572778')}{current_base_text}"
    )
    settings = _get_subscription_settings(context)
    kb = build_sub_link_status_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_sub_status_reminder_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    reminder = _get_sub_reminder_settings()
    text = _adm_t('ub_lit_687c62e630e9')
    kb = build_sub_status_reminder_menu_keyboard(reminder)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_trial_spec_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    spec = _get_trial_spec_settings()
    text = _adm_t('ub_lit_687c62e630e9')
    kb = build_trial_spec_menu_keyboard(spec)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_buy_renew_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_afb60db755b7')
    settings = _get_buy_renew_settings()
    kb = build_buy_renew_settings_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_tx_plans_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_e860c9645679')
    settings = _get_tx_plans_settings()
    kb = build_tx_plans_settings_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_text_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_bc9e898ba647')
    kb = build_text_settings_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_guide_text_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_7309defe7c2d')
    kb = build_guide_text_settings_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_invite_text_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_e6cf02596d2f')
    kb = build_invite_text_settings_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_marketing_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    settings = _get_marketing_settings()
    text = _adm_t('ub_lit_5995f47cd544')
    kb = build_marketing_settings_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_force_join_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    settings = _get_force_join_settings()
    channel_disp = (
        ("@" + str(settings.get("channel_username") or "").strip().lstrip("@"))
        if str(settings.get("channel_username") or "").strip()
        else str(settings.get("channel_id") or "").strip() or "—"
    )
    text = (
        f"{_adm_t('ub_lit_316e1bdc2100')}{channel_disp}"
    )
    kb = build_force_join_settings_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_payment_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    settings = _get_payment_settings()
    text = _adm_t("us_payment_settings")
    kb = build_payment_settings_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_backup_restore_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    settings = _get_backup_restore_settings()
    text = _adm_t('ub_lit_f578d83a8ca1')
    kb = build_backup_restore_settings_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_payment_method_menu(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    method: str,
    message=None,
) -> None:
    settings = _get_payment_settings()
    key_map = {
        "card": "enable_card_to_card",
        "zarinpal": "enable_zarinpal",
        "perfect": "enable_perfect_money",
        "crypto": "enable_crypto",
    }
    key = key_map.get(method, "")
    enabled = bool(settings.get(key, False)) if key else False
    title_map = {
        "card": _adm_t('ub_lit_382c9247203e'),
        "zarinpal": _adm_t('ub_lit_f467f9ba2646'),
        "perfect": _adm_t('ub_lit_9b9fdba14bf1'),
        "crypto": _adm_t('ub_lit_750a545f9880'),
    }
    text = title_map.get(method, _adm_t("us_payment_settings"))
    kb = build_payment_method_menu_keyboard(method, enabled)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_sms_webhook_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    status = _sms_webhook_status()
    enabled = _adm_t('ub_lit_135fbb4fb4bd') if status.get("enabled") else _adm_t('ub_lit_e0f67de63f35')
    secret = str(status.get("secret") or "")
    secret_status = _mask_secret(secret)
    text = (
        f"{_adm_t('ub_lit_7ed168af2c69')}{enabled}\nSecret Key: {secret_status}{_adm_t('ub_lit_a80787fe6680')}{status.get('age')}{_adm_t('ub_lit_cd134390d2eb')}{html_escape(str(status.get('endpoint') or ''))}{_adm_t('ub_lit_90871b9b0d87')}"
    )
    kb = build_sms_webhook_settings_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            await context.bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


async def send_payment_cards_list_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    cards = database.get_cards()
    text = _adm_t('ub_lit_28e22efbe6f9')
    kb = build_payment_cards_list_keyboard(cards)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_payment_card_item_menu(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    number: str,
    message=None,
) -> None:
    card = database.get_card(number)
    if not card:
        if message:
            try:
                await message.edit_text(_adm_t('ub_lit_2963964ea45c'))
            except Exception:
                await context.bot.send_message(chat_id, _adm_t('ub_lit_2963964ea45c'))
        else:
            await context.bot.send_message(chat_id, _adm_t('ub_lit_2963964ea45c'))
        await send_payment_cards_list_menu(chat_id, context)
        return

    n = str(card.get("number") or "").strip() or "-"
    owner = str(card.get("owner") or "").strip() or "-"
    text = (
        f"{_adm_t('ub_lit_1a5f71be6f9c')}{n}{_adm_t('ub_lit_a624c3ee9b5d')}{owner}"
    )
    kb = build_payment_card_item_keyboard(n)
    if message:
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            await context.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


async def send_plan_categories_mode_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_e860c9645679')
    settings = _get_tx_plans_settings()
    kb = build_plan_categories_mode_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_plan_sort_mode_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_e860c9645679')
    settings = _get_tx_plans_settings()
    kb = build_plan_sort_mode_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_plan_columns_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_afb60db755b7')
    settings = _get_buy_renew_settings()
    kb = build_plan_columns_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_server_columns_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = _adm_t('ub_lit_afb60db755b7')
    settings = _get_buy_renew_settings()
    kb = build_server_columns_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_renew_policy_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    settings = _get_buy_renew_settings()
    days = int(settings.get("renew_max_days") or 3)
    usage = int(settings.get("renew_max_remaining_gb") or 3)
    policy = str(settings.get("renew_policy") or "advanced").strip().lower()
    policy_title = {"advanced": _adm_t('ub_lit_b9c0603306ff'), "default": _adm_t('ub_lit_8d125a15fc60'), "fair": _adm_t('ub_lit_f3c819cdcb0c')}.get(policy, _adm_t('ub_lit_b9c0603306ff'))
    volume_mode = str(settings.get("renew_volume_mode") or "").strip().lower()
    time_mode = str(settings.get("renew_time_mode") or "").strip().lower()
    if volume_mode not in {"add", "reset"}:
        volume_mode = "add" if policy in {"default", "fair"} else "reset"
    if time_mode not in {"add", "reset"}:
        time_mode = "add" if policy == "fair" else "reset"
    volume_text = _adm_t('ub_lit_4f20b39bb8ce') if volume_mode == "add" else _adm_t('ub_lit_46f6951526d5')
    time_text = _adm_t('ub_lit_4f20b39bb8ce') if time_mode == "add" else _adm_t('ub_lit_46f6951526d5')
    text = (
        f"{_adm_t('ub_lit_42f87bd98e28')}{policy_title}{_adm_t('ub_lit_1fe5cf65a7b4')}{volume_text}{_adm_t('ub_lit_024e2035262e')}{time_text}{_adm_t('ub_lit_3e5975438bc7')}{days}{_adm_t('ub_lit_0b1252e09b27')}{usage}{_adm_t('ub_lit_e5fc3be2014a')}"
    )
    kb = build_renew_policy_menu_keyboard(settings)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_reset_free_trial_confirm(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = (
        _adm_t('ub_lit_1db3e812c718')
    )
    kb = build_reset_free_trial_confirm_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_orders_list(page: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    """لیست سفارشات (هدر آماری کامل + لیست گرید 3 ستونه)"""
    if page < 1: page = 1
    
    # گرفتن داده‌ها
    # تابع get_orders_page فقط لیست سفارشات (برای دکمه‌ها) را می‌دهد
    orders, _ = userbot_db.get_orders_page(page, USERBOT_PAGE_SIZE)
    # تابع get_orders_stats آمار کل، ۳۰ روزه و ماه جاری را می‌دهد
    stats = userbot_db.get_orders_stats()
    
    # محاسبه تعداد صفحات
    total_count = stats['total_count']
    total_pages = max(1, math.ceil(total_count / USERBOT_PAGE_SIZE))
    if page > total_pages: 
        page = total_pages
        # اگر صفحه تغییر کرد دوباره سفارشات اون صفحه رو بگیر
        orders, _ = userbot_db.get_orders_page(page, USERBOT_PAGE_SIZE)

    # تابع برای جدا کردن سه رقم سه رقم
    def fmt(val): 
        try:
            return f"{int(val):,}"
        except:
            return str(val)

    # --- ساخت متن پیام دقیقاً طبق عکس ---
    # داده‌ها از دیکشنری stats خوانده می‌شوند
    
    lines = [
        _adm_t('ub_lit_7cf5e2e43617'),
        f"{_adm_t('ub_lit_f9aee7068ade')}{stats['total_count']}",
        f"{_adm_t('ub_lit_f59c1811609a')}{fmt(stats['total_gb'])}",
        f"{_adm_t('ub_lit_42e0d913c805')}{fmt(stats['total_price'])}{_adm_t('ub_lit_9e29f6087438')}",
        "❖ ⬩----------------------------------⬩ ❖",
        f"{_adm_t('ub_lit_9576ea746aa4')}{stats['last30_count']}",
        f"{_adm_t('ub_lit_7244b0fb05b9')}{fmt(stats['last30_gb'])}",
        f"{_adm_t('ub_lit_24e15088e51e')}{fmt(stats['last30_price'])}{_adm_t('ub_lit_9e29f6087438')}",
        "❖ ⬩----------------------------------⬩ ❖",
        f"{_adm_t('ub_lit_20d53f2b7c8c')}{stats['month_count']}",
        f"{_adm_t('ub_lit_7666328876c7')}{fmt(stats['month_gb'])}",
        f"{_adm_t('ub_lit_751a63dab25a')}{fmt(stats['month_price'])}{_adm_t('ub_lit_9e29f6087438')}"
    ]
    text = "\n".join(lines)

    # --- ساخت دکمه‌ها (3 ستونه) ---
    rows = []
    current_row = []
    
    for o in orders:
        # نمایش شناسه سفارش روی دکمه
        oid = str(o.get('order_id') or o.get('id'))
        current_row.append(
            InlineKeyboardButton(oid, callback_data=f"userbot:order:{oid}")
        )
        
        # اگر 3 تا شد، برو سطر بعد
        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []
            
    # اگر چیزی ته لیست مانده بود
    if current_row:
        rows.append(current_row)

    # دکمه‌های صفحه بندی
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"userbot:orders:list:{page-1}"))
    
    # دکمه وسط شماره صفحه (غیر فعال)
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="userbot:noop"))
    
    if page < total_pages:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"userbot:orders:list:{page+1}"))
    
    rows.append(nav)

    # دکمه بازگشت
    rows.append([InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:menu")])
    
    kb = InlineKeyboardMarkup(rows)
    
    # ارسال پیام
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)

async def send_order_detail(order_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    order = userbot_db.get_order_by_id(order_id)
    if not order:
        await context.bot.send_message(chat_id, _adm_t('ub_lit_7c9ac47051f9'))
        return

    text = (
        f"{_adm_t('ub_lit_eb5940f7a7d5')}{order.get('order_id')}{_adm_t('ub_lit_58bf58a8228f')}{_display_name(order)}{_adm_t('ub_lit_d102d9511c0a')}{order.get('created_at')}{_adm_t('ub_lit_6de942617fe4')}{order.get('plan_title')}{_adm_t('ub_lit_8fa035765635')}{_format_toman(order.get('price'))}{_adm_t('ub_lit_fe23cefdf304')}{order.get('status', _adm_t('ub_lit_9ee9b55b6605'))}"
    )
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:orders_menu")]])
    
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


def _ticket_bucket_title(status: str) -> str:
    s = str(status or "").strip().lower()
    if s == "open":
        return _adm_t('ub_lit_3d534da229c2')
    if s == "closed":
        return _adm_t('ub_lit_2c4064cc56f7')
    return _adm_t('ub_lit_e24eb17fdeee')


async def send_tickets_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    stats = userbot_db.get_tickets_stats()
    text = _build_tickets_stats_text(stats)
    kb = build_tickets_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except BadRequest:
            pass
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)


async def send_broadcast_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    stats = userbot_db.get_broadcast_stats()
    text = _build_broadcast_stats_text(stats)
    kb = build_broadcast_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except BadRequest:
            pass
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)


async def _send_broadcast_to_targets(
    context: ContextTypes.DEFAULT_TYPE,
    telegram_ids: List[int],
    text: str,
    photo_file_id: str = "",
) -> Tuple[int, int]:
    if not USER_BOT_TOKEN:
        raise RuntimeError(_adm_t('ub_lit_29b006b6d4f5'))

    user_bot = Bot(token=USER_BOT_TOKEN)
    body = str(text or "").strip()
    photo_id = str(photo_file_id or "").strip()
    sent_count = 0
    fail_count = 0

    for tg_id in telegram_ids:
        try:
            if photo_id:
                if len(body) <= 1024:
                    await user_bot.send_photo(chat_id=tg_id, photo=photo_id, caption=body)
                else:
                    await user_bot.send_photo(chat_id=tg_id, photo=photo_id)
                    await user_bot.send_message(chat_id=tg_id, text=body)
            else:
                await user_bot.send_message(chat_id=tg_id, text=body)
            sent_count += 1
        except Exception:
            fail_count += 1
        await asyncio.sleep(0.03)

    return sent_count, fail_count


async def send_tickets_list(
    status: str,
    page: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    s = str(status or "pending").strip().lower()
    if s not in {"pending", "open", "closed"}:
        s = "pending"
    p = max(1, int(page or 1))
    tickets, total = userbot_db.get_tickets_page(s, p, TICKETS_PAGE_SIZE)
    total_pages = max(1, math.ceil(int(total) / TICKETS_PAGE_SIZE))
    if p > total_pages:
        p = total_pages
        tickets, total = userbot_db.get_tickets_page(s, p, TICKETS_PAGE_SIZE)
    text = (
        f"{_ticket_bucket_title(s)}{_adm_t('ub_lit_a89c572882a4')}{int(total)}"
    )
    kb = build_tickets_list_keyboard(tickets, status=s, page=p, total_pages=total_pages)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except BadRequest:
            pass
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)


async def send_user_tickets_list(
    user_id: int,
    page: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    uid = int(user_id or 0)
    if uid <= 0:
        await context.bot.send_message(chat_id, _adm_t('ub_lit_9024444be650'))
        return
    user = userbot_db.get_user_by_id(uid) or {}
    tickets, total = userbot_db.get_tickets_for_user(uid, page=max(1, int(page or 1)), page_size=TICKETS_PAGE_SIZE)
    total_pages = max(1, math.ceil(int(total) / TICKETS_PAGE_SIZE))
    p = max(1, min(int(page or 1), total_pages))
    if p != int(page or 1):
        tickets, total = userbot_db.get_tickets_for_user(uid, page=p, page_size=TICKETS_PAGE_SIZE)
    display = _display_name(user) if user else str(uid)
    text = (
        f"{_adm_t('ub_lit_676d102cf101')}{display}{_adm_t('ub_lit_a89c572882a4')}{int(total)}"
    )
    kb = build_tickets_list_keyboard(
        tickets,
        status="pending",
        page=p,
        total_pages=total_pages,
        from_user_id=uid,
    )
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except BadRequest:
            pass
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)


async def send_ticket_detail(
    ticket_code: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    list_status: str = "pending",
    page: int = 1,
    from_user_id: int = 0,
    message=None,
    send_new: bool = False,
) -> None:
    code = int(ticket_code or 0)
    if code <= 0:
        await context.bot.send_message(chat_id, _adm_t('ub_lit_649771e664cf'))
        return
    ticket = userbot_db.get_ticket_by_code(code)
    if not ticket:
        await context.bot.send_message(chat_id, _adm_t('ub_lit_6b02b8271e76'))
        return
    messages = userbot_db.get_ticket_messages(code)
    photo_refs = _collect_ticket_photo_refs(messages)
    screenshot_links = await _build_ticket_screenshot_links(context, code, photo_refs) if photo_refs else {}
    text = _build_ticket_detail_text(ticket, messages, screenshot_links=screenshot_links)
    if len(text) > 3900:
        # اگر طول متن زیاد شد، نسخه ساده و بدون لینک را نمایش می‌دهیم.
        text = _build_ticket_detail_text(ticket, messages, screenshot_links=None)
        if len(text) > 3900:
            text = text[:3890] + "\n..."
    kb = build_ticket_detail_keyboard(
        ticket,
        list_status=list_status,
        page=max(1, int(page or 1)),
        from_user_id=max(0, int(from_user_id or 0)),
    )

    if message and not send_new:
        try:
            await message.edit_text(
                text,
                reply_markup=kb,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except BadRequest:
            pass
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

# ---------------------------------------------------------
# PART 3: HANDLERS & DISPATCHERS
# ---------------------------------------------------------

async def handle_userbot_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر ورود به منوی مدیریت ربات کاربران (از دکمه اصلی ادمین)"""
    message = update.message or update.callback_query.message
    await send_userbot_main_menu(message.chat_id, context, message=message)


async def handle_user_search_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """جستجوی کاربر (نام یا آیدی)"""
    message = update.message
    text = (message.text or "").strip()
    state = context.user_data.get(USER_SEARCH_STATE_KEY)

    if text in CANCEL_WORDS:
        context.user_data.pop(USER_SEARCH_STATE_KEY, None)
        await message.reply_text(_adm_t("admin_search_cancelled"), reply_markup=admin_main_keyboard())
        return

    # جستجو با Telegram ID
    if state == "by_id":
        if not text.isdigit():
            await message.reply_text(_adm_t("ub_number_only"))
            return
        results = userbot_db.search_users_by_telegram_id(int(text))
    else:
        if not text:
            await message.reply_text(_adm_t("ub_user_name_prompt"))
            return
        # جستجو با نام
        results = userbot_db.search_users_by_name(text)

    context.user_data.pop(USER_SEARCH_STATE_KEY, None)

    if not results:
        await message.reply_text(_adm_t("adm_err_user_not_found"), reply_markup=admin_main_keyboard())
        return
    
    if len(results) == 1:
        await message.reply_text(_adm_t("ub_user_found"), reply_markup=admin_main_keyboard())
        await send_user_profile(results[0]['id'], message.chat_id, context)
        return

    # نمایش لیست نتایج
    lines = [_adm_t('ub_lit_e46fa9ad3d4e')]
    rows = []
    for u in results:
        rows.append([InlineKeyboardButton(_display_name(u), callback_data=f"userbot:user:{u['id']}")])
    rows.append([InlineKeyboardButton(_adm_t("btn_back"), callback_data="userbot:users_menu")])
    
    await message.reply_text(_adm_t("ub_user_found"), reply_markup=admin_main_keyboard())
    await message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def handle_payment_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """جستجوی تراکنش با ID"""
    message = update.message
    text = (message.text or "").strip()

    if text in CANCEL_WORDS:
        context.user_data.pop(PAYMENT_SEARCH_STATE, None)
        await message.reply_text(_adm_t("admin_search_cancelled"), reply_markup=admin_main_keyboard())
        return

    if not text.isdigit():
        await message.reply_text(_adm_t("ub_payment_id_prompt"))
        return

    pid = int(text)
    pay = userbot_db.get_payment_by_id(pid)
    
    context.user_data.pop(PAYMENT_SEARCH_STATE, None)

    if pay:
        await message.reply_text(_adm_t("ub_payment_found"), reply_markup=admin_main_keyboard())
        await send_payment_detail(pid, message.chat_id, context)
    else:
        await message.reply_text(_adm_t("ub_payment_id_not_found", id=pid), reply_markup=admin_main_keyboard())


async def handle_orders_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """جستجوی سفارش با ID"""
    message = update.message
    text = (message.text or "").strip()

    if text in CANCEL_WORDS:
        context.user_data.pop(ORDERS_SEARCH_STATE_KEY, None)
        await message.reply_text(_adm_t("admin_search_cancelled"), reply_markup=admin_main_keyboard())
        return

    if not text.isdigit():
        await message.reply_text(_adm_t("ub_order_id_prompt"))
        return

    oid = int(text)
    order = userbot_db.get_order_by_id(oid)

    context.user_data.pop(ORDERS_SEARCH_STATE_KEY, None)

    if order:
        await message.reply_text(_adm_t("ub_order_found"), reply_markup=admin_main_keyboard())
        await send_order_detail(oid, message.chat_id, context)
    else:
        await message.reply_text(_adm_t("ub_order_id_not_found", id=oid), reply_markup=admin_main_keyboard())


# در فایل AdminBot/userbot.py
# تابع handle_userbot_callback را با کد زیر جایگزین کنید:

async def handle_userbot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر اصلی تمام دکمه‌های ربات کاربران"""
    query = update.callback_query
    if not query: return
    data = (query.data or "").strip()
    msg = query.message
    cid = msg.chat_id

    # دکمه‌های نمایشی بدون عمل
    if data == "userbot:noop": 
        await query.answer()
        return

    # --- 1. منوی اصلی ---
    if data == "userbot:menu":
        await query.answer()
        await send_userbot_main_menu(cid, context, message=msg)
        return

    if data == "userbot:subs_menu":
        context.user_data[SUB_TRACKING_STATE] = True
        await query.answer()
        await send_subscription_tracking_prompt(cid, context, message=msg)
        return

    if data.startswith("userbot:subs:detail:"):
        await query.answer()
        try:
            sid = int(data.split(":")[3])
        except Exception:
            sid = 0
        if sid <= 0:
            context.user_data[SUB_TRACKING_STATE] = True
            await send_subscription_tracking_prompt(cid, context, message=msg)
            return
        service = userbot_db.get_service_by_id(sid)
        if not service:
            await msg.reply_text(_adm_t("ub_subscription_not_found"))
            context.user_data[SUB_TRACKING_STATE] = True
            await send_subscription_tracking_prompt(cid, context, message=msg)
            return
        await send_subscription_tracking_detail(
            cid,
            context,
            service=service,
            message=msg,
            edit=True,
        )
        return

    # --- 2. مدیریت کاربران (Users) ---
    if data == "userbot:users_menu":
        await query.answer()
        await send_users_menu(cid, context, message=msg)
        return

    # >>> بخش مهمی که باعث ارور شده بود <<<
    if data.startswith("userbot:users:"):
        await query.answer()
        try: 
            parts = data.split(":")
            # userbot:users:PAGE
            page = int(parts[2]) if len(parts) >= 3 else 1
        except: 
            page = 1
        await send_users_page(page, cid, context, message=msg)
        return
    # >>> ----------------------------- <<<

    if data == "userbot:users_search_menu":
        await query.answer()
        await msg.edit_text(_adm_t('ub_lit_c9c5fdce772b'), reply_markup=build_users_search_menu_keyboard())
        return

    if data.startswith("userbot:search:"):
        await query.answer()
        m = data.split(":")[2]
        context.user_data[USER_SEARCH_STATE_KEY] = "by_id" if m == "id" else "by_name"
        await msg.reply_text(
            f"{_adm_t('ub_lit_efa9ddae95b9')}{_adm_t('ub_lit_9d759792fb5a') if m == 'id' else _adm_t('ub_lit_d44a6f31a96e')}{_adm_t('ub_lit_d19849496859')}", 
            reply_markup=userbot_cancel_keyboard()
        )
        return

    # --- 3. پروفایل کاربر + اکشن‌ها ---
    if data.startswith("userbot:user:"):
        parts = data.split(":")
        if len(parts) < 3: 
            await query.answer(_adm_t('ub_lit_76d2e0c39c67'), show_alert=True)
            return
            
        uid = int(parts[2])
        
        # اگر فقط userbot:user:ID بود (بازگشت به پروفایل)
        if len(parts) == 3:
            await query.answer()
            await send_user_profile(uid, cid, context, message=msg)
            return
        
        act = parts[3]
        if act == "from_subs":
            back_callback = "userbot:subs_menu"
            if len(parts) >= 5:
                try:
                    sid = int(parts[4])
                except Exception:
                    sid = 0
                if sid > 0:
                    back_callback = f"userbot:subs:detail:{sid}"
            await query.answer()
            await send_user_profile(
                uid,
                cid,
                context,
                message=msg,
                back_callback=back_callback,
            )
            return
        if act == "services":
            await query.answer()
            await send_user_services_list(uid, cid, context, message=msg)
        elif act == "orders":
            pg = 1
            if len(parts)>=6 and parts[4]=="list": pg=int(parts[5])
            await query.answer()
            await send_user_orders_list(uid, pg, cid, context, message=msg)
        elif act == "payments":
            pg = 1
            if len(parts)>=6 and parts[4]=="list": pg=int(parts[5])
            await query.answer()
            await send_user_payments_list(uid, pg, cid, context, message=msg)
        
        elif act == "wallet":
            await query.answer()
            context.user_data[WALLET_EDIT_STATE] = uid
            await msg.reply_text(_adm_t('ub_lit_802a7d278c75'), reply_markup=userbot_cancel_keyboard())
        
        elif act == "message":
            await query.answer()
            context.user_data[MESSAGE_SEND_STATE] = {"user_id": int(uid)}
            await msg.reply_text(
                _adm_t('ub_lit_2da8e3e729d6'),
                reply_markup=userbot_cancel_keyboard(),
            )
        
        elif act == "reset_trial":
            userbot_db.reset_free_trial(uid)
            await query.answer(_adm_t('ub_lit_e5943a5940e0'), show_alert=True)
            await send_user_profile(uid, cid, context, message=msg)
        
        elif act == "ban":
            nst = userbot_db.toggle_ban_user(uid)
            alert_text = _adm_t('ub_lit_c52406fc222c') if nst else _adm_t('ub_lit_107005b9a08d')
            await query.answer(alert_text, show_alert=True)
            await send_user_profile(uid, cid, context, message=msg)
        
        elif act == "tickets":
            await query.answer()
            pg = 1
            if len(parts) >= 5:
                try:
                    pg = int(parts[4])
                except Exception:
                    pg = 1
            await send_user_tickets_list(uid, pg, cid, context, message=msg)
        
        return

    # --- 4. سرویس‌ها (Services) ---
    if data.startswith("userbot:svc:"):
        parts = data.split(":")
        if len(parts) < 3:
            await query.answer(_adm_t('ub_lit_aba30e3a701d'), show_alert=True)
            return
        try:
            service_id = int(parts[2])
        except Exception:
            await query.answer(_adm_t('ub_lit_7075a9646717'), show_alert=True)
            return

        action = parts[3] if len(parts) >= 4 else ""
        service = userbot_db.get_service_by_id(service_id)
        if not service:
            await query.answer(_adm_t('ub_lit_d3f440f81a00'), show_alert=True)
            return

        if not action:
            await query.answer()
            await send_service_detail(service_id, cid, context, message=msg)
            return

        target_server_id, target_user_uuid = _service_primary_target(service)
        if target_server_id <= 0 or not target_user_uuid:
            await query.answer(
                _adm_t('ub_lit_1a63ed0fef0b'),
                show_alert=True,
            )
            return

        # Local import to avoid module-load circular dependency.
        from AdminBot import servers as server_ops

        if action == "configs":
            await query.answer()
            await server_ops.send_user_configs_menu(
                target_server_id,
                target_user_uuid,
                cid,
                context,
                message=msg,
            )
            return

        if action == "edit":
            await query.answer()
            await server_ops.send_user_edit_menu(
                target_server_id,
                target_user_uuid,
                cid,
                context,
                message=msg,
            )
            return

        if action == "extend":
            await query.answer()
            await server_ops.send_user_extend_menu(
                target_server_id,
                target_user_uuid,
                cid,
                context,
                message=msg,
            )
            return

        if action == "delete":
            await query.answer()
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            _adm_t('ub_lit_0eae2c069e55'),
                            callback_data=f"userbot:svc:{service_id}:delete_yes",
                        ),
                        InlineKeyboardButton(
                            _adm_t('ub_lit_751a0da43792'),
                            callback_data=f"userbot:svc:{service_id}:delete_no",
                        ),
                    ]
                ]
            )
            await msg.edit_text(
                _adm_t('ub_lit_69fae50e59fb'),
                reply_markup=kb,
            )
            return

        if action == "delete_no":
            await query.answer(_adm_t('ub_lit_f4a4621817ae'), show_alert=True)
            await send_service_detail(service_id, cid, context, message=msg)
            return

        if action == "delete_yes":
            user_id = int(service.get("user_id") or 0)
            deleted_server_ids, failed_servers = await server_ops._delete_user_across_related_servers(
                target_server_id,
                target_user_uuid,
            )

            if not deleted_server_ids:
                details = "\n".join(failed_servers[:3])
                if details:
                    await msg.edit_text(f"{_adm_t('ub_lit_d2a385103a65')}{details}")
                else:
                    await msg.edit_text(_adm_t('ub_lit_594d4439eef4'))
                return

            try:
                userbot_db.delete_service(service_id)
            except Exception:
                pass

            # حذف نرم اشتراک از سیستم نمایندگی/مشتری (ردیف دیتابیس تا ۷ روز می‌ماند)
            try:
                from Shared import agent_db as _agn
                _agn.soft_delete_service_by_uuid(target_user_uuid, target_server_id)
            except Exception:
                pass

            if failed_servers:
                await query.answer(
                    f"{_adm_t('ub_lit_5377d83662ad')}{len(failed_servers)}{_adm_t('ub_lit_b10bc403694d')}",
                    show_alert=True,
                )
            else:
                await query.answer(_adm_t('ub_lit_ca2604ee1e33'), show_alert=True)

            if user_id > 0:
                await send_user_profile(user_id, cid, context, message=msg)
            else:
                await msg.edit_text(_adm_t('ub_lit_ca2604ee1e33'))
            return

        await query.answer(_adm_t('ub_lit_0376ef76612d'), show_alert=True)
        return

    # --- 5. سفارشات (Orders) ---
    if data == "userbot:orders_menu":
        await query.answer()
        await send_orders_menu(cid, context, message=msg)
        return
    if data.startswith("userbot:orders:list:"):
        await query.answer()
        pg = int(data.split(":")[-1])
        await send_orders_list(pg, cid, context, message=msg)
        return
    if data.startswith("userbot:order:"):
        await query.answer()
        oid = int(data.split(":")[-1])
        await send_order_detail(oid, cid, context, message=msg)
        return
    if data == "userbot:orders:search":
        await query.answer()
        context.user_data[ORDERS_SEARCH_STATE_KEY] = True
        await msg.reply_text(_adm_t('ub_lit_677595114cf7'), reply_markup=userbot_cancel_keyboard())
        return

    # --- 6. تراکنشات (Payments) ---
    if data == "userbot:payments_menu":
        await query.answer()
        await send_payments_menu(cid, context, message=msg)
        return
    if data.startswith("userbot:payments:list:"):
        await query.answer()
        parts = data.split(":")
        # userbot:payments:list:TYPE:PAGE
        f_type = parts[3]
        pg = int(parts[4]) if len(parts) > 4 else 1
        await send_payments_list(f_type, pg, cid, context, message=msg)
        return
    if data.startswith("userbot:pay:detail:"):
        await query.answer()
        pid = int(data.split(":")[-1])
        # Keep the payments list message intact; open details in a new message.
        await send_payment_detail(pid, cid, context)
        return
    if data.startswith("userbot:pay:chg:"):
        parts = data.split(":")
        # userbot:pay:chg:PID
        if len(parts) == 4:
            await query.answer()
            pid = int(parts[3])
            pay = userbot_db.get_payment_by_id(pid)
            if not pay:
                await query.answer(_adm_t('ub_lit_b23c299ea063'), show_alert=True)
                return
            text = _build_payment_detail_text(pay) + _adm_t('ub_lit_3bde8686c3ea')
            kb = _build_payment_change_confirm_keyboard(pid)
            receipt_raw = pay.get('receipt_image') or ""
            receipt_meta = _parse_receipt_meta(receipt_raw)
            receipt_admin_fid = receipt_meta.get("admin_fid")
            receipt_legacy = receipt_raw if receipt_raw and ":" not in receipt_raw and "|" not in receipt_raw else ""
            receipt_to_send = receipt_admin_fid or receipt_legacy
            if msg and getattr(msg, "photo", None):
                try:
                    await msg.edit_caption(caption=text, reply_markup=kb)
                    return
                except Exception:
                    pass
            if msg:
                try:
                    await msg.edit_text(text, reply_markup=kb)
                    return
                except Exception:
                    pass
            if receipt_to_send:
                try:
                    await context.bot.send_photo(chat_id=cid, photo=receipt_to_send, caption=text, reply_markup=kb)
                    return
                except Exception:
                    pass
            await context.bot.send_message(chat_id=cid, text=text, reply_markup=kb)
            return
        # userbot:pay:chg:yes|no:PID
        if len(parts) == 5:
            action = parts[3]
            pid = int(parts[4])
            await query.answer()
            if action == "no":
                await send_payment_detail(pid, cid, context, message=msg)
                return
            if action == "yes":
                pay = userbot_db.get_payment_by_id(pid)
                if not pay:
                    await query.answer(_adm_t('ub_lit_b23c299ea063'), show_alert=True)
                    return
                text = _build_payment_detail_text(pay) + _adm_t('ub_lit_ad6e92c58bce')
                kb = _build_payment_change_options_keyboard(pid, str(pay.get("status") or "pending"))
                if msg and getattr(msg, "photo", None):
                    try:
                        await msg.edit_caption(caption=text, reply_markup=kb)
                        return
                    except Exception:
                        pass
                try:
                    await msg.edit_text(text, reply_markup=kb)
                except Exception:
                    await context.bot.send_message(chat_id=cid, text=text, reply_markup=kb)
                return
        await query.answer(_adm_t('ub_lit_eb5b16527169'), show_alert=True)
        return
    if data.startswith("userbot:pay:set:"):
        parts = data.split(":")
        if len(parts) != 5:
            await query.answer(_adm_t('ub_lit_11a4e021aa73'), show_alert=True)
            return
        pid = int(parts[3])
        new_status = parts[4]
        prev_pay = userbot_db.get_payment_by_id(pid)
        if not prev_pay:
            await query.answer(_adm_t('ub_lit_b23c299ea063'), show_alert=True)
            return
        prev_status = str(prev_pay.get("status") or "pending").strip().lower()

        ok, msg_text, _ = userbot_db.change_payment_status_with_wallet(pid, new_status)
        if not ok:
            await query.answer(msg_text, show_alert=True)
            return

        await query.answer()
        if new_status == "approved":
            # بعد از تایید، پیام جزئیات تراکنش دوباره ارسال نشود.
            try:
                await msg.delete()
            except Exception:
                pass
            pay = userbot_db.get_payment_by_id(pid)
            if pay:
                if prev_status == "rejected":
                    _reset_direct_delivery_meta(pid)
                    await _notify_user_about_redelivery(pay)
                uid = int(pay.get("user_id") or 0)
                user_btn_title = (pay.get("full_name") or pay.get("username") or str(pay.get("telegram_id") or uid)).strip()
                kb = None
                if uid > 0:
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"👤 {user_btn_title}", callback_data=f"userbot:user:{uid}")]
                    ])
                report_text = _build_payment_approved_report_text(pay)
                if prev_status == "rejected":
                    report_text += _adm_t('ub_lit_7191f2d166c6')
                await context.bot.send_message(
                    chat_id=cid,
                    text=report_text,
                    reply_markup=kb,
                )
                await _send_auto_gift_message_if_needed(pay)
                await _send_payment_event_channel_report_if_enabled(pay)
            return

        if prev_status == "approved":
            await _notify_user_payment_status_change(prev_pay, new_status)
            try:
                await msg.delete()
            except Exception:
                pass
            ok_rev, rev_msg, failed = await _revert_approved_payment(userbot_db.get_payment_by_id(pid) or prev_pay)
            if not ok_rev:
                userbot_db.change_payment_status_with_wallet(pid, "approved")
                await context.bot.send_message(
                    chat_id=cid,
                    text=_adm_t('ub_lit_d2fc384b1bd0') + (rev_msg or ""),
                )
                return
            text = _build_payment_detail_text(userbot_db.get_payment_by_id(pid) or prev_pay)
            lines = [text]
            if rev_msg:
                lines.append(f"\n{rev_msg}")
            if failed:
                lines.append(_adm_t('ub_lit_dcbf864fb2b9') + _adm_t('ub_lit_8a78cc5f8f64').join(dict.fromkeys(failed)))
            await context.bot.send_message(chat_id=cid, text="\n".join(lines))
            return

        await send_payment_detail(
            pid,
            cid,
            context,
            message=msg,
            force_text_only=False,
        )
        return
    if data.startswith("userbot:pay:msg:"):
        await query.answer()
        pid = int(data.split(":")[-1])
        pay = userbot_db.get_payment_by_id(pid)
        if not pay:
            await query.answer(_adm_t('ub_lit_b23c299ea063'), show_alert=True)
            return
        uid = pay.get('user_id')
        if not uid:
            await query.answer(_adm_t('ub_lit_9024444be650'), show_alert=True)
            return
        context.user_data[MESSAGE_SEND_STATE] = {"user_id": int(uid)}
        await msg.reply_text(
            _adm_t('ub_lit_2da8e3e729d6'),
            reply_markup=userbot_cancel_keyboard(),
        )
        return
    if data == "userbot:payments:search":
        await query.answer()
        context.user_data[PAYMENT_SEARCH_STATE] = True
        await msg.reply_text(_adm_t('ub_lit_977b5719f18b'), reply_markup=userbot_cancel_keyboard())
        return
    if data.startswith("userbot:pay:act:"):
        parts = data.split(":")
        act = parts[3]
        pid = int(parts[4])
        
        pay = userbot_db.get_payment_by_id(pid)
        if pay and pay['status']=='pending':
            new_st = "approved" if act=="approve" else "rejected"
            ok, err, _ = userbot_db.change_payment_status_with_wallet(pid, new_st)
            if not ok:
                await query.answer(err, show_alert=True)
                return

            # نوتیفیکیشن به کاربر
            try:
                tg_id = pay.get('telegram_id')
                if tg_id and USER_BOT_TOKEN:
                    user_bot = Bot(token=USER_BOT_TOKEN)
                    amount_value = int(pay.get('amount') or 0)
                    amount_txt = _format_toman(amount_value)
                    pay_meta = _parse_receipt_meta(str(pay.get("receipt_image") or ""))
                    is_direct_buy = str(pay_meta.get("pay_flow") or "").strip().lower() == "direct_buy"
                    notify_text = ""
                    if new_st == "approved":
                        if is_direct_buy:
                            # تحویل مستقیم و پیام تأیید توسط ربات کاربران (حلقه direct_done) انجام
                            # می‌شود؛ از ارسال پیام تکراری «تراکنش تایید شد» در اینجا صرف‌نظر می‌کنیم.
                            notify_text = ""
                        else:
                            notify_text = _i18n_user_t(_user_lang_of(tg_id), "pay_approved_wallet_topup", amount=amount_txt)
                    else:
                        notify_text = _i18n_user_t(_user_lang_of(tg_id), "pay_rejected_no_credit")
                    if notify_text:
                        await user_bot.send_message(chat_id=tg_id, text=notify_text)
            except Exception as e:
                logger.warning(f"Failed to notify user for payment {pid}: {e}")
            
            await query.answer()
            # پیام قبلی (در انتظار) حذف شود و گزارش نهایی به‌صورت پیام جدید ارسال گردد.
            try:
                await msg.delete()
            except Exception:
                pass
            if new_st == "approved":
                pay_after = userbot_db.get_payment_by_id(pid)
                if pay_after:
                    uid = int(pay_after.get("user_id") or 0)
                    user_btn_title = (pay_after.get("full_name") or pay_after.get("username") or str(pay_after.get("telegram_id") or uid)).strip()
                    kb = None
                    if uid > 0:
                        kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton(f"👤 {user_btn_title}", callback_data=f"userbot:user:{uid}")]
                        ])
                    await context.bot.send_message(
                        chat_id=cid,
                        text=_build_payment_approved_report_text(pay_after),
                        reply_markup=kb,
                    )
                    await _send_auto_gift_message_if_needed(pay_after)
                    await _send_payment_event_channel_report_if_enabled(pay_after)
            else:
                await send_payment_detail(
                    pid,
                    cid,
                    context,
                    force_text_only=False,
                )
        else:
            status = str((pay or {}).get("status") or "").strip().lower() if pay else ""
            try:
                if status in {"approved", "rejected"}:
                    await msg.delete()
                else:
                    await msg.edit_reply_markup(reply_markup=None)
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    pass
            except Exception:
                pass
            if status == "approved":
                await query.answer(_adm_t('ub_lit_0b1e2a6d5850'), show_alert=True)
            elif status == "rejected":
                await query.answer(_adm_t('ub_lit_0499d8b9bd0b'), show_alert=True)
            else:
                await query.answer(_adm_t('ub_lit_7919f18e7423'), show_alert=True)
        return

    # --- 7. مدیریت تیکت‌ها ---
    if data == "userbot:tickets_menu":
        await query.answer()
        await send_tickets_menu(cid, context, message=msg)
        return

    if data.startswith("userbot:tickets:list:"):
        await query.answer()
        parts = data.split(":")
        status = parts[3] if len(parts) > 3 else "pending"
        try:
            page = int(parts[4]) if len(parts) > 4 else 1
        except Exception:
            page = 1
        await send_tickets_list(status, page, cid, context, message=msg)
        return

    if data.startswith("userbot:ticket:detail:"):
        await query.answer()
        parts = data.split(":")
        code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        list_status = parts[4] if len(parts) > 4 else "pending"
        try:
            page = int(parts[5]) if len(parts) > 5 else 1
        except Exception:
            page = 1
        await send_ticket_detail(
            code,
            cid,
            context,
            list_status=list_status,
            page=page,
            send_new=True,
        )
        return

    if data.startswith("userbot:ticketu:detail:"):
        await query.answer()
        parts = data.split(":")
        code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        from_user_id = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        try:
            page = int(parts[5]) if len(parts) > 5 else 1
        except Exception:
            page = 1
        await send_ticket_detail(
            code,
            cid,
            context,
            from_user_id=from_user_id,
            page=page,
            send_new=True,
        )
        return

    if data.startswith("userbot:ticket:reply:"):
        await query.answer()
        parts = data.split(":")
        code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        list_status = parts[4] if len(parts) > 4 else "pending"
        try:
            page = int(parts[5]) if len(parts) > 5 else 1
        except Exception:
            page = 1
        context.user_data[TICKET_REPLY_STATE] = {
            "ticket_code": code,
            "list_status": list_status,
            "page": page,
            "from_user_id": 0,
            "step": "wait_text",
            "reply_text": "",
            "photo_file_id": "",
        }
        await msg.reply_text(_adm_t('ub_lit_f3ac5da57201'), reply_markup=userbot_cancel_keyboard())
        return

    if data.startswith("userbot:ticketu:reply:"):
        await query.answer()
        parts = data.split(":")
        code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        from_user_id = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        try:
            page = int(parts[5]) if len(parts) > 5 else 1
        except Exception:
            page = 1
        context.user_data[TICKET_REPLY_STATE] = {
            "ticket_code": code,
            "list_status": "pending",
            "page": page,
            "from_user_id": from_user_id,
            "step": "wait_text",
            "reply_text": "",
            "photo_file_id": "",
        }
        await msg.reply_text(_adm_t('ub_lit_f3ac5da57201'), reply_markup=userbot_cancel_keyboard())
        return

    if data.startswith("userbot:ticketreply:"):
        await query.answer()
        act = data.split(":")[2] if len(data.split(":")) > 2 else ""
        st = context.user_data.get(TICKET_REPLY_STATE) or {}
        if not isinstance(st, dict):
            context.user_data.pop(TICKET_REPLY_STATE, None)
            await query.answer(_adm_t('ub_lit_0c9cfb91d66d'), show_alert=True)
            return

        ticket_code = int(st.get("ticket_code") or 0)
        list_status = str(st.get("list_status") or "pending").strip().lower()
        page = max(1, int(st.get("page") or 1))
        from_user_id = int(st.get("from_user_id") or 0)
        step = str(st.get("step") or "").strip().lower()
        if ticket_code <= 0:
            context.user_data.pop(TICKET_REPLY_STATE, None)
            await query.answer(_adm_t('ub_lit_d06a724c733c'), show_alert=True)
            return

        if act == "cancel":
            context.user_data.pop(TICKET_REPLY_STATE, None)
            try:
                await msg.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=cid, text=_adm_t('ub_lit_3b3429cb5a61'), reply_markup=admin_main_keyboard())
            await send_ticket_detail(
                ticket_code,
                cid,
                context,
                list_status=list_status,
                page=page,
                from_user_id=from_user_id,
            )
            return

        if act == "edit":
            st["step"] = "wait_text"
            st["reply_text"] = ""
            st["photo_file_id"] = ""
            context.user_data[TICKET_REPLY_STATE] = st
            try:
                await msg.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=cid,
                text=_adm_t('ub_lit_f3ac5da57201'),
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        if act == "skip":
            if step != "wait_screenshot":
                await query.answer(_adm_t('ub_lit_1676366173c1'), show_alert=True)
                return
            st["photo_file_id"] = ""
            st["step"] = "wait_confirm"
            context.user_data[TICKET_REPLY_STATE] = st
            preview_text = _build_ticket_reply_preview_text(str(st.get("reply_text") or ""), False)
            try:
                await msg.edit_text(preview_text, reply_markup=build_ticket_reply_confirm_keyboard())
            except Exception:
                await context.bot.send_message(
                    chat_id=cid,
                    text=preview_text,
                    reply_markup=build_ticket_reply_confirm_keyboard(),
                )
            return

        if act == "send":
            if step != "wait_confirm":
                await query.answer(_adm_t('ub_lit_30209eae4be0'), show_alert=True)
                return
            reply_text = str(st.get("reply_text") or "").strip()
            photo_file_id = str(st.get("photo_file_id") or "").strip()
            if not reply_text:
                await query.answer(_adm_t('ub_lit_2e92a2d2dfe7'), show_alert=True)
                return

            ticket = userbot_db.get_ticket_by_code(ticket_code)
            if not ticket:
                context.user_data.pop(TICKET_REPLY_STATE, None)
                await query.answer(_adm_t('ub_lit_ba0dfc851ef2'), show_alert=True)
                return

            admin_name = str(query.from_user.full_name or query.from_user.username or "admin").strip()
            ok = userbot_db.add_ticket_message(
                ticket_code,
                sender_type="admin",
                sender_name=admin_name,
                message_text=reply_text,
                photo_file_id=photo_file_id,
            )
            if not ok:
                await query.answer(_adm_t('ub_lit_61ab962c156d'), show_alert=True)
                return
            userbot_db.set_ticket_status(
                ticket_code,
                "open",
                admin_name=admin_name,
                admin_telegram_id=int(query.from_user.id or 0),
            )

            tg_id = int(ticket.get("telegram_id") or ticket.get("db_telegram_id") or 0)
            if tg_id > 0 and USER_BOT_TOKEN:
                try:
                    user_bot = Bot(token=USER_BOT_TOKEN)
                    _ulg = _user_lang_of(tg_id)
                    notify_text = _i18n_user_t(_ulg, "ticket_new_reply_notify", code=ticket_code, reply=reply_text or _i18n_user_t(_ulg, "adm_default_reply_note"))
                    kb = InlineKeyboardMarkup(
                        [[InlineKeyboardButton(_i18n_user_t(_ulg, "btn_view_ticket2"), callback_data=f"support:view:{ticket_code}:1")]]
                    )
                    if photo_file_id:
                        try:
                            await user_bot.send_photo(chat_id=tg_id, photo=photo_file_id, caption=notify_text, reply_markup=kb)
                        except Exception:
                            await user_bot.send_message(chat_id=tg_id, text=notify_text, reply_markup=kb)
                    else:
                        await user_bot.send_message(chat_id=tg_id, text=notify_text, reply_markup=kb)
                except Exception as e:
                    logger.warning("Failed notifying user for ticket reply %s: %s", ticket_code, e)

            # close reply wizard after successful send
            context.user_data.pop(TICKET_REPLY_STATE, None)
            try:
                await msg.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=cid,
                text=_adm_t('ub_lit_167da4fb21a5'),
                reply_markup=admin_main_keyboard(),
            )
            await send_ticket_detail(
                ticket_code,
                cid,
                context,
                list_status=list_status,
                page=page,
                from_user_id=from_user_id,
            )
            return

        await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
        return

    if data.startswith("userbot:ticket:status:"):
        await query.answer()
        parts = data.split(":")
        code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        new_status = str(parts[4] if len(parts) > 4 else "open").strip().lower()
        list_status = parts[5] if len(parts) > 5 else "pending"
        try:
            page = int(parts[6]) if len(parts) > 6 else 1
        except Exception:
            page = 1
        if new_status not in {"open", "closed"}:
            await query.answer(_adm_t('ub_lit_b61ae6948a16'), show_alert=True)
            return
        admin_name = str(query.from_user.full_name or query.from_user.username or "admin").strip()
        ok = userbot_db.set_ticket_status(
            code,
            new_status,
            admin_name=admin_name,
            admin_telegram_id=int(query.from_user.id or 0),
        )
        if not ok:
            await query.answer(_adm_t('ub_lit_3b65c31638dc'), show_alert=True)
            return
        await send_ticket_detail(
            code,
            cid,
            context,
            list_status=list_status,
            page=page,
            message=msg,
        )
        return

    if data.startswith("userbot:ticketu:status:"):
        await query.answer()
        parts = data.split(":")
        code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        new_status = str(parts[4] if len(parts) > 4 else "open").strip().lower()
        from_user_id = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
        try:
            page = int(parts[6]) if len(parts) > 6 else 1
        except Exception:
            page = 1
        if new_status not in {"open", "closed"}:
            await query.answer(_adm_t('ub_lit_b61ae6948a16'), show_alert=True)
            return
        admin_name = str(query.from_user.full_name or query.from_user.username or "admin").strip()
        ok = userbot_db.set_ticket_status(
            code,
            new_status,
            admin_name=admin_name,
            admin_telegram_id=int(query.from_user.id or 0),
        )
        if not ok:
            await query.answer(_adm_t('ub_lit_3b65c31638dc'), show_alert=True)
            return
        await send_ticket_detail(
            code,
            cid,
            context,
            from_user_id=from_user_id,
            page=page,
            message=msg,
        )
        return

    # --- 8. هدایا / کوپن زرین پال ---
    if data == "userbot:gifts_menu":
        await query.answer()
        await send_gifts_menu(cid, context, message=msg)
        return

    if data == "userbot:gifts:dashboard":
        await query.answer()
        await send_gifts_dashboard(cid, context, message=msg)
        return

    if data == "userbot:gifts:coupons":
        await query.answer()
        await send_zarin_coupons_menu(cid, context, message=msg)
        return

    if data == "userbot:gifts:bulk":
        context.user_data[ZARIN_COUPON_BULK_STATE] = {"step": "prefix"}
        await query.answer()
        await msg.reply_text(
            _adm_t('ub_lit_ed951c8499d4'),
            reply_markup=userbot_cancel_keyboard(),
            parse_mode="Markdown",
        )
        return

    if data == "userbot:gifts:presets":
        await query.answer()
        await send_gift_presets_menu(cid, context, message=msg)
        return

    if data.startswith("userbot:gifts:preset:"):
        preset_key = str(data.rsplit(":", 1)[-1] or "").strip()
        await query.answer()
        try:
            item = _create_gift_preset_coupon(preset_key)
        except Exception as e:
            await msg.reply_text(f"{_adm_t('ub_lit_cdcc82495d59')}{e}")
            return
        code = str(item.get("code") or "").strip()
        title = str(item.get("_preset_title") or _adm_t('ub_lit_28fd26df61bf')).strip()
        await msg.reply_text(f"✅ {title}{_adm_t('ub_lit_1c891fd3b4d4')}{code}")
        await send_zarin_coupon_detail(cid, context, code=code, message=msg)
        return

    if data == "userbot:gifts:redemptions":
        await query.answer()
        await send_zarin_redemptions_report(cid, context, message=msg)
        return

    if data.startswith("userbot:gifts:redemptions:"):
        code = str(data.rsplit(":", 1)[-1] or "").strip()
        await query.answer()
        await send_zarin_redemptions_report(cid, context, code=code, message=msg)
        return

    if data == "userbot:gifts:campaign":
        await query.answer()
        await send_gifts_campaign_text(cid, context, message=msg)
        return

    if data == "userbot:gifts:security":
        await query.answer()
        await send_gifts_security_menu(cid, context, message=msg)
        return

    if data == "userbot:gifts:auto_off":
        await query.answer()
        changed = userbot_db.deactivate_unusable_zarin_vouchers()
        await msg.reply_text(f"🧹 {changed}{_adm_t('ub_lit_cdd017be48c8')}")
        await send_gifts_security_menu(cid, context, message=msg)
        return

    if data == "userbot:gifts:help":
        await query.answer()
        await send_gifts_help(cid, context, message=msg)
        return

    if data == "userbot:gifts:coupons:add":
        context.user_data[ZARIN_COUPON_ADD_STATE] = {"step": "code"}
        await query.answer()
        await msg.reply_text(
            _adm_t('ub_lit_1ae4180f420a'),
            reply_markup=userbot_cancel_keyboard(),
            parse_mode="Markdown",
        )
        return

    if data == "userbot:gifts:coupons:delete":
        context.user_data[ZARIN_COUPON_DELETE_STATE] = True
        await query.answer()
        await msg.reply_text(_adm_t('ub_lit_432434cfb6f4'), reply_markup=userbot_cancel_keyboard())
        return

    if data.startswith("userbot:gifts:coupon:"):
        if data.startswith("userbot:gifts:coupon:toggle:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            item = userbot_db.get_zarin_voucher(code)
            if not item:
                await query.answer(_adm_t('ub_lit_50ffbd21b0e4'), show_alert=True)
                return
            new_active = int(item.get("is_active") or 0) != 1
            try:
                userbot_db.set_zarin_voucher_active(code, new_active)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer(_adm_t('ub_lit_a6c28f429af7') if new_active else _adm_t('ub_lit_bcaf1eba81cc'))
            await send_zarin_coupon_detail(cid, context, code=code, message=msg)
            return
        if data.startswith("userbot:gifts:coupon:set_link:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            context.user_data[ZARIN_COUPON_LINK_STATE] = {"code": code}
            await query.answer()
            await msg.reply_text(_adm_t('ub_lit_dde124e9c3b9'), reply_markup=userbot_cancel_keyboard())
            return
        if data.startswith("userbot:gifts:coupon:set_code:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            context.user_data[ZARIN_COUPON_CODE_STATE] = {"code": code}
            await query.answer()
            await msg.reply_text(_adm_t('ub_lit_f0cc9024aff7'), reply_markup=userbot_cancel_keyboard())
            return
        if data.startswith("userbot:gifts:coupon:set_limit:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            context.user_data[ZARIN_COUPON_LIMIT_STATE] = {"code": code}
            await query.answer()
            await msg.reply_text(_adm_t('ub_lit_09c0f111d458'), reply_markup=userbot_cancel_keyboard())
            return
        if data.startswith("userbot:gifts:coupon:set_exp:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            context.user_data[ZARIN_COUPON_EXP_STATE] = {"code": code}
            await query.answer()
            await msg.reply_text(
                _adm_t('ub_lit_8cb5637b13be'),
                reply_markup=userbot_cancel_keyboard(),
            )
            return
        if data.startswith("userbot:gifts:coupon:set_amount:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            context.user_data[ZARIN_COUPON_AMOUNT_STATE] = {"code": code}
            await query.answer()
            await msg.reply_text(_adm_t('ub_lit_020d715277f9'), reply_markup=userbot_cancel_keyboard())
            return
        if data.startswith("userbot:gifts:coupon:campaign:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            await query.answer()
            await send_coupon_campaign_text(cid, context, code=code, message=msg)
            return
        if data.startswith("userbot:gifts:coupon:qr:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            await query.answer()
            await send_coupon_deeplink_qr(cid, context, code=code)
            return
        if data.startswith("userbot:gifts:coupon:deeplink:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            await query.answer()
            if not code:
                await msg.reply_text(_adm_t('ub_lit_5f4ca05563a4'))
                return
            bot_username = await _get_user_bot_username(context)
            if not bot_username:
                await msg.reply_text(
                    _adm_t('ub_lit_e003d621f181'),
                    parse_mode="Markdown",
                )
                return
            deep_link = _build_telegram_start_link(bot_username, code)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(_adm_t('ub_lit_dffe8eb29470'), url=deep_link)],
                [InlineKeyboardButton(_adm_t('ub_lit_e75d30bfd8a5'), callback_data=f"userbot:gifts:coupon:qr:{code}")],
                [InlineKeyboardButton(_adm_t('ub_lit_867000e0e91f'), callback_data=f"userbot:gifts:coupon:campaign:{code}")],
                [InlineKeyboardButton(_adm_t('ub_lit_030adba70185'), callback_data=f"userbot:gifts:coupon:{code}")],
            ])
            await msg.reply_text(
                f"{_adm_t('ub_lit_0abb003fa562')}{deep_link}",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            await send_zarin_coupon_detail(cid, context, code=code, message=msg)
            return
        code = str(data.rsplit(":", 1)[-1] or "").strip()
        await query.answer()
        await send_zarin_coupon_detail(cid, context, code=code, message=msg)
        return

    # --- رفرال (دعوت دوستان) ---
    if data == "userbot:referral_menu":
        await query.answer()
        await send_referral_admin_menu(cid, context, message=msg)
        return

    if data == "userbot:referral:dashboard":
        await query.answer()
        try:
            stats = userbot_db.get_referral_admin_stats()
        except Exception:
            stats = {}
        top = stats.get("top_referrers") or []
        top_lines = []
        for idx, item in enumerate(top, start=1):
            name = str(item.get("inviter_full_name") or item.get("inviter_username") or item.get("inviter_id") or "—")
            top_lines.append(f"{idx}. {name}{_adm_t('ub_lit_babb41e634ed')}{int(item.get('successful') or 0)}{_adm_t('ub_lit_1d816cdb8d22')}{int(item.get('rewards') or 0):f','}")
        text = (
            f"{_adm_t('ub_lit_def59159c7c7')}{int(stats.get('total_referrals') or 0)}{_adm_t('ub_lit_61149b538c4f')}{int(stats.get('active_referrals') or 0)}{_adm_t('ub_lit_22f50ee4cb04')}{max(0, int(stats.get('active_referrals') or 0) - int(stats.get('paid_purchase_rewards') or 0))}{_adm_t('ub_lit_7e8b983ca3eb')}{int(stats.get('trial_rewards_count') or 0)} ({int(stats.get('trial_rewards_amount') or 0):f','}{_adm_t('ub_lit_7a612c8c01a7')}{int(stats.get('purchase_rewards_count') or 0)} ({int(stats.get('purchase_rewards_amount') or 0):f','}{_adm_t('ub_lit_1b740f1423e2')}{int(stats.get('total_reward_cost') or 0):f','}{_adm_t('ub_lit_6eeb2f8e4709')}{int(stats.get('revoked_rewards_count') or 0)}{_adm_t('ub_lit_1afc4e046888')}{int(stats.get('revenue_generated') or 0):f','}{_adm_t('ub_lit_e7ce4334a0c5')}{stats.get('conversion_rate') or 0}{_adm_t('ub_lit_41bd9991c2b2')}{int(stats.get('fraud_flagged') or 0)}\n"
        )
        if top_lines:
            text += _adm_t('ub_lit_61cab5ddef96') + "\n".join(top_lines)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t('ub_lit_efb588c846b7'), callback_data="userbot:referral_menu")]])
        try:
            await msg.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(cid, text, reply_markup=kb)
        return

    if data == "userbot:referral:settings":
        await query.answer()
        await send_referral_admin_settings(cid, context, message=msg)
        return

    if data == "userbot:referral:toggle":
        try:
            settings = userbot_db.toggle_referral_setting("referral_enabled")
        except Exception as e:
            await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
            return
        await query.answer(_adm_t('ub_lit_b5fe65255f70') if bool(settings.get("referral_enabled")) else _adm_t('ub_lit_d1044dffa308'))
        await send_referral_admin_menu(cid, context, message=msg)
        return

    if data.startswith("userbot:referral:toggle:"):
        name = str(data.rsplit(":", 1)[-1] or "").strip()
        try:
            userbot_db.toggle_referral_setting(name)
        except Exception as e:
            await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
            return
        await query.answer(_adm_t('ub_lit_68bc6617f02e'))
        await send_referral_admin_settings(cid, context, message=msg)
        return

    if data.startswith("userbot:referral:edit:"):
        name = str(data.rsplit(":", 1)[-1] or "").strip()
        if name not in {"trial_reward_amount", "purchase_reward_amount", "max_successful_referrals", "min_purchase_amount", "invite_intro_text"}:
            await query.answer(_adm_t('ub_lit_647cf6505c85'), show_alert=True)
            return
        context.user_data[REFERRAL_VALUE_EDIT_STATE] = {"name": name}
        await query.answer()
        prompts = {
            "trial_reward_amount": _adm_t('ub_lit_3d1180ad4ffd'),
            "purchase_reward_amount": _adm_t('ub_lit_1cdee38cc0a2'),
            "max_successful_referrals": _adm_t('ub_lit_2eaee928a5dd'),
            "min_purchase_amount": _adm_t('ub_lit_595bbc73a86a'),
            "invite_intro_text": _adm_t('ub_lit_84d62c7a1948'),
        }
        await msg.reply_text(prompts[name], reply_markup=userbot_cancel_keyboard())
        return

    if data.startswith("userbot:referral:list:"):
        try:
            page = int(str(data.rsplit(":", 1)[-1] or 1))
        except ValueError:
            page = 1
        await query.answer()
        await send_referral_list(cid, context, page=page, message=msg)
        return

    if data.startswith("userbot:referral:detail:"):
        try:
            ref_id = int(str(data.rsplit(":", 1)[-1] or 0))
        except ValueError:
            ref_id = 0
        await query.answer()
        await send_referral_detail(ref_id, cid, context, message=msg)
        return

    if data.startswith("userbot:referral:reject:"):
        try:
            ref_id = int(str(data.rsplit(":", 1)[-1] or 0))
        except ValueError:
            ref_id = 0
        ok = userbot_db.set_referral_status(ref_id, "rejected", _adm_t('ub_lit_fd379ed76e00'))
        await query.answer(_adm_t('ub_lit_97bf90a459b8') if ok else _adm_t('ub_lit_22b964a4b577'))
        await send_referral_detail(ref_id, cid, context, message=msg)
        return

    if data.startswith("userbot:referral:activate:"):
        try:
            ref_id = int(str(data.rsplit(":", 1)[-1] or 0))
        except ValueError:
            ref_id = 0
        ok = userbot_db.set_referral_status(ref_id, "active", "")
        await query.answer(_adm_t('ub_lit_bd161f4f843a') if ok else _adm_t('ub_lit_22b964a4b577'))
        await send_referral_detail(ref_id, cid, context, message=msg)
        return

    if data.startswith("userbot:referral:fraud:"):
        try:
            ref_id = int(str(data.rsplit(":", 1)[-1] or 0))
        except ValueError:
            ref_id = 0
        ref = userbot_db.get_referral_by_id(ref_id) or {}
        new_flag = int(ref.get("fraud_flag") or 0) != 1
        userbot_db.set_referral_fraud_flag(ref_id, new_flag)
        await query.answer(_adm_t('ub_lit_e17a6a6edff7') if new_flag else _adm_t('ub_lit_cb3cd1bf30b7'))
        await send_referral_detail(ref_id, cid, context, message=msg)
        return

    if data.startswith("userbot:referral:revoke_reward:"):
        try:
            reward_id = int(str(data.rsplit(":", 1)[-1] or 0))
        except ValueError:
            reward_id = 0
        revoked = userbot_db.revoke_referral_reward_by_id(reward_id)
        if revoked:
            await query.answer(_adm_t('ub_lit_0e5080bc0a77'))
        else:
            await query.answer(_adm_t('ub_lit_66808f58ba59'), show_alert=True)
            return
        ref_id = int((revoked or {}).get("referral_id") or 0)
        if ref_id > 0:
            await send_referral_detail(ref_id, cid, context, message=msg)
        else:
            await send_referral_reward_detail(reward_id, cid, context, message=msg)
        return

    if data.startswith("userbot:referral:rewards:"):
        try:
            page = int(str(data.rsplit(":", 1)[-1] or 1))
        except ValueError:
            page = 1
        await query.answer()
        await send_referral_rewards_list(cid, context, page=page, message=msg)
        return

    if data.startswith("userbot:referral:reward:"):
        try:
            reward_id = int(str(data.rsplit(":", 1)[-1] or 0))
        except ValueError:
            reward_id = 0
        await query.answer()
        await send_referral_reward_detail(reward_id, cid, context, message=msg)
        return

    if data == "userbot:referral:manual":
        context.user_data[REFERRAL_MANUAL_REWARD_STATE] = {"step": "input"}
        await query.answer()
        await msg.reply_text(
            _adm_t('ub_lit_d1e9238354a8'),
            reply_markup=userbot_cancel_keyboard(),
        )
        return

    # --- 7. تنظیمات ربات کاربران ---
    if data == "userbot:settings_menu":
        await query.answer()
        await send_userbot_settings_menu(cid, context, message=msg)
        return

    if data == "userbot:settings:subscription":
        await query.answer()
        await send_subscription_settings_menu(cid, context, message=msg)
        return

    if data == "userbot:settings:sub_link_status":
        await query.answer()
        await send_sub_link_status_menu(cid, context, message=msg)
        return

    if data == "userbot:settings:ui":
        await query.answer()
        await send_colored_buttons_settings_menu(cid, context, message=msg)
        return

    if data == "userbot:settings:ui:colored_buttons":
        settings = userbot_db.toggle_ui_setting("colored_buttons")
        status = _adm_t('ub_lit_7485818ed4d2') if settings.get("colored_buttons", True) else _adm_t('ub_lit_b6f893a889d1')
        await query.answer(f"{_adm_t('ub_lit_c7556a539276')}{status}.")
        await send_colored_buttons_settings_menu(cid, context, message=msg)
        return

    if data.startswith("userbot:settings:ui:theme:"):
        theme = normalize_button_theme(data.rsplit(":", 1)[-1])
        userbot_db.set_ui_setting("button_theme", theme)
        theme_title = BUTTON_STYLE_THEMES.get(theme, BUTTON_STYLE_THEMES["smart"])["title"]
        await query.answer(f"{_adm_t('ub_lit_8cfd1bf89ba0')}{theme_title}{_adm_t('ub_lit_dd20bd5a2f38')}")
        await send_colored_buttons_settings_menu(cid, context, message=msg)
        return

    if data == "userbot:settings:buy_renew":
        await query.answer()
        await send_buy_renew_settings_menu(cid, context, message=msg)
        return

    if data == "userbot:settings:tx_plans":
        await query.answer()
        await send_tx_plans_settings_menu(cid, context, message=msg)
        return

    if data == "userbot:settings:texts":
        await query.answer()
        await send_text_settings_menu(cid, context, message=msg)
        return

    if data == "userbot:settings:marketing":
        await query.answer()
        await send_marketing_settings_menu(cid, context, message=msg)
        return

    if data == "userbot:settings:force_join":
        await query.answer()
        await send_force_join_settings_menu(cid, context, message=msg)
        return

    if data == "userbot:settings:payment":
        await query.answer()
        await send_payment_settings_menu(cid, context, message=msg)
        return

    if data == "userbot:settings:backup_restore":
        await query.answer()
        await send_backup_restore_settings_menu(cid, context, message=msg)
        return

    if data.startswith("userbot:settings:texts:"):
        if data == "userbot:settings:texts:invite_menu":
            await query.answer()
            await send_invite_text_settings_menu(cid, context, message=msg)
            return
        if data.startswith("userbot:settings:texts:invite:"):
            action = data.rsplit(":", 1)[-1].strip()
            if action == "add_photo":
                context.user_data[INVITE_BANNER_PHOTO_EDIT_STATE] = True
                await query.answer()
                await msg.reply_text(
                    _adm_t('ub_lit_2e7b5385de52'),
                    reply_markup=userbot_cancel_keyboard(),
                )
                return
            if action == "remove_photo":
                try:
                    userbot_db.set_text_setting("invite_banner_photo_id", "")
                except Exception as e:
                    await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                    return
                await query.answer(_adm_t('ub_lit_be8da7b33ce2'))
                await send_invite_text_settings_menu(cid, context, message=msg)
                return
            await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
            return
        if data == "userbot:settings:texts:guide_menu":
            await query.answer()
            await send_guide_text_settings_menu(cid, context, message=msg)
            return
        if ":edit:" in data:
            field_name = data.rsplit(":edit:", 1)[-1].strip()
            labels = {
                "welcome_message": _adm_t('ub_lit_16a4894155c6'),
                "faq_text": _adm_t('ub_lit_fe0c9752289d'),
                "guide_text": _adm_t('ub_lit_e0bb77a3e0b9'),
                "guide_android_text": _adm_t('ub_lit_26226141ec4f'),
                "guide_ios_text": _adm_t('ub_lit_457d0c610f42'),
                "guide_windows_text": _adm_t('ub_lit_c3793fb8bb0d'),
                "guide_mac_text": _adm_t('ub_lit_001643349e09'),
                "guide_linux_text": _adm_t('ub_lit_7f3f16b09ee6'),
                "invite_text": _adm_t('ub_lit_427df668b29e'),
                "invite_info_text": _adm_t('ub_lit_c7d3dfbeb307'),
                "invite_banner_text": _adm_t('ub_lit_867b52c86c46'),
                "servers_list_text": _adm_t('ub_lit_5b1832a63ddc'),
                "plans_list_text": _adm_t('ub_lit_6483c6b7f5d2'),
                "ticket_panel_text": _adm_t('ub_lit_fd47940c17b7'),
                "zarinpal_pro_text": _adm_t('ub_lit_bcd826eb617a'),
                "card_to_card_text": _adm_t('ub_lit_9d2a0c9b5195'),
            }
            if field_name not in labels:
                await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
                return
            settings = _get_text_settings()
            current = str(settings.get(field_name) or "").strip()
            if current.lower() in {"none", "null"}:
                current = ""
            current = current or "0"
            context.user_data[TEXT_SETTINGS_EDIT_STATE] = field_name
            await query.answer()
            prompt = (
                f"📝 {labels[field_name]}{_adm_t('ub_lit_a1828ebe872a')}{current}{_adm_t('ub_lit_d393c498139d')}"
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer(_adm_t('ub_lit_1b41f2d33558'), show_alert=True)
        return

    if data.startswith("userbot:settings:subscription:"):
        action = data.split(":")[-1]
        if action == "show_sub_link_status":
            await query.answer()
            await send_sub_link_status_menu(cid, context, message=msg)
            return
        if action == "sub_status_reminder":
            await query.answer()
            await send_sub_status_reminder_menu(cid, context, message=msg)
            return
        if action == "trial_spec":
            await query.answer()
            await send_trial_spec_menu(cid, context, message=msg)
            return
        if action == "reset_free_trial":
            await query.answer()
            await send_reset_free_trial_confirm(cid, context, message=msg)
            return
        if action == "reset_free_trial_confirm":
            await query.answer()
            try:
                changed = userbot_db.reset_all_free_trials()
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            try:
                await msg.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=cid,
                text=f"{_adm_t('ub_lit_ae268766695d')}{changed}",
            )
            await send_subscription_settings_menu(cid, context)
            return
        if action in {
            "show_user_page_link",
            "show_username",
            "shuffle_configs",
            "shuffle_server_layout",
            "shuffle_config_layout",
        }:
            _toggle_subscription_setting(context, action)
            await query.answer()
            await send_subscription_settings_menu(cid, context, message=msg)
            return

        await query.answer(_adm_t('ub_lit_035417d71a9c'), show_alert=True)
        return

    if data.startswith("userbot:settings:buy_renew:"):
        action = data.split(":")[-1]
        if action == "renew_unlimited_volume":
            settings = _get_buy_renew_settings()
            current = int(settings.get("renew_unlimited_volume_from_gb") or 1000)
            context.user_data[RENEW_POLICY_EDIT_STATE] = "renew_unlimited_volume_from_gb"
            await query.answer()
            prompt = (
                f"{_adm_t('ub_lit_2558ae156361')}{current}{_adm_t('ub_lit_64aa243a8eb6')}"
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        if action == "renew_unlimited_time":
            settings = _get_buy_renew_settings()
            current = int(settings.get("renew_unlimited_time_from_days") or 365)
            context.user_data[RENEW_POLICY_EDIT_STATE] = "renew_unlimited_time_from_days"
            await query.answer()
            prompt = (
                f"{_adm_t('ub_lit_2558ae156361')}{current}{_adm_t('ub_lit_3602351b8c52')}"
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        if ":plan_columns:menu" in data:
            await query.answer()
            await send_plan_columns_menu(cid, context, message=msg)
            return
        if ":server_columns:menu" in data:
            await query.answer()
            await send_server_columns_menu(cid, context, message=msg)
            return
        if ":plan_columns:set:" in data:
            try:
                col = int(data.rsplit(":", 1)[-1])
                userbot_db.set_buy_renew_columns("plans", col)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_plan_columns_menu(cid, context, message=msg)
            return
        if ":server_columns:set:" in data:
            try:
                col = int(data.rsplit(":", 1)[-1])
                userbot_db.set_buy_renew_columns("servers", col)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_server_columns_menu(cid, context, message=msg)
            return
        if action in {
            "enable_buy",
            "enable_renew",
            "show_renew_in_main_menu",
            "event_channel_enabled",
        }:
            try:
                userbot_db.toggle_buy_renew_setting(action)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_buy_renew_settings_menu(cid, context, message=msg)
            return
        if action == "renew_mode_info":
            await query.answer()
            await send_renew_policy_menu(cid, context, message=msg)
            return
        if action == "event_channel_set":
            context.user_data[EVENT_CHANNEL_EDIT_STATE] = True
            await query.answer()
            settings = _get_buy_renew_settings()
            current = str(settings.get("event_channel_id") or "—").strip() or "—"
            prompt = (
                f"{_adm_t('ub_lit_9ff807b4e182')}{current}{_adm_t('ub_lit_c6ee28167a1d')}"
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        if ":renew_rollover:" in data:
            tail = data.split(":renew_rollover:", 1)[-1]
            try:
                kind, mode = tail.split(":", 1)
            except ValueError:
                await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
                return
            kind = str(kind or "").strip().lower()
            mode = str(mode or "").strip().lower()
            if kind not in {"volume", "time"} or mode not in {"add", "reset"}:
                await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
                return
            try:
                userbot_db.set_buy_renew_rollover_mode(kind, mode)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer(_adm_t('ub_lit_5d7b6f5551fb'))
            await send_renew_policy_menu(cid, context, message=msg)
            return
        if ":renew_policy:" in data:
            policy = data.rsplit(":", 1)[-1].strip().lower()
            try:
                userbot_db.set_buy_renew_policy(policy)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_renew_policy_menu(cid, context, message=msg)
            return
        if ":renew_limit:" in data:
            limit_key = data.rsplit(":", 1)[-1].strip().lower()
            settings = _get_buy_renew_settings()
            if limit_key == "days":
                current = int(settings.get("renew_max_days") or 3)
                context.user_data[RENEW_POLICY_EDIT_STATE] = "renew_max_days"
                prompt = (
                    f"{_adm_t('ub_lit_330d3bdc5d09')}{current}{_adm_t('ub_lit_af71b6b54da9')}"
                )
            elif limit_key == "usage":
                current = int(settings.get("renew_max_remaining_gb") or 3)
                context.user_data[RENEW_POLICY_EDIT_STATE] = "renew_max_remaining_gb"
                prompt = (
                    f"{_adm_t('ub_lit_6b316581a513')}{current}{_adm_t('ub_lit_b81d6d664163')}"
                )
            else:
                await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
                return
            await query.answer()
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        if action in {"plans", "servers"} and "renew_mode" in data:
            try:
                userbot_db.set_buy_renew_mode(action)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_buy_renew_settings_menu(cid, context, message=msg)
            return
        await query.answer(_adm_t('ub_lit_1b41f2d33558'), show_alert=True)
        return

    if data.startswith("userbot:settings:tx_plans:"):
        if ":plan_categories_mode:menu" in data:
            await query.answer()
            await send_plan_categories_mode_menu(cid, context, message=msg)
            return
        if ":plan_categories_mode:set:" in data:
            mode = data.rsplit(":", 1)[-1].strip().lower()
            try:
                current = _get_tx_plans_settings()
                if mode == "simple":
                    current["plan_categories_enabled"] = False
                elif mode == "categorized":
                    current["plan_categories_enabled"] = True
                else:
                    await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
                    return
                userbot_db.set_tx_plans_settings(current)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_plan_categories_mode_menu(cid, context, message=msg)
            return
        if ":plan_sort_mode:menu" in data:
            await query.answer()
            await send_plan_sort_mode_menu(cid, context, message=msg)
            return
        if ":plan_sort_mode:set:" in data:
            mode = data.rsplit(":", 1)[-1].strip().lower()
            try:
                current = _get_tx_plans_settings()
                current["plan_sort_mode"] = mode if mode in {"price", "gb", "days"} else "price"
                userbot_db.set_tx_plans_settings(current)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_plan_sort_mode_menu(cid, context, message=msg)
            return
        if ":plan_sort_dir:set:" in data:
            direction = data.rsplit(":", 1)[-1].strip().lower()
            try:
                current = _get_tx_plans_settings()
                current["plan_sort_desc"] = True if direction == "desc" else False
                userbot_db.set_tx_plans_settings(current)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_plan_sort_mode_menu(cid, context, message=msg)
            return

        action = data.split(":")[-1]
        if action in {"random_tx_spec"}:
            try:
                userbot_db.toggle_tx_plans_setting(action)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_tx_plans_settings_menu(cid, context, message=msg)
            return
        if action == "min_tx":
            settings = _get_tx_plans_settings()
            current = int(settings.get("min_transaction_toman") or 10000)
            context.user_data[TX_PLANS_EDIT_STATE] = "min_transaction_toman"
            await query.answer()
            prompt = (
                f"{_adm_t('ub_lit_2558ae156361')}{current:f','}{_adm_t('ub_lit_514253239f55')}"
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer(_adm_t('ub_lit_1b41f2d33558'), show_alert=True)
        return

    if data.startswith("userbot:settings:trial_spec:"):
        action = data.split(":")[-1]
        if action == "enabled":
            try:
                userbot_db.toggle_trial_spec_enabled()
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_trial_spec_menu(cid, context, message=msg)
            return
        if action == "announce":
            try:
                userbot_db.toggle_trial_spec_announce_enabled()
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_trial_spec_menu(cid, context, message=msg)
            return
        if action in {"usage", "days"}:
            spec = _get_trial_spec_settings()
            if action == "usage":
                current = float(spec.get("usage_gb") or 1)
                context.user_data[TRIAL_SPEC_EDIT_STATE] = "usage_gb"
                prompt = (
                    f"{_adm_t('ub_lit_2558ae156361')}{current:f'g'}{_adm_t('ub_lit_3d39cb23c5fe')}"
                )
            else:
                current = int(spec.get("days") or 1)
                context.user_data[TRIAL_SPEC_EDIT_STATE] = "days"
                prompt = (
                    f"{_adm_t('ub_lit_2558ae156361')}{current}{_adm_t('ub_lit_e0a2efebc00f')}"
                )
            await query.answer()
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer(_adm_t('ub_lit_1b41f2d33558'), show_alert=True)
        return

    if data.startswith("userbot:settings:sub_status_reminder:"):
        action = data.split(":")[-1]
        if action == "enabled":
            try:
                userbot_db.toggle_sub_reminder_enabled()
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_sub_status_reminder_menu(cid, context, message=msg)
            return
        if action in {"usage", "days"}:
            reminder = _get_sub_reminder_settings()
            if action == "usage":
                current = int(reminder.get("usage_gb") or 3)
                context.user_data[SUB_REMINDER_EDIT_STATE] = "usage_gb"
                prompt = (
                    f"{_adm_t('ub_lit_330d3bdc5d09')}{current}{_adm_t('ub_lit_8fcb1814c790')}"
                )
            else:
                current = int(reminder.get("days") or 3)
                context.user_data[SUB_REMINDER_EDIT_STATE] = "days"
                prompt = (
                    f"{_adm_t('ub_lit_bb1b25c38952')}{current}{_adm_t('ub_lit_77be89d84c8a')}"
                )
            await query.answer()
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer(_adm_t('ub_lit_1b41f2d33558'), show_alert=True)
        return

    if data.startswith("userbot:settings:sub_link_status:"):
        action = data.split(":")[-1]
        if action == "set_base_url":
            context.user_data[SUB_BASE_URL_EDIT_STATE] = "edit"
            current = userbot_db.get_managed_sub_base_url() or _adm_t('ub_lit_084da407a88d')
            await query.answer()
            prompt = (
                f"{_adm_t('ub_lit_79a350aec8a6')}{current}{_adm_t('ub_lit_6a3be198e482')}"
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        if action == "ssl_help":
            await query.answer()
            hint_domain = _guess_ssl_domain_hint()
            guide = (
                f"{_adm_t('ub_lit_c4e8ee0d8e4b')}{hint_domain}{_adm_t('ub_lit_c26ce342c4ef')}"
            )
            try:
                await msg.reply_text(guide, parse_mode="Markdown", reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(
                    chat_id=cid,
                    text=guide,
                    parse_mode="Markdown",
                    reply_markup=userbot_cancel_keyboard(),
                )
            return
        if action in {
            "show_direct_config",
            "show_auto_sub_link",
            "show_sub_link",
            "show_sub_link_b64",
            "show_multi_server",
            "show_multi_server_b64",
        }:
            _toggle_subscription_setting(context, action)
            await query.answer()
            await send_sub_link_status_menu(cid, context, message=msg)
            return
        await query.answer(_adm_t('ub_lit_1b41f2d33558'), show_alert=True)
        return

    if data.startswith("userbot:settings:marketing:"):
        if ":toggle:" in data:
            name = data.rsplit(":toggle:", 1)[-1].strip()
            try:
                userbot_db.toggle_marketing_setting(name)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_marketing_settings_menu(cid, context, message=msg)
            return
        if ":edit:" in data:
            edit_name = data.rsplit(":edit:", 1)[-1].strip()
            settings = _get_marketing_settings()
            if edit_name == "auto_gift_text":
                current = str(settings.get("auto_gift_text") or "").strip() or "—"
                context.user_data[MARKETING_EDIT_STATE] = "auto_gift_text"
                prompt = (
                    f"{_adm_t('ub_lit_7aa9994a2ecc')}{current}{_adm_t('ub_lit_d393c498139d')}"
                )
            elif edit_name == "min_auto_gift_charge":
                current = int(settings.get("min_auto_gift_charge") or 0)
                context.user_data[MARKETING_EDIT_STATE] = "min_auto_gift_charge"
                prompt = (
                    f"{_adm_t('ub_lit_3a9288de90d0')}{current:f','}{_adm_t('ub_lit_d245af6d7e5d')}"
                )
            else:
                await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
                return
            await query.answer()
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer(_adm_t('ub_lit_1b41f2d33558'), show_alert=True)
        return

    if data.startswith("userbot:settings:force_join:"):
        action = data.split(":")[-1].strip().lower()
        if action == "help":
            settings = _get_force_join_settings()
            await query.answer()
            try:
                await msg.reply_text(str(settings.get("guide_text") or "").strip() or _adm_t('ub_lit_c31fe348e1cb'))
            except Exception:
                await context.bot.send_message(
                    chat_id=cid,
                    text=str(settings.get("guide_text") or "").strip() or _adm_t('ub_lit_c31fe348e1cb'),
                )
            return
        if action == "toggle":
            try:
                userbot_db.toggle_force_join_enabled()
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_force_join_settings_menu(cid, context, message=msg)
            return
        if action == "set_channel":
            context.user_data[FORCE_JOIN_EDIT_STATE] = True
            await query.answer()
            prompt = (
                _adm_t('ub_lit_335033076585')
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
        return

    if data.startswith("userbot:settings:payment:"):
        if ":toggle:" in data:
            method = data.rsplit(":toggle:", 1)[-1].strip().lower()
            key_map = {
                "card": "enable_card_to_card",
                "zarinpal": "enable_zarinpal",
                "perfect": "enable_perfect_money",
                "crypto": "enable_crypto",
            }
            key = key_map.get(method)
            if not key:
                await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
                return
            try:
                userbot_db.toggle_payment_setting(key)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_payment_method_menu(cid, context, method=method, message=msg)
            return
        if data == "userbot:settings:payment:card:sms":
            await query.answer()
            await send_sms_webhook_settings_menu(cid, context, message=msg)
            return
        if data == "userbot:settings:payment:card:sms:toggle":
            status = _sms_webhook_status()
            new_enabled = not bool(status.get("enabled"))
            updates = {"SMS_WEBHOOK_ENABLED": "true" if new_enabled else "false"}
            if new_enabled and not str(status.get("secret") or "").strip():
                updates["SMS_WEBHOOK_SECRET"] = secrets.token_hex(32)
            if new_enabled:
                updates["SMS_WEBHOOK_MAX_PENDING_AGE_MINUTES"] = "360"
            try:
                _write_env_values(updates)
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_890ce1b4b903')}{e}", show_alert=True)
                return
            await query.answer(_adm_t('ub_lit_55a39817f190'), show_alert=True)
            await send_sms_webhook_settings_menu(cid, context, message=msg)
            return
        if data == "userbot:settings:payment:card:sms:regen":
            new_secret = secrets.token_hex(32)
            try:
                _write_env_values(
                    {
                        "SMS_WEBHOOK_ENABLED": "true",
                        "SMS_WEBHOOK_SECRET": new_secret,
                        "SMS_WEBHOOK_MAX_PENDING_AGE_MINUTES": "360",
                    }
                )
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_890ce1b4b903')}{e}", show_alert=True)
                return
            await query.answer(_adm_t('ub_lit_165d6b096dda'))
            await send_sms_webhook_settings_menu(cid, context, message=msg)
            copy_text = (
                f"{_adm_t('ub_lit_01c80f1c876d')}{html_escape(new_secret)}</code></pre>"
            )
            try:
                await msg.reply_text(copy_text, parse_mode="HTML")
            except Exception:
                await context.bot.send_message(chat_id=cid, text=copy_text, parse_mode="HTML")
            return
        if data == "userbot:settings:payment:card:sms:show":
            status = _sms_webhook_status()
            secret = str(status.get("secret") or "").strip()
            if not secret:
                await query.answer(_adm_t('ub_lit_30e18cf10903'), show_alert=True)
                return
            text = (
                f"{_adm_t('ub_lit_d0079edbfe95')}{html_escape(secret)}</code></pre>\n\nWebhook URL:\n<pre><code>{html_escape(str(status.get('endpoint') or ''))}</code></pre>"
            )
            await query.answer()
            try:
                await msg.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                await context.bot.send_message(chat_id=cid, text=text, parse_mode="HTML", disable_web_page_preview=True)
            return
        if data == "userbot:settings:payment:card:sms:help":
            status = _sms_webhook_status()
            text = (
                f"{_adm_t('ub_lit_3aec5931b2c8')}{html_escape(str(status.get('endpoint') or ''))}{_adm_t('ub_lit_ee8e61f0d905')}"
            )
            await query.answer()
            try:
                await msg.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                await context.bot.send_message(chat_id=cid, text=text, parse_mode="HTML", disable_web_page_preview=True)
            return
        if data.startswith("userbot:settings:payment:card:item:"):
            raw = data.rsplit(":", 1)[-1].strip()
            number = re.sub(r"\D", "", raw)
            if len(number) != 16:
                await query.answer(_adm_t('ub_lit_4dae3ea921f5'), show_alert=True)
                return
            await query.answer()
            await send_payment_card_item_menu(cid, context, number=number, message=msg)
            return
        if data.startswith("userbot:settings:payment:card:copy:"):
            raw = data.rsplit(":", 1)[-1].strip()
            number = re.sub(r"\D", "", raw)
            if len(number) != 16:
                await query.answer(_adm_t('ub_lit_4dae3ea921f5'), show_alert=True)
                return
            await query.answer(f"{_adm_t('ub_lit_0028ef6bbfb2')}{number}", show_alert=True)
            return
        if data.startswith("userbot:settings:payment:card:edit_number:"):
            raw = data.rsplit(":", 1)[-1].strip()
            number = re.sub(r"\D", "", raw)
            if len(number) != 16:
                await query.answer(_adm_t('ub_lit_4dae3ea921f5'), show_alert=True)
                return
            context.user_data[PAYMENT_CARD_EDIT_STATE] = {"mode": "number", "number": number}
            await query.answer()
            try:
                await msg.reply_text(_adm_t('ub_lit_c4191e107229'), reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=_adm_t('ub_lit_c4191e107229'), reply_markup=userbot_cancel_keyboard())
            return
        if data.startswith("userbot:settings:payment:card:edit_owner:"):
            raw = data.rsplit(":", 1)[-1].strip()
            number = re.sub(r"\D", "", raw)
            if len(number) != 16:
                await query.answer(_adm_t('ub_lit_4dae3ea921f5'), show_alert=True)
                return
            context.user_data[PAYMENT_CARD_EDIT_STATE] = {"mode": "owner", "number": number}
            await query.answer()
            try:
                await msg.reply_text(_adm_t('ub_lit_942dd32d8c8a'), reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=_adm_t('ub_lit_942dd32d8c8a'), reply_markup=userbot_cancel_keyboard())
            return
        if data == "userbot:settings:payment:card:list":
            await query.answer()
            await send_payment_cards_list_menu(cid, context, message=msg)
            return
        if data == "userbot:settings:payment:card:add":
            context.user_data[PAYMENT_CARD_ADD_STATE] = {"step": "number"}
            await query.answer()
            prompt = (
                _adm_t('ub_lit_b4de48857dac')
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        if data == "userbot:settings:payment:card:delete":
            context.user_data[PAYMENT_CARD_DELETE_STATE] = True
            await query.answer()
            prompt = _adm_t('ub_lit_8aff19585c27')
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        if data == "userbot:settings:payment:card:text":
            context.user_data[TEXT_SETTINGS_EDIT_STATE] = "card_to_card_text"
            await query.answer()
            current = str(_get_text_settings().get("card_to_card_text") or "").strip()
            if current.lower() in {"none", "null"}:
                current = ""
            current = current or "0"
            prompt = (
                _adm_t('ub_lit_9370b34ab82b')
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
                await msg.reply_text(f"{_adm_t('ub_lit_2558ae156361')}{current}", reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
                await context.bot.send_message(chat_id=cid, text=f"{_adm_t('ub_lit_2558ae156361')}{current}", reply_markup=userbot_cancel_keyboard())
            return
        if data == "userbot:settings:payment:card:random_tx_spec":
            try:
                userbot_db.toggle_tx_plans_setting("random_tx_spec")
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_payment_method_menu(cid, context, method="card", message=msg)
            return
        if data == "userbot:settings:payment:card:require_last4":
            try:
                userbot_db.toggle_payment_setting("require_last4_for_card_receipt")
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_payment_method_menu(cid, context, method="card", message=msg)
            return
        action = data.split(":")[-1].strip().lower()
        if action in {"card", "zarinpal", "perfect", "crypto"}:
            await query.answer()
            await send_payment_method_menu(cid, context, method=action, message=msg)
            return
        if action == "event_channel_toggle":
            try:
                userbot_db.toggle_payment_setting("event_channel_enabled")
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_payment_settings_menu(cid, context, message=msg)
            return
        if action == "event_channel_set":
            context.user_data[PAYMENT_CHANNEL_EDIT_STATE] = True
            await query.answer()
            prompt = (
                _adm_t('ub_lit_d4979d7ba1fe')
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
        return

    if data.startswith("userbot:settings:backup_restore:"):
        action = data.split(":")[-1].strip().lower()
        if action == "auto_toggle":
            try:
                userbot_db.toggle_backup_restore_setting("auto_backup_enabled")
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_backup_restore_settings_menu(cid, context, message=msg)
            return
        if action == "event_toggle":
            try:
                userbot_db.toggle_backup_restore_setting("event_channel_enabled")
            except Exception as e:
                await query.answer(f"{_adm_t('ub_lit_05f6ce76ab68')}{e}", show_alert=True)
                return
            await query.answer()
            await send_backup_restore_settings_menu(cid, context, message=msg)
            return
        if action == "event_set":
            context.user_data[BACKUP_CHANNEL_EDIT_STATE] = True
            await query.answer()
            prompt = (
                _adm_t('ub_lit_869c28957a0c')
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        if action == "restore":
            context.user_data[BACKUP_RESTORE_STATE] = True
            await query.answer()
            try:
                await msg.reply_text(
                    _adm_t('ub_lit_5f85aff13323'),
                    reply_markup=userbot_cancel_keyboard(),
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=cid,
                    text=_adm_t('ub_lit_5f85aff13323'),
                    reply_markup=userbot_cancel_keyboard(),
                )
            return
        if action == "download":
            await query.answer()
            try:
                backup_path = _make_bot_backup_zip()
            except Exception as e:
                await context.bot.send_message(chat_id=cid, text=f"{_adm_t('ub_lit_6e7e87d6d4bc')}{e}")
                return
            try:
                with backup_path.open("rb") as fh:
                    await context.bot.send_document(
                        chat_id=cid,
                        document=fh,
                        filename=backup_path.name,
                        caption=_adm_t('ub_lit_e4f3841765e2'),
                    )
            except Exception as e:
                await context.bot.send_message(chat_id=cid, text=f"{_adm_t('ub_lit_3de8b26e7d5a')}{e}")
            return
        await query.answer(_adm_t('ub_lit_57af267e1a57'), show_alert=True)
        return

    # --- 8. ارسال پیام همگانی ---
    if data == "userbot:broadcast_menu":
        context.user_data.pop(BROADCAST_SEND_STATE, None)
        await query.answer()
        await send_broadcast_menu(cid, context, message=msg)
        return

    if data.startswith("userbot:broadcast:segment:"):
        segment = str(data.rsplit(":", 1)[-1] or "").strip().lower()
        allowed_segments = {
            "all",
            "expired_all",
            "no_order",
            "expired_1w",
            "expired_2w",
            "expired_4w",
            "expired_8w",
        }
        if segment not in allowed_segments:
            await query.answer(_adm_t('ub_lit_6808a6dd39e5'), show_alert=True)
            return
        context.user_data[BROADCAST_SEND_STATE] = {
            "segment": segment,
            "step": "wait_text",
            "text": "",
        }
        await query.answer()
        await msg.reply_text(
            f"{_adm_t('ub_lit_4deb504a809a')}{_broadcast_segment_label(segment)}{_adm_t('ub_lit_6e15e865cb28')}",
            reply_markup=userbot_cancel_keyboard(),
        )
        return

    if data.startswith("userbot:settings:"):
        await query.answer(_adm_t('ub_lit_bd549efdc483'), show_alert=True)
        return

    # لاگ کردن دکمه‌های ناشناخته برای دیباگ
    logger.warning(f"Unhandled userbot callback: {data}")
    await query.answer()
