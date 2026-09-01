"""Pure helper functions moved verbatim from UserBot/main.py (zero logic change)."""

import re
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from telegram import Update
from Shared import plans_storage, userbot_db


def _normalize_action_text(text: str) -> str:
    t = (text or "").strip()
    for ch in ("\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u2066", "\u2067", "\u2068", "\u2069"):
        t = t.replace(ch, "")
    return " ".join(t.split())


def _is_back_or_cancel_text(text: str) -> bool:
    t = _normalize_action_text(text)
    return t in {
        "بازگشت",
        "🔙بازگشت",
        "🔙 بازگشت",
        "لغو",
        "❌لغو",
        "❌ لغو",
        "/cancel",
    }


def _extract_start_payload(update: Update) -> str:
    try:
        txt = str((update.message.text if update and update.message else "") or "").strip()
    except Exception:
        return ""
    if not txt.startswith("/start"):
        return ""
    parts = txt.split(maxsplit=1)
    return str(parts[1]).strip() if len(parts) > 1 else ""


def _sort_plans(plans: list[dict], txp: dict) -> list[dict]:
    mode = str(txp.get("plan_sort_mode") or "price").strip().lower()
    desc = bool(txp.get("plan_sort_desc", False))
    key_name = "price" if mode == "price" else ("gb" if mode == "gb" else "days")

    def _to_number(v: Any) -> float:
        # مقدار ممکن است عدد، رشته‌ی ساده یا رشته‌ی تزئینی مثل "70,000 تومان" باشد.
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v or "").strip()
        if not s:
            return 0.0
        s = s.replace(",", "").replace("،", "")
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if not m:
            return 0.0
        try:
            return float(m.group(0))
        except Exception:
            return 0.0

    def _key(p: dict):
        return _to_number(p.get(key_name))

    return sorted(list(plans or []), key=_key, reverse=desc)


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value, default=0) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _calc_dynamic_price(gb: int, months: int, dyn_settings: Optional[Dict[str, Any]]) -> tuple[int, int]:
    """
    Calculate dynamic plan final price and applied discount percent.
    Discount model:
      discount_tiers uses the highest threshold reached.
      Legacy fallback: floor(gb / discount_step_gb) * discount_percent_step,
      capped by discount_percent_max.
    """
    settings = dyn_settings or {}

    gb_val = max(0, _to_int(gb, 0))
    months_val = max(0, _to_int(months, 0))
    price_per_gb = max(0, _to_int(settings.get("price_per_gb"), 2000))
    price_per_month = max(0, _to_int(settings.get("price_per_month"), 30000))

    base_price = (gb_val * price_per_gb) + (months_val * price_per_month)

    discount_step_gb = max(0, _to_int(settings.get("discount_step_gb"), 0))
    discount_percent_step = max(0, _to_int(settings.get("discount_percent_step"), 0))
    discount_percent_max = max(0, _to_int(settings.get("discount_percent_max"), 0))

    off_percent = 0
    discount_tiered_enabled = bool(settings.get("discount_tiered_enabled", False))
    discount_simple_enabled = plans_storage.is_simple_discount_active(settings)
    discount_tiers = plans_storage.normalize_discount_tiers(settings.get("discount_tiers", []))
    
    # محاسبه تخفیف پلاکانی اگر فعال باشد
    tiered_off = 0
    if discount_tiered_enabled and discount_tiers:
        for tier in discount_tiers:
            if gb_val >= int(tier["gb"]):
                tiered_off = int(tier["percent"])
            else:
                break
        tiered_off = max(0, min(tiered_off, 100))
    
    # محاسبه تخفیف حجمی ساده اگر فعال باشد
    simple_off = 0
    if discount_simple_enabled and discount_step_gb > 0 and discount_percent_step > 0 and gb_val >= discount_step_gb:
        stages = gb_val // discount_step_gb
        simple_off = stages * discount_percent_step
        if discount_percent_max > 0:
            simple_off = min(simple_off, discount_percent_max)
        simple_off = max(0, min(simple_off, 100))
    
    # انتخاب بهترین (بیشترین) تخفیف
    off_percent = max(tiered_off, simple_off)

    final_price = int(round(base_price * (100 - off_percent) / 100))
    final_price = max(0, final_price)
    return final_price, off_percent


def _expected_server_price(sid: int, gb: int, days: int, plan_id: int) -> Optional[int]:
    """قیمت واقعی سمت سرور برای یک خرید — ضد دستکاری callback data.

    ترتیب تشخیص: پلن ثابت با plan_id → پلن ثابت با تطبیق gb/days (دکمه‌های قدیمی)
    → قیمت پویا. اگر هیچ‌کدام قابل تشخیص نبود None برمی‌گردد (درخواست باید رد شود).
    """
    try:
        server_block = plans_storage._load_all_plans().get("servers", {}).get(str(sid), {})
        if plan_id > 0:
            plan = next(
                (p for p in server_block.get("plans", []) if int(p.get("id") or 0) == int(plan_id)),
                None,
            )
            if not plan:
                return None
            return max(0, int(plan.get("price") or 0))
        matches = [
            p for p in server_block.get("plans", [])
            if int(float(p.get("gb") or 0)) == int(gb) and int(p.get("days") or 0) == int(days)
        ]
        if matches:
            prices = {max(0, int(p.get("price") or 0)) for p in matches}
            if len(prices) == 1:
                return prices.pop()
            return None
        if int(gb) > 0 and int(days) > 0 and int(days) % 30 == 0:
            dyn_settings = server_block.get("dynamic_settings", {})
            price, _off = _calc_dynamic_price(int(gb), int(days) // 30, dyn_settings)
            return max(0, int(price or 0))
        return None
    except Exception:
        return None


def _generate_order_id() -> int:
    """Generate unique order_id for userbot_orders table."""
    conn = userbot_db._get_conn()
    cur = conn.cursor()
    try:
        for _ in range(50):
            oid = random.randint(1000000, 9999999)
            cur.execute("SELECT 1 FROM userbot_orders WHERE order_id = ? LIMIT 1", (oid,))
            if not cur.fetchone():
                return oid
        return random.randint(1000000, 9999999)
    finally:
        conn.close()


def _generate_service_code() -> str:
    """Generate a 7-digit service code (best-effort unique)."""
    conn = userbot_db._get_conn()
    cur = conn.cursor()
    try:
        for _ in range(50):
            code = f"{random.randint(0, 9999999):07d}"
            cur.execute(
                "SELECT 1 FROM userbot_services WHERE comment LIKE ? LIMIT 1",
                (f"%code:{code}%",),
            )
            if not cur.fetchone():
                return code
        return f"{random.randint(0, 9999999):07d}"
    finally:
        conn.close()


def _parse_service_comment(comment: str) -> dict:
    """
    Parse service comment stored as key:value pairs separated by '|',
    e.g. "uuid:...|code:1234567".
    """
    parsed = {}
    raw = (comment or "").strip()
    if not raw:
        return parsed
    for part in raw.split("|"):
        part = part.strip()
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        if k and v:
            parsed[k] = v
    return parsed


def _build_panel_user_comment(user_id: int, *, is_test: bool = False) -> str:
    base = f"HiddifyBot:{int(user_id)}"
    if is_test:
        return f"{base}|test"
    return base


def _extract_uuid_from_comment(comment: str) -> Optional[str]:
    return _parse_service_comment(comment).get("uuid")


def _extract_uuid_from_user_input(raw: str) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    # Plain UUID or UUID embedded in link/config text
    m = re.search(
        r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b",
        text,
    )
    if not m:
        return None
    return m.group(1).lower()


def _is_user_missing_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return ("http 404" in text) or ("not found" in text) or (" پیدا نشد" in text)


def _parse_panel_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None

    # ISO handling (with timezone / microseconds / trailing Z)
    try:
        iso_raw = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f",):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    if raw.endswith("Z"):
        trimmed = raw[:-1]
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(trimmed, fmt)
            except ValueError:
                continue
    return None


def _optional_int_from_any(value: Any) -> Optional[int]:
    raw = str(value or "").replace(",", "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except Exception:
        return None


def _usage_limit_from_panel_user(user: dict) -> Optional[float]:
    for key in ("usage_limit_GB", "usage_limit_gb", "usage_limit", "package_traffic"):
        if key not in user:
            continue
        raw = user.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        val = _to_float(raw, -1.0)
        if val >= 0:
            return float(val)
    return None


def _days_left_from_panel_user(user: dict) -> Optional[int]:
    for key in ("remaining_days", "remaining_day", "days_left"):
        v = _optional_int_from_any(user.get(key))
        if v is not None:
            return v

    start_dt = _parse_panel_datetime(user.get("start_date"))
    package_days = user.get("package_days")
    if start_dt and package_days:
        try:
            end_dt = start_dt + timedelta(days=int(package_days))
            return (end_dt.date() - datetime.now(timezone.utc).replace(tzinfo=None).date()).days
        except Exception:
            pass

    for key in ("expire", "expire_date", "end_date", "expiration_date", "expires_at"):
        end_dt = _parse_panel_datetime(user.get(key))
        if end_dt:
            return (end_dt.date() - datetime.now(timezone.utc).replace(tzinfo=None).date()).days
    return None
