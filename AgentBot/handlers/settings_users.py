import logging
import math

from telegram import Update
from telegram.ext import ContextTypes

from Shared import agent_db
from Shared.tg_button_styles import inline_button as IButton
from AgentBot.constants import UD_STATE
from AgentBot.handlers.base import get_agent_id
from AgentBot.utils.helpers import _escape, _fmt_toman, _status_icon
from AgentBot.keyboards import (
    settings_sub_menu_keyboard,
    back_keyboard,
    cancel_keyboard,
    send_msg_keyboard,
    main_menu_keyboard,
    users_profile_keyboard,
    _ikb,
    BTN_BACK,
)

logger = logging.getLogger(__name__)


def _build_profile_text(agent_id: int, customer: dict) -> str:
    from CustomerBot.database import get_full_customer_stats
    telegram_id = int(customer.get("telegram_id") or 0)
    stats = get_full_customer_stats(agent_id, telegram_id)
    u = stats.get("user") or {}
    customer_id = customer.get("id") or 0
    name = _escape(customer.get("full_name", "") or customer.get("username", "")) or f"کاربر #{customer_id}"
    tg_id = customer.get("telegram_id", "—")
    username = u.get("username") or customer.get("username") or ""
    full_name = u.get("full_name") or customer.get("full_name") or ""
    user_display = _escape(full_name or username or name)
    trial = "✅" if stats.get("got_free_trial") else "❌"
    banned = stats.get("is_banned")
    ban_status = "🔴 مسدود" if banned else "🟢 فعال"
    gb = stats.get("orders_gb") or 0
    gb_str = f"{gb:g}" if isinstance(gb, (int, float)) else gb
    price = f"{int(stats.get('orders_price') or 0):,}"
    return (
        f"👤 کاربر: {user_display}\n"
        f"🔹 نام کاربری: @{_escape(username) if username else 'None'}\n"
        f"🔸 شناسه کاربر: {tg_id}\n"
        f"🔸 وضعیت دریافت تست رایگان: {trial}\n"
        f"🔸 وضعیت اکانت: {ban_status}\n"
        "❖ ⬩----------------------------------⬩ ❖\n"
        f"🔸 تعداد اشتراک‌های خریداری شده: {stats.get('services_total')}\n"
        f"🔸 تعداد اشتراک‌های متصل شده: {stats.get('services_active')}\n"
        f"🔸 تعداد تراکنشات: {stats.get('tx_total')}\n"
        f"🔸 تعداد تراکنشات تایید شده: {stats.get('tx_approved')}\n"
        "❖ ⬩----------------------------------⬩ ❖\n"
        f"🔸 تعداد سفارشات: {stats.get('orders_count')}\n"
        f"🔸 مجموع حجم سفارشات(GB): {gb_str}\n"
        f"🔸 مجموع ارزش سفارشات: {price}تومان"
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            "\U0001f465 <b>\u0645\u062f\u06cc\u0631\u06cc\u062a \u06a9\u0627\u0631\u0628\u0631\u0627\u0646 \u0631\u0628\u0627\u062a</b>",
            reply_markup=settings_sub_menu_keyboard("set:users", "\U0001f465 \u0644\u06cc\u0633\u062a \u06a9\u0627\u0631\u0628\u0631\u0627\u0646 \u0631\u0628\u0627\u062a"), parse_mode="HTML",
        )
        return

    if (p2 == "back" and p1 == "set") or (p2 == "users" and p3 == "back"):
        from AgentBot.keyboards import settings_menu_keyboard
        await query.edit_message_text(
            "\u2699\ufe0f <b>\u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u0631\u0628\u0627\u062a</b>",
            reply_markup=settings_menu_keyboard(), parse_mode="HTML",
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
            f"\U0001f4cb <b>\u0644\u06cc\u0633\u062a \u06a9\u0627\u0631\u0628\u0631\u0627\u0646 \u0631\u0628\u0627\u062a</b>",
            f"\u062a\u0639\u062f\u0627\u062f \u06a9\u0644: {total}",
            f"\u0635\u0641\u062d\u0647: {page}/{total_pages}",
            "",
        ]
        if not customers:
            lines.append("\u0647\u06cc\u0686 \u06a9\u0627\u0631\u0628\u0631\u06cc \u0648\u062c\u0648\u062f \u0646\u062f\u0627\u0631\u062f.")
        rows = []
        if customers:
            row = []
            for c in customers:
                name = _escape(c.get("full_name", "") or c.get("username", "")) or f"\u06a9\u0627\u0631\u0628\u0631 #{c['id']}"
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
        rows.append([IButton(BTN_BACK, callback_data="agbot:set:back")])
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
            await query.edit_message_text(
                "\U0001f50d \u0646\u0627\u0645 \u06cc\u0627 \u0622\u06cc\u062f\u06cc \u06a9\u0627\u0631\u0628\u0631 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if p3 == "services":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        services = agent_db.get_services_by_customer(customer_id)
        lines = [f"📋 <b>لیست سرویس‌ها</b> ({len(services)})"]
        if not services:
            lines.append("مشتری سرویسی ندارد.")
        else:
            for s in services:
                sname = _escape(s.get("name", "")) or f"سرویس #{s['id']}"
                active = "🟢" if int(s.get("is_active", 0) or 0) == 1 else "🔴"
                trial = " (تست)" if int(s.get("is_trial", 0) or 0) == 1 else ""
                lines.append(f"{active} {sname}{trial}")
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=back_keyboard(f"agbot:set:users:detail:{customer_id}"),
            parse_mode="HTML",
        )
        return

    if p3 == "orders":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        customer = agent_db.get_customer_by_id(customer_id)
        telegram_id = int((customer or {}).get("telegram_id") or 0)
        if not telegram_id:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        from CustomerBot.database import get_user_orders
        orders = get_user_orders(agent_id, telegram_id, limit=20)
        lines = [f"📗 <b>لیست سفارشات</b> ({len(orders)})"]
        if not orders:
            lines.append("مشتری سفارشی ندارد.")
        else:
            for o in orders:
                price = _fmt_toman(int(o.get("price") or 0))
                status = _status_icon(o.get("status", ""))
                lines.append(
                    f"{status} <b>{_escape(o.get('plan_title', '') or 'سفارش')}</b> • {price} تومان • {_escape(str(o.get('created_at', ''))[:16])}"
                )
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=back_keyboard(f"agbot:set:users:detail:{customer_id}"),
            parse_mode="HTML",
        )
        return

    if p3 == "tx":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        customer = agent_db.get_customer_by_id(customer_id)
        telegram_id = int((customer or {}).get("telegram_id") or 0)
        if not telegram_id:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        from CustomerBot.database import get_full_customer_stats
        stats = get_full_customer_stats(agent_id, telegram_id)
        lines = [f"💵 <b>لیست تراکنش‌ها</b>\n"]
        if not stats.get("tx_total"):
            lines.append("مشتری تراکنشی ندارد.")
        else:
            lines.append(f"🔸 تعداد تراکنشات: {stats.get('tx_total')}")
            lines.append(f"🔸 تایید شده: {stats.get('tx_approved')}")
            lines.append(f"🔸 در انتظار: {int(stats.get('tx_total')) - int(stats.get('tx_approved'))}")
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=back_keyboard(f"agbot:set:users:detail:{customer_id}"),
            parse_mode="HTML",
        )
        return

    if p3 == "ban":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        customer = agent_db.get_customer_by_id(customer_id)
        telegram_id = int((customer or {}).get("telegram_id") or 0)
        if not telegram_id:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        from CustomerBot.database import get_full_customer_stats, get_user, set_user_banned
        u = get_user(agent_id, telegram_id) or {}
        banned = int(u.get("is_banned") or 0)
        ok = set_user_banned(agent_id, telegram_id, not banned)
        if ok:
            state = "مسدود شد 🔴" if not banned else "آزاد شد 🟢"
            await query.answer(f"کاربر {state}", show_alert=True)
        else:
            await query.answer("خطا در تغییر وضعیت.", show_alert=True)
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
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        customer = agent_db.get_customer_by_id(customer_id)
        telegram_id = int((customer or {}).get("telegram_id") or 0)
        if not telegram_id:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        from CustomerBot.database import get_user_tickets
        tickets = get_user_tickets(agent_id, telegram_id)
        lines = [f"📑 <b>لیست تیکت‌ها</b> ({len(tickets)})"]
        if not tickets:
            lines.append("مشتری تیکتی ندارد.")
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
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        context.user_data["userbot_msg_target"] = telegram_id
        context.user_data[UD_STATE] = "st:send_user_msg"
        context.user_data.pop("subs_back_to", None)
        await query.edit_message_text(
            "✍️ لطفا متن پیامی که می خواهید برای کاربر ارسال شود را وارد کنید:",
        )
        await query.message.reply_text(
            "✍️ لطفا متن پیامی که می خواهید برای کاربر ارسال شود را وارد کنید:",
            reply_markup=send_msg_keyboard(),
        )
        return

    if p3 == "detail":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        customer = agent_db.get_customer_by_id(customer_id)
        if not customer:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
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
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    state = context.user_data.get(UD_STATE)
    text = update.message.text.strip()

    if state in ("st:send_user_msg",):
        if text in {"بازگشت", "◀️ بازگشت", "❌ لغو", "لغو", "/cancel"}:
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop("userbot_msg_target", None)
            await update.message.reply_text("عملیات لغو شد.", reply_markup=main_menu_keyboard())
            return True
        if not text:
            await update.message.reply_text("📨 متن پیام خالی است. لطفاً متن را بنویسید:")
            return True
        target = int(context.user_data.get("userbot_msg_target") or 0)
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop("userbot_msg_target", None)
        if not target:
            await update.message.reply_text("❌ کاربر پیدا نشد.", reply_markup=main_menu_keyboard())
            return True
        from Shared.agent_db import get_active_customer_bot
        from telegram import Bot
        bot_row = get_active_customer_bot(agent_id)
        token = str((bot_row or {}).get("bot_token") or "").strip()
        if not token:
            await update.message.reply_text("❌ ربات مشتری برای این نماینده فعال نیست.", reply_markup=main_menu_keyboard())
            return True
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("📩 پاسخ", callback_data="agmsg:reply")]]
            )
            await Bot(token=token).send_message(
                chat_id=target,
                text=f"📨 پیام از طرف نماینده:\n\n{text}",
                reply_markup=kb,
            )
            await update.message.reply_text("📩پیام ارسال شد", reply_markup=main_menu_keyboard())
        except Exception as e:
            logger.warning("send user msg failed tg_id=%s: %s", target, e)
            await update.message.reply_text(f"❌ ارسال پیام ناموفق بود:\n{e}", reply_markup=main_menu_keyboard())
        return True

    if state not in ("st:search_user",):
        return False
    if text in {"بازگشت", "❌ لغو", "لغو", "/cancel"}:
        context.user_data.pop(UD_STATE, None)
        from AgentBot.keyboards import settings_menu_keyboard
        await update.message.reply_text("⚙️ <b>تنظیمات ربات</b>", reply_markup=settings_menu_keyboard(), parse_mode="HTML")
        return True
    customers = agent_db.search_customers(agent_id, text, limit=10)
    if not customers:
        await update.message.reply_text("\u0647\u06cc\u0686 \u06a9\u0627\u0631\u0628\u0631\u06cc \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f.")
        return True
    from AgentBot.keyboards import _ikb
    from Shared.tg_button_styles import inline_button as IButton
    from AgentBot.keyboards import main_menu_keyboard
    rows = []
    for c in customers:
        name = _escape(c.get("full_name", "") or c.get("username", "")) or f"\u06a9\u0627\u0631\u0628\u0631 #{c['id']}"
        rows.append([IButton(name, callback_data=f"agbot:set:users:detail:{c['id']}")])
    await update.message.reply_text(
        f"\U0001f50d \u0646\u062a\u0627\u06cc\u062c \u0628\u0631\u0627\u06cc \"{_escape(text)}\":",
        reply_markup=_ikb(rows), parse_mode="HTML",
    )
    context.user_data.pop(UD_STATE, None)
    return True
