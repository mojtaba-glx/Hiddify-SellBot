# AdminBot/agencies.py
# مدیریت سیستم نمایندگی (Agency/Reseller) در پنل ادمین

import logging
import subprocess
import signal
import os
import json
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
from AgentBot import database as agentbot_db
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
        ident = f"نماینده #{agent_id}"

    return f"{active} {ident}"


def _main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ افزودن نماینده", callback_data="agency:add")],
            [
                InlineKeyboardButton("📋 لیست نماینده‌ها", callback_data="agency:list:1"),
                InlineKeyboardButton("⏳ شارژهای در انتظار", callback_data="agency:payments:1"),
            ],
            [
                InlineKeyboardButton("📊 آمار کلی", callback_data="agency:stats"),
                InlineKeyboardButton("⚙️ توکن ربات نماینده", callback_data="agency:agenttoken"),
            ],
            [InlineKeyboardButton("🔙 منوی اصلی", callback_data="agency:exit")],
        ]
    )


def _agent_detail_kb(agent_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💰 شارژ کیف پول", callback_data=f"agency:charge:{agent_id}"),
                InlineKeyboardButton("💳 کیف پول", callback_data=f"agency:wallet:{agent_id}"),
            ],
            [InlineKeyboardButton("📦 سرویس‌ها", callback_data=f"agency:services:{agent_id}:1")],
            [InlineKeyboardButton("💵 تعرفه عمده", callback_data=f"agency:prices:{agent_id}:1")],
            [InlineKeyboardButton("🤖 ربات مشتری", callback_data=f"agency:bots:{agent_id}")],
            [InlineKeyboardButton("✏️ ویرایش نام", callback_data=f"agency:editname:{agent_id}")],
            [InlineKeyboardButton("✏️ ویرایش تلفن", callback_data=f"agency:editphone:{agent_id}")],
            [
                InlineKeyboardButton("🔁 فعال/غیرفعال", callback_data=f"agency:toggle:{agent_id}"),
                InlineKeyboardButton("🗑 حذف", callback_data=f"agency:delete:{agent_id}"),
            ],
            [InlineKeyboardButton("🔙 لیست نماینده‌ها", callback_data="agency:list:1")],
        ]
    )


# ===============================
#   ورود به منوی نمایندگی‌ها
# ===============================
async def handle_agencies_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ورود به منوی مدیریت نماینده‌ها."""
    agent_db.init_db()
    stats = agent_db.get_global_agency_stats()

    text = (
        "🏢 <b>داشبورد مدیریت نماینده‌ها</b>\n"
        f"{SEPARATOR}\n\n"
        f"👥 تعداد کل نمایندگان: <b>{stats['agents_total']}</b>\n"
        f"✅ فعال: <b>{stats['agents_active']}</b> | ❌ غیرفعال: <b>{stats['agents_total'] - stats['agents_active']}</b>\n\n"
        f"👤 مشتریان کل: <b>{stats['customers_total']}</b>\n"
        f"📦 سرویس‌ها: <b>{stats['services_total']}</b> (فعال: {stats['services_active']})\n\n"
        f"💰 فروش کل: <b>{_fmt_toman(stats['total_sales'])}</b> تومان\n"
        f"🏷 سود سیستم: <b>{_fmt_toman(stats['total_profit'])}</b> تومان\n"
        f"📥 شارژ کل: <b>{_fmt_toman(stats['total_charges'])}</b> تومان\n\n"
        f"🤖 ربات‌های فعال: <b>{stats['bots_active']}</b>"
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
        f"📋 <b>لیست نماینده‌ها</b>\n"
        f"{SEPARATOR}\n"
        f"صفحه {page} از {total_pages} | مجموع: {total} نفر\n"
    ]
    if not agents:
        lines.append("\nهیچ نماینده‌ای ثبت نشده است.\nبرای افزودن، روی «➕ افزودن نماینده» بزنید.")

    rows: List[List[Any]] = []
    # دکمه‌های هر نماینده
    for a in agents:
        label = _fmt_agent_display(a)
        rows.append([InlineKeyboardButton(label, callback_data=f"agency:view:{a['id']}")])

    # صفحه‌بندی
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"agency:list:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"agency:list:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("➕ افزودن نماینده", callback_data="agency:add")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="agency:root")])

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
            await query.answer("نماینده پیدا نشد.", show_alert=True)
        return

    stats = agent_db.get_agent_stats(agent_id)
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id

    active = "فعال ✅" if int(agent.get("is_active", 0)) else "غیرفعال ❌"
    name = _escape(agent.get('full_name')) or "—"
    username = f"@{_escape(agent.get('username'))}" if agent.get('username') else "—"
    phone = _escape(agent.get('phone')) or "—"

    text = (
        f"👤 <b>جزئیات نماینده</b>\n"
        f"{SEPARATOR}\n"
        f"📱 <b>تلگرام:</b> <code>{agent.get('telegram_id', '?')}</code>\n"
        f"🔢 <b>شناسه:</b> <code>{agent['id']}</code>\n"
        f"👤 <b>نام:</b> {name}\n"
        f"🔗 <b>یوزرنیم:</b> {username}\n"
        f"📞 <b>تلفن:</b> {phone}\n"
        f"📍 <b>وضعیت:</b> {active}\n"
        f"🕒 <b>عضویت:</b> {_escape(agent.get('created_at'))}\n"
        f"{SEPARATOR}\n"
        f"💰 <b>کیف پول:</b> {_fmt_toman(stats['wallet_balance'])} تومان\n"
        f"👥 <b>مشتریان:</b> {stats['customers_count']}\n"
        f"📦 <b>سرویس‌ها:</b> {stats['services_total']} (فعال: {stats['services_active']})\n"
        f"🔥 <b>ترایال:</b> {stats['trials_count']}\n"
        f"💵 <b>فروش کل:</b> {_fmt_toman(stats['total_sales'])} تومان\n"
        f"🏷 <b>سود نماینده:</b> {_fmt_toman(stats['total_profit'])} تومان"
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
        "➕ <b>افزودن نماینده جدید</b>\n"
        f"{SEPARATOR}\n\n"
        "مرحله ۱ از ۳\n\n"
        "لطفاً <b>آیدی عددی تلگرام</b> کاربر را ارسال کنید.\n\n"
        "💡 برای پیدا کردن آیدی، کاربر می‌تواند به @userinfobot پیام بدهد.\n\n"
        "یا کاربر ابتدا به ربات نمایندگی /start بزند تا شناسایی شود.\n\n"
        "برای لغو /cancel را بفرستید."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="agency:root")]])

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
                "❌ آیدی تلگرام نامعتبر است. لطفاً عدد صحیح ارسال کنید."
            )
            return True

        # بررسی تکراری نبودن
        existing = agent_db.get_agent_by_telegram_id(telegram_id)
        if existing:
            await update.message.reply_text(
                f"⚠️ این کاربر قبلاً به‌عنوان نماینده ثبت شده است (شناسه {existing['id']})."
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
            auto_info += f"\n👤 نام خودکار: <b>{_escape(full_name)}</b>"
        if username:
            auto_info += f"\n🔗 یوزرنیم خودکار: <b>@{_escape(username)}</b>"

        await update.message.reply_text(
            f"✅ آیدی تلگرام ثبت شد: <code>{telegram_id}</code>{auto_info}\n\n"
            "مرحله ۲ از ۳\n\n"
            "لطفاً <b>نام کامل</b> نماینده را ارسال کنید.\n"
            f"یا برای استفاده از نام خودکار «—» بفرستید.\n\n"
            "برای لغو /cancel را بفرستید."
        )
        return True

    if state == AGENCY_ADD_NAME:
        full_name = text.strip()
        if full_name == "—":
            full_name = context.user_data.get("agency_new_full_name", "")

        context.user_data["agency_new_full_name"] = full_name
        context.user_data["state"] = AGENCY_ADD_PHONE

        await update.message.reply_text(
            f"✅ نام ثبت شد: <b>{_escape(full_name) or '—'}</b>\n\n"
            "مرحله ۳ از ۳\n\n"
            "لطفاً <b>شماره تلفن</b> نماینده را ارسال کنید.\n"
            "برای رد کردن «—» بفرستید.\n\n"
            "برای لغو /cancel را بفرستید."
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
            f"✅ <b>نماینده با موفقیت ثبت شد!</b>\n"
            f"{SEPARATOR}\n\n"
            f"🆔 شناسه: <code>{agent_id}</code>\n"
            f"👤 نام: {_escape(full_name) or '—'}\n"
            f"🔗 یوزرنیم: @{_escape(username) if username else '—'}\n\n"
            "نماینده باید به ربات نمایندگی (AgentBot) بزند /start تا اطلاعات‌اش کامل شود.",
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
            await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return

    wallet = agent_db.get_wallet(agent_id)
    context.user_data["state"] = AGENCY_WALLET_CHARGE
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id

    text = (
        f"💰 <b>شارژ کیف پول</b>\n\n"
        f"👤 نماینده: {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n"
        f"💳 موجودی فعلی: <b>{_fmt_toman(wallet['balance'])}</b> تومان\n\n"
        "مبلغ شارژ (به تومان) را ارسال کنید.\n"
        "برای لغو /cancel را بفرستید."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"agency:view:{agent_id}")]])

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
        await update.message.reply_text("❌ مبلغ نامعتبر است. لطفاً عدد صحیح مثبت ارسال کنید.")
        return True

    wallet = agent_db.charge_wallet(agent_id, amount, description="شارژ توسط ادمین")
    context.user_data.pop("state", None)

    await update.message.reply_text(
        f"✅ کیف پول شارژ شد!\n"
        f"💰 مبلغ: <b>{_fmt_toman(amount)}</b> تومان\n"
        f"💳 موجودی جدید: <b>{_fmt_toman(wallet['balance'])}</b> تومان",
        reply_markup=admin_main_keyboard(),
    )
    await send_agent_detail(update, context, agent_id)
    return True


# ===============================
#   فعال/غیرفعال + حذف
# ===============================
async def toggle_agent_active(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    new_active = not bool(int(agent.get("is_active", 0)))
    agent_db.set_agent_active(agent_id, new_active)
    await update.callback_query.answer(
        f"نماینده {'فعال شد ✅' if new_active else 'غیرفعال شد ❌'}"
    )
    await send_agent_detail(update, context, agent_id)


async def confirm_delete_agent(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """نمایش تأیید حذف نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    stats = agent_db.get_agent_stats(agent_id)
    text = (
        f"⚠️ <b>تأیید حذف نماینده</b>\n\n"
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n\n"
        f"📦 سرویس‌ها: {stats['services_total']}\n"
        f"👥 مشتریان: {stats['customers_count']}\n\n"
        "❗️ با حذف، تمام داده‌های این نماینده (مشتریان، سرویس‌ها، کیف پول و...) پاک خواهد شد.\n"
        "آیا مطمئن هستید؟"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 بله، حذف کن", callback_data=f"agency:dodelete:{agent_id}"),
                InlineKeyboardButton("❌ خیر", callback_data=f"agency:view:{agent_id}"),
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
        await update.callback_query.answer("نماینده حذف شد.")
    else:
        await update.callback_query.answer("حذف ناموفق بود.", show_alert=True)
    await send_agents_list(update, context, page=1)


# ===============================
#   ویرایش نام
# ===============================
async def start_edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    context.user_data["state"] = AGENCY_EDIT_NAME
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    text = (
        f"✏️ <b>ویرایش نام نماینده</b>\n"
        f"{SEPARATOR}\n\n"
        f"نام فعلی: <b>{_escape(agent.get('full_name')) or '—'}</b>\n\n"
        "نام جدید را ارسال کنید (برای خالی کردن «—» بفرستید)."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"agency:view:{agent_id}")]])
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
        f"✅ نام نماینده بروزرسانی شد: <b>{_escape(name) or '—'}</b>",
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
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    context.user_data["state"] = AGENCY_EDIT_PHONE
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    text = (
        f"✏️ <b>ویرایش تلفن</b>\n"
        f"{SEPARATOR}\n\n"
        f"تلفن فعلی: <b>{_escape(agent.get('phone') or '—')}</b>\n\n"
        "تلفن جدید را ارسال کنید (برای خالی کردن «—» بفرستید)."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"agency:view:{agent_id}")]])
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
    await update.message.reply_text("✅ تلفن بروزرسانی شد.", reply_markup=admin_main_keyboard())
    await send_agent_detail(update, context, agent_id)
    return True


# ===============================
#   مشاهده کیف پول / تراکنش‌ها
# ===============================
async def send_agent_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    wallet = agent_db.get_wallet(agent_id)
    transactions, total = agent_db.get_transactions(agent_id, page=1, page_size=10)

    lines = [
        f"💳 <b>کیف پول نماینده</b>\n\n",
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n",
        f"💰 موجودی: <b>{_fmt_toman(wallet['balance'])}</b> تومان\n",
        f"🕒 بروزرسانی: {_escape(wallet.get('updated_at'))}\n",
        f"\n━━━━━━━━━━━━━\n📜 <b>آخرین تراکنش‌ها</b> ({total})\n",
    ]
    if not transactions:
        lines.append("تراکنشی ثبت نشده است.")
    else:
        for tx in transactions:
            tx_type = tx.get("tx_type", "")
            amount = int(tx.get("amount", 0))
            sign = "+" if tx_type == "charge" else "-"
            type_fa = {"charge": "شارژ", "purchase": "خرید", "refund": "بازگشت"}.get(tx_type, tx_type)
            lines.append(
                f"{sign}{_fmt_toman(amount)} · {type_fa} · {_escape(tx.get('description') or '—')[:40]}\n"
                f"   {_escape(tx.get('created_at'))}"
            )

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💰 شارژ", callback_data=f"agency:charge:{agent_id}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"agency:view:{agent_id}")],
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
        "📊 <b>آمار کلی سیستم نمایندگی</b>\n\n"
        f"👥 نماینده‌ها: <b>{stats['agents_total']}</b> (فعال: {stats['agents_active']})\n"
        f"👤 مشتریان: <b>{stats['customers_total']}</b>\n"
        f"📦 سرویس‌ها: <b>{stats['services_total']}</b> (فعال: {stats['services_active']})\n"
        f"🤖 ربات فعال: <b>{stats['bots_active']}</b>\n"
        f"\n━━━━━━━━━━━━━\n"
        f"💰 فروش کل: <b>{_fmt_toman(stats['total_sales'])}</b> تومان\n"
        f"🏷 هزینه عمده: <b>{_fmt_toman(stats['total_wholesale'])}</b> تومان\n"
        f"💵 سود سیستم: <b>{_fmt_toman(stats['total_profit'])}</b> تومان\n"
        f"📥 شارژ کیف پول: <b>{_fmt_toman(stats['total_charges'])}</b> تومان\n"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="agency:root")]])
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
        "🕊 <b>گزارش تایید پرداخت نماینده</b> 🕊\n\n"
        "💸 شیوه پرداخت: کارت به کارت\n"
        f"🔑 شناسه تراکنش: <code>{ref_id}</code>\n"
        f"👤 نماینده: <b>{name}</b>\n"
        f"💰 مبلغ پرداخت: <b>{_fmt_toman(amount)}</b> تومان\n"
        f"💳 4 رقم آخر کارت مبدا: <code>{last4}</code>"
    )


def _agent_payment_action_kb(payment_id: int, agent_id: int) -> InlineKeyboardMarkup:
    agent = agent_db.get_agent_by_id(agent_id) or {}
    name = _escape(agent.get("full_name") or agent.get("username") or f"نماینده #{agent_id}")
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("رد ❌", callback_data=f"agency:payno:{payment_id}"),
            InlineKeyboardButton("تایید ✅", callback_data=f"agency:payok:{payment_id}"),
        ],
        [InlineKeyboardButton(f"{name} 👤", callback_data=f"agency:view:{agent_id}")],
    ])


async def send_pending_agent_payments(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1) -> None:
    payments, total = agentbot_db.get_pending_wallet_charge_payments(page=page, page_size=8)
    total_pages = max(1, (total + 7) // 8)
    lines = [
        "⏳ <b>شارژهای نماینده در انتظار تایید</b>\n\n",
        f"تعداد: <b>{total}</b> | صفحه {page}/{total_pages}\n",
    ]
    rows: List[List[Any]] = []
    if not payments:
        lines.append("موردی برای تایید وجود ندارد.")
    for p in payments:
        agent = agent_db.get_agent_by_id(int(p.get("agent_id") or 0)) or {}
        name = agent.get("full_name") or agent.get("username") or p.get("customer_name") or f"نماینده #{p.get('agent_id')}"
        lines.append(f"• {_escape(name)} | {_fmt_toman(p.get('amount'))} تومان | کد {p.get('ref_id')}")
        rows.append([InlineKeyboardButton(f"{name} - {_fmt_toman(p.get('amount'))}", callback_data=f"agency:payview:{p['id']}")])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("قبلی", callback_data=f"agency:payments:{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("بعدی", callback_data=f"agency:payments:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="agency:root")])
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def show_agent_payment_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: int) -> None:
    query = update.callback_query
    payment = agentbot_db.get_payment_by_id(payment_id)
    if not payment:
        await query.answer("پرداخت پیدا نشد.", show_alert=True)
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
        await query.answer("پرداخت پیدا نشد.", show_alert=True)
        return
    if str(payment.get("status") or "") != "pending":
        await query.answer("این پرداخت قبلاً بررسی شده است.", show_alert=True)
        return
    agent_id = int(payment.get("agent_id") or 0)
    amount = int(payment.get("amount") or 0)
    agentbot_db.set_payment_status(payment_id, agent_id, "approved")
    wallet = agent_db.charge_wallet(agent_id, amount, description=f"شارژ کارت به کارت نماینده - تراکنش {payment.get('ref_id')}")
    agent = agent_db.get_agent_by_id(agent_id) or {}
    try:
        token = os.getenv("AGENT_BOT_TOKEN", "").strip()
        agent_tg_id = int(agent.get("telegram_id") or 0)
        if token and agent_tg_id:
            from telegram import Bot
            bot = Bot(token=token)
            await bot.send_message(
                chat_id=agent_tg_id,
                text=f"✅ پرداخت شما تایید شد.\n\nمبلغ {_fmt_toman(amount)} تومان به کیف پول شما اضافه شد.",
            )
    except Exception as e:
        logger.warning("Failed notifying agent payment approval: %s", e)
    await query.answer("پرداخت تایید و کیف پول شارژ شد.", show_alert=True)
    try:
        await query.edit_message_caption(caption=f"✅ پرداخت تایید شد.\nموجودی جدید: {_fmt_toman(wallet['balance'])} تومان", parse_mode="HTML")
    except BadRequest:
        try:
            await query.edit_message_text(f"✅ پرداخت تایید شد.\nموجودی جدید: {_fmt_toman(wallet['balance'])} تومان", parse_mode="HTML")
        except BadRequest:
            pass


async def reject_agent_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: int) -> None:
    query = update.callback_query
    payment = agentbot_db.get_payment_by_id(payment_id)
    if not payment:
        await query.answer("پرداخت پیدا نشد.", show_alert=True)
        return
    if str(payment.get("status") or "") != "pending":
        await query.answer("این پرداخت قبلاً بررسی شده است.", show_alert=True)
        return
    agent_id = int(payment.get("agent_id") or 0)
    amount = int(payment.get("amount") or 0)
    agentbot_db.set_payment_status(payment_id, agent_id, "rejected")
    agent = agent_db.get_agent_by_id(agent_id) or {}
    try:
        token = os.getenv("AGENT_BOT_TOKEN", "").strip()
        agent_tg_id = int(agent.get("telegram_id") or 0)
        if token and agent_tg_id:
            from telegram import Bot
            bot = Bot(token=token)
            await bot.send_message(chat_id=agent_tg_id, text=f"❌ پرداخت شما به مبلغ {_fmt_toman(amount)} تومان رد شد.")
    except Exception as e:
        logger.warning("Failed notifying agent payment rejection: %s", e)
    await query.answer("پرداخت رد شد.", show_alert=True)
    try:
        await query.edit_message_caption(caption="❌ پرداخت رد شد.", parse_mode="HTML")
    except BadRequest:
        try:
            await query.edit_message_text("❌ پرداخت رد شد.", parse_mode="HTML")
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
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return

    rates = agent_db.get_wholesale_pricing(agent_id)

    lines = [
        f"💵 <b>تعرفه عمده نماینده</b>\n\n",
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n",
        f"📊 هر گیگ: <b>{_fmt_toman(rates['price_per_gb'])}</b> تومان\n",
        f"⏰ هر ۳۰ روز: <b>{_fmt_toman(rates['price_per_30_days'])}</b> تومان\n\n",
        "وقتی نماینده پرداخت مشتری را تایید می‌کند، این مبلغ از کیف پول نماینده کم می‌شود.\n",
        "کیف پول نماینده تاریخ انقضا ندارد و فقط با سفارش‌های تاییدشده مصرف می‌شود.\n\n",
        "🧮 <b>فرمول کسر</b>\n",
        "<code>حجم سرویس × قیمت هر گیگ + ماه سرویس × قیمت هر ۳۰ روز</code>\n\n",
        "مثال: اگر سرویس ۱۰ گیگ و ۴۵ روز باشد، زمان آن ۲ ماه حساب می‌شود.",
    ]

    kb_rows = [
        [InlineKeyboardButton("⚙️ تنظیم تعرفه عمده", callback_data=f"agency:rates:{agent_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"agency:view:{agent_id}")],
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
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    rates = agent_db.get_wholesale_pricing(agent_id)
    context.user_data["state"] = AGENCY_SET_WHOLESALE_GB
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    context.user_data.pop("agency_wholesale_price_per_gb", None)
    context.user_data.pop("agency_wholesale_price_per_30_days", None)
    text = (
        "⚙️ <b>تنظیم تعرفه عمده</b>\n\n"
        "مرحله ۱ از ۳\n\n"
        "قیمت هر گیگ را به تومان وارد کنید.\n"
        "این مبلغ برای هر گیگ سرویس مشتری از کیف پول نماینده کسر می‌شود.\n\n"
        "تعرفه فعلی:\n"
        f"📊 هر گیگ: <b>{_fmt_toman(rates['price_per_gb'])}</b> تومان\n"
        f"⏰ هر ۳۰ روز: <b>{_fmt_toman(rates['price_per_30_days'])}</b> تومان\n\n"
        "مثال: <code>2000</code>"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"agency:view:{agent_id}")]])
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
        await update.message.reply_text("❌ لطفاً فقط عدد ارسال کنید.")
        return True
    if value < 0:
        await update.message.reply_text("❌ اعداد نباید منفی باشند.")
        return True

    if state == AGENCY_SET_WHOLESALE_GB:
        context.user_data["agency_wholesale_price_per_gb"] = value
        context.user_data["state"] = AGENCY_SET_WHOLESALE_DAYS
        await update.message.reply_text(
            "⚙️ <b>تنظیم تعرفه عمده</b>\n\n"
            "مرحله ۲ از ۳\n\n"
            f"📊 قیمت هر گیگ: <b>{_fmt_toman(value)}</b> تومان\n\n"
            "حالا قیمت هر ۳۰ روز را به تومان وارد کنید.\n"
            "اگر سرویس ۴۵ روزه باشد، زمان آن ۲ ماه حساب می‌شود.\n\n"
            "مثال: <code>10000</code>",
            parse_mode="HTML",
        )
        return True

    context.user_data["agency_wholesale_price_per_30_days"] = value
    price_per_gb = int(context.user_data.get("agency_wholesale_price_per_gb") or 0)
    price_per_30_days = value
    context.user_data["state"] = "agency:confirm_wholesale_rates"
    text = (
        "⚙️ <b>تایید تعرفه عمده</b>\n\n"
        "مرحله ۳ از ۳\n\n"
        f"📊 هر گیگ: <b>{_fmt_toman(price_per_gb)}</b> تومان\n"
        f"⏰ هر ۳۰ روز: <b>{_fmt_toman(price_per_30_days)}</b> تومان\n\n"
        "این تعرفه از سفارش‌های بعدی مشتریان نماینده کسر می‌شود.\n"
        "آیا ذخیره شود؟"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید و ذخیره", callback_data=f"agency:ratesave:{agent_id}")],
        [InlineKeyboardButton("✏️ ویرایش از اول", callback_data=f"agency:rates:{agent_id}")],
        [InlineKeyboardButton("❌ لغو", callback_data=f"agency:view:{agent_id}")],
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
        "✅ تعرفه عمده ثبت شد.\n"
        f"📊 هر گیگ: <b>{_fmt_toman(rates['price_per_gb'])}</b> تومان\n"
        f"⏰ هر ۳۰ روز: <b>{_fmt_toman(rates['price_per_30_days'])}</b> تومان"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به نماینده", callback_data=f"agency:view:{agent_id}")]])
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await query.answer("تعرفه ذخیره شد.", show_alert=True)


async def start_add_price_server_select(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """انتخاب سرور برای قیمت‌گذاری جدید."""
    servers = database.get_servers() or []
    if not servers:
        await update.callback_query.answer("هیچ سروری ثبت نشده است.", show_alert=True)
        return

    rows: List[List[Any]] = []
    for s in servers:
        sid = s["id"]
        stitle = _escape(s.get("title")) or f"server #{sid}"
        rows.append([InlineKeyboardButton(
            stitle,
            callback_data=f"agency:pricesrv:{agent_id}:{sid}",
        )])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"agency:prices:{agent_id}:1")])

    kb = InlineKeyboardMarkup(rows)
    text = f"🖥 <b>انتخاب سرور</b>\n\nسروری که می‌خواهید قیمت عمده تعیین کنید را انتخاب کنید:"
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
        "💵 <b>افزودن قیمت عمده</b>\n\n"
        "مشخصات پلن را به این شکل ارسال کنید:\n\n"
        "<code>روز حجم قیمت_عمده</code>\n\n"
        "مثال: <code>30 50 80000</code>\n"
        "یعنی ۳۰ روز، ۵۰ گیگ، قیمت عمده ۸۰٬۰۰۰ تومان\n\n"
        "(قیمت فروش بعداً توسط خود نماینده تعیین می‌شود)"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"agency:prices:{agent_id}:1")]])
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
            "❌ فرمت نامعتبر. مثال صحیح:\n<code>30 50 80000</code>\n(روز حجم قیمت_عمده)"
        )
        return True

    try:
        days = int(parts[0])
        gb = float(parts[1])
        wholesale = int(parts[2])
    except ValueError:
        await update.message.reply_text("❌ اعداد نامعتبر هستند.")
        return True

    if days <= 0 or gb <= 0 or wholesale < 0:
        await update.message.reply_text("❌ مقادیر باید مثبت باشند.")
        return True

    plan = agent_db.set_agent_plan(
        agent_id=agent_id,
        server_id=server_id,
        days=days,
        gb=gb,
        wholesale_price=wholesale,
        sale_price=0,
        plan_title=f"{days} روز / {gb}GB",
    )

    context.user_data.pop("state", None)
    context.user_data.pop("agency_price_server_id", None)

    await update.message.reply_text(
        f"✅ قیمت عمده ثبت شد!\n"
        f"📦 {days} روز / {gb}GB\n"
        f"🏷 قیمت عمده: <b>{_fmt_toman(wholesale)}</b> تومان\n\n"
        "نماینده باید قیمت فروش را خودش تعیین کند.",
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
    """نمایش سرویس‌های یک نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return

    if page < 1:
        page = 1
    services, total = agent_db.get_services_by_agent(agent_id, page=page, page_size=SERVICES_PAGE_SIZE)
    total_pages = max(1, (total + SERVICES_PAGE_SIZE - 1) // SERVICES_PAGE_SIZE)

    lines = [
        f"📦 <b>سرویس‌های نماینده</b> (صفحه {page}/{total_pages})\n\n",
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n",
        f"📊 مجموع: <b>{total}</b>\n\n",
    ]
    if not services:
        lines.append("سرویسی ثبت نشده است.")
    else:
        for svc in services:
            active = "✅" if int(svc.get("is_active", 0)) else "❌"
            trial = "🔥" if int(svc.get("is_trial", 0)) else ""
            lines.append(
                f"{active}{trial} <b>{_escape(svc.get('name')) or 'بی‌نام'}</b>\n"
                f"   🖥 {_escape(svc.get('server_title'))} | 💧 {svc.get('usage_current', 0)}/{svc.get('usage_limit', 0)}GB\n"
                f"   🏷 عمده: {_fmt_toman(svc.get('wholesale_price'))} | فروش: {_fmt_toman(svc.get('sale_price'))}\n"
                f"   🆔 <code>{svc['id']}</code> · expiry: {_escape(svc.get('end_date') or '—')}"
            )

    rows: List[List[Any]] = []
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"agency:services:{agent_id}:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"agency:services:{agent_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"agency:view:{agent_id}")])

    kb = InlineKeyboardMarkup(rows)
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


# ===============================
#   مدیریت ربات‌های مشتری
# ===============================
async def send_agent_bots(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """نمایش ربات‌های مشتری یک نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return

    bots = agent_db.get_customer_bots(agent_id)
    lines = [
        f"🤖 <b>ربات‌های مشتری نماینده</b>\n\n",
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n",
        f"📦 تعداد: <b>{len(bots)}</b>\n\n",
    ]
    if not bots:
        lines.append("رباتی ثبت نشده است.\n")
        lines.append("نماینده از ربات AgentBot خودش توکن ربات مشتری را ثبت می‌کند.")
    else:
        for b in bots:
            active = "✅" if int(b.get("is_active", 0)) else "❌"
            uname = b.get("bot_username") or "—"
            lines.append(f"{active} @{_escape(uname)} · 🆔 <code>{b['id']}</code>")

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"agency:view:{agent_id}")],
        ]
    )
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
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
            "⚙️ <b>تنظیمات توکن ربات نماینده</b>\n"
            f"{SEPARATOR}\n\n"
            f"🔑 <b>توکن فعلی:</b>\n"
            f"<code>{masked}</code>\n\n"
            f"✅ توکن ثبت شده و آماده استفاده است.\n"
            f"برای تغییر، توکن جدید را ارسال کنید."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✏️ تغییر توکن", callback_data="agency:agenttoken:change")],
                [InlineKeyboardButton("🔄 ریستارت ربات", callback_data="agency:agenttoken:restart")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="agency:root")],
            ]
        )
    else:
        text = (
            "⚙️ <b>تنظیمات توکن ربات نماینده</b>\n"
            f"{SEPARATOR}\n\n"
            "⚠️ <b>هنوز توکنی ثبت نشده!</b>\n\n"
            "برای فعال‌سازی سیستم نمایندگی:\n"
            "۱. به @BotFather بروید\n"
            "۲. یک ربات جدید بسازید\n"
            "۳. توکن را کپی کنید\n"
            "۴. روی دکمه زیر بزنید و توکن را بفرستید"
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("➕ افزودن توکن", callback_data="agency:agenttoken:change")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="agency:root")],
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
        "✏️ <b>تغییر توکن ربات نماینده</b>\n"
        f"{SEPARATOR}\n\n"
        "توکن جدید ربات نماینده را ارسال کنید.\n\n"
        "💡 <b>راهنما:</b>\n"
        "• به @BotFather بروید\n"
        "• /newbot بزنید و ربات بسازید\n"
        "• توکن را کپی کنید\n\n"
        "فرمت توکن:\n"
        "<code>1234567890:ABCdefGhIJKlmNoPQRsTUVwxyz</code>\n\n"
        "❌ برای لغو /cancel بفرستید"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="agency:agenttoken")]])

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
        "🔄 <b>در حال ریستارت ربات نماینده...</b>",
        parse_mode="HTML",
    )

    success = _restart_agent_bot()

    text = (
        "✅ <b>ربات نماینده با موفقیت ریستارت شد.</b>"
        if success
        else "❌ <b>خطا در ریستارت ربات نماینده.</b>\nلطفاً به صورت دستی ریستارت کنید."
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔙 بازگشت به تنظیمات توکن", callback_data="agency:agenttoken")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="agency:exit")],
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
            "❌ <b>فرمت توکن نامعتبر است!</b>\n"
            f"{SEPARATOR}\n\n"
            "توکن باید به این شکل باشد:\n"
            "<code>1234567890:ABCdefGhIJKlmNoPQRsTUVwxyz</code>\n\n"
            "💡 دوباره توکن را از @BotFather کپی کنید.",
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
        [[InlineKeyboardButton("🔙 بازگشت", callback_data="agency:root")]]
    )
    msg = (
        "✅ <b>توکن ربات نماینده ذخیره شد!</b>\n"
        f"{SEPARATOR}\n\n"
        f"🔑 توکن: <code>{masked}</code>\n\n"
    )
    if env_updated:
        msg += "📄 فایل .env بروزرسانی شد.\n"
    else:
        msg += "⚠️ خطا در بروزرسانی فایل .env.\n"

    if agent_restarted:
        msg += "🔄 ربات AgentBot ریستارت شد."
    else:
        msg += "⚠️ ریستارت خودکار انجام نشد. لطفاً دستی ریستارت کنید."

    await update.message.reply_text(msg, reply_markup=kb, parse_mode="HTML")
    return True


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
            text="به منوی اصلی بازگشتید.",
            reply_markup=admin_main_keyboard(),
        )
        return

    if action == "stats":
        await send_global_stats(update, context)
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
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        await send_agent_services(update, context, agent_id, page=page)
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
            await query.answer("سرور نامعتبر.", show_alert=True)
            return
        await start_add_price_input(update, context, agent_id, server_id)
        return

    if action == "bots":
        await send_agent_bots(update, context, agent_id)
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
        await update.message.reply_text("عملیات لغو شد.", reply_markup=admin_main_keyboard())
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

    if state == AGENCY_SET_AGENT_TOKEN:
        return await handle_set_agent_token_text(update, context)

    return False
