from __future__ import annotations

import os
from typing import Any, Optional

from telegram import InlineKeyboardButton as TelegramInlineKeyboardButton
from telegram import KeyboardButton as TelegramKeyboardButton


VALID_BUTTON_STYLES = {"primary", "success", "danger"}
BUTTON_STYLE_THEMES = {
    "smart": {
        "title": "✨ هوشمند",
        "description": "متعادل: تأیید و خرید سبز، هشدار قرمز، مسیرها آبی",
    },
    "shop": {
        "title": "🛒 فروشگاهی",
        "description": "پررنگ‌تر برای خرید، تمدید، کیف پول و پرداخت",
    },
    "pro": {
        "title": "💼 حرفه‌ای",
        "description": "آرام و مدیریتی: رنگ فقط برای اکشن‌های مهم",
    },
    "minimal": {
        "title": "🕊 مینیمال",
        "description": "خلوت: فقط تأییدهای مهم سبز و کارهای خطرناک قرمز",
    },
}

DANGER_TOKENS = (
    "❌",
    "🗑",
    "🚫",
    "لغو",
    "حذف",
    "رد",
    "بستن",
    "غیرفعال",
    "disable",
    "delete",
    "remove",
    "reject",
    "cancel",
    "close",
)
SUCCESS_TOKENS = (
    "✅",
    "➕",
    "💳",
    "💰",
    "🎁",
    "🔥",
    "تایید",
    "تأیید",
    "پرداخت",
    "خرید",
    "تمدید",
    "افزودن",
    "ارسال",
    "ساخت",
    "فعال",
    "approve",
    "confirm",
    "pay",
    "buy",
    "renew",
    "add",
    "send",
    "enable",
)
STRONG_SUCCESS_TOKENS = (
    "✅",
    "تایید",
    "تأیید",
    "پرداخت کردم",
    "تایید و پرداخت",
    "ارسال",
    "افزودن",
    "approve",
    "confirm",
    "send",
    "add",
)
SHOP_TOKENS = (
    "💳",
    "💰",
    "🎁",
    "🔥",
    "🏷",
    "خرید",
    "تمدید",
    "پرداخت",
    "کیف پول",
    "شارژ",
    "کارت",
    "کوپن",
    "هدیه",
    "پلن",
    "بسته",
    "قیمت",
    "wallet",
    "coupon",
    "gift",
    "plan",
    "price",
)
PRIMARY_TOKENS = (
    "🔙",
    "➡️",
    "⬅️",
    "◀️",
    "▶️",
    "📊",
    "📈",
    "📋",
    "📁",
    "⚙️",
    "🌐",
    "🔗",
    "🔄",
    "بازگشت",
    "وضعیت",
    "لیست",
    "تنظیم",
    "راهنما",
    "جستجو",
    "noop",
    "back",
    "status",
    "list",
    "settings",
    "menu",
    "guide",
    "search",
)


def normalize_button_theme(value: Any) -> str:
    theme = str(value or "smart").strip().lower()
    return theme if theme in BUTTON_STYLE_THEMES else "smart"


def _contains_any(haystack: str, tokens: tuple[str, ...]) -> bool:
    return any(token in haystack for token in tokens)


def _styles_enabled() -> bool:
    try:
        from Shared import userbot_db

        settings = userbot_db.get_ui_settings()
        if "colored_buttons" in settings:
            return bool(settings.get("colored_buttons"))
    except Exception:
        pass

    value = str(os.getenv("TG_BUTTON_STYLES_ENABLED", "true")).strip().lower()
    return value not in {"0", "false", "no", "off", "disable", "disabled"}


def _selected_theme() -> str:
    try:
        from Shared import userbot_db

        settings = userbot_db.get_ui_settings()
        return normalize_button_theme(settings.get("button_theme"))
    except Exception:
        return normalize_button_theme(os.getenv("TG_BUTTON_STYLE_THEME", "smart"))


def _normalize_style(style: Optional[str]) -> Optional[str]:
    if not style:
        return None
    normalized = str(style).strip().lower()
    return normalized if normalized in VALID_BUTTON_STYLES else None


def infer_button_style(text: Any, callback_data: Any = None, theme: Optional[str] = None) -> Optional[str]:
    label = str(text or "")
    data = str(callback_data or "")
    haystack = f"{label} {data}".lower()
    selected_theme = normalize_button_theme(theme or _selected_theme())

    if _contains_any(haystack, DANGER_TOKENS):
        return "danger"

    if selected_theme == "minimal":
        return "success" if _contains_any(haystack, STRONG_SUCCESS_TOKENS) else None

    if selected_theme == "pro":
        if _contains_any(haystack, STRONG_SUCCESS_TOKENS):
            return "success"
        return "primary" if _contains_any(haystack, PRIMARY_TOKENS) else None

    if selected_theme == "shop":
        if _contains_any(haystack, SUCCESS_TOKENS) or _contains_any(haystack, SHOP_TOKENS):
            return "success"
        if _contains_any(haystack, PRIMARY_TOKENS):
            return "primary"
        return "primary"

    if _contains_any(haystack, SUCCESS_TOKENS):
        return "success"
    if _contains_any(haystack, PRIMARY_TOKENS):
        return "primary"
    return "primary"


def _merge_style(api_kwargs: Optional[dict[str, Any]], style: Optional[str]) -> dict[str, Any]:
    merged = dict(api_kwargs or {})
    normalized = _normalize_style(style)
    if _styles_enabled() and normalized and "style" not in merged:
        merged["style"] = normalized
    return merged


def inline_button(*args: Any, style: Optional[str] = None, **kwargs: Any) -> TelegramInlineKeyboardButton:
    text = args[0] if args else kwargs.get("text", "")
    callback_data = kwargs.get("callback_data")
    selected_style = style or infer_button_style(text, callback_data)
    kwargs["api_kwargs"] = _merge_style(kwargs.get("api_kwargs"), selected_style)
    return TelegramInlineKeyboardButton(*args, **kwargs)


def keyboard_button(*args: Any, style: Optional[str] = None, **kwargs: Any) -> TelegramKeyboardButton:
    text = args[0] if args else kwargs.get("text", "")
    selected_style = style or infer_button_style(text)
    kwargs["api_kwargs"] = _merge_style(kwargs.get("api_kwargs"), selected_style)
    return TelegramKeyboardButton(*args, **kwargs)
