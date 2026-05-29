import logging
import os
import re
import asyncio
import json
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from html import escape
from typing import Any, Dict, List, Optional

import qrcode
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes
from telegram.error import BadRequest, NetworkError
from pathlib import Path
import sys
from urllib.parse import urlparse

# اضافه کردن ریشه پروژه به sys.path تا Shared و بقیه ماژول‌ها پیدا شوند
ROOT_DIR = Path(__file__).resolve().parents[1]  # پوشه‌ی Hiddify-SellBot
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from Shared import database, hiddify_api, userbot_db, plans_storage
from AdminBot.keyboards import (
    admin_main_keyboard,
    confirm_add_user_keyboard,
    BTN_SERVERS,
    BTN_SEARCH_USER,
    BTN_USERBOT,
    BTN_STATUS,
    BTN_BACKUP,
    cancel_keyboard,
)


from AdminBot.plans import (
    send_plans_root_menu,
    handle_plans_callback,
    handle_plans_message,
)
from AdminBot.nodes import (
    send_nodes_menu,
    handle_nodes_state_message,
    handle_nodes_inline_callback,
)
from AdminBot.userbot import (
    handle_userbot_entry,
    handle_userbot_callback,
    handle_admin_text_input,
    handle_user_search_message,
    make_bot_backup_zip,
    prune_full_backup_files,
    WALLET_EDIT_STATE,
    MESSAGE_SEND_STATE,
    SUB_REMINDER_EDIT_STATE,
    TRIAL_SPEC_EDIT_STATE,
    RENEW_POLICY_EDIT_STATE,
    USER_SEARCH_STATE_KEY,
    ORDERS_SEARCH_STATE_KEY,
    PAYMENT_SEARCH_STATE,
    TEXT_SETTINGS_EDIT_STATE,
    INVITE_BANNER_PHOTO_EDIT_STATE,
    MARKETING_EDIT_STATE,
    FORCE_JOIN_EDIT_STATE,
    PAYMENT_CHANNEL_EDIT_STATE,
    BACKUP_CHANNEL_EDIT_STATE,
    BACKUP_RESTORE_STATE,
    PAYMENT_CARD_ADD_STATE,
    PAYMENT_CARD_DELETE_STATE,
    PAYMENT_CARD_EDIT_STATE,
    ZARIN_COUPON_ADD_STATE,
    ZARIN_COUPON_DELETE_STATE,
    ZARIN_COUPON_LINK_STATE,
    ZARIN_COUPON_AMOUNT_STATE,
    ZARIN_COUPON_CODE_STATE,
    ZARIN_COUPON_LIMIT_STATE,
    ZARIN_COUPON_EXP_STATE,
    SUB_TRACKING_STATE,
    TICKET_REPLY_STATE,
    BROADCAST_SEND_STATE,
    SUB_BASE_URL_EDIT_STATE,
)


load_dotenv()
SUB_BOT_USERNAME = os.getenv("SUB_BOT_USERNAME", "")
PANEL_PREREQ_SCRIPT_URL = (
    os.getenv("PANEL_PREREQ_SCRIPT_URL", "")
    or "https://raw.githubusercontent.com/mojtaba-glx/Hiddify-Panel-Prereq/main/install.sh"
).strip()
SERVER_DISPLAY_VERSION = (os.getenv("SERVER_DISPLAY_VERSION", "V11,12") or "V11,12").strip()

logger = logging.getLogger(__name__)

# ===============================
#   ثابت‌ها و کمک‌کننده‌ها
# ===============================

CANCEL_WORDS = {"لغو❌", "لغو", "/cancel"}


def _menu_key(text: str) -> str:
    """
    Normalize menu text from Telegram clients (remove hidden marks/emojis/spaces)
    so button matching stays stable.
    """
    t = (text or "").strip()
    for ch in ("\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u2066", "\u2067", "\u2068", "\u2069"):
        t = t.replace(ch, "")
    t = re.sub(r"[^\w\u0600-\u06FF]+", "", t)
    return t


def _is_cancel_text(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.lower() == "/cancel":
        return True
    key = _menu_key(raw).lower()
    return key in {"لغو", "cancel"}


def _is_confirm_text(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    key = _menu_key(raw).lower()
    return key in {"تایید", "تائید", "تاييد", "confirm", "yes", "ok"}


def _is_servers_button(text: str, text_key: str) -> bool:
    return text in {BTN_SERVERS, "مدیریت سرورها🖥️"} or "مدیریتسرورها" in text_key


def _is_search_button(text: str, text_key: str) -> bool:
    return text in {BTN_SEARCH_USER, "جستجوی کاربر🔍"} or "جستجویکاربر" in text_key


def _is_userbot_button(text: str, text_key: str) -> bool:
    return text == BTN_USERBOT or "مدیریترباتکاربران" in text_key


def _is_status_button(text: str, text_key: str) -> bool:
    return text in {BTN_STATUS, "وضعیت سرور📈"} or "وضعیتسرور" in text_key


def _is_backup_button(text: str, text_key: str) -> bool:
    return text in {BTN_BACKUP, "دریافت بکاپ📬"} or "دریافتبکاپ" in text_key


def _is_any_main_menu_button(text: str, text_key: str) -> bool:
    return (
        _is_servers_button(text, text_key)
        or _is_search_button(text, text_key)
        or _is_userbot_button(text, text_key)
        or _is_status_button(text, text_key)
        or _is_backup_button(text, text_key)
    )


def _clear_search_states(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("state", None)
    context.user_data.pop("search_scope", None)
    context.user_data.pop("smart_search_results", None)
    context.user_data.pop(USER_SEARCH_STATE_KEY, None)


def _backup_storage_dir() -> Path:
    backup_dir = ROOT_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def _safe_backup_name(name: str, default: str = "backup.json") -> str:
    raw = str(name or "").strip().replace("\\", "/")
    raw = raw.split("/")[-1]
    raw = re.sub(r'[^A-Za-z0-9._\- @()\u0600-\u06FF]+', "_", raw).strip(" .")
    return raw or default


def _server_title(server: Dict[str, Any]) -> str:
    title = str(server.get("title") or "").strip()
    if title:
        return title
    return f"server-{server.get('id', '?')}"


def _format_server_location_title(title: str) -> str:
    """
    Normalize server title to look like: «لوکیشن 🇩🇪 آلمان»
    while avoiding duplicate location word/flag.
    """
    raw = str(title or "").strip()
    if not raw:
        return "لوکیشن نامشخص"

    flag = ""
    if "ترکیه" in raw:
        flag = "🇹🇷"
    elif "آلمان" in raw:
        flag = "🇩🇪"
    elif "هلند" in raw:
        flag = "🇳🇱"
    elif "فنلاند" in raw:
        flag = "🇫🇮"
    elif "هند" in raw:
        flag = "🇮🇳"

    has_location_word = "لوکیشن" in raw
    has_flag = bool(flag) and (flag in raw)
    if has_location_word:
        if has_flag:
            return raw
        return f"{raw} {flag}".strip()

    if flag:
        return f"لوکیشن {flag} {raw}".strip()
    return f"لوکیشن {raw}".strip()


def _build_full_backup_zip(
    bot_backup_path: Path,
    panel_backups: List[Dict[str, Any]],
    panel_errors: List[str],
) -> Path:
    ts = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")
    out_path = _backup_storage_dir() / f"Backup_All_{ts}.zip"

    if out_path.exists():
        idx = 1
        while True:
            candidate = _backup_storage_dir() / f"Backup_All_{ts}_{idx}.zip"
            if not candidate.exists():
                out_path = candidate
                break
            idx += 1

    used_names: set[str] = set()
    with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as out_zip:
        # Keep bot backup entries at root to remain restore-compatible.
        with zipfile.ZipFile(bot_backup_path, mode="r") as bot_zip:
            for info in bot_zip.infolist():
                if info.is_dir():
                    continue
                out_zip.writestr(info.filename, bot_zip.read(info.filename))

        for item in panel_backups:
            server_id = int(item.get("server_id") or 0)
            server_name = _safe_backup_name(str(item.get("server_title") or f"server-{server_id}"), default=f"server-{server_id}")
            filename = _safe_backup_name(str(item.get("filename") or f"server-{server_id}.json"))

            base_arc = f"PanelBackups/{server_name}/{filename}"
            arcname = base_arc
            suffix = 1
            while arcname in used_names:
                stem, dot, ext = filename.rpartition(".")
                stem = stem or filename
                ext = f".{ext}" if dot else ""
                arcname = f"PanelBackups/{server_name}/{stem}_{suffix}{ext}"
                suffix += 1

            used_names.add(arcname)
            out_zip.writestr(arcname, item.get("content") or b"")

        manifest = {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "backup_type": "full",
            "bot_backup_file": bot_backup_path.name,
            "panel_backups_count": len(panel_backups),
            "panel_errors_count": len(panel_errors),
            "panel_backups": [
                {
                    "server_id": int(i.get("server_id") or 0),
                    "server_title": str(i.get("server_title") or ""),
                    "filename": str(i.get("filename") or ""),
                    "source_url": str(i.get("source_url") or ""),
                    "size": len(i.get("content") or b""),
                }
                for i in panel_backups
            ],
            "panel_errors": panel_errors,
        }
        out_zip.writestr(
            f"Backup_All_{ts}.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    return out_path


async def send_admin_full_backup(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message=None) -> None:
    if message:
        await message.reply_text("⏳ در حال تهیه بکاپ کامل (ربات + سرورها/نودها)...")
    else:
        await context.bot.send_message(chat_id, "⏳ در حال تهیه بکاپ کامل (ربات + سرورها/نودها)...")

    bot_backup_path: Optional[Path] = None
    full_backup_path: Optional[Path] = None
    try:
        bot_backup_path = await asyncio.to_thread(make_bot_backup_zip)
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ خطا در ساخت بکاپ ربات:\n{e}")
        return

    servers = database.get_servers() or []
    panel_backups: List[Dict[str, Any]] = []
    panel_errors: List[str] = []

    for server in servers:
        sid = int(server.get("id") or 0)
        stitle = _server_title(server)
        try:
            data = await hiddify_api.download_server_backup(server)
            panel_backups.append(
                {
                    "server_id": sid,
                    "server_title": stitle,
                    "filename": str(data.get("filename") or ""),
                    "content": data.get("content") or b"",
                    "source_url": str(data.get("source_url") or ""),
                }
            )
        except Exception as e:
            panel_errors.append(f"{stitle} (id={sid}): {e}")

    try:
        full_backup_path = await asyncio.to_thread(
            _build_full_backup_zip,
            bot_backup_path,
            panel_backups,
            panel_errors,
        )
        await asyncio.to_thread(prune_full_backup_files, 50)
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ خطا در ساخت فایل بکاپ نهایی:\n{e}")
        return

    caption = (
        "📬 فایل بکاپ کامل آماده شد\n"
        f"🤖 بکاپ ربات: ✅\n"
        f"🖥️ بکاپ سرورها/نودها: {len(panel_backups)} مورد\n"
        f"⚠️ خطاها: {len(panel_errors)} مورد"
    )
    try:
        with full_backup_path.open("rb") as fh:
            await context.bot.send_document(
                chat_id=chat_id,
                document=fh,
                filename=full_backup_path.name,
                caption=caption,
            )
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ خطا در ارسال بکاپ:\n{e}")
        return
    finally:
        # فایل واسط Backup_Bot فقط برای ساخت Backup_All است و بعد از اتمام باید حذف شود
        try:
            if bot_backup_path and bot_backup_path.exists():
                bot_backup_path.unlink(missing_ok=True)
        except Exception:
            pass

    if panel_errors:
        preview = "\n".join(panel_errors[:10])
        more = ""
        if len(panel_errors) > 10:
            more = f"\n... و {len(panel_errors) - 10} خطای دیگر"
        await context.bot.send_message(
            chat_id,
            "⚠️ برخی بکاپ‌های پنل دریافت نشدند:\n" + preview + more,
        )

# --- state ها برای ماشین حالت‌ها ---

# دامنه
ADD_DOMAIN_TITLE = "add_domain_title"
ADD_DOMAIN_DOMAIN = "add_domain_domain"

# افزودن سرور
ADD_STATE_TITLE = "add_server_title"
ADD_STATE_PANEL_URL = "add_server_panel_url"
ADD_STATE_ADMIN_PROXY = "add_server_admin_proxy"
ADD_STATE_ADMIN_UUID = "add_server_admin_uuid"
ADD_STATE_USER_PROXY = "add_server_user_proxy"
ADD_STATE_LIMIT = "add_server_limit"

# ویرایش سرور
EDIT_SERVER_TITLE = "edit_server_title"
EDIT_SERVER_PANEL_URL = "edit_server_panel_url"
EDIT_SERVER_ADMIN_PROXY = "edit_server_admin_proxy"
EDIT_SERVER_ADMIN_UUID = "edit_server_admin_uuid"
EDIT_SERVER_USER_PROXY = "edit_server_user_proxy"
EDIT_SERVER_LIMIT = "edit_server_limit"
EDIT_SERVER_PRIORITY = "edit_server_priority"

# افزودن کاربر
ADD_USER_NAME = "add_user_name"
ADD_USER_USAGE = "add_user_usage"
ADD_USER_DAYS = "add_user_days"
ADD_USER_CONFIRM = "add_user_confirm"
ADD_USER_PLAN_NAME = "add_user_plan_name"
ADD_USER_PLAN_CONFIRM = "add_user_plan_confirm"

# ویرایش کاربر
EDIT_STATE_NAME = "edit_user_name"
EDIT_STATE_USAGE = "edit_user_usage"
EDIT_STATE_DAYS = "edit_user_days"
EDIT_STATE_COMMENT = "edit_user_comment"

# جستجوی هوشمند
SEARCH_SMART_INPUT = "search_smart_input"


def _parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def _compute_package_info(user: Dict[str, Any]):
    start_dt = _parse_dt(user.get("start_date"))
    package_days = user.get("package_days")
    if not start_dt or not package_days:
        return None, None, None
    try:
        end_dt = start_dt + timedelta(days=int(package_days))
        days_left = (end_dt.date() - datetime.now(timezone.utc).replace(tzinfo=None).date()).days
        return start_dt, end_dt, days_left
    except Exception:
        return None, None, None


def format_gb(value: Any) -> str:
    if value is None:
        return "نامشخص"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v.is_integer():
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _format_usage_current(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "نامشخص"
    text = f"{v:.2f}"
    if text.endswith("00"):
        return f"{v:.1f}"
    if text.endswith("0"):
        return text[:-1]
    return text


def _format_usage_limit(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "نامشخص"
    return f"{v:.1f}"


def _display_safe_note(note_text: Any) -> str:
    raw = str(note_text or "").strip()
    if not raw:
        return "—"
    fa_digits = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    return re.sub(
        r"(?i)\b(HiddifyBot:\s*[\u200c\u200d\u200e\u200f]*)([0-9]+)\b",
        lambda m: f"{m.group(1)}{m.group(2).translate(fa_digits)}",
        raw,
    )


def _extract_hiddifybot_telegram_id(raw_comment: Any) -> Optional[int]:
    text = str(raw_comment or "").strip()
    if not text:
        return None
    en = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    m = re.search(r"(?i)\bhiddifybot:\s*([0-9]+)\b", en)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _resolve_owner_for_panel_user(
    panel_user_uuid: str,
    panel_comment: Any = "",
) -> Optional[Dict[str, Any]]:
    owner = userbot_db.get_service_owner_by_panel_uuid(str(panel_user_uuid or "").strip())
    if owner:
        return owner

    tg_id = _extract_hiddifybot_telegram_id(panel_comment)
    if not tg_id:
        return None
    user = userbot_db.get_user_by_telegram_id(tg_id)

    if user:
        return {
            "user_id": user.get("id"),
            "telegram_id": user.get("telegram_id"),
            "username": user.get("username"),
            "full_name": user.get("full_name"),
        }

    return None


def _owner_button_title(owner: Dict[str, Any]) -> str:
    full_name = str(owner.get("full_name") or "").strip()
    if full_name:
        return full_name
    username = str(owner.get("username") or "").strip().lstrip("@")
    if username:
        return username
    telegram_id = owner.get("telegram_id")
    return str(telegram_id) if telegram_id is not None else "پروفایل کاربر"


async def _resolve_panel_user_uuid(
    server: Dict[str, Any],
    server_id: int,
    user_ref: Any,
) -> str:
    """
    Resolve a stable panel UUID for user operations.
    user_ref can be UUID or local/panel numeric ID.
    """
    ref = str(user_ref or "").strip()
    if not ref:
        return ref

    try:
        data = await hiddify_api.get_user_by_uuid(server, ref)
        resolved = str((data or {}).get("uuid") or ref).strip()
        if resolved:
            return resolved
    except Exception:
        pass

    try:
        local_id = int(ref)
    except (TypeError, ValueError):
        local_id = None

    if local_id is not None:
        try:
            local_user = database.get_user(server_id, local_id) or {}
        except Exception:
            local_user = {}
        local_uuid = str(local_user.get("uuid") or "").strip()
        if local_uuid:
            return local_uuid

    try:
        users = await hiddify_api.list_users(server)
        for u in users or []:
            if str(u.get("id") or "").strip() == ref:
                found_uuid = str(u.get("uuid") or "").strip()
                if found_uuid:
                    return found_uuid
    except Exception:
        pass

    return ref


async def _set_user_active_state_on_related_servers(
    server_id: int,
    user_uuid: str,
    *,
    active: bool,
) -> tuple[str, int, int, List[str]]:
    """
    Enable/disable user on full related cluster (main + nodes).
    Returns: (resolved_uuid, changed_count, total_targets, failed_titles)
    """
    server = database.get_server_by_id(server_id)
    if not server:
        return str(user_uuid or "").strip(), 0, 0, ["سرور اصلی پیدا نشد"]

    resolved_uuid = await _resolve_panel_user_uuid(server, server_id, user_uuid)
    targets = _get_related_server_targets(server_id)
    if not targets:
        targets = [server]

    changed = 0
    failed: List[str] = []
    for target in targets:
        try:
            tid = int(target.get("id") or 0)
        except (TypeError, ValueError):
            tid = 0
        title = (target.get("title") or f"سرور #{tid or '?'}").strip()
        try:
            if active:
                await hiddify_api.enable_user(target, resolved_uuid)
            else:
                await hiddify_api.disable_user(target, resolved_uuid)
            changed += 1
        except Exception as e:
            logger.warning(
                "Failed setting active=%s for user_uuid=%s on server_id=%s (%s): %s",
                active,
                resolved_uuid,
                tid,
                title,
                e,
            )
            failed.append(title)

    return resolved_uuid, changed, len(targets), failed


async def _patch_user_on_related_servers(
    server_id: int,
    user_uuid: str,
    patch_data: Dict[str, Any],
) -> tuple[str, int, int, List[str]]:
    """
    Apply patch_user on full related cluster (main + nodes).
    Returns: (resolved_uuid, changed_count, total_targets, failed_titles)
    """
    server = database.get_server_by_id(server_id)
    if not server:
        return str(user_uuid or "").strip(), 0, 0, ["سرور اصلی پیدا نشد"]

    resolved_uuid = await _resolve_panel_user_uuid(server, server_id, user_uuid)
    targets = _get_related_server_targets(server_id)
    if not targets:
        targets = [server]

    changed = 0
    failed: List[str] = []
    for target in targets:
        try:
            tid = int(target.get("id") or 0)
        except (TypeError, ValueError):
            tid = 0
        title = (target.get("title") or f"سرور #{tid or '?'}").strip()

        target_uuid = resolved_uuid
        if tid > 0:
            try:
                target_uuid = await _resolve_panel_user_uuid(
                    target, tid, resolved_uuid
                )
            except Exception:
                target_uuid = resolved_uuid

        try:
            await hiddify_api.patch_user(target, target_uuid, patch_data)
            changed += 1
        except Exception as e:
            logger.warning(
                "Failed patching user on server_id=%s (%s), uuid=%s, patch=%s: %s",
                tid,
                title,
                target_uuid,
                patch_data,
                e,
            )
            failed.append(title)

    return resolved_uuid, changed, len(targets), failed


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "on"}:
            return True
        if v in {"false", "0", "no", "off"}:
            return False
    return None


def _panel_user_is_active(user_data: Dict[str, Any]) -> bool:
    """
    Detect active/disabled state from heterogeneous panel fields.
    """
    mode = str(user_data.get("mode") or "").strip().lower()
    status = str(user_data.get("status") or "").strip().lower()

    if mode in {"disable", "disabled", "inactive"}:
        return False
    if status in {"disable", "disabled", "inactive", "deactive", "off"}:
        return False

    explicit_false = False
    explicit_true = False
    for key in ("is_active", "active", "enabled", "enable"):
        b = _to_bool(user_data.get(key))
        if b is False:
            explicit_false = True
        elif b is True:
            explicit_true = True

    if explicit_false:
        return False
    if explicit_true:
        return True

    if mode in {"no_reset", "active", "enabled", "enable"}:
        return True
    if status in {"active", "enabled", "enable", "on"}:
        return True

    # default in panel is usually active unless explicitly disabled
    return True


# مثلا ۵ دقیقه؛ اگر خواستی مثل پنل دقیق‌تر بشه، همین عدد رو بعدا تنظیم می‌کنیم
ONLINE_WINDOW_SECONDS = 5 * 60


# بازه تشخیص آنلاین بودن (اینجا ۱۵ دقیقه در نظر گرفتیم مثل خیلی از پنل‌ها)
ONLINE_WINDOW_SECONDS = 15 * 60
# تلورانس برای اختلاف ساعت‌های خیلی کم (۱-۲ دقیقه)
CLOCK_SKEW_TOLERANCE = 120


def classify_user_status(user: Dict[str, Any]) -> str:
    """
    تعیین وضعیت کاربر:
    - expired اگر تاریخ پکیج گذشته باشد
    - online اگر last_online در بازه زمانی آنلاین باشد
    - offline در غیر این صورت
    """
    # 1) اگر کاربر غیرفعال باشد، منقضی در نظر گرفته می‌شود.
    is_active = _to_bool(user.get("is_active"))
    if is_active is False:
        return "expired"

    # 2) اگر انقضای زمانی در فیلدهای متداول وجود داشته باشد.
    for key in ("expire", "expire_date", "end_date", "expiration_date", "expires_at"):
        end_dt = _parse_dt(user.get(key))
        if end_dt and end_dt.date() < datetime.now(timezone.utc).replace(tzinfo=None).date():
            return "expired"

    # 3) انقضای زمان پکیج
    _, _, days_left = _compute_package_info(user)
    if days_left is not None and days_left < 0:
        return "expired"

    # 4) انقضای حجمی (فقط اگر سقف حجم > 0 باشد؛ صفر یا خالی یعنی نامحدود)
    usage_limit = _to_float(user.get("usage_limit_GB"))
    usage_current = _to_float(user.get("current_usage_GB"))
    if (
        usage_limit is not None
        and usage_current is not None
        and usage_limit > 0
        and usage_current >= usage_limit
    ):
        return "expired"

    last_online_dt = _parse_dt(user.get("last_online"))
    if last_online_dt:
        try:
            # نکته مهم: از now() استفاده می‌کنیم (ساعت لوکال سرور)
            # نه utcnow() → تا با زمانی که پنل ذخیره کرده هم‌تایم باشد
            now = datetime.now()
            delta = now - last_online_dt
            seconds = delta.total_seconds()

            # اگر زمان last_online کمی جلوتر از now باشد (تا ۲ دقیقه)
            # یا تا ۱۵ دقیقه قبل باشد → آنلاین حسابش می‌کنیم
            if -CLOCK_SKEW_TOLERANCE <= seconds <= ONLINE_WINDOW_SECONDS:
                return "online"
        except Exception:
            # اگر فرمت تاریخ مشکل داشت، می‌افتد روی offline
            pass

    # اگر نه منقضی بود و نه تو بازه‌ی آنلاین، می‌شود آفلاین
    return "offline"


def make_qr_image(data: str) -> BytesIO:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    bio.name = "qr.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


def _build_user_base_url(server: Dict[str, Any], user_uuid: str) -> Optional[str]:
    """
    آدرس پایه‌ی کاربر در پنل را می‌سازد.
    اگر برای سرور دامنه‌ی نمایش تعریف شده باشد، به جای panel_url از آن استفاده می‌کنیم.
    """
    panel_url = (server.get("panel_url") or "").rstrip("/")
    user_proxy = (server.get("user_proxy_path") or "").strip("/")
    if not panel_url or not user_proxy:
        return None

    # آدرس اصلی (بر اساس پنل)
    base_url = f"{panel_url}/{user_proxy}/{user_uuid}"

    # اگر دامنه‌ی نمایش تعریف شده باشد، فقط دامنه را عوض می‌کنیم
    domains = server.get("domains") or []
    display_domain: Optional[str] = None

    if domains:
        best_score = -10**9
        best_domain = None
        for d in domains:
            if isinstance(d, dict):
                raw_domain = (
                    d.get("domain")
                    or d.get("host")
                    or d.get("url")
                    or ""
                )
                title = (d.get("title") or d.get("name") or "").strip().lower()
            else:
                raw_domain = str(d)
                title = ""
            raw_domain = str(raw_domain).strip()
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
                best_domain = raw_domain
        display_domain = best_domain

    if display_domain:
        display_domain = display_domain.strip()
        # اگر بدون http/https وارد شده، https را اضافه می‌کنیم
        if not (display_domain.startswith("http://") or display_domain.startswith("https://")):
            display_domain = "https://" + display_domain

        # فقط ابتدای لینک (panel_url) را با دامنه‌ی جدید عوض می‌کنیم
        base_url = base_url.replace(panel_url, display_domain.rstrip("/"), 1)

    return base_url


# ===============================
#   سرورها
# ===============================

def _get_child_server_ids() -> set[int]:
    """
    سرورهایی که فقط نودِ یک سرور دیگر هستند.
    این‌ها باید از لیست مدیریت سرورها مخفی شوند.
    """
    child_ids: set[int] = set()
    for s in (database.get_servers() or []):
        for n in (s.get("nodes") or []):
            if not isinstance(n, dict):
                continue
            try:
                cid = int(n.get("target_server_id") or 0)
            except (TypeError, ValueError):
                cid = 0
            if cid > 0:
                child_ids.add(cid)
    return child_ids


def _get_related_server_targets(server_id: int) -> List[Dict[str, Any]]:
    """
    خوشه‌ی مرتبط با یک سرور را برمی‌گرداند:
    - خود سرور
    - اگر سرور اصلی باشد: تمام نودهای target_server_id آن
    - اگر سرور نود باشد: سرور اصلی(ها) + نودهای همان سرور اصلی
    """
    servers = database.get_servers() or []
    by_id: Dict[int, Dict[str, Any]] = {}
    for s in servers:
        try:
            sid = int(s.get("id") or 0)
        except (TypeError, ValueError):
            sid = 0
        if sid > 0:
            by_id[sid] = s

    target_ids: set[int] = set()
    if server_id in by_id:
        target_ids.add(server_id)

    # اگر خودش Parent باشد، نودهایش را بگیر
    parent = by_id.get(server_id)
    if parent:
        for n in (parent.get("nodes") or []):
            if not isinstance(n, dict):
                continue
            try:
                nid = int(n.get("target_server_id") or 0)
            except (TypeError, ValueError):
                nid = 0
            if nid > 0 and nid in by_id:
                target_ids.add(nid)

    # اگر خودش Node باشد، Parent(ها) و نودهای همان Parent را بگیر
    for p in servers:
        try:
            pid = int(p.get("id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            continue
        p_nodes = p.get("nodes") or []
        if not isinstance(p_nodes, list):
            continue
        has_current = False
        for n in p_nodes:
            if not isinstance(n, dict):
                continue
            try:
                nid = int(n.get("target_server_id") or 0)
            except (TypeError, ValueError):
                nid = 0
            if nid == server_id:
                has_current = True
                break
        if not has_current:
            continue
        target_ids.add(pid)
        for n in p_nodes:
            if not isinstance(n, dict):
                continue
            try:
                nid = int(n.get("target_server_id") or 0)
            except (TypeError, ValueError):
                nid = 0
            if nid > 0 and nid in by_id:
                target_ids.add(nid)

    # مرتب‌سازی: ابتدا server_id جاری، سپس بقیه
    ordered_ids = [server_id] + sorted(i for i in target_ids if i != server_id)
    result: List[Dict[str, Any]] = []
    for sid in ordered_ids:
        srv = by_id.get(sid)
        if srv:
            result.append(srv)
    return result


def build_servers_inline_keyboard() -> InlineKeyboardMarkup:
    servers = database.get_servers()
    child_ids = _get_child_server_ids()
    keyboard: List[List[InlineKeyboardButton]] = []
    for s in servers:
        sid = s.get("id")
        if sid is None:
            continue
        try:
            sid_int = int(sid)
        except (TypeError, ValueError):
            continue
        if sid_int in child_ids:
            continue
        title = (s.get("title", f"سرور #{sid}") or "").strip()
        flag = ""
        if "ترکیه" in title:
            flag = "🇹🇷"
        elif "آلمان" in title:
            flag = "🇩🇪"
        elif "هلند" in title:
            flag = "🇳🇱"
        elif "فنلاند" in title:
            flag = "🇫🇮"
        elif "هند" in title:
            flag = "🇮🇳"

        if "لوکیشن" in title:
            btn_text = title
        else:
            btn_text = f"لوکیشن {flag} {title}".strip()

        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"server:{sid_int}")])

    keyboard.append([InlineKeyboardButton("افزودن سرور➕", callback_data="servers:add")])
    return InlineKeyboardMarkup(keyboard)


async def send_servers_list(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    servers = database.get_servers()
    child_ids = _get_child_server_ids()
    count = sum(1 for s in servers if int((s or {}).get("id") or 0) not in child_ids)
    text = (
        "‏🖥 مدیریت سرورها\n"
        "⬇️ لیست سرور های شما"
    )
    kb = build_servers_inline_keyboard()

    if message is not None:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest:
            await message.reply_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)


def build_server_detail_text(
    server: Dict[str, Any],
    *,
    users_count_override: Optional[int] = None,
    plans_count_override: Optional[int] = None,
) -> str:
    title = server.get("title", f"سرور #{server.get('id')}")
    panel_url = (server.get("panel_url") or "").strip()
    admin_proxy = (server.get("admin_proxy_path") or "").strip().strip("/")
    admin_uuid = (server.get("admin_uuid") or server.get("api_key") or "").strip().strip("/")
    server_id = int(server.get("id") or 0)
    users_limit = int(server.get("users_limit") or 0)
    users_count = int(users_count_override) if users_count_override is not None else 0
    plans_count = int(plans_count_override) if plans_count_override is not None else 0
    if users_count_override is None:
        try:
            users_count = len(database.get_users(server_id) or [])
        except Exception:
            users_count = len(server.get("users") or [])
    if plans_count_override is None:
        try:
            mode = str(plans_storage.get_plan_display_mode(server_id) or "dynamic").strip().lower()
            if mode == "dynamic":
                plans_count = 0
            else:
                plans_count = len(plans_storage.get_plans(server_id) or [])
        except Exception:
            plans_count = 0
    priority = int(server.get("priority") or 0)
    version_text = SERVER_DISPLAY_VERSION or f"V{int(server.get('version') or 11)}"
    safe_title = escape(str(title))

    admin_panel_url = ""
    if panel_url.startswith(("http://", "https://")):
        panel_base = panel_url.rstrip("/")
        if admin_proxy and admin_uuid:
            admin_panel_url = f"{panel_base}/{admin_proxy}/{admin_uuid}/"
        elif admin_proxy:
            admin_panel_url = f"{panel_base}/{admin_proxy}/"
        else:
            admin_panel_url = panel_base

    if admin_panel_url:
        title_line = f'<a href="{escape(admin_panel_url, quote=True)}">🖥 سرور: {safe_title}</a>'
    else:
        title_line = f"🖥 سرور: {safe_title}"

    return (
        f"{title_line}\n"
        "❖ • -------------------------- • ❖\n"
        f"👤 تعداد کاربران: {users_count} از {users_limit}\n"
        f"📋 تعداد پلن ها: {plans_count}\n"
        f"🟩 اولویت: {priority}\n"
        f"📦 نسخه: {escape(version_text)}"
    )


async def build_server_detail_text_live(server: Dict[str, Any]) -> str:
    server_id = int(server.get("id") or 0)
    users_count = 0
    try:
        users_count = len(await hiddify_api.list_users(server))
    except Exception as e:
        logger.warning("Failed reading users count from panel (server_id=%s): %s", server_id, e)
        try:
            users_count = len(database.get_users(server_id) or [])
        except Exception:
            users_count = len(server.get("users") or [])

    try:
        mode = str(plans_storage.get_plan_display_mode(server_id) or "dynamic").strip().lower()
        plans_count = 0 if mode == "dynamic" else len(plans_storage.get_plans(server_id) or [])
    except Exception:
        plans_count = 0

    return build_server_detail_text(
        server,
        users_count_override=users_count,
        plans_count_override=plans_count,
    )


def build_user_ops_keyboard(server_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                "افزودن کاربر➕",
                callback_data=f"userops:{server_id}:add",
            )
        ],
        [
            InlineKeyboardButton(
                "افزودن کاربر با پلن➕",
                callback_data=f"userops:{server_id}:add_with_plan",
            )
        ],
        [
            InlineKeyboardButton(
                "جستجوی کاربر🔍",
                callback_data=f"userops:{server_id}:search",
            )
        ],
        [
            InlineKeyboardButton(
                "بازگشت🔙",
                callback_data=f"server:{server_id}",
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_server_detail_keyboard(server_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("👤لیست کاربران", callback_data=f"server:{server_id}:users")],
        [InlineKeyboardButton("🛡️عملیات کاربری", callback_data=f"server:{server_id}:user_ops")],
        [InlineKeyboardButton("📋پلن ها", callback_data=f"server:{server_id}:plans")],
        [InlineKeyboardButton("🔗لیست دامنه‌ها", callback_data=f"server:{server_id}:domains")],
        [InlineKeyboardButton("✏️ویرایش سرور", callback_data=f"server:{server_id}:edit")],
        [InlineKeyboardButton("🗑️حذف سرور", callback_data=f"serverdel:{server_id}")],
        [InlineKeyboardButton("⚙️لیست نودها", callback_data=f"server:{server_id}:nodes")],
        [InlineKeyboardButton("🔄همگام سازی نودها", callback_data=f"server:{server_id}:sync_nodes")],
        [InlineKeyboardButton("↩️بازگشت", callback_data="servers:list_back")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ===============================
#   دامنه‌ها
# ===============================

async def send_domains_menu(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """منوی اصلی لیست دامنه‌های یک سرور"""
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    if hasattr(database, "get_server_domains"):
        domains = database.get_server_domains(server_id) or []
    else:
        # اگر تابع در دیتابیس نبود، از خود سرور بخوانیم و نرمال کنیم
        raw = server.get("domains") or []
        domains = []
        for idx, d in enumerate(raw, start=1):
            if isinstance(d, dict):
                dom = d.get("domain") or d.get("host") or d.get("url")
                title = d.get("title") or d.get("name") or dom
            else:
                dom = str(d)
                title = dom
            if not dom:
                continue
            domains.append({"id": idx, "title": title, "domain": dom})

    lines = [
        "✏️ لیست دامنه‌ها",
        "📌 در این قسمت شما میتوانید دامنه‌های خود را اضافه کنید تا لینک و کانفیگ‌های دریافتی توسط شما و کاربران به جای استفاده از آدرس مستقیم پنل، از این دامنه(ها) استفاده کنند.",
        "",
    ]

    if not domains:
        lines.append("در حال حاضر هیچ دامنه‌ای ثبت نشده است.")
    else:
        lines.append("دامنه‌های ثبت‌شده:")
        for d in domains:
            lines.append(f"• {d['title']} → {d['domain']}")

    keyboard_rows: List[List[InlineKeyboardButton]] = []

    # دکمه برای هر دامنه (با عنوان)
    for d in domains:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    d["title"],
                    callback_data=f"domaininfo:{server_id}:{d['id']}",
                )
            ]
        )

    keyboard_rows.append(
        [InlineKeyboardButton("➕ افزودن دامنه", callback_data=f"domains:{server_id}:add")]
    )

    if domains:
        keyboard_rows.append(
            [InlineKeyboardButton("🗑 حذف دامنه", callback_data=f"domains:{server_id}:remove")]
        )

    keyboard_rows.append(
        [InlineKeyboardButton("بازگشت🔙", callback_data=f"server:{server_id}")]
    )

    kb = InlineKeyboardMarkup(keyboard_rows)
    text = "\n".join(lines)

    if message is not None:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def send_domains_delete_menu(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """نمایش لیست دامنه‌ها برای حذف"""
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    if hasattr(database, "get_server_domains"):
        domains = database.get_server_domains(server_id) or []
    else:
        raw = server.get("domains") or []
        domains = []
        for idx, d in enumerate(raw, start=1):
            if isinstance(d, dict):
                dom = d.get("domain") or d.get("host") or d.get("url")
                title = d.get("title") or d.get("name") or dom
            else:
                dom = str(d)
                title = dom
            if not dom:
                continue
            domains.append({"id": idx, "title": title, "domain": dom})

    if not domains:
        text = "هیچ دامنه‌ای برای حذف وجود ندارد."
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("بازگشت🔙", callback_data=f"domains:{server_id}:back")]]
        )
        if message is not None:
            await message.edit_text(text, reply_markup=kb)
        else:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
        return

    lines = [
        "🗑 حذف دامنه",
        "یکی از دامنه‌های زیر را برای حذف انتخاب کنید:",
        "",
    ]

    rows: List[List[InlineKeyboardButton]] = []
    for d in domains:
        lines.append(f"• {d['title']} → {d['domain']}")
        rows.append(
            [
                InlineKeyboardButton(
                    d["title"],
                    callback_data=f"deldomain:{server_id}:{d['id']}",
                )
            ]
        )

    rows.append(
        [InlineKeyboardButton("بازگشت🔙", callback_data=f"domains:{server_id}:back")]
    )

    kb = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)

    if message is not None:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


# ===============================
#   لیست کاربران
# ===============================

async def send_user_list(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
    page: int = 1,
) -> None:
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    users: List[Dict[str, Any]] = []
    source = "api"
    try:
        users = await hiddify_api.list_users(server)
    except hiddify_api.HiddifyApiError as e:
        logger.warning("Hiddify API error on server %s: %s", server_id, e)
        users = database.get_users(server_id)
        source = "local"

    total_users = len(users)
    online_users = offline_users = expired_users = 0
    items: List[tuple[str, str, str]] = []

    for u in users:
        user_uuid = u.get("uuid") or u.get("id")
        name = u.get("name") or u.get("username") or f"User_{user_uuid}"
        status = classify_user_status(u)
        if status == "online":
            online_users += 1
        elif status == "expired":
            expired_users += 1
        else:
            offline_users += 1
        items.append((name, status, str(user_uuid)))

    if total_users == 0:
        text = (
            "[📋 لیست کاربران]\n"
            "هنوز هیچ کاربری برای این سرور ثبت نشده است.\n\n"
            f"👥 تعداد کاربران: {total_users}\n"
            f"🔵 آنلاین: {online_users}\n"
            f"🟡 آفلاین: {offline_users}\n"
            f"🔴 منقضی شده: {expired_users}"
        )
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("بازگشت", callback_data=f"server:{server_id}")]]
        )
        if message is not None:
            await message.edit_text(text, reply_markup=kb)
        else:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
        return

    # صفحه‌بندی: هر صفحه 20 کاربر
    page_size = 20
    if page < 1:
        page = 1
    total_pages = max(1, (total_users + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    start = (page - 1) * page_size
    page_items = items[start:start + page_size]

    # ساخت لیست دکمه‌ها برای صفحه جاری
    page_buttons: List[InlineKeyboardButton] = []
    for name, status, user_uuid in page_items:
        if status == "online":
            emoji = "🔵"
        elif status == "expired":
            emoji = "🔴"
        else:
            emoji = "🟡"
        label = f"{emoji}{name}"
        page_buttons.append(
            InlineKeyboardButton(
                label,
                callback_data=f"server:{server_id}:useruuid:{user_uuid}",
            )
        )

    # چیدمان 3 ستونه (راست‌به‌چپ) و نمایش ردیف‌ها از پایین به بالا
    keyboard_rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(page_buttons), 3):
        row = page_buttons[i:i + 3]
        keyboard_rows.append(list(reversed(row)))
    keyboard_rows = list(reversed(keyboard_rows))

    # ناوبری صفحه
    nav_row: List[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=f"server:{server_id}:users:{page - 1}",
            )
        )
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=f"server:{server_id}:users:{page + 1}",
            )
        )
    keyboard_rows.append(nav_row)

    keyboard_rows.append(
        [InlineKeyboardButton("بازگشت", callback_data=f"server:{server_id}")]
    )

    extra = ""
    if source == "local":
        extra = "\n\n⚠️ اتصال به Hiddify API انجام نشد، لیست از دیتابیس محلی خوانده شد."

    text = (
        "[📋 لیست کاربران]\n"
        "❕ شما می‌توانید لیست کاربران و اطلاعات آن‌ها را اینجا مشاهده کنید.\n"
        f"📄 صفحه: {page}/{total_pages}\n"
        f"👥 تعداد کاربران: {total_users}\n"
        f"🔵 آنلاین: {online_users}\n"
        f"🟡 آفلاین: {offline_users}\n"
        f"🔴 منقضی شده: {expired_users}"
        f"{extra}"
    )
    kb = InlineKeyboardMarkup(keyboard_rows)
    if message is not None:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


# ===============================
#   جزئیات کاربر
# ===============================

def build_user_detail_text(
    server: Dict[str, Any],
    user_data: Dict[str, Any],
    source: str = "api",
) -> str:
    user_uuid = user_data.get("uuid") or user_data.get("id")
    name = user_data.get("name") or user_data.get("username") or f"User_{user_uuid}"
    server_title = server.get("title", "نامشخص")

    usage_current = user_data.get("current_usage_GB")
    usage_limit = user_data.get("usage_limit_GB")
    comment = _display_safe_note(user_data.get("comment") or "—")

    _, _, days_left = _compute_package_info(user_data)
    last_online_raw = user_data.get("last_online")

    if usage_current is None:
        usage_line = "📊مصرف: نامشخص"
    elif usage_limit is None:
        usage_line = f"📊مصرف: {format_gb(usage_current)} گیگابایت (نامحدود)"
    else:
        usage_line = (
            f"📊مصرف: {_format_usage_current(usage_current)} از "
            f"{_format_usage_limit(usage_limit)} گیگابایت"
        )

    if days_left is None:
        expire_line = "📆انقضا: نامشخص"
    elif days_left < 0:
        expire_line = f"📆انقضا: منقضی شده ({abs(days_left)} روز پیش)"
    else:
        expire_line = f"📆انقضا: {days_left} روز دیگر"

    last_dt = _parse_dt(last_online_raw)
    if not last_dt:
        last_online_line = "📶آخرین اتصال: نامشخص"
    else:
        delta = datetime.now(timezone.utc).replace(tzinfo=None) - last_dt
        days = delta.days
        seconds = delta.seconds
        if days <= 0:
            if seconds < 60:
                rel = "چند ثانیه پیش"
            elif seconds < 3600:
                rel = f"{seconds // 60} دقیقه پیش"
            else:
                rel = f"{seconds // 3600} ساعت پیش"
        elif days < 30:
            rel = f"{days} روز پیش"
        elif days < 365:
            rel = f"{days // 30} ماه پیش"
        else:
            rel = f"{days // 365} سال پیش"
        last_online_line = f"📶آخرین اتصال: {rel}"

    header_line = f"👤 کاربر:  {name}"
    sep_line = "❖⬩╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍⬩❖"
    server_line = f"⬖ سرور:  {server_title}"

    text_lines = [
        header_line,
        sep_line,
        server_line,
        usage_line,
        expire_line,
        last_online_line,
        f"📝یادداشت: {comment}",
    ]
    if source == "local":
        text_lines.append(
            "\n⚠️ اطلاعات این کاربر از دیتابیس محلی خوانده شده است (نه از API)."
        )
    return "\n".join(text_lines)


def build_user_detail_html_text(
    server: Dict[str, Any],
    user_data: Dict[str, Any],
    source: str = "api",
    *,
    user_name_link: Optional[str] = None,
) -> str:
    user_uuid = user_data.get("uuid") or user_data.get("id")
    name = user_data.get("name") or user_data.get("username") or f"User_{user_uuid}"
    server_title = server.get("title", "نامشخص")

    usage_current = user_data.get("current_usage_GB")
    usage_limit = user_data.get("usage_limit_GB")
    comment = _display_safe_note(user_data.get("comment") or "—")

    _, _, days_left = _compute_package_info(user_data)
    last_online_raw = user_data.get("last_online")

    if usage_current is None:
        usage_line = "📊مصرف: نامشخص"
    elif usage_limit is None:
        usage_line = f"📊مصرف: {format_gb(usage_current)} گیگابایت (نامحدود)"
    else:
        usage_line = (
            f"📊مصرف: {_format_usage_current(usage_current)} از "
            f"{_format_usage_limit(usage_limit)} گیگابایت"
        )

    if days_left is None:
        expire_line = "📆انقضا: نامشخص"
    elif days_left < 0:
        expire_line = f"📆انقضا: منقضی شده ({abs(days_left)} روز پیش)"
    else:
        expire_line = f"📆انقضا: {days_left} روز دیگر"

    last_dt = _parse_dt(last_online_raw)
    if not last_dt:
        last_online_line = "📶آخرین اتصال: نامشخص"
    else:
        delta = datetime.now(timezone.utc).replace(tzinfo=None) - last_dt
        days = delta.days
        seconds = delta.seconds
        if days <= 0:
            if seconds < 60:
                rel = "چند ثانیه پیش"
            elif seconds < 3600:
                rel = f"{seconds // 60} دقیقه پیش"
            else:
                rel = f"{seconds // 3600} ساعت پیش"
        elif days < 30:
            rel = f"{days} روز پیش"
        elif days < 365:
            rel = f"{days // 30} ماه پیش"
        else:
            rel = f"{days // 365} سال پیش"
        last_online_line = f"📶آخرین اتصال: {rel}"

    safe_name = escape(str(name))
    if user_name_link:
        safe_link = escape(user_name_link, quote=True)
        header_line = f'👤 کاربر:  <a href="{safe_link}">{safe_name}</a>'
    else:
        header_line = f"👤 کاربر:  {safe_name}"

    text_lines = [
        header_line,
        "❖⬩╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍⬩❖",
        f"⬖ سرور:  {escape(str(server_title))}",
        escape(usage_line),
        escape(expire_line),
        escape(last_online_line),
        f"📝یادداشت: {escape(comment)}",
    ]
    if source == "local":
        text_lines.append(
            "\n⚠️ اطلاعات این کاربر از دیتابیس محلی خوانده شده است (نه از API)."
        )
    return "\n".join(text_lines)


def build_expired_user_detail_keyboard(
    server_id: int,
    user_uuid: str,
    owner: Optional[Dict[str, Any]] = None,
) -> InlineKeyboardMarkup:
    if owner is None:
        owner = userbot_db.get_service_owner_by_panel_uuid(str(user_uuid))
    rows: List[List[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "کانفیگ ها📄",
                callback_data=f"server:{server_id}:usercfg:{user_uuid}",
            )
        ],
        [
            InlineKeyboardButton(
                "ویرایش کاربر✏️",
                callback_data=f"server:{server_id}:useredit:{user_uuid}",
            )
        ],
        [
            InlineKeyboardButton(
                "تمدید اشتراک♾️",
                callback_data=f"server:{server_id}:userextend:{user_uuid}",
            )
        ],
        [
            InlineKeyboardButton(
                "حذف کاربر🗑️",
                callback_data=f"server:{server_id}:userdel:{user_uuid}",
            )
        ],
    ]

    owner_user_id = int((owner or {}).get("user_id") or 0)
    if owner_user_id > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    f"👤 {_owner_button_title(owner or {})}",
                    callback_data=f"userbot:user:{owner_user_id}",
                )
            ]
        )
    else:
        rows.append([InlineKeyboardButton("پروفایل کاربر👤", callback_data="userbot:users_menu")])

    return InlineKeyboardMarkup(rows)


async def send_expired_user_detail(
    server_id: int,
    user_uuid: str,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    user_data = None
    source = "api"
    try:
        user_data = await hiddify_api.get_user_by_uuid(server, user_uuid)
    except hiddify_api.HiddifyApiError as e:
        logger.warning("get_user_by_uuid error (expired detail): %s", e)
        try:
            local_id = int(user_uuid)
        except ValueError:
            local_id = None
        if local_id is not None:
            user_data = database.get_user(server_id, local_id)
            source = "local"

    if not user_data:
        text = "❌ کاربر پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    owner = _resolve_owner_for_panel_user(
        str(user_data.get("uuid") or user_uuid or ""),
        user_data.get("comment") or "",
    )
    panel_user_uuid = str(user_data.get("uuid") or user_uuid or "")
    user_link_base = _build_user_base_url(server, panel_user_uuid)
    user_link = f"{user_link_base.rstrip('/')}/" if user_link_base else None
    text = build_user_detail_html_text(
        server,
        user_data,
        source,
        user_name_link=user_link,
    )
    keyboard = build_expired_user_detail_keyboard(server_id, panel_user_uuid, owner=owner)
    if message is not None:
        try:
            await message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            raise
    else:
        await context.bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def send_user_detail(
    server_id: int,
    user_uuid: str,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    user_data = None
    source = "api"
    try:
        user_data = await hiddify_api.get_user_by_uuid(server, user_uuid)
    except hiddify_api.HiddifyApiError as e:
        logger.warning("get_user_by_uuid error: %s", e)
        try:
            local_id = int(user_uuid)
        except ValueError:
            local_id = None
        if local_id is not None:
            user_data = database.get_user(server_id, local_id)
            source = "local"

    if not user_data:
        text = "❌ کاربر پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    panel_user_uuid = str(user_data.get("uuid") or user_uuid or "")
    user_link_base = _build_user_base_url(server, panel_user_uuid)
    user_link = f"{user_link_base.rstrip('/')}/" if user_link_base else None
    text = build_user_detail_html_text(
        server,
        user_data,
        source,
        user_name_link=user_link,
    )

    action_user_uuid = panel_user_uuid
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "کانفیگ ها📄",
                    callback_data=f"server:{server_id}:usercfg:{action_user_uuid}",
                )
            ],
            [
                InlineKeyboardButton(
                    "ویرایش کاربر✏️",
                    callback_data=f"server:{server_id}:useredit:{action_user_uuid}",
                )
            ],
            [
                InlineKeyboardButton(
                    "تمدید اشتراک♾️",
                    callback_data=f"server:{server_id}:userextend:{action_user_uuid}",
                )
            ],
            [
                InlineKeyboardButton(
                    "حذف کاربر🗑️",
                    callback_data=f"server:{server_id}:userdel:{action_user_uuid}",
                )
            ],
            [
                InlineKeyboardButton(
                    "بازگشت به لیست کاربران",
                    callback_data=f"server:{server_id}:users",
                )
            ],
        ]
    )

    if message is not None:
        try:
            await message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            raise
    else:
        await context.bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


# ===============================
#   کانفیگ‌ها
# ===============================

async def send_user_configs_menu(
    server_id: int,
    user_uuid: str,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None and message.text:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    user_data = None
    source = "api"
    try:
        user_data = await hiddify_api.get_user_by_uuid(server, user_uuid)
    except hiddify_api.HiddifyApiError:
        source = "local"
        try:
            local_id = int(user_uuid)
        except ValueError:
            local_id = None
        if local_id is not None:
            user_data = database.get_user(server_id, local_id)
    user_data = dict(user_data or {})
    if not user_data.get("uuid"):
        user_data["uuid"] = user_uuid
    if not user_data.get("name") and not user_data.get("username"):
        user_data["name"] = f"User_{user_uuid}"

    # نمایش عنوان سرور با قالب «لوکیشن 🇩🇪 آلمان»
    server_for_display = dict(server)
    server_for_display["title"] = _format_server_location_title(server.get("title") or "")

    # لینک پنل کاربر پشت اسم نمایش داده شود.
    user_name_link = _build_user_base_url(server, user_uuid)
    text = build_user_detail_html_text(
        server_for_display,
        user_data,
        source=source,
        user_name_link=user_name_link,
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📄 کانفیگ مستقیم",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:direct",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔗 لینک اشتراک خودکار",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:auto_sub",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔗 لینک اشتراک",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:sub",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔗 لینک اشتراک b64",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:sub_b64",
                )
            ],
            [
                InlineKeyboardButton(
                    "Multi Server 🌐",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:multi",
                )
            ],
            [
                InlineKeyboardButton(
                    "Multi Server b64 🌐",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:multi_b64",
                )
            ],
            [
                InlineKeyboardButton(
                    "🤖 لینک اتصال اشتراک به ربات",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:bot_link",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data=f"server:{server_id}:useruuid:{user_uuid}",
                )
            ],
        ]
    )

    if message is not None and message.text:
        await message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def send_direct_config_menu(
    server_id: int,
    user_uuid: str,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    user_data = None
    try:
        user_data = await hiddify_api.get_user_by_uuid(server, user_uuid)
    except hiddify_api.HiddifyApiError:
        user_data = None

    name = (user_data or {}).get("name") or (user_data or {}).get(
        "username"
    ) or f"User_{user_uuid}"

    text = (
        "📄 کانفیگ مستقیم\n"
        f"👤 کاربر: {name}\n"
        "━━━━━━━━━━━━━━\n"
        "پروتکل موردنظر را انتخاب کنید:"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🟢 VLESS",
                    callback_data=f"server:{server_id}:directcfg:{user_uuid}:vless",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔵 VMESS",
                    callback_data=f"server:{server_id}:directcfg:{user_uuid}:vmess",
                )
            ],
            [
                InlineKeyboardButton(
                    "🟠 TROJAN",
                    callback_data=f"server:{server_id}:directcfg:{user_uuid}:trojan",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}",
                )
            ],
        ]
    )

    if message is not None:
        await message.edit_text(text, reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=keyboard)


# ===============================
#   تمدید اشتراک با پلن
# ===============================

async def send_user_extend_menu(
    server_id: int,
    user_uuid: str,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    get_plans = getattr(database, "get_plans", None)
    if callable(get_plans):
        plans = get_plans(server_id) or []
    else:
        plans = []

    if not plans:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "بازگشت به جزئیات کاربر",
                callback_data=f"server:{server_id}:useruuid:{user_uuid}",
            )]]
        )
        text = (
            "❌ برای این سرور هنوز هیچ پلنی ثبت نشده است.\n"
            "از منوی «مدیریت ربات کاربران» می‌توانید بعداً پلن‌ها را اضافه کنید."
        )
        if message is not None:
            await message.edit_text(text, reply_markup=kb)
        else:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
        return

    lines = [
        "♾️ تمدید اشتراک کاربر",
        "یکی از پلن‌های زیر را برای تمدید انتخاب کنید:",
        "",
    ]
    keyboard_rows: List[List[InlineKeyboardButton]] = []

    for p in plans:
        pid = p.get("id")
        title = p.get("title") or p.get("name") or f"پلن #{pid}"
        days = p.get("days") or p.get("duration_days") or "-"
        gb = p.get("gb") or p.get("usage_limit_GB") or p.get("volume_GB")
        price = p.get("price") or p.get("price_toman") or "-"

        if gb in (None, "", 0):
            gb_text = "نامحدود"
        else:
            gb_text = f"{format_gb(gb)} گیگابایت"

        lines.append(f"• {title} | {price} تومان | {days} روز | {gb_text}")
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    title,
                    callback_data=f"extend:{server_id}:{user_uuid}:{pid}",
                )
            ]
        )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                "بازگشت🔙",
                callback_data=f"server:{server_id}:useruuid:{user_uuid}",
            )
        ]
    )

    kb = InlineKeyboardMarkup(keyboard_rows)
    text = "\n".join(lines)
    if message is not None:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def apply_plan_to_user(
    server_id: int,
    user_uuid: str,
    plan_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    server = database.get_server_by_id(server_id)
    if not server:
        await context.bot.send_message(chat_id, "❌ سرور پیدا نشد.")
        return

    get_plan = getattr(database, "get_plan", None)
    if not callable(get_plan):
        await context.bot.send_message(
            chat_id,
            "❌ تابع get_plan در دیتابیس پیاده‌سازی نشده است، "
            "بخش تمدید اشتراک هنوز کامل نشده.",
        )
        return

    plan = get_plan(server_id, plan_id)
    if not plan:
        await context.bot.send_message(chat_id, "❌ پلن انتخاب‌شده پیدا نشد.")
        return

    days = plan.get("days") or plan.get("duration_days")
    gb = plan.get("gb") or plan.get("usage_limit_GB") or plan.get("volume_GB")

    if not days:
        await context.bot.send_message(
            chat_id,
            "❌ در پلن انتخاب‌شده مقدار روز (days) مشخص نشده است.",
        )
        return

    patch_data: Dict[str, Any] = {
        "package_days": int(days),
        "start_date": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d"),
        "current_usage_GB": 0,
        "last_reset_time": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
    }
    if gb:
        patch_data["usage_limit_GB"] = float(gb)

    target_user_uuid = await _resolve_panel_user_uuid(server, server_id, user_uuid)
    try:
        await hiddify_api.patch_user(server, target_user_uuid, patch_data)
    except Exception as e:
        await context.bot.send_message(
            chat_id, f"❌ خطا در اعمال پلن روی کاربر:\n{e}"
        )
        return

    await context.bot.send_message(
        chat_id,
        "✅ اشتراک کاربر با موفقیت بر اساس پلن انتخاب‌شده تمدید شد.",
    )
    await send_user_detail(server_id, target_user_uuid, chat_id, context)


# ===============================
#   منوی ویرایش کاربر
# ===============================

async def send_user_edit_menu(
    server_id: int,
    user_uuid: str,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    user_data = None
    source = "api"
    try:
        user_data = await hiddify_api.get_user_by_uuid(server, user_uuid)
    except hiddify_api.HiddifyApiError as e:
        logger.warning("get_user_by_uuid error (edit menu): %s", e)
        try:
            local_id = int(user_uuid)
        except ValueError:
            local_id = None
        if local_id is not None:
            user_data = database.get_user(server_id, local_id)
            source = "local"

    if not user_data:
        text = "❌ کاربر پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    text = build_user_detail_text(server, user_data, source)

    is_active_now = _panel_user_is_active(user_data if isinstance(user_data, dict) else {})
    toggle_label = "کاربر فعال 🟢" if is_active_now else "کاربر غیرفعال 🔴"

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    toggle_label,
                    callback_data=f"ued:{server_id}:{user_uuid}:toggle_active",
                )
            ],
            [
                InlineKeyboardButton(
                    "بازنشانی حجم🔄",
                    callback_data=f"ued:{server_id}:{user_uuid}:reset_usage",
                ),
                InlineKeyboardButton(
                    "ویرایش حجم📊",
                    callback_data=f"ued:{server_id}:{user_uuid}:usage",
                ),
            ],
            [
                InlineKeyboardButton(
                    "بازنشانی مدت🔄",
                    callback_data=f"ued:{server_id}:{user_uuid}:reset_days",
                ),
                InlineKeyboardButton(
                    "ویرایش مدت📅",
                    callback_data=f"ued:{server_id}:{user_uuid}:days",
                ),
            ],
            [
                InlineKeyboardButton(
                    "ویرایش یادداشت📝",
                    callback_data=f"ued:{server_id}:{user_uuid}:comment",
                )
            ],
            [
                InlineKeyboardButton(
                    "تغییرنام اشتراک✏️",
                    callback_data=f"ued:{server_id}:{user_uuid}:rename_sub",
                )
            ],
            [
                InlineKeyboardButton(
                    "بازگشت🔙",
                    callback_data=f"server:{server_id}:useruuid:{user_uuid}",
                )
            ],
        ]
    )

    if message is not None:
        try:
            await message.edit_text(text, reply_markup=keyboard)
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return
            raise
    else:
        await context.bot.send_message(chat_id, text, reply_markup=keyboard)


# ===============================
#   منوی جستجو
# ===============================

def build_search_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔍 جستجوی هوشمند کاربر",
                    callback_data="searchmenu:smart",
                )
            ],
            [
                InlineKeyboardButton(
                    "📊پیگیری اشتراک",
                    callback_data="userbot:subs_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    "⚠️ لیست کاربران منقضی شده",
                    callback_data="searchmenu:expired",
                )
            ],
            [
                InlineKeyboardButton(
                    "بازگشت🔙",
                    callback_data="searchmenu:back_main",
                )
            ],
        ]
    )


async def send_search_menu(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    # متن کوتاه برای جلوگیری از ارسال حباب خالی (فقط ساعت).
    text = "🔍 جستجوی کاربر"
    kb = build_search_menu_keyboard()
    if message is not None:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)


SEARCH_RESULTS_COLUMNS = 3
SEARCH_RESULTS_ROWS = 7
SEARCH_RESULTS_PAGE_SIZE = SEARCH_RESULTS_COLUMNS * SEARCH_RESULTS_ROWS


def _smart_status_emoji(status: str) -> str:
    s = str(status or "").strip().lower()
    if s == "online":
        return "🔵"
    if s == "expired":
        return "🔴"
    return "🟡"


def _build_smart_search_results_keyboard(
    results: List[Dict[str, Any]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    start = (page - 1) * SEARCH_RESULTS_PAGE_SIZE
    end = start + SEARCH_RESULTS_PAGE_SIZE
    page_items = results[start:end]

    rows: List[List[InlineKeyboardButton]] = []
    page_buttons: List[InlineKeyboardButton] = []
    for item in page_items:
        name = str(item.get("name") or "").strip() or "کاربر"
        name_short = name[:20]
        emoji = _smart_status_emoji(str(item.get("status") or ""))
        page_buttons.append(
            InlineKeyboardButton(
                f"{name_short}{emoji}",
                callback_data=f"search:sel:{item['server_id']}:{item['user_uuid']}",
            )
        )

    for i in range(0, len(page_buttons), SEARCH_RESULTS_COLUMNS):
        chunk = page_buttons[i:i + SEARCH_RESULTS_COLUMNS]
        rows.append(list(reversed(chunk)))

    if total_pages > 1:
        nav: List[InlineKeyboardButton] = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(
                    "➡️",
                    callback_data=f"search:page:{page - 1}",
                )
            )
        nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav.append(
                InlineKeyboardButton(
                    "⬅️",
                    callback_data=f"search:page:{page + 1}",
                )
            )
        rows.append(nav)

    rows.append([InlineKeyboardButton("بازگشت🔙", callback_data="search:back")])
    return InlineKeyboardMarkup(rows)


async def send_smart_search_results_page(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    page: int = 1,
    message=None,
) -> None:
    results = context.user_data.get("smart_search_results") or []
    if not results:
        text = "❌ نتیجه جستجو در دسترس نیست."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    total = len(results)
    total_pages = max(1, (total + SEARCH_RESULTS_PAGE_SIZE - 1) // SEARCH_RESULTS_PAGE_SIZE)
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    online_count = sum(1 for r in results if str(r.get("status") or "").lower() == "online")
    expired_count = sum(1 for r in results if str(r.get("status") or "").lower() == "expired")
    offline_count = max(0, total - online_count - expired_count)

    text = (
        "[📥 نتیجه جستجو]\n"
        "#️⃣ لیست کاربران\n"
        "شما می‌توانید لیست کاربران و اطلاعات آن‌ها را اینجا مشاهده کنید\n"
        f"👤 تعداد کاربران: {total}\n"
        f"🔵 کاربران آنلاین: {online_count}\n"
        f"🟡 کاربران آفلاین: {offline_count}\n"
        f"🔴 کاربران منقضی: {expired_count}"
    )
    kb = _build_smart_search_results_keyboard(results, page, total_pages)

    if message is not None:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def _list_server_users_fast(server: Dict[str, Any], timeout_sec: float = 4.5) -> List[Dict[str, Any]]:
    """گرفتن کاربران سرور با timeout کوتاه + fallback محلی برای کاهش کندی."""
    sid = int(server.get("id") or 0)
    try:
        users = await asyncio.wait_for(hiddify_api.list_users(server), timeout=timeout_sec)
        if isinstance(users, list):
            return users
    except Exception:
        pass
    try:
        return database.get_users(sid) or []
    except Exception:
        return []


async def send_expired_users_list(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    servers = database.get_servers()
    results: List[Dict[str, Any]] = []
    online_count = 0

    valid_servers = [s for s in servers if s.get("id") is not None]
    users_batches = await asyncio.gather(*[_list_server_users_fast(s) for s in valid_servers])

    for s, users in zip(valid_servers, users_batches):
        server_id = s.get("id")
        server_title = s.get("title") or f"سرور #{server_id}"

        for u in users:
            status = classify_user_status(u)
            if status == "expired":
                user_uuid = str(u.get("uuid") or u.get("id") or "")
                name = u.get("name") or u.get("username") or f"User_{user_uuid}"
                results.append(
                    {
                        "server_id": server_id,
                        "server_title": server_title,
                        "user_uuid": user_uuid,
                        "name": name,
                    }
                )
            if status == "online":
                online_count += 1

    if not results:
        text = (
            "[⚠️لیست کاربران منقضی شده]\n"
            "#️⃣ لیست کاربران\n"
            "شما می‌توانید لیست کاربران و اطلاعات آن‌ها را اینجا مشاهده کنید\n"
            "👤 تعداد کاربران: 0\n"
            f"🔵 کاربران آنلاین: {online_count}\n\n"
            "در حال حاضر هیچ کاربر منقضی‌شده‌ای پیدا نشد."
        )
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("بازگشت🔙", callback_data="expired:back")]]
        )
        if message is not None:
            await message.edit_text(text, reply_markup=kb)
        else:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
        return

    total = len(results)
    lines = [
        "[⚠️لیست کاربران منقضی شده]",
        "#️⃣ لیست کاربران",
        "شما می‌توانید لیست کاربران و اطلاعات آن‌ها را اینجا مشاهده کنید",
        f"👤 تعداد کاربران: {total}",
        f"🔵 کاربران آنلاین: {online_count}",
        "",
    ]

    user_buttons: List[InlineKeyboardButton] = []
    for r in results:
        label = f"🔴|{r['name']}"
        user_buttons.append(
            InlineKeyboardButton(
                label,
                callback_data=f"expired:sel:{r['server_id']}:{r['user_uuid']}",
            )
        )

    keyboard_rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(user_buttons), 3):
        chunk = user_buttons[i:i + 3]
        keyboard_rows.append(list(reversed(chunk)))

    keyboard_rows.append(
        [InlineKeyboardButton("بازگشت🔙", callback_data="expired:back")]
    )

    kb = InlineKeyboardMarkup(keyboard_rows)
    text = "\n".join(lines)

    if message is not None:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            raise
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


# ===============================
#   افزودن سرور - ویزارد
# ===============================

async def handle_add_server_flow(
    state: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ویزارد چند مرحله‌ای برای افزودن سرور جدید"""
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()

    # لغو در هر مرحله
    if _is_cancel_text(text):
        context.user_data.pop("state", None)
        context.user_data.pop("new_server", None)
        await message.reply_text(
            "❌ عملیات افزودن سرور لغو شد.",
            reply_markup=admin_main_keyboard(),
        )
        await send_servers_list(chat_id=message.chat_id, context=context)
        return

    # شیء موقت سرور جدید
    new_server = context.user_data.get("new_server") or {}

    # مرحله ۱: عنوان
    if state == ADD_STATE_TITLE:
        new_server["title"] = text
        context.user_data["new_server"] = new_server
        context.user_data["state"] = ADD_STATE_PANEL_URL
        await message.reply_text(
            "🌐 لطفاً آدرس پنل هیدیفای را وارد کنید:\nمثال: https://site.example.com",
            reply_markup=cancel_keyboard(),
        )
        return

    # مرحله ۲: آدرس پنل
    if state == ADD_STATE_PANEL_URL:
        panel_url = text.strip()
        if not (panel_url.startswith("http://") or panel_url.startswith("https://")):
            await message.reply_text(
                "❌ لطفاً آدرس پنل را به صورت کامل و با http/https ارسال کنید.\n"
                "مثال: https://site.example.com",
                reply_markup=cancel_keyboard(),
            )
            return

        new_server["panel_url"] = panel_url
        context.user_data["new_server"] = new_server
        context.user_data["state"] = ADD_STATE_ADMIN_PROXY
        await message.reply_text(
            "🔑 لطفاً «کد مسیر ادمین پنل» را وارد کنید (Admin Proxy Path):\n"
            "مثال: cNT69A5AAw",
            reply_markup=cancel_keyboard(),
        )
        return

    # مرحله ۳: admin_proxy_path
    if state == ADD_STATE_ADMIN_PROXY:
        new_server["admin_proxy_path"] = text.strip().strip("/")
        context.user_data["new_server"] = new_server
        context.user_data["state"] = ADD_STATE_ADMIN_UUID
        await message.reply_text(
            "🧩 لطفاً «کلید ادمین پنل» را وارد کنید (UUID یا API Key):\n"
            "⚠️ فقط مقدار خالص، بدون / و بدون آدرس",
            reply_markup=cancel_keyboard(),
        )
        return

    # مرحله ۴: admin_uuid
    if state == ADD_STATE_ADMIN_UUID:
        admin_key = text.strip()
        if "/" in admin_key or admin_key.startswith("http://") or admin_key.startswith("https://"):
            await message.reply_text(
                "❌ UUID/API Key ادمین نامعتبر است.\n"
                "فقط مقدار خالص را وارد کنید (بدون / و بدون آدرس).\n"
                "مثال درست:\n"
                "123e4567-e89b-12d3-a456-426614174000",
                reply_markup=cancel_keyboard(),
            )
            return
        new_server["admin_uuid"] = admin_key
        context.user_data["new_server"] = new_server
        context.user_data["state"] = ADD_STATE_USER_PROXY
        await message.reply_text(
            "🔑 لطفاً «کد مسیر کاربران پنل» را وارد کنید (User Proxy Path):",
            reply_markup=cancel_keyboard(),
        )
        return

    # مرحله ۵: user_proxy_path
    if state == ADD_STATE_USER_PROXY:
        new_server["user_proxy_path"] = text.strip().strip("/")
        context.user_data["new_server"] = new_server
        context.user_data["state"] = ADD_STATE_LIMIT
        await message.reply_text(
            "📊 لطفاً محدودیت تعداد کاربران سرور را وارد کنید (عدد):",
            reply_markup=cancel_keyboard(),
        )
        return

    # مرحله ۶: users_limit + تست اتصال + ذخیره
    if state == ADD_STATE_LIMIT:
        try:
            limit = int(text)
            if limit <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text(
                "❌ لطفاً یک عدد صحیح و بزرگ‌تر از صفر برای محدودیت کاربران وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return

        new_server["users_limit"] = limit
        context.user_data["new_server"] = new_server

        # تست اتصال به پنل با اطلاعات وارد شده
        try:
            await hiddify_api.list_users(new_server)
        except Exception as e:
            err_text = str(e or "")
            err_low = err_text.lower()
            if "just a moment" in err_low or "cloudflare" in err_low or "enable javascript and cookies" in err_low:
                await message.reply_text(
                    "❌ اتصال به پنل توسط Cloudflare مسدود شد (HTTP 403).\n"
                    "برای این دامنه/مسیر API باید Challenge خاموش یا Bypass شود.\n\n"
                    "✅ راهکار:\n"
                    "1) روی Cloudflare یک Rule بگذارید که مسیر\n"
                    "   `/<admin_proxy_path>/api/*`\n"
                    "   بدون Challenge/Captcha باشد.\n"
                    "2) یا برای اتصال ربات، دامنه‌ای استفاده کنید که پشت Cloudflare Challenge نباشد.\n"
                    "3) مقدار UUID ادمین را فقط UUID/API Key خالص وارد کنید (بدون / و بدون proxy path).",
                    reply_markup=cancel_keyboard(),
                )
                return
            if len(err_text) > 800:
                err_text = err_text[:800] + "\n... (خطا کوتاه شد)"
            await message.reply_text(
                "❌ اتصال به پنل با اطلاعات وارد شده برقرار نشد.\n"
                "لطفاً آدرس پنل، Proxy Pathها و UUID/API Key را بررسی کنید.\n\n"
                f"جزئیات خطا:\n{err_text}",
                reply_markup=cancel_keyboard(),
            )
            return

        # ذخیره در دیتابیس
        saved = database.add_server(new_server)

        # پاک‌کردن state
        context.user_data.pop("state", None)
        context.user_data.pop("new_server", None)

        summary = (
            "✅ سرور با موفقیت اضافه شد.\n\n"
            f"🖥️ عنوان: {saved.get('title')}\n"
            f"🌐 آدرس پنل: {saved.get('panel_url')}\n"
            f"👥 محدودیت کاربران: {saved.get('users_limit')}\n"
        )
        await message.reply_text(summary, reply_markup=admin_main_keyboard())
        await send_servers_list(chat_id=message.chat_id, context=context)
        return

    # اگر state نامعتبر بود
    await message.reply_text(
        "❌ وضعیت افزودن سرور نامعتبر است. دوباره از منوی «افزودن سرور» اقدام کنید.",
        reply_markup=admin_main_keyboard(),
    )
    context.user_data.pop("state", None)
    context.user_data.pop("new_server", None)


# ===============================
#   افزودن دامنه - ویزارد
# ===============================

async def handle_add_domain_flow(
    state: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ویزارد دو مرحله‌ای: عنوان دامنه → خود دامنه"""
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()

    # لغو در هر مرحله
    if _is_cancel_text(text):
        context.user_data.pop("state", None)
        context.user_data.pop("domains_server_id", None)
        context.user_data.pop("new_domain", None)
        await message.reply_text(
            "❌ افزودن دامنه لغو شد.",
            reply_markup=admin_main_keyboard(),
        )
        return

    server_id = context.user_data.get("domains_server_id")
    if server_id is None:
        context.user_data.pop("state", None)
        context.user_data.pop("new_domain", None)
        await message.reply_text(
            "❌ وضعیت سرور برای افزودن دامنه نامشخص است. دوباره از منوی «لیست دامنه‌ها» اقدام کنید.",
            reply_markup=admin_main_keyboard(),
        )
        return

    new_domain = context.user_data.get("new_domain") or {}

    # مرحله ۱: گرفتن عنوان دامنه
    if state == ADD_DOMAIN_TITLE:
        new_domain["title"] = text
        context.user_data["new_domain"] = new_domain
        context.user_data["state"] = ADD_DOMAIN_DOMAIN

        await message.reply_text(
            "🌐 حالا خود دامنه را وارد کنید (مثال: example.com یا https://example.com):",
            reply_markup=cancel_keyboard(),
        )
        return

    # مرحله ۲: گرفتن خود دامنه
    if state == ADD_DOMAIN_DOMAIN:
        domain_raw = text.strip()
        if not domain_raw:
            await message.reply_text(
                "❌ لطفاً دامنه را وارد کنید. مثال: example.com یا https://example.com",
                reply_markup=cancel_keyboard(),
            )
            return

        # اگر بدون http/https بود، https را اضافه کن
        if not (domain_raw.startswith("http://") or domain_raw.startswith("https://")):
            domain_raw = "https://" + domain_raw

        parsed = urlparse(domain_raw)
        if not parsed.netloc:
            await message.reply_text(
                "❌ دامنه وارد شده معتبر نیست. مثال: example.com یا https://example.com",
                reply_markup=cancel_keyboard(),
            )
            return

        normalized = f"{parsed.scheme}://{parsed.netloc}"
        title = new_domain.get("title") or normalized

        try:
            # اگر توی لایه دیتابیس تابع اختصاصی وجود داشت، از اون استفاده کن
            if hasattr(database, "add_server_domain"):
                database.add_server_domain(server_id, title, normalized)
            else:
                # ذخیره داخل فیلد domains خود سرور
                server = database.get_server_by_id(server_id)
                if not server:
                    raise RuntimeError("Server not found")
                domains = server.get("domains") or []
                domains.append(
                    {
                        "id": len(domains) + 1,
                        "title": title,
                        "domain": normalized,
                    }
                )
                database.update_server(server_id, {"domains": domains})
        except Exception as e:
            await message.reply_text(
                f"❌ خطا در ذخیره دامنه:\n{e}",
                reply_markup=cancel_keyboard(),
            )
            return

        # پاک کردن state
        context.user_data.pop("state", None)
        context.user_data.pop("domains_server_id", None)
        context.user_data.pop("new_domain", None)

        await message.reply_text(
            f"✅ دامنه «{title}» با آدرس «{normalized}» اضافه شد.",
            reply_markup=admin_main_keyboard(),
        )
        await send_domains_menu(server_id, message.chat_id, context)
        return

    # اگر state شناخته نشد
    context.user_data.pop("state", None)
    await message.reply_text(
        "❌ وضعیت افزودن دامنه نامعتبر است. دوباره از منوی «لیست دامنه‌ها» اقدام کنید.",
        reply_markup=admin_main_keyboard(),
    )


# ===============================
#   ویرایش سرور (state)
# ===============================

async def handle_edit_server_flow(
    state: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()
    server_id = context.user_data.get("edit_server_id")

    if server_id is None:
        context.user_data.pop("state", None)
        await message.reply_text(
            "❌ وضعیت ویرایش سرور نامشخص است. دوباره از منوی «ویرایش سرور✏️» اقدام کنید.",
            reply_markup=admin_main_keyboard(),
        )
        return

    if _is_cancel_text(text):
        context.user_data.pop("state", None)
        context.user_data.pop("edit_server_id", None)
        await message.reply_text("❌ ویرایش سرور لغو شد.")
        await send_servers_list(chat_id=message.chat_id, context=context)
        return

    server = database.get_server_by_id(server_id)
    if not server:
        context.user_data.pop("state", None)
        context.user_data.pop("edit_server_id", None)
        await message.reply_text("❌ سرور پیدا نشد.", reply_markup=admin_main_keyboard())
        return

    updates: Dict[str, Any] = {}

    if state == EDIT_SERVER_TITLE:
        updates["title"] = text
        msg_ok = f"✅ نام سرور به «{text}» تغییر کرد."
    elif state == EDIT_SERVER_PANEL_URL:
        panel_url = text.strip()
        if not (panel_url.startswith("http://") or panel_url.startswith("https://")):
            await message.reply_text(
                "❌ لطفاً آدرس پنل را به صورت کامل و با http/https ارسال کنید.\n"
                "مثال: https://site.example.com",
                reply_markup=cancel_keyboard(),
            )
            return
        updates["panel_url"] = panel_url
        msg_ok = "✅ آدرس پنل بروزرسانی شد."
    elif state == EDIT_SERVER_ADMIN_PROXY:
        updates["admin_proxy_path"] = text.strip().strip("/")
        msg_ok = "✅ کد مسیر ادمین (Admin Proxy Path) بروزرسانی شد."
    elif state == EDIT_SERVER_ADMIN_UUID:
        admin_key = text.strip()
        if "/" in admin_key or admin_key.startswith("http://") or admin_key.startswith("https://"):
            await message.reply_text(
                "❌ UUID/API Key ادمین نامعتبر است.\n"
                "فقط مقدار خالص را وارد کنید (بدون / و بدون آدرس).",
                reply_markup=cancel_keyboard(),
            )
            return
        updates["admin_uuid"] = admin_key
        msg_ok = "✅ کلید ادمین (UUID / API Key) بروزرسانی شد."
    elif state == EDIT_SERVER_USER_PROXY:
        updates["user_proxy_path"] = text.strip().strip("/")
        msg_ok = "✅ کد مسیر کاربران (User Proxy Path) بروزرسانی شد."
    elif state == EDIT_SERVER_LIMIT:
        try:
            limit = int(text)
            if limit <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text(
                "❌ لطفاً یک عدد صحیح بزرگ‌تر از صفر برای محدودیت کاربران وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return
        updates["users_limit"] = limit
        msg_ok = "✅ محدودیت تعداد کاربران بروزرسانی شد."
    elif state == EDIT_SERVER_PRIORITY:
        try:
            priority = int(text)
            if priority < 0:
                raise ValueError
        except ValueError:
            await message.reply_text(
                "❌ لطفاً یک عدد صحیح صفر یا بزرگ‌تر برای اولویت وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return
        updates["priority"] = priority
        msg_ok = "✅ اولویت سرور بروزرسانی شد."
    else:
        await message.reply_text("❌ حالت ویرایش سرور نامعتبر است.")
        context.user_data.pop("state", None)
        context.user_data.pop("edit_server_id", None)
        return

    try:
        database.update_server(server_id, updates)
    except Exception as e:
        logger.exception("update_server error: %s", e)
        await message.reply_text(f"❌ خطا در بروزرسانی سرور:\n{e}")
        return

    await message.reply_text(msg_ok)
    context.user_data.pop("state", None)
    context.user_data.pop("edit_server_id", None)

    # بازگشت به منوی جزئیات سرور
    server = database.get_server_by_id(server_id)
    if server:
        text_detail = await build_server_detail_text_live(server)
        kb = build_server_detail_keyboard(server_id)
        await message.reply_text(
            text_detail,
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def send_server_edit_menu(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    text = await build_server_detail_text_live(server)

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📌ویرایش عنوان", callback_data=f"seredit:{server_id}:title"
                )
            ],
            [
                InlineKeyboardButton(
                    "🗿ویرایش محدودیت کاربر",
                    callback_data=f"seredit:{server_id}:limit",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔢ویرایش اولویت ترتیب",
                    callback_data=f"seredit:{server_id}:priority",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔗ویرایش دامنه",
                    callback_data=f"server:{server_id}:domains",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔐ویرایش کد مسیر ادمین",
                    callback_data=f"seredit:{server_id}:admin_proxy",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔐ویرایش کد مسیر کاربران",
                    callback_data=f"seredit:{server_id}:user_proxy",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔑ویرایش کلید ادمین (UUID/API)",
                    callback_data=f"seredit:{server_id}:admin_uuid",
                )
            ],
            [
                InlineKeyboardButton(
                    "🌐ویرایش آدرس پنل",
                    callback_data=f"seredit:{server_id}:panel_url",
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑️حذف سرور",
                    callback_data=f"serverdel:{server_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙بازگشت",
                    callback_data=f"server:{server_id}",
                )
            ],
        ]
    )

    if message is not None:
        await message.edit_text(
            text,
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await context.bot.send_message(
            chat_id,
            text,
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


# ===============================
#   افزودن کاربر (with/without plan)
# ===============================

async def handle_add_user_flow(
    state: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()

    server_id = context.user_data.get("add_user_server_id")
    if server_id is None:
        context.user_data.pop("state", None)
        await message.reply_text(
            "❌ وضعیت افزودن کاربر نامشخص است. دوباره از منوی «افزودن کاربر➕» اقدام کنید.",
            reply_markup=admin_main_keyboard(),
        )
        return

    if _is_cancel_text(text):
        context.user_data.pop("state", None)
        context.user_data.pop("add_user_server_id", None)
        context.user_data.pop("add_user", None)
        context.user_data.pop("add_user_plan_id", None)
        await message.reply_text(
            "❌ عملیات افزودن کاربر لغو شد.",
            reply_markup=admin_main_keyboard(),
        )
        return

    new_user = context.user_data.get("add_user") or {}

    # حالت: افزودن کاربر با پلن – مرحله‌ی گرفتن نام
    if state == ADD_USER_PLAN_NAME:
        plan_id = context.user_data.get("add_user_plan_id")
        if plan_id is None:
            context.user_data.pop("state", None)
            await message.reply_text(
                "❌ پلن انتخاب‌شده نامعتبر است. دوباره از منوی «افزودن کاربر با پلن➕» اقدام کنید.",
                reply_markup=admin_main_keyboard(),
            )
            return

        get_plan = getattr(database, "get_plan", None)
        if not callable(get_plan):
            context.user_data.pop("state", None)
            context.user_data.pop("add_user_plan_id", None)
            await message.reply_text(
                "❌ تابع get_plan در دیتابیس پیاده‌سازی نشده است، "
                "بخش افزودن کاربر با پلن هنوز کامل نشده.",
                reply_markup=build_user_ops_keyboard(server_id),
            )
            return

        plan = get_plan(server_id, plan_id)
        if not plan:
            context.user_data.pop("state", None)
            context.user_data.pop("add_user_plan_id", None)
            await message.reply_text(
                "❌ پلن انتخاب‌شده پیدا نشد.",
                reply_markup=build_user_ops_keyboard(server_id),
            )
            return

        days = plan.get("days") or plan.get("duration_days")
        gb = plan.get("gb") or plan.get("usage_limit_GB") or plan.get("volume_GB")
        title = plan.get("title") or plan.get("name") or f"پلن #{plan_id}"

        if not days:
            context.user_data.pop("state", None)
            context.user_data.pop("add_user_plan_id", None)
            await message.reply_text(
                "❌ این پلن مدت (روز) مشخصی ندارد. لطفاً ابتدا پلن را اصلاح کنید.",
                reply_markup=build_user_ops_keyboard(server_id),
            )
            return

        if gb in (None, "", 0):
            gb_value = 0
            gb_text = "نامحدود"
        else:
            gb_value = float(gb)
            gb_text = f"{format_gb(gb_value)} گیگابایت"

        new_user["name"] = text
        new_user["usage_limit_GB"] = gb_value
        new_user["package_days"] = int(days)
        new_user["plan_id"] = plan_id
        context.user_data["add_user"] = new_user
        context.user_data["state"] = ADD_USER_PLAN_CONFIRM

        summary = (
            "لطفاً اطلاعات را تایید کنید:\n"
            f"👤 کاربر: {text}\n"
            f"📋 پلن: {title}\n"
            f"📊 مصرف: {gb_text}\n"
            f"📅 مدت: {days} روز"
        )
        await message.reply_text(
            summary, reply_markup=confirm_add_user_keyboard()
        )
        return

    # حالت معمولی: نام → حجم → روز
    if state == ADD_USER_NAME:
        new_user["name"] = text
        context.user_data["add_user"] = new_user
        context.user_data["state"] = ADD_USER_USAGE
        await message.reply_text(
            "📊 لطفاً محدودیت استفاده کاربر(GB) را وارد کنید:\nمثال: 30",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == ADD_USER_USAGE:
        try:
            usage_gb = float(text.replace(",", "."))
            if usage_gb <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text(
                "❌ لطفاً یک عدد معتبر بزرگ‌تر از صفر برای حجم (GB) وارد کنید.\nمثال: 30",
                reply_markup=cancel_keyboard(),
            )
            return

        new_user["usage_limit_GB"] = usage_gb
        context.user_data["add_user"] = new_user
        context.user_data["state"] = ADD_USER_DAYS
        await message.reply_text(
            "📅 لطفاً مدت اشتراک (به روز) را وارد کنید:\nمثال: 30",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == ADD_USER_DAYS:
        try:
            days = int(text)
            if days <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text(
                "❌ لطفاً یک عدد صحیح بزرگ‌تر از صفر برای روزها وارد کنید.\nمثال: 30",
                reply_markup=cancel_keyboard(),
            )
            return

        new_user["package_days"] = days
        context.user_data["add_user"] = new_user
        context.user_data["state"] = ADD_USER_CONFIRM

        name = new_user.get("name")
        usage_gb = new_user.get("usage_limit_GB")

        summary = (
            "لطفاً اطلاعات را تایید کنید:\n"
            f"👤 کاربر: {name}\n"
            f"📊 مصرف: {format_gb(usage_gb)} گیگابایت\n"
            f"📅 مدت: {days} روز"
        )
        await message.reply_text(summary, reply_markup=confirm_add_user_keyboard())
        return

    # مرحله تایید برای هر دو حالت
    if state in {ADD_USER_CONFIRM, ADD_USER_PLAN_CONFIRM}:
        if _is_confirm_text(text):
            server = database.get_server_by_id(server_id)
            if not server:
                await message.reply_text(
                    "❌ سرور پیدا نشد.",
                    reply_markup=admin_main_keyboard(),
                )
                context.user_data.pop("state", None)
                return

            new_user = context.user_data.get("add_user") or {}
            name = new_user.get("name")
            usage_gb = new_user.get("usage_limit_GB")
            days = new_user.get("package_days")

            if not (name and usage_gb is not None and days):
                await message.reply_text(
                    "❌ اطلاعات کاربر ناقص است. دوباره از ابتدا تلاش کنید.",
                    reply_markup=admin_main_keyboard(),
                )
                context.user_data.pop("state", None)
                context.user_data.pop("add_user", None)
                context.user_data.pop("add_user_server_id", None)
                context.user_data.pop("add_user_plan_id", None)
                return

            if not hasattr(hiddify_api, "create_user"):
                await message.reply_text(
                    "❌ تابع create_user در hiddify_api پیاده‌سازی نشده است.",
                    reply_markup=admin_main_keyboard(),
                )
                context.user_data.pop("state", None)
                context.user_data.pop("add_user_plan_id", None)
                return

            payload = {
                "name": name,
                "usage_limit_GB": float(usage_gb),
                "package_days": int(days),
                "start_date": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d"),
                "current_usage_GB": 0,
                "last_reset_time": datetime.now(timezone.utc).replace(tzinfo=None).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "is_active": True,
            }

            try:
                created = await hiddify_api.create_user(server, payload)
            except Exception as e:
                await message.reply_text(
                    f"❌ خطا در ایجاد کاربر روی سرور:\n{e}",
                    reply_markup=admin_main_keyboard(),
                )
                context.user_data.pop("state", None)
                return

            uuid = str(created.get("uuid") or created.get("id") or "")

            await message.reply_text(
                "✅ کاربر جدید با موفقیت ساخته شد.\n"
                f"👤 نام: {name}\n"
                f"📊 حجم: {format_gb(usage_gb)} گیگابایت\n"
                f"📅 مدت: {days} روز",
                reply_markup=admin_main_keyboard(),
            )

            context.user_data.pop("state", None)
            context.user_data.pop("add_user", None)
            context.user_data.pop("add_user_server_id", None)
            context.user_data.pop("add_user_plan_id", None)

            if uuid:
                await send_user_detail(
                    server_id, uuid, message.chat_id, context
                )
            else:
                await send_user_list(server_id, message.chat_id, context)
            return

        await message.reply_text(
            "لطفاً با دکمه‌های «✅تایید» یا «❌لغو» پاسخ دهید.",
            reply_markup=confirm_add_user_keyboard(),
        )
        return


# ===============================
#   ویرایش کاربر (state)
# ===============================

async def handle_edit_user_flow(
    state: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()

    server_id = context.user_data.get("edit_user_server_id")
    user_uuid = context.user_data.get("edit_user_uuid")

    if server_id is None or user_uuid is None:
        context.user_data.pop("state", None)
        await message.reply_text(
            "❌ وضعیت ویرایش کاربر نامشخص است. دوباره از منوی «ویرایش کاربر» انتخاب کنید."
        )
        return

    if _is_cancel_text(text):
        context.user_data.pop("state", None)
        await message.reply_text("❌ لغو شد.", reply_markup=admin_main_keyboard())
        await send_user_edit_menu(server_id, user_uuid, message.chat_id, context)
        return

    server = database.get_server_by_id(server_id)
    if not server:
        await message.reply_text(
            "❌ سرور پیدا نشد.", reply_markup=admin_main_keyboard()
        )
        context.user_data.pop("state", None)
        return

    target_user_uuid = await _resolve_panel_user_uuid(server, server_id, user_uuid)

    try:
        if state == EDIT_STATE_NAME:
            new_name = text
            target_user_uuid, changed, total, failed = await _patch_user_on_related_servers(
                server_id,
                target_user_uuid,
                {"name": new_name},
            )
            if changed <= 0:
                detail = f"\n⚠️ سرورهای خطادار: {', '.join(failed[:3])}" if failed else ""
                await message.reply_text(f"❌ تغییر نام اشتراک انجام نشد.{detail}")
                context.user_data.pop("state", None)
                await send_user_edit_menu(server_id, target_user_uuid, message.chat_id, context)
                return
            try:
                owner = userbot_db.get_service_owner_by_panel_uuid(str(target_user_uuid))
                local_service_id = int((owner or {}).get("service_id") or 0)
                if local_service_id > 0:
                    userbot_db.update_service_name(local_service_id, new_name)
            except Exception as sync_err:
                logger.warning(
                    "Failed syncing service name to userbot_services (server_id=%s, user_uuid=%s): %s",
                    server_id,
                    user_uuid,
                    sync_err,
                )
            success_text = f"✅ نام اشتراک به «{new_name}» بروزرسانی شد."
            if failed:
                success_text += f"\n⚠️ روی {changed} از {total} سرور اعمال شد."
            await message.reply_text(success_text)

        elif state == EDIT_STATE_USAGE:
            usage_gb = float(text.replace(",", "."))
            if usage_gb < 0:
                raise ValueError
            target_user_uuid, changed, total, failed = await _patch_user_on_related_servers(
                server_id,
                target_user_uuid,
                {"usage_limit_GB": usage_gb},
            )
            if changed <= 0:
                detail = f"\n⚠️ سرورهای خطادار: {', '.join(failed[:3])}" if failed else ""
                await message.reply_text(f"❌ تنظیم حجم کاربر انجام نشد.{detail}")
                context.user_data.pop("state", None)
                await send_user_edit_menu(server_id, target_user_uuid, message.chat_id, context)
                return
            success_text = f"✅ محدودیت حجم کاربر روی {format_gb(usage_gb)} گیگابایت تنظیم شد."
            if failed:
                success_text += f"\n⚠️ روی {changed} از {total} سرور اعمال شد."
            await message.reply_text(success_text)

        elif state == EDIT_STATE_DAYS:
            days = int(text)
            if days <= 0:
                raise ValueError
            today_str = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            target_user_uuid, changed, total, failed = await _patch_user_on_related_servers(
                server_id,
                target_user_uuid,
                {"package_days": days, "start_date": today_str},
            )
            if changed <= 0:
                detail = f"\n⚠️ سرورهای خطادار: {', '.join(failed[:3])}" if failed else ""
                await message.reply_text(f"❌ تنظیم مدت اشتراک انجام نشد.{detail}")
                context.user_data.pop("state", None)
                await send_user_edit_menu(server_id, target_user_uuid, message.chat_id, context)
                return
            success_text = f"✅ مدت اشتراک روی {days} روز از امروز تنظیم شد."
            if failed:
                success_text += f"\n⚠️ روی {changed} از {total} سرور اعمال شد."
            await message.reply_text(success_text)

        elif state == EDIT_STATE_COMMENT:
            comment = text
            target_user_uuid, changed, total, failed = await _patch_user_on_related_servers(
                server_id,
                target_user_uuid,
                {"comment": comment},
            )
            if changed <= 0:
                detail = f"\n⚠️ سرورهای خطادار: {', '.join(failed[:3])}" if failed else ""
                await message.reply_text(f"❌ بروزرسانی یادداشت انجام نشد.{detail}")
                context.user_data.pop("state", None)
                await send_user_edit_menu(server_id, target_user_uuid, message.chat_id, context)
                return
            try:
                userbot_db.update_service_note_by_panel_user(server_id, str(target_user_uuid), comment)
            except Exception as e:
                logger.warning(
                    "Failed syncing note to userbot_services (server_id=%s, user_uuid=%s): %s",
                    server_id,
                    target_user_uuid,
                    e,
                )
            success_text = "✅ یادداشت کاربر بروزرسانی شد."
            if failed:
                success_text += f"\n⚠️ روی {changed} از {total} سرور اعمال شد."
            await message.reply_text(success_text)

        else:
            await message.reply_text("❌ حالت ویرایش نامعتبر است.")

    except Exception as e:
        logger.exception("Error in edit_user_flow: %s", e)
        await message.reply_text(f"❌ خطا در بروزرسانی کاربر:\n{e}")

    context.user_data.pop("state", None)
    await send_user_edit_menu(server_id, target_user_uuid, message.chat_id, context)


# ===============================
#   جستجوی هوشمند (state)
# ===============================

async def handle_smart_search_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()
    if _is_cancel_text(text):
        context.user_data.pop("state", None)
        context.user_data.pop("search_scope", None)
        context.user_data.pop("smart_search_results", None)
        await message.reply_text(
            "❌ جستجو لغو شد.", reply_markup=admin_main_keyboard()
        )
        return

    try:
        query_str = text.lower()
        servers = database.get_servers()

        scope = context.user_data.get("search_scope", {"type": "all"})
        only_server_id = None
        if isinstance(scope, dict) and scope.get("type") == "server":
            only_server_id = scope.get("server_id")

        results: List[Dict[str, Any]] = []

        valid_servers: List[Dict[str, Any]] = []
        for s in servers:
            sid = s.get("id")
            if sid is None:
                continue
            if only_server_id is not None and sid != only_server_id:
                continue
            valid_servers.append(s)

        users_batches = await asyncio.gather(*[_list_server_users_fast(s) for s in valid_servers])

        for s, users in zip(valid_servers, users_batches):
            server_id = s.get("id")
            server_title = s.get("title") or f"سرور #{server_id}"

            for u in users:
                user_uuid = str(u.get("uuid") or u.get("id") or "")
                name = u.get("name") or u.get("username") or f"User_{user_uuid}"
                name_l = str(name).lower()

                match = False
                if query_str in name_l:
                    match = True
                elif query_str == user_uuid.lower():
                    match = True
                elif user_uuid and user_uuid.lower() in query_str:
                    match = True

                if match:
                    results.append(
                        {
                            "server_id": server_id,
                            "server_title": server_title,
                            "user_uuid": user_uuid,
                            "name": name,
                            "status": classify_user_status(u),
                        }
                    )

        context.user_data.pop("state", None)
        context.user_data.pop("search_scope", None)

        if not results:
            context.user_data.pop("smart_search_results", None)
            await message.reply_text(
                "❌ کاربر یافت نشد.",
                reply_markup=admin_main_keyboard(),
            )
            return

        context.user_data["smart_search_results"] = results
        await message.reply_text("✅ کاربر یافت شد", reply_markup=admin_main_keyboard())
        await send_smart_search_results_page(message.chat_id, context, page=1)
    except Exception as e:
        logger.exception("Smart search failed: %s", e)
        _clear_search_states(context)
        await message.reply_text(
            "❌ جستجو انجام نشد. لطفاً دوباره تلاش کنید.",
            reply_markup=admin_main_keyboard(),
        )


# ===============================
#   Dispatcherهای عمومی
# ===============================

async def handle_server_state_message(
    state: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if state.startswith("nodes_"):
        await handle_nodes_state_message(state, update, context)
        return

    if state.startswith("add_domain_"):
        await handle_add_domain_flow(state, update, context)
        return

    if state.startswith("add_server_"):
        await handle_add_server_flow(state, update, context)
        return

    if state.startswith("edit_server_"):
        await handle_edit_server_flow(state, update, context)
        return

    if state.startswith("edit_user_"):
        await handle_edit_user_flow(state, update, context)
        return

    if state.startswith("add_user_"):
        await handle_add_user_flow(state, update, context)
        return

    if state == SEARCH_SMART_INPUT:
        await handle_smart_search_input(update, context)
        return

    logger.warning("Unknown state in handle_server_state_message: %s", state)


# ===============================
#   هندلر اینلاین (CallbackQuery)
# ===============================

async def handle_server_inline_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """تمام دکمه‌های اینلاین مربوط به سرورها / کاربران / جستجو را مدیریت می‌کند."""
    query = update.callback_query
    if not query:
        return

    data = (query.data or "").strip()
    msg = query.message
    chat_id = msg.chat_id

    if data == "noop":
        await query.answer()
        return

    # ------ دکمه‌های مربوط به نودها ------
    if data.startswith("nodes:") or data.startswith("delnode:") or data.startswith("nodeinfo:") or data.startswith("nodeedit:"):
        await handle_nodes_inline_callback(update, context)
        return

    # ✅ دکمه‌های مربوط به پلن‌ها را بده به plans.py
    if data.startswith("plans:"):
        await handle_plans_callback(update, context)
        return

    # ------ منوی جستجو (searchmenu:...) ------
    if data.startswith("searchmenu:"):
        await query.answer()
        action = data.split(":", 1)[1]

        if action == "smart":
            try:
                await msg.delete()
            except Exception:
                try:
                    await msg.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            context.user_data["state"] = SEARCH_SMART_INPUT
            await msg.reply_text(
                "🔍 جستجوی هوشمند کاربر در کل ربات\n"
                "نام کاربر، UUID یا لینک کانفیگ را ارسال کنید.",
                reply_markup=cancel_keyboard(),
            )
            return

        if action == "expired":
            await send_expired_users_list(chat_id, context, message=msg)
            return

        if action == "back_main":
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await msg.reply_text(
                "به منوی اصلی برگشتید.",
                reply_markup=admin_main_keyboard(),
            )
            return

    # ------ لیست کاربران منقضی‌شده (expired:...) ------
    if data.startswith("expired:"):
        await query.answer()

        if data == "expired:back":
            await send_search_menu(chat_id=msg.chat_id, context=context, message=msg)
            return

        parts = data.split(":")
        if len(parts) >= 4 and parts[1] == "sel":
            try:
                server_id = int(parts[2])
            except ValueError:
                await msg.edit_text("❌ شناسه سرور نامعتبر است.")
                return
            user_uuid = parts[3]
            await send_expired_user_detail(
                server_id,
                user_uuid,
                msg.chat_id,
                context,
            )
            return

        await msg.edit_text("❌ داده‌ی دکمه منقضی نامعتبر است.")
        return

    # ------ حذف کاربر (deluser:...) ------
    if data.startswith("deluser:"):
        await query.answer()
        try:
            _, sid_str, user_uuid, choice = data.split(":", 3)
            server_id = int(sid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه حذف نامعتبر است.")
            return

        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        if choice == "no":
            await msg.edit_text("❌ حذف کاربر لغو شد.")
            return

        server = database.get_server_by_id(server_id)
        if not server:
            await msg.edit_text("❌ سرور پیدا نشد.")
            return

        related_servers = _get_related_server_targets(server_id)
        if not related_servers:
            related_servers = [server]

        deleted_server_ids: List[int] = []
        failed_servers: List[str] = []

        for target in related_servers:
            try:
                target_id = int(target.get("id") or 0)
            except (TypeError, ValueError):
                target_id = 0
            if target_id <= 0:
                continue

            target_title = (target.get("title") or f"سرور #{target_id}").strip()

            try:
                if hasattr(hiddify_api, "delete_user"):
                    await hiddify_api.delete_user(target, user_uuid)
                else:
                    await hiddify_api.disable_user(target, user_uuid)
            except Exception as e:
                failed_servers.append(f"{target_title}: {e}")
                continue

            deleted_server_ids.append(target_id)

            try:
                local_id = int(user_uuid)
                try:
                    database.delete_user(target_id, local_id)
                except Exception:
                    pass
            except ValueError:
                pass

            # همگام‌سازی با دیتابیس ربات کاربران:
            # اگر کاربر روی پنل حذف شد، سرویس متناظر هم در userbot_services حذف شود.
            try:
                userbot_db.delete_services_by_panel_user(target_id, user_uuid)
            except Exception:
                pass

        if not deleted_server_ids:
            details = "\n".join(failed_servers[:3])
            if details:
                await msg.edit_text(f"❌ حذف کاربر روی هیچ سروری موفق نشد:\n{details}")
            else:
                await msg.edit_text("❌ حذف کاربر روی هیچ سروری موفق نشد.")
            return

        if failed_servers:
            await msg.edit_text(
                "✅ کاربر از سرور اصلی/نودهای قابل‌دسترسی حذف شد.\n"
                f"⚠️ برخی سرورها حذف نشدند: {len(failed_servers)}"
            )
        else:
            await msg.edit_text("✅ کاربر با موفقیت از سرور اصلی و نودهای مرتبط حذف شد.")
        await send_user_list(server_id, chat_id, context)
        return

    # ------ ارسال یک کانفیگ مستقیم انتخاب‌شده (cfgsend:...) ------
    if data.startswith("cfgsend:"):
        await query.answer()
        try:
            _, sid_str, user_uuid, proto, idx_str = data.split(":", 4)
            server_id = int(sid_str)
            idx = int(idx_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه نامعتبر است.")
            return

        cfg_key = f"cfg_{server_id}_{user_uuid}_{proto}"
        items = context.user_data.get(cfg_key, [])

        selected = None
        for item in items:
            if item["idx"] == idx:
                selected = item
                break

        if not selected:
            await msg.edit_text(
                "❌ کانفیگ مورد نظر پیدا نشد. لطفاً دوباره از منوی کانفیگ‌ها انتخاب کنید."
            )
            return

        name = selected["name"]
        link = selected["link"]
        await msg.edit_text(f"{name}\n{link}")
        return

    # ------ دکمه‌های منوی ویرایش کاربر (ued:...) ------
    if data.startswith("ued:"):
        await query.answer()

        if data == "ued:cancel":
            context.user_data.pop("state", None)
            context.user_data.pop("edit_user_server_id", None)
            context.user_data.pop("edit_user_uuid", None)

            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            await msg.edit_text("❌ لغو شد.")
            return

        try:
            _, sid_str, user_uuid, field = data.split(":", 3)
            server_id = int(sid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه نامعتبر است.")
            return

        def set_edit_state(st: str):
            context.user_data["state"] = st
            context.user_data["edit_user_server_id"] = server_id
            context.user_data["edit_user_uuid"] = user_uuid

        cancel_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("لغو❌", callback_data="ued:cancel")]]
        )

        if field == "name":
            set_edit_state(EDIT_STATE_NAME)
            await msg.edit_text(
                "لطفاً نام جدید کاربر را وارد کنید:",
                reply_markup=cancel_kb,
            )
            return

        if field == "activate":
            server = database.get_server_by_id(server_id)
            if not server:
                await msg.edit_text("❌ سرور پیدا نشد.")
                return
            target_user_uuid, changed, total, failed = await _set_user_active_state_on_related_servers(
                server_id,
                user_uuid,
                active=True,
            )
            if changed <= 0:
                detail = f"\n⚠️ سرورهای خطادار: {', '.join(failed[:3])}" if failed else ""
                await msg.edit_text(f"❌ فعال‌سازی کاربر انجام نشد.{detail}")
                return
            if failed:
                try:
                    await query.answer(
                        f"⚠️ روی {changed} از {total} سرور فعال شد.",
                        show_alert=False,
                    )
                except Exception:
                    pass
            await send_user_edit_menu(
                server_id, target_user_uuid, chat_id, context, message=msg
            )
            return

        if field == "deactivate":
            server = database.get_server_by_id(server_id)
            if not server:
                await msg.edit_text("❌ سرور پیدا نشد.")
                return
            target_user_uuid, changed, total, failed = await _set_user_active_state_on_related_servers(
                server_id,
                user_uuid,
                active=False,
            )
            if changed <= 0:
                detail = f"\n⚠️ سرورهای خطادار: {', '.join(failed[:3])}" if failed else ""
                await msg.edit_text(f"❌ غیرفعال‌سازی کاربر انجام نشد.{detail}")
                return
            if failed:
                try:
                    await query.answer(
                        f"⚠️ روی {changed} از {total} سرور غیرفعال شد.",
                        show_alert=False,
                    )
                except Exception:
                    pass
            await send_user_edit_menu(
                server_id, target_user_uuid, chat_id, context, message=msg
            )
            return

        if field == "toggle_active":
            server = database.get_server_by_id(server_id)
            if not server:
                await msg.edit_text("❌ سرور پیدا نشد.")
                return

            target_user_uuid = await _resolve_panel_user_uuid(server, server_id, user_uuid)
            try:
                current = await hiddify_api.get_user_by_uuid(server, target_user_uuid)
            except Exception:
                current = {}

            currently_active = _panel_user_is_active(current if isinstance(current, dict) else {})
            target_user_uuid, changed, total, failed = await _set_user_active_state_on_related_servers(
                server_id,
                target_user_uuid,
                active=(not currently_active),
            )
            if changed <= 0:
                detail = f"\n⚠️ سرورهای خطادار: {', '.join(failed[:3])}" if failed else ""
                await msg.edit_text(f"❌ تغییر وضعیت کاربر انجام نشد.{detail}")
                return
            if failed:
                action_title = "فعال" if not currently_active else "غیرفعال"
                try:
                    await query.answer(
                        f"⚠️ روی {changed} از {total} سرور {action_title} شد.",
                        show_alert=False,
                    )
                except Exception:
                    pass

            await send_user_edit_menu(
                server_id, target_user_uuid, chat_id, context, message=msg
            )
            return

        if field == "usage":
            set_edit_state(EDIT_STATE_USAGE)
            await msg.edit_text(
                "لطفاً محدودیت استفاده جدید (GB) را وارد کنید:\nمثال: 30",
                reply_markup=cancel_kb,
            )
            return

        if field == "days":
            set_edit_state(EDIT_STATE_DAYS)
            await msg.edit_text(
                "لطفاً مدت اشتراک جدید (روز) را وارد کنید:\nمثال: 30",
                reply_markup=cancel_kb,
            )
            return

        if field == "comment":
            set_edit_state(EDIT_STATE_COMMENT)
            await msg.edit_text(
                "لطفاً یادداشت جدید کاربر را وارد کنید:",
                reply_markup=cancel_kb,
            )
            return

        if field == "rename_sub":
            set_edit_state(EDIT_STATE_NAME)
            await msg.edit_text(
                "✍️ لطفاً نام جدید اشتراک را ارسال کنید:",
                reply_markup=cancel_kb,
            )
            return

        if field == "reset_usage":
            server = database.get_server_by_id(server_id)
            if not server:
                await msg.edit_text("❌ سرور پیدا نشد.")
                return
            target_user_uuid = await _resolve_panel_user_uuid(server, server_id, user_uuid)
            now_str = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
            try:
                target_user_uuid, changed, total, failed = await _patch_user_on_related_servers(
                    server_id,
                    target_user_uuid,
                    {"current_usage_GB": 0, "last_reset_time": now_str},
                )
                if changed <= 0:
                    detail = f"\n⚠️ سرورهای خطادار: {', '.join(failed[:3])}" if failed else ""
                    await msg.edit_text(f"❌ بازنشانی حجم انجام نشد.{detail}")
                else:
                    success_text = "✅ حجم مصرفی کاربر به 0 گیگابایت بازنشانی شد."
                    if failed:
                        success_text += f"\n⚠️ روی {changed} از {total} سرور اعمال شد."
                    await msg.edit_text(success_text)
            except Exception as e:
                await msg.edit_text(f"❌ خطا در بازنشانی حجم:\n{e}")
            await send_user_edit_menu(
                server_id, target_user_uuid, chat_id, context
            )
            return

        if field == "reset_days":
            server = database.get_server_by_id(server_id)
            if not server:
                await msg.edit_text("❌ سرور پیدا نشد.")
                return
            target_user_uuid = await _resolve_panel_user_uuid(server, server_id, user_uuid)
            try:
                user_data = await hiddify_api.get_user_by_uuid(server, target_user_uuid)
                package_days = user_data.get("package_days") or 0
                if not package_days:
                    await msg.edit_text(
                        "❌ برای این کاربر مقدار package_days تنظیم نشده است."
                    )
                else:
                    now_str = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
                    target_user_uuid, changed, total, failed = await _patch_user_on_related_servers(
                        server_id,
                        target_user_uuid,
                        {"package_days": int(package_days), "start_date": now_str},
                    )
                    if changed <= 0:
                        detail = f"\n⚠️ سرورهای خطادار: {', '.join(failed[:3])}" if failed else ""
                        await msg.edit_text(f"❌ بازنشانی مدت انجام نشد.{detail}")
                    else:
                        success_text = f"✅ مدت اشتراک کاربر با همان {int(package_days)} روز، از امروز بازنشانی شد."
                        if failed:
                            success_text += f"\n⚠️ روی {changed} از {total} سرور اعمال شد."
                        await msg.edit_text(success_text)
            except Exception as e:
                await msg.edit_text(f"❌ خطا در بازنشانی مدت:\n{e}")
            await send_user_edit_menu(
                server_id, target_user_uuid, chat_id, context
            )
            return

        if field == "refresh":
            await send_user_edit_menu(
                server_id, user_uuid, chat_id, context, message=msg
            )
            return

        await msg.edit_text("❌ گزینه‌ی ویرایش نامعتبر است.")
        return

    # ------ اعمال پلن تمدید (extend:...) ------
    if data.startswith("extend:"):
        await query.answer()
        try:
            _, sid_str, user_uuid, pid_str = data.split(":", 3)
            server_id = int(sid_str)
            plan_id = int(pid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه تمدید نامعتبر است.")
            return

        await apply_plan_to_user(
            server_id, user_uuid, plan_id, chat_id, context
        )
        return

    # ------ انتخاب پلن برای افزودن کاربر جدید (addplan:SERVER_ID:PLAN_ID) ------
    if data.startswith("addplan:"):
        await query.answer()
        try:
            _, sid_str, pid_str = data.split(":", 2)
            server_id = int(sid_str)
            plan_id = int(pid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه پلن نامعتبر است.")
            return

        context.user_data["add_user_server_id"] = server_id
        context.user_data["add_user_plan_id"] = plan_id
        context.user_data["add_user"] = {}
        context.user_data["state"] = ADD_USER_PLAN_NAME

        await msg.edit_text(
            "لطفاً نام کاربر را وارد کنید:",
            reply_markup=cancel_keyboard(),
        )
        return

    # ------ منوی عملیات کاربری (userops:SERVER_ID:ACTION) ------
    if data.startswith("userops:"):
        await query.answer()
        try:
            _, sid_str, action = data.split(":", 2)
            server_id = int(sid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه عملیات کاربری نامعتبر است.")
            return

        if action == "add":
            context.user_data["state"] = ADD_USER_NAME
            context.user_data["add_user_server_id"] = server_id
            context.user_data["add_user"] = {}

            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            await msg.reply_text(
                "لطفاً نام کاربر را وارد کنید:",
                reply_markup=cancel_keyboard(),
            )
            return

        if action == "add_with_plan":
            get_plans = getattr(database, "get_plans", None)
            if not callable(get_plans):
                await msg.edit_text(
                    "❌ تابع get_plans در دیتابیس پیاده‌سازی نشده است، "
                    "بخش «افزودن کاربر با پلن➕» هنوز کامل نشده.",
                    reply_markup=build_user_ops_keyboard(server_id),
                )
                return

            plans = get_plans(server_id) or []

            if not plans:
                await msg.edit_text(
                    "❌ برای این سرور هنوز هیچ پلنی ثبت نشده است.\n"
                    "از منوی «مدیریت ربات کاربران» می‌توانید بعداً پلن‌ها را اضافه کنید.",
                    reply_markup=build_user_ops_keyboard(server_id),
                )
                return

            lines = ["📋 لیست پلن های موجود", ""]
            rows: List[List[InlineKeyboardButton]] = []

            for p in plans:
                pid = p.get("id")
                title = p.get("title") or p.get("name") or f"پلن #{pid}"
                days = p.get("days") or p.get("duration_days") or "-"
                gb = p.get("gb") or p.get("usage_limit_GB") or p.get("volume_GB")
                price = p.get("price") or p.get("price_toman") or "-"

                if gb in (None, "", 0):
                    gb_text = "نامحدود"
                else:
                    gb_text = f"{format_gb(gb)} گیگابایت"

                lines.append(
                    f"• {title} | {price} تومان | {days} روز | {gb_text}"
                )
                rows.append(
                    [
                        InlineKeyboardButton(
                            f"{title} | {price} | {days} روز",
                            callback_data=f"addplan:{server_id}:{pid}",
                        )
                    ]
                )

            rows.append(
                [
                    InlineKeyboardButton(
                        "بازگشت🔙",
                        callback_data=f"server:{server_id}:user_ops",
                    )
                ]
            )

            kb = InlineKeyboardMarkup(rows)
            await msg.edit_text("\n".join(lines), reply_markup=kb)
            return

        if action == "search":
            context.user_data["state"] = SEARCH_SMART_INPUT
            context.user_data["search_scope"] = {
                "type": "server",
                "server_id": server_id,
            }

            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            await msg.reply_text(
                "🔍 جستجوی هوشمند کاربر در این سرور\n"
                "نام کاربر، UUID یا لینک کانفیگ را ارسال کنید.",
                reply_markup=cancel_keyboard(),
            )
            return

        await msg.edit_text(
            "❌ گزینه‌ی عملیات کاربری نامعتبر است.",
            reply_markup=build_user_ops_keyboard(server_id),
        )
        return

    # ------ callbackهای جستجو (search:...) ------
    if data.startswith("search:"):
        await query.answer()

        if data == "search:back":
            context.user_data.pop("smart_search_results", None)
            await send_search_menu(chat_id=msg.chat_id, context=context, message=msg)
            return

        parts = data.split(":")
        if len(parts) == 3 and parts[1] == "page":
            try:
                page = int(parts[2])
            except ValueError:
                await msg.edit_text("❌ شماره صفحه نامعتبر است.")
                return
            await send_smart_search_results_page(
                chat_id=msg.chat_id,
                context=context,
                page=page,
                message=msg,
            )
            return

        if len(parts) == 4 and parts[1] == "sel":
            try:
                server_id = int(parts[2])
            except ValueError:
                await msg.edit_text("❌ داده‌ی انتخاب کاربر نامعتبر است.")
                return
            user_uuid = parts[3]
            await send_user_detail(server_id, user_uuid, chat_id, context)
            return

        await msg.edit_text("❌ داده‌ی جستجو نامعتبر است.")
        return

    # ------ نمایش اطلاعات دامنه (domaininfo:SERVER_ID:DOMAIN_ID) ------
    if data.startswith("domaininfo:"):
        await query.answer(
            "این دامنه برای ساخت لینک و کانفیگ کاربران استفاده می‌شود.",
            show_alert=False,
        )
        return

    # ------ مدیریت دامنه‌ها (domains:SERVER_ID:ACTION) ------
    if data.startswith("domains:"):
        await query.answer()
        try:
            _, sid_str, action = data.split(":", 2)
            server_id = int(sid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه دامنه نامعتبر است.")
            return

        if action == "add":
            # مرحله اول: عنوان دامنه
            context.user_data["state"] = ADD_DOMAIN_TITLE
            context.user_data["domains_server_id"] = server_id
            context.user_data["new_domain"] = {}

            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            await msg.reply_text(
                "برای این دامنه یک عنوان انتخاب کنید (مثلاً: «کاربران»، «User» و ...):",
                reply_markup=cancel_keyboard(),
            )
            return

        if action == "remove":
            await send_domains_delete_menu(server_id, chat_id, context, message=msg)
            return

        if action == "back":
            await send_domains_menu(server_id, chat_id, context, message=msg)
            return

        await msg.edit_text("❌ گزینه‌ی دامنه نامعتبر است.")
        return

    # ------ حذف دامنه (deldomain:SERVER_ID:DOMAIN_ID) ------
    if data.startswith("deldomain:"):
        await query.answer()
        try:
            _, sid_str, did_str = data.split(":", 2)
            server_id = int(sid_str)
            domain_id = int(did_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه حذف دامنه نامعتبر است.")
            return

        try:
            if hasattr(database, "delete_server_domain"):
                deleted = database.delete_server_domain(server_id, domain_id)
            else:
                # حذف از لیست domains داخل خود سرور
                server = database.get_server_by_id(server_id)
                if not server:
                    raise RuntimeError("Server not found")
                domains = server.get("domains") or []
                new_domains = []
                for d in domains:
                    if isinstance(d, dict) and d.get("id") == domain_id:
                        continue
                    new_domains.append(d)
                deleted = len(new_domains) != len(domains)
                if deleted:
                    database.update_server(server_id, {"domains": new_domains})
        except Exception as e:
            await msg.edit_text(f"❌ خطا در حذف دامنه:\n{e}")
            return

        if not deleted:
            await msg.edit_text("❌ دامنه مورد نظر پیدا نشد یا قبلاً حذف شده است.")
        else:
            await msg.edit_text("✅ دامنه با موفقیت حذف شد.")

        await send_domains_menu(server_id, chat_id, context)
        return

    # ------ حذف سرور (serverdel:...) ------
    if data.startswith("serverdel:"):
        await query.answer()
        parts = data.split(":")
        # serverdel:SID  یا serverdel:SID:yes/no
        if len(parts) == 2:
            try:
                server_id = int(parts[1])
            except ValueError:
                await msg.edit_text("❌ شناسه سرور نامعتبر است.")
                return

            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ بله، حذف شود",
                            callback_data=f"serverdel:{server_id}:yes",
                        ),
                        InlineKeyboardButton(
                            "لغو❌",
                            callback_data=f"serverdel:{server_id}:no",
                        ),
                    ]
                ]
            )
            await msg.edit_text(
                "❓ آیا از حذف کامل این سرور مطمئن هستید؟\n"
                "تمام کاربران و پلن‌های مرتبط ممکن است دیگر قابل استفاده نباشند.",
                reply_markup=kb,
            )
            return

        if len(parts) == 3:
            try:
                server_id = int(parts[1])
            except ValueError:
                await msg.edit_text("❌ شناسه سرور نامعتبر است.")
                return
            choice = parts[2]

            if choice == "no":
                server = database.get_server_by_id(server_id)
                if not server:
                    await msg.edit_text("❌ سرور پیدا نشد.")
                    return
                text = await build_server_detail_text_live(server)
                kb = build_server_detail_keyboard(server_id)
                await msg.edit_text(
                    text,
                    reply_markup=kb,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return

            if choice == "yes":
                try:
                    removed_services = 0
                    try:
                        removed_services = int(
                            userbot_db.delete_services_by_server(server_id) or 0
                        )
                    except Exception as cleanup_err:
                        logger.warning(
                            "Failed deleting userbot services for server_id=%s during server delete: %s",
                            server_id,
                            cleanup_err,
                        )
                    database.delete_server(server_id)
                except Exception as e:
                    await msg.edit_text(f"❌ خطا در حذف سرور:\n{e}")
                    return

                await msg.edit_text(
                    f"✅ سرور با موفقیت حذف شد.\n🧹 سرویس‌های پاک‌شده از دیتابیس ربات: {removed_services}"
                )
                await send_servers_list(chat_id, context)
                return

        await msg.edit_text("❌ داده‌ی حذف سرور نامعتبر است.")
        return

    # ------ ویرایش سرور (seredit:SID:field) ------
    if data.startswith("seredit:"):
        await query.answer()
        try:
            _, sid_str, field = data.split(":", 2)
            server_id = int(sid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه ویرایش سرور نامعتبر است.")
            return

        server = database.get_server_by_id(server_id)
        if not server:
            await msg.edit_text("❌ سرور پیدا نشد.")
            return

        def set_server_state(st: str):
            context.user_data["state"] = st
            context.user_data["edit_server_id"] = server_id

        cancel_kb = InlineKeyboardMarkup(
    [[InlineKeyboardButton("لغو❌", callback_data=f"seredit:{server_id}:cancel")]]
)


        if field == "title":
            set_server_state(EDIT_SERVER_TITLE)
            await msg.edit_text(
                "لطفاً عنوان جدید سرور را وارد کنید:",
                reply_markup=cancel_kb,
            )
            return

        if field == "panel_url":
            set_server_state(EDIT_SERVER_PANEL_URL)
            await msg.edit_text(
                "🌐 لطفاً آدرس جدید پنل را وارد کنید:\nمثال: https://site.example.com",
                reply_markup=cancel_kb,
            )
            return

        if field == "admin_proxy":
            set_server_state(EDIT_SERVER_ADMIN_PROXY)
            await msg.edit_text(
                "🔑 لطفاً کد مسیر جدید ادمین را وارد کنید (Admin Proxy Path):",
                reply_markup=cancel_kb,
            )
            return

        if field == "admin_uuid":
            set_server_state(EDIT_SERVER_ADMIN_UUID)
            await msg.edit_text(
                "🧩 لطفاً کلید جدید ادمین را وارد کنید (UUID / API Key):\n"
                "⚠️ فقط مقدار خالص، بدون / و بدون آدرس",
                reply_markup=cancel_kb,
            )
            return

        if field == "user_proxy":
            set_server_state(EDIT_SERVER_USER_PROXY)
            await msg.edit_text(
                "🔑 لطفاً کد مسیر جدید کاربران را وارد کنید (User Proxy Path):",
                reply_markup=cancel_kb,
            )
            return

        if field == "limit":
            set_server_state(EDIT_SERVER_LIMIT)
            await msg.edit_text(
                "📊 لطفاً محدودیت جدید کاربران را (عدد) وارد کنید:",
                reply_markup=cancel_kb,
            )
            return

        if field == "priority":
            set_server_state(EDIT_SERVER_PRIORITY)
            await msg.edit_text(
                "🔢 لطفاً اولویت جدید سرور را وارد کنید (صفر یا بیشتر):",
                reply_markup=cancel_kb,
            )
            return

        if field == "test":
            try:
                await hiddify_api.list_users(server)
                await msg.edit_text(
                    "✅ اتصال به پنل با موفقیت انجام شد.",
                    reply_markup=build_server_detail_keyboard(server_id),
                )
            except Exception as e:
                await msg.edit_text(
                    f"❌ اتصال به پنل ناموفق بود:\n{e}",
                    reply_markup=build_server_detail_keyboard(server_id),
                )
            return

        if field == "cancel":
            context.user_data.pop("state", None)
            context.user_data.pop("edit_server_id", None)
            await msg.edit_text("❌ ویرایش سرور لغو شد.")
            return

        await msg.edit_text("❌ گزینه‌ی ویرایش سرور نامعتبر است.")
        return

    # ------ مدیریت سرورها (servers:...) و server:... ------
    await query.answer()

    if data == "servers:list_back":
        await send_servers_list(chat_id=chat_id, context=context, message=msg)
        return

    if data == "servers:add":
        context.user_data["state"] = ADD_STATE_TITLE
        context.user_data["new_server"] = {}
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        prereq_cmd = f'sudo bash -c "$(curl -fsSL {PANEL_PREREQ_SCRIPT_URL})" install'
        await msg.reply_text(
            "⚠️ قبل از اضافه کردن سرور، حتما اسکریپت پیش‌نیاز را روی همان سرور پنل هیدیفای نصب کنید:\n\n"
            "```shell\n"
            f"{prereq_cmd}\n"
            "```",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        await msg.reply_text(
            "لطفاً عنوان سرور را وارد کنید:",
            reply_markup=cancel_keyboard(),
        )
        return

    if data.startswith("server:"):
        parts = data.split(":")

        if len(parts) == 2:
            try:
                server_id = int(parts[1])
            except ValueError:
                await msg.edit_text("❌ شناسه سرور نامعتبر است.")
                return
            server = database.get_server_by_id(server_id)
            if not server:
                await msg.edit_text("❌ سرور پیدا نشد.")
                return
            text = await build_server_detail_text_live(server)
            kb = build_server_detail_keyboard(server_id)
            await msg.edit_text(
                text,
                reply_markup=kb,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return

        try:
            server_id = int(parts[1])
        except ValueError:
            await msg.edit_text("❌ شناسه سرور نامعتبر است.")
            return

        action = parts[2]

        if action == "users":
            page = 1
            if len(parts) >= 4:
                try:
                    page = int(parts[3])
                except ValueError:
                    page = 1
            await send_user_list(server_id, chat_id, context, message=msg, page=page)
            return

        if action == "useruuid" and len(parts) >= 4:
            user_uuid = parts[3]
            await send_user_detail(server_id, user_uuid, chat_id, context, message=msg)
            return

        if action == "usercfg":
            if len(parts) == 4:
                user_uuid = parts[3]
                await send_user_configs_menu(
                    server_id, user_uuid, chat_id, context, message=msg
                )
                return
            if len(parts) >= 5:
                user_uuid = parts[3]
                cfg_type = parts[4]
                server = database.get_server_by_id(server_id)
                if not server:
                    await msg.edit_text("❌ سرور پیدا نشد.")
                    return

                if cfg_type == "direct":
                    await send_direct_config_menu(
                        server_id, user_uuid, chat_id, context, message=msg
                    )
                    return

                base = _build_user_base_url(server, user_uuid)
                if not base:
                    await msg.edit_text(
                        "❌ تنظیمات panel_url یا user_proxy_path برای این سرور کامل نیست.",
                    )
                    return

                url = ""
                caption_title = ""

                if cfg_type == "auto_sub":
                    url = f"{base}/sub/?asn=unknown"
                    caption_title = "لینک اشتراک خودکار"
                elif cfg_type == "sub":
                    url = f"{base}/all.txt"
                    caption_title = "لینک اشتراک"
                elif cfg_type == "sub_b64":
                    url = f"{base}/all.txt?base64=True"
                    caption_title = "لینک اشتراک b64"
                elif cfg_type == "multi":
                    url = f"{base}/hidybot.txt"
                    caption_title = "Multi Server"
                elif cfg_type == "multi_b64":
                    url = f"{base}/hidybot.txt?base64=True"
                    caption_title = "Multi Server b64"
                elif cfg_type == "bot_link":
                    if not SUB_BOT_USERNAME:
                        await msg.edit_text(
                            "❌ متغیر SUB_BOT_USERNAME در فایل .env تنظیم نشده است.",
                        )
                        return
                    url = f"https://t.me/{SUB_BOT_USERNAME}?start={user_uuid}"
                    text = f"لینک اتصال اشتراک به ربات 🤖\n{url}"
                    kb = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "بازگشت به منوی کانفیگ‌ها",
                                    callback_data=f"server:{server_id}:usercfg:{user_uuid}",
                                )
                            ]
                        ]
                    )
                    await msg.edit_text(text, reply_markup=kb)
                    return
                else:
                    await msg.edit_text("این گزینه هنوز پیاده‌سازی نشده است.")
                    return

                qr_image = make_qr_image(url)
                caption = f"{caption_title}\n{url}"
                kb = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔙 بازگشت به منوی کانفیگ‌ها",
                                callback_data=f"server:{server_id}:usercfg:{user_uuid}",
                            )
                        ]
                    ]
                )
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=qr_image,
                    caption=caption,
                    reply_markup=kb,
                )
                return

        if action == "directcfg" and len(parts) >= 5:
            user_uuid = parts[3]
            proto = parts[4].lower()

            server = database.get_server_by_id(server_id)
            if not server:
                await msg.edit_text("❌ سرور پیدا نشد.")
                return

            try:
                configs = await hiddify_api.get_user_configs(server, user_uuid)
            except hiddify_api.HiddifyApiError as e:
                kb_err = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "بازگشت به منوی کانفیگ‌ها",
                                callback_data=f"server:{server_id}:usercfg:{user_uuid}",
                            )
                        ]
                    ]
                )
                await msg.edit_text(
                    f"❌ خطا در دریافت کانفیگ‌ها از Hiddify API:\n{e}",
                    reply_markup=kb_err,
                )
                return

            links: List[str] = []
            seen_links: set[str] = set()
            for cfg in configs or []:
                c_proto = str(cfg.get("protocol") or "").strip().lower()
                link = str(cfg.get("link") or "").strip()
                if not link:
                    continue

                link_l = link.lower()
                if not c_proto:
                    if link_l.startswith("vless://"):
                        c_proto = "vless"
                    elif link_l.startswith("vmess://"):
                        c_proto = "vmess"
                    elif link_l.startswith("trojan://"):
                        c_proto = "trojan"

                normalized = c_proto.replace("-", "").replace("_", "")
                if proto not in normalized and not link_l.startswith(f"{proto}://"):
                    continue
                if link in seen_links:
                    continue
                seen_links.add(link)
                links.append(link)

            if not links:
                text = (
                    f"❌ کانفیگ مستقیم {proto.upper()} یافت نشد.\n"
                    "برای این کاربر هیچ کانفیگ مناسبی یافت نشد."
                )
                kb = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔙 بازگشت به منوی کانفیگ‌ها",
                                callback_data=f"server:{server_id}:usercfg:{user_uuid}",
                            )
                        ]
                    ]
                )
                await msg.edit_text(text, reply_markup=kb)
                return

            header = f"🔗 کانفیگ‌های {proto.upper()}"
            all_links_text = "\n".join(links)
            one_block_text = (
                f"{header}\n"
                "برای کپی، کل باکس زیر را یکجا کپی کنید:\n"
                f"<pre><code class=\"language-shell\">{escape(all_links_text)}</code></pre>"
            )
            back_kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 بازگشت به منوی کانفیگ‌ها",
                            callback_data=f"server:{server_id}:usercfg:{user_uuid}",
                        )
                    ]
                ]
            )

            if len(one_block_text) <= 3900:
                await msg.edit_text(
                    one_block_text,
                    reply_markup=back_kb,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return

            max_payload = 2800
            chunks: List[List[str]] = []
            cur: List[str] = []
            cur_len = 0
            for link in links:
                add = len(link) + 1
                if cur and (cur_len + add > max_payload):
                    chunks.append(cur)
                    cur = [link]
                    cur_len = add
                else:
                    cur.append(link)
                    cur_len += add
            if cur:
                chunks.append(cur)

            for idx, chunk in enumerate(chunks, start=1):
                part_header = (
                    header
                    if len(chunks) == 1
                    else f"{header} ({idx}/{len(chunks)})"
                )
                part_text = (
                    f"{part_header}\n"
                    "برای کپی، باکس زیر را کپی کنید:\n"
                    f"<pre><code class=\"language-shell\">{escape(chr(10).join(chunk))}</code></pre>"
                )
                if idx == 1:
                    await msg.edit_text(
                        part_text,
                        reply_markup=back_kb,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=part_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
            return

        if action == "useredit" and len(parts) >= 4:
            user_uuid = parts[3]
            await send_user_edit_menu(
                server_id, user_uuid, chat_id, context, message=msg
            )
            return

        if action == "userextend" and len(parts) >= 4:
            user_uuid = parts[3]
            await send_user_extend_menu(
                server_id, user_uuid, chat_id, context, message=msg
            )
            return

        if action == "userdel" and len(parts) >= 4:
            user_uuid = parts[3]
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ بله، حذف شود",
                            callback_data=f"deluser:{server_id}:{user_uuid}:yes",
                        ),
                        InlineKeyboardButton(
                            "لغو❌",
                            callback_data=f"deluser:{server_id}:{user_uuid}:no",
                        ),
                    ]
                ]
            )
            await msg.edit_text(
                "❓ آیا از حذف کامل این کاربر مطمئن هستید؟\n"
                "این عملیات قابل بازگشت نیست.",
                reply_markup=kb,
            )
            return

        if action == "user_ops":
            await msg.edit_text(
                "عملیات کاربری🛡️\n"
                "در این بخش می‌توانید کاربران جدید اضافه کنید یا بین کاربران جستجو کنید.",
                reply_markup=build_user_ops_keyboard(server_id),
            )
            return

        if action == "plans":
            await send_plans_root_menu(
                server_id,
                chat_id,
                context,
                message=msg,
            )
            return

        if action == "domains":
            await send_domains_menu(server_id, chat_id, context, message=msg)
            return

        if action == "edit":
            await send_server_edit_menu(
                server_id, chat_id, context, message=msg
            )
            return

        if action == "nodes":
            # باز کردن منوی نودها (فاز ۱: فقط افزودن/حذف/لیست)
            await send_nodes_menu(server_id, chat_id, context, message=msg)
            return

        if action == "sync_nodes":
            # فاز ۱: فقط پیام اطلاع‌رسانی – منطق واقعی Sync را می‌گذاریم برای فاز ۲
            await msg.edit_text(
                "🔄 بخش «همگام‌سازی نودها» برای فاز بعدی (مولتی‌سرور هوشمند) در نظر گرفته شده است.\n"
                "فعلاً فقط می‌توانید نودها را از منوی «لیست نودها» مدیریت کنید. ✅",
                reply_markup=build_server_detail_keyboard(server_id),
            )
            return

# ===============================
#   هندلر اصلی منوی ادمین
# ===============================

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE): 
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()
    text_key = _menu_key(text)
    chat_id = update.effective_chat.id

    # اگر وسط جستجو بودیم و کاربر دکمه‌های منوی اصلی را زد، state را آزاد کن.
    if context.user_data.get(USER_SEARCH_STATE_KEY):
        if _is_cancel_text(text):
            context.user_data.pop(USER_SEARCH_STATE_KEY, None)
            await message.reply_text("❌ جستجو لغو شد.", reply_markup=admin_main_keyboard())
            return
        if not _is_any_main_menu_button(text, text_key):
            await handle_user_search_message(update, context)
            return
        context.user_data.pop(USER_SEARCH_STATE_KEY, None)

    if context.user_data.get("state") == SEARCH_SMART_INPUT and _is_any_main_menu_button(text, text_key):
        _clear_search_states(context)

    # مسیرهای ورودی متنی ادمین (ویزاردها)
    # NOTE: کلید متنی userbot_sub_reminder_edit هم عمداً چک می‌شود
    # تا در هر شرایطی ویزارد یادآور از دست نرود.
    if context.user_data.get(WALLET_EDIT_STATE) or \
       context.user_data.get(MESSAGE_SEND_STATE) or \
       context.user_data.get(SUB_REMINDER_EDIT_STATE) or \
       context.user_data.get(TRIAL_SPEC_EDIT_STATE) or \
       context.user_data.get(RENEW_POLICY_EDIT_STATE) or \
       context.user_data.get(TEXT_SETTINGS_EDIT_STATE) or \
       context.user_data.get(INVITE_BANNER_PHOTO_EDIT_STATE) or \
       context.user_data.get(MARKETING_EDIT_STATE) or \
       context.user_data.get(FORCE_JOIN_EDIT_STATE) or \
       context.user_data.get(PAYMENT_CHANNEL_EDIT_STATE) or \
       context.user_data.get(BACKUP_CHANNEL_EDIT_STATE) or \
       context.user_data.get(BACKUP_RESTORE_STATE) or \
       context.user_data.get(PAYMENT_CARD_ADD_STATE) or \
       context.user_data.get(PAYMENT_CARD_EDIT_STATE) or \
       context.user_data.get(PAYMENT_CARD_DELETE_STATE) or \
       context.user_data.get(ZARIN_COUPON_ADD_STATE) or \
       context.user_data.get(ZARIN_COUPON_DELETE_STATE) or \
       context.user_data.get(ZARIN_COUPON_LINK_STATE) or \
       context.user_data.get(ZARIN_COUPON_AMOUNT_STATE) or \
       context.user_data.get(ZARIN_COUPON_CODE_STATE) or \
       context.user_data.get(ZARIN_COUPON_LIMIT_STATE) or \
       context.user_data.get(ZARIN_COUPON_EXP_STATE) or \
       context.user_data.get(SUB_TRACKING_STATE) or \
       context.user_data.get(TICKET_REPLY_STATE) or \
       context.user_data.get(BROADCAST_SEND_STATE) or \
       context.user_data.get(SUB_BASE_URL_EDIT_STATE) or \
       context.user_data.get("userbot_ticket_reply") or \
       context.user_data.get("userbot_sub_reminder_edit") or \
       context.user_data.get("userbot_sub_base_url_edit") or \
       context.user_data.get(ORDERS_SEARCH_STATE_KEY) or \
       context.user_data.get(PAYMENT_SEARCH_STATE):
        
        await handle_admin_text_input(update, context)
        return

    if _is_status_button(text, text_key):
        await send_status_servers_list(update.effective_chat.id, context, message)
        return

    state = context.user_data.get("state")
    if state and str(state).startswith("plans:"):
        await handle_plans_message(str(state), update, context)
        return

    if state:
        await handle_server_state_message(state, update, context)
        return

    # اگر مدیر روی دکمه «مدیریت ربات کاربران🤖» کلیک کرد
    if _is_userbot_button(text, text_key):
        # منوی اینلاین مدیریت ربات کاربران را نشان بده
        await handle_userbot_entry(update, context)
        return

    if _is_backup_button(text, text_key):
        await send_admin_full_backup(chat_id, context, message=message)
        return

    # دکمه‌های منوی اصلی ادمین
    if _is_servers_button(text, text_key):
        await send_servers_list(chat_id, context)
    elif _is_search_button(text, text_key):
        await send_search_menu(chat_id, context)
    else:
        await message.reply_text(
            "گزینه انتخاب‌شده معتبر نیست.",
            reply_markup=admin_main_keyboard(),
        )

# ==============================
#   هندلر خطا (Error Handler)
# ===============================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ثبت خطاهای احتمالی برای لاگ‌ها"""
    err = context.error
    if isinstance(err, NetworkError):
        logger.warning("Telegram network error: %s", err)
        return
    logger.error("Exception while handling an update:", exc_info=err)

    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("❌ خطایی در سرور رخ داد.")
    except Exception:
        # حتی اگر خود ارسال پیام خطا داد، نذار کل بات بپُره
        pass


# ===============================
#   رَپر برای main.py
# ===============================

async def admin_inline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    msg = query.message

    # --- هندلرهای وضعیت سرور ---
    if data == "status:back":
        await query.answer()
        if msg:
            try:
                await msg.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text="به منوی اصلی برگشتید.",
                reply_markup=admin_main_keyboard(),
            )
        return

    if data == "status:back_to_list":
        await query.answer()
        if msg:
            await send_status_servers_list(msg.chat_id, context, msg=msg)
        return

    if data.startswith("status_srv:"):
        await query.answer()
        if not msg:
            return
        try:
            srv_id = int(data.split(":")[1])
        except (IndexError, ValueError):
            await msg.edit_text("❌ شناسه سرور نامعتبر است.")
            return
        await send_server_status_detail(msg.chat_id, context, srv_id, msg=msg)
        return

    # اگر callback مربوط به منوی «مدیریت ربات کاربران🤖» بود
    if data.startswith("userbot:"):
        await handle_userbot_callback(update, context)
        return

    # بقیه callback ها مثل قبل برن سمت منطق سرورها / پلن‌ها / نودها و ...
    await handle_server_inline_callback(update, context)

# --- اضافه به AdminBot/servers.py ---

# منوی لیست سرورها برای وضعیت
async def send_status_servers_list(chat_id: int, context: ContextTypes.DEFAULT_TYPE, msg=None):
    servers = database.get_servers()
    child_ids = _get_child_server_ids()
    if not servers:
        text = "❌ سروری یافت نشد."
        if msg: await msg.reply_text(text, reply_markup=admin_main_keyboard())
        else: await context.bot.send_message(chat_id, text, reply_markup=admin_main_keyboard())
        return

    # دکمه‌ها دقیقاً مثل عکس (زیر هم)
    rows = []
    for s in servers:
        sid = s.get("id")
        try:
            sid_int = int(sid or 0)
        except (TypeError, ValueError):
            sid_int = 0
        if sid_int <= 0 or sid_int in child_ids:
            continue
        title = (s.get("title") or "Server").strip()
        # تلاش برای اضافه کردن پرچم (سلیقه‌ای طبق عکس)
        flag = "🏳️"
        if "ترکیه" in title: flag = "🇹🇷"
        elif "آلمان" in title: flag = "🇩🇪"
        elif "هلند" in title: flag = "🇳🇱"
        elif "فنلاند" in title: flag = "🇫🇮"

        # جلوگیری از تکرار «لوکیشن/پرچم» اگر در title از قبل باشد
        has_location_word = "لوکیشن" in title
        has_flag = flag != "🏳️" and flag in title
        if has_location_word:
            if has_flag:
                btn_text = title
            else:
                btn_text = f"{title} {flag}" if flag != "🏳️" else title
        else:
            btn_text = f"لوکیشن {flag} {title}" if flag != "🏳️" else f"لوکیشن {title}"

        rows.append([InlineKeyboardButton(btn_text, callback_data=f"status_srv:{sid_int}")])

    if not rows:
        text = "❌ سروری یافت نشد."
        if msg:
            await msg.reply_text(text, reply_markup=admin_main_keyboard())
        else:
            await context.bot.send_message(chat_id, text, reply_markup=admin_main_keyboard())
        return
    
    rows.append([InlineKeyboardButton("بازگشت🔙", callback_data="status:back")])
    kb = InlineKeyboardMarkup(rows)
    
    text = "📈 **وضعیت سرور**\n\nیکی از سرورهای زیر را انتخاب کنید:"
    
    if msg:
        try: await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        except: await context.bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)


# نمایش جزئیات (دقیقاً مثل عکس دوم)
async def send_server_status_detail(chat_id: int, context: ContextTypes.DEFAULT_TYPE, server_id: int, msg=None):
    server = database.get_server_by_id(server_id)
    if not server:
        await context.bot.send_message(chat_id, "❌ سرور پیدا نشد.")
        return

    # دریافت آمار (ممکن است چند ثانیه طول بکشد، پیام ویتینگ می‌دهیم)
    if msg:
        try: await msg.edit_text("⏳ در حال دریافت اطلاعات از سرور...")
        except: pass
    
    try:
        stats = await hiddify_api.get_server_stats(server)
    except Exception as e:
        stats = {} # یا هندل کردن ارور
        logger.error(f"Stats Error: {e}")

    # استخراج مقادیر یا استفاده از پیش‌فرض برای شباهت به عکس
    cpu = stats.get('cpu_percent', 0)
    core = stats.get('cpu_cores', 1)
    
    ram_u = stats.get('ram_used', 0)
    ram_t = stats.get('ram_total', 1)
    ram_p = (ram_u / ram_t * 100) if ram_t else 0
    
    disk_u = stats.get('disk_used', 0)
    disk_t = stats.get('disk_total', 20)
    disk_p = (disk_u / disk_t * 100) if disk_t else 0
    
    u_total = stats.get('users_total', 0)
    u_online_now = stats.get('users_online', 0)
    u_active_today = stats.get('users_today', 0)
    u_active_30 = stats.get('users_month', 0)
    
    usage_today = stats.get('usage_today_gb', 0)
    usage_30 = stats.get('usage_30days_gb', 0)

    dl_total = stats.get('traffic_dl', 0)
    ul_total = stats.get('traffic_ul', 0)
    net_now_recv_mb = stats.get('now_net_recv_mb', 0)
    net_now_sent_mb = stats.get('now_net_sent_mb', 0)

    # نام سرور + پرچم (بدون تکرار)
    title = (server.get('title') or 'Server').strip()
    flag = ""
    if "ترکیه" in title: flag = "🇹🇷"
    elif "آلمان" in title: flag = "🇩🇪"
    elif "هلند" in title: flag = "🇳🇱"
    elif "فنلاند" in title: flag = "🇫🇮"

    has_location_word = "لوکیشن" in title
    has_flag = bool(flag) and (flag in title)
    if has_location_word:
        title_line = title if has_flag else (f"{title} {flag}" if flag else title)
    else:
        title_line = f"لوکیشن {flag} {title}" if flag else f"لوکیشن {title}"

    # فرمت بندی دقیق متن (طبق عکس)
    text = (
        f"Server: {title_line}\n"
        "--------------------------------\n"
        "SYSTEM INFO\n"
        f"CPU: {cpu}% - {core} CORE\n"
        f"RAM: {ram_u:.2f} GB / {ram_t:.2f} GB ({ram_p:.2f}%)\n"
        f"DISK: {disk_u:.2f} GB / {disk_t:.2f} GB  ({disk_p:.2f}%)\n\n"
        "NETWORK INFO\n"
        f"Total Users: {u_total} User\n"
        f"Usage (Today): {usage_today:.2f} GB\n"
        f"Online (Now): {u_online_now} User\n"
        f"Now Network Received: {net_now_recv_mb:.2f} MB\n"
        f"Now Network Sent: {net_now_sent_mb:.2f} MB\n"
        f"Online (Today): {u_active_today} User\n"
        f"Online(30 Days): {u_active_30} User\n"
        f"Usage(30 Days): {usage_30:.2f} GB\n"
        f"Total Download (Server): {dl_total:.2f} GB\n"
        f"Total Upload (Server): {ul_total:.2f} GB"
    )
    
    # دکمه بازگشت به لیست لوکیشن‌ها
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("بازگشت🔙", callback_data=f"status:back_to_list")]
    ])
    
    if msg:
        await msg.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)
