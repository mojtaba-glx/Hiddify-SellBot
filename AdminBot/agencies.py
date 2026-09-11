# AdminBot/agencies.py
# مدیریت سیستم نمایندگی (Agency/Reseller) در پنل ادمین

import logging
import subprocess
import signal
import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from html import escape
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from Shared import agent_db, database, userbot_db, secure_io
from AgentBot import database as agentbot_db
from CustomerBot import database as customerbot_db
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from AdminBot.keyboards import admin_main_keyboard
try:
    from AdminBot.keyboards import cancel_keyboard
except ImportError:  # محیط‌های تست با استاب minimal
    def cancel_keyboard():
        return ReplyKeyboardMarkup([[KeyboardButton("❌ لغو")]], resize_keyboard=True, one_time_keyboard=True)

logger = logging.getLogger(__name__)

# ===============================
#   ثابت‌ها
# ===============================
AGENCIES_PAGE_SIZE = 8
SERVICES_PAGE_SIZE = 6

SEPARATOR = "❖ • ────────────────────── • ❖"

# استیت‌های متنی (wizard)
AGENCY_ADD_TELEGRAM = "agency:add_telegram"
AGENCY_ADD_PHONE = "agency:add_phone"
AGENCY_ADD_NAME = "agency:add_name"
AGENCY_EDIT_PHONE = "agency:edit_phone"
AGENCY_EDIT_NAME = "agency:edit_name"
AGENCY_WALLET_CHARGE = "agency:wallet_charge"
AGENCY_SET_WHOLESALE = "agency:set_wholesale"
AGENCY_SET_WHOLESALE_GB = "agency:set_wholesale_gb"
AGENCY_SET_WHOLESALE_DAYS = "agency:set_wholesale_days"
AGENCY_BULK_WHOLESALE = "agency:bulk_wholesale"
AGENCY_SET_AGENT_TOKEN = "agency:set_agent_token"
AGENCY_SVC_SEARCH = "agency:svc_search"
AGENCY_SVC_EDITNOTE = "agency:svc_editnote"
AGENCY_SVC_EDITNOTE_VALUE = "agency:svc_editnote_value"
AGENCY_SVC_EDIT_TARGET = "agency_svc_edit_target"
AGENCY_EVENT_CHANNEL_STATE = "agency:event_channel"

# کلیدهای user_data برای صفحه‌بندی
AGENCY_PAGE_KEY = "agency_page"
AGENCY_SERVICES_PAGE_KEY = "agency_services_page"
AGENCY_VIEWING_ID_KEY = "agency_viewing_id"

# وضعیت رابط سرویس‌ها — به‌ازای هر (ادمین، نماینده) جدا نگه داشته می‌شود.
# کلید درون user_data (که خودش به‌ازای هر ادمین جداست): agency_svc_ui_{agent_id}
AGENCY_SVC_UI_KEY = "agency_svc_ui_{agent_id}"

# برچسب‌های فیلتر/مرتب‌سازی
SVC_FILTER_LABELS = {
    "all": "همه",
    "active": "فعال",
    "inactive": "غیرفعال",
    "expired": "منقضی",
}
SVC_FILTER_ORDER = ("all", "active", "inactive", "expired")
SVC_FILTER_ICONS = {
    "all": "🎛",
    "active": "🟢",
    "inactive": "🔴",
    "expired": "💀",
}
SVC_SORT_LABELS = {
    "newest": "جدیدترین",
    "expiry": "نزدیک‌ترین انقضا",
    "name": "نام",
}
SVC_SORT_ICONS = {
    "newest": "↕️",
    "expiry": "⏳",
    "name": "🔤",
}
SVC_SORT_ORDER = ("newest", "expiry", "name")


# ===============================
#   توابع کمکی
# ===============================
def _escape(text: Any) -> str:
    return escape(str(text or ""))


def _fmt_toman(amount: int) -> str:
    try:
        return f"{int(amount or 0):,}"
    except Exception:
        return str(amount or 0)


def _fmt_gb(value: float) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v < 0.0001:
        return "0"
    if v >= 1024:
        return f"{v / 1024:g}T"
    if v == int(v):
        return f"{int(v)}"
    return f"{v:g}"


_FA_MONTHS = {
    1: "ژانویه", 2: "فوریه", 3: "مارس", 4: "آوریل", 5: "مه", 6: "ژوئن",
    7: "ژوئیه", 8: "آگوست", 9: "سپتامبر", 10: "اکتبر", 11: "نوامبر", 12: "دسامبر",
}

_LOCATION_FLAGS = {
    "ترکیه": "🇹🇷",
    "آلمان": "🇩🇪",
    "هلند": "🇳🇱",
    "فنلاند": "🇫🇮",
    "هند": "🇮🇳",
}


def _fmt_fa_date(ts: str) -> str:
    """تبدیل تاریخ ISO به «روز ماه» مثل «06 سپتامبر»."""
    try:
        raw = str(ts or "").strip()
        if not raw:
            return "—"
        dt = datetime.fromisoformat(raw[:19])
    except Exception:
        return "—"
    month = _FA_MONTHS.get(dt.month, "")
    if not month:
        return f"{dt.day:02d}"
    return f"{dt.day:02d} {month}"


def _server_flag_title(title: str) -> str:
    """از عنوان سرور یک برچسب کوتاه با flag می‌سازد؛ مثل «🇩🇪 آلمان»."""
    raw = str(title or "").strip()
    if not raw:
        return ""
    flag = ""
    for word, fl in _LOCATION_FLAGS.items():
        if word in raw:
            flag = fl
            break
    location = raw.replace("لوکیشن", "")
    for fl in set(_LOCATION_FLAGS.values()):
        location = location.replace(fl, "")
    location = location.strip()
    if flag and location:
        return f"{flag} {location}"
    if flag:
        return flag
    return raw.strip()


def _usage_text(usage_cur: float, usage_lim: float) -> str:
    c, l = float(usage_cur or 0), float(usage_lim or 0)
    return f"{_fmt_gb(c)}/{_fmt_gb(l)}GB"


# ===============================
#   کمکی‌های رابط سرویس‌های نماینده
# ===============================
NEAR_EXPIRY_DAYS = 3
UNKNOWN = "نامشخص"
SVC_EDIT_KEEP = "⏭ بدون تغییر"
SVC_EDIT_CLEAR_NOTE = "🗑 حذف یادداشت"


def _utcnow() -> datetime:
    """زمان فعلی UTC بدون timezone — هم‌قرارداد با end_date ذخیره‌شده در DB.

    دیتابیس زمان پایان را UTC naive ذخیره می‌کند؛ مقایسه با datetime.now()
    محلی در سرورهای غیر UTC باعث انقضای زودتر/دیرتر نمایش می‌شد.
    """
    from datetime import timezone as _tz
    return datetime.now(_tz.utc).replace(tzinfo=None)


def _svc_ui(context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> Dict[str, Any]:
    """وضعیت رابط سرویس‌ها برای یک نماینده — جدا به‌ازای هر ادمین و نماینده.

    context.user_data در PTB به‌ازای هر کاربر (اینجا: ادمین) جداست؛ کلید هم
    شامل agent_id است، پس فیلتر/جستجو/صفحه دو نماینده با هم قاطی نمی‌شود.

    نکته: عمداً از «user_data or {}» استفاده نشده — اگر user_data خالی
    (falsy) باشد، «or» یک دیکشنری جدید می‌سازد و state در user_data واقعی
    ذخیره نمی‌شود. به‌جای آن با isinstance بررسی می‌کنیم.
    """
    key = AGENCY_SVC_UI_KEY.format(agent_id=agent_id)
    ud = getattr(context, "user_data", None)
    if not isinstance(ud, dict):
        ud = {}
    state = ud.get(key)
    if not isinstance(state, dict):
        state = {}
    state.setdefault("page", 1)
    state.setdefault("filter", "all")
    state.setdefault("sort", "newest")
    state.setdefault("query", "")
    if state["filter"] not in SVC_FILTER_LABELS:
        state["filter"] = "all"
    if state["sort"] not in SVC_SORT_LABELS:
        state["sort"] = "newest"
    try:
        state["page"] = max(1, int(state["page"] or 1))
    except (TypeError, ValueError):
        state["page"] = 1
    state["query"] = str(state.get("query") or "")
    ud[key] = state
    return state


def _svc_save_ui(context: ContextTypes.DEFAULT_TYPE, agent_id: int, state: Dict[str, Any]) -> None:
    ud = getattr(context, "user_data", None)
    if isinstance(ud, dict):
        ud[AGENCY_SVC_UI_KEY.format(agent_id=agent_id)] = state


def _svc_code(svc: Dict[str, Any]) -> str:
    """شناسه ۷ رقمی سرویس از comment (فرم code:XXXXXXX)."""
    for part in str((svc or {}).get("comment") or "").split("|"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        if k.strip().lower() == "code":
            return str(v).strip()
    return ""


def _svc_note_text(svc: Dict[str, Any]) -> str:
    raw = str((svc or {}).get("comment") or "")
    idx = raw.find("note:")
    return raw[idx + len("note:"):].strip() if idx != -1 else ""


def _svc_rebuild_comment(comment: str, new_note: str) -> str:
    """جایگزینی فقط بخش note در comment؛ بخش code (شناسه) و بقیه حفظ می‌شود."""
    raw = str(comment or "")
    idx = raw.find("note:")
    base = raw[:idx].rstrip("|") if idx != -1 else raw.rstrip("|")
    note = str(new_note or "").strip()
    if not note:
        return base
    return f"{base}|note:{note}" if base else f"note:{note}"


def _svc_parse_end(svc: Dict[str, Any]) -> Optional[datetime]:
    raw = str((svc or {}).get("end_date") or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except ValueError:
            continue
    return None


def _svc_expiry_parts(svc: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    محاسبه وضعیت انقضا از داده ذخیره‌شده (بدون تماس با پنل).
    همه محاسبات زمانی بر مبنای UTC است (end_date در DB به‌صورت UTC naive
    ذخیره می‌شود) — منطق مشترک با فیلترها و آمار (انحصاری).

    خروجی: icon، time (برچسب کوتاه زمان)، expired، near، unknown_time
    """
    current = now or _utcnow()
    end = _svc_parse_end(svc)
    is_active = bool(int((svc or {}).get("is_active", 0) or 0))
    days_left: Optional[int] = None
    try:
        raw_days = (svc or {}).get("days_left")
        if raw_days is not None and str(raw_days).strip() != "":
            days_left = int(float(raw_days))
    except (TypeError, ValueError):
        days_left = None

    icon, time_label = "🟢", UNKNOWN
    expired = False
    near = False
    unknown_time = True

    if end is not None:
        # end_date معتبر ملاک است؛ days_left قدیمی نتیجه را خراب نمی‌کند
        unknown_time = False
        remaining = (end - current).total_seconds()
        if remaining <= 0:
            expired = True
            icon, time_label = "🔴", "منقضی شده"
        else:
            days = int(remaining // 86400)
            if remaining < 86400:
                time_label = "امروز منقضی می‌شود"
            elif remaining < 2 * 86400:
                time_label = "فردا منقضی می‌شود"
            else:
                time_label = f"{days} روز باقی‌مانده"
            if remaining <= NEAR_EXPIRY_DAYS * 86400:
                near = True
    elif days_left is not None:
        unknown_time = days_left == 0
        if days_left < 0:
            expired = True
            icon, time_label = "🔴", "منقضی شده"
        elif days_left == 1:
            time_label = "فردا منقضی می‌شود"
            near = True
        elif days_left > 1:
            time_label = f"{days_left} روز باقی‌مانده"
            if days_left <= NEAR_EXPIRY_DAYS:
                near = True

    # آیکون‌ها بر اساس وضعیت انحصاری: منقضی > غیرفعال > نزدیک انقضا > فعال
    if expired:
        icon = "🔴"
    elif not is_active:
        icon = "⚪️"
    elif near:
        icon = "⏳"
    else:
        icon = "🟢"
    return {
        "icon": icon,
        "time": time_label,
        "expired": expired,
        "near": near,
        "unknown_time": unknown_time,
    }


def _svc_status_word(svc: Dict[str, Any]) -> str:
    """عبارت وضعیت برای صفحه جزئیات — تعریف انحصاری مشترک با فیلترها/آمار."""
    parts = _svc_expiry_parts(svc)
    if parts["expired"]:
        return "منقضی شده"
    if not bool(int((svc or {}).get("is_active", 0) or 0)):
        return "غیرفعال"
    return "فعال"


def _shorten(text: Any, limit: int = 16) -> str:
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def _svc_button_label(svc: Dict[str, Any]) -> str:
    parts = _svc_expiry_parts(svc)
    sid = int((svc or {}).get("id") or 0)
    name = _shorten((svc or {}).get("name"), 14) or f"#{sid}"
    return f"{parts['icon']} {name} · #{sid} · {parts['time']}"


def _fmt_end_full(svc: Dict[str, Any]) -> str:
    """پایان اعتبار: تاریخ + ساعت + منطقه زمانی (end_date به‌صورت UTC ذخیره می‌شود)."""
    end = _svc_parse_end(svc)
    if end is None:
        return UNKNOWN
    month = _FA_MONTHS.get(end.month, "")
    base = f"{end.day:02d} {month} {end.year}" if month else f"{end.year}-{end.month:02d}-{end.day:02d}"
    return f"{base} · {end:%H:%M} (UTC)"


def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _svc_is_admin(update: Update) -> bool:
    """کنترل دسترسی ادمین برای مسیرهای این ماژول (لایه دوم؛ ورودی اصلی
    از AdminBot/servers.py هم کنترل می‌شود)."""
    import os as _os
    user = getattr(update, "effective_user", None)
    try:
        admin_id = int(_os.getenv("ADMIN_ID", "0") or "0")
    except (TypeError, ValueError):
        return False
    return bool(user and admin_id > 0 and int(user.id) == admin_id)


def _parse_panel_dt(value: Any) -> Optional[datetime]:
    """پارس زمان پنل (ISO یا timestamp) به datetime بی‌منطقه (UTC)."""
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    try:
        stamp = float(raw)
        if stamp > 0:
            if stamp > 10_000_000_000:
                stamp /= 1000.0
            from datetime import timezone as _tz
            return datetime.fromtimestamp(stamp, _tz.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        pass
    raw = raw.replace("Z", "+00:00").replace("z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw[:26])
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw[:19], fmt)
            except ValueError:
                continue
        return None
    if parsed.tzinfo is not None:
        from datetime import timezone as _tz
        parsed = parsed.astimezone(_tz.utc).replace(tzinfo=None)
    return parsed


def _panel_expiry_datetime(user: Dict[str, Any], now: Optional[datetime] = None) -> Optional[datetime]:
    """استخراج زمان انقضای کاربر پنل — هم‌منطق با AgentBot (استفاده از توابع معتبر موجود)."""
    from AgentBot.services.subscription_service import _panel_expiry_datetime as _impl
    return _impl(user, now)


def _fmt_agent_display(agent: Dict[str, Any]) -> str:
    """نمایش خلاصه یک نماینده."""
    name = str(agent.get("full_name") or "").strip()
    username = str(agent.get("username") or "").strip()
    agent_id = agent.get("id", "?")
    active = "✅" if int(agent.get("is_active", 0)) else "❌"

    if name:
        ident = name
    elif username:
        ident = f"@{username}"
    else:
        ident = f"نماینده #{agent_id}"

    return f"{active} {ident}"


def _main_menu_kb() -> InlineKeyboardMarkup:
    try:
        ev = userbot_db.get_agency_event_settings()
        event_icon = "✅" if ev.get("event_channel_enabled") else "❌"
    except Exception:
        event_icon = "❌"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ افزودن نماینده", callback_data="agency:add")],
            [
                InlineKeyboardButton("📋 لیست نماینده‌ها", callback_data="agency:list:1"),
                InlineKeyboardButton("⏳ شارژهای در انتظار", callback_data="agency:payments:1"),
            ],
            [
                InlineKeyboardButton("📊 آمار کلی", callback_data="agency:stats"),
                InlineKeyboardButton("⚙️ توکن ربات نماینده", callback_data="agency:agenttoken"),
            ],
            [
                InlineKeyboardButton(event_icon, callback_data="agency:event:toggle"),
                InlineKeyboardButton("تنظیم کانال رویداد📢", callback_data="agency:event:set"),
            ],
            [InlineKeyboardButton("🔙 منوی اصلی", callback_data="agency:exit")],
        ]
    )


def _agent_detail_kb(agent_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💰 شارژ کیف پول", callback_data=f"agency:charge:{agent_id}"),
                InlineKeyboardButton("💳 کیف پول", callback_data=f"agency:wallet:{agent_id}"),
            ],
            [InlineKeyboardButton("📦 سرویس‌ها", callback_data=f"agency:services:{agent_id}:1")],
            [InlineKeyboardButton("💵 تعرفه عمده", callback_data=f"agency:prices:{agent_id}:1")],
            [InlineKeyboardButton("🤖 ربات مشتری", callback_data=f"agency:bots:{agent_id}")],
            [InlineKeyboardButton("🔄 بازنشانی تست رایگان", callback_data=f"agency:resettrial:{agent_id}")],
            [InlineKeyboardButton("✏️ ویرایش نام", callback_data=f"agency:editname:{agent_id}")],
            [InlineKeyboardButton("✏️ ویرایش تلفن", callback_data=f"agency:editphone:{agent_id}")],
            [
                InlineKeyboardButton("🔁 فعال/غیرفعال", callback_data=f"agency:toggle:{agent_id}"),
                InlineKeyboardButton("🗑 حذف", callback_data=f"agency:delete:{agent_id}"),
            ],
            [InlineKeyboardButton("🔙 لیست نماینده‌ها", callback_data="agency:list:1")],
        ]
    )


# ===============================
#   ورود به منوی نمایندگی‌ها
# ===============================
async def handle_agencies_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ورود به منوی مدیریت نماینده‌ها."""
    agent_db.init_db()
    stats = agent_db.get_global_agency_stats()

    text = (
        "🏢 <b>داشبورد مدیریت نماینده‌ها</b>\n"
        f"{SEPARATOR}\n\n"
        f"👥 تعداد کل نمایندگان: <b>{stats['agents_total']}</b>\n"
        f"✅ فعال: <b>{stats['agents_active']}</b> | ❌ غیرفعال: <b>{stats['agents_total'] - stats['agents_active']}</b>\n\n"
        f"👤 مشتریان کل: <b>{stats['customers_total']}</b>\n"
        f"📦 سرویس‌ها: <b>{stats['services_total']}</b> (فعال: {stats['services_active']})\n\n"
        f"💰 فروش کل: <b>{_fmt_toman(stats['total_sales'])}</b> تومان\n"
        f"🏷 سود سیستم: <b>{_fmt_toman(stats['total_profit'])}</b> تومان\n"
        f"📥 شارژ کل: <b>{_fmt_toman(stats['total_charges'])}</b> تومان\n\n"
        f"🤖 ربات‌های فعال: <b>{stats['bots_active']}</b>"
    )

    # پاک کردن stateهای قبلی
    context.user_data.pop("state", None)
    context.user_data[AGENCY_PAGE_KEY] = 1

    if update.message:
        await update.message.reply_text(text, reply_markup=_main_menu_kb(), parse_mode="HTML")
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=_main_menu_kb(), parse_mode="HTML")
        except BadRequest:
            await update.callback_query.answer()


# ===============================
#   لیست نماینده‌ها
# ===============================
async def send_agents_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 1,
) -> None:
    """نمایش لیست نماینده‌ها با صفحه‌بندی."""
    if page < 1:
        page = 1
    context.user_data[AGENCY_PAGE_KEY] = page

    agents, total = agent_db.get_agents_list(page=page, page_size=AGENCIES_PAGE_SIZE)
    total_pages = max(1, (total + AGENCIES_PAGE_SIZE - 1) // AGENCIES_PAGE_SIZE)

    lines = [
        f"📋 <b>لیست نماینده‌ها</b>\n"
        f"{SEPARATOR}\n"
        f"صفحه {page} از {total_pages} | مجموع: {total} نفر\n"
    ]
    if not agents:
        lines.append("\nهیچ نماینده‌ای ثبت نشده است.\nبرای افزودن، روی «➕ افزودن نماینده» بزنید.")

    rows: List[List[Any]] = []
    # دکمه‌های هر نماینده
    for a in agents:
        label = _fmt_agent_display(a)
        rows.append([InlineKeyboardButton(label, callback_data=f"agency:view:{a['id']}")])

    # صفحه‌بندی
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"agency:list:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"agency:list:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("➕ افزودن نماینده", callback_data="agency:add")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="agency:root")])

    kb = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)

    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            await query.answer()
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


# ===============================
#   جزئیات یک نماینده
# ===============================
async def send_agent_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
) -> None:
    """نمایش جزئیات یک نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        query = update.callback_query
        if query:
            await query.answer("نماینده پیدا نشد.", show_alert=True)
        return

    stats = agent_db.get_agent_stats(agent_id)
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id

    active = "فعال ✅" if int(agent.get("is_active", 0)) else "غیرفعال ❌"
    name = _escape(agent.get('full_name')) or "—"
    username = f"@{_escape(agent.get('username'))}" if agent.get('username') else "—"
    phone = _escape(agent.get('phone')) or "—"

    text = (
        f"👤 <b>جزئیات نماینده</b>\n"
        f"{SEPARATOR}\n"
        f"📱 <b>تلگرام:</b> <code>{agent.get('telegram_id', '?')}</code>\n"
        f"🔢 <b>شناسه:</b> <code>{agent['id']}</code>\n"
        f"👤 <b>نام:</b> {name}\n"
        f"🔗 <b>یوزرنیم:</b> {username}\n"
        f"📞 <b>تلفن:</b> {phone}\n"
        f"📍 <b>وضعیت:</b> {active}\n"
        f"🕒 <b>عضویت:</b> {_escape(agent.get('created_at'))}\n"
        f"{SEPARATOR}\n"
        f"💰 <b>کیف پول:</b> {_fmt_toman(stats['wallet_balance'])} تومان\n"
        f"👥 <b>مشتریان:</b> {stats['customers_count']}\n"
        f"📦 <b>سرویس‌ها:</b> {stats['services_total']} (فعال: {stats['services_active']})\n"
        f"🔥 <b>ترایال:</b> {stats['trials_count']}\n"
        f"💵 <b>فروش کل:</b> {_fmt_toman(stats['total_sales'])} تومان\n"
        f"🏷 <b>سود نماینده:</b> {_fmt_toman(stats['total_profit'])} تومان"
    )

    kb = _agent_detail_kb(agent_id)
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            await query.answer()
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


# ===============================
#   افزودن نماینده (wizard)
# ===============================
AGENCY_CANCEL_KB_TEXT = "❌ لغو"


def _agency_cancel_kb() -> ReplyKeyboardMarkup:
    """کیبرد پایین صفحه با فقط دکمه لغو — برای مراحل ویزارد متنی.

    دکمه با ابزار استایل مشترک پروژه (tg_button_styles) ساخته می‌شود؛ چون
    متن دکمه «لغو» است، استایل danger (قرمز) می‌گیرد — هم‌سان با بقیه ربات.
    """
    from Shared.tg_button_styles import keyboard_button as _StyledKeyboardButton
    return ReplyKeyboardMarkup(
        [[_StyledKeyboardButton(AGENCY_CANCEL_KB_TEXT, style="danger")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def _agency_wizard_drop_user_message(update: Update) -> None:
    """حذف پیام متنی ادمین (ورودی آیدی/توکن) — بهترین تلاش."""
    if getattr(update, "message", None):
        try:
            await update.message.delete()
        except Exception:
            pass


async def _agency_wizard_drop_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """حذف پیام قبلی ویزارد (سوال مرحله قبل) — بهترین تلاش."""
    prompt_id = context.user_data.pop("agency_wizard_msg_id", None)
    if prompt_id and update.effective_chat:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id, message_id=int(prompt_id))
        except Exception:
            pass


async def _agency_wizard_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    delete_user_message: bool = False,
) -> None:
    """ارسال مرحله جدید ویزارد: پیام قبلی (سوال مرحله قبل) پاک می‌شود و
    پیام جدید با دکمه لغو در کیبرد پایین صفحه ارسال می‌شود.

    delete_user_message: پیام ورودی ادمین (مثل آیدی عددی) هم پاک می‌شود.
    """
    kb = _agency_cancel_kb()
    prev_id = context.user_data.get("agency_wizard_msg_id")
    context.user_data.pop("agency_wizard_msg_id", None)
    chat_id = update.effective_chat.id

    # پاک کردن کیبرد inline لغو قدیمی اگر از callback آمده
    query = update.callback_query
    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=kb,
            parse_mode="HTML",
        )
        context.user_data["agency_wizard_msg_id"] = sent.message_id
    elif update.message:
        # اول پیام جدید (در پاسخ به پیام فعلی)، بعد پاک‌سازی
        msg = await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
        context.user_data["agency_wizard_msg_id"] = msg.message_id

    # پاک‌سازی: پیام ورودی ادمین و پیام مرحله قبلی ویزارد
    if delete_user_message and getattr(update, "message", None):
        try:
            await update.message.delete()
        except Exception:
            pass
    if prev_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=int(prev_id))
        except Exception:
            pass


async def _agency_wizard_clear(update: Update, context: ContextTypes.DEFAULT_TYPE, done_text: str, reply_markup=None) -> None:
    """پایان ویزارد: پیام نهایی با کیبرد اصلی + پاک‌سازی پیام‌های قبلی."""
    chat_id = update.effective_chat.id
    prev_id = context.user_data.get("agency_wizard_msg_id")
    context.user_data.pop("agency_wizard_msg_id", None)

    kwargs = {"parse_mode": "HTML"}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    await update.message.reply_text(done_text, **kwargs)

    # پاک‌سازی بعد از ارسال (تا reply به پیام حذف‌شده نخورد)
    if getattr(update, "message", None):
        try:
            await update.message.delete()
        except Exception:
            pass
    if prev_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=int(prev_id))
        except Exception:
            pass


async def start_add_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """شروع ویزارد افزودن نماینده — دریافت آیدی تلگرام."""
    context.user_data["state"] = AGENCY_ADD_TELEGRAM
    context.user_data.pop("agency_new_telegram_id", None)
    context.user_data.pop("agency_new_name", None)
    context.user_data.pop("agency_wizard_msg_id", None)

    text = (
        "➕ <b>افزودن نماینده جدید</b>\n"
        f"{SEPARATOR}\n\n"
        "مرحله ۱ از ۳\n\n"
        "لطفاً <b>آیدی عددی تلگرام</b> کاربر را ارسال کنید.\n\n"
        "💡 برای پیدا کردن آیدی، کاربر می‌تواند به @userinfobot پیام بدهد.\n\n"
        "یا کاربر ابتدا به ربات نمایندگی /start بزند تا شناسایی شود.\n\n"
        "برای لغو دکمه «❌ لغو» پایین صفحه را بزنید."
    )
    await _agency_wizard_step(update, context, text)


async def handle_add_agent_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    هندلر پیام متنی در حالت افزودن نماینده.
    خروجی True اگر پیام مصرف شد.
    """
    state = context.user_data.get("state")
    text = (update.message.text or "").strip()

    if state == AGENCY_ADD_TELEGRAM:
        # پارس کردن آیدی تلگرام
        raw = text.replace("،", "").replace(",", "").replace(" ", "")
        try:
            telegram_id = int(raw)
            if telegram_id <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ آیدی تلگرام نامعتبر است. لطفاً عدد صحیح ارسال کنید."
            )
            await _agency_wizard_drop_user_message(update)
            return True

        # بررسی تکراری نبودن
        existing = agent_db.get_agent_by_telegram_id(telegram_id)
        if existing:
            await update.message.reply_text(
                f"⚠️ این کاربر قبلاً به‌عنوان نماینده ثبت شده است (شناسه {existing['id']})."
            )
            context.user_data.pop("state", None)
            await send_agent_detail(update, context, existing["id"])
            await _agency_wizard_drop_user_message(update)
            await _agency_wizard_drop_prompt(update, context)
            return True

        # تلاش برای دریافت اطلاعات خودکار از تلگرام
        username = ""
        full_name = ""
        try:
            from telegram import Bot
            bot = Bot(token=os.getenv("ADMIN_BOT_TOKEN"))
            user = await bot.get_chat(telegram_id)
            username = user.username or ""
            full_name = user.full_name or ""
        except Exception:
            pass

        context.user_data["agency_new_telegram_id"] = telegram_id
        context.user_data["agency_new_username"] = username
        context.user_data["agency_new_full_name"] = full_name
        context.user_data["state"] = AGENCY_ADD_NAME

        auto_info = ""
        if full_name:
            auto_info += f"\n👤 نام خودکار: <b>{_escape(full_name)}</b>"
        if username:
            auto_info += f"\n🔗 یوزرنیم خودکار: <b>@{_escape(username)}</b>"

        await _agency_wizard_step(
            update, context,
            f"✅ آیدی تلگرام ثبت شد: <code>{telegram_id}</code>{auto_info}\n\n"
            "مرحله ۲ از ۳\n\n"
            "لطفاً <b>نام کامل</b> نماینده را ارسال کنید.\n"
            "یا برای استفاده از نام خودکار «—» بفرستید.\n\n"
            "برای لغو دکمه «❌ لغو» پایین صفحه را بزنید.",
            delete_user_message=True,
        )
        return True

    if state == AGENCY_ADD_NAME:
        full_name = text.strip()
        if full_name == "—":
            full_name = context.user_data.get("agency_new_full_name", "")

        context.user_data["agency_new_full_name"] = full_name
        context.user_data["state"] = AGENCY_ADD_PHONE

        await _agency_wizard_step(
            update, context,
            f"✅ نام ثبت شد: <b>{_escape(full_name) or '—'}</b>\n\n"
            "مرحله ۳ از ۳\n\n"
            "لطفاً <b>شماره تلفن</b> نماینده را ارسال کنید.\n"
            "برای رد کردن «—» بفرستید.\n\n"
            "برای لغو دکمه «❌ لغو» پایین صفحه را بزنید.",
            delete_user_message=True,
        )
        return True

    if state == AGENCY_ADD_PHONE:
        telegram_id = context.user_data.get("agency_new_telegram_id")
        if not telegram_id:
            context.user_data.pop("state", None)
            return False

        phone = text.strip()
        if phone in {"0", "۰", "بدون", "ندارم", "—"}:
            phone = ""

        full_name = context.user_data.get("agency_new_full_name", "")
        username = context.user_data.get("agency_new_username", "")

        # ساخت نماینده
        agent_id = agent_db.upsert_agent(
            telegram_id=int(telegram_id),
            username=username,
            full_name=full_name,
        )
        if phone:
            agent_db.update_agent(agent_id, {"phone": phone})
        # کیف پول اولیه
        agent_db.get_wallet(agent_id)

        context.user_data.pop("state", None)
        context.user_data.pop("agency_new_telegram_id", None)
        context.user_data.pop("agency_new_full_name", None)
        context.user_data.pop("agency_new_username", None)

        await _agency_wizard_clear(
            update, context,
            f"✅ <b>نماینده با موفقیت ثبت شد!</b>\n"
            f"{SEPARATOR}\n\n"
            f"🆔 شناسه: <code>{agent_id}</code>\n"
            f"👤 نام: {_escape(full_name) or '—'}\n"
            f"🔗 یوزرنیم: @{_escape(username) if username else '—'}\n\n"
            "نماینده باید به ربات نمایندگی (AgentBot) بزند /start تا اطلاعات‌اش کامل شود.",
            reply_markup=admin_main_keyboard(),
        )
        await send_agent_detail(update, context, agent_id)
        return True

    return False


# ===============================
#   شارژ کیف پول
# ===============================
async def start_wallet_charge(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """شروع شارژ کیف پول — دریافت مبلغ."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        if update.callback_query:
            await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return

    wallet = agent_db.get_wallet(agent_id)
    context.user_data["state"] = AGENCY_WALLET_CHARGE
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id

    text = (
        f"💰 <b>شارژ کیف پول</b>\n\n"
        f"👤 نماینده: {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n"
        f"💳 موجودی فعلی: <b>{_fmt_toman(wallet['balance'])}</b> تومان\n\n"
        "مبلغ شارژ (به تومان) را ارسال کنید.\n"
        "برای لغو /cancel را بفرستید."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"agency:view:{agent_id}")]])

    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            await query.answer()


async def handle_wallet_charge_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """هندلر پیام متنی مبلغ شارژ."""
    state = context.user_data.get("state")
    if state != AGENCY_WALLET_CHARGE:
        return False

    agent_id = context.user_data.get(AGENCY_VIEWING_ID_KEY)
    if not agent_id:
        context.user_data.pop("state", None)
        return False

    text = (update.message.text or "").strip()
    # نرمال‌سازی ارقام فارسی
    fa_digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    raw = text.translate(fa_digits).replace(",", "").replace("،", "").replace(" ", "")
    try:
        amount = int(raw)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ مبلغ نامعتبر است. لطفاً عدد صحیح مثبت ارسال کنید.")
        return True

    wallet = agent_db.charge_wallet(agent_id, amount, description="شارژ توسط ادمین")
    context.user_data.pop("state", None)

    await update.message.reply_text(
        f"✅ کیف پول شارژ شد!\n"
        f"💰 مبلغ: <b>{_fmt_toman(amount)}</b> تومان\n"
        f"💳 موجودی جدید: <b>{_fmt_toman(wallet['balance'])}</b> تومان",
        reply_markup=admin_main_keyboard(),
        parse_mode="HTML",
    )
    await send_agent_detail(update, context, agent_id)
    return True


# ===============================
#   فعال/غیرفعال + حذف
# ===============================
async def toggle_agent_active(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    new_active = not bool(int(agent.get("is_active", 0)))
    agent_db.set_agent_active(agent_id, new_active)
    await update.callback_query.answer(
        f"نماینده {'فعال شد ✅' if new_active else 'غیرفعال شد ❌'}"
    )
    await send_agent_detail(update, context, agent_id)


async def confirm_delete_agent(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """نمایش تأیید حذف نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    stats = agent_db.get_agent_stats(agent_id)
    text = (
        f"⚠️ <b>تأیید حذف نماینده</b>\n\n"
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n\n"
        f"📦 سرویس‌ها: {stats['services_total']}\n"
        f"👥 مشتریان: {stats['customers_count']}\n\n"
        "❗️ با حذف، تمام داده‌های این نماینده (مشتریان، سرویس‌ها، کیف پول و...) پاک خواهد شد.\n"
        "آیا مطمئن هستید؟"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 بله، حذف کن", callback_data=f"agency:dodelete:{agent_id}"),
                InlineKeyboardButton("❌ خیر", callback_data=f"agency:view:{agent_id}"),
            ],
        ]
    )
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def do_delete_agent(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    ok = agent_db.delete_agent(agent_id)
    if ok:
        await update.callback_query.answer("نماینده حذف شد.")
    else:
        await update.callback_query.answer("حذف ناموفق بود.", show_alert=True)
    await send_agents_list(update, context, page=1)


# ===============================
#   ویرایش نام
# ===============================
async def start_edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    context.user_data["state"] = AGENCY_EDIT_NAME
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    text = (
        f"✏️ <b>ویرایش نام نماینده</b>\n"
        f"{SEPARATOR}\n\n"
        f"نام فعلی: <b>{_escape(agent.get('full_name')) or '—'}</b>\n\n"
        "نام جدید را ارسال کنید (برای خالی کردن «—» بفرستید)."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"agency:view:{agent_id}")]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def handle_edit_name_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = context.user_data.get("state")
    if state != AGENCY_EDIT_NAME:
        return False
    agent_id = context.user_data.get(AGENCY_VIEWING_ID_KEY)
    if not agent_id:
        context.user_data.pop("state", None)
        return False
    name = (update.message.text or "").strip()
    if name == "—":
        name = ""
    agent_db.update_agent(agent_id, {"full_name": name})
    context.user_data.pop("state", None)
    await update.message.reply_text(
        f"✅ نام نماینده بروزرسانی شد: <b>{_escape(name) or '—'}</b>",
        reply_markup=admin_main_keyboard(),
        parse_mode="HTML",
    )
    await send_agent_detail(update, context, agent_id)
    return True


# ===============================
#   ویرایش تلفن
# ===============================
async def start_edit_phone(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    context.user_data["state"] = AGENCY_EDIT_PHONE
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    text = (
        f"✏️ <b>ویرایش تلفن</b>\n"
        f"{SEPARATOR}\n\n"
        f"تلفن فعلی: <b>{_escape(agent.get('phone') or '—')}</b>\n\n"
        "تلفن جدید را ارسال کنید (برای خالی کردن «—» بفرستید)."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"agency:view:{agent_id}")]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def handle_edit_phone_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = context.user_data.get("state")
    if state != AGENCY_EDIT_PHONE:
        return False
    agent_id = context.user_data.get(AGENCY_VIEWING_ID_KEY)
    if not agent_id:
        context.user_data.pop("state", None)
        return False
    phone = (update.message.text or "").strip()
    if phone in {"0", "۰"}:
        phone = ""
    agent_db.update_agent(agent_id, {"phone": phone})
    context.user_data.pop("state", None)
    await update.message.reply_text("✅ تلفن بروزرسانی شد.", reply_markup=admin_main_keyboard())
    await send_agent_detail(update, context, agent_id)
    return True


# ===============================
#   مشاهده کیف پول / تراکنش‌ها
# ===============================
async def send_agent_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    wallet = agent_db.get_wallet(agent_id)
    transactions, total = agent_db.get_transactions(agent_id, page=1, page_size=10)

    lines = [
        f"💳 <b>کیف پول نماینده</b>\n\n",
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n",
        f"💰 موجودی: <b>{_fmt_toman(wallet['balance'])}</b> تومان\n",
        f"🕒 بروزرسانی: {_escape(wallet.get('updated_at'))}\n",
        f"\n━━━━━━━━━━━━━\n📜 <b>آخرین تراکنش‌ها</b> ({total})\n",
    ]
    if not transactions:
        lines.append("تراکنشی ثبت نشده است.")
    else:
        for tx in transactions:
            tx_type = tx.get("tx_type", "")
            amount = int(tx.get("amount", 0))
            sign = "+" if tx_type == "charge" else "-"
            type_fa = {"charge": "شارژ", "purchase": "خرید", "refund": "بازگشت"}.get(tx_type, tx_type)
            lines.append(
                f"{sign}{_fmt_toman(amount)} · {type_fa} · {_escape(tx.get('description') or '—')[:40]}\n"
                f"   {_escape(tx.get('created_at'))}"
            )

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💰 شارژ", callback_data=f"agency:charge:{agent_id}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"agency:view:{agent_id}")],
        ]
    )
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


# ===============================
#   آمار کلی سیستم
# ===============================
async def send_global_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stats = agent_db.get_global_agency_stats()
    text = (
        "📊 <b>آمار کلی سیستم نمایندگی</b>\n\n"
        f"👥 نماینده‌ها: <b>{stats['agents_total']}</b> (فعال: {stats['agents_active']})\n"
        f"👤 مشتریان: <b>{stats['customers_total']}</b>\n"
        f"📦 سرویس‌ها: <b>{stats['services_total']}</b> (فعال: {stats['services_active']})\n"
        f"🤖 ربات فعال: <b>{stats['bots_active']}</b>\n"
        f"\n━━━━━━━━━━━━━\n"
        f"💰 فروش کل: <b>{_fmt_toman(stats['total_sales'])}</b> تومان\n"
        f"🏷 هزینه عمده: <b>{_fmt_toman(stats['total_wholesale'])}</b> تومان\n"
        f"💵 سود سیستم: <b>{_fmt_toman(stats['total_profit'])}</b> تومان\n"
        f"📥 شارژ کیف پول: <b>{_fmt_toman(stats['total_charges'])}</b> تومان\n"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="agency:root")]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


def _parse_agent_payment_meta(payment: Dict[str, Any]) -> Dict[str, Any]:
    try:
        meta = json.loads(str(payment.get("receipt_image") or "{}"))
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _agent_payment_report_text(payment: Dict[str, Any], agent: Dict[str, Any]) -> str:
    meta = _parse_agent_payment_meta(payment)
    name = _escape(agent.get("full_name") or agent.get("username") or payment.get("customer_name") or agent.get("telegram_id"))
    amount = int(payment.get("amount") or meta.get("final_amount") or 0)
    last4 = _escape(payment.get("card_last4") or meta.get("card_last4") or "----")
    ref_id = _escape(payment.get("ref_id") or payment.get("id"))
    return (
        "🕊 <b>گزارش تایید پرداخت نماینده</b> 🕊\n\n"
        "💸 شیوه پرداخت: کارت به کارت\n"
        f"🔑 شناسه تراکنش: <code>{ref_id}</code>\n"
        f"👤 نماینده: <b>{name}</b>\n"
        f"💰 مبلغ پرداخت: <b>{_fmt_toman(amount)}</b> تومان\n"
        f"💳 4 رقم آخر کارت مبدا: <code>{last4}</code>"
    )


def _agent_payment_action_kb(payment_id: int, agent_id: int) -> InlineKeyboardMarkup:
    agent = agent_db.get_agent_by_id(agent_id) or {}
    name = _escape(agent.get("full_name") or agent.get("username") or f"نماینده #{agent_id}")
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("رد ❌", callback_data=f"agency:payno:{payment_id}"),
            InlineKeyboardButton("تایید ✅", callback_data=f"agency:payok:{payment_id}"),
        ],
        [InlineKeyboardButton(f"{name} 👤", callback_data=f"agency:view:{agent_id}")],
    ])


async def send_pending_agent_payments(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1) -> None:
    payments, total = agentbot_db.get_pending_wallet_charge_payments(page=page, page_size=8)
    total_pages = max(1, (total + 7) // 8)
    lines = [
        "⏳ <b>شارژهای نماینده در انتظار تایید</b>\n\n",
        f"تعداد: <b>{total}</b> | صفحه {page}/{total_pages}\n",
    ]
    rows: List[List[Any]] = []
    if not payments:
        lines.append("موردی برای تایید وجود ندارد.")
    for p in payments:
        agent = agent_db.get_agent_by_id(int(p.get("agent_id") or 0)) or {}
        name = agent.get("full_name") or agent.get("username") or p.get("customer_name") or f"نماینده #{p.get('agent_id')}"
        lines.append(f"• {_escape(name)} | {_fmt_toman(p.get('amount'))} تومان | کد {p.get('ref_id')}")
        rows.append([InlineKeyboardButton(f"{name} - {_fmt_toman(p.get('amount'))}", callback_data=f"agency:payview:{p['id']}")])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("قبلی", callback_data=f"agency:payments:{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("بعدی", callback_data=f"agency:payments:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="agency:root")])
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def show_agent_payment_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: int) -> None:
    query = update.callback_query
    payment = agentbot_db.get_payment_by_id(payment_id)
    if not payment:
        await query.answer("پرداخت پیدا نشد.", show_alert=True)
        return
    agent_id = int(payment.get("agent_id") or 0)
    agent = agent_db.get_agent_by_id(agent_id) or {}
    text = _agent_payment_report_text(payment, agent)
    meta = _parse_agent_payment_meta(payment)
    receipt = str(meta.get("receipt_file_id") or "")
    kb = _agent_payment_action_kb(payment_id, agent_id)
    if receipt:
        try:
            await query.message.delete()
        except BadRequest:
            pass
        await context.bot.send_photo(chat_id=query.message.chat_id, photo=receipt, caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")


async def approve_agent_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: int) -> None:
    query = update.callback_query
    payment = agentbot_db.get_payment_by_id(payment_id)
    if not payment:
        await query.answer("پرداخت پیدا نشد.", show_alert=True)
        return
    operation_key = f"agent-wallet-payment:{int(payment_id)}"
    status = str(payment.get("status") or "")
    if status not in {"pending", "processing"}:
        await query.answer("این پرداخت قبلاً بررسی شده است.", show_alert=True)
        return
    if status == "processing" and str(payment.get("processing_key") or "") != operation_key:
        await query.answer("این پرداخت در انتظار بررسی خودکار است.", show_alert=True)
        return
    agent_id = int(payment.get("agent_id") or 0)
    amount = int(payment.get("amount") or 0)
    # Claim the payment atomically before crediting the separate wallet DB.
    # A second callback can no longer approve/credit the same pending payment.
    if not agentbot_db.claim_payment_processing(payment_id, agent_id, operation_key):
        await query.answer("این پرداخت قبلاً در حال بررسی یا بررسی شده است.", show_alert=True)
        return
    try:
        wallet = agent_db.charge_wallet_once(
            agent_id,
            amount,
            operation_key,
            description=f"شارژ کارت به کارت نماینده - تراکنش {payment.get('ref_id')}",
        )
        if not agentbot_db.finish_payment_processing(
            payment_id, agent_id, operation_key, "approved"
        ):
            logger.error("Wallet credited but payment status could not be finalized (payment=%s)", payment_id)
            recover_processing_agent_wallet_payments()
            recovered = agentbot_db.get_payment_by_id(payment_id) or {}
            if str(recovered.get("status") or "") != "approved":
                await query.answer(
                    "کیف پول شارژ شد اما ثبت نهایی پرداخت به بازیابی خودکار سپرده شد.",
                    show_alert=True,
                )
                return
    except Exception:
        # If the wallet transaction exists, leave the payment in processing so
        # startup recovery can finalize it without a second credit.
        if not agent_db.get_wallet_transaction_by_key(operation_key):
            agentbot_db.finish_payment_processing(
                payment_id, agent_id, operation_key, "pending"
            )
        raise
    agent = agent_db.get_agent_by_id(agent_id) or {}
    try:
        token = os.getenv("AGENT_BOT_TOKEN", "").strip()
        agent_tg_id = int(agent.get("telegram_id") or 0)
        if token and agent_tg_id:
            from telegram import Bot
            bot = Bot(token=token)
            await bot.send_message(
                chat_id=agent_tg_id,
                text=f"✅ پرداخت شما تایید شد.\n\nمبلغ {_fmt_toman(amount)} تومان به کیف پول شما اضافه شد.",
            )
    except Exception as e:
        logger.warning("Failed notifying agent payment approval: %s", e)
    await query.answer("پرداخت تایید و کیف پول شارژ شد.", show_alert=True)
    try:
        await query.edit_message_caption(caption=f"✅ پرداخت تایید شد.\nموجودی جدید: {_fmt_toman(wallet['balance'])} تومان", parse_mode="HTML")
    except BadRequest:
        try:
            await query.edit_message_text(f"✅ پرداخت تایید شد.\nموجودی جدید: {_fmt_toman(wallet['balance'])} تومان", parse_mode="HTML")
        except BadRequest:
            pass


def recover_processing_agent_wallet_payments() -> Dict[str, int]:
    """Recover interrupted admin approvals without crediting a wallet twice."""
    result = {"approved": 0, "released": 0, "review": 0, "legacy": 0}
    for payment in agentbot_db.get_processing_wallet_charge_payments():
        payment_id = int(payment.get("id") or 0)
        agent_id = int(payment.get("agent_id") or 0)
        amount = int(payment.get("amount") or 0)
        key = str(payment.get("processing_key") or "").strip()
        if not key:
            result["legacy"] += 1
            continue
        tx = agent_db.get_wallet_transaction_by_key(key)
        if not tx:
            if agentbot_db.finish_payment_processing(payment_id, agent_id, key, "pending"):
                result["released"] += 1
            else:
                result["review"] += 1
            continue
        matches = (
            int(tx.get("agent_id") or 0) == agent_id
            and int(tx.get("amount") or 0) == amount
            and str(tx.get("tx_type") or "") == "charge"
        )
        if matches and agentbot_db.finish_payment_processing(
            payment_id, agent_id, key, "approved"
        ):
            result["approved"] += 1
        else:
            result["review"] += 1
    return result


async def reject_agent_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: int) -> None:
    query = update.callback_query
    payment = agentbot_db.get_payment_by_id(payment_id)
    if not payment:
        await query.answer("پرداخت پیدا نشد.", show_alert=True)
        return
    if str(payment.get("status") or "") != "pending":
        await query.answer("این پرداخت قبلاً بررسی شده است.", show_alert=True)
        return
    agent_id = int(payment.get("agent_id") or 0)
    amount = int(payment.get("amount") or 0)
    if not agentbot_db.set_payment_status(payment_id, agent_id, "rejected", expected_status="pending"):
        await query.answer("این پرداخت قبلاً در حال بررسی یا بررسی شده است.", show_alert=True)
        return
    agent = agent_db.get_agent_by_id(agent_id) or {}
    try:
        token = os.getenv("AGENT_BOT_TOKEN", "").strip()
        agent_tg_id = int(agent.get("telegram_id") or 0)
        if token and agent_tg_id:
            from telegram import Bot
            bot = Bot(token=token)
            await bot.send_message(chat_id=agent_tg_id, text=f"❌ پرداخت شما به مبلغ {_fmt_toman(amount)} تومان رد شد.")
    except Exception as e:
        logger.warning("Failed notifying agent payment rejection: %s", e)
    await query.answer("پرداخت رد شد.", show_alert=True)
    try:
        await query.edit_message_caption(caption="❌ پرداخت رد شد.", parse_mode="HTML")
    except BadRequest:
        try:
            await query.edit_message_text("❌ پرداخت رد شد.", parse_mode="HTML")
        except BadRequest:
            pass


# ===============================
#   تعرفه عمده حجم/زمان
# ===============================
async def send_agent_prices(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    page: int = 1,
) -> None:
    """نمایش تعرفه عمده حجم/زمان نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return

    rates = agent_db.get_wholesale_pricing(agent_id)

    lines = [
        f"💵 <b>تعرفه عمده نماینده</b>\n\n",
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n",
        f"📊 هر گیگ: <b>{_fmt_toman(rates['price_per_gb'])}</b> تومان\n",
        f"⏰ هر ۳۰ روز: <b>{_fmt_toman(rates['price_per_30_days'])}</b> تومان\n\n",
        "وقتی نماینده پرداخت مشتری را تایید می‌کند، این مبلغ از کیف پول نماینده کم می‌شود.\n",
        "کیف پول نماینده تاریخ انقضا ندارد و فقط با سفارش‌های تاییدشده مصرف می‌شود.\n\n",
        "🧮 <b>فرمول کسر</b>\n",
        "<code>حجم سرویس × قیمت هر گیگ + ماه سرویس × قیمت هر ۳۰ روز</code>\n\n",
        "مثال: اگر سرویس ۱۰ گیگ و ۴۵ روز باشد، زمان آن ۲ ماه حساب می‌شود.",
    ]

    kb_rows = [
        [InlineKeyboardButton("⚙️ تنظیم تعرفه عمده", callback_data=f"agency:rates:{agent_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"agency:view:{agent_id}")],
    ]
    kb = InlineKeyboardMarkup(kb_rows)
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def start_wholesale_rates_input(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """شروع ویزارد مرحله‌ای تعرفه عمده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return
    rates = agent_db.get_wholesale_pricing(agent_id)
    context.user_data["state"] = AGENCY_SET_WHOLESALE_GB
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    context.user_data.pop("agency_wholesale_price_per_gb", None)
    context.user_data.pop("agency_wholesale_price_per_30_days", None)
    text = (
        "⚙️ <b>تنظیم تعرفه عمده</b>\n\n"
        "مرحله ۱ از ۳\n\n"
        "قیمت هر گیگ را به تومان وارد کنید.\n"
        "این مبلغ برای هر گیگ سرویس مشتری از کیف پول نماینده کسر می‌شود.\n\n"
        "تعرفه فعلی:\n"
        f"📊 هر گیگ: <b>{_fmt_toman(rates['price_per_gb'])}</b> تومان\n"
        f"⏰ هر ۳۰ روز: <b>{_fmt_toman(rates['price_per_30_days'])}</b> تومان\n\n"
        "مثال: <code>2000</code>"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"agency:view:{agent_id}")]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def handle_wholesale_rates_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """دریافت مرحله‌ای قیمت هر گیگ و هر ۳۰ روز."""
    state = context.user_data.get("state")
    if state not in {AGENCY_SET_WHOLESALE_GB, AGENCY_SET_WHOLESALE_DAYS}:
        return False
    agent_id = int(context.user_data.get(AGENCY_VIEWING_ID_KEY) or 0)
    if agent_id <= 0:
        context.user_data.pop("state", None)
        return False
    fa_digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    raw = (update.message.text or "").strip().translate(fa_digits)
    try:
        value = int(raw.replace(",", ""))
    except ValueError:
        await update.message.reply_text("❌ لطفاً فقط عدد ارسال کنید.")
        return True
    if value < 0:
        await update.message.reply_text("❌ اعداد نباید منفی باشند.")
        return True

    if state == AGENCY_SET_WHOLESALE_GB:
        context.user_data["agency_wholesale_price_per_gb"] = value
        context.user_data["state"] = AGENCY_SET_WHOLESALE_DAYS
        await update.message.reply_text(
            "⚙️ <b>تنظیم تعرفه عمده</b>\n\n"
            "مرحله ۲ از ۳\n\n"
            f"📊 قیمت هر گیگ: <b>{_fmt_toman(value)}</b> تومان\n\n"
            "حالا قیمت هر ۳۰ روز را به تومان وارد کنید.\n"
            "اگر سرویس ۴۵ روزه باشد، زمان آن ۲ ماه حساب می‌شود.\n\n"
            "مثال: <code>10000</code>",
            parse_mode="HTML",
        )
        return True

    context.user_data["agency_wholesale_price_per_30_days"] = value
    price_per_gb = int(context.user_data.get("agency_wholesale_price_per_gb") or 0)
    price_per_30_days = value
    context.user_data["state"] = "agency:confirm_wholesale_rates"
    text = (
        "⚙️ <b>تایید تعرفه عمده</b>\n\n"
        "مرحله ۳ از ۳\n\n"
        f"📊 هر گیگ: <b>{_fmt_toman(price_per_gb)}</b> تومان\n"
        f"⏰ هر ۳۰ روز: <b>{_fmt_toman(price_per_30_days)}</b> تومان\n\n"
        "این تعرفه از سفارش‌های بعدی مشتریان نماینده کسر می‌شود.\n"
        "آیا ذخیره شود؟"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید و ذخیره", callback_data=f"agency:ratesave:{agent_id}")],
        [InlineKeyboardButton("✏️ ویرایش از اول", callback_data=f"agency:rates:{agent_id}")],
        [InlineKeyboardButton("❌ لغو", callback_data=f"agency:view:{agent_id}")],
    ])
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    return True


async def confirm_wholesale_rates(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """ذخیره تعرفه فقط بعد از تایید نهایی ادمین."""
    query = update.callback_query
    price_per_gb = int(context.user_data.get("agency_wholesale_price_per_gb") or 0)
    price_per_30_days = int(context.user_data.get("agency_wholesale_price_per_30_days") or 0)
    rates = agent_db.set_wholesale_pricing(agent_id, price_per_gb, price_per_30_days)
    context.user_data.pop("state", None)
    context.user_data.pop("agency_wholesale_price_per_gb", None)
    context.user_data.pop("agency_wholesale_price_per_30_days", None)
    text = (
        "✅ تعرفه عمده ثبت شد.\n"
        f"📊 هر گیگ: <b>{_fmt_toman(rates['price_per_gb'])}</b> تومان\n"
        f"⏰ هر ۳۰ روز: <b>{_fmt_toman(rates['price_per_30_days'])}</b> تومان"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به نماینده", callback_data=f"agency:view:{agent_id}")]])
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await query.answer("تعرفه ذخیره شد.", show_alert=True)


async def start_add_price_server_select(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """انتخاب سرور برای قیمت‌گذاری جدید."""
    servers = database.get_servers() or []
    if not servers:
        await update.callback_query.answer("هیچ سروری ثبت نشده است.", show_alert=True)
        return

    rows: List[List[Any]] = []
    for s in servers:
        sid = s["id"]
        stitle = _escape(s.get("title")) or f"server #{sid}"
        rows.append([InlineKeyboardButton(
            stitle,
            callback_data=f"agency:pricesrv:{agent_id}:{sid}",
        )])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"agency:prices:{agent_id}:1")])

    kb = InlineKeyboardMarkup(rows)
    text = f"🖥 <b>انتخاب سرور</b>\n\nسروری که می‌خواهید قیمت عمده تعیین کنید را انتخاب کنید:"
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def start_add_price_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    server_id: int,
) -> None:
    """دریافت مشخصات پلن (روز/حجم/قیمت عمده) به‌صورت متن."""
    context.user_data["state"] = AGENCY_SET_WHOLESALE
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    context.user_data["agency_price_server_id"] = server_id

    text = (
        "💵 <b>افزودن قیمت عمده</b>\n\n"
        "مشخصات پلن را به این شکل ارسال کنید:\n\n"
        "<code>روز حجم قیمت_عمده</code>\n\n"
        "مثال: <code>30 50 80000</code>\n"
        "یعنی ۳۰ روز، ۵۰ گیگ، قیمت عمده ۸۰٬۰۰۰ تومان\n\n"
        "(قیمت فروش بعداً توسط خود نماینده تعیین می‌شود)"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"agency:prices:{agent_id}:1")]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def handle_wholesale_price_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """پارس متن قیمت عمده."""
    state = context.user_data.get("state")
    if state != AGENCY_SET_WHOLESALE:
        return False

    agent_id = context.user_data.get(AGENCY_VIEWING_ID_KEY)
    server_id = context.user_data.get("agency_price_server_id")
    if not agent_id or not server_id:
        context.user_data.pop("state", None)
        return False

    fa_digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    raw = (update.message.text or "").strip().translate(fa_digits)
    parts = raw.replace(",", " ").replace("،", " ").split()
    if len(parts) != 3:
        await update.message.reply_text(
            "❌ فرمت نامعتبر. مثال صحیح:\n<code>30 50 80000</code>\n(روز حجم قیمت_عمده)",
            parse_mode="HTML",
        )
        return True

    try:
        days = int(parts[0])
        gb = float(parts[1])
        wholesale = int(parts[2])
    except ValueError:
        await update.message.reply_text("❌ اعداد نامعتبر هستند.")
        return True

    if days <= 0 or gb <= 0 or wholesale < 0:
        await update.message.reply_text("❌ مقادیر باید مثبت باشند.")
        return True

    plan = agent_db.set_agent_plan(
        agent_id=agent_id,
        server_id=server_id,
        days=days,
        gb=gb,
        wholesale_price=wholesale,
        sale_price=0,
        plan_title=f"{days} روز / {gb}GB",
    )

    context.user_data.pop("state", None)
    context.user_data.pop("agency_price_server_id", None)

    await update.message.reply_text(
        f"✅ قیمت عمده ثبت شد!\n"
        f"📦 {days} روز / {gb}GB\n"
        f"🏷 قیمت عمده: <b>{_fmt_toman(wholesale)}</b> تومان\n\n"
        "نماینده باید قیمت فروش را خودش تعیین کند.",
        reply_markup=admin_main_keyboard(),
        parse_mode="HTML",
    )
    await send_agent_prices(update, context, agent_id)
    return True


# ===============================
#   لیست سرویس‌های یک نماینده
# ===============================
async def send_agent_services(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    page: Optional[int] = None,
) -> None:
    """لیست اشتراک‌های نماینده: هدر با آمار کل، فیلتر، مرتب‌سازی، جستجو و
    دکمه تمام‌عرض برای هر اشتراک (۶ عدد در هر صفحه).

    آمار بالای لیست همیشه برای «کل» اشتراک‌های همان نماینده است، نه فقط
    صفحه فعلی. صفحه‌بندی صرفاً از دیتابیس محلی خوانده می‌شود و هیچ درخواست
    پنلی برای سایر اشتراک‌ها ارسال نمی‌کند.
    """
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return

    state = _svc_ui(context, agent_id)
    if page is not None:
        state["page"] = max(1, int(page))

    total_pages = 1
    services: List[Dict[str, Any]] = []
    total = 0
    try:
        services, total = agent_db.list_services_by_agent_sorted(
            agent_id,
            page=state["page"],
            page_size=SERVICES_PAGE_SIZE,
            status_filter=state["filter"],
            sort=state["sort"],
            search=state["query"],
        )
        total_pages = max(1, (total + SERVICES_PAGE_SIZE - 1) // SERVICES_PAGE_SIZE)
        if state["page"] > total_pages:
            # اگر آخرین موردِ آخرین صفحه حذف شده، به صفحه معتبر قبلی برگرد
            state["page"] = total_pages
            services, total = agent_db.list_services_by_agent_sorted(
                agent_id,
                page=state["page"],
                page_size=SERVICES_PAGE_SIZE,
                status_filter=state["filter"],
                sort=state["sort"],
                search=state["query"],
            )
    except Exception:
        logger.exception("list_services_by_agent_sorted failed agent=%s", agent_id)
    _svc_save_ui(context, agent_id, state)
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id

    stats = agent_db.get_agent_services_stats(agent_id)
    agent_name = str(agent.get("full_name") or "").strip() or str(agent.get("username") or "").strip()
    agent_username = str(agent.get("username") or "").strip()

    header_name = _escape(_shorten(agent_name, 24) or f"#{agent_id}")
    if agent_username:
        header_name += f" · @{_escape(agent_username)}"

    lines = [
        "🏢 <b>مدیریت اشتراک‌های نماینده</b>",
        f"👤 {header_name}",
        SEPARATOR,
        f"📦 تعداد کل اشتراک‌ها: <b>{stats.get('total', 0)}</b>",
        f"🟢 فعال: <b>{stats.get('active', 0)}</b>   "
        f"🔴 منقضی: <b>{stats.get('expired', 0)}</b>   "
        f"⏳ نزدیک انقضا: <b>{stats.get('near_expiry', 0)}</b>",
        f"⚪️ غیرفعال (دستی): <b>{stats.get('inactive', 0)}</b>",
        "",
        f"نمایش: {_svc_filter_label(state['filter'])} · مرتب‌سازی: {_svc_sort_label(state['sort'])}",
    ]
    if state["query"]:
        lines.append(f"🔎 جستجو: «{_escape(state['query'])}»")

    if not services:
        if state["query"]:
            lines.append("")
            lines.append("موردی مطابق جستجو پیدا نشد.")
        elif state["filter"] != "all":
            lines.append("")
            lines.append("در این فیلتر اشتراکی نیست.")
        else:
            lines.append("")
            lines.append("هیچ اشتراکی ثبت نشده است.")

    text = "\n".join(lines)

    rows_kb: List[List[Any]] = []
    rows_kb.append([InlineKeyboardButton("🔎 جستجوی اشتراک", callback_data=f"agency:svcsearch:{agent_id}")])
    rows_kb.append([
        InlineKeyboardButton(
            f"{SVC_FILTER_ICONS.get(state['filter'], '🎛')} فیلتر: {SVC_FILTER_LABELS[state['filter']]}",
            callback_data=f"agency:svcfilter:{agent_id}:{state['page']}",
        ),
        InlineKeyboardButton(
            f"{SVC_SORT_ICONS.get(state['sort'], '↕️')} {SVC_SORT_LABELS[state['sort']]}",
            callback_data=f"agency:svcsort:{agent_id}:{state['page']}",
        ),
    ])
    rows_kb.append([
        InlineKeyboardButton("ℹ️ راهنمای وضعیت", callback_data=f"agency:svchelp:{agent_id}"),
    ])

    for svc in services:
        sid = int(svc.get("id") or 0)
        rows_kb.append([InlineKeyboardButton(
            _svc_button_label(svc),
            callback_data=f"agency:svcview:{agent_id}:{sid}:{state['page']}",
        )])

    nav: List[Any] = []
    if state["page"] > 1:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"agency:services:{agent_id}:{state['page'] - 1}"))
    nav.append(InlineKeyboardButton(f"صفحه {state['page']} از {total_pages}", callback_data="agency:noop"))
    if state["page"] < total_pages:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"agency:services:{agent_id}:{state['page'] + 1}"))
    rows_kb.append(nav)

    rows_kb.append([
        InlineKeyboardButton("🔄 تازه‌سازی", callback_data=f"agency:services:{agent_id}:{state['page']}"),
        InlineKeyboardButton("↩️ پروفایل نماینده", callback_data=f"agency:view:{agent_id}"),
    ])

    kb = InlineKeyboardMarkup(rows_kb)
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            await query.answer()
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


def _svc_filter_label(mode: str) -> str:
    return SVC_FILTER_LABELS.get(mode, SVC_FILTER_LABELS["all"])


def _svc_sort_label(mode: str) -> str:
    return SVC_SORT_LABELS.get(mode, SVC_SORT_LABELS["newest"])


async def show_service_ui_help(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """راهنمای معنای وضعیت‌ها و تعریف «نزدیک انقضا»."""
    state = _svc_ui(context, agent_id)
    lines = [
        "ℹ️ <b>راهنمای وضعیت اشتراک‌ها</b>",
        SEPARATOR,
        "🟢 فعال — اشتراک دارای اعتبار",
        "⚪️ غیرفعال — به‌صورت دستی غیرفعال شده (منقضی‌شده نیست)",
        "🔴 منقضی — اعتبار زمانی اشتراک به پایان رسیده",
        "⏳ نزدیک انقضا — اعتبار مثبت با حداکثر ۳ روز باقی‌مانده",
        "",
        f"فیلتر فعلی: <b>{_svc_filter_label(state['filter'])}</b>",
        f"مرتب‌سازی فعلی: <b>{_svc_sort_label(state['sort'])}</b>",
        "",
        "🔎 جستجو در نام اشتراک، شناسه ۷ رقمی و UUID انجام می‌شود.",
        "↕️ مرتب‌سازی «نزدیک‌ترین انقضا» اشتراک بدون تاریخ انقضا را در انتها نشان می‌دهد.",
    ]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔙 بازگشت به لیست", callback_data=f"agency:services:{agent_id}:{state['page']}")
    ]])
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            await query.answer()


# ===============================
#   جزئیات یک سرویس نماینده
# ===============================
def _svc_probe_key(agent_id: int, service_id: int) -> str:
    return f"agency_svc_probe_{agent_id}_{service_id}"


def _svc_get_probe(context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> Optional[Dict[str, Any]]:
    ud = getattr(context, "user_data", None) or {}
    probe = ud.get(_svc_probe_key(agent_id, service_id))
    return probe if isinstance(probe, dict) and probe.get("total") else None


def _svc_store_probe(context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int, data: Dict[str, Any]) -> None:
    ud = getattr(context, "user_data", None)
    if isinstance(ud, dict):
        data = dict(data)
        data["checked_at"] = _now_hms()
        ud[_svc_probe_key(agent_id, service_id)] = data


def _svc_runtime_lines(context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> List[str]:
    """خطوط وضعیت زنده (آخرین اتصال/پنل‌ها/آخرین بررسی) فقط از نتایج واقعی."""
    ud = getattr(context, "user_data", None) or {}
    rt = ud.get(f"agency_svc_rt_{agent_id}_{service_id}")
    lines: List[str] = []
    if isinstance(rt, dict) and rt.get("online"):
        lines.append(f"📶 <b>آخرین اتصال:</b> {_escape(rt.get('online'))}")
    else:
        lines.append(f"📶 <b>آخرین اتصال:</b> {UNKNOWN} (بررسی زنده نشده)")
    probe = _svc_get_probe(context, agent_id, service_id)
    if probe:
        lines.append(
            f"🌐 <b>وضعیت پنل‌ها:</b> {probe.get('up', 0)} از {probe.get('total', 0)} پاسخ‌گو"
        )
        lines.append(f"🕒 <b>آخرین بررسی:</b> {_escape(probe.get('checked_at') or '')}")
    else:
        lines.append(f"🌐 <b>وضعیت پنل‌ها:</b> بررسی نشده")
        lines.append("🕒 <b>آخرین بررسی:</b> —")
    return lines


def _service_detail_text(
    svc: Dict[str, Any],
    agent: Dict[str, Any],
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
) -> str:
    parts = _svc_expiry_parts(svc)
    name = str(svc.get("name") or "").strip() or "بی‌نام"
    code = _svc_code(svc)
    agent_label = (
        str(agent.get("full_name") or "").strip()
        or (f"@{agent.get('username')}" if agent.get("username") else "")
        or f"#{agent_id}"
    )
    usage_cur = float(svc.get("usage_current") or 0)
    usage_lim = float(svc.get("usage_limit") or 0)
    if usage_lim > 0:
        usage_line = f"{_fmt_gb(usage_cur)} از {_fmt_gb(usage_lim)} گیگابایت"
        remaining_gb = max(0.0, usage_lim - usage_cur)
        remaining_line = f"{_fmt_gb(remaining_gb)} گیگابایت"
    else:
        usage_line = f"{_fmt_gb(usage_cur)} گیگابایت (سقف ثبت نشده)"
        remaining_line = UNKNOWN

    if parts["expired"]:
        credit_line = "منقضی شده"
    else:
        end = _svc_parse_end(svc)
        if end is not None:
            remaining = (end - _utcnow()).total_seconds()
            days = int(remaining // 86400)
            hours = int((remaining % 86400) // 3600)
            credit_line = f"{days} روز و {hours} ساعت" if days else f"{hours} ساعت"
        else:
            try:
                raw_days = int(float(svc.get("days_left") or 0))
            except (TypeError, ValueError):
                raw_days = 0
            credit_line = f"حدود {raw_days} روز" if raw_days > 0 else UNKNOWN

    trial_mark = " · 🔥 آزمایشی" if int(svc.get("is_trial", 0) or 0) else ""
    wholesale = int(svc.get("wholesale_price") or 0)
    sale = int(svc.get("sale_price") or 0)

    lines = [
        f"👤 <b>اشتراک {_escape(_shorten(name, 32))}</b>{trial_mark}",
        f"شناسه: <code>{_escape(code) or '—'}</code> · نماینده: {_escape(agent_label)}",
        SEPARATOR,
        f"{parts['icon']} <b>وضعیت:</b> {_svc_status_word(svc)}",
        f"🌐 <b>سرور:</b> {_escape(str(svc.get('server_title') or '—'))}",
        f"📊 <b>مصرف:</b> {usage_line}",
        f"📦 <b>حجم باقی‌مانده:</b> {remaining_line}",
        "",
        f"⏳ <b>اعتبار:</b> {credit_line}",
        f"📅 <b>پایان اعتبار:</b> {_fmt_end_full(svc)}",
        *_svc_runtime_lines(context, agent_id, int(svc.get("id") or 0)),
        "",
        f"💰 <b>عمده:</b> {_fmt_toman(wholesale)} | 💵 <b>فروش:</b> {_fmt_toman(sale)} تومان",
    ]
    return "\n".join(lines)


def _service_detail_kb(agent_id: int, service_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("♻️ تمدید", callback_data=f"agency:svcrenew:{agent_id}:{service_id}"),
                InlineKeyboardButton("📥 دریافت کانفیگ", callback_data=f"agency:svcconfig:{agent_id}:{service_id}"),
            ],
            [
                InlineKeyboardButton("🌐 وضعیت نودها", callback_data=f"agency:svcnodes:{agent_id}:{service_id}"),
                InlineKeyboardButton("💳 سوابق مالی", callback_data=f"agency:svcfin:{agent_id}:{service_id}:1"),
            ],
            [
                InlineKeyboardButton("✏️ نام و یادداشت", callback_data=f"agency:svcedit:{agent_id}:{service_id}"),
                InlineKeyboardButton("⚙️ عملیات بیشتر", callback_data=f"agency:svcmore:{agent_id}:{service_id}"),
            ],
            [
                InlineKeyboardButton("↩️ برگشت به لیست", callback_data=f"agency:svcback:{agent_id}"),
            ],
        ]
    )


async def send_agent_service_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    service_id: int,
    page: Optional[int] = None,
) -> bool:
    """جزئیات اشتراک از داده ذخیره‌شده (بدون تماس پنلی).

    خروجی True اگر سرویس متعلق به همین نماینده بود و نمایش داده شد.
    داده زنده از مسیر «وضعیت نودها» دریافت می‌شود.
    """
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        if query:
            await query.answer("سرویس پیدا نشد.", show_alert=True)
        return False

    agent = agent_db.get_agent_by_id(agent_id) or {}
    state = _svc_ui(context, agent_id)
    if page is not None:
        state["page"] = max(1, int(page))
        _svc_save_ui(context, agent_id, state)

    text = _service_detail_text(svc, agent, context, agent_id)
    kb = _service_detail_kb(agent_id, service_id)
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            await query.answer()
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    return True


# ===============================
#   مدیریت ربات‌های مشتری
# ===============================
async def send_agent_bots(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """نمایش ربات‌های مشتری یک نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return

    bots = agent_db.get_customer_bots(agent_id)
    lines = [
        f"🤖 <b>ربات‌های مشتری نماینده</b>\n\n",
        f"👤 {_escape(agent.get('full_name')) or agent.get('telegram_id')}\n",
        f"📦 تعداد: <b>{len(bots)}</b>\n\n",
    ]
    if not bots:
        lines.append("رباتی ثبت نشده است.\n")
        lines.append("نماینده از ربات AgentBot خودش توکن ربات مشتری را ثبت می‌کند.")
    else:
        for b in bots:
            active = "✅" if int(b.get("is_active", 0)) else "❌"
            uname = b.get("bot_username") or "—"
            lines.append(f"{active} @{_escape(uname)} · 🆔 <code>{b['id']}</code>")

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"agency:view:{agent_id}")],
        ]
    )
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


# ===============================
#   بازنشانی تست رایگان مشتریان یک نماینده
# ===============================
async def show_reset_trial_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """نمایش پیام تأیید برای بازنشانی همه تست‌های رایگان یک نماینده."""
    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.callback_query.answer("نماینده پیدا نشد.", show_alert=True)
        return

    try:
        trial_users = customerbot_db.count_free_trial_users(agent_id)
    except Exception:
        trial_users = 0

    name = _escape(agent.get('full_name')) or str(agent.get('telegram_id'))
    text = (
        f"🔄 <b>بازنشانی تست رایگان</b>\n"
        f"{SEPARATOR}\n"
        f"👤 نماینده: <b>{name}</b>\n"
        f"🔢 شناسه: <code>{agent_id}</code>\n\n"
        f"🧮 تعداد مشتریانی که تست رایگان گرفته‌اند: <b>{trial_users}</b>\n\n"
        f"⚠️ با تأیید، همه این کاربران دوباره مجاز به ساخت سرویس تست رایگان می‌شوند."
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تأیید و بازنشانی", callback_data=f"agency:resettrialdo:{agent_id}"),
                InlineKeyboardButton("❌ انصراف", callback_data=f"agency:view:{agent_id}"),
            ],
        ]
    )
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def do_reset_free_trials(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """اجرای بازنشانی تست‌های رایگان همه مشتریان یک نماینده."""
    try:
        count = customerbot_db.reset_all_free_trials(agent_id)
    except Exception as e:
        logger.exception("Failed to reset free trials for agent %s: %s", agent_id, e)
        await update.callback_query.answer("خطا در بازنشانی. دوباره تلاش کنید.", show_alert=True)
        return

    text = (
        f"✅ <b>بازنشانی انجام شد</b>\n"
        f"{SEPARATOR}\n"
        f"🔢 شناسه نماینده: <code>{agent_id}</code>\n"
        f"🧮 تعداد رکوردهای بازنشانی‌شده: <b>{count}</b>\n\n"
        f"کاربران این نمایندگی دوباره می‌توانند سرویس تست رایگان بسازند."
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔙 جزئیات نماینده", callback_data=f"agency:view:{agent_id}")],
        ]
    )
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


# ===============================
#   توکن ربات نماینده (Agent Bot Token)
# ===============================
def _update_env_file(token: str) -> bool:
    """
    بروزرسانی فایل .env با توکن جدید ربات نماینده.
    خروجی: True اگر موفق باشد.
    نوشتن با Utility مشترک و اتمیک انجام میشود (Shared/secure_io).
    """
    try:
        from pathlib import Path
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if not env_path.exists():
            # اگر فایل .env وجود نداشت، از .env.example بساز
            example_path = Path(__file__).resolve().parents[1] / ".env.example"
            if example_path.exists():
                env_path.write_bytes(example_path.read_bytes())
            else:
                env_path.write_text("", encoding="utf-8")
            secure_io.ensure_private_file(env_path)

        ok = secure_io.atomic_update_env(env_path, {"AGENT_BOT_TOKEN": str(token or "")})
        logger.info("Agent bot token updated in .env")
        return True
    except Exception as e:
        logger.error(
            "Failed to update .env file: %s: %s",
            secure_io.safe_exception_name(e),
            secure_io.redact_sensitive_text(str(e)),
        )
        return False


async def send_agent_token_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش منوی تنظیمات توکن ربات نماینده."""
    settings = userbot_db.get_agent_bot_settings()
    token = settings.get("agent_bot_token", "")

    if token:
        masked = token[:8] + "..." + token[-6:] if len(token) > 14 else "••••••••"
        text = (
            "⚙️ <b>تنظیمات توکن ربات نماینده</b>\n"
            f"{SEPARATOR}\n\n"
            f"🔑 <b>توکن فعلی:</b>\n"
            f"<code>{masked}</code>\n\n"
            f"✅ توکن ثبت شده و آماده استفاده است.\n"
            f"برای تغییر، توکن جدید را ارسال کنید."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✏️ تغییر توکن", callback_data="agency:agenttoken:change")],
                [InlineKeyboardButton("🔄 ریستارت ربات", callback_data="agency:agenttoken:restart")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="agency:root")],
            ]
        )
    else:
        text = (
            "⚙️ <b>تنظیمات توکن ربات نماینده</b>\n"
            f"{SEPARATOR}\n\n"
            "⚠️ <b>هنوز توکنی ثبت نشده!</b>\n\n"
            "برای فعال‌سازی سیستم نمایندگی:\n"
            "۱. به @BotFather بروید\n"
            "۲. یک ربات جدید بسازید\n"
            "۳. توکن را کپی کنید\n"
            "۴. روی دکمه زیر بزنید و توکن را بفرستید"
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("➕ افزودن توکن", callback_data="agency:agenttoken:change")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="agency:root")],
            ]
        )

    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
            except Exception:
                pass
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def start_set_agent_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """شروع ویزارد تنظیم توکن ربات نماینده."""
    context.user_data["state"] = AGENCY_SET_AGENT_TOKEN
    context.user_data.pop("agency_wizard_msg_id", None)

    text = (
        "✏️ <b>تغییر توکن ربات نماینده</b>\n"
        f"{SEPARATOR}\n\n"
        "توکن جدید ربات نماینده را ارسال کنید.\n"
        "⚠️ توکن شما حساس است؛ پس از ارسال، پیام آن به‌صورت خودکار پاک می‌شود.\n\n"
        "💡 <b>راهنما:</b>\n"
        "• به @BotFather بروید\n"
        "• /newbot بزنید و ربات بسازید\n"
        "• توکن را کپی کنید\n\n"
        "فرمت توکن:\n"
        "<code>1234567890:ABCdefGhIJKlmNoPQRsTUVwxyz</code>\n\n"
        "برای لغو دکمه «❌ لغو» پایین صفحه را بزنید."
    )
    await _agency_wizard_step(update, context, text)


def _restart_agent_bot() -> bool:
    """
    ریستارت خودکار AgentBot.
    ابتدا پروسه فعلی رو می‌کشه، بعد دوباره اجرا می‌کنه.
    خروجی: True اگر موفق باشد.
    """
    try:
        root_dir = Path(__file__).resolve().parents[1]
        venv_python = root_dir / "venv" / "bin" / "python"
        agent_main = root_dir / "AgentBot" / "main.py"
        log_file = root_dir / "logs" / "agent.log"

        # پیدا کردن و کشتن پروسه فعلی AgentBot
        try:
            result = subprocess.run(
                ["pgrep", "-f", "AgentBot/main.py"],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                pids = result.stdout.strip().split("\n")
                for pid in pids:
                    try:
                        os.kill(int(pid.strip()), signal.SIGTERM)
                    except (ProcessLookupError, ValueError):
                        pass
                logger.info("Killed old AgentBot processes: %s", pids)
                import time
                time.sleep(2)
        except Exception as e:
            logger.warning("Could not kill old AgentBot: %s", e)

        # اجرای مجدد AgentBot
        cmd = f"cd {root_dir} && {venv_python} {agent_main} >> {log_file} 2>&1 &"
        subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("AgentBot restarted successfully")
        return True
    except Exception as e:
        logger.error("Failed to restart AgentBot: %s", e)
        return False


async def restart_agent_bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ریستارت ربات نماینده از طریق دکمه."""
    query = update.callback_query
    await query.answer()

    msg = await query.edit_message_text(
        "🔄 <b>در حال ریستارت ربات نماینده...</b>",
        parse_mode="HTML",
    )

    success = _restart_agent_bot()

    text = (
        "✅ <b>ربات نماینده با موفقیت ریستارت شد.</b>"
        if success
        else "❌ <b>خطا در ریستارت ربات نماینده.</b>\nلطفاً به صورت دستی ریستارت کنید."
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔙 بازگشت به تنظیمات توکن", callback_data="agency:agenttoken")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="agency:exit")],
        ]
    )
    await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")


async def handle_set_agent_token_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    هندلر پیام متنی در حالت تنظیم توکن ربات نماینده.
    خروجی True اگر پیام مصرف شد.
    """
    state = context.user_data.get("state")
    if state != AGENCY_SET_AGENT_TOKEN:
        return False

    text = (update.message.text or "").strip()

    # توکن داده حساس است: پیام ورودی ادمین و پرامپت قبلی پاک می‌شود
    chat_id = update.effective_chat.id
    prev_id = context.user_data.pop("agency_wizard_msg_id", None)
    try:
        await update.message.delete()
    except Exception:
        pass
    if prev_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=int(prev_id))
        except Exception:
            pass

    def _send(**kwargs):
        return context.bot.send_message(chat_id=chat_id, **kwargs)

    # بررسی فرمت توکن (باید شامل : باشد و حداقل ۳۰ کاراکتر)
    if ":" not in text or len(text) < 30:
        await _send(
            text=(
                "❌ <b>فرمت توکن نامعتبر است!</b>\n"
                f"{SEPARATOR}\n\n"
                "توکن باید به این شکل باشد:\n"
                "<code>1234567890:ABCdefGhIJKlmNoPQRsTUVwxyz</code>\n\n"
                "💡 دوباره توکن را از @BotFather کپی کنید.\n"
                "برای لغو /cancel را بفرستید."
            ),
            parse_mode="HTML",
        )
        return True

    # ذخیره در دیتابیس
    userbot_db.set_agent_bot_settings({"agent_bot_token": text})

    # بروزرسانی فایل .env
    env_updated = _update_env_file(text)

    context.user_data.pop("state", None)

    # ریستارت خودکار AgentBot
    agent_restarted = _restart_agent_bot()

    masked = text[:8] + "..." + text[-6:] if len(text) > 14 else "••••••••"
    msg = (
        "✅ <b>توکن ربات نماینده ذخیره شد!</b>\n"
        f"{SEPARATOR}\n\n"
        f"🔑 توکن: <code>{masked}</code>\n\n"
    )
    if env_updated:
        msg += "📄 فایل .env بروزرسانی شد.\n"
    else:
        msg += "⚠️ خطا در بروزرسانی فایل .env.\n"

    if agent_restarted:
        msg += "🔄 ربات AgentBot ریستارت شد."
    else:
        msg += "⚠️ ریستارت خودکار انجام نشد. لطفاً دستی ریستارت کنید."

    await _send(text=msg, reply_markup=admin_main_keyboard(), parse_mode="HTML")
    return True


# ===============================
#   سرویس جدید / جستجو / فیلتر سرویس‌های نماینده
# ===============================
async def send_agent_svc_add_help(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """راهنمای ساخت سرویس جدید — ساخت از طریق ربات نماینده انجام می‌شود."""
    agent = agent_db.get_agent_by_id(agent_id)
    name = _escape(agent.get('full_name')) if agent else f"#{agent_id}"
    text = (
        "➕ <b>ساخت سرویس جدید</b>\n"
        f"{SEPARATOR}\n\n"
        f"👤 نماینده: <b>{name}</b>\n\n"
        "🛠 ساخت سرویس جدید مستقیماً توسط <b>خودِ نماینده</b> از طریق ربات اختصاصی‌اش "
        "(AgentBot) انجام می‌شود؛ ادمین نیازی به ساخت دستی ندارد.\n\n"
        "🧭 برای نماینده:\n"
        "• وارد AgentBot شود\n"
        "• گزینه «ساخت سرویس» را بزند\n"
        "• سرور، حجم و مدت را انتخاب کند\n\n"
        "✅ پس از ساخت، سرویس به‌صورت خودکار اینجا نمایش داده می‌شود.\n\n"
        "⚙️ برای مدیریت قیمت‌ها از دکمه «قیمت‌گذاری» استفاده کنید."
    )
    state = _svc_ui(context, agent_id)
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💵 قیمت‌گذاری", callback_data=f"agency:prices:{agent_id}:1"),
                InlineKeyboardButton("🔙 بازگشت", callback_data=f"agency:services:{agent_id}:{state['page']}"),
            ]
        ]
    )
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def start_agent_service_search(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    """شروع ویزارد جستجو در اشتراک‌های همان نماینده (نام، شناسه، UUID)."""
    context.user_data["state"] = AGENCY_SVC_SEARCH
    context.user_data[AGENCY_VIEWING_ID_KEY] = agent_id
    text = (
        "🔎 <b>جستجوی اشتراک</b>\n"
        f"{SEPARATOR}\n\n"
        "جستجو فقط داخل اشتراک‌های <b>همین نماینده</b> انجام می‌شود.\n\n"
        "می‌توانید بنویسید:\n"
        "• بخشی از <b>نام</b> اشتراک — مثل <code>vpn</code>\n"
        "• <b>شناسه ۷ رقمی</b> — مثل <code>0421531</code>\n"
        "• بخشی از <b>UUID</b>\n"
        "• <b>شناسه داخلی</b> روی دکمه‌ها — مثل <code>66</code> یا <code>#66</code>\n\n"
        "برای لغو /cancel را بفرستید."
    )
    state = _svc_ui(context, agent_id)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "❌ لغو",
        callback_data=f"agency:services:{agent_id}:{state['page']}",
    )]])
    try:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


async def handle_agent_service_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """پردازش متن جستجو؛ نتیجه در همان رابط لیست (با حفظ فیلتر/مرتب‌سازی) نشان داده می‌شود."""
    text = (update.message.text or "").strip()
    agent_id = int(context.user_data.get(AGENCY_VIEWING_ID_KEY) or 0)
    context.user_data.pop("state", None)
    if agent_id <= 0 or not text:
        await update.message.reply_text("جستجو لغو شد.")
        return True

    agent = agent_db.get_agent_by_id(agent_id)
    if not agent:
        await update.message.reply_text("نماینده پیدا نشد.")
        return True

    state = _svc_ui(context, agent_id)
    state["query"] = text[:64]
    state["page"] = 1
    _svc_save_ui(context, agent_id, state)

    await send_agent_services(update, context, agent_id)
    return True


async def cycle_agent_service_filter(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, page: int = 1) -> None:
    """چرخش فیلتر: همه → فعال → غیرفعال → منقضی → همه."""
    state = _svc_ui(context, agent_id)
    idx = SVC_FILTER_ORDER.index(state["filter"]) if state["filter"] in SVC_FILTER_ORDER else 0
    state["filter"] = SVC_FILTER_ORDER[(idx + 1) % len(SVC_FILTER_ORDER)]
    state["page"] = max(1, int(page or 1))
    _svc_save_ui(context, agent_id, state)
    await send_agent_services(update, context, agent_id)


async def cycle_agent_service_sort(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, page: int = 1) -> None:
    """چرخش مرتب‌سازی: جدیدترین → نزدیک‌ترین انقضا → نام → جدیدترین."""
    state = _svc_ui(context, agent_id)
    idx = SVC_SORT_ORDER.index(state["sort"]) if state["sort"] in SVC_SORT_ORDER else 0
    state["sort"] = SVC_SORT_ORDER[(idx + 1) % len(SVC_SORT_ORDER)]
    state["page"] = max(1, int(page or 1))
    _svc_save_ui(context, agent_id, state)
    await send_agent_services(update, context, agent_id)


# ===============================
#   زیرنماهای جزئیات اشتراک (نودها / کانفیگ / مالی / عملیات)
# ===============================
async def show_service_nodes_status(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> None:
    """بررسی واقعی پاسخ‌گویی پنل‌های این اشتراک (دارای timeout و مدیریت خطا)."""
    import asyncio as _asyncio
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return

    from Shared.sub_links import get_service_panel_targets
    try:
        targets = get_service_panel_targets(svc)
    except Exception:
        targets = []
    if not targets:
        await query.answer("نودی برای بررسی وجود ندارد.", show_alert=True)
        return

    await query.answer("در حال بررسی نودها…")

    from Shared import hiddify_api

    async def _probe(server: Dict[str, Any], uuid: str) -> bool:
        try:
            await _asyncio.wait_for(
                hiddify_api.get_user_by_uuid(server, uuid),
                timeout=8.0,
            )
            return True
        except Exception:
            return False

    results = await _asyncio.gather(*[_probe(srv, str(uuid or "")) for srv, uuid, _m in targets])
    total = len(targets)
    up = int(sum(1 for ok in results if ok))
    _svc_store_probe(context, agent_id, service_id, {"total": total, "up": up})

    lines = [
        f"🌐 <b>وضعیت نودهای اشتراک #{service_id}</b>",
        SEPARATOR,
    ]
    for (srv, uuid, _m), ok in zip(targets, results):
        title = _shorten(str((srv or {}).get("title") or f"سرور #{(srv or {}).get('id') or '?'}"), 28)
        lines.append(f"{'🟢' if ok else '🔴'} {_escape(title)}")
    if up == 0:
        lines.append("")
        lines.append("⚠️ هیچ پنلی پاسخ نداد؛ ممکن است خطای شبکه/اعتبار باشد.")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 بررسی مجدد", callback_data=f"agency:svcnodes:{agent_id}:{service_id}"),
        InlineKeyboardButton("🔙 جزئیات", callback_data=f"agency:svcview:{agent_id}:{service_id}:{_svc_ui(context, agent_id)['page']}"),
    ]])
    try:
        await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await query.answer()


async def send_service_configs(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> None:
    """دریافت کانفیگ‌های واقعی اشتراک از پنل‌ها (بازاستفاده از منطق موجود نمایندگی)."""
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return

    await query.answer("در حال دریافت کانفیگ‌ها…")

    try:
        import asyncio as _asyncio
        from AgentBot.services.subscription_service import get_configs
        cfgs = await _asyncio.wait_for(get_configs(agent_id, service_id), timeout=20.0)
    except Exception as e:
        logger.warning("svcconfig failed svc=%s: %s", service_id, type(e).__name__)
        cfgs = []

    lines = [f"📥 <b>کانفیگ‌های اشتراک #{service_id}</b>", SEPARATOR]
    links: List[str] = []
    for item in cfgs or []:
        link = item if isinstance(item, str) else str((item or {}).get("link") or "").strip()
        if link and link not in links:
            links.append(link)

    if not links:
        lines.append("در این لحظه کانفیگی دریافت نشد.")
        lines.append("ممکن است پنل در دسترس نباشد؛ بعداً دوباره تلاش کنید.")
        ok = False
    else:
        ok = True
        shown = links[:4]
        for ln in shown:
            lines.append(f"🔗 <code>{_escape(ln[:180])}</code>")
        if len(links) > len(shown):
            lines.append(f"… و {len(links) - len(shown)} کانفیگ دیگر")

    back_cb = f"agency:svcview:{agent_id}:{service_id}:{_svc_ui(context, agent_id)['page']}"
    rows = [[InlineKeyboardButton("🔄 دریافت مجدد", callback_data=f"agency:svcconfig:{agent_id}:{service_id}")]]
    if ok:
        from Shared.sub_links import get_or_create_bot_sub_links
        try:
            sub_link, _b64 = get_or_create_bot_sub_links(svc)
        except Exception:
            sub_link = ""
        if sub_link:
            lines.append("")
            lines.append(f"🌐 لینک اشتراک:\n<code>{_escape(sub_link[:200])}</code>")
    rows.append([InlineKeyboardButton("🔙 جزئیات", callback_data=back_cb)])

    try:
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML", disable_web_page_preview=True)
    except BadRequest:
        await query.answer()


async def send_service_finance(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int, page: int = 1) -> None:
    """سوابق مالی فقط از تراکنش‌هایی که صریحاً service_id همین اشتراک دارند."""
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return

    code = _svc_code(svc)
    try:
        txs = agent_db.get_service_transactions(service_id, agent_id, limit=50)
    except Exception:
        txs = []

    page = max(1, int(page or 1))
    page_size = 6
    total = len(txs)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
    chunk = txs[(page - 1) * page_size: page * page_size]

    type_icons = {"charge": "➕", "purchase": "🛒", "refund": "↩️"}
    type_names = {"charge": "شارژ", "purchase": "خرید/تمدید", "refund": "بازگشت وجه"}

    lines = [
        f"💳 <b>سوابق مالی اشتراک #{service_id}</b>",
        f"شناسه: <code>{_escape(code) or '—'}</code>",
        SEPARATOR,
        f"مجموع تراکنش‌های مرتبط: <b>{total}</b>",
    ]
    if not chunk:
        lines.append("")
        lines.append("تراکنشی با اتصال مستقیم به این اشتراک ثبت نشده است.")
        lines.append("(تراکنش‌های کلی نماینده به این اشتراک نسبت داده نمی‌شود.)")
    else:
        for tx in chunk:
            ttype = str(tx.get("tx_type") or "")
            amount = int(tx.get("amount") or 0)
            icon = type_icons.get(ttype, "•")
            tname = type_names.get(ttype, ttype or UNKNOWN)
            created = str(tx.get("created_at") or "").strip() or UNKNOWN
            desc = _shorten(tx.get("description"), 36)
            lines.append(
                f"{icon} {tname} · {_fmt_toman(amount)} تومان\n"
                f"   {created}" + (f" · {_escape(desc)}" if desc else "")
            )

    nav: List[Any] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"agency:svcfin:{agent_id}:{service_id}:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="agency:noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"agency:svcfin:{agent_id}:{service_id}:{page + 1}"))
    rows: List[List[Any]] = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(
        "🔙 جزئیات",
        callback_data=f"agency:svcview:{agent_id}:{service_id}:{_svc_ui(context, agent_id)['page']}",
    )])
    try:
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")
    except BadRequest:
        await query.answer()


def _service_more_kb(agent_id: int, service_id: int, page: int = 1) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ ویرایش نام/یادداشت", callback_data=f"agency:svcedit:{agent_id}:{service_id}"),
            ],
            [
                InlineKeyboardButton("🔁 فعال/غیرفعال", callback_data=f"agency:svctoggle:{agent_id}:{service_id}"),
                InlineKeyboardButton("🔗 تعویض لینک", callback_data=f"agency:svcrelink:{agent_id}:{service_id}"),
            ],
            [InlineKeyboardButton("🗑 حذف اشتراک", callback_data=f"agency:svcdelete:{agent_id}:{service_id}")],
            [InlineKeyboardButton("🔙 جزئیات", callback_data=f"agency:svcview:{agent_id}:{service_id}:{max(1, int(page or 1))}")],
        ]
    )


async def show_service_more(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> None:
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await update.callback_query.answer("سرویس پیدا نشد.", show_alert=True)
        return
    code = _svc_code(svc)
    name = _shorten(svc.get("name"), 24) or "بی‌نام"
    state = _svc_ui(context, agent_id)
    lines = [
        f"⚙️ <b>عملیات اشتراک</b>",
        SEPARATOR,
        f"👤 {_escape(name)} · شناسه: <code>{_escape(code) or '—'}</code>",
        "",
        "⚠️ عملیات‌های حساس (حذف، فعال/غیرفعال، تعویض لینک) از همین منو انجام می‌شود.",
    ]
    try:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=_service_more_kb(agent_id, service_id, state["page"]), parse_mode="HTML")
    except BadRequest:
        await update.callback_query.answer()


def _svc_delete_targets_text(svc: Dict[str, Any]) -> str:
    """مقصدهای شناخته‌شده حذف از داده ذخیره‌شده: سرور اصلی + نگاشت نودها.

    این همان چیزی است که delete_subscription (از طریق get_service_panel_targets)
    واقعاً حذف می‌کند؛ هیچ تماس پنلی برای این نمایش انجام نمی‌شود.
    """
    titles: List[str] = []
    seen_ids: set = set()
    try:
        from Shared import database as _shared_db
        primary_sid = int(svc.get("server_id") or 0)
        if primary_sid > 0:
            primary = _shared_db.get_server_by_id(primary_sid)
            if primary:
                seen_ids.add(primary_sid)
                titles.append(str(primary.get("title") or f"سرور #{primary_sid}"))
        for m in agent_db.get_service_nodes(int(svc.get("id") or 0)):
            try:
                msid = int(m.get("server_id") or 0)
            except (TypeError, ValueError):
                msid = 0
            if msid <= 0 or msid in seen_ids:
                continue
            seen_ids.add(msid)
            srv = _shared_db.get_server_by_id(msid)
            title = str(srv.get("title") or m.get("server_title") or f"سرور #{msid}") if srv else str(m.get("server_title") or f"سرور #{msid}")
            titles.append(title)
    except Exception:
        logger.warning("svc delete targets lookup failed svc=%s", svc.get("id"))
    if not titles:
        return "مقصدهای پنل از داده ذخیره‌شده قابل تشخیص نیست."
    shown = titles[:8]
    lines = [f"🖥 {_escape(_shorten(t, 32))}" for t in shown]
    if len(titles) > len(shown):
        lines.append(f"… و {len(titles) - len(shown)} مقصد دیگر")
    return "\n".join(lines)


async def show_service_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> None:
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return
    code = _svc_code(svc)
    name = str(svc.get("name") or "").strip() or "بی‌نام"
    text = (
        "🗑 <b>حذف اشتراک</b>\n"
        f"{SEPARATOR}\n"
        f"👤 نام: <b>{_escape(name)}</b>\n"
        f"🆔 شناسه اشتراک: <code>{_escape(code) or service_id}</code>\n\n"
        "مقصدهای حذف (از داده ذخیره‌شده):\n"
        f"{_svc_delete_targets_text(svc)}\n\n"
        "❗️ اشتراک روی پنل‌های بالا حذف و سپس از دیتابیس پاک می‌شود.\n"
        "این عملیات بازگشت‌پذیر نیست.\n\n"
        "پس از حذف موفق به لیست اشتراک‌ها (با حفظ صفحه/فیلتر/جستجو) برمی‌گردید."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ حذف قطعی", callback_data=f"agency:svcdeleteok:{agent_id}:{service_id}")],
        [InlineKeyboardButton("❌ انصراف", callback_data=f"agency:svcmore:{agent_id}:{service_id}")],
    ])
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await query.answer()


async def do_service_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> None:
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return

    try:
        import asyncio as _asyncio
        from AgentBot.services.subscription_service import delete_subscription
        ok = await _asyncio.wait_for(delete_subscription(agent_id, service_id), timeout=45.0)
    except Exception as e:
        logger.warning("svcdeleteok failed svc=%s: %s", service_id, type(e).__name__)
        ok = False

    state = _svc_ui(context, agent_id)
    if ok:
        total = agent_db.list_services_by_agent_sorted(agent_id, page=1, page_size=1, status_filter=state["filter"], sort=state["sort"], search=state["query"])[1]
        total_pages = max(1, (total + SERVICES_PAGE_SIZE - 1) // SERVICES_PAGE_SIZE)
        if state["page"] > total_pages:
            state["page"] = total_pages
        _svc_save_ui(context, agent_id, state)
        await query.answer("✅ اشتراک حذف شد.")
        await send_agent_services(update, context, agent_id)
    else:
        await query.answer("❌ حذف کامل نشد؛ سرویس حفظ شد تا دوباره تلاش شود.", show_alert=True)


async def do_service_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> None:
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return
    going_active = not bool(int(svc.get("is_active", 0) or 0))
    try:
        import asyncio as _asyncio
        from AgentBot.services import subscription_service as _ss
        if going_active:
            ok = await _asyncio.wait_for(
                _ss.enable_subscription(agent_id, service_id), timeout=15.0)
        else:
            ok = await _asyncio.wait_for(
                _ss.disable_subscription(agent_id, service_id), timeout=15.0)
    except Exception as e:
        logger.warning("svctoggle failed svc=%s: %s", service_id, type(e).__name__)
        ok = False

    if ok:
        await query.answer("✅ فعال شد." if going_active else "⚪️ غیرفعال شد.")
        state = _svc_ui(context, agent_id)
        await send_agent_service_detail(update, context, agent_id, service_id, page=state["page"])
    else:
        await query.answer("❌ تغییر وضعیت انجام نشد.", show_alert=True)


async def do_service_relink(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> None:
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return
    try:
        import asyncio as _asyncio
        from AgentBot.services.subscription_service import change_subscription_link
        updated = await _asyncio.wait_for(
            change_subscription_link(agent_id, service_id), timeout=20.0)
    except Exception as e:
        logger.warning("svcrelink failed svc=%s: %s", service_id, type(e).__name__)
        updated = None

    if updated:
        await query.answer("✅ لینک اشتراک تعویض شد.")
        state = _svc_ui(context, agent_id)
        await send_agent_service_detail(update, context, agent_id, service_id, page=state["page"])
    else:
        await query.answer("❌ تعویض لینک انجام نشد.", show_alert=True)


async def start_service_note_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> None:
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return
    context.user_data["state"] = AGENCY_SVC_EDITNOTE
    context.user_data[AGENCY_SVC_EDIT_TARGET] = {"agent_id": agent_id, "service_id": service_id}
    name = _shorten(svc.get("name"), 24) or "بی‌نام"
    current_note = _svc_note_text(svc) or "—"
    text = (
        "✏️ <b>ویرایش نام و یادداشت</b>\n"
        f"{SEPARATOR}\n"
        f"👤 اشتراک: <b>{_escape(name)}</b>\n"
        f"📝 یادداشت فعلی: {_escape(current_note)}\n\n"
        "مرحله ۱ از ۲: نام جدید اشتراک را بفرستید.\n"
        "برای حفظ نام فعلی، دکمه «⏭ بدون تغییر» را بزنید.\n\n"
        "همچنین می‌توانید نام و یادداشت را یکجا بفرستید:\n"
        "<code>نام | یادداشت</code>\n"
        "مثال: <code>علی | کانکشن پرسرعت</code>"
    )
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton(SVC_EDIT_KEEP)], [KeyboardButton("❌ لغو")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    try:
        await query.edit_message_text(text, parse_mode="HTML")
    except BadRequest:
        await query.answer()
    await query.message.reply_text(
        "نام جدید را ارسال کنید:",
        reply_markup=kb,
    )


async def _save_service_name_note(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    svc: Dict[str, Any],
    agent_id: int,
    service_id: int,
    new_name: str,
    new_note: str,
) -> None:
    """نام و یادداشت را مستقل ذخیره و نتیجه واقعی هر بخش را گزارش می‌کند."""
    old_name = str(svc.get("name") or "").strip()
    old_comment = str(svc.get("comment") or "")
    changed: List[str] = []
    errors: List[str] = []

    if new_name != old_name:
        try:
            import asyncio as _asyncio
            from AgentBot.services.subscription_service import rename_service_on_panels
            ok, msg = await _asyncio.wait_for(
                rename_service_on_panels(agent_id, service_id, new_name), timeout=30.0)
            if ok:
                changed.append("نام")
            else:
                errors.append(f"نام تغییر نکرد: {msg}")
        except Exception as e:
            logger.warning("svc rename failed svc=%s: %s", service_id, type(e).__name__)
            errors.append("نام روی پنل‌ها تغییر نکرد (مهلت/خطا).")

    new_comment = _svc_rebuild_comment(old_comment, new_note)
    if new_comment != old_comment:
        if agent_db.update_service(service_id, {"comment": new_comment}):
            changed.append("یادداشت")
        else:
            errors.append("یادداشت در دیتابیس ذخیره نشد.")

    context.user_data.pop("state", None)
    context.user_data.pop(AGENCY_SVC_EDIT_TARGET, None)

    if errors:
        prefix = "⚠️ بخشی از تغییرات ذخیره شد." if changed else "❌ تغییری ذخیره نشد."
        result = prefix + "\n" + "\n".join(f"• {item}" for item in errors)
    elif changed:
        result = "✅ " + " و ".join(changed) + " با موفقیت ذخیره شد."
    else:
        result = "ℹ️ تغییری انجام نشد."

    await update.message.reply_text(result, reply_markup=ReplyKeyboardRemove())
    await send_agent_service_detail(update, context, agent_id, service_id)


async def handle_service_note_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    raw = (update.message.text or "").strip()
    target = context.user_data.get(AGENCY_SVC_EDIT_TARGET) or {}
    agent_id = int(target.get("agent_id") or 0)
    service_id = int(target.get("service_id") or 0)
    state = context.user_data.get("state")

    if agent_id <= 0 or service_id <= 0:
        context.user_data.pop("state", None)
        context.user_data.pop(AGENCY_SVC_EDIT_TARGET, None)
        await update.message.reply_text("ویرایش لغو شد.")
        return True
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        context.user_data.pop("state", None)
        context.user_data.pop(AGENCY_SVC_EDIT_TARGET, None)
        await update.message.reply_text("سرویس پیدا نشد؛ ویرایش لغو شد.")
        return True

    current_name = str(svc.get("name") or "").strip()
    current_note = _svc_note_text(svc)

    if state == AGENCY_SVC_EDITNOTE:
        # قالب یک‌مرحله‌ای قدیمی همچنان پشتیبانی می‌شود.
        if "|" in raw:
            name_part, new_note = [p.strip() for p in raw.split("|", 1)]
            new_name = name_part or current_name
            if len(new_name) < 3 or len(new_name) > 64:
                await update.message.reply_text("❌ نام باید بین ۳ تا ۶۴ کاراکتر باشد.")
                return True
            if len(new_note) > 120:
                await update.message.reply_text("❌ یادداشت حداکثر ۱۲۰ کاراکتر است.")
                return True
            await _save_service_name_note(
                update, context, svc, agent_id, service_id, new_name, new_note)
            return True

        new_name = current_name if raw == SVC_EDIT_KEEP else raw
        if len(new_name) < 3 or len(new_name) > 64:
            await update.message.reply_text(
                "❌ نام باید بین ۳ تا ۶۴ کاراکتر باشد. دوباره ارسال کنید."
            )
            return True

        target["new_name"] = new_name
        context.user_data[AGENCY_SVC_EDIT_TARGET] = target
        context.user_data["state"] = AGENCY_SVC_EDITNOTE_VALUE
        await update.message.reply_text(
            "مرحله ۲ از ۲: یادداشت جدید را ارسال کنید.\n"
            "برای حفظ یا حذف یادداشت فعلی، یکی از دکمه‌ها را بزنید.",
            reply_markup=ReplyKeyboardMarkup(
                [
                    [KeyboardButton(SVC_EDIT_KEEP)],
                    [KeyboardButton(SVC_EDIT_CLEAR_NOTE)],
                    [KeyboardButton("❌ لغو")],
                ],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return True

    new_name = str(target.get("new_name") or current_name).strip()
    if raw == SVC_EDIT_KEEP:
        new_note = current_note
    elif raw == SVC_EDIT_CLEAR_NOTE:
        new_note = ""
    else:
        new_note = raw
    if len(new_note) > 120:
        await update.message.reply_text(
            "❌ یادداشت حداکثر ۱۲۰ کاراکتر است. دوباره ارسال کنید."
        )
        return True
    await _save_service_name_note(
        update, context, svc, agent_id, service_id, new_name, new_note)
    return True


async def refresh_service_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> None:
    """تازه‌سازی شبکه‌ای محدود: وضعیت پنل‌ها + آخرین اتصال (همان منطق AgentBot)."""
    import asyncio as _asyncio
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return

    await query.answer("در حال دریافت اطلاعات از پنل…")

    from Shared.sub_links import get_service_panel_targets
    from Shared import hiddify_api

    try:
        targets = get_service_panel_targets(svc)
    except Exception:
        targets = []

    sem = _asyncio.Semaphore(4)

    async def _fetch(target):
        server, uuid, _m = target
        async with sem:
            try:
                user = await _asyncio.wait_for(
                    hiddify_api.get_user_by_uuid(server, str(uuid or "")),
                    timeout=8.0,
                )
                return target, user if isinstance(user, dict) and user else None
            except Exception as exc:
                logger.warning(
                    "admin svc refresh failed svc=%s server=%s: %s",
                    service_id, (server or {}).get("id"), type(exc).__name__,
                )
                return target, None

    fetched = await _asyncio.gather(*[_fetch(t) for t in targets]) if targets else []
    available = [(t, u) for t, u in fetched if u]
    up = len(available)
    total = len(targets)

    if available:
        primary_id = int(svc.get("server_id") or 0)
        authoritative = next(
            (u for (srv, _u, _n), u in available if int((srv or {}).get("id") or 0) == primary_id),
            available[0][1],
        )
        from datetime import timezone
        now = datetime.now(timezone.utc)

        # مصرف/حجم از همه نودهای پاسخ‌گو (مثل منطق نمایندگی)
        usage_values = []
        for _t, u in available:
            try:
                usage_values.append(float(u.get("current_usage_GB") or 0))
            except (TypeError, ValueError):
                pass
        updates: Dict[str, Any] = {}
        if usage_values and up == total:
            updates["usage_current"] = sum(usage_values)
        try:
            if authoritative.get("usage_limit_GB") is not None:
                updates["usage_limit"] = float(authoritative.get("usage_limit_GB") or 0)
        except (TypeError, ValueError):
            pass
        end = _panel_expiry_datetime(authoritative, now)
        if end is not None:
            remaining = (end - now).total_seconds()
            updates["end_date"] = end.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
            import math as _math
            updates["days_left"] = _math.ceil(remaining / 86400) if remaining >= 0 else _math.floor(remaining / 86400)
        active_raw = authoritative.get("is_active")
        if active_raw is not None:
            if isinstance(active_raw, str):
                updates["is_active"] = 0 if active_raw.strip().lower() in {"0", "false", "off", "inactive", "disabled"} else 1
            else:
                updates["is_active"] = 1 if bool(active_raw) else 0
        if updates:
            try:
                agent_db.update_service(service_id, updates)
            except Exception:
                pass

        # آخرین اتصال از تازه‌ترین last_online بین نودها
        latest = None
        for _t, u in available:
            dt = _parse_panel_dt(u.get("last_online"))
            if dt is not None and (latest is None or dt > latest):
                latest = dt
        online_label = UNKNOWN
        if latest is not None:
            seconds = (now - latest).total_seconds()
            if -120 <= seconds <= 900:
                online_label = "آنلاین"
            else:
                try:
                    from AgentBot.services.subscription_service import _human_duration
                    online_label = _human_duration(seconds)
                except Exception:
                    online_label = "مدتی پیش"
        ud = getattr(context, "user_data", None)
        if isinstance(ud, dict):
            ud[f"agency_svc_rt_{agent_id}_{service_id}"] = {"online": online_label}

    if total:
        _svc_store_probe(context, agent_id, service_id, {"total": total, "up": up})

    state = _svc_ui(context, agent_id)
    await send_agent_service_detail(update, context, agent_id, service_id, page=state["page"])


# ===============================
#   تمدید اشتراک نماینده (اتصال به سرویس معتبر موجود)
# ===============================
async def show_service_renew_plans(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> None:
    """انتخاب پلن تمدید از تعرفه‌های واقعی نماینده برای همان سرور.

    سرویس تمدید: AgentBot.services.subscription_service.renew_subscription
    (کسر کیف پول، پچ پنل‌ها و rollback همان‌جاست؛ اینجا فقط رابط است).
    """
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return

    try:
        plans = agent_db.get_agent_plans(agent_id, server_id=int(svc.get("server_id") or 0) or None)
    except Exception:
        plans = []

    if not plans:
        await query.answer("پلن معتبری برای تمدید تعریف نشده است.", show_alert=True)
        return

    code = _svc_code(svc)
    name = _shorten(svc.get("name"), 24) or "بی‌نام"
    lines = [
        "♻️ <b>تمدید اشتراک</b>",
        SEPARATOR,
        f"👤 {_escape(name)} · شناسه: <code>{_escape(code) or '—'}</code>",
        "",
        "پلن مورد نظر را انتخاب کنید:",
    ]
    rows: List[List[Any]] = []
    for plan in plans[:8]:
        label = (
            f"{int(plan.get('days') or 0)} روز · {_fmt_gb(float(plan.get('gb') or 0))}GB · "
            f"{_fmt_toman(int(plan.get('wholesale_price') or 0))} تومان"
        )
        rows.append([InlineKeyboardButton(
            label,
            callback_data=f"agency:svcrenewplan:{agent_id}:{service_id}:{int(plan.get('id') or 0)}",
        )])
    if len(plans) > 8:
        lines.append("")
        lines.append(f"… {len(plans) - 8} پلن دیگر (فقط ۸ پلن اول نمایش داده می‌شود)")
    state = _svc_ui(context, agent_id)
    rows.append([InlineKeyboardButton(
        "🔙 جزئیات",
        callback_data=f"agency:svcview:{agent_id}:{service_id}:{state['page']}",
    )])
    try:
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")
    except BadRequest:
        await query.answer()


async def confirm_service_renew(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int, plan_id: int) -> None:
    """تأیید تمدید: نمایش دقیق اثر پلن انتخابی بر سرویس و کیف پول نماینده."""
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return

    try:
        plans = agent_db.get_agent_plans(agent_id, server_id=int(svc.get("server_id") or 0) or None)
    except Exception:
        plans = []
    plan = next((p for p in plans if int(p.get("id") or 0) == int(plan_id)), None)
    if not plan:
        await query.answer("پلن پیدا نشد یا برای این سرور معتبر نیست.", show_alert=True)
        return

    days = int(plan.get("days") or 0)
    gb = float(plan.get("gb") or 0)
    wholesale = int(plan.get("wholesale_price") or 0)
    balance = agent_db.get_wallet_balance(agent_id)
    code = _svc_code(svc)
    name = _shorten(svc.get("name"), 24) or "بی‌نام"

    warn = ""
    if wholesale > balance:
        warn = "\n⚠️ موجودی کیف پول نماینده کمتر از قیمت عمده است؛ تمدید انجام نخواهد شد."

    lines = [
        "♻️ <b>تأیید تمدید اشتراک</b>",
        SEPARATOR,
        f"👤 {_escape(name)} · شناسه: <code>{_escape(code) or '—'}</code>",
        f"📦 حجم جدید: <b>{_fmt_gb(gb)}GB</b>",
        f"⏳ مدت جدید: <b>{days} روز</b>",
        f"💰 قیمت عمده: <b>{_fmt_toman(wholesale)}</b> تومان",
        f"💳 موجودی کیف پول نماینده: <b>{_fmt_toman(balance)}</b> تومان",
        "📉 اثر: این مبلغ از کیف پول نماینده کسر می‌شود؛ حجم و زمان اشتراک با مقادیر جدید جایگزین می‌شود.",
        warn,
        "",
        "تمدید را تأیید می‌کنید؟",
    ]
    state = _svc_ui(context, agent_id)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأیید تمدید", callback_data=f"agency:svcrenewdo:{agent_id}:{service_id}:{int(plan_id)}")],
        [InlineKeyboardButton("❌ انصراف", callback_data=f"agency:svcview:{agent_id}:{service_id}:{state['page']}")],
    ])
    try:
        await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        await query.answer()


async def do_service_renew(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int, plan_id: int) -> None:
    """اجرای تمدید با همان سرویس معتبر موجود (بدون بازنویسی منطق کسر/پنل/rollback)."""
    query = update.callback_query
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0) or 0) != agent_id:
        await query.answer("سرویس پیدا نشد.", show_alert=True)
        return

    try:
        plans = agent_db.get_agent_plans(agent_id, server_id=int(svc.get("server_id") or 0) or None)
    except Exception:
        plans = []
    plan = next((p for p in plans if int(p.get("id") or 0) == int(plan_id)), None)
    if not plan:
        await query.answer("پلن پیدا نشد یا برای این سرور معتبر نیست.", show_alert=True)
        return

    await query.answer("در حال انجام تمدید…")
    try:
        import asyncio as _asyncio
        from AgentBot.services.subscription_service import renew_subscription
        updated = await _asyncio.wait_for(
            renew_subscription(
                agent_id, service_id,
                extra_days=int(plan.get("days") or 0),
                extra_gb=float(plan.get("gb") or 0),
            ),
            timeout=60.0,
        )
    except Exception as e:
        logger.warning("svcrenewdo failed svc=%s: %s", service_id, type(e).__name__)
        updated = None

    state = _svc_ui(context, agent_id)
    if updated:
        # موفقیت: بازگشت به جزئیات با اطلاعات تازه (مصرف/انقضای جدید)
        await query.answer("✅ تمدید انجام شد.")
        await send_agent_service_detail(update, context, agent_id, service_id, page=state["page"])
    else:
        await query.answer(
            "❌ تمدید انجام نشد. دلایل احتمالی: موجودی ناکافی کیف پول نماینده، "
            "در دسترس نبودن سرور اصلی یا نامعتبر بودن سرویس.",
            show_alert=True,
        )


# ===============================
#   هندلر اصلی inline (callback router)
# ===============================
async def handle_agencies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    هندلر مرکزی تمام callback های agency:*.
    در AdminBot/servers.py از این تابع استفاده می‌شود.
    """
    query = update.callback_query
    if not query:
        return

    data = (query.data or "").strip()
    if not data.startswith("agency:"):
        return

    await query.answer()
    parts = data.split(":")

    action = parts[1] if len(parts) > 1 else ""

    # کنترل ADMIN_ID برای همه مسیرهای این ماژول
    if not _svc_is_admin(update):
        await query.answer("⛔️ دسترسی مجاز نیست.", show_alert=True)
        return

    if action == "noop":
        return

    if action == "root":
        await handle_agencies_entry(update, context)
        return

    if action == "exit":
        try:
            await query.message.delete()
        except BadRequest:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="به منوی اصلی بازگشتید.",
            reply_markup=admin_main_keyboard(),
        )
        return

    if action == "stats":
        await send_global_stats(update, context)
        return

    if action == "event" and len(parts) > 2:
        sub = parts[2]
        if sub == "toggle":
            ev = userbot_db.toggle_agency_event_enabled()
            state_txt = "فعال شد ✅ — گزارش‌ها به کانال رویداد ارسال می‌شود." if ev.get("event_channel_enabled") else "غیرفعال شد ❌ — گزارش‌ها به چت ادمین ارسال می‌شود."
            if ev.get("event_channel_enabled") and not str(ev.get("event_channel_id") or "").strip():
                state_txt += "\n⚠️ هنوز کانالی تنظیم نشده است؛ دکمه «تنظیم کانال رویداد» را بزنید."
            await query.answer(state_txt, show_alert=True)
            await handle_agencies_entry(update, context)
            return
        if sub == "set":
            context.user_data["state"] = AGENCY_EVENT_CHANNEL_STATE
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    "📢 تنظیم کانال رویداد نمایندگی\n\n"
                    "یک پیام از کانال مورد نظر را فوروارد کنید\n"
                    "یا آیدی کانال را بفرستید (@channel یا -100...)\n\n"
                    "❗️ ربات ادمین باید در کانال ادمین باشد.\n"
                    "برای لغو: لغو"
                ),
                reply_markup=admin_main_keyboard(),
            )
            return
        if sub == "status":
            ev = userbot_db.get_agency_event_settings()
            status_txt = "✅ فعال" if ev.get("event_channel_enabled") else "❌ غیرفعال"
            channel = str(ev.get("event_channel_id") or "—تنظیم نشده—")
            await query.answer(
                f"وضعیت: {status_txt}\nکانال: {channel}",
                show_alert=True,
            )
            return

    if action == "list":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        await send_agents_list(update, context, page=page)
        return

    if action == "payments":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        await send_pending_agent_payments(update, context, page=page)
        return

    if action == "payview":
        payment_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await show_agent_payment_detail(update, context, payment_id)
        return

    if action == "payok":
        payment_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await approve_agent_payment(update, context, payment_id)
        return

    if action == "payno":
        payment_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await reject_agent_payment(update, context, payment_id)
        return

    if action == "add":
        await start_add_agent(update, context)
        return

    # توکن ربات نماینده
    if action == "agenttoken":
        sub = parts[2] if len(parts) > 2 else ""
        if sub == "change":
            await start_set_agent_token(update, context)
        elif sub == "restart":
            await restart_agent_bot_callback(update, context)
        else:
            await send_agent_token_menu(update, context)
        return

    # اکشن‌هایی که نیاز به agent_id دارند
    if len(parts) < 3:
        return
    agent_id = int(parts[2]) if parts[2].lstrip("-").isdigit() else 0
    if agent_id <= 0:
        return

    if action == "svcback":
        # بازگشت از جزئیات به لیست با حفظ صفحه/فیلتر/ترتیب/جستجو
        context.user_data.pop("state", None)
        await send_agent_services(update, context, agent_id)
        return

    if action == "view":
        await send_agent_detail(update, context, agent_id)
        return

    if action == "charge":
        await start_wallet_charge(update, context, agent_id)
        return

    if action == "wallet":
        await send_agent_wallet(update, context, agent_id)
        return

    if action == "toggle":
        await toggle_agent_active(update, context, agent_id)
        return

    if action == "delete":
        await confirm_delete_agent(update, context, agent_id)
        return

    if action == "dodelete":
        await do_delete_agent(update, context, agent_id)
        return

    if action == "editname":
        await start_edit_name(update, context, agent_id)
        return

    if action == "editphone":
        await start_edit_phone(update, context, agent_id)
        return

    if action == "services":
        context.user_data.pop("state", None)
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
        await send_agent_services(update, context, agent_id, page=page)
        return

    if action == "svcadd":
        context.user_data.pop("state", None)
        await send_agent_svc_add_help(update, context, agent_id)
        return

    if action == "svcsearch":
        await start_agent_service_search(update, context, agent_id)
        return

    if action == "svcfilter":
        context.user_data.pop("state", None)
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        await cycle_agent_service_filter(update, context, agent_id, page=page)
        return

    if action == "svcsort":
        context.user_data.pop("state", None)
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        await cycle_agent_service_sort(update, context, agent_id, page=page)
        return

    if action == "svchelp":
        await show_service_ui_help(update, context, agent_id)
        return

    if action == "svcview":
        context.user_data.pop("state", None)
        context.user_data.pop(AGENCY_SVC_EDIT_TARGET, None)
        service_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None
        if service_id <= 0:
            await query.answer("سرویس نامعتبر.", show_alert=True)
            return
        await send_agent_service_detail(update, context, agent_id, service_id, page=page)
        return

    # --- مسیرهای جزئیات اشتراک (به‌ازای هر سرویس) ---
    if action in {
        "svcconfig", "svcnodes", "svcfin", "svcmore", "svcdelete", "svcdeleteok",
        "svctoggle", "svcrelink", "svcrefresh", "svcedit",
        "svcrenew", "svcrenewplan", "svcrenewdo",
    }:
        context.user_data.pop("state", None)
        service_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        if service_id <= 0:
            await query.answer("سرویس نامعتبر.", show_alert=True)
            return
        if action == "svcconfig":
            await send_service_configs(update, context, agent_id, service_id)
        elif action == "svcnodes":
            await show_service_nodes_status(update, context, agent_id, service_id)
        elif action == "svcfin":
            page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 1
            await send_service_finance(update, context, agent_id, service_id, page=page)
        elif action == "svcmore":
            await show_service_more(update, context, agent_id, service_id)
        elif action == "svcdelete":
            await show_service_delete_confirm(update, context, agent_id, service_id)
        elif action == "svcdeleteok":
            await do_service_delete(update, context, agent_id, service_id)
        elif action == "svctoggle":
            await do_service_toggle(update, context, agent_id, service_id)
        elif action == "svcrelink":
            await do_service_relink(update, context, agent_id, service_id)
        elif action == "svcrefresh":
            await refresh_service_detail(update, context, agent_id, service_id)
        elif action == "svcedit":
            await start_service_note_edit(update, context, agent_id, service_id)
        elif action == "svcrenew":
            await show_service_renew_plans(update, context, agent_id, service_id)
        elif action == "svcrenewplan":
            plan_id = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            if plan_id <= 0:
                await query.answer("پلن نامعتبر.", show_alert=True)
                return
            await confirm_service_renew(update, context, agent_id, service_id, plan_id)
        elif action == "svcrenewdo":
            plan_id = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            if plan_id <= 0:
                await query.answer("پلن نامعتبر.", show_alert=True)
                return
            await do_service_renew(update, context, agent_id, service_id, plan_id)
        return

    if action == "prices":
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        await send_agent_prices(update, context, agent_id, page=page)
        return

    if action == "rates":
        await start_wholesale_rates_input(update, context, agent_id)
        return

    if action == "ratesave":
        await confirm_wholesale_rates(update, context, agent_id)
        return

    if action == "addprice":
        await start_add_price_server_select(update, context, agent_id)
        return

    if action == "pricesrv":
        server_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        if server_id <= 0:
            await query.answer("سرور نامعتبر.", show_alert=True)
            return
        await start_add_price_input(update, context, agent_id, server_id)
        return

    if action == "bots":
        await send_agent_bots(update, context, agent_id)
        return

    if action == "resettrial":
        await show_reset_trial_confirm(update, context, agent_id)
        return

    if action == "resettrialdo":
        await do_reset_free_trials(update, context, agent_id)
        return


# ===============================
#   هندلر پیام‌های متنی agency
# ===============================
async def handle_agencies_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    هندلر پیام‌های متنی مربوط به ویزارد‌های agency.
    خروجی True اگر پیام مصرف شد.
    """
    state = context.user_data.get("state") or ""

    if not state.startswith("agency:"):
        return False

    # لغو (هم با دستور، هم با دکمه «❌ لغو» کیبرد پایین صفحه)
    text = (update.message.text or "").strip()
    if text in {"/cancel", "لغو", "لغو❌", "❌لغو", "❌ لغو"}:
        context.user_data.pop("state", None)
        context.user_data.pop(AGENCY_SVC_EDIT_TARGET, None)
        prompt_id = context.user_data.pop("agency_wizard_msg_id", None)
        chat_id = update.effective_chat.id
        try:
            await update.message.reply_text("عملیات لغو شد.", reply_markup=admin_main_keyboard())
        except Exception:
            pass
        # پاک کردن خود پیام «لغو» و پرامپت مرحله قبلی برای تمیز ماندن چت
        try:
            await update.message.delete()
        except Exception:
            pass
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=int(prompt_id))
            except Exception:
                pass
        return True

    if state in {AGENCY_ADD_TELEGRAM, AGENCY_ADD_PHONE, AGENCY_ADD_NAME}:
        return await handle_add_agent_text(update, context)

    if state == AGENCY_WALLET_CHARGE:
        return await handle_wallet_charge_text(update, context)

    if state == AGENCY_EDIT_PHONE:
        return await handle_edit_phone_text(update, context)

    if state == AGENCY_EDIT_NAME:
        return await handle_edit_name_text(update, context)

    if state == AGENCY_SET_WHOLESALE:
        return await handle_wholesale_price_text(update, context)

    if state in {AGENCY_SET_WHOLESALE_GB, AGENCY_SET_WHOLESALE_DAYS}:
        return await handle_wholesale_rates_text(update, context)

    if state == AGENCY_SVC_SEARCH:
        return await handle_agent_service_search_text(update, context)

    if state in {AGENCY_SVC_EDITNOTE, AGENCY_SVC_EDITNOTE_VALUE}:
        return await handle_service_note_edit_text(update, context)

    if state == AGENCY_EVENT_CHANNEL_STATE:
        context.user_data.pop("state", None)
        channel_target = ""
        channel_title = ""

        # 1) فوروارد پیام از کانال
        try:
            fchat = getattr(update.message, "forward_from_chat", None)
            if fchat and str(getattr(fchat, "type", "")) in {"channel", "supergroup"}:
                channel_target = str(getattr(fchat, "id", "") or "").strip()
                channel_title = str(getattr(fchat, "title", "") or "").strip()
        except Exception:
            pass

        # 2) PTB v20+: forward_origin
        if not channel_target:
            try:
                origin = getattr(update.message, "forward_origin", None)
                ochat = getattr(origin, "chat", None) if origin else None
                if ochat and str(getattr(ochat, "type", "")) in {"channel", "supergroup"}:
                    channel_target = str(getattr(ochat, "id", "") or "").strip()
                    channel_title = str(getattr(ochat, "title", "") or "").strip()
            except Exception:
                pass

        # 3) ورود دستی @channel یا -100...
        if not channel_target:
            t = text.strip()
            if t.startswith("@") and len(t) > 1:
                channel_target = t
            elif t.lstrip("-").isdigit():
                channel_target = t

        if not channel_target:
            await update.message.reply_text(
                "❌ ورودی معتبر نیست.\n"
                "لطفاً یک پیام از کانال فوروارد کنید یا @channel / -100... را بفرستید.",
                reply_markup=admin_main_keyboard(),
            )
            return True

        try:
            userbot_db.set_agency_event_settings({
                "event_channel_id": channel_target,
                "event_channel_enabled": userbot_db.get_agency_event_settings().get("event_channel_enabled", False),
            })
        except Exception as e:
            await update.message.reply_text(f"❌ خطا در ذخیره کانال رویداد:\n{e}", reply_markup=admin_main_keyboard())
            return True

        title_part = f" ({channel_title})" if channel_title else ""
        await update.message.reply_text(
            f"✅ کانال رویداد نمایندگی ذخیره شد:\n{channel_target}{title_part}",
            reply_markup=admin_main_keyboard(),
        )
        return True

    if state == AGENCY_SET_AGENT_TOKEN:
        return await handle_set_agent_token_text(update, context)

    return False
