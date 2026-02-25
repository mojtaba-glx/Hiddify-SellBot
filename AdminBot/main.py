# AdminBot/main.py
# لانچر سبک برای ربات ادمین

import asyncio
import logging
import os
import sys
from types import SimpleNamespace
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ===============================
#   تنظیم مسیر پروژه برای import ها
# ===============================
# ساختار پروژه: Hiddify-SellBot/
#   ├── Shared/
#   └── AdminBot/
# این فایل داخل AdminBot است؛ باید روت پروژه را به sys.path اضافه کنیم
ROOT_DIR = Path(__file__).resolve().parents[1]  # /home/.../Hiddify-SellBot
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# الان می‌توانیم ماژول‌های داخلی را ایمپورت کنیم
from AdminBot.servers import (  # noqa: E402
    handle_admin_menu,    # پیام‌های متنی ادمین (منوی اصلی + state ها)
    admin_inline_handler, # همه‌ی دکمه‌های inline
    error_handler,        # هندلر خطا
)
from AdminBot.keyboards import admin_main_keyboard  # noqa: E402
from AdminBot.userbot import handle_ticket_screenshot_start, run_userbot_auto_backup_job  # noqa: E402
from Shared import service_enforcer  # noqa: E402
from Shared import node_ops  # noqa: E402

# ===============================
#   تنظیمات عمومی
# ===============================
load_dotenv()
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
GLOBAL_ENFORCER_ENABLED = (os.getenv("GLOBAL_ENFORCER_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}
GLOBAL_ENFORCER_INTERVAL = int(os.getenv("GLOBAL_ENFORCER_INTERVAL_SECONDS", "300") or "300")
NODE_MONITOR_ENABLED = (os.getenv("NODE_MONITOR_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}
NODE_MONITOR_INTERVAL = int(os.getenv("NODE_MONITOR_INTERVAL_SECONDS", "180") or "180")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Reduce third-party HTTP verbosity to avoid leaking bot tokens in request URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ===============================
#   /start ادمین
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message

    if not user or not message:
        return

    if user.id != ADMIN_ID:
        await message.reply_text("🚫 شما دسترسی ادمین ندارید.")
        return

    payload = ""
    try:
        payload = " ".join(context.args or []).strip()
    except Exception:
        payload = ""
    if payload:
        handled = await handle_ticket_screenshot_start(update, context, payload)
        if handled:
            return

    text = (
        "به ربات مدیریت هیدیفای خوش آمدید 👑\n"
        "از منوی زیر یکی از گزینه‌ها را انتخاب کنید."
    )
    await message.reply_text(text, reply_markup=admin_main_keyboard())


async def enforce_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if not user or not message:
        return
    if user.id != ADMIN_ID:
        await message.reply_text("🚫 شما دسترسی ادمین ندارید.")
        return

    await message.reply_text("⏳ در حال اجرای جمع مصرف سراسری و کنترل محدودیت...")
    summary = await service_enforcer.run_global_usage_enforcer()
    await message.reply_text(
        "✅ اجرای کنترل سراسری تمام شد.\n"
        f"سرویس بررسی‌شده: {summary['services_scanned']}\n"
        f"سرویس همگام‌شده: {summary['services_synced']}\n"
        f"سرویس قطع‌شده: {summary['services_disabled']}\n"
        f"نود قطع‌شده: {summary['nodes_disabled']}\n"
        f"خطا: {summary['errors']}"
    )


async def _enforcer_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    summary = await service_enforcer.run_global_usage_enforcer()
    logger.info(
        "Global enforcer cycle done: scanned=%s synced=%s services_disabled=%s nodes_disabled=%s errors=%s",
        summary["services_scanned"],
        summary["services_synced"],
        summary["services_disabled"],
        summary["nodes_disabled"],
        summary["errors"],
    )


async def nodes_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if not user or not message:
        return
    if user.id != ADMIN_ID:
        await message.reply_text("🚫 شما دسترسی ادمین ندارید.")
        return
    await message.reply_text("⏳ در حال بررسی سلامت نودها و تلاش بازیابی...")
    summary = await node_ops.monitor_and_recover_nodes()
    await message.reply_text(
        "✅ بررسی نودها انجام شد.\n"
        f"نود بررسی‌شده: {summary['nodes_scanned']}\n"
        f"نود Up: {summary['nodes_up']}\n"
        f"نود Down: {summary['nodes_down']}\n"
        f"ریکاوری انجام‌شده: {summary['recoveries']}\n"
        f"خطا: {summary['errors']}"
    )


async def _node_monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    summary = await node_ops.monitor_and_recover_nodes()
    logger.info(
        "Node monitor cycle done: scanned=%s up=%s down=%s recoveries=%s errors=%s",
        summary["nodes_scanned"],
        summary["nodes_up"],
        summary["nodes_down"],
        summary["recoveries"],
        summary["errors"],
    )


async def _userbot_auto_backup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_userbot_auto_backup_job(context)


def _build_fallback_context(application) -> SimpleNamespace:
    return SimpleNamespace(bot=application.bot, bot_data=application.bot_data)


async def _run_fallback_loop(
    application,
    *,
    name: str,
    worker,
    interval: int,
    first: int = 0,
) -> None:
    try:
        await asyncio.sleep(max(0, int(first or 0)))
        while True:
            try:
                await worker(_build_fallback_context(application))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Fallback loop '%s' failed in worker cycle", name)
            await asyncio.sleep(max(1, int(interval or 1)))
    except asyncio.CancelledError:
        logger.info("Fallback loop '%s' stopped", name)
        raise


async def _post_init(application) -> None:
    if application.job_queue is not None:
        return

    fallback_tasks = []
    if GLOBAL_ENFORCER_ENABLED:
        fallback_tasks.append(
            application.create_task(
                _run_fallback_loop(
                    application,
                    name="global-usage-enforcer-fallback",
                    worker=_enforcer_job,
                    interval=max(60, GLOBAL_ENFORCER_INTERVAL),
                    first=30,
                ),
                name="global-usage-enforcer-fallback",
            )
        )
        logger.info("✅ Global enforcer fallback scheduler enabled")

    if NODE_MONITOR_ENABLED:
        fallback_tasks.append(
            application.create_task(
                _run_fallback_loop(
                    application,
                    name="node-monitor-fallback",
                    worker=_node_monitor_job,
                    interval=max(60, NODE_MONITOR_INTERVAL),
                    first=45,
                ),
                name="node-monitor-fallback",
            )
        )
        logger.info("✅ Node monitor fallback scheduler enabled")

    fallback_tasks.append(
        application.create_task(
            _run_fallback_loop(
                application,
                name="userbot-auto-backup-fallback",
                worker=_userbot_auto_backup_job,
                interval=60,
                first=20,
            ),
            name="userbot-auto-backup-fallback",
        )
    )
    logger.info("✅ Userbot auto backup fallback scheduler enabled (interval=60s)")

    application.bot_data["_fallback_tasks"] = fallback_tasks


async def _post_shutdown(application) -> None:
    tasks = list(application.bot_data.get("_fallback_tasks") or [])
    for t in tasks:
        try:
            t.cancel()
        except Exception:
            pass
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    application.bot_data["_fallback_tasks"] = []


# ===============================
#   main
# ===============================
def main() -> None:
    if not ADMIN_BOT_TOKEN:
        raise RuntimeError("❌ متغیر ADMIN_BOT_TOKEN در فایل .env تنظیم نشده است.")

    application = (
        ApplicationBuilder()
        .token(ADMIN_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # /start — همین فایل
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("enforce_now", enforce_now))
    application.add_handler(CommandHandler("nodes_health", nodes_health))

    # همه‌ی پیام‌های متنی — داخل AdminBot/servers.py
    application.add_handler(
        MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, handle_admin_menu)
    )

    # همه‌ی دکمه‌های inline — داخل AdminBot/servers.py
    application.add_handler(CallbackQueryHandler(admin_inline_handler))

    # هندلر خطا — داخل AdminBot/servers.py
    application.add_error_handler(error_handler)

    if GLOBAL_ENFORCER_ENABLED and application.job_queue is not None:
        application.job_queue.run_repeating(
            _enforcer_job,
            interval=max(60, GLOBAL_ENFORCER_INTERVAL),
            first=30,
            name="global-usage-enforcer",
        )
        logger.info(
            "✅ Global enforcer enabled (interval=%ss)",
            max(60, GLOBAL_ENFORCER_INTERVAL),
        )
    elif GLOBAL_ENFORCER_ENABLED:
        logger.warning("⚠️ Global enforcer requested but job_queue is unavailable.")
    else:
        logger.info("ℹ️ Global enforcer disabled by env")

    if NODE_MONITOR_ENABLED and application.job_queue is not None:
        application.job_queue.run_repeating(
            _node_monitor_job,
            interval=max(60, NODE_MONITOR_INTERVAL),
            first=45,
            name="node-monitor",
        )
        logger.info("✅ Node monitor enabled (interval=%ss)", max(60, NODE_MONITOR_INTERVAL))
    elif NODE_MONITOR_ENABLED:
        logger.warning("⚠️ Node monitor requested but job_queue is unavailable.")
    else:
        logger.info("ℹ️ Node monitor disabled by env")

    if application.job_queue is not None:
        application.job_queue.run_repeating(
            _userbot_auto_backup_job,
            interval=60,
            first=20,
            name="userbot-auto-backup",
        )
        logger.info("✅ Userbot auto backup scheduler enabled (interval=60s)")
    else:
        logger.warning("⚠️ Userbot auto backup scheduler unavailable (no job_queue).")

    logger.info("✅ AdminBot started and polling...")
    application.run_polling()


if __name__ == "__main__":
    main()
