import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from Shared import agent_db, database as shared_db
from AgentBot.constants import (
    SUBS_CREATE, SUBS_SEARCH, SUBS_EXPIRED, SUBS_DETAIL,
    SUBS_CFG, SUBS_RENEW, SUBS_DISABLE, SUBS_ENABLE, SUBS_DELETE, SUBS_DODELETE,
    SUBS_BACK, MENU_MAIN, UD_STATE, UD_SELECTED_SERVER, UD_SELECTED_PLAN,
    UD_SELECTED_SERVICE, UD_PAGE,
    STATE_CREATE_SERVICE_NAME, STATE_RENEW_DAYS, STATE_RENEW_GB, STATE_SEARCH_SERVICE,
    STATE_SEARCH_NAME, STATE_RENAME_SERVICE,
)
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import (
    subs_menu_keyboard, subs_configs_keyboard, service_detail_keyboard,
    back_keyboard, cancel_keyboard, rename_cancel_keyboard, main_menu_keyboard, BTN_BACK,
    pagination_keyboard,
)
from AgentBot.utils.helpers import _escape, _fmt_toman, _fmt_gb, _normalize_digits
from AgentBot.services.subscription_service import (
    create_subscription, renew_subscription,
    disable_subscription, enable_subscription, delete_subscription,
    change_subscription_link, get_subs_link_settings, get_sub_link_for_type,
    rename_service_on_panels,
)
from AgentBot.database import create_order as db_create_order, get_setting as db_get_setting
from Shared.qr_utils import make_qr_image

logger = logging.getLogger(__name__)

_PAGE_SIZE = 8


def _calc_dynamic_price(agent_id: int, server_id: int, gb: int, months: int):
    settings = db_get_setting(agent_id, "dynamic_plan_settings", {})
    price_per_gb = settings.get("price_per_gb", 0)
    price_per_month = settings.get("price_per_month", 0)
    discount_pct = settings.get("discount_pct", 0)
    wholesale_price = agent_db.calculate_wholesale_price(agent_id, gb, months * 30, server_id)
    sale_price = (gb * price_per_gb) + (months * price_per_month)
    if discount_pct:
        sale_price = int(sale_price * (1 - discount_pct / 100))
    return wholesale_price, sale_price, discount_pct


def _get_wizard_defaults(agent_id: int):
    settings = db_get_setting(agent_id, "dynamic_plan_settings", {})
    min_gb = settings.get("min_gb", 1)
    return min_gb, 1


async def _safe_answer(query, text: str = "", alert: bool = False) -> None:
    """پاسخ امن به callback query؛ اگر قبلاً پاسخ داده شده باشد خطا نمی‌دهد."""
    try:
        await query.answer(text, show_alert=alert)
    except Exception:
        pass


def _link_type_title(link_type: str) -> str:
    titles = {
        "sub_link": "لینک اشتراک",
        "auto_sub": "لینک اشتراک خودکار",
        "sub_b64": "لینک اشتراک b64",
        "multi": "لینک اشتراک هوشمند",
        "multi_b64": "لینک اشتراک هوشمند b64",
    }
    return titles.get(link_type, "لینک اشتراک")


async def _send_sub_link_with_qr(update: Update, context: ContextTypes.DEFAULT_TYPE, svc_id: int, sub_link: str, link_type: str = "") -> None:
    """ارسال لینک همراه با QR (دقیقاً مثل ربات مشتری)."""
    query = update.callback_query
    chat_id = query.message.chat_id if query and query.message else update.effective_chat.id
    svc = agent_db.get_service_by_id(svc_id) or {}
    title = _link_type_title(link_type)
    name_line = f"\U0001f4e6 <b>{_escape(svc.get('name') or 'سرویس')}</b>"
    caption = (
        f"{name_line}\n\n"
        f"\U0001f517 <b>{title}</b>\n"
        "\U0001f4c4 جهت کپی شدن لینک کافیست یک بار لینک زیر را لمس کنید \U0001f447\n\n"
        f"<code>{_escape(sub_link)}</code>"
    )
    if len(caption) > 1000:
        caption = name_line
    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=make_qr_image(sub_link),
            caption=caption,
            parse_mode="HTML",
        )
        return
    except Exception as e:
        logger.warning("send sub link QR failed svc=%s: %s", svc_id, e)
    try:
        link_line = f"\U0001f517 <b>{title}</b>\n<code>{_escape(sub_link)}</code>"
        await context.bot.send_message(
            chat_id=chat_id,
            text=caption if len(caption) <= 1000 else f"{name_line}\n{link_line}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        pass


async def _send_direct_configs(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, svc_id: int) -> None:
    """ارسال کانفیگ‌های مستقیم (مثل ربات مشتری)."""
    query = update.callback_query
    chat_id = query.message.chat_id if query and query.message else update.effective_chat.id
    svc = agent_db.get_service_by_id(svc_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        await _safe_answer(query, "سرویس پیدا نشد.", alert=True)
        return
    from Shared.sub_links import collect_all_direct_configs_for_service
    import asyncio
    try:
        await query.edit_message_text("⏳ در حال استخراج کانفیگ‌های مستقیم... لطفاً صبر کنید.", parse_mode="HTML")
    except Exception:
        pass
    try:
        links = await asyncio.to_thread(collect_all_direct_configs_for_service, svc)
    except Exception:
        links = []
    source_hint = ""
    if not links:
        await _safe_answer(query, "کانفیگ مستقیمی برای این سرویس ایجاد نشد.", alert=True)
        return
    header = "\U0001f512 کانفیگ‌های مستقیم"
    clean_links = [str(x).strip() for x in links if str(x).strip()]
    all_text = "\n".join(clean_links)
    one_block = (
        f"{source_hint}{header}\n"
        "برای کپی، کل باکس زیر را یکجا کپی کنید:\n"
        f"<pre><code class=\"language-shell\">{_escape(all_text)}</code></pre>"
    )
    if len(one_block) <= 3900:
        try:
            await context.bot.send_message(chat_id=chat_id, text=one_block, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass
        return
    max_payload = 2800
    parts_list = []
    cur = []
    cur_len = 0
    for link in clean_links:
        add = len(link) + 1
        if cur and (cur_len + add > max_payload):
            parts_list.append(cur)
            cur = [link]
            cur_len = add
        else:
            cur.append(link)
            cur_len += add
    if cur:
        parts_list.append(cur)
    for idx, chunk in enumerate(parts_list, start=1):
        part_header = header if len(parts_list) == 1 else f"{header} ({idx}/{len(parts_list)})"
        part_text = (
            f"{source_hint if idx == 1 else ''}{part_header}\n"
            "برای کپی، باکس زیر را کپی کنید:\n"
            f"<pre><code class=\"language-shell\">{_escape(chr(10).join(chunk))}</code></pre>"
        )
        try:
            await context.bot.send_message(chat_id=chat_id, text=part_text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass


def _service_detail_text(svc, last_online: str = "هرگز") -> str:
    """متن کارت جزئیات سرویس (بدون قیمت و بدون UUID)."""
    name = _escape(svc.get('name') or 'سرویس')
    server = _escape(svc.get('server_title') or '—')
    gb = _fmt_gb(svc.get('usage_limit', 0))
    days = svc.get('days_left') or svc.get('days') or 0
    used = _fmt_gb(svc.get('usage_current', 0))
    code = agent_db._service_code_from_comment(svc.get("comment") or "")
    note = agent_db._service_note_from_comment(svc.get("comment") or "") or '—'
    online_line = _escape(last_online or 'هرگز')
    return (
        f"\U0001f464 کاربر: <b>{name}</b>\n"
        f"❖⬩╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍⬩❖\n"
        f"⬖ سرور: {server}\n"
        f"\U0001f4ca مصرف: {used} از {gb} گیگابایت\n"
        f"\U0001f4c6 انقضا: {days} روز دیگر\n"
        f"\U0001f4f6 آخرین اتصال: {online_line}\n"
        f"\U0001f4dd یادداشت: {_escape(note)}\n"
        f"\U0001f511 شناسه: <code>{_escape(code or '—')}</code>"
    )


def _service_created_text(svc) -> str:
    """متن کوتاه «ساخته شد» — پیام اول."""
    name = _escape(svc.get('name') or 'سرویس')
    gb = _fmt_gb(svc.get('usage_limit', 0))
    days = svc.get('days_left') or svc.get('days') or 0
    return (
        f"✅ <b>اشتراک با موفقیت ساخته شد!</b>\n\n"
        f"\U0001f464 نام: <b>{name}</b>\n"
        f"\U0001f4ca حجم: {gb} گیگابایت\n"
        f"\U0001f4c5 مدت: {days} روز"
    )


def _service_detail_card_text(svc, note: str = "", last_online: str = "هرگز") -> str:
    """متن جزئیات کامل اکانت — پیام دوم (بالای دکمه‌ها)."""
    name = _escape(svc.get('name') or 'سرویس')
    server = _escape(svc.get('server_title') or '—')
    gb = _fmt_gb(svc.get('usage_limit', 0))
    days = svc.get('days_left') or svc.get('days') or 0
    used = _fmt_gb(svc.get('usage_current', 0))
    code = agent_db._service_code_from_comment(svc.get("comment") or "")
    wholesale = _fmt_toman(svc.get('wholesale_price') or 0)
    sale = _fmt_toman(svc.get('sale_price') or 0)
    note_line = _escape(note or '—')
    online_line = _escape(last_online or 'هرگز')
    return (
        f"\U0001f464 کاربر: <b>{name}</b>\n"
        f"❖⬩╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍⬩❖\n"
        f"⬖ سرور: {server}\n"
        f"\U0001f4ca مصرف: {used} از {gb} گیگابایت\n"
        f"\U0001f4c6 انقضا: {days} روز دیگر\n"
        f"\U0001f4f6 آخرین اتصال: {online_line}\n"
        f"\U0001f4dd یادداشت: {note_line}\n"
        f"\U0001f511 شناسه: <code>{_escape(code or '—')}</code>\n"
        f"💎 عمده: {wholesale} | 💸 فروش: {sale} تومان"
    )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "\U0001f4ca <b>\u0645\u062f\u06cc\u0631\u06cc\u062a \u0627\u0634\u062a\u0631\u0627\u06a9\u200c\u0647\u0627</b>"
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=subs_menu_keyboard(), parse_mode="HTML")
            return
        except Exception:
            pass
        try:
            await update.callback_query.message.reply_text(text, reply_markup=subs_menu_keyboard(), parse_mode="HTML")
        except Exception:
            pass
    else:
        await update.message.reply_text(text, reply_markup=subs_menu_keyboard(), parse_mode="HTML")


async def _send_expired_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1) -> None:
    """جدول کاربران منقضی‌شده: هدر آمار + جدول ۳ ستونه با 🔴 + دکمه حذف همه."""
    agent_id = get_agent_id(context)
    if page < 1:
        page = 1

    items, online_cnt, offline_cnt, expired_cnt = await _fetch_services_with_status(agent_id)
    expired_items = [(s, st) for s, st in items if st == "expired"]
    total = len(expired_items)

    LIST_PAGE_SIZE = 18
    total_pages = max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    if page > total_pages:
        page = total_pages
    page_items = expired_items[(page - 1) * LIST_PAGE_SIZE: page * LIST_PAGE_SIZE]

    from AgentBot.keyboards import _ikb, BTN_BACK
    from Shared.tg_button_styles import inline_button as IButton

    rows = []
    row = []
    for s, st in page_items:
        name = (s.get("name") or "بی‌نام").strip()
        sid = int(s["id"])
        row.append(IButton(f"🔴 |{name}", callback_data=f"agbot:subs:detail:{sid}:expired:{page}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # دکمه حذف همه کاربران منقضی
    if total > 0:
        rows.append([IButton("🗑 حذف همه کاربران منقضی", callback_data="agbot:subs:delexpired")])

    # صفحه‌بندی
    nav = []
    if page < total_pages:
        nav.append(IButton("بعدی ➡️", callback_data=f"agbot:subs:expired:{page + 1}"))
    nav.append(IButton(f"{page}/{total_pages}", callback_data="noop"))
    if page > 1:
        nav.append(IButton("⬅️ قبلی", callback_data=f"agbot:subs:expired:{page - 1}"))
    if nav:
        rows.append(nav)
    rows.append([IButton(BTN_BACK, callback_data="agbot:subs:back")])

    text = (
        f"⚠️ <b>لیست کاربران منقضی شده</b>\n"
        f"# لیست کاربران\n"
        f"شما می‌توانید لیست کاربران و اطلاعات آن‌ها را اینجا مشاهده کنید.\n\n"
        f"👤 تعداد کاربران: {total}\n"
        f"🔵 کاربران آنلاین: {online_cnt}"
    )
    if not expired_items:
        text = "⚠️ <b>لیست کاربران منقضی شده</b>\n\nهیچ کاربر منقضی‌شده‌ای یافت نشد."

    query = update.callback_query
    try:
        await query.edit_message_text(text, reply_markup=_ikb(rows), parse_mode="HTML")
    except Exception:
        pass





def _panel_dt(value) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


def _panel_days_left(u: dict) -> Optional[int]:
    for key in ("expire_date", "end_date", "expires_at", "expiry_date", "expiration_date"):
        dt = _panel_dt(u.get(key))
        if dt:
            return (dt.date() - datetime.now().date()).days
    start = _panel_dt(u.get("start_date"))
    pkg = u.get("package_days")
    try:
        pkg = int(pkg)
    except (TypeError, ValueError):
        pkg = 0
    if start and pkg:
        end = start + timedelta(days=pkg)
        return (end.date() - datetime.now().date()).days
    return None


def _is_user_missing_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return ("http 404" in text) or ("not found" in text) or (" پیدا نشد" in text)


async def _panel_user_status(svc) -> Optional[str]:
    """بررسی وجود سرویس روی سرور و برگرداندن وضعیت: online / offline / expired.
    اگر روی سرور پیدا نشود → None (نمایش داده نمی‌شود) و در probe ثبت می‌شود."""
    try:
        sid = int(svc.get("server_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    uuid = str(svc.get("panel_user_uuid") or "").strip()
    service_id = int(svc.get("id") or 0)
    if sid <= 0 or not uuid:
        return None
    server = shared_db.get_server_by_id(sid)
    if not server:
        return None
    from Shared import hiddify_api
    try:
        u = await hiddify_api.get_user_by_uuid(server, uuid)
    except Exception as e:
        if service_id and _is_user_missing_error(e):
            try:
                agent_db.mark_service_missing(service_id)
            except Exception:
                pass
        return None
    if not isinstance(u, dict) or not u:
        if service_id:
            try:
                agent_db.mark_service_missing(service_id)
            except Exception:
                pass
        return None
    if service_id:
        try:
            agent_db.mark_service_seen(service_id)
        except Exception:
            pass
    # منقضی / غیرفعال
    try:
        is_active = u.get("is_active", True)
    except Exception:
        is_active = True
    if not is_active:
        return "expired"
    days_left = _panel_days_left(u)
    if days_left is not None and days_left <= 0:
        return "expired"
    try:
        limit = float(u.get("usage_limit_GB") or 0)
        used = float(u.get("current_usage_GB") or 0)
        if limit > 0 and used >= limit:
            return "expired"
    except Exception:
        pass
    # آنلاین؟
    lo = _panel_dt(u.get("last_online"))
    if lo:
        try:
            if abs((datetime.now() - lo).total_seconds()) <= 15 * 60:
                return "online"
        except Exception:
            pass
    return "offline"


async def _fetch_services_with_status(agent_id: int):
    """همه اشتراک‌های نماینده (از دیتابیس خودش) + بررسی وجود/وضعیت روی سرور.
    برمی‌گرداند: (items: List[(svc, status)], online, offline, expired)."""
    all_services, _ = agent_db.get_services_by_agent(agent_id, page=1, page_size=1000)
    sem = asyncio.Semaphore(8)

    async def _check(s):
        async with sem:
            return await _panel_user_status(s)

    statuses = await asyncio.gather(*[_check(s) for s in all_services]) if all_services else []
    items = [(s, st) for s, st in zip(all_services, statuses) if st is not None]
    online = sum(1 for _, st in items if st == "online")
    offline = sum(1 for _, st in items if st == "offline")
    expired = sum(1 for _, st in items if st == "expired")
    return items, online, offline, expired


async def _send_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1) -> None:
    """جدول لیست کاربران: فقط از دیتابیس نماینده خوانده و وجود/وضعیت روی سرور چک می‌شود."""
    agent_id = get_agent_id(context)
    if page < 1:
        page = 1

    items, online_cnt, offline_cnt, expired_cnt = await _fetch_services_with_status(agent_id)
    total = len(items)

    LIST_PAGE_SIZE = 18
    total_pages = max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    if page > total_pages:
        page = total_pages
    page_items = items[(page - 1) * LIST_PAGE_SIZE: page * LIST_PAGE_SIZE]

    from AgentBot.keyboards import _ikb, BTN_BACK
    from Shared.tg_button_styles import inline_button as IButton

    STATUS_ICON = {"online": "🔵", "offline": "🟡", "expired": "🔴"}
    rows = []
    row = []
    for s, st in page_items:
        name = (s.get("name") or "بی‌نام").strip()
        sid = int(s["id"])
        row.append(IButton(f"{STATUS_ICON.get(st, '🟡')} {name}", callback_data=f"agbot:subs:detail:{sid}:list:{page}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # صفحه‌بندی (صفحه فعلی وسط، قبلی/بعدی)
    nav = []
    if page < total_pages:
        nav.append(IButton("بعدی ➡️", callback_data=f"agbot:subs:list:{page + 1}"))
    nav.append(IButton(f"{page}/{total_pages}", callback_data="noop"))
    if page > 1:
        nav.append(IButton("⬅️ قبلی", callback_data=f"agbot:subs:list:{page - 1}"))
    if nav:
        rows.append(nav)
    rows.append([IButton(BTN_BACK, callback_data="agbot:subs:back")])

    text = (
        f"📃 <b>لیست کاربران</b>\n"
        f"شما می‌توانید لیست کاربران و اطلاعات آن‌ها را اینجا مشاهده کنید.\n\n"
        f"📄 صفحه: {page}/{total_pages}\n"
        f"👥 تعداد کاربران: {total}\n"
        f"🔵 آنلاین: {online_cnt}\n"
        f"🟡 آفلاین: {offline_cnt}\n"
        f"🔴 منقضی شده: {expired_cnt}"
    )
    if not items:
        text = f"📃 <b>لیست کاربران</b>\n\nهیچ کاربری (روی سرور) یافت نشد."

    query = update.callback_query
    try:
        await query.edit_message_text(text, reply_markup=_ikb(rows), parse_mode="HTML")
    except Exception:
        pass


async def _send_name_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1, use_callback: bool = False) -> None:
    """نمایش نتایج جستجو با نام به صورت جدول (۳ ستونه + صفحه‌بندی)، فقط آن‌هایی که روی سرور موجودند."""
    agent_id = get_agent_id(context)
    if page < 1:
        page = 1
    term = context.user_data.get("subs_search_term") or ""

    services, _ = agent_db.search_services_by_name(agent_id, term, page=1, page_size=1000)
    if not services:
        text = "❌ اشتراکی با این نام یافت نشد."
        kb = None
    else:
        sem = asyncio.Semaphore(8)

        async def _check(s):
            async with sem:
                return await _panel_user_status(s)

        statuses = await asyncio.gather(*[_check(s) for s in services]) if services else []
        items = [(s, st) for s, st in zip(services, statuses) if st is not None]
        if not items:
            text = "❌ اشتراک موردنظر در دیتابیس هست اما روی سرور یافت نشد."
            kb = None
        else:
            total = len(items)
            LIST_PAGE_SIZE = 18
            total_pages = max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
            if page > total_pages:
                page = total_pages
            page_items = items[(page - 1) * LIST_PAGE_SIZE: page * LIST_PAGE_SIZE]

            from AgentBot.keyboards import _ikb, BTN_BACK
            from Shared.tg_button_styles import inline_button as IButton

            STATUS_ICON = {"online": "🔵", "offline": "🟡", "expired": "🔴"}
            rows = []
            row = []
            for s, st in page_items:
                name = (s.get("name") or "بی‌نام").strip()
                sid = int(s["id"])
                row.append(IButton(f"{STATUS_ICON.get(st, '🟡')} {name}", callback_data=f"agbot:subs:detail:{sid}"))
                if len(row) == 3:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)

            nav = []
            if page < total_pages:
                nav.append(IButton("بعدی ➡️", callback_data=f"agbot:subs:namesearch:{page + 1}"))
            nav.append(IButton(f"{page}/{total_pages}", callback_data="noop"))
            if page > 1:
                nav.append(IButton("⬅️ قبلی", callback_data=f"agbot:subs:namesearch:{page - 1}"))
            if nav:
                rows.append(nav)
            rows.append([IButton(BTN_BACK, callback_data="agbot:subs:back")])

            text = (
                f"🔍 <b>نتایج جستجو</b> «{_escape(term)}»\n\n"
                f"📄 صفحه: {page}/{total_pages}\n"
                f"👥 تعداد یافت‌شده: {total}"
            )
            kb = _ikb(rows)

    if use_callback and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def _notify_customer_deleted(context: ContextTypes.DEFAULT_TYPE, agent_id: int, service_id: int) -> None:
    """پس از حذف اشتراک، به مشتری صاحب سرویس اطلاع بده (از طریق ربات مشتری)."""
    try:
        svc = agent_db.get_service_by_id(service_id)
        if not svc or int(svc.get("agent_id", 0)) != agent_id:
            return
        customer_id = int(svc.get("customer_id") or 0)
        if customer_id <= 0:
            return
        customer = agent_db.get_customer_by_id(customer_id)
        tg_id = int((customer or {}).get("telegram_id") or 0)
        if not tg_id:
            return
        from Shared.agent_db import get_active_customer_bot
        from telegram import Bot
        bot_row = get_active_customer_bot(agent_id)
        token = str((bot_row or {}).get("bot_token") or "").strip()
        if not token:
            return
        name = str(svc.get("name") or "")
        text = (
            f"⚠️ اشتراک شما حذف شد.\n\n"
            f"📄 نام سرویس: {_escape(name)}\n"
            f"❌ دسترسی شما به این سرویس قطع شد."
        )
        try:
            await Bot(token=token).send_message(chat_id=tg_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning("Failed to notify customer of deletion svc=%s: %s", service_id, e)
    except Exception as e:
        logger.warning("delete notification error svc=%s: %s", service_id, e)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    parts = data.split(":")
    action = parts[2] if len(parts) > 2 else ""

    agent_id = get_agent_id(context)

    if action == "back":
        context.user_data.pop(UD_STATE, None)
        back_to = context.user_data.pop("subs_back_to", None)
        if back_to and back_to.startswith("agbot:subs:list:"):
            try:
                pg = int(back_to.split(":")[-1])
            except Exception:
                pg = 1
            await _send_users_list(update, context, pg)
            return
        if back_to and back_to.startswith("agbot:subs:expired:"):
            try:
                pg = int(back_to.split(":")[-1])
            except Exception:
                pg = 1
            await _send_expired_list(update, context, pg)
            return
        await show_menu(update, context)
        return

    if action == "detail":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        src = parts[4] if len(parts) > 4 else ""
        pg = parts[5] if len(parts) > 5 and parts[5].isdigit() else "1"
        if src in ("list", "expired"):
            context.user_data["subs_back_to"] = f"agbot:subs:{src}:{pg}"
        svc = agent_db.get_service_by_id(svc_id)
        if not svc:
            await query.answer("سرویس پیدا نشد.", show_alert=True)
            return
        is_active = bool(int(svc.get("is_active", 0) or 0))
        last_online = "هرگز"
        try:
            from AgentBot.services.subscription_service import get_service_last_online
            last_online = await get_service_last_online(svc)
        except Exception:
            last_online = "هرگز"
        await query.edit_message_text(
            _service_detail_text(svc, last_online),
            reply_markup=service_detail_keyboard(svc_id, is_active),
            parse_mode="HTML",
        )
        return

    if action == "search":
        context.user_data.pop("subs_back_to", None)
        context.user_data.pop(UD_STATE, None)
        try:
            from AgentBot.keyboards import subs_search_keyboard
            await query.edit_message_text(
                "🔍 <b>جستجوی اشتراک</b>\n\n"
                "یکی از گزینه‌های زیر را انتخاب کنید:",
                reply_markup=subs_search_keyboard(),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "searchname":
        context.user_data.pop("subs_back_to", None)
        context.user_data[UD_STATE] = STATE_SEARCH_NAME
        try:
            await query.edit_message_text(
                "🔍 <b>جستجو با نام</b>\n\n"
                "نام اشتراک را بفرستید:",
                reply_markup=back_keyboard("agbot:subs:back"),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "searchid":
        context.user_data.pop("subs_back_to", None)
        context.user_data[UD_STATE] = STATE_SEARCH_SERVICE
        try:
            await query.edit_message_text(
                "🔍 <b>جستجوی اشتراک با شناسه</b>\n\n"
                "شناسه ۷ رقمی اشتراک را بفرستید:",
                reply_markup=back_keyboard("agbot:subs:back"),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "namesearch":
        try:
            pg = int(parts[3])
        except Exception:
            pg = 1
        context.user_data["subs_back_to"] = None
        await _send_name_search_results(update, context, page=pg, use_callback=True)
        try:
            await query.answer()
        except Exception:
            pass
        return

    if action == "create":
        servers = shared_db.get_main_servers() or []
        if not servers:
            await query.answer("\u0647\u06cc\u0686 \u0633\u0631\u0648\u0631 \u0627\u0635\u0644\u06cc \u062b\u0628\u062a \u0646\u0634\u062f\u0647.", show_alert=True)
            return
        from AgentBot.keyboards import _ikb
        from Shared.tg_button_styles import inline_button as IButton
        rows = [[IButton(_escape(s.get("title") or f"\u0633\u0631\u0648\u0631 #{s['id']}"),
                         callback_data=f"agbot:subs:picksrv:{s['id']}")] for s in servers]
        rows.append([IButton("\U0001f519 \u0628\u0627\u0632\u06af\u0634\u062a", callback_data="agbot:subs:back")])
        try:
            await query.edit_message_text(
                "\U0001f5a5 <b>\u0627\u0646\u062a\u062e\u0627\u0628 \u0633\u0631\u0648\u0631</b>\n\n\u0644\u0637\u0641\u0627 \u0633\u0631\u0648\u0631 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f:",
                reply_markup=_ikb(rows), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "picksrv":
        server_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        context.user_data[UD_SELECTED_SERVER] = server_id
        gb, months = _get_wizard_defaults(agent_id)
        context.user_data["wiz_gb"] = gb
        context.user_data["wiz_months"] = months
        wholesale, sale, off_pct = _calc_dynamic_price(agent_id, server_id, gb, months)
        context.user_data["wiz_wholesale"] = wholesale
        context.user_data["wiz_sale"] = sale
        context.user_data["wiz_off"] = off_pct
        from AgentBot.keyboards import agent_dynamic_wizard_keyboard
        kb = agent_dynamic_wizard_keyboard(server_id, gb, months, sale, off_pct, wholesale)
        try:
            await query.edit_message_text(
                "\U0001f3af <b>\u0633\u06cc\u0633\u062a\u0645 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u062c\u0647\u062a \u062e\u0631\u06cc\u062f \u0637\u0631\u0627\u062d\u06cc \u06a9\u0646\u06cc\u062f:</b>",
                reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "picksrv_back":
        context.user_data.pop(UD_SELECTED_SERVER, None)
        context.user_data.pop("wiz_gb", None)
        context.user_data.pop("wiz_months", None)
        context.user_data.pop("wiz_wholesale", None)
        context.user_data.pop("wiz_sale", None)
        context.user_data.pop("wiz_off", None)
        query = update.callback_query
        query.data = "agbot:subs:create"
        await handle_callback(update, context)
        return

    if action == "wiz":
        sub = parts[3] if len(parts) > 3 else ""
        server_id = context.user_data.get(UD_SELECTED_SERVER, 0) or 0
        gb = context.user_data.get("wiz_gb", 1)
        months = context.user_data.get("wiz_months", 1)

        if sub == "confirm":
            wholesale = context.user_data.get("wiz_wholesale", 0)
            sale = context.user_data.get("wiz_sale", 0)
            plan = {
                "gb": gb,
                "days": months * 30,
                "wholesale_price": wholesale,
                "sale_price": sale,
                "price": sale,
            }
            context.user_data[UD_SELECTED_PLAN] = plan
            context.user_data[UD_STATE] = STATE_CREATE_SERVICE_NAME
            try:
                await query.edit_message_text(
                    "\U0001f4e1 <b>\u0646\u0627\u0645 \u0633\u0631\u0648\u06cc\u0633</b>\n\n\u062d\u0627\u0644\u0627 \u0646\u0627\u0645 \u0633\u0631\u0648\u06cc\u0633 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return

        settings = db_get_setting(agent_id, "dynamic_plan_settings", {})
        step_gb = settings.get("step_gb", 1)
        step_month = settings.get("step_month", 1)
        min_gb = settings.get("min_gb", 1)
        max_gb = settings.get("max_gb", 999)
        min_month = settings.get("min_month", 1)
        max_month = settings.get("max_month", 12)
        if sub == "gb_inc":
            gb = min(gb + step_gb, max_gb)
        elif sub == "gb_dec":
            gb = max(gb - step_gb, min_gb)
        elif sub == "gb_inc10":
            gb = min(gb + 10, max_gb)
        elif sub == "gb_dec10":
            gb = max(gb - 10, min_gb)
        elif sub == "month_inc":
            months = min(months + step_month, max_month)
        elif sub == "month_dec":
            months = max(months - step_month, min_month)
        context.user_data["wiz_gb"] = gb
        context.user_data["wiz_months"] = months
        wholesale, sale, off_pct = _calc_dynamic_price(agent_id, server_id, gb, months)
        context.user_data["wiz_wholesale"] = wholesale
        context.user_data["wiz_sale"] = sale
        context.user_data["wiz_off"] = off_pct
        from AgentBot.keyboards import agent_dynamic_wizard_keyboard
        kb = agent_dynamic_wizard_keyboard(server_id, gb, months, sale, off_pct, wholesale)
        try:
            await query.edit_message_text(
                "\U0001f3af <b>\u0633\u06cc\u0633\u062a\u0645 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u062c\u0647\u062a \u062e\u0631\u06cc\u062f \u0637\u0631\u0627\u062d\u06cc \u06a9\u0646\u06cc\u062f:</b>",
                reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            pass
        await query.answer()
        return

    if action == "pickplan":
        plan_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        from AgentBot.database import get_fixed_plan
        plan = get_fixed_plan(agent_id, plan_id)
        if not plan:
            await query.answer("\u067e\u0644\u0646 \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f.", show_alert=True)
            return
        server_id = context.user_data.get(UD_SELECTED_SERVER, 0) or 0
        plan["wholesale_price"] = agent_db.calculate_wholesale_price(agent_id, plan.get("gb", 0), plan.get("days", 30), server_id)
        plan["sale_price"] = plan.get("price", 0)
        context.user_data[UD_SELECTED_PLAN] = plan
        context.user_data[UD_STATE] = STATE_CREATE_SERVICE_NAME
        try:
            await query.edit_message_text(
                "\U0001f4e1 <b>\u0646\u0627\u0645 \u0633\u0631\u0648\u06cc\u0633</b>\n\n\u062d\u0627\u0644\u0627 \u0646\u0627\u0645 \u0633\u0631\u0648\u06cc\u0633 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "expired":
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        await _send_expired_list(update, context, page)
        return

    if action == "delexpired":
        from AgentBot.keyboards import _ikb
        from Shared.tg_button_styles import inline_button as IButton
        kb = _ikb([
            [IButton("🗑 بله، همه را حذف کن", callback_data="agbot:subs:dodelexpired")],
            [IButton("❌ لغو", callback_data="agbot:subs:expired:1")],
        ])
        try:
            await query.edit_message_text(
                "⚠️ از حذف همه کاربران منقضی اطمینان دارید؟\n\nاین عملیات قابل بازگشت نیست.",
                reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "dodelexpired":
        try:
            await query.edit_message_text("⏳ در حال حذف کاربران منقضی... لطفاً صبر کنید.", parse_mode="HTML")
        except Exception:
            pass
        items, _, _, _ = await _fetch_services_with_status(agent_id)
        expired_items = [(s, st) for s, st in items if st == "expired"]
        ok = 0
        fail = 0
        for s, _ in expired_items:
            try:
                done = await delete_subscription(agent_id, int(s["id"]))
                if done:
                    ok += 1
                    await _notify_customer_deleted(context, agent_id, int(s["id"]))
                else:
                    fail += 1
            except Exception:
                fail += 1
        await _safe_answer(query, f"حذف شد ({ok})", alert=False)
        try:
            await query.edit_message_text(
                f"✅ حذف کاربران منقضی انجام شد.\n\n🗑 حذف‌شده: {ok} | ❌ خطا: {fail}",
                reply_markup=subs_menu_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "list":
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        await _send_users_list(update, context, page)
        return

    if action == "cfg":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        svc = agent_db.get_service_by_id(svc_id)
        if not svc or int(svc.get("agent_id", 0)) != agent_id:
            await _safe_answer(query, "سرویس پیدا نشد.", alert=True)
            return
        ss = get_subs_link_settings()
        await query.edit_message_text(
            "🔗 <b>دریافت کانفیگ</b>\n\nلطفاً نوع اتصال را انتخاب کنید:",
            reply_markup=subs_configs_keyboard(
                svc_id,
                show_direct_config=ss.get("show_direct_config", True),
                show_sub_link=ss.get("show_sub_link", True),
                show_auto_sub_link=ss.get("show_auto_sub_link", False),
                show_sub_link_b64=ss.get("show_sub_link_b64", False),
                show_multi_server=ss.get("show_multi_server", False),
                show_multi_server_b64=ss.get("show_multi_server_b64", False),
            ),
            parse_mode="HTML",
        )
        return

    if action == "cfgmenu":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        link_type = parts[4] if len(parts) > 4 else ""
        if link_type == "direct":
            await _send_direct_configs(update, context, agent_id, svc_id)
            return
        sub_link = get_sub_link_for_type(agent_id, svc_id, link_type)
        if not sub_link:
            await _safe_answer(query, "لینکی برای این نوع پیدا نشد.", alert=True)
            return
        await _send_sub_link_with_qr(update, context, svc_id, sub_link)
        return

    if action == "renew":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        svc = agent_db.get_service_by_id(svc_id)
        if not svc or int(svc.get("agent_id", 0)) != agent_id:
            await _safe_answer(query, "سرویس پیدا نشد.", alert=True)
            return
        server_id = int(svc.get("server_id") or 0)
        context.user_data[UD_SELECTED_SERVICE] = svc_id
        gb, months = _get_wizard_defaults(agent_id)
        context.user_data["rewiz_gb"] = gb
        context.user_data["rewiz_months"] = months
        wholesale, sale, off_pct = _calc_dynamic_price(agent_id, server_id, gb, months)
        context.user_data["rewiz_wholesale"] = wholesale
        context.user_data["rewiz_sale"] = sale
        context.user_data["rewiz_off"] = off_pct
        vol_mode = time_mode = "reset"
        context.user_data["rewiz_vol_mode"] = vol_mode
        context.user_data["rewiz_time_mode"] = time_mode
        vol_label = "ریست"
        time_label = "ریست"
        from AgentBot.keyboards import agent_renew_wizard_keyboard
        kb = agent_renew_wizard_keyboard(svc_id, gb, months, sale, off_pct, wholesale)
        try:
            await query.edit_message_text(
                "🔄 <b>تمدید اشتراک</b>\n\n"
                f"📦 <b>{_escape(svc.get('name') or 'سرویس')}</b>\n"
                f"\U0001f4ca حجم: <b>{vol_label}</b> | ⏳ زمان: <b>{time_label}</b>\n"
                "مقدار «حجم تمدید» و «مدت تمدید» را انتخاب کنید.\n"
                "هزینه از کیف پول شما کسر می‌شود:",
                reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "rewiz":
        sub = parts[3] if len(parts) > 3 else ""
        svc_id = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        svc = agent_db.get_service_by_id(svc_id)
        if not svc or int(svc.get("agent_id", 0)) != agent_id:
            await _safe_answer(query, "سرویس پیدا نشد.", alert=True)
            return
        server_id = int(svc.get("server_id") or 0)
        gb = context.user_data.get("rewiz_gb", 1)
        months = context.user_data.get("rewiz_months", 1)

        if sub == "confirm":
            wholesale = int(context.user_data.get("rewiz_wholesale", 0) or 0)
            vol_mode = context.user_data.get("rewiz_vol_mode") or "reset"
            time_mode = context.user_data.get("rewiz_time_mode") or "reset"
            days = months * 30
            updated = await renew_subscription(
                agent_id, svc_id, days, extra_gb=float(gb),
                override_cost=wholesale, volume_mode=vol_mode, time_mode=time_mode,
            )
            if not updated:
                try:
                    await query.edit_message_text(
                        "❌ موجودی کیف پول کافی نیست.\nلطفاً ابتدا کیف پول خود را شارژ کنید.",
                        reply_markup=service_detail_keyboard(svc_id, bool(int(svc.get("is_active", 0) or 0))),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                await _safe_answer(query, "موجودی کافی نیست!", alert=True)
                return
            context.user_data.pop("rewiz_gb", None)
            context.user_data.pop("rewiz_months", None)
            context.user_data.pop("rewiz_wholesale", None)
            renew_amount = int(context.user_data.get("rewiz_sale") or wholesale) if context.user_data.get("rewiz_sale") else wholesale
            context.user_data.pop("rewiz_sale", None)
            context.user_data.pop("rewiz_off", None)
            context.user_data.pop(UD_SELECTED_SERVICE, None)
            try:
                from Shared.subscription_reports import send_subscription_report
                renew_user_tg = 0
                renew_customer_id = int(updated.get("customer_id") or 0)
                if renew_customer_id:
                    renew_customer = agent_db.get_customer_by_id(renew_customer_id)
                    renew_user_tg = int((renew_customer or {}).get("telegram_id") or 0)
                if renew_user_tg:
                    await send_subscription_report(
                        context.bot,
                        query.message.chat_id,
                        agent_id,
                        renew_user_tg,
                        updated,
                        "renew",
                        renew_amount,
                    )
            except Exception as report_err:
                logger.warning("Failed to send renew subscription report svc=%s: %s", svc_id, report_err)
            try:
                await query.edit_message_text(
                    f"✅ <b>اشتراک با موفقیت تمدید شد!</b>\n\n"
                    f"📦 <b>{_escape(updated.get('name') or 'سرویس')}</b>\n"
                    f"📊 حجم تمدید: {_fmt_gb(gb)}GB\n"
                    f"⏰ مدت تمدید: {days} روز\n"
                    f"{'📦 حجم: افزایشی (باقی‌مانده + تمدید)' if vol_mode == 'add' else '📦 حجم: ریست (پلن جدید)'}\n"
                    f"{'⏳ زمان: افزایشی (باقی‌مانده + تمدید)' if time_mode == 'add' else '⏳ زمان: ریست (شروع از امروز)'}\n"
                    f"💴 کسر از کیف پول: {_fmt_toman(wholesale)} تومان\n\n"
                    f"📈 کل حجم: {_fmt_gb(updated.get('usage_limit', 0))}GB\n"
                    f"⏳ روز باقی‌مانده: {updated.get('days_left') or updated.get('days') or 0}",
                    reply_markup=service_detail_keyboard(svc_id, bool(int(updated.get("is_active", 0) or 0))),
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return

        settings = db_get_setting(agent_id, "dynamic_plan_settings", {})
        step_gb = settings.get("step_gb", 1)
        step_month = settings.get("step_month", 1)
        min_gb = settings.get("min_gb", 1)
        max_gb = settings.get("max_gb", 999)
        min_month = settings.get("min_month", 1)
        max_month = settings.get("max_month", 12)
        if sub == "gb_inc":
            gb = min(gb + step_gb, max_gb)
        elif sub == "gb_dec":
            gb = max(gb - step_gb, min_gb)
        elif sub == "gb_inc10":
            gb = min(gb + 10, max_gb)
        elif sub == "gb_dec10":
            gb = max(gb - 10, min_gb)
        elif sub == "month_inc":
            months = min(months + step_month, max_month)
        elif sub == "month_dec":
            months = max(months - step_month, min_month)
        context.user_data["rewiz_gb"] = gb
        context.user_data["rewiz_months"] = months
        wholesale, sale, off_pct = _calc_dynamic_price(agent_id, server_id, gb, months)
        context.user_data["rewiz_wholesale"] = wholesale
        context.user_data["rewiz_sale"] = sale
        context.user_data["rewiz_off"] = off_pct
        from AgentBot.keyboards import agent_renew_wizard_keyboard
        kb = agent_renew_wizard_keyboard(svc_id, gb, months, sale, off_pct, wholesale)
        try:
            await query.edit_message_text(
                "🔄 <b>تمدید اشتراک</b>\n\n"
                f"📦 <b>{_escape(svc.get('name') or 'سرویس')}</b>\n"
                "مقدار «حجم تمدید» و «مدت تمدید» را انتخاب کنید.\n"
                "هزینه از کیف پول شما کسر می‌شود:",
                reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            pass
        await query.answer()
        return

    if action == "disable":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        try:
            await query.edit_message_text("⏳ در حال غیرفعال کردن سرویس... لطفاً صبر کنید.", parse_mode="HTML")
        except Exception:
            pass
        ok = await disable_subscription(agent_id, svc_id)
        await _safe_answer(query, "غیرفعال شد ✅" if ok else "خطا!", alert=not ok)
        if ok:
            svc = agent_db.get_service_by_id(svc_id)
            if svc:
                try:
                    from AgentBot.services.subscription_service import get_service_last_online
                    _lo = await get_service_last_online(svc)
                    await query.edit_message_text(
                        _service_detail_text(svc, _lo),
                        reply_markup=service_detail_keyboard(svc_id, False),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        return

    if action == "enable":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        try:
            await query.edit_message_text("⏳ در حال فعال کردن سرویس... لطفاً صبر کنید.", parse_mode="HTML")
        except Exception:
            pass
        ok = await enable_subscription(agent_id, svc_id)
        await _safe_answer(query, "فعال شد ✅" if ok else "خطا!", alert=not ok)
        if ok:
            svc = agent_db.get_service_by_id(svc_id)
            if svc:
                try:
                    from AgentBot.services.subscription_service import get_service_last_online
                    _lo = await get_service_last_online(svc)
                    await query.edit_message_text(
                        _service_detail_text(svc, _lo),
                        reply_markup=service_detail_keyboard(svc_id, True),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        return

    if action == "delete":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        from AgentBot.keyboards import _ikb
        from Shared.tg_button_styles import inline_button as IButton
        kb = _ikb([
            [IButton("\U0001f5d1 \u0628\u0644\u0647\u060c \u062d\u0630\u0641 \u06a9\u0646", callback_data=f"agbot:subs:dodelete:{svc_id}")],
            [IButton("\u274c \u0644\u063a\u0648", callback_data=f"agbot:subs:detail:{svc_id}")],
        ])
        try:
            await query.edit_message_text(
                f"\u26a0\ufe0f \u0627\u0632 \u062d\u0630\u0641 \u0633\u0631\u0648\u06cc\u0633 \u0627\u0637\u0645\u06cc\u0646\u0627\u0646 \u062f\u0627\u0631\u06cc\u062f\u061f\n\n"
                f"\u0627\u06cc\u0646 \u0639\u0645\u0644 \u0642\u0627\u0628\u0644 \u0628\u0627\u0632\u06af\u0634\u062a \u0646\u06cc\u0633\u062a.",
                reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "dodelete":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        ok = await delete_subscription(agent_id, svc_id)
        await _safe_answer(query, "حذف شد ✅" if ok else "خطا!", alert=not ok)
        if ok:
            await _notify_customer_deleted(context, agent_id, svc_id)
            await show_menu(update, context)
        return

    if action == "rename":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        svc = agent_db.get_service_by_id(svc_id)
        if not svc or int(svc.get("agent_id", 0)) != agent_id:
            await _safe_answer(query, "سرویس پیدا نشد.", alert=True)
            return
        context.user_data[UD_STATE] = STATE_RENAME_SERVICE
        context.user_data[UD_SELECTED_SERVICE] = svc_id
        try:
            await query.edit_message_text(
                f"✏️ <b>تغییر نام اشتراک</b>\n\n"
                f"📦 نام فعلی: <b>{_escape(svc.get('name') or '—')}</b>\n\n"
                "نام جدید را ارسال کنید:\n"
                "• حداقل 3 و حداکثر 64 کاراکتر\n"
                "• برای انصراف «🔙 بازگشت» در کیبورد پایین یا «❌ لغو» را بزنید.",
                reply_markup=back_keyboard(f"agbot:subs:rename_cancel:{svc_id}"),
                parse_mode="HTML",
            )
        except Exception:
            pass
        # نمایش دکمه بازگشت در کیبورد اصلی پایین (ReplyKeyboard)
        try:
            chat_id = query.message.chat_id if query and query.message else update.effective_chat.id
            await context.bot.send_message(
                chat_id=chat_id,
                text="👇 برای انصراف از تغییر نام، دکمه «🔙 بازگشت» در پایین را بزنید.",
                reply_markup=rename_cancel_keyboard(),
            )
        except Exception:
            pass
        return

    if action == "rename_cancel":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_SELECTED_SERVICE, None)
        svc = agent_db.get_service_by_id(svc_id) if svc_id else None
        # بازگرداندن کیبورد اصلی
        try:
            chat_id = query.message.chat_id if query and query.message else update.effective_chat.id
            await context.bot.send_message(
                chat_id=chat_id,
                text="لغو شد.",
                reply_markup=main_menu_keyboard(),
            )
        except Exception:
            pass
        if svc and int(svc.get("agent_id", 0)) == agent_id:
            try:
                from AgentBot.services.subscription_service import get_service_last_online
                last_online = await get_service_last_online(svc)
            except Exception:
                last_online = "هرگز"
            is_active = bool(int(svc.get("is_active", 0) or 0))
            try:
                await query.edit_message_text(
                    _service_detail_text(svc, last_online),
                    reply_markup=service_detail_keyboard(svc_id, is_active),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        else:
            await show_menu(update, context)
        return

    if action == "newlink":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        svc = agent_db.get_service_by_id(svc_id)
        if not svc:
            await query.answer("\u0633\u0631\u0648\u06cc\u0633 \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f.", show_alert=True)
            return
        await query.edit_message_text(
            "\u23f3 \u062f\u0631 \u062d\u0627\u0644 \u062a\u063a\u06cc\u06cc\u0631 \u0644\u06cc\u0646\u06a9... \u0644\u0637\u0641\u0627 \u0635\u0628\u0631 \u06a9\u0646\u06cc\u062f.",
            parse_mode="HTML",
        )
        result = await change_subscription_link(agent_id, svc_id)
        if result:
            is_active = bool(int(result.get("is_active", 0) or 0))
            text = (
                f"\u2705 <b>\u0644\u06cc\u0646\u06a9 \u0628\u0627 \u0645\u0648\u0641\u0642\u06cc\u062a \u062a\u063a\u06cc\u06cc\u0631 \u06a9\u0631\u062f!</b>\n\n"
                f"\U0001f4e1 \u0633\u0631\u0648\u06cc\u0633: {_escape(result.get('name') or '')}\n"
                f"\U0001f4e1 \u06cc\u0648\u06cc\u06cc\u062f\u06cc \u062c\u062f\u06cc\u062f: <code>{_escape(result.get('panel_user_uuid') or '')}</code>\n\n"
                "\U0001f447 \u0644\u06cc\u0646\u06a9 \u062c\u062f\u06cc\u062f \u0647\u0645\u0631\u0627\u0647 \u0628\u0627 QR \u062f\u0631 \u067e\u06cc\u0627\u0645 \u0628\u0639\u062f\u06cc \u0627\u0631\u0633\u0627\u0644 \u0645\u06cc\u0634\u0648\u062f."
            )
            try:
                await query.edit_message_text(text, reply_markup=service_detail_keyboard(svc_id, is_active), parse_mode="HTML")
            except Exception:
                pass
            try:
                await query.answer()
            except Exception:
                pass
            ss = get_subs_link_settings()
            await query.edit_message_text(
                "✅ <b>لینک با موفقیت تغییر کرد!</b>\n\n🔗 لطفاً نوع اتصال را انتخاب کنید:",
                reply_markup=subs_configs_keyboard(
                    svc_id,
                    show_direct_config=ss.get("show_direct_config", True),
                    show_sub_link=ss.get("show_sub_link", True),
                    show_auto_sub_link=ss.get("show_auto_sub_link", False),
                    show_sub_link_b64=ss.get("show_sub_link_b64", False),
                    show_multi_server=ss.get("show_multi_server", False),
                    show_multi_server_b64=ss.get("show_multi_server_b64", False),
                ),
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                "\u274c \u062e\u0637\u0627 \u062f\u0631 \u062a\u063a\u06cc\u06cc\u0631 \u0644\u06cc\u0646\u06a9. \u0644\u0637\u0641\u0627 \u062f\u0648\u0628\u0627\u0631\u0647 \u062a\u0644\u0627\u0634 \u06a9\u0646\u06cc\u062f.",
                reply_markup=service_detail_keyboard(svc_id, bool(int(svc.get("is_active", 0) or 0))),
                parse_mode="HTML",
            )
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    text = update.message.text.strip()
    state = context.user_data.get(UD_STATE)

    if state == STATE_SEARCH_SERVICE:
        code = _normalize_digits(text)
        if not code.isdigit():
            await update.message.reply_text("❌ شناسه نامعتبر است. شناسه ۷ رقمی اشتراک را بفرستید.")
            return True
        svc = agent_db.get_service_by_code(code, agent_id=agent_id)
        if not svc:
            await update.message.reply_text("❌ اشتراکی با این شناسه یافت نشد.")
            return True
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop("subs_back_to", None)
        is_active = bool(int(svc.get("is_active", 0) or 0))
        try:
            from AgentBot.services.subscription_service import get_service_last_online
            _lo = await get_service_last_online(svc)
        except Exception:
            _lo = "هرگز"
        detail = f"✅ <b>اشتراک یافت شد</b>\n\n" + _service_detail_text(svc, _lo)
        await update.message.reply_text(detail, reply_markup=service_detail_keyboard(int(svc["id"]), is_active), parse_mode="HTML")
        return True

    if state == STATE_SEARCH_NAME:
        name = text
        context.user_data["subs_search_term"] = name
        await _send_name_search_results(update, context, page=1, use_callback=False)
        return True

    if state == STATE_RENAME_SERVICE:
        # دکمه بازگشت در کیبورد پایین — انصراف
        if text in (BTN_BACK, "🔙 بازگشت", "❌ لغو", "/cancel", "لغو"):
            svc_id_cancel = int(context.user_data.get(UD_SELECTED_SERVICE) or 0)
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_SELECTED_SERVICE, None)
            await update.message.reply_text("لغو شد.", reply_markup=main_menu_keyboard())
            if svc_id_cancel:
                svc_cancel = agent_db.get_service_by_id(svc_id_cancel)
                if svc_cancel and int(svc_cancel.get("agent_id", 0)) == agent_id:
                    try:
                        from AgentBot.services.subscription_service import get_service_last_online
                        last_online = await get_service_last_online(svc_cancel)
                    except Exception:
                        last_online = "هرگز"
                    is_active = bool(int(svc_cancel.get("is_active", 0) or 0))
                    await update.message.reply_text(
                        _service_detail_text(svc_cancel, last_online),
                        reply_markup=service_detail_keyboard(svc_id_cancel, is_active),
                        parse_mode="HTML",
                    )
            return True
        svc_id = int(context.user_data.get(UD_SELECTED_SERVICE) or 0)
        new_name = re.sub(r"\s+", " ", (text or "").strip())
        if len(new_name) < 3:
            await update.message.reply_text(
                "❌ نام اشتراک خیلی کوتاه است. حداقل 3 کاراکتر وارد کنید.",
                reply_markup=rename_cancel_keyboard(),
            )
            return True
        if len(new_name) > 64:
            await update.message.reply_text(
                "❌ نام اشتراک خیلی طولانی است. حداکثر 64 کاراکتر وارد کنید.",
                reply_markup=rename_cancel_keyboard(),
            )
            return True
        svc = agent_db.get_service_by_id(svc_id) if svc_id else None
        if not svc or int(svc.get("agent_id", 0)) != agent_id:
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_SELECTED_SERVICE, None)
            await update.message.reply_text("❌ اشتراک موردنظر یافت نشد.", reply_markup=main_menu_keyboard())
            return True
        old_name = str(svc.get("name") or "").strip()
        if new_name == old_name:
            await update.message.reply_text(
                "ℹ️ نام جدید با نام فعلی یکسان است. نام دیگری وارد کنید.",
                reply_markup=rename_cancel_keyboard(),
            )
            return True
        await update.message.reply_text("⏳ در حال بروزرسانی نام اشتراک... لطفاً صبر کنید.")
        ok, result_text = await rename_service_on_panels(agent_id, svc_id, new_name)
        if not ok:
            await update.message.reply_text(result_text, reply_markup=rename_cancel_keyboard())
            return True
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_SELECTED_SERVICE, None)
        await update.message.reply_text(result_text, reply_markup=main_menu_keyboard())
        # نمایش مجدد پروفایل با نام جدید
        refreshed = agent_db.get_service_by_id(svc_id)
        if refreshed:
            try:
                from AgentBot.services.subscription_service import get_service_last_online
                last_online = await get_service_last_online(refreshed)
            except Exception:
                last_online = "هرگز"
            is_active = bool(int(refreshed.get("is_active", 0) or 0))
            await update.message.reply_text(
                _service_detail_text(refreshed, last_online),
                reply_markup=service_detail_keyboard(svc_id, is_active),
                parse_mode="HTML",
            )
        return True

    if state == STATE_CREATE_SERVICE_NAME:
        plan = context.user_data.get(UD_SELECTED_PLAN)
        server_id = context.user_data.get(UD_SELECTED_SERVER)
        if not plan or not server_id:
            await update.message.reply_text("\u062e\u0637\u0627: \u0627\u0637\u0644\u0627\u0639\u0627\u062a \u06af\u0645 \u0634\u062f. \u062f\u0648\u0628\u0627\u0631\u0647 \u0634\u0631\u0648\u0639 \u06a9\u0646\u06cc\u062f.")
            context.user_data.pop(UD_STATE, None)
            return True
        note_text = agent_db.make_service_note(agent_id)
        svc = await create_subscription(agent_id, 0, server_id, plan, text, note=note_text)
        if not svc:
            await update.message.reply_text(
                "\u062e\u0637\u0627 \u062f\u0631 \u0633\u0627\u062e\u062a \u0633\u0631\u0648\u06cc\u0633. \u0645\u0648\u062c\u0648\u062f\u06cc \u06a9\u0627\u0641\u06cc \u0646\u06cc\u0633\u062a \u06cc\u0627 \u062e\u0637\u0627\u06cc \u0633\u06cc\u0633\u062a\u0645.",
                reply_markup=cancel_keyboard(),
            )
            return True
        plan_title = f"{plan['days']} \u0631\u0648\u0632 / {_fmt_gb(plan['gb'])}GB"
        db_create_order(agent_id, 0, "", plan.get("wholesale_price", 0), "new", plan.get("id", 0), text, volume_gb=plan.get("gb", 0))
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_SELECTED_PLAN, None)
        context.user_data.pop(UD_SELECTED_SERVER, None)
        svc_id = int(svc["id"])
        is_active = bool(int(svc.get("is_active", 0) or 0))

        # پیام اول: تأیید کوتاه ساخت
        await update.message.reply_text(
            _service_created_text(svc),
            parse_mode="HTML",
        )

        # یادداشت: عدد ۷ رقمی رندم اختصاصی هر اکانت
        note = agent_db._service_note_from_comment(svc.get("comment") or "")
        if not note:
            import random
            note = f"{random.randint(0, 9999999):07d}"

        # آخرین اتصال: از پنل (بلافاصله بعد از ساخت «هرگز» است)
        last_online = "هرگز"
        try:
            from AgentBot.services.subscription_service import get_service_last_online
            last_online = await get_service_last_online(svc)
        except Exception:
            last_online = "هرگز"

        # پیام دوم: جزئیات اکانت + دکمه‌ها
        await update.message.reply_text(
            _service_detail_card_text(svc, note, last_online),
            reply_markup=service_detail_keyboard(svc_id, is_active),
            parse_mode="HTML",
        )
        return True

    return False
