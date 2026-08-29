import os
import json
import logging
import re
import sqlite3
from io import BytesIO
from typing import Optional

logger = logging.getLogger(__name__)

from telegram import Bot, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from Shared.tg_button_styles import inline_button as InlineKeyboardButton

from CustomerBot.constants import (
    UD_STATE, UD_BUY_GB, UD_BUY_MONTHS, UD_BUY_SERVER_ID, UD_TICKET_MODE,
    UD_TICKET_QUESTION,
    STATE_RECEIPT_WAITING, STATE_CARD_LAST4, STATE_TICKET_WAITING_TEXT, STATE_TICKET_WAITING_TITLE,
    STATE_TICKET_WAITING_PHOTO, STATE_TICKET_CONFIRM,
    STATE_TRIAL_WAITING_NAME,
    BTN_BACK, BTN_PAY_DONE, STATE_START,
)
from CustomerBot.database import (
    get_user, get_buy_renew_settings, get_payment_settings,
    get_marketing_settings, get_text_settings,
    create_payment, update_payment_status, get_payment_by_tx_code,
    get_payment_by_idempotency_key,
    get_pending_payments,
    create_ticket, get_ticket, add_ticket_message, update_ticket_status,
    get_user_tickets, get_ticket_messages,
)
from Shared.agent_db import (
    get_customer_by_telegram_id, upsert_customer, get_services_by_customer,
    create_service, get_service_by_id, add_service_node, renew_service, make_service_note,
    calculate_wholesale_price,
)
from Shared.database import get_servers
from Shared.database import get_server_by_id
from CustomerBot.keyboards import main_menu_keyboard, cancel_keyboard, ticket_skip_screenshot_keyboard, ticket_confirm_keyboard, user_ticket_detail_keyboard
from CustomerBot.utils.helpers import is_cancel_text, is_pay_done_text


def _fmt_ticket_time(ts) -> str:
    """تبدیل 2026-08-14 08:00:32 به 08-14-2026 08:00:32 (مثل ربات کاربران)."""
    raw = str(ts or "").strip()
    if not raw:
        return "-"
    s = raw
    try:
        from datetime import datetime as _dt
        dt = _dt.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%m-%d-%Y %H:%M:%S")
    except Exception:
        return s


def _build_ticket_detail_text(ticket, messages, screenshot_links=None) -> str:
    """متن جزئیات تیکت — دقیقاً مثل ربات کاربران/ادمین (اسکرین‌شات به‌صورت لینک قابل‌کلیک)."""
    from html import escape
    try:
        code = str(ticket.get("ticket_code") or ticket.get("id") or "-")
    except Exception:
        code = "-"
    title = str(ticket.get("title") or "").strip() or "-"
    lines = [
        f"🧾 شناسه تیکت: {escape(code)}",
        "❖⬩--------------------------------⬩❖",
    ]
    for idx, item in enumerate(messages or [], start=1):
        sender_type = str(item.get("sender_type") or "").strip().lower()
        sender_title = "◈سوال:" if sender_type == "user" else "◈پاسخ:"
        text_part = str(item.get("message_text") or "").strip()
        when = _fmt_ticket_time(item.get("created_at"))
        has_photo = bool(str(item.get("photo_file_id") or "").strip())
        lines.append(f"📅تاریخ ایجاد: {escape(when)}")
        if idx == 1:
            lines.append("◈عنوان:")
            lines.append(escape(title))
        lines.append(sender_title)
        if text_part:
            lines.append(escape(text_part))
        if has_photo:
            link = str((screenshot_links or {}).get(idx) or "").strip()
            if link:
                lines.append(f'<a href="{escape(link, quote=True)}">اسکرین‌شات</a>')
            else:
                lines.append("اسکرین‌شات")
        lines.append("❖⬩------------------------------⬩❖")
    out = "\n".join(lines)
    if len(out) > 3900:
        return out[:3890] + "\n..."
    return out


SHOT_START_PREFIX = "tshotu"


async def _get_bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    """گرفتن نام‌کاربری ربات مشتری فعلی برای ساخت لینک اسکرین‌شات."""
    cached = str(context.bot_data.get("_cb_bot_username") or "").strip().lstrip("@")
    if cached:
        return cached
    try:
        me = await context.bot.get_me()
        username = str(getattr(me, "username", "") or "").strip().lstrip("@")
        if username:
            context.bot_data["_cb_bot_username"] = username
        return username
    except Exception:
        return ""


def _build_shot_payload(ticket_code: int, message_id: int) -> str:
    return f"{SHOT_START_PREFIX}_{int(ticket_code)}_{int(message_id)}"


def _parse_shot_payload(payload: str):
    m = re.match(rf"^{re.escape(SHOT_START_PREFIX)}_(\d+)_(\d+)$", str(payload or "").strip())
    if not m:
        return 0, 0
    try:
        return int(m.group(1)), int(m.group(2))
    except Exception:
        return 0, 0


async def build_ticket_screenshot_links(context: ContextTypes.DEFAULT_TYPE, ticket_code: int, messages) -> dict:
    """برای هر پیامی که عکس دارد یک لینک اسکرین‌شات می‌سازد (tshotu_...)."""
    username = await _get_bot_username(context)
    if not username:
        return {}
    links = {}
    for idx, item in enumerate(messages or [], start=1):
        fid = str(item.get("photo_file_id") or "").strip()
        mid = int(item.get("id") or 0)
        if not fid or mid <= 0:
            continue
        links[idx] = f"https://t.me/{username}?start={_build_shot_payload(ticket_code, mid)}"
    return links


async def handle_ticket_shot_start(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str) -> bool:
    """هندل دپ‌لینک اسکرین‌شات (tshotu_code_msgid)."""
    code, msg_id = _parse_shot_payload(payload)
    if code <= 0 or msg_id <= 0:
        return False
    agent_id = context.bot_data.get("agent_id", 0)
    if not agent_id:
        return True
    try:
        rows = get_ticket_messages(agent_id, code)
    except Exception:
        rows = []
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
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به تیکت", callback_data=f"support:view:{code}:1")]])
    try:
        await update.message.reply_photo(photo=target["photo_file_id"], caption=caption, reply_markup=kb)
    except Exception:
        try:
            await update.message.reply_text("❌ نمایش اسکرین‌شات ممکن نشد.", reply_markup=kb)
        except Exception:
            pass
    return True


async def _notify_agent_ticket_reply(context: ContextTypes.DEFAULT_TYPE, agent_id: int, ticket: dict, message_text: str, photo_file_id: str = "") -> None:
    """خبر دادن به نماینده که مشتری به تیکت پاسخ داده است (عکس مستقیم ارسال می‌شود)."""
    try:
        from Shared.agent_db import get_agent_by_id
        agent = get_agent_by_id(agent_id)
        agent_tg_id = int((agent or {}).get("telegram_id") or 0)
        token = os.getenv("AGENT_BOT_TOKEN", "").strip()
        if not agent_tg_id or not token:
            return
        bot = Bot(token=token)
        code = ticket.get("ticket_code", "?")
        title = ticket.get("title") or "بدون موضوع"
        notify_text = (
            f"💬 پاسخ جدید مشتری به تیکت #{code}\n"
            f"📋 موضوع: {title}\n\n"
            f"{message_text or '[بدون متن]'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👁 مشاهده تیکت", callback_data=f"agbot:ticket:view:{code}")],
            [InlineKeyboardButton("💬 پاسخ", callback_data=f"agbot:ticket:reply:{code}"),
             InlineKeyboardButton("✅ بستن", callback_data=f"agbot:ticket:close:{code}")],
        ])
        if photo_file_id:
            try:
                tg_file = await context.bot.get_file(photo_file_id)
                bio = BytesIO()
                await tg_file.download_to_memory(out=bio)
                bio.seek(0)
                bio.name = f"ticket_{code}.jpg"
                await bot.send_photo(chat_id=agent_tg_id, photo=bio, caption=notify_text[:1024], reply_markup=kb)
                return
            except Exception as e:
                logger.warning("notify agent ticket reply photo failed: %s", e)
        await bot.send_message(chat_id=agent_tg_id, text=notify_text, reply_markup=kb)
    except Exception:
        return


async def _notify_agent_new_ticket(context: ContextTypes.DEFAULT_TYPE, agent_id: int, ticket: dict, message_text: str, photo_file_id: str = "") -> None:
    try:
        from Shared.agent_db import get_agent_by_id
        agent = get_agent_by_id(agent_id)
        agent_tg_id = int((agent or {}).get("telegram_id") or 0)
        token = os.getenv("AGENT_BOT_TOKEN", "").strip()
        if not agent_tg_id or not token:
            return
        bot = Bot(token=token)
        code = ticket.get("ticket_code", "?")
        title = ticket.get("title") or "بدون موضوع"
        notify_text = (
            f"📩 تیکت جدید #{code}\n"
            f"📋 موضوع: {title}\n"
            f"👤 مشتری: {ticket.get('full_name') or ticket.get('username') or ticket.get('telegram_id')}\n\n"
            f"{message_text or '[بدون متن]'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👁 مشاهده تیکت", callback_data=f"agbot:ticket:view:{code}")],
            [InlineKeyboardButton("💬 پاسخ", callback_data=f"agbot:ticket:reply:{code}"),
             InlineKeyboardButton("✅ بستن", callback_data=f"agbot:ticket:close:{code}")],
        ])
        if photo_file_id:
            try:
                tg_file = await context.bot.get_file(photo_file_id)
                bio = BytesIO()
                await tg_file.download_to_memory(out=bio)
                bio.seek(0)
                bio.name = f"ticket_{code}.jpg"
                await bot.send_photo(chat_id=agent_tg_id, photo=bio, caption=notify_text[:1024], reply_markup=kb)
                return
            except Exception as e:
                logger.warning("notify agent new ticket photo failed: %s", e)
        await bot.send_message(chat_id=agent_tg_id, text=notify_text, reply_markup=kb)
    except Exception:
        return


async def _notify_agent_new_payment(
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    pay: dict,
    meta: dict,
    order: Optional[dict],
    customer_user,
) -> None:
    """ارسال رسید مشتری به ربات نماینده همراه دکمه تایید/رد."""
    try:
        from Shared.agent_db import get_agent_by_id, get_wallet_balance
        agent = get_agent_by_id(agent_id)
        agent_tg_id = int((agent or {}).get("telegram_id") or 0)
        token = os.getenv("AGENT_BOT_TOKEN", "").strip()
        if not agent_tg_id or not token:
            return

        amount = int(pay.get("amount") or meta.get("sale_price") or 0)
        wholesale = int(meta.get("wholesale_price") or 0)
        wallet_balance = get_wallet_balance(agent_id)
        order_id = int(meta.get("order_id") or 0)
        tx_code = str(pay.get("tx_code") or "-")
        user_tg_id = int(getattr(customer_user, "id", 0) or 0)
        name = getattr(customer_user, "full_name", "") or getattr(customer_user, "username", "") or str(user_tg_id)
        caption = (
            "💳 <b>رسید پرداخت مشتری</b>\n"
            f"👤 مشتری: <b>{name}</b>\n"
            f"🔖 کد پیگیری: <code>{tx_code}</code>\n"
            f"💰 مبلغ: <b>{amount:,}</b> تومان\n"
        )
        card_last4 = str(meta.get("card_last4") or "").strip()
        if card_last4:
            caption += f"💳 ۴ رقم آخر کارت: <code>{card_last4}</code>\n"
        tx_marker = 0
        try:
            tx_marker = int(meta.get("tx_marker") or 0)
        except (TypeError, ValueError):
            pass
        if tx_marker > 0:
            caption += f"🔢 مشخصه تراکنش: <code>{tx_marker}</code>\n"
        if order:
            order_kind = "♻️ تمدید" if int(order.get("renew_service_id") or 0) else "📦 سفارش"
            caption += (
                f"{order_kind}: <code>{order_id}</code> | 📊 حجم: {float(meta.get('gb') or order.get('volume_gb') or 0):g} گیگ\n"
                f"⏰ زمان: {int(meta.get('days') or order.get('days') or 0)} روز | 🏷 هزینه عمده: <b>{wholesale:,}</b> تومان\n"
                f"💼 کیف پول نماینده: <b>{wallet_balance:,}</b> تومان\n"
            )
        caption += "\nدر تایید، " + ("اشتراک مشتری تمدید می‌شود." if (order and int(order.get('renew_service_id') or 0)) else "اشتراک مشتری پس از بررسی کیف پول نماینده فعال می‌شود.")

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("❌ رد پرداخت", callback_data=f"agbot:custpay:reject:{pay['id']}"),
                InlineKeyboardButton("✅ تایید پرداخت", callback_data=f"agbot:custpay:approve:{pay['id']}"),
            ],
            [
                InlineKeyboardButton(f"👤 {name}", callback_data=f"agbot:custpay:profile:{user_tg_id}"),
            ],
        ])
        bot = Bot(token=token)
        file_id = str(meta.get("file_id") or "")
        sent_message = None
        if file_id:
            try:
                tg_file = await context.bot.get_file(file_id)
                bio = BytesIO()
                await tg_file.download_to_memory(out=bio)
                bio.seek(0)
                bio.name = f"payment_{pay['id']}.jpg"
                sent_message = await bot.send_photo(chat_id=agent_tg_id, photo=bio, caption=caption[:1024], reply_markup=kb, parse_mode="HTML")
            except Exception:
                sent_message = None
        if sent_message is None:
            try:
                sent_message = await bot.send_message(chat_id=agent_tg_id, text=caption, reply_markup=kb, parse_mode="HTML")
            except Exception:
                sent_message = None
        if sent_message is not None:
            await _store_agent_pending_message_marker(agent_id, int(pay["id"]), int(sent_message.message_id))
    except Exception:
        return


def _receipt_meta_has_agent_notified_marker(receipt_meta: str) -> bool:
    return "|agent_notified:1" in str(receipt_meta or "")


async def _store_agent_pending_message_marker(agent_id: int, pay_id: int, message_id: int) -> None:
    """message_id پیام رسید در چت نماینده را در متادیتا ذخیره می‌کند تا بعد از
    تایید خودکار (SMS بانکی) بتوان همان پیام را از چت نماینده پاک کرد."""
    try:
        from CustomerBot.database import get_payment, update_payment_status
        pay = get_payment(agent_id, pay_id)
        if not pay:
            return
        meta_raw = str(pay.get("receipt_image") or "")
        if f"agent_pending_message_id:{message_id}" in meta_raw:
            return
        updated_meta = _append_receipt_meta_marker(meta_raw, f"agent_pending_message_id:{message_id}")
        update_payment_status(
            agent_id=agent_id,
            payment_id=pay_id,
            status=str(pay.get("status") or "pending"),
            receipt_image=updated_meta,
        )
    except Exception as e:
        logger.warning("store agent pending message marker failed (pay=%s): %s", pay_id, e)


def _append_receipt_meta_marker(receipt_meta: str, marker: str) -> str:
    raw = str(receipt_meta or "")
    if marker and marker not in raw:
        return f"{raw}|{marker}" if raw else marker
    return raw


def _format_ticket_confirm_text(pending: dict) -> str:
    has_photo = "✅ ارسال شده" if pending.get("photo_file_id") else "❌ ارسال نشده"
    return (
        "📩 <b>تایید اطلاعات تیکت</b>\n\n"
        f"📌 <b>عنوان:</b>\n{pending.get('title') or 'بدون موضوع'}\n\n"
        f"📝 <b>سوال:</b>\n{pending.get('question') or '[بدون متن]'}\n\n"
        f"📎 <b>اسکرین‌شات:</b> {has_photo}\n\n"
        "❗️در صورت تایید اطلاعات، برای ارسال تیکت گزینه ✅ ارسال را انتخاب نمایید."
    )


async def _finalize_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, meta: dict, amount: int):
    user = update.effective_user
    if not user:
        return
    receipt_meta = json.dumps(meta, ensure_ascii=False)
    order_id = int(meta.get("order_id") or 0)
    idempotency_key = f"receipt_{user.id}_{order_id}_0"
    pay = get_payment_by_idempotency_key(agent_id, idempotency_key)
    notify_agent = False
    existing_receipt_meta = str((pay or {}).get("receipt_image") or "")

    if pay and str(pay.get("status") or "") == "pending" and str(pay.get("receipt_image") or "").strip():
        updated = update_payment_status(
            agent_id=agent_id,
            payment_id=pay["id"],
            status="pending",
            receipt_image=receipt_meta,
        )
        if updated and not _receipt_meta_has_agent_notified_marker(existing_receipt_meta):
            notify_agent = True
    else:
        if not pay:
            try:
                pay = create_payment(
                    agent_id=agent_id,
                    user_id=user.id,
                    amount=amount,
                    method="card",
                    idempotency_key=idempotency_key,
                )
            except sqlite3.IntegrityError:
                pay = get_payment_by_idempotency_key(agent_id, idempotency_key)
        if pay:
            existing_receipt_meta = str(pay.get("receipt_image") or existing_receipt_meta or "")
            updated = update_payment_status(
                agent_id=agent_id,
                payment_id=pay["id"],
                status="pending",
                receipt_image=receipt_meta,
            )
            notify_agent = updated and not _receipt_meta_has_agent_notified_marker(existing_receipt_meta)

    order = None
    if order_id:
        from CustomerBot.database import get_order
        order = get_order(agent_id, order_id)

    if pay and notify_agent:
        await _notify_agent_new_payment(context, agent_id, pay, meta, order, user)
        try:
            pay = get_payment_by_idempotency_key(agent_id, idempotency_key) or pay
            update_payment_status(
                agent_id=agent_id,
                payment_id=int(pay["id"]),
                status="pending",
                receipt_image=_append_receipt_meta_marker(receipt_meta, "agent_notified:1"),
            )
        except Exception:
            pass
    pending_msg = await update.message.reply_text(
        "✅ تراکنش شما در انتظار تایید توسط ادمین است.\n"
        "لطفا صبر کنید و از ارسال رسید تکراری بپرهیزید.",
        reply_markup=main_menu_keyboard(),
    )
    if pay and pending_msg:
        try:
            pending_meta = _append_receipt_meta_marker(receipt_meta, f"customer_pending_message_id:{pending_msg.message_id}")
            if notify_agent:
                pending_meta = _append_receipt_meta_marker(pending_meta, "agent_notified:1")
            elif _receipt_meta_has_agent_notified_marker(existing_receipt_meta):
                pending_meta = _append_receipt_meta_marker(pending_meta, "agent_notified:1")
            update_payment_status(
                agent_id=agent_id,
                payment_id=int(pay["id"]),
                status="pending",
                receipt_image=pending_meta,
            )
        except Exception:
            pass
    context.user_data.pop(UD_STATE, None)
    context.user_data.pop("card_last4", None)
    context.user_data.pop("pending_receipt_meta", None)
    context.user_data.pop("pending_amount", None)
    context.user_data.pop("pending_order_id", None)
    context.user_data.pop("pending_pay_price", None)
    context.user_data.pop("pending_tx_marker", None)


async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    agent_id = context.bot_data.get("agent_id", 0)
    if not agent_id:
        return

    state = context.user_data.get(UD_STATE, "")
    text = ((update.message.text or update.message.caption or "").strip()) if update.message else ""

    if is_cancel_text(text):
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_TICKET_MODE, None)
        context.user_data.pop(UD_BUY_GB, None)
        context.user_data.pop(UD_BUY_MONTHS, None)
        context.user_data.pop(UD_BUY_SERVER_ID, None)
        context.user_data.pop(UD_TICKET_QUESTION, None)
        context.user_data.pop("pending_ticket", None)
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=main_menu_keyboard(),
        )
        return

    # ---- Payment receipt trigger ----
    if state == STATE_RECEIPT_WAITING and is_pay_done_text(text):
        context.user_data[UD_STATE] = "wallet_receipt_photo"
        await update.message.reply_text(
            "📤 لطفاً تصویر رسید پرداخت را ارسال کنید.",
            reply_markup=cancel_keyboard(),
        )
        return

    # ---- Receipt photo (kept until last-4 if required, else finalize now) ----
    if state in {STATE_RECEIPT_WAITING, "wallet_receipt_photo"}:
        if not (update.message and update.message.photo):
            await update.message.reply_text(
                "❌ لطفاً تصویر رسید را ارسال کنید.",
                reply_markup=cancel_keyboard(),
            )
            return
        try:
            photo = update.message.photo[-1]
            file_id = photo.file_id
            order_id = context.user_data.get("last_order_id", 0)
            server_id = context.user_data.get(UD_BUY_SERVER_ID, 0)
            gb = context.user_data.get(UD_BUY_GB, 0)
            days = 0
            amount = 0
            order = None
            if order_id:
                from CustomerBot.database import get_order
                order = get_order(agent_id, order_id)
                if order:
                    amount = int(order.get("price", 0))
                    server_id = int(order.get("server_id") or server_id or 0)
                    gb = float(order.get("volume_gb") or gb or 0)
                    days = int(order.get("days") or 0)
            pending_pay_price = context.user_data.get("pending_pay_price")
            if pending_pay_price:
                try:
                    amount = int(pending_pay_price)
                except (TypeError, ValueError):
                    pass
            if not days:
                days = int(context.user_data.get(UD_BUY_MONTHS, 0) or 0)
            wholesale_price = 0
            if order_id:
                wholesale_price = int((order or {}).get("wholesale_price") or 0)
                if wholesale_price <= 0:
                    wholesale_price = calculate_wholesale_price(agent_id, gb, days, server_id)
            tx_marker = 0
            try:
                tx_marker = int(context.user_data.get("pending_tx_marker") or 0)
            except (TypeError, ValueError):
                pass
            meta = {
                "file_id": file_id,
                "order_id": order_id,
                "server_id": server_id,
                "gb": gb,
                "days": days,
                "sale_price": amount,
                "wholesale_price": wholesale_price,
                "plan_title": (order or {}).get("plan_title", ""),
                "server_location": (order or {}).get("server_location", ""),
                "card_last4": "",
                "tx_marker": tx_marker,
                "type": "buy",
            }
            context.user_data["pending_receipt_meta"] = meta
            context.user_data["pending_order_id"] = order_id
            context.user_data["pending_amount"] = amount

            from CustomerBot.database import get_payment_settings
            req_last4 = bool((get_payment_settings(agent_id) or {}).get("require_last4_for_card_receipt"))
            if req_last4:
                context.user_data[UD_STATE] = STATE_CARD_LAST4
                await update.message.reply_text(
                    "📸 رسید شما ثبت شد.\n\n"
                    "💳 حالا لطفاً <b>۴ رقم آخر کارت‌بانکی‌ای</b> که از آن پرداخت کرده‌اید را وارد کنید "
                    "(فارسی یا انگلیسی، فقط ۴ رقم):",
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard(),
                )
                return
            return await _finalize_receipt(update, context, agent_id, meta, amount)
        except Exception as e:
            logger.exception("customer receipt photo step failed uid=%s: %s", user.id, e)
            try:
                await update.message.reply_text(
                    "❌ خطایی در ثبت رسید رخ داد. لطفاً دوباره تلاش کنید.",
                    reply_markup=cancel_keyboard(),
                )
            except Exception:
                pass
            return

    # ---- Card last-4 entry (required by agent settings) ----
    if state == STATE_CARD_LAST4:
        fa_map = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        digits = re.sub(r"[^0-9]", "", str((text or "")).translate(fa_map))
        if len(digits) != 4:
            await update.message.reply_text(
                "❌ باید دقیقاً <b>۴ رقم</b> آخر کارت را وارد کنید (نه کمتر، نه بیشتر).",
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
            return
        meta = context.user_data.get("pending_receipt_meta") or {}
        meta["card_last4"] = digits
        context.user_data["pending_receipt_meta"] = meta
        amount = int(context.user_data.get("pending_amount") or 0)
        await _finalize_receipt(update, context, agent_id, meta, amount)
        return


    # ---- نام سرویس تست رایگان (بعد از انتخاب لوکیشن، مثل UserBot) ----
    if state == STATE_TRIAL_WAITING_NAME:
        if is_cancel_text(text):
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop("pending_trial_server_id", None)
            context.user_data.pop("pending_trial_server", None)
            await update.message.reply_text("🔙 عملیات لغو شد.", reply_markup=main_menu_keyboard())
            return
        from CustomerBot.handlers.callback import _build_trial_service
        await _build_trial_service(update, context, agent_id, update.effective_user, text or "")
        return


    # ---- Ticket title handler (step 1 of new ticket) ----
    if state == STATE_TICKET_WAITING_TITLE:
        mode = context.user_data.get(UD_TICKET_MODE, "new")
        if mode == "new":
            title = text or "بدون موضوع"
            context.user_data[UD_TICKET_QUESTION] = title
            context.user_data[UD_STATE] = STATE_TICKET_WAITING_TEXT
            await update.message.reply_text(
                f"📋 موضوع: {title}\n\n"
                f"📩 حالا متن پیام خود را ارسال کنید:",
                reply_markup=cancel_keyboard(),
            )
            return

    # ---- Ticket text handler (step 2: question, then optional screenshot) ----
    if state == STATE_TICKET_WAITING_TEXT:
        mode = context.user_data.get(UD_TICKET_MODE, "new")
        msg_text = text or ""

        if mode == "new":
            title = context.user_data.get(UD_TICKET_QUESTION, "بدون موضوع")
            if update.message.photo and not msg_text:
                msg_text = "[عکس]"
            if not msg_text:
                await update.message.reply_text("❌ لطفاً متن سوال را ارسال کنید.", reply_markup=cancel_keyboard())
                return
            context.user_data["pending_ticket"] = {
                "title": title,
                "question": msg_text,
                "photo_file_id": "",
            }
            context.user_data[UD_STATE] = STATE_TICKET_WAITING_PHOTO
            await update.message.reply_text(
                "🖼 لطفاً اگر اسکرین‌شات دارید ارسال کنید (عکس + کپشن).\n"
                "اگر اسکرین‌شات ندارید روی دکمه «رد کردن» بزنید.\n\n"
                "💡 می‌توانید عکس هم ضمیمه کنید (عکس + کپشن):",
                reply_markup=ticket_skip_screenshot_keyboard("new"),
            )
            return
        elif mode.startswith("reply:"):
            code = int(mode.split(":")[1])
            ticket = get_ticket(agent_id, code)
            if not ticket:
                await update.message.reply_text("❌ تیکت یافت نشد.", reply_markup=main_menu_keyboard())
                context.user_data.pop(UD_STATE, None)
                return
            if str(ticket.get("status") or "").strip().lower() == "closed":
                await update.message.reply_text("❌ این تیکت بسته شده است.", reply_markup=main_menu_keyboard())
                context.user_data.pop(UD_STATE, None)
                return
            # مرحله ۱: ثبت متن پاسخ سپس رفتن به مرحله اسکرین‌شات (مثل ربات کاربران)
            context.user_data["pending_reply"] = {
                "ticket_code": code,
                "reply_text": msg_text,
                "photo_file_id": "",
            }
            context.user_data[UD_STATE] = STATE_TICKET_WAITING_PHOTO
            await update.message.reply_text(
                "🖼 لطفاً اگر اسکرین‌شات دارید ارسال کنید.\n"
                "اگر اسکرین‌شات ندارید روی دکمه «رد کردن» بزنید.",
                reply_markup=ticket_skip_screenshot_keyboard("reply"),
            )
            return

        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_TICKET_MODE, None)
        context.user_data.pop(UD_TICKET_QUESTION, None)
        return

    # ---- Ticket optional screenshot handler ----
    if state == STATE_TICKET_WAITING_PHOTO:
        is_reply = bool(context.user_data.get("pending_reply"))
        if is_reply:
            pending = context.user_data.get("pending_reply", {})
        else:
            pending = context.user_data.get("pending_ticket", {})
        if not pending:
            context.user_data.pop(UD_STATE, None)
            await update.message.reply_text("❌ اطلاعات تیکت پیدا نشد. دوباره تلاش کنید.", reply_markup=main_menu_keyboard())
            return
        _flow = "reply" if is_reply else "new"
        if not update.message.photo:
            await update.message.reply_text(
                "🖼 لطفاً عکس ارسال کنید یا دکمه «رد کردن» را بزنید.",
                reply_markup=ticket_skip_screenshot_keyboard(_flow),
            )
            return
        pending["photo_file_id"] = update.message.photo[-1].file_id
        context.user_data["pending_reply" if is_reply else "pending_ticket"] = pending
        context.user_data[UD_STATE] = STATE_TICKET_CONFIRM
        if is_reply:
            preview_text = f"📩 <b>تایید اطلاعات پاسخ تیکت</b>\n\n📝 پاسخ: {pending.get('reply_text', '')}\n\n❗️در صورت تایید، دکمه «✅ارسال» را بزنید."
            await update.message.reply_text(preview_text, reply_markup=ticket_confirm_keyboard("reply"), parse_mode="HTML")
        else:
            await update.message.reply_text(
                _format_ticket_confirm_text(pending),
                reply_markup=ticket_confirm_keyboard(_flow),
                parse_mode="HTML",
            )
        return

    # ---- Connect subscription handler ----
    if state == "WAIT_CONNECT_SUB_INPUT":
        context.user_data.pop(UD_STATE, None)
        uuid_pattern = re.findall(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            text,
        )
        if not uuid_pattern:
            await update.message.reply_text(
                "❌ UUID معتبر یافت نشد. لطفاً یک کانفیگ یا UUID معتبر ارسال کنید.",
                reply_markup=main_menu_keyboard(),
            )
            return
        parsed_uuid = uuid_pattern[0]
        # Find the subscription on panels
        from Shared.hiddify_api import get_user_by_uuid
        servers_list = get_servers()
        found_server = None
        for srv in servers_list:
            try:
                user_data = get_user_by_uuid(
                    panel_url=srv.get("panel_url", ""),
                    admin_uuid=srv.get("admin_uuid", ""),
                    uuid=parsed_uuid,
                    proxy_path=srv.get("proxy_path", ""),
                )
                if user_data:
                    found_server = srv
                    break
            except Exception:
                continue
        if not found_server:
            await update.message.reply_text(
                "❌ این اشتراک در هیچ سروری یافت نشد.",
                reply_markup=main_menu_keyboard(),
            )
            return
        cust = get_customer_by_telegram_id(agent_id, user.id)
        if not cust:
            cust_id = upsert_customer(agent_id, user.id, user.username or "", user.full_name or "")
        else:
            cust_id = cust["id"]
        existing = get_services_by_customer(cust_id)
        for svc in existing:
            if svc.get("panel_user_uuid") == parsed_uuid:
                await update.message.reply_text(
                    "❌ این اشتراک قبلاً متصل شده است.",
                    reply_markup=main_menu_keyboard(),
                )
                return
        note = make_service_note(agent_id)
        try:
            from Shared import hiddify_api
            await hiddify_api.patch_user(found_server, parsed_uuid, {"comment": note})
        except Exception:
            pass
        svc = create_service(
            agent_id=agent_id,
            customer_id=cust_id,
            server_id=found_server["id"],
            server_title=found_server.get("title", ""),
            name=f"اشتراک متصل {parsed_uuid[:8]}",
            panel_user_uuid=parsed_uuid,
            usage_limit=0,
            days=0,
            sale_price=0,
            note=note,
        )
        if svc:
            add_service_node(
                service_id=svc["id"],
                server_id=found_server["id"],
                server_title=found_server.get("title", ""),
                panel_user_uuid=parsed_uuid,
            )
            from Shared.agent_db import update_service
            update_service(svc["id"], {"comment": "connected"})
        await update.message.reply_text(
            "✅ اشتراک با موفقیت متصل شد.",
            reply_markup=main_menu_keyboard(),
        )
        return

    # ---- Rename handler (مثل ربات کاربران: اعتبارسنجی + اعمال روی همه پنل‌ها) ----
    if state and state.startswith("rename:"):
        svc_id = int(state.split(":")[1])
        new_name = re.sub(r"\s+", " ", (text or "").strip())
        if len(new_name) < 3:
            await update.message.reply_text(
                "❌ نام اشتراک خیلی کوتاه است. حداقل 3 کاراکتر وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return
        if len(new_name) > 64:
            await update.message.reply_text(
                "❌ نام اشتراک خیلی طولانی است. حداکثر 64 کاراکتر وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return
        svc = get_service_by_id(svc_id)
        if not svc:
            context.user_data.pop(UD_STATE, None)
            await update.message.reply_text("❌ اشتراک موردنظر یافت نشد.", reply_markup=main_menu_keyboard())
            return
        old_name = str(svc.get("name") or "").strip()
        if new_name == old_name:
            await update.message.reply_text(
                "ℹ️ نام جدید با نام فعلی یکسان است. نام دیگری وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return
        await update.message.reply_text("⏳ در حال بروزرسانی نام اشتراک...")
        from CustomerBot.services import rename_service_on_panels
        ok, result_text = await rename_service_on_panels(svc, new_name)
        if not ok:
            await update.message.reply_text(result_text, reply_markup=cancel_keyboard())
            return
        context.user_data.pop(UD_STATE, None)
        await update.message.reply_text(result_text, reply_markup=main_menu_keyboard())
        # نمایش مجدد وضعیت با نام جدید (مثل ربات کاربران)
        try:
            from CustomerBot.services import build_subscription_status_text
            from CustomerBot.database import get_subs_settings, get_buy_renew_settings
            from CustomerBot.keyboards import subscription_status_keyboard
            refreshed = get_service_by_id(svc_id)
            if refreshed:
                status_text = build_subscription_status_text(
                    refreshed, get_subs_settings(agent_id), get_buy_renew_settings(agent_id)
                )
                await update.message.reply_text(
                    status_text,
                    parse_mode="Markdown",
                    reply_markup=subscription_status_keyboard(svc_id),
                )
        except Exception:
            pass
        return
