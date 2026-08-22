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
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes
from telegram.error import BadRequest, NetworkError
from pathlib import Path
from AdminBot.nodes import _short_error
import sys
from urllib.parse import urlparse

# اضافه کردن ریشه پروژه به sys.path تا Shared و بقیه ماژول‌ها پیدا شوند
ROOT_DIR = Path(__file__).resolve().parents[1]  # پوشه‌ی Hiddify-SellBot
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from Shared import database, hiddify_api, userbot_db, plans_storage, xui_api
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from AdminBot.keyboards import (
    admin_main_keyboard,
    confirm_add_user_keyboard,
    BTN_SERVERS,
    BTN_SEARCH_USER,
    BTN_USERBOT,
    BTN_STATUS,
    BTN_BACKUP,
    BTN_AGENCIES,
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
    ZARIN_COUPON_BULK_STATE,
    SUB_TRACKING_STATE,
    TICKET_REPLY_STATE,
    BROADCAST_SEND_STATE,
    SUB_BASE_URL_EDIT_STATE,
    TX_PLANS_EDIT_STATE,
    REFERRAL_VALUE_EDIT_STATE,
    REFERRAL_MANUAL_REWARD_STATE,
)

from AdminBot.agencies import (
    handle_agencies_entry,
    handle_agencies_callback,
    handle_agencies_text,
)


load_dotenv()
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
    return text in {BTN_SERVERS, "مدیریت سرورها🖥", "🖥️ سرورها"} or "مدیریتسرورها" in text_key or "سرورها" in text_key


def _is_search_button(text: str, text_key: str) -> bool:
    return text in {BTN_SEARCH_USER, "🔍 جستجوی کاربر", "جستجوی کاربر🔍"} or "جستجویکاربر" in text_key


def _is_userbot_button(text: str, text_key: str) -> bool:
    return text in {BTN_USERBOT, "🤖 مدیریت ربات کاربران", "🤖 ربات کاربران"} or "مدیریترباتکاربران" in text_key or "ربات کاربران" in text_key


def _is_status_button(text: str, text_key: str) -> bool:
    return text in {BTN_STATUS, "📊 وضعیت سرور", "📈 وضعیت سرور", "🖥 وضعیت سرور"} or "وضعیتسرور" in text_key


def _is_backup_button(text: str, text_key: str) -> bool:
    return text in {BTN_BACKUP, "📫 دریافت بکاپ", "📣 دریافت بکاپ", "📬 دریافت بکاپ"} or "دریافتبکاپ" in text_key


def _is_agencies_button(text: str, text_key: str) -> bool:
    return text in {BTN_AGENCIES, "🏢 نمایندگی", "🏢 نمایندگی‌ها"} or "نمایندگی" in text_key or "مدیریتنمایندهها" in text_key


def _is_any_main_menu_button(text: str, text_key: str) -> bool:
    return (
        _is_servers_button(text, text_key)
        or _is_search_button(text, text_key)
        or _is_userbot_button(text, text_key)
        or _is_status_button(text, text_key)
        or _is_backup_button(text, text_key)
        or _is_agencies_button(text, text_key)
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
ADD_STATE_TYPE = "add_server_type"
ADD_STATE_TITLE = "add_server_title"
ADD_STATE_PANEL_URL = "add_server_panel_url"
ADD_STATE_ADMIN_PROXY = "add_server_admin_proxy"
ADD_STATE_ADMIN_UUID = "add_server_admin_uuid"
ADD_STATE_USER_PROXY = "add_server_user_proxy"
ADD_STATE_LIMIT = "add_server_limit"
ADD_STATE_XUI_USERNAME = "add_server_xui_username"
ADD_STATE_XUI_PASSWORD = "add_server_xui_password"
ADD_STATE_XUI_SUB_DOMAIN = "add_server_xui_sub_domain"
ADD_STATE_XUI_INBOUND = "add_server_xui_inbound"

# ویرایش سرور
EDIT_SERVER_TITLE = "edit_server_title"
EDIT_SERVER_PANEL_URL = "edit_server_panel_url"
EDIT_SERVER_ADMIN_PROXY = "edit_server_admin_proxy"
EDIT_SERVER_ADMIN_UUID = "edit_server_admin_uuid"
EDIT_SERVER_USER_PROXY = "edit_server_user_proxy"
EDIT_SERVER_LIMIT = "edit_server_limit"
EDIT_SERVER_PRIORITY = "edit_server_priority"
EDIT_SERVER_XUI_USERNAME = "edit_server_xui_username"
EDIT_SERVER_XUI_PASSWORD = "edit_server_xui_password"
EDIT_SERVER_XUI_SUB_DOMAIN = "edit_server_xui_sub_domain"
EDIT_SERVER_XUI_INBOUND = "edit_server_xui_inbound"

# افزودن کاربر
ADD_USER_NAME = "add_user_name"
ADD_USER_USAGE = "add_user_usage"
ADD_USER_DAYS = "add_user_days"
ADD_USER_CONFIRM = "add_user_confirm"
ADD_MULTI_USERS_INPUT = "add_user_multi_count"
ADD_MULTI_USERS_BASE_NAME = "add_user_multi_base_name"
ADD_MULTI_USERS_USAGE = "add_user_multi_usage"
ADD_MULTI_USERS_DAYS = "add_user_multi_days"
ADD_MULTI_USERS_CONFIRM = "add_user_multi_confirm"
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


_FA_DIGITS_TRANS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _normalize_num_token(raw: str) -> str:
    token = str(raw or "").strip().translate(_FA_DIGITS_TRANS)
    token = token.replace("٬", "").replace("٫", ".").replace(" ", "")
    if token.count(",") > 0:
        # 1,5 => 1.5
        if "." not in token and token.count(",") == 1 and len(token.split(",")[-1]) <= 2:
            token = token.replace(",", ".")
        else:
            token = token.replace(",", "")
    return token


def _parse_positive_float(raw: str) -> Optional[float]:
    token = _normalize_num_token(raw)
    try:
        value = float(token)
    except Exception:
        return None
    if value <= 0:
        return None
    return value


def _parse_positive_int(raw: str) -> Optional[int]:
    token = _normalize_num_token(raw)
    try:
        value = int(token)
    except Exception:
        return None
    if value <= 0:
        return None
    return value


def _parse_bulk_users_text(raw_text: str) -> tuple[List[Dict[str, Any]], List[str]]:
    entries: List[Dict[str, Any]] = []
    errors: List[str] = []
    lines = [ln.strip() for ln in str(raw_text or "").splitlines() if ln.strip()]

    for idx, line in enumerate(lines, start=1):
        name = ""
        gb_raw = ""
        days_raw = ""

        parts = re.split(r"\s*[|,،;]\s*", line, maxsplit=2)
        if len(parts) == 3:
            name, gb_raw, days_raw = [p.strip() for p in parts]
        else:
            tokens = [t.strip() for t in line.split() if t.strip()]
            if len(tokens) >= 3:
                name = " ".join(tokens[:-2]).strip()
                gb_raw = tokens[-2]
                days_raw = tokens[-1]

        if not name:
            errors.append(f"خط {idx}: نام کاربر نامعتبر است.")
            continue

        gb = _parse_positive_float(gb_raw)
        if gb is None:
            errors.append(f"خط {idx}: حجم نامعتبر است («{gb_raw}»).")
            continue

        days = _parse_positive_int(days_raw)
        if days is None:
            errors.append(f"خط {idx}: روز نامعتبر است («{days_raw}»).")
            continue

        entries.append(
            {
                "name": name,
                "usage_limit_GB": float(gb),
                "package_days": int(days),
            }
        )

    if not entries and not errors:
        errors.append("هیچ خط معتبری دریافت نشد.")
    return entries, errors


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


def _last_online_line(user_data: Dict[str, Any]) -> str:
    forced_status = str(user_data.get("_user_list_status") or "").lower()
    if forced_status == "online" or classify_user_status(user_data) == "online":
        return "📶آخرین اتصال: آنلاین"

    last_dt = _parse_dt(user_data.get("last_online"))
    if not last_dt:
        return "📶آخرین اتصال: نامشخص"

    delta = datetime.now() - last_dt
    seconds_total = delta.total_seconds()
    if -CLOCK_SKEW_TOLERANCE <= seconds_total <= ONLINE_WINDOW_SECONDS:
        return "📶آخرین اتصال: آنلاین"

    if seconds_total < 0:
        rel = "چند ثانیه پیش"
    else:
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

    return f"📶آخرین اتصال: {rel}"


def _merge_user_list_snapshot(
    user_data: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not snapshot:
        return user_data

    merged = dict(user_data)

    cached_last_online = snapshot.get("last_online")
    cached_dt = _parse_dt(cached_last_online)
    current_dt = _parse_dt(merged.get("last_online"))
    if cached_dt and (not current_dt or cached_dt > current_dt):
        merged["last_online"] = cached_last_online

    if str(snapshot.get("status") or "").lower() == "online":
        try:
            fetched_at = float(snapshot.get("fetched_at") or 0)
        except (TypeError, ValueError):
            fetched_at = 0.0
        cache_age = datetime.now().timestamp() - fetched_at if fetched_at else 0.0
        if not fetched_at or cache_age <= ONLINE_WINDOW_SECONDS + CLOCK_SKEW_TOLERANCE:
            merged["_user_list_status"] = "online"

    return merged


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


def _panel_user_link_from_base(base_url: Optional[str]) -> str:
    base = str(base_url or "").strip()
    if not base:
        return ""
    return f"{base.rstrip('/')}/"


def _public_origin_from_url(raw_url: str) -> str:
    raw = str(raw_url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except Exception:
        return ""
    scheme = (parsed.scheme or "https").strip().lower()
    host = (parsed.hostname or "").strip()
    if not host:
        return ""
    port = parsed.port
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = host if port is None or default_port else f"{host}:{port}"
    return f"{scheme}://{netloc}"


def _public_origin_from_host(raw_host: str, *, default_scheme: str = "https", default_port: int = 443) -> str:
    raw = str(raw_host or "").strip().rstrip("/")
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"//{raw}")
    except Exception:
        return ""
    scheme = (parsed.scheme or default_scheme or "https").strip().lower()
    host = (parsed.hostname or "").strip()
    if not host:
        return ""
    port = parsed.port if parsed.port is not None else int(default_port or 0)
    default = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    return f"{scheme}://{host}" if default else f"{scheme}://{host}:{port}"


def _resolve_managed_sub_base_url_for_admin(server: Optional[Dict[str, Any]] = None, user_uuid: str = "") -> str:
    try:
        custom_base = (userbot_db.get_managed_sub_base_url() or "").strip().rstrip("/")
    except Exception:
        custom_base = ""
    if custom_base:
        return custom_base

    env_base = (os.getenv("SUB_SERVICE_BASE_URL", "") or "").strip().rstrip("/")
    if env_base:
        return env_base

    explicit_host = (os.getenv("SUB_SERVER_PUBLIC_HOST", "") or "").strip()
    if explicit_host:
        scheme = (os.getenv("SUB_SERVER_PUBLIC_SCHEME", "https") or "https").strip().lower()
        try:
            port = int(os.getenv("SUB_SERVER_PUBLIC_PORT", "443") or "443")
        except (TypeError, ValueError):
            port = 443
        explicit_base = _public_origin_from_host(explicit_host, default_scheme=scheme, default_port=port)
        if explicit_base:
            return explicit_base

    if server:
        user_base = _build_user_base_url(server, str(user_uuid or "").strip())
        origin = _public_origin_from_url(user_base or "")
        if origin:
            return origin
        origin = _public_origin_from_url(server.get("panel_url") or "")
        if origin:
            return origin

    return ""


def _build_admin_managed_sub_links(
    server: Dict[str, Any],
    panel_user_uuid: str,
) -> tuple[str, str, Optional[Dict[str, Any]]]:
    user_uuid = str(panel_user_uuid or "").strip()
    if not user_uuid:
        return "", "", None

    owner = userbot_db.get_service_owner_by_panel_uuid(user_uuid)
    base = _resolve_managed_sub_base_url_for_admin(server, user_uuid)
    if not base:
        return "", "", owner

    try:
        server_id = int((server or {}).get("id") or 0)
    except (TypeError, ValueError):
        server_id = 0

    if server_id > 0:
        # Admin-created panel users may not have a local UserBot service/token yet.
        # Include the source server id so the managed subscription endpoint can
        # resolve the panel user directly instead of returning "subscription token not found".
        panel_token = f"panel-srv-{server_id}"
        return (
            f"{base}/sub/{panel_token}/{user_uuid}/all.txt",
            f"{base}/sub/{panel_token}/{user_uuid}/all.txt?base64=1",
            owner,
        )

    return (
        f"{base}/sub/{user_uuid}/all.txt",
        f"{base}/sub/{user_uuid}/all.txt?base64=1",
        owner,
    )


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


def _panel_user_uuid(user: Dict[str, Any]) -> str:
    return str((user or {}).get("uuid") or (user or {}).get("id") or "").strip()


def _node_sync_targets(server_id: int) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    source = database.get_server_by_id(server_id)
    if not source:
        return None, [], ["سرور اصلی پیدا نشد."]

    targets: List[Dict[str, Any]] = []
    warnings: List[str] = []
    seen: set[int] = {int(server_id)}
    for node in (source.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        try:
            target_sid = int(node.get("target_server_id") or 0)
        except (TypeError, ValueError):
            target_sid = 0
        title = str(node.get("title") or node.get("host") or f"نود #{node.get('id') or '?'}").strip()
        if target_sid <= 0:
            warnings.append(f"{title}: سرور مقصد وصل نشده است.")
            continue
        if target_sid in seen:
            continue
        target = database.get_server_by_id(target_sid)
        if not target:
            warnings.append(f"{title}: سرور مقصد #{target_sid} پیدا نشد.")
            continue
        targets.append(target)
        seen.add(target_sid)

    return source, targets, warnings


async def _auto_propagate_user_to_nodes(
    server_id: int,
    *,
    user_uuid: str,
    user_name: str = "",
    usage_limit_GB: float = 0.0,
    package_days: int = 0,
    start_date: str = "",
    comment: str = "",
) -> str:
    """
    بعد از ساخت کاربر روی سرور اصلی، اون رو روی تمام نودهای متصل هم بسازه.
    همون UUID رو استفاده می‌کنه تا لینک هوشمند روی همه سرورها کار کنه.
    """
    source, targets, warnings = _node_sync_targets(server_id)
    if not targets:
        return ""

    if not start_date:
        start_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
    now_dt = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

    payload = {
        "uuid": user_uuid,
        "name": user_name or f"user-{user_uuid[:8]}",
        "usage_limit_GB": float(usage_limit_GB),
        "current_usage_GB": 0,
    }
    if package_days > 0:
        payload["package_days"] = int(package_days)
    payload["start_date"] = start_date
    payload["last_reset_time"] = now_dt
    payload["is_active"] = True
    if comment:
        payload["comment"] = comment

    success_nodes: List[str] = []
    updated_nodes: List[str] = []
    failed_nodes: List[str] = []

    # ثبت mapping سرور اصلی (source) — بدون این، enforcer مصرف سرور اصلی رو نمیبینه
    source_id = int(source.get("id") or 0) if source else 0
    if source_id > 0:
        try:
            existing_owner = _node_sync_service_owner(user_uuid)
            if existing_owner:
                userbot_db.add_service_node(
                    service_id=int(existing_owner.get("service_id") or 0),
                    server_id=source_id,
                    server_title=str(source.get("title") or f"سرور #{source_id}"),
                    panel_user_uuid=user_uuid,
                    is_active=1,
                )
            else:
                existing_svc = userbot_db.get_service_by_panel_uuid(user_uuid)
                if existing_svc:
                    svc_id = int(existing_svc["id"])
                else:
                    svc_id = userbot_db.create_admin_service(
                        panel_user_uuid=user_uuid,
                        name=user_name or f"admin-{user_uuid[:8]}",
                        server_id=source_id,
                        server_title=str(source.get("title") or f"سرور #{source_id}"),
                        usage_limit=int(usage_limit_GB),
                        days=package_days,
                    )
                if svc_id:
                    userbot_db.add_service_node(
                        service_id=svc_id,
                        server_id=source_id,
                        server_title=str(source.get("title") or f"سرور #{source_id}"),
                        panel_user_uuid=user_uuid,
                        is_active=1,
                    )
        except Exception as src_err:
            logger.warning("Failed to record source server mapping for %s: %s", user_uuid, src_err)

    for target in targets:
        node_title = str(target.get("title") or f"سرور #{target.get('id')}")
        try:
            created, action = await _create_or_patch_user_on_node(
                target, user_uuid, payload
            )
            created_uuid = str(created.get("uuid") or created.get("id") or user_uuid).strip()
            if action == "updated":
                updated_nodes.append(node_title)
            else:
                success_nodes.append(node_title)

            # ثبت mapping در دیتابیس userbot_service_nodes
            owner = _node_sync_service_owner(user_uuid)
            if owner:
                try:
                    target_sid = int(target.get("id") or 0)
                    userbot_db.add_service_node(
                        service_id=int(owner.get("service_id") or 0),
                        server_id=target_sid,
                        server_title=node_title,
                        panel_user_uuid=created_uuid,
                        panel_user_id=(str(created.get("id")).strip() if created.get("id") else None),
                        is_active=1,
                    )
                except Exception as map_err:
                    logger.warning("Failed to record node mapping for %s: %s", node_title, map_err)
            else:
                try:
                    target_sid = int(target.get("id") or 0)
                    existing_svc = userbot_db.get_service_by_panel_uuid(user_uuid)
                    if existing_svc:
                        svc_id = int(existing_svc["id"])
                    else:
                        svc_id = userbot_db.create_admin_service(
                            panel_user_uuid=user_uuid,
                            name=user_name or f"admin-{user_uuid[:8]}",
                            server_id=target_sid,
                            server_title=str(target.get("title") or f"سرور #{target_sid}"),
                            usage_limit=int(usage_limit_GB),
                            days=package_days,
                        )
                    if svc_id:
                        userbot_db.add_service_node(
                            service_id=svc_id,
                            server_id=target_sid,
                            server_title=node_title,
                            panel_user_uuid=created_uuid,
                            panel_user_id=(str(created.get("id")).strip() if created.get("id") else None),
                            is_active=1,
                        )
                except Exception as map_err:
                    logger.warning("Failed to create admin service record for %s: %s", node_title, map_err)
        except Exception as e:
            err_msg = str(e).strip().splitlines()[0] if str(e).strip() else "خطای نامشخص"
            failed_nodes.append(f"{node_title}: {err_msg}")

    parts: List[str] = []
    if success_nodes:
        parts.append(f"✅ ساخته شد روی نودها: {', '.join(success_nodes)}")
    if updated_nodes:
        parts.append(f"🔁 به‌روز شد (موجود بود) روی نودها: {', '.join(updated_nodes)}")
    if failed_nodes:
        parts.append("❌ خطا روی نودها:\n" + "\n".join(f"  • {f}" for f in failed_nodes))
    return "\n".join(parts) if parts else ""


async def _create_or_patch_user_on_node(
    target: Dict[str, Any],
    user_uuid: str,
    payload: Dict[str, Any],
) -> tuple[Dict[str, Any], str]:
    """
    روی نود مقصد، کاربر را ایجاد می‌کند یا اگر از قبل وجود داشت به‌روز می‌کند.

    این تابع propagation را idempotent می‌کند: اگر کاربر قبلاً روی نود ساخته شده
    باشد (مثلاً پس از re-sync یا برای کاربران قدیمی)، به جای خطای 400 یک PATCH
    انجام می‌شود. payload ورودی فقط فیلدهای هدف را دارد و شامل current_usage_GB
    نیست (تا مصرف شمرده‌شده روی نود صفر نشود).

    برمی‌گرداند: (پاسخ پنل, "created" | "updated")
    """
    patch_payload = {k: v for k, v in payload.items() if k != "current_usage_GB"}

    # ۱) ابتدا بررسی می‌کنیم آیا کاربر از قبل روی نود وجود دارد.
    try:
        existing = await hiddify_api.get_user_by_uuid(target, user_uuid)
        if isinstance(existing, dict) and existing:
            updated = await hiddify_api.patch_user(target, user_uuid, patch_payload)
            return (updated or existing), "updated"
    except Exception as e:
        err_lower = str(e).lower()
        if "404" not in err_lower and "not found" not in err_lower and "not exist" not in err_lower:
            logger.debug(
                "get_user_by_uuid probe failed on node %s for uuid=%s: %s",
                target.get("title") or target.get("id"), user_uuid, e,
            )

    # ۲) کاربر وجود نداشت؛ ایجاد می‌کنیم.
    try:
        created = await hiddify_api.create_user(target, payload)
        return created, "created"
    except Exception as e:
        err_lower = str(e).lower()
        # ۳) رقابتی: بین probe و create، کاربر ساخته شد. fallback به patch.
        if "already exists" in err_lower or "uuid" in err_lower and "exist" in err_lower:
            updated = await hiddify_api.patch_user(target, user_uuid, patch_payload)
            return updated, "updated"
        raise


def build_node_sync_menu_keyboard(server_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 فقط بررسی و گزارش", callback_data=f"server:{server_id}:sync_nodes_report")],
            [InlineKeyboardButton("🧩 ساخت کاربران جاافتاده", callback_data=f"server:{server_id}:sync_nodes_missing")],
            [InlineKeyboardButton("🔁 همسان‌سازی مشخصات موجودها", callback_data=f"server:{server_id}:sync_nodes_details")],
            [InlineKeyboardButton("✅ اجرای کامل امن", callback_data=f"server:{server_id}:sync_nodes_full")],
            [InlineKeyboardButton("🔄 ثبت سرویس کاربران قدیمی ادمین", callback_data=f"server:{server_id}:sync_nodes_migrate_users")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"server:{server_id}")],
        ]
    )


def _build_node_sync_payload(source_user: Dict[str, Any], *, for_create: bool) -> Dict[str, Any]:
    """
    Build a safe copy payload from main server to node.
    Important: current_usage_GB is never copied to a new node, otherwise total
    multi-node usage would be counted twice.
    """
    payload: Dict[str, Any] = {}
    source_uuid = _panel_user_uuid(source_user)
    if for_create and source_uuid:
        payload["uuid"] = source_uuid

    name = str(source_user.get("name") or source_user.get("username") or source_uuid or "user").strip()
    payload["name"] = name

    usage_limit = _to_float(source_user.get("usage_limit_GB"))
    if usage_limit is not None:
        payload["usage_limit_GB"] = float(usage_limit)

    try:
        package_days = int(source_user.get("package_days") or 0)
    except (TypeError, ValueError):
        package_days = 0
    if package_days > 0:
        payload["package_days"] = int(package_days)

    start_date = str(source_user.get("start_date") or "").strip()
    payload["start_date"] = start_date or datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

    last_reset = str(source_user.get("last_reset_time") or "").strip()
    if last_reset:
        payload["last_reset_time"] = last_reset

    comment = str(source_user.get("comment") or "").strip()
    if comment:
        payload["comment"] = comment

    payload["is_active"] = _panel_user_is_active(source_user)
    if for_create:
        payload["current_usage_GB"] = 0
    return payload


def _node_sync_service_owner(source_uuid: str) -> Optional[Dict[str, Any]]:
    try:
        return userbot_db.get_service_owner_by_panel_uuid(source_uuid)
    except Exception as e:
        logger.warning("Failed resolving service owner for node sync uuid=%s: %s", source_uuid, e)
        return None


def _record_node_sync_mapping(
    *,
    source_uuid: str,
    target: Dict[str, Any],
    target_uuid: str,
    target_user_id: Any = None,
    is_active: bool = True,
) -> bool:
    owner = _node_sync_service_owner(source_uuid)
    if not owner:
        return False
    try:
        service_id = int(owner.get("service_id") or 0)
        target_sid = int(target.get("id") or 0)
    except (TypeError, ValueError):
        return False
    if service_id <= 0 or target_sid <= 0 or not target_uuid:
        return False
    userbot_db.add_service_node(
        service_id=service_id,
        server_id=target_sid,
        server_title=str(target.get("title") or f"سرور #{target_sid}"),
        panel_user_uuid=target_uuid,
        panel_user_id=(str(target_user_id).strip() if target_user_id is not None else None),
        is_active=1 if is_active else 0,
    )
    return True


async def _run_node_sync(
    server_id: int,
    *,
    create_missing: bool = False,
    patch_existing: bool = False,
) -> Dict[str, Any]:
    source, targets, warnings = _node_sync_targets(server_id)
    result: Dict[str, Any] = {
        "source_title": str((source or {}).get("title") or f"سرور #{server_id}"),
        "source_count": 0,
        "targets": [],
        "warnings": list(warnings),
        "created": 0,
        "patched": 0,
        "missing": 0,
        "existing": 0,
        "extra": 0,
        "mapped": 0,
        "skipped": 0,
        "errors": [],
        "mode": "report",
    }
    if create_missing and patch_existing:
        result["mode"] = "full"
    elif create_missing:
        result["mode"] = "missing"
    elif patch_existing:
        result["mode"] = "details"

    if not source:
        return result
    if not targets:
        result["warnings"].append("برای این سرور هیچ نود متصل‌شده‌ای پیدا نشد.")
        return result

    try:
        source_users_raw = await hiddify_api.list_users(source)
    except Exception as e:
        result["errors"].append(f"{result['source_title']}: {_short_error(e)}")
        return result

    source_by_uuid: Dict[str, Dict[str, Any]] = {}
    for user in source_users_raw or []:
        if not isinstance(user, dict):
            continue
        uuid = _panel_user_uuid(user)
        if not uuid:
            result["skipped"] += 1
            continue
        source_by_uuid[uuid] = user
    result["source_count"] = len(source_by_uuid)

    for target in targets:
        target_sid = int(target.get("id") or 0)
        target_title = str(target.get("title") or f"سرور #{target_sid or '?'}")
        target_summary = {
            "title": target_title,
            "total": 0,
            "missing": 0,
            "existing": 0,
            "extra": 0,
            "created": 0,
            "patched": 0,
            "mapped": 0,
            "errors": [],
        }
        try:
            target_users_raw = await hiddify_api.list_users(target)
        except Exception as e:
            err = f"{target_title}: {_short_error(e)}"
            target_summary["errors"].append(err)
            result["errors"].append(err)
            result["targets"].append(target_summary)
            continue

        target_by_uuid: Dict[str, Dict[str, Any]] = {}
        for user in target_users_raw or []:
            if not isinstance(user, dict):
                continue
            uuid = _panel_user_uuid(user)
            if uuid:
                target_by_uuid[uuid] = user
        target_summary["total"] = len(target_by_uuid)

        missing_uuids = [uuid for uuid in source_by_uuid.keys() if uuid not in target_by_uuid]
        existing_uuids = [uuid for uuid in source_by_uuid.keys() if uuid in target_by_uuid]
        extra_uuids = [uuid for uuid in target_by_uuid.keys() if uuid not in source_by_uuid]
        target_summary["missing"] = len(missing_uuids)
        target_summary["existing"] = len(existing_uuids)
        target_summary["extra"] = len(extra_uuids)
        result["missing"] += len(missing_uuids)
        result["existing"] += len(existing_uuids)
        result["extra"] += len(extra_uuids)

        if create_missing:
            for uuid in missing_uuids:
                source_user = source_by_uuid[uuid]
                payload = _build_node_sync_payload(source_user, for_create=True)
                try:
                    created = await hiddify_api.create_user(target, payload)
                    created_uuid = str(created.get("uuid") or created.get("id") or uuid).strip()
                    target_summary["created"] += 1
                    result["created"] += 1
                    mapped = _record_node_sync_mapping(
                        source_uuid=uuid,
                        target=target,
                        target_uuid=created_uuid,
                        target_user_id=created.get("id"),
                        is_active=_panel_user_is_active(source_user),
                    )
                    if mapped:
                        target_summary["mapped"] += 1
                        result["mapped"] += 1
                except Exception as e:
                    err = f"{target_title} / {source_user.get('name') or uuid}: {_short_error(e)}"
                    target_summary["errors"].append(err)
                    result["errors"].append(err)

        if patch_existing:
            for uuid in existing_uuids:
                source_user = source_by_uuid[uuid]
                target_user = target_by_uuid[uuid]
                payload = _build_node_sync_payload(source_user, for_create=False)
                try:
                    patched = await hiddify_api.patch_user(target, uuid, payload)
                    if _panel_user_is_active(source_user):
                        await hiddify_api.enable_user(target, uuid)
                    else:
                        await hiddify_api.disable_user(target, uuid)
                    target_summary["patched"] += 1
                    result["patched"] += 1
                    mapped = _record_node_sync_mapping(
                        source_uuid=uuid,
                        target=target,
                        target_uuid=str(patched.get("uuid") or uuid).strip(),
                        target_user_id=patched.get("id") or target_user.get("id"),
                        is_active=_panel_user_is_active(source_user),
                    )
                    if mapped:
                        target_summary["mapped"] += 1
                        result["mapped"] += 1
                except Exception as e:
                    err = f"{target_title} / {source_user.get('name') or uuid}: {_short_error(e)}"
                    target_summary["errors"].append(err)
                    result["errors"].append(err)

        result["targets"].append(target_summary)

    return result


def _admin_user_has_mapping(panel_user_uuid: str) -> bool:
    conn = userbot_db._get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM userbot_service_nodes WHERE panel_user_uuid = ? LIMIT 1", (panel_user_uuid,))
        if cur.fetchone():
            return True
        cur.execute("SELECT 1 FROM userbot_services WHERE comment LIKE ? LIMIT 1", (f"%uuid:{panel_user_uuid}%",))
        return cur.fetchone() is not None
    finally:
        conn.close()


async def _run_admin_user_migration(server_id: int) -> Dict[str, Any]:
    source, targets, warnings = _node_sync_targets(server_id)
    result: Dict[str, Any] = {"total": 0, "created": 0, "skipped": 0, "errors_count": 0, "warnings": warnings}
    if not source:
        return result

    all_servers = [source] + targets
    seen_uuids: set[str] = set()

    # مرحله ۱: دریافت همزمان کاربران از همه سرورها
    async def _fetch_users(srv):
        sid = int(srv.get("id") or 0)
        if sid <= 0:
            return sid, [], None
        try:
            users = await hiddify_api.list_users(srv)
            return sid, users or [], None
        except Exception as e:
            return sid, [], e

    fetch_results = await asyncio.gather(*[_fetch_users(s) for s in all_servers])

    # مرحله ۲: پردازش کاربران
    for sid, users, fetch_error in fetch_results:
        srv_title = next((str(s.get("title") or f"#{s.get('id')}") for s in all_servers if int(s.get("id") or 0) == sid), f"#{sid}")
        if fetch_error:
            result["errors_count"] += 1
            result.setdefault("errors", []).append(f"{srv_title}: {_short_error(fetch_error)}")
            continue

        for user in users:
            uuid = str((user.get("uuid") or user.get("id") or "")).strip()
            if not uuid or uuid in seen_uuids:
                continue
            seen_uuids.add(uuid)
            result["total"] += 1

            if _admin_user_has_mapping(uuid):
                result["skipped"] += 1
                continue

            name = str(user.get("name") or f"user-{uuid[:8]}")
            usage_limit = float(user.get("usage_limit_GB") or 0)
            current_usage = float(user.get("current_usage_GB") or 0)
            package_days = int(user.get("package_days") or 0)
            last_online = str(user.get("last_online") or "")
            panel_user_id = str(user.get("id") or "").strip()
            comment = f"uuid:{uuid}|admin:1|migrated:1"

            conn = userbot_db._get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO userbot_services (user_id, name, server_id, usage_current, usage_limit, days_left, last_online, comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (0, name, sid, current_usage, usage_limit, package_days, last_online, comment),
                )
                service_id = int(cur.lastrowid)
                for tgt in all_servers:
                    tgt_sid = int(tgt.get("id") or 0)
                    if tgt_sid <= 0:
                        continue
                    tgt_title = str(tgt.get("title") or f"#{tgt_sid}")
                    cur.execute(
                        "INSERT OR IGNORE INTO userbot_service_nodes (service_id, server_id, server_title, panel_user_uuid, panel_user_id, is_active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                        (service_id, tgt_sid, tgt_title, uuid, panel_user_id or None, last_online or None),
                    )
                conn.commit()
                result["created"] += 1
            except Exception as e:
                conn.rollback()
                result["errors_count"] += 1
                result.setdefault("errors", []).append(f"{uuid[:8]}...: {_short_error(e)}")
            finally:
                conn.close()

    return result


def _format_node_sync_report(summary: Dict[str, Any]) -> str:
    mode_titles = {
        "report": "📊 گزارش همگام‌سازی نودها",
        "missing": "🧩 نتیجه ساخت کاربران جاافتاده",
        "details": "🔁 نتیجه همسان‌سازی مشخصات",
        "full": "✅ نتیجه اجرای کامل امن",
    }
    mode = str(summary.get("mode") or "report")
    lines = [
        mode_titles.get(mode, "🔄 همگام‌سازی نودها"),
        "❖⬩──────────────⬩❖",
        f"🖥 سرور اصلی: {summary.get('source_title')}",
        f"👤 کاربران سرور اصلی: {int(summary.get('source_count') or 0)}",
        "",
        "نکته: مصرف فعلی روی نود جدید از صفر شروع می‌شود تا مصرف کل دوبار حساب نشود.",
        "",
    ]
    warnings = summary.get("warnings") or []
    if warnings:
        lines.append("⚠️ هشدارها:")
        for item in warnings[:5]:
            lines.append(f"- {item}")
        lines.append("")

    targets = summary.get("targets") or []
    if not targets:
        lines.append("❌ هیچ نودی برای بررسی پیدا نشد.")
    else:
        lines.append("📡 وضعیت نودها:")
        for target in targets:
            lines.append(
                f"• {target.get('title')}: "
                f"موجود={int(target.get('existing') or 0)} | "
                f"جاافتاده={int(target.get('missing') or 0)} | "
                f"اضافی={int(target.get('extra') or 0)}"
            )
            if mode != "report":
                created = int(target.get("created") or 0)
                patched = int(target.get("patched") or 0)
                mapped = int(target.get("mapped") or 0)
                lines.append(f"  انجام‌شده: ساخت={created} | بروزرسانی={patched} | نگاشت={mapped}")
            errors = target.get("errors") or []
            if errors:
                lines.append(f"  ❌ خطا: {len(errors)} مورد")

    lines.extend(
        [
            "",
            "جمع کل:",
            f"🧩 کاربران جاافتاده: {int(summary.get('missing') or 0)}",
            f"🔁 کاربران موجود: {int(summary.get('existing') or 0)}",
            f"🧹 کاربران اضافه روی نودها: {int(summary.get('extra') or 0)}",
        ]
    )
    if mode != "report":
        lines.extend(
            [
                f"✅ ساخته‌شده: {int(summary.get('created') or 0)}",
                f"🔄 بروزرسانی‌شده: {int(summary.get('patched') or 0)}",
                f"🧷 نگاشت دیتابیس: {int(summary.get('mapped') or 0)}",
            ]
        )

    errors = summary.get("errors") or []
    if errors:
        lines.append("")
        lines.append("نمونه خطاها:")
        for err in errors[:5]:
            lines.append(f"- {err}")

    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3890] + "\n..."
    return text


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
    is_xui = str(server.get("panel_type") or "").strip().lower() in {"xui", "x-ui"}
    if is_xui:
        version_text = "X-UI"
        # برای X-UI آدرس پنل خودش لینک است (شامل base path)
        admin_panel_url = panel_url if panel_url.startswith(("http://", "https://")) else ""
    else:
        version_text = SERVER_DISPLAY_VERSION or f"V{int(server.get('version') or 11)}"
        admin_panel_url = ""
        if panel_url.startswith(("http://", "https://")):
            panel_base = panel_url.rstrip("/")
            if admin_proxy and admin_uuid:
                admin_panel_url = f"{panel_base}/{admin_proxy}/{admin_uuid}/"
            elif admin_proxy:
                admin_panel_url = f"{panel_base}/{admin_proxy}/"
            else:
                admin_panel_url = panel_base

    safe_title = escape(str(title))

    if admin_panel_url:
        title_line = f'<a href="{escape(admin_panel_url, quote=True)}">🖥 سرور: {safe_title}</a>'
    else:
        title_line = f"🖥 سرور: {safe_title}"

    if is_xui:
        # برای X-UI اطلاعات اضافی
        xui_info = ""
        try:
            xui_user = (server.get("xui_username") or "").strip()
            if xui_user:
                xui_info += f"\n👤 یوزر پنل: {escape(xui_user)}"
            inbound_info = str(server.get("xui_inbound_id") or "").strip()
            if inbound_info == "0":
                xui_info += f"\n🧩 اینباند: همه (0)"
            elif inbound_info:
                xui_info += f"\n🧩 اینباند: {escape(inbound_info)}"
        except Exception:
            pass
        return (
            f"{title_line}\n"
            "❖ • -------------------------- • ❖\n"
            f"👤 تعداد کاربران: {users_count} از {users_limit}\n"
            f"📋 تعداد پلن ها: {plans_count}\n"
            f"🟩 اولویت: {priority}\n"
            f"📦 پنل: {escape(version_text)}{xui_info}"
        )

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
                "افزودن چندین کاربر➕",
                callback_data=f"userops:{server_id}:add_multi",
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
    # برای X-UI دکمه ساخت اینباند از لینک را اضافه کن
    try:
        srv = database.get_server_by_id(server_id)
        is_xui = str((srv or {}).get("panel_type") or "").strip().lower() in {"xui", "x-ui"}
    except Exception:
        is_xui = False
    keyboard = [
        [InlineKeyboardButton("👤لیست کاربران", callback_data=f"server:{server_id}:users")],
        [InlineKeyboardButton("🛡️عملیات کاربری", callback_data=f"server:{server_id}:user_ops")],
        [InlineKeyboardButton("📋پلن ها", callback_data=f"server:{server_id}:plans")],
        [InlineKeyboardButton("🔗لیست دامنه‌ها", callback_data=f"server:{server_id}:domains")],
        [InlineKeyboardButton("✏️ویرایش سرور", callback_data=f"server:{server_id}:edit")],
        [InlineKeyboardButton("🗑️حذف سرور", callback_data=f"serverdel:{server_id}")],
        [InlineKeyboardButton("⚙️لیست نودها", callback_data=f"server:{server_id}:nodes")],
        [InlineKeyboardButton("🔄همگام سازی نودها", callback_data=f"server:{server_id}:sync_nodes")],
    ]
    if is_xui:
        keyboard.insert(4, [InlineKeyboardButton("➕ ساخت اینباند از لینک", callback_data=f"server:{server_id}:create_inbound_from_link")])
        keyboard.insert(5, [InlineKeyboardButton("🔄 همگام‌سازی یوزرها روی اینباندها", callback_data=f"server:{server_id}:sync_inbounds")])
    keyboard.append([InlineKeyboardButton("↩️بازگشت", callback_data="servers:list_back")])
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

    def _plain_domain(raw: Any) -> str:
        value = str(raw or "").strip().rstrip("/")
        value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
        return escape(value or "—")

    lines = [
        "🔗 مدیریت دامنه‌ها",
        "❖ • -------------------------- • ❖",
        "دامنه‌هایی که اینجا ثبت می‌کنی برای لینک‌های اشتراک و کانفیگ‌ها استفاده می‌شوند.",
        "",
    ]

    if not domains:
        lines.append("در حال حاضر هیچ دامنه‌ای ثبت نشده است.")
    else:
        lines.append("📌 دامنه‌های ثبت‌شده:")
        for idx, d in enumerate(domains, start=1):
            title = escape(str(d.get("title") or f"دامنه {idx}"))
            domain = _plain_domain(d.get("domain"))
            lines.append(f"{idx}) {title}")
            lines.append(f"   🌐 {domain}")

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

    def _plain_domain(raw: Any) -> str:
        value = str(raw or "").strip().rstrip("/")
        value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
        return escape(value or "—")

    lines = [
        "🗑 حذف دامنه",
        "❖ • -------------------------- • ❖",
        "یکی از دامنه‌های زیر را برای حذف انتخاب کنید:",
        "",
    ]

    rows: List[List[InlineKeyboardButton]] = []
    for d in domains:
        title = escape(str(d.get("title") or f"دامنه {d.get('id')}"))
        lines.append(f"• {title} — {_plain_domain(d.get('domain'))}")
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
#   لیست کاربران
# ===============================

async def send_user_list(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
    page: int = 1,
    back_callback: Optional[str] = None,
) -> None:
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    if back_callback:
        context.user_data[f"user_list_back:{server_id}"] = back_callback
    else:
        back_callback = context.user_data.get(f"user_list_back:{server_id}") or f"server:{server_id}"

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
    fetched_at = datetime.now().timestamp()
    user_snapshot: Dict[str, Dict[str, Any]] = {}

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
        user_snapshot[str(user_uuid)] = {
            "status": status,
            "last_online": u.get("last_online"),
            "fetched_at": fetched_at,
        }

    context.user_data[f"user_list_snapshot:{server_id}"] = user_snapshot

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
            [[InlineKeyboardButton("بازگشت", callback_data=back_callback)]]
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

    # چیدمان 3 ستونه (راست‌به‌چپ) و نمایش ردیف‌ها از بالا به پایین
    keyboard_rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(page_buttons), 3):
        row = page_buttons[i:i + 3]
        keyboard_rows.append(list(reversed(row)))

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
        [InlineKeyboardButton("بازگشت", callback_data=back_callback)]
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

    last_online_line = _last_online_line(user_data)

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

    last_online_line = _last_online_line(user_data)

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
    context.user_data["userdel_source"] = "expired"
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
    context.user_data["userdel_source"] = "users"
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

    snapshot_cache = context.user_data.get(f"user_list_snapshot:{server_id}") or {}
    snapshot = snapshot_cache.get(str(user_uuid)) or snapshot_cache.get(
        str(user_data.get("uuid") or user_data.get("id") or "")
    )
    user_data = _merge_user_list_snapshot(user_data, snapshot)

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
    panel_user_link = _panel_user_link_from_base(user_name_link)
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
                    "📄 کانفیگ‌های مستقیم",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:direct",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔄 اشتراک خودکار",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:auto_sub",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔗 لینک اشتراک اصلی",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:sub",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    "🧬 لینک اشتراک Base64",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:sub_b64",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    "🌐 اشتراک هوشمند",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:multi",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    "🌐 اشتراک هوشمند Base64",
                    callback_data=f"server:{server_id}:usercfg:{user_uuid}:multi_b64",
                    style="success",
                )
            ],
            [
                (
                    InlineKeyboardButton(
                        "🚪 ورود به پنل کاربر",
                        url=panel_user_link,
                        style="success",
                    )
                    if panel_user_link
                    else InlineKeyboardButton(
                        "🚪 ورود به پنل کاربر",
                        callback_data=f"server:{server_id}:usercfg:{user_uuid}:bot_link",
                        style="success",
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 برگشت به جزئیات کاربر",
                    callback_data=f"server:{server_id}:useruuid:{user_uuid}",
                    style="primary",
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
#   تمدید اشتراک (پلن پویا - دلخواه)
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

    # تمدید اشتراک همیشه با پلن پویا (دلخواه) باز می‌شود.
    await _send_dynamic_extend(server_id, user_uuid, chat_id, context, message=message)


# ===============================
#   تمدید اشتراک با پلن پویا (مثل ربات نمایندگی)
# ===============================

def _extend_dyn_keyboard(server_id: int, user_uuid: str, gb: int, months: int):
    rows = [
        [InlineKeyboardButton("\U0001f4ca حجم تمدید", callback_data="noop")],
        [
            InlineKeyboardButton("➖10", callback_data=f"extdyn:{server_id}:{user_uuid}:gb_dec10"),
            InlineKeyboardButton("➖", callback_data=f"extdyn:{server_id}:{user_uuid}:gb_dec"),
            InlineKeyboardButton("➕", callback_data=f"extdyn:{server_id}:{user_uuid}:gb_inc"),
            InlineKeyboardButton("➕10", callback_data=f"extdyn:{server_id}:{user_uuid}:gb_inc10"),
        ],
        [InlineKeyboardButton(f"{gb} گیگابایت", callback_data="noop")],
        [InlineKeyboardButton("📦 حجم: ریست | ⏳ زمان: ریست", callback_data=f"extdyn:{server_id}:{user_uuid}:reset")],
        [InlineKeyboardButton("\u23f3 مدت تمدید", callback_data="noop")],
        [
            InlineKeyboardButton("➖", callback_data=f"extdyn:{server_id}:{user_uuid}:month_dec"),
            InlineKeyboardButton(f"{months} ماه", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"extdyn:{server_id}:{user_uuid}:month_inc"),
        ],
        [InlineKeyboardButton("✅ اعمال و تمدید", callback_data=f"extdyn:{server_id}:{user_uuid}:confirm")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"server:{server_id}:useruuid:{user_uuid}")],
    ]
    return InlineKeyboardMarkup(rows)


def _extend_dyn_defaults(server_id: int) -> tuple:
    try:
        dyn = database.get_plan_dynamic_settings(server_id) or {}
    except Exception:
        dyn = {}
    min_gb = _to_int(dyn.get("min_gb"), 10)
    min_months = _to_int(dyn.get("min_months"), 1)
    if min_gb <= 0:
        min_gb = 10
    if min_months <= 0:
        min_months = 1
    return min_gb, min_months


async def _send_dynamic_extend(server_id: int, user_uuid: str, chat_id: int, context, message=None):
    """نمایش ویزارد پویای تمدید اشتراک."""
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return
    gb, months = _extend_dyn_defaults(server_id)
    state_key = f"extdyn:{server_id}:{user_uuid}"
    context.user_data[state_key] = {"gb": gb, "months": months}
    kb = _extend_dyn_keyboard(server_id, user_uuid, gb, months)
    text = (
        "🎛 <b>تمدید اشتراک با پلن پویا</b>\n\n"
        "حجم و مدت دلخواه تمدید را انتخاب کنید، سپس «✅ اعمال و تمدید» را بزنید."
    )
    if message is not None:
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await context.bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")


def _to_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _apply_dynamic_extend(server_id: int, user_uuid: str, gb: int, months: int, chat_id: int, context):
    """اعمال تمدید پویا روی کاربر پنل.

    حجم و زمان قبلی به‌طور کامل صفر (ریست) می‌شود و مقدارهای جدید جایگزین می‌شوند،
    سپس اشتراک فعال (تیک فعال شدن) می‌شود.
    """
    server = database.get_server_by_id(server_id)
    if not server:
        await context.bot.send_message(chat_id, "❌ سرور پیدا نشد.")
        return

    new_days = months * 30
    now_dt = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

    target_uuid = await _resolve_panel_user_uuid(server, server_id, user_uuid)

    # حجم و زمان قبلی صفر شده و مقادیر جدید جایگزین می‌شود (ریست کامل)
    patch_data: Dict[str, Any] = {
        "usage_limit_GB": float(gb),
        "package_days": int(new_days),
        "current_usage_GB": 0,
        "last_reset_time": now_dt,
        "start_date": today,
    }

    # اعمال تمدید (ریست حجم/زمان) روی سرور اصلی و همه نودها
    try:
        _, changed, total, failed = await _patch_user_on_related_servers(
            server_id, user_uuid, patch_data
        )
    except Exception as e:
        logger.warning("Failed applying dynamic extend on cluster: %s", e)
        changed, total, failed = 0, 0, []

    if changed <= 0:
        detail = f"\n⚠️ سرورهای خطادار: {', '.join(failed[:3])}" if failed else ""
        await context.bot.send_message(chat_id, f"❌ خطا در اعمال تمدید پویا.{detail}")
        return

    # فعال‌سازی اشتراک (تیک فعال شدن) روی کل خوشه
    try:
        await _set_user_active_state_on_related_servers(server_id, user_uuid, active=True)
    except Exception as e:
        logger.warning("Failed to activate user after dynamic extend: %s", e)

    node_note = f"\n⚠️ {len(failed)} نود اعمال نشد: {', '.join(failed[:3])}" if failed else ""
    await context.bot.send_message(
        chat_id,
        f"✅ اشتراک با پلن پویا تمدید و فعال شد ({changed}/{total} سرور)!\n"
        f"حجم: {format_gb(gb)}GB (ریست کامل - حجم قبلی صفر شد)\n"
        f"زمان: {int(new_days)} روز (ریست کامل - زمان قبلی صفر شد)"
        f"{node_note}",
    )
    await send_user_detail(server_id, target_uuid, chat_id, context)


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
    results, online_count = await _collect_expired_users()

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
        [InlineKeyboardButton("🗑 حذف همه کاربران منقضی", callback_data="expired:delall:ask")]
    )
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


async def _collect_expired_users() -> tuple[List[Dict[str, Any]], int]:
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

    return results, online_count


def _dedupe_expired_targets(expired_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set[tuple[tuple[int, ...], str]] = set()

    for row in expired_rows:
        try:
            sid = int(row.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(row.get("user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue

        related_ids: List[int] = []
        for target in (_get_related_server_targets(sid) or []):
            try:
                tid = int(target.get("id") or 0)
            except (TypeError, ValueError):
                tid = 0
            if tid > 0:
                related_ids.append(tid)
        if not related_ids:
            related_ids = [sid]
        cluster = tuple(sorted(set(related_ids)))
        key = (cluster, uuid)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "server_id": sid,
                "user_uuid": uuid,
                "name": str(row.get("name") or uuid),
            }
        )
    return deduped


async def _delete_user_across_related_servers(
    server_id: int,
    user_uuid: str,
) -> tuple[List[int], List[str]]:
    server = database.get_server_by_id(server_id)
    if not server:
        return [], [f"سرور #{server_id}: یافت نشد"]

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

        try:
            userbot_db.delete_services_by_panel_user(target_id, user_uuid)
        except Exception:
            pass

    return deleted_server_ids, failed_servers


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

    # انتخاب نوع پنل (از دکمه‌های اینلاین) - اگر متن ارسال شد، دوباره درخواست کن
    if state == ADD_STATE_TYPE:
        await message.reply_text(
            "لطفاً از دکمه‌های زیر نوع پنل سرور را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("هیدیفای (Hiddify)", callback_data="servers:add:type:hiddify"),
                        InlineKeyboardButton("X-UI", callback_data="servers:add:type:xui"),
                    ],
                    [InlineKeyboardButton("🔙بازگشت", callback_data="servers:list_back")],
                ]
            ),
        )
        return

    # مرحله ۱: عنوان
    if state == ADD_STATE_TITLE:
        new_server["title"] = text
        context.user_data["new_server"] = new_server
        context.user_data["state"] = ADD_STATE_PANEL_URL
        if str(new_server.get("panel_type") or "").strip().lower() in {"xui", "x-ui"}:
            await message.reply_text(
                "🌐 لطفاً آدرس پنل را وارد کنید:\nمثال: https://site.example.com/E6xNPh2XZF5A6UO\n"
                "اگر پنل روی پورت غیر استاندارد است، پورت را هم وارد کنید (مثال: https://site.example.com:2056/E6xNPh2XZF5A6UO)",
                reply_markup=cancel_keyboard(),
            )
        else:
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
                "مثال: https://site.example.com/E6xNPh2XZF5A6UO",
                reply_markup=cancel_keyboard(),
            )
            return

        new_server["panel_url"] = panel_url
        context.user_data["new_server"] = new_server

        if new_server.get("panel_type") == "xui":
            context.user_data["state"] = ADD_STATE_XUI_USERNAME
            await message.reply_text(
                "👤 لطفاً «نام کاربری» پنل X-UI را وارد کنید:",
                reply_markup=cancel_keyboard(),
            )
            return

        context.user_data["state"] = ADD_STATE_ADMIN_PROXY
        await message.reply_text(
            "🔑 لطفاً «کد مسیر ادمین پنل» را وارد کنید (Admin Proxy Path):\n"
            "مثال: cNT69A5AAw",
            reply_markup=cancel_keyboard(),
        )
        return

    # مرحله X-UI: نام کاربری
    if state == ADD_STATE_XUI_USERNAME:
        new_server["xui_username"] = text.strip()
        context.user_data["new_server"] = new_server
        context.user_data["state"] = ADD_STATE_XUI_PASSWORD
        await message.reply_text(
            "🔑 لطفاً «رمز عبور» پنل X-UI را وارد کنید:",
            reply_markup=cancel_keyboard(),
        )
        return

    # مرحله X-UI: رمز عبور
    if state == ADD_STATE_XUI_PASSWORD:
        new_server["xui_password"] = text
        context.user_data["new_server"] = new_server
        context.user_data["state"] = ADD_STATE_XUI_SUB_DOMAIN
        await message.reply_text(
            "🌐 دامنه سابسکریپشن (Subscription Domain) را وارد کنید (اختیاری):\n"
            "اگر خالی گذاشته شود، از آدرس پنل استفاده می‌شود.\n\n"
            "برای گذشتن از این مرحله، «0» یا «skip» یا «-» را بفرستید.",
            reply_markup=cancel_keyboard(),
        )
        return

    # مرحله X-UI: دامنه ساب
    if state == ADD_STATE_XUI_SUB_DOMAIN:
        sub_domain = text.strip()
        if sub_domain not in {"skip", "-", "_", ".", "done", "نه", "خیر", "0"}:
            sub_domain = sub_domain.rstrip("/")
            if not sub_domain.startswith(("http://", "https://")):
                sub_domain = "https://" + sub_domain
            new_server["xui_sub_domain"] = sub_domain
        context.user_data["new_server"] = new_server
        context.user_data["state"] = ADD_STATE_XUI_INBOUND
        await message.reply_text(
            "🧩 شناسه اینباند فروش (Inbound ID) را وارد کنید (اختیاری):\n"
            "اگر خالی/skip باشد، اولین اینباند فعال انتخاب می‌شود.\n"
            "`0` = همه اینباندهای فعال، `1` = تک اینباند، `1,2,3` = چند اینباند\n\n"
            "مثال: `0` یا `1` یا `1,2,3`",
            reply_markup=cancel_keyboard(),
        )
        return

    # مرحله X-UI: شناسه اینباند
    if state == ADD_STATE_XUI_INBOUND:
        raw_inbound = text.strip()
        if raw_inbound not in {"skip", "-", "_", ".", "done", "نه", "خیر"}:
            # 0 = همه اینباندها، 1 = تک اینباند، 1,2,3 = چند اینباند
            normalized = raw_inbound.replace("،", ",").replace(" ", ",")
            parts = [p.strip() for p in normalized.split(",") if p.strip()]
            if not parts:
                pass  # خالی = خودکار (اولین اینباند)
            elif parts == ["0"]:
                new_server["xui_inbound_id"] = "0"
            else:
                try:
                    ids = []
                    for p in parts:
                        n = int(p)
                        if n <= 0:
                            raise ValueError
                        ids.append(str(n))
                    new_server["xui_inbound_id"] = ",".join(ids)
                except ValueError:
                    await message.reply_text(
                        "❌ شناسه اینباند باید عدد باشد.\n"
                        "مثال: `1` (تک)، `1,2,3` (چندتا)، `0` (همه)",
                        reply_markup=cancel_keyboard(),
                    )
                    return
        context.user_data["new_server"] = new_server
        context.user_data["state"] = ADD_STATE_LIMIT
        await message.reply_text(
            "📊 لطفاً محدودیت تعداد کاربران سرور را وارد کنید (عدد):",
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
            if new_server.get("panel_type") == "xui":
                await xui_api.test_connect(new_server)
            else:
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
            f"🧩 نوع پنل: {'X-UI' if saved.get('panel_type') == 'xui' else 'هیدیفای'}\n"
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
        await message.reply_text("❌ ویرایش سرور لغو شد.", reply_markup=admin_main_keyboard())
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
    elif state == EDIT_SERVER_XUI_USERNAME:
        updates["xui_username"] = text.strip()
        msg_ok = "✅ نام کاربری پنل X-UI بروزرسانی شد."
    elif state == EDIT_SERVER_XUI_PASSWORD:
        updates["xui_password"] = text.strip()
        msg_ok = "✅ رمز پنل X-UI بروزرسانی شد."
    elif state == EDIT_SERVER_XUI_SUB_DOMAIN:
        sub = text.strip()
        if sub in {"0", "skip", "-", "_", ".", "done", "نه", "خیر", ""}:
            updates["xui_sub_domain"] = ""
            msg_ok = "✅ دامنه ساب پاک شد (از آدرس پنل استفاده می‌شود)."
        else:
            sub = sub.rstrip("/")
            if not sub.startswith(("http://", "https://")):
                sub = "https://" + sub
            updates["xui_sub_domain"] = sub
            msg_ok = "✅ دامنه ساب بروزرسانی شد."
    elif state == EDIT_SERVER_XUI_INBOUND:
        raw = text.strip()
        if raw in {"skip", "-", "_", ".", "done", "نه", "خیر", ""}:
            updates["xui_inbound_id"] = ""
            msg_ok = "✅ اینباند روی حالت خودکار (اولین فعال) تنظیم شد."
        elif raw == "0":
            updates["xui_inbound_id"] = "0"
            msg_ok = "✅ اینباند روی «همه» (0) تنظیم شد."
        else:
            normalized = raw.replace("،", ",").replace(" ", ",")
            parts = [p.strip() for p in normalized.split(",") if p.strip()]
            try:
                ids = []
                for p in parts:
                    n = int(p)
                    if n <= 0:
                        raise ValueError
                    ids.append(str(n))
                updates["xui_inbound_id"] = ",".join(ids)
                msg_ok = f"✅ اینباند روی {','.join(ids)} تنظیم شد."
            except ValueError:
                await message.reply_text(
                    "❌ شناسه اینباند باید عدد باشد.\nمثال: `0` (همه) یا `1` یا `1,2,3`",
                    reply_markup=cancel_keyboard(),
                )
                return
    else:
        await message.reply_text("❌ حالت ویرایش سرور نامعتبر است.", reply_markup=admin_main_keyboard())
        context.user_data.pop("state", None)
        context.user_data.pop("edit_server_id", None)
        return

    try:
        database.update_server(server_id, updates)
    except Exception as e:
        logger.exception("update_server error: %s", e)
        await message.reply_text(f"❌ خطا در بروزرسانی سرور:\n{e}", reply_markup=admin_main_keyboard())
        return

    await message.reply_text(msg_ok, reply_markup=admin_main_keyboard())
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

    is_xui = str(server.get("panel_type") or "").strip().lower() in {"xui", "x-ui"}
    if is_xui:
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📌ویرایش عنوان", callback_data=f"seredit:{server_id}:title")],
                [InlineKeyboardButton("🌐ویرایش آدرس پنل", callback_data=f"seredit:{server_id}:panel_url")],
                [InlineKeyboardButton("👤ویرایش نام کاربری پنل", callback_data=f"seredit:{server_id}:xui_username")],
                [InlineKeyboardButton("🔑ویرایش رمز پنل", callback_data=f"seredit:{server_id}:xui_password")],
                [InlineKeyboardButton("🔗ویرایش دامنه ساب", callback_data=f"seredit:{server_id}:xui_sub_domain")],
                [InlineKeyboardButton("🧩ویرایش اینباند", callback_data=f"seredit:{server_id}:xui_inbound")],
                [InlineKeyboardButton("🗿ویرایش محدودیت کاربر", callback_data=f"seredit:{server_id}:limit")],
                [InlineKeyboardButton("🔢ویرایش اولویت ترتیب", callback_data=f"seredit:{server_id}:priority")],
                [InlineKeyboardButton("🔗ویرایش دامنه", callback_data=f"server:{server_id}:domains")],
                [InlineKeyboardButton("🗑️حذف سرور", callback_data=f"serverdel:{server_id}")],
                [InlineKeyboardButton("🔙بازگشت", callback_data=f"server:{server_id}")],
            ]
        )
    else:
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
        context.user_data.pop("add_multi_users", None)
        context.user_data.pop("add_user_plan_id", None)
        context.user_data.pop("add_user_plan_source_server_id", None)
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
            context.user_data.pop("add_user_plan_source_server_id", None)
            await message.reply_text(
                "❌ پلن انتخاب‌شده نامعتبر است. دوباره از منوی «افزودن کاربر با پلن➕» اقدام کنید.",
                reply_markup=admin_main_keyboard(),
            )
            return

        get_plan = getattr(database, "get_plan", None)
        if not callable(get_plan):
            context.user_data.pop("state", None)
            context.user_data.pop("add_user_plan_id", None)
            context.user_data.pop("add_user_plan_source_server_id", None)
            await message.reply_text(
                "❌ تابع get_plan در دیتابیس پیاده‌سازی نشده است، "
                "بخش افزودن کاربر با پلن هنوز کامل نشده.",
                reply_markup=build_user_ops_keyboard(server_id),
            )
            return

        plan_source_server_id = int(context.user_data.get("add_user_plan_source_server_id") or server_id)
        plan = get_plan(plan_source_server_id, plan_id)
        if not plan:
            context.user_data.pop("state", None)
            context.user_data.pop("add_user_plan_id", None)
            context.user_data.pop("add_user_plan_source_server_id", None)
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
            context.user_data.pop("add_user_plan_source_server_id", None)
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

    # حالت: افزودن چندین کاربر (مرحله‌ای)
    if state == ADD_MULTI_USERS_INPUT:
        count = _parse_positive_int(text)
        if count is None:
            await message.reply_text(
                "❌ لطفاً تعداد معتبر وارد کنید.\nمثال: 5",
                reply_markup=cancel_keyboard(),
            )
            return

        if count > 100:
            await message.reply_text(
                "❌ حداکثر 100 کاربر را در هر مرحله می‌توانید اضافه کنید.",
                reply_markup=cancel_keyboard(),
            )
            return

        context.user_data["add_multi_users"] = {"count": int(count)}
        context.user_data["state"] = ADD_MULTI_USERS_BASE_NAME
        await message.reply_text(
            "✅ تعداد کاربران ثبت شد.\n"
            "📝 لطفاً نام پایه را وارد کنید.\n"
            "مثال: تست",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == ADD_MULTI_USERS_BASE_NAME:
        base_name = str(text or "").strip()
        if not base_name:
            await message.reply_text(
                "❌ نام پایه نمی‌تواند خالی باشد.",
                reply_markup=cancel_keyboard(),
            )
            return

        spec = context.user_data.get("add_multi_users") or {}
        spec["base_name"] = base_name
        context.user_data["add_multi_users"] = spec
        context.user_data["state"] = ADD_MULTI_USERS_USAGE
        await message.reply_text(
            "📊 لطفاً حجم هر کاربر (GB) را وارد کنید:\n"
            "مثال: 5 یا 0.5",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == ADD_MULTI_USERS_USAGE:
        usage_gb = _parse_positive_float(text)
        if usage_gb is None:
            await message.reply_text(
                "❌ لطفاً یک عدد معتبر بزرگ‌تر از صفر برای حجم وارد کنید.\nمثال: 5 یا 0.5",
                reply_markup=cancel_keyboard(),
            )
            return

        spec = context.user_data.get("add_multi_users") or {}
        spec["usage_limit_GB"] = float(usage_gb)
        context.user_data["add_multi_users"] = spec
        context.user_data["state"] = ADD_MULTI_USERS_DAYS
        await message.reply_text(
            "📅 لطفاً مدت هر کاربر (روز) را وارد کنید:\n"
            "مثال: 30",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == ADD_MULTI_USERS_DAYS:
        days = _parse_positive_int(text)
        if days is None:
            await message.reply_text(
                "❌ لطفاً یک عدد صحیح معتبر برای روز وارد کنید.\nمثال: 30",
                reply_markup=cancel_keyboard(),
            )
            return

        spec = context.user_data.get("add_multi_users") or {}
        spec["package_days"] = int(days)
        context.user_data["add_multi_users"] = spec
        context.user_data["state"] = ADD_MULTI_USERS_CONFIRM

        count = int(spec.get("count") or 0)
        base_name = str(spec.get("base_name") or "").strip()
        usage = float(spec.get("usage_limit_GB") or 0)
        sample_names = [base_name if i == 0 else f"{base_name}{i}" for i in range(max(0, min(count, 6)))]
        names_preview = "، ".join(sample_names) if sample_names else "-"
        summary = (
            "لطفاً اطلاعات را تایید کنید:\n"
            f"👥 تعداد: {count} کاربر\n"
            f"👤 نام پایه: {base_name}\n"
            f"📊 حجم هر کاربر: {format_gb(usage)} گیگابایت\n"
            f"📅 مدت هر کاربر: {days} روز\n"
            f"🧾 نمونه نام‌ها: {names_preview}"
        )
        await message.reply_text(summary, reply_markup=confirm_add_user_keyboard())
        return

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
    if state == ADD_MULTI_USERS_CONFIRM:
        if _is_confirm_text(text):
            server = database.get_server_by_id(server_id)
            if not server:
                await message.reply_text(
                    "❌ سرور پیدا نشد.",
                    reply_markup=admin_main_keyboard(),
                )
                context.user_data.pop("state", None)
                context.user_data.pop("add_multi_users", None)
                context.user_data.pop("add_user_server_id", None)
                return

            spec = context.user_data.get("add_multi_users") or {}
            count = int(spec.get("count") or 0)
            base_name = str(spec.get("base_name") or "").strip()
            usage_gb = spec.get("usage_limit_GB")
            days = spec.get("package_days")

            if count <= 0 or not base_name or usage_gb is None or not days:
                await message.reply_text(
                    "❌ اطلاعات افزودن چندکاربره ناقص است. دوباره تلاش کنید.",
                    reply_markup=admin_main_keyboard(),
                )
                context.user_data.pop("state", None)
                context.user_data.pop("add_multi_users", None)
                context.user_data.pop("add_user_server_id", None)
                return

            success: List[Dict[str, Any]] = []
            failed: List[str] = []
            now_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            now_dt = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

            for i in range(count):
                user_name = base_name if i == 0 else f"{base_name}{i}"
                payload = {
                    "name": user_name,
                    "usage_limit_GB": float(usage_gb),
                    "package_days": int(days),
                    "start_date": now_date,
                    "current_usage_GB": 0,
                    "last_reset_time": now_dt,
                    "is_active": True,
                }
                try:
                    created = await hiddify_api.create_user(server, payload)
                    created_uuid = str(created.get("uuid") or created.get("id") or "").strip()
                    success.append(
                        {
                            "name": user_name,
                            "uuid": created_uuid,
                        }
                    )
                    # ساخت خودکار روی نودهای متصل (با همان UUID)
                    if created_uuid:
                        try:
                            await _auto_propagate_user_to_nodes(
                                int(server_id),
                                user_uuid=created_uuid,
                                user_name=user_name,
                                usage_limit_GB=float(usage_gb or 0),
                                package_days=int(days or 0),
                                start_date=now_date,
                            )
                        except Exception as node_err:
                            logger.warning("Auto node propagation failed for %s: %s", user_name, node_err)
                except Exception as e:
                    err = str(e).strip().splitlines()[0] if str(e).strip() else "خطای نامشخص"
                    failed.append(f"{user_name}: {err}")

            context.user_data.pop("state", None)
            context.user_data.pop("add_multi_users", None)
            context.user_data.pop("add_user_server_id", None)
            context.user_data.pop("add_user", None)
            context.user_data.pop("add_user_plan_id", None)
            context.user_data.pop("add_user_plan_source_server_id", None)

            lines = [
                "📦 نتیجه افزودن چندین کاربر",
                f"✅ موفق: {len(success)}",
                f"❌ ناموفق: {len(failed)}",
            ]
            if success:
                lines.append("")
                lines.append("کاربران ساخته‌شده:")
                for row in success[:12]:
                    lines.append(f"• {row['name']}")
                if len(success) > 12:
                    lines.append(f"... و {len(success) - 12} کاربر دیگر")

            if failed:
                lines.append("")
                lines.append("خطاها:")
                for row in failed[:8]:
                    lines.append(f"• {row}")
                if len(failed) > 8:
                    lines.append(f"... و {len(failed) - 8} خطای دیگر")

            await message.reply_text(
                "\n".join(lines),
                reply_markup=admin_main_keyboard(),
            )

            # ارسال جزئیات + لینک QR برای کاربران ساخته‌شده
            detail_limit = 20
            if len(success) > detail_limit:
                await context.bot.send_message(
                    chat_id=message.chat_id,
                    text=f"ℹ️ برای جلوگیری از شلوغی چت، جزئیات فقط برای {detail_limit} کاربر اول ارسال می‌شود.",
                )

            for idx, row in enumerate(success):
                if idx >= detail_limit:
                    break
                row_uuid = str(row.get("uuid") or "").strip()
                if not row_uuid:
                    continue

                await send_user_detail(server_id, row_uuid, message.chat_id, context)

                base_url = _build_user_base_url(server, row_uuid)
                if not base_url:
                    continue
                sub_url = f"{base_url}/all.txt"
                qr_image = make_qr_image(sub_url)
                await context.bot.send_photo(
                    chat_id=message.chat_id,
                    photo=qr_image,
                    caption=f"🔗 لینک اشتراک {row.get('name')}:\n{sub_url}",
                )

            return

        await message.reply_text(
            "لطفاً با دکمه‌های «✅تایید» یا «❌لغو» پاسخ دهید.",
            reply_markup=confirm_add_user_keyboard(),
        )
        return

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

            # ساخت خودکار کاربر روی نودهای متصل (با همان UUID)
            if uuid:
                try:
                    node_report = await _auto_propagate_user_to_nodes(
                        int(server_id),
                        user_uuid=uuid,
                        user_name=name,
                        usage_limit_GB=float(usage_gb or 0),
                        package_days=int(days or 0),
                        start_date=str(payload.get("start_date") or ""),
                    )
                except Exception as node_err:
                    logger.exception("Auto node propagation failed for uuid=%s: %s", uuid, node_err)
                    node_report = ""
                if node_report:
                    try:
                        await message.reply_text(node_report)
                    except Exception:
                        pass

            context.user_data.pop("state", None)
            context.user_data.pop("add_user", None)
            context.user_data.pop("add_user_server_id", None)
            context.user_data.pop("add_user_plan_id", None)
            context.user_data.pop("add_user_plan_source_server_id", None)

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

    if state == "xui_create_inbound_from_link":
        await handle_xui_create_inbound_from_link(update, context)
        return

    if state == "xui_create_inbound_from_link_port":
        await handle_xui_create_inbound_from_link_port(update, context)
        return

    if state == SEARCH_SMART_INPUT:
        await handle_smart_search_input(update, context)
        return

    logger.warning("Unknown state in handle_server_state_message: %s", state)


async def handle_xui_create_inbound_from_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        context.user_data.pop("state", None)
        context.user_data.pop("create_inbound_server_id", None)
        await message.reply_text("❌ ساخت اینباند لغو شد.", reply_markup=admin_main_keyboard())
        return
    server_id = int(context.user_data.get("create_inbound_server_id") or 0)
    server = database.get_server_by_id(server_id)
    if not server:
        context.user_data.pop("state", None)
        context.user_data.pop("create_inbound_server_id", None)
        await message.reply_text("❌ سرور پیدا نشد.", reply_markup=admin_main_keyboard())
        return
    # لینک را از متن بگیر (ممکنه چند خط باشه، اولین لینک)
    link = ""
    for line in text.splitlines():
        line=line.strip()
        if line.startswith(("vless://", "vmess://", "trojan://", "hysteria2://", "hy2://", "ss://")):
            link = line
            break
    if not link:
        # کل متن را به عنوان لینک بگیر اگر single line بود
        if text.startswith(("vless://", "vmess://", "trojan://", "hysteria2://", "hy2://", "ss://")):
            link = text.strip()
    if not link:
        await message.reply_text(
            "❌ لینک نامعتبر است. لطفاً یک لینک vless/vmess/hysteria2/trojan بفرستید.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو❌", callback_data=f"server:{server_id}")]]),
        )
        return
    try:
        from Shared import xui_api

        parsed = xui_api.parse_config_link(link)
    except Exception as e:
        await message.reply_text(
            f"❌ خطا در پارس لینک:\n{e}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو❌", callback_data=f"server:{server_id}")]]),
        )
        return
    # ذخیره لینک برای مرحله بعد و سوال پورت
    context.user_data["create_inbound_link"] = link
    context.user_data["create_inbound_parsed_port"] = str(parsed.get("port") or "")
    from AdminBot.states import XUI_CREATE_INBOUND_FROM_LINK_PORT

    context.user_data["state"] = XUI_CREATE_INBOUND_FROM_LINK_PORT
    # پیشنهاد پورت: اگر پورت لینک اشغال بود، بعدی را پیشنهاد بده
    try:
        inbounds = await xui_api._list_inbounds(server)  # type: ignore
        used = {int(ib.get("port") or 0) for ib in inbounds}
        try:
            pu = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(str(server.get("panel_url") or ""))
            pp = pu.port or (443 if pu.scheme == "https" else 80)
            if pp:
                used.add(pp)
        except Exception:
            pass
        orig_port = int(parsed.get("port") or 443)
        suggest = orig_port
        if suggest in used:
            nxt = suggest + 1
            while nxt in used and nxt < 65535:
                nxt += 1
            suggest = nxt
        port_msg = f"🔌 پورت داخل لینک: `{orig_port}`\n"
        if suggest != orig_port:
            port_msg += f"⚠️ این پورت قبلاً استفاده شده، پیشنهاد: `{suggest}`\n"
        port_msg += f"\nروی کدام پورت بسازم؟\nعدد را بفرستید (مثلاً `{suggest}`) یا `0` برای همان `{orig_port}`"
    except Exception:
        port_msg = f"روی کدام پورت بسازم؟ (پورت لینک: `{parsed.get('port')}`)\nعدد را بفرستید یا `0`"
    await message.reply_text(
        f"🔍 لینک تشخیص داده شد:\n"
        f"🔧 پروتکل: `{parsed.get('protocol')}`\n"
        f"{port_msg}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو❌", callback_data=f"server:{server_id}")]]),
    )
    return


async def handle_xui_create_inbound_from_link_port(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        context.user_data.pop("state", None)
        context.user_data.pop("create_inbound_server_id", None)
        context.user_data.pop("create_inbound_link", None)
        context.user_data.pop("create_inbound_parsed_port", None)
        await message.reply_text("❌ ساخت اینباند لغو شد.", reply_markup=admin_main_keyboard())
        return
    server_id = int(context.user_data.get("create_inbound_server_id") or 0)
    link = str(context.user_data.get("create_inbound_link") or "").strip()
    server = database.get_server_by_id(server_id)
    if not server or not link:
        context.user_data.pop("state", None)
        context.user_data.pop("create_inbound_server_id", None)
        context.user_data.pop("create_inbound_link", None)
        await message.reply_text("❌ اطلاعات ناقص است.", reply_markup=admin_main_keyboard())
        return
    # پورت را بگیر
    raw = text.strip().lower()
    port_override = None
    if raw not in {"skip", "-", "0", ""}:
        try:
            port_override = int(raw)
            if not (1 <= port_override <= 65535):
                raise ValueError
        except ValueError:
            await message.reply_text(
                "❌ پورت نامعتبر است. عدد 1 تا 65535 بفرستید یا `0` برای همان پورت لینک.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو❌", callback_data=f"server:{server_id}")]]),
            )
            return
    await message.reply_text("⏳ در حال ساخت اینباند...")
    try:
        from Shared import xui_api

        result = await xui_api.create_inbound_from_link(server, link, port_override=port_override)
        context.user_data.pop("state", None)
        context.user_data.pop("create_inbound_server_id", None)
        context.user_data.pop("create_inbound_link", None)
        context.user_data.pop("create_inbound_parsed_port", None)
        parsed = xui_api.parse_config_link(link)
        inbound_id = ""
        try:
            if isinstance(result, dict):
                inbound_id = str(result.get("id") or result.get("obj", {}).get("id") or "")
        except Exception:
            inbound_id = ""
        final_port = port_override or parsed.get("port")
        await message.reply_text(
            f"✅ اینباند با موفقیت ساخته شد.\n\n"
            f"🔧 پروتکل: {parsed.get('protocol')}\n"
            f"🔌 پورت: {final_port}\n"
            + (f"🆔 ID اینباند: {inbound_id}\n" if inbound_id else "")
            + f"🌐 دامنه پنل: {server.get('panel_url')}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به سرور", callback_data=f"server:{server_id}")]]),
        )
    except Exception as e:
        err = str(e)
        if len(err) > 700:
            err = err[:700] + "..."
        await message.reply_text(
            f"❌ خطا در ساخت اینباند:\n{err}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"server:{server_id}")]]),
        )


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
    if data.startswith("nodes:") or data.startswith("delnode:") or data.startswith("nodeinfo:") or data.startswith("nodeedit:") or data.startswith("nodeact:"):
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

        if data == "expired:delall:ask":
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ بله، حذف همه کاربران منقضی",
                            callback_data="expired:delall:yes",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ لغو",
                            callback_data="expired:delall:no",
                        )
                    ],
                ]
            )
            await msg.edit_text(
                "⚠️ آیا از حذف همزمان همه کاربران منقضی مطمئن هستید؟\n"
                "این عملیات قابل بازگشت نیست.",
                reply_markup=kb,
            )
            return

        if data == "expired:delall:no":
            await send_expired_users_list(chat_id=msg.chat_id, context=context, message=msg)
            return

        if data == "expired:delall:yes":
            await msg.edit_text("⏳ در حال حذف کاربران منقضی...")
            rows, _ = await _collect_expired_users()
            targets = _dedupe_expired_targets(rows)

            if not targets:
                await msg.edit_text("ℹ️ کاربر منقضی برای حذف پیدا نشد.")
                return

            total = len(targets)
            success_count = 0
            partial_count = 0
            failed_count = 0
            failed_samples: List[str] = []

            for target in targets:
                sid = int(target.get("server_id") or 0)
                user_uuid = str(target.get("user_uuid") or "").strip()
                if sid <= 0 or not user_uuid:
                    failed_count += 1
                    continue

                deleted_server_ids, failed_servers = await _delete_user_across_related_servers(
                    sid, user_uuid
                )

                if deleted_server_ids and not failed_servers:
                    try:
                        from Shared import agent_db as _agn
                        _agn.soft_delete_service_by_uuid(user_uuid, sid)
                    except Exception:
                        pass
                    success_count += 1
                elif deleted_server_ids and failed_servers:
                    partial_count += 1
                    if len(failed_samples) < 3:
                        failed_samples.append(f"uuid={user_uuid} ({len(failed_servers)} خطا)")
                else:
                    failed_count += 1
                    if len(failed_samples) < 3:
                        failed_samples.append(f"uuid={user_uuid} (عدم حذف)")

            if success_count > 0 and partial_count == 0 and failed_count == 0:
                await msg.edit_text(
                    "✅ کاربران منقضی با موفقیت از سرور اصلی و نودهای مرتبط حذف شدند.\n"
                    f"👤 تعداد حذف‌شده: {success_count}"
                )
                return

            text = (
                "✅ عملیات حذف گروهی کاربران منقضی انجام شد.\n"
                f"👤 کل: {total}\n"
                f"✅ موفق کامل: {success_count}\n"
                f"⚠️ موفق ناقص: {partial_count}\n"
                f"❌ ناموفق: {failed_count}"
            )
            if failed_samples:
                text += "\n\nنمونه خطا:\n- " + "\n- ".join(failed_samples)
            await msg.edit_text(text)
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
        parts = data.split(":")
        if len(parts) < 4:
            await msg.edit_text("❌ داده‌ی دکمه حذف نامعتبر است.")
            return
        try:
            server_id = int(parts[1])
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه حذف نامعتبر است.")
            return
        user_uuid = parts[2]
        choice = parts[3]
        source = parts[4] if len(parts) >= 5 else "users"
        if source not in {"users", "expired"}:
            source = "users"

        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        if choice == "no":
            await msg.edit_text("❌ حذف کاربر لغو شد.")
            return

        deleted_server_ids, failed_servers = await _delete_user_across_related_servers(
            server_id, user_uuid
        )

        if not deleted_server_ids:
            details = "\n".join(failed_servers[:3])
            if details:
                await msg.edit_text(f"❌ حذف کاربر روی هیچ سروری موفق نشد:\n{details}")
            else:
                await msg.edit_text("❌ حذف کاربر روی هیچ سروری موفق نشد.")
            return

        try:
            from Shared import agent_db as _agn
            _agn.soft_delete_service_by_uuid(user_uuid, server_id)
        except Exception:
            pass

        if failed_servers:
            await msg.edit_text(
                "✅ کاربر از سرور اصلی/نودهای قابل‌دسترسی حذف شد.\n"
                f"⚠️ برخی سرورها حذف نشدند: {len(failed_servers)}"
            )
        else:
            await msg.edit_text("✅ کاربر با موفقیت از سرور اصلی و نودهای مرتبط حذف شد.")
        if source != "expired":
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

    # ------ تمدید با پلن پویا (extdyn:...) ------
    if data.startswith("extdyn:"):
        await query.answer()
        try:
            _, sid_str, user_uuid, action = data.split(":", 3)
            server_id = int(sid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه تمدید پویا نامعتبر است.")
            return
        state_key = f"extdyn:{server_id}:{user_uuid}"
        state = context.user_data.get(state_key) or {}
        gb = _to_int(state.get("gb"), 10)
        months = _to_int(state.get("months"), 1)
        if gb <= 0:
            gb = 10
        if months <= 0:
            months = 1

        if action == "menu":
            await _send_dynamic_extend(server_id, user_uuid, chat_id, context, message=msg)
            return
        if action == "confirm":
            context.user_data.pop(state_key, None)
            await _apply_dynamic_extend(server_id, user_uuid, gb, months, chat_id, context)
            return

        dyn = database.get_plan_dynamic_settings(server_id) or {}
        step_gb = max(1, _to_int(dyn.get("step_gb"), 1))
        max_gb = max(1, _to_int(dyn.get("max_gb"), 1000))
        min_gb = max(1, _to_int(dyn.get("min_gb"), 1))
        min_months = max(1, _to_int(dyn.get("min_months"), 1))
        max_months = max(1, _to_int(dyn.get("max_months"), 12))

        if action == "reset":
            gb, months = _extend_dyn_defaults(server_id)
        elif action == "gb_inc":
            gb = min(gb + 1, max_gb)
        elif action == "gb_dec":
            gb = max(min_gb, gb - 1)
        elif action == "gb_inc10":
            gb = min(gb + 10, max_gb)
        elif action == "gb_dec10":
            gb = max(min_gb, gb - 10)
        elif action == "month_inc":
            months = min(months + 1, max_months)
        elif action == "month_dec":
            months = max(min_months, months - 1)

        context.user_data[state_key] = {"gb": gb, "months": months}
        kb = _extend_dyn_keyboard(server_id, user_uuid, gb, months)
        text = (
            "🎛 <b>تمدید اشتراک با پلن پویا</b>\n\n"
            "حجم و مدت دلخواه تمدید را انتخاب کنید، سپس «✅ اعمال و تمدید» را بزنید."
        )
        try:
            await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
        return

    # ------ انتخاب پلن برای افزودن کاربر جدید (addplan:SERVER_ID:PLAN_ID) ------
    if data.startswith("nodeaddplan:"):
        await query.answer()
        try:
            _, target_sid_str, source_sid_str, pid_str = data.split(":", 3)
            server_id = int(target_sid_str)
            plan_source_server_id = int(source_sid_str)
            plan_id = int(pid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه پلن نود نامعتبر است.")
            return

        context.user_data["add_user_server_id"] = server_id
        context.user_data["add_user_plan_source_server_id"] = plan_source_server_id
        context.user_data["add_user_plan_id"] = plan_id
        context.user_data["add_user"] = {}
        context.user_data.pop("add_multi_users", None)
        context.user_data["state"] = ADD_USER_PLAN_NAME

        plan_title = f"پلن #{plan_id}"
        get_plan = getattr(database, "get_plan", None)
        if callable(get_plan):
            try:
                selected_plan = get_plan(plan_source_server_id, plan_id) or {}
                plan_title = (
                    str(
                        selected_plan.get("title")
                        or selected_plan.get("name")
                        or plan_title
                    ).strip()
                    or plan_title
                )
            except Exception:
                pass

        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await msg.reply_text(
            f"✅ {plan_title} برای نود انتخاب شد.\n"
            "📝 لطفاً نام کاربر را وارد کنید:",
            reply_markup=cancel_keyboard(),
        )
        return

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
        context.user_data.pop("add_user_plan_source_server_id", None)
        context.user_data["add_user_plan_id"] = plan_id
        context.user_data["add_user"] = {}
        context.user_data.pop("add_multi_users", None)
        context.user_data["state"] = ADD_USER_PLAN_NAME

        plan_title = f"پلن #{plan_id}"
        get_plan = getattr(database, "get_plan", None)
        if callable(get_plan):
            try:
                selected_plan = get_plan(server_id, plan_id) or {}
                plan_title = (
                    str(
                        selected_plan.get("title")
                        or selected_plan.get("name")
                        or plan_title
                    ).strip()
                    or plan_title
                )
            except Exception:
                pass

        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await msg.reply_text(
            f"✅ {plan_title} انتخاب شد.\n"
            "📝 لطفاً نام کاربر را وارد کنید:",
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
            context.user_data.pop("add_user_plan_source_server_id", None)

            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            await msg.reply_text(
                "لطفاً نام کاربر را وارد کنید:",
                reply_markup=cancel_keyboard(),
            )
            return

        if action == "add_multi":
            context.user_data["state"] = ADD_MULTI_USERS_INPUT
            context.user_data["add_user_server_id"] = server_id
            context.user_data.pop("add_user", None)
            context.user_data.pop("add_user_plan_id", None)
            context.user_data.pop("add_user_plan_source_server_id", None)
            context.user_data.pop("add_multi_users", None)

            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            await msg.reply_text(
                "➕ افزودن چندین کاربر\n\n"
                "👥 چند کاربر می‌خواهید اضافه کنید؟\n"
                "مثال: 5",
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

        if field == "xui_username":
            set_server_state(EDIT_SERVER_XUI_USERNAME)
            await msg.edit_text(
                "👤 لطفاً نام کاربری جدید پنل X-UI را وارد کنید:",
                reply_markup=cancel_kb,
            )
            return

        if field == "xui_password":
            set_server_state(EDIT_SERVER_XUI_PASSWORD)
            await msg.edit_text(
                "🔑 لطفاً رمز جدید پنل X-UI را وارد کنید:",
                reply_markup=cancel_kb,
            )
            return

        if field == "xui_sub_domain":
            set_server_state(EDIT_SERVER_XUI_SUB_DOMAIN)
            await msg.edit_text(
                "🌐 لطفاً دامنه ساب جدید را وارد کنید:\nمثال: sub.example.com\nبرای خالی گذاشتن «0» یا «skip» بفرستید.",
                reply_markup=cancel_kb,
            )
            return

        if field == "xui_inbound":
            set_server_state(EDIT_SERVER_XUI_INBOUND)
            await msg.edit_text(
                "🧩 لطفاً شناسه اینباند جدید را وارد کنید:\n`0`=همه، `1`=تک، `1,2,3`=چندتا\nمثال: 0 یا 1 یا 1,2",
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
            await msg.reply_text("❌ ویرایش سرور لغو شد.", reply_markup=admin_main_keyboard())
            return

        await msg.edit_text("❌ گزینه‌ی ویرایش سرور نامعتبر است.")
        return

    # ------ مدیریت سرورها (servers:...) و server:... ------
    await query.answer()

    if data == "servers:list_back":
        # اگر از افزودن سرور آمده، state را پاک کن
        for k in ("state", "new_server", "add_user", "add_user_server_id", "add_user_plan_id"):
            context.user_data.pop(k, None)
        await send_servers_list(chat_id=chat_id, context=context, message=msg)
        return

    if data.startswith("servers:add:type:"):
        ptype = data.split("servers:add:type:", 1)[1]
        if ptype not in {"hiddify", "xui"}:
            await query.answer("نوع پنل نامعتبر است.")
            return
        context.user_data["state"] = ADD_STATE_TITLE
        new_server = context.user_data.get("new_server") or {}
        new_server["panel_type"] = ptype
        context.user_data["new_server"] = new_server
        try:
            await msg.edit_text("نوع پنل: " + ("هیدیفای" if ptype == "hiddify" else "X-UI"))
        except Exception:
            pass
        await msg.reply_text(
            "لطفاً عنوان سرور را وارد کنید:",
            reply_markup=cancel_keyboard(),
        )
        return

    if data == "servers:add":
        context.user_data["state"] = ADD_STATE_TYPE
        context.user_data["new_server"] = {}
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await msg.reply_text(
            "نوع پنل سرور را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("هیدیفای (Hiddify)", callback_data="servers:add:type:hiddify"),
                        InlineKeyboardButton("X-UI", callback_data="servers:add:type:xui"),
                    ],
                    [InlineKeyboardButton("🔙بازگشت", callback_data="servers:list_back")],
                ]
            ),
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

                panel_user_uuid = await _resolve_panel_user_uuid(server, server_id, user_uuid)
                base = _build_user_base_url(server, panel_user_uuid)
                if cfg_type in {"auto_sub", "sub", "sub_b64"} and not base:
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
                    url, _, _owner = _build_admin_managed_sub_links(server, panel_user_uuid)
                    if not url:
                        kb = InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "🚪 ورود به پنل کاربر",
                                        callback_data=f"server:{server_id}:usercfg:{panel_user_uuid}:bot_link",
                                        style="success",
                                    )
                                ],
                                [
                                    InlineKeyboardButton(
                                        "🔙 برگشت به منوی لینک‌ها",
                                        callback_data=f"server:{server_id}:usercfg:{panel_user_uuid}",
                                        style="primary",
                                    )
                                ],
                            ]
                        )
                        await msg.edit_text(
                            "❌ لینک اشتراک هوشمند برای این کاربر هنوز آماده نیست.\n"
                            "می‌توانید لینک پنل کاربر هیدیفای را دریافت کنید.",
                            reply_markup=kb,
                        )
                        return
                    caption_title = "لینک اشتراک هوشمند"
                elif cfg_type == "multi_b64":
                    _, url, _owner = _build_admin_managed_sub_links(server, panel_user_uuid)
                    if not url:
                        kb = InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "🚪 ورود به پنل کاربر",
                                        callback_data=f"server:{server_id}:usercfg:{panel_user_uuid}:bot_link",
                                        style="success",
                                    )
                                ],
                                [
                                    InlineKeyboardButton(
                                        "🔙 برگشت به منوی لینک‌ها",
                                        callback_data=f"server:{server_id}:usercfg:{panel_user_uuid}",
                                        style="primary",
                                    )
                                ],
                            ]
                        )
                        await msg.edit_text(
                            "❌ لینک اشتراک هوشمند b64 برای این کاربر هنوز آماده نیست.\n"
                            "می‌توانید لینک پنل کاربر هیدیفای را دریافت کنید.",
                            reply_markup=kb,
                        )
                        return
                    caption_title = "لینک اشتراک هوشمند b64"
                elif cfg_type == "bot_link":
                    if not base:
                        await msg.edit_text(
                            "❌ تنظیمات panel_url یا user_proxy_path برای این سرور کامل نیست.",
                        )
                        return
                    url = _panel_user_link_from_base(base)
                    text = f"🌐 لینک پنل کاربر هیدیفای\n{url}"
                    kb = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🚪 باز کردن پنل کاربر",
                                    url=url,
                                    style="success",
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    "🔙 برگشت به منوی لینک‌ها",
                                    callback_data=f"server:{server_id}:usercfg:{panel_user_uuid}",
                                    style="primary",
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
            userdel_source = str(context.user_data.get("userdel_source") or "users").strip().lower()
            if userdel_source not in {"users", "expired"}:
                userdel_source = "users"
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ بله، حذف شود",
                            callback_data=f"deluser:{server_id}:{user_uuid}:yes:{userdel_source}",
                        ),
                        InlineKeyboardButton(
                            "لغو❌",
                            callback_data=f"deluser:{server_id}:{user_uuid}:no:{userdel_source}",
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
            await msg.edit_text(
                "🔄 همگام‌سازی نودها\n\n"
                "از این بخش می‌توانید بعد از اضافه کردن نود جدید، کاربران موجود سرور اصلی را روی نودها بسازید "
                "و مشخصات حجم/زمان/وضعیت را همسان کنید.\n\n"
                "برای امنیت، کاربران اضافه روی نودها حذف نمی‌شوند و فقط گزارش داده می‌شوند.",
                reply_markup=build_node_sync_menu_keyboard(server_id),
            )
            return

        if action in {"sync_nodes_report", "sync_nodes_missing", "sync_nodes_details", "sync_nodes_full"}:
            mode_text = {
                "sync_nodes_report": "📊 در حال بررسی نودها...",
                "sync_nodes_missing": "🧩 در حال ساخت کاربران جاافتاده روی نودها...",
                "sync_nodes_details": "🔁 در حال همسان‌سازی مشخصات کاربران موجود...",
                "sync_nodes_full": "✅ در حال اجرای کامل امن همگام‌سازی...",
            }.get(action, "🔄 در حال همگام‌سازی...")
            await msg.edit_text(mode_text)

            summary = await _run_node_sync(
                server_id,
                create_missing=action in {"sync_nodes_missing", "sync_nodes_full"},
                patch_existing=action in {"sync_nodes_details", "sync_nodes_full"},
            )
            await msg.edit_text(
                _format_node_sync_report(summary),
                reply_markup=build_node_sync_menu_keyboard(server_id),
            )
            return

        if action == "sync_nodes_migrate_users":
            await msg.edit_text("🔄 در حال ثبت سرویس کاربران قدیمی ادمین در دیتابیس...")
            try:
                result = await _run_admin_user_migration(server_id)
                errors = result.get("errors") or []
                error_lines = "\n".join(f"- {e}" for e in errors[:5]) if errors else ""
                rtl = "\u200f"
                await msg.edit_text(
                    f"{rtl}✅ ثبت سرویس کاربران قدیمی ادمین به پایان رسید.\n\n"
                    f"{rtl}👤 کل کاربران بررسی‌شده: {result['total']}\n"
                    f"{rtl}🆕 سرویس جدید ساخته شد: {result['created']}\n"
                    f"{rtl}⏭ از قبل وجود داشت: {result['skipped']}\n"
                    f"{rtl}❌ خطا: {result['errors_count']}"
                    f"{chr(10) + chr(10) + f'{rtl}جزئیات خطا:' + chr(10) + error_lines if error_lines else ''}",
                    reply_markup=build_node_sync_menu_keyboard(server_id),
                )
            except Exception as e:
                await msg.edit_text(
                    f"❌ خطا در مایگریشن کاربران قدیمی:\n\n{e}",
                    reply_markup=build_node_sync_menu_keyboard(server_id),
                )
            return

        if action == "create_inbound_from_link":
            server = database.get_server_by_id(server_id)
            if not server or str(server.get("panel_type") or "").strip().lower() not in {"xui", "x-ui"}:
                await msg.edit_text("❌ این قابلیت فقط برای پنل X-UI است.")
                return
            from AdminBot.states import XUI_CREATE_INBOUND_FROM_LINK

            context.user_data["state"] = XUI_CREATE_INBOUND_FROM_LINK
            context.user_data["create_inbound_server_id"] = server_id
            await msg.edit_text(
                "🔗 لطفاً لینک کانفیگ (vless/vmess/hysteria2/trojan) را ارسال کنید:\n\n"
                "مثال:\n`vless://uuid@host:443?security=tls&type=httpupgrade...`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("لغو❌", callback_data=f"server:{server_id}")]]
                ),
            )
            return

        if action == "sync_inbounds":
            server = database.get_server_by_id(server_id)
            if not server or str(server.get("panel_type") or "").strip().lower() not in {"xui", "x-ui"}:
                await msg.edit_text("❌ این قابلیت فقط برای پنل X-UI است.")
                return
            await msg.edit_text("⏳ در حال همگام‌سازی یوزرها روی همه اینباندها...")
            try:
                from Shared import xui_api

                result = await xui_api.sync_users_to_inbounds(server)
                if result.get("ok"):
                    errs = result.get("errors") or []
                    err_txt = ""
                    if errs:
                        err_txt = f"\n❌ خطاها ({len(errs)}):\n" + "\n".join(errs[:3])
                    await msg.edit_text(
                        f"✅ همگام‌سازی انجام شد.\n\n"
                        f"👥 کل یوزرها: {result.get('total_users')}\n"
                        f"🧩 اینباندهای هدف: {result.get('target_inbounds')}\n"
                        f"➕ ساخته شد: {result.get('created')}\n"
                        f"⏭ از قبل بود: {result.get('skipped')}{err_txt}",
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"server:{server_id}")]]
                        ),
                    )
                else:
                    await msg.edit_text(
                        f"❌ {result.get('msg')}",
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"server:{server_id}")]]
                        ),
                    )
            except Exception as e:
                await msg.edit_text(
                    f"❌ خطا در همگام‌سازی:\n{e}",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"server:{server_id}")]]
                    ),
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

    # ویزارد‌های مدیریت نماینده‌ها (agency)
    state_val = context.user_data.get("state") or ""
    if isinstance(state_val, str) and state_val.startswith("agency:"):
        consumed = await handle_agencies_text(update, context)
        if consumed:
            return

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
       context.user_data.get(ZARIN_COUPON_BULK_STATE) or \
       context.user_data.get(SUB_TRACKING_STATE) or \
       context.user_data.get(TICKET_REPLY_STATE) or \
       context.user_data.get(BROADCAST_SEND_STATE) or \
       context.user_data.get(SUB_BASE_URL_EDIT_STATE) or \
       context.user_data.get("userbot_ticket_reply") or \
       context.user_data.get("userbot_sub_reminder_edit") or \
       context.user_data.get("userbot_sub_base_url_edit") or \
       context.user_data.get(ORDERS_SEARCH_STATE_KEY) or \
       context.user_data.get(PAYMENT_SEARCH_STATE) or \
       context.user_data.get(TX_PLANS_EDIT_STATE) or \
       context.user_data.get(REFERRAL_VALUE_EDIT_STATE) or \
       context.user_data.get(REFERRAL_MANUAL_REWARD_STATE):
        
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

    # اگر مدیر روی دکمه «مدیریت نماینده‌ها🏢» کلیک کرد
    if _is_agencies_button(text, text_key):
        await handle_agencies_entry(update, context)
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

    # --- هندلر مدیریت نماینده‌ها (agency) ---
    if data.startswith("agency:"):
        await handle_agencies_callback(update, context)
        return

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
