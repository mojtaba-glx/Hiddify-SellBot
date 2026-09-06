import logging

from telegram import Update
from telegram.ext import ContextTypes

from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import back_keyboard
from Shared import i18n

logger = logging.getLogger(__name__)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
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
        text = i18n.t('🎁 <b>مدیریت هدایا</b>\n\n', _lg)
        if not gifts:
            text += i18n.t('هنوز هدیه‌ای ثبت نشده.\n\n(این بخش در حال توسعه است)', _lg)
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
                i18n.t('⚙️ <b>تنظیمات ربات</b>', _lg),
                reply_markup=settings_menu_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return
