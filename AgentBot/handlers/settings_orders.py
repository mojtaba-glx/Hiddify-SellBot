from Shared import i18n
import logging

from telegram import Update
from telegram.ext import ContextTypes

from AgentBot.constants import UD_STATE, STATE_SEARCH_ORDER
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import orders_menu_keyboard, back_keyboard, cancel_keyboard, orders_list_keyboard, order_search_results_keyboard
from AgentBot.utils.helpers import _escape, _fmt_toman, _fmt_gb, _status_icon
from AgentBot.database import get_orders, get_order_stats, get_order_by_id, search_orders

logger = logging.getLogger(__name__)

_PAGE_SIZE = 9


def _build_order_detail_text(order) -> str:
    """متن جزئیات سفارش (هماهنگ با customer_orders)."""
    _lg = "fa"
    oid = order.get('order_id') or order.get('id') or '?'
    name = order.get('full_name') or order.get('customer_name') or '-'
    amt = order.get('price') or order.get('amount') or 0
    return (
        f"{i18n.t('◈ شناسه سفارش: ', _lg)}{oid}{i18n.t('\n👤 مشتری: ', _lg)}{_escape(name)}{i18n.t('\n◈ تاریخ سفارش: ', _lg)}{_escape(order.get('created_at', '') or '-')}{i18n.t('\n◈ مبلغ سفارش: ', _lg)}{_fmt_toman(amt)}{i18n.t(' تومان\n⬖ حجم: ', _lg)}{_fmt_gb(order.get('volume_gb', 0))}{i18n.t('GB\n⬖ مدت: ', _lg)}{order.get('days', 0)}{i18n.t(' روز\n⬖ پلن: ', _lg)}{_escape(order.get('plan_title', '') or '-')}{i18n.t('\n⬖ سرور: ', _lg)}{_escape(order.get('server_location', '') or '-')}{i18n.t('\n⬖ نوع سفارش: ', _lg)}{_escape(order.get('order_type', '') or '-')}{i18n.t('\n⬖ وضعیت: ', _lg)}{_status_icon(order.get('status', ''))} {_escape(order.get('status', ''))}\n"
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

    if p1 == "set" and p2 == "orders" and not p3:
        await query.edit_message_text(
            i18n.t('📦 <b>مدیریت سفارشات</b>', _lg),
            reply_markup=orders_menu_keyboard(), parse_mode="HTML",
        )
        return

    if (p2 == "back" and p1 == "set") or (p2 == "orders" and p3 == "back"):
        from AgentBot.keyboards import settings_menu_keyboard
        await query.edit_message_text(
            i18n.t('⚙️ <b>تنظیمات ربات</b>', _lg),
            reply_markup=settings_menu_keyboard(), parse_mode="HTML",
        )
        return

    if p3 == "noop":
        await query.answer()
        return

    if p3 == "list":
        page = int(p4) if p4.isdigit() else 1
        stats = get_order_stats(agent_id)
        total_pages = max(1, (int(stats["total_count"]) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        if page > total_pages:
            page = total_pages
        orders, _ = get_orders(agent_id, page=page, page_size=_PAGE_SIZE)
        text = (
            f"{i18n.t('🔹 لیست سفارشات\n🔸 تعداد سفارشات: ', _lg)}{stats['total_count']}{i18n.t('\n🔸 مجموع حجم سفارشات(GB): ', _lg)}{_fmt_gb(stats['total_gb'])}{i18n.t('\n🔸 مجموع ارزش سفارشات: ', _lg)}{_fmt_toman(stats['total_amount'])}{i18n.t('تومان\n❖ ⬩----------------------------------⬩ ❖\n🔸 تعداد سفارشات 30 روز گذشته: ', _lg)}{stats['last30_count']}{i18n.t('\n🔸 حجم سفارشات 30 روز گذشته(GB): ', _lg)}{_fmt_gb(stats['last30_gb'])}{i18n.t('\n🔸 ارزش سفارشات 30 روز گذشته: ', _lg)}{_fmt_toman(stats['last30_amount'])}{i18n.t('تومان\n❖ ⬩----------------------------------⬩ ❖\n🔸 تعداد سفارشات این ماه: ', _lg)}{stats['month_count']}{i18n.t('\n🔸 حجم سفارشات این ماه(GB): ', _lg)}{_fmt_gb(stats['month_gb'])}{i18n.t('\n🔸 ارزش سفارشات این ماه: ', _lg)}{_fmt_toman(stats['month_amount'])}{i18n.t('تومان', _lg)}"
        )
        try:
            await query.edit_message_text(
                text,
                reply_markup=orders_list_keyboard(orders, page, total_pages),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if p3 == "detail":
        order_id = int(p4) if p4.isdigit() else 0
        order = get_order_by_id(agent_id, order_id)
        if not order:
            await query.answer(i18n.t('❌ سفارش یافت نشد.', _lg), show_alert=True)
            return
        text = _build_order_detail_text(order)
        try:
            await query.edit_message_text(
                text,
                reply_markup=back_keyboard("agbot:set:orders"),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if p3 == "search":
        context.user_data[UD_STATE] = STATE_SEARCH_ORDER
        try:
            await query.answer()
        except Exception:
            pass
        try:
            await query.message.reply_text(
                i18n.t('🔍 <b>جستجوی سفارش</b>\nشناسه سفارش (عدد) یا نام مشتری را وارد کنید:', _lg),
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
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
    if state != STATE_SEARCH_ORDER:
        return False
    text = update.message.text.strip()
    if text in {"بازگشت", "❌ لغو", "لغو", "/cancel"}:
        context.user_data.pop(UD_STATE, None)
        from AgentBot.keyboards import settings_menu_keyboard
        await update.message.reply_text(i18n.t("ag_settings_title", agent_lang(context)), reply_markup=settings_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML")
        return True

    # جستجوی مستقیم با شناسه سفارش (مثل ربات ادمین)
    if text.isdigit():
        oid = int(text)
        order = get_order_by_id(agent_id, oid)
        context.user_data.pop(UD_STATE, None)
        if not order:
            await update.message.reply_text(
                f"{i18n.t('❌ سفارشی با شناسه ', _lg)}{oid}{i18n.t(' یافت نشد.', _lg)}",
                reply_markup=orders_menu_keyboard(), parse_mode="HTML",
            )
            return True
        await update.message.reply_text(
            _build_order_detail_text(order),
            reply_markup=back_keyboard("agbot:set:orders"),
            parse_mode="HTML",
        )
        return True

    # جستجو بر اساس نام مشتری
    orders = search_orders(agent_id, text, limit=15)
    context.user_data.pop(UD_STATE, None)
    if not orders:
        await update.message.reply_text(
            i18n.t('❌ هیچ سفارشی با این مشخصات پیدا نشد.', _lg),
            reply_markup=orders_menu_keyboard(), parse_mode="HTML",
        )
        return True
    lines = [f"{i18n.t('🔍 <b>نتایج جستجو برای "', _lg)}{_escape(text)}\"</b> ({len(orders)}):\n"]
    for o in orders:
        oid = o.get('order_id') or o.get('id') or '?'
        name = o.get('full_name') or o.get('customer_name') or '-'
        amt = o.get('price') or o.get('amount') or 0
        lines.append(
            f"{_status_icon(o.get('status', ''))} #{oid} • {_escape(name)} • {_fmt_toman(amt)}{i18n.t(' تومان', _lg)}"
        )
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=order_search_results_keyboard(orders),
        parse_mode="HTML",
    )
    return True
