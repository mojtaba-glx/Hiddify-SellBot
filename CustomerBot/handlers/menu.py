from telegram import Update
from telegram.ext import ContextTypes

from CustomerBot.constants import (
    UD_STATE, STATE_START,
    STATE_RECEIPT_WAITING, STATE_CARD_LAST4, STATE_TICKET_WAITING_TEXT, STATE_TICKET_WAITING_TITLE,
    STATE_TICKET_WAITING_PHOTO, STATE_TICKET_CONFIRM,
    STATE_COUPON_WAITING, STATE_ZARINPAL_WAITING, STATE_PERFECT_MONEY_WAITING,
    STATE_CRYPTO_WAITING, STATE_CONNECT_WAITING, STATE_RENAME_WAITING,
    STATE_REPLACE_LINK_WAITING,
    STATE_TRIAL_WAITING_NAME,
    STATE_AGENT_MSG_WAITING,
    BTN_STATUS, BTN_RENEW, BTN_BUY, BTN_CONNECT, BTN_TRIAL,
    BTN_SUPPORT, BTN_GUIDE, BTN_FAQ,
)
from CustomerBot.database import (
    get_user, get_buy_renew_settings, get_text_settings, get_localized_text, get_faq_text,
    get_payment_settings, get_marketing_settings, get_trial_spec_settings,
    get_subs_settings, get_force_join_settings,
)
from Shared.agent_db import get_customer_by_telegram_id
from Shared.database import get_servers, get_main_servers
from Shared import i18n
from CustomerBot.keyboards import (
    main_menu_keyboard, location_keyboard, trial_location_keyboard,
    services_list_keyboard, renew_services_keyboard,
    support_panel_keyboard, guide_os_keyboard,
    cancel_keyboard, subscription_status_keyboard,
    MENU_BTN_KEYS,
)
from CustomerBot.utils.helpers import is_rate_limited, format_price

# نگاشت کلید i18n → لیبل مرجع فارسی (برای مچرهای موجود)
_BTN_KEY_TO_FA = {
    "menu_status": BTN_STATUS, "menu_renew": BTN_RENEW, "menu_buy": BTN_BUY,
    "menu_trial": BTN_TRIAL, "menu_support": BTN_SUPPORT,
    "menu_guide": BTN_GUIDE, "menu_faq": BTN_FAQ,
    "btn_back": "بازگشت", "btn_cancel": "لغو",
}


def _menu_lang(agent_id: int, telegram_id: int) -> str:
    try:
        return i18n.get_customer_lang(int(agent_id or 0), int(telegram_id or 0))
    except Exception:
        return "fa"


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user
    if not user:
        return
    agent_id = context.bot_data.get("agent_id", 0)
    lang = _menu_lang(agent_id, user.id)

    # نگاشت دکمه هر زبان به لیبل مرجع فارسی — مچرهای موجود بدون تغییر کار می‌کنند
    menu_key = i18n.resolve_button(text, MENU_BTN_KEYS)
    if menu_key:
        text = _BTN_KEY_TO_FA.get(menu_key, text)

    main_buttons = {
        BTN_STATUS, BTN_RENEW, BTN_BUY, BTN_CONNECT, BTN_TRIAL,
        BTN_SUPPORT, BTN_GUIDE, BTN_FAQ,
    }
    if text in main_buttons or text in {"بازگشت", "لغو", "❌ لغو", "🚫 لغو", "/cancel"}:
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop("wallet_card_amount", None)
        context.user_data.pop("last_order_id", None)
        context.user_data.pop("pending_ticket", None)
        context.user_data.pop("current_payment", None)
        context.user_data.pop("pending_pay_price", None)
        context.user_data.pop("pending_tx_marker", None)
        if text in {"بازگشت", "لغو", "❌ لغو", "🚫 لغو", "/cancel"}:
            await update.message.reply_text(
                i18n.t("back_to_main", lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return

    state = context.user_data.get(UD_STATE, "")

    if state == STATE_AGENT_MSG_WAITING:
        await _handle_agent_msg_reply(update, context, text)
        return

    routed_states = {
        STATE_RECEIPT_WAITING,
        STATE_CARD_LAST4,
        "wallet_receipt_photo",
        STATE_TICKET_WAITING_TEXT,
        STATE_TICKET_WAITING_TITLE,
        STATE_TICKET_WAITING_PHOTO,
        STATE_TICKET_CONFIRM,
        STATE_COUPON_WAITING,
        STATE_ZARINPAL_WAITING,
        STATE_PERFECT_MONEY_WAITING,
        STATE_CRYPTO_WAITING,
        STATE_CONNECT_WAITING,
        STATE_RENAME_WAITING,
        STATE_REPLACE_LINK_WAITING,
        STATE_TRIAL_WAITING_NAME,
        "wallet_card_amount",
        "coupon_code",
        "zarinpal_amount",
        "perfect_amount",
        "crypto_amount",
        "WAIT_CONNECT_SUB_INPUT",
    }
    if state in routed_states or (isinstance(state, str) and state.startswith("rename:")):
        from CustomerBot.handlers.receipt import receipt_handler
        await receipt_handler(update, context)
        return

    agent_id = context.bot_data.get("agent_id", 0)
    if not agent_id:
        await update.message.reply_text(i18n.t("not_configured", lang))
        return

    if is_rate_limited(f"menu_{user.id}_{text}"):
        return

    text_settings = get_text_settings(agent_id)
    br = get_buy_renew_settings(agent_id)

    if BTN_BUY in text:
        if not br.get("enable_buy", True):
            await update.message.reply_text(
                i18n.t("buy_disabled", lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return
        servers = get_main_servers()
        if not servers:
            await update.message.reply_text(
                i18n.t("no_server_available", lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return
        sc = int(br.get("server_columns", 1))
        await update.message.reply_text(
            get_localized_text(agent_id, "servers_list_text", lang),
            parse_mode="Markdown",
            reply_markup=location_keyboard(servers, columns=sc, lang=lang),
        )

    elif BTN_TRIAL in text:
        u_db = get_user(agent_id, user.id)
        if u_db and u_db.get("got_free_trial"):
            await update.message.reply_text(
                i18n.t("trial_already_used", lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return
        trial = get_trial_spec_settings(agent_id)
        if not trial.get("enabled", True):
            await update.message.reply_text(
                i18n.t("trial_disabled_msg", lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return
        servers = get_main_servers()
        if not servers:
            await update.message.reply_text(
                i18n.t("trial_no_server", lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return
        await update.message.reply_text(
            get_localized_text(agent_id, "servers_list_text", lang),
            parse_mode="Markdown",
            reply_markup=trial_location_keyboard(servers, lang=lang),
        )

    elif BTN_RENEW in text:
        if not br.get("enable_renew", True):
            await update.message.reply_text(
                i18n.t("renew_disabled", lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return
        cust = get_customer_by_telegram_id(agent_id, user.id)
        if not cust:
            await update.message.reply_text(
                i18n.t("user_not_found_msg", lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return
        from Shared.agent_db import get_services_by_customer
        from CustomerBot.services import is_customer_service_visible, service_is_renewable, renew_not_allowed_text, service_is_renewable_live
        services = get_services_by_customer(cust["id"])
        visible = [s for s in services if is_customer_service_visible(s)]
        if not visible:
            await update.message.reply_text(
                i18n.t("no_service_for_renew", lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return
        # قوانین تمدید (حالت پیشرفته): فقط سرویس‌های نزدیک به اتمام حجم/زمان
        renewable = [s for s in visible if await service_is_renewable_live(int(s.get("id") or 0), agent_id)]
        if not renewable:
            await update.message.reply_text(
                renew_not_allowed_text(agent_id, lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return
        await update.message.reply_text(
            i18n.t("renew_pick_prompt", lang),
            reply_markup=renew_services_keyboard(renewable, lang=lang),
        )

    elif BTN_STATUS in text or text == BTN_STATUS:
        cust = get_customer_by_telegram_id(agent_id, user.id)
        if not cust:
            await update.message.reply_text(
                i18n.t("user_not_found_msg", lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return
        from Shared.agent_db import get_services_by_customer
        from CustomerBot.services import sync_service_status_from_panels, is_customer_service_visible
        services = get_services_by_customer(cust["id"])
        for svc in services:
            await sync_service_status_from_panels(svc.get("id", 0))
        services = get_services_by_customer(cust["id"])
        visible = [s for s in services if is_customer_service_visible(s)]
        if not visible:
            await update.message.reply_text(
                i18n.t("no_services_yet", lang),
                reply_markup=main_menu_keyboard(lang=lang),
            )
            return
        if len(visible) > 3:
            await update.message.reply_text(
                i18n.t("status_pick_prompt", lang),
                reply_markup=services_list_keyboard(visible, lang=lang),
            )
            return
        for svc in visible:
            from CustomerBot.services import build_subscription_status_text
            msg = build_subscription_status_text(svc, get_subs_settings(agent_id), br, lang=lang)
            await update.message.reply_text(
                msg,
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(svc.get("id"), lang=lang),
            )

    elif BTN_CONNECT in text:
        context.user_data[UD_STATE] = "WAIT_CONNECT_SUB_INPUT"
        await update.message.reply_text(
            i18n.t("connect_prompt", lang),
            reply_markup=cancel_keyboard(lang=lang),
        )

    elif BTN_SUPPORT in text:
        await update.message.reply_text(
            get_localized_text(agent_id, "ticket_panel_text", lang),
            reply_markup=support_panel_keyboard(lang=lang),
        )

    elif BTN_GUIDE in text:
        await update.message.reply_text(
            get_localized_text(agent_id, "guide_text", lang),
            reply_markup=guide_os_keyboard("m", lang=lang),
        )

    elif BTN_FAQ in text:
        faq = get_faq_text(agent_id, lang)
        await update.message.reply_text(faq, reply_markup=main_menu_keyboard(lang=lang))


async def _handle_agent_msg_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    user = update.effective_user
    agent_id = context.bot_data.get("agent_id", 0)
    lang = i18n.get_customer_lang(agent_id, user.id) if user else "fa"
    if not text:
        await update.message.reply_text(i18n.t("empty_msg", lang))
        return
    if text in {"بازگشت", "لغو", "❌ لغو", "🚫 لغو", "/cancel"}:
        context.user_data.pop(UD_STATE, None)
        await update.message.reply_text(i18n.t("back_to_main_menu", lang), reply_markup=main_menu_keyboard(lang=lang))
        return

    from Shared.agent_db import get_active_customer_bot
    bot_row = get_active_customer_bot(agent_id)
    agent_tg = int((bot_row or {}).get("agent_telegram_id") or 0)
    context.user_data.pop(UD_STATE, None)

    if not agent_tg:
        await update.message.reply_text(i18n.t("agent_msg_failed", lang), reply_markup=main_menu_keyboard(lang=lang))
        return

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    from telegram import Bot as TelegramBot
    import os
    from Shared.agent_db import get_customer_by_telegram_id
    customer = get_customer_by_telegram_id(agent_id, user.id)
    display = str(
        (customer or {}).get("full_name")
        or user.full_name
        or (customer or {}).get("username")
        or user.username
        or user.id
    ).strip()
    agent_bot_token = os.getenv("AGENT_BOT_TOKEN", "").strip()
    if not agent_bot_token:
        await update.message.reply_text(i18n.t("agent_msg_failed", lang), reply_markup=main_menu_keyboard(lang=lang))
        return
    try:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(i18n.t("agent_reply_btn", lang), callback_data=f"agbot:set:users:message:{user.id}")]]
        )
        # پیام باید از طریق «ربات نمایندگی» ارسال شود تا دکمه‌ی پاسخ در
        # چت ربات نمایندگی قرار بگیرد و کلیک روی آن به ربات نمایندگی برگردد.
        await TelegramBot(token=agent_bot_token).send_message(
            chat_id=agent_tg,
            text=i18n.t("agent_msg_from_customer", lang, n=display, i=user.id, t=text),
            reply_markup=kb,
        )
        await update.message.reply_text(i18n.t("agent_msg_sent", lang), reply_markup=main_menu_keyboard(lang=lang))
    except Exception as e:
        import logging
        logger = logging.getLogger("CustomerBot.Menu")
        logger.warning("send agent msg reply failed tg=%s: %s", user.id, e)
        await update.message.reply_text(i18n.t("agent_msg_failed", lang), reply_markup=main_menu_keyboard(lang=lang))
