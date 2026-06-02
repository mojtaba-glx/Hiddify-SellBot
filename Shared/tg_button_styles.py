from __future__ import annotations

import os
from typing import Any, Optional

from telegram import InlineKeyboardButton as TelegramInlineKeyboardButton
from telegram import KeyboardButton as TelegramKeyboardButton


VALID_BUTTON_STYLES = {"primary", "success", "danger"}


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


def _normalize_style(style: Optional[str]) -> Optional[str]:
    if not style:
        return None
    normalized = str(style).strip().lower()
    return normalized if normalized in VALID_BUTTON_STYLES else None


def infer_button_style(text: Any, callback_data: Any = None) -> Optional[str]:
    label = str(text or "")
    data = str(callback_data or "")
    haystack = f"{label} {data}".lower()

    danger_tokens = (
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
    success_tokens = (
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
    primary_tokens = (
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

    if any(token in haystack for token in danger_tokens):
        return "danger"
    if any(token in haystack for token in success_tokens):
        return "success"
    if any(token in haystack for token in primary_tokens):
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
