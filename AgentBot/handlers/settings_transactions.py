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


def _parse_receipt_meta(raw: str) -> Dict[str, Any]:
    """پارس receipt_image: file_id ساده | JSON خالص | \"{json}|key:val|...\"."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    if "|" not in raw and ":" not in raw:
        return {"file_id": raw}
    if "{" in raw and "|" in raw:
        json_part, _, rest = raw.partition("|")
        meta: Dict[str, Any] = {}
        try:
            parsed = json.loads(json_part)
            if isinstance(parsed, dict):
                meta.update(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        for seg in rest.split("|"):
            seg = seg.strip()
            if ":" not in seg:
                continue
            k, _, v = seg.partition(":")
            k, v = k.strip(), v.strip()
            if k and v:
                meta[k] = v
        return meta
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def _parse_receipt_card_last4(pay: Dict[str, Any]) -> str:
    meta = _parse_receipt_meta(str(pay.get("receipt_image") or ""))
    return str(meta.get("card_last4") or "").strip()


def _build_payment_detail_text(pay: Dict[str, Any]) -> str:
    tx_code = str(pay.get("tx_code") or "").strip() or str(pay.get("id") or "-")
    username = (str(pay.get("username") or "").strip()) or "-"
    if username != "-":
        username = f"@{username}"
    lines = [
        f"\u25c8 \u0634\u0646\u0627\u0633\u0647 \u062a\u0631\u0627\u06a9\u0646\u0634: {_escape(tx_code)}",
        f"\U0001f464 \u06a9\u0627\u0631\u0628\u0631: {_escape((pay.get('full_name') or '-').strip() or '-')}",
        f"\u25c8 \u0646\u0627\u0645 \u06a9\u0627\u0631\u0628\u0631\u06cc: {_escape(username)}",
        f"\u25c8 \u0634\u0646\u0627\u0633\u0647 \u06a9\u0627\u0631\u0628\u0631: {_escape(str(pay.get('user_id') or '-'))}",
        f"\u25c8 \u062a\u0627\u0631\u06cc\u062e \u062a\u0631\u0627\u06a9\u0646\u0634: {_escape(pay.get('created_at', '') or '-')}",
        f"\u25c8 \u0645\u0628\u0644\u063a \u062a\u0631\u0627\u06a9\u0646\u0634: {_fmt_toman(pay.get('amount', 0))} \u062a\u0648\u0645\u0627\u0646",
        "\u2756 \u2022 -------------------------- \u2022 \u2756",
        f"\u25c8 \u0648\u0636\u0639\u06cc\u062a: {_STATUS_TITLES.get(str(pay.get('status')), pay.get('status'))}",
        f"\u25c8 \u0631\u0648\u0634 \u062a\u0631\u0627\u06a9\u0646\u0634: {_payment_method_title(pay)}",
    ]
    card_last4 = _parse_receipt_card_last4(pay)
    if card_last4:
        lines.append(f"\u25c8 \u06f4 \u0631\u0642\u0645 \u0622\u062e\u0631 \u06a9\u0627\u0631\u062a \u0645\u0628\u062f\u0627: {_escape(card_last4)}")
    return "\n".join(lines)


def _payment_receipt_fid(pay: Dict[str, Any]) -> str:
    meta = _parse_receipt_meta(str(pay.get("receipt_image") or ""))
    if meta.get("file_id"):
        return str(meta["file_id"]).strip()
    return ""


def _payment_user_button_title(pay: Dict[str, Any]) -> str:
    name = (str(pay.get("full_name") or pay.get("username") or "").strip())
    if name:
        return name
    uid = pay.get("user_id")
    return f"\u06a9\u0627\u0631\u0628\u0631 #{uid}" if uid else "\u067e\u0631\u0648\u0641\u0627\u06cc\u0644 \u06a9\u0627\u0631\u0628\u0631"


def _tx_detail_keyboard(pay: Dict[str, Any]):
    from AgentBot.keyboards import _ikb, IButton, BTN_BACK

    rows = [
        [IButton(f"\U0001f464 {_payment_user_button_title(pay)}", callback_data=f"agbot:custpay:profile:{pay.get('user_id', 0)}")],
        [IButton(BTN_BACK, callback_data=f"agbot:set:tx:{pay.get('status') or 'pending'}", style="danger")],
    ]
    return _ikb(rows)


async def _send_payment_detail(context: ContextTypes.DEFAULT_TYPE, chat_id: int, pay: Dict[str, Any], source_message=None) -> None:
    caption = _build_payment_detail_text(pay)
    kb = _tx_detail_keyboard(pay)
    receipt = _payment_receipt_fid(pay)

    if receipt:
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=receipt, caption=caption, reply_markup=kb, parse_mode="HTML")
            if source_message is not None:
                try:
                    await source_message.delete()
                except Exception:
                    pass
            return
        except Exception as e:
            logger.warning("send tx detail photo failed pay=%s: %s", pay.get("id"), e)

    if source_message is not None:
        await source_message.reply_text(caption, reply_markup=kb, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=caption, reply_markup=kb, parse_mode="HTML")


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
        await _send_payment_detail(context, query.message.chat_id, pay, source_message=query.message)
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
        receipt = _payment_receipt_fid(pay)
        kb = _tx_detail_keyboard(pay)
        caption = _build_payment_detail_text(pay)
        if receipt:
            try:
                await context.bot.send_photo(chat_id=update.message.chat_id, photo=receipt, caption=caption, reply_markup=kb, parse_mode="HTML")
            except Exception as e:
                logger.warning("send tx detail photo failed pay=%s: %s", pid, e)
                await update.message.reply_text(caption, reply_markup=kb, parse_mode="HTML")
        else:
            await update.message.reply_text(caption, reply_markup=kb, parse_mode="HTML")
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
