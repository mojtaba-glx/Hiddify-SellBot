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
    """متن جزئیات سفارش (مثل ربات ادمین)."""
    return (
        f"\u25c8 \u0634\u0646\u0627\u0633\u0647 \u0633\u0641\u0627\u0631\u0634: {order.get('id')}\n"
        f"\U0001f464 \u0645\u0634\u062a\u0631\u06cc: {_escape(order.get('customer_name', '') or '-')}\n"
        f"\u25c8 \u062a\u0627\u0631\u06cc\u062e \u0633\u0641\u0627\u0631\u0634: {_escape(order.get('created_at', '') or '-')}\n"
        f"\u25c8 \u0645\u0628\u0644\u063a \u0633\u0641\u0627\u0631\u0634: {_fmt_toman(order.get('amount', 0))} \u062a\u0648\u0645\u0627\u0646\n"
        f"\u2756 \u2022 -------------------------- \u2022 \u2756\n"
        f"\u25c8 \u0648\u0636\u0639\u06cc\u062a: {_status_icon(order.get('status', ''))} {_escape(order.get('status', ''))}\n"
        f"\u25c8 \u0646\u0648\u0639 \u0633\u0641\u0627\u0631\u0634: {_escape(order.get('order_type', '') or '-')}\n"
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

    if p1 == "set" and p2 == "orders" and not p3:
        await query.edit_message_text(
            "\U0001f4e6 <b>\u0645\u062f\u06cc\u0631\u06cc\u062a \u0633\u0641\u0627\u0631\u0634\u0627\u062a</b>",
            reply_markup=orders_menu_keyboard(), parse_mode="HTML",
        )
        return

    if (p2 == "back" and p1 == "set") or (p2 == "orders" and p3 == "back"):
        from AgentBot.keyboards import settings_menu_keyboard
        await query.edit_message_text(
            "\u2699\ufe0f <b>\u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u0631\u0628\u0627\u062a</b>",
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
            f"\U0001f539 \u0644\u06cc\u0633\u062a \u0633\u0641\u0627\u0631\u0634\u0627\u062a\n"
            f"\U0001f538 \u062a\u0639\u062f\u0627\u062f \u0633\u0641\u0627\u0631\u0634\u0627\u062a: {stats['total_count']}\n"
            f"\U0001f538 \u0645\u062c\u0645\u0648\u0639 \u062d\u062c\u0645 \u0633\u0641\u0627\u0631\u0634\u0627\u062a(GB): {_fmt_gb(stats['total_gb'])}\n"
            f"\U0001f538 \u0645\u062c\u0645\u0648\u0639 \u0627\u0631\u0632\u0634 \u0633\u0641\u0627\u0631\u0634\u0627\u062a: {_fmt_toman(stats['total_amount'])}\u062a\u0648\u0645\u0627\u0646\n"
            f"\u2756 \u2b29----------------------------------\u2b29 \u2756\n"
            f"\U0001f538 \u062a\u0639\u062f\u0627\u062f \u0633\u0641\u0627\u0631\u0634\u0627\u062a 30 \u0631\u0648\u0632 \u06af\u0630\u0634\u062a\u0647: {stats['last30_count']}\n"
            f"\U0001f538 \u062d\u062c\u0645 \u0633\u0641\u0627\u0631\u0634\u0627\u062a 30 \u0631\u0648\u0632 \u06af\u0630\u0634\u062a\u0647(GB): {_fmt_gb(stats['last30_gb'])}\n"
            f"\U0001f538 \u0627\u0631\u0632\u0634 \u0633\u0641\u0627\u0631\u0634\u0627\u062a 30 \u0631\u0648\u0632 \u06af\u0630\u0634\u062a\u0647: {_fmt_toman(stats['last30_amount'])}\u062a\u0648\u0645\u0627\u0646\n"
            f"\u2756 \u2b29----------------------------------\u2b29 \u2756\n"
            f"\U0001f538 \u062a\u0639\u062f\u0627\u062f \u0633\u0641\u0627\u0631\u0634\u0627\u062a \u0627\u06cc\u0646 \u0645\u0627\u0647: {stats['month_count']}\n"
            f"\U0001f538 \u062d\u062c\u0645 \u0633\u0641\u0627\u0631\u0634\u0627\u062a \u0627\u06cc\u0646 \u0645\u0627\u0647(GB): {_fmt_gb(stats['month_gb'])}\n"
            f"\U0001f538 \u0627\u0631\u0632\u0634 \u0633\u0641\u0627\u0631\u0634\u0627\u062a \u0627\u06cc\u0646 \u0645\u0627\u0647: {_fmt_toman(stats['month_amount'])}\u062a\u0648\u0645\u0627\u0646"
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
            await query.answer("\u274c \u0633\u0641\u0627\u0631\u0634 \u06cc\u0627\u0641\u062a \u0646\u0634\u062f.", show_alert=True)
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
                "\U0001f50d <b>\u062c\u0633\u062a\u062c\u0648\u06cc \u0633\u0641\u0627\u0631\u0634</b>\n"
                "\u0634\u0646\u0627\u0633\u0647 \u0633\u0641\u0627\u0631\u0634 (\u0639\u062f\u062f) \u06cc\u0627 \u0646\u0627\u0645 \u0645\u0634\u062a\u0631\u06cc \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
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
        await update.message.reply_text("⚙️ <b>تنظیمات ربات</b>", reply_markup=settings_menu_keyboard(), parse_mode="HTML")
        return True

    # جستجوی مستقیم با شناسه سفارش (مثل ربات ادمین)
    if text.isdigit():
        oid = int(text)
        order = get_order_by_id(agent_id, oid)
        context.user_data.pop(UD_STATE, None)
        if not order:
            await update.message.reply_text(
                f"❌ سفارشی با شناسه {oid} یافت نشد.",
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
            "❌ هیچ سفارشی با این مشخصات پیدا نشد.",
            reply_markup=orders_menu_keyboard(), parse_mode="HTML",
        )
        return True
    lines = [f"\U0001f50d <b>نتایج جستجو برای \"{_escape(text)}\"</b> ({len(orders)}):\n"]
    for o in orders:
        lines.append(
            f"{_status_icon(o.get('status', ''))} #{o.get('id')} \u2022 {_escape(o.get('customer_name', ''))} \u2022 "
            f"{_fmt_toman(o.get('amount', 0))} تومان"
        )
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=order_search_results_keyboard(orders),
        parse_mode="HTML",
    )
    return True
