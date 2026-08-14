"""
یادآوری تمدید اشتراک برای سرویس‌های نمایندگی (agent_services).

هر ربات مشتری (که متعلق به یک نماینده است) به‌صورت دوره‌ای این ماژول را
اجرا می‌کند و برای مشتریانی که اشتراکشان نزدیک انقضا یا رو به اتمامِ حجم
است، پیام یادآوری می‌فرستد — دقیقاً مثل ربات کاربران.
"""

import logging
import math

from Shared import agent_db

logger = logging.getLogger(__name__)


def _to_float(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value, default=0):
    try:
        if value is None:
            return int(default)
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _format_gb(value):
    v = _to_float(value, 0.0)
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.1f}"


def build_renewal_reminder_message(service_name, *, days_left=None, remaining_gb=None):
    title = str(service_name or "").strip() or "اشتراک شما"
    lines = ["🚨 یادآوری تمدید اشتراک", f"🔹 اشتراک: «{title}»"]
    if days_left is not None:
        lines.append(f"📅 روز باقی‌مانده: {int(days_left)} روز")
    elif remaining_gb is not None:
        lines.append(f"🚥 حجم باقی‌مانده: {_format_gb(remaining_gb)} گیگ")
    lines.append("لطفاً برای جلوگیری از قطع سرویس، اشتراک را تمدید کنید.")
    return "\n".join(lines)


def _is_unlimited_volume(limit_gb, br):
    if not bool(br.get("renew_unlimited_volume", False)):
        return False
    try:
        threshold = float(br.get("renew_unlimited_volume_from_gb") or 1000)
    except Exception:
        threshold = 1000.0
    return float(limit_gb) >= threshold


def _is_unlimited_time(days_val, br):
    if not bool(br.get("renew_unlimited_time", False)):
        return False
    try:
        threshold = int(br.get("renew_unlimited_time_from_days") or 365)
    except Exception:
        threshold = 365
    return int(days_val) >= threshold


async def run_agent_reminder_cycle(bot, agent_id):
    summary = {"scanned": 0, "days_sent": 0, "usage_sent": 0, "unreachable": 0, "errors": 0}
    try:
        from CustomerBot.database import get_buy_renew_settings
        br = get_buy_renew_settings(agent_id)
    except Exception:
        br = {}
    if not bool(br.get("enable_renew", True)):
        return summary
    try:
        days_threshold = max(1, _to_int(br.get("renew_max_days"), 3))
    except Exception:
        days_threshold = 3
    try:
        usage_threshold = max(0.1, _to_float(br.get("renew_max_remaining_gb"), 3))
    except Exception:
        usage_threshold = 3

    services = agent_db.get_agent_services_for_reminder(agent_id)
    sent_days_keys = set()
    sent_usage_keys = set()
    for svc in services:
        summary["scanned"] += 1
        try:
            service_id = _to_int(svc.get("id"), 0)
            telegram_id = _to_int(svc.get("telegram_id"), 0)
            if service_id <= 0 or telegram_id <= 0:
                continue
            service_name = str(svc.get("name") or "").strip() or f"اشتراک #{service_id}"
            usage_current = _to_float(svc.get("usage_current"), 0.0)
            usage_limit = _to_float(svc.get("usage_limit"), 0.0)
            try:
                days_left = _to_int(svc.get("days_left"), 0)
            except Exception:
                days_left = 0
            unlimited_time = _is_unlimited_time(days_left, br)
            unlimited_volume = _is_unlimited_volume(usage_limit, br)
            remaining_gb = (usage_limit - usage_current) if usage_limit > 0 else -1.0
            state = agent_db.get_service_reminder_state(service_id)
            last_days_notified = _to_int(state.get("days_sent"), -1)
            last_usage_notified = _to_int(state.get("usage_sent"), -1)
            should_days = (not unlimited_time) and days_left >= 0 and days_left <= days_threshold
            remaining_bucket = int(max(0, math.ceil(remaining_gb))) if remaining_gb >= 0 else -1
            should_usage = (
                (not unlimited_volume) and usage_limit > 0 and remaining_gb >= 0
                and remaining_bucket <= int(math.ceil(usage_threshold))
            )
            new_days_state = last_days_notified
            new_usage_state = last_usage_notified
            if should_days and days_left != last_days_notified:
                day_key = (telegram_id, service_id, days_left)
                if day_key not in sent_days_keys:
                    await bot.send_message(chat_id=telegram_id, text=build_renewal_reminder_message(service_name, days_left=days_left))
                    sent_days_keys.add(day_key)
                    summary["days_sent"] += 1
                new_days_state = days_left
            elif not should_days and last_days_notified != -1:
                new_days_state = -1
            if should_usage and remaining_bucket != last_usage_notified:
                usage_key = (telegram_id, service_id, remaining_bucket)
                if usage_key not in sent_usage_keys:
                    await bot.send_message(chat_id=telegram_id, text=build_renewal_reminder_message(service_name, remaining_gb=remaining_bucket))
                    sent_usage_keys.add(usage_key)
                    summary["usage_sent"] += 1
                new_usage_state = remaining_bucket
            elif not should_usage and last_usage_notified != -1:
                new_usage_state = -1
            if new_days_state != last_days_notified or new_usage_state != last_usage_notified:
                agent_db.set_service_reminder_state(service_id, days_sent=new_days_state, usage_sent=new_usage_state)
        except Exception as e:
            msg = str(e or "").strip().lower()
            if "chat not found" in msg or "forbidden" in msg or "blocked" in msg:
                summary["unreachable"] += 1
            else:
                summary["errors"] += 1
                logger.warning("Agent reminder error svc=%s: %s", svc.get("id"), e)
    return summary
