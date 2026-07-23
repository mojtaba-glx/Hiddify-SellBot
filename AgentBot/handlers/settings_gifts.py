import logging

from telegram import Update
from telegram.ext import ContextTypes

from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import back_keyboard

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

    if p1 == "set" and p2 == "gifts":
        agent_id = get_agent_id(context)
        from AgentBot.database import get_gifts
        gifts = get_gifts(agent_id)
        text = "\U0001f381 <b>\u0645\u062f\u06cc\u0631\u06cc\u062a \u0647\u062f\u0627\u06cc\u0627</b>\n\n"
        if not gifts:
            text += "\u0647\u0646\u0648\u0632 \u0647\u062f\u06cc\u0647\u200c\u0627\u06cc \u062b\u0628\u062a \u0646\u0634\u062f\u0647.\n\n(\u0627\u06cc\u0646 \u0628\u062e\u0634 \u062f\u0631 \u062d\u0627\u0644 \u062a\u0648\u0633\u0639\u0647 \u0627\u0633\u062a)"
        else:
            for g in gifts:
                text += f"\u2022 {g.get('gift_type', '')} - {g.get('amount', 0)} - {g.get('customer_name', '')}\n"
        try:
            await query.edit_message_text(text, reply_markup=back_keyboard("agbot:set:back"), parse_mode="HTML")
        except Exception:
            pass
        return

    if p3 == "back" or p2 == "back":
        from AgentBot.keyboards import settings_menu_keyboard
        try:
            await query.edit_message_text(
                "\u2699\ufe0f <b>\u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u0631\u0628\u0627\u062a</b>",
                reply_markup=settings_menu_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return
