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
    toggle_label = "✅ عضویت اجباری | فعال" if enabled else "❌ عضویت اجباری | غیرفعال"
    rows = [
        [IButton("🧭 راهنما", callback_data="agbot:set:cfg:forcejoin:help")],
        [IButton(toggle_label, callback_data="agbot:set:cfg:forcejoin:toggle",
                 style="success" if enabled else "danger")],
        [IButton("📢 تنظیم کانال پشتیبانی", callback_data="agbot:set:cfg:forcejoin:setchannel")],
        [IButton("🔙 بازگشت", callback_data=_BACK_CONFIG_CB)],
    ]
    return InlineKeyboardMarkup(rows)


def _build_status_text(agent_id: int) -> str:
    fjs = get_force_join_settings(agent_id)
    enabled = fjs.get("enabled", False)
    username = fjs.get("channel_username", "") or ""
    channel_display = f"@{username}" if username else "—"
    status = "✅ فعال" if enabled else "❌ غیرفعال"
    return (
        f"🔒 <b>تنظیمات عضویت اجباری</b>\n\n"
        f"📢 کانال فعلی: <b>{channel_display}</b>\n"
        f"وضعیت: {status}"
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

    # راهنما
    if data == "agbot:set:cfg:forcejoin:help":
        await query.answer()
        help_text = (
            "🧭 <b>راهنمای عضویت اجباری</b>\n\n"
            "🔒 برای استفاده از ربات، ابتدا در کانال پشتیبانی عضو شوید.\n"
            "پس از عضویت روی ✅ «بررسی عضویت» بزنید.\n\n"
            "اگر عضویت شما تایید نشد:\n"
            "1) مطمئن شوید دقیقاً در همان کانال اعلام‌شده عضو شده‌اید.\n"
            "2) ربات باید ادمین باشد در کانال، تا عضویت کاربران را تشخیص دهد."
        )
        fjs = get_force_join_settings(agent_id)
        enabled = bool(fjs.get("enabled", False))
        has_channel = bool(fjs.get("channel_username", ""))
        try:
            await query.message.reply_text(
                help_text,
                parse_mode="HTML",
                reply_markup=_forcejoin_menu_keyboard(enabled, has_channel),
            )
        except Exception:
            pass
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
                f"📢 <b>تنظیم کانال عضویت اجباری</b>\n\n"
                f"کانال فعلی: {current_display}\n\n"
                "یوزرنیم کانال را ارسال کنید.\n"
                "مثال: <code>mychannel</code> یا <code>@mychannel</code>\n\n"
                "⚠️ ربات باید ادمین کانال باشد.\n"
                "برای لغو، /cancel را بفرستید.",
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

    text = (update.message.text or "").strip()
    if text in ("/cancel", "❌ لغو", "لغو"):
        context.user_data.pop(UD_STATE, None)
        await update.message.reply_text("لغو شد.")
        return True

    # پاکسازی username
    username = text.lstrip("@").strip()
    if not username:
        await update.message.reply_text("❌ یوزرنیم نامعتبر است. دوباره ارسال کنید یا /cancel بفرستید.")
        return True

    fjs = get_force_join_settings(agent_id)
    fjs["channel_username"] = username
    fjs["channel_link"] = f"https://t.me/{username}"
    set_force_join_settings(agent_id, fjs)
    context.user_data.pop(UD_STATE, None)

    enabled = bool(fjs.get("enabled", False))
    kb = _forcejoin_menu_keyboard(enabled, True)
    await update.message.reply_text(
        f"✅ کانال <b>@{username}</b> تنظیم شد.\n\n"
        f"{_build_status_text(agent_id)}",
        parse_mode="HTML",
        reply_markup=kb,
    )
    return True
