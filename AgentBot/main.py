import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.error import NetworkError, TimedOut
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AgentBot.handlers.main_menu import handle_start, handle_main_menu_callback, handle_agent_text, handle_language_command
from AgentBot.database import init_db as init_agent_db
from Shared import i18n as _i18n
from Shared import i18n

load_dotenv()
AGENT_BOT_TOKEN = os.getenv("AGENT_BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


async def _sms_webhook_queue_worker(application) -> None:
    """صف تایید خودکار وب‌هوک SMS بانکی را هر ۵ ثانیه پردازش می‌کند.

    وب‌هوک پرداخت‌های تطبیق‌یافته نمایندگی‌ها را در customer_bot.db صف می‌کند؛
    ساخت سرویس و تحویل باید در همین پروسه انجام شود (توکن ربات مشتری اینجاست).
    """
    from AgentBot.handlers.settings_customer_payments import process_sms_webhook_queue
    from AgentBot.database import recover_processing_agent_wallet_sms_payments

    while True:
        try:
            recovered = await asyncio.to_thread(
                recover_processing_agent_wallet_sms_payments, 20
            )
            if recovered:
                logger.info(
                    "Recovered %s interrupted agent wallet SMS approvals", recovered
                )
            await process_sms_webhook_queue(application, limit=5)
        except Exception as e:
            logger.warning("sms webhook queue worker error: %s", e)
        await asyncio.sleep(5)


async def _post_init(application) -> None:
    def _cmds(lang: str):
        return [
            BotCommand("start", _i18n.t("cmd_start", lang)),
            BotCommand("language", _i18n.t("cmd_language", lang)),
            BotCommand("cancel", _i18n.t("cmd_cancel", lang)),
        ]
    try:
        await application.bot.set_my_commands(_cmds("fa"))
        await application.bot.set_my_commands(_cmds("en"), language_code="en")
        await application.bot.set_my_commands(_cmds("ru"), language_code="ru")
    except Exception as e:
        logger.warning("Failed setting bot commands: %s", e)
    try:
        application.create_task(_sms_webhook_queue_worker(application))
    except Exception as e:
        logger.warning("Failed starting sms webhook queue worker: %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("AgentBot error:", exc_info=context.error)


def main() -> None:
    if not AGENT_BOT_TOKEN:
        raise RuntimeError("AGENT_BOT_TOKEN is not set in .env")

    init_agent_db()

    backoff_seconds = 5
    max_backoff_seconds = 60

    while True:
        # python-telegram-bot's run_polling closes the event loop when it stops.
        # Without a fresh loop, the next iteration raises "Event loop is closed".
        try:
            existing_loop = asyncio.get_event_loop_policy().get_event_loop()
            if not existing_loop.is_closed():
                existing_loop.close()
        except RuntimeError:
            pass
        asyncio.set_event_loop(asyncio.new_event_loop())

        application = (
            ApplicationBuilder()
            .token(AGENT_BOT_TOKEN)
            .post_init(_post_init)
            .connect_timeout(15)
            .read_timeout(30)
            .write_timeout(30)
            .pool_timeout(30)
            .build()
        )

        application.add_handler(CommandHandler("start", handle_start))
        application.add_handler(CommandHandler("language", handle_language_command))
        application.add_handler(CommandHandler("cancel", handle_agent_text))
        application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_agent_text))
        application.add_handler(CallbackQueryHandler(handle_main_menu_callback))
        application.add_error_handler(error_handler)

        try:
            logger.info("AgentBot started and polling...")
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
                poll_interval=1.0,
                timeout=30,
            )
            logger.warning("AgentBot polling stopped unexpectedly; restarting in %s seconds.", backoff_seconds)
        except (TimedOut, NetworkError) as e:
            logger.warning("AgentBot polling network error: %s. Restarting in %s seconds.", e, backoff_seconds)
        except Exception as e:
            logger.exception("AgentBot fatal polling error: %s. Restarting in %s seconds.", e, backoff_seconds)

        time.sleep(backoff_seconds)
        backoff_seconds = min(backoff_seconds * 2, max_backoff_seconds)


if __name__ == "__main__":
    main()
