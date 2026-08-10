import logging

from telegram import Update
from telegram.ext import ContextTypes

from AgentBot.constants import UD_STATE, STATE_SEARCH_TX
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import tx_menu_keyboard, back_keyboard, cancel_keyboard, tx_list_keyboard
from AgentBot.utils.helpers import _escape, _fmt_toman, _status_icon
from AgentBot.database import get_payments, search_payments, get_payment_stats, get_payment_by_id

logger = logging.getLogger(__name__)

_PAGE_SIZE = 8


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

    if p1 == "set" and p2 == "tx" and not p3:
        await query.edit_message_text(
            "\U0001f4b3 <b>\u0645\u062f\u06cc\u0631\u06cc\u062a \u062a\u0631\u0627\u06a9\u0646\u0634\u0627\u062a</b>",
            reply_markup=tx_menu_keyboard(), parse_mode="HTML",
        )
        return

    if (p2 == "back" and p1 == "set") or (p2 == "tx" and p3 == "back"):
        from AgentBot.keyboards import settings_menu_keyboard
        await query.edit_message_text(
            "\u2699\ufe0f <b>\u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u0631\u0628\u0627\u062a</b>",
            reply_markup=settings_menu_keyboard(), parse_mode="HTML",
        )
        return

    if p3 in ("approved", "rejected", "pending", "card"):
        page = int(p4) if p4 and p4.isdigit() else 1
        status = p3 if p3 != "card" else None
        method = "card_to_card" if p3 == "card" else None
        stats = get_payment_stats(agent_id, status=status, method=method)
        total_pages = max(1, (int(stats["total_count"]) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        if page > total_pages:
            page = total_pages
        payments, _ = get_payments(agent_id, status=status, method=method, page=page, page_size=_PAGE_SIZE)
        header_titles = {
            "approved": "\u0644\u06cc\u0633\u062a \u062a\u0631\u0627\u06a9\u0646\u0634\u0627\u062a \u062a\u0627\u06cc\u06cc\u062f \u0634\u062f\u0647 \u2705",
            "rejected": "\u0644\u06cc\u0633\u062a \u062a\u0631\u0627\u06a9\u0646\u0634\u0627\u062a \u0631\u062f \u0634\u062f\u0647 \u274c",
            "pending": "\u0644\u06cc\u0633\u062a \u062a\u0631\u0627\u06a9\u0646\u0634\u0627\u062a \u062f\u0631 \u0627\u0646\u062a\u0638\u0627\u0631 \u23f3",
            "card": "\u0644\u06cc\u0633\u062a \u062a\u0631\u0627\u06a9\u0646\u0634\u0627\u062a \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a \U0001f4b3",
        }
        text = (
            f"\U0001f539 {header_titles[p3]}\n"
            f"\U0001f538 \u062a\u0639\u062f\u0627\u062f \u062a\u0631\u0627\u06a9\u0646\u0634\u0627\u062a: {stats['total_count']}\n"
            f"\U0001f538 \u0645\u0628\u0644\u063a \u062a\u0631\u0627\u06a9\u0646\u0634\u0627\u062a: {_fmt_toman(stats['total_amount'])} \u062a\u0648\u0645\u0627\u0646\n"
            f"\u2756 \u2b2c----------------------------------\u2b2c \u2756\n"
            f"\U0001f538 \u062a\u0631\u0627\u06a9\u0646\u0634\u0627\u062a 30 \u0631\u0648\u0632 \u06af\u0630\u0634\u062a\u0647: {stats['last30_count']}\n"
            f"\U0001f538 \u0645\u0628\u0644\u063a \u062a\u0631\u0627\u06a9\u0646\u0634\u0627\u062a 30 \u0631\u0648\u0632 \u06af\u0630\u0634\u062a\u0647: {_fmt_toman(stats['last30_amount'])} \u062a\u0648\u0645\u0627\u0646\n"
            f"\u2756 \u2b2c----------------------------------\u2b2c \u2756\n"
            f"\U0001f538 \u062a\u0631\u0627\u06a9\u0646\u0634\u0627\u062a \u0627\u06cc\u0646 \u0645\u0627\u0647: {stats['month_count']}\n"
            f"\U0001f538 \u0645\u0628\u0644\u063a \u062a\u0631\u0627\u06a9\u0646\u0634\u0627\u062a \u0627\u06cc\u0646 \u0645\u0627\u0647: {_fmt_toman(stats['month_amount'])} \u062a\u0648\u0645\u0627\u0646"
        )
        try:
            await query.edit_message_text(
                text,
                reply_markup=tx_list_keyboard(payments, p3, page, total_pages),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if p3 == "noop":
        await query.answer()
        return

    if p3 == "detail":
        pay_id = int(p4) if p4 and p4.isdigit() else 0
        pay = get_payment_by_id(pay_id)
        if not pay:
            await query.answer("\u274c \u062a\u0631\u0627\u06a9\u0646\u0634 \u06cc\u0627\u0641\u062a \u0646\u0634\u062f.", show_alert=True)
            return
        status_titles = {
            "approved": "\u2705 \u062a\u0627\u06cc\u06cc\u062f \u0634\u062f\u0647",
            "rejected": "\u274c \u0631\u062f \u0634\u062f\u0647",
            "pending": "\u23f3 \u062f\u0631 \u0627\u0646\u062a\u0638\u0627\u0631",
        }
        text = (
            f"\u25c8 \u0634\u0646\u0627\u0633\u0647 \u062a\u0631\u0627\u06a9\u0646\u0634: {pay.get('id')}\n"
            f"\U0001f464 \u0645\u0634\u062a\u0631\u06cc: {_escape(pay.get('customer_name', '') or '-')}\n"
            f"\u25c8 \u062a\u0627\u0631\u06cc\u062e \u062a\u0631\u0627\u06a9\u0646\u0634: {_escape(pay.get('created_at', '') or '-')}\n"
            f"\u25c8 \u0645\u0628\u0644\u063a \u062a\u0631\u0627\u06a9\u0646\u0634: {_fmt_toman(pay.get('amount', 0))} \u062a\u0648\u0645\u0627\u0646\n"
            f"\u2756 \u2022 -------------------------- \u2022 \u2756\n"
            f"\u25c8 \u0648\u0636\u0639\u06cc\u062a: {status_titles.get(str(pay.get('status')), pay.get('status'))}\n"
            f"\u25c8 \u0631\u0648\u0634 \u062a\u0631\u0627\u06a9\u0646\u0634: {_escape(pay.get('method', '') or '-')}\n"
        )
        try:
            await query.edit_message_text(text, reply_markup=back_keyboard(f"agbot:set:tx:{pay.get('status')}"), parse_mode="HTML")
        except Exception:
            pass
        return

    if p3 == "search":
        context.user_data[UD_STATE] = STATE_SEARCH_TX
        try:
            await query.edit_message_text(
                "\U0001f50d \u0646\u0627\u0645 \u0645\u0634\u062a\u0631\u06cc \u06cc\u0627 \u0634\u0646\u0627\u0633\u0647 \u062a\u0631\u0627\u06a9\u0646\u0634 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
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
    if state != STATE_SEARCH_TX:
        return False
    text = update.message.text.strip()
    if text in {"بازگشت", "❌ لغو", "لغو", "/cancel"}:
        context.user_data.pop(UD_STATE, None)
        from AgentBot.keyboards import settings_menu_keyboard
        await update.message.reply_text("⚙️ <b>تنظیمات ربات</b>", reply_markup=settings_menu_keyboard(), parse_mode="HTML")
        return True
    payments = search_payments(agent_id, text, limit=10)
    if not payments:
        await update.message.reply_text("\u0647\u06cc\u0686 \u062a\u0631\u0627\u06a9\u0646\u0634\u06cc \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f.")
        return True
    from AgentBot.keyboards import main_menu_keyboard
    lines = [f"\U0001f50d <b>\u0646\u062a\u0627\u06cc\u062c \u062c\u0633\u062a\u062c\u0648 \u0628\u0631\u0627\u06cc \"{_escape(text)}\":</b>\n"]
    for p in payments:
        lines.append(
            f"{_status_icon(p.get('status', ''))} {_escape(p.get('customer_name', ''))} \u2022 "
            f"{_fmt_toman(p.get('amount', 0))} \u062a\u0648\u0645\u0627\u0646"
        )
    await update.message.reply_text("\n".join(lines), reply_markup=main_menu_keyboard(), parse_mode="HTML")
    context.user_data.pop(UD_STATE, None)
    return True
