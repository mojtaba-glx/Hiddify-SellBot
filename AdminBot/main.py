# AdminBot/main.py
# لانچر سبک برای ربات ادمین

import asyncio
import logging
import os
import sys
import math
import socket
import time
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, timezone
from collections import deque
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import Update, Bot, BotCommand, BotCommandScopeChat, InlineKeyboardMarkup
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
from Shared import server_health  # noqa: E402
from Shared import userbot_db  # noqa: E402
from Shared import database  # noqa: E402
from Shared import agent_enforcer  # noqa: E402
from Shared.tg_button_styles import inline_button as InlineKeyboardButton  # noqa: E402

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
SERVER_HEALTH_ENABLED = (os.getenv("SERVER_HEALTH_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}
SERVER_HEALTH_INTERVAL = max(60, int(os.getenv("SERVER_HEALTH_INTERVAL_SECONDS", "300") or "300"))
SUB_REMINDER_INTERVAL = max(60, int(os.getenv("SUB_REMINDER_INTERVAL_SECONDS", "300") or "300"))
AGENT_ENFORCER_ENABLED = (os.getenv("AGENT_ENFORCER_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}
AGENT_ENFORCER_INTERVAL = max(60, int(os.getenv("AGENT_ENFORCER_INTERVAL_SECONDS", "180") or "180"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
_USER_NOTIFY_BOT: Bot | None = None
BOT_START_TS = time.time()
ADMIN_LOG_PATH = ROOT_DIR / "logs" / "adminbot.log"
USER_LOG_PATH = ROOT_DIR / "logs" / "userbot.log"
ENV_PATH = ROOT_DIR / ".env"
DB_PATH = ROOT_DIR / "Shared" / "hiddify_sellbot.db"
SERVERS_JSON_PATH = ROOT_DIR / "Shared" / "servers.json"
PLANS_JSON_PATH = ROOT_DIR / "Shared" / "plans.json"
VERSION_PATH = ROOT_DIR / "VERSION"

# Reduce third-party HTTP verbosity to avoid leaking bot tokens in request URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _fmt_duration(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _mask_token(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return "—"
    if len(token) <= 12:
        return f"{token[:3]}***"
    return f"{token[:6]}...{token[-4:]}"


def _file_state(path: Path) -> str:
    try:
        if not path.exists():
            return "❌ وجود ندارد"
        st = path.stat()
        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        size_kb = st.st_size / 1024.0
        return f"✅ {size_kb:.1f}KB | {mtime}"
    except Exception as e:
        return f"⚠️ خطا: {e}"


def _tail_lines(path: Path, limit: int = 200) -> list[str]:
    if not path.exists():
        return []
    buf: deque[str] = deque(maxlen=max(10, int(limit)))
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                buf.append(line.rstrip("\n"))
    except Exception:
        return []
    return list(buf)


def _extract_log_stats(lines: list[str]) -> tuple[int, int, list[str]]:
    if not lines:
        return 0, 0, []
    err = 0
    warn = 0
    picked: list[str] = []
    keywords = (" - ERROR - ", "Traceback", "NetworkError", "ConnectError", "timeout", "timed out")
    for ln in lines:
        low = ln.lower()
        if " - error - " in low or "traceback" in low:
            err += 1
        if " - warning - " in low:
            warn += 1
    for ln in reversed(lines):
        if any(k.lower() in ln.lower() for k in keywords):
            picked.append(ln.strip())
        if len(picked) >= 3:
            break
    picked.reverse()
    return err, warn, picked


def _tcp_probe(host: str, port: int, timeout: float = 1.8) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, "ok"
    except Exception as e:
        return False, str(e)


def _build_jobs_summary(context: ContextTypes.DEFAULT_TYPE) -> str:
    app = context.application if context else None
    if not app:
        return "نامشخص"
    jq = getattr(app, "job_queue", None)
    if jq is None:
        fallback_tasks = list(app.bot_data.get("_fallback_tasks") or [])
        running = sum(1 for t in fallback_tasks if not t.done())
        return f"fallback={running}/{len(fallback_tasks)}"

    try:
        jobs = list(jq.jobs())
    except Exception:
        jobs = []
    if not jobs:
        return "0 job"

    parts: list[str] = []
    for j in jobs[:8]:
        name = str(getattr(j, "name", "job"))
        next_t = getattr(j, "next_t", None)
        if next_t is None:
            parts.append(f"{name}=paused")
            continue
        try:
            if isinstance(next_t, datetime):
                ts = next_t.astimezone().strftime("%H:%M:%S")
            else:
                ts = str(next_t)
        except Exception:
            ts = str(next_t)
        parts.append(f"{name}@{ts}")
    if len(jobs) > 8:
        parts.append(f"+{len(jobs)-8} more")
    return " | ".join(parts)


def _split_text(text: str, chunk_size: int = 3800) -> list[str]:
    out: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current:
            out.append(current)
        current = line
    if current:
        out.append(current)
    return out or [text]


def _build_debug_report(context: ContextTypes.DEFAULT_TYPE) -> str:
    now_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    uptime = _fmt_duration(time.time() - BOT_START_TS)

    version = "dev"
    try:
        if VERSION_PATH.exists():
            version = VERSION_PATH.read_text(encoding="utf-8").strip() or "dev"
    except Exception:
        pass

    servers = database.get_servers() or []
    servers_count = len(servers)
    local_cached_users = sum(len(s.get("users") or []) for s in servers if isinstance(s, dict))
    nodes_count = sum(len(s.get("nodes") or []) for s in servers if isinstance(s, dict))

    users_count = services_count = orders_count = payments_count = tickets_count = vouchers_count = 0
    try:
        conn = userbot_db._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM userbot_users")
        users_count = _safe_int(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM userbot_services")
        services_count = _safe_int(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM userbot_orders")
        orders_count = _safe_int(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM userbot_payments")
        payments_count = _safe_int(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM userbot_tickets")
        tickets_count = _safe_int(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM userbot_zarin_vouchers")
        vouchers_count = _safe_int(cur.fetchone()[0], 0)
        conn.close()
    except Exception as e:
        logger.warning("Debug report db count failed: %s", e)

    sub_base = ""
    try:
        sub_base = userbot_db.get_managed_sub_base_url() or "خودکار"
    except Exception:
        sub_base = "خطا"

    reminder = userbot_db.get_sub_reminder_settings()
    trial_spec = userbot_db.get_trial_spec_settings()
    buy_renew = userbot_db.get_buy_renew_settings()

    admin_tail = _tail_lines(ADMIN_LOG_PATH, 220)
    user_tail = _tail_lines(USER_LOG_PATH, 220)
    admin_err, admin_warn, admin_hot = _extract_log_stats(admin_tail)
    user_err, user_warn, user_hot = _extract_log_stats(user_tail)

    tg_ok, tg_msg = _tcp_probe("api.telegram.org", 443)

    panel_checks: list[str] = []
    for s in servers[:5]:
        panel = str((s or {}).get("panel_url") or "").strip()
        sid = _safe_int((s or {}).get("id"), 0)
        if not panel:
            continue
        parsed = urlparse(panel)
        host = (parsed.hostname or "").strip()
        scheme = (parsed.scheme or "https").strip().lower()
        port = parsed.port or (443 if scheme == "https" else 80)
        if not host:
            continue
        ok, msg = _tcp_probe(host, port)
        panel_checks.append(f"#{sid} {host}:{port} {'✅' if ok else '❌'} ({msg if not ok else 'ok'})")

    lines = [
        "🧪 Debug Report",
        f"⏱ زمان محلی: {now_local}",
        f"🌍 UTC: {now_utc}",
        f"📦 نسخه: {version}",
        f"🐍 Python: {sys.version.split()[0]}",
        f"🆔 PID: {os.getpid()} | Uptime: {uptime}",
        "",
        "🔐 ENV",
        f"- ADMIN_ID: {ADMIN_ID}",
        f"- ADMIN_BOT_TOKEN: {_mask_token(ADMIN_BOT_TOKEN or '')}",
        f"- USER_BOT_TOKEN: {_mask_token(USER_BOT_TOKEN or '')}",
        f"- GLOBAL_ENFORCER_ENABLED: {GLOBAL_ENFORCER_ENABLED} ({GLOBAL_ENFORCER_INTERVAL}s)",
        f"- NODE_MONITOR_ENABLED: {NODE_MONITOR_ENABLED} ({NODE_MONITOR_INTERVAL}s)",
        f"- SUB_REMINDER_INTERVAL: {SUB_REMINDER_INTERVAL}s",
        "",
        "📁 Files",
        f"- .env: {_file_state(ENV_PATH)}",
        f"- Shared/hiddify_sellbot.db: {_file_state(DB_PATH)}",
        f"- Shared/servers.json: {_file_state(SERVERS_JSON_PATH)}",
        f"- Shared/plans.json: {_file_state(PLANS_JSON_PATH)}",
        f"- logs/adminbot.log: {_file_state(ADMIN_LOG_PATH)}",
        f"- logs/userbot.log: {_file_state(USER_LOG_PATH)}",
        "",
        "📊 Data",
        f"- servers={servers_count} | nodes={nodes_count} | cached_users={local_cached_users}",
        f"- db_users={users_count} | services={services_count} | orders={orders_count}",
        f"- payments={payments_count} | tickets={tickets_count} | vouchers={vouchers_count}",
        f"- managed_sub_base_url={sub_base}",
        f"- trial_spec: enabled={bool(trial_spec.get('enabled', True))}, usage_gb={trial_spec.get('usage_gb')}, days={trial_spec.get('days')}",
        f"- reminders: enabled={bool(reminder.get('enabled', True))}, usage_gb={reminder.get('usage_gb')}, days={reminder.get('days')}",
        f"- buy/renew: buy={bool(buy_renew.get('enable_buy', True))}, renew={bool(buy_renew.get('enable_renew', True))}",
        "",
        "⚙️ Jobs",
        f"- {_build_jobs_summary(context)}",
        "",
        "🌐 Network",
        f"- Telegram api.telegram.org:443 => {'✅' if tg_ok else '❌'} ({tg_msg if not tg_ok else 'ok'})",
    ]
    if panel_checks:
        lines.append("- Panel probes:")
        for item in panel_checks:
            lines.append(f"  {item}")

    lines.extend(
        [
            "",
            "📜 Log Snapshot",
            f"- adminbot: errors={admin_err} warnings={admin_warn}",
            f"- userbot: errors={user_err} warnings={user_warn}",
        ]
    )
    if admin_hot:
        lines.append("- adminbot recent issues:")
        for ln in admin_hot:
            lines.append(f"  {ln[:220]}")
    if user_hot:
        lines.append("- userbot recent issues:")
        for ln in user_hot:
            lines.append(f"  {ln[:220]}")

    return "\n".join(lines)


async def _set_admin_commands(application) -> None:
    commands = [
        BotCommand("start", "منوی اصلی ادمین"),
        BotCommand("debug", "گزارش اشکال‌زدایی کامل"),
        BotCommand("enforce_now", "اجرای فوری کنترل مصرف"),
        BotCommand("agent_enforce", "اجرای فوری کنترل مصرف نمایندگی"),
        BotCommand("nodes_health", "بررسی سلامت نودها"),
    ]
    try:
        await application.bot.set_my_commands(commands)
    except Exception as e:
        logger.warning("Failed setting global bot commands: %s", e)
    if ADMIN_ID > 0:
        try:
            await application.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
        except Exception as e:
            logger.warning("Failed setting admin-scope commands: %s", e)


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


def _build_renewal_reminder_message(
    service_name: str,
    *,
    days_left: int | None = None,
    remaining_gb: float | None = None,
) -> str:
    title = str(service_name or "").strip() or "اشتراک شما"
    lines = [
        "🚨 یادآوری تمدید اشتراک",
        f"🔹 اشتراک: «{title}»",
    ]
    if days_left is not None:
        lines.append(f"📅 روز باقی‌مانده: {int(days_left)} روز")
    elif remaining_gb is not None:
        lines.append(f"🚥 حجم باقی‌مانده: {_format_gb(remaining_gb)} گیگ")
    lines.append("لطفاً برای جلوگیری از قطع سرویس، اشتراک را تمدید کنید.")
    return "\n".join(lines)


def _build_expired_notice_message(service_name: str, *, reason: str = "time") -> str:
    title = str(service_name or "").strip() or "اشتراک شما"
    if reason == "usage":
        detail = "حجم اشتراک شما به اتمام رسیده است."
    elif reason == "both":
        detail = "حجم و مدت اشتراک شما به اتمام رسیده است."
    else:
        detail = "مدت اشتراک شما به اتمام رسیده است."
    return "\n".join(
        [
            "⚠️ اشتراک شما منقضی شد",
            f"🔹 اشتراک: «{title}»",
            f"متأسفانه {detail}",
            "لطفاً جهت تمدید از دکمه زیر اقدام فرمایید.",
        ]
    )


def _build_renew_button_keyboard(service_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("تمدید اشتراک", callback_data=f"status:renew:{int(service_id or 0)}", style="danger")]]
    )


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
    summary = {"scanned": 0, "days_sent": 0, "usage_sent": 0, "expired_sent": 0, "unreachable": 0, "errors": 0}
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
    sent_days_keys: set[tuple[int, int, int]] = set()
    sent_usage_keys: set[tuple[int, int, int]] = set()
    sent_expired_keys: set[tuple[int, int]] = set()

    for svc in services:
        summary["scanned"] += 1
        try:
            service_id = int(svc.get("id") or 0)
            telegram_id = int(svc.get("telegram_id") or 0)
            if service_id <= 0 or telegram_id <= 0:
                continue
            service_name = str(svc.get("name") or "").strip() or f"اشتراک #{service_id}"

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
            last_expired_notified = int(state.get("expired_sent", 0))

            should_days = (not unlimited_time) and days_left > 0 and days_left <= days_threshold
            remaining_bucket = int(max(0, math.ceil(remaining_gb))) if remaining_gb >= 0 else -1
            should_usage = (
                (not unlimited_volume)
                and usage_limit > 0
                and remaining_gb > 0
                and remaining_bucket <= int(math.ceil(usage_threshold))
            )

            new_days_state = last_days_notified
            new_usage_state = last_usage_notified
            new_expired_state = last_expired_notified

            expired_by_time = (not unlimited_time) and days_left <= 0
            expired_by_usage = (not unlimited_volume) and usage_limit > 0 and usage_current >= usage_limit
            if expired_by_time and expired_by_usage:
                expire_reason = "both"
            elif expired_by_usage:
                expire_reason = "usage"
            elif expired_by_time:
                expire_reason = "time"
            else:
                expire_reason = None

            if expire_reason and last_expired_notified == 0:
                expire_key = (telegram_id, service_id)
                if expire_key not in sent_expired_keys:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=_build_expired_notice_message(service_name, reason=expire_reason),
                        reply_markup=_build_renew_button_keyboard(service_id),
                    )
                    sent_expired_keys.add(expire_key)
                    summary["expired_sent"] += 1
                new_expired_state = 1
            elif not expire_reason and last_expired_notified != 0:
                # Reset arm when the subscription is renewed/upgraded again.
                new_expired_state = 0

            if should_days and days_left != last_days_notified:
                day_key = (telegram_id, service_id, days_left)
                if day_key not in sent_days_keys:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=_build_renewal_reminder_message(service_name, days_left=days_left),
                    )
                    sent_days_keys.add(day_key)
                    summary["days_sent"] += 1
                new_days_state = days_left
            elif not should_days and last_days_notified != -1:
                # Reset arm when service gets healthy again (after renew/upgrade).
                new_days_state = -1

            if should_usage and remaining_bucket != last_usage_notified:
                usage_key = (telegram_id, service_id, remaining_bucket)
                if usage_key not in sent_usage_keys:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=_build_renewal_reminder_message(service_name, remaining_gb=remaining_bucket),
                    )
                    sent_usage_keys.add(usage_key)
                    summary["usage_sent"] += 1
                new_usage_state = remaining_bucket
            elif not should_usage and last_usage_notified != -1:
                # Reset arm when user is out of reminder window.
                new_usage_state = -1

            if (
                new_days_state != last_days_notified
                or new_usage_state != last_usage_notified
                or new_expired_state != last_expired_notified
            ):
                userbot_db.set_service_reminder_state(
                    service_id,
                    days_sent=new_days_state,
                    usage_sent=new_usage_state,
                    expired_sent=new_expired_state,
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
        f"🔔 یادآور تمدید: روز={reminder_summary['days_sent']} | حجم={reminder_summary['usage_sent']} | منقضی‌شده={reminder_summary['expired_sent']} | دسترسی‌ندارد={reminder_summary['unreachable']} | خطا={reminder_summary['errors']}"
    )


async def agent_enforce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if not user or not message:
        return
    if user.id != ADMIN_ID:
        await message.reply_text("🚫 شما دسترسی ادمین ندارید.")
        return

    await message.reply_text("⏳ در حال بررسی مصرف سرویس‌های نمایندگی...")
    summary = await agent_enforcer.run_agent_usage_enforcer(scan_all=True)
    await message.reply_text(
        "✅ بررسی مصرف نمایندگی تمام شد.\n"
        f"سرویس بررسی‌شده: {summary['services_scanned']} از {summary['services_total']}\n"
        f"سرویس همگام‌شده: {summary['services_synced']}\n"
        f"سرویس قطع‌شده: {summary['services_disabled']}\n"
        f"نود قطع‌شده: {summary['nodes_disabled']}\n"
        f"خطا: {summary['errors']}"
    )


async def _enforcer_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    summary = await service_enforcer.run_global_usage_enforcer(scan_all=False)
    reminder_summary = {"days_sent": 0, "usage_sent": 0, "expired_sent": 0, "unreachable": 0, "errors": 0}
    now_ts = int(asyncio.get_running_loop().time())
    bot_data = context.application.bot_data if context and context.application else {}
    last_ts = int(bot_data.get("_sub_reminder_last_ts") or 0)
    if (now_ts - last_ts) >= SUB_REMINDER_INTERVAL:
        reminder_summary = await _run_subscription_reminder_cycle()
        bot_data["_sub_reminder_last_ts"] = now_ts
    logger.info(
        "Global enforcer cycle done: scanned=%s/%s synced=%s services_disabled=%s nodes_disabled=%s nodes_disable_failed=%s errors=%s | reminders: days=%s usage=%s expired=%s unreachable=%s errors=%s",
        summary["services_scanned"],
        summary.get("services_total", summary["services_scanned"]),
        summary["services_synced"],
        summary["services_disabled"],
        summary["nodes_disabled"],
        summary["nodes_disable_failed"],
        summary["errors"],
        reminder_summary["days_sent"],
        reminder_summary["usage_sent"],
        reminder_summary["expired_sent"],
        reminder_summary["unreachable"],
        reminder_summary["errors"],
    )


async def _agent_enforcer_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    summary = await agent_enforcer.run_agent_usage_enforcer(scan_all=True)
    logger.info(
        "Agent enforcer cycle done: scanned=%s/%s synced=%s disabled=%s nodes_disabled=%s errors=%s",
        summary["services_scanned"],
        summary["services_total"],
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


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if not user or not message:
        return
    if user.id != ADMIN_ID:
        await message.reply_text("🚫 شما دسترسی ادمین ندارید.")
        return
    await message.reply_text("⏳ در حال جمع‌آوری گزارش اشکال‌زدایی...")
    try:
        report = _build_debug_report(context)
    except Exception as e:
        logger.exception("Debug report build failed: %s", e)
        await message.reply_text(f"❌ خطا در تهیه گزارش: {e}")
        return

    for part in _split_text(report):
        await message.reply_text(part)


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


async def _server_health_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """مانیتورینگ سلامت همهٔ سرورها و هشدار به ادمین هنگام قطع/برگشت."""
    try:
        summary = await server_health.run_server_health_check()
        logger.info(
            "Server health cycle done: scanned=%s up=%s down=%s alerts=%s errors=%s",
            summary["servers_scanned"],
            summary["servers_up"],
            summary["servers_down"],
            summary["alerts"],
            summary["errors"],
        )
    except Exception as e:
        logger.warning("Server health job error: %s", e)


async def _userbot_auto_backup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_userbot_auto_backup_job(context)


async def _purge_soft_deleted_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """پاکسازی قطعی اشتراک‌هایی که بیش از ۷ روز پیش توسط ادمین به‌صورت نرم حذف شده‌اند."""
    try:
        from Shared import agent_db
        purged = agent_db.purge_expired_soft_deleted(days=7)
        if purged:
            logging.getLogger(__name__).info("Soft-deleted purge: %s service(s) removed.", purged)
    except Exception:
        logging.getLogger(__name__).exception("Soft-deleted purge job failed.")


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
    await _set_admin_commands(application)

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

    if AGENT_ENFORCER_ENABLED:
        fallback_tasks.append(
            application.create_task(
                _run_fallback_loop(
                    application,
                    name="agent-enforcer-fallback",
                    worker=_agent_enforcer_job,
                    interval=AGENT_ENFORCER_INTERVAL,
                    first=30,
                ),
                name="agent-enforcer-fallback",
            )
        )
        logger.info("✅ Agent enforcer fallback scheduler enabled (interval=%ss)", AGENT_ENFORCER_INTERVAL)

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

    if SERVER_HEALTH_ENABLED:
        fallback_tasks.append(
            application.create_task(
                _run_fallback_loop(
                    application,
                    name="server-health-fallback",
                    worker=_server_health_job,
                    interval=SERVER_HEALTH_INTERVAL,
                    first=60,
                ),
                name="server-health-fallback",
            )
        )
        logger.info("✅ Server health fallback scheduler enabled")

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

    # Migration: اضافه کردن mapping سرور اصلی برای سرویس‌های ادمین قدیمی
    try:
        fixed = userbot_db.fix_admin_services_missing_source_mapping()
        if fixed > 0:
            logger.info("✅ Migration: fixed %d admin services missing source server mapping", fixed)
    except Exception as e:
        logger.warning("⚠️ Migration fix_admin_services failed: %s", e)

    application = (
        ApplicationBuilder()
        .token(ADMIN_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # /start — همین فایل
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("debug", debug))
    application.add_handler(CommandHandler("enforce_now", enforce_now))
    application.add_handler(CommandHandler("agent_enforce", agent_enforce))
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

    if AGENT_ENFORCER_ENABLED and application.job_queue is not None:
        application.job_queue.run_repeating(
            _agent_enforcer_job,
            interval=AGENT_ENFORCER_INTERVAL,
            first=30,
            name="agent-enforcer",
            job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 60},
        )
        logger.info("✅ Agent enforcer enabled (interval=%ss)", AGENT_ENFORCER_INTERVAL)
    elif AGENT_ENFORCER_ENABLED:
        logger.warning("⚠️ Agent enforcer requested but job_queue is unavailable.")
    else:
        logger.info("ℹ️ Agent enforcer disabled by env")

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

    if SERVER_HEALTH_ENABLED and application.job_queue is not None:
        application.job_queue.run_repeating(
            _server_health_job,
            interval=SERVER_HEALTH_INTERVAL,
            first=60,
            name="server-health",
            job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 120},
        )
        logger.info("✅ Server health monitor enabled (interval=%ss)", SERVER_HEALTH_INTERVAL)
    elif SERVER_HEALTH_ENABLED:
        logger.warning("⚠️ Server health monitor requested but job_queue is unavailable.")
    else:
        logger.info("ℹ️ Server health monitor disabled by env")

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

    # پاکسازی نرم-حذف‌شده‌ها (۷ روز) — هر روز
    if application.job_queue is not None:
        from datetime import time as _dt_time
        application.job_queue.run_daily(
            _purge_soft_deleted_job,
            time=_dt_time(hour=4, minute=0),
            name="purge-soft-deleted-services",
        )
        logger.info("✅ Soft-delete purge scheduler enabled (daily 04:00).")
    else:
        logger.warning("⚠️ Soft-delete purge scheduler unavailable (no job_queue).")

    logger.info("✅ AdminBot started and polling...")
    application.run_polling()


if __name__ == "__main__":
    main()
