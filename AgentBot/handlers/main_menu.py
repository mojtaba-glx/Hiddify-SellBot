import logging

from telegram import Update
from telegram.ext import ContextTypes

from Shared import agent_db
from AgentBot.handlers.base import authenticate, get_agent_id, clear_state
from AgentBot.keyboards import main_menu_keyboard, language_keyboard, AGENT_MENU_KEYS, agent_lang
from AgentBot.constants import MENU_MAIN, UD_STATE
from AgentBot.handlers import (
    subscriptions, wallet, plans, customer_bot, tickets,
    settings_users, settings_orders, settings_transactions, settings_gifts,
    settings_shop, settings_payment, settings_customer_payments, settings_broadcast,
    settings_forcejoin,
)
from Shared import i18n

logger = logging.getLogger(__name__)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent = await authenticate(update, context)
    _lg = i18n.get_agent_lang(int((agent or {}).get("id") or 0)) if agent else "fa"
    if not agent:
        await update.message.reply_text(
            i18n.t('⚠️ شما به عنوان نماینده ثبت نشده‌اید.\nبا ادمین در ارتباط باشید.', _lg)
        )
        return

    # دپ‌لینک اسکرین‌شات تیکت: /start tshotu_... (باید بعد از authenticate باشد تا agent_id ست شود)
    try:
        parts = (update.message.text or "").split()
        if len(parts) > 1 and str(parts[1] or "").startswith("tshotu_"):
            from AgentBot.handlers.tickets import handle_ticket_shot_start
            handled = await handle_ticket_shot_start(update, context, parts[1])
            if handled:
                return
    except Exception:
        pass

    clear_state(context)
    name = agent.get("full_name") or agent.get("username") or f"{i18n.t('agent_fallback', _lg)}"
    from Shared import i18n as _i18n
    from Shared import agent_db as _adb
    _lg = _i18n.get_agent_lang(int(agent.get("id") or 0))
    await update.message.reply_text(
        f"{i18n.t('خوش آمدید ', _lg)}{name}{i18n.t(' عزیز 👋\nاز منوی زیر گزینه مورد نظر را انتخاب کنید.', _lg)}",
        reply_markup=main_menu_keyboard(lang=_lg),
    )
    await update.message.reply_text(
        _i18n.t("lang_choose", _lg),
        reply_markup=language_keyboard( lang=_lg),
    )


async def handle_language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دستور /language — تغییر زبان رابط کاربری نماینده."""
    from Shared import i18n as _i18n
    from Shared import agent_db as _adb
    if not update.message or not update.effective_user:
        return
    try:
        agent = _adb.get_agent_by_telegram_id(int(update.effective_user.id) or 0)
    except Exception:
        agent = None
    if not agent:
        await update.message.reply_text(i18n.t('not_authenticated', "fa"))
        return
    _lg = _i18n.get_agent_lang(int(agent.get("id") or 0))
    await update.message.reply_text(
        _i18n.t("lang_choose", _lg),
        reply_markup=language_keyboard( lang=_lg),
    )


async def handle_main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    # --- تغییر زبان رابط کاربری نماینده ---
    if data.startswith("lang:set:"):
        from Shared import i18n as _i18n
        from Shared import agent_db as _adb
        from AgentBot.handlers.base import get_agent_id as _gid
        new_lang = data.split(":")[2].strip().lower()
        if not _i18n.is_supported(new_lang):
            new_lang = "fa"
        try:
            _adb.set_agent_language(int(_gid(context) or 0), new_lang)
        except Exception:
            pass
        try:
            await query.answer()
        except Exception:
            pass
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=_i18n.t("lang_changed", new_lang, lang_name=_i18n.lang_display_name(new_lang)),
            reply_markup=main_menu_keyboard(lang=new_lang),
        )
        return
    if not data.startswith("agbot:"):
        return
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    # Avoid pre-answering callbacks that intend to show alerts (e.g. "*:help").
    if action != "custpay" and not data.endswith(":help"):
        try:
            await query.answer()
        except Exception:
            pass

    agent_id = get_agent_id(context)
    if not agent_id:
        agent = await authenticate(update, context)
        if not agent:
            return
        agent_id = agent["id"]

    if action == "menu" or action == "":
        clear_state(context)
        text = (
            i18n.t('📊 <b>پانل نمایندگی</b>\nاز منوی زیر گزینه مورد نظر را انتخاب کنید.', _lg)
        )
        try:
            await query.edit_message_text(text, reply_markup=main_menu_keyboard(lang=_lg), parse_mode="HTML")
        except Exception:
            await query.message.reply_text(text, reply_markup=main_menu_keyboard(lang=_lg), parse_mode="HTML")
        return

    routing = {
        "subs": subscriptions,
        "wallet": wallet,
        "plans": plans,
        "cbot": customer_bot,
        "ticket": tickets,
        "broadcast": settings_broadcast,
        "custpay": settings_customer_payments,
        "set": None,
        "shop": settings_shop,
        "pay": settings_payment,
    }

    module = routing.get(action)
    if module:
        await module.handle_callback(update, context)
        return

    if action == "set":
        sub = parts[2] if len(parts) > 2 else ""
        if sub == "back":
            clear_state(context)
            from AgentBot.keyboards import settings_menu_keyboard
            await query.edit_message_text(
                i18n.t('⚙️ <b>مدیریت ربات</b>', _lg),
                reply_markup=settings_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML",
            )
            return
        set_routing = {
            "users": settings_users,
            "orders": settings_orders,
            "tx": settings_transactions,
            "gifts": settings_gifts,
            "broadcast": settings_broadcast,
            "config": None,
            "cfg": None,
        }
        set_module = set_routing.get(sub)
        if set_module:
            await set_module.handle_callback(update, context)
            return
        if sub == "config" or sub == "cfg":
            cfg_sub = parts[3] if len(parts) > 3 else ""
            if cfg_sub == "shop":
                await settings_shop.handle_callback(update, context)
            elif cfg_sub == "payment":
                await settings_payment.handle_callback(update, context)
            elif cfg_sub == "forcejoin" or data.startswith("agbot:set:cfg:forcejoin"):
                await settings_forcejoin.handle_callback(update, context)
            else:
                from AgentBot.keyboards import config_menu_keyboard
                try:
                    await query.edit_message_text(
                        i18n.t('⚙️ <b>تنظیمات</b>\nگزینه مورد نظر را انتخاب کنید:', _lg),
                        reply_markup=config_menu_keyboard(), parse_mode="HTML",
                    )
                except Exception:
                    pass
            return


async def handle_agent_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    if not update.message:
        return
    agent_id = get_agent_id(context)
    if not agent_id:
        agent = await authenticate(update, context)
        if not agent:
            return
    text = (update.message.text or update.message.caption or "").strip()

    from AgentBot.keyboards import (
        BTN_SUBSCRIPTIONS, BTN_WALLET, BTN_PLANS, BTN_CUSTOMER_BOT,
        BTN_TICKETS, BTN_SETTINGS, BTN_BACK,
    )
    # --- مچر چندزبانه: نگاشت دکمه هر زبان به لیبل مرجع فارسی ---
    from Shared import i18n as _i18n
    _menu_key = _i18n.resolve_button(text, AGENT_MENU_KEYS)
    if _menu_key:
        _fa_map = {
            "ag_menu_subscriptions": BTN_SUBSCRIPTIONS, "ag_menu_wallet": BTN_WALLET,
            "ag_menu_plans": BTN_PLANS, "ag_menu_customer_bot": BTN_CUSTOMER_BOT,
            "ag_menu_tickets": BTN_TICKETS, "ag_menu_settings": BTN_SETTINGS,
        }
        text = _fa_map.get(_menu_key, text)
    state = context.user_data.get(UD_STATE)

    # اگر در حالت تغییر نام هستیم، دکمه بازگشت پایین باید مستقیم به هندلر rename برود
    if state == "st:rename_service" and text in (BTN_BACK, "🔙 بازگشت", "❌ لغو", "/cancel", "لغو"):
        consumed = await subscriptions.handle_text(update, context)
        if consumed:
            return

    menu_map = {
        BTN_SUBSCRIPTIONS: subscriptions,
        BTN_WALLET: wallet,
        BTN_PLANS: plans,
        BTN_CUSTOMER_BOT: customer_bot,
        BTN_TICKETS: tickets,
        BTN_SETTINGS: None,
    }
    if text in menu_map or text in ("❌ لغو", "/cancel"):
        if text in ("❌ لغو", "/cancel"):
            payment_states = {
                "st:add_card", "st:add_card_number", "st:add_card_owner", "st:add_card_bank",
                "st:edit_card", "st:card_text",
            }
            current_state = state
            clear_state(context)
            if current_state in payment_states:
                await update.message.reply_text(i18n.t('عملیات لغو شد.', _lg))
                await settings_payment.show_menu(update, context)
                return
            await update.message.reply_text(i18n.t('عملیات لغو شد.', _lg), reply_markup=main_menu_keyboard())
            return
        clear_state(context)
        target = menu_map.get(text)
        if target:
            await target.show_menu(update, context)
            return
        from AgentBot.keyboards import settings_menu_keyboard
        await update.message.reply_text(
            i18n.t('⚙️ <b>مدیریت ربات</b>', _lg),
            reply_markup=settings_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML",
        )
        return

    if state:
        sub_state_handlers = {
            "st:search_user": settings_users.handle_text,
            "st:send_user_msg": settings_users.handle_text,
            "fj:set_username": settings_forcejoin.handle_text,
            "st:createsvc_name": subscriptions.handle_text,
            "st:rename_service": subscriptions.handle_text,
            "st:renew_days": subscriptions.handle_text,
            "st:renew_gb": subscriptions.handle_text,
            "st:wallet_create": wallet.handle_text,
            "st:wallet_charge_amount": wallet.handle_text,
            "st:wallet_charge_receipt": wallet.handle_text,
            "st:wallet_charge_last4": wallet.handle_text,
            "st:sale_price": plans.handle_text,
            "st:cbot_token": customer_bot.handle_text,
            "st:reply_ticket": tickets.handle_text,
            "st:reply_ticket_shot": tickets.handle_text,
            "st:reply_ticket_confirm": tickets.handle_text,
            "st:add_card": settings_payment.handle_text,
            "st:add_card_number": settings_payment.handle_text,
            "st:add_card_owner": settings_payment.handle_text,
            "st:add_card_bank": settings_payment.handle_text,
            "st:edit_card": settings_payment.handle_text,
            "st:card_text": settings_payment.handle_text,
            "st:search_order": settings_orders.handle_text,
            "st:search_svc": subscriptions.handle_text,
            "st:search_name": subscriptions.handle_text,
            "st:search_tx": settings_transactions.handle_text,
            "st:broadcast_message": settings_broadcast.handle_text,
            "st:dyn_settings": plans.handle_text,
            "st:dyn_edit_field": plans.handle_text,
            "st:fixed_add_cat_title": plans.handle_text,
            "st:fixed_edit_cat_title": plans.handle_text,
            "st:fixed_add_plan_title": plans.handle_text,
            "st:fixed_add_plan_gb": plans.handle_text,
            "st:fixed_add_plan_days": plans.handle_text,
            "st:fixed_add_plan_price": plans.handle_text,
        }
        handler = sub_state_handlers.get(state)
        if handler:
            consumed = await handler(update, context)
            if consumed:
                return

    target = menu_map.get(text)
    if target:
        clear_state(context)
        await target.show_menu(update, context)
        return

    if text == BTN_SETTINGS:
        clear_state(context)
        from AgentBot.keyboards import settings_menu_keyboard
        await update.message.reply_text(
            i18n.t('⚙️ <b>مدیریت ربات</b>', _lg),
            reply_markup=settings_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML",
        )
        return

    if text in ("\u274c \u0644\u063a\u0648", "/cancel"):
        clear_state(context)
        await update.message.reply_text(
            i18n.t('لغو شد.', _lg), reply_markup=main_menu_keyboard()
        )
        return

    if text == BTN_BACK or text == "/start":
        await handle_start(update, context)
        return
