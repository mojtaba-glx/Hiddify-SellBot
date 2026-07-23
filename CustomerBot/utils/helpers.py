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


def format_gb(gb: float) -> str:
    if gb >= 1024:
        return f"{gb / 1024:.1f} ترابایت"
    return f"{gb:g} گیگابایت"


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
    t = text.strip()
    return t in ("بازگشت", "/cancel", "لغو", "❌ لغو", "❌لغو")


def is_pay_done_text(text: str) -> bool:
    t = text.strip()
    return "پرداخت کردم" in t or "ارسال رسید" in t


def build_service_name(plan_title: str, server_title: str) -> str:
    return f"{plan_title} - {server_title}"
