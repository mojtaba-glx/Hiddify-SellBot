# AdminBot/main.py
# لانچر سبک برای ربات ادمین

import asyncio
import logging
import os
import sys
import math
from types import SimpleNamespace
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, Bot
from telegram.error import Forbidden, BadRequest
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
from Shared import userbot_db  # noqa: E402

# ===============================
#   تنظیمات عمومی
# ===============================
load_dotenv()
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
USER_BOT_TOKEN = os.getenv("USER_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
GLOBAL_ENFORCER_ENABLED = (os.getenv("GLOBAL_ENFORCER_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}
GLOBAL_ENFORCER_INTERVAL = max(10, int(os.getenv("GLOBAL_ENFORCER_INTERVAL_SECONDS", "20") or "20"))
NODE_MONITOR_ENABLED = (os.getenv("NODE_MONITOR_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}
NODE_MONITOR_INTERVAL = int(os.getenv("NODE_MONITOR_INTERVAL_SECONDS", "180") or "180")
SUB_REMINDER_INTERVAL = max(60, int(os.getenv("SUB_REMINDER_INTERVAL_SECONDS", "300") or "300"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
_USER_NOTIFY_BOT: Bot | None = None

# Reduce third-party HTTP verbosity to avoid leaking bot tokens in request URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_gb(value: float) -> str:
    try:
        v = float(value)
    except Exception:
        v = 0.0
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.1f}"


def _is_unlimited_volume(limit_gb: float, br: dict) -> bool:
    if not bool(br.get("renew_unlimited_volume", False)):
        return False
    try:
        threshold = float(br.get("renew_unlimited_volume_from_gb") or 1000)
    except (TypeError, ValueError):
        threshold = 1000.0
    return float(limit_gb) >= threshold


def _is_unlimited_time(days_val: int, br: dict) -> bool:
    if not bool(br.get("renew_unlimited_time", False)):
        return False
    try:
        threshold = int(br.get("renew_unlimited_time_from_days") or 365)
    except (TypeError, ValueError):
        threshold = 365
    return int(days_val) >= threshold


def _get_user_notify_bot() -> Bot | None:
    global _USER_NOTIFY_BOT
    if not USER_BOT_TOKEN:
        return None
    if _USER_NOTIFY_BOT is None:
        _USER_NOTIFY_BOT = Bot(token=USER_BOT_TOKEN)
    return _USER_NOTIFY_BOT


async def _run_subscription_reminder_cycle() -> dict:
    summary = {"scanned": 0, "days_sent": 0, "usage_sent": 0, "unreachable": 0, "errors": 0}
    reminder = userbot_db.get_sub_reminder_settings()
    if not bool(reminder.get("enabled", True)):
        return summary

    bot = _get_user_notify_bot()
    if bot is None:
        logger.warning("Subscription reminders skipped: USER_BOT_TOKEN is not set")
        return summary

    br = userbot_db.get_buy_renew_settings()
    days_threshold = max(1, int(br.get("renew_max_days") or 3))
    usage_threshold = max(0.1, float(br.get("renew_max_remaining_gb") or 3))
    services = userbot_db.get_services_for_reminder()
    sent_days_keys: set[tuple[int, int]] = set()
    sent_usage_keys: set[tuple[int, int]] = set()

    for svc in services:
        summary["scanned"] += 1
        try:
            service_id = int(svc.get("id") or 0)
            telegram_id = int(svc.get("telegram_id") or 0)
            if service_id <= 0 or telegram_id <= 0:
                continue

            usage_current = _to_float(svc.get("usage_current"), 0.0)
            usage_limit = _to_float(svc.get("usage_limit"), 0.0)
            try:
                days_left = int(svc.get("days_left"))
            except Exception:
                days_left = 0

            unlimited_time = _is_unlimited_time(days_left, br)
            unlimited_volume = _is_unlimited_volume(usage_limit, br)

            remaining_gb = (usage_limit - usage_current) if usage_limit > 0 else -1.0

            state = userbot_db.get_service_reminder_state(service_id)
            last_days_notified = int(state.get("days_sent", -1))
            last_usage_notified = int(state.get("usage_sent", -1))

            should_days = (not unlimited_time) and days_left >= 0 and days_left <= days_threshold
            remaining_bucket = int(max(0, math.ceil(remaining_gb))) if remaining_gb >= 0 else -1
            should_usage = (
                (not unlimited_volume)
                and usage_limit > 0
                and remaining_gb >= 0
                and remaining_bucket <= int(math.ceil(usage_threshold))
            )

            new_days_state = last_days_notified
            new_usage_state = last_usage_notified

            if should_days and days_left != last_days_notified:
                day_key = (telegram_id, days_left)
                if day_key not in sent_days_keys:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=(
                            "🚨 مهلت اشتراک شما به زودی به اتمام میرسد، هرچه زودتر برای تمدید آن اقدام کنید.\n"
                            f"📅 تعداد روز های باقی مانده: {days_left} روز"
                        ),
                    )
                    sent_days_keys.add(day_key)
                    summary["days_sent"] += 1
                new_days_state = days_left
            elif not should_days and last_days_notified != -1:
                # Reset arm when service gets healthy again (after renew/upgrade).
                new_days_state = -1

            if should_usage and remaining_bucket != last_usage_notified:
                usage_key = (telegram_id, remaining_bucket)
                if usage_key not in sent_usage_keys:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=(
                            "🚨 مهلت اشتراک شما به زودی به اتمام میرسد، هرچه زودتر برای تمدید آن اقدام کنید.\n"
                            f"🚥 حجم ترافیک باقی مانده کمتر: {remaining_bucket} گیگ"
                        ),
                    )
                    sent_usage_keys.add(usage_key)
                    summary["usage_sent"] += 1
                new_usage_state = remaining_bucket
            elif not should_usage and last_usage_notified != -1:
                # Reset arm when user is out of reminder window.
                new_usage_state = -1

            if new_days_state != last_days_notified or new_usage_state != last_usage_notified:
                userbot_db.set_service_reminder_state(
                    service_id,
                    days_sent=new_days_state,
                    usage_sent=new_usage_state,
                )
        except Exception as e:
            msg = str(e or "").strip().lower()
            if isinstance(e, (Forbidden, BadRequest)) and (
                "chat not found" in msg
                or "forbidden" in msg
                or "blocked" in msg
                or "bot was blocked" in msg
            ):
                summary["unreachable"] += 1
            else:
                summary["errors"] += 1
                logger.warning("Subscription reminder error on service_id=%s: %s", svc.get("id"), e)

    return summary


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
    summary = await service_enforcer.run_global_usage_enforcer(scan_all=True)
    reminder_summary = await _run_subscription_reminder_cycle()
    await message.reply_text(
        "✅ اجرای کنترل سراسری تمام شد.\n"
        f"سرویس بررسی‌شده: {summary['services_scanned']} از {summary.get('services_total', summary['services_scanned'])}\n"
        f"سرویس همگام‌شده: {summary['services_synced']}\n"
        f"سرویس قطع‌شده: {summary['services_disabled']}\n"
        f"نود قطع‌شده: {summary['nodes_disabled']}\n"
        f"نود قطع‌ناموفق: {summary['nodes_disable_failed']}\n"
        f"خطا: {summary['errors']}\n\n"
        f"🔔 یادآور تمدید: روز={reminder_summary['days_sent']} | حجم={reminder_summary['usage_sent']} | دسترسی‌ندارد={reminder_summary['unreachable']} | خطا={reminder_summary['errors']}"
    )


async def _enforcer_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    summary = await service_enforcer.run_global_usage_enforcer(scan_all=False)
    reminder_summary = {"days_sent": 0, "usage_sent": 0, "unreachable": 0, "errors": 0}
    now_ts = int(asyncio.get_running_loop().time())
    bot_data = context.application.bot_data if context and context.application else {}
    last_ts = int(bot_data.get("_sub_reminder_last_ts") or 0)
    if (now_ts - last_ts) >= SUB_REMINDER_INTERVAL:
        reminder_summary = await _run_subscription_reminder_cycle()
        bot_data["_sub_reminder_last_ts"] = now_ts
    logger.info(
        "Global enforcer cycle done: scanned=%s/%s synced=%s services_disabled=%s nodes_disabled=%s nodes_disable_failed=%s errors=%s | reminders: days=%s usage=%s unreachable=%s errors=%s",
        summary["services_scanned"],
        summary.get("services_total", summary["services_scanned"]),
        summary["services_synced"],
        summary["services_disabled"],
        summary["nodes_disabled"],
        summary["nodes_disable_failed"],
        summary["errors"],
        reminder_summary["days_sent"],
        reminder_summary["usage_sent"],
        reminder_summary["unreachable"],
        reminder_summary["errors"],
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
                    interval=GLOBAL_ENFORCER_INTERVAL,
                    first=10,
                ),
                name="global-usage-enforcer-fallback",
            )
        )
        logger.info("✅ Global enforcer fallback scheduler enabled (interval=%ss)", GLOBAL_ENFORCER_INTERVAL)

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
            interval=GLOBAL_ENFORCER_INTERVAL,
            first=10,
            name="global-usage-enforcer",
            job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 30},
        )
        logger.info(
            "✅ Global enforcer enabled (interval=%ss)",
            GLOBAL_ENFORCER_INTERVAL,
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
            job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 60},
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
