import json
import logging
from typing import Any, Dict

from telegram import Update
from telegram.ext import ContextTypes

from AgentBot.constants import UD_STATE, STATE_SEARCH_TX
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import tx_menu_keyboard, back_keyboard, cancel_keyboard, tx_list_keyboard, tx_search_results_keyboard
from AgentBot.utils.helpers import _escape, _fmt_toman, _status_icon
from AgentBot.database import (
    get_customer_payments,
    get_customer_payment_stats,
    search_customer_payments,
    get_customer_payment_detail,
)

logger = logging.getLogger(__name__)

_PAGE_SIZE = 8

_STATUS_TITLES = {
    "approved": "\u2705 \u062a\u0627\u06cc\u06cc\u062f \u0634\u062f\u0647",
    "rejected": "\u274c \u0631\u062f \u0634\u062f\u0647",
    "pending": "\u23f3 \u062f\u0631 \u0627\u0646\u062a\u0638\u0627\u0631",
}

_METHOD_TITLES = {
    "card": "\u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a",
}


def _payment_customer_name(pay: Dict[str, Any]) -> str:
    name = (pay.get("full_name") or pay.get("username") or "").strip()
    if not name:
        name = f"\u06a9\u0627\u0631\u0628\u0631 #{pay.get('user_id', '-')}"
    return name


def _payment_method_title(pay: Dict[str, Any]) -> str:
    method = str(pay.get("method") or "").strip()
    return _METHOD_TITLES.get(method, method or "-")


def _parse_receipt_card_last4(pay: Dict[str, Any]) -> str:
    raw = str(pay.get("receipt_image") or "").strip()
    if not raw:
        return ""
    try:
        meta = json.loads(raw)
        if isinstance(meta, dict):
            return str(meta.get("card_last4") or "").strip()
    except (json.JSONDecodeError, TypeError):
        pass
    return ""


def _build_payment_detail_text(pay: Dict[str, Any]) -> str:
    lines = [
        f"\u25c8 \u0634\u0646\u0627\u0633\u0647 \u062a\u0631\u0627\u06a9\u0646\u0634: {pay.get('id')}",
        f"\U0001f464 \u0645\u0634\u062a\u0631\u06cc: {_escape(_payment_customer_name(pay))}",
    ]
    tx_code = str(pay.get("tx_code") or "").strip()
    if tx_code:
        lines.append(f"\U0001f511 \u0634\u0646\u0627\u0633\u0647 \u062a\u0631\u0627\u06a9\u0646\u0634 \u0628\u0627\u0646\u06a9: {tx_code}")
    lines.append(f"\u25c8 \u062a\u0627\u0631\u06cc\u062e \u062a\u0631\u0627\u06a9\u0646\u0634: {_escape(pay.get('created_at', '') or '-')}")
    lines.append(f"\u25c8 \u0645\u0628\u0644\u063a \u062a\u0631\u0627\u06a9\u0646\u0634: {_fmt_toman(pay.get('amount', 0))} \u062a\u0648\u0645\u0627\u0646")
    lines.append("\u2756 \u2022 -------------------------- \u2022 \u2756")
    lines.append(f"\u25c8 \u0648\u0636\u0639\u06cc\u062a: {_STATUS_TITLES.get(str(pay.get('status')), pay.get('status'))}")
    lines.append(f"\u25c8 \u0631\u0648\u0634 \u062a\u0631\u0627\u06a9\u0646\u0634: {_payment_method_title(pay)}")
    card_last4 = _parse_receipt_card_last4(pay)
    if card_last4:
        lines.append(f"\U0001f4b3 \u06a9\u0627\u0631\u062a (\u0622\u062e\u0631\u06cc\u0646 \u0686\u0647\u0627\u0631 \u0631\u0642\u0645): {card_last4}")
    return "\n".join(lines)


def _filter_params(p3: str) -> Dict[str, Any]:
    status = None
    method = None
    if p3 == "card":
        method = "card"
    elif p3 in ("approved", "rejected", "pending"):
        status = p3
    return {"status": status, "method": method}


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
        fp = _filter_params(p3)
        stats = get_customer_payment_stats(agent_id, status=fp["status"], method=fp["method"])
        total_pages = max(1, (stats["total_count"] + _PAGE_SIZE - 1) // _PAGE_SIZE)
        if page > total_pages:
            page = total_pages
        payments, _ = get_customer_payments(
            agent_id, status=fp["status"], method=fp["method"], page=page, page_size=_PAGE_SIZE,
        )
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
        pay = get_customer_payment_detail(pay_id)
        if not pay:
            await query.answer("\u274c \u062a\u0631\u0627\u06a9\u0646\u0634 \u06cc\u0627\u0641\u062a \u0646\u0634\u062f.", show_alert=True)
            return
        text = _build_payment_detail_text(pay)
        try:
            await query.edit_message_text(text, reply_markup=back_keyboard(f"agbot:set:tx:{pay.get('status')}"), parse_mode="HTML")
        except Exception:
            pass
        return

    if p3 == "search":
        context.user_data[UD_STATE] = STATE_SEARCH_TX
        try:
            await query.answer()
        except Exception:
            pass
        try:
            await query.message.reply_text(
                "\U0001f50d <b>\u062c\u0633\u062a\u062c\u0648\u06cc \u062a\u0631\u0627\u06a9\u0646\u0634</b>\n"
                "\u0634\u0646\u0627\u0633\u0647 \u062a\u0631\u0627\u06a9\u0646\u0634 (\u0639\u062f\u062f) \u06cc\u0627 \u0646\u0627\u0645 \u0645\u0634\u062a\u0631\u06cc \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
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

    if text.isdigit():
        pid = int(text)
        pay = get_customer_payment_detail(pid)
        context.user_data.pop(UD_STATE, None)
        if not pay or int(pay.get("agent_id") or 0) != agent_id:
            await update.message.reply_text(f"❌ تراکنشی با شناسه {pid} یافت نشد.", reply_markup=tx_menu_keyboard(), parse_mode="HTML")
            return True
        await update.message.reply_text(
            _build_payment_detail_text(pay),
            reply_markup=back_keyboard(f"agbot:set:tx:{pay.get('status')}"),
            parse_mode="HTML",
        )
        return True

    payments = search_customer_payments(agent_id, text, limit=15)
    context.user_data.pop(UD_STATE, None)
    if not payments:
        await update.message.reply_text(
            "❌ هیچ تراکنشی با این مشخصات پیدا نشد.",
            reply_markup=tx_menu_keyboard(), parse_mode="HTML",
        )
        return True
    lines = [f"\U0001f50d <b>نتایج جستجو برای \"{_escape(text)}\"</b> ({len(payments)}):\n"]
    for p in payments:
        lines.append(
            f"{_status_icon(p.get('status', ''))} #{p.get('id')} \u2022 {_escape(_payment_customer_name(p))} \u2022 "
            f"{_fmt_toman(p.get('amount', 0))} تومان"
        )
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=tx_search_results_keyboard(payments),
        parse_mode="HTML",
    )
    return True
