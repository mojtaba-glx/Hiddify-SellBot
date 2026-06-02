from __future__ import annotations

import math
import os
import logging
import re
import asyncio
import json
import tempfile
import zipfile
import shutil
import sqlite3
from io import BytesIO
from html import escape as html_escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime, timezone
from urllib.parse import urlparse

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
    return str(user.get("telegram_id") or user.get("id") or "کاربر")


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


def _format_gb(value: Any) -> str:
    if value is None:
        return "نامشخص"
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
        return "✅ تایید شده"
    if s == "rejected":
        return "❌ رد شده"
    return "⏳ در انتظار"


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
    payer_line = f"\n◈ ۴ رقم آخر کارت مبدا: {payer_last4}" if payer_last4 else ""
    return (
        f"◈ شناسه تراکنش: {tx_code}\n"
        f"👤 کاربر: {full_name}\n"
        f"◈ نام کاربری: {username}\n"
        f"◈ شناسه کاربر: {tg_id}\n"
        f"◈ تاریخ تراکنش: {created_at}\n"
        f"◈ مبلغ تراکنش: {amount} تومان\n"
        "❖ • -------------------------- • ❖\n"
        f"◈ وضعیت: {status_title}\n"
        f"◈ روش تراکنش: {method}"
        f"{payer_line}"
    )


def _build_payment_approved_report_text(pay: Dict[str, Any]) -> str:
    tx_code = str(pay.get("tx_code") or pay.get("id") or "-")
    amount = _format_toman(pay.get("amount") or 0)
    method = str(pay.get("method") or "card")
    receipt_meta = _parse_receipt_meta(str(pay.get("receipt_image") or ""))
    is_direct_buy = str(receipt_meta.get("pay_flow") or "").strip().lower() == "direct_buy"
    if method == "card" and is_direct_buy:
        method_title = "کارت به کارت (خرید مستقیم)"
    else:
        method_title = "کارت به کارت" if method == "card" else method
    return (
        "💸گزارش تایید پرداخت🕊\n\n"
        f"🔖شیوه پرداخت:{method_title}\n"
        f"🔑شناسه تراکنش:{tx_code}\n"
        f"💰مبلغ پرداخت:{amount} تومان"
    )


def _ticket_status_title(status: str) -> str:
    s = str(status or "").strip().lower()
    if s == "open":
        return "✅ باز"
    if s == "closed":
        return "📪 بسته"
    return "❌ در انتظار"


def _ticket_user_label(ticket: Dict[str, Any]) -> str:
    full_name = str(ticket.get("full_name") or ticket.get("db_full_name") or "").strip()
    username = str(ticket.get("username") or ticket.get("db_username") or "").strip().lstrip("@")
    tg_id = str(ticket.get("telegram_id") or ticket.get("db_telegram_id") or "").strip()
    if full_name:
        return full_name
    if username:
        return username
    return tg_id or "کاربر"


def _build_tickets_stats_text(stats: Dict[str, Any]) -> str:
    total = int(stats.get("total_count") or 0)
    pending = int(stats.get("pending_count") or 0)
    opened = int(stats.get("open_count") or 0)
    closed = int(stats.get("closed_count") or 0)
    feedback_total = int(stats.get("feedback_total") or 0)
    feedback_pos = int(stats.get("feedback_positive") or 0)
    feedback_neg = int(stats.get("feedback_negative") or 0)
    return (
        "📮 مدیریت تیکت‌ها\n"
        "❖⬩--------------------------------⬩❖\n"
        f"◈ تعداد تیکت‌ها: {total}\n"
        f"◈ تعداد تیکت‌های باز: {opened}\n"
        f"◈ تعداد تیکت‌های بسته: {closed}\n"
        f"◈ تعداد تیکت‌های در انتظار: {pending}\n"
        f"◈ تعداد نظرسنجی‌ها: {feedback_total}\n"
        f"◈ تعداد نظرسنجی‌های مثبت: {feedback_pos}\n"
        f"◈ تعداد نظرسنجی‌های منفی: {feedback_neg}\n"
        "❖⬩--------------------------------⬩❖"
    )


def _broadcast_segment_label(segment: str) -> str:
    seg = str(segment or "").strip().lower()
    mapping = {
        "all": "تمام کاربران",
        "expired_all": "تمام کاربران منقضی شده",
        "no_order": "کاربران بدون سفارش",
        "expired_1w": "کاربران منقضی شده بیش از یک هفته",
        "expired_2w": "کاربران منقضی شده بیش از دو هفته",
        "expired_4w": "کاربران منقضی شده بیش از چهار هفته",
        "expired_8w": "کاربران منقضی شده بیش از هشت هفته",
    }
    return mapping.get(seg, "تمام کاربران")


def _build_broadcast_stats_text(stats: Dict[str, Any]) -> str:
    return (
        f"◈ تعداد کاربران تلگرام: {int(stats.get('total_users') or 0)}\n"
        f"◈ تعداد کاربران منقضی: {int(stats.get('expired_users') or 0)}\n"
        f"◈ تعداد کاربران بدون سفارش: {int(stats.get('no_order_users') or 0)}\n"
        f"◈ تعداد کاربران منقضی شده بیش از یک هفته: {int(stats.get('expired_1w_users') or 0)}\n"
        f"◈ تعداد کاربران منقضی شده بیش از دو هفته: {int(stats.get('expired_2w_users') or 0)}\n"
        f"◈ تعداد کاربران منقضی شده بیش از چهار هفته: {int(stats.get('expired_4w_users') or 0)}\n"
        f"◈ تعداد کاربران منقضی شده بیش از هشت هفته: {int(stats.get('expired_8w_users') or 0)}"
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
    shot_line = "📎 اسکرین‌شات: ارسال شده ✅" if has_photo else "📎 اسکرین‌شات: ارسال نشد"
    return (
        "📩 تایید اطلاعات پاسخ تیکت\n\n"
        f"📝 پاسخ:\n{body}\n\n"
        f"{shot_line}\n\n"
        "❗️در صورت تایید اطلاعات، برای ارسال تیکت گزینه «✅ارسال» را انتخاب نمایید."
    )


def _build_ticket_detail_text(
    ticket: Dict[str, Any],
    messages: List[Dict[str, Any]],
    screenshot_links: Optional[Dict[int, str]] = None,
) -> str:
    code = str(ticket.get("ticket_code") or "-")
    status = _ticket_status_title(str(ticket.get("status") or "pending"))
    created = str(ticket.get("created_at") or "-")
    admin_name = str(ticket.get("admin_name") or "").strip() or "تنظیم نشده"
    user_label = _ticket_user_label(ticket)
    username = str(ticket.get("username") or ticket.get("db_username") or "").strip().lstrip("@")
    tg_id = str(ticket.get("telegram_id") or ticket.get("db_telegram_id") or "-")

    lines = [
        f"🧾 شناسه تیکت: {html_escape(code)}",
        f"📅 تاریخ ایجاد: {html_escape(created)}",
        f"◈ وضعیت تیکت: {html_escape(status)}",
        f"👤 کاربر: {html_escape(user_label)}",
        f"🔹 نام کاربری: {html_escape(username or '-')}",
        f"🔢 شناسه کاربر: {html_escape(tg_id)}",
        f"👨‍💻 ادمین: {html_escape(admin_name)}",
        "❖⬩--------------------------------⬩❖",
    ]

    for idx, m in enumerate(messages, start=1):
        sender_type = str(m.get("sender_type") or "").strip().lower()
        sender_name = str(m.get("sender_name") or "").strip() or ("کاربر" if sender_type == "user" else "ادمین")
        created_at = str(m.get("created_at") or "-")
        text = str(m.get("message_text") or "").strip()
        photo = str(m.get("photo_file_id") or "").strip()
        lines.append(f"📅 تاریخ ایجاد: {html_escape(created_at)} | #{idx}")
        lines.append(f"◈ {'سوال' if sender_type == 'user' else 'پاسخ'}:")
        lines.append(html_escape(sender_name))
        if text:
            lines.append(html_escape(text))
        if photo:
            shot_link = str((screenshot_links or {}).get(idx) or "").strip()
            if shot_link:
                lines.append(f"🖼 <a href=\"{html_escape(shot_link, quote=True)}\">اسکرین‌شات #{idx}</a>")
            else:
                lines.append(f"🖼 اسکرین‌شات #{idx}")
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
        await message.reply_text("❌ تیکت موردنظر پیدا نشد.")
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
        await message.reply_text("❌ اسکرین‌شات موردنظر یافت نشد.")
        return True

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت به تیکت", callback_data=f"userbot:ticket:detail:{code}:pending:1")]
    ])
    caption = (
        f"🖼 اسکرین‌شات تیکت #{code}\n"
        f"◈ عکس #{int(photo_ref['idx'])}\n"
        f"📅 زمان: {photo_ref['created_at']}"
    )
    sent = await _send_ticket_photo_with_fallback(
        context=context,
        chat_id=message.chat_id,
        photo_id=str(photo_ref["photo_file_id"]),
        caption=caption,
        reply_markup=kb,
    )
    if not sent:
        await message.reply_text("❌ ارسال عکس ممکن نشد.")
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
            "📣 گزارش رویداد پرداخت\n"
            f"👤 کاربر: {full_name}\n"
            f"◈ نام کاربری: {username}\n"
            f"◈ شناسه کاربر: {tg_id}\n"
            f"🔑 شناسه تراکنش: {tx_code}\n"
            f"💰 مبلغ: {amount} تومان\n"
            "✅ وضعیت: تایید شده"
        )
        await user_bot.send_message(chat_id=chat_target, text=text)
    except Exception as e:
        logger.warning("Failed sending payment event-channel report: %s", e)


def _build_payment_action_keyboard(payment_id: int, user_btn_title: str, uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("✏️ تغییر وضعیت تراکنش", callback_data=f"userbot:pay:chg:{payment_id}")],
        [InlineKeyboardButton(f"👤 {user_btn_title}", callback_data=f"userbot:user:{uid}")],
    ]
    return InlineKeyboardMarkup(rows)


def _build_payment_change_confirm_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله", callback_data=f"userbot:pay:chg:yes:{payment_id}")],
        [InlineKeyboardButton("❌ خیر", callback_data=f"userbot:pay:chg:no:{payment_id}")],
    ])


def _build_payment_change_options_keyboard(payment_id: int, current_status: str) -> InlineKeyboardMarkup:
    status = (current_status or "").strip().lower()
    rows = []
    if status != "approved":
        rows.append([InlineKeyboardButton("✅ تایید شده", callback_data=f"userbot:pay:set:{payment_id}:approved")])
    if status != "rejected":
        rows.append([InlineKeyboardButton("❌ رد شده", callback_data=f"userbot:pay:set:{payment_id}:rejected")])
    if status != "pending":
        rows.append([InlineKeyboardButton("⏳ در انتظار", callback_data=f"userbot:pay:set:{payment_id}:pending")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"userbot:pay:detail:{payment_id}")])
    return InlineKeyboardMarkup(rows)


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
        return "📶آخرین اتصال: نامشخص"
    
    delta = datetime.now(timezone.utc).replace(tzinfo=None) - dt
    days = delta.days
    seconds = delta.seconds
    if days <= 0:
        if seconds < 60:
            rel = "چند ثانیه پیش"
        elif seconds < 3600:
            rel = f"{seconds // 60} دقیقه پیش"
        else:
            rel = f"{seconds // 3600} ساعت پیش"
    elif days < 30:
        rel = f"{days} روز پیش"
    elif days < 365:
        rel = f"{days // 30} ماه پیش"
    else:
        rel = f"{days // 365} سال پیش"
    return f"📶آخرین اتصال: {rel}"


def _parse_last_online_dt(last_online_raw: Optional[str]) -> Optional[datetime]:
    if not last_online_raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(last_online_raw, fmt)
        except ValueError:
            continue
    return None


def _service_last_online_line(last_online_raw: Optional[str]) -> str:
    dt = _parse_last_online_dt(last_online_raw)
    if not dt:
        return "📶آخرین اتصال: نامشخص"
    # اگر اخیراً آنلاین بوده، خروجی ساده و شبیه UI مدنظر نشان بده.
    if (datetime.now(timezone.utc).replace(tzinfo=None) - dt).total_seconds() <= 4 * 3600:
        return "📶آخرین اتصال: آنلاین"
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
    return " 🀄️لطفا شناسه اشتراک را وارد کنید:"


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
    title = str(raw_title or "").strip() or "نامشخص"
    flag = _location_flag_from_title(title)
    has_location_word = "لوکیشن" in title
    has_flag = flag != "🏳️" and flag in title
    if has_location_word:
        if has_flag or flag == "🏳️":
            return title
        return f"{title} {flag}"
    if flag == "🏳️":
        return f"لوکیشن {title}"
    return f"لوکیشن {flag} {title}"


def _resolve_live_server_title(service: Dict[str, Any], default: str = "سرور") -> str:
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
        return f"سرور #{sid}"

    return stored_title or default


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
    service_name = str(service.get("name") or "سرویس").strip() or "سرویس"
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
        [InlineKeyboardButton("📄کانفیگ ها", callback_data=cfg_cb)],
        [InlineKeyboardButton("✏️ویرایش کاربر", callback_data=edit_cb)],
        [InlineKeyboardButton("🗑حذف کاربر", callback_data=del_cb)],
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


def _is_locally_active_service(service: Dict[str, Any]) -> bool:
    status = str(service.get("status") or "").strip().lower()
    if status in {"deleted", "removed", "inactive", "disabled", "expired"}:
        return False
    days_left = _to_int_or_none(service.get("days_left"))
    if days_left is not None and days_left <= 0:
        return False
    return True


def _panel_user_is_active(user_data: Dict[str, Any]) -> bool:
    raw = user_data.get("is_active")
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in {"0", "false", "off", "inactive", "disabled"}


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
    targets = _service_panel_targets(service)
    if not targets:
        return True

    had_unknown_error = False
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
            if isinstance(panel_user, dict) and _panel_user_is_active(panel_user):
                return True
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
        return True
    return False


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
    server_title = _format_server_location_title(_resolve_live_server_title(service, default="سرور"))

    usage_current = _to_float(service.get("usage_current"))
    usage_limit = _to_float(service.get("usage_limit"))
    days_left = service.get("days_left")
    meta = _parse_service_comment_meta(str(service.get("comment") or ""))

    if usage_current is None and usage_limit is None:
        usage_line = "📊میزان استفاده: نامشخص"
    elif usage_limit is None or usage_limit <= 0:
        usage_line = f"📊میزان استفاده: {(usage_current or 0.0):.1f} از نامحدود گیگ"
    else:
        usage_line = f"📊میزان استفاده: {(usage_current or 0.0):.1f} از {usage_limit:.1f} گیگ"

    if days_left is None:
        expire_line = "⏳زمان باقی مانده: نامشخص"
    elif days_left < 0:
        expire_line = f"⏳زمان باقی مانده: منقضی شده ({abs(int(days_left))} روز پیش)"
    else:
        expire_line = f"⏳زمان باقی مانده: {int(days_left)} روز"

    price_line = "💰قیمت اشتراک: نامشخص"
    price_raw = str(meta.get("price") or "").strip()
    if price_raw.isdigit():
        price_line = f"💰قیمت اشتراک: {int(price_raw):,} تومان"

    lines = [
        f"👤 کاربر:  {user_name}",
        "❖⬩╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍⬩❖",
        f"📡سرور: {server_title}",
        usage_line,
        expire_line,
        price_line,
        f"📝یادداشت: {_service_public_note_text(service)}",
    ]
    return "\n".join(lines)


def build_service_detail_text(user: Dict[str, Any], service: Dict[str, Any]) -> str:
    """متن جزئیات سرویس (برای منوی لیست سرویس‌ها)."""
    service_name = (service.get("name") or "سرویس").strip()
    server_title = _format_server_location_title(_resolve_live_server_title(service, default="سرور"))

    usage_current = _to_float(service.get("usage_current"))
    usage_limit = _to_float(service.get("usage_limit"))
    days_left = service.get("days_left")
    last_online_raw = service.get("last_online")
    comment = _service_public_note_text(service)
    if comment == "-":
        comment = _synthetic_hiddify_note(service)
    comment = _display_safe_note(comment)

    if usage_current is None:
        usage_line = "📊مصرف: نامشخص"
    elif usage_limit is None:
        usage_line = f"📊مصرف: {usage_current:.2f} گیگابایت (نامحدود)"
    else:
        usage_line = f"📊مصرف: {usage_current:.2f} از {usage_limit:.1f} گیگابایت"

    if days_left is None:
        expire_line = "📆انقضا: نامشخص"
    elif days_left < 0:
        expire_line = f"📆انقضا: منقضی شده ({abs(int(days_left))} روز پیش)"
    else:
        expire_line = f"📆انقضا: {int(days_left)} روز دیگر"

    last_online_line = _service_last_online_line(last_online_raw)

    header_line = f"👤 کاربر:  {service_name}"
    sep_line = "❖⬩╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍⬩❖"
    server_line = f"⬖ سرور:  {server_title}"

    lines = [
        header_line,
        sep_line,
        server_line,
        usage_line,
        expire_line,
        last_online_line,
        f"📝یادداشت: {comment if str(comment).strip() else '—'}",
    ]
    return "\n".join(lines)


def userbot_cancel_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد لغو برای ویزاردها"""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("❌لغو")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ===============================
#   Keyboards (منوها)
# ===============================

def build_userbot_main_menu() -> InlineKeyboardMarkup:
    """منوی اصلی مدیریت ربات کاربران"""
    rows = [
        [
            InlineKeyboardButton("👤مدیریت کاربران ربات", callback_data="userbot:users_menu")
        ],
        [
            InlineKeyboardButton("💵مدیریت تراکنشات", callback_data="userbot:payments_menu"),
            InlineKeyboardButton("📗مدیریت سفارشات", callback_data="userbot:orders_menu"),
        ],
        [
            InlineKeyboardButton("🎁مدیریت هدایا", callback_data="userbot:gifts_menu")
        ],
        [
            InlineKeyboardButton("📑مدیریت تیکت‌ها", callback_data="userbot:tickets_menu"),
            InlineKeyboardButton("📧ارسال پیام همگانی", callback_data="userbot:broadcast_menu"),
        ],
        [
            InlineKeyboardButton("⚙️تنظیمات", callback_data="userbot:settings_menu")
        ],
    ]
    return InlineKeyboardMarkup(rows)


def build_payments_menu_keyboard() -> InlineKeyboardMarkup:
    """منوی مدیریت تراکنشات (طبق عکس)"""
    rows = [
        [InlineKeyboardButton("✅لیست تراکنشات تایید شده", callback_data="userbot:payments:list:approved")],
        [InlineKeyboardButton("🚫لیست تراکنشات رد شده", callback_data="userbot:payments:list:rejected")],
        [InlineKeyboardButton("⏳لیست تراکنشات در انتظار", callback_data="userbot:payments:list:pending")],
        [InlineKeyboardButton("💳لیست تراکنشات کارت به کارت", callback_data="userbot:payments:list:card")],
        [InlineKeyboardButton("🔍جستجوی تراکنش", callback_data="userbot:payments:search")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_users_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("👥لیست کاربران ربات", callback_data="userbot:users:1")],
        [InlineKeyboardButton("🔍جستجوی کاربران", callback_data="userbot:users_search_menu")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_users_search_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("👤جستجو با نام", callback_data="userbot:search:name")],
        [InlineKeyboardButton("✝️جستجو با Telegram ID", callback_data="userbot:search:id")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:users_menu")],
    ]
    return InlineKeyboardMarkup(rows)


# ... (Keyboards قبلی) ...
def build_user_profile_keyboard(user_id: int, back_callback: str = "userbot:users_menu") -> InlineKeyboardMarkup:
    """کیبورد پروفایل"""
    rows = [
        [InlineKeyboardButton("📋 لیست سرویس‌ها", callback_data=f"userbot:user:{user_id}:services")],
        [InlineKeyboardButton("📗 لیست سفارشات", callback_data=f"userbot:user:{user_id}:orders")],
        [InlineKeyboardButton("💵 لیست تراکنشات", callback_data=f"userbot:user:{user_id}:payments")],
        [InlineKeyboardButton("💳 ویرایش کیف پول", callback_data=f"userbot:user:{user_id}:wallet")],
        [InlineKeyboardButton("🔄 بازنشانی اشتراک تستی", callback_data=f"userbot:user:{user_id}:reset_trial")],
        [InlineKeyboardButton("🚫 مسدود/آزاد سازی کاربر", callback_data=f"userbot:user:{user_id}:ban")],
        [InlineKeyboardButton("📨 ارسال پیام", callback_data=f"userbot:user:{user_id}:message"),
         InlineKeyboardButton("📑 لیست تیکت‌ها", callback_data=f"userbot:user:{user_id}:tickets")],
        [InlineKeyboardButton("🔙بازگشت", callback_data=back_callback)],
    ]
    return InlineKeyboardMarkup(rows)

def build_service_detail_keyboard(user_id: int, service_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📄کانفیگ ها", callback_data=f"userbot:svc:{service_id}:configs")],
        [InlineKeyboardButton("✏️ویرایش کاربر", callback_data=f"userbot:svc:{service_id}:edit")],
        [InlineKeyboardButton("∞تمدید اشتراک", callback_data=f"userbot:svc:{service_id}:extend")],
        [InlineKeyboardButton("🗑حذف کاربر", callback_data=f"userbot:svc:{service_id}:delete")],
        [InlineKeyboardButton("📋بازگشت به سرویس‌ها", callback_data=f"userbot:user:{user_id}:services")],
        [InlineKeyboardButton("👤بازگشت به پروفایل", callback_data=f"userbot:user:{user_id}")],
    ]
    return InlineKeyboardMarkup(rows)

def build_orders_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📗لیست سفارشات", callback_data="userbot:orders:list:1")],
        [InlineKeyboardButton("🔍جستجوی سفارشات", callback_data="userbot:orders:search")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:menu")],
    ])


def build_gifts_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🏷کد تخفیف و کوپن", callback_data="userbot:gifts:coupons")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_tickets_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📨تیکت‌های در انتظار", callback_data="userbot:tickets:list:pending:1")],
        [InlineKeyboardButton("📬تیکت‌های باز", callback_data="userbot:tickets:list:open:1")],
        [InlineKeyboardButton("📩تیکت‌های بسته", callback_data="userbot:tickets:list:closed:1")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_broadcast_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("تمام کاربران", callback_data="userbot:broadcast:segment:all")],
        [InlineKeyboardButton("تمام کاربران منقضی شده", callback_data="userbot:broadcast:segment:expired_all")],
        [InlineKeyboardButton("کاربران بدون سفارش", callback_data="userbot:broadcast:segment:no_order")],
        [InlineKeyboardButton("کاربران منقضی شده بیش از یک هفته", callback_data="userbot:broadcast:segment:expired_1w")],
        [InlineKeyboardButton("کاربران منقضی شده بیش از دو هفته", callback_data="userbot:broadcast:segment:expired_2w")],
        [InlineKeyboardButton("کاربران منقضی شده بیش از چهار هفته", callback_data="userbot:broadcast:segment:expired_4w")],
        [InlineKeyboardButton("کاربران منقضی شده بیش از هشت هفته", callback_data="userbot:broadcast:segment:expired_8w")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def broadcast_skip_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("⏩رد کردن")],
            [KeyboardButton("❌لغو")],
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
        rows.append([InlineKeyboardButton("🔙بازگشت", callback_data=f"userbot:user:{from_user_id}")])
    else:
        rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="userbot:tickets_menu")])
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
    else:
        list_status = str(list_status or "pending").strip().lower()
        reply_callback = f"userbot:ticket:reply:{code}:{list_status}:{page}"

    rows = []
    if user_id > 0:
        rows.append([InlineKeyboardButton(user_btn, callback_data=f"userbot:user:{user_id}")])
    rows.append([InlineKeyboardButton("📩پاسخ", callback_data=reply_callback)])
    return InlineKeyboardMarkup(rows)


def build_ticket_reply_screenshot_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏩رد کردن", callback_data="userbot:ticketreply:skip")],
        [InlineKeyboardButton("❌لغو", callback_data="userbot:ticketreply:cancel")],
    ])


def build_ticket_reply_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ارسال", callback_data="userbot:ticketreply:send"),
            InlineKeyboardButton("✏️ویرایش", callback_data="userbot:ticketreply:edit"),
        ],
        [InlineKeyboardButton("❌لغو", callback_data="userbot:ticketreply:cancel")],
    ])


def build_zarin_coupons_list_keyboard(coupons: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for item in coupons:
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        rows.append([InlineKeyboardButton(code, callback_data=f"userbot:gifts:coupon:{code}")])
    rows.append([InlineKeyboardButton("افزودن کوپن جدید➕", callback_data="userbot:gifts:coupons:add")])
    rows.append([InlineKeyboardButton("حذف کوپن➖", callback_data="userbot:gifts:coupons:delete")])
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="userbot:gifts_menu")])
    return InlineKeyboardMarkup(rows)


def build_zarin_coupon_detail_keyboard(code: str) -> InlineKeyboardMarkup:
    c = str(code or "").strip()
    rows = [
        [InlineKeyboardButton("💳تنظیم لینک پرداخت زرین پال", callback_data=f"userbot:gifts:coupon:set_link:{c}")],
        [InlineKeyboardButton("🚀ایجاد دیپ لینک", callback_data=f"userbot:gifts:coupon:deeplink:{c}")],
        [InlineKeyboardButton("✏️ویرایش کد", callback_data=f"userbot:gifts:coupon:set_code:{c}")],
        [InlineKeyboardButton("👤ویرایش محدودیت کاربر", callback_data=f"userbot:gifts:coupon:set_limit:{c}")],
        [InlineKeyboardButton("🕒ویرایش مدت زمان انقضا", callback_data=f"userbot:gifts:coupon:set_exp:{c}")],
        [InlineKeyboardButton("🎁ویرایش هدیه کوپن", callback_data=f"userbot:gifts:coupon:set_amount:{c}")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:gifts:coupons")],
    ]
    return InlineKeyboardMarkup(rows)


def build_userbot_settings_menu_keyboard(ui_settings: Optional[Dict[str, Any]] = None) -> InlineKeyboardMarkup:
    theme = normalize_button_theme((ui_settings or {}).get("button_theme"))
    theme_title = BUTTON_STYLE_THEMES.get(theme, BUTTON_STYLE_THEMES["smart"])["title"]
    rows = [
        [InlineKeyboardButton("🛍تنظیمات اشتراک", callback_data="userbot:settings:subscription")],
        [InlineKeyboardButton("📁وضعیت نمایش لینک اشتراک", callback_data="userbot:settings:sub_link_status")],
        [InlineKeyboardButton(f"🎨 دکمه‌های رنگی | {theme_title}", callback_data="userbot:settings:ui")],
        [InlineKeyboardButton("🛒تنظیمات خرید و تمدید", callback_data="userbot:settings:buy_renew")],
        [InlineKeyboardButton("🧮تنظیمات تراکنشات و پلن ها", callback_data="userbot:settings:tx_plans")],
        [InlineKeyboardButton("🧾تنظیمات متون", callback_data="userbot:settings:texts")],
        [InlineKeyboardButton("🎯تنظیمات بازاریابی", callback_data="userbot:settings:marketing")],
        [InlineKeyboardButton("🔒تنظیمات عضویت اجباری", callback_data="userbot:settings:force_join")],
        [InlineKeyboardButton("💳تنظیمات پرداخت", callback_data="userbot:settings:payment")],
        [InlineKeyboardButton("🗂️تنظیمات بکاپ و بازیابی", callback_data="userbot:settings:backup_restore")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_colored_buttons_settings_keyboard(ui_settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    enabled = bool(ui_settings.get("colored_buttons", True))
    current_theme = normalize_button_theme(ui_settings.get("button_theme"))
    enabled_icon = "✅" if enabled else "❌"
    rows = [
        [InlineKeyboardButton(f"رنگی بودن دکمه‌ها | {enabled_icon}", callback_data="userbot:settings:ui:colored_buttons")],
        [InlineKeyboardButton("🎛 انتخاب طرح رنگی", callback_data="userbot:noop")],
    ]
    for theme_key, meta in BUTTON_STYLE_THEMES.items():
        selected_icon = "✅" if theme_key == current_theme else "▫️"
        rows.append([
            InlineKeyboardButton(
                f"{selected_icon} {meta['title']}",
                callback_data=f"userbot:settings:ui:theme:{theme_key}",
            )
        ])
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings_menu")])
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
        [InlineKeyboardButton(f"نمایش لینک صفحه یوزر هیدیفای | {user_page_icon}", callback_data="userbot:settings:subscription:show_user_page_link")],
        [InlineKeyboardButton(f"نمایش نام کاربری | {username_icon}", callback_data="userbot:settings:subscription:show_username")],
        [InlineKeyboardButton(f"تصادفی کردن کانفیگ‌ها | {shuffle_icon}", callback_data="userbot:settings:subscription:shuffle_configs")],
        [InlineKeyboardButton(f"تصادفی کردن چینش سرورها | {shuffle_server_icon}", callback_data="userbot:settings:subscription:shuffle_server_layout")],
        [InlineKeyboardButton(f"تصادفی کردن چینش کانفیگ‌ها | {shuffle_config_icon}", callback_data="userbot:settings:subscription:shuffle_config_layout")],
        [InlineKeyboardButton("🔔یادآور وضعیت اشتراک", callback_data="userbot:settings:subscription:sub_status_reminder")],
        [InlineKeyboardButton("🎊مشخصات اشتراک تستی", callback_data="userbot:settings:subscription:trial_spec")],
        [InlineKeyboardButton("🔄بازنشانی تست رایگان", callback_data="userbot:settings:subscription:reset_free_trial")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings_menu")],
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
        [InlineKeyboardButton(f"کانفیگ مستقیم | {direct_icon}", callback_data="userbot:settings:sub_link_status:show_direct_config")],
        [InlineKeyboardButton(f"لینک اشتراک خودکار | {auto_icon}", callback_data="userbot:settings:sub_link_status:show_auto_sub_link")],
        [InlineKeyboardButton(f"لینک اشتراک | {sub_icon}", callback_data="userbot:settings:sub_link_status:show_sub_link")],
        [InlineKeyboardButton(f"لینک اشتراک b64 | {sub_b64_icon}", callback_data="userbot:settings:sub_link_status:show_sub_link_b64")],
        [InlineKeyboardButton(f"لینک اشتراک هوشمند | {multi_icon}", callback_data="userbot:settings:sub_link_status:show_multi_server")],
        [InlineKeyboardButton(f"لینک اشتراک هوشمند b64 | {multi_b64_icon}", callback_data="userbot:settings:sub_link_status:show_multi_server_b64")],
        [InlineKeyboardButton("🌐 تنظیم دامنه لینک اشتراک هوشمند", callback_data="userbot:settings:sub_link_status:set_base_url")],
        [InlineKeyboardButton("🔐 راهنمای SSL دامنه", callback_data="userbot:settings:sub_link_status:ssl_help")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_sub_status_reminder_menu_keyboard(reminder: Dict[str, Any]) -> InlineKeyboardMarkup:
    enabled_icon = "✅" if reminder.get("enabled", True) else "❌"
    rows = [
        [InlineKeyboardButton(f"🔔 یادآور وضعیت اشتراک | {enabled_icon}", callback_data="userbot:settings:sub_status_reminder:enabled")],
        [InlineKeyboardButton("📊 یادآور وضعیت مصرف", callback_data="userbot:settings:sub_status_reminder:usage")],
        [InlineKeyboardButton("📆 یادآور وضعیت زمان", callback_data="userbot:settings:sub_status_reminder:days")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:subscription")],
    ]
    return InlineKeyboardMarkup(rows)


def build_trial_spec_menu_keyboard(spec: Dict[str, Any]) -> InlineKeyboardMarkup:
    enabled_icon = "✅" if spec.get("enabled", True) else "❌"
    announce_icon = "✅" if spec.get("announce_enabled", True) else "❌"
    rows = [
        [InlineKeyboardButton(f"🔥 وضعیت اشتراک تستی | {enabled_icon}", callback_data="userbot:settings:trial_spec:enabled")],
        [InlineKeyboardButton(f"🔔 اعلان اشتراک تستی | {announce_icon}", callback_data="userbot:settings:trial_spec:announce")],
        [InlineKeyboardButton("📊 حجم اشتراک تستی", callback_data="userbot:settings:trial_spec:usage")],
        [InlineKeyboardButton("📆 مدت اشتراک تستی", callback_data="userbot:settings:trial_spec:days")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:subscription")],
    ]
    return InlineKeyboardMarkup(rows)


def build_buy_renew_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    buy_icon = "✅" if settings.get("enable_buy", True) else "❌"
    renew_icon = "✅" if settings.get("enable_renew", True) else "❌"
    renew_btn_icon = "✅" if settings.get("show_renew_in_main_menu", True) else "❌"
    event_status = "✅" if settings.get("event_channel_enabled", False) else "❌"

    rows = [
        [InlineKeyboardButton(f"امکان خرید اشتراک | {buy_icon}", callback_data="userbot:settings:buy_renew:enable_buy")],
        [InlineKeyboardButton(f"امکان تمدید اشتراک | {renew_icon}", callback_data="userbot:settings:buy_renew:enable_renew")],
        [InlineKeyboardButton(f"دکمه تمدید اشتراک در منوی اصلی | {renew_btn_icon}", callback_data="userbot:settings:buy_renew:show_renew_in_main_menu")],
        [InlineKeyboardButton("تنظیم شیوه تمدید", callback_data="userbot:settings:buy_renew:renew_mode_info")],
        [
            InlineKeyboardButton("ستون‌های پلن‌ها", callback_data="userbot:settings:buy_renew:plan_columns:menu"),
            InlineKeyboardButton("ستون‌های سرورها", callback_data="userbot:settings:buy_renew:server_columns:menu"),
        ],
        [InlineKeyboardButton(f"حجم نامحدود∞ | {'✅' if settings.get('renew_unlimited_volume', False) else '❌'}", callback_data="userbot:settings:buy_renew:renew_unlimited_volume")],
        [InlineKeyboardButton(f"زمان نامحدود∞ | {'✅' if settings.get('renew_unlimited_time', False) else '❌'}", callback_data="userbot:settings:buy_renew:renew_unlimited_time")],
        [
            InlineKeyboardButton(event_status, callback_data="userbot:settings:buy_renew:event_channel_enabled"),
            InlineKeyboardButton("تنظیم کانال رویداد📢", callback_data="userbot:settings:buy_renew:event_channel_set"),
        ],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_tx_plans_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    random_icon = "✅" if settings.get("random_tx_spec", False) else "❌"
    rows = [
        [InlineKeyboardButton(f"🔢مشخصه تصادفی تراکنش | {random_icon}", callback_data="userbot:settings:tx_plans:random_tx_spec")],
        [InlineKeyboardButton("🧲محدودیت حداقل تراکنش", callback_data="userbot:settings:tx_plans:min_tx")],
        [InlineKeyboardButton("🗂دسته بندی پلن‌ها", callback_data="userbot:settings:tx_plans:plan_categories_mode:menu")],
        [InlineKeyboardButton("🔢ترتیب پلن‌ها", callback_data="userbot:settings:tx_plans:plan_sort_mode:menu")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_text_settings_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔔پیام خوش آمدگویی", callback_data="userbot:settings:texts:edit:welcome_message")],
        [InlineKeyboardButton("📕متن سوالات متداول", callback_data="userbot:settings:texts:edit:faq_text")],
        [InlineKeyboardButton("💡متن راهنما", callback_data="userbot:settings:texts:guide_menu")],
        [InlineKeyboardButton("📄تنظیم بنر دعوت", callback_data="userbot:settings:texts:invite_menu")],
        [InlineKeyboardButton("🛰️متن لیست سرورها", callback_data="userbot:settings:texts:edit:servers_list_text")],
        [InlineKeyboardButton("📋متن لیست پلن‌ها", callback_data="userbot:settings:texts:edit:plans_list_text")],
        [InlineKeyboardButton("📬متن پنل تیکت", callback_data="userbot:settings:texts:edit:ticket_panel_text")],
        [InlineKeyboardButton("📝تنظیم متن زرین پال", callback_data="userbot:settings:texts:edit:zarinpal_pro_text")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_guide_text_settings_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📱 راهنمای اندروید", callback_data="userbot:settings:texts:edit:guide_android_text")],
        [InlineKeyboardButton("📱 راهنمای IOS", callback_data="userbot:settings:texts:edit:guide_ios_text")],
        [InlineKeyboardButton("🖥️ راهنمای ویندوز", callback_data="userbot:settings:texts:edit:guide_windows_text")],
        [InlineKeyboardButton("💻 راهنمای مک", callback_data="userbot:settings:texts:edit:guide_mac_text")],
        [InlineKeyboardButton("🖥️ راهنمای لینوکس", callback_data="userbot:settings:texts:edit:guide_linux_text")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:texts")],
    ]
    return InlineKeyboardMarkup(rows)


def build_invite_text_settings_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📄تنظیم متن اطلاعات دعوت", callback_data="userbot:settings:texts:edit:invite_info_text")],
        [InlineKeyboardButton("🧾تنظیم متن بنر", callback_data="userbot:settings:texts:edit:invite_banner_text")],
        [InlineKeyboardButton("🖼️افزودن عکس", callback_data="userbot:settings:texts:invite:add_photo")],
        [InlineKeyboardButton("❌حذف بنر", callback_data="userbot:settings:texts:invite:remove_photo")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:texts")],
    ]
    return InlineKeyboardMarkup(rows)


def build_marketing_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    discount_icon = "✅" if settings.get("enable_discount_code", False) else "❌"
    increase_icon = "✅" if settings.get("enable_increase_code", False) else "❌"
    gift_btn_icon = "✅" if settings.get("show_gift_button", False) else "❌"
    user_status_icon = "✅" if settings.get("show_user_status", True) else "❌"
    instant_coupon_icon = "✅" if settings.get("instant_gift_coupon", False) else "❌"
    rows = [
        [InlineKeyboardButton("🎯تنظیمات بازاریابی", callback_data="userbot:noop")],
        [InlineKeyboardButton(f"🛍 اعمال کد تخفیف | {discount_icon}", callback_data="userbot:settings:marketing:toggle:enable_discount_code")],
        [InlineKeyboardButton(f"🔼 اعمال کد افزایشی | {increase_icon}", callback_data="userbot:settings:marketing:toggle:enable_increase_code")],
        [InlineKeyboardButton(f"🎁 دکمه هدیه | {gift_btn_icon}", callback_data="userbot:settings:marketing:toggle:show_gift_button")],
        [InlineKeyboardButton(f"👤 نمایش وضعیت کاربر | {user_status_icon}", callback_data="userbot:settings:marketing:toggle:show_user_status")],
        [InlineKeyboardButton(f"🚀 استفاده آنی هدیه کوپن | {instant_coupon_icon}", callback_data="userbot:settings:marketing:toggle:instant_gift_coupon")],
        [InlineKeyboardButton("⚙️تنظیم متن هدایای اتوماتیک", callback_data="userbot:settings:marketing:edit:auto_gift_text")],
        [InlineKeyboardButton("⚙️حداقل شارژ هدیه اتوماتیک", callback_data="userbot:settings:marketing:edit:min_auto_gift_charge")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_force_join_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    enabled_icon = "✅" if settings.get("enabled", False) else "❌"
    rows = [
        [InlineKeyboardButton("🧩راهنما", callback_data="userbot:settings:force_join:help")],
        [InlineKeyboardButton("عضویت اجباری | " + enabled_icon, callback_data="userbot:settings:force_join:toggle")],
        [InlineKeyboardButton("📢تنظیم کانال پشتیبانی", callback_data="userbot:settings:force_join:set_channel")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_payment_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    event_icon = "✅" if settings.get("event_channel_enabled", False) else "❌"
    rows = [
        [InlineKeyboardButton("💳تنظیمات کارت به کارت", callback_data="userbot:settings:payment:card")],
        [InlineKeyboardButton("📦تنظیمات زرین پال", callback_data="userbot:settings:payment:zarinpal")],
        [InlineKeyboardButton("🧰تنظیمات پرفکت مانی", callback_data="userbot:settings:payment:perfect")],
        [InlineKeyboardButton("🔗تنظیمات پرداخت ارز دیجیتال", callback_data="userbot:settings:payment:crypto")],
        [
            InlineKeyboardButton(event_icon, callback_data="userbot:settings:payment:event_channel_toggle"),
            InlineKeyboardButton("📢تنظیم کانال رویداد", callback_data="userbot:settings:payment:event_channel_set"),
        ],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_backup_restore_settings_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    auto_icon = "✅" if settings.get("auto_backup_enabled", True) else "❌"
    event_icon = "✅" if settings.get("event_channel_enabled", False) else "❌"
    rows = [
        [InlineKeyboardButton(f"ارسال خودکار بکاپ | {auto_icon}", callback_data="userbot:settings:backup_restore:auto_toggle")],
        [
            InlineKeyboardButton("📩دریافت فایل بکاپ", callback_data="userbot:settings:backup_restore:download"),
            InlineKeyboardButton("📩بازیابی بکاپ", callback_data="userbot:settings:backup_restore:restore"),
        ],
        [
            InlineKeyboardButton(event_icon, callback_data="userbot:settings:backup_restore:event_toggle"),
            InlineKeyboardButton("📢تنظیم کانال رویداد", callback_data="userbot:settings:backup_restore:event_set"),
        ],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def build_payment_method_menu_keyboard(method: str, enabled: bool) -> InlineKeyboardMarkup:
    title_map = {
        "card": "کارت به کارت",
        "zarinpal": "زرین پال",
        "perfect": "پرفکت مانی",
        "crypto": "پرداخت ارز دیجیتال",
    }
    title = title_map.get(method, "روش پرداخت")
    icon = "✅" if enabled else "❌"
    if method == "card":
        tx_settings = _get_tx_plans_settings()
        random_icon = "✅" if bool(tx_settings.get("random_tx_spec", False)) else "❌"
        pay_settings = _get_payment_settings()
        last4_icon = "✅" if bool(pay_settings.get("require_last4_for_card_receipt", False)) else "❌"
        rows = [
            [InlineKeyboardButton(f"پرداخت کارت به کارت | {icon}", callback_data="userbot:settings:payment:toggle:card")],
            [InlineKeyboardButton(f"🔐 الزام ۴ رقم آخر کارت | {last4_icon}", callback_data="userbot:settings:payment:card:require_last4")],
            [InlineKeyboardButton(f"🔢مشخصه تصادفی تراکنش | {random_icon}", callback_data="userbot:settings:payment:card:random_tx_spec")],
            [InlineKeyboardButton("💳لیست کارت‌ها", callback_data="userbot:settings:payment:card:list")],
            [InlineKeyboardButton("📝تنظیم متن کارت به کارت", callback_data="userbot:settings:payment:card:text")],
            [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:payment")],
        ]
        return InlineKeyboardMarkup(rows)

    if method == "zarinpal":
        rows = [
            [InlineKeyboardButton(f"{icon} | درگاه زرین پال", callback_data="userbot:settings:payment:toggle:zarinpal")],
            [InlineKeyboardButton("📝تنظیم متن زرین پال", callback_data="userbot:settings:texts:edit:zarinpal_pro_text")],
            [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:payment")],
        ]
        return InlineKeyboardMarkup(rows)

    rows = [[InlineKeyboardButton(f"{title} | {icon}", callback_data=f"userbot:settings:payment:toggle:{method}")]]
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:payment")])
    return InlineKeyboardMarkup(rows)


def build_payment_cards_list_keyboard(cards: List[Dict[str, str]]) -> InlineKeyboardMarkup:
    rows = []
    for c in cards:
        number = str(c.get("number") or "").strip()
        if not number:
            continue
        rows.append([InlineKeyboardButton(number, callback_data=f"userbot:settings:payment:card:item:{number}")])
    rows.append([InlineKeyboardButton("افزودن کارت➕", callback_data="userbot:settings:payment:card:add")])
    rows.append([InlineKeyboardButton("حذف کارت➖", callback_data="userbot:settings:payment:card:delete")])
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:payment:card")])
    return InlineKeyboardMarkup(rows)


def build_payment_card_item_keyboard(number: str) -> InlineKeyboardMarkup:
    n = str(number or "").strip()
    rows = [
        [InlineKeyboardButton("💳ویرایش شماره کارت", callback_data=f"userbot:settings:payment:card:edit_number:{n}")],
        [InlineKeyboardButton("🧑ویرایش نام صاحب کارت", callback_data=f"userbot:settings:payment:card:edit_owner:{n}")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:payment:card:list")],
    ]
    return InlineKeyboardMarkup(rows)


def build_plan_categories_mode_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    categories_enabled = bool(settings.get("plan_categories_enabled", True))
    simple_icon = "✅" if not categories_enabled else "❌"
    categorized_icon = "✅" if categories_enabled else "❌"
    rows = [
        [
            InlineKeyboardButton(f"{simple_icon} | ساده", callback_data="userbot:settings:tx_plans:plan_categories_mode:set:simple"),
            InlineKeyboardButton(f"{categorized_icon} | دسته‌بندی", callback_data="userbot:settings:tx_plans:plan_categories_mode:set:categorized"),
        ],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:tx_plans")],
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
        [InlineKeyboardButton(f"{price_icon} | مرتب سازی بر اساس قیمت", callback_data="userbot:settings:tx_plans:plan_sort_mode:set:price")],
        [InlineKeyboardButton(f"{gb_icon} | مرتب سازی بر اساس حجم", callback_data="userbot:settings:tx_plans:plan_sort_mode:set:gb")],
        [InlineKeyboardButton(f"{days_icon} | مرتب سازی بر اساس زمان", callback_data="userbot:settings:tx_plans:plan_sort_mode:set:days")],
        [
            InlineKeyboardButton(f"{desc_icon} | نزولی", callback_data="userbot:settings:tx_plans:plan_sort_dir:set:desc"),
            InlineKeyboardButton(f"{asc_icon} | صعودی", callback_data="userbot:settings:tx_plans:plan_sort_dir:set:asc"),
        ],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:tx_plans")],
    ]
    return InlineKeyboardMarkup(rows)


def build_plan_columns_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    col = int(settings.get("plan_columns") or 1)
    one_icon = "✅" if col == 1 else "❌"
    two_icon = "✅" if col == 2 else "❌"
    rows = [
        [
            InlineKeyboardButton(f"{one_icon} | یک", callback_data="userbot:settings:buy_renew:plan_columns:set:1"),
            InlineKeyboardButton(f"{two_icon} | دو", callback_data="userbot:settings:buy_renew:plan_columns:set:2"),
        ],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:buy_renew")],
    ]
    return InlineKeyboardMarkup(rows)


def build_server_columns_menu_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    col = int(settings.get("server_columns") or 1)
    one_icon = "✅" if col == 1 else "❌"
    two_icon = "✅" if col == 2 else "❌"
    three_icon = "✅" if col == 3 else "❌"
    rows = [
        [
            InlineKeyboardButton(f"{one_icon} | یک", callback_data="userbot:settings:buy_renew:server_columns:set:1"),
            InlineKeyboardButton(f"{two_icon} | دو", callback_data="userbot:settings:buy_renew:server_columns:set:2"),
            InlineKeyboardButton(f"{three_icon} | سه", callback_data="userbot:settings:buy_renew:server_columns:set:3"),
        ],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:buy_renew")],
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
            InlineKeyboardButton(f"پیشفرض | {default_icon}", callback_data="userbot:settings:buy_renew:renew_policy:default"),
            InlineKeyboardButton(f"پیشرفته | {advanced_icon}", callback_data="userbot:settings:buy_renew:renew_policy:advanced"),
            InlineKeyboardButton(f"منصفانه | {fair_icon}", callback_data="userbot:settings:buy_renew:renew_policy:fair"),
        ],
        [
            InlineKeyboardButton(
                f"حجم افزایشی | {volume_add_icon}",
                callback_data="userbot:settings:buy_renew:renew_rollover:volume:add",
            ),
            InlineKeyboardButton(
                f"حجم ریست | {volume_reset_icon}",
                callback_data="userbot:settings:buy_renew:renew_rollover:volume:reset",
            ),
        ],
        [
            InlineKeyboardButton(
                f"زمان افزایشی | {time_add_icon}",
                callback_data="userbot:settings:buy_renew:renew_rollover:time:add",
            ),
            InlineKeyboardButton(
                f"زمان ریست | {time_reset_icon}",
                callback_data="userbot:settings:buy_renew:renew_rollover:time:reset",
            ),
        ],
        [InlineKeyboardButton("حداکثر زمان مجاز برای تمدید📊", callback_data="userbot:settings:buy_renew:renew_limit:days")],
        [InlineKeyboardButton("حداکثر مصرف مجاز برای تمدید📆", callback_data="userbot:settings:buy_renew:renew_limit:usage")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:buy_renew")],
    ]
    return InlineKeyboardMarkup(rows)


def build_reset_free_trial_confirm_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("✅تایید", callback_data="userbot:settings:subscription:reset_free_trial_confirm")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="userbot:settings:subscription")],
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
            "welcome_message": "سلام {full_name} عزیز 👋\nبه ربات ما خوش آمدید.",
            "faq_text": (
                "❓ سوالات متداول\n\n"
                "1) لینک اشتراک را کجا بزنم؟\n"
                "از بخش «📊وضعیت اشتراک» وارد سرویس شوید و روی «لینک اشتراک» بزنید.\n\n"
                "2) اگر کانفیگ وصل نشد چه کنم؟\n"
                "اول اینترنت و تاریخ/ساعت گوشی را چک کنید، سپس «بروزرسانی اطلاعات» بزنید.\n\n"
                "3) چطور تمدید کنم؟\n"
                "از «♾تمدید اشتراک» سرویس را انتخاب کنید و پلن تمدید را بخرید.\n\n"
                "4) پشتیبانی از کجاست؟\n"
                "از دکمه «📩پشتیبانی» پیام خود را ارسال کنید."
            ),
            "guide_text": "انتخاب سیستم عامل ⬇️",
            "guide_android_text": (
                "📱 راهنمای اندروید\n\n"
                "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
                "2) v2rayNG:\nhttps://github.com/2dust/v2rayNG/releases\n\n"
                "3) NekoBox for Android:\nhttps://github.com/MatsuriDayo/NekoBoxForAndroid/releases\n\n"
                "بعد از نصب، لینک اشتراک را Import کنید و Connect بزنید."
            ),
            "guide_ios_text": (
                "📱 راهنمای iOS\n\n"
                "1) Streisand:\nhttps://apps.apple.com/app/streisand/id6450534064\n\n"
                "2) Hiddify (iOS):\nhttps://apps.apple.com/app/hiddify-proxy-vpn/id6596777532\n\n"
                "بعد از نصب، لینک اشتراک را Import کرده و اتصال را فعال کنید."
            ),
            "guide_windows_text": (
                "🖥️ راهنمای ویندوز\n\n"
                "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
                "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
                "3) v2rayN:\nhttps://github.com/2dust/v2rayN/releases\n\n"
                "پس از نصب، لینک اشتراک را Paste/Import کنید و Connect شوید."
            ),
            "guide_mac_text": (
                "💻 راهنمای مک\n\n"
                "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
                "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
                "پس از نصب، لینک اشتراک را Import کنید و اتصال را فعال کنید."
            ),
            "guide_linux_text": (
                "🖥️ راهنمای لینوکس\n\n"
                "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
                "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
                "پس از نصب، لینک اشتراک را در برنامه وارد کنید و Connect بزنید."
            ),
            "invite_text": "💌 لینک دعوت شما:\n{invite_link}",
            "invite_info_text": "🎁 دعوت دوستان خود از هدایای ویژه ای بهره مند شوید",
            "invite_banner_text": (
                "🎁 بنر دعوت اختصاصی شما\n\n"
                "🔗 لینک دعوت شما:\n{invite_link}\n\n"
                "دوستانت را دعوت کن و از مزایای ویژه بهره‌مند شو."
            ),
            "invite_banner_photo_id": "",
            "servers_list_text": "📡 **لیست سرورها**\nلطفاً لوکیشن مورد نظر خود را انتخاب کنید:",
            "plans_list_text": "🛒 **لطفاً پلن مورد نظر خود را انتخاب کنید:**",
            "ticket_panel_text": "📩 برای ارتباط با پشتیبانی، پیام خود را ارسال کنید.",
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
            "auto_gift_text": "🎁 هدیه شما فعال شد. از همراهی‌تان متشکریم.",
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
                "🔒 برای استفاده از ربات، ابتدا در کانال پشتیبانی عضو شوید.\n"
                "پس از عضویت روی «✅ بررسی عضویت» بزنید.\n\n"
                "اگر عضویت شما تایید نشد:\n"
                "1) مطمئن شوید دقیقاً در همان کانال اعلام‌شده عضو شده‌اید.\n"
                "2) ربات کاربران باید در کانال، ادمین باشد تا عضویت را تشخیص دهد."
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
    backup_dir.mkdir(parents=True, exist_ok=True)
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
        raise ValueError("مقدار plans.json معتبر نیست.")
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
        raise ValueError("مقدار servers.json معتبر نیست.")
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
        with sqlite3.connect(str(dst_db), timeout=30) as dst_conn:
            src_conn.backup(dst_conn)
            dst_conn.commit()


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
            "title": _legacy_clean_str(item.get("title"), "دامنه"),
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
            "title": _legacy_clean_str(row.get("title"), f"سرور {sid}"),
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
                "title": _legacy_clean_str(row.get("title"), f"دسته {cat_id}"),
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
                title = f"{_format_gb(gb)} گیگ / {days} روز"
            else:
                title = f"{days} روزه"

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
        raise ValueError("پایگاه داده اصلی ربات کاربران یافت نشد.")

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
                full_name = f"کاربر {tg}"
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
                server_title_by_id[sid] = _legacy_clean_str(row.get("title"), f"سرور {sid}")

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
                    "server_title": server_title_by_id.get(server_id, f"سرور {server_id}"),
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
                    "server_title": server_title_by_id.get(server_id, f"سرور {server_id}"),
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
                    "last_online": "نامشخص",
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
                    or (str(tg) if tg > 0 else ("پشتیبانی" if sender_type == "admin" else "کاربر")),
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
            for item in {"Shared/hiddify_sellbot.db", "Shared/servers.json", "Shared/plans.json"}
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
        raise ValueError("در فایل zip هیچ داده قابل‌بازیابی پیدا نشد.")

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
        raise ValueError("فایل JSON باید یک آبجکت معتبر باشد.")

    if "backup_type" in data and "files" in data:
        raise ValueError("این فایل فقط Manifest بکاپ است. لطفاً فایل zip کامل بکاپ را ارسال کنید.")

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
            raise ValueError("مقدار servers_json معتبر نیست.")
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
            raise ValueError("مقدار plans_json معتبر نیست.")
        payload = _normalize_plans_payload(payload)
        _atomic_write_bytes(shared_dir / "plans.json", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        restored_files.append("Shared/plans.json")
    elif "servers" in data and isinstance(data.get("servers"), dict) and "settings" not in data:
        payload = _normalize_plans_payload(data)
        _atomic_write_bytes(shared_dir / "plans.json", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        restored_files.append("Shared/plans.json")

    if not restored_files:
        raise ValueError(
            "JSON ارسال‌شده قابل بازیابی نیست.\n"
            "برای بازیابی کامل، فایل zip بکاپ را ارسال کنید."
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
        raise ValueError("فرمت فایل نامعتبر است. فقط zip یا json پشتیبانی می‌شود.")

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
        "✅ بازیابی بکاپ با موفقیت انجام شد.",
        f"🧩 نوع فایل: {mode}",
        f"📦 فایل‌های بازیابی‌شده: {len(restored_files)}",
    ]
    if receipts_count > 0:
        lines.append(f"🖼 تعداد رسیدهای بازیابی‌شده: {receipts_count}")
    if restored_files:
        lines.append("🗂 موارد:")
        for item in restored_files:
            lines.append(f"• {item}")
    if isinstance(legacy_stats, dict) and legacy_stats:
        lines.append("🧬 تبدیل ساختار بکاپ قدیمی انجام شد:")
        labels = [
            ("users", "کاربران"),
            ("orders", "سفارشات"),
            ("payments", "تراکنش‌ها"),
            ("services", "اشتراک‌ها"),
            ("tickets", "تیکت‌ها"),
            ("ticket_messages", "پیام‌های تیکت"),
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
    ]

    added: List[Dict[str, Any]] = []
    with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in files_to_add:
            if not src.exists() or not src.is_file():
                continue
            zf.write(src, arcname=arcname)
            try:
                fsize = int(src.stat().st_size)
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

    return out_path


async def _collect_panel_backups() -> Tuple[List[Dict[str, Any]], List[str]]:
    servers = database.get_servers() or []
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


async def _make_full_backup_zip() -> Tuple[Path, int, int, List[str]]:
    bot_backup_path = await asyncio.to_thread(_make_bot_backup_zip)
    try:
        panel_backups, panel_errors = await _collect_panel_backups()
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


async def run_userbot_auto_backup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _get_backup_restore_settings()
    if not bool(settings.get("auto_backup_enabled", True)):
        return

    now_local = datetime.now()
    if now_local.minute != 0 or now_local.hour not in {0, 6, 12, 18}:
        return

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
        logger.warning("Auto full backup creation failed: %s", e)
        context.bot_data["_userbot_auto_backup_slot"] = slot_key
        return

    caption = (
        "⏰ بکاپ خودکار کامل\n"
        f"🕐 زمان: {now_local.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "🤖 بکاپ ربات: ✅\n"
        f"🖥️ بکاپ سرورها/نودها: {panel_ok_count} مورد\n"
        f"⚠️ خطاها: {panel_err_count} مورد"
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

    if panel_errors:
        preview = "\n".join(panel_errors[:5])
        extra = f"\n... و {len(panel_errors) - 5} خطای دیگر" if len(panel_errors) > 5 else ""
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text="⚠️ برخی بکاپ‌های پنل دریافت نشدند:\n" + preview + extra,
            )
        except Exception as e:
            logger.warning("Auto backup error report to admin failed: %s", e)

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
            if panel_errors:
                preview = "\n".join(panel_errors[:5])
                extra = f"\n... و {len(panel_errors) - 5} خطای دیگر" if len(panel_errors) > 5 else ""
                try:
                    await context.bot.send_message(
                        chat_id=target,
                        text="⚠️ برخی بکاپ‌های پنل دریافت نشدند:\n" + preview + extra,
                    )
                except Exception as e:
                    logger.warning("Auto backup error report to event channel failed: %s", e)

    context.bot_data["_userbot_auto_backup_slot"] = slot_key

# ---------------------------------------------------------
# PART 2: SEND FUNCTIONS & DISPLAYS
# ---------------------------------------------------------

async def send_userbot_main_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = (
        "🤖 مدیریت ربات کاربران\n"
        "از این بخش می‌توانید کاربران ربات، سفارشات، تراکنش‌ها و سایر بخش‌ها را مدیریت کنید."
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


# ===============================
#   بخش مدیریت تراکنشات (تکمیل شده)
# ===============================

async def send_payments_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = "💵 مدیریت تراکنشات\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:"
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
        header_title = "لیست تراکنشات تایید شده ✅"
    elif filter_type == 'rejected':
        status = 'rejected'
        header_title = "لیست تراکنشات رد شده 🚫"
    elif filter_type == 'pending':
        status = 'pending'
        header_title = "لیست تراکنشات در انتظار ⏳"
    elif filter_type == 'card':
        method = 'card'
        header_title = "لیست تراکنشات کارت به کارت 💳"

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
        f"🔹 {header_title}\n"
        f"🔸 تعداد تراکنشات: {stats['total_count']}\n"
        f"🔸 مبلغ تراکنشات: {_format_toman(stats['total_amount'])} تومان\n"
        f"❖ ⬩----------------------------------⬩ ❖\n"
        f"🔸 تراکنشات 30 روز گذشته: {stats['last30_count']}\n"
        f"🔸 مبلغ تراکنشات 30 روز گذشته: {_format_toman(stats['last30_amount'])} تومان\n"
        f"❖ ⬩----------------------------------⬩ ❖\n"
        f"🔸 تراکنشات این ماه: {stats['month_count']}\n"
        f"🔸 مبلغ تراکنشات این ماه: {_format_toman(stats['month_amount'])} تومان"
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

    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="userbot:payments_menu")])

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
        await context.bot.send_message(chat_id, "❌ تراکنش یافت نشد.")
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
    kb = _build_payment_action_keyboard(payment_id, user_btn_title, uid)

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
            await context.bot.send_message(chat_id, caption + "\n(تصویر فیش در دسترس نیست)", reply_markup=kb)
    elif receipt_local_path and os.path.exists(receipt_local_path) and not force_text_only:
        try:
            with open(receipt_local_path, "rb") as f:
                await context.bot.send_photo(chat_id, f, caption=caption, reply_markup=kb)
            return
        except Exception:
            await context.bot.send_message(chat_id, caption + "\n(تصویر فیش محلی خوانده نشد)", reply_markup=kb)
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
    text = "👤 مدیریت کاربران ربات\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:"
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
        "👥 لیست کاربران ربات",
        f"تعداد کل: {total_count}",
        f"صفحه: {page}/{total_pages}",
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
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="userbot:users_menu")])

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
        await context.bot.send_message(chat_id, "❌ کاربر یافت نشد.")
        return

    stats = userbot_db.get_full_user_stats(user_id)
    wallet = _format_toman(user.get("wallet_balance", 0))
    
    # چک کردن وضعیت بن و تست
    is_banned = user.get('is_banned', 0)
    got_trial = user.get('got_free_trial', 0)
    
    trial_icon = "✅" if got_trial else "❌ (نگرفته)"
    ban_status = "🔴 مسدود" if is_banned else "🟢 فعال"

    # متن پیام طبق عکس
    text = (
        f"👤 کاربر: {_display_name(user)}\n"
        f"🔹 نام کاربری: @{user.get('username','-')}\n"
        f"🔸 شناسه کاربر: {user['telegram_id']}\n"
        f"🔸 وضعیت دریافت تست رایگان: {trial_icon}\n"
        f"🔸 موجودی کیف پول: {wallet}تومان\n"
        f"🔸 وضعیت اکانت: {ban_status}\n"
        "❖ ⬩----------------------------------⬩ ❖\n"
        f"🔸 تعداد اشتراک‌های خریداری شده: {stats['subs_bought']}\n"
        f"🔸 تعداد اشتراک‌های متصل شده: {stats['subs_connected']}\n"
        f"🔸 تعداد تراکنشات: {stats['tx_total']}\n"
        f"🔸 تعداد تراکنشات تایید شده: {stats['tx_approved']}\n"
        "❖ ⬩----------------------------------⬩ ❖\n"
        f"🔸 تعداد سفارشات: {stats['orders_count']}\n"
        f"🔸 مجموع حجم سفارشات(GB): {stats['orders_gb']}\n"
        f"🔸 مجموع ارزش سفارشات: {_format_toman(stats['orders_price'])}تومان"
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

    if context.user_data.get(SUB_TRACKING_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(SUB_TRACKING_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            return

        sub_code = str(text or "").strip()
        if not sub_code:
            await msg.reply_text(_subscription_tracking_prompt_text(), reply_markup=userbot_cancel_keyboard())
            return

        service = userbot_db.get_service_by_code(sub_code)
        if not service:
            await msg.reply_text("❌اشتراکی با این شناسه یافت نشد")
            await msg.reply_text(_subscription_tracking_prompt_text(), reply_markup=userbot_cancel_keyboard())
            return

        context.user_data.pop(SUB_TRACKING_STATE, None)
        await msg.reply_text("✅اشتراک یافت شد", reply_markup=admin_main_keyboard())
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
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            return

        doc = getattr(msg, "document", None)
        if not doc:
            await msg.reply_text(
                "📦 لطفاً فایل بکاپ را ارسال کنید (zip/json) یا «❌لغو» بزنید.",
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        file_name = str(getattr(doc, "file_name", "") or "").strip()
        low_name = file_name.lower()
        if not (low_name.endswith(".zip") or low_name.endswith(".json")):
            await msg.reply_text(
                "❌ فرمت فایل نامعتبر است. فقط zip یا json ارسال کنید.",
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
            await msg.reply_text(f"❌ دریافت فایل بکاپ ناموفق بود:\n{e}", reply_markup=userbot_cancel_keyboard())
            return

        context.user_data.pop(BACKUP_RESTORE_STATE, None)
        if BACKUP_RESTORE_LOCK.locked():
            try:
                save_path.unlink(missing_ok=True)
            except Exception:
                pass
            await msg.reply_text("⏳ یک عملیات بازیابی دیگر در حال انجام است. چند لحظه بعد دوباره تلاش کنید.", reply_markup=admin_main_keyboard())
            await send_backup_restore_settings_menu(msg.chat_id, context)
            return

        await msg.reply_text("⏳ فایل دریافت شد. در حال بازیابی بکاپ...", reply_markup=admin_main_keyboard())
        try:
            async with BACKUP_RESTORE_LOCK:
                result = await asyncio.to_thread(_restore_backup_file, save_path)
        except Exception as e:
            logger.exception("Backup restore failed: %s", e)
            await msg.reply_text(f"❌ بازیابی بکاپ ناموفق بود:\n{e}", reply_markup=admin_main_keyboard())
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
                            "♻️ بازیابی بکاپ انجام شد.\n"
                            f"👤 توسط ادمین: {msg.from_user.id if msg.from_user else '-'}\n"
                            f"🕐 زمان: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        ),
                    )
                except Exception as e:
                    logger.warning("Send restore event to channel failed: %s", e)

        await send_backup_restore_settings_menu(msg.chat_id, context)
        return

    if context.user_data.get(BACKUP_CHANNEL_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(BACKUP_CHANNEL_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
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
                    "❌ ورودی معتبر نیست.\nیک پیام از کانال فوروارد کنید یا @channel / -100... بفرستید.",
                    reply_markup=userbot_cancel_keyboard(),
                )
                return

        try:
            settings = _get_backup_restore_settings()
            settings["event_channel_id"] = channel_target
            userbot_db.set_backup_restore_settings(settings)
        except Exception as e:
            context.user_data.pop(BACKUP_CHANNEL_EDIT_STATE, None)
            await msg.reply_text(f"❌ خطا در ذخیره کانال رویداد بکاپ:\n{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(BACKUP_CHANNEL_EDIT_STATE, None)
        await msg.reply_text(f"✅ کانال رویداد بکاپ ذخیره شد:\n{channel_target}", reply_markup=admin_main_keyboard())
        await send_backup_restore_settings_menu(msg.chat_id, context)
        return

    if context.user_data.get(BROADCAST_SEND_STATE):
        st = context.user_data.get(BROADCAST_SEND_STATE)
        if not isinstance(st, dict):
            context.user_data.pop(BROADCAST_SEND_STATE, None)
            await msg.reply_text("❌ وضعیت ارسال همگانی نامعتبر است.", reply_markup=admin_main_keyboard())
            return

        segment = str(st.get("segment") or "all").strip().lower()
        step = str(st.get("step") or "wait_text").strip().lower()

        if text in CANCEL_WORDS:
            context.user_data.pop(BROADCAST_SEND_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            return

        if step == "wait_text":
            body_text = str(text or "").strip()
            if not body_text:
                await msg.reply_text(
                    "❌ لطفاً متن پیام را کامل ارسال کنید.",
                    reply_markup=userbot_cancel_keyboard(),
                )
                return
            st["text"] = body_text
            st["step"] = "wait_photo"
            context.user_data[BROADCAST_SEND_STATE] = st
            await msg.reply_text(
                "🖼️ لطفا عکس خود را برای ارسال به کاربران ارسال کنید یا روی دکمه [⏩رد کردن] کلیک کنید:",
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
                    "❌ لطفا عکس ارسال کنید یا روی دکمه [⏩رد کردن] بزنید.",
                    reply_markup=broadcast_skip_cancel_keyboard(),
                )
                return

            body_text = str(st.get("text") or "").strip()
            if not body_text:
                st["step"] = "wait_text"
                context.user_data[BROADCAST_SEND_STATE] = st
                await msg.reply_text("❌ متن پیام خالی است. لطفاً دوباره متن را ارسال کنید.", reply_markup=userbot_cancel_keyboard())
                return

            target_ids = userbot_db.get_broadcast_target_telegram_ids(segment)
            try:
                await _send_broadcast_to_targets(context, target_ids, body_text, photo_file_id)
            except Exception as e:
                await msg.reply_text(f"❌ خطا در ارسال پیام همگانی:\n{e}", reply_markup=admin_main_keyboard())
                context.user_data.pop(BROADCAST_SEND_STATE, None)
                return

            context.user_data.pop(BROADCAST_SEND_STATE, None)
            await msg.reply_text("✅پیام به کاربران ارسال شد", reply_markup=admin_main_keyboard())
            return

        # fallback
        st["step"] = "wait_text"
        context.user_data[BROADCAST_SEND_STATE] = st
        await msg.reply_text(
            f"✍ لطفا پیام خود را برای «{_broadcast_segment_label(segment)}» وارد کنید:",
            reply_markup=userbot_cancel_keyboard(),
        )
        return

    if context.user_data.get(TICKET_REPLY_STATE):
        st = context.user_data.get(TICKET_REPLY_STATE)
        if not isinstance(st, dict):
            context.user_data.pop(TICKET_REPLY_STATE, None)
            await msg.reply_text("❌ وضعیت پاسخ تیکت نامعتبر است.", reply_markup=admin_main_keyboard())
            return

        ticket_code = int(st.get("ticket_code") or 0)
        list_status = str(st.get("list_status") or "pending").strip().lower()
        page = max(1, int(st.get("page") or 1))
        from_user_id = int(st.get("from_user_id") or 0)
        step = str(st.get("step") or "wait_text").strip().lower()

        if text in CANCEL_WORDS:
            context.user_data.pop(TICKET_REPLY_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
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
            await msg.reply_text("❌ تیکت یافت نشد.", reply_markup=admin_main_keyboard())
            return

        if step == "wait_text":
            body_text = str(text or "").strip()
            if not body_text:
                await msg.reply_text("❌ لطفاً متن پاسخ را کامل ارسال کنید.", reply_markup=userbot_cancel_keyboard())
                return
            st["reply_text"] = body_text
            st["photo_file_id"] = ""
            st["step"] = "wait_screenshot"
            context.user_data[TICKET_REPLY_STATE] = st
            await msg.reply_text(
                "📱 لطفاً اسکرین‌شات خود را ارسال کنید یا روی دکمه [⏩رد کردن] کلیک کنید",
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
                    "❌ لطفا عکس ارسال کنید یا روی دکمه [⏩رد کردن] بزنید.",
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
                "برای ارسال پاسخ از دکمه‌های «✅ارسال» یا «✏️ویرایش» استفاده کنید.",
                reply_markup=build_ticket_reply_confirm_keyboard(),
            )
            return
        # fallback
        st["step"] = "wait_text"
        context.user_data[TICKET_REPLY_STATE] = st
        await msg.reply_text("📩 متن پاسخ تیکت را ارسال کنید:", reply_markup=userbot_cancel_keyboard())
        return

    if context.user_data.get(PAYMENT_CARD_ADD_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(PAYMENT_CARD_ADD_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
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
                await msg.reply_text("❌ شماره کارت باید 16 رقم باشد.\nلطفا شماره کارت را وارد کنید:", reply_markup=userbot_cancel_keyboard())
                return
            add_state["step"] = "owner"
            add_state["number"] = number
            context.user_data[PAYMENT_CARD_ADD_STATE] = add_state
            await msg.reply_text("➡️ لطفا نام صاحب کارت را وارد کنید:", reply_markup=userbot_cancel_keyboard())
            return

        if step == "owner":
            owner = text.strip()
            number = re.sub(r"\D", "", str(add_state.get("number") or ""))
            if not owner:
                await msg.reply_text("❌ نام صاحب کارت معتبر نیست.\nلطفا نام صاحب کارت را وارد کنید:", reply_markup=userbot_cancel_keyboard())
                return
            if len(number) != 16:
                context.user_data[PAYMENT_CARD_ADD_STATE] = {"step": "number"}
                await msg.reply_text("❌ شماره کارت نامعتبر شد. دوباره شماره کارت را وارد کنید:", reply_markup=userbot_cancel_keyboard())
                return
            add_state["step"] = "bank"
            add_state["owner"] = owner
            context.user_data[PAYMENT_CARD_ADD_STATE] = add_state
            await msg.reply_text(
                "🏦 لطفا نام بانک را وارد کنید:\n"
                "برای رد شدن این مرحله عدد 0 را ارسال کنید.",
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
                await msg.reply_text("❌ شماره کارت نامعتبر شد. دوباره شماره کارت را وارد کنید:", reply_markup=userbot_cancel_keyboard())
                return
            if not owner:
                context.user_data[PAYMENT_CARD_ADD_STATE] = {"step": "owner", "number": number}
                await msg.reply_text("❌ نام صاحب کارت نامعتبر شد. دوباره نام صاحب کارت را وارد کنید:", reply_markup=userbot_cancel_keyboard())
                return
            try:
                database.add_or_update_card(owner=owner, number=number, bank_name=bank)
            except Exception as e:
                await msg.reply_text(f"❌ خطا در ذخیره کارت:\n{e}", reply_markup=userbot_cancel_keyboard())
                return
            context.user_data.pop(PAYMENT_CARD_ADD_STATE, None)
            await msg.reply_text("✅ کارت با موفقیت افزوده شد.", reply_markup=admin_main_keyboard())
            await send_payment_card_item_menu(msg.chat_id, context, number=number)
            return

        context.user_data[PAYMENT_CARD_ADD_STATE] = {"step": "number"}
        await msg.reply_text("⬇️ لطفا شماره کارت را وارد کنید:", reply_markup=userbot_cancel_keyboard())
        return

    if context.user_data.get(PAYMENT_CARD_EDIT_STATE):
        edit_state = context.user_data.get(PAYMENT_CARD_EDIT_STATE)
        if not isinstance(edit_state, dict):
            context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
            await msg.reply_text("❌ خطا در وضعیت ویرایش کارت.", reply_markup=admin_main_keyboard())
            await send_payment_cards_list_menu(msg.chat_id, context)
            return

        target_number = re.sub(r"\D", "", str(edit_state.get("number") or ""))
        mode = str(edit_state.get("mode") or "").strip().lower()

        if text in CANCEL_WORDS:
            context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            if target_number:
                await send_payment_card_item_menu(msg.chat_id, context, number=target_number)
            else:
                await send_payment_cards_list_menu(msg.chat_id, context)
            return

        if mode == "number":
            new_number = re.sub(r"\D", "", text)
            if len(new_number) != 16:
                await msg.reply_text("❌ شماره کارت باید 16 رقم باشد.\nشماره کارت جدید را وارد کنید:", reply_markup=userbot_cancel_keyboard())
                return
            if new_number != target_number and database.get_card(new_number):
                await msg.reply_text("❌ این شماره کارت قبلاً ثبت شده است.", reply_markup=userbot_cancel_keyboard())
                return
            ok = database.update_card_number(target_number, new_number)
            if not ok:
                context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
                await msg.reply_text("❌ کارت موردنظر برای ویرایش پیدا نشد.", reply_markup=admin_main_keyboard())
                await send_payment_cards_list_menu(msg.chat_id, context)
                return
            context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
            await msg.reply_text("✅ شماره کارت با موفقیت ویرایش شد.", reply_markup=admin_main_keyboard())
            await send_payment_card_item_menu(msg.chat_id, context, number=new_number)
            return

        if mode == "owner":
            new_owner = text.strip()
            if not new_owner:
                await msg.reply_text("❌ نام صاحب کارت معتبر نیست.\nنام جدید را وارد کنید:", reply_markup=userbot_cancel_keyboard())
                return
            ok = database.update_card_owner(target_number, new_owner)
            if not ok:
                context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
                await msg.reply_text("❌ کارت موردنظر برای ویرایش پیدا نشد.", reply_markup=admin_main_keyboard())
                await send_payment_cards_list_menu(msg.chat_id, context)
                return
            context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
            await msg.reply_text("✅ نام صاحب کارت با موفقیت ویرایش شد.", reply_markup=admin_main_keyboard())
            await send_payment_card_item_menu(msg.chat_id, context, number=target_number)
            return

        context.user_data.pop(PAYMENT_CARD_EDIT_STATE, None)
        await msg.reply_text("❌ نوع ویرایش نامعتبر است.", reply_markup=admin_main_keyboard())
        await send_payment_cards_list_menu(msg.chat_id, context)
        return

    if context.user_data.get(PAYMENT_CARD_DELETE_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(PAYMENT_CARD_DELETE_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            await send_payment_cards_list_menu(msg.chat_id, context)
            return
        number = re.sub(r"\D", "", text)
        if len(number) < 16:
            await msg.reply_text("❌ شماره کارت معتبر نیست.", reply_markup=userbot_cancel_keyboard())
            return
        ok = False
        try:
            ok = database.delete_card(number)
        except Exception as e:
            await msg.reply_text(f"❌ خطا در حذف کارت:\n{e}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(PAYMENT_CARD_DELETE_STATE, None)
        await msg.reply_text("✅ کارت حذف شد." if ok else "❌ کارت پیدا نشد.", reply_markup=admin_main_keyboard())
        await send_payment_cards_list_menu(msg.chat_id, context)
        return

    if context.user_data.get(ZARIN_COUPON_ADD_STATE):
        add_state = context.user_data.get(ZARIN_COUPON_ADD_STATE)
        if text in CANCEL_WORDS:
            context.user_data.pop(ZARIN_COUPON_ADD_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
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
                    "❌ کد نامعتبر است.\nفقط حروف/عدد/`_`/`-` و حداقل 4 کاراکتر.",
                    reply_markup=userbot_cancel_keyboard(),
                    parse_mode="Markdown",
                )
                return
            if userbot_db.get_zarin_voucher(code):
                await msg.reply_text("❌ این کد قبلا ثبت شده است.", reply_markup=userbot_cancel_keyboard())
                return
            add_state["step"] = "amount"
            add_state["code"] = code
            context.user_data[ZARIN_COUPON_ADD_STATE] = add_state
            await msg.reply_text("💰 مبلغ هدیه کیف پول (تومان) را وارد کنید:", reply_markup=userbot_cancel_keyboard())
            return
        if step == "amount":
            code = str(add_state.get("code") or "").strip()
            try:
                amount = int(str(text).replace(",", ""))
                if amount <= 0:
                    raise ValueError
            except Exception:
                await msg.reply_text("❌ مبلغ نامعتبر است. عدد مثبت وارد کنید.", reply_markup=userbot_cancel_keyboard())
                return
            try:
                userbot_db.upsert_zarin_voucher(code, amount, max_uses=1, is_active=1)
            except Exception as e:
                await msg.reply_text(f"❌ خطا در ذخیره کوپن:\n{e}", reply_markup=userbot_cancel_keyboard())
                return
            context.user_data.pop(ZARIN_COUPON_ADD_STATE, None)
            await msg.reply_text("✅ کوپن با موفقیت افزوده شد.", reply_markup=admin_main_keyboard())
            await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            return

    if context.user_data.get(ZARIN_COUPON_DELETE_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(ZARIN_COUPON_DELETE_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        code = str(text or "").strip()
        ok = userbot_db.delete_zarin_voucher(code)
        context.user_data.pop(ZARIN_COUPON_DELETE_STATE, None)
        await msg.reply_text("✅ کوپن حذف شد." if ok else "❌ کوپن پیدا نشد.", reply_markup=admin_main_keyboard())
        await send_zarin_coupons_menu(msg.chat_id, context)
        return

    if context.user_data.get(ZARIN_COUPON_LINK_STATE):
        st = context.user_data.get(ZARIN_COUPON_LINK_STATE)
        if text in CANCEL_WORDS:
            code = str((st or {}).get("code") or "").strip() if isinstance(st, dict) else ""
            context.user_data.pop(ZARIN_COUPON_LINK_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            if code:
                await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            else:
                await send_zarin_coupons_menu(msg.chat_id, context)
            return
        if not isinstance(st, dict):
            context.user_data.pop(ZARIN_COUPON_LINK_STATE, None)
            await msg.reply_text("❌ خطا در وضعیت ویرایش لینک.", reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        code = str(st.get("code") or "").strip()
        link = str(text or "").strip()
        if not (link.startswith("http://") or link.startswith("https://")):
            await msg.reply_text("❌ لینک معتبر نیست. باید با http یا https شروع شود.", reply_markup=userbot_cancel_keyboard())
            return
        try:
            userbot_db.set_zarin_voucher_link(code, link)
        except Exception as e:
            await msg.reply_text(f"❌ خطا در ذخیره لینک:\n{e}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(ZARIN_COUPON_LINK_STATE, None)
        await msg.reply_text("✅ لینک پرداخت زرین پال ثبت شد.", reply_markup=admin_main_keyboard())
        await send_zarin_coupon_detail(msg.chat_id, context, code=code)
        return

    if context.user_data.get(ZARIN_COUPON_AMOUNT_STATE):
        st = context.user_data.get(ZARIN_COUPON_AMOUNT_STATE)
        if text in CANCEL_WORDS:
            code = str((st or {}).get("code") or "").strip() if isinstance(st, dict) else ""
            context.user_data.pop(ZARIN_COUPON_AMOUNT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            if code:
                await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            else:
                await send_zarin_coupons_menu(msg.chat_id, context)
            return
        if not isinstance(st, dict):
            context.user_data.pop(ZARIN_COUPON_AMOUNT_STATE, None)
            await msg.reply_text("❌ خطا در وضعیت ویرایش مبلغ.", reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        code = str(st.get("code") or "").strip()
        try:
            amount = int(str(text).replace(",", ""))
            if amount <= 0:
                raise ValueError
        except Exception:
            await msg.reply_text("❌ مبلغ نامعتبر است. عدد مثبت وارد کنید.", reply_markup=userbot_cancel_keyboard())
            return
        try:
            userbot_db.set_zarin_voucher_amount(code, amount)
        except Exception as e:
            await msg.reply_text(f"❌ خطا در ذخیره مبلغ:\n{e}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(ZARIN_COUPON_AMOUNT_STATE, None)
        await msg.reply_text("✅ مبلغ هدیه کوپن به‌روزرسانی شد.", reply_markup=admin_main_keyboard())
        await send_zarin_coupon_detail(msg.chat_id, context, code=code)
        return

    if context.user_data.get(ZARIN_COUPON_CODE_STATE):
        st = context.user_data.get(ZARIN_COUPON_CODE_STATE)
        if text in CANCEL_WORDS:
            code = str((st or {}).get("code") or "").strip() if isinstance(st, dict) else ""
            context.user_data.pop(ZARIN_COUPON_CODE_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            if code:
                await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            else:
                await send_zarin_coupons_menu(msg.chat_id, context)
            return
        if not isinstance(st, dict):
            context.user_data.pop(ZARIN_COUPON_CODE_STATE, None)
            await msg.reply_text("❌ خطا در وضعیت ویرایش کد.", reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        old_code = str(st.get("code") or "").strip()
        new_code = str(text or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", new_code):
            await msg.reply_text(
                "❌ کد نامعتبر است.\nفقط حروف/عدد/`_`/`-` و حداقل 4 کاراکتر.",
                reply_markup=userbot_cancel_keyboard(),
                parse_mode="Markdown",
            )
            return
        ok, result = userbot_db.rename_zarin_voucher(old_code, new_code)
        if not ok:
            await msg.reply_text(f"❌ {result}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(ZARIN_COUPON_CODE_STATE, None)
        await msg.reply_text("✅ کد کوپن به‌روزرسانی شد.", reply_markup=admin_main_keyboard())
        await send_zarin_coupon_detail(msg.chat_id, context, code=new_code)
        return

    if context.user_data.get(ZARIN_COUPON_LIMIT_STATE):
        st = context.user_data.get(ZARIN_COUPON_LIMIT_STATE)
        if text in CANCEL_WORDS:
            code = str((st or {}).get("code") or "").strip() if isinstance(st, dict) else ""
            context.user_data.pop(ZARIN_COUPON_LIMIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            if code:
                await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            else:
                await send_zarin_coupons_menu(msg.chat_id, context)
            return
        if not isinstance(st, dict):
            context.user_data.pop(ZARIN_COUPON_LIMIT_STATE, None)
            await msg.reply_text("❌ خطا در وضعیت ویرایش محدودیت.", reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        code = str(st.get("code") or "").strip()
        try:
            limit = int(str(text).replace(",", ""))
            if limit <= 0:
                raise ValueError
        except Exception:
            await msg.reply_text("❌ مقدار محدودیت نامعتبر است. عدد مثبت وارد کنید.", reply_markup=userbot_cancel_keyboard())
            return
        try:
            userbot_db.set_zarin_voucher_max_uses(code, limit)
        except Exception as e:
            await msg.reply_text(f"❌ خطا در ذخیره محدودیت:\n{e}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(ZARIN_COUPON_LIMIT_STATE, None)
        await msg.reply_text("✅ محدودیت استفاده به‌روزرسانی شد.", reply_markup=admin_main_keyboard())
        await send_zarin_coupon_detail(msg.chat_id, context, code=code)
        return

    if context.user_data.get(ZARIN_COUPON_EXP_STATE):
        st = context.user_data.get(ZARIN_COUPON_EXP_STATE)
        if text in CANCEL_WORDS:
            code = str((st or {}).get("code") or "").strip() if isinstance(st, dict) else ""
            context.user_data.pop(ZARIN_COUPON_EXP_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            if code:
                await send_zarin_coupon_detail(msg.chat_id, context, code=code)
            else:
                await send_zarin_coupons_menu(msg.chat_id, context)
            return
        if not isinstance(st, dict):
            context.user_data.pop(ZARIN_COUPON_EXP_STATE, None)
            await msg.reply_text("❌ خطا در وضعیت ویرایش انقضا.", reply_markup=admin_main_keyboard())
            await send_zarin_coupons_menu(msg.chat_id, context)
            return
        code = str(st.get("code") or "").strip()
        try:
            hours = int(str(text).replace(",", ""))
            if hours < 0:
                raise ValueError
        except Exception:
            await msg.reply_text("❌ مقدار نامعتبر است. عدد ساعت (۰ یا بیشتر) وارد کنید.", reply_markup=userbot_cancel_keyboard())
            return
        try:
            userbot_db.set_zarin_voucher_expire_hours(code, hours)
        except Exception as e:
            await msg.reply_text(f"❌ خطا در ذخیره انقضا:\n{e}", reply_markup=userbot_cancel_keyboard())
            return
        context.user_data.pop(ZARIN_COUPON_EXP_STATE, None)
        await msg.reply_text("✅ مدت زمان انقضا به‌روزرسانی شد.", reply_markup=admin_main_keyboard())
        await send_zarin_coupon_detail(msg.chat_id, context, code=code)
        return

    if context.user_data.get(PAYMENT_CHANNEL_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(PAYMENT_CHANNEL_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
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
                    "❌ ورودی معتبر نیست.\nیک پیام از کانال فوروارد کنید یا @channel / -100... بفرستید.",
                    reply_markup=userbot_cancel_keyboard(),
                )
                return

        try:
            s = _get_payment_settings()
            s["event_channel_id"] = channel_target
            userbot_db.set_payment_settings(s)
        except Exception as e:
            context.user_data.pop(PAYMENT_CHANNEL_EDIT_STATE, None)
            await msg.reply_text(f"❌ خطا در ذخیره کانال رویداد پرداخت:\n{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(PAYMENT_CHANNEL_EDIT_STATE, None)
        await msg.reply_text(f"✅ کانال رویداد پرداخت ذخیره شد:\n{channel_target}", reply_markup=admin_main_keyboard())
        await send_payment_settings_menu(msg.chat_id, context)
        return

    if context.user_data.get(FORCE_JOIN_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(FORCE_JOIN_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
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
                    "❌ ورودی معتبر نیست.\nیک پیام از کانال فوروارد کنید یا @channel / -100... بفرستید.",
                    reply_markup=userbot_cancel_keyboard(),
                )
                return

        try:
            userbot_db.set_force_join_channel(channel_target, channel_link)
        except Exception as e:
            context.user_data.pop(FORCE_JOIN_EDIT_STATE, None)
            await msg.reply_text(f"❌ خطا در ذخیره کانال:\n{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(FORCE_JOIN_EDIT_STATE, None)
        await msg.reply_text(f"✅ کانال عضویت اجباری ذخیره شد:\n{channel_target}", reply_markup=admin_main_keyboard())
        await send_force_join_settings_menu(msg.chat_id, context)
        return

    if context.user_data.get(MARKETING_EDIT_STATE):
        edit_type = str(context.user_data.get(MARKETING_EDIT_STATE) or "").strip()
        if text in CANCEL_WORDS:
            context.user_data.pop(MARKETING_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            await send_marketing_settings_menu(msg.chat_id, context)
            return

        try:
            if edit_type == "auto_gift_text":
                userbot_db.set_marketing_value("auto_gift_text", text)
                done_text = "✅ متن هدایای اتوماتیک ذخیره شد."
            elif edit_type == "min_auto_gift_charge":
                value = int(text.replace(",", ""))
                if value < 0:
                    raise ValueError
                userbot_db.set_marketing_value("min_auto_gift_charge", value)
                done_text = f"✅ حداقل شارژ هدیه اتوماتیک روی {value:,} تومان تنظیم شد."
            else:
                raise ValueError("invalid marketing edit state")
        except Exception:
            if edit_type == "min_auto_gift_charge":
                await msg.reply_text("❌ لطفاً عدد صحیح معتبر (۰ یا بیشتر) وارد کنید.", reply_markup=userbot_cancel_keyboard())
            else:
                await msg.reply_text("❌ خطا در ذخیره تنظیمات بازاریابی.", reply_markup=userbot_cancel_keyboard())
            return

        context.user_data.pop(MARKETING_EDIT_STATE, None)
        await msg.reply_text(done_text, reply_markup=admin_main_keyboard())
        await send_marketing_settings_menu(msg.chat_id, context)
        return

    if context.user_data.get(INVITE_BANNER_PHOTO_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(INVITE_BANNER_PHOTO_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            await send_invite_text_settings_menu(msg.chat_id, context)
            return

        if not getattr(msg, "photo", None):
            await msg.reply_text(
                "❌ لطفاً فقط عکس ارسال کنید یا برای لغو «❌لغو» را بزنید.",
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        try:
            file_id = msg.photo[-1].file_id
            userbot_db.set_text_setting("invite_banner_photo_id", file_id)
        except Exception as e:
            context.user_data.pop(INVITE_BANNER_PHOTO_EDIT_STATE, None)
            await msg.reply_text(f"❌ خطا در ذخیره عکس بنر:\n{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(INVITE_BANNER_PHOTO_EDIT_STATE, None)
        await msg.reply_text("✅ عکس بنر دعوت ذخیره شد.", reply_markup=admin_main_keyboard())
        await send_invite_text_settings_menu(msg.chat_id, context)
        return

    # بررسی ویزارد تنظیم کانال رویداد
    if context.user_data.get(EVENT_CHANNEL_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(EVENT_CHANNEL_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
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
                "❌ ورودی معتبر نیست.\n"
                "لطفاً یک پیام از کانال فوروارد کنید یا @channel / -100... را بفرستید.",
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        try:
            settings = _get_buy_renew_settings()
            settings["event_channel_id"] = channel_target
            userbot_db.set_buy_renew_settings(settings)
        except Exception as e:
            context.user_data.pop(EVENT_CHANNEL_EDIT_STATE, None)
            await msg.reply_text(f"❌ خطا در ذخیره کانال رویداد:\n{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(EVENT_CHANNEL_EDIT_STATE, None)
        title_part = f" ({channel_title})" if channel_title else ""
        await msg.reply_text(
            f"✅ کانال رویداد ذخیره شد:\n{channel_target}{title_part}",
            reply_markup=admin_main_keyboard(),
        )
        await send_buy_renew_settings_menu(msg.chat_id, context)
        return

    # بررسی ویزارد یادآور وضعیت اشتراک
    if context.user_data.get(SUB_REMINDER_EDIT_STATE):
        edit_type = context.user_data.get(SUB_REMINDER_EDIT_STATE)
        if text in CANCEL_WORDS:
            context.user_data.pop(SUB_REMINDER_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            return

        try:
            value = int(text.replace(",", ""))
            if value <= 0:
                raise ValueError
        except ValueError:
            unit = "گیگابایت" if edit_type == "usage_gb" else "روز"
            await msg.reply_text(f"❌ لطفاً عدد معتبر بزرگ‌تر از صفر وارد کنید ({unit}).", reply_markup=userbot_cancel_keyboard())
            return

        try:
            userbot_db.set_sub_reminder_value(edit_type, value)
        except Exception as e:
            await msg.reply_text(f"❌ خطا در ذخیره تنظیمات یادآور:\n{e}", reply_markup=admin_main_keyboard())
            context.user_data.pop(SUB_REMINDER_EDIT_STATE, None)
            return

        context.user_data.pop(SUB_REMINDER_EDIT_STATE, None)
        reminder = _get_sub_reminder_settings()
        if edit_type == "usage_gb":
            await msg.reply_text(
                f"✅ مقدار یادآور مصرف روی {reminder.get('usage_gb', value)} گیگابایت تنظیم شد.",
                reply_markup=admin_main_keyboard(),
            )
        else:
            await msg.reply_text(
                f"✅ مقدار یادآور زمان روی {reminder.get('days', value)} روز تنظیم شد.",
                reply_markup=admin_main_keyboard(),
            )
        await send_sub_status_reminder_menu(msg.chat_id, context)
        return

    # بررسی ویزارد تنظیم دامنه Multi Server
    if context.user_data.get(SUB_BASE_URL_EDIT_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(SUB_BASE_URL_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            return

        normalized = _normalize_public_base_url(text)
        if text.strip() != "0" and not normalized:
            await msg.reply_text(
                "❌ ورودی نامعتبر است.\n"
                "نمونه‌های صحیح:\n"
                "user.yourdomain.com\n"
                "https://user.yourdomain.com\n"
                "یا برای ریست: 0",
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        try:
            stored = userbot_db.set_managed_sub_base_url(normalized)
        except Exception as e:
            context.user_data.pop(SUB_BASE_URL_EDIT_STATE, None)
            await msg.reply_text(f"❌ خطا در ذخیره دامنه:\n{e}", reply_markup=admin_main_keyboard())
            return

        context.user_data.pop(SUB_BASE_URL_EDIT_STATE, None)
        if stored:
            ssl_hint = ""
            host_hint = _extract_host_only(stored)
            if host_hint:
                ssl_hint = (
                    "\n\nاگر SSL این دامنه هنوز فعال نیست، اجرا کنید:\n"
                    f"cd ~/Hiddify-SellBot && sudo ./install.sh ssl {host_hint} your-email@example.com"
                )
            await msg.reply_text(
                f"✅ دامنه لینک اشتراک هوشمند ذخیره شد:\n{stored}{ssl_hint}",
                reply_markup=admin_main_keyboard(),
            )
        else:
            await msg.reply_text(
                "✅ دامنه لینک اشتراک هوشمند به حالت خودکار برگشت.",
                reply_markup=admin_main_keyboard(),
            )
        await send_sub_link_status_menu(msg.chat_id, context)
        return

    # بررسی ویزارد مشخصات اشتراک تستی
    if context.user_data.get(TRIAL_SPEC_EDIT_STATE):
        edit_type = context.user_data.get(TRIAL_SPEC_EDIT_STATE)
        if text in CANCEL_WORDS:
            context.user_data.pop(TRIAL_SPEC_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
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
                await msg.reply_text("❌ لطفاً مقدار معتبر وارد کنید. مثال: 0.5 یا 1", reply_markup=userbot_cancel_keyboard())
            else:
                await msg.reply_text("❌ لطفاً عدد صحیح معتبر بزرگ‌تر از صفر (روز) وارد کنید.", reply_markup=userbot_cancel_keyboard())
            return

        try:
            userbot_db.set_trial_spec_value(edit_type, value)
        except Exception as e:
            await msg.reply_text(f"❌ خطا در ذخیره مشخصات تستی:\n{e}", reply_markup=admin_main_keyboard())
            context.user_data.pop(TRIAL_SPEC_EDIT_STATE, None)
            return

        context.user_data.pop(TRIAL_SPEC_EDIT_STATE, None)
        spec = _get_trial_spec_settings()
        if edit_type == "usage_gb":
            usage_val = float(spec.get("usage_gb", value))
            usage_txt = f"{usage_val:g}"
            await msg.reply_text(
                f"✅ حجم اشتراک تستی روی {usage_txt} گیگابایت تنظیم شد.",
                reply_markup=admin_main_keyboard(),
            )
        else:
            await msg.reply_text(
                f"✅ زمان اشتراک تستی روی {spec.get('days', value)} روز تنظیم شد.",
                reply_markup=admin_main_keyboard(),
            )
        await send_trial_spec_menu(msg.chat_id, context)
        return

    # بررسی ویزارد کیف پول
    if context.user_data.get(RENEW_POLICY_EDIT_STATE):
        edit_type = context.user_data.get(RENEW_POLICY_EDIT_STATE)
        if text in CANCEL_WORDS:
            context.user_data.pop(RENEW_POLICY_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            return

        try:
            value = int(text.replace(",", ""))
            if value <= 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("❌ لطفاً عدد صحیح بزرگ‌تر از صفر وارد کنید.", reply_markup=userbot_cancel_keyboard())
            return

        try:
            userbot_db.set_buy_renew_limit(edit_type, value)
        except Exception as e:
            await msg.reply_text(f"❌ خطا در ذخیره تنظیمات تمدید:\n{e}", reply_markup=admin_main_keyboard())
            context.user_data.pop(RENEW_POLICY_EDIT_STATE, None)
            return

        context.user_data.pop(RENEW_POLICY_EDIT_STATE, None)
        if edit_type == "renew_max_days":
            await msg.reply_text(
                f"✅ حداکثر زمان مجاز برای تمدید روی {value} روز تنظیم شد.",
                reply_markup=admin_main_keyboard(),
            )
        elif edit_type == "renew_unlimited_volume_from_gb":
            await msg.reply_text(
                f"✅ آستانه نمایش حجم نامحدود روی {value} گیگابایت تنظیم شد.",
                reply_markup=admin_main_keyboard(),
            )
            await send_buy_renew_settings_menu(msg.chat_id, context)
            return
        elif edit_type == "renew_unlimited_time_from_days":
            await msg.reply_text(
                f"✅ آستانه نمایش زمان نامحدود روی {value} روز تنظیم شد.",
                reply_markup=admin_main_keyboard(),
            )
            await send_buy_renew_settings_menu(msg.chat_id, context)
            return
        else:
            await msg.reply_text(
                f"✅ حداکثر مصرف مجاز برای تمدید روی {value} گیگابایت تنظیم شد.",
                reply_markup=admin_main_keyboard(),
            )
        await send_renew_policy_menu(msg.chat_id, context)
        return

    # بررسی ویزارد تنظیمات تراکنش/پلن
    if context.user_data.get(TX_PLANS_EDIT_STATE):
        edit_type = context.user_data.get(TX_PLANS_EDIT_STATE)
        if text in CANCEL_WORDS:
            context.user_data.pop(TX_PLANS_EDIT_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            return

        try:
            value = int(text.replace(",", ""))
            if value <= 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("❌ لطفاً عدد صحیح بزرگ‌تر از صفر وارد کنید (تومان).", reply_markup=userbot_cancel_keyboard())
            return

        try:
            if edit_type == "min_transaction_toman":
                userbot_db.set_tx_plans_min_transaction(value)
            else:
                raise ValueError("invalid edit state")
        except Exception as e:
            await msg.reply_text(f"❌ خطا در ذخیره تنظیمات تراکنش/پلن:\n{e}", reply_markup=admin_main_keyboard())
            context.user_data.pop(TX_PLANS_EDIT_STATE, None)
            return

        context.user_data.pop(TX_PLANS_EDIT_STATE, None)
        await msg.reply_text(
            f"✅ حداقل تراکنش روی {value:,} تومان تنظیم شد.",
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
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
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
            await msg.reply_text(f"❌ خطا در ذخیره متن:\n{e}", reply_markup=admin_main_keyboard())
            context.user_data.pop(TEXT_SETTINGS_EDIT_STATE, None)
            return

        context.user_data.pop(TEXT_SETTINGS_EDIT_STATE, None)
        await msg.reply_text("✅ متن با موفقیت ذخیره شد.", reply_markup=admin_main_keyboard())
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
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
            return
        
        # مقدار باید عدد باشد
        try:
            amount = int(text.replace(",", ""))
        except ValueError:
            await msg.reply_text("❌ لطفاً عدد وارد کنید (تومان).")
            return
        
        user_id = context.user_data.pop(WALLET_EDIT_STATE) # گرفتن ID و پاک کردن استیت
        
        # 1. آپدیت دیتابیس
        userbot_db.set_user_wallet(user_id, amount)
        
        # 2. دریافت اطلاعات کاربر برای ارسال نوتیفیکیشن
        user = userbot_db.get_user_by_id(user_id)
        
        await msg.reply_text(f"✅ موجودی کیف پول کاربر با موفقیت به {_format_toman(amount)} تومان تغییر کرد.", reply_markup=admin_main_keyboard())
        await send_user_profile(user_id, msg.chat_id, context)

        # 3. ارسال پیام به ربات کاربر
        if user and user.get('telegram_id') and USER_BOT_TOKEN:
            try:
                user_bot = Bot(token=USER_BOT_TOKEN)
                notify_text = f"💰 کیف پول\n\nموجودی حساب شما توسط مدیریت به {_format_toman(amount)} تومان تغییر یافت."
                await user_bot.send_message(chat_id=user['telegram_id'], text=notify_text)
            except Exception as e:
                await msg.reply_text(f"⚠️ موجودی آپدیت شد ولی پیام به کاربر ارسال نشد: {e}")
        return

    # بررسی ویزارد ارسال پیام
    if context.user_data.get(MESSAGE_SEND_STATE):
        if text in CANCEL_WORDS:
            context.user_data.pop(MESSAGE_SEND_STATE, None)
            await msg.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
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
            await msg.reply_text("❌ کاربر نامعتبر است.", reply_markup=admin_main_keyboard())
            return
        user = userbot_db.get_user_by_id(user_id)

        if user and user.get('telegram_id') and USER_BOT_TOKEN:
            try:
                user_bot = Bot(token=USER_BOT_TOKEN)
                final_msg = (
                    "📩 پیام جدیدی از سمت ادمین دریافت شد\n"
                    f"📄 متن پیام: {text}"
                )
                kb = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📩پاسخ", callback_data="support:adminmsg:reply")]]
                )
                await user_bot.send_message(
                    chat_id=user['telegram_id'],
                    text=final_msg,
                    reply_markup=kb,
                )
                await msg.reply_text("📩پیام ارسال شد", reply_markup=admin_main_keyboard())
            except Exception as e:
                await msg.reply_text(f"❌ خطا در ارسال پیام به کاربر: {e}", reply_markup=admin_main_keyboard())
        else:
            await msg.reply_text("❌ کاربر یافت نشد یا شناسه تلگرام ندارد.", reply_markup=admin_main_keyboard())

        return


async def send_user_services_list(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    raw_services = userbot_db.get_services_for_user(user_id)
    local_active_services = [s for s in raw_services if _is_locally_active_service(s)]

    services: List[Dict[str, Any]] = []
    if local_active_services:
        sem = asyncio.Semaphore(5)

        async def _check_visible(service: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            async with sem:
                return service if await _service_exists_on_panel(service) else None

        checked = await asyncio.gather(*[_check_visible(s) for s in local_active_services])
        services = [s for s in checked if s]

    if not services:
        text = (
            "#️⃣ لیست سرویس‌ها\n"
            "شما می‌توانید لیست سرویس‌ها و اطلاعات آن‌ها را اینجا مشاهده کنید\n"
            f"📦 تعداد کل سرویس‌ها: {len(raw_services)}\n"
            "🟢 سرویس‌های فعال: 0\n\n"
            "❌ سرویس فعال و موجودی برای این کاربر یافت نشد."
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙بازگشت", callback_data=f"userbot:user:{user_id}")]])
        if message:
            try: await message.edit_text(text, reply_markup=kb)
            except BadRequest: await context.bot.send_message(chat_id, text, reply_markup=kb)
        else: await context.bot.send_message(chat_id, text, reply_markup=kb)
        return

    rows: List[List[InlineKeyboardButton]] = []
    service_buttons: List[InlineKeyboardButton] = []
    for s in services:
        name = s.get("name") or f"Service #{s['id']}"
        emoji = "🟡" if _comment_has_flag(str(s.get("comment") or ""), "test") else "🔵"
        service_buttons.append(
            InlineKeyboardButton(
                f"{emoji} |{name}",
                callback_data=f"userbot:svc:{s['id']}",
            )
        )

    for i in range(0, len(service_buttons), 3):
        chunk = service_buttons[i:i + 3]
        rows.append(list(reversed(chunk)))

    rows.append([InlineKeyboardButton("بازگشت🔙", callback_data=f"userbot:user:{user_id}")])
    
    kb = InlineKeyboardMarkup(rows)
    text = (
        "#️⃣ لیست سرویس‌ها\n"
        "شما می‌توانید لیست سرویس‌ها و اطلاعات آن‌ها را اینجا مشاهده کنید\n"
        f"📦 تعداد کل سرویس‌ها: {len(raw_services)}\n"
        f"🟢 سرویس‌های فعال: {len(services)}"
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
        "🔹 لیست سفارشات\n"
        f"🔸 تعداد سفارشات: {stats['total_count']}\n"
        f"🔸 مجموع حجم سفارشات(GB): {fmt(stats['total_gb'])}\n"
        f"🔸 مجموع ارزش سفارشات: {fmt(stats['total_price'])}تومان\n"
        "❖ ⬩----------------------------------⬩ ❖\n"
        f"🔸 تعداد سفارشات 30 روز گذشته: {stats['last30_count']}\n"
        f"🔸 حجم سفارشات 30 روز گذشته(GB): {fmt(stats['last30_gb'])}\n"
        f"🔸 ارزش سفارشات 30 روز گذشته: {fmt(stats['last30_price'])}تومان\n"
        "❖ ⬩----------------------------------⬩ ❖\n"
        f"🔸 تعداد سفارشات این ماه: {stats['month_count']}\n"
        f"🔸 حجم سفارشات این ماه(GB): {fmt(stats['month_gb'])}\n"
        f"🔸 ارزش سفارشات این ماه: {fmt(stats['month_price'])}تومان"
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
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data=f"userbot:user:{user_id}")])

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
        "🔹 لیست تراکنشات\n"
        f"🔸 تعداد تراکنشات: {stats['total_count']}\n"
        f"🔸 مبلغ تراکنشات: {fmt(stats['total_amount'])}تومان\n"
        "❖ ⬩----------------------------------⬩ ❖\n"
        f"🔸 تراکنشات 30 روز گذشته: {stats['last30_count']}\n"
        f"🔸 مبلغ تراکنشات 30 روز گذشته: {fmt(stats['last30_amount'])}تومان\n"
        "❖ ⬩----------------------------------⬩ ❖\n"
        f"🔸 تراکنشات این ماه: {stats['month_count']}\n"
        f"🔸 مبلغ تراکنشات این ماه: {fmt(stats['month_amount'])}تومان"
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
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data=f"userbot:user:{user_id}")])

    kb = InlineKeyboardMarkup(rows)
    
    if message:
        try: await message.edit_text(text, reply_markup=kb)
        except BadRequest: await context.bot.send_message(chat_id, text, reply_markup=kb)
    else: await context.bot.send_message(chat_id, text, reply_markup=kb)

async def send_service_detail(service_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    svc = userbot_db.get_service_by_id(service_id)
    if not svc:
        return
    if not _is_locally_active_service(svc) or not await _service_exists_on_panel(svc):
        text = "❌ این سرویس حذف شده یا غیرفعال است."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙بازگشت", callback_data=f"userbot:user:{svc.get('user_id')}")]])
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
    text = "📗 مدیریت سفارشات"
    kb = build_orders_menu_keyboard()
    if message:
        try: await message.edit_text(text, reply_markup=kb)
        except BadRequest: await context.bot.send_message(chat_id, text, reply_markup=kb)
    else: await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_gifts_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = "🎁مدیریت هدایا"
    kb = build_gifts_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_zarin_coupons_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    coupons = userbot_db.list_zarin_vouchers(limit=300)
    total = len(coupons)
    active = len(userbot_db.list_active_zarin_vouchers(limit=500))
    text = (
        "💼 کوپن شارژ کیف پول\n"
        "❖ ◈━━━━━━━━━━━━━━━━━━━━◈ ❖\n"
        f"◈ تعداد کل: {total}\n"
        f"◈ فعال: {active}\n"
        f"◈ غیرفعال: {max(0, total - active)}"
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
    item = userbot_db.get_zarin_voucher(code)
    if not item:
        await context.bot.send_message(chat_id, "❌ کوپن یافت نشد.")
        await send_zarin_coupons_menu(chat_id, context)
        return
    c = str(item.get("code") or "").strip()
    amount = int(item.get("amount_toman") or 0)
    used = int(item.get("used_count") or 0)
    max_uses = int(item.get("max_uses") or 1)
    left = max(0, max_uses - used)
    exp_raw = str(item.get("expires_at") or "").strip()
    exp = exp_raw or "نامحدود"
    remain = "نامحدود"
    if exp_raw:
        try:
            exp_dt = datetime.strptime(exp_raw, "%Y-%m-%d %H:%M:%S")
            delta = exp_dt - datetime.now(timezone.utc).replace(tzinfo=None)
            if delta.total_seconds() <= 0:
                remain = "منقضی شده"
            else:
                total = int(delta.total_seconds())
                h = total // 3600
                m = (total % 3600) // 60
                s = total % 60
                remain = f"{h:02d}:{m:02d}:{s:02d}"
        except Exception:
            remain = "نامشخص"
    link = str(item.get("zarinpal_link") or "").strip() or "ثبت نشده"
    status = "فعال" if int(item.get("is_active") or 0) == 1 else "غیرفعال"
    if remain == "منقضی شده":
        status = "منقضی شده"
    elif left <= 0:
        status = "تکمیل ظرفیت"
    text = (
        f"🏷 کد: {c}\n"
        "❖ ◈━━━━━━━━━━━━━━━━━━━━◈ ❖\n"
        f"◈ وضعیت: {status}\n"
        f"◈ استفاده: {used} از {max_uses}\n"
        f"◈ باقی‌مانده: {left}\n"
        f"◈ هدیه شارژ کیف پول: {amount:,} تومان\n"
        f"◈ انقضا: {exp}\n"
        f"◈ زمان باقی‌مانده: {remain}\n"
        f"◈ لینک پرداخت: {link}"
    )
    kb = build_zarin_coupon_detail_keyboard(c)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)


async def send_userbot_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = "⚙️تنظیمات ربات کاربران"
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
    status = "روشن ✅" if enabled else "خاموش ❌"
    descriptions = "\n".join(
        f"{'✅' if key == theme else '▫️'} {meta['title']}: {meta['description']}"
        for key, meta in BUTTON_STYLE_THEMES.items()
    )
    text = (
        "🎨 تنظیمات دکمه‌های رنگی\n\n"
        f"وضعیت فعلی: {status}\n"
        f"طرح فعلی: {theme_meta['title']}\n\n"
        f"{descriptions}\n\n"
        "هر طرح فقط ظاهر دکمه‌ها را تغییر می‌دهد و روی عملکرد ربات اثری ندارد."
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
    text = "🛍تنظیمات اشتراک"
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
    current_base_text = current_base if current_base else "خودکار (بر اساس دامنه سرور/ENV)"
    text = (
        "📁وضعیت نمایش لینک اشتراک\n\n"
        f"🌐 دامنه فعلی لینک اشتراک هوشمند:\n{current_base_text}"
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
    text = "🛍تنظیمات اشتراک"
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
    text = "🛍تنظیمات اشتراک"
    kb = build_trial_spec_menu_keyboard(spec)
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_buy_renew_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = "🛒تنظیمات خرید و تمدید"
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
    text = "🧮تنظیمات تراکنشات و پلن ها"
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
    text = "🧾تنظیمات متون"
    kb = build_text_settings_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_guide_text_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = "🧾تنظیمات متنون"
    kb = build_guide_text_settings_menu_keyboard()
    if message:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_invite_text_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    text = "📄تنظیم بنر دعوت"
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
    text = "🎯تنظیمات بازاریابی"
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
        "🔒تنظیمات عضویت اجباری\n"
        f"📢 کانال فعلی: {channel_disp}"
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
    text = "💳تنظیمات پرداخت"
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
    text = "🗃تنظیمات بکاپ و بازیابی"
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
        "card": "💳تنظیمات کارت به کارت",
        "zarinpal": "📦تنظیمات زرین پال\n🧩راهنما:لینک",
        "perfect": "🧰تنظیمات پرفکت مانی",
        "crypto": "🔗تنظیمات پرداخت ارز دیجیتال",
    }
    text = title_map.get(method, "💳تنظیمات پرداخت")
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


async def send_payment_cards_list_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    cards = database.get_cards()
    text = "💳لیست کارت‌ها"
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
                await message.edit_text("❌ کارت یافت نشد.")
            except Exception:
                await context.bot.send_message(chat_id, "❌ کارت یافت نشد.")
        else:
            await context.bot.send_message(chat_id, "❌ کارت یافت نشد.")
        await send_payment_cards_list_menu(chat_id, context)
        return

    n = str(card.get("number") or "").strip() or "-"
    owner = str(card.get("owner") or "").strip() or "-"
    text = (
        f"❖ شماره کارت: <code>{n}</code>\n"
        f"❖ نام صاحب کارت: {owner}"
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
    text = "🧮تنظیمات تراکنشات و پلن ها"
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
    text = "🧮تنظیمات تراکنشات و پلن ها"
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
    text = "🛒تنظیمات خرید و تمدید"
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
    text = "🛒تنظیمات خرید و تمدید"
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
    policy_title = {"advanced": "پیشرفته", "default": "پیشفرض", "fair": "منصفانه"}.get(policy, "پیشرفته")
    volume_mode = str(settings.get("renew_volume_mode") or "").strip().lower()
    time_mode = str(settings.get("renew_time_mode") or "").strip().lower()
    if volume_mode not in {"add", "reset"}:
        volume_mode = "add" if policy in {"default", "fair"} else "reset"
    if time_mode not in {"add", "reset"}:
        time_mode = "add" if policy == "fair" else "reset"
    volume_text = "افزایشی (باقیمانده + پلن جدید)" if volume_mode == "add" else "ریست (فقط پلن جدید)"
    time_text = "افزایشی (باقیمانده + پلن جدید)" if time_mode == "add" else "ریست (فقط پلن جدید)"
    text = (
        "تنظیم شیوه تمدید\n"
        f"⚙️ پروفایل فعلی: {policy_title}\n"
        f"📦 حالت حجم در تمدید: {volume_text}\n"
        f"⏳ حالت زمان در تمدید: {time_text}\n"
        f"📊 مقدار فعلی زمان: {days} روز\n"
        f"📆 مقدار فعلی مصرف: {usage} گیگابایت"
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
        "🧨🎁 آیا از بازنشانی تست رایگان کاربران اطمینان دارید؟\n"
        "⚠️ با تایید این مورد تمامی کاربران قادر به گرفتن تست رایگان مجدد می‌باشند."
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
        "🔹 لیست سفارشات",
        f"🔸 تعداد سفارشات: {stats['total_count']}",
        f"🔸 مجموع حجم سفارشات(GB): {fmt(stats['total_gb'])}",
        f"🔸 مجموع ارزش سفارشات: {fmt(stats['total_price'])}تومان",
        "❖ ⬩----------------------------------⬩ ❖",
        f"🔸 تعداد سفارشات 30 روز گذشته: {stats['last30_count']}",
        f"🔸 حجم سفارشات 30 روز گذشته(GB): {fmt(stats['last30_gb'])}",
        f"🔸 ارزش سفارشات 30 روز گذشته: {fmt(stats['last30_price'])}تومان",
        "❖ ⬩----------------------------------⬩ ❖",
        f"🔸 تعداد سفارشات این ماه: {stats['month_count']}",
        f"🔸 حجم سفارشات این ماه(GB): {fmt(stats['month_gb'])}",
        f"🔸 ارزش سفارشات این ماه: {fmt(stats['month_price'])}تومان"
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
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="userbot:menu")])
    
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
        await context.bot.send_message(chat_id, "❌ سفارش یافت نشد.")
        return

    text = (
        f"📄 سفارش #{order.get('order_id')}\n"
        f"👤 خریدار: {_display_name(order)}\n"
        f"📅 تاریخ: {order.get('created_at')}\n"
        f"📦 پلن: {order.get('plan_title')}\n"
        f"💰 قیمت: {_format_toman(order.get('price'))} تومان\n"
        f"📊 وضعیت: {order.get('status', 'تکمیل شده')}"
    )
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙بازگشت", callback_data="userbot:orders_menu")]])
    
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
        return "📬تیکت‌های باز"
    if s == "closed":
        return "📩تیکت‌های بسته"
    return "📨تیکت‌های در انتظار"


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
        raise RuntimeError("USER_BOT_TOKEN تنظیم نشده است.")

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
        f"{_ticket_bucket_title(s)}\n"
        f"◈ تعداد تیکت‌ها: {int(total)}"
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
        await context.bot.send_message(chat_id, "❌ کاربر نامعتبر است.")
        return
    user = userbot_db.get_user_by_id(uid) or {}
    tickets, total = userbot_db.get_tickets_for_user(uid, page=max(1, int(page or 1)), page_size=TICKETS_PAGE_SIZE)
    total_pages = max(1, math.ceil(int(total) / TICKETS_PAGE_SIZE))
    p = max(1, min(int(page or 1), total_pages))
    if p != int(page or 1):
        tickets, total = userbot_db.get_tickets_for_user(uid, page=p, page_size=TICKETS_PAGE_SIZE)
    display = _display_name(user) if user else str(uid)
    text = (
        f"📑 لیست تیکت‌های کاربر {display}\n"
        f"◈ تعداد تیکت‌ها: {int(total)}"
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
        await context.bot.send_message(chat_id, "❌ شناسه تیکت نامعتبر است.")
        return
    ticket = userbot_db.get_ticket_by_code(code)
    if not ticket:
        await context.bot.send_message(chat_id, "❌ تیکت یافت نشد.")
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
        await message.reply_text("جستجو لغو شد.", reply_markup=admin_main_keyboard())
        return

    # جستجو با Telegram ID
    if state == "by_id":
        if not text.isdigit():
            await message.reply_text("❌ لطفاً فقط عدد وارد کنید.")
            return
        results = userbot_db.search_users_by_telegram_id(int(text))
    else:
        if not text:
            await message.reply_text("❌ لطفاً نام یا @یوزرنیم را وارد کنید.")
            return
        # جستجو با نام
        results = userbot_db.search_users_by_name(text)

    context.user_data.pop(USER_SEARCH_STATE_KEY, None)

    if not results:
        await message.reply_text("❌ کاربری یافت نشد.", reply_markup=admin_main_keyboard())
        return
    
    if len(results) == 1:
        await message.reply_text("✅ کاربر یافت شد", reply_markup=admin_main_keyboard())
        await send_user_profile(results[0]['id'], message.chat_id, context)
        return

    # نمایش لیست نتایج
    lines = ["نتایج جستجو:"]
    rows = []
    for u in results:
        rows.append([InlineKeyboardButton(_display_name(u), callback_data=f"userbot:user:{u['id']}")])
    rows.append([InlineKeyboardButton("بازگشت", callback_data="userbot:users_menu")])
    
    await message.reply_text("✅ کاربر یافت شد", reply_markup=admin_main_keyboard())
    await message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def handle_payment_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """جستجوی تراکنش با ID"""
    message = update.message
    text = (message.text or "").strip()

    if text in CANCEL_WORDS:
        context.user_data.pop(PAYMENT_SEARCH_STATE, None)
        await message.reply_text("جستجو لغو شد.", reply_markup=admin_main_keyboard())
        return

    if not text.isdigit():
        await message.reply_text("❌ لطفاً فقط شناسه تراکنش (عدد) وارد کنید.")
        return

    pid = int(text)
    pay = userbot_db.get_payment_by_id(pid)
    
    context.user_data.pop(PAYMENT_SEARCH_STATE, None)

    if pay:
        await message.reply_text("✅ تراکنش یافت شد.", reply_markup=admin_main_keyboard())
        await send_payment_detail(pid, message.chat_id, context)
    else:
        await message.reply_text(f"❌ تراکنشی با شناسه {pid} یافت نشد.", reply_markup=admin_main_keyboard())


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
            await msg.reply_text("❌ اشتراک موردنظر یافت نشد.")
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
        await msg.edit_text("روش جستجو:", reply_markup=build_users_search_menu_keyboard())
        return

    if data.startswith("userbot:search:"):
        await query.answer()
        m = data.split(":")[2]
        context.user_data[USER_SEARCH_STATE_KEY] = "by_id" if m == "id" else "by_name"
        await msg.reply_text(
            f"لطفاً {'شناسه عددی' if m=='id' else 'نام یا @یوزرنیم'} کاربر را وارد کنید:", 
            reply_markup=userbot_cancel_keyboard()
        )
        return

    # --- 3. پروفایل کاربر + اکشن‌ها ---
    if data.startswith("userbot:user:"):
        parts = data.split(":")
        if len(parts) < 3: 
            await query.answer("داده نامعتبر", show_alert=True)
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
            await msg.reply_text("💰 مبلغ جدید کیف پول (تومان) را وارد کنید:", reply_markup=userbot_cancel_keyboard())
        
        elif act == "message":
            await query.answer()
            context.user_data[MESSAGE_SEND_STATE] = {"user_id": int(uid)}
            await msg.reply_text(
                "✍ لطفا متن پیامی که می خواهید برای کاربر ارسال شود را وارد کنید:",
                reply_markup=userbot_cancel_keyboard(),
            )
        
        elif act == "reset_trial":
            userbot_db.reset_free_trial(uid)
            await query.answer("✅ اشتراک تستی بازنشانی شد.", show_alert=True)
            await send_user_profile(uid, cid, context, message=msg)
        
        elif act == "ban":
            nst = userbot_db.toggle_ban_user(uid)
            alert_text = "⛔️ کاربر مسدود شد." if nst else "✅ کاربر آزاد شد."
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
            await query.answer("❌ داده نامعتبر است.", show_alert=True)
            return
        try:
            service_id = int(parts[2])
        except Exception:
            await query.answer("❌ شناسه سرویس نامعتبر است.", show_alert=True)
            return

        action = parts[3] if len(parts) >= 4 else ""
        service = userbot_db.get_service_by_id(service_id)
        if not service:
            await query.answer("❌ سرویس یافت نشد.", show_alert=True)
            return

        if not action:
            await query.answer()
            await send_service_detail(service_id, cid, context, message=msg)
            return

        target_server_id, target_user_uuid = _service_primary_target(service)
        if target_server_id <= 0 or not target_user_uuid:
            await query.answer(
                "❌ شناسه پنل این سرویس پیدا نشد. ابتدا سرویس را همگام‌سازی کنید.",
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
                            "✅ بله، حذف شود",
                            callback_data=f"deluser:{target_server_id}:{target_user_uuid}:yes",
                        ),
                        InlineKeyboardButton(
                            "لغو❌",
                            callback_data=f"deluser:{target_server_id}:{target_user_uuid}:no",
                        ),
                    ]
                ]
            )
            await msg.edit_text(
                "❓ آیا از حذف کامل این کاربر مطمئن هستید؟\n"
                "این عملیات قابل بازگشت نیست.",
                reply_markup=kb,
            )
            return

        await query.answer("❌ گزینه نامعتبر است.", show_alert=True)
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
        await msg.reply_text("🔎 شناسه سفارش را وارد کنید:", reply_markup=userbot_cancel_keyboard())
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
                await query.answer("❌ تراکنش یافت نشد.", show_alert=True)
                return
            text = _build_payment_detail_text(pay) + "\n\n⚠️ آیا از تغییر وضعیت تراکنش، اطمینان دارید؟"
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
                    await query.answer("❌ تراکنش یافت نشد.", show_alert=True)
                    return
                text = _build_payment_detail_text(pay) + "\n\nوضعیت جدید را انتخاب کنید:"
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
        await query.answer("❌ عملیات نامعتبر.", show_alert=True)
        return
    if data.startswith("userbot:pay:set:"):
        parts = data.split(":")
        if len(parts) != 5:
            await query.answer("❌ داده نامعتبر.", show_alert=True)
            return
        pid = int(parts[3])
        new_status = parts[4]
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
                uid = int(pay.get("user_id") or 0)
                user_btn_title = (pay.get("full_name") or pay.get("username") or str(pay.get("telegram_id") or uid)).strip()
                kb = None
                if uid > 0:
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"👤 {user_btn_title}", callback_data=f"userbot:user:{uid}")]
                    ])
                await context.bot.send_message(
                    chat_id=cid,
                    text=_build_payment_approved_report_text(pay),
                    reply_markup=kb,
                )
                await _send_auto_gift_message_if_needed(pay)
                await _send_payment_event_channel_report_if_enabled(pay)
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
            await query.answer("❌ تراکنش یافت نشد.", show_alert=True)
            return
        uid = pay.get('user_id')
        if not uid:
            await query.answer("❌ کاربر نامعتبر است.", show_alert=True)
            return
        context.user_data[MESSAGE_SEND_STATE] = {"user_id": int(uid)}
        await msg.reply_text(
            "✍ لطفا متن پیامی که می خواهید برای کاربر ارسال شود را وارد کنید:",
            reply_markup=userbot_cancel_keyboard(),
        )
        return
    if data == "userbot:payments:search":
        await query.answer()
        context.user_data[PAYMENT_SEARCH_STATE] = True
        await msg.reply_text("🔎 شناسه تراکنش را وارد کنید:", reply_markup=userbot_cancel_keyboard())
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
                    amount_txt = _format_toman(pay.get('amount') or 0)
                    pay_meta = _parse_receipt_meta(str(pay.get("receipt_image") or ""))
                    is_direct_buy = str(pay_meta.get("pay_flow") or "").strip().lower() == "direct_buy"
                    if new_st == "approved":
                        if is_direct_buy:
                            notify_text = (
                                "✅ پرداخت شما تایید شد.\n\n"
                                "اشتراک شما در حال ساخت است و پس از آماده‌سازی ارسال می‌شود."
                            )
                        else:
                            notify_text = (
                                "✅ پرداخت شما تایید شد.\n\n"
                                f"مبلغ {amount_txt} تومان به کیف پول شما اضافه شد."
                            )
                    else:
                        notify_text = (
                            "🚫 پرداخت شما رد شد.\n\n"
                            "در صورت نیاز با پشتیبانی تماس بگیرید."
                        )
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
            await query.answer("❌ عملیات نامعتبر (قبلا انجام شده یا وجود ندارد)", show_alert=True)
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
        await msg.reply_text("✍️ لطفا پاسخ خود را به صورت کامل ارسال نمایید", reply_markup=userbot_cancel_keyboard())
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
        await msg.reply_text("✍️ لطفا پاسخ خود را به صورت کامل ارسال نمایید", reply_markup=userbot_cancel_keyboard())
        return

    if data.startswith("userbot:ticketreply:"):
        await query.answer()
        act = data.split(":")[2] if len(data.split(":")) > 2 else ""
        st = context.user_data.get(TICKET_REPLY_STATE) or {}
        if not isinstance(st, dict):
            context.user_data.pop(TICKET_REPLY_STATE, None)
            await query.answer("وضعیت پاسخ تیکت نامعتبر است.", show_alert=True)
            return

        ticket_code = int(st.get("ticket_code") or 0)
        list_status = str(st.get("list_status") or "pending").strip().lower()
        page = max(1, int(st.get("page") or 1))
        from_user_id = int(st.get("from_user_id") or 0)
        step = str(st.get("step") or "").strip().lower()
        if ticket_code <= 0:
            context.user_data.pop(TICKET_REPLY_STATE, None)
            await query.answer("تیکت نامعتبر است.", show_alert=True)
            return

        if act == "cancel":
            context.user_data.pop(TICKET_REPLY_STATE, None)
            try:
                await msg.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=cid, text="❌ لغو شد.", reply_markup=admin_main_keyboard())
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
                text="✍️ لطفا پاسخ خود را به صورت کامل ارسال نمایید",
                reply_markup=userbot_cancel_keyboard(),
            )
            return

        if act == "skip":
            if step != "wait_screenshot":
                await query.answer("در این مرحله قابل استفاده نیست.", show_alert=True)
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
                await query.answer("ابتدا مراحل پاسخ را کامل کنید.", show_alert=True)
                return
            reply_text = str(st.get("reply_text") or "").strip()
            photo_file_id = str(st.get("photo_file_id") or "").strip()
            if not reply_text:
                await query.answer("متن پاسخ خالی است.", show_alert=True)
                return

            ticket = userbot_db.get_ticket_by_code(ticket_code)
            if not ticket:
                context.user_data.pop(TICKET_REPLY_STATE, None)
                await query.answer("تیکت یافت نشد.", show_alert=True)
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
                await query.answer("ثبت پاسخ انجام نشد.", show_alert=True)
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
                    notify_text = (
                        f"📩 پاسخ جدید برای تیکت #{ticket_code}\n\n"
                        f"{reply_text or 'یک پاسخ جدید ارسال شد.'}"
                    )
                    kb = InlineKeyboardMarkup(
                        [[InlineKeyboardButton("📬 مشاهده تیکت", callback_data=f"support:view:{ticket_code}:1")]]
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
                text="✅ پاسخ تیکت ثبت شد.",
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

        await query.answer("گزینه نامعتبر است.", show_alert=True)
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
            await query.answer("وضعیت نامعتبر است.", show_alert=True)
            return
        admin_name = str(query.from_user.full_name or query.from_user.username or "admin").strip()
        ok = userbot_db.set_ticket_status(
            code,
            new_status,
            admin_name=admin_name,
            admin_telegram_id=int(query.from_user.id or 0),
        )
        if not ok:
            await query.answer("❌ تغییر وضعیت انجام نشد.", show_alert=True)
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
            await query.answer("وضعیت نامعتبر است.", show_alert=True)
            return
        admin_name = str(query.from_user.full_name or query.from_user.username or "admin").strip()
        ok = userbot_db.set_ticket_status(
            code,
            new_status,
            admin_name=admin_name,
            admin_telegram_id=int(query.from_user.id or 0),
        )
        if not ok:
            await query.answer("❌ تغییر وضعیت انجام نشد.", show_alert=True)
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

    if data == "userbot:gifts:coupons":
        await query.answer()
        await send_zarin_coupons_menu(cid, context, message=msg)
        return

    if data == "userbot:gifts:coupons:add":
        context.user_data[ZARIN_COUPON_ADD_STATE] = {"step": "code"}
        await query.answer()
        await msg.reply_text(
            "🏷 کد کوپن جدید را وارد کنید:\n(فقط حروف/عدد/`_`/`-`)",
            reply_markup=userbot_cancel_keyboard(),
            parse_mode="Markdown",
        )
        return

    if data == "userbot:gifts:coupons:delete":
        context.user_data[ZARIN_COUPON_DELETE_STATE] = True
        await query.answer()
        await msg.reply_text("➖ کد کوپن برای حذف را وارد کنید:", reply_markup=userbot_cancel_keyboard())
        return

    if data.startswith("userbot:gifts:coupon:"):
        if data.startswith("userbot:gifts:coupon:set_link:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            context.user_data[ZARIN_COUPON_LINK_STATE] = {"code": code}
            await query.answer()
            await msg.reply_text("🔗 لینک پرداخت زرین پال را ارسال کنید:", reply_markup=userbot_cancel_keyboard())
            return
        if data.startswith("userbot:gifts:coupon:set_code:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            context.user_data[ZARIN_COUPON_CODE_STATE] = {"code": code}
            await query.answer()
            await msg.reply_text("✏️ کد جدید کوپن را ارسال کنید:", reply_markup=userbot_cancel_keyboard())
            return
        if data.startswith("userbot:gifts:coupon:set_limit:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            context.user_data[ZARIN_COUPON_LIMIT_STATE] = {"code": code}
            await query.answer()
            await msg.reply_text("👤 محدودیت تعداد استفاده (عدد مثبت) را ارسال کنید:", reply_markup=userbot_cancel_keyboard())
            return
        if data.startswith("userbot:gifts:coupon:set_exp:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            context.user_data[ZARIN_COUPON_EXP_STATE] = {"code": code}
            await query.answer()
            await msg.reply_text(
                "🕒 مدت انقضا را به ساعت ارسال کنید.\nبرای نامحدود عدد 0 را ارسال کنید.",
                reply_markup=userbot_cancel_keyboard(),
            )
            return
        if data.startswith("userbot:gifts:coupon:set_amount:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            context.user_data[ZARIN_COUPON_AMOUNT_STATE] = {"code": code}
            await query.answer()
            await msg.reply_text("🎁 مبلغ هدیه کیف پول (تومان) را ارسال کنید:", reply_markup=userbot_cancel_keyboard())
            return
        if data.startswith("userbot:gifts:coupon:deeplink:"):
            code = str(data.rsplit(":", 1)[-1] or "").strip()
            bot_username = os.getenv("SUB_BOT_USERNAME", "").strip().lstrip("@")
            await query.answer()
            if not bot_username:
                await msg.reply_text("❌ متغیر `SUB_BOT_USERNAME` در `.env` تنظیم نشده است.")
                return
            deep_link = f"https://t.me/{bot_username}?start={code}"
            await msg.reply_text(f"🚀 دیپ لینک کوپن:\n{deep_link}", disable_web_page_preview=True)
            await send_zarin_coupon_detail(cid, context, code=code, message=msg)
            return
        code = str(data.rsplit(":", 1)[-1] or "").strip()
        await query.answer()
        await send_zarin_coupon_detail(cid, context, code=code, message=msg)
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
        status = "فعال شد" if settings.get("colored_buttons", True) else "غیرفعال شد"
        await query.answer(f"🎨 دکمه‌های رنگی {status}.")
        await send_colored_buttons_settings_menu(cid, context, message=msg)
        return

    if data.startswith("userbot:settings:ui:theme:"):
        theme = normalize_button_theme(data.rsplit(":", 1)[-1])
        userbot_db.set_ui_setting("button_theme", theme)
        theme_title = BUTTON_STYLE_THEMES.get(theme, BUTTON_STYLE_THEMES["smart"])["title"]
        await query.answer(f"طرح {theme_title} انتخاب شد.")
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
                    "🖼️ لطفاً عکس بنر دعوت را ارسال کنید.\nبرای لغو «❌لغو» را بزنید.",
                    reply_markup=userbot_cancel_keyboard(),
                )
                return
            if action == "remove_photo":
                try:
                    userbot_db.set_text_setting("invite_banner_photo_id", "")
                except Exception as e:
                    await query.answer(f"خطا: {e}", show_alert=True)
                    return
                await query.answer("✅ بنر حذف شد.")
                await send_invite_text_settings_menu(cid, context, message=msg)
                return
            await query.answer("گزینه نامعتبر است.", show_alert=True)
            return
        if data == "userbot:settings:texts:guide_menu":
            await query.answer()
            await send_guide_text_settings_menu(cid, context, message=msg)
            return
        if ":edit:" in data:
            field_name = data.rsplit(":edit:", 1)[-1].strip()
            labels = {
                "welcome_message": "پیام خوش آمدگویی",
                "faq_text": "متن سوالات متداول",
                "guide_text": "متن معرفی راهنما",
                "guide_android_text": "راهنمای اندروید",
                "guide_ios_text": "راهنمای IOS",
                "guide_windows_text": "راهنمای ویندوز",
                "guide_mac_text": "راهنمای مک",
                "guide_linux_text": "راهنمای لینوکس",
                "invite_text": "متن بنر دعوت (قدیمی)",
                "invite_info_text": "متن اطلاعات دعوت",
                "invite_banner_text": "متن بنر دعوت",
                "servers_list_text": "متن لیست سرورها",
                "plans_list_text": "متن لیست پلن‌ها",
                "ticket_panel_text": "متن پنل تیکت",
                "zarinpal_pro_text": "متن زرین پال",
                "card_to_card_text": "متن کارت به کارت",
            }
            if field_name not in labels:
                await query.answer("گزینه نامعتبر است.", show_alert=True)
                return
            settings = _get_text_settings()
            current = str(settings.get(field_name) or "").strip()
            if current.lower() in {"none", "null"}:
                current = ""
            current = current or "0"
            context.user_data[TEXT_SETTINGS_EDIT_STATE] = field_name
            await query.answer()
            prompt = (
                f"📝 {labels[field_name]}\n"
                f"📌 متن فعلی:\n{current}\n\n"
                "متن جدید را ارسال کنید."
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer("این مورد نامعتبر است.", show_alert=True)
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
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            try:
                await msg.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=cid,
                text=f"✅ بازنشانی تست رایگان برای همه کاربران انجام شد.\n👥 تعداد کاربران ریست‌شده: {changed}",
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

        await query.answer("این مورد در آپدیت بعدی فعال می‌شود 🚧", show_alert=True)
        return

    if data.startswith("userbot:settings:buy_renew:"):
        action = data.split(":")[-1]
        if action == "renew_unlimited_volume":
            settings = _get_buy_renew_settings()
            current = int(settings.get("renew_unlimited_volume_from_gb") or 1000)
            context.user_data[RENEW_POLICY_EDIT_STATE] = "renew_unlimited_volume_from_gb"
            await query.answer()
            prompt = (
                f"📝 مقدار فعلی: {current} گیگابایت\n"
                "♾ لطفاً حداقل مقدار (گیگابایت) برای نمایش حجم نامحدود را مشخص کنید:"
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
                f"📝 مقدار فعلی: {current} روز\n"
                "♾ لطفاً حداقل مقدار (روز) برای نمایش زمان نامحدود را مشخص کنید:"
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
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            await query.answer()
            await send_plan_columns_menu(cid, context, message=msg)
            return
        if ":server_columns:set:" in data:
            try:
                col = int(data.rsplit(":", 1)[-1])
                userbot_db.set_buy_renew_columns("servers", col)
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
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
                await query.answer(f"خطا: {e}", show_alert=True)
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
                "📢 تنظیم کانال رویداد\n"
                f"🔹 کانال فعلی: {current}\n\n"
                "🔗 ابتدا ربات ادمین را در کانال ادمین ادمین کنید، سپس یک پیام از همان کانال "
                "به اینجا فوروارد کنید.\n"
                "یا @channel / -100... را مستقیم ارسال کنید."
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
                await query.answer("گزینه نامعتبر است.", show_alert=True)
                return
            kind = str(kind or "").strip().lower()
            mode = str(mode or "").strip().lower()
            if kind not in {"volume", "time"} or mode not in {"add", "reset"}:
                await query.answer("گزینه نامعتبر است.", show_alert=True)
                return
            try:
                userbot_db.set_buy_renew_rollover_mode(kind, mode)
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            await query.answer("✅ ذخیره شد")
            await send_renew_policy_menu(cid, context, message=msg)
            return
        if ":renew_policy:" in data:
            policy = data.rsplit(":", 1)[-1].strip().lower()
            try:
                userbot_db.set_buy_renew_policy(policy)
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
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
                    f"📊 مقدار فعلی: {current} روز\n"
                    "📝 تعیین کنید حداکثر چند روز مانده به پایان اشتراک، تمدید مجاز باشد:"
                )
            elif limit_key == "usage":
                current = int(settings.get("renew_max_remaining_gb") or 3)
                context.user_data[RENEW_POLICY_EDIT_STATE] = "renew_max_remaining_gb"
                prompt = (
                    f"📆 مقدار فعلی: {current} گیگابایت\n"
                    "📝 تعیین کنید حداکثر چند گیگابایتِ باقی‌مانده، تمدید مجاز باشد:"
                )
            else:
                await query.answer("گزینه نامعتبر است.", show_alert=True)
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
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            await query.answer()
            await send_buy_renew_settings_menu(cid, context, message=msg)
            return
        await query.answer("این مورد نامعتبر است.", show_alert=True)
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
                    await query.answer("گزینه نامعتبر است.", show_alert=True)
                    return
                userbot_db.set_tx_plans_settings(current)
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
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
                await query.answer(f"خطا: {e}", show_alert=True)
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
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            await query.answer()
            await send_plan_sort_mode_menu(cid, context, message=msg)
            return

        action = data.split(":")[-1]
        if action in {"random_tx_spec"}:
            try:
                userbot_db.toggle_tx_plans_setting(action)
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
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
                f"📝 مقدار فعلی: {current:,} تومان\n"
                "💳 لطفاً حداقل مبلغ مجاز تراکنش را وارد کنید:"
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer("این مورد نامعتبر است.", show_alert=True)
        return

    if data.startswith("userbot:settings:trial_spec:"):
        action = data.split(":")[-1]
        if action == "enabled":
            try:
                userbot_db.toggle_trial_spec_enabled()
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            await query.answer()
            await send_trial_spec_menu(cid, context, message=msg)
            return
        if action == "announce":
            try:
                userbot_db.toggle_trial_spec_announce_enabled()
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
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
                    f"📝 مقدار فعلی: {current:g} گیگابایت\n"
                    "📊 لطفا حجم(گیگابایت) اشتراک تست را تنظیم کنید:"
                )
            else:
                current = int(spec.get("days") or 1)
                context.user_data[TRIAL_SPEC_EDIT_STATE] = "days"
                prompt = (
                    f"📝 مقدار فعلی: {current} روز\n"
                    "🕐 لطفا تعداد روزهای اشتراک تست را تنظیم کنید:"
                )
            await query.answer()
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer("این مورد نامعتبر است.", show_alert=True)
        return

    if data.startswith("userbot:settings:sub_status_reminder:"):
        action = data.split(":")[-1]
        if action == "enabled":
            try:
                userbot_db.toggle_sub_reminder_enabled()
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
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
                    f"📊 مقدار فعلی: {current} گیگابایت\n"
                    "📝 تعیین کنید چند گیگ مانده به اتمام اشتراک پیام یادآوری تمدید اشتراک ارسال شود:"
                )
            else:
                current = int(reminder.get("days") or 3)
                context.user_data[SUB_REMINDER_EDIT_STATE] = "days"
                prompt = (
                    f"⏳ مقدار فعلی: {current} روز\n"
                    "📝 تعیین کنید چند روز قبل از اتمام اشتراک پیام یادآوری تمدید اشتراک ارسال شود:"
                )
            await query.answer()
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer("این مورد نامعتبر است.", show_alert=True)
        return

    if data.startswith("userbot:settings:sub_link_status:"):
        action = data.split(":")[-1]
        if action == "set_base_url":
            context.user_data[SUB_BASE_URL_EDIT_STATE] = "edit"
            current = userbot_db.get_managed_sub_base_url() or "خودکار"
            await query.answer()
            prompt = (
                "🌐 دامنه عمومی لینک اشتراک هوشمند را وارد کنید.\n"
                "نمونه‌های معتبر:\n"
                "site.example.com\n"
                "https://site.example.com\n\n"
                "مقدار فعلی:\n"
                f"{current}\n\n"
                "برای بازگشت به حالت خودکار عدد 0 را ارسال کنید."
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
                "🔐 راهنمای فعال‌سازی SSL برای لینک‌های اشتراک هوشمند\n\n"
                "1) DNS دامنه را روی IP همین سرور ست کنید.\n"
                "2) روی سرور این دستور را اجرا کنید:\n"
                f"`cd ~/Hiddify-SellBot && sudo ./install.sh ssl {hint_domain} your-email@example.com`\n\n"
                "بعد از موفقیت، دامنه را در همین منو تنظیم کنید (با یا بدون https)."
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
        await query.answer("این مورد نامعتبر است.", show_alert=True)
        return

    if data.startswith("userbot:settings:marketing:"):
        if ":toggle:" in data:
            name = data.rsplit(":toggle:", 1)[-1].strip()
            try:
                userbot_db.toggle_marketing_setting(name)
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
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
                    "⚙️ تنظیم متن هدایای اتوماتیک\n"
                    f"📌 متن فعلی:\n{current}\n\n"
                    "متن جدید را ارسال کنید."
                )
            elif edit_name == "min_auto_gift_charge":
                current = int(settings.get("min_auto_gift_charge") or 0)
                context.user_data[MARKETING_EDIT_STATE] = "min_auto_gift_charge"
                prompt = (
                    "⚙️ حداقل شارژ هدیه اتوماتیک\n"
                    f"📌 مقدار فعلی: {current:,} تومان\n\n"
                    "مقدار جدید را به تومان ارسال کنید."
                )
            else:
                await query.answer("گزینه نامعتبر است.", show_alert=True)
                return
            await query.answer()
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer("این مورد نامعتبر است.", show_alert=True)
        return

    if data.startswith("userbot:settings:force_join:"):
        action = data.split(":")[-1].strip().lower()
        if action == "help":
            settings = _get_force_join_settings()
            await query.answer()
            try:
                await msg.reply_text(str(settings.get("guide_text") or "").strip() or "راهنما تنظیم نشده است.")
            except Exception:
                await context.bot.send_message(
                    chat_id=cid,
                    text=str(settings.get("guide_text") or "").strip() or "راهنما تنظیم نشده است.",
                )
            return
        if action == "toggle":
            try:
                userbot_db.toggle_force_join_enabled()
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            await query.answer()
            await send_force_join_settings_menu(cid, context, message=msg)
            return
        if action == "set_channel":
            context.user_data[FORCE_JOIN_EDIT_STATE] = True
            await query.answer()
            prompt = (
                "📢 تنظیم کانال پشتیبانی برای عضویت اجباری\n\n"
                "یک پیام از کانال فوروارد کنید یا @channel / -100... ارسال کنید."
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer("گزینه نامعتبر است.", show_alert=True)
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
                await query.answer("گزینه نامعتبر است.", show_alert=True)
                return
            try:
                userbot_db.toggle_payment_setting(key)
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            await query.answer()
            await send_payment_method_menu(cid, context, method=method, message=msg)
            return
        if data.startswith("userbot:settings:payment:card:item:"):
            raw = data.rsplit(":", 1)[-1].strip()
            number = re.sub(r"\D", "", raw)
            if len(number) != 16:
                await query.answer("شماره کارت نامعتبر است.", show_alert=True)
                return
            await query.answer()
            await send_payment_card_item_menu(cid, context, number=number, message=msg)
            return
        if data.startswith("userbot:settings:payment:card:copy:"):
            raw = data.rsplit(":", 1)[-1].strip()
            number = re.sub(r"\D", "", raw)
            if len(number) != 16:
                await query.answer("شماره کارت نامعتبر است.", show_alert=True)
                return
            await query.answer(f"شماره کارت:\n{number}", show_alert=True)
            return
        if data.startswith("userbot:settings:payment:card:edit_number:"):
            raw = data.rsplit(":", 1)[-1].strip()
            number = re.sub(r"\D", "", raw)
            if len(number) != 16:
                await query.answer("شماره کارت نامعتبر است.", show_alert=True)
                return
            context.user_data[PAYMENT_CARD_EDIT_STATE] = {"mode": "number", "number": number}
            await query.answer()
            try:
                await msg.reply_text("💳 شماره کارت جدید را وارد کنید:", reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text="💳 شماره کارت جدید را وارد کنید:", reply_markup=userbot_cancel_keyboard())
            return
        if data.startswith("userbot:settings:payment:card:edit_owner:"):
            raw = data.rsplit(":", 1)[-1].strip()
            number = re.sub(r"\D", "", raw)
            if len(number) != 16:
                await query.answer("شماره کارت نامعتبر است.", show_alert=True)
                return
            context.user_data[PAYMENT_CARD_EDIT_STATE] = {"mode": "owner", "number": number}
            await query.answer()
            try:
                await msg.reply_text("👤 نام جدید صاحب کارت را وارد کنید:", reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text="👤 نام جدید صاحب کارت را وارد کنید:", reply_markup=userbot_cancel_keyboard())
            return
        if data == "userbot:settings:payment:card:list":
            await query.answer()
            await send_payment_cards_list_menu(cid, context, message=msg)
            return
        if data == "userbot:settings:payment:card:add":
            context.user_data[PAYMENT_CARD_ADD_STATE] = {"step": "number"}
            await query.answer()
            prompt = (
                "⬇️ لطفا اطلاعات زیر را برای افزودن کارت وارد کنید\n"
                "💳 لطفا شماره کارت را وارد کنید:"
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        if data == "userbot:settings:payment:card:delete":
            context.user_data[PAYMENT_CARD_DELETE_STATE] = True
            await query.answer()
            prompt = "➖ حذف کارت\nشماره کارت را ارسال کنید:"
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
                "⚙️ در تعریف متن کارت به کارت، به جایگذاری مقادیر زیر توجه کنید:\n"
                "- شماره کارت: {CARD}\n"
                "- صاحب کارت: {HOLDER}\n"
                "- نام بانک: {BANK}\n"
                "- مقدار تومانی: {AMOUNT}\n"
                "- مقدار ریالی: {RIAL}\n"
                "❗️در بخش موردنظر، مقدار درج‌شده را همراه با کاراکترهای {} درج کنید تا مقدار موردنظر جایگزین شود."
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
                await msg.reply_text(f"📝 مقدار فعلی: {current}", reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
                await context.bot.send_message(chat_id=cid, text=f"📝 مقدار فعلی: {current}", reply_markup=userbot_cancel_keyboard())
            return
        if data == "userbot:settings:payment:card:random_tx_spec":
            try:
                userbot_db.toggle_tx_plans_setting("random_tx_spec")
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            await query.answer()
            await send_payment_method_menu(cid, context, method="card", message=msg)
            return
        if data == "userbot:settings:payment:card:require_last4":
            try:
                userbot_db.toggle_payment_setting("require_last4_for_card_receipt")
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
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
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            await query.answer()
            await send_payment_settings_menu(cid, context, message=msg)
            return
        if action == "event_channel_set":
            context.user_data[PAYMENT_CHANNEL_EDIT_STATE] = True
            await query.answer()
            prompt = (
                "📢 تنظیم کانال رویداد پرداخت\n\n"
                "یک پیام از کانال فوروارد کنید یا @channel / -100... ارسال کنید."
            )
            try:
                await msg.reply_text(prompt, reply_markup=userbot_cancel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=cid, text=prompt, reply_markup=userbot_cancel_keyboard())
            return
        await query.answer("گزینه نامعتبر است.", show_alert=True)
        return

    if data.startswith("userbot:settings:backup_restore:"):
        action = data.split(":")[-1].strip().lower()
        if action == "auto_toggle":
            try:
                userbot_db.toggle_backup_restore_setting("auto_backup_enabled")
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            await query.answer()
            await send_backup_restore_settings_menu(cid, context, message=msg)
            return
        if action == "event_toggle":
            try:
                userbot_db.toggle_backup_restore_setting("event_channel_enabled")
            except Exception as e:
                await query.answer(f"خطا: {e}", show_alert=True)
                return
            await query.answer()
            await send_backup_restore_settings_menu(cid, context, message=msg)
            return
        if action == "event_set":
            context.user_data[BACKUP_CHANNEL_EDIT_STATE] = True
            await query.answer()
            prompt = (
                "📢 تنظیم کانال رویداد بکاپ\n\n"
                "یک پیام از کانال فوروارد کنید یا @channel / -100... ارسال کنید."
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
                    "📦 فایل بکاپ را ارسال کنید.\n(فرمت قابل قبول: zip یا json)",
                    reply_markup=userbot_cancel_keyboard(),
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=cid,
                    text="📦 فایل بکاپ را ارسال کنید.\n(فرمت قابل قبول: zip یا json)",
                    reply_markup=userbot_cancel_keyboard(),
                )
            return
        if action == "download":
            await query.answer()
            try:
                backup_path = _make_bot_backup_zip()
            except Exception as e:
                await context.bot.send_message(chat_id=cid, text=f"❌ خطا در ساخت فایل بکاپ:\n{e}")
                return
            try:
                with backup_path.open("rb") as fh:
                    await context.bot.send_document(
                        chat_id=cid,
                        document=fh,
                        filename=backup_path.name,
                        caption="فایل پشتیبان ربات🗃",
                    )
            except Exception as e:
                await context.bot.send_message(chat_id=cid, text=f"❌ خطا در ارسال فایل بکاپ:\n{e}")
            return
        await query.answer("گزینه نامعتبر است.", show_alert=True)
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
            await query.answer("گروه ارسال نامعتبر است.", show_alert=True)
            return
        context.user_data[BROADCAST_SEND_STATE] = {
            "segment": segment,
            "step": "wait_text",
            "text": "",
        }
        await query.answer()
        await msg.reply_text(
            f"✍ لطفا پیام خود را برای ارسال به «{_broadcast_segment_label(segment)}» وارد کنید:",
            reply_markup=userbot_cancel_keyboard(),
        )
        return

    if data.startswith("userbot:settings:"):
        await query.answer("این بخش در آپدیت بعدی فعال می‌شود 🚧", show_alert=True)
        return

    # لاگ کردن دکمه‌های ناشناخته برای دیباگ
    logger.warning(f"Unhandled userbot callback: {data}")
    await query.answer()
