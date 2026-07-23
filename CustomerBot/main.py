import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from Shared.agent_db import get_all_active_customer_bots
from CustomerBot.database import init_db as init_customer_db
from CustomerBot.handlers.start import start_command
from CustomerBot.handlers.menu import menu_handler
from CustomerBot.handlers.callback import callback_handler
from CustomerBot.handlers.receipt import receipt_handler

logger = logging.getLogger("CustomerBot.Main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))


async def _post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "راه‌اندازی ربات"),
        ])
    except Exception as e:
        logger.warning("Failed to set customer bot commands: %s", e)


async def run_single_bot(token: str, agent_id: int):
    app = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(True)
        .build()
    )

    app.bot_data["agent_id"] = agent_id

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
            await app.updater.start_polling(drop_pending_updates=True)
        while True:
            await asyncio.sleep(3600)


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
    tasks = [run_single_bot(b["bot_token"], b["agent_id"]) for b in bots if b.get("bot_token")]
    if not tasks:
        logger.warning("No bots with valid tokens found.")
        return
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
