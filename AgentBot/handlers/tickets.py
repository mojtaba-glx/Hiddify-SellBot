import logging

from telegram import Bot, Update
from telegram.ext import ContextTypes

from AgentBot.constants import (
    TICKET_PENDING, TICKET_OPEN, TICKET_CLOSED,
    TICKET_VIEW, TICKET_REPLY, TICKET_CLOSE, TICKET_BACK,
    MENU_MAIN, UD_STATE, UD_SELECTED_TICKET,
    STATE_REPLY_TICKET, STATE_REPLY_TICKET_SHOT, STATE_REPLY_TICKET_CONFIRM,
)
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import (
    tickets_menu_keyboard, ticket_detail_keyboard, back_keyboard, cancel_keyboard,
    ticket_reply_skip_keyboard, ticket_reply_confirm_keyboard,
)
from AgentBot.utils.helpers import _escape
from AgentBot.database import (
    get_customer_tickets, get_customer_ticket,
    get_customer_ticket_messages, add_customer_ticket_message, set_customer_ticket_status,
)

logger = logging.getLogger(__name__)

_STATUS_MAP = {
    "pending": "❌ در انتظار",
    "open": "✅ باز",
    "closed": "📪 بسته",
}


async def _agent_bot_username(context) -> str:
    cached = str(context.bot_data.get("_agent_bot_username") or "").strip().lstrip("@")
    if cached:
        return cached
    try:
        me = await context.bot.get_me()
        username = str(getattr(me, "username", "") or "").strip().lstrip("@")
        if username:
            context.bot_data["_agent_bot_username"] = username
        return username
    except Exception:
        return ""


def _shot_payload(ticket_code: int, message_id: int) -> str:
    return f"tshotu_{int(ticket_code)}_{int(message_id)}"


async def _build_ticket_shot_links(context, ticket_code: int, messages) -> dict:
    username = await _agent_bot_username(context)
    if not username:
        return {}
    links = {}
    for idx, item in enumerate(messages or [], start=1):
        fid = str(item.get("photo_file_id") or "").strip()
        mid = int(item.get("id") or 0)
        if not fid or mid <= 0:
            continue
        links[idx] = f"https://t.me/{username}?start={_shot_payload(ticket_code, mid)}"
    return links


def _parse_shot_payload(payload: str):
    import re as _re
    m = _re.match(r"^tshotu_(\d+)_(\d+)$", str(payload or "").strip())
    if not m:
        return 0, 0
    try:
        return int(m.group(1)), int(m.group(2))
    except Exception:
        return 0, 0


def _reply_preview_text(pending: dict) -> str:
    reply_text = str((pending or {}).get("reply_text") or "").strip() or "-"
    has_photo = bool(str((pending or {}).get("photo_file_id") or "").strip())
    screenshot_line = "📎 اسکرین‌شات: ارسال شده ✅" if has_photo else "📎 اسکرین‌شات: ارسال نشد"
    return (
        "📧 تایید اطلاعات پاسخ تیکت\n\n"
        f"📝 پاسخ:\n{_escape(reply_text)}\n\n"
        f"{screenshot_line}\n\n"
        "⚠️ در صورت تایید اطلاعات، برای ارسال تیکت گزینه «✅ ارسال» را انتخاب نمایید."
    )


def _pending_reply(context) -> dict:
    return context.user_data.get("pending_reply") or {}


def _set_pending_reply(context, pending: dict) -> None:
    context.user_data["pending_reply"] = pending


async def handle_ticket_shot_start(update, context, payload: str) -> bool:
    code, msg_id = _parse_shot_payload(payload)
    if code <= 0 or msg_id <= 0:
        return False
    agent_id = get_agent_id(context)
    if not agent_id:
        return True
    rows = get_customer_ticket_messages(agent_id, code)
    target = None
    idx = 0
    for i, item in enumerate(rows or [], start=1):
        if int(item.get("id") or 0) == int(msg_id):
            target = item
            idx = i
            break
    if not target or not str(target.get("photo_file_id") or "").strip():
        await update.message.reply_text("❌ اسکرین‌شات یافت نشد یا دسترسی ندارید.")
        return True
    caption = f"🖼 اسکرین‌شات #{idx} | تیکت #{code}"
    from AgentBot.keyboards import _ikb
    from Shared.tg_button_styles import inline_button as IButton
    kb = _ikb([[IButton("\U0001f519 \u0628\u0627\u0632\u06af\u0634\u062a \u0628\u0647 \u062a\u06cc\u06a9\u062a", callback_data=f"agbot:ticket:view:{code}")]])
    fid = str(target["photo_file_id"] or "").strip()
    sent = False
    # ۱) مستقیم با file_id (فقط وقتی عکس متعلق به همین ربات باشد)
    try:
        await update.message.reply_photo(photo=fid, caption=caption, reply_markup=kb)
        sent = True
    except Exception:
        sent = False
    # ۲) درغیراین‌صورت فایل را از ربات مشتری (که عکس را آپلود کرده) دانلود و دوباره ارسال کن
    if not sent:
        try:
            import io as _io
            from Shared.agent_db import get_all_active_customer_bots
            bot_rows = [b for b in get_all_active_customer_bots() if int(b.get("agent_id") or 0) == int(agent_id)]
            token = (bot_rows[0].get("bot_token") if bot_rows else "") or ""
            if not token:
                raise RuntimeError("no customer bot token")
            cust_bot = Bot(token=token)
            f = await cust_bot.get_file(fid)
            raw = await f.download_as_bytearray()
            bio = _io.BytesIO(raw)
            bio.name = f"ticket_{code}.jpg"
            bio.seek(0)
            await update.message.reply_photo(photo=bio, caption=caption, reply_markup=kb)
            sent = True
        except Exception:
            sent = False
    if not sent:
        try:
            await update.message.reply_text("❌ نمایش اسکرین‌شات ممکن نشد.", reply_markup=kb)
        except Exception:
            pass
    return True


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

        # Build text summary (دقیقاً مثل ربات ادمین — اسکرین‌شات به‌صورت لینک)
        text = (
            f"🧾 شناسه تیکت: {_escape(ticket_code)}\n"
            f"📅 تاریخ ایجاد: {_escape(str(ticket.get('created_at', ''))[:19])}\n"
            f"◈ وضعیت تیکت: {_escape(status_fa)}\n"
            f"👤 کاربر: {name}\n"
            f"🔹 نام کاربری: {_escape(ticket.get('username', '') or '-')}\n"
            f"🔢 شناسه کاربر: {_escape(str(ticket.get('telegram_id', '') or '-'))}\n"
            f"👨‍💻 ادمین: {_escape(ticket.get('admin_name', '') or 'تنظیم نشده')}\n"
            "❖⬩--------------------------------⬩❖\n"
        )

        shot_links = await _build_ticket_shot_links(context, ticket_code, msgs)
        if msgs:
            for idx, m in enumerate(msgs, start=1):
                sender_type = str(m.get("sender_type") or "").strip().lower()
                sender_name = str(m.get("sender_name") or "").strip() or ("کاربر" if sender_type == "user" else "نماینده")
                msg_text = str(m.get("message_text") or "").strip()
                when = str(m.get("created_at") or "-")
                text += f"📅 تاریخ ایجاد: {_escape(when)} | #{idx}\n"
                text += "◈ سوال:\n" if sender_type == "user" else "◈ پاسخ:\n"
                text += f"{_escape(sender_name)}\n"
                if msg_text:
                    text += f"{_escape(msg_text)}\n"
                if str(m.get("photo_file_id") or "").strip():
                    link = (shot_links or {}).get(idx) or ""
                    if link:
                        from html import escape as _he
                        text += f'🖼 <a href="{_he(link, quote=True)}">اسکرین‌شات #{idx}</a>\n'
                    else:
                        text += f"🖼 اسکرین‌شات #{idx}\n"
                text += "❖⬩------------------------------⬩❖\n"
        else:
            text += "(\u067e\u06cc\u0627\u0645\u06cc \u0648\u062c\u0648\u062f \u0646\u062f\u0627\u0631\u062f)"

        kb = ticket_detail_keyboard(ticket_code, ticket.get("status", ""))

        try:
            await query.edit_message_text(text[:4000], reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
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
        context.user_data.pop("pending_reply", None)
        try:
            await query.edit_message_text(
                "\U0001f4ac <b>\u067e\u0627\u0633\u062e \u0628\u0647 \u062a\u06cc\u06a9\u062a</b>\n\n"
                "\u0645\u062a\u0646 \u067e\u0627\u0633\u062e \u062e\u0648\u062f \u0631\u0627 \u0628\u0646\u0648\u06cc\u0633\u06cc\u062f:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "replyshot":
        sub = parts[3] if len(parts) > 3 else ""
        pending = _pending_reply(context)
        if sub == "skip":
            pending["photo_file_id"] = ""
            _set_pending_reply(context, pending)
            context.user_data[UD_STATE] = STATE_REPLY_TICKET_CONFIRM
            try:
                await query.edit_message_text(
                    _reply_preview_text(pending),
                    reply_markup=ticket_reply_confirm_keyboard(),
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return
        if sub == "cancel":
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_SELECTED_TICKET, None)
            context.user_data.pop("pending_reply", None)
            try:
                await query.edit_message_text("\u274c \u0627\u0631\u0633\u0627\u0644 \u067e\u0627\u0633\u062e \u0644\u063a\u0648 \u0634\u062f.")
            except Exception:
                pass
            return

    if action == "replyconfirm":
        sub = parts[3] if len(parts) > 3 else ""
        pending = _pending_reply(context)
        ticket_code = int((pending or {}).get("ticket_code") or 0) or context.user_data.get(UD_SELECTED_TICKET) or 0
        if sub == "edit":
            context.user_data[UD_STATE] = STATE_REPLY_TICKET
            context.user_data[UD_SELECTED_TICKET] = ticket_code
            context.user_data.pop("pending_reply", None)
            try:
                await query.edit_message_text(
                    "\U0001f4ac <b>\u067e\u0627\u0633\u062e \u0628\u0647 \u062a\u06cc\u06a9\u062a</b>\n\n"
                    "\u0645\u062a\u0646 \u067e\u0627\u0633\u062e \u062e\u0648\u062f \u0631\u0627 \u062f\u0648\u0628\u0627\u0631\u0647 \u0628\u0646\u0648\u06cc\u0633\u06cc\u062f:",
                    reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
            except Exception:
                pass
            return
        if sub == "cancel":
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_SELECTED_TICKET, None)
            context.user_data.pop("pending_reply", None)
            try:
                await query.edit_message_text("\u274c \u0627\u0631\u0633\u0627\u0644 \u067e\u0627\u0633\u062e \u0644\u063a\u0648 \u0634\u062f.")
            except Exception:
                pass
            return
        if sub == "send":
            await _do_send_reply(update, context, ticket_code, pending)
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


async def _do_send_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_code: int, pending: dict) -> None:
    agent_id = get_agent_id(context)
    chat_id = update.effective_chat.id if update.effective_chat else 0
    reply_text = str((pending or {}).get("reply_text") or "").strip()
    photo_file_id = str((pending or {}).get("photo_file_id") or "").strip()
    agent_data = context.user_data.get("agent_data", {})
    name = agent_data.get("full_name", "") or agent_data.get("username", "") or f"\u0646\u0645\u0627\u06cc\u0646\u062f\u0647 #{agent_id}"
    add_customer_ticket_message(agent_id, ticket_code, "agent", name, reply_text, photo_file_id)
    set_customer_ticket_status(agent_id, ticket_code, "open")
    ticket = get_customer_ticket(agent_id, ticket_code)
    if ticket and ticket.get("telegram_id"):
        try:
            from Shared.agent_db import get_all_active_customer_bots
            bot_rows = [b for b in get_all_active_customer_bots() if int(b.get("agent_id") or 0) == int(agent_id)]
            token = (bot_rows[0].get("bot_token") if bot_rows else "") or ""
            notify_bot = Bot(token=token) if token else context.bot
            notify_text = f"\U0001f4ac \u067e\u0627\u0633\u062e \u062c\u062f\u06cc\u062f \u0628\u0631\u0627\u06cc \u062a\u06cc\u06a9\u062a #{ticket_code}:\n\n{reply_text}"
            if photo_file_id:
                await notify_bot.send_photo(chat_id=ticket["telegram_id"], photo=photo_file_id, caption=notify_text[:1024])
            else:
                await notify_bot.send_message(chat_id=ticket["telegram_id"], text=notify_text)
        except Exception as e:
            logger.warning(f"Failed to notify customer: {e}")
    context.user_data.pop(UD_STATE, None)
    context.user_data.pop(UD_SELECTED_TICKET, None)
    context.user_data.pop("pending_reply", None)
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
    out = "\u2705 \u067e\u0627\u0633\u062e \u062b\u0628\u062a \u0634\u062f \u0648 \u0628\u0647 \u0645\u0634\u062a\u0631\u06cc \u0627\u0637\u0644\u0627\u0639 \u062f\u0627\u062f\u0647 \u0634\u062f.\n\n" + text
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(out, reply_markup=kb, parse_mode="HTML")
        except Exception:
            if chat_id:
                await context.bot.send_message(chat_id=chat_id, text=out, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(out, reply_markup=kb, parse_mode="HTML")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    state = context.user_data.get(UD_STATE)
    if state not in (STATE_REPLY_TICKET, STATE_REPLY_TICKET_SHOT):
        return False
    ticket_code = context.user_data.get(UD_SELECTED_TICKET)
    if not ticket_code:
        return False

    text = (update.message.text or update.message.caption or "").strip()
    photo_file_id = ""
    if update.message.photo:
        photo_file_id = update.message.photo[-1].file_id

    # --- مرحله ۱: نوشتن متن پاسخ ---
    if state == STATE_REPLY_TICKET:
        if not text and not photo_file_id:
            await update.message.reply_text("\u0645\u062a\u0646 \u06cc\u0627 \u0639\u06a9\u0633 \u067e\u06cc\u0627\u0645 \u0646\u0645\u06cc\u200c\u062a\u0648\u0627\u0646\u062f \u062e\u0627\u0644\u06cc \u0628\u0627\u0634\u062f.")
            return True
        if not text and photo_file_id:
            text = "[عکس]"
        pending = {
            "ticket_code": ticket_code,
            "reply_text": text,
            "photo_file_id": photo_file_id,
        }
        _set_pending_reply(context, pending)
        # اگر عکس از قبل به‌عنوان پاسخ ارسال شده، مستقیم به تأیید برو
        if photo_file_id:
            context.user_data[UD_STATE] = STATE_REPLY_TICKET_CONFIRM
            await update.message.reply_text(
                _reply_preview_text(pending),
                reply_markup=ticket_reply_confirm_keyboard(),
                parse_mode="HTML",
            )
            return True
        # در غیر این صورت، اسکرین‌شات اختیاری بپرس
        context.user_data[UD_STATE] = STATE_REPLY_TICKET_SHOT
        await update.message.reply_text(
            "\U0001f4ce \u0622\u06cc\u0627 \u0627\u0633\u06a9\u0631\u06cc\u0646\u200c\u0634\u0627\u062a \u0647\u0645 \u062f\u0627\u0631\u06cc\u062f\u061f (\u0627\u062e\u062a\u06cc\u0627\u0631\u06cc)\n\n"
            "\u0627\u06af\u0631 \u062f\u0627\u0631\u06cc\u062f \u0639\u06a9\u0633 \u0631\u0627 \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f \u06cc\u0627 \u06af\u0632\u06cc\u0646\u0647 \u00ab\u25b6\ufe0f \u0631\u062f \u06a9\u0631\u062f\u0646\u00bb \u0631\u0627 \u0628\u0632\u0646\u06cc\u062f.",
            reply_markup=ticket_reply_skip_keyboard(),
            parse_mode="HTML",
        )
        return True

    # --- مرحله ۲: اسکرین‌شات اختیاری ---
    if state == STATE_REPLY_TICKET_SHOT:
        pending = _pending_reply(context)
        if not pending or not pending.get("ticket_code"):
            return False
        if photo_file_id:
            pending["photo_file_id"] = photo_file_id
        _set_pending_reply(context, pending)
        context.user_data[UD_STATE] = STATE_REPLY_TICKET_CONFIRM
        await update.message.reply_text(
            _reply_preview_text(pending),
            reply_markup=ticket_reply_confirm_keyboard(),
            parse_mode="HTML",
        )
        return True

    return False
