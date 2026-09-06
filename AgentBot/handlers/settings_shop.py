import logging

from telegram import Update
from telegram.ext import ContextTypes

from AgentBot.constants import UD_STATE
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import shop_settings_keyboard, config_menu_keyboard
from AgentBot.database import get_setting, set_setting
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
    agent_id = get_agent_id(context)

    if p1 == "set" and p2 == "cfg" and not p3:
        await query.edit_message_text(
            i18n.t('⚙️ <b>تنظیمات</b>', _lg),
            reply_markup=config_menu_keyboard(), parse_mode="HTML",
        )
        return

    if (p2 == "cfg" and p3 == "back") or (p2 == "back" and p1 == "set"):
        await query.edit_message_text(
            i18n.t('⚙️ <b>تنظیمات</b>\nگزینه مورد نظر را انتخاب کنید:', _lg),
            reply_markup=config_menu_keyboard(), parse_mode="HTML",
        )
        return

    if p3 == "shop":
        buy = bool(get_setting(agent_id, "buy_enabled", True))
        renew = bool(get_setting(agent_id, "renew_enabled", True))
        await query.edit_message_text(
            i18n.t('🛒 <b>تنظیمات خرید و تمدید</b>', _lg),
            reply_markup=shop_settings_keyboard(buy, renew), parse_mode="HTML",
        )
        return

    if p3 == "payment":
        from AgentBot.keyboards import payment_settings_keyboard
        await query.edit_message_text(
            i18n.t('💳 <b>تنظیمات پرداخت</b>', _lg),
            reply_markup=payment_settings_keyboard(), parse_mode="HTML",
        )
        return

    if p1 == "shop":
        if p2 == "buy":
            current = bool(get_setting(agent_id, "buy_enabled", True))
            set_setting(agent_id, "buy_enabled", not current)
            label = i18n.t('غیرفعال', _lg) if current else i18n.t('فعال', _lg)
            await query.answer(f"{i18n.t('خرید ', _lg)}{label}{i18n.t(' شد.', _lg)}")
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
            label = i18n.t('غیرفعال', _lg) if current else i18n.t('فعال', _lg)
            await query.answer(f"{i18n.t('تمدید ', _lg)}{label}{i18n.t(' شد.', _lg)}")
            buy = bool(get_setting(agent_id, "buy_enabled", True))
            renew = bool(get_setting(agent_id, "renew_enabled", True))
            try:
                await query.edit_message_reply_markup(reply_markup=shop_settings_keyboard(buy, renew))
            except Exception:
                pass
            return
        if p2 == "back":
            await query.edit_message_text(
                i18n.t('⚙️ <b>تنظیمات</b>\nگزینه مورد نظر را انتخاب کنید:', _lg),
                reply_markup=config_menu_keyboard(), parse_mode="HTML",
            )
            return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return False
