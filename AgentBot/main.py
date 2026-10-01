import asyncio
import fcntl
import logging
import os
import sys
import time
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.error import NetworkError, TimedOut
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AgentBot.handlers.main_menu import handle_start, handle_main_menu_callback, handle_agent_text
from AgentBot.database import init_db as init_agent_db
from Shared import secure_io
from Shared.env_utils import env_float, env_int

load_dotenv()
AGENT_BOT_TOKEN = os.getenv("AGENT_BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# The HTTP webhook runs in UserBot and hands work to AgentBot through SQLite.
# Keep this poll short so a queued bank SMS starts fulfillment promptly.  The
# operation itself remains protected by the existing payment/idempotency locks.
AGENT_SMS_QUEUE_POLL_SECONDS = env_float(
    "AGENT_SMS_QUEUE_POLL_SECONDS", 0.75, minimum=0.25, maximum=10.0
)
AGENT_SMS_QUEUE_BATCH_SIZE = env_int(
    "AGENT_SMS_QUEUE_BATCH_SIZE", 10, minimum=1, maximum=50
)
AGENT_DAILY_REPORT_ENABLED = (
    os.getenv("AGENT_DAILY_REPORT_ENABLED", "1") or "1"
).strip().lower() in {"1", "true", "yes", "on"}
AGENT_DAILY_REPORT_TIMEZONE = (
    os.getenv("AGENT_DAILY_REPORT_TIMEZONE", "Asia/Tehran") or "Asia/Tehran"
).strip()


async def _sms_webhook_queue_worker(application) -> None:
    """صف تایید خودکار وب‌هوک SMS بانکی را با تأخیر کمتر از یک ثانیه می‌خواند.

    وب‌هوک پرداخت‌های تطبیق‌یافته نمایندگی‌ها را در customer_bot.db صف می‌کند؛
    ساخت سرویس و تحویل باید در همین پروسه انجام شود (توکن ربات مشتری اینجاست).
    """
    from AgentBot.handlers.settings_customer_payments import process_sms_webhook_queue

    while True:
        processed = 0
        try:
            processed = await process_sms_webhook_queue(
                application, limit=AGENT_SMS_QUEUE_BATCH_SIZE
            )
        except Exception as e:
            logger.warning("sms webhook queue worker error: %s", e)
        # If a full batch was completed, drain the next batch immediately.
        # Otherwise avoid a busy loop while still starting new SMS approvals
        # well below the previous 20-second latency.
        if processed >= AGENT_SMS_QUEUE_BATCH_SIZE:
            await asyncio.sleep(0)
        else:
            await asyncio.sleep(AGENT_SMS_QUEUE_POLL_SECONDS)




def _agent_report_tz() -> ZoneInfo:
    try:
        return ZoneInfo(AGENT_DAILY_REPORT_TIMEZONE)
    except Exception:
        return ZoneInfo("Asia/Tehran")


async def _send_daily_agent_financial_reports(bot, *, now: datetime | None = None) -> dict:
    """Send the completed previous calendar day's report to every active reseller."""
    from AgentBot.handlers.finance import build_financial_report_text
    from Shared import agent_db

    tz = _agent_report_tz()
    local_now = now.astimezone(tz) if now and now.tzinfo else datetime.now(tz)
    report_day = (local_now.date() - timedelta(days=1)).isoformat()
    summary = {"day": report_day, "sent": 0, "skipped": 0, "failed": 0}

    for agent in agent_db.get_all_active_agents():
        agent_id = int(agent.get("id") or 0)
        telegram_id = int(agent.get("telegram_id") or 0)
        if agent_id <= 0 or telegram_id <= 0:
            summary["failed"] += 1
            continue
        if agent_db.was_agent_daily_report_sent(agent_id, report_day):
            summary["skipped"] += 1
            continue

        text = build_financial_report_text(
            agent_id,
            report_day=report_day,
            label=f"گزارش روزانه {report_day}",
        )
        try:
            await bot.send_message(chat_id=telegram_id, text=text, parse_mode="HTML")
            agent_db.mark_agent_daily_report_sent(agent_id, report_day)
            summary["sent"] += 1
        except Exception as exc:
            summary["failed"] += 1
            logger.warning(
                "Daily reseller report failed agent=%s telegram=%s: %s",
                agent_id,
                telegram_id,
                secure_io.redact_sensitive_text(str(exc)),
            )
        await asyncio.sleep(0.05)

    logger.info(
        "Daily reseller financial reports day=%s sent=%s skipped=%s failed=%s",
        summary["day"],
        summary["sent"],
        summary["skipped"],
        summary["failed"],
    )
    return summary


async def _daily_agent_financial_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not AGENT_DAILY_REPORT_ENABLED:
        return
    await _send_daily_agent_financial_reports(context.bot)


async def _daily_agent_report_fallback_loop(application) -> None:
    """Fallback for installs without PTB JobQueue; persistent dedupe makes it safe."""
    while True:
        try:
            now = datetime.now(_agent_report_tz())
            if now.hour == 0:
                await _send_daily_agent_financial_reports(application.bot, now=now)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily reseller report fallback cycle failed")
        await asyncio.sleep(60)


async def _daily_agent_report_startup_catchup(application) -> None:
    """If AgentBot restarts during 00:xx, still deliver that night's report once."""
    try:
        await asyncio.sleep(5)
        now = datetime.now(_agent_report_tz())
        if now.hour == 0:
            await _send_daily_agent_financial_reports(application.bot, now=now)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Daily reseller report startup catch-up failed")

async def _post_init(application) -> None:
    try:
        from AgentBot.handlers.settings_customer_payments import recover_customer_payment_operations
        recovery = recover_customer_payment_operations()
        if any(recovery.values()):
            logger.warning("Customer payment recovery: %s", recovery)
    except Exception as e:
        logger.exception("Customer payment recovery failed: %s", e)

    # Keep Telegram's command menu minimal: /start only.
    # /cancel still works when typed manually; it is simply hidden from the menu.
    commands = [
        BotCommand("start", "پنل نمایندگی"),
    ]
    try:
        await application.bot.set_my_commands(commands)
    except Exception as e:
        logger.warning("Failed setting bot commands: %s", e)
    try:
        application.create_task(_sms_webhook_queue_worker(application))
    except Exception as e:
        logger.warning("Failed starting sms webhook queue worker: %s", e)

    if AGENT_DAILY_REPORT_ENABLED:
        try:
            application.create_task(_daily_agent_report_startup_catchup(application))
        except Exception as e:
            logger.warning("Failed starting daily reseller report catch-up: %s", e)
        if application.job_queue is None:
            try:
                application.create_task(_daily_agent_report_fallback_loop(application))
                logger.info(
                    "Daily reseller report fallback enabled (00:00 %s)",
                    AGENT_DAILY_REPORT_TIMEZONE,
                )
            except Exception as e:
                logger.warning("Failed starting daily reseller report fallback: %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("AgentBot error:", exc_info=context.error)


_AGENTBOT_PID_FILE = str(ROOT_DIR / "logs" / "agentbot.pid")
_pid_lock_fd = None  # global file descriptor for flock


def _acquire_pid_lock() -> bool:
    """Prevent multiple AgentBot instances by PID file locking.

    Uses fcntl.flock for atomic lock acquisition to prevent race conditions.
    If a stale instance is found via the PID file, kill it and take over.
    Without this guard, two processes polling the same token each keep their
    own in-memory wizard state (wiz_gb/rewiz_gb), so rapid +10/-10 taps get
    load-balanced across desynced states and the volume jumps or goes up
    on minus. Mirrors UserBot's single-instance guard.
    """
    global _pid_lock_fd
    try:
        os.makedirs(os.path.dirname(_AGENTBOT_PID_FILE), exist_ok=True)

        # Open PID file with exclusive non-blocking lock (atomic operation)
        _pid_lock_fd = open(_AGENTBOT_PID_FILE, "a+")
        try:
            fcntl.flock(_pid_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.error("Another AgentBot instance is already running. Exiting.")
            try:
                _pid_lock_fd.close()
            except Exception:
                pass
            _pid_lock_fd = None
            return False

        # Read old PID (if any)
        _pid_lock_fd.seek(0)
        old_pid_str = _pid_lock_fd.read().strip()

        if old_pid_str:
            try:
                old_pid = int(old_pid_str)
            except ValueError:
                old_pid = None

            if old_pid is not None:
                if old_pid == os.getpid():
                    logger.debug("PID file contains our own PID (written by start script). Overwriting.")
                elif os.path.exists(f"/proc/{old_pid}"):
                    logger.warning("Stale AgentBot PID %s found. Sending SIGTERM...", old_pid)
                    try:
                        os.kill(old_pid, 15)
                        for _ in range(10):
                            if not os.path.exists(f"/proc/{old_pid}"):
                                break
                            time.sleep(0.5)
                        if os.path.exists(f"/proc/{old_pid}"):
                            os.kill(old_pid, 9)
                            time.sleep(0.5)
                        logger.info("Old AgentBot instance (PID %s) terminated.", old_pid)
                    except ProcessLookupError:
                        pass
                    except Exception as e:
                        logger.warning("Failed to kill old PID %s: %s", old_pid, e)
                else:
                    logger.warning("Stale PID file found for PID %s. Removing.", old_pid)

        # Write our PID (atomic because we hold the lock)
        _pid_lock_fd.seek(0)
        _pid_lock_fd.truncate()
        _pid_lock_fd.write(str(os.getpid()))
        _pid_lock_fd.flush()

        # Keep _pid_lock_fd open to hold the lock for the lifetime of the process
        return True
    except Exception as e:
        logger.warning("Failed to acquire PID lock: %s", e)
        return True  # Fallback: allow startup even if locking fails


def _release_pid_lock() -> None:
    global _pid_lock_fd
    try:
        if _pid_lock_fd is not None:
            try:
                fcntl.flock(_pid_lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                _pid_lock_fd.close()
            except Exception:
                pass
            _pid_lock_fd = None
        if os.path.exists(_AGENTBOT_PID_FILE):
            try:
                os.remove(_AGENTBOT_PID_FILE)
            except Exception:
                pass
    except Exception:
        pass


def main() -> None:
    if not AGENT_BOT_TOKEN:
        raise RuntimeError("AGENT_BOT_TOKEN is not set in .env")

    if not _acquire_pid_lock():
        sys.exit(1)
    import atexit
    atexit.register(_release_pid_lock)

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
        application.add_handler(CommandHandler("cancel", handle_agent_text))
        application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.IMAGE) & ~filters.COMMAND, handle_agent_text))
        application.add_handler(CallbackQueryHandler(handle_main_menu_callback))
        application.add_error_handler(error_handler)

        if AGENT_DAILY_REPORT_ENABLED and application.job_queue is not None:
            report_tz = _agent_report_tz()
            application.job_queue.run_daily(
                _daily_agent_financial_report_job,
                time=dt_time(hour=0, minute=0, tzinfo=report_tz),
                name="daily-reseller-financial-report",
                job_kwargs={
                    "max_instances": 1,
                    "coalesce": True,
                    "misfire_grace_time": 3600,
                },
            )
            logger.info(
                "Daily reseller financial report enabled (00:00 %s)",
                AGENT_DAILY_REPORT_TIMEZONE,
            )
        elif not AGENT_DAILY_REPORT_ENABLED:
            logger.info("Daily reseller financial report disabled by env")

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
            logger.warning("AgentBot polling network error: %s. Restarting in %s seconds.",
                           secure_io.redact_sensitive_text(str(e)), backoff_seconds)
        except Exception as e:
            # بدون logger.exception: traceback خام میتواند متن Exception
            # حاوی توکن را ثبت کند. فقط نام کلاس و متن پاکسازی‌شده.
            logger.error("AgentBot fatal polling error (%s): %s. Restarting in %s seconds.",
                         type(e).__name__, secure_io.redact_sensitive_text(str(e)), backoff_seconds)

        time.sleep(backoff_seconds)
        backoff_seconds = min(backoff_seconds * 2, max_backoff_seconds)


if __name__ == "__main__":
    main()
