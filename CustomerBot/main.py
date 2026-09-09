import asyncio
import logging
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env")

from telegram import Update, BotCommand
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from Shared.agent_db import get_all_active_customer_bots
from CustomerBot.database import init_db as init_customer_db, get_force_join_settings, get_user
from Shared import agent_reminder
from Shared.env_utils import env_int
from CustomerBot.handlers.start import start_command
from CustomerBot.handlers.menu import menu_handler
from CustomerBot.handlers.callback import callback_handler
from CustomerBot.handlers.receipt import receipt_handler
from CustomerBot.keyboards import force_join_keyboard

logger = logging.getLogger("CustomerBot.Main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

AGENT_REMINDER_INTERVAL = env_int("AGENT_REMINDER_INTERVAL_SECONDS", 1800, minimum=600)


def _is_user_banned(agent_id: int, telegram_id: int) -> bool:
    try:
        u = get_user(agent_id, telegram_id)
        return bool(u and int(u.get("is_banned") or 0) == 1)
    except Exception:
        # Do not fail open when the ban database is unavailable.
        logger.exception("Failed to check customer ban status (agent=%s user=%s)", agent_id, telegram_id)
        return True


async def force_join_middleware(update: Update, context) -> None:
    """قبل از هر handler چک میکند کاربر مسدود نیست و عضو کانال هست.

    ترتیب کنترل (سخت‌گیری امنیتی):
    ۱) اعتبار agent_id (خطای پیکربندی → توقف + لاگ امن)
    ۲) وجود effective_user
    ۳) وضعیت ban کاربر در هر Update (خطای دیتابیس → Fail Closed)
    ۴) فقط پس از عبور از کنترل ban، callback نوع forcejoin:* آزاد میشود.
    """
    agent_id = context.bot_data.get("agent_id", 0)
    if not agent_id:
        # خطای پیکربندی — هیچ handler تجاری نباید اجرا شود.
        logger.error("CustomerBot misconfigured: agent_id is missing; update blocked.")
        raise ApplicationHandlerStop

    user = update.effective_user
    if not user:
        logger.warning("CustomerBot: update without effective_user blocked (agent=%s).", agent_id)
        raise ApplicationHandlerStop

    # اگر کاربر مسدود شده باشد، از همه فعالیتها جلوگیری کن — شامل forcejoin:*.
    if _is_user_banned(agent_id, user.id):
        if update.callback_query:
            try:
                await update.callback_query.answer(
                    "🚫 حساب شما توسط مدیر مسدود شده است.",
                    show_alert=True,
                )
            except Exception:
                pass
        elif update.message and update.message.text:
            try:
                await update.message.reply_text("🚫 حساب شما توسط مدیر مسدود شده است.")
            except Exception:
                pass
        raise ApplicationHandlerStop

    # callback دکمه بررسی عضویت رو رد کن — خودش چک میکنه.
    # این آزادسازی فقط پس از عبور از کنترل ban انجام میشود.
    if update.callback_query and (update.callback_query.data or "").startswith("forcejoin:"):
        return

    fjs = get_force_join_settings(agent_id)
    if not fjs.get("enabled") or not fjs.get("channel_username"):
        return

    ch = str(fjs["channel_username"])
    chat_target = ch if ch.lstrip("-").isdigit() else f"@{ch}"
    link = fjs.get("channel_link") or (f"https://t.me/{ch}" if not ch.lstrip("-").isdigit() else "")
    guide = fjs.get("guide_text", "🔒 لطفاً ابتدا در کانال پشتیبانی عضو شوید.\nپس از عضویت روی «✅ بررسی عضویت» بزنید.")

    allowed_statuses = {"member", "administrator", "creator", "owner"}
    try:
        member = await context.bot.get_chat_member(chat_target, user.id)
        status = str(getattr(member, "status", "")).lower()
        if status not in allowed_statuses:
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(guide, reply_markup=force_join_keyboard(link))
            elif update.message:
                await update.message.reply_text(guide, reply_markup=force_join_keyboard(link))
            raise ApplicationHandlerStop
    except ApplicationHandlerStop:
        raise
    except Exception as e:
        # ربات ادمین کانال نیست یا کانال اشتباه است — کاربر را مسدود نکن،
        # فقط راهنمای عضویت را نشان بده تا دوباره تلاش کند و لاگ بزن.
        logger.warning(
            "force_join: getChatMember failed for agent=%d chat=%s — "
            "make sure the bot is admin in the channel. Error: %s",
            agent_id, chat_target, e,
        )
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(guide, reply_markup=force_join_keyboard(link))
        elif update.message:
            await update.message.reply_text(guide, reply_markup=force_join_keyboard(link))
        raise ApplicationHandlerStop


async def _post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "راه‌اندازی ربات"),
        ])
    except Exception as e:
        logger.warning("Failed to set customer bot commands: %s", e)


async def _customer_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """سراسری: خطای پردازش هر Update را با جزئیات کافی و بدون دادهٔ حساس ثبت میکند."""
    error = context.error
    agent_id = context.bot_data.get("agent_id", 0)

    # شناسه‌ها/نوع Update را استخراج کن؛ هرگز callback_data یا متن پیام را لاگ نکن.
    update_id = None
    user_id = None
    update_kind = "unknown"
    if isinstance(update, Update):
        update_id = update.update_id
        user = update.effective_user
        if user:
            user_id = user.id
        if update.callback_query:
            update_kind = "callback"
        elif update.message:
            update_kind = "message"
        elif update.edited_message:
            update_kind = "edited_message"
        elif update.pre_checkout_query:
            update_kind = "pre_checkout_query"

    if isinstance(error, (NetworkError, TimedOut)):
        logger.warning(
            "CustomerBot transient network error (agent=%s update_id=%s tg_user=%s kind=%s): %s: %s",
            agent_id, update_id, user_id, update_kind,
            type(error).__name__, error,
        )
        return

    if error is not None:
        logger.error(
            "CustomerBot unhandled exception (agent=%s update_id=%s tg_user=%s kind=%s, %s)",
            agent_id, update_id, user_id, update_kind, type(error).__name__,
            exc_info=(type(error), error, error.__traceback__),
        )
    else:
        logger.error(
            "CustomerBot unhandled exception (agent=%s update_id=%s tg_user=%s kind=%s, unknown error)",
            agent_id, update_id, user_id, update_kind,
        )

    # پیام عمومی کوتاه — شکست ارسالش نباید error handler را دوباره خراب کند.
    generic_text = "❌ خطایی رخ داد. لطفاً دوباره تلاش کنید."
    try:
        if isinstance(update, Update) and update.callback_query:
            await update.callback_query.answer(text=generic_text, show_alert=True)
        elif isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(generic_text)
    except Exception as send_err:
        logger.debug(
            "CustomerBot failed to deliver generic error notice (agent=%s): %s: %s",
            agent_id, type(send_err).__name__, send_err,
        )


async def run_single_bot(token: str, agent_id: int):
    app = (
        ApplicationBuilder()
        .token(token)
        .build()
    )

    app.bot_data["agent_id"] = agent_id
    app.add_error_handler(_customer_error_handler)

    app.add_handler(MessageHandler(filters.ALL, force_join_middleware), group=-1)
    app.add_handler(CallbackQueryHandler(force_join_middleware), group=-1)

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT, menu_handler))
    app.add_handler(MessageHandler(filters.PHOTO, menu_handler))

    try:
        bot_user = await app.bot.get_me()
        await _post_init(app)
        logger.info("Agent #%d bot @%s started", agent_id, bot_user.username or "?")
    except Exception as e:
        logger.error("Agent #%d invalid token: %s", agent_id, e)
        return

    async with app:
        await app.start()
        if app.updater:
            await app.updater.start_polling(drop_pending_updates=False)
        import time as _t
        last_reminder_ts = 0.0
        while True:
            now = _t.monotonic()
            if now - last_reminder_ts >= AGENT_REMINDER_INTERVAL:
                try:
                    summary = await agent_reminder.run_agent_reminder_cycle(app.bot, agent_id)
                    logger.info(
                        'Agent #%d reminder: scanned=%s days=%s usage=%s expired=%s',
                        agent_id,
                        summary['scanned'],
                        summary['days_sent'],
                        summary['usage_sent'],
                        summary['expired_sent'],
                    )
                except Exception as e:
                    logger.warning('Agent #%d reminder error: %s', agent_id, e)
                last_reminder_ts = now
            await asyncio.sleep(60)


async def _run_single_bot_guarded(token: str, agent_id: int) -> None:
    """خرابی یک ربات مشتری را ایزوله میکند تا بقیهٔ رباتهای همان پروسه بالا بمانند."""
    try:
        await run_single_bot(token, agent_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Customer bot task crashed (agent=%d)", agent_id)


async def main():
    init_customer_db()
    bots = get_all_active_customer_bots()
    if not bots:
        logger.info("No active customer bots found. Waiting 30s then retrying...")
        await asyncio.sleep(30)
        bots = get_all_active_customer_bots()
        if not bots:
            logger.warning("Still no active customer bots. Exiting.")
            return

    logger.info("Starting %d customer bot(s)", len(bots))
    tasks = [
        asyncio.create_task(_run_single_bot_guarded(b["bot_token"], b["agent_id"]))
        for b in bots if b.get("bot_token")
    ]
    if not tasks:
        logger.warning("No bots with valid tokens found.")
        return
    # Wrapper هر exception غیر-cancel را میبلعد؛ پس خرابی یک ربات
    # باعث لغو task سایر رباتها نمیشود. CancelledError به بیرون
    # پرتاب میشود تا لغو طبیعی کل پروسه ممکن باشد.
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
