from Shared import i18n
import logging
import math

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from Shared import agent_db
from Shared.tg_button_styles import inline_button as IButton
from AgentBot.constants import UD_STATE
from AgentBot.handlers.base import get_agent_id
from AgentBot.utils.helpers import _escape, _fmt_toman, _fmt_gb, _status_icon
from AgentBot.keyboards import (
    agent_lang,
    settings_sub_menu_keyboard,
    back_keyboard,
    cancel_keyboard,
    send_msg_keyboard,
    main_menu_keyboard,
    settings_menu_keyboard,
    users_profile_keyboard,
    _ikb,
    BTN_BACK,
)

logger = logging.getLogger(__name__)


def _build_profile_text(agent_id: int, customer: dict) -> str:
    try:
        _lg = i18n.get_agent_lang(int(agent_id or 0))
    except Exception:
        _lg = "fa"
    from CustomerBot.database import get_full_customer_stats
    telegram_id = int(customer.get("telegram_id") or 0)
    stats = get_full_customer_stats(agent_id, telegram_id)
    u = stats.get("user") or {}
    customer_id = customer.get("id") or 0
    name = _escape(customer.get("full_name", "") or customer.get("username", "")) or f"{i18n.t('کاربر #', _lg)}{customer_id}"
    tg_id = customer.get("telegram_id", "—")
    username = u.get("username") or customer.get("username") or ""
    full_name = u.get("full_name") or customer.get("full_name") or ""
    user_display = _escape(full_name or username or name)
    trial = "✅" if stats.get("got_free_trial") else "❌"
    banned = stats.get("is_banned")
    ban_status = i18n.t('🔴 مسدود', _lg) if banned else i18n.t('🟢 فعال', _lg)
    gb = stats.get("orders_gb") or 0
    gb_str = f"{gb:g}" if isinstance(gb, (int, float)) else gb
    price = f"{int(stats.get('orders_price') or 0):,}"
    return (
        f"{i18n.t('👤 کاربر: ', _lg)}{user_display}{i18n.t('\n🔹 نام کاربری: @', _lg)}{_escape(username) if username else 'None'}{i18n.t('\n🔸 شناسه کاربر: ', _lg)}{tg_id}{i18n.t('\n🔸 وضعیت دریافت تست رایگان: ', _lg)}{trial}{i18n.t('\n🔸 وضعیت اکانت: ', _lg)}{ban_status}{i18n.t('\n❖ ⬩----------------------------------⬩ ❖\n🔸 تعداد اشتراک‌های خریداری شده: ', _lg)}{stats.get('services_total')}{i18n.t('\n🔸 تعداد اشتراک‌های متصل شده: ', _lg)}{stats.get('services_active')}{i18n.t('\n🔸 تعداد تراکنشات: ', _lg)}{stats.get('tx_total')}{i18n.t('\n🔸 تعداد تراکنشات تایید شده: ', _lg)}{stats.get('tx_approved')}{i18n.t('\n❖ ⬩----------------------------------⬩ ❖\n🔸 تعداد سفارشات: ', _lg)}{stats.get('orders_count')}{i18n.t('\n🔸 مجموع حجم سفارشات(GB): ', _lg)}{gb_str}{i18n.t('\n🔸 مجموع ارزش سفارشات: ', _lg)}{price}{i18n.t('تومان', _lg)}"
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    parts = data.split(":")
    agent_id = get_agent_id(context)
    p1 = parts[1] if len(parts) > 1 else ""
    p2 = parts[2] if len(parts) > 2 else ""
    p3 = parts[3] if len(parts) > 3 else ""
    p4 = parts[4] if len(parts) > 4 else ""

    if p1 == "set" and p2 == "users" and not p3:
        await query.edit_message_text(
            i18n.t('👥 <b>مدیریت کاربران ربات</b>', _lg),
            reply_markup=settings_sub_menu_keyboard("set:users", i18n.t('👥 لیست کاربران ربات', _lg)), parse_mode="HTML",
        )
        return

    if (p2 == "back" and p1 == "set") or (p2 == "users" and p3 == "back"):
        from AgentBot.keyboards import settings_menu_keyboard
        await query.edit_message_text(
            i18n.t('⚙️ <b>تنظیمات ربات</b>', _lg),
            reply_markup=settings_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML",
        )
        return

    if p3 == "list":
        page = int(p4) if len(parts) > 4 and p4.isdigit() else 1
        page_size = 9
        customers, total = agent_db.get_customers_list(agent_id, page=page, page_size=page_size)
        total_pages = max(1, math.ceil(total / page_size))
        if page > total_pages:
            page = total_pages
            customers, _ = agent_db.get_customers_list(agent_id, page=page, page_size=page_size)
        lines = [
            f"{i18n.t('📋 <b>لیست کاربران ربات</b>', _lg)}",
            f"{i18n.t('تعداد کل: ', _lg)}{total}",
            f"{i18n.t('صفحه: ', _lg)}{page}/{total_pages}",
            "",
        ]
        if not customers:
            lines.append(i18n.t('هیچ کاربری وجود ندارد.', _lg))
        rows = []
        if customers:
            row = []
            for c in customers:
                name = _escape(c.get("full_name", "") or c.get("username", "")) or f"{i18n.t('کاربر #', _lg)}{c['id']}"
                row.append(IButton(f"\U0001f535 {name}", callback_data=f"agbot:set:users:detail:{c['id']}"))
                if len(row) == 3:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)
        nav = []
        if page > 1:
            nav.append(IButton("\u2b05\ufe0f", callback_data=f"agbot:set:users:list:{page-1}"))
        nav.append(IButton(f"{page}/{total_pages}", callback_data="agbot:noop"))
        if page < total_pages:
            nav.append(IButton("\u27a1\ufe0f", callback_data=f"agbot:set:users:list:{page+1}"))
        rows.append(nav)
        rows.append([IButton(i18n.t("back", _lg), callback_data="agbot:set:back")])
        try:
            await query.edit_message_text(
                "\n".join(lines),
                reply_markup=_ikb(rows), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if p3 == "search":
        context.user_data[UD_STATE] = "st:search_user"
        try:
            await query.answer()
        except Exception:
            pass
        try:
            await query.message.reply_text(
                i18n.t('🔍 <b>جستجوی کاربر</b>\nنام یا آیدی کاربر را وارد کنید:', _lg),
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if p3 == "services":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        services = agent_db.get_services_by_customer(customer_id)
        total = len(services)
        active = sum(1 for s in services if int(s.get("is_active", 0) or 0) == 1)
        expired = total - active
        text = (
            f"{i18n.t('#️⃣ لیست سرویس‌ها\nشما می‌توانید لیست سرویس‌ها و اطلاعات آن‌ها را اینجا مشاهده کنید\n📦 تعداد کل سرویس‌ها: ', _lg)}{total}{i18n.t('\n🟢 سرویس‌های فعال: ', _lg)}{active}{i18n.t('\n🔴 سرویس‌های منقضی: ', _lg)}{expired}"
        )
        rows = []
        if not services:
            text += i18n.t('\n\n❌ سرویسی برای نمایش این کاربر یافت نشد.', _lg)
        else:
            for s in services:
                name = s.get("name") or f"Service #{s['id']}"
                if int(s.get("is_active", 0) or 0) == 1:
                    emoji = "🟡" if int(s.get("is_trial", 0) or 0) == 1 else "🔵"
                else:
                    emoji = "🔴"
                rows.append([IButton(f"{emoji} |{name}", callback_data=f"agbot:subs:detail:{s['id']}")])
        rows.append([IButton(i18n.t('بازگشت🔙', _lg), callback_data=f"agbot:set:users:detail:{customer_id}")])
        await query.edit_message_text(
            text,
            reply_markup=_ikb(rows),
            parse_mode="HTML",
        )
        return

    if p3 == "orders":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        customer = agent_db.get_customer_by_id(customer_id)
        telegram_id = int((customer or {}).get("telegram_id") or 0)
        if not telegram_id:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        from CustomerBot.database import get_user_orders, get_user_orders_stats
        stats = get_user_orders_stats(agent_id, telegram_id)
        orders = get_user_orders(agent_id, telegram_id, limit=20)
        text = (
            f"{i18n.t('🔹 لیست سفارشات\n🔸 تعداد سفارشات: ', _lg)}{stats['total_count']}{i18n.t('\n🔸 مجموع حجم سفارشات(GB): ', _lg)}{_fmt_gb(stats['total_gb'])}{i18n.t('\n🔸 مجموع ارزش سفارشات: ', _lg)}{_fmt_toman(stats['total_price'])}{i18n.t('تومان\n❖ ⬩----------------------------------⬩ ❖\n🔸 تعداد سفارشات 30 روز گذشته: ', _lg)}{stats['last30_count']}{i18n.t('\n🔸 حجم سفارشات 30 روز گذشته(GB): ', _lg)}{_fmt_gb(stats['last30_gb'])}{i18n.t('\n🔸 ارزش سفارشات 30 روز گذشته: ', _lg)}{_fmt_toman(stats['last30_price'])}{i18n.t('تومان\n❖ ⬩----------------------------------⬩ ❖\n🔸 تعداد سفارشات این ماه: ', _lg)}{stats['month_count']}{i18n.t('\n🔸 حجم سفارشات این ماه(GB): ', _lg)}{_fmt_gb(stats['month_gb'])}{i18n.t('\n🔸 ارزش سفارشات این ماه: ', _lg)}{_fmt_toman(stats['month_price'])}{i18n.t('تومان', _lg)}"
        )
        rows = []
        current_row = []
        for o in orders:
            oid = str(o.get("order_id") or o.get("id"))
            current_row.append(IButton(oid, callback_data=f"agbot:set:orders:detail:{oid}"))
            if len(current_row) == 3:
                rows.append(current_row)
                current_row = []
        if current_row:
            rows.append(current_row)
        rows.append([IButton(i18n.t('بازگشت🔙', _lg), callback_data=f"agbot:set:users:detail:{customer_id}")])
        await query.edit_message_text(
            text,
            reply_markup=_ikb(rows),
            parse_mode="HTML",
        )
        return

    if p3 == "tx":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        customer = agent_db.get_customer_by_id(customer_id)
        telegram_id = int((customer or {}).get("telegram_id") or 0)
        if not telegram_id:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        from AgentBot.database import get_customer_payments, get_customer_payment_stats
        stats = get_customer_payment_stats(agent_id, user_id=telegram_id)
        payments, _ = get_customer_payments(agent_id, user_id=telegram_id, page=1, page_size=20)
        text = (
            f"{i18n.t('🔹 لیست تراکنشات\n🔸 تعداد تراکنشات: ', _lg)}{stats['total_count']}{i18n.t('\n🔸 مبلغ تراکنشات: ', _lg)}{_fmt_toman(stats['total_amount'])}{i18n.t('تومان\n❖ ⬩----------------------------------⬩ ❖\n🔸 تراکنشات 30 روز گذشته: ', _lg)}{stats['last30_count']}{i18n.t('\n🔸 مبلغ تراکنشات 30 روز گذشته: ', _lg)}{_fmt_toman(stats['last30_amount'])}{i18n.t('تومان\n❖ ⬩----------------------------------⬩ ❖\n🔸 تراکنشات این ماه: ', _lg)}{stats['month_count']}{i18n.t('\n🔸 مبلغ تراکنشات این ماه: ', _lg)}{_fmt_toman(stats['month_amount'])}{i18n.t('تومان', _lg)}"
        )
        rows = []
        current_row = []
        for p in payments:
            current_row.append(IButton(str(p.get("id")), callback_data=f"agbot:set:tx:detail:{p.get('id')}"))
            if len(current_row) == 3:
                rows.append(current_row)
                current_row = []
        if current_row:
            rows.append(current_row)
        rows.append([IButton(i18n.t('بازگشت🔙', _lg), callback_data=f"agbot:set:users:detail:{customer_id}")])
        await query.edit_message_text(
            text,
            reply_markup=_ikb(rows),
            parse_mode="HTML",
        )
        return

    if p3 == "ban":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        customer = agent_db.get_customer_by_id(customer_id)
        telegram_id = int((customer or {}).get("telegram_id") or 0)
        if not telegram_id:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        from CustomerBot.database import get_full_customer_stats, get_user, set_user_banned
        u = get_user(agent_id, telegram_id) or {}
        banned = int(u.get("is_banned") or 0)
        ok = set_user_banned(agent_id, telegram_id, not banned)
        if ok:
            state = i18n.t('مسدود شد 🔴', _lg) if not banned else i18n.t('آزاد شد 🟢', _lg)
            await query.answer(f"{i18n.t('کاربر ', _lg)}{state}", show_alert=True)
        else:
            await query.answer(i18n.t('خطا در تغییر وضعیت.', _lg), show_alert=True)
        text = _build_profile_text(agent_id, customer)
        await query.edit_message_text(
            text,
            reply_markup=users_profile_keyboard(customer_id, telegram_id, back_callback="agbot:set:users"),
            parse_mode="HTML",
        )
        return

    if p3 == "tickets":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        customer = agent_db.get_customer_by_id(customer_id)
        telegram_id = int((customer or {}).get("telegram_id") or 0)
        if not telegram_id:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        from CustomerBot.database import get_user_tickets
        tickets = get_user_tickets(agent_id, telegram_id)
        lines = [f"{i18n.t('📑 <b>لیست تیکت‌ها</b> (', _lg)}{len(tickets)})"]
        if not tickets:
            lines.append(i18n.t('مشتری تیکتی ندارد.', _lg))
        else:
            for t in tickets:
                status = _status_icon(t.get("status", ""))
                lines.append(
                    f"{status} <b>{_escape(t.get('title', ''))}</b> • {_escape(str(t.get('created_at', ''))[:16])}"
                )
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=back_keyboard(f"agbot:set:users:detail:{customer_id}"),
            parse_mode="HTML",
        )
        return

    if p3 == "message":
        telegram_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not telegram_id:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        context.user_data["userbot_msg_target"] = telegram_id
        context.user_data[UD_STATE] = "st:send_user_msg"
        context.user_data.pop("subs_back_to", None)
        try:
            await query.answer()
        except Exception:
            pass
        await query.message.reply_text(
            i18n.t('✍️ لطفا متن پیامی که می خواهید برای کاربر ارسال شود را وارد کنید:', _lg),
            reply_markup=send_msg_keyboard(),
        )
        return

    if p3 == "detail":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        customer = agent_db.get_customer_by_id(customer_id)
        if not customer:
            await query.answer(i18n.t('کاربر پیدا نشد.', _lg), show_alert=True)
            return
        telegram_id = int(customer.get("telegram_id") or 0)
        text = _build_profile_text(agent_id, customer)
        kb = users_profile_keyboard(customer_id, telegram_id, back_callback="agbot:set:users")
        await query.edit_message_text(
            text,
            reply_markup=kb,
            parse_mode="HTML",
        )
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    state = context.user_data.get(UD_STATE)
    text = update.message.text.strip()

    if state in ("st:send_user_msg",):
        if text in {"بازگشت", "◀️ بازگشت", "❌ لغو", "لغو", "/cancel"}:
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop("userbot_msg_target", None)
            await update.message.reply_text(i18n.t('عملیات لغو شد.', _lg), reply_markup=main_menu_keyboard(lang=_lg))
            return True
        if not text:
            await update.message.reply_text(i18n.t('📨 متن پیام خالی است. لطفاً متن را بنویسید:', _lg))
            return True
        target = int(context.user_data.get("userbot_msg_target") or 0)
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop("userbot_msg_target", None)
        if not target:
            await update.message.reply_text(i18n.t('❌ کاربر پیدا نشد.', _lg), reply_markup=main_menu_keyboard(lang=_lg))
            return True
        from Shared.agent_db import get_active_customer_bot
        bot_row = get_active_customer_bot(agent_id)
        token = str((bot_row or {}).get("bot_token") or "").strip()
        if not token:
            await update.message.reply_text(i18n.t('❌ ربات مشتری برای این نماینده فعال نیست.', _lg), reply_markup=main_menu_keyboard(lang=_lg))
            return True
        try:
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton(i18n.t('📩 پاسخ', _lg), callback_data="agmsg:reply")]]
            )
            await Bot(token=token).send_message(
                chat_id=target,
                text=f"{i18n.t('📨 پیام از طرف نماینده:\n\n', _lg)}{text}",
                reply_markup=kb,
            )
            await update.message.reply_text(i18n.t('📩پیام ارسال شد', _lg), reply_markup=main_menu_keyboard(lang=_lg))
        except Exception as e:
            logger.warning("send user msg failed tg_id=%s: %s", target, e)
            await update.message.reply_text(f"{i18n.t('❌ ارسال پیام ناموفق بود:\n', _lg)}{e}", reply_markup=main_menu_keyboard(lang=_lg))
        return True

    if state not in ("st:search_user",):
        return False
    if text in {"بازگشت", "❌ لغو", "لغو", "/cancel"}:
        context.user_data.pop(UD_STATE, None)
        await update.message.reply_text(i18n.t("ag_settings_title", agent_lang(context)), reply_markup=settings_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML")
        return True
    customers = agent_db.search_customers(agent_id, text, limit=10)
    if not customers:
        await update.message.reply_text(i18n.t('هیچ کاربری پیدا نشد.', _lg))
        return True
    rows = []
    for c in customers:
        name = _escape(c.get("full_name", "") or c.get("username", "")) or f"{i18n.t('کاربر #', _lg)}{c['id']}"
        rows.append([IButton(name, callback_data=f"agbot:set:users:detail:{c['id']}")])
    await update.message.reply_text(
        f"{i18n.t('🔍 نتایج برای "', _lg)}{_escape(text)}\":",
        reply_markup=_ikb(rows), parse_mode="HTML",
    )
    context.user_data.pop(UD_STATE, None)
    return True
