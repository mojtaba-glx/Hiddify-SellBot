import logging

from telegram import Bot, Update
from telegram.ext import ContextTypes

from AgentBot.constants import (
    TICKET_PENDING, TICKET_OPEN, TICKET_CLOSED,
    TICKET_VIEW, TICKET_REPLY, TICKET_CLOSE, TICKET_BACK,
    MENU_MAIN, UD_STATE, UD_SELECTED_TICKET, STATE_REPLY_TICKET,
)
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import tickets_menu_keyboard, ticket_detail_keyboard, back_keyboard, cancel_keyboard
from AgentBot.utils.helpers import _escape
from AgentBot.database import (
    get_customer_tickets, get_customer_ticket,
    get_customer_ticket_messages, add_customer_ticket_message, set_customer_ticket_status,
)

logger = logging.getLogger(__name__)

_STATUS_MAP = {
    "pending": "\u23f3 \u062f\u0631 \u0627\u0646\u062a\u0638\u0627\u0631",
    "open": "\U0001f4ec \u0628\u0627\u0632",
    "closed": "\u2705 \u0628\u0633\u062a\u0647",
}


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent_id = get_agent_id(context)
    pending = get_customer_tickets(agent_id, "pending")
    open_count = len(get_customer_tickets(agent_id, "open"))
    text = (
        f"\U0001f3ab <b>\u0645\u062f\u06cc\u0631\u06cc\u062a \u062a\u06cc\u06a9\u062a\u200c\u0647\u0627</b>\n\n"
        f"\u23f3 \u062f\u0631 \u0627\u0646\u062a\u0638\u0627\u0631: <b>{len(pending)}</b>\n"
        f"\U0001f4ec \u0628\u0627\u0632: <b>{open_count}</b>\n"
    )
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=tickets_menu_keyboard(), parse_mode="HTML")
        except Exception:
            await update.message.reply_text(text, reply_markup=tickets_menu_keyboard(), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=tickets_menu_keyboard(), parse_mode="HTML")


async def _send_ticket_list(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str) -> None:
    agent_id = get_agent_id(context)
    tickets = get_customer_tickets(agent_id, status)
    status_fa = _STATUS_MAP.get(status, status)
    from AgentBot.keyboards import _ikb
    from Shared.tg_button_styles import inline_button as IButton
    lines = [f"<b>{status_fa}</b> ({len(tickets)})\n"]
    if not tickets:
        lines.append("\u0647\u06cc\u0686 \u062a\u06cc\u06a9\u062a\u06cc \u0648\u062c\u0648\u062f \u0646\u062f\u0627\u0631\u062f.")
    else:
        for t in tickets:
            name = _escape(t.get("full_name", "")) or f"\u06a9\u0627\u0631\u0628\u0631 #{t.get('telegram_id', '?')}"
            title = _escape(str(t.get("title") or t.get("question", "")[:40] or "\u0628\u062f\u0648\u0646 \u0645\u0648\u0636\u0648\u0639")[:40])
            lines.append(f"\U0001f4ec <b>#{t['ticket_code']}</b> - {title}\n   \U0001f464 {name} \u2022 \U0001f4c5 {_escape(str(t.get('created_at', ''))[:16])}")
    rows = [[IButton(f"\U0001f4ec #{t['ticket_code']} - {_escape(str(t.get('title') or '')[:25])}", callback_data=f"agbot:ticket:view:{t['ticket_code']}")] for t in tickets[:10]]
    rows.append([IButton("\U0001f519 \u0628\u0627\u0632\u06af\u0634\u062a", callback_data="agbot:ticket:back")])
    query = update.callback_query
    try:
        await query.edit_message_text("\n".join(lines), reply_markup=_ikb(rows), parse_mode="HTML")
    except Exception:
        pass


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    parts = data.split(":")
    action = parts[2] if len(parts) > 2 else ""
    agent_id = get_agent_id(context)

    if action == "back":
        await show_menu(update, context)
        return

    if action in ("pending", "open", "closed"):
        await _send_ticket_list(update, context, action)
        return

    if action == "view":
        ticket_code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        context.user_data[UD_SELECTED_TICKET] = ticket_code
        ticket = get_customer_ticket(agent_id, ticket_code)
        if not ticket:
            await query.answer("\u062a\u06cc\u06a9\u062a \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f.", show_alert=True)
            return
        msgs = get_customer_ticket_messages(agent_id, ticket_code)
        status_fa = _STATUS_MAP.get(ticket.get("status", ""), ticket.get("status", ""))
        title = _escape(str(ticket.get("title") or ticket.get("question", "")[:50] or "\u0628\u062f\u0648\u0646 \u0645\u0648\u0636\u0648\u0639"))
        name = _escape(ticket.get("full_name", "")) or f"\u06a9\u0627\u0631\u0628\u0631 #{ticket.get('telegram_id', '?')}"

        # Build text summary
        text = (
            f"\U0001f4ec <b>\u062a\u06cc\u06a9\u062a #{ticket_code}</b>\n"
            f"\U0001f4cb \u0645\u0648\u0636\u0648\u0639: {title}\n"
            f"\U0001f464 \u0645\u0634\u062a\u0631\u06cc: {name}\n"
            f"\U0001f4c5 {_escape(str(ticket.get('created_at', ''))[:16])}\n"
            f"\U0001f4cc \u0648\u0636\u0639\u06cc\u062a: {status_fa}\n\n"
            f"\u2501\u2501\u2501 \u067e\u06cc\u0627\u0645\u200c\u0647\u0627 \u2501\u2501\u2501\n"
        )

        # Find first photo in messages
        first_photo_fid = ""
        if msgs:
            for m in msgs:
                _agent_label = '\u0646\u0645\u0627\u06cc\u0646\u062f\u0647'
                sender = "\U0001f464 \u0645\u0634\u062a\u0631\u06cc" if m.get("sender_type") == "user" else f"\U0001f916 {_escape(m.get('sender_name', _agent_label))}"
                msg_text = _escape(m.get("message_text", ""))
                photo_fid = m.get("photo_file_id", "")
                ts = _escape(str(m.get("created_at", ""))[:16])
                photo_tag = " \U0001f4f7 [\u0639\u06a9\u0633]" if photo_fid else ""
                text += f"\n{sender} ({ts}):\n{msg_text}{photo_tag}\n"
                if photo_fid and not first_photo_fid:
                    first_photo_fid = photo_fid
        else:
            text += "(\u067e\u06cc\u0627\u0645\u06cc \u0648\u062c\u0648\u062f \u0646\u062f\u0627\u0631\u062f)"

        kb = ticket_detail_keyboard(ticket_code, ticket.get("status", ""))

        try:
            await query.edit_message_text(text[:4000], reply_markup=kb, parse_mode="HTML")
            for m in msgs:
                photo_fid = m.get("photo_file_id", "")
                if photo_fid:
                    sender = "مشتری" if m.get("sender_type") == "user" else m.get("sender_name", "نماینده")
                    caption = f"📷 عکس تیکت #{ticket_code} - {sender}\n{m.get('message_text', '') or ''}"
                    await context.bot.send_photo(chat_id=query.message.chat_id, photo=photo_fid, caption=caption[:1024])
        except Exception:
            try:
                await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass
        return

    if action == "reply":
        ticket_code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        context.user_data[UD_SELECTED_TICKET] = ticket_code
        context.user_data[UD_STATE] = STATE_REPLY_TICKET
        try:
            await query.edit_message_text(
                "\U0001f4ac <b>\u067e\u0627\u0633\u062e \u0628\u0647 \u062a\u06cc\u06a9\u062a</b>\n\n"
                "\u0645\u062a\u0646 \u067e\u0627\u0633\u062e \u062e\u0648\u062f \u0631\u0627 \u0628\u0646\u0648\u06cc\u0633\u06cc\u062f:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "close":
        ticket_code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        ok = set_customer_ticket_status(agent_id, ticket_code, "closed")
        await query.answer("\u062a\u06cc\u06a9\u062a \u0628\u0633\u062a\u0647 \u0634\u062f \u2705" if ok else "\u062e\u0637\u0627!")
        if ok:
            try:
                await query.edit_message_reply_markup(reply_markup=ticket_detail_keyboard(ticket_code, "closed"))
            except Exception:
                pass
        return

    if action == "reopen":
        ticket_code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        ok = set_customer_ticket_status(agent_id, ticket_code, "open")
        await query.answer("\u062a\u06cc\u06a9\u062a \u062f\u0648\u0628\u0627\u0631\u0647 \u0628\u0627\u0632 \u0634\u062f \U0001f4ec" if ok else "\u062e\u0637\u0627!")
        try:
            await query.edit_message_reply_markup(reply_markup=ticket_detail_keyboard(ticket_code, "open"))
        except Exception:
            pass
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    state = context.user_data.get(UD_STATE)
    if state != STATE_REPLY_TICKET:
        return False
    ticket_code = context.user_data.get(UD_SELECTED_TICKET)
    if not ticket_code:
        return False
    text = (update.message.text or update.message.caption or "").strip()
    photo_file_id = ""
    if update.message.photo:
        photo_file_id = update.message.photo[-1].file_id
        if not text:
            text = "[عکس]"
    if not text and not photo_file_id:
        await update.message.reply_text("\u0645\u062a\u0646 \u06cc\u0627 \u0639\u06a9\u0633 \u067e\u06cc\u0627\u0645 \u0646\u0645\u06cc\u200c\u062a\u0648\u0627\u0646\u062f \u062e\u0627\u0644\u06cc \u0628\u0627\u0634\u062f.")
        return True
    agent_data = context.user_data.get("agent_data", {})
    name = agent_data.get("full_name", "") or agent_data.get("username", "") or f"\u0646\u0645\u0627\u06cc\u0646\u062f\u0647 #{agent_id}"
    add_customer_ticket_message(agent_id, ticket_code, "agent", name, text, photo_file_id)
    set_customer_ticket_status(agent_id, ticket_code, "open")
    # Notify customer via the customer's own bot token. Most customers never start AgentBot.
    ticket = get_customer_ticket(agent_id, ticket_code)
    if ticket and ticket.get("telegram_id"):
        try:
            from Shared.agent_db import get_all_active_customer_bots
            bot_rows = [b for b in get_all_active_customer_bots() if int(b.get("agent_id") or 0) == int(agent_id)]
            token = (bot_rows[0].get("bot_token") if bot_rows else "") or ""
            notify_bot = Bot(token=token) if token else context.bot
            notify_text = f"\U0001f4ac \u067e\u0627\u0633\u062e \u062c\u062f\u06cc\u062f \u0628\u0631\u0627\u06cc \u062a\u06cc\u06a9\u062a #{ticket_code}:\n\n{text}"
            if photo_file_id:
                await notify_bot.send_photo(chat_id=ticket["telegram_id"], photo=photo_file_id, caption=notify_text[:1024])
            else:
                await notify_bot.send_message(chat_id=ticket["telegram_id"], text=notify_text)
        except Exception as e:
            logger.warning(f"Failed to notify customer: {e}")
    context.user_data.pop(UD_STATE, None)
    context.user_data.pop(UD_SELECTED_TICKET, None)
    # برگشت به صفحه جزئیات تیکت با دکمه٬های پاسخ/بستن (مثل ربات ادمین)
    fresh = get_customer_ticket(agent_id, ticket_code)
    msgs = get_customer_ticket_messages(agent_id, ticket_code) if fresh else []
    status_fa = _STATUS_MAP.get((fresh or {}).get("status", ""), (fresh or {}).get("status", ""))
    title = _escape(str((fresh or {}).get("title") or (fresh or {}).get("question", "")[:50] or "\u0628\u062f\u0648\u0646 \u0645\u0648\u0636\u0648\u0639"))
    name = _escape((fresh or {}).get("full_name", "")) or f"\u06a9\u0627\u0631\u0628\u0631 #{(fresh or {}).get('telegram_id', '?')}"
    text = (
        f"\U0001f4ec <b>\u062a\u06cc\u06a9\u062a #{ticket_code}</b>\n"
        f"\U0001f4cb \u0645\u0648\u0636\u0648\u0639: {title}\n"
        f"\U0001f464 \u0645\u0634\u062a\u0631\u06cc: {name}\n"
        f"\U0001f4c5 {_escape(str((fresh or {}).get('created_at', ''))[:16])}\n"
        f"\U0001f4cc \u0648\u0636\u0639\u06cc\u062a: {status_fa}\n\n"
        f"\u2501\u2501\u2501 \u067e\u06cc\u0627\u0645\u200c\u0647\u0627 \u2501\u2501\u2501\n"
    )
    if msgs:
        for m in msgs:
            _agent_label = '\u0646\u0645\u0627\u06cc\u0646\u062f\u0647'
            sender = "\U0001f464 \u0645\u0634\u062a\u0631\u06cc" if m.get("sender_type") == "user" else f"\U0001f916 {_escape(m.get('sender_name', _agent_label))}"
            msg_text = _escape(m.get("message_text", ""))
            photo_tag = " \U0001f4f7 [\u0639\u06a9\u0633]" if m.get("photo_file_id") else ""
            ts = _escape(str(m.get("created_at", ""))[:16])
            text += f"\n{sender} ({ts}):\n{msg_text}{photo_tag}\n"
    else:
        text += "(\u067e\u06cc\u0627\u0645\u06cc \u0648\u062c\u0648\u062f \u0646\u062f\u0627\u0631\u062f)"
    kb = ticket_detail_keyboard(ticket_code, (fresh or {}).get("status", ""))
    await update.message.reply_text("\u2705 \u067e\u0627\u0633\u062e \u062b\u0628\u062a \u0634\u062f \u0648 \u0628\u0647 \u0645\u0634\u062a\u0631\u06cc \u0627\u0637\u0644\u0627\u0639 \u062f\u0627\u062f\u0647 \u0634\u062f.\n\n" + text, reply_markup=kb, parse_mode="HTML")
    return True
