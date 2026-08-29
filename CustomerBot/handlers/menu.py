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
    get_user, get_buy_renew_settings, get_text_settings,
    get_payment_settings, get_marketing_settings, get_trial_spec_settings,
    get_subs_settings, get_force_join_settings,
)
from Shared.agent_db import get_customer_by_telegram_id
from Shared.database import get_servers, get_main_servers
from CustomerBot.keyboards import (
    main_menu_keyboard, location_keyboard, trial_location_keyboard,
    services_list_keyboard, renew_services_keyboard,
    support_panel_keyboard, guide_os_keyboard,
    cancel_keyboard, subscription_status_keyboard,
)
from CustomerBot.utils.helpers import is_rate_limited, format_price


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user
    if not user:
        return

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
            await update.message.reply_text("🔙 به منوی اصلی بازگشتید.", reply_markup=main_menu_keyboard())
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
        await update.message.reply_text("❌ ربات به درستی پیکربندی نشده است.")
        return

    if is_rate_limited(f"menu_{user.id}_{text}"):
        return

    text_settings = get_text_settings(agent_id)
    br = get_buy_renew_settings(agent_id)

    if BTN_BUY in text:
        if not br.get("enable_buy", True):
            await update.message.reply_text(
                "🚫 خرید اشتراک در حال حاضر غیرفعال است.",
                reply_markup=main_menu_keyboard(),
            )
            return
        servers = get_main_servers()
        if not servers:
            await update.message.reply_text(
                "❌ هیچ سروری در دسترس نیست.",
                reply_markup=main_menu_keyboard(),
            )
            return
        sc = int(br.get("server_columns", 1))
        await update.message.reply_text(
            text_settings.get("servers_list_text", "📡 لطفاً لوکیشن مورد نظر را انتخاب کنید:"),
            parse_mode="Markdown",
            reply_markup=location_keyboard(servers, columns=sc),
        )

    elif BTN_TRIAL in text:
        u_db = get_user(agent_id, user.id)
        if u_db and u_db.get("got_free_trial"):
            await update.message.reply_text(
                "🚫 شما قبلا تست رایگان دریافت کرده‌اید!",
                reply_markup=main_menu_keyboard(),
            )
            return
        trial = get_trial_spec_settings(agent_id)
        if not trial.get("enabled", True):
            await update.message.reply_text(
                "🚫 تست رایگان در حال حاضر غیرفعال است.",
                reply_markup=main_menu_keyboard(),
            )
            return
        servers = get_main_servers()
        if not servers:
            await update.message.reply_text(
                "❌ سروری برای تست رایگان در دسترس نیست.",
                reply_markup=main_menu_keyboard(),
            )
            return
        await update.message.reply_text(
            text_settings.get("servers_list_text", "📡 لطفاً لوکیشن را انتخاب کنید:"),
            parse_mode="Markdown",
            reply_markup=trial_location_keyboard(servers),
        )

    elif BTN_RENEW in text:
        if not br.get("enable_renew", True):
            await update.message.reply_text(
                "🚫 تمدید اشتراک در حال حاضر غیرفعال است.",
                reply_markup=main_menu_keyboard(),
            )
            return
        cust = get_customer_by_telegram_id(agent_id, user.id)
        if not cust:
            await update.message.reply_text(
                "❌ کاربر یافت نشد.",
                reply_markup=main_menu_keyboard(),
            )
            return
        from Shared.agent_db import get_services_by_customer
        from CustomerBot.services import is_customer_service_visible, service_is_renewable, renew_not_allowed_text
        services = get_services_by_customer(cust["id"])
        visible = [s for s in services if is_customer_service_visible(s)]
        if not visible:
            await update.message.reply_text(
                "❌ اشتراک فعالی برای تمدید وجود ندارد.",
                reply_markup=main_menu_keyboard(),
            )
            return
        # قوانین تمدید (حالت پیشرفته): فقط سرویس‌های نزدیک به اتمام حجم/زمان
        renewable = [s for s in visible if service_is_renewable(s, agent_id)]
        if not renewable:
            await update.message.reply_text(
                renew_not_allowed_text(agent_id),
                reply_markup=main_menu_keyboard(),
            )
            return
        await update.message.reply_text(
            "👇 لطفا یکی از اشتراک‌ها را برای تمدید انتخاب کنید:",
            reply_markup=renew_services_keyboard(renewable),
        )

    elif BTN_STATUS in text or text == BTN_STATUS:
        cust = get_customer_by_telegram_id(agent_id, user.id)
        if not cust:
            await update.message.reply_text(
                "❌ کاربر یافت نشد.",
                reply_markup=main_menu_keyboard(),
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
                "❌ هیچ سرویس فعال ندارید.\n"
                "💡 برای خرید سرویس جدید، روی دکمه «💳خرید اشتراک» کلیک کنید.",
                reply_markup=main_menu_keyboard(),
            )
            return
        if len(visible) > 3:
            await update.message.reply_text(
                "👇 یکی از اشتراک‌ها را انتخاب کنید:",
                reply_markup=services_list_keyboard(visible),
            )
            return
        for svc in visible:
            from CustomerBot.services import build_subscription_status_text
            msg = build_subscription_status_text(svc, get_subs_settings(agent_id), br)
            await update.message.reply_text(
                msg,
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(svc.get("id")),
            )

    elif BTN_CONNECT in text:
        context.user_data[UD_STATE] = "WAIT_CONNECT_SUB_INPUT"
        await update.message.reply_text(
            "لطفا اطلاعات اشتراک خود را وارد کنید.\n"
            "یکی از کانفیگ‌ها، uuid یا لینک اشتراک را بفرستید:",
            reply_markup=cancel_keyboard(),
        )

    elif BTN_SUPPORT in text:
        await update.message.reply_text(
            text_settings.get("ticket_panel_text", "📩 برای ارتباط با پشتیبانی، پیام خود را ارسال کنید."),
            reply_markup=support_panel_keyboard(),
        )

    elif BTN_GUIDE in text:
        await update.message.reply_text(
            text_settings.get("guide_text", "انتخاب سیستم عامل ⬇️"),
            reply_markup=guide_os_keyboard("m"),
        )

    elif BTN_FAQ in text:
        faq = text_settings.get("faq_text", "")
        if not faq:
            faq = "❗️ سوالات متداول\n\nبه‌زودی تکمیل می‌شود."
        await update.message.reply_text(faq, reply_markup=main_menu_keyboard())


async def _handle_agent_msg_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    user = update.effective_user
    if not text:
        await update.message.reply_text("📩 متن خالی است. لطفا پاسخ خود را بنویسید:")
        return
    if text in {"بازگشت", "لغو", "❌ لغو", "🚫 لغو", "/cancel"}:
        context.user_data.pop(UD_STATE, None)
        await update.message.reply_text("🔙 به منوی اصلی بازگشتید.", reply_markup=main_menu_keyboard())
        return

    agent_id = context.bot_data.get("agent_id", 0)
    from Shared.agent_db import get_active_customer_bot
    bot_row = get_active_customer_bot(agent_id)
    agent_tg = int((bot_row or {}).get("agent_telegram_id") or 0)
    context.user_data.pop(UD_STATE, None)

    if not agent_tg:
        await update.message.reply_text("❌ ارسال پیام ناموفق بود. لطفا دوباره تلاش کنید.", reply_markup=main_menu_keyboard())
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
        await update.message.reply_text("❌ ارسال پیام ناموفق بود. لطفا دوباره تلاش کنید.", reply_markup=main_menu_keyboard())
        return
    try:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📨 پاسخ", callback_data=f"agbot:set:users:message:{user.id}")]]
        )
        # پیام باید از طریق «ربات نمایندگی» ارسال شود تا دکمه‌ی پاسخ در
        # چت ربات نمایندگی قرار بگیرد و کلیک روی آن به ربات نمایندگی برگردد.
        await TelegramBot(token=agent_bot_token).send_message(
            chat_id=agent_tg,
            text=(
                f"📨 پیام از طرف مشتری:\n"
                f"👤 {display} (tg: {user.id})\n\n"
                f"📄 متن: {text}"
            ),
            reply_markup=kb,
        )
        await update.message.reply_text("✅ پاسخ شما برای نماینده ارسال شد.", reply_markup=main_menu_keyboard())
    except Exception as e:
        import logging
        logger = logging.getLogger("CustomerBot.Menu")
        logger.warning("send agent msg reply failed tg=%s: %s", user.id, e)
        await update.message.reply_text("❌ ارسال پیام ناموفق بود. لطفا دوباره تلاش کنید.", reply_markup=main_menu_keyboard())
