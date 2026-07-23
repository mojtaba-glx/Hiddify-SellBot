import logging

from telegram import Update
from telegram.ext import ContextTypes

from AgentBot.constants import UD_STATE
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import shop_settings_keyboard, config_menu_keyboard
from AgentBot.database import get_setting, set_setting

logger = logging.getLogger(__name__)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    parts = data.split(":")
    p1 = parts[1] if len(parts) > 1 else ""
    p2 = parts[2] if len(parts) > 2 else ""
    p3 = parts[3] if len(parts) > 3 else ""
    agent_id = get_agent_id(context)

    if p1 == "set" and p2 == "cfg" and not p3:
        await query.edit_message_text(
            "\u2699\ufe0f <b>\u062a\u0646\u0638\u06cc\u0645\u0627\u062a</b>",
            reply_markup=config_menu_keyboard(), parse_mode="HTML",
        )
        return

    if (p2 == "cfg" and p3 == "back") or (p2 == "back" and p1 == "set"):
        await query.edit_message_text(
            "⚙️ <b>تنظیمات</b>\nگزینه مورد نظر را انتخاب کنید:",
            reply_markup=config_menu_keyboard(), parse_mode="HTML",
        )
        return

    if p3 == "shop":
        buy = bool(get_setting(agent_id, "buy_enabled", True))
        renew = bool(get_setting(agent_id, "renew_enabled", True))
        await query.edit_message_text(
            "\U0001f6d2 <b>\u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u062e\u0631\u06cc\u062f \u0648 \u062a\u0645\u062f\u06cc\u062f</b>",
            reply_markup=shop_settings_keyboard(buy, renew), parse_mode="HTML",
        )
        return

    if p3 == "payment":
        from AgentBot.keyboards import payment_settings_keyboard
        await query.edit_message_text(
            "\U0001f4b3 <b>\u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u067e\u0631\u062f\u0627\u062e\u062a</b>",
            reply_markup=payment_settings_keyboard(), parse_mode="HTML",
        )
        return

    if p1 == "shop":
        if p2 == "buy":
            current = bool(get_setting(agent_id, "buy_enabled", True))
            set_setting(agent_id, "buy_enabled", not current)
            await query.answer(f"\u062e\u0631\u06cc\u062f {'\u0641\u0639\u0627\u0644' if not current else '\u063a\u06cc\u0631\u0641\u0639\u0627\u0644'} \u0634\u062f.")
            buy = bool(get_setting(agent_id, "buy_enabled", True))
            renew = bool(get_setting(agent_id, "renew_enabled", True))
            try:
                await query.edit_message_reply_markup(reply_markup=shop_settings_keyboard(buy, renew))
            except Exception:
                pass
            return
        if p2 == "renew":
            current = bool(get_setting(agent_id, "renew_enabled", True))
            set_setting(agent_id, "renew_enabled", not current)
            await query.answer(f"\u062a\u0645\u062f\u06cc\u062f {'\u0641\u0639\u0627\u0644' if not current else '\u063a\u06cc\u0631\u0641\u0639\u0627\u0644'} \u0634\u062f.")
            buy = bool(get_setting(agent_id, "buy_enabled", True))
            renew = bool(get_setting(agent_id, "renew_enabled", True))
            try:
                await query.edit_message_reply_markup(reply_markup=shop_settings_keyboard(buy, renew))
            except Exception:
                pass
            return
        if p2 == "back":
            await query.edit_message_text(
                "⚙️ <b>تنظیمات</b>\nگزینه مورد نظر را انتخاب کنید:",
                reply_markup=config_menu_keyboard(), parse_mode="HTML",
            )
            return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return False
