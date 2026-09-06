# AdminBot/agencies.py
# مدیریت سیستم نمایندگی (Agency/Reseller) در پنل ادمین

import logging
import subprocess
import signal
import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from html import escape
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from Shared import agent_db, database, userbot_db
from Shared import i18n as _i18n_mod
from AgentBot import database as agentbot_db
from CustomerBot import database as customerbot_db
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from AdminBot.keyboards import admin_main_keyboard

logger = logging.getLogger(__name__)

# ===============================
#   ثابت‌ها
# ===============================
AGENCIES_PAGE_SIZE = 8
SERVICES_PAGE_SIZE = 8

SEPARATOR = "❖ • ────────────────────── • ❖"

# استیت‌های متنی (wizard)
AGENCY_ADD_TELEGRAM = "agency:add_telegram"
AGENCY_ADD_PHONE = "agency:add_phone"
AGENCY_ADD_NAME = "agency:add_name"
AGENCY_EDIT_PHONE = "agency:edit_phone"
AGENCY_EDIT_NAME = "agency:edit_name"
AGENCY_WALLET_CHARGE = "agency:wallet_charge"
AGENCY_SET_WHOLESALE = "agency:set_wholesale"
AGENCY_SET_WHOLESALE_GB = "agency:set_wholesale_gb"
AGENCY_SET_WHOLESALE_DAYS = "agency:set_wholesale_days"
AGENCY_BULK_WHOLESALE = "agency:bulk_wholesale"
AGENCY_SET_AGENT_TOKEN = "agency:set_agent_token"
AGENCY_SVC_SEARCH = "agency:svc_search"
AGENCY_EVENT_CHANNEL_STATE = "agency:event_channel"

# کلیدهای user_data برای صفحه‌بندی
AGENCY_PAGE_KEY = "agency_page"
AGENCY_SERVICES_PAGE_KEY = "agency_services_page"
AGENCY_VIEWING_ID_KEY = "agency_viewing_id"


# ===============================
#   توابع کمکی
# ===============================
def _escape(text: Any) -> str:
    return escape(str(text or ""))


def _fmt_toman(amount: int) -> str:
    try:
        return f"{int(amount or 0):,}"
    except Exception:
        return str(amount or 0)


def _fmt_gb(value: float) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v < 0.0001:
        return "0"
    if v >= 1024:
        return f"{v / 1024:g}T"
    if v == int(v):
        return f"{int(v)}"
    return f"{v:g}"


def _t(key: str, **kw) -> str:
    return _i18n_mod.t(key, userbot_db.get_admin_language(), **kw)


def _adm_t(key: str, **kw) -> str:
    return _t(key, **kw)


_FA_MONTHS = {
    1: "ژانویه", 2: "فوریه", 3: "مارس", 4: "آوریل", 5: "مه", 6: "ژوئن",
    7: "ژوئیه", 8: "آگوست", 9: "سپتامبر", 10: "اکتبر", 11: "نوامبر", 12: "دسامبر",
}

_LOCATION_FLAGS = {
    "ترکیه": "🇹🇷",
    "آلمان": "🇩🇪",
    "هلند": "🇳🇱",
    "فنلاند": "🇫🇮",
    "هند": "🇮🇳",
}


def _fmt_fa_date(ts: str) -> str:
    """تبدیل تاریخ ISO به «روز ماه» مثل «06 سپتامبر»."""
    try:
        raw = str(ts or "").strip()
        if not raw:
            return "—"
        dt = datetime.fromisoformat(raw[:19])
    except Exception:
        return "—"
    month = _FA_MONTHS.get(dt.month, "")
    if not month:
        return f"{dt.day:02d}"
    return f"{dt.day:02d} {month}"


def _server_flag_title(title: str) -> str:
    """از عنوان سرور یک برچسب کوتاه با flag می‌سازد؛ مثل «🇩🇪 آلمان»."""
    raw = str(title or "").strip()
    if not raw:
        return ""
    flag = ""
    for word, fl in _LOCATION_FLAGS.items():
        if word in raw:
            flag = fl
            break
    location = raw.replace("لوکیشن", "")
    for fl in set(_LOCATION_FLAGS.values()):
        location = location.replace(fl, "")
    location = location.strip()
    if flag and location:
        return f"{flag} {location}"
    if flag:
        return flag
    return raw.strip()


def _usage_text(usage_cur: float, usage_lim: float) -> str:
    c, l = float(usage_cur or 0), float(usage_lim or 0)
    return f"{_fmt_gb(c)}/{_fmt_gb(l)}GB"


def _fmt_agent_display(agent: Dict[str, Any]) -> str:
    """نمایش خلاصه یک نماینده."""
    name = str(agent.get("full_name") or "").strip()
    username = str(agent.get("username") or "").strip()
    agent_id = agent.get("id", "?")
    active = "✅" if int(agent.get("is_active", 0)) else "❌"

    if name:
        ident = name
    elif username:
        ident = f"@{username}"
    else:
        ident = f"{_adm_t('ub_lit_d416fa44016d')}{agent_id}"

    return f"{active} {ident}"


def _main_menu_kb() -> InlineKeyboardMarkup:
    _lg = userbot_db.get_admin_language()
    _t = lambda k: _i18n_mod.t(k, _lg)
    try:
        ev = userbot_db.get_agency_event_settings()
        event_icon = "✅" if ev.get("event_channel_enabled") else "❌"
    except Exception:
        event_icon = "❌"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(_t("ag_add_agent"), callback_data="agency:add")],
            [
                InlineKeyboardButton(_t("ag_list_agents"), callback_data="agency:list:1"),
                InlineKeyboardButton(_t("ag_pending_charges"), callback_data="agency:payments:1"),
            ],
            [
                InlineKeyboardButton(_t("ag_stats"), callback_data="agency:stats"),
                InlineKeyboardButton(_t("ag_agent_token"), callback_data="agency:agenttoken"),
            ],
            [
                InlineKeyboardButton(event_icon, callback_data="agency:event:toggle"),
                InlineKeyboardButton(_t("ag_event_set"), callback_data="agency:event:set"),
            ],
            [InlineKeyboardButton(_t("ag_main_menu_back"), callback_data="agency:exit")],
        ]
    )


def _agent_detail_kb(agent_id: int) -> InlineKeyboardMarkup:
    _lg = userbot_db.get_admin_language()
    _t = lambda key: _i18n_mod.t(key, _lg)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(_t("ag_detail_charge"), callback_data=f"agency:charge:{agent_id}"),
                InlineKeyboardButton(_t("ag_detail_wallet"), callback_data=f"agency:wallet:{agent_id}"),
            ],
            [InlineKeyboardButton(_t("ag_detail_services"), callback_data=f"agency:services:{agent_id}:1")],
            [InlineKeyboardButton(_t("ag_detail_prices"), callback_data=f"agency:prices:{agent_id}:1")],
            [InlineKeyboardButton(_t("ag_detail_customer_bot"), callback_data=f"agency:bots:{agent_id}")],
            [InlineKeyboardButton(_t("ag_detail_reset_trial"), callback_data=f"agency:resettrial:{agent_id}")],
            [InlineKeyboardButton(_t("ag_detail_edit_name"), callback_data=f"agency:editname:{agent_id}")],
            [InlineKeyboardButton(_t("ag_detail_edit_phone"), callback_data=f"agency:editphone:{agent_id}")],
            [
                InlineKeyboardButton(_t("ag_detail_toggle"), callback_data=f"agency:toggle:{agent_id}"),
                InlineKeyboardButton(_t("ag_detail_delete"), callback_data=f"agency:delete:{agent_id}"),
            ],
            [InlineKeyboardButton(_t("ag_detail_back_list"), callback_data="agency:list:1")],
        ]
    )


# ===============================
#   ورود به منوی نمایندگی‌ها
# ===============================
async def handle_agencies_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ورود به منوی مدیریت نماینده‌ها."""
    agent_db.init_db()
    stats = agent_db.get_global_agency_stats()
    _lg = userbot_db.get_admin_language()
    _t = lambda k, **kw: userbot_db and _i18n_mod.t(k, _lg, **kw)

    text = (
        _t("ag_dash_title") + "\n"
        f"{SEPARATOR}\n\n"
        + _t("ag_dash_agents", n=stats['agents_total']) + "\n"
        + _t("ag_dash_active", a=stats['agents_active'], i=stats['agents_total'] - stats['agents_active']) + "\n\n"
        + _t("ag_dash_customers", n=stats['customers_total']) + "\n"
        + _t("ag_dash_services", n=stats['services_total'], a=stats['services_active']) + "\n\n"
        + _t("ag_dash_sales", v=_fmt_toman(stats['total_sales'])) + "\n"
        + _t("ag_dash_profit", v=_fmt_toman(stats['total_profit'])) + "\n"
        + _t("ag_dash_charges", v=_fmt_toman(stats['total_charges'])) + "\n\n"
        + _t("ag_dash_bots", n=stats['bots_active'])
    )

    # پاک کردن stateهای قبلی
    context.user_data.pop("state", None)
    context.user_data[AGENCY_PAGE_KEY] = 1

    if update.message:
        await update.message.reply_text(text, reply_markup=_main_menu_kb(), parse_mode="HTML")
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=_main_menu_kb(), parse_mode="HTML")
        except BadRequest:
            await update.callback_query.answer()


# ===============================
#   لیست نماینده‌ها
# ===============================
async def send_agents_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 1,
) -> None:
    """نمایش لیست نماینده‌ها با صفحه‌بندی."""
    if page < 1:
        page = 1
    context.user_data[AGENCY_PAGE_KEY] = page

    agents, total = agent_db.get_agents_list(page=page, page_size=AGENCIES_PAGE_SIZE)
    total_pages = max(1, (total + AGENCIES_PAGE_SIZE - 1) // AGENCIES_PAGE_SIZE)

    lines = [
        f"{_t('ag_list_header')}\n"
        f"{SEPARATOR}\n"
        f"{_t('ag_list_page', page=page, pages=total_pages, total=total)}\n"
    ]
    if not agents:
        lines.append("\n" + _t("ag_list_empty"))

    rows: List[List[Any]] = []
    # دکمه‌های هر نماینده
    for a in agents:
        label = _fmt_agent_display(a)
        rows.append([InlineKeyboardButton(label, callback_data=f"agency:view:{a['id']}")])

    # صفحه‌بندی
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(_t("ag_prev"), callback_data=f"agency:list:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(_t("ag_next"), callback_data=f"agency:list:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(_t("ag_add_agent"), callback_data="agency:add")])
    rows.append([InlineKeyboardButton(_t("btn_back"), callback_data="agency:root")])

    kb = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)

    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            await query.answer()
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


# ===============================
#   جزئیات یک نماینده
# ===============================
async def send_agent_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
) -> None:
    """نمایش جزئیات یک نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        query = update.callback_query
        if query:
            await query.answer(_t("ag_agent_not_found"), show_alert=True)
        return

    stats = agent_db.get_agent_stats(agent_id)
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id

    active = _adm_t('ub_lit_3d635dbbe56d') if int(agent.get("is_active", 0)) else _adm_t('ub_lit_4e52e06f8fc2')
    name = _escape(agent.get('full_name')) or "—"
    username = f"@{_escape(agent.get('username'))}" if agent.get('username') else "—"
    phone = _escape(agent.get('phone')) or "—"

    text = (
        f"{_adm_t('ub_lit_77392d1beff3')}{SEPARATOR}{_adm_t('ub_lit_3d9f1e17231e')}{agent.get('telegram_id', '?')}{_adm_t('ub_lit_8c2d7e21a4fe')}{agent['id']}{_adm_t('ub_lit_c92ebfa7431d')}{name}{_adm_t('ub_lit_2a31060a3872')}{username}{_adm_t('ub_lit_f6032c00b389')}{phone}{_adm_t('ub_lit_1166000020cd')}{active}{_adm_t('ub_lit_95db50b3622a')}{_escape(agent.get('created_at'))}\n{SEPARATOR}{_adm_t('ub_lit_c010039df0c2')}{_fmt_toman(stats['wallet_balance'])}{_adm_t('ub_lit_4f5fcb755df8')}{stats['customers_count']}{_adm_t('ub_lit_22bea4a64ad8')}{stats['services_total']}{_adm_t('ub_lit_3c9fea9e8888')}{stats['services_active']}{_adm_t('ub_lit_9c006db3aa5b')}{stats['trials_count']}{_adm_t('ub_lit_70511c55ac9e')}{_fmt_toman(stats['total_sales'])}{_adm_t('ub_lit_f7999257c3fc')}{_fmt_toman(stats['total_profit'])}{_adm_t('ub_lit_f6ac3483a71a')}"
    )

    kb = _agent_detail_kb(agent_id)
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            await query.answer()
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


# ===============================
#   افزودن نماینده (wizard)
# ===============================
async def start_add_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """شروع ویزارد افزودن نماینده — دریافت آیدی تلگرام."""
    context.user_data["state"] = AGENCY_ADD_TELEGRAM
    context.user_data.pop("agency_new_telegram_id", None)
    context.user_data.pop("agency_new_name", None)

    query = update.callback_query
    text = (
        f"{_adm_t('ub_lit_d6884ce9dec6')}{SEPARATOR}{_adm_t('ub_lit_fde8850b23e6')}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(_t("btn_cancel"), callback_data="agency:root")]])

    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            await query.answer()
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def handle_add_agent_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    هندلر پیام متنی در حالت افزودن نماینده.
    خروجی True اگر پیام مصرف شد.
    """
    state = context.user_data.get("state")
    text = (update.message.text or "").strip()

    if state == AGENCY_ADD_TELEGRAM:
        # پارس کردن آیدی تلگرام
        raw = text.replace("،", "").replace(",", "").replace(" ", "")
        try:
            telegram_id = int(raw)
            if telegram_id <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                _adm_t("agency_add_invalid_telegram")
            )
            return True

        # بررسی تکراری نبودن
        existing = agent_db.get_agent_by_telegram_id(telegram_id)
        if existing:
            await update.message.reply_text(
                _adm_t("agency_add_duplicate", id=existing["id"])
            )
            context.user_data.pop("state", None)
            await send_agent_detail(update, context, existing["id"])
            return True

        # تلاش برای دریافت اطلاعات خودکار از تلگرام
        username = ""
        full_name = ""
        try:
            from telegram import Bot
            bot = Bot(token=os.getenv("ADMIN_BOT_TOKEN"))
            user = await bot.get_chat(telegram_id)
            username = user.username or ""
            full_name = user.full_name or ""
        except Exception:
            pass

        context.user_data["agency_new_telegram_id"] = telegram_id
        context.user_data["agency_new_username"] = username
        context.user_data["agency_new_full_name"] = full_name
        context.user_data["state"] = AGENCY_ADD_NAME

        auto_info = ""
        if full_name:
            auto_info += f"{_adm_t('ub_lit_990cb441f734')}{_escape(full_name)}</b>"
        if username:
            auto_info += f"{_adm_t('ub_lit_b45b0c9c8439')}{_escape(username)}</b>"

        await update.message.reply_text(
            _adm_t("agency_add_name_prompt", id=telegram_id, auto_info=auto_info)
        )
        return True

    if state == AGENCY_ADD_NAME:
        full_name = text.strip()
        if full_name == "—":
            full_name = context.user_data.get("agency_new_full_name", "")

        context.user_data["agency_new_full_name"] = full_name
        context.user_data["state"] = AGENCY_ADD_PHONE

        await update.message.reply_text(
            _adm_t("agency_add_phone_prompt", name=_escape(full_name) or "—")
        )
        return True

    if state == AGENCY_ADD_PHONE:
        telegram_id = context.user_data.get("agency_new_telegram_id")
        if not telegram_id:
            context.user_data.pop("state", None)
            return False

        phone = text.strip()
        if phone in {"0", "۰", "بدون", "ندارم", "—"}:
            phone = ""

        full_name = context.user_data.get("agency_new_full_name", "")
        username = context.user_data.get("agency_new_username", "")

        # ساخت نماینده
        agent_id = agent_db.upsert_agent(
            telegram_id=int(telegram_id),
            username=username,
            full_name=full_name,
        )
        if phone:
            agent_db.update_agent(agent_id, {"phone": phone})
        # کیف پول اولیه
        agent_db.get_wallet(agent_id)

        context.user_data.pop("state", None)
        context.user_data.pop("agency_new_telegram_id", None)
        context.user_data.pop("agency_new_full_name", None)
        context.user_data.pop("agency_new_username", None)

        await update.message.reply_text(
            _adm_t("agency_add_saved", id=agent_id, name=_escape(full_name) or "—", username=_escape(username) if username else "—"),
            reply_markup=admin_main_keyboard(),
        )
        await send_agent_detail(update, context, agent_id)
        return True

    return False


# ===============================
#   شارژ کیف پول
# ===============================
async def start_wallet_charge(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """شروع شارژ کیف پول — دریافت مبلغ."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        if update.callback_query:
            await update.callback_query.answer(_t("ag_agent_not_found"), show_alert=True)
        return

    wallet = agent_db.get_wallet(agent_id)
    context.user_data["state"] = AGENCY_WALLET_CHARGE
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id

    text = (
        _adm_t("agency_wallet_charge_prompt", name=_escape(agent.get("full_name")) or agent.get("telegram_id"), balance=_fmt_toman(wallet["balance"]))
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(_t("btn_cancel"), callback_data=f"agency:view:{agent_id}")]])

    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            await query.answer()


async def handle_wallet_charge_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """هندلر پیام متنی مبلغ شارژ."""
    state = context.user_data.get("state")
    if state != AGENCY_WALLET_CHARGE:
        return False

    agent_id = context.user_data.get(AGENCY_VIEWING_ID_KEY)
    if not agent_id:
        context.user_data.pop("state", None)
        return False

    text = (update.message.text or "").strip()
    # نرمال‌سازی ارقام فارسی
    fa_digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    raw = text.translate(fa_digits).replace(",", "").replace("،", "").replace(" ", "")
    try:
        amount = int(raw)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(_t("ag_amount_invalid"))
        return True

    wallet = agent_db.charge_wallet(agent_id, amount, description=_adm_t('ub_lit_a23a4bcdcff5'))
    context.user_data.pop("state", None)

    await update.message.reply_text(
        _adm_t("agency_wallet_charged", amount=_fmt_toman(amount), balance=_fmt_toman(wallet["balance"])),
        reply_markup=admin_main_keyboard(),
        parse_mode="HTML",
    )
    await send_agent_detail(update, context, agent_id)
    return True


# ===============================
#   فعال/غیرفعال + حذف
# ===============================
async def toggle_agent_active(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer(_t("ag_agent_not_found"), show_alert=True)
        return
    new_active = not bool(int(agent.get("is_active", 0)))
    agent_db.set_agent_active(agent_id, new_active)
    await update.callback_query.answer(
        _adm_t("agency_agent_status_changed", status=_adm_t("ag_active") if new_active else _adm_t("ag_inactive"))
    )
    await send_agent_detail(update, context, agent_id)


async def confirm_delete_agent(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """نمایش تأیید حذف نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer(_t("ag_agent_not_found"), show_alert=True)
        return
    stats = agent_db.get_agent_stats(agent_id)
    text = (
        _adm_t("agency_delete_confirm", name=_escape(agent.get("full_name")) or agent.get("telegram_id"), services=stats["services_total"], customers=stats["customers_count"])
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(_t("ag_yes_delete"), callback_data=f"agency:dodelete:{agent_id}"),
                InlineKeyboardButton(_t("ag_no"), callback_data=f"agency:view:{agent_id}"),
            ],
        ]
    )
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def do_delete_agent(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    ok = agent_db.delete_agent(agent_id)
    if ok:
        await update.callback_query.answer(_t("ag_deleted"))
    else:
        await update.callback_query.answer(_t("ag_delete_failed"), show_alert=True)
    await send_agents_list(update, context, page=1)


# ===============================
#   ویرایش نام
# ===============================
async def start_edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer(_t("ag_agent_not_found"), show_alert=True)
        return
    context.user_data["state"] = AGENCY_EDIT_NAME
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    text = (
        f"✏️ <b>{_adm_t('agency_edit_name')}</b>\n"
        f"{SEPARATOR}\n\n"
        + _adm_t("agency_edit_name_prompt", name=_escape(agent.get("full_name")) or "—")
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(_t("btn_cancel"), callback_data=f"agency:view:{agent_id}")]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def handle_edit_name_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = context.user_data.get("state")
    if state != AGENCY_EDIT_NAME:
        return False
    agent_id = context.user_data.get(AGENCY_VIEWING_ID_KEY)
    if not agent_id:
        context.user_data.pop("state", None)
        return False
    name = (update.message.text or "").strip()
    if name == "—":
        name = ""
    agent_db.update_agent(agent_id, {"full_name": name})
    context.user_data.pop("state", None)
    await update.message.reply_text(
        _adm_t("agency_name_updated", name=_escape(name) or "—"),
        reply_markup=admin_main_keyboard(),
        parse_mode="HTML",
    )
    await send_agent_detail(update, context, agent_id)
    return True


# ===============================
#   ویرایش تلفن
# ===============================
async def start_edit_phone(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer(_t("ag_agent_not_found"), show_alert=True)
        return
    context.user_data["state"] = AGENCY_EDIT_PHONE
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    text = (
        f"{_adm_t('ub_lit_8b14ed2aa3fb')}{SEPARATOR}{_adm_t('ub_lit_ef52123a2755')}{_escape(agent.get('phone') or '—')}{_adm_t('ub_lit_abb2369d5134')}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(_t("btn_cancel"), callback_data=f"agency:view:{agent_id}")]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def handle_edit_phone_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = context.user_data.get("state")
    if state != AGENCY_EDIT_PHONE:
        return False
    agent_id = context.user_data.get(AGENCY_VIEWING_ID_KEY)
    if not agent_id:
        context.user_data.pop("state", None)
        return False
    phone = (update.message.text or "").strip()
    if phone in {"0", "۰"}:
        phone = ""
    agent_db.update_agent(agent_id, {"phone": phone})
    context.user_data.pop("state", None)
    await update.message.reply_text(_t("ag_phone_updated"), reply_markup=admin_main_keyboard())
    await send_agent_detail(update, context, agent_id)
    return True


# ===============================
#   مشاهده کیف پول / تراکنش‌ها
# ===============================
async def send_agent_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer(_t("ag_agent_not_found"), show_alert=True)
        return
    wallet = agent_db.get_wallet(agent_id)
    transactions, total = agent_db.get_transactions(agent_id, page=1, page_size=10)

    lines = [
        f"{_adm_t('ub_lit_5bb4883a9530')}",
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n",
        f"{_adm_t('ub_lit_5edf215212b2')}{_fmt_toman(wallet['balance'])}{_adm_t('ub_lit_95dfbec8e0e7')}",
        f"{_adm_t('ub_lit_fc09de89a02b')}{_escape(wallet.get('updated_at'))}\n",
        f"{_adm_t('ub_lit_7890e1df5666')}{total})\n",
    ]
    if not transactions:
        lines.append(_adm_t('ub_lit_3c4f3687f478'))
    else:
        for tx in transactions:
            tx_type = tx.get("tx_type", "")
            amount = int(tx.get("amount", 0))
            sign = "+" if tx_type == "charge" else "-"
            type_fa = {"charge": _adm_t('ub_lit_44253ef99979'), "purchase": _adm_t('ub_lit_149670029304'), "refund": _adm_t('ub_lit_a7976da7948a')}.get(tx_type, tx_type)
            lines.append(
                f"{sign}{_fmt_toman(amount)} · {type_fa} · {_escape(tx.get('description') or '—')[:40]}\n"
                f"   {_escape(tx.get('created_at'))}"
            )

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(_adm_t("ag_detail_charge"), callback_data=f"agency:charge:{agent_id}")],
            [InlineKeyboardButton(_t("btn_back"), callback_data=f"agency:view:{agent_id}")],
        ]
    )
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


# ===============================
#   آمار کلی سیستم
# ===============================
async def send_global_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stats = agent_db.get_global_agency_stats()
    text = (
        f"{_adm_t('ub_lit_1084dd7a2890')}{stats['agents_total']}{_adm_t('ub_lit_29382728386d')}{stats['agents_active']}{_adm_t('ub_lit_502cf8ab4ed7')}{stats['customers_total']}{_adm_t('ub_lit_4ebaeafa5148')}{stats['services_total']}{_adm_t('ub_lit_29382728386d')}{stats['services_active']}{_adm_t('ub_lit_20a786a76f75')}{stats['bots_active']}{_adm_t('ub_lit_a387876e6778')}{_fmt_toman(stats['total_sales'])}{_adm_t('ub_lit_386469d4182c')}{_fmt_toman(stats['total_wholesale'])}{_adm_t('ub_lit_c544262de44a')}{_fmt_toman(stats['total_profit'])}{_adm_t('ub_lit_66b89d22405e')}{_fmt_toman(stats['total_charges'])}{_adm_t('ub_lit_95dfbec8e0e7')}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t("ag_main_menu_back"), callback_data="agency:root")]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


def _parse_agent_payment_meta(payment: Dict[str, Any]) -> Dict[str, Any]:
    try:
        meta = json.loads(str(payment.get("receipt_image") or "{}"))
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _agent_payment_report_text(payment: Dict[str, Any], agent: Dict[str, Any]) -> str:
    meta = _parse_agent_payment_meta(payment)
    name = _escape(agent.get("full_name") or agent.get("username") or payment.get("customer_name") or agent.get("telegram_id"))
    amount = int(payment.get("amount") or meta.get("final_amount") or 0)
    last4 = _escape(payment.get("card_last4") or meta.get("card_last4") or "----")
    ref_id = _escape(payment.get("ref_id") or payment.get("id"))
    return (
        f"{_adm_t('ub_lit_9e83e9206cf2')}{ref_id}{_adm_t('ub_lit_d77136a2d677')}{name}{_adm_t('ub_lit_118f43e620ec')}{_fmt_toman(amount)}{_adm_t('ub_lit_c21051965173')}{last4}</code>"
    )


def _agent_payment_action_kb(payment_id: int, agent_id: int) -> InlineKeyboardMarkup:
    agent = agent_db.get_agent_by_id(agent_id) or {}
    name = _escape(agent.get("full_name") or agent.get("username") or f"{_adm_t('ub_lit_d416fa44016d')}{agent_id}")
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_adm_t("ag_payment_reject"), callback_data=f"agency:payno:{payment_id}"),
            InlineKeyboardButton(_adm_t("ag_payment_approve"), callback_data=f"agency:payok:{payment_id}"),
        ],
        [InlineKeyboardButton(f"{name} 👤", callback_data=f"agency:view:{agent_id}")],
    ])


async def send_pending_agent_payments(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1) -> None:
    payments, total = agentbot_db.get_pending_wallet_charge_payments(page=page, page_size=8)
    total_pages = max(1, (total + 7) // 8)
    lines = [
        _adm_t('ub_lit_7339c054a52b'),
        f"{_adm_t('ub_lit_9c7f18f36e4f')}{total}{_adm_t('ub_lit_a7fe259bdda9')}{page}/{total_pages}\n",
    ]
    rows: List[List[Any]] = []
    if not payments:
        lines.append(_adm_t('ub_lit_ec6e0317d9b3'))
    for p in payments:
        agent = agent_db.get_agent_by_id(int(p.get("agent_id") or 0)) or {}
        name = agent.get("full_name") or agent.get("username") or p.get("customer_name") or f"{_adm_t('ub_lit_d416fa44016d')}{p.get('agent_id')}"
        lines.append(f"• {_escape(name)} | {_fmt_toman(p.get('amount'))}{_adm_t('ub_lit_79e4c1ead1f5')}{p.get('ref_id')}")
        rows.append([InlineKeyboardButton(f"{name} - {_fmt_toman(p.get('amount'))}", callback_data=f"agency:payview:{p['id']}")])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(_adm_t("ag_prev"), callback_data=f"agency:payments:{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(_adm_t("ag_next"), callback_data=f"agency:payments:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(_adm_t("ag_main_menu_back"), callback_data="agency:root")])
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def show_agent_payment_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: int) -> None:
    query = update.callback_query
    payment = agentbot_db.get_payment_by_id(payment_id)
    if not payment:
        await query.answer(_adm_t("agency_payment_not_found"), show_alert=True)
        return
    agent_id = int(payment.get("agent_id") or 0)
    agent = agent_db.get_agent_by_id(agent_id) or {}
    text = _agent_payment_report_text(payment, agent)
    meta = _parse_agent_payment_meta(payment)
    receipt = str(meta.get("receipt_file_id") or "")
    kb = _agent_payment_action_kb(payment_id, agent_id)
    if receipt:
        try:
            await query.message.delete()
        except BadRequest:
            pass
        await context.bot.send_photo(chat_id=query.message.chat_id, photo=receipt, caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")


async def approve_agent_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: int) -> None:
    query = update.callback_query
    payment = agentbot_db.get_payment_by_id(payment_id)
    if not payment:
        await query.answer(_adm_t("agency_payment_not_found"), show_alert=True)
        return
    if str(payment.get("status") or "").strip().lower() not in {"pending", "processing"}:
        await query.answer(_adm_t("agency_payment_already_checked"), show_alert=True)
        return
    agent_id = int(payment.get("agent_id") or 0)
    amount = int(payment.get("amount") or 0)
    result = agentbot_db.approve_wallet_charge_payment_once(payment_id, source="manual")
    if not result.get("ok"):
        await query.answer(
            _adm_t("agency_payment_approval_failed", reason=str(result.get("reason") or "unknown")[:120]),
            show_alert=True,
        )
        return
    wallet = result.get("wallet") or agent_db.get_wallet(agent_id)
    agent = agent_db.get_agent_by_id(agent_id) or {}
    try:
        token = os.getenv("AGENT_BOT_TOKEN", "").strip()
        agent_tg_id = int(agent.get("telegram_id") or 0)
        if result.get("credited_now") and token and agent_tg_id:
            from telegram import Bot
            bot = Bot(token=token)
            await bot.send_message(
                chat_id=agent_tg_id,
                text=_adm_t("agency_payment_customer_approved", amount=_fmt_toman(amount)),
            )
    except Exception as e:
        logger.warning("Failed notifying agent payment approval: %s", e)
    await query.answer(_adm_t("agency_payment_approved"), show_alert=True)
    try:
        await query.edit_message_caption(caption=_adm_t("agency_payment_approved_balance", balance=_fmt_toman(wallet["balance"])), parse_mode="HTML")
    except BadRequest:
        try:
            await query.edit_message_text(_adm_t("agency_payment_approved_balance", balance=_fmt_toman(wallet["balance"])), parse_mode="HTML")
        except BadRequest:
            pass


async def reject_agent_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: int) -> None:
    query = update.callback_query
    payment = agentbot_db.get_payment_by_id(payment_id)
    if not payment:
        await query.answer(_adm_t("agency_payment_not_found"), show_alert=True)
        return
    if str(payment.get("status") or "") != "pending":
        await query.answer(_adm_t("agency_payment_already_checked"), show_alert=True)
        return
    agent_id = int(payment.get("agent_id") or 0)
    amount = int(payment.get("amount") or 0)
    if not agentbot_db.set_payment_status(
        payment_id, agent_id, "rejected", expected_status="pending"
    ):
        await query.answer(_adm_t("agency_payment_concurrent"), show_alert=True)
        return
    agent = agent_db.get_agent_by_id(agent_id) or {}
    try:
        token = os.getenv("AGENT_BOT_TOKEN", "").strip()
        agent_tg_id = int(agent.get("telegram_id") or 0)
        if token and agent_tg_id:
            from telegram import Bot
            bot = Bot(token=token)
            await bot.send_message(chat_id=agent_tg_id, text=_adm_t("agency_payment_customer_rejected", amount=_fmt_toman(amount)))
    except Exception as e:
        logger.warning("Failed notifying agent payment rejection: %s", e)
    await query.answer(_adm_t("agency_payment_rejected"), show_alert=True)
    try:
        await query.edit_message_caption(caption=_adm_t("agency_payment_rejected_short"), parse_mode="HTML")
    except BadRequest:
        try:
            await query.edit_message_text(_adm_t("agency_payment_rejected_short"), parse_mode="HTML")
        except BadRequest:
            pass


# ===============================
#   تعرفه عمده حجم/زمان
# ===============================
async def send_agent_prices(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    page: int = 1,
) -> None:
    """نمایش تعرفه عمده حجم/زمان نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer(_adm_t("ag_agent_not_found"), show_alert=True)
        return

    rates = agent_db.get_wholesale_pricing(agent_id)

    lines = [
        f"{_adm_t('ub_lit_39f45e862d93')}",
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n",
        f"{_adm_t('ub_lit_a59a5803e202')}{_fmt_toman(rates['price_per_gb'])}{_adm_t('ub_lit_95dfbec8e0e7')}",
        f"{_adm_t('ub_lit_4e4e07908b21')}{_fmt_toman(rates['price_per_30_days'])}{_adm_t('ub_lit_fd2dd3161775')}",
        _adm_t('ub_lit_84efebe257e9'),
        _adm_t('ub_lit_76e87afbec2c'),
        _adm_t('ub_lit_a44089e5d231'),
        _adm_t('ub_lit_b325865f566a'),
        _adm_t('ub_lit_a920ffb5dfa1'),
    ]

    kb_rows = [
        [InlineKeyboardButton(_adm_t("agency_wholesale_rates"), callback_data=f"agency:rates:{agent_id}")],
        [InlineKeyboardButton(_adm_t("btn_back"), callback_data=f"agency:view:{agent_id}")],
    ]
    kb = InlineKeyboardMarkup(kb_rows)
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def start_wholesale_rates_input(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """شروع ویزارد مرحله‌ای تعرفه عمده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer(_adm_t("ag_agent_not_found"), show_alert=True)
        return
    rates = agent_db.get_wholesale_pricing(agent_id)
    context.user_data["state"] = AGENCY_SET_WHOLESALE_GB
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    context.user_data.pop("agency_wholesale_price_per_gb", None)
    context.user_data.pop("agency_wholesale_price_per_30_days", None)
    text = (
        f"{_adm_t('ub_lit_e843d5887b86')}{_fmt_toman(rates['price_per_gb'])}{_adm_t('ub_lit_66e2bd36389b')}{_fmt_toman(rates['price_per_30_days'])}{_adm_t('ub_lit_d5e673f53cec')}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t("btn_cancel"), callback_data=f"agency:view:{agent_id}")]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def handle_wholesale_rates_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """دریافت مرحله‌ای قیمت هر گیگ و هر ۳۰ روز."""
    state = context.user_data.get("state")
    if state not in {AGENCY_SET_WHOLESALE_GB, AGENCY_SET_WHOLESALE_DAYS}:
        return False
    agent_id = int(context.user_data.get(AGENCY_VIEWING_ID_KEY) or 0)
    if agent_id <= 0:
        context.user_data.pop("state", None)
        return False
    fa_digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    raw = (update.message.text or "").strip().translate(fa_digits)
    try:
        value = int(raw.replace(",", ""))
    except ValueError:
        await update.message.reply_text(_adm_t("ag_number_only"))
        return True
    if value < 0:
        await update.message.reply_text(_adm_t("ag_nonnegative"))
        return True

    if state == AGENCY_SET_WHOLESALE_GB:
        context.user_data["agency_wholesale_price_per_gb"] = value
        context.user_data["state"] = AGENCY_SET_WHOLESALE_DAYS
        await update.message.reply_text(
            _adm_t("agency_wholesale_days_prompt", value=_fmt_toman(value)),
            parse_mode="HTML",
        )
        return True

    context.user_data["agency_wholesale_price_per_30_days"] = value
    price_per_gb = int(context.user_data.get("agency_wholesale_price_per_gb") or 0)
    price_per_30_days = value
    context.user_data["state"] = "agency:confirm_wholesale_rates"
    text = (
        f"{_adm_t('ub_lit_18408a5e8107')}{_fmt_toman(price_per_gb)}{_adm_t('ub_lit_66e2bd36389b')}{_fmt_toman(price_per_30_days)}{_adm_t('ub_lit_302c98ebc96b')}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_adm_t("agency_rates_save"), callback_data=f"agency:ratesave:{agent_id}")],
        [InlineKeyboardButton(_adm_t("agency_rates_edit"), callback_data=f"agency:rates:{agent_id}")],
        [InlineKeyboardButton(_adm_t("btn_cancel"), callback_data=f"agency:view:{agent_id}")],
    ])
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    return True


async def confirm_wholesale_rates(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """ذخیره تعرفه فقط بعد از تایید نهایی ادمین."""
    query = update.callback_query
    price_per_gb = int(context.user_data.get("agency_wholesale_price_per_gb") or 0)
    price_per_30_days = int(context.user_data.get("agency_wholesale_price_per_30_days") or 0)
    rates = agent_db.set_wholesale_pricing(agent_id, price_per_gb, price_per_30_days)
    context.user_data.pop("state", None)
    context.user_data.pop("agency_wholesale_price_per_gb", None)
    context.user_data.pop("agency_wholesale_price_per_30_days", None)
    text = (
        f"{_adm_t('ub_lit_825f91675b46')}{_fmt_toman(rates['price_per_gb'])}{_adm_t('ub_lit_66e2bd36389b')}{_fmt_toman(rates['price_per_30_days'])}{_adm_t('ub_lit_3f6bcfd76ac7')}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t("agency_back_agent"), callback_data=f"agency:view:{agent_id}")]])
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await query.answer(_adm_t("agency_rates_saved"), show_alert=True)


async def start_add_price_server_select(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """انتخاب سرور برای قیمت‌گذاری جدید."""
    servers = database.get_servers() or []
    if not servers:
        await update.callback_query.answer(_adm_t("agency_no_servers"), show_alert=True)
        return

    rows: List[List[Any]] = []
    for s in servers:
        sid = s["id"]
        stitle = _escape(s.get("title")) or f"server #{sid}"
        rows.append([InlineKeyboardButton(
            stitle,
            callback_data=f"agency:pricesrv:{agent_id}:{sid}",
        )])
    rows.append([InlineKeyboardButton(_adm_t("btn_back"), callback_data=f"agency:prices:{agent_id}:1")])

    kb = InlineKeyboardMarkup(rows)
    text = f"{_adm_t('ub_lit_e8b591d7b40c')}"
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def start_add_price_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    server_id: int,
) -> None:
    """دریافت مشخصات پلن (روز/حجم/قیمت عمده) به‌صورت متن."""
    context.user_data["state"] = AGENCY_SET_WHOLESALE
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    context.user_data["agency_price_server_id"] = server_id

    text = (
        _adm_t('ub_lit_8a53dd8f9fc4')
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t("btn_cancel"), callback_data=f"agency:prices:{agent_id}:1")]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def handle_wholesale_price_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """پارس متن قیمت عمده."""
    state = context.user_data.get("state")
    if state != AGENCY_SET_WHOLESALE:
        return False

    agent_id = context.user_data.get(AGENCY_VIEWING_ID_KEY)
    server_id = context.user_data.get("agency_price_server_id")
    if not agent_id or not server_id:
        context.user_data.pop("state", None)
        return False

    fa_digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    raw = (update.message.text or "").strip().translate(fa_digits)
    parts = raw.replace(",", " ").replace("،", " ").split()
    if len(parts) != 3:
        await update.message.reply_text(
            _adm_t("agency_invalid_price_format")
        )
        return True

    try:
        days = int(parts[0])
        gb = float(parts[1])
        wholesale = int(parts[2])
    except ValueError:
        await update.message.reply_text(_adm_t("agency_invalid_numbers"))
        return True

    if days <= 0 or gb <= 0 or wholesale < 0:
        await update.message.reply_text(_adm_t("agency_positive_values"))
        return True

    plan = agent_db.set_agent_plan(
        agent_id=agent_id,
        server_id=server_id,
        days=days,
        gb=gb,
        wholesale_price=wholesale,
        sale_price=0,
        plan_title=f"{days}{_adm_t('ub_lit_9f224154c8b7')}{gb}GB",
    )

    context.user_data.pop("state", None)
    context.user_data.pop("agency_price_server_id", None)

    await update.message.reply_text(
        _adm_t("agency_wholesale_price_saved", days=days, gb=gb, price=_fmt_toman(wholesale)),
        reply_markup=admin_main_keyboard(),
    )
    await send_agent_prices(update, context, agent_id)
    return True


# ===============================
#   لیست سرویس‌های یک نماینده
# ===============================
async def send_agent_services(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    page: int = 1,
) -> None:
    """نمایش ساده سرویس‌های یک نماینده: آمار + دکمه‌های شماره."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer(_adm_t("ag_agent_not_found"), show_alert=True)
        return

    if page < 1:
        page = 1
    filter_key = f"agency_svc_filter_{agent_id}"
    filter_mode = "all"
    if context and hasattr(context, "user_data") and context.user_data:
        filter_mode = context.user_data.get(filter_key, "all")
    if filter_mode == "active":
        services, total = agent_db.get_active_services_by_agent_paged(agent_id, page=page, page_size=SERVICES_PAGE_SIZE)
    elif filter_mode == "inactive":
        services, total = agent_db.get_inactive_services_by_agent_paged(agent_id, page=page, page_size=SERVICES_PAGE_SIZE)
    else:
        services, total = agent_db.get_services_by_agent(agent_id, page=page, page_size=SERVICES_PAGE_SIZE)
    total_pages = max(1, (total + SERVICES_PAGE_SIZE - 1) // SERVICES_PAGE_SIZE)

    agent_name = str(agent.get("full_name") or "").strip() or str(agent.get("username") or "").strip() or str(agent.get("telegram_id") or "")
    stats = agent_db.get_agent_services_stats(agent_id)

    filter_label = {"active": " 🟢", "inactive": " 🔴"}.get(filter_mode, "")
    blocks: List[str] = [
        f"{_adm_t('ub_lit_cb76db05e9c6')}{filter_label}",
        f"👤 {_escape(agent_name)}",
        f"🟢 {stats['active']}{_adm_t('ub_lit_2b556e077f6f')}{stats['inactive']}{_adm_t('ub_lit_0201a74950fe')}{stats['near_expiry']}{_adm_t('ub_lit_20125174c820')}",
        f"{_adm_t('ub_lit_c50ad34309d2')}{total}{_adm_t('ub_lit_dd53580def27')}",
    ]
    if not services:
        blocks.append("")
        blocks.append(_adm_t('ub_lit_fb99774e3cc9'))

    text = "\n".join(blocks)

    # ── دکمه‌ها ──
    rows_kb: List[List[Any]] = []
    if services:
        chunk: List[Any] = []
        for svc in services:
            sid = int(svc.get("id") or 0)
            is_active = bool(int(svc.get("is_active", 0) or 0))
            chunk.append(InlineKeyboardButton(
                f"{'🟢' if is_active else '🔴'} {sid}",
                callback_data=f"agency:svcview:{agent_id}:{sid}:{page}",
            ))
            if len(chunk) == 4:
                rows_kb.append(chunk)
                chunk = []
        if chunk:
            rows_kb.append(chunk)

    rows_kb.append([
        InlineKeyboardButton(_adm_t("agency_search"), callback_data=f"agency:svcsearch:{agent_id}"),
        InlineKeyboardButton(_adm_t("agency_filter"), callback_data=f"agency:svcfilter:{agent_id}:{page}"),
    ])

    nav: List[Any] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"agency:services:{agent_id}:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="agency:noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"agency:services:{agent_id}:{page + 1}"))
    rows_kb.append(nav)

    rows_kb.append([InlineKeyboardButton(_adm_t("btn_back"), callback_data=f"agency:view:{agent_id}")])

    kb = InlineKeyboardMarkup(rows_kb)
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


# ===============================
#   جزئیات یک سرویس نماینده
# ===============================
async def send_agent_service_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    service_id: int,
    page: int = 1,
) -> None:
    """نمایش جزئیات فشرده یک سرویس نماینده."""
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        await update.callback_query.answer(_adm_t("agency_service_not_found"), show_alert=True)
        return

    name = str(svc.get("name") or _adm_t('ub_lit_b55e872162f6')).strip()
    server_title = str(svc.get("server_title") or "—").strip()
    usage_cur = float(svc.get("usage_current") or 0)
    usage_lim = float(svc.get("usage_limit") or 0)
    days = int(svc.get("days_left") or 0)
    end = str(svc.get("end_date") or "").strip()
    start = str(svc.get("start_date") or "").strip()
    is_active = bool(int(svc.get("is_active", 0) or 0))
    is_trial = bool(int(svc.get("is_trial", 0) or 0))
    wholesale = int(svc.get("wholesale_price") or 0)
    sale = int(svc.get("sale_price") or 0)
    customer_id = int(svc.get("customer_id") or 0)

    status = _adm_t('ub_lit_0c90fe92316c') if is_active else _adm_t('ub_lit_f33c272eee0a')
    cust_name = "—"
    if customer_id:
        try:
            cust = agent_db.get_customer_by_id(customer_id)
            cust_name = (
                str(cust.get("full_name") or "").strip()
                or str(cust.get("username") or "").strip()
                or f"#{customer_id}"
            )
        except Exception:
            cust_name = f"#{customer_id}"

    trial_txt = _adm_t('ub_lit_e4d4509db0ca') if is_trial else ""
    blocks = [
        f"{_adm_t('ub_lit_8f9a55ab3ff0')}{service_id}</b>{trial_txt}",
        SEPARATOR,
        f"{_adm_t('ub_lit_c93f3c57e0ab')}{_escape(name)}",
        f"{_adm_t('ub_lit_aaf70720ab98')}{status}",
        f"{_adm_t('ub_lit_9369bb287a3a')}{_escape(_server_flag_title(server_title))}",
        f"{_adm_t('ub_lit_84e582bd13c2')}{_escape(cust_name)}",
        f"{_adm_t('ub_lit_6edb33274ec9')}{_usage_text(usage_cur, usage_lim)}",
        f"{_adm_t('ub_lit_146f724603ab')}{_fmt_fa_date(end)} ({days}{_adm_t('ub_lit_e20f93b0d19d')}",
    ]
    if start:
        blocks.append(f"{_adm_t('ub_lit_efc1deba10e7')}{_fmt_fa_date(start)}")
    blocks.append(f"{_adm_t('ub_lit_3f2e80adfba3')}{_fmt_toman(wholesale)}{_adm_t('ub_lit_638581aad6a9')}{_fmt_toman(sale)}")

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(_adm_t("btn_back"), callback_data=f"agency:services:{agent_id}:{page}"),
            ]
        ]
    )
    try:
        await update.callback_query.edit_message_text("\n".join(blocks), reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


# ===============================
#   مدیریت ربات‌های مشتری
# ===============================
async def send_agent_bots(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """نمایش ربات‌های مشتری یک نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer(_adm_t("ag_agent_not_found"), show_alert=True)
        return

    bots = agent_db.get_customer_bots(agent_id)
    lines = [
        f"{_adm_t('ub_lit_9b4fad462641')}",
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n",
        f"{_adm_t('ub_lit_efc6704ad4d4')}{len(bots)}</b>\n\n",
    ]
    if not bots:
        lines.append(_adm_t('ub_lit_eb27ea4bc5ce'))
        lines.append(_adm_t('ub_lit_c7e0012c05a6'))
    else:
        for b in bots:
            active = "✅" if int(b.get("is_active", 0)) else "❌"
            uname = b.get("bot_username") or "—"
            lines.append(f"{active} @{_escape(uname)} · 🆔 <code>{b['id']}</code>")

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(_adm_t("btn_back"), callback_data=f"agency:view:{agent_id}")],
        ]
    )
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


# ===============================
#   بازنشانی تست رایگان مشتریان یک نماینده
# ===============================
async def show_reset_trial_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """نمایش پیام تأیید برای بازنشانی همه تست‌های رایگان یک نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer(_adm_t("ag_agent_not_found"), show_alert=True)
        return

    try:
        trial_users = customerbot_db.count_free_trial_users(agent_id)
    except Exception:
        trial_users = 0

    name = _escape(agent.get('full_name')) or str(agent.get('telegram_id'))
    text = (
        f"{_adm_t('ub_lit_f9e3f3beaf03')}{SEPARATOR}{_adm_t('ub_lit_8d5d8fb6558c')}{name}{_adm_t('ub_lit_a462cd6a7ce9')}{agent_id}{_adm_t('ub_lit_064c4039dd87')}{trial_users}{_adm_t('ub_lit_7f9444e882d4')}"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(_adm_t("agency_trial_reset_confirm"), callback_data=f"agency:resettrialdo:{agent_id}"),
                InlineKeyboardButton(_adm_t("agency_cancel"), callback_data=f"agency:view:{agent_id}"),
            ],
        ]
    )
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def do_reset_free_trials(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """اجرای بازنشانی تست‌های رایگان همه مشتریان یک نماینده."""
    try:
        count = customerbot_db.reset_all_free_trials(agent_id)
    except Exception as e:
        logger.exception("Failed to reset free trials for agent %s: %s", agent_id, e)
        await update.callback_query.answer(_adm_t("agency_trial_reset_error"), show_alert=True)
        return

    text = (
        f"{_adm_t('ub_lit_200282c2bffd')}{SEPARATOR}{_adm_t('ub_lit_6ed74b663f86')}{agent_id}{_adm_t('ub_lit_c287fa7ddd5a')}{count}{_adm_t('ub_lit_680beec77a43')}"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(_adm_t("agency_agent_details"), callback_data=f"agency:view:{agent_id}")],
        ]
    )
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


# ===============================
#   توکن ربات نماینده (Agent Bot Token)
# ===============================
def _update_env_file(token: str) -> bool:
    """
    بروزرسانی فایل .env با توکن جدید ربات نماینده.
    خروجی: True اگر موفق باشد.
    """
    try:
        from pathlib import Path
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if not env_path.exists():
            # اگر فایل .env وجود نداشت، از .env.example بساز
            example_path = Path(__file__).resolve().parents[1] / ".env.example"
            if example_path.exists():
                env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                env_path.write_text("", encoding="utf-8")

        lines = env_path.read_text(encoding="utf-8").splitlines()
        found = False
        new_lines = []
        for line in lines:
            if line.strip().startswith("AGENT_BOT_TOKEN="):
                new_lines.append(f"AGENT_BOT_TOKEN={token}")
                found = True
            else:
                new_lines.append(line)

        if not found:
            new_lines.append(f"AGENT_BOT_TOKEN={token}")

        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        logger.info("Agent bot token updated in .env")
        return True
    except Exception as e:
        logger.error("Failed to update .env file: %s", e)
        return False


async def send_agent_token_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش منوی تنظیمات توکن ربات نماینده."""
    settings = userbot_db.get_agent_bot_settings()
    token = settings.get("agent_bot_token", "")

    if token:
        masked = token[:8] + "..." + token[-6:] if len(token) > 14 else "••••••••"
        text = (
            f"{_adm_t('ub_lit_4b9f9c9a8546')}{SEPARATOR}{_adm_t('ub_lit_dcec50bb1d99')}{masked}{_adm_t('ub_lit_e83e786c8cb3')}"
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(_adm_t("agency_token_change"), callback_data="agency:agenttoken:change")],
                [InlineKeyboardButton(_adm_t("agency_bot_restart"), callback_data="agency:agenttoken:restart")],
                [InlineKeyboardButton(_adm_t("btn_back"), callback_data="agency:root")],
            ]
        )
    else:
        text = (
            f"{_adm_t('ub_lit_4b9f9c9a8546')}{SEPARATOR}{_adm_t('ub_lit_67e16bf6fd0a')}"
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(_adm_t("agency_add_token"), callback_data="agency:agenttoken:change")],
                [InlineKeyboardButton(_adm_t("btn_back"), callback_data="agency:root")],
            ]
        )

    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
            except Exception:
                pass
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def start_set_agent_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """شروع ویزارد تنظیم توکن ربات نماینده."""
    context.user_data["state"] = AGENCY_SET_AGENT_TOKEN

    text = (
        f"{_adm_t('ub_lit_ecc996f20326')}{SEPARATOR}{_adm_t('ub_lit_adff40d55672')}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t("btn_cancel"), callback_data="agency:agenttoken")]])

    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
            except Exception:
                pass
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


def _restart_agent_bot() -> bool:
    """
    ریستارت خودکار AgentBot.
    ابتدا پروسه فعلی رو می‌کشه، بعد دوباره اجرا می‌کنه.
    خروجی: True اگر موفق باشد.
    """
    try:
        root_dir = Path(__file__).resolve().parents[1]
        venv_python = root_dir / "venv" / "bin" / "python"
        agent_main = root_dir / "AgentBot" / "main.py"
        log_file = root_dir / "logs" / "agent.log"

        # پیدا کردن و کشتن پروسه فعلی AgentBot
        try:
            result = subprocess.run(
                ["pgrep", "-f", "AgentBot/main.py"],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                pids = result.stdout.strip().split("\n")
                for pid in pids:
                    try:
                        os.kill(int(pid.strip()), signal.SIGTERM)
                    except (ProcessLookupError, ValueError):
                        pass
                logger.info("Killed old AgentBot processes: %s", pids)
                import time
                time.sleep(2)
        except Exception as e:
            logger.warning("Could not kill old AgentBot: %s", e)

        # اجرای مجدد AgentBot
        cmd = f"cd {root_dir} && {venv_python} {agent_main} >> {log_file} 2>&1 &"
        subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("AgentBot restarted successfully")
        return True
    except Exception as e:
        logger.error("Failed to restart AgentBot: %s", e)
        return False


async def restart_agent_bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ریستارت ربات نماینده از طریق دکمه."""
    query = update.callback_query
    await query.answer()

    msg = await query.edit_message_text(
        _adm_t("agency_restart_progress"),
        parse_mode="HTML",
    )

    success = _restart_agent_bot()

    text = (
        _adm_t('ub_lit_233cf6dbed47')
        if success
        else _adm_t('ub_lit_85fb60563229')
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(_adm_t("agency_token_settings_back"), callback_data="agency:agenttoken")],
            [InlineKeyboardButton(_adm_t("ag_main_menu_back"), callback_data="agency:exit")],
        ]
    )
    await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")


async def handle_set_agent_token_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    هندلر پیام متنی در حالت تنظیم توکن ربات نماینده.
    خروجی True اگر پیام مصرف شد.
    """
    state = context.user_data.get("state")
    if state != AGENCY_SET_AGENT_TOKEN:
        return False

    text = (update.message.text or "").strip()

    # بررسی فرمت توکن (باید شامل : باشد و حداقل ۳۰ کاراکتر)
    if ":" not in text or len(text) < 30:
        await update.message.reply_text(
            f"{_adm_t('ub_lit_cdc41462ce22')}{SEPARATOR}{_adm_t('ub_lit_bd418c7890ad')}",
            parse_mode="HTML",
        )
        return True

    # ذخیره در دیتابیس
    userbot_db.set_agent_bot_settings({"agent_bot_token": text})

    # بروزرسانی فایل .env
    env_updated = _update_env_file(text)

    context.user_data.pop("state", None)

    # ریستارت خودکار AgentBot
    agent_restarted = _restart_agent_bot()

    masked = text[:8] + "..." + text[-6:] if len(text) > 14 else "••••••••"
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_adm_t("btn_back"), callback_data="agency:root")]]
    )
    msg = (
        f"{_adm_t('ub_lit_6a33859ebc7b')}{SEPARATOR}{_adm_t('ub_lit_54592c266820')}{masked}</code>\n\n"
    )
    if env_updated:
        msg += _adm_t('ub_lit_f246d98c3952')
    else:
        msg += _adm_t('ub_lit_3c891a0fb8f2')

    if agent_restarted:
        msg += _adm_t('ub_lit_379e341cfea9')
    else:
        msg += _adm_t('ub_lit_62eebb084750')

    await update.message.reply_text(msg, reply_markup=kb, parse_mode="HTML")
    return True


# ===============================
#   سرویس جدید / جستجو / فیلتر سرویس‌های نماینده
# ===============================
async def send_agent_svc_add_help(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """راهنمای ساخت سرویس جدید — ساخت از طریق ربات نماینده انجام می‌شود."""
    agent = agent_db.get_agent_by_id(agent_id)
    name = _escape(agent.get('full_name')) if agent else f"#{agent_id}"
    text = (
        f"{_adm_t('ub_lit_58ac18a28b6d')}{SEPARATOR}{_adm_t('ub_lit_1241b3ec98ee')}{name}{_adm_t('ub_lit_b61930a1ffec')}"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(_adm_t("agency_pricing"), callback_data=f"agency:prices:{agent_id}:1"),
                InlineKeyboardButton(_adm_t("btn_back"), callback_data=f"agency:services:{agent_id}:1"),
            ]
        ]
    )
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def start_agent_service_search(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """شروع ویزارد جستجوی نام سرویس."""
    context.user_data["state"] = AGENCY_SVC_SEARCH
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    context.user_data.pop(f"agency_svc_filter_{agent_id}", None)
    text = (
        f"{_adm_t('ub_lit_ceebd4cc7a9a')}{SEPARATOR}{_adm_t('ub_lit_796f058d6921')}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(_adm_t("btn_cancel"), callback_data=f"agency:services:{agent_id}:1")]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def handle_agent_service_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """پردازش متن جستجو و نمایش نتایج."""
    text = (update.message.text or "").strip()
    agent_id = int(context.user_data.get(AGENCY_VIEWING_ID_KEY) or 0)
    context.user_data.pop("state", None)
    if agent_id <= 0 or not text:
        await update.message.reply_text(_adm_t("agency_search_cancelled"))
        return True

    services, total = agent_db.search_services_by_name(agent_id, text, page=1, page_size=SERVICES_PAGE_SIZE)
    total_pages = max(1, (total + SERVICES_PAGE_SIZE - 1) // SERVICES_PAGE_SIZE)
    agent = agent_db.get_agent_by_id(agent_id) or {}

    blocks = [
        f"{_adm_t('ub_lit_a407bea472bd')}{_escape(text)}»</b>",
        f"👤 {_escape(agent.get('full_name') or '')}",
        f"📊 <b>{total}{_adm_t('ub_lit_ad6448a480af')}",
        "",
    ]
    if not services:
        blocks.append(_adm_t('ub_lit_19686e2dfcce'))
        blocks.append("")
        blocks.append(_adm_t('ub_lit_edc5f934800b'))
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(_adm_t("btn_back"), callback_data=f"agency:services:{agent_id}:1")]]
        )
        try:
            await update.message.reply_text("\n".join(blocks), reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            pass
        return True

    kb_rows: List[List[Any]] = []
    for svc in services[:8]:
        sid = int(svc.get("id") or 0)
        nm = str(svc.get("name") or _adm_t('ub_lit_494367d01a08')).strip()
        kb_rows.append([InlineKeyboardButton(f"🔹 {sid} · {nm[:20]}", callback_data=f"agency:svcview:{agent_id}:{sid}:1")])
    kb_rows.append([InlineKeyboardButton(_adm_t("btn_back"), callback_data=f"agency:services:{agent_id}:1")])
    kb = InlineKeyboardMarkup(kb_rows)
    try:
        await update.message.reply_text("\n".join(blocks), reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        pass
    return True


async def cycle_agent_service_filter(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, page: int = 1) -> None:
    """چرخش فیلتر سرویس‌ها: همه → فعال → غیرفعال."""
    key = f"agency_svc_filter_{agent_id}"
    current = context.user_data.get(key, "all")
    nxt = {"all": "active", "active": "inactive", "inactive": "all"}.get(current, "all")
    context.user_data[key] = nxt
    await send_agent_services(update, context, agent_id, page=page)


# ===============================
#   هندلر اصلی inline (callback router)
# ===============================
async def handle_agencies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    هندلر مرکزی تمام callback های agency:*.
    در AdminBot/servers.py از این تابع استفاده می‌شود.
    """
    query = update.callback_query
    if not query:
        return

    data = (query.data or "").strip()
    if not data.startswith("agency:"):
        return

    await query.answer()
    parts = data.split(":")

    action = parts[1] if len(parts) > 1 else ""

    if action == "noop":
        return

    if action == "root":
        await handle_agencies_entry(update, context)
        return

    if action == "exit":
        try:
            await query.message.delete()
        except BadRequest:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=_adm_t("back_to_main_menu"),
            reply_markup=admin_main_keyboard(),
        )
        return

    if action == "stats":
        await send_global_stats(update, context)
        return

    if action == "event" and len(parts) > 2:
        sub = parts[2]
        if sub == "toggle":
            ev = userbot_db.toggle_agency_event_enabled()
            state_txt = _adm_t('ub_lit_2ecce95c9038') if ev.get("event_channel_enabled") else _adm_t('ub_lit_fcf97591e49b')
            if ev.get("event_channel_enabled") and not str(ev.get("event_channel_id") or "").strip():
                state_txt += _adm_t('ub_lit_2714b881cee2')
            await query.answer(state_txt, show_alert=True)
            await handle_agencies_entry(update, context)
            return
        if sub == "set":
            context.user_data["state"] = AGENCY_EVENT_CHANNEL_STATE
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=_adm_t("agency_event_channel_prompt"),
                reply_markup=admin_main_keyboard(),
            )
            return
        if sub == "status":
            ev = userbot_db.get_agency_event_settings()
            status_txt = _adm_t('ub_lit_f1bc469f39f7') if ev.get("event_channel_enabled") else _adm_t('ub_lit_fcc2f9a81e87')
            channel = str(ev.get("event_channel_id") or _adm_t('ub_lit_ce1bb87c0d4e'))
            await query.answer(
                f"{_adm_t('ub_lit_b368ed422a72')}{status_txt}{_adm_t('ub_lit_f51733a0dd1f')}{channel}",
                show_alert=True,
            )
            return

    if action == "list":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        await send_agents_list(update, context, page=page)
        return

    if action == "payments":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        await send_pending_agent_payments(update, context, page=page)
        return

    if action == "payview":
        payment_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await show_agent_payment_detail(update, context, payment_id)
        return

    if action == "payok":
        payment_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await approve_agent_payment(update, context, payment_id)
        return

    if action == "payno":
        payment_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await reject_agent_payment(update, context, payment_id)
        return

    if action == "add":
        await start_add_agent(update, context)
        return

    # توکن ربات نماینده
    if action == "agenttoken":
        sub = parts[2] if len(parts) > 2 else ""
        if sub == "change":
            await start_set_agent_token(update, context)
        elif sub == "restart":
            await restart_agent_bot_callback(update, context)
        else:
            await send_agent_token_menu(update, context)
        return

    # اکشن‌هایی که نیاز به agent_id دارند
    if len(parts) < 3:
        return
    agent_id = int(parts[2]) if parts[2].lstrip("-").isdigit() else 0
    if agent_id <= 0:
        return

    if action == "view":
        await send_agent_detail(update, context, agent_id)
        return

    if action == "charge":
        await start_wallet_charge(update, context, agent_id)
        return

    if action == "wallet":
        await send_agent_wallet(update, context, agent_id)
        return

    if action == "toggle":
        await toggle_agent_active(update, context, agent_id)
        return

    if action == "delete":
        await confirm_delete_agent(update, context, agent_id)
        return

    if action == "dodelete":
        await do_delete_agent(update, context, agent_id)
        return

    if action == "editname":
        await start_edit_name(update, context, agent_id)
        return

    if action == "editphone":
        await start_edit_phone(update, context, agent_id)
        return

    if action == "services":
        context.user_data.pop("state", None)
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        await send_agent_services(update, context, agent_id, page=page)
        return

    if action == "svcadd":
        context.user_data.pop("state", None)
        await send_agent_svc_add_help(update, context, agent_id)
        return

    if action == "svcsearch":
        await start_agent_service_search(update, context, agent_id)
        return

    if action == "svcfilter":
        context.user_data.pop("state", None)
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        await cycle_agent_service_filter(update, context, agent_id, page=page)
        return

    if action == "svcview":
        context.user_data.pop("state", None)
        service_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 1
        if service_id <= 0:
            await query.answer(_adm_t("agency_service_invalid"), show_alert=True)
            return
        await send_agent_service_detail(update, context, agent_id, service_id, page=page)
        return

    if action == "prices":
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        await send_agent_prices(update, context, agent_id, page=page)
        return

    if action == "rates":
        await start_wholesale_rates_input(update, context, agent_id)
        return

    if action == "ratesave":
        await confirm_wholesale_rates(update, context, agent_id)
        return

    if action == "addprice":
        await start_add_price_server_select(update, context, agent_id)
        return

    if action == "pricesrv":
        server_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        if server_id <= 0:
            await query.answer(_adm_t("agency_server_invalid"), show_alert=True)
            return
        await start_add_price_input(update, context, agent_id, server_id)
        return

    if action == "bots":
        await send_agent_bots(update, context, agent_id)
        return

    if action == "resettrial":
        await show_reset_trial_confirm(update, context, agent_id)
        return

    if action == "resettrialdo":
        await do_reset_free_trials(update, context, agent_id)
        return


# ===============================
#   هندلر پیام‌های متنی agency
# ===============================
async def handle_agencies_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    هندلر پیام‌های متنی مربوط به ویزارد‌های agency.
    خروجی True اگر پیام مصرف شد.
    """
    state = context.user_data.get("state") or ""

    if not state.startswith("agency:"):
        return False

    # لغو
    text = (update.message.text or "").strip()
    if text in {"/cancel", "لغو", "لغو❌", "❌لغو"}:
        context.user_data.pop("state", None)
        await update.message.reply_text(_adm_t("operation_cancelled"), reply_markup=admin_main_keyboard())
        return True

    if state in {AGENCY_ADD_TELEGRAM, AGENCY_ADD_PHONE, AGENCY_ADD_NAME}:
        return await handle_add_agent_text(update, context)

    if state == AGENCY_WALLET_CHARGE:
        return await handle_wallet_charge_text(update, context)

    if state == AGENCY_EDIT_PHONE:
        return await handle_edit_phone_text(update, context)

    if state == AGENCY_EDIT_NAME:
        return await handle_edit_name_text(update, context)

    if state == AGENCY_SET_WHOLESALE:
        return await handle_wholesale_price_text(update, context)

    if state in {AGENCY_SET_WHOLESALE_GB, AGENCY_SET_WHOLESALE_DAYS}:
        return await handle_wholesale_rates_text(update, context)

    if state == AGENCY_SVC_SEARCH:
        return await handle_agent_service_search_text(update, context)

    if state == AGENCY_EVENT_CHANNEL_STATE:
        context.user_data.pop("state", None)
        channel_target = ""
        channel_title = ""

        # 1) فوروارد پیام از کانال
        try:
            fchat = getattr(update.message, "forward_from_chat", None)
            if fchat and str(getattr(fchat, "type", "")) in {"channel", "supergroup"}:
                channel_target = str(getattr(fchat, "id", "") or "").strip()
                channel_title = str(getattr(fchat, "title", "") or "").strip()
        except Exception:
            pass

        # 2) PTB v20+: forward_origin
        if not channel_target:
            try:
                origin = getattr(update.message, "forward_origin", None)
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
            await update.message.reply_text(
                _adm_t("agency_event_channel_invalid"),
                reply_markup=admin_main_keyboard(),
            )
            return True

        try:
            userbot_db.set_agency_event_settings({
                "event_channel_id": channel_target,
                "event_channel_enabled": userbot_db.get_agency_event_settings().get("event_channel_enabled", False),
            })
        except Exception as e:
            await update.message.reply_text(f"{_adm_t('ub_lit_04623a2f0eec')}{e}", reply_markup=admin_main_keyboard())
            return True

        title_part = f" ({channel_title})" if channel_title else ""
        await update.message.reply_text(
            f"{_adm_t('ub_lit_c43e4b41cc11')}{channel_target}{title_part}",
            reply_markup=admin_main_keyboard(),
        )
        return True

    if state == AGENCY_SET_AGENT_TOKEN:
        return await handle_set_agent_token_text(update, context)

    return False
