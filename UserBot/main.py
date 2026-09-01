# UserBot/main.py
import os
import sys
import logging
import io
import random
import base64
import time
import hashlib
import re
import json
import asyncio
import socket
import fcntl
from html import escape
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from types import SimpleNamespace

# --- 1. مسیردهی پروژه (بسیار مهم برای پیدا کردن پوشه Shared) ---
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]  # رفتن به پوشه Hiddify-SellBot
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# --- 3. ایمپورت ماژول‌های خارجی با مدیریت خطا و اعتبارسنجی نسخه ---
import importlib
from typing import Optional, Any, List, Dict
from collections import defaultdict

# قفل (lock) برای جلوگیری از race condition در ویزارد خرید
_USER_WIZARD_LOCKS: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

# Version compatibility matrix
REQUIRED_VERSIONS = {
    'python-telegram-bot': '>=20.0'
    # python-dotenv doesn't have __version__ attribute, skip version check
}

def _check_package_version(package_name: str, min_version: str) -> bool:
    """Check if package meets minimum version requirement."""
    try:
        if package_name == 'python-telegram-bot':
            module = importlib.import_module('telegram')
        else:
            module = importlib.import_module(package_name.replace('-', '_'))
        
        version = getattr(module, '__version__', '0.0.0')
        
        # Simple version comparison for major.minor
        version_parts = version.split('.')[:2]
        min_parts = min_version.replace('>=', '').split('.')[:2]
        
        # Convert to integers for comparison
        try:
            version_num = int(version_parts[0]) * 100 + int(version_parts[1])
            min_num = int(min_parts[0]) * 100 + int(min_parts[1])
            return version_num >= min_num
        except (ValueError, IndexError):
            # Fallback: check if version string contains required version
            return min_version.replace('>=', '') in version or version >= min_version.replace('>=', '')
            
    except (ImportError, AttributeError):
        return False

def _safe_import_with_validation():
    """Safely import external packages with validation."""
    global load_dotenv, Update, InlineKeyboardMarkup, InlineKeyboardButton, Bot, ApplicationBuilder, CommandHandler, MessageHandler
    global CallbackQueryHandler, ContextTypes, filters, TelegramError, BadRequest, NetworkError, Conflict, BotCommand, MenuButtonCommands, HTTPXRequest
    
    try:
        # Check versions first
        for package, min_version in REQUIRED_VERSIONS.items():
            if not _check_package_version(package, min_version):
                raise ImportError(f"{package} version {min_version} required")
        
        from dotenv import load_dotenv
        from telegram import Update, InlineKeyboardMarkup, Bot, BotCommand, MenuButtonCommands
        from Shared.tg_button_styles import inline_button as InlineKeyboardButton
        from telegram.ext import (
            ApplicationBuilder, CommandHandler, MessageHandler, 
            CallbackQueryHandler, ContextTypes, filters
        )
        from telegram.error import TelegramError, BadRequest, NetworkError, Conflict
        from telegram.request import HTTPXRequest
        
        # Validate critical components
        if not all([Update, ApplicationBuilder, CommandHandler, MessageHandler]):
            raise ImportError("Critical telegram components missing")
            
        return True
        
    except ImportError as e:
        error_msg = f"❌ Critical: Import failed - {e}"
        install_cmd = "pip install python-telegram-bot>=20.0 python-dotenv>=0.19.0"
        
        print(error_msg)
        print(f"Install command: {install_cmd}")
        
        # Log to system if possible
        try:
            import logging
            logging.critical(f"Bot startup failed: {e}")
        except ImportError:
            pass
            
        sys.exit(1)

# Execute import with validation
_safe_import_with_validation()

# --- 2. ایمپورت ماژول‌های داخلی با مدیریت خطا و اعتبارسنجی ---
def _validate_internal_modules():
    """Validate that internal modules have required attributes."""
    validation_errors = []
    
    # Check database module
    try:
        if not hasattr(database, 'get_servers') or not callable(database.get_servers):
            validation_errors.append("database.get_servers missing or not callable")
        if not hasattr(database, 'get_welcome_message') or not callable(database.get_welcome_message):
            validation_errors.append("database.get_welcome_message missing or not callable")
    except Exception as e:
        validation_errors.append(f"database module validation failed: {e}")
    
    # Check plans_storage module
    try:
        if not hasattr(plans_storage, '_load_all_plans') or not callable(plans_storage._load_all_plans):
            validation_errors.append("plans_storage._load_all_plans missing or not callable")
    except Exception as e:
        validation_errors.append(f"plans_storage module validation failed: {e}")
    
    # Check userbot_db module
    try:
        if not hasattr(userbot_db, 'upsert_user') or not callable(userbot_db.upsert_user):
            validation_errors.append("userbot_db.upsert_user missing or not callable")
        if not hasattr(userbot_db, 'get_user_by_id') or not callable(userbot_db.get_user_by_id):
            validation_errors.append("userbot_db.get_user_by_id missing or not callable")
    except Exception as e:
        validation_errors.append(f"userbot_db module validation failed: {e}")
    
    # Check keyboard functions
    required_keyboards = [
        'main_menu_keyboard', 'cancel_keyboard', 'location_keyboard',
        'confirm_payment_keyboard', 'category_keyboard', 'plans_keyboard',
        'confirm_buy_keyboard', 'buy_wizard_keyboard', 'mixed_buy_keyboard',
        'trial_location_keyboard', 'services_list_keyboard', 'renew_services_keyboard',
    ]
    
    for keyboard_name in required_keyboards:
        try:
            keyboard_module = sys.modules.get('UserBot.keyboards')
            if not keyboard_module or not hasattr(keyboard_module, keyboard_name):
                validation_errors.append(f"keyboard.{keyboard_name} missing")
        except Exception as e:
            validation_errors.append(f"keyboard.{keyboard_name} validation failed: {e}")
    
    return validation_errors

try:
    from Shared import database, plans_storage, userbot_db, hiddify_api, multi_panel, sub_http_server
    from Shared import i18n as i18n
    from Shared.qr_utils import make_qr_image
    from UserBot import keyboards as UserBot_keyboards
    from UserBot.keyboards import (
        main_menu_keyboard, cancel_keyboard, receipt_cancel_keyboard, location_keyboard,
        confirm_payment_keyboard, category_keyboard, plans_keyboard,
        confirm_buy_keyboard, buy_wizard_keyboard, mixed_buy_keyboard,
        trial_location_keyboard,
        renew_services_keyboard,
        selected_plan_keyboard, wallet_inline_keyboard,
        confirm_payment_inline_keyboard, cancel_inline_keyboard,
        subscription_status_keyboard, direct_configs_keyboard,
        subscription_links_keyboard, subscription_configs_keyboard,
        replace_subscription_link_confirm_keyboard,
        services_list_keyboard, guide_os_keyboard, invite_banner_keyboard, force_join_keyboard,
        support_panel_keyboard, ticket_skip_screenshot_keyboard, ticket_confirm_keyboard,
        user_tickets_list_keyboard, user_ticket_detail_keyboard,
        language_keyboard,
    )
    from UserBot.utils.helpers import (  # noqa: F401
        _build_panel_user_comment,
        _calc_dynamic_price,
        _days_left_from_panel_user,
        _expected_server_price,
        _extract_start_payload,
        _extract_uuid_from_comment,
        _extract_uuid_from_user_input,
        _generate_order_id,
        _generate_service_code,
        _is_back_or_cancel_text,
        _is_user_missing_error,
        _normalize_action_text,
        _optional_int_from_any,
        _parse_panel_datetime,
        _parse_service_comment,
        _sort_plans,
        _to_float,
        _to_int,
        _usage_limit_from_panel_user,
    )

    # Validate internal modules
    validation_errors = _validate_internal_modules()
    if validation_errors:
        error_msg = "❌ Internal module validation failed:\n" + "\n".join(f"  - {err}" for err in validation_errors)
        print(error_msg)
        print("Please ensure all required functions and modules are properly implemented.")
        sys.exit(1)

except ImportError as e:
    print(f"❌ Critical: Internal module import failed - {e}")
    print("Ensure Shared module and UserBot.keyboards exist and are accessible")
    print(f"Current sys.path: {sys.path[:3]}...")  # Show first 3 paths for debugging
    sys.exit(1)
except Exception as e:
    print(f"❌ Critical: Unexpected error during module loading - {e}")
    print("This may indicate a syntax error or circular import in internal modules")
    sys.exit(1)

# --- 3. تنظیمات لاگ و توکن با بهبودهای تولیدی ---
LOG_DIR = project_root / "logs"
LOG_DIR.mkdir(exist_ok=True)
RECEIPTS_DIR = project_root / "Receiptions"
RECEIPTS_DIR.mkdir(exist_ok=True)

# Configure logging with rotation and better formatting
import logging.handlers
file_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "userbot.log", 
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5,
    encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s'
))

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)

# Reduce third-party HTTP verbosity to avoid leaking bot tokens in request URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Environment and token validation with better error handling
try:
    load_dotenv()
    TOKEN = os.getenv("USER_BOT_TOKEN")
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
    ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
    
    if not TOKEN:
        raise ValueError("USER_BOT_TOKEN not found")
    
    # Basic token validation (Telegram tokens are like: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz)
    if ':' not in TOKEN or len(TOKEN) < 20:
        raise ValueError("Invalid token format")
        
except Exception as e:
    logger.error(f"Configuration error: {e}")
    print(f"❌ Error: {e}")
    print("Please check your .env file and ensure USER_BOT_TOKEN is set correctly")
    sys.exit(1)

# --- 4. توابع کمکی ---
def get_user_step(context, user_id):
    step_key = f"step_{user_id}"
    ts_key = f"step_ts_{user_id}"
    step = context.user_data.get(step_key)
    if not step:
        return None

    ttl_seconds = 10 * 60
    try:
        ttl_seconds = int(os.getenv("USERBOT_STATE_TTL_SECONDS", "600") or "600")
    except (TypeError, ValueError):
        ttl_seconds = 10 * 60
    if ttl_seconds <= 0:
        return step

    now_ts = int(time.time())
    raw_ts = context.user_data.get(ts_key)
    if raw_ts is None:
        # Backward-compatible for sessions created before TTL support.
        context.user_data[ts_key] = now_ts
        return step

    try:
        age = now_ts - int(raw_ts)
    except (TypeError, ValueError):
        context.user_data[ts_key] = now_ts
        return step

    if age <= ttl_seconds:
        return step

    logger.info("User state expired (telegram_id=%s, step=%s, age=%ss)", user_id, step, age)
    context.user_data.pop(step_key, None)
    context.user_data.pop(ts_key, None)
    context.user_data.pop(f"pending_wallet_{user_id}", None)
    context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
    context.user_data.pop(f"pending_pay_{user_id}", None)
    context.user_data.pop(f"pending_rename_service_{user_id}", None)
    return None

def set_user_step(context, user_id, step):
    step_key = f"step_{user_id}"
    ts_key = f"step_ts_{user_id}"
    if step:
        context.user_data[step_key] = step
        context.user_data[ts_key] = int(time.time())
    else:
        context.user_data.pop(step_key, None)
        context.user_data.pop(ts_key, None)

DEFAULT_SUBS_SETTINGS = {
    "show_user_page_link": True,
    "show_username": True,
    "shuffle_configs": True,
    "shuffle_server_layout": True,
    "shuffle_config_layout": True,
    "show_direct_config": True,
    "show_auto_sub_link": False,
    "show_sub_link": True,
    "show_sub_link_b64": False,
    "show_multi_server": False,
    "show_multi_server_b64": False,
}
SUB_SERVICE_BASE_URL = (os.getenv("SUB_SERVICE_BASE_URL", "") or "").strip().rstrip("/")
SUB_SERVER_ENABLED = (os.getenv("SUB_SERVER_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}
SUB_SERVER_HOST = (os.getenv("SUB_SERVER_HOST", "0.0.0.0") or "0.0.0.0").strip()
SUB_SERVER_PORT = int(os.getenv("SUB_SERVER_PORT", "8787") or "8787")
SUB_SERVER_PUBLIC_SCHEME = (os.getenv("SUB_SERVER_PUBLIC_SCHEME", "https") or "https").strip().lower()
SUB_SERVER_PUBLIC_PORT = int(os.getenv("SUB_SERVER_PUBLIC_PORT", str(SUB_SERVER_PORT)) or str(SUB_SERVER_PORT))
SUB_SERVER_PUBLIC_HOST = (os.getenv("SUB_SERVER_PUBLIC_HOST", "") or "").strip()
USERBOT_ACTION_COOLDOWN_SECONDS = float(os.getenv("USERBOT_ACTION_COOLDOWN_SECONDS", "0.5") or "0.5")
USERBOT_ANTI_SPAM_ENABLED = (os.getenv("USERBOT_ANTI_SPAM_ENABLED", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
USERBOT_RATE_LIMIT_NOTICE_SECONDS = float(os.getenv("USERBOT_RATE_LIMIT_NOTICE_SECONDS", "5.0") or "5.0")
BUY_MENU_ACTION_COOLDOWN_SECONDS = float(os.getenv("USERBOT_BUY_MENU_ACTION_COOLDOWN_SECONDS", "0") or "0")
BUY_CALLBACK_COOLDOWN_SECONDS = float(os.getenv("USERBOT_BUY_CALLBACK_COOLDOWN_SECONDS", "0.2") or "0.2")
BUY_MENU_HOLD_SECONDS = float(os.getenv("USERBOT_BUY_MENU_HOLD_SECONDS", "1.0") or "1.0")
USERBOT_STATUS_PROBE_CONCURRENCY = int(os.getenv("USERBOT_STATUS_PROBE_CONCURRENCY", "3") or "3")
USERBOT_STATUS_SYNC_CONCURRENCY = int(os.getenv("USERBOT_STATUS_SYNC_CONCURRENCY", "2") or "2")
USERBOT_MISSING_SERVICE_DELETE_DAYS = int(
    os.getenv("USERBOT_MISSING_SERVICE_DELETE_DAYS", "7") or "7"
)
USERBOT_TICKET_AUTOCLOSE_ENABLED = (os.getenv("USERBOT_TICKET_AUTOCLOSE_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}
USERBOT_TICKET_AUTOCLOSE_HOURS = int(os.getenv("USERBOT_TICKET_AUTOCLOSE_HOURS", "24") or "24")
USERBOT_TICKET_AUTOCLOSE_INTERVAL_SECONDS = int(os.getenv("USERBOT_TICKET_AUTOCLOSE_INTERVAL_SECONDS", "600") or "600")

# Direct-buy delivery retry on transient Hiddify API errors.
DIRECT_DELIVERY_MAX_RETRIES = int(os.getenv("USERBOT_DIRECT_DELIVERY_MAX_RETRIES", "5") or "5")
DIRECT_DELIVERY_RETRY_DELAY_SECONDS = float(os.getenv("USERBOT_DIRECT_DELIVERY_RETRY_DELAY_SECONDS", "60") or "60")






async def _gather_with_limit(items: list[Any], worker, limit: int = 6) -> list[Any]:
    """
    اجرای موازی با سقف همزمانی برای کاهش زمان انتظار بدون فشار شدید به پنل.
    """
    if not items:
        return []
    sem = asyncio.Semaphore(max(1, int(limit)))

    async def _run_one(item):
        async with sem:
            return await worker(item)

    return await asyncio.gather(*(_run_one(i) for i in items))


def _check_action_rate_limit(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    action_key: str,
    *,
    cooldown: Optional[float] = None,
    event_ts: Optional[float] = None,
) -> tuple[bool, float]:
    """
    Anti-spam throttle per-user:
    - یک محدودیت کلی سریع برای همه اکشن‌ها
    - یک محدودیت مجزا برای خود اکشن (کلید action_key)
    """
    # اگر timestamp واقعی رویداد (زمان ارسال پیام کاربر) موجود باشد،
    # از همان استفاده می‌کنیم تا در زمان لگ/صف، پیام‌های قدیمی یکجا اجرا نشوند.
    now = float(event_ts) if event_ts is not None else time.time()
    data = context.user_data.setdefault("_rate_limit", {})

    # اگر ضداسپم غیرفعال باشد، هیچ محدودیتی اعمال نمی‌شود.
    if not USERBOT_ANTI_SPAM_ENABLED:
        return False, 0.0

    # Global short throttle (all actions)
    global_key = f"{user_id}:__global__"
    last_global = float(data.get(global_key) or 0.0)
    if now - last_global < 0.35:
        return True, max(0.0, 0.35 - (now - last_global))
    data[global_key] = now

    # Action-specific cooldown
    cd = float(cooldown if cooldown is not None else USERBOT_ACTION_COOLDOWN_SECONDS)
    action_full_key = f"{user_id}:{action_key}"
    last = float(data.get(action_full_key) or 0.0)
    if now - last < cd:
        return True, max(0.0, cd - (now - last))
    data[action_full_key] = now
    return False, 0.0


def _should_skip_stale_startup_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> bool:
    """
    فقط در لحظه استارت ربات:
    اگر برای یک کاربر چند آپدیت صف شده باشد، فقط آخرین آپدیت او را پردازش کن.
    """
    try:
        pending_map = context.application.bot_data.get("_startup_latest_pending_per_user") or {}
        if not isinstance(pending_map, dict):
            return False
        target_update_id = pending_map.get(int(user_id))
        if target_update_id is None:
            return False
        curr_update_id = int(getattr(update, "update_id", 0) or 0)
        if curr_update_id < int(target_update_id):
            return True
        # curr == target یا curr > target => از این به بعد صف اولیه برای این کاربر تمام است
        pending_map.pop(int(user_id), None)
        context.application.bot_data["_startup_latest_pending_per_user"] = pending_map
        return False
    except Exception:
        return False


def _get_subscription_settings() -> dict:
    settings = dict(DEFAULT_SUBS_SETTINGS)
    try:
        raw = userbot_db.get_subscription_settings()
        if isinstance(raw, dict):
            for k in settings.keys():
                if k in raw:
                    settings[k] = bool(raw[k])
    except Exception as e:
        logger.warning(f"Failed to load subscription settings in user bot: {e}")
    return settings


def _get_buy_renew_settings() -> dict:
    try:
        settings = userbot_db.get_buy_renew_settings()
        if isinstance(settings, dict):
            mode = str(settings.get("renew_policy") or "advanced").strip().lower()
            if mode == "oversell":
                mode = "default"
            settings["renew_policy"] = mode if mode in {"fair", "advanced", "default"} else "advanced"
            fallback_volume_mode = "add" if settings["renew_policy"] in {"default", "fair"} else "reset"
            fallback_time_mode = "add" if settings["renew_policy"] == "fair" else "reset"
            volume_mode = str(settings.get("renew_volume_mode") or "").strip().lower()
            time_mode = str(settings.get("renew_time_mode") or "").strip().lower()
            settings["renew_volume_mode"] = volume_mode if volume_mode in {"add", "reset"} else fallback_volume_mode
            settings["renew_time_mode"] = time_mode if time_mode in {"add", "reset"} else fallback_time_mode
            try:
                settings["renew_max_days"] = max(1, int(settings.get("renew_max_days") or 3))
            except Exception:
                settings["renew_max_days"] = 3
            try:
                settings["renew_max_remaining_gb"] = max(1, int(settings.get("renew_max_remaining_gb") or 3))
            except Exception:
                settings["renew_max_remaining_gb"] = 3
            settings["renew_unlimited_volume"] = bool(settings.get("renew_unlimited_volume", False))
            settings["renew_unlimited_time"] = bool(settings.get("renew_unlimited_time", False))
            try:
                settings["renew_unlimited_volume_from_gb"] = max(1, int(settings.get("renew_unlimited_volume_from_gb") or 1000))
            except Exception:
                settings["renew_unlimited_volume_from_gb"] = 1000
            try:
                settings["renew_unlimited_time_from_days"] = max(1, int(settings.get("renew_unlimited_time_from_days") or 365))
            except Exception:
                settings["renew_unlimited_time_from_days"] = 365
            try:
                settings["plan_columns"] = int(settings.get("plan_columns") or 1)
            except Exception:
                settings["plan_columns"] = 1
            if settings["plan_columns"] not in {1, 2}:
                settings["plan_columns"] = 1
            try:
                settings["server_columns"] = int(settings.get("server_columns") or 1)
            except Exception:
                settings["server_columns"] = 1
            if settings["server_columns"] not in {1, 2, 3}:
                settings["server_columns"] = 1
            return settings
    except Exception as e:
        logger.warning(f"Failed to load buy/renew settings in user bot: {e}")
    return {
        "enable_buy": True,
        "enable_renew": True,
        "show_renew_in_main_menu": True,
        "renew_policy": "advanced",
        "renew_volume_mode": "reset",
        "renew_time_mode": "reset",
        "renew_max_days": 3,
        "renew_max_remaining_gb": 3,
        "renew_unlimited_volume": False,
        "renew_unlimited_time": False,
        "renew_unlimited_volume_from_gb": 1000,
        "renew_unlimited_time_from_days": 365,
        "plan_columns": 1,
        "server_columns": 1,
        "event_channel_enabled": False,
        "event_channel_id": "",
    }


def _get_tx_plans_settings() -> dict:
    try:
        settings = userbot_db.get_tx_plans_settings()
        if isinstance(settings, dict):
            settings["random_tx_spec"] = bool(settings.get("random_tx_spec", False))
            settings["plan_categories_enabled"] = bool(settings.get("plan_categories_enabled", True))
            settings["plan_sort_by_priority"] = bool(settings.get("plan_sort_by_priority", True))
            mode = str(settings.get("plan_sort_mode") or "price").strip().lower()
            settings["plan_sort_mode"] = mode if mode in {"price", "gb", "days"} else "price"
            settings["plan_sort_desc"] = bool(settings.get("plan_sort_desc", False))
            try:
                settings["min_transaction_toman"] = max(1, int(settings.get("min_transaction_toman") or 10000))
            except Exception:
                settings["min_transaction_toman"] = 10000
            return settings
    except Exception as e:
        logger.warning(f"Failed to load tx/plans settings in user bot: {e}")
    return {
        "random_tx_spec": False,
        "min_transaction_toman": 10000,
        "plan_categories_enabled": True,
        "plan_sort_by_priority": True,
        "plan_sort_mode": "price",
        "plan_sort_desc": False,
    }


def _get_marketing_settings() -> dict:
    try:
        settings = userbot_db.get_marketing_settings()
        if isinstance(settings, dict):
            return settings
    except Exception as e:
        logger.warning(f"Failed to load marketing settings in user bot: {e}")
    return {
        "enable_discount_code": False,
        "enable_increase_code": False,
        "show_gift_button": False,
        "show_user_status": True,
        "instant_gift_coupon": False,
        "auto_gift_text": "🎁 هدیه شما فعال شد. از همراهی‌تان متشکریم.",
        "min_auto_gift_charge": 100000,
    }


def _get_force_join_settings() -> dict:
    try:
        settings = userbot_db.get_force_join_settings()
        if isinstance(settings, dict):
            return settings
    except Exception as e:
        logger.warning(f"Failed to load force-join settings in user bot: {e}")
    return {
        "enabled": False,
        "channel_id": "",
        "channel_username": "",
        "channel_link": "",
        "guide_text": (
            "🔒 برای استفاده از ربات، ابتدا در کانال پشتیبانی عضو شوید.\n"
            "پس از عضویت روی «✅ بررسی عضویت» بزنید."
        ),
    }


def _get_payment_settings() -> dict:
    try:
        settings = userbot_db.get_payment_settings()
        if isinstance(settings, dict):
            return settings
    except Exception as e:
        logger.warning(f"Failed to load payment settings in user bot: {e}")
    return {
        "enable_card_to_card": True,
        "require_last4_for_card_receipt": False,
        "enable_zarinpal": False,
        "enable_perfect_money": False,
        "enable_crypto": False,
        "event_channel_enabled": False,
        "event_channel_id": "",
    }


def _force_join_target(settings: dict) -> Any:
    username = str((settings or {}).get("channel_username") or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    channel_id = str((settings or {}).get("channel_id") or "").strip()
    if channel_id.lstrip("-").isdigit():
        return int(channel_id)
    return None


def _force_join_url(settings: dict) -> str:
    link = str((settings or {}).get("channel_link") or "").strip()
    if link:
        return link
    username = str((settings or {}).get("channel_username") or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}"
    return ""


async def _user_joined_force_channel(context: ContextTypes.DEFAULT_TYPE, user_id: int, settings: dict) -> bool:
    target = _force_join_target(settings)
    if target is None:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id=target, user_id=user_id)
        status = str(getattr(member, "status", "")).lower()
        return status in {"member", "administrator", "creator", "owner"}
    except Exception:
        return False


async def _enforce_force_join(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    send_text,
) -> bool:
    settings = _get_force_join_settings()
    if not bool(settings.get("enabled", False)):
        return True
    target = _force_join_target(settings)
    if target is None:
        return True
    if await _user_joined_force_channel(context, user_id, settings):
        return True
    guide_text = str(settings.get("guide_text") or "").strip() or "🔒 لطفاً ابتدا در کانال عضو شوید."
    await send_text(guide_text, reply_markup=force_join_keyboard(_force_join_url(settings)))
    return False


def _get_text_settings() -> dict:
    try:
        settings = userbot_db.get_text_settings()
        if isinstance(settings, dict):
            return settings
    except Exception as e:
        logger.warning(f"Failed to load text settings in user bot: {e}")
    return {
        "welcome_message": "سلام {full_name} عزیز 👋\nبه ربات ما خوش آمدید.",
        "faq_text": (
            "❓ سوالات متداول\n\n"
            "1) لینک اشتراک را کجا بزنم؟\n"
            "از بخش «📊وضعیت اشتراک» وارد سرویس شوید و روی «لینک اشتراک» بزنید.\n\n"
            "2) اگر کانفیگ وصل نشد چه کنم؟\n"
            "اول اینترنت و تاریخ/ساعت گوشی را چک کنید، سپس «بروزرسانی اطلاعات» بزنید.\n\n"
            "3) چطور تمدید کنم؟\n"
            "از «♾تمدید اشتراک» سرویس را انتخاب کنید و پلن تمدید را بخرید.\n\n"
            "4) پشتیبانی از کجاست؟\n"
            "از دکمه «📩پشتیبانی» پیام خود را ارسال کنید."
        ),
        "guide_text": "انتخاب سیستم عامل ⬇️",
        "guide_android_text": (
            "📱 راهنمای اندروید\n\n"
            "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
            "2) v2rayNG:\nhttps://github.com/2dust/v2rayNG/releases\n\n"
            "3) NekoBox for Android:\nhttps://github.com/MatsuriDayo/NekoBoxForAndroid/releases\n\n"
            "بعد از نصب، لینک اشتراک را Import کنید و Connect بزنید."
        ),
        "guide_ios_text": (
            "📱 راهنمای iOS\n\n"
            "1) Streisand:\nhttps://apps.apple.com/app/streisand/id6450534064\n\n"
            "2) Hiddify (iOS):\nhttps://apps.apple.com/app/hiddify-proxy-vpn/id6596777532\n\n"
            "بعد از نصب، لینک اشتراک را Import کرده و اتصال را فعال کنید."
        ),
        "guide_windows_text": (
            "🖥️ راهنمای ویندوز\n\n"
            "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
            "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
            "3) v2rayN:\nhttps://github.com/2dust/v2rayN/releases\n\n"
            "پس از نصب، لینک اشتراک را Paste/Import کنید و Connect شوید."
        ),
        "guide_mac_text": (
            "💻 راهنمای مک\n\n"
            "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
            "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
            "پس از نصب، لینک اشتراک را Import کنید و اتصال را فعال کنید."
        ),
        "guide_linux_text": (
            "🖥️ راهنمای لینوکس\n\n"
            "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
            "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
            "پس از نصب، لینک اشتراک را در برنامه وارد کنید و Connect بزنید."
        ),
        "invite_text": "💌 لینک دعوت شما:\n{invite_link}",
        "invite_info_text": "🎁 دعوت دوستان خود از هدایای ویژه ای بهره مند شوید",
        "invite_banner_text": (
            "🎁 بنر دعوت اختصاصی شما\n\n"
            "🔗 لینک دعوت شما:\n{invite_link}\n\n"
            "دوستانت را دعوت کن و از مزایای ویژه بهره‌مند شو."
        ),
        "invite_banner_photo_id": "",
        "servers_list_text": "📡 **لیست سرورها**\nلطفاً لوکیشن مورد نظر خود را انتخاب کنید:",
        "plans_list_text": "🛒 **لطفاً پلن مورد نظر خود را انتخاب کنید:**",
        "ticket_panel_text": "📩 برای ارتباط با پشتیبانی، پیام خود را ارسال کنید.",
        "zarinpal_pro_text": "0",
        "card_to_card_text": "0",
    }


def _format_text_template(template: str, **kwargs: Any) -> str:
    try:
        return str(template).format(**kwargs)
    except Exception:
        return str(template)


def _build_card_to_card_payment_text(
    *,
    amount_toman: int,
    card_number: str,
    card_owner: str,
    card_bank: str,
    text_settings: dict,
) -> str:
    owner_safe = escape(str(card_owner or ""))
    bank_safe = escape(str(card_bank or ""))
    card_safe = escape(str(card_number or ""))
    template = str((text_settings or {}).get("card_to_card_text") or "").strip()
    if not template or template == "0":
        bank_line = f"🏦 بانک: {bank_safe}\n" if bank_safe else ""
        return (
            f"💰 لطفا دقیقا مبلغ: <code>{int(amount_toman) * 10:d}</code> ریال\n"
            f"💰 معادل: {int(amount_toman):,} تومان\n"
            f"💳 به شماره کارت: <code>{card_safe}</code>\n"
            f"👤 به نام: {owner_safe}\n"
            f"{bank_line}"
            "❗ بعد از واریز مبلغ اسکرین شات از تراکنش برای ما ارسال کنید."
        )
    return (
        template
        .replace("{CARD}", card_safe)
        .replace("{HOLDER}", owner_safe)
        .replace("{BANK}", bank_safe)
        .replace("{AMOUNT}", f"{int(amount_toman):,}")
        .replace("{RIAL}", f"{int(amount_toman) * 10:d}")
    )


def _apply_random_tx_marker(amount_toman: int, tx_settings: Optional[dict] = None):
    """
    If random_tx_spec is enabled, add a small random marker amount to make each
    card-to-card transaction distinguishable.
    Returns: (final_amount_toman, marker_delta_toman)
    """
    try:
        base_amount = int(amount_toman or 0)
    except Exception:
        base_amount = 0
    if base_amount <= 0:
        return 0, 0

    settings = tx_settings if isinstance(tx_settings, dict) else _get_tx_plans_settings()
    if not bool((settings or {}).get("random_tx_spec", False)):
        return base_amount, 0

    marker = random.randint(101, 997)
    if marker % 10 == 0:
        marker += 1
    return base_amount + marker, marker




USER_TICKET_SHOT_START_PREFIX = "tshotu"


def _build_user_ticket_shot_payload(ticket_code: int, message_id: int) -> str:
    return f"{USER_TICKET_SHOT_START_PREFIX}_{int(ticket_code)}_{int(message_id)}"


def _parse_user_ticket_shot_payload(payload: str) -> tuple[int, int]:
    raw = str(payload or "").strip()
    m = re.match(rf"^{re.escape(USER_TICKET_SHOT_START_PREFIX)}_(\d+)_(\d+)$", raw)
    if not m:
        return 0, 0
    try:
        code = int(m.group(1))
        msg_id = int(m.group(2))
    except Exception:
        return 0, 0
    if code <= 0 or msg_id <= 0:
        return 0, 0
    return code, msg_id


async def _get_user_bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    cached = str(context.bot_data.get("_user_bot_username") or "").strip().lstrip("@")
    if cached:
        return cached
    env_name = str(os.getenv("SUB_BOT_USERNAME", "") or "").strip().lstrip("@")
    if env_name:
        context.bot_data["_user_bot_username"] = env_name
        return env_name
    try:
        me = await context.bot.get_me()
        username = str(getattr(me, "username", "") or "").strip().lstrip("@")
        if username:
            context.bot_data["_user_bot_username"] = username
        return username
    except Exception:
        return ""


async def _build_user_ticket_screenshot_links(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_code: int,
    messages: List[Dict[str, Any]],
) -> Dict[int, str]:
    username = await _get_user_bot_username(context)
    if not username:
        return {}
    links: Dict[int, str] = {}
    for idx, item in enumerate(messages or [], start=1):
        fid = str(item.get("photo_file_id") or "").strip()
        mid = int(item.get("id") or 0)
        if not fid or mid <= 0:
            continue
        payload = _build_user_ticket_shot_payload(ticket_code, mid)
        links[idx] = f"https://t.me/{username}?start={payload}"
    return links


async def _handle_user_ticket_shot_start(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    payload: str,
    user_id: int,
    internal_user_id: int,
) -> bool:
    code, msg_id = _parse_user_ticket_shot_payload(payload)
    if code <= 0 or msg_id <= 0:
        return False

    msg_obj = update.callback_query.message if update.callback_query else update.message
    if not msg_obj:
        return True

    ticket = userbot_db.get_user_ticket_by_code(int(internal_user_id), int(code))
    if not ticket:
        await msg_obj.reply_text("❌ اسکرین‌شات یافت نشد یا دسترسی ندارید.")
        return True

    rows = userbot_db.get_ticket_messages(code)
    target: Dict[str, Any] | None = None
    idx = 0
    for i, item in enumerate(rows or [], start=1):
        if int(item.get("id") or 0) == int(msg_id):
            if str(item.get("photo_file_id") or "").strip():
                target = item
                idx = i
            break

    if not target:
        await msg_obj.reply_text("❌ اسکرین‌شات موردنظر یافت نشد.")
        return True

    photo_id = str(target.get("photo_file_id") or "").strip()
    caption = f"🖼 اسکرین‌شات #{idx} | تیکت #{code}"
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 بازگشت به تیکت", callback_data=f"support:view:{code}:1")]]
    )

    sent = False
    try:
        await context.bot.send_photo(chat_id=user_id, photo=photo_id, caption=caption, reply_markup=kb)
        sent = True
    except Exception:
        sent = False

    if not sent and ADMIN_BOT_TOKEN:
        try:
            admin_bot = Bot(token=ADMIN_BOT_TOKEN)
            f = await admin_bot.get_file(photo_id)
            raw = await f.download_as_bytearray()
            bio = io.BytesIO(raw)
            bio.name = "ticket_screenshot.jpg"
            bio.seek(0)
            await context.bot.send_photo(chat_id=user_id, photo=bio, caption=caption, reply_markup=kb)
            sent = True
        except Exception:
            sent = False

    if not sent:
        await msg_obj.reply_text("❌ نمایش اسکرین‌شات ممکن نشد.")
    return True


def _default_zarinpal_text() -> str:
    return (
        "🌼 لطفا روی دکمه زیر کلیک کنید تا به درگاه پرداخت منتقل شوید و پرداخت را انجام دهید.\n"
        "⚠️ توجه: پس از پرداخت، فیلترشکن خود را روشن کرده و روی «دریافت محصول» بزنید "
        "تا به ربات برگردید و پرداخت شما تایید شود."
    )


def _build_zarinpal_links_keyboard(vouchers: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for item in vouchers:
        link = str(item.get("zarinpal_link") or "").strip()
        if not link:
            continue
        amount = int(item.get("amount_toman") or 0)
        label = f"💳 پرداخت {amount:,} تومان"
        rows.append([InlineKeyboardButton(label, url=link)])
    rows.append([InlineKeyboardButton("بازگشت", callback_data="wallet:back")])
    return InlineKeyboardMarkup(rows)


def _default_faq_text() -> str:
    return (
        "❓ سوالات متداول\n\n"
        "1) لینک اشتراک را کجا بزنم؟\n"
        "از بخش «📊وضعیت اشتراک» وارد سرویس شوید و روی «لینک اشتراک» بزنید.\n\n"
        "2) اگر کانفیگ وصل نشد چه کنم؟\n"
        "اول اینترنت و تاریخ/ساعت گوشی را چک کنید، سپس «بروزرسانی اطلاعات» بزنید.\n\n"
        "3) چطور تمدید کنم؟\n"
        "از «♾تمدید اشتراک» سرویس را انتخاب کنید و پلن تمدید را بخرید.\n\n"
        "4) پشتیبانی از کجاست؟\n"
        "از دکمه «📩پشتیبانی» پیام خود را ارسال کنید."
    )


def _default_guide_intro_text() -> str:
    return "انتخاب سیستم عامل ⬇️"


def _default_guide_platform_text(platform: str) -> str:
    p = str(platform or "").strip().lower()
    guides = {
        "android": (
            "📱 راهنمای اندروید\n\n"
            "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
            "2) v2rayNG:\nhttps://github.com/2dust/v2rayNG/releases\n\n"
            "3) NekoBox for Android:\nhttps://github.com/MatsuriDayo/NekoBoxForAndroid/releases\n\n"
            "بعد از نصب، لینک اشتراک را Import کنید و Connect بزنید."
        ),
        "ios": (
            "📱 راهنمای iOS\n\n"
            "1) Streisand:\nhttps://apps.apple.com/app/streisand/id6450534064\n\n"
            "2) Hiddify (iOS):\nhttps://apps.apple.com/app/hiddify-proxy-vpn/id6596777532\n\n"
            "بعد از نصب، لینک اشتراک را Import کرده و اتصال را فعال کنید."
        ),
        "windows": (
            "🖥️ راهنمای ویندوز\n\n"
            "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
            "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
            "3) v2rayN:\nhttps://github.com/2dust/v2rayN/releases\n\n"
            "پس از نصب، لینک اشتراک را Paste/Import کنید و Connect شوید."
        ),
        "mac": (
            "💻 راهنمای مک\n\n"
            "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
            "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
            "پس از نصب، لینک اشتراک را Import کنید و اتصال را فعال کنید."
        ),
        "linux": (
            "🖥️ راهنمای لینوکس\n\n"
            "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
            "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
            "پس از نصب، لینک اشتراک را در برنامه وارد کنید و Connect بزنید."
        ),
    }
    return guides.get(p, "❌ راهنمای این سیستم‌عامل یافت نشد.")


def _guide_platform_text(platform: str, text_settings: dict) -> str:
    key_map = {
        "android": "guide_android_text",
        "ios": "guide_ios_text",
        "windows": "guide_windows_text",
        "mac": "guide_mac_text",
        "linux": "guide_linux_text",
    }
    field = key_map.get(str(platform or "").strip().lower())
    if not field:
        return "❌ راهنمای این سیستم‌عامل یافت نشد."
    custom = str((text_settings or {}).get(field) or "").strip()
    return custom or _default_guide_platform_text(platform)


def _ticket_status_title(status: str) -> str:
    s = str(status or "").strip().lower()
    if s == "open":
        return "✅ باز"
    if s == "closed":
        return "📪 بسته"
    return "⏳ در انتظار"


def _ticket_compose_preview_text(data: Dict[str, Any]) -> str:
    title = str((data or {}).get("title") or "").strip() or "-"
    question = str((data or {}).get("question") or "").strip() or "-"
    has_photo = bool(str((data or {}).get("receipt_photo_id") or "").strip())
    screenshot_line = "📎 اسکرین‌شات: ارسال شده ✅" if has_photo else "📎 اسکرین‌شات: ارسال نشد"
    return (
        "📩 تایید اطلاعات تیکت\n\n"
        f"📌 عنوان:\n{title}\n\n"
        f"📝 سوال:\n{question}\n\n"
        f"{screenshot_line}\n\n"
        "❗️در صورت تایید اطلاعات، برای ارسال تیکت گزینه «✅ارسال» را انتخاب نمایید."
    )


def _ticket_reply_preview_text(data: Dict[str, Any]) -> str:
    reply_text = str((data or {}).get("reply_text") or "").strip() or "-"
    has_photo = bool(str((data or {}).get("receipt_photo_id") or "").strip())
    screenshot_line = "📎 اسکرین‌شات: ارسال شده ✅" if has_photo else "📎 اسکرین‌شات: ارسال نشد"
    return (
        "📩 تایید اطلاعات پاسخ تیکت\n\n"
        f"📝 پاسخ شما:\n{reply_text}\n\n"
        f"{screenshot_line}\n\n"
        "❗️در صورت تایید اطلاعات، برای ارسال پاسخ تیکت گزینه «✅ارسال» را انتخاب نمایید."
    )


def _ticket_text_cancel_keyboard(mode: str = "new") -> InlineKeyboardMarkup:
    flow = str(mode or "new").strip().lower()
    if flow not in {"new", "reply"}:
        flow = "new"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌لغو", callback_data=f"support:{flow}:cancel")]]
    )


def _ticket_detail_text(
    ticket: Dict[str, Any],
    messages: List[Dict[str, Any]],
    screenshot_links: Optional[Dict[int, str]] = None,
) -> str:
    code = str(ticket.get("ticket_code") or "-")
    title = str(ticket.get("title") or "").strip() or "-"
    lines = [
        f"🧾 شناسه تیکت: {escape(code)}",
        "❖⬩--------------------------------⬩❖",
    ]

    for idx, item in enumerate(messages or [], start=1):
        sender = str(item.get("sender_type") or "").strip().lower()
        sender_title = "◈سوال:" if sender == "user" else "◈پاسخ:"
        text = str(item.get("message_text") or "").strip()
        when = str(item.get("created_at") or "-")
        has_photo = bool(str(item.get("photo_file_id") or "").strip())
        lines.append(f"📅تاریخ ایجاد: {escape(when)}")
        if idx == 1:
            lines.append("◈عنوان:")
            lines.append(escape(title))
        lines.append(sender_title)
        if text:
            lines.append(escape(text))
        if has_photo:
            shot_link = str((screenshot_links or {}).get(idx) or "").strip()
            if shot_link:
                lines.append(f"<a href=\"{escape(shot_link, quote=True)}\">اسکرین‌شات</a>")
            else:
                lines.append("اسکرین‌شات")
        lines.append("❖⬩------------------------------⬩❖")
    text = "\n".join(lines)
    if len(text) > 3900:
        if screenshot_links:
            return _ticket_detail_text(ticket, messages, screenshot_links=None)
        return text[:3890] + "\n..."
    return text


async def _send_support_panel(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    message=None,
    text_settings: Optional[Dict[str, Any]] = None,
) -> None:
    settings = text_settings if isinstance(text_settings, dict) else _get_text_settings()
    panel_text = str(settings.get("ticket_panel_text") or "").strip() or "📩 برای ارتباط با پشتیبانی، پیام خود را ارسال کنید."
    if message is not None:
        try:
            await message.edit_text(panel_text, reply_markup=support_panel_keyboard())
            return
        except Exception:
            pass
    await context.bot.send_message(
        chat_id=user_id,
        text=panel_text,
        reply_markup=support_panel_keyboard(),
    )


async def _notify_admin_new_ticket(ticket: Dict[str, Any]) -> None:
    if not (ADMIN_ID and ADMIN_BOT_TOKEN):
        return
    code = int(ticket.get("ticket_code") or 0)
    if code <= 0:
        return
    full_name = str(ticket.get("full_name") or ticket.get("db_full_name") or "").strip()
    username = str(ticket.get("username") or ticket.get("db_username") or "").strip().lstrip("@")
    telegram_id = str(ticket.get("telegram_id") or ticket.get("db_telegram_id") or "-")
    title = str(ticket.get("title") or "").strip() or "-"
    question = str(ticket.get("question") or "").strip() or "-"
    receipt_photo_id = str(ticket.get("receipt_photo_id") or "").strip()
    display_name = full_name or (f"@{username}" if username else telegram_id)
    text = (
        f"📩 تیکت جدید #{code}\n"
        f"📋 موضوع: {title}\n"
        f"👤 مشتری: {display_name}\n\n"
        f"{question}"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📩 مشاهده تیکت", callback_data=f"userbot:ticket:detail:{code}:pending:1")],
            [InlineKeyboardButton("📬تیکت‌های در انتظار", callback_data="userbot:tickets:list:pending:1")],
        ]
    )
    try:
        admin_bot = Bot(token=ADMIN_BOT_TOKEN)
        await admin_bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=kb)
    except Exception as e:
        logger.warning("Failed to notify admin on new ticket: %s", e)


async def _notify_admin_ticket_reply(ticket: Dict[str, Any]) -> None:
    if not (ADMIN_ID and ADMIN_BOT_TOKEN):
        return
    code = int(ticket.get("ticket_code") or 0)
    if code <= 0:
        return
    full_name = str(ticket.get("full_name") or ticket.get("db_full_name") or "").strip()
    username = str(ticket.get("username") or ticket.get("db_username") or "").strip().lstrip("@")
    telegram_id = str(ticket.get("telegram_id") or ticket.get("db_telegram_id") or "-")
    display_name = full_name or (f"@{username}" if username else telegram_id)
    text = (
        "📨 پاسخ جدید کاربر در تیکت\n\n"
        f"🆔 شناسه تیکت: {code}\n"
        f"👤 کاربر: {display_name}\n"
        "برای مشاهده جزئیات وارد تیکت شوید."
    )
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📩 مشاهده تیکت", callback_data=f"userbot:ticket:detail:{code}:open:1")]]
    )
    try:
        admin_bot = Bot(token=ADMIN_BOT_TOKEN)
        await admin_bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=kb)
    except Exception as e:
        logger.warning("Failed to notify admin on ticket reply: %s", e)




def _is_unlimited_volume(limit_gb: float) -> bool:
    br = _get_buy_renew_settings()
    if not bool(br.get("renew_unlimited_volume", False)):
        return False
    try:
        threshold = float(br.get("renew_unlimited_volume_from_gb") or 1000)
    except (TypeError, ValueError):
        threshold = 1000.0
    return limit_gb >= threshold


def _is_unlimited_time(days_val: int) -> bool:
    br = _get_buy_renew_settings()
    if not bool(br.get("renew_unlimited_time", False)):
        return False
    try:
        threshold = int(br.get("renew_unlimited_time_from_days") or 365)
    except (TypeError, ValueError):
        threshold = 365
    return int(days_val) >= threshold


def _main_menu_keyboard(lang: str = "fa"):
    br = _get_buy_renew_settings()
    mkt = _get_marketing_settings()
    try:
        ref_settings = userbot_db.get_referral_settings()
        ref_enabled = bool(ref_settings.get("referral_enabled", False))
    except Exception:
        ref_enabled = False
    return main_menu_keyboard(
        show_renew=bool(br.get("show_renew_in_main_menu", True)),
        show_invite=bool(mkt.get("show_gift_button", False)) or ref_enabled,
        lang=lang,
    )


def _user_lang(user_id: int) -> str:
    """زبان ذخیره‌شده کاربر (چندزبانه)."""
    try:
        return i18n.get_user_lang(int(user_id or 0))
    except Exception:
        return "fa"


def _resolve_sub_service_base_url(service: Optional[dict] = None) -> str:
    """
    آدرس عمومی سرویس ساب:
    1) اگر دامنه سفارشی از تنظیمات ادمین ست باشد، همان
    2) اگر SUB_SERVICE_BASE_URL ست باشد، همان
    3) در غیر این‌صورت تلاش از دامنه سرویس
    """
    try:
        custom_base = (userbot_db.get_managed_sub_base_url() or "").strip().rstrip("/")
    except Exception:
        custom_base = ""
    if custom_base:
        return custom_base

    if SUB_SERVICE_BASE_URL:
        return SUB_SERVICE_BASE_URL

    # Optional explicit public host (domain or IP) for managed sub links.
    explicit = (SUB_SERVER_PUBLIC_HOST or "").strip().rstrip("/")
    if explicit:
        try:
            parsed = urlparse(explicit if "://" in explicit else f"//{explicit}")
            host = (parsed.hostname or "").strip()
            scheme = (parsed.scheme or SUB_SERVER_PUBLIC_SCHEME or "https").strip().lower()
            port = parsed.port if parsed.port is not None else SUB_SERVER_PUBLIC_PORT
            if host:
                default_port = (scheme == "https" and int(port) == 443) or (
                    scheme == "http" and int(port) == 80
                )
                if default_port:
                    return f"{scheme}://{host}"
                return f"{scheme}://{host}:{int(port)}"
        except Exception:
            pass

    if service:
        try:
            base_urls = _get_service_node_base_urls(service)
        except Exception:
            base_urls = []
        if base_urls:
            p = urlparse(base_urls[0])
            host = (p.hostname or "").strip()
            if host:
                scheme = SUB_SERVER_PUBLIC_SCHEME or (p.scheme or "https")
                default_port = (scheme == "https" and SUB_SERVER_PUBLIC_PORT == 443) or (
                    scheme == "http" and SUB_SERVER_PUBLIC_PORT == 80
                )
                if default_port:
                    return f"{scheme}://{host}"
                return f"{scheme}://{host}:{SUB_SERVER_PUBLIC_PORT}"

        # Fallback for old services without uuid/node mapping:
        # use primary server host so we never return localhost in public links.
        try:
            sid = int(service.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        if sid > 0:
            srv = database.get_server_by_id(sid)
            if srv:
                panel = (srv.get("panel_url") or "").strip()
                if panel:
                    p = urlparse(panel)
                    host = (p.hostname or "").strip()
                    if host:
                        scheme = SUB_SERVER_PUBLIC_SCHEME or (p.scheme or "https")
                        default_port = (scheme == "https" and SUB_SERVER_PUBLIC_PORT == 443) or (
                            scheme == "http" and SUB_SERVER_PUBLIC_PORT == 80
                        )
                        if default_port:
                            return f"{scheme}://{host}"
                        return f"{scheme}://{host}:{SUB_SERVER_PUBLIC_PORT}"

    # Last fallback: use server IP (not localhost) to keep links usable remotely.
    detected_ip = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = str(s.getsockname()[0] or "").strip()
            if ip and not ip.startswith("127."):
                detected_ip = ip
    except Exception:
        pass

    if not detected_ip:
        try:
            infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
            for info in infos:
                ip = str((info[4] or [""])[0] or "").strip()
                if ip and not ip.startswith("127."):
                    detected_ip = ip
                    break
        except Exception:
            pass

    if not detected_ip:
        host = (SUB_SERVER_HOST or "").strip()
        if host and host not in {"0.0.0.0", "::", "127.0.0.1", "localhost"}:
            detected_ip = host
    if not detected_ip:
        detected_ip = "127.0.0.1"

    scheme = SUB_SERVER_PUBLIC_SCHEME or "https"
    default_port = (scheme == "https" and SUB_SERVER_PUBLIC_PORT == 443) or (
        scheme == "http" and SUB_SERVER_PUBLIC_PORT == 80
    )
    if default_port:
        return f"{scheme}://{detected_ip}"
    return f"{scheme}://{detected_ip}:{SUB_SERVER_PUBLIC_PORT}"


def _resolve_service_uuid_for_managed_sub_link(service_id: int, service: Optional[dict] = None) -> str:
    service_obj = service or {}
    try:
        sid = int(service_id or 0)
    except (TypeError, ValueError):
        sid = 0
    if sid > 0:
        try:
            mappings = userbot_db.get_service_nodes(sid) or []
        except Exception:
            mappings = []

        for m in mappings:
            candidate = str((m or {}).get("panel_user_uuid") or "").strip()
            if candidate:
                return candidate

    uuid = _extract_uuid_from_comment(service_obj.get("comment") or "")
    if uuid:
        return str(uuid).strip()
    return ""


def _get_or_create_bot_sub_links(service_id: int, service: Optional[dict] = None) -> tuple[str, str]:
    base = _resolve_sub_service_base_url(service)
    service_uuid = _resolve_service_uuid_for_managed_sub_link(int(service_id), service=service)
    if service_uuid:
        return (
            f"{base}/sub/{service_uuid}/all.txt",
            f"{base}/sub/{service_uuid}/all.txt?base64=1",
        )
    token = userbot_db.ensure_service_sub_token(int(service_id))
    return f"{base}/sub/{token}/all.txt", f"{base}/sub/{token}/all.txt?base64=1"


def _should_show_configs_button(settings: dict) -> bool:
    return bool(
        settings.get("show_direct_config", True)
        or settings.get("show_sub_link", True)
        or settings.get("show_auto_sub_link", False)
        or settings.get("show_sub_link_b64", False)
        or settings.get("show_multi_server", False)
        or settings.get("show_multi_server_b64", False)
    )


def _get_location_servers() -> list:
    servers = database.get_servers() or []
    # سرورهایی که فقط نقش نود دارند، در لیست خرید مستقیم نمایش داده نشوند.
    child_server_ids: set[int] = set()
    for s in servers:
        for n in (s.get("nodes") or []):
            if not isinstance(n, dict):
                continue
            try:
                sid = int(n.get("target_server_id") or 0)
            except (TypeError, ValueError):
                sid = 0
            if sid > 0:
                child_server_ids.add(sid)
        # پرچم is_node/parent_server_id برای حالتی که nodes ناقص است
        try:
            sid_self = int(s.get("id") or 0)
        except (TypeError, ValueError):
            sid_self = 0
        if sid_self > 0 and s.get("is_node"):
            child_server_ids.add(sid_self)
        if sid_self > 0 and s.get("parent_server_id"):
            try:
                if int(s.get("parent_server_id") or 0) > 0:
                    child_server_ids.add(sid_self)
            except (TypeError, ValueError):
                pass

    servers = [s for s in servers if int(s.get("id") or 0) not in child_server_ids]
    settings = _get_subscription_settings()
    if settings.get("shuffle_server_layout", True):
        random.shuffle(servers)
    return servers













def _resolve_live_server_title(service: dict, default: str = "نامشخص") -> str:
    stored_title = str(service.get("server_title") or "").strip()
    try:
        server_id = int(service.get("server_id") or 0)
    except (TypeError, ValueError):
        server_id = 0

    if server_id > 0:
        try:
            server = database.get_server_by_id(server_id)
        except Exception:
            server = None
        if server:
            live_title = str(server.get("title") or "").strip()
            if live_title:
                return live_title
        if stored_title:
            return stored_title
        return f"سرور #{server_id}"

    return stored_title or default


def _build_subscription_status_text(service):
    service_name = service.get('name') or 'سرویس'
    server_title = _resolve_live_server_title(service, default='نامشخص')
    usage_current = _to_float(service.get('usage_current', 0))
    usage_limit = _to_float(service.get('usage_limit', 0))
    days_left = int(service.get('days_left') or 0)
    comment_meta = _parse_service_comment(service.get("comment") or "")
    service_id = comment_meta.get("code") or service.get('id') or "—"
    price_raw = comment_meta.get("price")
    if price_raw and str(price_raw).isdigit():
        price_toman = int(price_raw)
    else:
        try:
            price_toman = userbot_db.get_last_order_price_for_service(
                int(service.get("user_id") or 0),
                str(service_name),
            )
        except Exception:
            price_toman = None
    if price_toman is not None:
        try:
            shown_price = max(0, int(price_toman))
            if shown_price >= 1000:
                shown_price = (shown_price // 1000) * 1000
            price_toman = shown_price
        except Exception:
            price_toman = None

    unlimited_volume = usage_limit > 0 and _is_unlimited_volume(usage_limit)
    unlimited_time = _is_unlimited_time(days_left)

    if usage_limit > 0:
        usage_line = f"{usage_current:.1f} از {'نامحدود' if unlimited_volume else f'{usage_limit:.1f} گیگ'}"
    else:
        usage_line = f"{usage_current:.1f} گیگ"

    settings = _get_subscription_settings()
    lines = ["📄اطلاعات اشتراک شما", ""]
    if settings.get("show_username", True):
        lines.append(f"👤نام: {service_name}")
    lines.extend([
        f"📡سرور: {server_title}",
        f"📊میزان استفاده: {usage_line}",
        f"⏳زمان باقی مانده: {'نامحدود' if unlimited_time else f'{days_left} روز'}",
        f"💰قیمت اشتراک: {price_toman:,} تومان" if price_toman is not None else "💰قیمت اشتراک: نامشخص",
        f"🔑شناسه: `{service_id}`",
    ])
    return "\n".join(lines)






def _is_connected_service(service: dict) -> bool:
    meta = _parse_service_comment(service.get("comment") or "")
    return str(meta.get("linked") or "").strip() == "1" or str(meta.get("source") or "").strip() == "connect"


def _service_local_lock_reason(service: dict) -> Optional[str]:
    """
    Local lock state used to block config/subscription access immediately.
    """
    if not service:
        return "service_not_found"

    usage_current = _to_float(service.get("usage_current"), 0.0)
    usage_limit = _to_float(service.get("usage_limit"), 0.0)
    if usage_limit > 0 and (not _is_unlimited_volume(usage_limit)) and usage_current >= usage_limit:
        return "usage_limit_reached"

    try:
        days_left = int(service.get("days_left"))
    except Exception:
        days_left = None
    if days_left is not None and (not _is_unlimited_time(days_left)) and days_left < 0:
        return "time_expired"

    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0
    if service_id > 0:
        mappings = userbot_db.get_service_nodes(service_id)
        if mappings and not any(int((m or {}).get("is_active") or 0) == 1 for m in mappings):
            return "nodes_inactive"

    return None


def _service_local_lock_text(reason: Optional[str]) -> str:
    if reason == "usage_limit_reached":
        return "⛔ این اشتراک به سقف حجم رسیده و موقتاً غیرفعال است."
    if reason == "time_expired":
        return "⛔ زمان این اشتراک به پایان رسیده و موقتاً غیرفعال است."
    if reason == "nodes_inactive":
        return "⛔ این اشتراک در حال حاضر غیرفعال است."
    return "⛔ دسترسی این اشتراک موقتاً محدود شده است."


async def _resolve_service_access_lock(service: dict) -> tuple[dict, Optional[str]]:
    """
    قفل دسترسی مؤثر برای اکشن‌های کانفیگ/لینک.
    - usage/time: فوری و قطعی
    - nodes_inactive: قبل از بلاک، یک بار با پنل همگام‌سازی می‌شود
    """
    reason = _service_local_lock_reason(service)
    if reason != "nodes_inactive":
        return service, reason

    try:
        probe = await _service_probe_state(service)
    except Exception:
        return service, reason

    if probe != "exists":
        return service, reason

    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0
    if service_id > 0:
        try:
            userbot_db.set_service_nodes_active(service_id, 1)
        except Exception:
            pass

    refreshed = await _sync_service_runtime_from_panels(service)
    refreshed_reason = _service_local_lock_reason(refreshed)
    if refreshed_reason == "nodes_inactive":
        return refreshed, "nodes_inactive"
    return refreshed, refreshed_reason


async def _find_panel_user_targets_by_uuid(user_uuid: str) -> list[tuple[dict, dict]]:
    """
    جستجوی UUID روی همه سرورهای ثبت‌شده:
    خروجی: [(server, panel_user), ...]
    """
    out: list[tuple[dict, dict]] = []
    seen: set[int] = set()
    for srv in (database.get_servers() or []):
        try:
            sid = int(srv.get("id") or 0)
        except (TypeError, ValueError):
            sid = 0
        if sid <= 0 or sid in seen:
            continue
        seen.add(sid)
        try:
            pu = await hiddify_api.get_user_by_uuid(srv, user_uuid)
        except Exception:
            continue
        if isinstance(pu, dict) and pu:
            out.append((srv, pu))
    return out


def _service_is_renewable(service: dict) -> bool:
    """
    شرط مجاز بودن تمدید — در همه حالت‌ها اعمال می‌شود (حتی default/fair):
    - کمتر از renew_max_days روز تا اتمام اشتراک مانده باشد، یا
    - حجم باقی‌مانده کمتر از renew_max_remaining_gb گیگابایت باشد
    """
    br = _get_buy_renew_settings()
    max_days = int(br.get("renew_max_days") or 3)
    max_remaining_gb = int(br.get("renew_max_remaining_gb") or 3)

    # days_left نامشخص (NULL/غیرعددی) → اجازه تمدید داده نمی‌شود (fail-closed)
    raw_days = service.get("days_left")
    try:
        days_left = int(float(raw_days)) if raw_days is not None else None
    except (TypeError, ValueError):
        days_left = None
    days_ok = days_left is not None and days_left < max_days

    usage_limit = _to_float(service.get("usage_limit"), 0.0)
    usage_current = _to_float(service.get("usage_current"), 0.0)
    usage_ok = False
    if usage_limit > 0:
        remaining_gb = usage_limit - usage_current
        usage_ok = remaining_gb < max_remaining_gb

    # advanced
    return days_ok or usage_ok


async def _service_is_renewable_live(service: dict) -> bool:
    """بررسی مجاز بودن تمدید با داده لحظه‌ای پنل (سینک مصرف/روز قبل از چک)."""
    if not isinstance(service, dict):
        return False
    try:
        refreshed = await _sync_service_runtime_from_panels(service)
        if isinstance(refreshed, dict) and refreshed:
            service = refreshed
    except Exception:
        logger.warning("renew policy: live sync failed svc=%s; using DB values", service.get("id"))
    ok = _service_is_renewable(service)
    logger.info(
        "renew policy svc=%s days_left=%s usage=%s/%sGB → renewable=%s",
        service.get("id"), service.get("days_left"),
        service.get("usage_current"), service.get("usage_limit"), ok,
    )
    return ok


def _renew_not_allowed_text() -> str:
    br = _get_buy_renew_settings()
    max_days = int(br.get("renew_max_days") or 3)
    max_remaining_gb = int(br.get("renew_max_remaining_gb") or 3)
    return (
        "🛑 در حال حاضر شما امکان تمدید اشتراک خود را ندارید.\n"
        f"1- کمتر از {max_days} روز تا اتمام اشتراک شما باقی مانده باشد.\n"
        f"2- حجم باقی مانده اشتراک شما کمتر از {max_remaining_gb} گیگابایت باشد."
    )












def _get_service_panel_targets(service: dict) -> list[tuple[dict, str]]:
    targets: list[tuple[dict, str]] = []
    seen: set[tuple[int, str]] = set()
    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0

    mappings = userbot_db.get_service_nodes(service_id) if service_id > 0 else []
    for m in mappings:
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        srv = database.get_server_by_id(sid)
        if not srv:
            continue
        key = (sid, uuid)
        if key in seen:
            continue
        seen.add(key)
        targets.append((srv, uuid))

    if targets:
        return targets

    try:
        sid = int(service.get("server_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    uuid = _extract_uuid_from_comment(service.get("comment") or "")
    srv = database.get_server_by_id(sid) if sid > 0 else None
    if srv and uuid:
        targets.append((srv, uuid))
    return targets


async def _sync_service_runtime_from_panels(service: dict) -> dict:
    """
    سینک لحظه‌ای مصرف سرویس از همه نودها + سرور اصلی.
    """
    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0
    if service_id <= 0:
        return service

    targets = _get_service_panel_targets(service)
    if not targets:
        return service

    total_usage = 0.0
    min_days_left: Optional[int] = None
    latest_last_online: Optional[datetime] = None
    synced_usage_limit: Optional[float] = None
    fallback_usage_limit: Optional[float] = None
    found_any = False
    try:
        primary_server_id = int(service.get("server_id") or 0)
    except (TypeError, ValueError):
        primary_server_id = 0

    for srv, uuid in targets:
        try:
            panel_user = await hiddify_api.get_user_by_uuid(srv, uuid)
        except Exception:
            continue

        found_any = True
        total_usage += _to_float(panel_user.get("current_usage_GB"), 0.0)

        panel_limit = _usage_limit_from_panel_user(panel_user)
        if panel_limit is not None:
            try:
                sid = int(srv.get("id") or 0)
            except (TypeError, ValueError):
                sid = 0
            if primary_server_id > 0 and sid == primary_server_id:
                synced_usage_limit = panel_limit
            elif fallback_usage_limit is None:
                fallback_usage_limit = panel_limit

        days_left = _days_left_from_panel_user(panel_user)
        if days_left is not None:
            min_days_left = days_left if min_days_left is None else min(min_days_left, days_left)

        dt = _parse_panel_datetime(panel_user.get("last_online"))
        if dt and (latest_last_online is None or dt > latest_last_online):
            latest_last_online = dt

    if not found_any:
        return service

    if synced_usage_limit is None:
        synced_usage_limit = fallback_usage_limit

    userbot_db.update_service_runtime(
        service_id=service_id,
        usage_current=total_usage,
        usage_limit=synced_usage_limit,
        days_left=min_days_left,
        last_online=(latest_last_online.strftime("%Y-%m-%d %H:%M:%S") if latest_last_online else None),
    )
    refreshed = userbot_db.get_service_by_id(service_id)
    return refreshed or service


def _older_than_days(ts: str, days: int) -> bool:
    raw = str(ts or "").strip()
    if not raw:
        return False
    dt = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if not dt:
        return False
    return (datetime.now(timezone.utc).replace(tzinfo=None) - dt) >= timedelta(days=int(days))


async def _service_exists_on_panel(service: dict) -> bool:
    """
    سرویس را روی پنل(ها) چک می‌کند.
    - اگر واقعاً حذف شده باشد => False
    - اگر خطای ارتباطی/موقتی باشد => True (برای جلوگیری از حذف اشتباه)
    """
    try:
        mappings = userbot_db.get_service_nodes(int(service.get("id") or 0))
    except Exception:
        mappings = []

    targets: list[tuple[dict, str]] = []
    for m in mappings:
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        srv = database.get_server_by_id(sid)
        if not srv:
            continue
        targets.append((srv, uuid))

    if not targets:
        try:
            sid = int(service.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        srv = database.get_server_by_id(sid) if sid > 0 else None
        uuid = _extract_uuid_from_comment(service.get("comment") or "")
        if srv and uuid:
            targets.append((srv, uuid))

    if not targets:
        return False

    found_any = False
    missing_count = 0
    for srv, uuid in targets:
        try:
            await hiddify_api.get_user_by_uuid(srv, uuid)
            found_any = True
        except Exception as e:
            if _is_user_missing_error(e):
                missing_count += 1
            else:
                # خطای موقت شبکه/SSL/API: فعلاً سرویس نمایش داده نشود
                # ولی حذف نهایی با TTL انجام می‌شود.
                missing_count += 1

    if found_any:
        return True
    return missing_count < len(targets)


def _normalize_service_name_input(raw: str) -> str:
    text = str(raw or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _is_panel_unauthorized_error(exc: Exception) -> bool:
    t = str(exc or "").lower()
    return (
        "http 401" in t
        or "http 403" in t
        or "unauthorized" in t
        or "unathorized" in t
        or "forbidden" in t
    )


def _normalized_panel_identity(server: dict) -> tuple[str, str]:
    panel_url = str(server.get("panel_url") or "").strip().rstrip("/").lower()
    admin_proxy = str(server.get("admin_proxy_path") or "").strip().strip("/").lower()
    return panel_url, admin_proxy


def _find_auth_fallback_servers_for_panel(server: dict) -> list[dict]:
    """
    If one server record has wrong admin_uuid/api_key, try sibling records
    with same panel_url/admin_proxy_path and different credentials.
    """
    target_panel, target_proxy = _normalized_panel_identity(server)
    if not target_panel or not target_proxy:
        return []
    curr_id = int(server.get("id") or 0)
    curr_key = str(server.get("admin_uuid") or server.get("api_key") or "").strip()
    out: list[dict] = []
    seen_ids: set[int] = set()
    for s in (database.get_servers() or []):
        sid = int(s.get("id") or 0)
        if sid == curr_id or sid in seen_ids:
            continue
        p, a = _normalized_panel_identity(s)
        if p != target_panel or a != target_proxy:
            continue
        alt_key = str(s.get("admin_uuid") or s.get("api_key") or "").strip()
        if not alt_key or alt_key == curr_key:
            continue
        out.append(s)
        seen_ids.add(sid)
    return out


def _format_rename_panel_error(server: dict, exc: Exception) -> str:
    title = str(server.get("title") or f"server-{server.get('id') or '?'}")
    if _is_panel_unauthorized_error(exc):
        return f"{title}: دسترسی ادمین نامعتبر است (کلید Admin/API این سرور را بررسی کنید)."
    return f"{title}: خطا در بروزرسانی نام."


async def _rename_service_across_panels_and_db(service: dict, new_name: str) -> tuple[bool, str]:
    service_id = int(service.get("id") or 0)
    if service_id <= 0:
        return False, "❌ سرویس نامعتبر است."

    old_name = str(service.get("name") or "").strip()
    targets = _get_service_panel_targets(service)
    if not targets:
        return False, "❌ مسیرهای پنل این اشتراک یافت نشد."

    updated_targets: list[tuple[dict, str]] = []
    errors: list[str] = []

    for srv, uuid in targets:
        try:
            await multi_panel.patch_user(srv, uuid, {"name": new_name})
            updated_targets.append((srv, uuid))
        except Exception as e:
            # Retry with sibling server records (same panel/proxy, different admin key).
            patched = False
            if _is_panel_unauthorized_error(e):
                for alt_srv in _find_auth_fallback_servers_for_panel(srv):
                    try:
                        await multi_panel.patch_user(alt_srv, uuid, {"name": new_name})
                        updated_targets.append((alt_srv, uuid))
                        patched = True
                        break
                    except Exception:
                        continue
            if not patched:
                errors.append(_format_rename_panel_error(srv, e))

    # اگر حتی یک پنل fail شد، برای جلوگیری از ناسازگاری، پنل‌های موفق را rollback می‌کنیم.
    if errors:
        if updated_targets and old_name:
            for srv, uuid in updated_targets:
                try:
                    await hiddify_api.patch_user(srv, uuid, {"name": old_name})
                except Exception as re_err:
                    logger.warning(
                        "Failed rollback service name on panel (service_id=%s, server_id=%s, uuid=%s): %s",
                        service_id,
                        srv.get("id"),
                        uuid,
                        re_err,
                    )
        preview = "\n".join(errors[:3])
        extra = f"\n... و {len(errors) - 3} خطای دیگر" if len(errors) > 3 else ""
        return False, "❌ تغییر نام روی همه سرورها انجام نشد.\n" + preview + extra

    try:
        ok_db = userbot_db.update_service_name(service_id, new_name)
    except Exception as e:
        logger.exception("Failed updating service name in DB (service_id=%s): %s", service_id, e)
        return False, "❌ نام روی پنل بروزرسانی شد ولی ذخیره در دیتابیس خطا داد."

    if not ok_db:
        return False, "❌ بروزرسانی نام در دیتابیس انجام نشد."

    return True, "✅ نام اشتراک با موفقیت بروزرسانی شد."


async def _regenerate_service_uuid_for_service(service: dict) -> tuple[bool, str, Optional[str]]:
    service_id = int(service.get("id") or 0)
    if service_id <= 0:
        return False, "❌ سرویس نامعتبر است.", None

    current_uuid = _resolve_service_uuid_for_managed_sub_link(service_id, service=service)
    if not current_uuid:
        return False, "❌ UUID فعلی اشتراک تعیین نشده است.", None

    targets = _get_service_panel_targets(service)
    if not targets:
        return False, "❌ مسیرهای پنل این اشتراک پیدا نشد.", None

    desired_uuid = str(uuid4())
    final_uuid: Optional[str] = None
    updated_targets: list[tuple[dict, str, str]] = []

    for srv, old_uuid in targets:
        if not old_uuid:
            continue
        patch_data = {"uuid": desired_uuid}
        patched = None
        try:
            patched = await hiddify_api.patch_user(srv, old_uuid, patch_data)
        except Exception as e:
            if _is_panel_unauthorized_error(e):
                for alt_srv in _find_auth_fallback_servers_for_panel(srv):
                    try:
                        patched = await hiddify_api.patch_user(alt_srv, old_uuid, patch_data)
                        break
                    except Exception:
                        continue
        if not isinstance(patched, dict):
            # rollback any successful target modifications
            for srv2, old_uuid2, new_uuid2 in updated_targets:
                try:
                    await hiddify_api.patch_user(srv2, new_uuid2, {"uuid": old_uuid2})
                except Exception:
                    pass
            return False, "❌ بازسازی UUID روی همه سرورها انجام نشد. لطفاً مجدداً تلاش کنید یا با پشتیبانی تماس بگیرید.", None

        returned_uuid = str(patched.get("uuid") or patched.get("id") or "").strip()
        if not returned_uuid:
            returned_uuid = desired_uuid

        if final_uuid is None:
            final_uuid = returned_uuid
        elif returned_uuid != final_uuid:
            for srv2, old_uuid2, new_uuid2 in updated_targets:
                try:
                    await hiddify_api.patch_user(srv2, new_uuid2, {"uuid": old_uuid2})
                except Exception:
                    pass
            return False, "❌ UUID جدید روی همه سرورها همگن نشد. لطفاً مجدداً تلاش کنید یا با پشتیبانی تماس بگیرید.", None

        updated_targets.append((srv, old_uuid, returned_uuid))

    if not final_uuid:
        return False, "❌ UUID جدید تهیه نشد.", None

    # Persist new UUID in local DB mappings and service comment.
    comment = str(service.get("comment") or "").strip()
    comment_parts = [p.strip() for p in comment.split("|") if p.strip()]
    kept_parts = [p for p in comment_parts if not p.split(":", 1)[0].strip().lower() == "uuid"]
    kept_parts.insert(0, f"uuid:{final_uuid}")
    new_comment = "|".join(kept_parts)

    had_node_mappings = bool(userbot_db.get_service_nodes(service_id))
    comment_saved = False
    node_updates = 0
    try:
        comment_saved = bool(userbot_db.update_service_comment(service_id, new_comment))
    except Exception as e:
        logger.warning("Failed updating regenerated service UUID in comment (service_id=%s): %s", service_id, e)

    for srv, old_uuid, new_uuid in updated_targets:
        try:
            node_updates += int(userbot_db.update_service_node_uuid(service_id, int(srv.get("id") or 0), old_uuid, new_uuid) or 0)
        except Exception as e:
            logger.warning(
                "Failed updating regenerated service UUID in node mapping (service_id=%s, server_id=%s): %s",
                service_id,
                srv.get("id"),
                e,
            )

    if (had_node_mappings and node_updates < len(updated_targets)) or (not had_node_mappings and not comment_saved):
        for srv, old_uuid, new_uuid in updated_targets:
            try:
                await hiddify_api.patch_user(srv, new_uuid, {"uuid": old_uuid})
            except Exception:
                pass
        return False, "❌ لینک در پنل تغییر کرد اما ذخیره در دیتابیس کامل نشد. تغییرات پنل تا حد امکان برگردانده شد؛ لطفاً دوباره تلاش کنید.", None

    return True, (
        "✅ لینک اشتراک با موفقیت تغییر کرد.\n"
        "لینک و کانفیگ قبلی از کار می‌افتد؛ لطفاً لینک جدید را دوباره دریافت و در برنامه وارد کنید."
    ), final_uuid


async def _service_probe_state(service: dict) -> str:
    """
    وضعیت سرویس روی پنل(ها):
    - exists: حداقل روی یک تارگت پیدا شد
    - missing: روی همه تارگت‌ها «یافت نشد/404» بود
    - unreachable: خطای ارتباطی/موقتی (TLS/timeout/...) غالب است
    """
    try:
        mappings = userbot_db.get_service_nodes(int(service.get("id") or 0))
    except Exception:
        mappings = []

    targets: list[tuple[dict, str]] = []
    for m in mappings:
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        srv = database.get_server_by_id(sid)
        if not srv:
            continue
        targets.append((srv, uuid))

    if not targets:
        try:
            sid = int(service.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        srv = database.get_server_by_id(sid) if sid > 0 else None
        uuid = _extract_uuid_from_comment(service.get("comment") or "")
        if srv and uuid:
            targets.append((srv, uuid))

    if not targets:
        return "missing"

    found_any = False
    missing_count = 0
    unreachable_count = 0
    for srv, uuid in targets:
        try:
            await hiddify_api.get_user_by_uuid(srv, uuid)
            found_any = True
        except Exception as e:
            if _is_user_missing_error(e):
                missing_count += 1
            else:
                unreachable_count += 1

    if found_any:
        return "exists"
    if missing_count == len(targets):
        return "missing"
    # اگر حتی یک خطای ارتباطی باشد و چیزی پیدا نشود، وضعیت را موقتاً unreachable می‌گیریم.
    if unreachable_count > 0:
        return "unreachable"
    return "missing"


async def _filter_existing_services(services: list[dict]) -> tuple[list[dict], int]:
    """
    فقط سرویس‌های موجود روی پنل را نگه می‌دارد.
    سرویس‌های حذف‌شده از پنل را از دیتابیس محلی هم پاک می‌کند.
    """
    visible: list[dict] = []
    removed_count = 0

    async def _probe_item(svc: dict) -> tuple[dict, str]:
        return svc, await _service_probe_state(svc)

    probed = await _gather_with_limit(
        services,
        _probe_item,
        limit=USERBOT_STATUS_PROBE_CONCURRENCY,
    )
    for s, state in probed:
        sid = int(s.get("id") or 0)
        if state == "exists":
            try:
                userbot_db.mark_service_seen(sid)
            except Exception:
                pass
            visible.append(s)
            continue
        if state == "unreachable":
            # در قطعی موقت، سرویس موقتاً مخفی شود ولی streak حذف افزایش پیدا نکند.
            continue

        missing_info = {"missing_streak": 1, "first_missing_at": "", "last_missing_at": ""}
        try:
            missing_info = userbot_db.mark_service_missing(sid)
        except Exception:
            missing_info = {"missing_streak": 1, "first_missing_at": "", "last_missing_at": ""}

        # سرویس فعلاً مخفی بماند، اما حذف نهایی فقط بعد از TTL تنظیم‌شده انجام شود.
        if not _older_than_days(
            str(missing_info.get("first_missing_at") or ""),
            USERBOT_MISSING_SERVICE_DELETE_DAYS,
        ):
            continue
        try:
            userbot_db.delete_service(sid)
            removed_count += 1
        except Exception as e:
            logger.warning("Failed deleting stale service id=%s: %s", s.get("id"), e)
    return visible, removed_count


def _get_target_servers_for_sale(primary_server: dict) -> list[dict]:
    """
    سرور اصلی + نودهای متصل به آن (اگر target_server_id داشته باشند).
    """
    targets: list[dict] = []
    seen: set[int] = set()

    sid = int(primary_server.get("id") or 0)
    if sid > 0:
        targets.append(primary_server)
        seen.add(sid)

    for node in (primary_server.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        try:
            target_sid = int(node.get("target_server_id") or 0)
        except (TypeError, ValueError):
            target_sid = 0
        if target_sid <= 0 or target_sid in seen:
            continue
        srv = database.get_server_by_id(target_sid)
        if not srv:
            continue
        targets.append(srv)
        seen.add(target_sid)

    return targets


async def _create_service_users_on_targets(
    targets: list[dict],
    payload: dict,
) -> tuple[dict, list[dict]]:
    """
    روی همه سرورهای هدف کاربر می‌سازد.
    خروجی:
      - created_primary: خروجی create_user سرور اول
      - created_nodes: لیست ساخت‌شده‌ها با server_id/title/uuid/id/created
    """
    created_nodes: list[dict] = []
    # Force one shared UUID across main server + all nodes
    # so duplicate names never break multi-node mapping logic.
    shared_uuid = str((payload or {}).get("uuid") or "").strip() or str(uuid4())
    payload_base = dict(payload or {})
    payload_base["uuid"] = shared_uuid
    try:
        for idx, srv in enumerate(targets):
            try:
                created = await multi_panel.create_user(srv, payload_base)
            except Exception as e:
                if idx == 0:
                    raise
                logger.warning("Node create_user failed server=%s: %s", srv.get("id"), e)
                continue
            user_uuid = str(created.get("uuid") or created.get("id") or "").strip()
            if not user_uuid:
                if idx == 0:
                    raise RuntimeError("uuid کاربر ساخته‌شده از پنل دریافت نشد.")
                continue
            if user_uuid != shared_uuid:
                logger.warning(
                    "Panel returned a different uuid than requested (server_id=%s requested=%s returned=%s)",
                    srv.get("id"),
                    shared_uuid,
                    user_uuid,
                )
            created_nodes.append(
                {
                    "server_id": int(srv.get("id") or 0),
                    "server_title": srv.get("title") or f"سرور #{srv.get('id')}",
                    "panel_user_uuid": user_uuid,
                    "panel_user_id": created.get("id"),
                    "created": created,
                    "is_primary": idx == 0,
                }
            )
    except Exception:
        if created_nodes:
            await _deactivate_created_users(created_nodes)
        raise
    return created_nodes[0]["created"], created_nodes


async def _deactivate_created_users(created_nodes: list[dict]) -> None:
    for item in created_nodes:
        try:
            sid = int(item.get("server_id") or 0)
            uuid = str(item.get("panel_user_uuid") or "").strip()
            if sid <= 0 or not uuid:
                continue
            server = database.get_server_by_id(sid)
            if not server:
                continue
            await hiddify_api.disable_user(server, uuid)
        except Exception as e:
            logger.warning(
                "Rollback deactivate failed for sid=%s uuid=%s: %s",
                item.get("server_id"),
                item.get("panel_user_uuid"),
                e,
            )


def _get_service_targets_for_renew(service: dict) -> list[tuple[dict, str]]:
    """
    تارگت‌های تمدید سرویس:
    - همه نودهای ثبت‌شده در service_nodes
    - fallback: سرور اصلی + uuid کامنت
    """
    targets: list[tuple[dict, str]] = []
    seen: set[tuple[int, str]] = set()

    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0

    mappings = userbot_db.get_service_nodes(service_id) if service_id > 0 else []
    for m in mappings:
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        srv = database.get_server_by_id(sid)
        if not srv:
            continue
        key = (sid, uuid)
        if key in seen:
            continue
        seen.add(key)
        targets.append((srv, uuid))

    if targets:
        return targets

    try:
        sid = int(service.get("server_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    uuid = _extract_uuid_from_comment(service.get("comment") or "")
    srv = database.get_server_by_id(sid) if sid > 0 else None
    if srv and uuid:
        targets.append((srv, uuid))
    return targets


def _build_renew_patch_payload(service: dict, *, package_gb: float, package_days: int) -> tuple[dict, float, int]:
    """
    خروجی:
      payload برای PATCH
      usage_limit نهایی
      days_left نهایی
    """
    br = _get_buy_renew_settings()
    policy = str(br.get("renew_policy") or "advanced").strip().lower()
    volume_mode = str(br.get("renew_volume_mode") or "").strip().lower()
    time_mode = str(br.get("renew_time_mode") or "").strip().lower()
    if volume_mode not in {"add", "reset"}:
        volume_mode = "add" if policy in {"default", "fair"} else "reset"
    if time_mode not in {"add", "reset"}:
        time_mode = "add" if policy == "fair" else "reset"

    usage_current = _to_float(service.get("usage_current"), 0.0)
    usage_limit_old = _to_float(service.get("usage_limit"), 0.0)
    days_left_old = int(service.get("days_left") or 0)

    remaining_gb = 0.0
    if usage_limit_old > 0:
        remaining_gb = max(usage_limit_old - usage_current, 0.0)
    remaining_days = max(days_left_old, 0)

    final_limit = float(package_gb + remaining_gb) if volume_mode == "add" else float(package_gb)
    final_days = int(package_days + remaining_days) if time_mode == "add" else int(package_days)

    now_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
    now_dt = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "usage_limit_GB": float(final_limit),
        "package_days": int(final_days),
        "start_date": now_date,
        "current_usage_GB": 0,
        "last_reset_time": now_dt,
        "is_active": True,
    }
    return payload, final_limit, final_days


async def _apply_service_renewal_on_targets(
    service: dict,
    *,
    user_id: int,
    service_name: str,
    package_gb: float,
    package_days: int,
) -> tuple[float, int, Any, list[str]]:
    """
    تمدید سرویس روی سرور اصلی + نودها بر اساس uuid مشترک.
    خروجی: (usage_limit, days_left, last_online, failed_servers)
      - failed_servers: لیست عنوان‌های سرور/نودهایی که در دسترس نبودند
    """
    targets = _get_service_targets_for_renew(service)
    if not targets:
        raise RuntimeError("شناسه UUID سرویس برای تمدید پیدا نشد.")

    payload, final_limit, final_days = _build_renew_patch_payload(
        service,
        package_gb=package_gb,
        package_days=package_days,
    )
    payload["name"] = service_name
    payload["comment"] = _build_panel_user_comment(user_id, is_test=False)

    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0

    last_online = None
    ok_count = 0
    errors_primary: list[str] = []
    failed_servers: list[str] = []
    for srv, uuid in targets:
        try:
            patched = await multi_panel.patch_user(srv, uuid, payload)
            await multi_panel.enable_user(srv, uuid)
        except Exception as e:
            # یک نود down نباید مانع تحویل به بقیه شود؛ فقط لاگ و ادامه بده.
            logger.warning(
                "Renewal skipped for server_id=%s (uuid=%s) due to error: %s",
                srv.get("id"),
                uuid,
                e,
            )
            errors_primary.append(str(e))
            failed_servers.append(str(srv.get("title") or f"سرور #{srv.get('id')}"))
            continue
        ok_count += 1
        if last_online is None:
            last_online = patched.get("last_online")

    # اگر هیچ نودی موفق نشد، یعنی کل شبکه سرورها از دسترس خارج است -> fail واقعی.
    if ok_count == 0:
        raise RuntimeError("تمدید سرویس روی هیچ سرور/نودی انجام نشد: " + (" | ".join(errors_primary[:3]) or "all servers unreachable"))

    if ok_count < len(targets):
        logger.warning(
            "Renewal partially applied (service_id=%s): %s/%s targets succeeded.",
            service_id,
            ok_count,
            len(targets),
        )

    if service_id > 0:
        try:
            userbot_db.set_service_nodes_active(service_id, 1)
            # ریست کامل حسابداری نودها: حذف رکوردهای نودِ حذف‌شده (شبح) و صفر/یخ‌زدایی
            # بقیه نودها (شروع دورهٔ جدید اشتراک).
            userbot_db.reset_service_nodes_on_renew(service_id)
        except Exception as e:
            logger.warning("Failed to re-enable/reset service_nodes after renewal (service_id=%s): %s", service_id, e)
    return final_limit, final_days, last_online, failed_servers


def _build_user_base_url(server: dict, user_uuid: str) -> Optional[str]:
    if not user_uuid:
        return None
    try:
        from Shared import xui_api
        if xui_api.is_xui_server(server):
            origin = xui_api._public_origin(server)
            sub_path = xui_api._sub_path(server)
            if origin and sub_path:
                return f"{origin.rstrip('/')}{sub_path}{user_uuid}"
    except Exception:
        pass
    panel_url = (server.get("panel_url") or "").rstrip("/")
    user_proxy = (server.get("user_proxy_path") or "").strip("/")
    if not panel_url or not user_proxy or not user_uuid:
        return None

    base_url = f"{panel_url}/{user_proxy}/{user_uuid}"
    domains = server.get("domains") or []
    if not domains:
        return base_url

    best_score = -10**9
    display_domain = ""
    for d in domains:
        if isinstance(d, dict):
            raw_domain = (d.get("domain") or d.get("host") or d.get("url") or "").strip()
            title = (d.get("title") or d.get("name") or "").strip().lower()
        else:
            raw_domain = str(d).strip()
            title = ""
        if not raw_domain:
            continue
        low = raw_domain.lower()
        score = 0
        if "user." in low or low.startswith("user"):
            score += 50
        if "sub" in low:
            score += 15
        if "ساب" in title or "user" in title:
            score += 10
        if "dl." in low or low.startswith("dl"):
            score -= 10
        if score > best_score:
            best_score = score
            display_domain = raw_domain

    if not display_domain:
        return base_url

    if not (display_domain.startswith("http://") or display_domain.startswith("https://")):
        display_domain = "https://" + display_domain

    return base_url.replace(panel_url, display_domain.rstrip("/"), 1)


def _build_panel_base_url(server: dict, user_uuid: str) -> Optional[str]:
    if not user_uuid:
        return None
    try:
        from Shared import xui_api
        if xui_api.is_xui_server(server):
            origin = xui_api._public_origin(server)
            sub_path = xui_api._sub_path(server)
            if origin and sub_path:
                return f"{origin.rstrip('/')}{sub_path}{user_uuid}"
    except Exception:
        pass
    panel_url = (server.get("panel_url") or "").rstrip("/")
    user_proxy = (server.get("user_proxy_path") or "").strip("/")
    if not panel_url or not user_proxy or not user_uuid:
        return None
    return f"{panel_url}/{user_proxy}/{user_uuid}"


def _get_service_node_fetch_base_urls(service: dict) -> list[str]:
    """
    Base URLs for fetching direct configs.
    Tries both display domain (user.*) and raw panel domain (dl.*).
    """
    out: list[str] = []
    seen: set[str] = set()

    service_id = int(service.get("id") or 0)
    mappings = userbot_db.get_service_nodes(service_id) if service_id > 0 else []
    if mappings:
        active_mappings = [m for m in mappings if int((m or {}).get("is_active") or 0) == 1]
        if active_mappings:
            mappings = active_mappings
        else:
            return []
    primary_server_id = int(service.get("server_id") or 0)
    mappings = sorted(
        mappings,
        key=lambda m: int((m or {}).get("server_id") or 0) == primary_server_id,
    )

    for m in mappings:
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        server = database.get_server_by_id(sid)
        if not server:
            continue
        for base in (_build_user_base_url(server, uuid), _build_panel_base_url(server, uuid)):
            if not base or base in seen:
                continue
            out.append(base)
            seen.add(base)

    if not out:
        sid = int(service.get("server_id") or 0)
        server = database.get_server_by_id(sid) if sid else None
        uuid = _extract_uuid_from_comment(service.get("comment") or "")
        if server and uuid:
            for base in (_build_user_base_url(server, uuid), _build_panel_base_url(server, uuid)):
                if not base or base in seen:
                    continue
                out.append(base)
                seen.add(base)
    return out


def _get_service_node_base_urls(service: dict) -> list[str]:
    """
    آدرس base_url سرویس روی همه نودهای نگاشت‌شده.
    """
    out: list[str] = []
    seen: set[str] = set()

    service_id = int(service.get("id") or 0)
    mappings = userbot_db.get_service_nodes(service_id) if service_id > 0 else []
    if mappings:
        active_mappings = [m for m in mappings if int((m or {}).get("is_active") or 0) == 1]
        if active_mappings:
            mappings = active_mappings
        else:
            return []
    primary_server_id = int(service.get("server_id") or 0)
    # اول نودها، بعد سرور اصلی
    mappings = sorted(
        mappings,
        key=lambda m: int((m or {}).get("server_id") or 0) == primary_server_id,
    )

    for m in mappings:
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        server = database.get_server_by_id(sid)
        if not server:
            continue
        base = _build_user_base_url(server, uuid)
        if not base:
            continue
        if base not in seen:
            out.append(base)
            seen.add(base)

    if not out:
        sid = int(service.get("server_id") or 0)
        server = database.get_server_by_id(sid) if sid else None
        uuid = _extract_uuid_from_comment(service.get("comment") or "")
        if server and uuid:
            base = _build_user_base_url(server, uuid)
            if base:
                out.append(base)
    return out


def _sanitize_config_text(value: Any) -> str:
    text = str(value or "").strip()
    # Remove hidden directional/BOM chars that break protocol detection.
    for ch in ("\ufeff", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c"):
        text = text.replace(ch, "")
    return text.strip()


def _extract_proto_and_link(value: Any) -> tuple[str, str]:
    text = _sanitize_config_text(value)
    if not text:
        return "", ""

    candidates: list[str] = []
    seen: set[str] = set()

    def _push(s: Any) -> None:
        t = _sanitize_config_text(s)
        if not t or t in seen:
            return
        seen.add(t)
        candidates.append(t)

    _push(text)
    _push(unquote(text))
    _push(unquote(unquote(text)))

    # Support wrapped links such as hiddify://... ?url=<encoded-config>
    for t in list(candidates):
        try:
            parsed = urlparse(t)
            q = parse_qs(parsed.query or "")
        except Exception:
            q = {}
        for key in ("url", "config", "link", "uri", "sub"):
            for val in q.get(key, []):
                _push(val)
                _push(unquote(val))
                _push(unquote(unquote(val)))

    for cand in candidates:
        low = cand.lower()
        for proto in ("vless", "vmess", "trojan"):
            if low.startswith(f"{proto}://"):
                return proto, cand.rstrip("'\",;)]}")
        m = re.search(r"(?i)\b(vless|vmess|trojan)://\S+", cand)
        if not m:
            continue
        link = _sanitize_config_text(m.group(0)).rstrip("'\",;)]}")
        return str(m.group(1)).lower(), link
    return "", ""


def _extract_all_config_links(value: Any) -> list[str]:
    """
    Extract any config-like URI from text (not only vless/vmess/trojan).
    Source can be raw line, decoded line, or wrapped URL query params.
    """
    text = _sanitize_config_text(value)
    if not text:
        return []

    candidates: list[str] = []
    seen_candidates: set[str] = set()

    def _push_candidate(s: Any) -> None:
        t = _sanitize_config_text(s)
        if not t or t in seen_candidates:
            return
        seen_candidates.add(t)
        candidates.append(t)

    _push_candidate(text)
    _push_candidate(unquote(text))
    _push_candidate(unquote(unquote(text)))

    for t in list(candidates):
        try:
            parsed = urlparse(t)
            q = parse_qs(parsed.query or "")
        except Exception:
            q = {}
        for key in ("url", "config", "link", "uri", "sub"):
            for val in q.get(key, []):
                _push_candidate(val)
                _push_candidate(unquote(val))
                _push_candidate(unquote(unquote(val)))

    uri_re = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]{1,24})://[^\s<>\"']+")
    blocked_schemes = {"http", "https"}

    out: list[str] = []
    seen_links: set[str] = set()
    for cand in candidates:
        for m in uri_re.finditer(cand):
            link = _sanitize_config_text(m.group(0)).rstrip("'\",;)]}")
            if not link:
                continue
            scheme = str(m.group(1) or "").lower()
            if not scheme or scheme in blocked_schemes:
                continue
            if link in seen_links:
                continue
            seen_links.add(link)
            out.append(link)
    return out


def _extract_config_link_from_line(value: Any) -> str:
    """
    Strict parser:
    - only accepts full-line URI
    - no substring extraction
    - blocks non-config schemes
    """
    raw = _sanitize_config_text(value)
    if not raw:
        return ""

    raw = raw.rstrip("'\",;)]}")
    if "://" not in raw:
        return ""
    if re.search(r"\s", raw):
        return ""

    m = re.match(r"(?i)^([a-z][a-z0-9+.\-]{1,24})://", raw)
    if not m:
        return ""

    scheme = str(m.group(1) or "").lower()
    blocked = {"http", "https", "hiddify", "ftp", "file", "mailto", "tg"}
    if scheme in blocked:
        return ""
    if len(raw) < 16:
        return ""
    return raw


def _collect_all_direct_configs_for_service(service: dict) -> list[str]:
    """
    Strict source: only all.txt outputs from service/node fetch bases.
    Returns unique config links in discovered order.
    """
    out: list[str] = []
    seen_links: set[str] = set()

    # Use only user-facing subscription domains to stay aligned with all.txt shown to user.
    # X-UI detection for this service
    _is_xui_service = False
    try:
        from Shared import xui_api as _xui_dc
        sid_tmp = int(service.get("server_id") or 0)
        srv_tmp = database.get_server_by_id(sid_tmp) if sid_tmp else None
        if srv_tmp and _xui_dc.is_xui_server(srv_tmp):
            _is_xui_service = True
        else:
            for m in (userbot_db.get_service_nodes(int(service.get("id") or 0)) if service.get("id") else []):
                s = database.get_server_by_id(int(m.get("server_id") or 0))
                if s and _xui_dc.is_xui_server(s):
                    _is_xui_service = True
                    break
    except Exception:
        pass
    for base_url in _get_service_node_base_urls(service):
        seen_lines: set[str] = set()
        if _is_xui_service:
            suffixes = ("", "?base64=1")
        else:
            suffixes = ("all.txt", "all.txt?base64=1")
        for suffix in suffixes:
            if _is_xui_service:
                fetch_url = base_url if not suffix else f"{base_url}{suffix}"
            else:
                fetch_url = f"{base_url}/{suffix}"
            lines = _fetch_remote_lines(fetch_url)
            for ln in lines:
                raw = _sanitize_config_text(ln)
                if not raw or raw in seen_lines:
                    continue
                seen_lines.add(raw)
                link = _extract_config_link_from_line(raw)
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                out.append(link)
    return out


async def _collect_all_direct_configs_from_api_for_service(service: dict) -> list[str]:
    """
    Fallback source when subscription endpoints (all.txt) are blocked (e.g. HTTP 400/403).
    """
    out: list[str] = []
    seen: set[str] = set()
    try:
        mapped = await _collect_direct_configs_map_from_api(
            service,
            protocols=("vless", "vmess", "trojan"),
        )
    except Exception as e:
        logger.warning("API fallback for direct configs failed: %s", e)
        return out

    for proto in ("vless", "vmess", "trojan"):
        for link in (mapped.get(proto) or []):
            raw = _sanitize_config_text(link)
            if not raw or raw in seen:
                continue
            seen.add(raw)
            out.append(raw)
    return out


async def _send_service_direct_configs_shell(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    service_id: int,
    service: dict,
) -> None:
    service, lock_reason = await _resolve_service_access_lock(service)
    if lock_reason:
        await context.bot.send_message(
            chat_id=user_id,
            text=_service_local_lock_text(lock_reason),
            reply_markup=_main_menu_keyboard(),
        )
        return

    # جمع‌کردن کانفیگ‌ها (شامل urlopen بلاک‌کننده) در thread جدا — event loop فریز نشود
    links = await asyncio.to_thread(_collect_all_direct_configs_for_service, service)
    source_hint = ""
    allow_api_fallback = str(os.getenv("DIRECT_CONFIG_API_FALLBACK", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    # X-UI: subscription fetch via base_url may need special handling; always allow API fallback
    if not links:
        try:
            from Shared import xui_api as _xui_fallback_check
            sid_tmp = int(service.get("server_id") or 0)
            srv_tmp = database.get_server_by_id(sid_tmp) if sid_tmp else None
            if srv_tmp and _xui_fallback_check.is_xui_server(srv_tmp):
                allow_api_fallback = True
            else:
                for m in (userbot_db.get_service_nodes(int(service.get("id") or 0)) if service.get("id") else []):
                    s = database.get_server_by_id(int(m.get("server_id") or 0))
                    if s and _xui_fallback_check.is_xui_server(s):
                        allow_api_fallback = True
                        break
        except Exception:
            pass
    if not links and allow_api_fallback:
        links = await _collect_all_direct_configs_from_api_for_service(service)
        if links:
            source_hint = (
                "⚠️ دریافت مستقیم از لینک اشتراک محدود بود؛ "
                "کانفیگ‌ها از API پنل خوانده شد.\n\n"
            )
    base_urls = _get_service_node_base_urls(service)
    fallback_base = base_urls[0] if base_urls else ""

    if not links:
        msg = "❌ کانفیگی از لینک اشتراک استخراج نشد."
        if fallback_base:
            msg = (
                f"{msg}\n"
                "می‌توانید از لینک اشتراک استفاده کنید:\n"
                f"{fallback_base}/all.txt"
            )
        await context.bot.send_message(
            chat_id=user_id,
            text=msg,
            disable_web_page_preview=True,
        )
        return

    server_title = _resolve_live_server_title(service, default="")
    _dlg = _user_lang(user_id)
    header = i18n.t("direct_configs_title", _dlg)
    if server_title:
        header = f"{header} | {server_title}"
    clean_links = [str(x).strip() for x in links if str(x).strip()]

    # Try single message first (best UX for one-shot copy).
    all_links_text = "\n".join(clean_links)
    one_block_text = (
        f"{source_hint}{header}\n"
        + i18n.t("direct_configs_copy_hint", _dlg) + "\n"
        f"<pre><code class=\"language-shell\">{escape(all_links_text)}</code></pre>"
    )
    if len(one_block_text) <= 3900:
        await context.bot.send_message(
            chat_id=user_id,
            text=one_block_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    # Fallback: split into multiple shell messages (no txt file).
    max_payload = 2800
    parts: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0
    for link in clean_links:
        add = len(link) + 1
        if cur and (cur_len + add > max_payload):
            parts.append(cur)
            cur = [link]
            cur_len = add
        else:
            cur.append(link)
            cur_len += add
    if cur:
        parts.append(cur)

    for idx, chunk in enumerate(parts, start=1):
        part_header = header if len(parts) == 1 else f"{header} ({idx}/{len(parts)})"
        part_text = (
            f"{source_hint if idx == 1 else ''}{part_header}\n"
            + i18n.t("direct_configs_copy_hint_paged", _dlg) + "\n"
            f"<pre><code class=\"language-shell\">{escape(chr(10).join(chunk))}</code></pre>"
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=part_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def _iter_text_values(obj: Any):
    if obj is None:
        return
    if isinstance(obj, str):
        txt = _sanitize_config_text(obj)
        if txt:
            yield txt
        return
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_text_values(v)
        return
    if isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _iter_text_values(v)
        return


def _fetch_remote_lines(url: str) -> list[str]:
    raw_url = str(url or "").strip()
    if not raw_url:
        return []

    def _decode_to_lines(body_text: str) -> list[str]:
        lines = [_sanitize_config_text(ln) for ln in body_text.splitlines() if _sanitize_config_text(ln)]
        if lines and any("://" in ln for ln in lines):
            return lines

        compact = re.sub(r"\s+", "", body_text or "")
        candidates = [body_text.strip(), compact]
        for cand in candidates:
            cand = str(cand or "").strip()
            if not cand:
                continue
            for decoder in (base64.b64decode, base64.urlsafe_b64decode):
                try:
                    padded = cand + ("=" * ((4 - len(cand) % 4) % 4))
                    decoded = decoder(padded).decode("utf-8", errors="ignore")
                    decoded_lines = [_sanitize_config_text(ln) for ln in decoded.splitlines() if _sanitize_config_text(ln)]
                    if decoded_lines:
                        return decoded_lines
                except Exception:
                    continue
        return lines

    urls_to_try = [raw_url]
    low = raw_url.lower()
    if "/all.txt" in low and "asn=" not in low:
        sep = "&" if "?" in raw_url else "?"
        urls_to_try.append(f"{raw_url}{sep}asn=unknown")

    user_agents = [
        "HiddifyNext/1.0",
        "ClashMetaForAndroid/2.11.5",
        "v2rayN/6.45",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]

    base_headers = {
        "Accept": "text/plain,*/*;q=0.9",
        "Accept-Language": "en-US,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    errors: list[str] = []
    seen_errors: set[str] = set()

    for target in urls_to_try:
        for ua in user_agents:
            try:
                req = Request(target, headers={**base_headers, "User-Agent": ua})
                with urlopen(req, timeout=15) as resp:
                    raw = resp.read()
                body = raw.decode("utf-8", errors="ignore")
                parsed = _decode_to_lines(body)
                if parsed:
                    return parsed
            except HTTPError as e:
                body_snippet = ""
                try:
                    body_snippet = (e.read() or b"").decode("utf-8", errors="ignore").strip()
                except Exception:
                    body_snippet = ""
                msg = f"{target} -> HTTP {getattr(e, 'code', '?')}: {(body_snippet or str(e))[:220]}"
                if msg not in seen_errors:
                    seen_errors.add(msg)
                    errors.append(msg)
            except Exception as e:
                msg = f"{target} -> {str(e)[:220]}"
                if msg not in seen_errors:
                    seen_errors.add(msg)
                    errors.append(msg)

    if errors:
        logger.warning(
            "Failed to fetch remote lines from %s. tries=%s, first_errors=%s",
            raw_url,
            len(urls_to_try) * len(user_agents),
            " | ".join(errors[:2]),
        )
    else:
        logger.warning("Failed to fetch remote lines from %s: unknown error", raw_url)
    return []


def _collect_direct_configs(base_url: str, proto: str) -> list[str]:
    proto = (proto or "").strip().lower()
    if not proto:
        return []
    return _collect_direct_configs_map(base_url, protocols=(proto,)).get(proto, [])


def _collect_direct_configs_map(
    base_url: str,
    protocols: tuple[str, ...] = ("vless", "vmess", "trojan"),
) -> dict[str, list[str]]:
    allowed = tuple((p or "").strip().lower() for p in protocols if (p or "").strip())
    result: dict[str, list[str]] = {proto: [] for proto in allowed}
    if not result:
        return result

    lines = _fetch_remote_lines(f"{base_url}/all.txt")
    if not lines:
        lines = _fetch_remote_lines(f"{base_url}/sub/?format=base64")

    for ln in lines:
        low = ln.lower()
        for proto in result:
            if low.startswith(f"{proto}://"):
                result[proto].append(ln)
                break

    # Fallback endpoint per protocol for entries that are still empty.
    for proto in result:
        if result[proto]:
            continue
        fallback_lines = _fetch_remote_lines(f"{base_url}/{proto}.txt")
        if fallback_lines:
            result[proto] = [ln for ln in fallback_lines if ln.lower().startswith(f"{proto}://")]
    return result


async def _collect_direct_configs_map_from_api(
    service: dict,
    protocols: tuple[str, ...] = ("vless", "vmess", "trojan"),
) -> dict[str, list[str]]:
    allowed = tuple((p or "").strip().lower() for p in protocols if (p or "").strip())
    result: dict[str, list[str]] = {proto: [] for proto in allowed}
    seen: dict[str, set[str]] = {proto: set() for proto in allowed}
    if not result:
        return result

    targets: list[tuple[dict, str]] = []
    seen_targets: set[tuple[int, str]] = set()

    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0

    mappings = userbot_db.get_service_nodes(service_id) if service_id > 0 else []
    for m in mappings:
        try:
            sid = int((m or {}).get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str((m or {}).get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        server = database.get_server_by_id(sid)
        if not server:
            continue
        key = (sid, uuid)
        if key in seen_targets:
            continue
        seen_targets.add(key)
        targets.append((server, uuid))

    if not targets:
        try:
            sid = int(service.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        server = database.get_server_by_id(sid) if sid > 0 else None
        uuid = _extract_uuid_from_comment(service.get("comment") or "")
        if server and uuid:
            targets.append((server, uuid))

    for server, uuid in targets:
        try:
            configs_raw = await multi_panel.get_user_configs(server, uuid)
        except Exception:
            continue

        if isinstance(configs_raw, dict):
            configs = (
                configs_raw.get("configs")
                or configs_raw.get("items")
                or configs_raw.get("all_configs")
                or configs_raw.get("results")
                or configs_raw.get("data")
                or []
            )
        elif isinstance(configs_raw, list):
            configs = configs_raw
        else:
            configs = []

        for item in configs:
            candidates: list[Any] = []
            preferred_proto = ""
            if isinstance(item, dict):
                preferred_proto = _sanitize_config_text(item.get("protocol")).lower()
                candidates.extend(
                    [
                        item.get("link"),
                        item.get("url"),
                        item.get("uri"),
                        item.get("config"),
                    ]
                )
                # Deep scan to support nested API schemas.
                candidates.extend(list(_iter_text_values(item)))
            else:
                candidates.extend(list(_iter_text_values(item)))

            for raw in candidates:
                proto, link = _extract_proto_and_link(raw)
                if not link:
                    continue
                # Trust protocol extracted from link first; only fallback to API field if extraction failed.
                if not proto and preferred_proto in result:
                    proto = preferred_proto
                if proto not in result:
                    continue
                if link in seen[proto]:
                    continue
                seen[proto].add(link)
                result[proto].append(link)

    return result


async def _collect_direct_configs_map_for_service(
    service: dict,
    protocols: tuple[str, ...] = ("vless", "vmess", "trojan"),
) -> dict[str, list[str]]:
    # Strict source: only from all.txt (subscription output), no API extras.
    allowed = tuple((p or "").strip().lower() for p in protocols if (p or "").strip())
    result: dict[str, list[str]] = {proto: [] for proto in allowed}
    seen: dict[str, set[str]] = {proto: set() for proto in allowed}

    for base_url in _get_service_node_fetch_base_urls(service):
        candidate_lines: list[str] = []
        local_seen: set[str] = set()
        for suffix in ("all.txt", "all.txt?base64=1"):
            lines = _fetch_remote_lines(f"{base_url}/{suffix}")
            for ln in lines:
                raw = str(ln).strip()
                if not raw or raw in local_seen:
                    continue
                local_seen.add(raw)
                candidate_lines.append(raw)
        if not candidate_lines:
            continue
        for ln in candidate_lines:
            proto, link = _extract_proto_and_link(ln)
            if proto not in allowed or not link:
                continue
            if link in seen[proto]:
                continue
            seen[proto].add(link)
            result[proto].append(link)
    return result


def _available_direct_protocols(base_url: str) -> set[str]:
    mapped = _collect_direct_configs_map(base_url)
    return {proto for proto, items in mapped.items() if items}


async def _send_long_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    *,
    parse_mode: Optional[str] = None,
    reply_markup=None,
    chunk_size: int = 3500,
) -> None:
    if len(text) <= chunk_size:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return

    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines(True):
        if current_len + len(line) > chunk_size and current:
            chunks.append("".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)
    if current:
        chunks.append("".join(current))

    for i, chunk in enumerate(chunks):
        await context.bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode=parse_mode,
            reply_markup=reply_markup if i == 0 else None,
            disable_web_page_preview=True,
        )

def _build_payment_report_caption(tx_code: str, amount: int, payer_last4: str = "") -> str:
    last4 = _normalize_card_last4(payer_last4)
    last4_line = f"\n💳۴ رقم آخر کارت مبدا: {last4}" if last4 else ""
    return (
        "💸گزارش تایید پرداخت🕊\n\n"
        "🔖شیوه پرداخت:کارت به کارت\n"
        f"🔑شناسه تراکنش:{tx_code}\n"
        f"💰مبلغ پرداخت:{int(amount):,} تومان"
        f"{last4_line}"
    )

def _build_subscription_created_caption(
    service_name: str,
    server_title: str,
    gb: float,
    days: int,
    service_code: str,
    amount: Optional[int] = None,
    is_trial: bool = False,
    is_renew: bool = False,
    payment_method: str = "",
    wallet_balance_after: Optional[int] = None,
) -> str:
    if is_trial:
        title = "📄 گزارش ایجاد اشتراک تستی"
    elif is_renew:
        title = "📄 گزارش تمدید اشتراک"
    else:
        title = "📄 گزارش ایجاد اشتراک"
    lines = [
        title,
        "",
        f"👤اشتراک: {service_name}",
        f"🛰سرور: {server_title}",
        f"📊حجم: {float(gb):.1f} گیگابایت",
        f"⏳زمان: {int(days)} روز",
    ]
    if amount is not None:
        lines.append(f"💰مبلغ پرداختی: {int(amount):,} تومان")
    if payment_method == "wallet":
        lines.append("💳پرداخت از کیف پول کاربر — مبلغ کسر شد")
        if wallet_balance_after is not None:
            lines.append(f"💼مانده کیف پول کاربر: {int(wallet_balance_after):,} تومان")
    lines.append(f"🔑شناسه اشتراک:{service_code}")
    return "\n".join(lines)


def _event_channel_target_from_settings() -> Optional[str]:
    br = _get_buy_renew_settings()
    if not bool(br.get("event_channel_enabled", False)):
        return None
    target = str(br.get("event_channel_id") or "").strip()
    return target or None


async def _send_event_channel_subscription_report(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    action_title: str,
    telegram_id: int,
    display_name: str,
    service_name: str,
    server_title: str,
    gb: float,
    days: int,
    service_code: str,
    amount: Optional[int] = None,
) -> None:
    target = _event_channel_target_from_settings()
    if not target or not ADMIN_BOT_TOKEN:
        return

    amount_text = f"{int(amount):,} تومان" if amount is not None else "رایگان"
    text = (
        "📣 گزارش رویداد اشتراک\n"
        f"🔖 نوع عملیات: {action_title}\n"
        f"👤 کاربر: {display_name}\n"
        f"🆔 شناسه تلگرام: {telegram_id}\n"
        f"🏷 نام اشتراک: {service_name}\n"
        f"🛰 سرور: {server_title}\n"
        f"📊حجم: {float(gb):.1f} گیگابایت\n"
        f"⏳زمان: {int(days)} روز\n"
        f"💰مبلغ: {amount_text}\n"
        f"🔑شناسه اشتراک:{service_code}"
    )
    try:
        bot = Bot(token=ADMIN_BOT_TOKEN)
        chat_target: Any = target
        if target.lstrip("-").isdigit():
            chat_target = int(target)
        await bot.send_message(chat_id=chat_target, text=text)
    except Exception as e:
        logger.warning("Failed to send event-channel report (target=%s): %s", target, e)


async def _send_admin_sms_auto_approval_report(payment: dict, *, flow: str = "") -> None:
    if not (ADMIN_ID and ADMIN_BOT_TOKEN and payment):
        return
    try:
        payment_id = int(payment.get("id") or 0)
        amount = int(payment.get("amount") or 0)
        tx_code = str(payment.get("tx_code") or "").strip()
        uid = int(payment.get("user_id") or 0)
        username = str(payment.get("username") or "").strip()
        full_name = str(payment.get("full_name") or "").strip()
        telegram_id = str(payment.get("telegram_id") or "").strip()
        user_label = (full_name or (f"@{username}" if username else telegram_id) or str(uid) or "نامشخص").strip()
        meta = _parse_receipt_meta(str(payment.get("receipt_image") or ""))
        if str(meta.get("sms_auto_admin_report_at") or "").strip():
            return
        sms_sender = str(meta.get("sms_sender") or "").strip()
        sms_reference = str(meta.get("sms_reference") or "").strip()
        sms_amount_raw = str(meta.get("sms_amount_raw") or "").strip()
        sms_currency = str(meta.get("sms_currency") or "").strip()
        receipt_admin_fid = str(meta.get("admin_fid") or "").strip()
        receipt_local_path = str(meta.get("local_path") or "").strip()
        has_receipt = bool(receipt_admin_fid or receipt_local_path)
        flow_label = {
            "wallet_topup": "شارژ کیف پول",
            "buy_payment": "خرید اشتراک",
            "direct_buy_payment": "خرید مستقیم",
            "direct_buy": "خرید مستقیم",
            "renew": "تمدید",
        }.get(str(flow or "").strip().lower(), str(flow or "").strip() or "پرداخت")

        text = (
            "✅ پرداخت با SMS بانک تایید شد\n"
            f"🔖 نوع: {flow_label}\n"
            f"👤 کاربر: {user_label}\n"
            f"💰 مبلغ: {amount:,} تومان\n"
            f"🧾 کد تراکنش: {tx_code or '-'}\n"
            f"🆔 شناسه پرداخت: {payment_id or '-'}\n"
            f"📨 سرشماره SMS: {sms_sender or '-'}\n"
            f"🏦 مبلغ خام SMS: {sms_amount_raw or '-'} {sms_currency or ''}\n"
            f"🔖 پیگیری SMS: {sms_reference or '-'}\n"
            f"🖼 رسید کاربر: {'پیوست شد' if has_receipt else 'در دسترس نیست'}"
        )
        kb = None
        if uid > 0:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"👤 {user_label}", callback_data=f"userbot:user:{uid}")]
            ])
        bot = Bot(token=ADMIN_BOT_TOKEN)
        await _clear_pending_admin_payment_keyboard(payment, bot=bot)
        if receipt_admin_fid:
            await bot.send_photo(chat_id=ADMIN_ID, photo=receipt_admin_fid, caption=text, reply_markup=kb)
        elif receipt_local_path and os.path.exists(receipt_local_path):
            with open(receipt_local_path, "rb") as receipt_file:
                await bot.send_photo(chat_id=ADMIN_ID, photo=receipt_file, caption=text, reply_markup=kb)
        else:
            await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=kb)
        if payment_id > 0:
            _update_payment_receipt_meta(
                payment_id,
                {
                    "sms_auto_admin_report_at": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
    except Exception as e:
        logger.warning("Failed to send admin SMS auto approval report: %s", e)


def _sms_auto_approval_user_text(amount: int, *, direct_note: bool = False) -> str:
    text = (
        "✅ پرداخت شما به‌صورت خودکار با SMS بانک تایید شد.\n"
        f"💰 مبلغ تاییدشده: {int(amount or 0):,} تومان"
    )
    if direct_note:
        text += "\n⏳ اگر خرید مستقیم بوده، اشتراک تا چند لحظه دیگر ساخته و ارسال می‌شود."
    return text


def _card_payment_result_user_text(amount: int, result: str, *, direct_note: bool = False, user_id: int = 0) -> str:
    status = str(result or "").strip().lower()
    if status == "auto_approved":
        return _sms_auto_approval_user_text(amount, direct_note=direct_note)
    if status == "duplicate_approved":
        return (
            "⚠️ این رسید قبلاً ثبت و تایید شده است.\n"
            "برای جلوگیری از تایید اشتباه، رسید تکراری دوباره پردازش نمی‌شود."
        )
    if status == "duplicate_rejected":
        return (
            "❌ این رسید قبلاً توسط ادمین رد شده است.\n"
            "اگر پرداخت جدید انجام داده‌اید، لطفاً رسید جدید همان پرداخت را ارسال کنید."
        )
    return i18n.t("pay_pending_admin", _user_lang(user_id))


async def _safe_edit_message_text(query, text: str, **kwargs):
    """Edit text when possible; fallback for photo/QR messages with inline buttons."""
    try:
        return await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return None
        photo_or_media_error = any(
            needle in err
            for needle in (
                "there is no text in the message to edit",
                "message is not a text message",
                "message can't be edited",
                "message to edit not found",
            )
        )
        if photo_or_media_error and getattr(query, "message", None):
            try:
                await query.message.delete()
            except Exception:
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
            return await query.message.reply_text(text, **kwargs)
        raise

async def _safe_edit_message_reply_markup(query, **kwargs):
    """Ignore Telegram 'Message is not modified' errors for edit_message_reply_markup."""
    try:
        return await query.edit_message_reply_markup(**kwargs)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return None
        raise

def _save_admin_receipt_file_id(payment_id: int, admin_file_id: str) -> None:
    """Persist AdminBot file_id so receipt stays accessible in AdminBot later."""
    if not payment_id or not admin_file_id:
        return
    try:
        with userbot_db._get_conn() as conn:
            cur = conn.cursor()
            now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("SELECT receipt_image FROM userbot_payments WHERE id = ?", (payment_id,))
            row = cur.fetchone()
            current = (row["receipt_image"] if row and row["receipt_image"] else "") if row else ""
            meta = _parse_receipt_meta(current)
            meta["admin_fid"] = admin_file_id
            stored = _build_receipt_meta(meta)
            cur.execute(
                "UPDATE userbot_payments SET receipt_image = ?, updated_at = ? WHERE id = ?",
                (stored, now, payment_id),
            )
    except Exception as e:
        logger.warning(f"Failed to persist admin receipt file_id for payment {payment_id}: {e}")

def _generate_7_digit_code() -> str:
    return f"{random.randint(0, 9999999):07d}"

def _parse_receipt_meta(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {}
    if "|" not in raw and ":" not in raw:
        return {"admin_fid": raw}
    data = {}
    for part in raw.split("|"):
        part = part.strip()
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip()
        v = v.strip()
        if k and v:
            data[k] = v
    return data

def _build_receipt_meta(meta: dict) -> str:
    ordered_keys = [
        "admin_fid",
        "user_fid",
        "local_path",
        "code",
        "payer_last4",
        "pay_flow",
        "sid",
        "gb",
        "days",
        "renew_service_id",
        "service_name",
        "direct_done",
        "direct_done_at",
        "direct_error",
        "direct_attempts",
        "direct_error_at",
        "admin_notified_at",
        "admin_chat_id",
        "admin_message_id",
        "admin_message_deleted_at",
        "admin_keyboard_cleared_at",
        "admin_notify_flow",
        "admin_notify_error",
        "admin_notify_error_at",
        "sms_auto_admin_report_at",
    ]
    seen = set()
    parts = []
    for k in ordered_keys:
        v = meta.get(k)
        if v is None or v == "":
            continue
        parts.append(f"{k}:{v}")
        seen.add(k)
    for k, v in (meta or {}).items():
        if k in seen or v is None or v == "":
            continue
        parts.append(f"{k}:{v}")
    if not parts:
        return ""
    return "|".join(parts)


def _has_active_pending_admin_report(meta: dict) -> bool:
    if not isinstance(meta, dict):
        return False
    if not str(meta.get("admin_notified_at") or "").strip():
        return False
    if not str(meta.get("admin_message_id") or "").strip():
        return False
    if str(meta.get("admin_message_deleted_at") or "").strip():
        return False
    if str(meta.get("admin_keyboard_cleared_at") or "").strip():
        return False
    if str(meta.get("admin_notify_error") or "").strip():
        return False
    return True


def _parse_admin_notify_time(meta: dict) -> Optional[datetime]:
    raw = str((meta or {}).get("admin_notified_at") or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except Exception:
            continue
    return None


async def _clear_pending_admin_payment_keyboard(payment: dict, *, bot: Optional[Bot] = None) -> None:
    try:
        payment_id = int((payment or {}).get("id") or 0)
        meta = _parse_receipt_meta(str((payment or {}).get("receipt_image") or ""))
        chat_id = int(str(meta.get("admin_chat_id") or ADMIN_ID or "0").strip() or 0)
        message_id = int(str(meta.get("admin_message_id") or "0").strip() or 0)
        if chat_id <= 0 or message_id <= 0 or not ADMIN_BOT_TOKEN:
            return
        admin_bot = bot or Bot(token=ADMIN_BOT_TOKEN)
        now_s = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        deleted = False
        try:
            await admin_bot.delete_message(chat_id=chat_id, message_id=message_id)
            deleted = True
        except BadRequest as e:
            msg = str(e)
            if "Message to delete not found" in msg or "message to delete not found" in msg:
                deleted = True
            elif "message can't be deleted" not in msg and "Message can't be deleted" not in msg:
                raise
        try:
            if not deleted:
                await admin_bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise
        if payment_id > 0:
            patch = {"admin_message_deleted_at": now_s} if deleted else {"admin_keyboard_cleared_at": now_s}
            _update_payment_receipt_meta(payment_id, patch)
    except Exception as e:
        logger.warning("Failed to clear pending admin keyboard for payment: %s", e)


_PERSIAN_ARABIC_DIGITS_TRANS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def _to_en_digits(value: Any) -> str:
    return str(value or "").translate(_PERSIAN_ARABIC_DIGITS_TRANS)


def _normalize_card_last4(value: Any) -> str:
    digits = re.sub(r"\D", "", _to_en_digits(value))
    if len(digits) < 4:
        return ""
    return digits[-4:]


def _parse_exact_card_last4(value: Any) -> str:
    """
    Accept only exactly 4 digits for payer card last4.
    1-3 digits or 5+ digits are rejected.
    """
    raw = _to_en_digits(value).strip()
    if not re.fullmatch(r"\d{4}", raw):
        return ""
    return raw


def _service_name_exists(name: str) -> bool:
    n = str(name or "").strip()
    if not n:
        return False
    conn = userbot_db._get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM userbot_services WHERE name = ? LIMIT 1", (n,))
        return cur.fetchone() is not None
    finally:
        conn.close()


def _generate_random_service_name() -> str:
    for _ in range(200):
        candidate = f"vpn-{random.randint(100000, 999999)}"
        if not _service_name_exists(candidate):
            return candidate
    return f"vpn-{int(time.time())}"


def _build_payment_idempotency_key(
    *,
    flow: str,
    internal_user_id: int,
    amount: int,
    photo_file_id: str,
) -> str:
    raw = f"{flow}|{internal_user_id}|{int(amount)}|{photo_file_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _insert_pending_card_payment(
    *,
    internal_user_id: int,
    amount: int,
    receipt_meta: str,
    idempotency_key: str,
    now: str,
) -> tuple[int, str, bool]:
    """
    Insert pending card payment idempotently.
    Returns: (payment_id, tx_code, is_new_row)
    """
    conn = userbot_db._get_conn()
    cur = conn.cursor()
    try:
        # First, try strong idempotency key path (new schema).
        try:
            cur.execute(
                "SELECT id, tx_code FROM userbot_payments WHERE idempotency_key = ? LIMIT 1",
                (idempotency_key,),
            )
            row = cur.fetchone()
            if row:
                return int(row["id"]), str(row["tx_code"] or ""), False

            tx_code = userbot_db.generate_tx_code()
            cur.execute(
                """
                INSERT INTO userbot_payments
                (tx_code, user_id, amount, method, status, receipt_image, idempotency_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tx_code, internal_user_id, amount, "card", "pending", receipt_meta, idempotency_key, now, now),
            )
            conn.commit()
            return int(cur.lastrowid), tx_code, True
        except Exception:
            # Backward-compatible path if DB is older than migration.
            pass

        cur.execute(
            """
            SELECT id, tx_code
            FROM userbot_payments
            WHERE user_id = ? AND amount = ? AND method = 'card' AND status = 'pending' AND receipt_image = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (internal_user_id, amount, receipt_meta),
        )
        row = cur.fetchone()
        if row:
            return int(row["id"]), str(row["tx_code"] or ""), False

        tx_code = userbot_db.generate_tx_code()
        cur.execute(
            """
            INSERT INTO userbot_payments
            (tx_code, user_id, amount, method, status, receipt_image, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tx_code, internal_user_id, amount, "card", "pending", receipt_meta, now, now),
        )
        conn.commit()
        return int(cur.lastrowid), tx_code, True
    finally:
        conn.close()

async def _save_receipt_local_copy(
    context: ContextTypes.DEFAULT_TYPE,
    photo_file_id: str,
    telegram_id: int,
) -> tuple[str, str]:
    """
    Save a local copy of receipt image with random 7-digit filename.
    Returns: (local_path, code)
    """
    code = _generate_7_digit_code()
    local_path = RECEIPTS_DIR / f"{telegram_id}-{code}.jpg"
    for _ in range(20):
        if not local_path.exists():
            break
        code = _generate_7_digit_code()
        local_path = RECEIPTS_DIR / f"{telegram_id}-{code}.jpg"

    f = await context.bot.get_file(photo_file_id)
    data = await f.download_as_bytearray()
    with open(local_path, "wb") as out:
        out.write(data)
    return str(local_path), code


async def _send_admin_pending_card_payment_report(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    payment_id: int,
    tx_code: str,
    amount: int,
    photo_file_id: str,
    user_btn_title: str,
    internal_user_id: int,
    payer_last4: str = "",
    flow: str = "",
    force_recreate: bool = False,
) -> bool:
    if not payment_id:
        return False
    if not (ADMIN_ID and ADMIN_BOT_TOKEN):
        _update_payment_receipt_meta(
            int(payment_id),
            {
                "admin_notify_error": "missing_admin_config",
                "admin_notify_error_at": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        return False
    try:
        payment = userbot_db.get_payment_by_id(int(payment_id)) or {}
        if str(payment.get("status") or "").strip().lower() != "pending":
            return False
        meta = _parse_receipt_meta(str(payment.get("receipt_image") or ""))
        if _has_active_pending_admin_report(meta):
            if not force_recreate:
                return False
            await _clear_pending_admin_payment_keyboard(payment)
            payment = userbot_db.get_payment_by_id(int(payment_id)) or payment
            meta = _parse_receipt_meta(str(payment.get("receipt_image") or ""))

        caption = _build_payment_report_caption(
            tx_code or str(payment.get("tx_code") or ""),
            int(amount or payment.get("amount") or 0),
            payer_last4,
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("رد ❌", callback_data=f"userbot:pay:act:reject:{payment_id}"),
                InlineKeyboardButton("تایید ✅", callback_data=f"userbot:pay:act:approve:{payment_id}"),
            ],
            [InlineKeyboardButton("📩 ارسال پیام", callback_data=f"userbot:pay:msg:{payment_id}")],
            [InlineKeyboardButton(f"👤 {user_btn_title}", callback_data=f"userbot:user:{internal_user_id}")],
        ])

        admin_bot = Bot(token=ADMIN_BOT_TOKEN)
        receipt_admin_fid = str(meta.get("admin_fid") or "").strip()
        receipt_local_path = str(meta.get("local_path") or "").strip()
        user_receipt_file_id = str(photo_file_id or meta.get("user_fid") or "").strip()
        sent = None

        try:
            if receipt_admin_fid:
                sent = await admin_bot.send_photo(chat_id=ADMIN_ID, photo=receipt_admin_fid, caption=caption, reply_markup=kb)
        except Exception as e:
            logger.warning("Failed to reuse admin receipt file_id for payment %s: %s", payment_id, e)

        if sent is None and user_receipt_file_id:
            try:
                f = await context.bot.get_file(user_receipt_file_id)
                data = await f.download_as_bytearray()
                bio = io.BytesIO(data)
                bio.name = "receipt.jpg"
                sent = await admin_bot.send_photo(chat_id=ADMIN_ID, photo=bio, caption=caption, reply_markup=kb)
            except Exception as e:
                logger.warning("Failed to forward user receipt to admin for payment %s: %s", payment_id, e)

        if sent is None and receipt_local_path and os.path.exists(receipt_local_path):
            try:
                with open(receipt_local_path, "rb") as receipt_file:
                    sent = await admin_bot.send_photo(chat_id=ADMIN_ID, photo=receipt_file, caption=caption, reply_markup=kb)
            except Exception as e:
                logger.warning("Failed to send local receipt to admin for payment %s: %s", payment_id, e)

        if sent is None:
            sent = await admin_bot.send_message(chat_id=ADMIN_ID, text=caption, reply_markup=kb)

        try:
            admin_file_id = (sent.photo[-1].file_id if sent and getattr(sent, "photo", None) else None)
        except Exception:
            admin_file_id = None
        if admin_file_id:
            _save_admin_receipt_file_id(int(payment_id), admin_file_id)

        _update_payment_receipt_meta(
            int(payment_id),
            {
                "admin_notified_at": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                "admin_chat_id": str(getattr(getattr(sent, "chat", None), "id", ADMIN_ID) or ADMIN_ID),
                "admin_message_id": str(getattr(sent, "message_id", "") or ""),
                "admin_notify_flow": str(flow or "").strip(),
                "admin_notify_error": None,
                "admin_notify_error_at": None,
                "admin_message_deleted_at": None,
                "admin_keyboard_cleared_at": None,
            },
        )
        return True
    except Exception as e:
        error_text = str(e)[:120]
        _update_payment_receipt_meta(
            int(payment_id),
            {
                "admin_notify_error": error_text,
                "admin_notify_error_at": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        logger.warning("Failed to notify admin (AdminBot) for payment %s: %s", payment_id, e)
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ گزارش پرداخت به AdminBot ارسال نشد.\n"
                    f"🆔 شناسه پرداخت: {payment_id}\n"
                    f"💰 مبلغ: {int(amount or 0):,} تومان\n"
                    f"🔑 کد تراکنش: {tx_code or '-'}\n"
                    f"خطا: {error_text}\n\n"
                    "لطفاً ADMIN_BOT_TOKEN و ADMIN_ID را بررسی کن."
                ),
            )
        except Exception:
            pass
        return False


async def _notify_unreported_pending_card_payments(context: ContextTypes.DEFAULT_TYPE, *, limit: int = 30) -> int:
    """Retry AdminBot reports for pending card payments that have no admin notification marker."""
    if not (ADMIN_ID and ADMIN_BOT_TOKEN):
        return 0
    conn = userbot_db._get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT p.id, p.tx_code, p.user_id, p.amount, p.receipt_image,
                   u.telegram_id, u.username, u.full_name
            FROM userbot_payments p
            LEFT JOIN userbot_users u ON u.id = p.user_id
            WHERE p.status = 'pending' AND p.method = 'card'
            ORDER BY p.id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()

    sent_count = 0
    for row in rows:
        payment_id = int(row.get("id") or 0)
        if payment_id <= 0:
            continue
        meta = _parse_receipt_meta(str(row.get("receipt_image") or ""))
        if _has_active_pending_admin_report(meta):
            continue
        user_title = (
            str(row.get("full_name") or "").strip()
            or str(row.get("username") or "").strip()
            or str(row.get("telegram_id") or "").strip()
            or str(row.get("user_id") or payment_id)
        )
        ok = await _send_admin_pending_card_payment_report(
            context=context,
            payment_id=payment_id,
            tx_code=str(row.get("tx_code") or ""),
            amount=int(row.get("amount") or 0),
            photo_file_id=str(meta.get("user_fid") or "").strip(),
            user_btn_title=user_title,
            internal_user_id=int(row.get("user_id") or 0),
            payer_last4=str(meta.get("payer_last4") or "").strip(),
            flow=str(meta.get("pay_flow") or meta.get("admin_notify_flow") or "").strip(),
            force_recreate=False,
        )
        if ok:
            sent_count += 1
    return sent_count


async def _pending_card_admin_notify_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        sent_count = await _notify_unreported_pending_card_payments(context)
        if sent_count:
            logger.info("Pending card admin notifier sent %s report(s).", sent_count)
    except Exception as e:
        logger.warning("Pending card admin notifier job error: %s", e)


async def _finalize_pending_card_payment(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    amount: int,
    photo_file_id: str,
    flow: str,
    payer_last4: str = "",
    extra_meta: Optional[dict] = None,
) -> tuple[bool, str]:
    if amount <= 0 or not photo_file_id:
        return False, "invalid"

    u_db = userbot_db.get_user_by_telegram_id(user_id)
    internal_user_id = (u_db or {}).get("id")
    if not internal_user_id:
        internal_user_id = userbot_db.upsert_user(
            update.effective_user.id,
            update.effective_user.username,
            update.effective_user.full_name,
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    local_path = ""
    receipt_code = ""
    try:
        local_path, receipt_code = await _save_receipt_local_copy(context, photo_file_id, user_id)
    except Exception as e:
        logger.warning("Failed to save local receipt copy for %s user=%s: %s", flow, user_id, e)

    clean_last4 = _normalize_card_last4(payer_last4)
    receipt_meta_dict = {
        "user_fid": photo_file_id,
        "local_path": local_path,
        "code": receipt_code,
    }
    if isinstance(extra_meta, dict):
        for k, v in extra_meta.items():
            if v is None:
                continue
            txt = str(v).strip()
            if txt == "":
                continue
            receipt_meta_dict[str(k)] = txt
    if clean_last4:
        receipt_meta_dict["payer_last4"] = clean_last4
    receipt_meta = _build_receipt_meta(receipt_meta_dict) or photo_file_id

    idempotency_key = _build_payment_idempotency_key(
        flow=flow,
        internal_user_id=int(internal_user_id),
        amount=int(amount),
        photo_file_id=photo_file_id,
    )
    payment_id, tx_code, is_new_payment = _insert_pending_card_payment(
        internal_user_id=int(internal_user_id),
        amount=int(amount),
        receipt_meta=receipt_meta,
        idempotency_key=idempotency_key,
        now=now,
    )

    existing_payment = userbot_db.get_payment_by_id(int(payment_id)) if payment_id else None
    existing_status = str((existing_payment or {}).get("status") or "").strip().lower()
    if not is_new_payment:
        if existing_status == "approved":
            return False, "duplicate_approved"
        if existing_status == "rejected":
            return False, "duplicate_rejected"

    auto_approved = False
    auto_payment = None
    if payment_id:
        try:
            auto_approved, _auto_msg, auto_payment = userbot_db.try_approve_payment_from_unmatched_sms(payment_id)
        except Exception as e:
            logger.warning("Failed auto-approving payment %s from unmatched SMS: %s", payment_id, e)
            auto_approved = False

    if auto_approved:
        await _send_admin_sms_auto_approval_report(
            auto_payment or userbot_db.get_payment_by_id(int(payment_id)) or {},
            flow=flow,
        )

    if (not auto_approved) and payment_id:
        uname = update.effective_user.username
        full_name = update.effective_user.full_name
        user_btn_title = (full_name or uname or str(user_id)).strip()
        await _send_admin_pending_card_payment_report(
            context=context,
            payment_id=int(payment_id),
            tx_code=tx_code,
            amount=int(amount),
            photo_file_id=photo_file_id,
            user_btn_title=user_btn_title,
            internal_user_id=int(internal_user_id),
            payer_last4=clean_last4,
            flow=flow,
            force_recreate=False,
        )
    return bool(auto_approved), ("auto_approved" if auto_approved else "pending")


async def _deliver_direct_buy_after_sms_notice(context, enabled: bool) -> None:
    if not enabled:
        return
    try:
        app = getattr(context, "application", None) or SimpleNamespace(bot=context.bot)
        await _process_approved_direct_buy_payments(app)
    except Exception as e:
        logger.warning("Direct-buy delivery after SMS auto-approval notice failed: %s", e)


def _update_payment_receipt_meta(payment_id: int, patch: dict) -> None:
    if not payment_id or not isinstance(patch, dict):
        return
    try:
        with userbot_db._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT receipt_image FROM userbot_payments WHERE id = ? LIMIT 1", (int(payment_id),))
            row = cur.fetchone()
            current = str((row["receipt_image"] if row else "") or "")
            meta = _parse_receipt_meta(current)
            for k, v in patch.items():
                if v is None:
                    meta.pop(str(k), None)
                    continue
                val = str(v).strip()
                if val == "":
                    meta.pop(str(k), None)
                else:
                    meta[str(k)] = val
            now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "UPDATE userbot_payments SET receipt_image = ?, updated_at = ? WHERE id = ?",
                (_build_receipt_meta(meta), now, int(payment_id)),
            )
    except Exception as e:
        logger.warning("Failed to patch receipt meta for payment %s: %s", payment_id, e)


def _parse_number_meta(value: Any, as_int: bool = True):
    try:
        if as_int:
            return int(float(str(value or "0").strip()))
        return float(str(value or "0").strip())
    except Exception:
        return 0 if as_int else 0.0


def _parse_direct_retry_count(meta: dict) -> int:
    try:
        return int(meta.get("direct_attempts") or 0)
    except (TypeError, ValueError):
        return 0


def _try_claim_direct_buy_payment(payment_id: int) -> bool:
    conn = userbot_db._get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT receipt_image FROM userbot_payments WHERE id = ? AND status = 'approved' LIMIT 1", (payment_id,))
        row = cur.fetchone()
        if not row:
            return False
        current = str(row["receipt_image"] or "")
        meta = _parse_receipt_meta(current)
        done_state = str(meta.get("direct_done") or "").strip().lower()
        # پرداخت‌هایی که با موفقیت تحویل شده یا در حال پردازش‌اند دوباره claim نمی‌شوند.
        if done_state in {"1", "processing"}:
            return False
        # پرداخت‌هایی که با خطای موقت شکست خورده‌اند، تا سقف مشخصی می‌توانند دوباره تلاش شوند.
        if done_state == "err":
            err_type = str(meta.get("direct_error") or "").strip()
            if _is_non_retryable_direct_error(err_type):
                return False
            if _parse_direct_retry_count(meta) >= DIRECT_DELIVERY_MAX_RETRIES:
                return False
        meta["direct_done"] = "processing"
        meta["direct_error"] = ""
        new_raw = _build_receipt_meta(meta)
        cur.execute(
            "UPDATE userbot_payments SET receipt_image = ?, updated_at = ? WHERE id = ? AND status = 'approved'",
            (new_raw, datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"), payment_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


async def _process_approved_direct_buy_payments(application) -> None:
    conn = userbot_db._get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT p.id, p.tx_code, p.user_id, p.amount, p.receipt_image,
                   u.telegram_id, u.username, u.full_name
            FROM userbot_payments p
            LEFT JOIN userbot_users u ON u.id = p.user_id
            WHERE p.status = 'approved' AND p.method = 'card'
            ORDER BY p.id DESC
            LIMIT 40
            """
        )
        rows = [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()

    if not rows:
        return

    fake_ctx = SimpleNamespace(bot=application.bot)
    for row in rows:
        payment_id = int(row.get("id") or 0)
        if payment_id <= 0:
            continue

        try:
            meta = _parse_receipt_meta(str(row.get("receipt_image") or ""))
            if str(meta.get("pay_flow") or "").strip().lower() != "direct_buy":
                continue
            done_state = str(meta.get("direct_done") or "").strip().lower()
            if done_state in {"1", "processing"}:
                continue
            if done_state == "err":
                err_type = str(meta.get("direct_error") or "").strip()
                if _is_non_retryable_direct_error(err_type):
                    continue
                if _parse_direct_retry_count(meta) >= DIRECT_DELIVERY_MAX_RETRIES:
                    continue
                err_at = str(meta.get("direct_error_at") or "").strip()
                if err_at:
                    try:
                        err_dt = datetime.strptime(err_at, "%Y-%m-%d %H:%M:%S")
                        elapsed = (datetime.now(timezone.utc).replace(tzinfo=None) - err_dt).total_seconds()
                        if elapsed < DIRECT_DELIVERY_RETRY_DELAY_SECONDS:
                            continue
                    except ValueError:
                        pass

            internal_user_id = int(row.get("user_id") or 0)
            tg_id = int(row.get("telegram_id") or 0)
            amount = int(row.get("amount") or 0)
            sid = _parse_number_meta(meta.get("sid"), as_int=True)
            gb = _parse_number_meta(meta.get("gb"), as_int=False)
            days = _parse_number_meta(meta.get("days"), as_int=True)
            renew_service_id = _parse_number_meta(meta.get("renew_service_id"), as_int=True)
            service_name = str(meta.get("service_name") or "").strip()
            if renew_service_id > 0 and not service_name:
                renew_service = userbot_db.get_service_by_id(renew_service_id) or {}
                service_name = (renew_service.get("name") or "").strip()
            if not service_name:
                service_name = _generate_random_service_name()

            if internal_user_id <= 0 or tg_id <= 0 or amount <= 0 or sid <= 0 or gb <= 0 or days <= 0:
                _update_payment_receipt_meta(payment_id, {"direct_done": "err", "direct_error": "invalid_meta"})
                continue

            if not _try_claim_direct_buy_payment(payment_id):
                continue

            tg_user = SimpleNamespace(
                id=tg_id,
                username=str(row.get("username") or ""),
                full_name=str(row.get("full_name") or ""),
            )
            pending_wallet = {
                "internal_user_id": internal_user_id,
                "amount": amount,
                "sid": sid,
                "gb": gb,
                "days": days,
                "renew_service_id": renew_service_id,
            }
            delivery_info: dict = {}
            ok = await _process_wallet_purchase(
                context=fake_ctx,
                user_id=tg_id,
                tg_user=tg_user,
                chat_id=tg_id,
                pending_wallet=pending_wallet,
                service_name=service_name,
                skip_wallet_charge=True,
                tx_code_override=str(row.get("tx_code") or "").strip(),
                delivery_info_out=delivery_info,
            )
            if ok:
                delivered_patch = {
                    "direct_done": "1",
                    "direct_done_at": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                    "service_name": service_name,
                    "delivered_service_id": int(delivery_info.get("delivered_service_id") or 0),
                }
                renew_snapshot = delivery_info.get("renew_snapshot")
                if isinstance(renew_snapshot, dict) and renew_snapshot:
                    try:
                        delivered_patch["renew_snapshot"] = base64.b64encode(
                            json.dumps(renew_snapshot, ensure_ascii=False).encode("utf-8")
                        ).decode("ascii")
                    except Exception:
                        pass
                _update_payment_receipt_meta(payment_id, delivered_patch)
            else:
                meta_after = _parse_receipt_meta(str(row.get("receipt_image") or ""))
                attempts = _parse_direct_retry_count(meta_after) + 1
                _update_payment_receipt_meta(
                    payment_id,
                    {
                        "direct_done": "err",
                        "direct_error": "fulfillment_failed",
                        "direct_error_at": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                        "direct_attempts": str(attempts),
                        "service_name": service_name,
                    },
                )
                if attempts >= DIRECT_DELIVERY_MAX_RETRIES:
                    await _warn_admin_direct_delivery_exhausted(payment_id, row, attempts)
        except Exception as e:
            meta_ex = _parse_receipt_meta(str(row.get("receipt_image") or ""))
            attempts = _parse_direct_retry_count(meta_ex) + 1
            _update_payment_receipt_meta(
                payment_id,
                {
                    "direct_done": "err",
                    "direct_error": "fulfillment_exception",
                    "direct_error_at": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                    "direct_attempts": str(attempts),
                },
            )
            logger.exception(
                "Direct-buy fulfillment row failed (payment_id=%s): %s",
                payment_id,
                e,
            )
            if attempts >= DIRECT_DELIVERY_MAX_RETRIES:
                await _warn_admin_direct_delivery_exhausted(payment_id, row, attempts)
            continue


async def _direct_buy_delivery_loop(application) -> None:
    while True:
        try:
            await _process_approved_direct_buy_payments(application)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("Direct-buy delivery loop error: %s", e)
        await asyncio.sleep(6)


async def _direct_buy_delivery_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await _process_approved_direct_buy_payments(context.application)
    except Exception as e:
        logger.warning("Direct-buy delivery job error: %s", e)


def _is_non_retryable_direct_error(err_type: str) -> bool:
    return str(err_type or "").strip().lower() in {"invalid_meta", "already_done"}


async def _warn_admin_direct_delivery_exhausted(payment_id: int, row: dict, attempts: int) -> None:
    """به ادمین اخطار می‌دهد که تحویل خرید مستقیم پس از چند تلاش شکست خورده است."""
    if not (ADMIN_ID and ADMIN_BOT_TOKEN):
        return
    try:
        from telegram import Bot
        admin_bot = Bot(token=ADMIN_BOT_TOKEN)
        user_title = (
            str(row.get("full_name") or "").strip()
            or str(row.get("username") or "").strip()
            or str(row.get("telegram_id") or "").strip()
            or "-"
        )
        await admin_bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "⚠️ تحویل خرید مستقیم پس از چند تلاش موفق نشد.\n"
                f"🆔 پرداخت: {payment_id}\n"
                f"👤 کاربر: {user_title}\n"
                f"🔑 کد تراکنش: {row.get('tx_code') or '-'}\n"
                f"📦 تعداد تلاش: {attempts}\n\n"
                "لطفاً به صورت دستی بررسی/تحویل دهید یا پنل/نود را بررسی کنید."
            ),
        )
    except Exception as e:
        logger.warning("Failed to warn admin about direct delivery exhaustion (payment_id=%s): %s", payment_id, e)


async def _warn_admin_pending_node_sync(
    *,
    server_id: int,
    failed_servers: list[str],
    service_label: str,
) -> None:
    """اگر تمدید/ساخت روی بعضی نودها با خطا مواجه شد، به ادمین هشدار + دکمه sync سریع بده."""
    if not (ADMIN_ID and ADMIN_BOT_TOKEN) or not failed_servers:
        return
    try:
        from telegram import Bot
        admin_bot = Bot(token=ADMIN_BOT_TOKEN)
        fail_lines = "\n".join(f"• {t}" for t in list(dict.fromkeys(failed_servers))[:10])
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔄 همگام‌سازی و ساخت کاربران جاافتاده روی نودها",
                        callback_data=f"server:{server_id}:sync_nodes_missing",
                    )
                ]
            ]
        )
        await admin_bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"⚠️ {service_label} روی بعضی نودها تحویل نشد.\n\n"
                f"سرورهای در دسترس نبودند:\n{fail_lines}\n\n"
                "سرویس روی بقیه نودها تحویل داده شد. بعد از بازگشت آن سرورها، "
                "روی دکمه زیر بزنید تا ربات بررسی کند و کاربران جاافتاده را بسازد."
            ),
            reply_markup=kb,
        )
    except Exception as e:
        logger.warning("Failed to warn admin about pending node sync (server_id=%s): %s", server_id, e)


def _resolve_plan_display_mode(server_block: Optional[Dict[str, Any]]) -> str:
    """Resolve plan display mode with backward compatibility for legacy `mode` key."""
    block = server_block or {}
    raw_mode = str(block.get("display_mode") or block.get("mode") or "").strip().lower()
    if raw_mode in {"fixed", "dynamic", "mixed"}:
        return raw_mode
    return "dynamic"


async def show_fixed_categories(query, sid, server_block):
    txp = _get_tx_plans_settings()
    text_settings = _get_text_settings()
    plans_all = server_block.get("plans", []) or []
    plans_all = _sort_plans(plans_all, txp)

    if not bool(txp.get("plan_categories_enabled", True)):
        if not plans_all:
            await query.answer("❌ پلن ثابت برای این سرور یافت نشد.", show_alert=True)
            return
        plan_columns = int(_get_buy_renew_settings().get("plan_columns") or 1)
        uv = bool(_get_buy_renew_settings().get("renew_unlimited_volume", False))
        ut = bool(_get_buy_renew_settings().get("renew_unlimited_time", False))
        uv_from = int(_get_buy_renew_settings().get("renew_unlimited_volume_from_gb") or 1000)
        ut_from = int(_get_buy_renew_settings().get("renew_unlimited_time_from_days") or 365)
        await _safe_edit_message_text(
            query,
            text_settings.get("plans_list_text") or "🛒 **لطفاً پلن مورد نظر خود را انتخاب کنید:**",
            parse_mode="Markdown",
            reply_markup=plans_keyboard(
                plans_all,
                sid,
                0,
                columns=plan_columns,
                unlimited_volume=uv,
                unlimited_volume_from=uv_from,
                unlimited_time=ut,
                unlimited_time_from=ut_from,
                sort_by_priority=False,
                back_to_categories=False,
                rtl_rows=bool(txp.get("plan_sort_desc", False)),
            ),
        )
        return

    categories = server_block.get("categories", [])
    if not categories:
        await query.answer("❌ دسته‌بندی برای این سرور یافت نشد.", show_alert=True)
        return
    await _safe_edit_message_text(
        query,
        "📂 **لطفاً دسته بندی مورد نظر را انتخاب کنید:**", 
        parse_mode="Markdown", 
        reply_markup=category_keyboard(categories, sid)
    )

async def show_main_buy_menu(query, sid, server_block, user_id, context):
    """نمایش صفحه اصلی خرید شبیه عکس کاربر"""
    dyn_settings = server_block.get("dynamic_settings", {})
    
    # مقادیر پیش‌فرض برای ویزارد داینامیک
    default_gb = dyn_settings.get("min_gb", 20)
    default_months = dyn_settings.get("min_month", 1)
    price, off_percent = _calc_dynamic_price(default_gb, default_months, dyn_settings)
    
    # ذخیره اطلاعات ویزارد
    context.user_data[f"wiz_{user_id}"] = {"gb": default_gb, "months": default_months}
    
    # نمایش صفحه ترکیبی با ویزارد داینامیک و دکمه پلن‌های آماده (بددون نمایش پلن‌ها)
    await _safe_edit_message_text(
        query,
        "🛒 **خرید اشتراک**\n\n🎛 **بسته دلخواه خود را بسازید یا از پلن‌های آماده استفاده کنید:**", 
        parse_mode="Markdown",
        reply_markup=mixed_buy_keyboard(sid, default_gb, default_months, price, off_percent=off_percent)
    )

async def start_dynamic_wizard(query, context, sid, user_id, server_block):
    dyn_settings = server_block.get("dynamic_settings", {})
    # مقادیر پیش‌فرض
    default_gb = dyn_settings.get("min_gb", 20)
    default_months = dyn_settings.get("min_month", 1)
    price, off_percent = _calc_dynamic_price(default_gb, default_months, dyn_settings)
    
    context.user_data[f"wiz_{user_id}"] = {"gb": default_gb, "months": default_months}
    
    await _safe_edit_message_text(
        query,
        "📦بسته مورد نیاز خود را جهت خرید تنظیم کنید", 
        parse_mode="Markdown",
        reply_markup=buy_wizard_keyboard(sid, default_gb, default_months, price, off_percent=off_percent)
    )


async def _send_buy_flow_for_server(
    chat_id: int,
    sid: int,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    is_renew: bool = False,
):
    data_plans = plans_storage._load_all_plans()
    server_block = data_plans.get("servers", {}).get(str(sid), {})
    if not server_block:
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ تنظیمات پلن برای این سرور یافت نشد.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    # قوانین تمدید — گیت ورود به ویزارد (ضد پرش مراحل)
    if is_renew:
        renew_target_id = int(context.user_data.get(f"renew_target_{user_id}") or 0)
        renew_svc_gate = userbot_db.get_service_by_id(renew_target_id) if renew_target_id > 0 else None
        if not renew_svc_gate:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ سرویس انتخاب‌شده برای تمدید یافت نشد.",
                reply_markup=_main_menu_keyboard(),
            )
            return
        if not await _service_is_renewable_live(renew_svc_gate):
            context.user_data.pop(f"renew_target_{user_id}", None)
            await context.bot.send_message(
                chat_id=chat_id,
                text=_renew_not_allowed_text(),
                reply_markup=_main_menu_keyboard(),
            )
            return

    display_mode = _resolve_plan_display_mode(server_block)
    _lg = _user_lang(user_id)
    title = i18n.t("flow_renew_title", _lg) if is_renew else i18n.t("flow_buy_title", _lg)

    if display_mode == "mixed":
        dyn_settings = server_block.get("dynamic_settings", {})
        default_gb = dyn_settings.get("min_gb", 20)
        default_months = dyn_settings.get("min_month", 1)
        price, off_percent = _calc_dynamic_price(default_gb, default_months, dyn_settings)
        context.user_data[f"wiz_{user_id}"] = {"gb": default_gb, "months": default_months}
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🛒 **{title}**\n\n🎛 **بسته دلخواه خود را بسازید یا از پلن‌های آماده استفاده کنید:**",
            parse_mode="Markdown",
            reply_markup=mixed_buy_keyboard(sid, default_gb, default_months, price, off_percent=off_percent),
        )
        return

    if display_mode == "dynamic":
        dyn_settings = server_block.get("dynamic_settings", {})
        default_gb = dyn_settings.get("min_gb", 20)
        default_months = dyn_settings.get("min_month", 1)
        price, off_percent = _calc_dynamic_price(default_gb, default_months, dyn_settings)
        context.user_data[f"wiz_{user_id}"] = {"gb": default_gb, "months": default_months}
        await context.bot.send_message(
            chat_id=chat_id,
            text="📦بسته مورد نیاز خود را جهت خرید تنظیم کنید",
            parse_mode="Markdown",
            reply_markup=buy_wizard_keyboard(sid, default_gb, default_months, price, off_percent=off_percent),
        )
        return

    txp = _get_tx_plans_settings()
    text_settings = _get_text_settings()
    plans_all = server_block.get("plans", []) or []
    plans_all = _sort_plans(plans_all, txp)

    if not bool(txp.get("plan_categories_enabled", True)):
        if not plans_all:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ پلن ثابت برای این سرور یافت نشد.",
                reply_markup=_main_menu_keyboard(),
            )
            return
        br = _get_buy_renew_settings()
        await context.bot.send_message(
            chat_id=chat_id,
            text=text_settings.get("plans_list_text") or "🛒 **لطفاً پلن مورد نظر خود را انتخاب کنید:**",
            parse_mode="Markdown",
            reply_markup=plans_keyboard(
                plans_all,
                sid,
                0,
                columns=int(br.get("plan_columns") or 1),
                unlimited_volume=bool(br.get("renew_unlimited_volume", False)),
                unlimited_volume_from=int(br.get("renew_unlimited_volume_from_gb") or 1000),
                unlimited_time=bool(br.get("renew_unlimited_time", False)),
                unlimited_time_from=int(br.get("renew_unlimited_time_from_days") or 365),
                sort_by_priority=False,
                back_to_categories=False,
                rtl_rows=bool(txp.get("plan_sort_desc", False)),
            ),
        )
        return

    categories = server_block.get("categories", [])
    if not categories:
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ دسته‌بندی برای این سرور یافت نشد.",
            reply_markup=_main_menu_keyboard(),
        )
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text="📂 **لطفاً دسته بندی مورد نظر را انتخاب کنید:**",
        parse_mode="Markdown",
        reply_markup=category_keyboard(categories, sid),
    )


async def _send_config_and_qr_after_delivery(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    service: dict,
) -> str:
    """
    بلافاصله پس از ساخت/تمدید اشتراک، لینک کانفیگ + QR را برای کاربر ارسال می‌کند.
    خروجی: نوع تحویل ارسال‌شده ('qr' / 'direct_config') یا رشته خالی در صورت عدم ارسال.
    """
    settings = _get_subscription_settings()
    base_urls = _get_service_node_base_urls(service)
    service_id = int(service.get("id") or 0)

    config_items: list[tuple[str, str]] = []
    if base_urls:
        base_url = base_urls[0]
        # Detect X-UI: base_url is already the full sub URL ( .../sub/{uuid} )
        is_xui = False
        try:
            from Shared import xui_api as _xui_check
            # Find the server for this base_url to detect X-UI
            # Quick heuristic: if base_url contains /sub/ and service server is X-UI
            sid_tmp = int(service.get("server_id") or 0)
            srv_tmp = database.get_server_by_id(sid_tmp) if sid_tmp else None
            if srv_tmp and _xui_check.is_xui_server(srv_tmp):
                is_xui = True
            elif "/sub/" in base_url:
                # Fallback: if any mapping server is X-UI
                for m in (userbot_db.get_service_nodes(service_id) if service_id else []):
                    s = database.get_server_by_id(int(m.get("server_id") or 0))
                    if s and _xui_check.is_xui_server(s):
                        is_xui = True
                        break
        except Exception:
            is_xui = False
        _dlg = _user_lang(user_id)
        if is_xui:
            if settings.get("show_sub_link", True):
                config_items.append(("🔗 " + i18n.t("config_sub_link", _dlg) + ":", base_url))
            if settings.get("show_sub_link_b64", False):
                sep = "&" if "?" in base_url else "?"
                config_items.append(("🔐 " + i18n.t("sub_b64_label", _dlg), f"{base_url}{sep}base64=1"))
        else:
            if settings.get("show_sub_link", True):
                config_items.append(("🔗 " + i18n.t("config_sub_link", _dlg) + ":", f"{base_url}/all.txt"))
            if settings.get("show_auto_sub_link", False):
                config_items.append(("🤖 " + i18n.t("auto_sub_link_label", _dlg), f"{base_url}/sub/?asn=unknown"))
            if settings.get("show_sub_link_b64", False):
                config_items.append(("🔐 " + i18n.t("sub_b64_label", _dlg), f"{base_url}/all.txt?base64=1"))
        if settings.get("show_multi_server", False):
            try:
                managed_link, _ = _get_or_create_bot_sub_links(int(service_id), service=service)
                if managed_link:
                    config_items.append((i18n.t("config_smart", _dlg) + ":", managed_link))
            except Exception as e:
                logger.warning("Failed to build managed sub link after delivery (service_id=%s): %s", service_id, e)
        if settings.get("show_multi_server_b64", False):
            try:
                _, managed_link_b64 = _get_or_create_bot_sub_links(int(service_id), service=service)
                if managed_link_b64:
                    config_items.append(("🌐 " + i18n.t("smart_b64_label", _dlg), managed_link_b64))
            except Exception as e:
                logger.warning("Failed to build managed sub b64 link after delivery (service_id=%s): %s", service_id, e)

    if len(config_items) == 1:
        primary_link = config_items[0][1]
        qr_image = make_qr_image(primary_link)
        qr_caption = (
            i18n.t("delivery_ready_title", _dlg) + "\n\n"
            f"{config_items[0][0]}\n"
            f"<code>{escape(primary_link)}</code>\n\n"
            + i18n.t("delivery_copy_hint", _dlg)
        )
        try:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=qr_image,
                caption=qr_caption,
                parse_mode="HTML",
                reply_markup=subscription_links_keyboard(service_id) if service_id else None,
            )
        except Exception:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=qr_caption,
                    parse_mode="HTML",
                    reply_markup=subscription_links_keyboard(service_id) if service_id else None,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
        return "qr"

    if len(config_items) > 1:
        # وقتی چند روش نمایش لینک فعال است، به‌جای انتخاب/تکرار لینک‌ها،
        # اطلاعات اشتراک نمایش داده می‌شود تا کاربر از کیبورد وضعیت انتخاب کند.
        return ""

    if settings.get("show_direct_config", True):
        try:
            await _send_service_direct_configs_shell(
                context,
                user_id=user_id,
                service_id=service_id,
                service=service,
            )
            return "direct_config"
        except Exception as e:
            logger.warning(
                "Failed to send direct configs after delivery (service_id=%s): %s",
                service_id,
                e,
            )

    return ""


async def _process_wallet_purchase(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    tg_user,
    chat_id: int,
    pending_wallet: dict,
    service_name: str,
    skip_wallet_charge: bool = False,
    tx_code_override: str = "",
    delivery_info_out: Optional[dict] = None,
) -> bool:
    internal_user_id = pending_wallet.get("internal_user_id")
    amount = int(pending_wallet.get("amount") or 0)
    sid = int(pending_wallet.get("sid") or 0)
    gb = float(pending_wallet.get("gb") or 0)
    days = int(pending_wallet.get("days") or 0)
    renew_service_id = int(pending_wallet.get("renew_service_id") or 0)

    if not internal_user_id:
        u_db = userbot_db.get_user_by_telegram_id(user_id)
        internal_user_id = (u_db or {}).get("id")

    if not internal_user_id or amount <= 0 or sid <= 0 or gb <= 0 or days <= 0:
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ اطلاعات خرید ناقص است. لطفاً دوباره اقدام کنید.",
            reply_markup=_main_menu_keyboard(),
        )
        return False

    # قوانین تمدید (حالت پیشرفته) — قبل از هر پرداختی بررسی می‌شود تا پولی کم و کسر نشود
    if renew_service_id > 0:
        renew_service_pre = userbot_db.get_service_by_id(renew_service_id)
        if (not renew_service_pre) or int(renew_service_pre.get("user_id") or 0) != int(internal_user_id):
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ سرویس انتخاب‌شده برای تمدید یافت نشد.",
                reply_markup=_main_menu_keyboard(),
            )
            return False
        if not await _service_is_renewable_live(renew_service_pre):
            await context.bot.send_message(
                chat_id=chat_id,
                text=_renew_not_allowed_text(),
                reply_markup=_main_menu_keyboard(),
            )
            return False

    if not skip_wallet_charge:
        current_user = userbot_db.get_user_by_id(internal_user_id) or {}
        wallet_balance = int(current_user.get("wallet_balance") or 0)
        if wallet_balance < amount:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ موجودی کیف پول شما کافی نیست. لطفاً ابتدا کیف پول را شارژ کنید.",
                reply_markup=_main_menu_keyboard(),
            )
            return False

    # کسر اتمیک موجودی قبل از هر عملیات پنل — اگر هر جایی بعد از این خطا داد،
    # مبلغ در مسیرهای خطا برگردانده می‌شود (anti double-spend)
    wallet_charged = False
    if not skip_wallet_charge:
        if not userbot_db.decrease_user_wallet(internal_user_id, amount):
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ موجودی کیف پول شما کافی نیست. لطفاً ابتدا کیف پول را شارژ کنید.",
                reply_markup=_main_menu_keyboard(),
            )
            return False
        wallet_charged = True

    server = database.get_server_by_id(sid)
    if not server:
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ سرور انتخاب‌شده یافت نشد. لطفاً دوباره خرید را انجام دهید.",
            reply_markup=_main_menu_keyboard(),
        )
        return False

    created_nodes: list[dict] = []
    panel_user_uuid = ""
    panel_user_id = None
    usage_current = 0.0
    usage_limit = float(gb)
    days_left = int(days)
    server_title = server.get("title") or f"سرور #{sid}"
    is_renew_flow = renew_service_id > 0
    renew_service = None
    last_online = None
    renew_failed_servers: list[str] = []

    if is_renew_flow:
        renew_service = userbot_db.get_service_by_id(renew_service_id)
        if (not renew_service) or int(renew_service.get("user_id") or 0) != int(internal_user_id):
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ سرویس انتخاب‌شده برای تمدید یافت نشد.",
                reply_markup=_main_menu_keyboard(),
            )
            return False
        if not await _service_is_renewable_live(renew_service):
            await context.bot.send_message(
                chat_id=chat_id,
                text=_renew_not_allowed_text(),
                reply_markup=_main_menu_keyboard(),
            )
            return False
        try:
            renew_snapshot = {
                "usage_current": _to_float(renew_service.get("usage_current"), 0.0),
                "usage_limit": _to_float(renew_service.get("usage_limit"), 0.0),
                "days_left": int(renew_service.get("days_left") or 0),
                "name": str(renew_service.get("name") or "").strip(),
            }
            if isinstance(delivery_info_out, dict):
                delivery_info_out["renew_snapshot"] = renew_snapshot
            usage_limit, days_left, last_online, renew_failed_servers = (
                await _apply_service_renewal_on_targets(
                    renew_service,
                    user_id=int(user_id),
                    service_name=service_name,
                    package_gb=float(gb),
                    package_days=int(days),
                )
            )
            usage_current = 0.0
            server_title = (renew_service.get("server_title") or server_title).strip() or server_title
            sid = int(renew_service.get("server_id") or sid)
        except Exception as e:
            logger.exception("Failed renewing Hiddify user(s) for telegram_id=%s", user_id)
            if wallet_charged:
                try:
                    userbot_db.increase_user_wallet(internal_user_id, amount)
                except Exception:
                    logger.exception("Refund after failed renewal also failed (user=%s)", internal_user_id)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ تمدید سرویس روی سرور/نودها انجام نشد.\nجزئیات خطا: {e}",
                reply_markup=_main_menu_keyboard(),
            )
            return False
    else:
        payload = {
            "name": service_name,
            "usage_limit_GB": float(gb),
            "package_days": int(days),
            "start_date": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d"),
            "current_usage_GB": 0,
            "last_reset_time": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
            "is_active": True,
            "comment": _build_panel_user_comment(int(user_id), is_test=False),
        }
        targets = _get_target_servers_for_sale(server)
        if not targets:
            targets = [server]
        try:
            created, created_nodes = await _create_service_users_on_targets(targets, payload)
        except Exception as e:
            logger.exception("Failed creating Hiddify user(s) for telegram_id=%s", user_id)
            if created_nodes:
                await _deactivate_created_users(created_nodes)
            if wallet_charged:
                try:
                    userbot_db.increase_user_wallet(internal_user_id, amount)
                except Exception:
                    logger.exception("Refund after failed purchase also failed (user=%s)", internal_user_id)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ ساخت سرویس روی سرور/نودها انجام نشد.\nجزئیات خطا: {e}\n\nدوباره تلاش کنید یا با پشتیبانی تماس بگیرید.",
                reply_markup=_main_menu_keyboard(),
            )
            return False

        panel_user_uuid = str(created.get("uuid") or created.get("id") or "").strip()
        panel_user_id = created.get("id")
        usage_limit = float(created.get("usage_limit_GB") or gb)
        usage_current = float(created.get("current_usage_GB") or 0)
        last_online = created.get("last_online")

        days_left = int(days)
        try:
            start_raw = created.get("start_date")
            package_days = int(created.get("package_days") or days)
            if start_raw:
                start_dt = None
                for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        start_dt = datetime.strptime(start_raw, fmt)
                        break
                    except ValueError:
                        continue
                if start_dt:
                    end_dt = start_dt + timedelta(days=package_days)
                    days_left = (end_dt.date() - datetime.now(timezone.utc).replace(tzinfo=None).date()).days
        except Exception:
            days_left = int(days)

    tx_code = str(tx_code_override or "").strip() or userbot_db.generate_tx_code()
    order_id = _generate_order_id()
    service_code = _generate_service_code()
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

    service_db_id = renew_service_id if is_renew_flow else None
    wallet_purchase_payment_id = 0
    try:
        with userbot_db._get_conn() as conn:
            cur = conn.cursor()

            if is_renew_flow:
                old_comment = str((renew_service or {}).get("comment") or "")
                parsed_comment = _parse_service_comment(old_comment)
                comment_parts = []
                old_uuid = str(parsed_comment.get("uuid") or "").strip()
                if old_uuid:
                    comment_parts.append(f"uuid:{old_uuid}")
                comment_parts.append(f"price:{amount}")
                comment_parts.append(f"code:{service_code}")
                service_comment = "|".join(comment_parts)
                cur.execute(
                    """
                    UPDATE userbot_services
                    SET name = ?, server_id = ?, server_title = ?, usage_current = ?, usage_limit = ?,
                        days_left = ?, last_online = ?, comment = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        service_name,
                        sid,
                        server_title,
                        usage_current,
                        usage_limit,
                        days_left,
                        last_online,
                        service_comment,
                        int(renew_service_id),
                        int(internal_user_id),
                    ),
                )
                service_db_id = int(renew_service_id)
            else:
                comment_parts = []
                if panel_user_uuid:
                    comment_parts.append(f"uuid:{panel_user_uuid}")
                comment_parts.append(f"price:{amount}")
                comment_parts.append(f"code:{service_code}")
                service_comment = "|".join(comment_parts)
                cur.execute(
                    """
                    INSERT INTO userbot_services
                    (user_id, name, server_id, server_title, usage_current, usage_limit, days_left, last_online, comment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        internal_user_id,
                        service_name,
                        sid,
                        server_title,
                        usage_current,
                        usage_limit,
                        days_left,
                        last_online,
                        service_comment,
                    ),
                )
                service_db_id = cur.lastrowid

            cur.execute(
                """
                INSERT INTO userbot_orders
                (order_id, user_id, telegram_id, username, full_name, created_at, volume_gb, days, price, plan_title, server_location, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    internal_user_id,
                    tg_user.id,
                    tg_user.username,
                    tg_user.full_name,
                    now,
                    gb,
                    days,
                    amount,
                    service_name,
                    server_title,
                    "approved",
                ),
            )

            if not skip_wallet_charge:
                cur.execute(
                    """
                    INSERT INTO userbot_payments
                    (tx_code, user_id, amount, method, status, receipt_image, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tx_code,
                        internal_user_id,
                        amount,
                        "wallet",
                        "approved",
                        None,
                        now,
                        now,
                    ),
                )
                wallet_purchase_payment_id = int(cur.lastrowid or 0)
        if not skip_wallet_charge:
            if wallet_purchase_payment_id > 0:
                try:
                    userbot_db.try_grant_referral_purchase_reward(internal_user_id, wallet_purchase_payment_id)
                except Exception as e:
                    logger.warning(
                        "Failed to process referral purchase reward for wallet purchase (user=%s): %s",
                        internal_user_id,
                        e,
                    )
    except Exception as e:
        logger.exception("Failed to persist wallet purchase for telegram_id=%s", user_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ سرویس روی سرور ساخته شد اما ثبت نهایی خرید خطا داد: {e}\nمبلغ از کیف پول شما کسر شده و تا رفع مشکل نزد پشتیبانی نگاهداری می‌شود. لطفاً به پشتیبانی پیام دهید.",
            reply_markup=_main_menu_keyboard(),
        )
        return False

    if (not is_renew_flow) and service_db_id and created_nodes:
        for node_item in created_nodes:
            try:
                node_sid = int(node_item.get("server_id") or 0)
                node_uuid = str(node_item.get("panel_user_uuid") or "").strip()
                if node_sid <= 0 or not node_uuid:
                    continue
                userbot_db.add_service_node(
                    service_id=int(service_db_id),
                    server_id=node_sid,
                    panel_user_uuid=node_uuid,
                    server_title=str(node_item.get("server_title") or ""),
                    panel_user_id=(
                        str(node_item.get("panel_user_id"))
                        if node_item.get("panel_user_id") is not None
                        else None
                    ),
                    is_active=1,
                )
            except Exception as e:
                logger.warning(
                    "Failed to create service-node mapping (service_id=%s, server_id=%s): %s",
                    service_db_id,
                    node_item.get("server_id"),
                    e,
                )

    if isinstance(delivery_info_out, dict):
        delivery_info_out["delivered_service_id"] = int(service_db_id or 0)

    # نودهای down هنگام ساخت: گزارش به ادمین تا بعداً sync شوند.
    created_server_ids = {int(int(n.get("server_id") or 0)) for n in (created_nodes or [])}
    create_failed_servers = [
        str(s.get("title") or f"سرور #{s.get('id')}")
        for s in (targets if not is_renew_flow else [])
        if int(s.get("id") or 0) not in created_server_ids
    ]

    _dlg = _user_lang(user_id)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            i18n.t("created_notify_title", _dlg) + "\n"
            + i18n.t("created_notify_hint", _dlg) + "\n\n"
            f"🎁 {i18n.t('tx_id_label', _dlg)}{tx_code}"
        ),
        reply_markup=_main_menu_keyboard(lang=_dlg),
    )

    delivered_service = {
        "id": service_db_id or panel_user_id or "—",
        "name": service_name,
        "server_title": server_title,
        "usage_current": usage_current,
        "usage_limit": usage_limit,
        "days_left": days_left,
        "comment": f"code:{service_code}",
    }
    settings = _get_subscription_settings()
    delivered_kind = await _send_config_and_qr_after_delivery(
        context,
        user_id=chat_id,
        service=delivered_service,
    )
    if not delivered_kind:
        await context.bot.send_message(
            chat_id=chat_id,
            text=_build_subscription_status_text(delivered_service),
            parse_mode="Markdown",
            reply_markup=subscription_status_keyboard(
                service_db_id,
                show_direct_config=settings.get("show_direct_config", True),
                show_sub_link=settings.get("show_sub_link", True),
                show_configs=_should_show_configs_button(settings),
                show_detach=_is_connected_service(delivered_service),
            ),
        )

    if ADMIN_ID and ADMIN_BOT_TOKEN:
        try:
            admin_bot = Bot(token=ADMIN_BOT_TOKEN)
            user_btn_title = (tg_user.full_name or tg_user.username or str(user_id)).strip()
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"👤 {user_btn_title}", callback_data=f"userbot:user:{internal_user_id}")]
            ])
            wallet_balance_after: Optional[int] = None
            if wallet_charged:
                try:
                    wallet_balance_after = int((userbot_db.get_user_by_id(internal_user_id) or {}).get("wallet_balance") or 0)
                except Exception:
                    wallet_balance_after = None
            await admin_bot.send_message(
                chat_id=ADMIN_ID,
                text=_build_subscription_created_caption(
                    service_name=service_name,
                    server_title=server_title,
                    gb=gb,
                    days=days,
                    amount=amount,
                    service_code=service_code,
                    is_renew=is_renew_flow,
                    payment_method="wallet" if wallet_charged else "card",
                    wallet_balance_after=wallet_balance_after,
                ),
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning(f"Failed to notify admin for subscription creation (user={user_id}): {e}")

    await _send_event_channel_subscription_report(
        context,
        action_title="تمدید اشتراک" if is_renew_flow else "خرید اشتراک",
        telegram_id=int(user_id),
        display_name=(tg_user.full_name or tg_user.username or str(user_id)).strip(),
        service_name=service_name,
        server_title=server_title,
        gb=float(gb),
        days=int(days),
        service_code=service_code,
        amount=int(amount),
    )

    pending_failed = list(dict.fromkeys((renew_failed_servers or []) + (create_failed_servers or [])))
    if pending_failed:
        try:
            await _warn_admin_pending_node_sync(
                server_id=int(sid or 0),
                failed_servers=pending_failed,
                service_label="تمدید اشتراک" if is_renew_flow else "ساخت اشتراک",
            )
        except Exception as e:
            logger.warning("Failed to warn admin about pending node sync: %s", e)

    return True


async def _connect_panel_subscription_by_uuid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parsed_uuid: str,
    internal_user_id: int,
) -> bool:
    parsed_uuid = str(parsed_uuid or "").strip().lower()
    if not parsed_uuid:
        return False

    user = update.effective_user
    if not user:
        return False
    user_id = int(user.id)
    msg_obj = update.callback_query.message if update.callback_query else update.message
    if not msg_obj:
        return False

    internal_user_id = int(internal_user_id or 0)
    if internal_user_id <= 0:
        internal_user_id = int(userbot_db.upsert_user(user.id, user.username, user.full_name) or 0)
    if internal_user_id <= 0:
        await msg_obj.reply_text("❌ کاربر یافت نشد.", reply_markup=_main_menu_keyboard())
        set_user_step(context, user_id, None)
        return True

    owner = userbot_db.get_service_owner_by_panel_uuid(parsed_uuid)
    if owner and int(owner.get("user_id") or 0) != int(internal_user_id):
        await msg_obj.reply_text(
            "⛔ این اشتراک قبلاً توسط کاربر دیگری متصل شده است و قابل اتصال مجدد نیست.",
            reply_markup=_main_menu_keyboard(),
        )
        set_user_step(context, user_id, None)
        return True

    existing_self = userbot_db.get_user_service_by_panel_uuid(internal_user_id, parsed_uuid)
    if existing_self:
        set_user_step(context, user_id, None)
        service = await _sync_service_runtime_from_panels(existing_self)
        settings = _get_subscription_settings()
        await msg_obj.reply_text(
            "ℹ️ این اشتراک قبلاً به حساب شما متصل شده است.",
            reply_markup=_main_menu_keyboard(),
        )
        await msg_obj.reply_text(
            _build_subscription_status_text(service),
            parse_mode="Markdown",
            reply_markup=subscription_status_keyboard(
                service.get("id"),
                show_direct_config=settings.get("show_direct_config", True),
                show_sub_link=settings.get("show_sub_link", True),
                show_configs=_should_show_configs_button(settings),
                show_detach=_is_connected_service(service),
            ),
        )
        return True

    await msg_obj.reply_text("⏳ در حال بررسی اشتراک...")
    targets = await _find_panel_user_targets_by_uuid(parsed_uuid)
    if not targets:
        await msg_obj.reply_text(
            "❌ اشتراکی با این UUID روی سرورهای ربات پیدا نشد.",
            reply_markup=_main_menu_keyboard(),
        )
        set_user_step(context, user_id, None)
        return True

    primary_server, primary_user = targets[0]
    service_name = str(primary_user.get("name") or "اشتراک متصل‌شده").strip() or "اشتراک متصل‌شده"
    usage_limit = _to_float(primary_user.get("usage_limit_GB"), 0.0)
    total_usage = 0.0
    min_days_left: Optional[int] = None
    latest_last_online: Optional[datetime] = None
    for _srv, pu in targets:
        total_usage += _to_float(pu.get("current_usage_GB"), 0.0)
        dleft = _days_left_from_panel_user(pu)
        if dleft is not None:
            min_days_left = dleft if min_days_left is None else min(min_days_left, dleft)
        dt = _parse_panel_datetime(pu.get("last_online"))
        if dt and (latest_last_online is None or dt > latest_last_online):
            latest_last_online = dt

    server_id = int(primary_server.get("id") or 0)
    server_title = str(primary_server.get("title") or f"سرور #{server_id}").strip()
    service_code = _generate_service_code()
    service_comment = f"uuid:{parsed_uuid}|code:{service_code}|linked:1|source:connect"
    service_db_id = None

    try:
        with userbot_db._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO userbot_services
                (user_id, name, server_id, server_title, usage_current, usage_limit, days_left, last_online, comment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(internal_user_id),
                    service_name,
                    int(server_id),
                    server_title,
                    float(total_usage),
                    float(usage_limit),
                    int(min_days_left if min_days_left is not None else 0),
                    (latest_last_online.strftime("%Y-%m-%d %H:%M:%S") if latest_last_online else None),
                    service_comment,
                ),
            )
            service_db_id = int(cur.lastrowid)
    except Exception as e:
        logger.exception("Failed persisting connected subscription (telegram_id=%s)", user_id)
        await msg_obj.reply_text(
            f"❌ اتصال اشتراک با خطا مواجه شد: {e}",
            reply_markup=_main_menu_keyboard(),
        )
        set_user_step(context, user_id, None)
        return True

    for srv, pu in targets:
        try:
            sid = int(srv.get("id") or 0)
            if sid <= 0:
                continue
            userbot_db.add_service_node(
                service_id=int(service_db_id),
                server_id=sid,
                panel_user_uuid=parsed_uuid,
                server_title=str(srv.get("title") or ""),
                panel_user_id=(str(pu.get("id")).strip() if pu.get("id") is not None else None),
                is_active=1,
            )
        except Exception:
            pass

    set_user_step(context, user_id, None)
    service = userbot_db.get_service_by_id(int(service_db_id)) or {}
    service = await _sync_service_runtime_from_panels(service)
    settings = _get_subscription_settings()
    await msg_obj.reply_text(
        "✅ اشتراک شما با موفقیت متصل شد.",
        reply_markup=_main_menu_keyboard(),
    )
    await msg_obj.reply_text(
        _build_subscription_status_text(service),
        parse_mode="Markdown",
        reply_markup=subscription_status_keyboard(
            service.get("id"),
            show_direct_config=settings.get("show_direct_config", True),
            show_sub_link=settings.get("show_sub_link", True),
            show_configs=_should_show_configs_button(settings),
            show_detach=_is_connected_service(service),
        ),
    )
    return True


# --- 5. هندلر دستور استارت ---
async def _render_invite_home_text(context: ContextTypes.DEFAULT_TYPE, internal_user_id: int) -> str:
    """متن صفحه اصلی «دعوت دوستان» با آمار کاربر."""
    try:
        settings = userbot_db.get_referral_settings()
    except Exception:
        settings = {}
    try:
        stats = userbot_db.get_referral_user_stats(int(internal_user_id or 0))
    except Exception:
        stats = {}
    bot_username = await _get_user_bot_username(context)
    code = ""
    try:
        code = userbot_db.get_or_create_user_referral_code(int(internal_user_id or 0))
    except Exception:
        code = ""
    invite_link = f"https://t.me/{bot_username}?start=ref_{code}" if bot_username and code else ""

    def _toman(v: Any) -> str:
        try:
            return f"{int(v or 0):,}"
        except Exception:
            return "0"

    trial_amount = _toman(settings.get("trial_reward_amount"))
    purchase_amount = _toman(settings.get("purchase_reward_amount"))
    enabled = bool(settings.get("referral_enabled", False))

    ref_settings_text = userbot_db.DEFAULT_REFERRAL_SETTINGS["invite_intro_text"]
    try:
        intro = str(settings.get("invite_intro_text") or ref_settings_text)
        intro = intro.format(
            invite_link=invite_link or "—",
            trial_reward=f"{trial_amount} تومان",
            purchase_reward=f"{purchase_amount} تومان",
        )
    except Exception:
        intro = (
            "🎁 دوستان خود را دعوت کنید و از هر دعوت پاداش بگیرید.\n\n"
            f"🔗 لینک دعوت:\n{invite_link or '—'}"
        )

    if not enabled:
        return "🎁 سیستم دعوت دوستان به‌طور موقت غیرفعال است."

    total_referrals = int(stats.get("total_referrals") or 0)
    successful = int(stats.get("successful_referrals") or 0)
    pending = int(stats.get("pending_purchase") or 0)
    total_rewards = int(stats.get("total_rewards") or 0)
    paid_count = int(stats.get("paid_rewards_count") or 0)

    stats_text = (
        f"👥 کل دعوت‌ها: {total_referrals}\n"
        f"✅ دعوت‌های موفق: {successful}\n"
        f"⏳ در انتظار خرید: {pending}\n"
        f"🎁 مجموع پاداش‌ها: {total_rewards:,} تومان\n"
        f"🧾 تعداد جوایز دریافتی: {paid_count}"
    )

    return f"{intro}\n\n❖ ◈━━━━━━━━━━━━━━━◈ ❖\n{stats_text}"


def _handle_referral_start_payload(payload: str, internal_user_id: int) -> bool:
    """
    If the /start deep-link payload is a referral code, register the referral.
    Returns True when the payload was consumed as a referral (registered or the
    user already has an inviter). Returns False when the payload should fall
    through to the rest of the /start flow (e.g. a coupon code that merely
    looks like a referral payload but belongs to no user).
    """
    ref_code = userbot_db.normalize_referral_payload(payload)
    if not ref_code:
        return False
    try:
        settings = userbot_db.get_referral_settings()
        if not bool(settings.get("referral_enabled", False)):
            return False
    except Exception:
        return False
    try:
        if not userbot_db.get_user_by_referral_code(ref_code):
            # Such an invitation code does not exist; let the payload flow on
            # (could be a coupon code), and do not show any referral error.
            return False
        created, status, _ref_id = userbot_db.register_referral(int(internal_user_id or 0), ref_code, payload)
        # سکوت در هر حالت: ثبت جدید، self-referral یا already-referred پیامی ندارد.
        if created or status in {"already_referred", "self_referral"}:
            return True
        return False
    except Exception as e:
        logger.warning(f"Failed to register referral invitee={internal_user_id}: {e}")
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    user_id = int(user.id)
    if _should_skip_stale_startup_update(update, context, user_id):
        return
    event_ts = None
    try:
        if update.message and update.message.date:
            event_ts = update.message.date.timestamp()
        elif update.callback_query and update.callback_query.message and update.callback_query.message.date:
            event_ts = update.callback_query.message.date.timestamp()
    except Exception:
        event_ts = None

    # Anti-spam for /start command (same behavior pattern as menu/callback handlers)
    limited, wait_s = _check_action_rate_limit(
        context,
        user_id,
        "cmd:start",
        cooldown=0.5,
        event_ts=event_ts,
    )
    if limited:
        notice_key = f"_rl_notice_cmd_start_{user_id}"
        now_ts = time.time()
        last_notice = float(context.user_data.get(notice_key) or 0.0)
        if now_ts - last_notice >= USERBOT_RATE_LIMIT_NOTICE_SECONDS:
            context.user_data[notice_key] = now_ts
            if update.callback_query:
                try:
                    await update.callback_query.answer(
                        f"⏳ لطفا {max(1, int(wait_s + 0.99))} ثانیه صبر کنید."
                    )
                except Exception:
                    pass
            msg_obj = update.callback_query.message if update.callback_query else update.message
            if msg_obj:
                await msg_obj.reply_text(
                    f"⏳ لطفا {max(1, int(wait_s + 0.99))} ثانیه صبر کنید.",
                    reply_markup=_main_menu_keyboard(),
                )
        elif update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
        return

    internal_user_id = userbot_db.upsert_user(user.id, user.username, user.full_name)
    msg_obj = update.callback_query.message if update.callback_query else update.message
    if not msg_obj:
        return
    allowed = await _enforce_force_join(
        context=context,
        user_id=int(user.id),
        send_text=msg_obj.reply_text,
    )
    if not allowed:
        return

    start_payload = _extract_start_payload(update)
    referral_consumed = False
    if start_payload:
        shot_handled = await _handle_user_ticket_shot_start(
            update=update,
            context=context,
            payload=start_payload,
            user_id=user_id,
            internal_user_id=int(internal_user_id),
        )
        if shot_handled:
            return

        parsed_uuid = _extract_uuid_from_user_input(start_payload)
        if parsed_uuid:
            connect_handled = await _connect_panel_subscription_by_uuid(
                update,
                context,
                parsed_uuid,
                int(internal_user_id),
            )
            if connect_handled:
                return

        referral_consumed = _handle_referral_start_payload(start_payload, int(internal_user_id))
    text_settings = _get_text_settings()
    _u_lang = _user_lang(user_id)
    welcome_text = (
        text_settings.get("welcome_message")
        or i18n.t("welcome", _u_lang, full_name=user.full_name)
    )
    formatted_text = _format_text_template(
        welcome_text,
        full_name=user.full_name,
        username=f"@{user.username}" if user.username else "",
        id=user.id,
    )

    if update.callback_query:
        await update.callback_query.message.reply_text(formatted_text, reply_markup=_main_menu_keyboard(lang=_u_lang))
    else:
        await update.message.reply_text(formatted_text, reply_markup=_main_menu_keyboard(lang=_u_lang))

    if start_payload:
        # referral codes must never be treated as a coupon code
        if referral_consumed:
            return
        try:
            ok, result_text, amount = userbot_db.redeem_zarin_voucher(start_payload, int(internal_user_id))
        except Exception as e:
            logger.warning(f"Failed to redeem zarin voucher payload={start_payload}: {e}")
            return
        if ok:
            u_db = userbot_db.get_user_by_id(int(internal_user_id)) or {}
            balance = int(u_db.get("wallet_balance") or 0)
            await msg_obj.reply_text(
                f"🎉 موجودی کیف پول شما افزایش یافت.\n\n"
                f"🪄 مبلغ هدیه: {int(amount):,} تومان\n"
                f"💰 موجودی جدید کیف پول: {balance:,} تومان"
            )
        else:
            await msg_obj.reply_text(f"⚠️ {result_text}")

# --- 6. هندلر منوی اصلی (دکمه‌های پایین صفحه) ---
async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور /language — تغییر زبان رابط کاربری هر کاربر."""
    user_id = update.effective_user.id if update.effective_user else 0
    if not user_id:
        return
    _lg = i18n.get_user_lang(user_id)
    await update.message.reply_text(
        i18n.t("lang_choose", _lg),
        reply_markup=language_keyboard(),
    )


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id
    if _should_skip_stale_startup_update(update, context, user_id):
        return

    # --- تغییر زبان رابط کاربری (چندزبانه) ---
    lang_key = i18n.resolve_button(text, ("lang_btn",))
    if lang_key == "lang_btn":
        await update.message.reply_text(
            i18n.t("lang_choose", i18n.get_user_lang(user_id)),
            reply_markup=language_keyboard(),
        )
        return

    # نگاشت دکمه فارسی/انگلیسی/روسی به کلید — کلید معادل همیشه به text تزریق می‌شود
    menu_key = i18n.resolve_button(text, UserBot_keyboards.MENU_BTN_KEYS)
    if menu_key:
        text = {
            "menu_status": "📊وضعیت اشتراک", "menu_renew": "♾تمدید اشتراک",
            "menu_buy": "💳خرید اشتراک", "menu_connect": "🔗اتصال اشتراک",
            "menu_trial": "🔥تست رایگان", "menu_wallet": "💰کیف پول",
            "menu_support": "📩پشتیبانی", "menu_guide": "📚راهنما",
            "menu_faq": "❗️سوالات متداول", "menu_invite": "💌دعوت دوستان",
            "btn_pay_done": "✅ پرداخت کردم، ارسال رسید", "btn_back": "بازگشت",
            "btn_cancel": "لغو",
        }.get(menu_key, text)

    # اگر کاربر وسط یک مرحله انتظار ورود متن است (مثلاً نام تست رایگان)،
    # حتی اگر متن شبیه دکمهٔ منو باشد، باید به هندلر مرحله برود نه به منو.
    current_step = get_user_step(context, user_id)
    if current_step:
        step_lower = str(current_step).lower()
        wait_keywords = ("trial", "name", "rename", "ticket", "receipt", "photo", "card", "amount", "wait")
        if any(k in step_lower for k in wait_keywords):
            from UserBot import main as _ubmod
            await _ubmod.receipt_handler(update, context)
            return

    normalized_text = _normalize_action_text(text)
    event_ts = None
    try:
        if update.message and update.message.date:
            event_ts = update.message.date.timestamp()
    except Exception:
        event_ts = None

    # Anti-spam for all menu buttons.
    # خرید اشتراک نباید پیام «لطفاً چند ثانیه صبر کنید» نشان بدهد؛
    # جلوگیری از تکرار خرید پایین‌تر با buy_loading/buy_open انجام می‌شود.
    is_buy_menu_action = "خرید اشتراک" in normalized_text
    limited, wait_s = False, 0.0
    if not is_buy_menu_action:
        if "وضعیت اشتراک" in normalized_text:
            menu_cd = 0.5
        else:
            menu_cd = USERBOT_ACTION_COOLDOWN_SECONDS
        limited, wait_s = _check_action_rate_limit(
            context,
            user_id,
            f"menu:{normalized_text}",
            cooldown=menu_cd,
            event_ts=event_ts,
        )
    if limited:
        # از ارسال پیام ضداسپم به‌صورت پشت‌سرهم جلوگیری می‌کنیم.
        notice_key = f"_rl_notice_menu_{user_id}_{normalized_text}"
        now_ts = time.time()
        last_notice = float(context.user_data.get(notice_key) or 0.0)
        if now_ts - last_notice >= USERBOT_RATE_LIMIT_NOTICE_SECONDS:
            context.user_data[notice_key] = now_ts
            await update.message.reply_text(
                f"⏳ لطفا {max(1, int(wait_s + 0.99))} ثانیه صبر کنید.",
                reply_markup=_main_menu_keyboard(),
            )
        return

    allowed = await _enforce_force_join(
        context=context,
        user_id=int(user_id),
        send_text=update.message.reply_text,
    )
    if not allowed:
        return

    text_settings = _get_text_settings()

    if "خرید اشتراک" in text:
        context.user_data.pop(f"renew_target_{user_id}", None)
        br = _get_buy_renew_settings()
        if not bool(br.get("enable_buy", True)):
            _lg = _user_lang(user_id)
            await update.message.reply_text(
                i18n.t("buy_disabled", _lg),
                reply_markup=_main_menu_keyboard(lang=_lg),
            )
            return
        buy_open_key = f"buy_menu_open_until_{user_id}"
        now_ts = time.time()
        open_until = float(context.user_data.get(buy_open_key) or 0.0)
        if now_ts < open_until:
            # منوی خرید قبلاً باز شده؛ از ارسال تکراری جلوگیری می‌کنیم.
            return

        buy_loading_key = f"buy_loading_{user_id}"
        if context.user_data.get(buy_loading_key):
            return
        context.user_data[buy_loading_key] = True
        try:
            servers = _get_location_servers()
            server_columns = int(br.get("server_columns") or 1)
            await update.message.reply_text(
                text_settings.get("servers_list_text") or "📡 **لیست سرورها**\nلطفاً لوکیشن مورد نظر خود را انتخاب کنید:",
                parse_mode="Markdown",
                reply_markup=location_keyboard(servers, columns=server_columns)
            )
            context.user_data[buy_open_key] = now_ts + BUY_MENU_HOLD_SECONDS
        finally:
            context.user_data.pop(buy_loading_key, None)

    elif "تست رایگان" in text:
        u_db = userbot_db.get_user_by_telegram_id(user_id)
        if not u_db:
            internal_user_id = userbot_db.upsert_user(
                update.effective_user.id,
                update.effective_user.username,
                update.effective_user.full_name,
            )
            u_db = userbot_db.get_user_by_id(internal_user_id) or {}

        _lg = _user_lang(user_id)
        if int(u_db.get("got_free_trial") or 0) == 1:
            await update.message.reply_text(
                i18n.t("trial_already_msg", _lg),
                reply_markup=_main_menu_keyboard(lang=_lg),
            )
            return

        trial_settings = userbot_db.get_trial_spec_settings()
        if not bool(trial_settings.get("enabled", True)):
            await update.message.reply_text(
                i18n.t("trial_disabled_msg", _lg),
                reply_markup=_main_menu_keyboard(lang=_lg),
            )
            return

        servers = _get_location_servers()
        if not servers:
            await update.message.reply_text(
                "❌ سروری برای ارائه تست رایگان در دسترس نیست.",
                reply_markup=_main_menu_keyboard(),
            )
            return

        await update.message.reply_text(
            text_settings.get("servers_list_text") or "📡 **لیست سرورها**\nلطفاً لوکیشن مورد نظر خود را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=trial_location_keyboard(servers),
        )

    elif "تمدید اشتراک" in text:
        context.user_data.pop(f"renew_target_{user_id}", None)
        br = _get_buy_renew_settings()
        if not bool(br.get("enable_renew", True)):
            await update.message.reply_text("🚫 تمدید اشتراک در حال حاضر غیرفعال است.", reply_markup=_main_menu_keyboard())
            return
        u_db = userbot_db.get_user_by_telegram_id(user_id)
        if not u_db:
            await update.message.reply_text("❌ کاربر یافت نشد.", reply_markup=_main_menu_keyboard())
            return

        internal_user_id = u_db.get("id")
        services = userbot_db.get_services_for_user(internal_user_id)
        if not services:
            await update.message.reply_text("❌ اشتراک وجود ندارد.", reply_markup=_main_menu_keyboard())
            return

        services, _ = await _filter_existing_services(services)
        visible_services = [s for s in services if int(s.get("days_left") or 0) > -30]
        if not visible_services:
            await update.message.reply_text("❌ اشتراک وجود ندارد.", reply_markup=_main_menu_keyboard())
            return

        # قوانین تمدید (حالت پیشرفته): فقط سرویس‌های نزدیک به اتمام حجم/زمان
        renewable_services = []
        for _s in visible_services:
            if await _service_is_renewable_live(_s):
                renewable_services.append(_s)
        if not renewable_services:
            await update.message.reply_text(_renew_not_allowed_text(), reply_markup=_main_menu_keyboard())
            return

        _lg = _user_lang(user_id)
        await update.message.reply_text(
            i18n.t("renew_choose", _lg),
            reply_markup=renew_services_keyboard(renewable_services),
        )

    elif "کیف پول" in text:
        u_db = userbot_db.get_user_by_telegram_id(user_id)
        balance = u_db['wallet_balance'] if u_db else 0
        mkt = _get_marketing_settings()
        pay_settings = _get_payment_settings()
        can_use_coupon = (
            bool(mkt.get("enable_discount_code", False))
            or bool(mkt.get("enable_increase_code", False))
        )
        status_line = ""
        if bool(mkt.get("show_user_status", True)):
            is_banned = int((u_db or {}).get("is_banned") or 0)
            status_line = f"\n👤 وضعیت کاربر: {'🔴 مسدود' if is_banned else '🟢 فعال'}"
        if not any(
            [
                bool(pay_settings.get("enable_card_to_card", True)),
                bool(pay_settings.get("enable_zarinpal", False)),
                bool(pay_settings.get("enable_perfect_money", False)),
                bool(pay_settings.get("enable_crypto", False)),
            ]
        ):
            _lg = _user_lang(user_id)
            await update.message.reply_text(
                i18n.t("wallet_title", _lg, b=balance) + status_line + "\n\n" + i18n.t("wallet_no_method", _lg),
                reply_markup=wallet_inline_keyboard(show_coupon=can_use_coupon, show_card=False),
            )
            return
        _lg = _user_lang(user_id)
        await update.message.reply_text(
            i18n.t("wallet_title", _lg, b=balance) + status_line,
            reply_markup=wallet_inline_keyboard(
                show_coupon=can_use_coupon,
                show_card=bool(pay_settings.get("enable_card_to_card", True)),
                show_zarinpal=bool(pay_settings.get("enable_zarinpal", False)),
                show_perfect_money=bool(pay_settings.get("enable_perfect_money", False)),
                show_crypto=bool(pay_settings.get("enable_crypto", False)),
            ),
        )
        
    elif "وضعیت اشتراک" in text:
        loading_key = f"status_loading_{user_id}"
        if context.user_data.get(loading_key):
            await update.message.reply_text(
                i18n.t("status_loading", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(),
            )
            return

        context.user_data[loading_key] = True
        loading_msg = None
        try:
            loading_msg = await update.message.reply_text("⏳ لطفا صبر کنید...")

            # دریافت اطلاعات کاربر
            u_db = userbot_db.get_user_by_telegram_id(user_id)
            if not u_db:
                await update.message.reply_text("❌ کاربر یافت نشد.", reply_markup=_main_menu_keyboard())
                return

            internal_user_id = u_db.get('id')

            # دریافت تمام سرویس‌های کاربر
            services = userbot_db.get_services_for_user(internal_user_id)

            if not services:
                await update.message.reply_text(
                    "❌ هیچ سرویس فعال ندارید.\n\n💡 برای خرید سرویس جدید، روی دکمه «💳خرید اشتراک» کلیک کنید.",
                    reply_markup=_main_menu_keyboard()
                )
                return

            # سرویس‌هایی که روی پنل حذف شده‌اند نمایش داده نشوند.
            services, removed_count = await _filter_existing_services(services)

            # قبل از نمایش وضعیت، runtime سرویس‌ها از پنل همگام شود
            # تا تغییرات دستی حجم/زمان در پنل سریعاً داخل ربات کاربر دیده شود.
            synced_services = await _gather_with_limit(
                services,
                _sync_service_runtime_from_panels,
                limit=USERBOT_STATUS_SYNC_CONCURRENCY,
            )

            # نمایش سرویس‌های فعال و منقضی (تا ۳۰ روز قبل)
            visible_services = [s for s in synced_services if _to_int(s.get("days_left"), 0) > -30]

            if not visible_services:
                await update.message.reply_text(
                    "❌ اشتراک وجود ندارد.",
                    reply_markup=_main_menu_keyboard()
                )
                return

            settings = _get_subscription_settings()
            if len(visible_services) > 3:
                await update.message.reply_text(
                    "👇 لطفا یکی از اشتراک‌های خود را انتخاب نمایید",
                    reply_markup=services_list_keyboard(visible_services),
                )
                if removed_count > 0:
                    await update.message.reply_text(
                        f"🧹 {removed_count} سرویس حذف‌شده از پنل، از لیست ربات پاک شد.",
                        reply_markup=_main_menu_keyboard(),
                    )
                return

            for service in visible_services:
                msg = _build_subscription_status_text(service)
                await update.message.reply_text(
                    msg,
                    parse_mode="Markdown",
                    reply_markup=subscription_status_keyboard(
                        service.get("id"),
                        show_direct_config=settings.get("show_direct_config", True),
                        show_sub_link=settings.get("show_sub_link", True),
                        show_configs=_should_show_configs_button(settings),
                        show_detach=_is_connected_service(service),
                    )
                )
            if removed_count > 0:
                await update.message.reply_text(
                    f"🧹 {removed_count} سرویس حذف‌شده از پنل، از لیست ربات پاک شد.",
                    reply_markup=_main_menu_keyboard(),
                )
        finally:
            context.user_data.pop(loading_key, None)
            if loading_msg:
                try:
                    await loading_msg.delete()
                except Exception:
                    pass

    elif "اتصال اشتراک" in text:
        _lg = _user_lang(user_id)
        set_user_step(context, user_id, "WAIT_CONNECT_SUB_INPUT")
        await update.message.reply_text(
            i18n.t("connect_prompt", _lg),
            reply_markup=cancel_keyboard(lang=_lg),
        )

    elif "پشتیبانی" in text:
        _lg = _user_lang(user_id)
        await update.message.reply_text(
            text_settings.get("ticket_panel_text") or i18n.t("support_panel_msg", _lg),
            reply_markup=support_panel_keyboard(lang=_lg),
        )

    elif "راهنما" in text:
        await update.message.reply_text(
            text_settings.get("guide_text") or _default_guide_intro_text(),
            reply_markup=guide_os_keyboard("m"),
        )

    elif "سوالات متداول" in text:
        _lg = _user_lang(user_id)
        faq_text = str(text_settings.get("faq_text") or "").strip()
        if (not faq_text) or ("به‌زودی تکمیل می‌شود" in faq_text) or ("به زودی تکمیل می شود" in faq_text):
            faq_text = i18n.t("faq_default_full", _lg)
        await update.message.reply_text(
            faq_text,
            reply_markup=_main_menu_keyboard(lang=_lg),
        )

    elif "دعوت دوستان" in text:
        u_db = userbot_db.get_user_by_telegram_id(user_id) or {}
        internal_uid = int(u_db.get("id") or 0)
        invite_text = await _render_invite_home_text(context, internal_uid)
        await update.message.reply_text(
            invite_text,
            reply_markup=invite_banner_keyboard(),
        )

# --- 7. هندلر اینلاین (عملیات خرید) ---
async def inline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    if _should_skip_stale_startup_update(update, context, user_id):
        try:
            await query.answer()
        except Exception:
            pass
        return

    # --- تغییر زبان رابط کاربری (چندزبانه) ---
    if data.startswith("lang:set:"):
        await _cb_lang_set(update, context, query, data, user_id)
        return
    br = _get_buy_renew_settings()
    text_settings = _get_text_settings()

    # Anti-spam for inline callbacks
    cb_cd = USERBOT_ACTION_COOLDOWN_SECONDS
    if data.startswith("status:"):
        cb_cd = 0.5
    elif data.startswith(("buy:", "wiz:")):
        cb_cd = BUY_CALLBACK_COOLDOWN_SECONDS
    limited, wait_s = _check_action_rate_limit(
        context,
        user_id,
        f"cb:{data}",
        cooldown=cb_cd,
    )
    if limited:
        try:
            notice_key = f"_rl_notice_cb_{user_id}_{data}"
            now_ts = time.time()
            last_notice = float(context.user_data.get(notice_key) or 0.0)
            if now_ts - last_notice >= USERBOT_RATE_LIMIT_NOTICE_SECONDS:
                context.user_data[notice_key] = now_ts
                await query.answer(f"⏳ لطفا {max(1, int(wait_s + 0.99))} ثانیه صبر کنید.")
            else:
                await query.answer()
        except Exception:
            pass
        return

    if data.startswith("forcejoin:"):
        await query.answer()
        action = data.split(":", 1)[1].strip().lower() if ":" in data else ""
        if action == "check":
            settings = _get_force_join_settings()
            if not bool(settings.get("enabled", False)):
                await context.bot.send_message(chat_id=user_id, text="✅ عضویت اجباری غیرفعال است.", reply_markup=_main_menu_keyboard())
                return
            if await _user_joined_force_channel(context, user_id, settings):
                await context.bot.send_message(chat_id=user_id, text="✅ عضویت شما تایید شد.", reply_markup=_main_menu_keyboard())
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=str(settings.get("guide_text") or "🔒 لطفاً ابتدا در کانال عضو شوید."),
                    reply_markup=force_join_keyboard(_force_join_url(settings)),
                )
        return

    # روی سایر callback ها، ابتدا عضویت اجباری بررسی شود.
    settings_fj = _get_force_join_settings()
    if bool(settings_fj.get("enabled", False)):
        if not await _user_joined_force_channel(context, user_id, settings_fj):
            try:
                await query.answer("ابتدا در کانال عضو شوید.", show_alert=True)
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=user_id,
                text=str(settings_fj.get("guide_text") or "🔒 لطفاً ابتدا در کانال عضو شوید."),
                reply_markup=force_join_keyboard(_force_join_url(settings_fj)),
            )
            return

    # --- راهنما (انتخاب سیستم‌عامل) ---
    if data.startswith("guide:"):
        await _cb_guide(update, context, query, data, user_id, text_settings)
        return

    if data.startswith("invite:"):
        await _cb_invite(update, context, query, data, user_id, text_settings)
        return

    if data.startswith("support:"):
        await _cb_support(update, context, query, data, user_id, text_settings)
        return

    # --- وضعیت اشتراک ---
    if data.startswith("status:"):
        await _cb_status(update, context, query, data, user_id, br, text_settings)
        return

    if data.startswith("renew:"):
        await _cb_renew(update, context, query, data, user_id)
        return

    # --- کیف پول (Inline) ---
    if data.startswith("wallet:"):
        await _cb_wallet(update, context, query, data, user_id)
        return

    # --- پرداخت کارت به کارت (Inline) ---
    if data.startswith("pay:"):
        await _cb_pay(update, context, query, data, user_id)
        return

    # --- تست رایگان ---
    if data == "trial:back":
        await _cb_trial_back(update, context, query, data, user_id)
        return

    if data.startswith("trial:loc:"):
        await _cb_trial_loc(update, context, query, data, user_id)
        return
    
    # بازگشت به لیست سرورها (از صفحات داخلی خرید)
    if data == "buy:back_main":
        await _cb_buy_back_main(update, context, query, data, user_id, br, text_settings)
        return

    # خروج از خودِ لیست سرورها به منوی اصلی
    if data == "buy:exit_main":
        await _cb_buy_exit_main(update, context, query, data, user_id)
        return

    # انتخاب لوکیشن -> هدایت بر اساس حالت نمایش سرور
    if data.startswith("buy:loc:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer("🚫 خرید غیرفعال است.", show_alert=True)
            return
        context.user_data.pop(f"buy_menu_open_until_{user_id}", None)
        sid = int(data.split(":")[2])
        data_plans = plans_storage._load_all_plans()
        server_block = data_plans.get("servers", {}).get(str(sid), {})
        
        display_mode = _resolve_plan_display_mode(server_block)
        
        if display_mode == "mixed":
            # نمایش صفحه اصلی خرید با ویزارد و دکمه پلن‌های آماده
            await show_main_buy_menu(query, sid, server_block, user_id, context)
        elif display_mode == "dynamic":
            await start_dynamic_wizard(query, context, sid, user_id, server_block)
        else:
            await show_fixed_categories(query, sid, server_block)

    # انتخاب‌های حالت ترکیبی
    elif data.startswith("buy:mixed:fixed:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer("🚫 خرید غیرفعال است.", show_alert=True)
            return
        sid = int(data.split(":")[3])
        server_block = plans_storage._load_all_plans().get("servers", {}).get(str(sid), {})
        await show_fixed_categories(query, sid, server_block)

    elif data.startswith("buy:mixed:dyn:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer("🚫 خرید غیرفعال است.", show_alert=True)
            return
        sid = int(data.split(":")[3])
        server_block = plans_storage._load_all_plans().get("servers", {}).get(str(sid), {})
        await start_dynamic_wizard(query, context, sid, user_id, server_block)

    # دسته‌بندی و پلن‌های ثابت
    elif data.startswith("buy:cat:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer("🚫 خرید غیرفعال است.", show_alert=True)
            return
        parts = data.split(":")
        sid, cat_id = int(parts[2]), int(parts[3])
        server_block = plans_storage._load_all_plans().get("servers", {}).get(str(sid), {})
        plans = [p for p in server_block.get("plans", []) if p.get("category_id") == cat_id]
        txp = _get_tx_plans_settings()
        plans = _sort_plans(plans, txp)
        plan_columns = int(br.get("plan_columns") or 1)
        uv = bool(br.get("renew_unlimited_volume", False))
        ut = bool(br.get("renew_unlimited_time", False))
        uv_from = int(br.get("renew_unlimited_volume_from_gb") or 1000)
        ut_from = int(br.get("renew_unlimited_time_from_days") or 365)
        
        await _safe_edit_message_text(
            query,
            text_settings.get("plans_list_text") or "🛒 **لطفاً پلن مورد نظر خود را انتخاب کنید:**", parse_mode="Markdown",
            reply_markup=plans_keyboard(
                plans,
                sid,
                cat_id,
                columns=plan_columns,
                unlimited_volume=uv,
                unlimited_volume_from=uv_from,
                unlimited_time=ut,
                unlimited_time_from=ut_from,
                sort_by_priority=False,
                rtl_rows=bool(txp.get("plan_sort_desc", False)),
            )
        )

    elif data.startswith("buy:plan:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer("🚫 خرید غیرفعال است.", show_alert=True)
            return
        parts = data.split(":")
        sid, plan_id = int(parts[2]), int(parts[3])
        server_block = plans_storage._load_all_plans().get("servers", {}).get(str(sid), {})
        plan = next((p for p in server_block.get("plans", []) if p.get("id") == plan_id), None)
        
        if not plan: return
        # نمایش اطلاعات پلن انتخاب شده (طبق اسکرین‌شات)
        plan_gb = float(plan["gb"])
        plan_days = int(plan["days"])
        plan_gb_text = "نامحدود" if _is_unlimited_volume(plan_gb) else f"{plan_gb:g} گیگ"
        plan_days_text = "نامحدود" if _is_unlimited_time(plan_days) else f"{plan_days} روز"
        text = (
            "📄 اطلاعات پلن انتخاب شده\n\n"
            f"📊 حجم: {plan_gb_text}\n"
            f"⏳ زمان: {plan_days_text}\n"
            f"💰 قیمت: {plan['price']:,} تومان"
        )
        await _safe_edit_message_text(
            query,
            text,
            reply_markup=selected_plan_keyboard(sid, int(plan['gb']), int(plan['days']), int(plan['price']), plan_id=int(plan.get('id') or 0))
        )

    # ویزارد پویا (دکمه‌های مثبت و منفی)
    elif data.startswith("wiz:"):
        async with _USER_WIZARD_LOCKS[user_id]:
            parts = data.split(":")
            sid, action = int(parts[1]), parts[2]

            wiz_data = context.user_data.get(f"wiz_{user_id}")
            if not wiz_data:
                data_plans = plans_storage._load_all_plans()
                server_block = data_plans.get("servers", {}).get(str(sid), {})
                dyn_settings = server_block.get("dynamic_settings", {})
                default_gb = dyn_settings.get("min_gb", 20)
                default_months = dyn_settings.get("min_month", 1)
                wiz_data = {"gb": default_gb, "months": default_months}
                context.user_data[f"wiz_{user_id}"] = wiz_data

            data_plans = plans_storage._load_all_plans()
            server_block = data_plans.get("servers", {}).get(str(sid), {})
            dyn_settings = server_block.get("dynamic_settings", {})
            display_mode = _resolve_plan_display_mode(server_block)
            gb, months = wiz_data['gb'], wiz_data['months']

            min_gb = max(1, int(dyn_settings.get('min_gb', 10) or 10))
            max_gb = max(min_gb, int(dyn_settings.get('max_gb', 500) or 500))
            min_month = max(1, int(dyn_settings.get('min_month', 1) or 1))
            max_month = max(min_month, int(dyn_settings.get('max_month', 12) or 12))
            step_gb = max(1, int(dyn_settings.get('step_gb', 10) or 10))
            step_month = max(1, int(dyn_settings.get('step_month', 1) or 1))

            if action == "gb_inc":
                if gb >= max_gb:
                    await query.answer(f"حداکثر حجم {max_gb} گیگابایت می‌باشد.", show_alert=True)
                    return
                gb = min(max_gb, gb + step_gb)
            elif action == "gb_dec":
                if gb <= min_gb:
                    await query.answer(f"حداقل حجم {min_gb} گیگابایت می‌باشد.", show_alert=True)
                    return
                gb = max(min_gb, gb - step_gb)
            elif action == "month_inc":
                if months >= max_month:
                    await query.answer(f"حداکثر دوره {max_month} ماه می‌باشد.", show_alert=True)
                    return
                months = min(max_month, months + step_month)
            elif action == "month_dec":
                if months <= min_month:
                    await query.answer(f"حداقل دوره {min_month} ماه می‌باشد.", show_alert=True)
                    return
                months = max(min_month, months - step_month)
            elif action == "show_fixed":
                if display_mode != "mixed":
                    await query.answer("این گزینه فقط در حالت ترکیبی فعال است.", show_alert=True)
                    return
                plans = server_block.get("plans", [])
                if not plans:
                    await query.answer("❌ پلن آماده‌ای برای این سرور وجود ندارد.", show_alert=True)
                    return
                txp = _get_tx_plans_settings()
                ordered = _sort_plans(plans, txp)
                plan_columns = int(br.get("plan_columns") or 1)
                uv = bool(br.get("renew_unlimited_volume", False))
                ut = bool(br.get("renew_unlimited_time", False))
                uv_from = int(br.get("renew_unlimited_volume_from_gb") or 1000)
                ut_from = int(br.get("renew_unlimited_time_from_days") or 365)
                await _safe_edit_message_text(
                    query,
                    text_settings.get("plans_list_text") or "🛒 **لطفاً پلن مورد نظر خود را انتخاب کنید:**",
                    parse_mode="Markdown",
                    reply_markup=plans_keyboard(
                        ordered,
                        sid,
                        0,
                        columns=plan_columns,
                        unlimited_volume=uv,
                        unlimited_volume_from=uv_from,
                        unlimited_time=ut,
                        unlimited_time_from=ut_from,
                        sort_by_priority=False,
                        back_to_categories=False,
                        rtl_rows=bool(txp.get("plan_sort_desc", False)),
                    ),
                )
                return

            wiz_data['gb'], wiz_data['months'] = gb, months
            context.user_data[f"wiz_{user_id}"] = wiz_data

            price, off_percent = _calc_dynamic_price(gb, months, dyn_settings)
            if display_mode == "mixed":
                markup = mixed_buy_keyboard(sid, gb, months, price, off_percent=off_percent)
            else:
                markup = buy_wizard_keyboard(sid, gb, months, price, off_percent=off_percent)
            await _safe_edit_message_reply_markup(query, reply_markup=markup)

    # تایید نهایی و هدایت به پرداخت
    elif data.startswith("buy:confirm_dyn:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer("🚫 خرید غیرفعال است.", show_alert=True)
            return
        # نمایش اطلاعات پلن انتخاب شده بعد از خرید بسته دلخواه
        parts = data.split(":")
        sid = int(parts[2])
        wiz_data = context.user_data.get(f"wiz_{user_id}")
        if not wiz_data:
            await query.answer("❌ زمان نشست تمام شده، لطفا دوباره تلاش کنید.", show_alert=True)
            return
        gb = int(wiz_data.get('gb') or 0)
        days = int(wiz_data.get('months') or 0) * 30
        dyn_settings = plans_storage._load_all_plans().get("servers", {}).get(str(sid), {}).get("dynamic_settings", {})
        price, off_percent = _calc_dynamic_price(gb, wiz_data.get("months"), dyn_settings)

        text = (
            "📄 اطلاعات پلن انتخاب شده\n\n"
            f"📊 حجم: {gb} گیگ\n"
            f"⏳ زمان: {days} روز\n"
            f"💰 قیمت: {price:,} تومان"
        )
        if off_percent > 0:
            text += f"\n🏷 تخفیف حجمی: {off_percent}٪"
        await _safe_edit_message_text(query, text, reply_markup=selected_plan_keyboard(sid, gb, days, price))
        return

    elif data.startswith("buy:pay_wallet:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer("🚫 خرید غیرفعال است.", show_alert=True)
            return
        # پرداخت از کیف پول (طبق اسکرین‌شات)
        await query.answer()
        parts = data.split(":")
        sid = int(parts[2])
        gb = int(parts[3])
        days = int(parts[4])
        price = int(parts[5])
        plan_id = int(parts[6]) if len(parts) > 6 else 0
        # قیمت همیشه سمت سرور دوباره محاسبه می‌شود — ضد دستکاری callback data
        expected_price = _expected_server_price(sid, gb, days, plan_id)
        if expected_price is None:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ اطلاعات این دکمه منقضی یا نامعتبر است. لطفاً خرید را از نو انجام دهید.",
                reply_markup=_main_menu_keyboard(),
            )
            return
        price = expected_price

        u_db = userbot_db.get_user_by_telegram_id(user_id)
        balance = int((u_db or {}).get('wallet_balance') or 0)
        internal_user_id = (u_db or {}).get('id')
        renew_target_service_id = int(context.user_data.get(f"renew_target_{user_id}") or 0)

        if balance >= price:
            context.user_data[f"pending_wallet_{user_id}"] = {
                "internal_user_id": internal_user_id,
                "amount": price,
                "sid": sid,
                "gb": gb,
                "days": days,
                "renew_service_id": renew_target_service_id,
            }

            # در تمدید: نام سرویس قبلی حفظ می‌شود و نباید دوباره از کاربر پرسیده شود.
            if renew_target_service_id > 0:
                renew_service = userbot_db.get_service_by_id(renew_target_service_id) or {}
                service_name = (renew_service.get("name") or "").strip() or "سرویس"
                try:
                    await query.message.delete()
                except Exception:
                    pass
                ok = await _process_wallet_purchase(
                    context=context,
                    user_id=user_id,
                    tg_user=query.from_user,
                    chat_id=user_id,
                    pending_wallet=context.user_data.get(f"pending_wallet_{user_id}") or {},
                    service_name=service_name,
                )
                context.user_data.pop(f"pending_wallet_{user_id}", None)
                context.user_data.pop(f"renew_target_{user_id}", None)
                set_user_step(context, user_id, None)
                if not ok:
                    await context.bot.send_message(chat_id=user_id, text="❌ عملیات تمدید انجام نشد.", reply_markup=_main_menu_keyboard())
                return

            # در خرید عادی: نام سرویس از کاربر گرفته می‌شود.
            set_user_step(context, user_id, "WAIT_SERVICE_NAME")
            await query.message.delete()
            await context.bot.send_message(chat_id=user_id, text="✍️ لطفا نام سرویس خود را ارسال کنید:", reply_markup=cancel_keyboard())
            return

        # موجودی کافی نیست -> کارت به کارت
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_card_to_card", True)):
            await query.message.delete()
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ موجودی کیف پول کافی نیست و روش کارت به کارت نیز غیرفعال است.",
                reply_markup=_main_menu_keyboard(),
            )
            return
        try:
            card_info = database.get_next_card()
        except Exception:
            card_info = None
        if not card_info:
            try:
                card_info = database.get_random_card()
            except Exception:
                card_info = None
        card_number = (card_info or {}).get('number') or "-"
        card_owner = (card_info or {}).get('owner') or "-"
        card_bank = (card_info or {}).get('bank') or ""

        pay_amount_toman, tx_marker = _apply_random_tx_marker(price, _get_tx_plans_settings())
        msg = _build_card_to_card_payment_text(
            amount_toman=pay_amount_toman,
            card_number=card_number,
            card_owner=card_owner,
            card_bank=card_bank,
            text_settings=text_settings,
        )
        if tx_marker > 0:
            msg = f"🔢 مشخصه تراکنش اعمال شد: +{tx_marker:,} تومان\n\n{msg}"

        context.user_data[f"pending_pay_{user_id}"] = {
            "amount": pay_amount_toman,
            "sid": sid,
            "gb": gb,
            "days": days,
            "plan_id": None,
            "renew_service_id": renew_target_service_id,
            "base_amount": price,
            "tx_marker": tx_marker,
        }
        set_user_step(context, user_id, "WAIT_RECEIPT_CONFIRM")
        await query.message.delete()
        await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="HTML", reply_markup=confirm_payment_inline_keyboard())
        return

    elif data.startswith("buy:pay_direct:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer("🚫 خرید غیرفعال است.", show_alert=True)
            return
        await query.answer()
        parts = data.split(":")
        sid = int(parts[2])
        gb = int(parts[3])
        days = int(parts[4])
        price = int(parts[5])
        plan_id = int(parts[6]) if len(parts) > 6 else 0
        # قیمت همیشه سمت سرور دوباره محاسبه می‌شود — ضد دستکاری callback data
        expected_price = _expected_server_price(sid, gb, days, plan_id)
        if expected_price is None:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ اطلاعات این دکمه منقضی یا نامعتبر است. لطفاً خرید را از نو انجام دهید.",
                reply_markup=_main_menu_keyboard(),
            )
            return
        price = expected_price

        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_card_to_card", True)):
            await query.message.delete()
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ روش پرداخت مستقیم (کارت به کارت) در حال حاضر غیرفعال است.",
                reply_markup=_main_menu_keyboard(),
            )
            return

        renew_target_service_id = int(context.user_data.get(f"renew_target_{user_id}") or 0)
        if renew_target_service_id > 0:
            renew_service = userbot_db.get_service_by_id(renew_target_service_id) or {}
            direct_service_name = (renew_service.get("name") or "").strip() or _generate_random_service_name()
        else:
            direct_service_name = _generate_random_service_name()

        try:
            card_info = database.get_next_card()
        except Exception:
            card_info = None
        if not card_info:
            try:
                card_info = database.get_random_card()
            except Exception:
                card_info = None
        card_number = (card_info or {}).get("number") or "-"
        card_owner = (card_info or {}).get("owner") or "-"
        card_bank = (card_info or {}).get("bank") or ""

        pay_amount_toman, tx_marker = _apply_random_tx_marker(price, _get_tx_plans_settings())
        msg = _build_card_to_card_payment_text(
            amount_toman=pay_amount_toman,
            card_number=card_number,
            card_owner=card_owner,
            card_bank=card_bank,
            text_settings=text_settings,
        )
        if tx_marker > 0:
            msg = f"🔢 مشخصه تراکنش اعمال شد: +{tx_marker:,} تومان\n\n{msg}"
        msg += "\n\n⚡ پس از تایید پرداخت، اشتراک شما به‌صورت خودکار ساخته و ارسال می‌شود."

        context.user_data[f"pending_pay_{user_id}"] = {
            "amount": pay_amount_toman,
            "sid": sid,
            "gb": gb,
            "days": days,
            "plan_id": None,
            "renew_service_id": renew_target_service_id,
            "base_amount": price,
            "tx_marker": tx_marker,
            "direct_buy": True,
            "direct_service_name": direct_service_name,
        }
        set_user_step(context, user_id, "WAIT_RECEIPT_CONFIRM")
        await query.message.delete()
        await context.bot.send_message(
            chat_id=user_id,
            text=msg,
            parse_mode="HTML",
            reply_markup=confirm_payment_inline_keyboard(),
        )
        return

# --- 8. هندلر ارسال عکس فیش پرداخت ---
async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if _should_skip_stale_startup_update(update, context, user_id):
        return
    step = get_user_step(context, user_id)
    text = update.message.text

    if step == "WAIT_ADMIN_DIRECT_REPLY_TEXT":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        reply_text = str(text or "").strip()
        if not reply_text:
            await update.message.reply_text("❌ لطفا پاسخ خود را به صورت متنی ارسال کنید:")
            return

        user_row = userbot_db.get_user_by_telegram_id(user_id)
        if not user_row:
            internal_uid = userbot_db.upsert_user(
                update.effective_user.id,
                update.effective_user.username,
                update.effective_user.full_name,
            )
            user_row = userbot_db.get_user_by_id(internal_uid) or {}
        internal_uid = int((user_row or {}).get("id") or 0)
        display_name = str(
            (user_row or {}).get("full_name")
            or update.effective_user.full_name
            or (user_row or {}).get("username")
            or update.effective_user.username
            or user_id
        ).strip()

        if not (ADMIN_ID and ADMIN_BOT_TOKEN):
            set_user_step(context, user_id, None)
            await update.message.reply_text(
                "❌ تنظیمات پشتیبانی کامل نیست. لطفا بعدا تلاش کنید.",
                reply_markup=_main_menu_keyboard(),
            )
            return

        admin_text = (
            "📬 تیکت جدیدی دریافت شد\n"
            f"📄 متن تیکت: {reply_text}"
        )

        rows = []
        if internal_uid > 0:
            rows.append([InlineKeyboardButton(display_name, callback_data=f"userbot:user:{internal_uid}")])
            rows.append([InlineKeyboardButton("📨پاسخ", callback_data=f"userbot:user:{internal_uid}:message")])
        admin_kb = InlineKeyboardMarkup(rows) if rows else None

        try:
            admin_bot = Bot(token=ADMIN_BOT_TOKEN)
            await admin_bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_text,
                reply_markup=admin_kb,
            )
        except Exception as e:
            logger.warning("Failed to forward direct admin message reply (tg=%s): %s", user_id, e)
            await update.message.reply_text(
                "❌ ارسال پیام با خطا مواجه شد. لطفا دوباره تلاش کنید.",
                reply_markup=_main_menu_keyboard(),
            )
            return

        set_user_step(context, user_id, None)
        await update.message.reply_text(
            "✅ پیام شما با موفقیت برای پشتیبانی ارسال شد\n⏳ در سریع‌ترین زمان ممکن پاسخگو خواهیم بود.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    if step == "WAIT_TICKET_TITLE":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_ticket_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return
        title = str(text or "").strip()
        if not title:
            await update.message.reply_text("❌ لطفا موضوع درخواست را ارسال کنید:", reply_markup=_ticket_text_cancel_keyboard())
            return
        pending = context.user_data.get(f"pending_ticket_{user_id}") or {}
        pending["title"] = title
        context.user_data[f"pending_ticket_{user_id}"] = pending
        set_user_step(context, user_id, "WAIT_TICKET_QUESTION")
        await update.message.reply_text("✍️ لطفا سوال خود را به صورت کامل ارسال نمایید:", reply_markup=_ticket_text_cancel_keyboard())
        return

    if step == "WAIT_TICKET_QUESTION":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_ticket_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return
        question = str(text or "").strip()
        if not question:
            await update.message.reply_text("❌ لطفا متن سوال را کامل وارد کنید:", reply_markup=_ticket_text_cancel_keyboard())
            return
        pending = context.user_data.get(f"pending_ticket_{user_id}") or {}
        pending["question"] = question
        context.user_data[f"pending_ticket_{user_id}"] = pending
        set_user_step(context, user_id, "WAIT_TICKET_SCREENSHOT")
        await update.message.reply_text(
            "🖼 لطفا اسکرین‌شات خود را ارسال کنید یا روی دکمه «▶️رد کردن» کلیک کنید.",
            reply_markup=ticket_skip_screenshot_keyboard(),
        )
        return

    if step == "WAIT_TICKET_SCREENSHOT":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_ticket_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        pending = context.user_data.get(f"pending_ticket_{user_id}") or {}
        skip_text = _normalize_action_text(text or "")
        if update.message.photo:
            pending["receipt_photo_id"] = update.message.photo[-1].file_id
        elif skip_text in {"▶️رد کردن", "▶️ رد کردن", "رد کردن", "⏭️رد کردن", "⏭️ رد کردن"}:
            pending["receipt_photo_id"] = ""
        else:
            await update.message.reply_text(
                "❌ لطفا عکس ارسال کنید یا روی دکمه «▶️رد کردن» بزنید.",
                reply_markup=ticket_skip_screenshot_keyboard(),
            )
            return

        context.user_data[f"pending_ticket_{user_id}"] = pending
        set_user_step(context, user_id, "WAIT_TICKET_CONFIRM")
        await update.message.reply_text(
            _ticket_compose_preview_text(pending),
            reply_markup=ticket_confirm_keyboard(),
        )
        return

    if step == "WAIT_TICKET_CONFIRM":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_ticket_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return
        await update.message.reply_text(
            "برای ارسال تیکت از دکمه‌های «✅ارسال» یا «✏️ویرایش» استفاده کنید.",
            reply_markup=ticket_confirm_keyboard(),
        )
        return

    if step in {"WAIT_TICKET_REPLY", "WAIT_TICKET_REPLY_TEXT"}:
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return
        state = context.user_data.get(f"ticket_reply_{user_id}") or {}
        ticket_code = int(state.get("ticket_code") or 0)
        if ticket_code <= 0:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text("❌ اطلاعات تیکت نامعتبر است.", reply_markup=_main_menu_keyboard())
            return

        user_row = userbot_db.get_user_by_telegram_id(user_id)
        internal_uid = int((user_row or {}).get("id") or 0)
        ticket = userbot_db.get_user_ticket_by_code(internal_uid, ticket_code)
        if not ticket:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text("❌ تیکت موردنظر یافت نشد.", reply_markup=_main_menu_keyboard())
            return

        message_text = str(text or "").strip()
        if not message_text:
            await update.message.reply_text(
                "❌ لطفا پاسخ خود را به صورت کامل ارسال نمایید:",
                reply_markup=_ticket_text_cancel_keyboard("reply"),
            )
            return

        state["reply_text"] = message_text
        context.user_data[f"ticket_reply_{user_id}"] = state
        set_user_step(context, user_id, "WAIT_TICKET_REPLY_SCREENSHOT")
        await update.message.reply_text(
            "🖼 لطفا اسکرین‌شات خود را ارسال کنید یا روی دکمه «▶️رد کردن» کلیک کنید.",
            reply_markup=ticket_skip_screenshot_keyboard("reply"),
        )
        return

    if step == "WAIT_TICKET_REPLY_SCREENSHOT":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        state = context.user_data.get(f"ticket_reply_{user_id}") or {}
        ticket_code = int(state.get("ticket_code") or 0)
        if ticket_code <= 0:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text("❌ اطلاعات تیکت نامعتبر است.", reply_markup=_main_menu_keyboard())
            return

        skip_text = _normalize_action_text(text or "")
        if update.message.photo:
            state["receipt_photo_id"] = update.message.photo[-1].file_id
        elif skip_text in {"▶️رد کردن", "▶️ رد کردن", "رد کردن", "⏭️رد کردن", "⏭️ رد کردن"}:
            state["receipt_photo_id"] = ""
        else:
            await update.message.reply_text(
                "❌ لطفا عکس ارسال کنید یا روی دکمه «▶️رد کردن» بزنید.",
                reply_markup=ticket_skip_screenshot_keyboard("reply"),
            )
            return

        context.user_data[f"ticket_reply_{user_id}"] = state
        set_user_step(context, user_id, "WAIT_TICKET_REPLY_CONFIRM")
        preview_text = _ticket_reply_preview_text(state)
        preview_photo_id = str(state.get("receipt_photo_id") or "").strip()
        if preview_photo_id:
            try:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=preview_photo_id,
                    caption=preview_text,
                    reply_markup=ticket_confirm_keyboard("reply"),
                )
            except Exception:
                await update.message.reply_text(
                    preview_text,
                    reply_markup=ticket_confirm_keyboard("reply"),
                )
        else:
            await update.message.reply_text(
                preview_text,
                reply_markup=ticket_confirm_keyboard("reply"),
            )
        return

    if step == "WAIT_TICKET_REPLY_CONFIRM":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return
        await update.message.reply_text(
            "برای ارسال پاسخ از دکمه‌های «✅ارسال» یا «✏️ویرایش» استفاده کنید.",
            reply_markup=ticket_confirm_keyboard("reply"),
        )
        return

    # --- اتصال اشتراک: دریافت UUID/لینک/کانفیگ ---
    if step == "WAIT_CONNECT_SUB_INPUT":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        parsed_uuid = _extract_uuid_from_user_input(text or "")
        if not parsed_uuid:
            await update.message.reply_text(
                "❌ UUID معتبر پیدا نشد.\n"
                "لطفاً UUID یا لینک اشتراک/کانفیگ معتبر بفرستید.",
                reply_markup=cancel_keyboard(),
            )
            return

        u_db = userbot_db.get_user_by_telegram_id(user_id)
        if not u_db:
            internal_user_id = userbot_db.upsert_user(
                update.effective_user.id,
                update.effective_user.username,
                update.effective_user.full_name,
            )
            u_db = userbot_db.get_user_by_id(internal_user_id) or {}
        internal_user_id = int((u_db or {}).get("id") or 0)
        if internal_user_id <= 0:
            await update.message.reply_text("❌ کاربر یافت نشد.", reply_markup=_main_menu_keyboard())
            set_user_step(context, user_id, None)
            return

        # امنیت: UUID فقط برای یک کاربر قابل اتصال باشد.
        owner = userbot_db.get_service_owner_by_panel_uuid(parsed_uuid)
        if owner and int(owner.get("user_id") or 0) != int(internal_user_id):
            await update.message.reply_text(
                "⛔ این اشتراک قبلاً توسط کاربر دیگری متصل شده است و قابل اتصال مجدد نیست.",
                reply_markup=_main_menu_keyboard(),
            )
            set_user_step(context, user_id, None)
            return

        existing_self = userbot_db.get_user_service_by_panel_uuid(internal_user_id, parsed_uuid)
        if existing_self:
            set_user_step(context, user_id, None)
            service = await _sync_service_runtime_from_panels(existing_self)
            settings = _get_subscription_settings()
            await update.message.reply_text(
                "ℹ️ این اشتراک قبلاً به حساب شما متصل شده است.",
                reply_markup=_main_menu_keyboard(),
            )
            await update.message.reply_text(
                _build_subscription_status_text(service),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(
                    service.get("id"),
                    show_direct_config=settings.get("show_direct_config", True),
                    show_sub_link=settings.get("show_sub_link", True),
                    show_configs=_should_show_configs_button(settings),
                    show_detach=_is_connected_service(service),
                ),
            )
            return

        await update.message.reply_text("⏳ در حال بررسی اشتراک...")
        targets = await _find_panel_user_targets_by_uuid(parsed_uuid)
        if not targets:
            await update.message.reply_text(
                "❌ اشتراکی با این UUID روی سرورهای ربات پیدا نشد.",
                reply_markup=_main_menu_keyboard(),
            )
            set_user_step(context, user_id, None)
            return

        # سرور اصلی اتصال: اولین سرور پیدا‌شده
        primary_server, primary_user = targets[0]
        service_name = str(primary_user.get("name") or "اشتراک متصل‌شده").strip() or "اشتراک متصل‌شده"
        usage_limit = _to_float(primary_user.get("usage_limit_GB"), 0.0)
        total_usage = 0.0
        min_days_left: Optional[int] = None
        latest_last_online: Optional[datetime] = None
        for _srv, pu in targets:
            total_usage += _to_float(pu.get("current_usage_GB"), 0.0)
            dleft = _days_left_from_panel_user(pu)
            if dleft is not None:
                min_days_left = dleft if min_days_left is None else min(min_days_left, dleft)
            dt = _parse_panel_datetime(pu.get("last_online"))
            if dt and (latest_last_online is None or dt > latest_last_online):
                latest_last_online = dt

        server_id = int(primary_server.get("id") or 0)
        server_title = str(primary_server.get("title") or f"سرور #{server_id}").strip()
        service_code = _generate_service_code()
        service_comment = f"uuid:{parsed_uuid}|code:{service_code}|linked:1|source:connect"
        service_db_id = None

        try:
            with userbot_db._get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO userbot_services
                    (user_id, name, server_id, server_title, usage_current, usage_limit, days_left, last_online, comment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(internal_user_id),
                        service_name,
                        int(server_id),
                        server_title,
                        float(total_usage),
                        float(usage_limit),
                        int(min_days_left if min_days_left is not None else 0),
                        (latest_last_online.strftime("%Y-%m-%d %H:%M:%S") if latest_last_online else None),
                        service_comment,
                    ),
                )
                service_db_id = int(cur.lastrowid)
        except Exception as e:
            logger.exception("Failed persisting connected subscription (telegram_id=%s)", user_id)
            await update.message.reply_text(
                f"❌ اتصال اشتراک با خطا مواجه شد: {e}",
                reply_markup=_main_menu_keyboard(),
            )
            set_user_step(context, user_id, None)
            return

        for srv, pu in targets:
            try:
                sid = int(srv.get("id") or 0)
                if sid <= 0:
                    continue
                userbot_db.add_service_node(
                    service_id=int(service_db_id),
                    server_id=sid,
                    panel_user_uuid=parsed_uuid,
                    server_title=str(srv.get("title") or ""),
                    panel_user_id=(str(pu.get("id")).strip() if pu.get("id") is not None else None),
                    is_active=1,
                )
            except Exception:
                pass

        set_user_step(context, user_id, None)
        service = userbot_db.get_service_by_id(int(service_db_id)) or {}
        service = await _sync_service_runtime_from_panels(service)
        settings = _get_subscription_settings()
        await update.message.reply_text(
            "✅ اشتراک شما با موفقیت متصل شد.",
            reply_markup=_main_menu_keyboard(),
        )
        await update.message.reply_text(
            _build_subscription_status_text(service),
            parse_mode="Markdown",
            reply_markup=subscription_status_keyboard(
                service.get("id"),
                show_direct_config=settings.get("show_direct_config", True),
                show_sub_link=settings.get("show_sub_link", True),
                show_configs=_should_show_configs_button(settings),
                show_detach=_is_connected_service(service),
            ),
        )
        return

    # --- تغییر نام اشتراک: دریافت نام جدید ---
    if step == "WAIT_RENAME_SERVICE_NAME":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_rename_service_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        pending_rename = context.user_data.get(f"pending_rename_service_{user_id}", None) or {}
        service_id = int(pending_rename.get("service_id") or 0)
        if service_id <= 0:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_rename_service_{user_id}", None)
            await update.message.reply_text("❌ اطلاعات سرویس نامعتبر است.", reply_markup=_main_menu_keyboard())
            return

        u_db = userbot_db.get_user_by_telegram_id(user_id) or {}
        internal_user_id = int(u_db.get("id") or 0)
        service = userbot_db.get_service_by_id(service_id) or {}
        if not service or internal_user_id <= 0 or int(service.get("user_id") or 0) != internal_user_id:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_rename_service_{user_id}", None)
            await update.message.reply_text("❌ اشتراک موردنظر یافت نشد.", reply_markup=_main_menu_keyboard())
            return

        new_name = _normalize_service_name_input(text or "")
        if len(new_name) < 3:
            await update.message.reply_text(
                "❌ نام اشتراک خیلی کوتاه است. حداقل 3 کاراکتر وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return
        if len(new_name) > 64:
            await update.message.reply_text(
                "❌ نام اشتراک خیلی طولانی است. حداکثر 64 کاراکتر وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return

        old_name = str(service.get("name") or "").strip()
        if new_name == old_name:
            await update.message.reply_text(
                "ℹ️ نام جدید با نام فعلی یکسان است. نام دیگری وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return

        await update.message.reply_text("⏳ در حال بروزرسانی نام اشتراک...")
        ok, result_text = await _rename_service_across_panels_and_db(service, new_name)
        if not ok:
            await update.message.reply_text(result_text, reply_markup=cancel_keyboard())
            return

        set_user_step(context, user_id, None)
        context.user_data.pop(f"pending_rename_service_{user_id}", None)
        refreshed = userbot_db.get_service_by_id(service_id) or service
        refreshed = await _sync_service_runtime_from_panels(refreshed)
        settings = _get_subscription_settings()
        await update.message.reply_text(result_text, reply_markup=_main_menu_keyboard())
        await update.message.reply_text(
            _build_subscription_status_text(refreshed),
            parse_mode="Markdown",
            reply_markup=subscription_status_keyboard(
                refreshed.get("id"),
                show_direct_config=settings.get("show_direct_config", True),
                show_sub_link=settings.get("show_sub_link", True),
                show_configs=_should_show_configs_button(settings),
                show_detach=_is_connected_service(refreshed),
            ),
        )
        return

    # --- تست رایگان: دریافت نام سرویس ---
    if step == "WAIT_TRIAL_SERVICE_NAME":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        service_name = (text or "").strip()
        if not service_name:
            await update.message.reply_text("❌ لطفاً نام خود را ارسال کنید:", reply_markup=cancel_keyboard())
            return

        pending_trial = context.user_data.get(f"pending_trial_{user_id}", None) or {}
        internal_user_id = int(pending_trial.get("internal_user_id") or 0)
        sid = int(pending_trial.get("sid") or 0)

        if not internal_user_id:
            u_db = userbot_db.get_user_by_telegram_id(user_id)
            internal_user_id = int((u_db or {}).get("id") or 0)

        if not internal_user_id or sid <= 0:
            await update.message.reply_text(
                "❌ اطلاعات تست رایگان ناقص است. لطفاً دوباره تلاش کنید.",
                reply_markup=_main_menu_keyboard(),
            )
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            return

        user_row = userbot_db.get_user_by_id(internal_user_id) or {}
        if int(user_row.get("got_free_trial") or 0) == 1:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            await update.message.reply_text(
                "🚫 شما قبلا اکانت تست رایگان خود را دریافت نموده‌اید!",
                reply_markup=_main_menu_keyboard(),
            )
            return

        trial_settings = userbot_db.get_trial_spec_settings()
        if not bool(trial_settings.get("enabled", True)):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            await update.message.reply_text(
                "🚫 دریافت تست رایگان در حال حاضر غیرفعال است.",
                reply_markup=_main_menu_keyboard(),
            )
            return

        gb = float(trial_settings.get("usage_gb") or 0.5)
        days = int(trial_settings.get("days") or 1)
        if gb <= 0:
            gb = 0.5
        if days <= 0:
            days = 1

        server = database.get_server_by_id(sid)
        if not server:
            await update.message.reply_text(
                "❌ سرور انتخاب‌شده یافت نشد. لطفاً دوباره تلاش کنید.",
                reply_markup=_main_menu_keyboard(),
            )
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            return

        payload = {
            "name": service_name,
            "usage_limit_GB": float(gb),
            "package_days": int(days),
            "start_date": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d"),
            "current_usage_GB": 0,
            "last_reset_time": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
            "is_active": True,
            "comment": _build_panel_user_comment(int(user_id), is_test=True),
        }

        created_nodes: list[dict] = []
        targets = _get_target_servers_for_sale(server)
        if not targets:
            targets = [server]

        try:
            created, created_nodes = await _create_service_users_on_targets(targets, payload)
        except Exception as e:
            logger.exception("Failed creating free-trial user(s) for telegram_id=%s", user_id)
            if created_nodes:
                await _deactivate_created_users(created_nodes)
            await update.message.reply_text(
                f"❌ ساخت اکانت تست رایگان انجام نشد.\nجزئیات خطا: {e}",
                reply_markup=_main_menu_keyboard(),
            )
            return

        panel_user_uuid = str(created.get("uuid") or created.get("id") or "").strip()
        panel_user_id = created.get("id")
        server_title = server.get("title") or f"سرور #{sid}"
        usage_limit = float(created.get("usage_limit_GB") or gb)
        usage_current = float(created.get("current_usage_GB") or 0)

        days_left = int(days)
        try:
            start_raw = created.get("start_date")
            package_days = int(created.get("package_days") or days)
            if start_raw:
                start_dt = None
                for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        start_dt = datetime.strptime(start_raw, fmt)
                        break
                    except ValueError:
                        continue
                if start_dt:
                    end_dt = start_dt + timedelta(days=package_days)
                    days_left = (end_dt.date() - datetime.now(timezone.utc).replace(tzinfo=None).date()).days
        except Exception:
            days_left = int(days)

        service_code = _generate_service_code()
        service_db_id = None
        try:
            with userbot_db._get_conn() as conn:
                cur = conn.cursor()
                comment_parts = []
                if panel_user_uuid:
                    comment_parts.append(f"uuid:{panel_user_uuid}")
                comment_parts.append("price:0")
                comment_parts.append(f"code:{service_code}")
                comment_parts.append("test")
                service_comment = "|".join(comment_parts)
                cur.execute(
                    """
                    INSERT INTO userbot_services
                    (user_id, name, server_id, server_title, usage_current, usage_limit, days_left, last_online, comment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        internal_user_id,
                        service_name,
                        sid,
                        server_title,
                        usage_current,
                        usage_limit,
                        days_left,
                        created.get("last_online"),
                        service_comment,
                    ),
                )
                service_db_id = cur.lastrowid
            userbot_db.set_free_trial_used(internal_user_id, 1)
            try:
                userbot_db.try_grant_referral_trial_reward(internal_user_id)
            except Exception as e:
                logger.warning(
                    "Failed to process referral trial reward (invitee user=%s): %s",
                    internal_user_id,
                    e,
                )
        except Exception as e:
            logger.exception("Failed persisting free trial for telegram_id=%s", user_id)
            await update.message.reply_text(
                f"⚠️ اکانت تست ساخته شد ولی ثبت نهایی در ربات خطا داد: {e}",
                reply_markup=_main_menu_keyboard(),
            )
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            return

        if service_db_id and created_nodes:
            for node_item in created_nodes:
                try:
                    node_sid = int(node_item.get("server_id") or 0)
                    node_uuid = str(node_item.get("panel_user_uuid") or "").strip()
                    if node_sid <= 0 or not node_uuid:
                        continue
                    userbot_db.add_service_node(
                        service_id=int(service_db_id),
                        server_id=node_sid,
                        panel_user_uuid=node_uuid,
                        server_title=str(node_item.get("server_title") or ""),
                        panel_user_id=(
                            str(node_item.get("panel_user_id"))
                            if node_item.get("panel_user_id") is not None
                            else None
                        ),
                        is_active=1,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to create trial service-node mapping (service_id=%s, server_id=%s): %s",
                        service_db_id,
                        node_item.get("server_id"),
                        e,
                    )

        set_user_step(context, user_id, None)
        context.user_data.pop(f"pending_trial_{user_id}", None)

        announce_enabled = bool(trial_settings.get("announce_enabled", True))
        if announce_enabled:
            await update.message.reply_text(
                "✅ اکانت تست رایگان شما با موفقیت ثبت شد\n"
                "از طریق دکمه [📊وضعیت اشتراک📊] میتوانید به اطلاعات اشتراک خود دسترسی داشته باشید.",
                reply_markup=_main_menu_keyboard(),
            )
        else:
            await update.message.reply_text("🏠 منوی اصلی", reply_markup=_main_menu_keyboard())

        delivered_service = {
            "id": service_db_id or panel_user_id or "—",
            "name": service_name,
            "server_title": server_title,
            "usage_current": usage_current,
            "usage_limit": usage_limit,
            "days_left": days_left,
            "comment": f"price:0|code:{service_code}",
            "user_id": internal_user_id,
        }
        settings = _get_subscription_settings()
        await update.message.reply_text(
            _build_subscription_status_text(delivered_service),
            parse_mode="Markdown",
            reply_markup=subscription_status_keyboard(
                service_db_id,
                show_direct_config=settings.get("show_direct_config", True),
                show_sub_link=settings.get("show_sub_link", True),
                show_configs=_should_show_configs_button(settings),
                show_detach=_is_connected_service(delivered_service),
            ),
        )

        # گزارش به ادمین (ربات ادمین): ایجاد اشتراک تستی
        if ADMIN_ID and ADMIN_BOT_TOKEN:
            try:
                admin_bot = Bot(token=ADMIN_BOT_TOKEN)
                user_btn_title = (update.effective_user.full_name or update.effective_user.username or str(user_id)).strip()
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"👤 {user_btn_title}", callback_data=f"userbot:user:{internal_user_id}")]
                ])

                await admin_bot.send_message(
                    chat_id=ADMIN_ID,
                    text=_build_subscription_created_caption(
                        service_name=service_name,
                        server_title=server_title,
                        gb=gb,
                        days=days,
                        service_code=service_code,
                        amount=None,
                        is_trial=True,
                    ),
                    reply_markup=kb,
                )
            except Exception as e:
                logger.warning(f"Failed to notify admin for free-trial creation (user={user_id}): {e}")

        await _send_event_channel_subscription_report(
            context,
            action_title="ایجاد تست رایگان",
            telegram_id=int(user_id),
            display_name=(update.effective_user.full_name or update.effective_user.username or str(user_id)).strip(),
            service_name=service_name,
            server_title=server_title,
            gb=float(gb),
            days=int(days),
            service_code=service_code,
            amount=None,
        )
        return

    # --- پرداخت از کیف پول: دریافت نام سرویس ---
    if step == "WAIT_SERVICE_NAME":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_{user_id}", None)
            context.user_data.pop(f"renew_target_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        service_name = (text or "").strip()
        if not service_name:
            await update.message.reply_text("❌ لطفاً نام سرویس را ارسال کنید:", reply_markup=cancel_keyboard())
            return

        pending_wallet = context.user_data.get(f"pending_wallet_{user_id}", None) or {}
        ok = await _process_wallet_purchase(
            context=context,
            user_id=user_id,
            tg_user=update.effective_user,
            chat_id=user_id,
            pending_wallet=pending_wallet,
            service_name=service_name,
        )
        set_user_step(context, user_id, None)
        context.user_data.pop(f"pending_wallet_{user_id}", None)
        context.user_data.pop(f"renew_target_{user_id}", None)
        if not ok:
            await update.message.reply_text("❌ عملیات خرید/تمدید انجام نشد.", reply_markup=_main_menu_keyboard())
        return

    # --- کیف پول: شارژ کارت به کارت (مبلغ دلخواه) ---
    if step == "WAIT_WALLET_TOPUP_AMOUNT":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_card_to_card", True)):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
            await update.message.reply_text("🚫 کارت به کارت در حال حاضر غیرفعال است.", reply_markup=_main_menu_keyboard())
            return

        raw = (text or "").replace(",", "").strip()
        if not raw.isdigit():
            await update.message.reply_text("🔻 لطفا مبلغی که قصد شارژ حساب خود دارید را به تومان وارد کنید:", reply_markup=cancel_keyboard())
            return

        amount_toman = int(raw)
        if amount_toman <= 0:
            await update.message.reply_text("🔻 لطفا مبلغی که قصد شارژ حساب خود دارید را به تومان وارد کنید:", reply_markup=cancel_keyboard())
            return
        txp = _get_tx_plans_settings()
        min_tx = int(txp.get("min_transaction_toman") or 1)
        if amount_toman < min_tx:
            await update.message.reply_text(
                f"❌ حداقل مبلغ مجاز تراکنش {min_tx:,} تومان است.",
                reply_markup=cancel_keyboard(),
            )
            return

        try:
            card_info = database.get_next_card()
        except Exception:
            card_info = None
        if not card_info:
            try:
                card_info = database.get_random_card()
            except Exception:
                card_info = None
        card_number = (card_info or {}).get('number') or "-"
        card_owner = (card_info or {}).get('owner') or "-"
        card_bank = (card_info or {}).get('bank') or ""

        pay_amount_toman, tx_marker = _apply_random_tx_marker(amount_toman, txp)

        context.user_data[f"pending_wallet_topup_{user_id}"] = {
            "amount": pay_amount_toman,
            "card_number": card_number,
            "card_owner": card_owner,
            "card_bank": card_bank,
            "base_amount": amount_toman,
            "tx_marker": tx_marker,
        }

        msg = _build_card_to_card_payment_text(
            amount_toman=pay_amount_toman,
            card_number=card_number,
            card_owner=card_owner,
            card_bank=card_bank,
            text_settings=_get_text_settings(),
        )
        if tx_marker > 0:
            msg = f"🔢 مشخصه تراکنش اعمال شد: +{tx_marker:,} تومان\n\n{msg}"
        set_user_step(context, user_id, "WAIT_WALLET_TOPUP_CONFIRM")
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=confirm_payment_keyboard())
        return

    if step == "WAIT_WALLET_TOPUP_CONFIRM":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        if text == "✅ پرداخت کردم، ارسال رسید":
            set_user_step(context, user_id, "WAIT_WALLET_TOPUP_IMAGE")
            await update.message.reply_text("⬇️ لطفا رسید پرداخت خود را در زیر این پیام ارسال کنید:", reply_markup=receipt_cancel_keyboard())
            return

    if step == "WAIT_WALLET_TOPUP_IMAGE":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        if update.message.photo:
            photo_file_id = update.message.photo[-1].file_id
            pending = context.user_data.get(f"pending_wallet_topup_{user_id}", {})
            amount = int(pending.get("amount") or 0)
            pay_settings = _get_payment_settings()
            require_last4 = bool(pay_settings.get("require_last4_for_card_receipt", False))
            if require_last4:
                pending["receipt_photo_file_id"] = photo_file_id
                context.user_data[f"pending_wallet_topup_{user_id}"] = pending
                set_user_step(context, user_id, "WAIT_WALLET_TOPUP_LAST4")
                await update.message.reply_text(
                    i18n.t("last4_prompt", _user_lang(user_id)),
                    reply_markup=cancel_keyboard(),
                )
                return

            pending = context.user_data.pop(f"pending_wallet_topup_{user_id}", {})
            amount = int(pending.get("amount") or 0)
            auto_approved = False
            payment_result = "pending"
            if amount > 0:
                auto_approved, payment_result = await _finalize_pending_card_payment(
                    update=update,
                    context=context,
                    user_id=user_id,
                    amount=int(amount),
                    photo_file_id=photo_file_id,
                    flow="wallet_topup",
                    payer_last4="",
                    extra_meta={
                        "pay_flow": "wallet_topup",
                        "base_amount": int(pending.get("base_amount") or amount),
                        "tx_marker": int(pending.get("tx_marker") or 0),
                    },
                )

            await update.message.reply_text(
                _card_payment_result_user_text(amount, payment_result, user_id=user_id),
                reply_markup=_main_menu_keyboard(),
            )
            set_user_step(context, user_id, None)
        else:
            await update.message.reply_text("❌ لطفاً فقط عکس رسید را ارسال کنید یا برای بازگشت «بازگشت» را بزنید.")
        return

    if step == "WAIT_WALLET_TOPUP_LAST4":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        payer_last4 = _parse_exact_card_last4(text)
        if not payer_last4:
            await update.message.reply_text(
                i18n.t("last4_invalid_short", _user_lang(user_id)),
                reply_markup=cancel_keyboard(),
            )
            return

        pending = context.user_data.pop(f"pending_wallet_topup_{user_id}", {})
        amount = int(pending.get("amount") or 0)
        photo_file_id = str(pending.get("receipt_photo_file_id") or "").strip()
        if amount <= 0 or not photo_file_id:
            set_user_step(context, user_id, None)
            await update.message.reply_text("❌ اطلاعات پرداخت ناقص است. لطفا دوباره تلاش کنید.", reply_markup=_main_menu_keyboard())
            return

        auto_approved, payment_result = await _finalize_pending_card_payment(
            update=update,
            context=context,
            user_id=user_id,
            amount=int(amount),
            photo_file_id=photo_file_id,
            flow="wallet_topup",
            payer_last4=payer_last4,
            extra_meta={
                "pay_flow": "wallet_topup",
                "base_amount": int(pending.get("base_amount") or amount),
                "tx_marker": int(pending.get("tx_marker") or 0),
            },
        )
        await update.message.reply_text(
            _card_payment_result_user_text(amount, payment_result, user_id=user_id),
            reply_markup=_main_menu_keyboard(),
        )
        set_user_step(context, user_id, None)
        return

    # --- کیف پول: کوپن هدیه ---
    if step == "WAIT_COUPON_CODE":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return
        mkt = _get_marketing_settings()
        if not (
            bool(mkt.get("enable_discount_code", False))
            or bool(mkt.get("enable_increase_code", False))
        ):
            set_user_step(context, user_id, None)
            await update.message.reply_text("🚫 استفاده از کد هدیه در حال حاضر غیرفعال است.", reply_markup=_main_menu_keyboard())
            return
        code = (text or "").strip()
        if not code:
            await update.message.reply_text("⬇️ لطفا کد کوپن خود را ارسال کنید:", reply_markup=cancel_keyboard())
            return
        set_user_step(context, user_id, None)
        u_db = userbot_db.get_user_by_telegram_id(user_id)
        internal_user_id = int((u_db or {}).get("id") or 0)
        if internal_user_id <= 0:
            await update.message.reply_text("❌ کاربر یافت نشد.", reply_markup=_main_menu_keyboard())
            return
        try:
            ok, result_text, amount = userbot_db.redeem_zarin_voucher(code, internal_user_id)
        except Exception as e:
            logger.warning(f"Failed to redeem coupon in WAIT_COUPON_CODE user={user_id}: {e}")
            await update.message.reply_text("❌ خطا در بررسی کوپن.", reply_markup=_main_menu_keyboard())
            return
        if not ok:
            await update.message.reply_text(f"⚠️ {result_text}", reply_markup=_main_menu_keyboard())
            return
        fresh = userbot_db.get_user_by_id(internal_user_id) or {}
        balance = int(fresh.get("wallet_balance") or 0)
        await update.message.reply_text(
            f"✅ کد شما با موفقیت اعمال شد.\n"
            f"🎁 مبلغ هدیه: {int(amount):,} تومان\n"
            f"💰 موجودی جدید کیف پول: {balance:,} تومان",
            reply_markup=_main_menu_keyboard(),
        )
        return
    
    if step == "WAIT_RECEIPT_CONFIRM":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_pay_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return
            
        if text == "✅ پرداخت کردم، ارسال رسید":
            set_user_step(context, user_id, "WAIT_RECEIPT_IMAGE")
            await update.message.reply_text("⬇️ لطفا رسید پرداخت خود را در زیر این پیام ارسال کنید:", reply_markup=receipt_cancel_keyboard())
            return

    if step == "WAIT_RECEIPT_IMAGE":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_pay_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        if update.message.photo:
            photo_file_id = update.message.photo[-1].file_id
            pending = context.user_data.get(f"pending_pay_{user_id}", {})
            amount = int(pending.get("amount") or 0)
            pay_settings = _get_payment_settings()
            require_last4 = bool(pay_settings.get("require_last4_for_card_receipt", False))
            if require_last4:
                pending["receipt_photo_file_id"] = photo_file_id
                context.user_data[f"pending_pay_{user_id}"] = pending
                set_user_step(context, user_id, "WAIT_RECEIPT_LAST4")
                await update.message.reply_text(
                    "🔢 لطفا ۴ رقم آخر کارت مبدا را ارسال کنید:",
                    reply_markup=cancel_keyboard(),
                )
                return

            pending = context.user_data.pop(f"pending_pay_{user_id}", {})
            amount = int(pending.get("amount") or 0)
            auto_approved = False
            payment_result = "pending"
            is_direct_buy = bool(pending.get("direct_buy"))
            if amount > 0:
                flow_kind = "direct_buy_payment" if is_direct_buy else "buy_payment"
                extra_meta = {
                    "pay_flow": "direct_buy" if is_direct_buy else "buy",
                    "base_amount": int(pending.get("base_amount") or amount),
                    "tx_marker": int(pending.get("tx_marker") or 0),
                }
                if is_direct_buy:
                    extra_meta.update({
                        "sid": int(pending.get("sid") or 0),
                        "gb": float(pending.get("gb") or 0),
                        "days": int(pending.get("days") or 0),
                        "renew_service_id": int(pending.get("renew_service_id") or 0),
                        "service_name": str(pending.get("direct_service_name") or "").strip(),
                    })
                auto_approved, payment_result = await _finalize_pending_card_payment(
                    update=update,
                    context=context,
                    user_id=user_id,
                    amount=int(amount),
                    photo_file_id=photo_file_id,
                    flow=flow_kind,
                    payer_last4="",
                    extra_meta=extra_meta,
                )
            
            await update.message.reply_text(
                _card_payment_result_user_text(amount, payment_result, direct_note=True, user_id=user_id),
                reply_markup=_main_menu_keyboard()
            )
            await _deliver_direct_buy_after_sms_notice(context, auto_approved and is_direct_buy)
            set_user_step(context, user_id, None)
        else:
             await update.message.reply_text("❌ لطفاً فقط عکس رسید را ارسال کنید یا برای بازگشت «بازگشت» را بزنید.")
        return

    if step == "WAIT_RECEIPT_LAST4":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_pay_{user_id}", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=_main_menu_keyboard())
            return

        payer_last4 = _parse_exact_card_last4(text)
        if not payer_last4:
            await update.message.reply_text(
                i18n.t("last4_invalid_short", _user_lang(user_id)),
                reply_markup=cancel_keyboard(),
            )
            return

        pending = context.user_data.pop(f"pending_pay_{user_id}", {})
        amount = int(pending.get("amount") or 0)
        photo_file_id = str(pending.get("receipt_photo_file_id") or "").strip()
        if amount <= 0 or not photo_file_id:
            set_user_step(context, user_id, None)
            await update.message.reply_text("❌ اطلاعات پرداخت ناقص است. لطفا دوباره تلاش کنید.", reply_markup=_main_menu_keyboard())
            return

        is_direct_buy = bool(pending.get("direct_buy"))
        flow_kind = "direct_buy_payment" if is_direct_buy else "buy_payment"
        extra_meta = {
            "pay_flow": "direct_buy" if is_direct_buy else "buy",
            "base_amount": int(pending.get("base_amount") or amount),
            "tx_marker": int(pending.get("tx_marker") or 0),
        }
        if is_direct_buy:
            extra_meta.update({
                "sid": int(pending.get("sid") or 0),
                "gb": float(pending.get("gb") or 0),
                "days": int(pending.get("days") or 0),
                "renew_service_id": int(pending.get("renew_service_id") or 0),
                "service_name": str(pending.get("direct_service_name") or "").strip(),
            })
        auto_approved, payment_result = await _finalize_pending_card_payment(
            update=update,
            context=context,
            user_id=user_id,
            amount=int(amount),
            photo_file_id=photo_file_id,
            flow=flow_kind,
            payer_last4=payer_last4,
            extra_meta=extra_meta,
        )
        await update.message.reply_text(
            _card_payment_result_user_text(amount, payment_result, direct_note=True, user_id=user_id),
            reply_markup=_main_menu_keyboard(),
        )
        await _deliver_direct_buy_after_sms_notice(context, auto_approved and is_direct_buy)
        set_user_step(context, user_id, None)
        return

async def _post_init_set_menu(application):
    """Set Telegram command menu (localized per app language)."""
    def _cmds(lang: str):
        return [
            BotCommand("start", i18n.t("cmd_start", lang)),
            BotCommand("language", i18n.t("cmd_language", lang)),
        ]
    try:
        await application.bot.set_my_commands(_cmds("fa"))
        await application.bot.set_my_commands(_cmds("en"), language_code="en")
        await application.bot.set_my_commands(_cmds("ru"), language_code="ru")
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Bot command menu configured: /start, /language (fa/en/ru)")
    except Exception as e:
        logger.warning(f"Failed to configure bot command menu: {e}")

    # Snapshot صف اولیه: برای هر کاربر فقط آخرین آپدیت pending را نگه می‌داریم
    # تا بعد از استارت، پیام‌های قدیمی او اجرا نشوند.
    # NOTE: get_updates قبل از run_polling می‌تواند Conflict ایجاد کند
    # اگر instance دیگری هنوز polling دارد. در صورت خطا رد می‌شویم.
    try:
        pending_updates = await application.bot.get_updates(
            timeout=0,
            limit=100,
            allowed_updates=["message", "callback_query"],
        )
        latest_per_user: dict[int, int] = {}
        for upd in pending_updates or []:
            u = upd.effective_user
            if not u:
                continue
            uid = int(u.id)
            upid = int(getattr(upd, "update_id", 0) or 0)
            if upid > int(latest_per_user.get(uid, 0)):
                latest_per_user[uid] = upid
        application.bot_data["_startup_latest_pending_per_user"] = latest_per_user
        if latest_per_user:
            logger.info(
                "Startup pending filter prepared for %s user(s).",
                len(latest_per_user),
            )
    except Conflict:
        logger.warning("Startup pending filter skipped due to active polling session.")
    except Exception as e:
        logger.warning(f"Failed to prepare startup pending filter: {e}")

    try:
        existing_task = application.bot_data.get("_direct_buy_delivery_task")
        if existing_task is not None and existing_task.done():
            application.bot_data.pop("_direct_buy_delivery_task", None)
            existing_task = None
        if existing_task is None:
            task = application.create_task(_direct_buy_delivery_loop(application))
            application.bot_data["_direct_buy_delivery_task"] = task
    except Exception as e:
        logger.warning(f"Failed to start direct-buy delivery loop: {e}")

    try:
        if application.job_queue is not None and not application.bot_data.get("_pending_card_admin_notify_job_registered"):
            application.job_queue.run_repeating(
                _pending_card_admin_notify_job,
                interval=25,
                first=8,
                name="pending-card-admin-notifier",
            )
            application.bot_data["_pending_card_admin_notify_job_registered"] = True
    except Exception as e:
        logger.warning(f"Failed to register pending-card admin notifier job: {e}")

    if USERBOT_TICKET_AUTOCLOSE_ENABLED and application.job_queue is not None:
        try:
            if not application.bot_data.get("_ticket_autoclose_job_registered"):
                application.job_queue.run_repeating(
                    _ticket_autoclose_job,
                    interval=max(60, USERBOT_TICKET_AUTOCLOSE_INTERVAL_SECONDS),
                    first=45,
                    name="userbot-ticket-autoclose",
                )
                application.bot_data["_ticket_autoclose_job_registered"] = True
                logger.info(
                    "Ticket auto-close job enabled (threshold=%sh, interval=%ss)",
                    USERBOT_TICKET_AUTOCLOSE_HOURS,
                    max(60, USERBOT_TICKET_AUTOCLOSE_INTERVAL_SECONDS),
                )
        except Exception as e:
            logger.warning("Failed to schedule ticket auto-close job: %s", e)


async def _post_shutdown_userbot(application):
    try:
        task = application.bot_data.pop("_direct_buy_delivery_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("Direct-buy delivery loop shutdown error: %s", e)
            logger.info("Direct-buy delivery loop stopped.")
    except Exception as e:
        logger.warning("UserBot post-shutdown cleanup failed: %s", e)


async def _ticket_autoclose_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        closed_count = userbot_db.auto_close_stale_open_tickets(USERBOT_TICKET_AUTOCLOSE_HOURS)
        if closed_count > 0:
            logger.info(
                "Auto-closed stale tickets: count=%s threshold=%sh",
                closed_count,
                USERBOT_TICKET_AUTOCLOSE_HOURS,
            )
    except Exception as e:
        logger.warning("Ticket auto-close job failed: %s", e)


async def _userbot_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    خطاهای transient شبکه تلگرام را نرم هندل می‌کنیم تا ربات پایدار بماند.
    """
    err = context.error
    if isinstance(err, NetworkError):
        logger.warning("Transient Telegram network error: %s", err)
        return
    logger.error("Unhandled exception while processing update", exc_info=err)


def _attach_userbot_handlers(app) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", language_command))

    # هندلرهای منوی متنی پایین صفحه
    app.add_handler(MessageHandler(filters.Regex("خرید اشتراک"), menu_handler))
    app.add_handler(MessageHandler(filters.Regex("کیف پول"), menu_handler))
    app.add_handler(MessageHandler(filters.Regex("وضعیت اشتراک"), menu_handler))
    app.add_handler(MessageHandler(filters.Regex("اتصال اشتراک"), menu_handler))
    app.add_handler(MessageHandler(filters.Regex("تمدید اشتراک"), menu_handler))
    app.add_handler(MessageHandler(filters.Regex("تست رایگان"), menu_handler))
    app.add_handler(MessageHandler(filters.Regex("پشتیبانی"), menu_handler))
    app.add_handler(MessageHandler(filters.Regex("راهنما"), menu_handler))
    app.add_handler(MessageHandler(filters.Regex("سوالات متداول"), menu_handler))
    app.add_handler(MessageHandler(filters.Regex("دعوت دوستان"), menu_handler))

    # هندلرهای شیشه‌ای (Inline)
    app.add_handler(CallbackQueryHandler(inline_handler))

    # هندلر دریافت عکس یا متن لغو رسید
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, receipt_handler))
    app.add_error_handler(_userbot_error_handler)

# --- 9. راه‌اندازی ربات ---
_USERBOT_PID_FILE = os.path.join(LOG_DIR, "userbot.pid")
_pid_lock_fd = None  # global file descriptor for flock

def _acquire_pid_lock() -> bool:
    """Prevent multiple UserBot instances by PID file locking.
    Uses fcntl.flock for atomic lock acquisition to prevent race conditions.
    If an old instance is found, kill it and take over."""
    global _pid_lock_fd
    try:
        os.makedirs(LOG_DIR, exist_ok=True)

        # Open PID file with exclusive non-blocking lock (atomic operation)
        _pid_lock_fd = open(_USERBOT_PID_FILE, "a+")
        try:
            fcntl.flock(_pid_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.error("Another UserBot instance is already starting. Exiting.")
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
                    logger.warning("Stale UserBot PID %s found. Sending SIGTERM...", old_pid)
                    try:
                        os.kill(old_pid, 15)
                        for _ in range(10):
                            if not os.path.exists(f"/proc/{old_pid}"):
                                break
                            time.sleep(0.5)
                        if os.path.exists(f"/proc/{old_pid}"):
                            os.kill(old_pid, 9)
                            time.sleep(0.5)
                        logger.info("Old UserBot instance (PID %s) terminated.", old_pid)
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
        if os.path.exists(_USERBOT_PID_FILE):
            try:
                os.remove(_USERBOT_PID_FILE)
            except Exception:
                pass
    except Exception:
        pass

def main():
    if not _acquire_pid_lock():
        sys.exit(1)
    import atexit
    atexit.register(_release_pid_lock)
    if SUB_SERVER_ENABLED:
        try:
            sub_http_server.start_sub_server(SUB_SERVER_HOST, SUB_SERVER_PORT)
            logger.info("✅ Sub server enabled on %s:%s", SUB_SERVER_HOST, SUB_SERVER_PORT)
        except Exception as e:
            logger.warning("⚠️ Failed to start sub server: %s", e)

    # پایدارسازی ارتباط با Telegram (کاهش خطاهای RemoteProtocolError)
    base_request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=10.0,
        http_version="1.1",
    )
    updates_request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=45.0,
        write_timeout=30.0,
        pool_timeout=10.0,
        http_version="1.1",
    )
    logger.info("UserBot Started Successfully...")
    while True:
        app = (
            ApplicationBuilder()
            .token(TOKEN)
            .request(base_request)
            .get_updates_request(updates_request)
            .post_init(_post_init_set_menu)
            .post_shutdown(_post_shutdown_userbot)
            .build()
        )
        _attach_userbot_handlers(app)
        try:
            app.run_polling(
                drop_pending_updates=False,
                timeout=30,
                bootstrap_retries=-1,
                poll_interval=1.0,
            )
            break
        except NetworkError as e:
            logger.warning("Polling stopped by transient network error: %s | retry in 5s", e)
            time.sleep(5)
        except Conflict as e:
            logger.error("Polling conflict — another bot instance is running with the same token: %s | retry in 30s", e)
            time.sleep(30)
        except Exception as e:
            logger.error("Polling crashed: %s | retry in 5s", e, exc_info=True)
            time.sleep(5)


# --- extracted inline_handler branches (verbatim bodies in UserBot/callback_branches.py) ---
from UserBot import callback_branches as _cb_branches
from UserBot.callback_branches import _cb_lang_set, _cb_guide, _cb_invite, _cb_support, _cb_status, _cb_renew, _cb_wallet, _cb_pay, _cb_trial_back, _cb_trial_loc, _cb_buy_back_main, _cb_buy_exit_main

_cb_branches.bind_main_namespace({
    "USERBOT_MISSING_SERVICE_DELETE_DAYS": USERBOT_MISSING_SERVICE_DELETE_DAYS,
    "_build_subscription_status_text": _build_subscription_status_text,
    "_build_user_ticket_screenshot_links": _build_user_ticket_screenshot_links,
    "_build_zarinpal_links_keyboard": _build_zarinpal_links_keyboard,
    "_default_faq_text": _default_faq_text,
    "_default_guide_intro_text": _default_guide_intro_text,
    "_default_zarinpal_text": _default_zarinpal_text,
    "_format_text_template": _format_text_template,
    "_get_location_servers": _get_location_servers,
    "_get_marketing_settings": _get_marketing_settings,
    "_get_or_create_bot_sub_links": _get_or_create_bot_sub_links,
    "_get_payment_settings": _get_payment_settings,
    "_get_service_node_base_urls": _get_service_node_base_urls,
    "_get_subscription_settings": _get_subscription_settings,
    "_get_text_settings": _get_text_settings,
    "_get_user_bot_username": _get_user_bot_username,
    "_guide_platform_text": _guide_platform_text,
    "_is_connected_service": _is_connected_service,
    "_main_menu_keyboard": _main_menu_keyboard,
    "_notify_admin_new_ticket": _notify_admin_new_ticket,
    "_notify_admin_ticket_reply": _notify_admin_ticket_reply,
    "_older_than_days": _older_than_days,
    "_regenerate_service_uuid_for_service": _regenerate_service_uuid_for_service,
    "_renew_not_allowed_text": _renew_not_allowed_text,
    "_resolve_service_access_lock": _resolve_service_access_lock,
    "_safe_edit_message_reply_markup": _safe_edit_message_reply_markup,
    "_safe_edit_message_text": _safe_edit_message_text,
    "_send_buy_flow_for_server": _send_buy_flow_for_server,
    "_send_long_message": _send_long_message,
    "_send_service_direct_configs_shell": _send_service_direct_configs_shell,
    "_send_support_panel": _send_support_panel,
    "_service_is_renewable_live": _service_is_renewable_live,
    "_service_local_lock_text": _service_local_lock_text,
    "_service_probe_state": _service_probe_state,
    "_should_show_configs_button": _should_show_configs_button,
    "_sync_service_runtime_from_panels": _sync_service_runtime_from_panels,
    "_ticket_compose_preview_text": _ticket_compose_preview_text,
    "_ticket_detail_text": _ticket_detail_text,
    "_ticket_reply_preview_text": _ticket_reply_preview_text,
    "_ticket_text_cancel_keyboard": _ticket_text_cancel_keyboard,
    "get_user_step": get_user_step,
    "logger": logger,
    "set_user_step": set_user_step,
})

if __name__ == '__main__':
    main()
