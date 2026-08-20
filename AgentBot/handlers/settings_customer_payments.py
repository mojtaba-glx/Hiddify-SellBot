import json
import logging
import asyncio
import re

from telegram import Bot, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes

from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import (
    _ikb, IButton, BTN_BACK, back_keyboard,
)
from AgentBot.utils.helpers import _fmt_toman
from AgentBot.database import (
    get_customer_pending_card_payments,
    update_customer_payment_status,
    get_customer_user,
    get_customer_payment_by_id_enriched,
)
from CustomerBot.database import update_order_status
from Shared import agent_db
from Shared.agent_db import get_active_customer_bot

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


def _calc_wholesale_price(agent_id: int, pay: dict, order: dict | None = None) -> int:
    """Calculate wholesale price from payment or order data."""
    if order is None:
        order = pay.get("_order") or {}
    wholesale = int(pay.get("_wholesale_price") or order.get("wholesale_price") or 0)
    if wholesale <= 0:
        wholesale = agent_db.calculate_wholesale_price(
            agent_id,
            float(order.get("volume_gb") or pay.get("_gb") or 0),
            int(order.get("days") or pay.get("_days") or 0),
            int(order.get("server_id") or pay.get("_server_id") or 0),
        )
    return wholesale


def _find_service_by_order(agent_id: int, order_id: int) -> dict:
    """اگر قبلاً برای این سفارش سرویس ساخته شده، آن را برمی‌گرداند."""
    try:
        from Shared.agent_db import search_services_by_name
        panel_name = f"vpn-{int(order_id):07d}"
        rows, _total = search_services_by_name(agent_id, panel_name, page=1, page_size=5)
        for r in rows or []:
            if str(r.get("name") or "") == panel_name:
                return r
    except Exception:
        pass
    return {}


def _change_status_options_keyboard(pay_id: int, current_status: str):
    status = (current_status or "").strip().lower()
    rows = []
    if status != "approved":
        rows.append([IButton("\u2705 \u062a\u0627\u06cc\u06cc\u062f \u0634\u062f\u0647", callback_data=f"agbot:custpay:set:{pay_id}:approved")])
    if status != "rejected":
        rows.append([IButton("\u274c \u0631\u062f \u0634\u062f\u0647", callback_data=f"agbot:custpay:set:{pay_id}:rejected")])
    if status != "pending":
        rows.append([IButton("\u23f3 \u062f\u0631 \u062d\u0627\u0644 \u0627\u0646\u062a\u0638\u0627\u0631", callback_data=f"agbot:custpay:set:{pay_id}:pending")])
    rows.append([IButton(BTN_BACK, callback_data=f"agbot:custpay:chg:back:{pay_id}")])
    return _ikb(rows)


async def _show_change_options(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    pay = get_customer_payment_by_id_enriched(agent_id, pay_id)
    if not pay:
        await query.answer("❌ تراکنش یافت نشد.", show_alert=True)
        return
    current_status = str(pay.get("status") or "pending").strip().lower()
    if current_status == "approved":
        await query.answer("🔒 تراکنش‌های تاییدشده قابل تغییر وضعیت نیستند.", show_alert=True)
        return
    from AgentBot.handlers.settings_transactions import _build_payment_detail_text
    text = _build_payment_detail_text(pay) + "\n\nوضعیت جدید تراکنش را انتخاب کنید:"
    kb = _change_status_options_keyboard(pay_id, current_status)
    try:
        if query.message and getattr(query.message, "photo", None):
            await query.edit_message_caption(caption=text, reply_markup=kb, parse_mode="HTML")
            return
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.warning("Failed to edit change-status options for payment %s: %s", pay_id, e)


async def _redetail_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    pay = get_customer_payment_by_id_enriched(agent_id, pay_id)
    if not pay:
        await query.answer("❌ تراکنش یافت نشد.", show_alert=True)
        return
    from AgentBot.handlers.settings_transactions import _send_payment_detail
    await _send_payment_detail(context, agent_id, query.message.chat_id, pay, source_message=query.message)


async def _apply_change_status(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int, new_status: str) -> None:
    query = update.callback_query
    new_status = (new_status or "").strip().lower()
    if new_status not in {"pending", "approved", "rejected"}:
        await query.answer("❌ وضعیت مقصد نامعتبر است.", show_alert=True)
        return
    pay = get_customer_payment_by_id_enriched(agent_id, pay_id)
    if not pay:
        await query.answer("❌ تراکنش یافت نشد.", show_alert=True)
        return
    old_status = str(pay.get("status") or "").strip().lower()
    if old_status == "approved":
        await query.answer("🔒 تراکنش‌های تاییدشده قابل تغییر وضعیت نیستند.", show_alert=True)
        return
    if old_status == new_status:
        await query.answer("وضعیت تراکنش تغییری نکرد.", show_alert=True)
        return

    if new_status == "approved":
        order = pay.get("_order") or {}
        order_id = int(pay.get("_order_id") or order.get("order_id") or 0)
        if order_id and _find_service_by_order(agent_id, order_id):
            await query.answer("⚠️ برای این سفارش سرویسی از قبل وجود دارد؛ امکان تایید مجدد نیست.", show_alert=True)
            return
        await _approve_payment(update, context, agent_id, pay_id, pay=pay)
        return

    if not update_customer_payment_status(agent_id, pay_id, new_status):
        await query.answer("❌ تغییر وضعیت انجام نشد.", show_alert=True)
        return

    order = pay.get("_order") or {}
    order_id = int(pay.get("_order_id") or order.get("order_id") or 0)
    if order_id:
        try:
            update_order_status(agent_id, order_id, new_status)
        except Exception as e:
            logger.warning("Failed to update order %s status to %s: %s", order_id, new_status, e)

    user_tg_id = int(pay.get("user_id") or 0)
    amount = int(pay.get("amount") or 0)
    if new_status == "rejected":
        notify_text = (
            f"\u274c \u067e\u0631\u062f\u0627\u062e\u062a \u0634\u0645\u0627 \u0628\u0647 \u0645\u0628\u0644\u063a {_fmt_toman(amount)} \u062a\u0648\u0645\u0627\u0646 \u0631\u062f \u0634\u062f.\n"
            f"\u0644\u0637\u0641\u0627 \u0628\u0631\u0627\u06cc \u0627\u0637\u0644\u0627\u0639\u0627\u062a \u0628\u06cc\u0634\u062a\u0631 \u0628\u0627 \u067e\u0634\u062a\u06cc\u0628\u0627\u0646\u06cc \u062a\u0645\u0627\u0633 \u0628\u06af\u06cc\u0631\u06cc\u062f."
        )
        await _notify_customer(context, agent_id, user_tg_id, notify_text)
        await _delete_pending_customer_message(context, agent_id, pay)
    else:
        notify_text = (
            f"\u23f3 \u0648\u0636\u0639\u06cc\u062a \u067e\u0631\u062f\u0627\u062e\u062a \u0634\u0645\u0627 \u0628\u0647 \u0645\u0628\u0644\u063a {_fmt_toman(amount)} \u062a\u0648\u0645\u0627\u0646 \u0628\u0647 \u062d\u0627\u0644\u062a \u00ab\u062f\u0631 \u062d\u0627\u0644 \u0627\u0646\u062a\u0638\u0627\u0631\u00bb \u062a\u063a\u06cc\u06cc\u0631 \u06a9\u0631\u062f.\n"
            f"پس از بررسی، نتیجه به اطلاع شما می‌رسد."
        )
        await _notify_customer(context, agent_id, user_tg_id, notify_text)

    try:
        await query.answer("✅ وضعیت تراکنش تغییر کرد.", show_alert=True)
    except Exception:
        pass
    try:
        pay_after = get_customer_payment_by_id_enriched(agent_id, pay_id) or pay
        pay_after = dict(pay_after)
        pay_after["status"] = new_status
        from AgentBot.handlers.settings_transactions import _send_payment_detail
        await _send_payment_detail(context, agent_id, query.message.chat_id, pay_after, source_message=query.message)
    except Exception as e:
        logger.warning("Failed to redisplay payment %s after status change: %s", pay_id, e)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query:
        return False
    data = (query.data or "").strip()
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    sub = parts[2] if len(parts) > 2 else ""
    agent_id = get_agent_id(context)

    if action == "custpay" and sub not in {"approve", "reject", "profile", "chg", "set"}:
        try:
            await query.answer()
        except Exception:
            pass

    # ---- Customer payment menu ----
    if action == "custpay":
        if sub == "menu":
            await _show_payments_menu(update, context)
            return

        if sub == "list":
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            await _list_pending_payments(update, context, agent_id, page)
            return

        if sub == "detail":
            pay_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            await _show_payment_detail(update, context, agent_id, pay_id)
            return

        if sub == "approve":
            pay_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            await _approve_payment(update, context, agent_id, pay_id)
            return

        if sub == "reject":
            pay_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            await _reject_payment(update, context, agent_id, pay_id)
            return

        if sub == "profile":
            user_tg_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            await _show_customer_profile(update, context, agent_id, user_tg_id)
            return

        if sub == "chg":
            if len(parts) == 5 and parts[3] == "back":
                pay_id = int(parts[4]) if parts[4].isdigit() else 0
                await _redetail_payment(update, context, agent_id, pay_id)
                return
            pay_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            await _show_change_options(update, context, agent_id, pay_id)
            return

        if sub == "set":
            if len(parts) == 5:
                pay_id = int(parts[3]) if parts[3].isdigit() else 0
                await _apply_change_status(update, context, agent_id, pay_id, parts[4])
                return
            await query.answer("❌ داده نامعتبر.", show_alert=True)
            return

        if sub == "back":
            from AgentBot.handlers.main_menu import handle_start
            await handle_start(update, context)
            return

    return False


async def _show_payments_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    text = (
            "💳 <b>مدیریت پرداخت‌های مشتریان</b>\n\n"
        "در این بخش می‌توانید رسیدهای خرید مشتریان را بررسی، تایید یا رد کنید."
    )
    kb = _ikb([
        [IButton("\U0001f4cb \u0644\u06cc\u0633\u062a \u067e\u0631\u062f\u0627\u062e\u062a\u200c\u0647\u0627\u06cc \u062f\u0631 \u0627\u0646\u062a\u0638\u0627\u0631", callback_data="agbot:custpay:list:1")],
        [IButton(BTN_BACK, callback_data="agbot:set:back")],
    ])
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


async def _list_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, page: int = 1) -> None:
    query = update.callback_query
    payments = get_customer_pending_card_payments(agent_id)
    total = len(payments)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_pays = payments[start:end]

    text = f"\U0001f4cb <b>\u067e\u0631\u062f\u0627\u062e\u062a\u200c\u0647\u0627\u06cc \u062f\u0631 \u0627\u0646\u062a\u0638\u0627\u0631</b> (\u062a\u0639\u062f\u0627\u062f: {total})\n\n"
    if not page_pays:
        text += "\u0647\u06cc\u0686 \u067e\u0631\u062f\u0627\u062e\u062a \u0645\u0646\u062a\u0638\u0631\u06cc \u0648\u062c\u0648\u062f \u0646\u062f\u0627\u0631\u062f."
    else:
        ptype = "\U0001f4e6 خرید اشتراک"
        for p in page_pays:
            name = p.get("full_name") or p.get("username") or f"\u06a9\u0627\u0631\u0628\u0631 #{p.get('user_id', '?')}"
            amount = p.get("amount", 0)
            tx_code = p.get("tx_code", "")
            created = (p.get("created_at") or "")[:16]
            text += f"{ptype} | <b>{name}</b> | {_fmt_toman(amount)} \u062a\u0648\u0645\u0627\u0646 | {created}\n"
            text += f"\u06a9\u062f: {tx_code} | "
            text += f"[\U0001f4fa \u0645\u0634\u0627\u0647\u062f\u0647 \u062c\u0632\u0626\u06cc\u0627\u062a](agbot:custpay:detail:{p['id']})\n\n"

    kb_rows = []
    if page_pays:
        for p in page_pays:
            kb_rows.append([
                IButton(f"\U0001f4fa {p.get('tx_code', '')}", callback_data=f"agbot:custpay:detail:{p['id']}"),
            ])
    # Pagination
    nav = []
    if page > 1:
        nav.append(IButton("\u2b05\ufe0f", callback_data=f"agbot:custpay:list:{page-1}"))
    if page < total_pages:
        nav.append(IButton("\u27a1\ufe0f", callback_data=f"agbot:custpay:list:{page+1}"))
    if nav:
        kb_rows.append(nav)
    kb_rows.append([IButton(BTN_BACK, callback_data="agbot:custpay:menu")])

    try:
        await query.edit_message_text(text, reply_markup=_ikb(kb_rows), parse_mode="HTML")
    except Exception:
        pass


async def _show_payment_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int) -> None:
    query = update.callback_query
    payments = get_customer_pending_card_payments(agent_id)
    pay = next((p for p in payments if p["id"] == pay_id), None)
    if not pay:
        try:
            await query.edit_message_text("\u274c \u067e\u0631\u062f\u0627\u062e\u062a \u06cc\u0627\u0641\u062a \u0646\u0634\u062f.", reply_markup=back_keyboard("agbot:custpay:list:1"))
        except Exception:
            pass
        return

    name = pay.get("full_name") or pay.get("username") or f"\u06a9\u0627\u0631\u0628\u0631 #{pay.get('user_id', '?')}"
    amount = pay.get("amount", 0)
    tx_code = pay.get("tx_code", "")
    created = pay.get("created_at", "") or ""
    ptype = "\U0001f4e6 خرید اشتراک"
    order = pay.get("_order")

    text = (
        f"<b>جزئیات درخواست مشتری</b>\n\n"
        f"\U0001f464 \u0645\u0634\u062a\u0631\u06cc: {name}\n"
        f"{ptype}\n"
        f"\U0001f4b0 \u0645\u0628\u0644\u063a: {_fmt_toman(amount)} \u062a\u0648\u0645\u0627\u0646\n"
        f"\U0001f522 \u06a9\u062f \u067e\u06cc\u06af\u06cc\u0631\u06cc: {tx_code}\n"
        f"\U0001f4c5 \u062a\u0627\u0631\u06cc\u062e: {created}\n"
    )
    if order:
        wholesale = _calc_wholesale_price(agent_id, pay, order)
        text += (
            f"\n<code>\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501</code>\n"
            f"\U0001f4e6 \u067e\u0644\u0646: {order.get('plan_title', '?')}\n"
            f"\U0001f4ca \u062d\u062c\u0645: {order.get('volume_gb', 0)} \u06af\u06cc\u06af\n"
            f"\u23f0 \u0631\u0648\u0632: {order.get('days', 0)}\n"
            f"\U0001f4cd \u0645\u06a9\u0627\u0646: {order.get('server_location', '?')}\n"
        )

        text += (
            f"\U0001f3f7 \u0647\u0632\u06cc\u0646\u0647 \u0639\u0645\u062f\u0647: {_fmt_toman(wholesale)} \u062a\u0648\u0645\u0627\u0646\n"
            f"\U0001f4bc \u0645\u0648\u062c\u0648\u062f\u06cc \u0646\u0645\u0627\u06cc\u0646\u062f\u0647: {_fmt_toman(agent_db.get_wallet_balance(agent_id))} \u062a\u0648\u0645\u0627\u0646\n"
        )

    raw_receipt = pay.get("receipt_image", "")
    from AgentBot.handlers.settings_transactions import _parse_receipt_meta
    meta = _parse_receipt_meta(str(raw_receipt or ""))
    receipt_fid = str(meta.get("file_id") or "").strip()
    if not receipt_fid and raw_receipt and ":" not in str(raw_receipt) and "|" not in str(raw_receipt):
        receipt_fid = str(raw_receipt).strip()
    card_last4 = str(meta.get("card_last4") or "").strip()
    if card_last4:
        text += f"\n\U0001f4b3 \u06f4 \u0631\u0642\u0645 \u0622\u062e\u0631 \u06a9\u0627\u0631\u062a: <code>{card_last4}</code>\n"
    if not receipt_fid:
        text += "\n\n\u28fe \u0647\u06cc\u0686 \u0631\u0633\u06cc\u062f\u06cc \u0628\u0631\u0627\u06cc \u0627\u06cc\u0646 \u067e\u0631\u062f\u0627\u062e\u062a \u0648\u062c\u0648\u062f \u0646\u062f\u0627\u0631\u062f."

    kb_rows = [
        [
            IButton("❌ رد پرداخت", callback_data=f"agbot:custpay:reject:{pay_id}"),
            IButton("✅ تایید پرداخت", callback_data=f"agbot:custpay:approve:{pay_id}"),
        ],
        [IButton(f"👤 {name}", callback_data=f"agbot:custpay:profile:{pay.get('user_id', 0)}")],
        [IButton(BTN_BACK, callback_data="agbot:custpay:list:1")],
    ]

    try:
        if receipt_fid:
            from AgentBot.handlers.settings_transactions import _send_receipt_photo_fallback
            ok = await _send_receipt_photo_fallback(context, agent_id, query.message.chat_id, receipt_fid, text, _ikb(kb_rows), None)
            if ok:
                try:
                    await query.message.delete()
                except Exception as del_err:
                    logger.debug("Failed to delete old message after sending photo: %s", del_err)
            else:
                await query.edit_message_text(text, reply_markup=_ikb(kb_rows), parse_mode="HTML")
        else:
            await query.edit_message_text(text, reply_markup=_ikb(kb_rows), parse_mode="HTML")
    except Exception as photo_err:
        logger.warning("Failed to send photo for payment %s: %s", pay_id, photo_err)
        try:
            await query.edit_message_text(text, reply_markup=_ikb(kb_rows), parse_mode="HTML")
        except Exception as edit_err:
            logger.warning("Failed to edit message as fallback for payment %s: %s", pay_id, edit_err)


async def _approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int, pay: dict = None) -> None:
    query = update.callback_query
    if pay is None:
        payments = get_customer_pending_card_payments(agent_id)
        pay = next((p for p in payments if p["id"] == pay_id), None)
    if not pay:
        await query.answer("❌ پرداخت در لیست در انتظار پیدا نشد.", show_alert=True)
        return

    user_tg_id = pay.get("user_id", 0)

    if pay.get("_pay_type") == "buy" and pay.get("_order"):
        # Create subscription for approved buy payment
        order = dict(pay["_order"])
        if not int(order.get("server_id") or 0):
            order["server_id"] = int(pay.get("_server_id") or 0)
        wholesale_price = _calc_wholesale_price(agent_id, pay, order)
        if wholesale_price <= 0:
            await query.answer("قیمت عمده برای این سفارش تنظیم نشده است. از ادمین بخواهید تعرفه عمده را ثبت کند.", show_alert=True)
            return
        wallet_balance = agent_db.get_wallet_balance(agent_id)
        if wallet_balance < wholesale_price:
            await query.answer(
                f"موجودی کیف پول کافی نیست. موجودی: {_fmt_toman(wallet_balance)} تومان | مورد نیاز: {_fmt_toman(wholesale_price)} تومان",
                show_alert=True,
            )
            return
        if not update_customer_payment_status(agent_id, pay_id, "processing"):
            await query.answer("خطا در قفل کردن پرداخت.", show_alert=True)
            return
        deducted, _ = agent_db.deduct_wallet(
            agent_id,
            wholesale_price,
            description=f"کسر عمده سفارش مشتری #{order.get('order_id') or pay.get('tx_code')}",
        )
        if not deducted:
            update_customer_payment_status(agent_id, pay_id, "pending")
            await query.answer("موجودی کیف پول کافی نیست. لطفاً کیف پول خود را شارژ کنید.", show_alert=True)
            return
        try:
            svc = await _create_subscription_from_order(context, agent_id, user_tg_id, order, wholesale_price, tx_code=str(pay.get("tx_code") or ""))
        except Exception as e:
            agent_db.refund_wallet(agent_id, wholesale_price, description=f"بازگشت بابت خطای ساخت سرویس سفارش #{order.get('order_id')}")
            update_customer_payment_status(agent_id, pay_id, "pending")
            if int(order.get("order_id") or 0):
                update_order_status(agent_id, int(order.get("order_id")), "pending")
            logger.error(f"Failed to create subscription for payment {pay_id}: {e}")
            await query.answer(f"خطا در ساخت سرویس؛ مبلغ از کیف پول نماینده برگشت خورد: {e}", show_alert=True)
            return
        if not update_customer_payment_status(agent_id, pay_id, "approved"):
            logger.error("Payment %s approved service %s but status update failed", pay_id, (svc or {}).get("id"))
            await query.answer("سرویس ساخته شد اما ثبت وضعیت پرداخت خطا داد. لاگ را بررسی کنید.", show_alert=True)
            return
        if int(order.get("order_id") or 0):
            update_order_status(agent_id, int(order.get("order_id") or 0), "approved")
        if svc:
            try:
                from Shared.subscription_reports import send_subscription_report
                await send_subscription_report(
                    context.bot,
                    query.message.chat_id,
                    agent_id,
                    user_tg_id,
                    svc,
                    "create",
                    int(pay.get("amount") or order.get("price") or 0),
                )
            except Exception as report_err:
                logger.warning("Failed to send create subscription report for payment %s: %s", pay_id, report_err)
    else:
        # No order info - just mark approved
        ok = update_customer_payment_status(agent_id, pay_id, "approved")
        if not ok:
            await query.answer("خطا در به‌روزرسانی.", show_alert=True)
            return

    amount = int(pay.get("amount") or 0)
    tx_code = str(pay.get("tx_code") or "-")
    customer_name = pay.get("full_name") or pay.get("username") or f"Customer {user_tg_id}"
    done_text = (
        "🕊️💸 گزارش تایید پرداخت 💸🕊️\n\n"
        "📌 شیوه پرداخت: کارت به کارت\n"
        f"🔑 شناسه تراکنش: {tx_code}\n"
        f"💰 مبلغ پرداخت: {amount:,} تومان"
    )
    card_last4 = str(pay.get("_card_last4") or "").strip()
    if not card_last4:
        try:
            from AgentBot.handlers.settings_transactions import _parse_receipt_meta
            card_last4 = str(_parse_receipt_meta(str(pay.get("receipt_image") or "")).get("card_last4") or "").strip()
        except Exception:
            card_last4 = ""
    if card_last4:
        done_text += f"\n💳 ۴ رقم آخر کارت: <code>{card_last4}</code>"
    done_text += "\n\n✅ پرداخت تایید شد؛ اشتراک مشتری ساخته شد."
    done_kb = _ikb([
        [IButton(f"👤 {customer_name}", callback_data=f"agbot:custpay:profile:{user_tg_id}")],
    ])
    try:
        await query.answer("✅ پرداخت با موفقیت تایید شد.", show_alert=True)
    except Exception:
        pass
    try:
        if query.message and query.message.photo:
            await query.edit_message_caption(caption=done_text, parse_mode="HTML", reply_markup=done_kb)
        else:
            await query.edit_message_text(done_text, parse_mode="HTML", reply_markup=done_kb)
    except Exception as edit_err:
        logger.warning("Failed to edit approval message for payment %s: %s", pay_id, edit_err)
        try:
            if query.message:
                await query.message.reply_text(done_text, parse_mode="HTML", reply_markup=done_kb)
        except Exception as reply_err:
            logger.warning("Failed to send approval reply for payment %s: %s", pay_id, reply_err)


async def _reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int, pay: dict = None) -> None:
    query = update.callback_query
    if pay is None:
        payments = get_customer_pending_card_payments(agent_id)
        pay = next((p for p in payments if p["id"] == pay_id), None)
    if not pay:
        await query.answer("❌ پرداخت در لیست در انتظار پیدا نشد.", show_alert=True)
        return

    if not update_customer_payment_status(agent_id, pay_id, "rejected"):
        await query.answer("❌ رد پرداخت انجام نشد.", show_alert=True)
        return
    order = pay.get("_order") or {}
    if int(order.get("order_id") or pay.get("_order_id") or 0):
        update_order_status(agent_id, int(order.get("order_id") or pay.get("_order_id") or 0), "rejected")

    user_tg_id = pay.get("user_id", 0)
    amount = pay.get("amount", 0)
    notify_text = (
        f"\u274c \u067e\u0631\u062f\u0627\u062e\u062a \u0634\u0645\u0627 \u0628\u0647 \u0645\u0628\u0644\u063a {_fmt_toman(amount)} \u062a\u0648\u0645\u0627\u0646 \u0631\u062f \u0634\u062f.\n"
        f"\u0644\u0637\u0641\u0627 \u0628\u0631\u0627\u06cc \u0627\u0637\u0644\u0627\u0639\u0627\u062a \u0628\u06cc\u0634\u062a\u0631 \u0628\u0627 \u067e\u0634\u062a\u06cc\u0628\u0627\u0646\u06cc \u062a\u0645\u0627\u0633 \u0628\u06af\u06cc\u0631\u06cc\u062f."
    )

    await _notify_customer(context, agent_id, user_tg_id, notify_text)
    await _delete_pending_customer_message(context, agent_id, pay)

    done_text = "❌ پرداخت رد شد."
    customer_name = pay.get("full_name") or pay.get("username") or f"Customer {user_tg_id}"
    done_kb = _ikb([
        [IButton("✏️ تغییر وضعیت", callback_data=f"agbot:custpay:chg:{pay_id}")],
        [IButton(f"👤 {customer_name}", callback_data=f"agbot:custpay:profile:{user_tg_id}")],
    ])
    try:
        await query.answer("❌ پرداخت رد شد.", show_alert=True)
    except Exception:
        pass
    try:
        if query.message and query.message.photo:
            await query.edit_message_caption(caption=done_text, parse_mode="HTML", reply_markup=done_kb)
        else:
            await query.edit_message_text(done_text, parse_mode="HTML", reply_markup=done_kb)
    except Exception as edit_err:
        logger.warning("Failed to edit rejection message for payment %s: %s", pay_id, edit_err)
        try:
            if query.message:
                await query.message.reply_text(done_text, parse_mode="HTML", reply_markup=done_kb)
        except Exception as reply_err:
            logger.warning("Failed to send rejection reply for payment %s: %s", pay_id, reply_err)


async def _show_customer_profile(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, user_tg_id: int) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    customer = agent_db.get_customer_by_telegram_id(agent_id, user_tg_id)
    if not customer:
        try:
            await query.answer("❌ اطلاعات مشتری یافت نشد.", show_alert=True)
        except Exception:
            pass
        return
    from AgentBot.handlers.settings_users import _build_profile_text
    from AgentBot.keyboards import users_profile_keyboard
    text = _build_profile_text(agent_id, customer)
    kb = users_profile_keyboard(int(customer.get("id") or 0), user_tg_id, back_callback="agbot:set:users")
    try:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as edit_err:
        logger.warning("custpay profile send failed user=%s: %s", user_tg_id, edit_err)


async def _create_subscription_from_order(context: ContextTypes.DEFAULT_TYPE, agent_id: int, user_tg_id: int, order: dict, wholesale_price: int = 0, tx_code: str = "") -> dict:
    import time
    import uuid

    from AgentBot.services.subscription_service import _get_cluster_servers
    from Shared.agent_db import upsert_customer, create_service, add_service_node, get_customer_by_telegram_id, make_service_note
    from AgentBot.database import upsert_customer_user, get_customer_user
    from Shared.database import get_server_by_id

    # Get or create customer (محلی customer_users) — اگر ردیف مشتری موجود نشد، می‌سازیم
    cust = get_customer_user(agent_id, user_tg_id)
    if not cust:
        upsert_customer_user(
            agent_id,
            user_tg_id,
            str(order.get("username") or "").strip(),
            str(order.get("full_name") or "").strip(),
        )
        cust = get_customer_user(agent_id, user_tg_id)
    if not cust:
        raise RuntimeError("customer_not_found")

    # Get customer from shared DB
    shared_cust = get_customer_by_telegram_id(agent_id, user_tg_id)
    if not shared_cust:
        shared_cust_id = upsert_customer(agent_id, user_tg_id, cust.get("username", ""), cust.get("full_name", ""))
    else:
        shared_cust_id = shared_cust["id"]

    server_id = order.get("server_id", 0)
    volume_gb = float(order.get("volume_gb", 0))
    days = int(order.get("days", 0))
    price = int(order.get("price", 0))
    plan_title = order.get("plan_title", "")

    server = get_server_by_id(server_id)
    if not server:
        logger.error(f"Server {server_id} not found for agent {agent_id}")
        raise RuntimeError("server_not_found")

    # Create user on Hiddify panel (main + all child nodes, shared UUID)
    new_uuid = str(uuid.uuid4())
    order_id_num = int(order.get("order_id") or 0)
    if order_id_num:
        panel_name = f"vpn-{order_id_num:07d}"
    else:
        panel_name = f"vpn-{user_tg_id}-{int(time.time())}"
    note = make_service_note(agent_id)
    payload = {
        "name": panel_name,
        "usage_limit_GB": volume_gb,
        "package_days": days,
        "uuid": new_uuid,
        "is_active": True,
        "comment": note,
    }

    targets = _get_cluster_servers(server_id)
    if not targets:
        targets = [server]

    from Shared.multi_panel import create_user as mp_create_user
    shared_uuid = new_uuid
    payload["uuid"] = shared_uuid
    created_nodes: list[dict] = []
    panel_user = None
    primary_marzban = ""
    for idx, tgt in enumerate(targets):
        try:
            created = await mp_create_user(tgt, payload)
        except Exception as e:
            if idx == 0:
                raise
            logger.warning("Cluster node create_user failed server=%s: %s", tgt.get("id"), e)
            continue
        created_uuid = str(created.get("uuid") or created.get("id") or "").strip()
        if not created_uuid:
            if idx == 0:
                raise RuntimeError("uuid کاربر ساخته‌شده از پنل دریافت نشد.")
            continue
        created_nodes.append(
            {
                "server_id": int(tgt.get("id") or 0),
                "server_title": tgt.get("title") or f"سرور #{tgt.get('id')}",
                "panel_user_uuid": created_uuid,
                "panel_user_id": str(created.get("id") or "").strip(),
                "marzban_username": str(created.get("_marzban_username") or "").strip(),
                "is_primary": idx == 0,
            }
        )
        if idx == 0:
            panel_user = created
            primary_marzban = str(created.get("_marzban_username") or "").strip()
    if panel_user is None:
        raise RuntimeError("no primary node created")
    panel_uuid = str(panel_user.get("uuid") or shared_uuid).strip()
    panel_user_id = str(panel_user.get("id") or "").strip()

    # Create service record
    svc = create_service(
        agent_id=agent_id,
        customer_id=shared_cust_id,
        server_id=server_id,
        server_title=server.get("title", ""),
        name=panel_name,
        panel_user_uuid=panel_uuid,
        usage_limit=volume_gb,
        days=days,
        wholesale_price=wholesale_price,
        sale_price=price,
        note=note,
    )
    if svc:
        for item in created_nodes:
            add_service_node(
                service_id=svc["id"],
                server_id=int(item.get("server_id") or 0),
                server_title=item.get("server_title") or "",
                panel_user_uuid=str(item.get("panel_user_uuid") or "").strip(),
                panel_user_id=str(item.get("panel_user_id") or "").strip(),
                marzban_username=str(item.get("marzban_username") or "").strip(),
            )

    # Notify customer
    notify = (
        f"\U0001f389 \u062a\u0631\u0627\u06a9\u0646\u0634 \u0634\u0645\u0627 \u062a\u0627\u06cc\u06cc\u062f \u0634\u062f\n"
        f"\u0627\u0632 \u0637\u0631\u06cc\u0642 \u062f\u06a9\u0645\u0647 [\U0001f4ca\u0648\u0636\u0639\u06cc\u062a \u0627\u0634\u062a\u0631\u0627\u06a9\U0001f4ca] \u0645\u06cc\u062a\u0648\u0627\u0646\u06cc\u062f \u0628\u0647 \u0627\u0637\u0644\u0627\u0639\u0627\u062a \u0627\u0634\u062a\u0631\u0627\u06a9 \u062e\u0648\u062f \u062f\u0633\u062a\u0631\u0633\u06cc \u062f\u0627\u0634\u062a\u0647 \u0628\u0627\u0634\u06cc\u062f.\n\n"
    )
    if tx_code:
        notify += f"\U0001f381 \u0634\u0646\u0627\u0633\u0647 \u062a\u0631\u0627\u06a9\u0646\u0634: {tx_code}"
    await _notify_customer(context, agent_id, user_tg_id, notify)
    pay_stub = {"id": order.get("payment_id") or 0, "user_id": user_tg_id, "receipt_image": order.get("receipt_image", "")}
    await _delete_pending_customer_message(context, agent_id, pay_stub)

    # Deliver subscription info + status keyboard to customer
    if svc:
        await _send_subscription_delivery(context, agent_id, user_tg_id, svc["id"])
    return svc or {}


async def _send_subscription_delivery(context: ContextTypes.DEFAULT_TYPE, agent_id: int, user_tg_id: int, service_id: int) -> None:
    """ارسال لینک اشتراک + QR (یا کانفیگ مستقیم) به مشتری بلافاصله پس از ساخت سرویس."""
    try:
        from html import escape
        from CustomerBot.database import get_subs_settings, get_buy_renew_settings
        from CustomerBot.keyboards import subscription_links_keyboard, subscription_status_keyboard
        from CustomerBot.services import build_subscription_status_text, get_service_node_base_urls, collect_all_direct_configs_for_service, get_or_create_bot_sub_links
        from Shared.qr_utils import make_qr_image
        from Shared.agent_db import get_service_by_id

        svc = get_service_by_id(service_id)
        if not svc:
            return
        bot_row = get_active_customer_bot(agent_id)
        customer_token = str((bot_row or {}).get("bot_token") or "").strip()
        if not customer_token:
            logger.warning("No active customer bot for agent=%s; cannot deliver subscription %s", agent_id, service_id)
            return
        customer_bot = Bot(token=customer_token)
        subs_settings = get_subs_settings(agent_id)
        sent_kind = ""

        config_items: list[tuple[str, str]] = []
        base_urls = get_service_node_base_urls(svc)
        if base_urls:
            base_url = base_urls[0]
            if subs_settings.get("show_sub_link", True):
                config_items.append(("🔗 لینک اشتراک:", f"{base_url}/all.txt"))
            if subs_settings.get("show_auto_sub_link", False):
                config_items.append(("🤖 لینک اشتراک خودکار:", f"{base_url}/sub/?asn=unknown"))
            if subs_settings.get("show_sub_link_b64", False):
                config_items.append(("🔐 لینک اشتراک b64:", f"{base_url}/all.txt?base64=1"))
            if subs_settings.get("show_multi_server", False):
                try:
                    managed_link, _ = get_or_create_bot_sub_links(svc)
                    if managed_link:
                        config_items.append(("🌐 لینک اشتراک هوشمند:", managed_link))
                except Exception as e:
                    logger.warning("Failed to build managed sub link after delivery (service_id=%s): %s", service_id, e)
            if subs_settings.get("show_multi_server_b64", False):
                try:
                    _, managed_link_b64 = get_or_create_bot_sub_links(svc)
                    if managed_link_b64:
                        config_items.append(("🌐 لینک اشتراک هوشمند b64:", managed_link_b64))
                except Exception as e:
                    logger.warning("Failed to build managed sub b64 link after delivery (service_id=%s): %s", service_id, e)

        if len(config_items) == 1:
            primary_link = config_items[0][1]
            qr_image = make_qr_image(primary_link)
            qr_caption = (
                "🎉 اشتراک شما آماده‌ی استفاده است ✅\n\n"
                f"{config_items[0][0]}\n"
                f"<code>{escape(primary_link)}</code>\n\n"
                "📄 جهت کپی شدن لینک کافیست یک‌بار روی لینک بالا لمس کنید."
            )
            try:
                await customer_bot.send_photo(
                    chat_id=user_tg_id,
                    photo=qr_image,
                    caption=qr_caption,
                    parse_mode="HTML",
                    reply_markup=subscription_links_keyboard(service_id),
                    read_timeout=20,
                    write_timeout=20,
                    connect_timeout=10,
                    pool_timeout=20,
                )
            except Exception:
                try:
                    await customer_bot.send_message(
                        chat_id=user_tg_id,
                        text=qr_caption,
                        parse_mode="HTML",
                        reply_markup=subscription_links_keyboard(service_id),
                        disable_web_page_preview=True,
                    )
                except Exception:
                    pass
            sent_kind = "qr"

        if len(config_items) > 1:
            # وقتی چند روش نمایش لینک فعال است، به‌جای انتخاب/تکرار لینک‌ها،
            # اطلاعات اشتراک نمایش داده می‌شود تا مشتری از کیبورد وضعیت انتخاب کند.
            sent_kind = "show_status"

        if not sent_kind and subs_settings.get("show_direct_config", True):
            links = await asyncio.to_thread(collect_all_direct_configs_for_service, svc)
            clean_links = [str(x).strip() for x in (links or []) if str(x).strip()]
            if clean_links:
                all_links_text = "\n".join(clean_links)
                one_block_text = (
                    "🔗 کانفیگ‌های مستقیم\n"
                    "برای کپی، کل باکس زیر را یکجا کپی کنید:\n"
                    f"<pre><code class=\"language-shell\">{escape(all_links_text)}</code></pre>"
                )
                if len(one_block_text) <= 3900:
                    await customer_bot.send_message(
                        chat_id=user_tg_id,
                        text=one_block_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    sent_kind = "direct_config"
                else:
                    max_payload = 2800
                    parts_list: list[list[str]] = []
                    cur: list[str] = []
                    cur_len = 0
                    for link in clean_links:
                        add = len(link) + 1
                        if cur and cur_len + add > max_payload:
                            parts_list.append(cur)
                            cur = [link]
                            cur_len = add
                        else:
                            cur.append(link)
                            cur_len += add
                    if cur:
                        parts_list.append(cur)
                    for idx, chunk in enumerate(parts_list, start=1):
                        header = "🔗 کانفیگ‌های مستقیم" if len(parts_list) == 1 else f"🔗 کانفیگ‌های مستقیم ({idx}/{len(parts_list)})"
                        part_text = (
                            f"{header}\n"
                            "برای کپی، باکس زیر را کپی کنید:\n"
                            f"<pre><code class=\"language-shell\">{escape(chr(10).join(chunk))}</code></pre>"
                        )
                        await customer_bot.send_message(
                            chat_id=user_tg_id,
                            text=part_text,
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                    sent_kind = "direct_config"

        if not sent_kind or sent_kind == "show_status":
            br = get_buy_renew_settings(agent_id)
            svc_text = build_subscription_status_text(svc, subs_settings, br)
            show_detach = bool(svc.get("comment") == "connected")
            kb = subscription_status_keyboard(
                service_id,
                show_direct_config=subs_settings.get("show_direct_config", True),
                show_sub_link=subs_settings.get("show_sub_link", True),
                show_detach=show_detach,
            )
            await customer_bot.send_message(
                chat_id=user_tg_id,
                text=svc_text,
                parse_mode="Markdown",
                reply_markup=kb,
                read_timeout=20,
                write_timeout=20,
                connect_timeout=10,
                pool_timeout=20,
            )

        logger.info("Subscription %s delivered to customer %s (kind=%s)", service_id, user_tg_id, sent_kind or "status")
    except Exception as e:
        logger.warning("Failed to deliver subscription %s to customer %s: %s", service_id, user_tg_id, e)


async def _notify_customer(context: ContextTypes.DEFAULT_TYPE, agent_id: int, user_tg_id: int, text: str) -> None:
    try:
        bot_row = get_active_customer_bot(agent_id)
        customer_token = str((bot_row or {}).get("bot_token") or "").strip()
        if not customer_token:
            logger.warning("Active customer bot token missing for agent=%s; cannot notify user %s", agent_id, user_tg_id)
            return
        customer_bot = Bot(token=customer_token)
        menu_kb = _get_customer_menu_keyboard()
        for attempt in range(1, 4):
            try:
                await customer_bot.send_message(
                    chat_id=user_tg_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=menu_kb,
                    read_timeout=20,
                    write_timeout=20,
                    connect_timeout=10,
                    pool_timeout=20,
                )
                logger.info("Customer notified successfully agent=%s user=%s attempt=%s", agent_id, user_tg_id, attempt)
                return
            except (TimedOut, NetworkError) as e:
                if attempt >= 3:
                    raise
                logger.warning(
                    "Customer notify retry agent=%s user=%s attempt=%s error=%s",
                    agent_id,
                    user_tg_id,
                    attempt,
                    e,
                )
                await asyncio.sleep(1.5 * attempt)
    except Exception as e:
        logger.warning(f"Failed to notify customer {user_tg_id}: {e}")


async def _delete_pending_customer_message(context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay: dict) -> None:
    try:
        user_tg_id = int(pay.get("user_id") or 0)
        if not user_tg_id:
            return
        raw_receipt = str(pay.get("receipt_image") or "")
        match = re.search(r"customer_pending_message_id:(\d+)", raw_receipt)
        if not match:
            return
        pending_message_id = int(match.group(1))
        bot_row = get_active_customer_bot(agent_id)
        customer_token = str((bot_row or {}).get("bot_token") or "").strip()
        if not customer_token:
            return
        customer_bot = Bot(token=customer_token)
        try:
            await customer_bot.delete_message(chat_id=user_tg_id, message_id=pending_message_id)
        except Exception as e:
            logger.warning("Failed to delete pending customer message agent=%s user=%s msg=%s: %s", agent_id, user_tg_id, pending_message_id, e)
    except Exception as e:
        logger.warning("Unexpected error while deleting pending customer message for payment %s: %s", pay.get("id"), e)


def _get_customer_menu_keyboard():
    """Safely import and return customer bot main menu keyboard."""
    try:
        from CustomerBot.keyboards import main_menu_keyboard
        return main_menu_keyboard()
    except Exception as e:
        logger.debug("Could not load customer menu keyboard: %s", e)
        return None


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text messages - not used in this handler, returns False to pass to next handler."""
    return False