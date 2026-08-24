"""
Shared/admin_notify.py
======================
ارسال هشدارهای ادمین (مثلاً وقتی یک سرور/نود از دسترس خارج می‌شود).
توکن و شناسه از متغیرهای محیطی ADMIN_BOT_TOKEN / ADMIN_ID خوانده می‌شوند.
"""

import logging
import os
from typing import Optional

from telegram import Bot

logger = logging.getLogger(__name__)

_ADMIN_BOT: Optional[Bot] = None


def _get_admin_bot() -> Optional[Bot]:
    global _ADMIN_BOT
    token = os.getenv("ADMIN_BOT_TOKEN")
    if not token:
        return None
    if _ADMIN_BOT is None:
        _ADMIN_BOT = Bot(token=token)
        # PTB v20+ Bot may need explicit initialize before send_message
        try:
            # initialize is async in newer versions; try sync path safely
            if hasattr(_ADMIN_BOT, "initialize"):
                # do not await here – notify_admin will initialize lazily
                pass
        except Exception:
            pass
    return _ADMIN_BOT


async def _ensure_bot_initialized(bot: Bot) -> None:
    """PTB v20+ requires Bot.initialize() before API calls when Bot is created manually."""
    try:
        # _initialized is private but stable in PTB 20.x
        if getattr(bot, "_initialized", False):
            return
        if hasattr(bot, "initialize"):
            await bot.initialize()
    except Exception as e:
        logger.debug("Bot initialize skipped: %s", e)


async def notify_admin(text: str) -> bool:
    """ارسال پیام متنی به ادمین. در صورت نبود توکن/شناسه فقط لاگ می‌کند."""
    bot = _get_admin_bot()
    if bot is None:
        logger.warning("notify_admin skipped (ADMIN_BOT_TOKEN not set): %s", text[:120])
        return False
    try:
        admin_id = int(os.getenv("ADMIN_ID", "0") or "0")
    except Exception:
        admin_id = 0
    if admin_id <= 0:
        logger.warning("notify_admin skipped (ADMIN_ID not set): %s", text[:120])
        return False
    try:
        await _ensure_bot_initialized(bot)
        await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        return True
    except Exception as e:
        logger.warning("notify_admin failed: %s", e)
        return False
