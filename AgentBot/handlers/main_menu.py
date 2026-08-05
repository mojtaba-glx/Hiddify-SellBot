import logging

from telegram import Update
from telegram.ext import ContextTypes

from Shared import agent_db
from AgentBot.handlers.base import authenticate, get_agent_id, clear_state
from AgentBot.keyboards import main_menu_keyboard
from AgentBot.constants import MENU_MAIN, UD_STATE
from AgentBot.handlers import (
    subscriptions, wallet, plans, customer_bot, tickets,
    settings_users, settings_orders, settings_transactions, settings_gifts,
    settings_shop, settings_payment, settings_customer_payments, settings_broadcast,
    settings_forcejoin,
)

logger = logging.getLogger(__name__)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent = await authenticate(update, context)
    if not agent:
        await update.message.reply_text(
            "\u26a0\ufe0f \u0634\u0645\u0627 \u0628\u0647 \u0639\u0646\u0648\u0627\u0646 \u0646\u0645\u0627\u06cc\u0646\u062f\u0647 \u062b\u0628\u062a \u0646\u0634\u062f\u0647\u200c\u0627\u06cc\u062f.\n"
            "\u0628\u0627 \u0627\u062f\u0645\u06cc\u0646 \u062f\u0631 \u0627\u0631\u062a\u0628\u0627\u0637 \u0628\u0627\u0634\u06cc\u062f."
        )
        return
    clear_state(context)
    name = agent.get("full_name") or agent.get("username") or f"\u0646\u0645\u0627\u06cc\u0646\u062f\u0647 #"
    await update.message.reply_text(
        f"\u062e\u0648\u0634 \u0622\u0645\u062f\u06cc\u062f {name} \u0639\u0632\u06cc\u0632 \U0001f44b\n"
        "\u0627\u0632 \u0645\u0646\u0648\u06cc \u0632\u06cc\u0631 \u06af\u0632\u06cc\u0646\u0647 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f.",
        reply_markup=main_menu_keyboard(),
    )


async def handle_main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    if not data.startswith("agbot:"):
        return
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action != "custpay":
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
            "\U0001f4ca <b>\u067e\u0627\u0646\u0644 \u0646\u0645\u0627\u06cc\u0646\u062f\u06af\u06cc</b>\n"
            "\u0627\u0632 \u0645\u0646\u0648\u06cc \u0632\u06cc\u0631 \u06af\u0632\u06cc\u0646\u0647 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f."
        )
        try:
            await query.edit_message_text(text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
        except Exception:
            await query.message.reply_text(text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
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
                "⚙️ <b>مدیریت ربات</b>",
                reply_markup=settings_menu_keyboard(), parse_mode="HTML",
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
                        "\u2699\ufe0f <b>\u062a\u0646\u0638\u06cc\u0645\u0627\u062a</b>\n"
                        "\u06af\u0632\u06cc\u0646\u0647 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f:",
                        reply_markup=config_menu_keyboard(), parse_mode="HTML",
                    )
                except Exception:
                    pass
            return


async def handle_agent_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    state = context.user_data.get(UD_STATE)

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
                await update.message.reply_text("عملیات لغو شد.")
                await settings_payment.show_menu(update, context)
                return
            await update.message.reply_text("عملیات لغو شد.", reply_markup=main_menu_keyboard())
            return
        clear_state(context)
        target = menu_map.get(text)
        if target:
            await target.show_menu(update, context)
            return
        from AgentBot.keyboards import settings_menu_keyboard
        await update.message.reply_text(
            "⚙️ <b>مدیریت ربات</b>",
            reply_markup=settings_menu_keyboard(), parse_mode="HTML",
        )
        return

    if state:
        sub_state_handlers = {
            "fj:set_username": settings_forcejoin.handle_text,
            "st:createsvc_name": subscriptions.handle_text,
            "st:renew_days": subscriptions.handle_text,
            "st:renew_gb": subscriptions.handle_text,
            "st:wallet_create": wallet.handle_text,
            "st:wallet_charge_amount": wallet.handle_text,
            "st:wallet_charge_receipt": wallet.handle_text,
            "st:wallet_charge_last4": wallet.handle_text,
            "st:sale_price": plans.handle_text,
            "st:cbot_token": customer_bot.handle_text,
            "st:reply_ticket": tickets.handle_text,
            "st:add_card": settings_payment.handle_text,
            "st:add_card_number": settings_payment.handle_text,
            "st:add_card_owner": settings_payment.handle_text,
            "st:add_card_bank": settings_payment.handle_text,
            "st:edit_card": settings_payment.handle_text,
            "st:card_text": settings_payment.handle_text,
            "st:search_order": settings_orders.handle_text,
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
            "\u2699\ufe0f <b>\u0645\u062f\u06cc\u0631\u06cc\u062a \u0631\u0628\u0627\u062a</b>",
            reply_markup=settings_menu_keyboard(), parse_mode="HTML",
        )
        return

    if text in ("\u274c \u0644\u063a\u0648", "/cancel"):
        clear_state(context)
        await update.message.reply_text(
            "\u0644\u063a\u0648 \u0634\u062f.", reply_markup=main_menu_keyboard()
        )
        return

    if text == BTN_BACK or text == "/start":
        await handle_start(update, context)
        return
