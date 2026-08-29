"""گزارش‌دهی رویدادهای مهم (خرید/تمدید/خطا) به ادمین در همه ربات‌ها.

هر ربات با ADMIN_BOT_TOKEN/ADMIN_ID (متغیرهای مشترک .env) می‌تواند
گزارش تحویل سرویس را برای ادمین بفرستد تا ادمین از وضعیت واقعی
خرید/تمدید نماینده‌ها و مشتریان مطلع باشد.
"""

import logging
import os

logger = logging.getLogger(__name__)


def _admin_credentials() -> tuple[int, str]:
    try:
        admin_id = int(os.getenv("ADMIN_ID", "0") or "0")
    except (TypeError, ValueError):
        admin_id = 0
    return admin_id, str(os.getenv("ADMIN_BOT_TOKEN", "") or "").strip()


def _agency_event_target() -> str:
    """مقصد گزارش‌های نمایندگی: کانال رویداد اگر فعال باشد، وگرنه چت ادمین."""
    target = ""
    try:
        from Shared import userbot_db
        s = userbot_db.get_agency_event_settings()
        if s.get("event_channel_enabled"):
            target = str(s.get("event_channel_id") or "").strip()
    except Exception:
        target = ""
    if not target:
        admin_id, _ = _admin_credentials()
        target = str(admin_id) if admin_id else ""
    return target


async def send_agency_event_report(text: str, *, parse_mode: str = "HTML", reply_markup=None) -> bool:
    """ارسال گزارش رویداد نمایندگی به کانال رویداد (اگر فعال باشد) یا چت ادمین."""
    admin_id, admin_token = _admin_credentials()
    if not admin_token:
        return False
    target = _agency_event_target()
    if not target:
        return False
    try:
        from telegram import Bot

        await Bot(token=admin_token).send_message(
            chat_id=target,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True
    except Exception as e:
        logger.warning("agency event report send failed target=%s: %s", target, e)
        return False


def _agent_display_name(agent: dict | None) -> str:
    if not agent:
        return "—"
    return (
        str(agent.get("full_name") or "").strip()
        or str(agent.get("username") or "").strip()
        or str(agent.get("telegram_id") or "").strip()
        or "—"
    )


def _short_error(exc) -> str:
    raw = str(exc or "").strip()
    if not raw:
        return "unknown error"
    raw = raw.replace("\n", " ")
    if len(raw) > 80:
        return raw[:80] + "…"
    return raw


async def notify_admin_delivery_report(
    *,
    action_title: str,
    agent: dict | None,
    customer_name: str = "",
    service_name: str = "",
    server_title: str = "",
    volume_gb: float = 0.0,
    days: int = 0,
    amount: int = 0,
    status: str = "success",
    error: str = "",
    pending_servers: list[str] | None = None,
    sync_primary_server_id: int = 0,
) -> bool:
    """یک گزارش تحویل سرویس (خريد/تمدید) برای ادمین می‌فرستد.

    status: success | partial | error
    - partial: برخی نودها در دسترس نبودند؛ با دکمه‌ی sync می‌توان ساخت/همگام‌سازی کرد.
    - error:   تحویل با خطا مواجه شد (نیاز به بررسی/رفع).
    """
    admin_id, admin_token = _admin_credentials()
    if not admin_id or not admin_token:
        return False
    try:
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

        agent_name = _agent_display_name(agent)
        lines = [
            "🛰 <b>گزارش تحویل سرویس</b>",
            "",
            f"➤ عملیات: {action_title}",
            f"👤 نماینده: <b>{agent_name}</b>",
        ]
        if customer_name:
            lines.append(f"👥 مشتری: {customer_name}")
        if service_name:
            lines.append(f"📦 سرویس: {service_name}")
        if server_title:
            lines.append(f"🖥 سرور: {server_title}")
        if volume_gb > 0:
            lines.append(f"📊 حجم: {_fmt_gb(volume_gb)}")
        if days > 0:
            lines.append(f"⏳ مدت: {days} روز")
        if amount > 0:
            lines.append(f"💴 مبلغ: {amount:,} تومان")

        if status == "success":
            lines.append("")
            lines.append("✅ تحویل سرویس با موفقیت انجام شد.")
        elif status == "partial":
            lines.append("")
            lines.append("⚠️ <b>تحویل ناقص:</b> برخی نودها در دسترس نبودند و "
                          "سرویس روی آن‌ها اعمال نشد.")
            if pending_servers:
                fail_lines = "\n".join(f"• {t}" for t in list(dict.fromkeys(pending_servers))[:10])
                lines.append(f"\nسرورهای در دسترس‌نیست:\n{fail_lines}")
            lines.append("\nبعد از بازگشت آن سرورها، روی دکمه زیر بزنید تا ربات "
                         "بررسی کند و کاربران جاافتاده را بسازد.")
        elif status == "error":
            lines.append("")
            lines.append("❌ <b>خطا در تحویل سرویس.</b>")
            if error:
                lines.append(f"جزئیات: {error}")
            lines.append("\nپول/کیفپول نماینده به‌درستی جابه‌جا شده؟ عملیات را بررسی کنید.")

        text = "\n".join(lines)

        # مسیریابی رویداد نمایندگی: اگر کانال رویداد فعال باشد، گزارش‌های
        # موفق/ناقص به کانال می‌رود؛ گزارش خطا همیشه به چت ادمین می‌رود.
        if status != "error":
            try:
                from Shared import userbot_db as _udb
                _s = _udb.get_agency_event_settings()
            except Exception:
                _s = {}
            if _s.get("event_channel_enabled") and str(_s.get("event_channel_id") or "").strip():
                try:
                    await Bot(token=admin_token).send_message(
                        chat_id=str(_s["event_channel_id"]).strip(),
                        text=text,
                        parse_mode="HTML",
                    )
                    return True
                except Exception as e:
                    logger.warning("agency event channel delivery failed: %s", e)

        kb = None
        if status == "partial" and sync_primary_server_id > 0:
            # دکمه‌ی ساخت کاربران جاافتاده روی نودها — AdminBot این callback را هندل می‌کند.
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔄 همگام‌سازی و ساخت کاربران جاافتاده",
                    callback_data=f"server:{sync_primary_server_id}:sync_nodes_missing",
                )]
            ])
        elif status == "error":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🧩 بررسی/رفع مشکل (منوی سرورها)",
                    callback_data="servers:list_back",
                )]
            ])

        bot = Bot(token=admin_token)
        await bot.send_message(chat_id=admin_id, text=text, reply_markup=kb, parse_mode="HTML")
        return True
    except Exception as e:
        logger.warning("Failed to notify admin delivery report: %s", e)
        return False


def _fmt_gb(value: float) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v >= 1024:
        return f"{v / 1024:g}TB"
    if v == int(v):
        return f"{int(v)}GB"
    return f"{v:g}GB"