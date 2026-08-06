# AgentBot/handlers/settings_forcejoin.py
# تنظیمات عضویت اجباری ربات مشتری

import logging

from telegram import Update
from telegram.ext import ContextTypes
from Shared.tg_button_styles import inline_button as IButton
from telegram import InlineKeyboardMarkup

from AgentBot.constants import UD_STATE
from AgentBot.handlers.base import get_agent_id
from CustomerBot.database import get_force_join_settings, set_force_join_settings

logger = logging.getLogger(__name__)

STATE_FJ_SET_USERNAME = "fj:set_username"

_BACK_CB = "agbot:set:cfg:forcejoin:menu"
_BACK_CONFIG_CB = "agbot:set:cfg:back"


def _forcejoin_menu_keyboard(enabled: bool, has_channel: bool):
    toggle_label = "✅ عضویت اجباری | فعال" if enabled else "✖️ عضویت اجباری | غیرفعال"
    rows = [
        [IButton("🧩راهنما", callback_data="agbot:set:cfg:forcejoin:help")],
        [IButton(toggle_label, callback_data="agbot:set:cfg:forcejoin:toggle",
                 style="success" if enabled else "danger")],
        [IButton("تنظیم کانال پشتیبانی📢", callback_data="agbot:set:cfg:forcejoin:setchannel")],
        [IButton("🔙بازگشت", callback_data=_BACK_CONFIG_CB)],
    ]
    return InlineKeyboardMarkup(rows)


def _build_status_text(agent_id: int) -> str:
    fjs = get_force_join_settings(agent_id)
    enabled = fjs.get("enabled", False)
    username = fjs.get("channel_username", "") or ""
    channel_display = f"@{username}" if username else "—"
    return (
        f"🔒تنظیمات عضویت اجباری\n"
        f"کانال فعلی: {channel_display} 📢"
    )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent_id = get_agent_id(context)
    fjs = get_force_join_settings(agent_id)
    enabled = bool(fjs.get("enabled", False))
    has_channel = bool(fjs.get("channel_username", ""))
    text = _build_status_text(agent_id)
    kb = _forcejoin_menu_keyboard(enabled, has_channel)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    agent_id = get_agent_id(context)

    # نمایش منوی اصلی
    if data == "agbot:set:cfg:forcejoin:menu" or data == "agbot:set:cfg:forcejoin":
        await show_menu(update, context)
        return

    # راهنما — پیام جدید پایین می‌آید، دکمه‌های بالا دست نخورده می‌مانند
    if data == "agbot:set:cfg:forcejoin:help":
        help_text = (
            "📋 <b>راهنمای عضویت اجباری</b>\n\n"
            "برای فعال‌سازی صحیح:\n"
            "1️⃣ کانال مورد نظر را با دکمه «تنظیم کانال» وارد کنید.\n"
            "2️⃣ <b>ربات مشتری را ادمین کانال کنید</b> — بدون این قدم، سیستم کار نمی‌کند.\n"
            "   (ربات را به کانال اضافه کنید → روی «ادمین‌ها» بزنید → ربات را ادمین کنید)\n"
            "3️⃣ عضویت اجباری را با دکمه toggle فعال کنید.\n\n"
            "⚠️ <b>اگر ربات ادمین نباشد:</b>\n"
            "کاربران پیام «سرویس موقتاً در دسترس نیست» می‌بینند و نمی‌توانند از ربات استفاده کنند."
        )
        await query.answer()
        await query.message.reply_text(help_text)
        return

    # فعال/غیرفعال کردن
    if data == "agbot:set:cfg:forcejoin:toggle":
        fjs = get_force_join_settings(agent_id)
        new_enabled = not bool(fjs.get("enabled", False))
        if new_enabled and not fjs.get("channel_username", ""):
            await query.answer("⚠️ ابتدا کانال را تنظیم کنید.", show_alert=True)
            return
        fjs["enabled"] = new_enabled
        set_force_join_settings(agent_id, fjs)
        label = "فعال ✅" if new_enabled else "غیرفعال ❌"
        await query.answer(f"عضویت اجباری {label} شد.")
        await show_menu(update, context)
        return

    # تنظیم کانال — درخواست username
    if data == "agbot:set:cfg:forcejoin:setchannel":
        await query.answer()
        context.user_data[UD_STATE] = STATE_FJ_SET_USERNAME
        fjs = get_force_join_settings(agent_id)
        current = fjs.get("channel_username", "") or ""
        current_display = f"@{current}" if current else "تنظیم نشده"
        try:
            await query.message.reply_text(
                f"📢 <b>تنظیم کانال پشتیبانی برای عضویت اجباری</b>\n\n"
                f"یک پیام از کانال فوروارد کنید یا <code>@channel</code> / <code>-100...</code> ارسال کنید.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = context.user_data.get(UD_STATE)
    if state != STATE_FJ_SET_USERNAME:
        return False

    agent_id = get_agent_id(context)
    if not agent_id:
        return False

    # حالت ۱: فوروارد از کانال
    fwd = getattr(update.message, "forward_origin", None) or getattr(update.message, "forward_from_chat", None)
    username = ""
    channel_id = ""

    if fwd:
        chat = None
        if hasattr(fwd, "chat"):
            chat = fwd.chat
        elif hasattr(fwd, "id"):
            chat = fwd
        if chat:
            raw_username = getattr(chat, "username", "") or ""
            raw_id = str(getattr(chat, "id", "") or "")
            if raw_username:
                username = raw_username.lstrip("@")
            elif raw_id:
                channel_id = raw_id
    else:
        text = (update.message.text or "").strip()
        if text in ("/cancel", "❌ لغو", "لغو"):
            context.user_data.pop(UD_STATE, None)
            await update.message.reply_text("لغو شد.")
            return True
        # channel_id عددی مثل -1001234567890
        if text.lstrip("-").isdigit():
            channel_id = text
        else:
            username = text.lstrip("@").strip()

    if not username and not channel_id:
        await update.message.reply_text(
            "❌ ورودی نامعتبر است.\n"
            "یک پیام از کانال فوروارد کنید یا <code>@channel</code> / <code>-100...</code> ارسال کنید.",
            parse_mode="HTML",
        )
        return True

    fjs = get_force_join_settings(agent_id)
    if username:
        fjs["channel_username"] = username
        fjs["channel_link"] = f"https://t.me/{username}"
        display = f"@{username}"
    else:
        fjs["channel_username"] = channel_id
        fjs["channel_link"] = ""
        display = channel_id

    set_force_join_settings(agent_id, fjs)
    context.user_data.pop(UD_STATE, None)

    enabled = bool(fjs.get("enabled", False))
    kb = _forcejoin_menu_keyboard(enabled, True)
    await update.message.reply_text(
        f"✅ کانال <b>{display}</b> تنظیم شد.\n\n"
        f"{_build_status_text(agent_id)}",
        parse_mode="HTML",
        reply_markup=kb,
    )
    return True
