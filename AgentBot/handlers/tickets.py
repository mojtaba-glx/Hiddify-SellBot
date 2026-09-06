import logging

from telegram import Bot, Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from Shared.tg_button_styles import inline_button as IButton

from AgentBot.constants import (
    TICKET_PENDING, TICKET_OPEN, TICKET_CLOSED,
    TICKET_VIEW, TICKET_REPLY, TICKET_CLOSE, TICKET_BACK,
    MENU_MAIN, UD_STATE, UD_SELECTED_TICKET,
    STATE_REPLY_TICKET, STATE_REPLY_TICKET_SHOT, STATE_REPLY_TICKET_CONFIRM,
)
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import (
    agent_lang,
    tickets_menu_keyboard, ticket_detail_keyboard, back_keyboard, cancel_keyboard,
    ticket_reply_skip_keyboard, ticket_reply_confirm_keyboard,
)
from AgentBot.utils.helpers import _escape
from AgentBot.database import (
    get_customer_tickets, get_customer_ticket,
    get_customer_ticket_messages, add_customer_ticket_message, set_customer_ticket_status,
)
from Shared import i18n

logger = logging.getLogger(__name__)

def _status_label(status: str, lang: str = "fa") -> str:
    """وضعیت تیکت به زبان نماینده ( به‌جای _STATUS_MAP ثابت فارسی)."""
    key = {"pending": "status_pending", "open": "status_open", "closed": "status_closed"}.get(
        str(status or "").strip().lower(), "")
    if key:
        return i18n.t(key, lang)
    return str(status or "")


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
    _lg = "fa"
    reply_text = str((pending or {}).get("reply_text") or "").strip() or "-"
    has_photo = bool(str((pending or {}).get("photo_file_id") or "").strip())
    screenshot_line = i18n.t('📎 اسکرین‌شات: ارسال شده ✅', _lg) if has_photo else i18n.t('📎 اسکرین‌شات: ارسال نشد', _lg)
    return (
        f"{i18n.t('📧 تایید اطلاعات پاسخ تیکت\n\n📝 پاسخ:\n', _lg)}{_escape(reply_text)}\n\n{screenshot_line}{i18n.t('\n\n⚠️ در صورت تایید اطلاعات، برای ارسال تیکت گزینه «✅ ارسال» را انتخاب نمایید.', _lg)}"
    )


def _pending_reply(context) -> dict:
    return context.user_data.get("pending_reply") or {}


def _set_pending_reply(context, pending: dict) -> None:
    context.user_data["pending_reply"] = pending


async def _edit_or_reply(query, text: str, kb, parse_mode: str = "HTML", **kwargs) -> bool:
    """اگر پیام عکس باشد، پیام جدید بفرست؛ وگرنه همان پیام را ویرایش کن."""
    if query.message and query.message.photo:
        try:
            await query.message.reply_text(text, reply_markup=kb, parse_mode=parse_mode, **kwargs)
            return True
        except Exception:
            return False
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=parse_mode, **kwargs)
        return True
    except Exception:
        return False


async def handle_ticket_shot_start(update, context, payload: str) -> bool:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
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
        await update.message.reply_text(i18n.t('❌ اسکرین‌شات یافت نشد یا دسترسی ندارید.', _lg))
        return True
    caption = f"{i18n.t('🖼 اسکرین‌شات #', _lg)}{idx}{i18n.t(' | تیکت #', _lg)}{code}"
    from AgentBot.keyboards import _ikb
    from Shared.tg_button_styles import inline_button as IButton
    kb = _ikb([[IButton(i18n.t('🔙 بازگشت به تیکت', _lg), callback_data=f"agbot:ticket:view:{code}")]])
    fid = str(target["photo_file_id"] or "").strip()
    sent = False
    # ۱) مستقیم با file_id (فقط وقتی عکس متعلق به همین ربات باشد)
    try:
        await update.message.reply_photo(photo=fid, caption=caption, reply_markup=kb)
        sent = True
    except Exception as e:
        logger.warning("ticket shot direct send failed code=%s msg=%s: %s", code, msg_id, e)
        sent = False
    # ۲) درغیراین‌صورت فایل را از ربات مشتری (که عکس را آپلود کرده) دانلود و دوباره ارسال کن
    if not sent:
        try:
            import io as _io
            from telegram.request import HTTPXRequest
            from Shared.agent_db import get_all_active_customer_bots
            bot_rows = [b for b in get_all_active_customer_bots() if int(b.get("agent_id") or 0) == int(agent_id)]
            request = HTTPXRequest(connect_timeout=15, read_timeout=60, write_timeout=60, pool_timeout=15)
            for bot_row in bot_rows:
                token = str(bot_row.get("bot_token") or "").strip()
                if not token:
                    continue
                try:
                    cust_bot = Bot(token=token, request=request)
                    f = await cust_bot.get_file(fid)
                    raw = await f.download_as_bytearray()
                    bio = _io.BytesIO(raw)
                    bio.name = f"ticket_{code}.jpg"
                    bio.seek(0)
                    await update.message.reply_photo(photo=bio, caption=caption, reply_markup=kb)
                    sent = True
                    break
                except Exception as e:
                    logger.warning("ticket shot download token attempt failed code=%s msg=%s token=%s...: %s", code, msg_id, token[:12], e)
        except Exception as e:
            logger.warning("ticket shot download-from-customer-bot failed code=%s msg=%s: %s", code, msg_id, e)
            sent = False
    if not sent:
        try:
            await update.message.reply_text(i18n.t('❌ نمایش اسکرین‌شات ممکن نشد.', _lg), reply_markup=kb)
        except Exception:
            pass
    return True


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent_id = get_agent_id(context)
    pending = get_customer_tickets(agent_id, "pending")
    open_count = len(get_customer_tickets(agent_id, "open"))
    from Shared import i18n as _i18n
    _lg = agent_lang(context)
    text = (
        _i18n.t("ag_tickets_title", _lg) + "\n\n"
        + _i18n.t("ag_tickets_pending_count", _lg, n=len(pending)) + "\n"
        + _i18n.t("ag_tickets_open_count", _lg, n=open_count) + "\n"
    )
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=tickets_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML")
        except Exception:
            await update.message.reply_text(text, reply_markup=tickets_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=tickets_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML")


async def _send_ticket_list(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    agent_id = get_agent_id(context)
    tickets = get_customer_tickets(agent_id, status)
    status_fa = _status_label(status, _lg)
    from AgentBot.keyboards import _ikb
    from Shared.tg_button_styles import inline_button as IButton
    lines = [f"<b>{status_fa}</b> ({len(tickets)})\n"]
    if not tickets:
        lines.append(i18n.t('هیچ تیکتی وجود ندارد.', _lg))
    else:
        for t in tickets:
            name = _escape(t.get("full_name", "")) or f"{i18n.t('کاربر #', _lg)}{t.get('telegram_id', '?')}"
            title = _escape(str(t.get("title") or t.get("question", "")[:40] or i18n.t('بدون موضوع', _lg))[:40])
            lines.append(f"\U0001f4ec <b>#{t['ticket_code']}</b> - {title}\n   \U0001f464 {name} \u2022 \U0001f4c5 {_escape(str(t.get('created_at', ''))[:16])}")
    rows = [[IButton(f"\U0001f4ec #{t['ticket_code']} - {_escape(str(t.get('title') or '')[:25])}", callback_data=f"agbot:ticket:view:{t['ticket_code']}")] for t in tickets[:10]]
    rows.append([IButton(i18n.t('🔙 بازگشت', _lg), callback_data="agbot:ticket:back")])
    query = update.callback_query
    try:
        await query.edit_message_text("\n".join(lines), reply_markup=_ikb(rows), parse_mode="HTML")
    except Exception:
        pass


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
            await query.answer(i18n.t('تیکت پیدا نشد.', _lg), show_alert=True)
            return
        msgs = get_customer_ticket_messages(agent_id, ticket_code)
        status_fa = _status_label(ticket.get("status", _lg), ticket.get("status", ""))
        title = _escape(str(ticket.get("title") or ticket.get("question", "")[:50] or i18n.t('بدون موضوع', _lg)))
        name = _escape(ticket.get("full_name", "")) or f"{i18n.t('کاربر #', _lg)}{ticket.get('telegram_id', '?')}"

        # Build text summary (دقیقاً مثل ربات ادمین — اسکرین‌شات به‌صورت لینک)
        text = (
            f"{i18n.t('🧾 شناسه تیکت: ', _lg)}{_escape(ticket_code)}{i18n.t('\n📅 تاریخ ایجاد: ', _lg)}{_escape(str(ticket.get('created_at', ''))[:19])}{i18n.t('\n◈ وضعیت تیکت: ', _lg)}{_escape(status_fa)}{i18n.t('\n👤 کاربر: ', _lg)}{name}{i18n.t('\n🔹 نام کاربری: ', _lg)}{_escape(ticket.get('username', '') or '-')}{i18n.t('\n🔢 شناسه کاربر: ', _lg)}{_escape(str(ticket.get('telegram_id', '') or '-'))}{i18n.t('\n👨‍💻 ادمین: ', _lg)}{_escape(ticket.get('admin_name', '') or i18n.t('unset_word', _lg))}\n❖⬩--------------------------------⬩❖\n"
        )

        shot_links = await _build_ticket_shot_links(context, ticket_code, msgs)
        if msgs:
            for idx, m in enumerate(msgs, start=1):
                sender_type = str(m.get("sender_type") or "").strip().lower()
                sender_name = str(m.get("sender_name") or "").strip() or (i18n.t('کاربر', _lg) if sender_type == "user" else i18n.t('نماینده', _lg))
                msg_text = str(m.get("message_text") or "").strip()
                when = str(m.get("created_at") or "-")
                text += f"{i18n.t('📅 تاریخ ایجاد: ', _lg)}{_escape(when)} | #{idx}\n"
                text += i18n.t('◈ سوال:\n', _lg) if sender_type == "user" else i18n.t('◈ پاسخ:\n', _lg)
                text += f"{_escape(sender_name)}\n"
                if msg_text:
                    text += f"{_escape(msg_text)}\n"
                if str(m.get("photo_file_id") or "").strip():
                    link = (shot_links or {}).get(idx) or ""
                    if link:
                        from html import escape as _he
                        text += f"🖼 <a href=\"{_he(link, quote=True)}{i18n.t('">اسکرین‌شات #', _lg)}{idx}</a>\n"
                    else:
                        text += f"{i18n.t('🖼 اسکرین‌شات #', _lg)}{idx}\n"
                text += "❖⬩------------------------------⬩❖\n"
        else:
            text += i18n.t('(پیامی وجود ندارد)', _lg)

        kb = ticket_detail_keyboard(ticket_code, ticket.get("status", ""), lang=agent_lang(context))

        await _edit_or_reply(query, text[:4000], kb, parse_mode="HTML", disable_web_page_preview=True)
        return

    if action == "reply":
        ticket_code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        context.user_data[UD_SELECTED_TICKET] = ticket_code
        context.user_data[UD_STATE] = STATE_REPLY_TICKET
        context.user_data.pop("pending_reply", None)
        await _edit_or_reply(
            query,
            i18n.t('💬 <b>پاسخ به تیکت</b>\n\nمتن پاسخ خود را بنویسید:', _lg),
            cancel_keyboard(),
        )
        return

    if action == "replyshot":
        sub = parts[3] if len(parts) > 3 else ""
        pending = _pending_reply(context)
        if sub == "skip":
            pending["photo_file_id"] = ""
            _set_pending_reply(context, pending)
            context.user_data[UD_STATE] = STATE_REPLY_TICKET_CONFIRM
            await _edit_or_reply(
                query,
                _reply_preview_text(pending),
                ticket_reply_confirm_keyboard(lang=_lg),
            )
            return
        if sub == "cancel":
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_SELECTED_TICKET, None)
            context.user_data.pop("pending_reply", None)
            await _edit_or_reply(query, i18n.t('❌ ارسال پاسخ لغو شد.', _lg), None)
            return

    if action == "replyconfirm":
        sub = parts[3] if len(parts) > 3 else ""
        pending = _pending_reply(context)
        ticket_code = int((pending or {}).get("ticket_code") or 0) or context.user_data.get(UD_SELECTED_TICKET) or 0
        if sub == "edit":
            context.user_data[UD_STATE] = STATE_REPLY_TICKET
            context.user_data[UD_SELECTED_TICKET] = ticket_code
            context.user_data.pop("pending_reply", None)
            await _edit_or_reply(
                query,
                i18n.t('💬 <b>پاسخ به تیکت</b>\n\nمتن پاسخ خود را دوباره بنویسید:', _lg),
                cancel_keyboard(),
            )
            return
        if sub == "cancel":
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_SELECTED_TICKET, None)
            context.user_data.pop("pending_reply", None)
            await _edit_or_reply(query, i18n.t('❌ ارسال پاسخ لغو شد.', _lg), None)
            return
        if sub == "send":
            await _do_send_reply(update, context, ticket_code, pending)
            return

    if action == "close":
        ticket_code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        ok = set_customer_ticket_status(agent_id, ticket_code, "closed")
        await query.answer(i18n.t('تیکت بسته شد ✅', _lg) if ok else i18n.t('خطا!', _lg))
        if ok:
            try:
                await query.edit_message_reply_markup(reply_markup=ticket_detail_keyboard(ticket_code, "closed", lang=agent_lang(context)))
            except Exception:
                pass
        return

    if action == "reopen":
        ticket_code = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        ok = set_customer_ticket_status(agent_id, ticket_code, "open")
        await query.answer(i18n.t('تیکت دوباره باز شد 📬', _lg) if ok else i18n.t('خطا!', _lg))
        try:
            await query.edit_message_reply_markup(reply_markup=ticket_detail_keyboard(ticket_code, "open", lang=agent_lang(context)))
        except Exception:
            pass
        return


async def _do_send_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_code: int, pending: dict) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    agent_id = get_agent_id(context)
    chat_id = update.effective_chat.id if update.effective_chat else 0
    reply_text = str((pending or {}).get("reply_text") or "").strip()
    photo_file_id = str((pending or {}).get("photo_file_id") or "").strip()
    agent_data = context.user_data.get("agent_data", {})
    name = agent_data.get("full_name", "") or agent_data.get("username", "") or f"{i18n.t('نماینده #', _lg)}{agent_id}"
    add_customer_ticket_message(agent_id, ticket_code, "agent", name, reply_text, photo_file_id)
    set_customer_ticket_status(agent_id, ticket_code, "open")
    ticket = get_customer_ticket(agent_id, ticket_code)
    if ticket and ticket.get("telegram_id"):
        try:
            from Shared.agent_db import get_all_active_customer_bots
            bot_rows = [b for b in get_all_active_customer_bots() if int(b.get("agent_id") or 0) == int(agent_id)]
            notify_bot = None
            for bot_row in bot_rows:
                token = str(bot_row.get("bot_token") or "").strip()
                if not token:
                    continue
                try:
                    candidate = Bot(token=token)
                    await candidate.get_me()
                    notify_bot = candidate
                    break
                except Exception:
                    continue
            if notify_bot is None:
                notify_bot = context.bot
            notify_text = f"{i18n.t('💬 پاسخ جدید برای تیکت #', _lg)}{ticket_code}:\n\n{reply_text}"
            kb = InlineKeyboardMarkup([
                [IButton(i18n.t('👁 مشاهده تیکت', _lg), callback_data=f"support:view:{ticket_code}:1")],
                [IButton(i18n.t('💬 پاسخ', _lg), callback_data=f"support:reply:{ticket_code}")],
            ])
            if photo_file_id:
                try:
                    import io as _io
                    tg_file = await context.bot.get_file(photo_file_id)
                    bio = _io.BytesIO()
                    await tg_file.download_to_memory(out=bio)
                    bio.seek(0)
                    bio.name = f"ticket_reply_{ticket_code}.jpg"
                    await notify_bot.send_photo(chat_id=ticket["telegram_id"], photo=bio, caption=notify_text[:1024], reply_markup=kb)
                except Exception as e:
                    logger.warning("agent reply photo foreign-send failed code=%s: %s", ticket_code, e)
                    await notify_bot.send_message(chat_id=ticket["telegram_id"], text=notify_text, reply_markup=kb)
            else:
                await notify_bot.send_message(chat_id=ticket["telegram_id"], text=notify_text, reply_markup=kb)
        except Exception as e:
            logger.warning(f"Failed to notify customer: {e}")
    context.user_data.pop(UD_STATE, None)
    context.user_data.pop(UD_SELECTED_TICKET, None)
    context.user_data.pop("pending_reply", None)
    fresh = get_customer_ticket(agent_id, ticket_code)
    msgs = get_customer_ticket_messages(agent_id, ticket_code) if fresh else []
    status_fa = _status_label((fresh or {}).get("status", _lg), (fresh or {}).get("status", ""))
    title = _escape(str((fresh or {}).get("title") or (fresh or {}).get("question", "")[:50] or i18n.t('بدون موضوع', _lg)))
    name = _escape((fresh or {}).get("full_name", "")) or f"{i18n.t('کاربر #', _lg)}{(fresh or {}).get('telegram_id', '?')}"
    text = (
        f"{i18n.t('📬 <b>تیکت #', _lg)}{ticket_code}{i18n.t('</b>\n📋 موضوع: ', _lg)}{title}{i18n.t('\n👤 مشتری: ', _lg)}{name}\n📅 {_escape(str((fresh or {}).get('created_at', ''))[:16])}{i18n.t('\n📌 وضعیت: ', _lg)}{status_fa}{i18n.t('\n\n━━━ پیام‌ها ━━━\n', _lg)}"
    )
    if msgs:
        for m in msgs:
            _agent_label = i18n.t('نماینده', _lg)
            sender = i18n.t('👤 مشتری', _lg) if m.get("sender_type") == "user" else f"\U0001f916 {_escape(m.get('sender_name', _agent_label))}"
            msg_text = _escape(m.get("message_text", ""))
            photo_tag = i18n.t(' 📷 [عکس]', _lg) if m.get("photo_file_id") else ""
            ts = _escape(str(m.get("created_at", ""))[:16])
            text += f"\n{sender} ({ts}):\n{msg_text}{photo_tag}\n"
    else:
        text += i18n.t('(پیامی وجود ندارد)', _lg)
    kb = ticket_detail_keyboard(ticket_code, (fresh or {}).get("status", ""), lang=agent_lang(context))
    out = i18n.t('✅ پاسخ ثبت شد و به مشتری اطلاع داده شد.\n\n', _lg) + text
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(out, reply_markup=kb, parse_mode="HTML")
        except Exception:
            if chat_id:
                await context.bot.send_message(chat_id=chat_id, text=out, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(out, reply_markup=kb, parse_mode="HTML")


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
            await update.message.reply_text(i18n.t('متن یا عکس پیام نمی‌تواند خالی باشد.', _lg))
            return True
        if not text and photo_file_id:
            text = i18n.t('[عکس]', _lg)
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
                reply_markup=ticket_reply_confirm_keyboard(lang=_lg),
                parse_mode="HTML",
            )
            return True
        # در غیر این صورت، اسکرین‌شات اختیاری بپرس
        context.user_data[UD_STATE] = STATE_REPLY_TICKET_SHOT
        await update.message.reply_text(
            i18n.t('📎 آیا اسکرین‌شات هم دارید؟ (اختیاری)\n\nاگر دارید عکس را ارسال کنید یا گزینه «▶️ رد کردن» را بزنید.', _lg),
            reply_markup=ticket_reply_skip_keyboard(lang=_lg),
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
            reply_markup=ticket_reply_confirm_keyboard(lang=_lg),
            parse_mode="HTML",
        )
        return True

    return False
