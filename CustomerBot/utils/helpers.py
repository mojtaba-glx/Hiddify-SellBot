import re
import time
from typing import Dict, Optional, Tuple

from telegram import Update
from telegram.ext import ContextTypes

from CustomerBot.constants import ACTION_COOLDOWN


_last_action: Dict[str, float] = {}


def is_rate_limited(key: str, cooldown: float = ACTION_COOLDOWN) -> bool:
    now = time.time()
    last = _last_action.get(key, 0)
    if now - last < cooldown:
        return True
    _last_action[key] = now
    return False


def escape_markdown(text: str) -> str:
    if not text:
        return ""
    for ch in r"\_*[]()~>#+-=|{}!":
        text = text.replace(ch, "\\" + ch)
    return text


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_price(price: int) -> str:
    return f"{price:,}"


def format_gb(gb: float, lang: str = "fa") -> str:
    from Shared import i18n
    unit_tb = i18n.t("unit_tb", lang)
    unit_gb = i18n.t("unit_gb", lang)
    if gb >= 1024:
        return f"{gb / 1024:.1f} {unit_tb}"
    return f"{gb:g} {unit_gb}"


def parse_deep_link(payload: str) -> Optional[Tuple[str, str]]:
    if not payload:
        return None
    m = re.match(r"^connect_(\d+)_(.+)$", payload)
    if m:
        return ("connect", f"{m.group(1)}:{m.group(2)}")
    m = re.match(r"^(\d+)_(.+)$", payload)
    if m:
        return ("connect", f"{m.group(1)}:{m.group(2)}")
    m = re.match(r"^tshotu_(\d+)_(.+)$", payload)
    if m:
        return ("ticket_shot", f"{m.group(1)}:{m.group(2)}")
    m = re.match(r"^zrv_(.+)$", payload)
    if m:
        return ("voucher", m.group(1))
    return None


def is_cancel_text(text: str) -> bool:
    """دکمه بازگشت/لغو در همه زبان‌ها + لیبل مرجع فارسی."""
    t = text.strip()
    if t in ("بازگشت", "/cancel", "لغو", "❌ لغو", "❌لغو"):
        return True
    try:
        from Shared import i18n
        key = i18n.resolve_button(t, ("btn_back", "btn_cancel", "back", "btn_back_plain", "cancel_btn2", "back_red"))
        return key is not None or t == "/cancel"
    except Exception:
        return False


def is_pay_done_text(text: str) -> bool:
    t = text.strip()
    if "پرداخت کردم" in t or "ارسال رسید" in t:
        return True
    try:
        from Shared import i18n
        key = i18n.resolve_button(t, ("paid_send_receipt",))
        return key is not None
    except Exception:
        return False


def build_service_name(plan_title: str, server_title: str) -> str:
    return f"{plan_title} - {server_title}"
