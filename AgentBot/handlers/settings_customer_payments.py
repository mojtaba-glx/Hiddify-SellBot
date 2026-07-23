import json
import logging
import os
import asyncio
import re

from telegram import Bot, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes

from AgentBot.constants import (
    UD_STATE, UD_PAGE,
)
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import (
    _ikb, IButton, BTN_BACK, back_keyboard, pagination_keyboard,
)
from AgentBot.utils.helpers import _fmt_toman
from AgentBot.database import (
    get_customer_pending_card_payments,
    update_customer_payment_status,
    get_customer_order_by_id,
    get_customer_user,
    get_customer_payment_by_tx,
    get_setting, set_setting,
)
from CustomerBot.database import get_payment_by_tx_code as get_customer_payment_by_tx_code
from CustomerBot.database import update_order_status
from Shared import agent_db
from Shared.agent_db import get_active_customer_bot

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    sub = parts[2] if len(parts) > 2 else ""
    agent_id = get_agent_id(context)

    if action == "custpay" and sub not in {"approve", "reject"}:
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
        for p in page_pays:
            name = p.get("full_name") or p.get("username") or f"\u06a9\u0627\u0631\u0628\u0631 #{p.get('user_id', '?')}"
            amount = p.get("amount", 0)
            tx_code = p.get("tx_code", "")
            created = (p.get("created_at") or "")[:16]
            ptype = "\U0001f4e6 خرید"  # Simplified to direct purchase only
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
    ptype = "\U0001f4e6 خرید اشتراک"  # Simplified to direct purchase only
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
        wholesale = int(pay.get("_wholesale_price") or order.get("wholesale_price") or 0)
        if wholesale <= 0:
            wholesale = agent_db.calculate_wholesale_price(
                agent_id,
                float(order.get("volume_gb") or pay.get("_gb") or 0),
                int(order.get("days") or pay.get("_days") or 0),
                int(order.get("server_id") or pay.get("_server_id") or 0),
            )
        text += (
            f"\n<code>\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501</code>\n"
            f"\U0001f4e6 \u067e\u0644\u0646: {order.get('plan_title', '?')}\n"
            f"\U0001f4ca \u062d\u062c\u0645: {order.get('volume_gb', 0)} \u06af\u06cc\u06af\n"
            f"\u23f0 \u0631\u0648\u0632: {order.get('days', 0)}\n"
            f"\U0001f4cd \u0645\u06a9\u0627\u0646: {order.get('server_location', '?')}\n"
        )

        text += (
            f"🏷 هزینه عمده: {_fmt_toman(wholesale)} تومان\n"
            f"💼 موجودی نماینده: {_fmt_toman(agent_db.get_wallet_balance(agent_id))} تومان\n"
        )

    raw_receipt = pay.get("receipt_image", "")
    receipt_fid = raw_receipt
    try:
        meta = json.loads(raw_receipt)
        if isinstance(meta, dict) and meta.get("file_id"):
            receipt_fid = meta["file_id"]
    except (json.JSONDecodeError, TypeError):
        pass
    if not receipt_fid:
        text += "\n\n\u28fe \u0647\u06cc\u0686 \u0631\u0633\u06cc\u062f\u06cc \u0628\u0631\u0627\u06cc \u0627\u06cc\u0646 \u067e\u0631\u062f\u0627\u062e\u062a \u0648\u062c\u0648\u062f \u0646\u062f\u0627\u0631\u062f."

    kb_rows = [
        [
            IButton("✅ تایید پرداخت", callback_data=f"agbot:custpay:approve:{pay_id}"),
            IButton("❌ رد پرداخت", callback_data=f"agbot:custpay:reject:{pay_id}"),
        ],
        [IButton(BTN_BACK, callback_data="agbot:custpay:list:1")],
    ]

    try:
        if receipt_fid:
            try:
                await query.message.delete()
            except Exception:
                pass
            msg = await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=receipt_fid,
                caption=text,
                reply_markup=_ikb(kb_rows),
                parse_mode="HTML",
            )
            context.user_data["last_pay_msg_id"] = msg.message_id
        else:
            await query.edit_message_text(text, reply_markup=_ikb(kb_rows), parse_mode="HTML")
    except Exception:
        try:
            await query.edit_message_text(text, reply_markup=_ikb(kb_rows), parse_mode="HTML")
        except Exception:
            pass


async def _approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int) -> None:
    query = update.callback_query
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
        wholesale_price = int(pay.get("_wholesale_price") or order.get("wholesale_price") or 0)
        if wholesale_price <= 0:
            wholesale_price = agent_db.calculate_wholesale_price(
                agent_id,
                float(order.get("volume_gb") or pay.get("_gb") or 0),
                int(order.get("days") or pay.get("_days") or 0),
                int(order.get("server_id") or 0),
            )
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
        deducted, wallet = agent_db.deduct_wallet(
            agent_id,
            wholesale_price,
            description=f"کسر عمده سفارش مشتری #{order.get('order_id') or pay.get('tx_code')}",
        )
        if not deducted:
            update_customer_payment_status(agent_id, pay_id, "pending")
            await query.answer("موجودی کیف پول کافی نیست. لطفاً کیف پول خود را شارژ کنید.", show_alert=True)
            return
        try:
            svc = await _create_subscription_from_order(update, context, agent_id, user_tg_id, order, wholesale_price)
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
            update_order_status(agent_id, int(order.get("order_id")), "approved")
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
    done_kb = _ikb([
        [IButton(f"👤 {customer_name} | {user_tg_id}", callback_data=f"agbot:custpay:detail:{pay_id}")],
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
    except Exception:
        try:
            await query.message.reply_text(done_text, parse_mode="HTML", reply_markup=done_kb)
        except Exception:
            pass


async def _reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int) -> None:
    query = update.callback_query
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
    try:
        await query.answer("❌ پرداخت رد شد.", show_alert=True)
    except Exception:
        pass
    try:
        if query.message and query.message.photo:
            await query.edit_message_caption(caption=done_text, parse_mode="HTML", reply_markup=None)
        else:
            await query.edit_message_text(done_text, parse_mode="HTML", reply_markup=None)
    except Exception:
        try:
            await query.message.reply_text(done_text, parse_mode="HTML")
        except Exception:
            pass


async def _create_subscription_from_order(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, user_tg_id: int, order: dict, wholesale_price: int = 0) -> dict:
    from Shared.agent_db import upsert_customer, create_service, add_service_node
    from Shared.database import get_server_by_id, get_plan
    from AgentBot.services.subscription_service import create_subscription

    # Get or create customer
    cust = get_customer_user(agent_id, user_tg_id)
    if not cust:
        raise RuntimeError("customer_not_found")

    # Get customer from shared DB
    from Shared.agent_db import get_customer_by_telegram_id, upsert_customer
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

    # Create user on Hiddify panel
    import uuid
    from Shared.hiddify_api import create_user
    new_uuid = str(uuid.uuid4())
    payload = {
        "name": f"{plan_title or 'buy'} - {user_tg_id}",
        "usage_limit_GB": volume_gb,
        "package_days": days,
        "uuid": new_uuid,
        "is_active": True,
    }
    try:
        panel_user = await create_user(server, payload)
        panel_uuid = str(panel_user.get("uuid") or new_uuid).strip()
    except Exception as e:
        logger.error(f"create_user failed for payment order: {e}")
        raise

    # Create service record
    svc = create_service(
        agent_id=agent_id,
        customer_id=shared_cust_id,
        server_id=server_id,
        server_title=server.get("title", ""),
        name=plan_title or f"\u062e\u0631\u06cc\u062f \u0627\u0634\u062a\u0631\u0627\u06a9",
        panel_user_uuid=panel_uuid,
        usage_limit=volume_gb,
        days=days,
        wholesale_price=wholesale_price,
        sale_price=price,
    )
    if svc:
        add_service_node(
            service_id=svc["id"],
            server_id=server_id,
            server_title=server.get("title", ""),
            panel_user_uuid=panel_uuid,
            panel_user_id=str(panel_user.get("id", "")),
        )

    # Notify customer
    await _notify_customer(context, agent_id, user_tg_id,
        f"\u2705 \u067e\u0631\u062f\u0627\u062e\u062a \u0634\u0645\u0627 \u062a\u0627\u06cc\u06cc\u062f \u0634\u062f!\n\n"
        f"\U0001f4e6 {plan_title}\n"
        f"\U0001f4ca {volume_gb:g} \u06af\u06cc\u06af\n"
        f"\u23f0 {days} \u0631\u0648\u0632\n\n"
        f"\U0001f4fa \u0648\u0636\u0639\u06cc\u062a \u0627\u0634\u062a\u0631\u0627\u06a9 \u062e\u0648\u062f \u0631\u0627 \u0627\u0632 \u0628\u062e\u0634 \u0648\u0636\u0639\u06cc\u062a \u0627\u0634\u062a\u0631\u0627\u06a9 \u0628\u0628\u06cc\u0646\u06cc\u062f."
    )
    pay_stub = {"id": order.get("payment_id") or 0, "user_id": user_tg_id, "receipt_image": order.get("receipt_image") or ""}
    await _delete_pending_customer_message(context, agent_id, pay_stub)
    return svc or {}


async def _notify_customer(context: ContextTypes.DEFAULT_TYPE, agent_id: int, user_tg_id: int, text: str) -> None:
    try:
        bot_row = get_active_customer_bot(agent_id)
        customer_token = str((bot_row or {}).get("bot_token") or "").strip()
        if not customer_token:
            logger.warning("Active customer bot token missing for agent=%s; cannot notify user %s", agent_id, user_tg_id)
            return
        customer_bot = Bot(token=customer_token)
        for attempt in range(1, 4):
            try:
                await customer_bot.send_message(
                    chat_id=user_tg_id,
                    text=text,
                    parse_mode="HTML",
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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return False
