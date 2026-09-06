import json
import logging
import asyncio
import re
import uuid

from Shared import i18n as _i18n


def _t(lang: str, key: str, **kw) -> str:
    """ترجمه کلید i18n برای متن‌های رو به مشتری."""
    return _i18n.t(key, lang, **kw)
from typing import Optional

from telegram import Bot, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes

from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import (
    _ikb, IButton, BTN_BACK, back_keyboard,
)
from AgentBot.utils.helpers import _fmt_toman
from AgentBot.database import (
    claim_customer_payment_processing,
    finish_customer_payment_processing,
    get_customer_pending_card_payments,
    get_customer_payment_by_id_enriched,
    get_customer_user,
    get_stale_customer_processing_payments,
    mark_customer_payment_manual_review,
    release_customer_payment_processing,
    set_customer_payment_processing_stage,
    update_customer_payment_status,
)
from CustomerBot.database import update_order_status
from Shared import agent_db
from Shared.agent_db import get_active_customer_bot
from Shared import i18n

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
    return agent_db.find_service_by_order_reference(agent_id, order_id) or {}


def _change_status_options_keyboard(pay_id: int, current_status: str, lang: str = "fa"):
    _lg = lang
    status = (current_status or "").strip().lower()
    rows = []
    if status != "approved":
        rows.append([IButton(i18n.t('✅ تایید شده', _lg), callback_data=f"agbot:custpay:set:{pay_id}:approved")])
    if status != "rejected":
        rows.append([IButton(i18n.t('❌ رد شده', _lg), callback_data=f"agbot:custpay:set:{pay_id}:rejected")])
    if status != "pending":
        rows.append([IButton(i18n.t('⏳ در حال انتظار', _lg), callback_data=f"agbot:custpay:set:{pay_id}:pending")])
    rows.append([IButton(i18n.t("back", _lg), callback_data=f"agbot:custpay:chg:back:{pay_id}")])
    return _ikb(rows)


async def _show_change_options(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    pay = get_customer_payment_by_id_enriched(agent_id, pay_id)
    if not pay:
        await query.answer(i18n.t('❌ تراکنش یافت نشد.', _lg), show_alert=True)
        return
    current_status = str(pay.get("status") or "pending").strip().lower()
    if current_status == "approved":
        await query.answer(i18n.t('🔒 تراکنش‌های تاییدشده قابل تغییر وضعیت نیستند.', _lg), show_alert=True)
        return
    if current_status == "processing":
        await query.answer(i18n.t('⏳ این تراکنش در حال پردازش است؛ تا پایان عملیات قابل تغییر نیست.', _lg), show_alert=True)
        return
    from AgentBot.handlers.settings_transactions import _build_payment_detail_text
    text = _build_payment_detail_text(pay) + i18n.t('\n\nوضعیت جدید تراکنش را انتخاب کنید:', _lg)
    kb = _change_status_options_keyboard(pay_id, current_status)
    try:
        if query.message and getattr(query.message, "photo", None):
            await query.edit_message_caption(caption=text, reply_markup=kb, parse_mode="HTML")
            return
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.warning("Failed to edit change-status options for payment %s: %s", pay_id, e)


async def _redetail_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    pay = get_customer_payment_by_id_enriched(agent_id, pay_id)
    if not pay:
        await query.answer(i18n.t('❌ تراکنش یافت نشد.', _lg), show_alert=True)
        return
    from AgentBot.handlers.settings_transactions import _send_payment_detail
    await _send_payment_detail(context, agent_id, query.message.chat_id, pay, source_message=query.message)


async def _apply_change_status(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int, new_status: str) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    new_status = (new_status or "").strip().lower()
    if new_status not in {"pending", "approved", "rejected"}:
        await query.answer(i18n.t('❌ وضعیت مقصد نامعتبر است.', _lg), show_alert=True)
        return
    pay = get_customer_payment_by_id_enriched(agent_id, pay_id)
    if not pay:
        await query.answer(i18n.t('❌ تراکنش یافت نشد.', _lg), show_alert=True)
        return
    old_status = str(pay.get("status") or "").strip().lower()
    if old_status == "approved":
        await query.answer(i18n.t('🔒 تراکنش‌های تاییدشده قابل تغییر وضعیت نیستند.', _lg), show_alert=True)
        return
    if old_status == "processing":
        await query.answer(i18n.t('⏳ این تراکنش در حال پردازش است؛ تا پایان عملیات قابل تغییر نیست.', _lg), show_alert=True)
        return
    if old_status == new_status:
        await query.answer(i18n.t('وضعیت تراکنش تغییری نکرد.', _lg), show_alert=True)
        return

    if new_status == "approved":
        order = pay.get("_order") or {}
        order_id = int(pay.get("_order_id") or order.get("order_id") or 0)
        if order_id and _find_service_by_order(agent_id, order_id):
            await query.answer(i18n.t('⚠️ برای این سفارش سرویسی از قبل وجود دارد؛ امکان تایید مجدد نیست.', _lg), show_alert=True)
            return
        await _approve_payment(update, context, agent_id, pay_id, pay=pay)
        return

    if not update_customer_payment_status(
        agent_id, pay_id, new_status, expected_status=old_status
    ):
        await query.answer(i18n.t('❌ تغییر وضعیت انجام نشد.', _lg), show_alert=True)
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
            f"{i18n.t('❌ پرداخت شما به مبلغ ', _lg)}{_fmt_toman(amount)}{i18n.t(' تومان رد شد.\nلطفا برای اطلاعات بیشتر با پشتیبانی تماس بگیرید.', _lg)}"
        )
        await _notify_customer(context, agent_id, user_tg_id, notify_text)
        await _delete_pending_customer_message(context, agent_id, pay)
    else:
        notify_text = (
            f"{i18n.t('⏳ وضعیت پرداخت شما به مبلغ ', _lg)}{_fmt_toman(amount)}{i18n.t(' تومان به حالت «در حال انتظار» تغییر کرد.\nپس از بررسی، نتیجه به اطلاع شما می‌رسد.', _lg)}"
        )
        await _notify_customer(context, agent_id, user_tg_id, notify_text)

    try:
        await query.answer(i18n.t('✅ وضعیت تراکنش تغییر کرد.', _lg), show_alert=True)
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
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
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
            await query.answer(i18n.t('❌ داده نامعتبر.', _lg), show_alert=True)
            return

        if sub == "back":
            from AgentBot.handlers.main_menu import handle_start
            await handle_start(update, context)
            return

    return False


async def _show_payments_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    text = (
            i18n.t('💳 <b>مدیریت پرداخت‌های مشتریان</b>\n\nدر این بخش می‌توانید رسیدهای خرید مشتریان را بررسی، تایید یا رد کنید.', _lg)
    )
    kb = _ikb([
        [IButton(i18n.t('📋 لیست پرداخت‌های در انتظار', _lg), callback_data="agbot:custpay:list:1")],
        [IButton(i18n.t("back", _lg), callback_data="agbot:set:back")],
    ])
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


async def _list_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, page: int = 1) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    payments = get_customer_pending_card_payments(agent_id)
    total = len(payments)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_pays = payments[start:end]

    text = f"{i18n.t('📋 <b>پرداخت‌های در انتظار</b> (تعداد: ', _lg)}{total})\n\n"
    if not page_pays:
        text += i18n.t('هیچ پرداخت منتظری وجود ندارد.', _lg)
    else:
        for p in page_pays:
            _order_row = p.get("_order") or {}
            ptype = i18n.t('♻️ تمدید اشتراک', _lg) if int(_order_row.get("renew_service_id") or 0) else i18n.t('📦 خرید اشتراک', _lg)
            name = p.get("full_name") or p.get("username") or f"{i18n.t('کاربر #', _lg)}{p.get('user_id', '?')}"
            amount = p.get("amount", 0)
            tx_code = p.get("tx_code", "")
            created = (p.get("created_at") or "")[:16]
            text += f"{ptype} | <b>{name}</b> | {_fmt_toman(amount)}{i18n.t(' تومان | ', _lg)}{created}\n"
            text += f"{i18n.t('کد: ', _lg)}{tx_code} | "
            text += f"{i18n.t('[📺 مشاهده جزئیات](agbot:custpay:detail:', _lg)}{p['id']})\n\n"

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
    kb_rows.append([IButton(i18n.t("back", _lg), callback_data="agbot:custpay:menu")])

    try:
        await query.edit_message_text(text, reply_markup=_ikb(kb_rows), parse_mode="HTML")
    except Exception:
        pass


async def _show_payment_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    payments = get_customer_pending_card_payments(agent_id)
    pay = next((p for p in payments if p["id"] == pay_id), None)
    if not pay:
        try:
            await query.edit_message_text(i18n.t('❌ پرداخت یافت نشد.', _lg), reply_markup=back_keyboard("agbot:custpay:list:1"))
        except Exception:
            pass
        return

    name = pay.get("full_name") or pay.get("username") or f"{i18n.t('کاربر #', _lg)}{pay.get('user_id', '?')}"
    amount = pay.get("amount", 0)
    tx_code = pay.get("tx_code", "")
    created = pay.get("created_at", "") or ""
    order = pay.get("_order")
    ptype = i18n.t('♻️ تمدید اشتراک', _lg) if (order and int(order.get("renew_service_id") or 0)) else i18n.t('📦 خرید اشتراک', _lg)

    text = (
        f"{i18n.t('<b>جزئیات درخواست مشتری</b>\n\n👤 مشتری: ', _lg)}{name}\n{ptype}{i18n.t('\n💰 مبلغ: ', _lg)}{_fmt_toman(amount)}{i18n.t(' تومان\n🔢 کد پیگیری: ', _lg)}{tx_code}{i18n.t('\n📅 تاریخ: ', _lg)}{created}\n"
    )
    if order:
        wholesale = _calc_wholesale_price(agent_id, pay, order)
        text += (
            f"{i18n.t('\n<code>━━━━━━━━━━━━━</code>\n📦 پلن: ', _lg)}{order.get('plan_title', '?')}{i18n.t('\n📊 حجم: ', _lg)}{order.get('volume_gb', 0)}{i18n.t(' گیگ\n⏰ روز: ', _lg)}{order.get('days', 0)}{i18n.t('\n📍 مکان: ', _lg)}{order.get('server_location', '?')}\n"
        )

        text += (
            f"{i18n.t('🏷 هزینه عمده: ', _lg)}{_fmt_toman(wholesale)}{i18n.t(' تومان\n💼 موجودی نماینده: ', _lg)}{_fmt_toman(agent_db.get_wallet_balance(agent_id))}{i18n.t(' تومان\n', _lg)}"
        )

    raw_receipt = pay.get("receipt_image", "")
    from AgentBot.handlers.settings_transactions import _parse_receipt_meta
    meta = _parse_receipt_meta(str(raw_receipt or ""))
    receipt_fid = str(meta.get("file_id") or "").strip()
    if not receipt_fid and raw_receipt and ":" not in str(raw_receipt) and "|" not in str(raw_receipt):
        receipt_fid = str(raw_receipt).strip()
    card_last4 = str(meta.get("card_last4") or "").strip()
    if card_last4:
        text += f"{i18n.t('\n💳 ۴ رقم آخر کارت: <code>', _lg)}{card_last4}</code>\n"
    if not receipt_fid:
        text += i18n.t('\n\n⣾ هیچ رسیدی برای این پرداخت وجود ندارد.', _lg)

    kb_rows = [
        [
            IButton(i18n.t('❌ رد پرداخت', _lg), callback_data=f"agbot:custpay:reject:{pay_id}"),
            IButton(i18n.t('✅ تایید پرداخت', _lg), callback_data=f"agbot:custpay:approve:{pay_id}"),
        ],
        [IButton(f"👤 {name}", callback_data=f"agbot:custpay:profile:{pay.get('user_id', 0)}")],
        [IButton(i18n.t("back", _lg), callback_data="agbot:custpay:list:1")],
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


def _fulfillment_result(
    outcome: str,
    note: str,
    *,
    pay: Optional[dict] = None,
    service: Optional[dict] = None,
    is_renew: bool = False,
) -> dict:
    return {
        "outcome": str(outcome or "manual_review"),
        "note": str(note or "")[:1000],
        "pay": pay or {},
        "service": service or {},
        "is_renew": bool(is_renew),
    }


def _mark_fulfillment_order_approved(agent_id: int, order_id: int) -> tuple[bool, str]:
    """Persist the order side of a completed fulfillment without hiding errors."""
    oid = int(order_id or 0)
    if oid <= 0:
        return True, ""
    try:
        if update_order_status(int(agent_id or 0), oid, "approved"):
            return True, ""
        return False, f"customer_order_{oid}_not_found"
    except Exception as exc:
        return False, f"order_status_update_failed:{type(exc).__name__}:{exc}"[:1000]


async def _fulfill_customer_payment(
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    pay_id: int,
    *,
    source: str,
    expected_status: Optional[str] = None,
    allow_resume: bool = False,
) -> dict:
    """Financial core shared by manual approval, SMS and crash recovery.

    The payment claim lives in customer_bot.db while the wallet reservation and
    fulfillment ledger live together in agency.db.  Every operation after that
    boundary is replay-safe: the debit has a unique reference, create has a
    stable UUID/source payment, and renew persists absolute target values.
    """
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    aid = int(agent_id or 0)
    pid = int(pay_id or 0)
    pay = get_customer_payment_by_id_enriched(aid, pid)
    if not pay:
        return _fulfillment_result("manual_review", "payment_not_found")

    current_status = str(pay.get("status") or "").strip().lower()
    if current_status == "approved":
        fulfillment = agent_db.get_payment_fulfillment(aid, pid)
        service = None
        if fulfillment and int(fulfillment.get("service_id") or 0):
            service = agent_db.get_service_by_id(int(fulfillment["service_id"]))
        if not service:
            service = agent_db.find_service_by_source_payment(aid, pid)
        fulfillment_state = str((fulfillment or {}).get("state") or "").strip().lower()
        if fulfillment and fulfillment_state != "completed":
            service_is_live = bool(service) and not str(
                (service or {}).get("deleted_at") or ""
            ).strip()
            if fulfillment_state == "local_recorded" and service_is_live:
                # Valid crash window: payment committed immediately before the
                # final ledger state update.
                agent_db.update_payment_fulfillment(
                    aid,
                    pid,
                    state="completed",
                    service_id=int((service or {}).get("id") or 0),
                    last_error="",
                )
            else:
                # An approved payment cannot prove that provisioning completed.
                # Preserve the inconsistency for an operator instead of turning
                # incomplete panel/wallet work into a false completed ledger.
                agent_db.update_payment_fulfillment(
                    aid,
                    pid,
                    state="manual_review",
                    last_error=f"approved payment with fulfillment state {fulfillment_state or 'unknown'}",
                )
        approved_order = dict(pay.get("_order") or {})
        approved_order_id = int(
            approved_order.get("order_id") or pay.get("_order_id") or 0
        )
        order_ok, order_note = _mark_fulfillment_order_approved(aid, approved_order_id)
        if not order_ok:
            logger.warning(
                "Could not reconcile approved customer order agent=%s pay=%s: %s",
                aid,
                pid,
                order_note,
            )
        return _fulfillment_result(
            "obsolete", "payment_already_approved", pay=pay, service=service
        )

    order = dict(pay.get("_order") or {})
    order_id_hint = int(pay.get("_order_id") or 0)
    if pay.get("_pay_type") != "buy" or not order:
        note = (
            f"customer order {order_id_hint} is missing or unreadable"
            if order_id_hint > 0
            else "customer payment has no traceable order"
        )
        if current_status == "processing":
            mark_customer_payment_manual_review(
                aid, pid, processing_token=str(pay.get("processing_token") or ""),
                note=note,
            )
        return _fulfillment_result(
            "manual_review",
            "order_not_found" if order_id_hint > 0 else "payment_without_order",
            pay=pay,
        )

    if not int(order.get("server_id") or 0):
        order["server_id"] = int(pay.get("_server_id") or 0)
    order_id = int(order.get("order_id") or pay.get("_order_id") or 0)
    renew_service_id = int(order.get("renew_service_id") or 0)
    is_renew = renew_service_id > 0
    kind = "renew" if is_renew else "create"
    wholesale_price = _calc_wholesale_price(aid, pay, order)
    if wholesale_price <= 0:
        return _fulfillment_result(
            "manual_review", "wholesale_price_not_configured", pay=pay, is_renew=is_renew
        )

    # A legacy service/order with no durable source is ambiguous.  Failing
    # closed is safer than either charging again or granting a free duplicate.
    fulfillment = agent_db.get_payment_fulfillment(aid, pid)
    if str((fulfillment or {}).get("state") or "").strip().lower() == "manual_review":
        if current_status == "processing":
            mark_customer_payment_manual_review(
                aid,
                pid,
                processing_token=str(pay.get("processing_token") or ""),
                note=str(
                    (fulfillment or {}).get("last_error")
                    or "fulfillment requires manual review"
                ),
            )
        return _fulfillment_result(
            "manual_review",
            str((fulfillment or {}).get("last_error") or "fulfillment_requires_manual_review"),
            pay=pay,
            is_renew=is_renew,
        )
    source_service = agent_db.find_service_by_source_payment(aid, pid) if not is_renew else None
    if not fulfillment:
        if is_renew and str(order.get("status") or "").strip().lower() == "approved":
            return _fulfillment_result(
                "manual_review", "legacy_renewal_already_marked_approved", pay=pay, is_renew=True
            )
        if not is_renew and not source_service and order_id:
            try:
                legacy_service = _find_service_by_order(aid, order_id)
            except Exception as exc:
                return _fulfillment_result(
                    "retry", f"service_idempotency_check_failed:{exc}", pay=pay
                )
            if legacy_service:
                return _fulfillment_result(
                    "manual_review", "legacy_service_exists_without_payment_source",
                    pay=pay, service=legacy_service,
                )

    stable_uuid = ""
    if not is_renew:
        stable_uuid = str((fulfillment or {}).get("panel_uuid") or "").strip()
        if not stable_uuid:
            stable_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"hiddify-sellbot:{aid}:{pid}"))
    try:
        fulfillment = agent_db.prepare_payment_fulfillment(
            aid,
            pid,
            order_id=order_id,
            kind=kind,
            wholesale_price=wholesale_price,
            panel_uuid=stable_uuid,
        )
    except Exception as exc:
        logger.exception("payment fulfillment preparation failed agent=%s pay=%s", aid, pid)
        return _fulfillment_result(
            "manual_review", f"fulfillment_conflict:{exc}", pay=pay, is_renew=is_renew
        )

    token = ""
    if current_status == "processing":
        token = str(pay.get("processing_token") or "").strip()
        if source != "recovery":
            try:
                from datetime import datetime, timedelta, timezone

                started = datetime.strptime(
                    str(pay.get("processing_started_at") or ""), "%Y-%m-%d %H:%M:%S"
                )
                if started > datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10):
                    return _fulfillment_result(
                        "retry", "payment_is_already_being_processed", pay=pay,
                        service=source_service, is_renew=is_renew,
                    )
            except (TypeError, ValueError):
                pass
        if (
            not allow_resume
            or not token
            or str(pay.get("processing_stage") or "").strip().lower() == "manual_review"
        ):
            return _fulfillment_result(
                "manual_review", "payment_processing_requires_review", pay=pay,
                service=source_service, is_renew=is_renew,
            )
    else:
        wanted = str(expected_status or "pending").strip().lower()
        if wanted not in {"pending", "rejected"} or current_status != wanted:
            return _fulfillment_result(
                "obsolete", f"payment_status_{current_status}", pay=pay, is_renew=is_renew
            )
        token = claim_customer_payment_processing(
            aid, pid, expected_status=wanted, source=source
        )
        if not token:
            fresh = get_customer_payment_by_id_enriched(aid, pid) or pay
            fresh_status = str(fresh.get("status") or "").strip().lower()
            outcome = "obsolete" if fresh_status == "approved" else "retry"
            return _fulfillment_result(
                outcome, f"payment_claim_failed:{fresh_status}", pay=fresh, is_renew=is_renew
            )
        pay = get_customer_payment_by_id_enriched(aid, pid) or pay

    # Crash after local persistence: finish the financial state without calling
    # the panel or debiting a second time.
    fulfillment = agent_db.get_payment_fulfillment(aid, pid) or fulfillment
    if not is_renew:
        source_service = agent_db.find_service_by_source_payment(aid, pid)
    service_id = int((fulfillment or {}).get("service_id") or 0)
    completed_service = agent_db.get_service_by_id(service_id) if service_id else source_service
    if completed_service and str(completed_service.get("deleted_at") or "").strip():
        mark_customer_payment_manual_review(
            aid, pid, processing_token=token, note="source service was deleted"
        )
        return _fulfillment_result(
            "manual_review", "source_service_deleted", pay=pay,
            service=completed_service, is_renew=is_renew,
        )
    fulfillment_state = str(fulfillment.get("state") or "").strip().lower()
    if not completed_service and fulfillment_state in {"local_recorded", "completed"}:
        mark_customer_payment_manual_review(
            aid, pid, processing_token=token,
            note=f"fulfillment references missing service {service_id}",
        )
        return _fulfillment_result(
            "manual_review", "recorded_service_missing", pay=pay, is_renew=is_renew,
        )
    if completed_service and fulfillment_state in {"local_recorded", "completed"}:
        try:
            agent_db.reserve_wallet_for_payment_once(
                aid,
                pid,
                int(fulfillment.get("wholesale_price") or wholesale_price),
                description="",
                service_id=int(completed_service["id"]),
            )
        except Exception as exc:
            mark_customer_payment_manual_review(
                aid, pid, processing_token=token,
                note=f"could not reconcile wallet transaction service: {exc}",
            )
            return _fulfillment_result(
                "manual_review", f"wallet_service_reconcile_failed:{exc}", pay=pay,
                service=completed_service, is_renew=is_renew,
            )
        order_ok, order_note = _mark_fulfillment_order_approved(aid, order_id)
        if not order_ok:
            set_customer_payment_processing_stage(
                aid, pid, token, "order_status_retry", order_note
            )
            return _fulfillment_result(
                "retry", order_note, pay=pay, service=completed_service,
                is_renew=is_renew,
            )
        finalized_here = finish_customer_payment_processing(
            aid, pid, token, "recovered local fulfillment"
        )
        if not finalized_here:
            fresh = get_customer_payment_by_id_enriched(aid, pid) or pay
            if str(fresh.get("status") or "").lower() != "approved":
                mark_customer_payment_manual_review(
                    aid, pid, processing_token=token, note="could not finalize recovered payment"
                )
                return _fulfillment_result(
                    "manual_review", "payment_finalize_failed", pay=fresh,
                    service=completed_service, is_renew=is_renew,
                )
        agent_db.update_payment_fulfillment(
            aid, pid, state="completed", service_id=int(completed_service["id"]), last_error=""
        )
        if not finalized_here:
            return _fulfillment_result(
                "obsolete", "payment_finalized_by_concurrent_worker", pay=fresh,
                service=completed_service, is_renew=is_renew,
            )
        return _fulfillment_result(
            "success", "recovered_completed_fulfillment",
            pay=get_customer_payment_by_id_enriched(aid, pid) or pay,
            service=completed_service, is_renew=is_renew,
        )

    set_customer_payment_processing_stage(aid, pid, token, "reserving_wallet")
    try:
        wallet_ok, wallet, _debited_now = agent_db.reserve_wallet_for_payment_once(
            aid,
            pid,
            wholesale_price,
            description=(
                f"{i18n.t('کسر عمده سفارش مشتری #', _lg)}{order_id or pay.get('tx_code')}"
                + (i18n.t(' (تایید خودکار SMS)', _lg) if source == "sms" else "")
            ),
        )
    except Exception as exc:
        mark_customer_payment_manual_review(
            aid, pid, processing_token=token, note=f"wallet ledger conflict: {exc}"
        )
        return _fulfillment_result(
            "manual_review", f"wallet_ledger_conflict:{exc}", pay=pay, is_renew=is_renew
        )
    if not wallet_ok:
        balance = int((wallet or {}).get("balance") or 0)
        agent_db.update_payment_fulfillment(
            aid, pid, state="waiting_wallet", last_error="insufficient_wallet"
        )
        release_customer_payment_processing(
            aid, pid, token, note=f"insufficient wallet ({balance} < {wholesale_price})"
        )
        return _fulfillment_result(
            "waiting_wallet", f"insufficient_wallet:{balance}:{wholesale_price}",
            pay=pay, is_renew=is_renew,
        )

    set_customer_payment_processing_stage(aid, pid, token, "applying_panel")
    agent_db.update_payment_fulfillment(
        aid, pid, increment_attempt=True, last_error=""
    )
    try:
        if is_renew:
            service = await _renew_subscription_from_order(
                context, aid, int(pay.get("user_id") or 0), order,
                tx_code=str(pay.get("tx_code") or ""), payment_id=pid,
            )
        else:
            service = await _create_subscription_from_order(
                context, aid, int(pay.get("user_id") or 0), order,
                wholesale_price, tx_code=str(pay.get("tx_code") or ""), payment_id=pid,
            )
        if not service or not int(service.get("id") or 0):
            raise RuntimeError("local_service_not_recorded")
    except Exception as exc:
        note = f"{type(exc).__name__}:{exc}"[:1000]
        agent_db.update_payment_fulfillment(aid, pid, last_error=note)
        release_customer_payment_processing(aid, pid, token, note=note)
        logger.exception("payment fulfillment failed agent=%s pay=%s", aid, pid)
        return _fulfillment_result("retry", note, pay=pay, is_renew=is_renew)

    service_id = int(service["id"])
    agent_db.update_payment_fulfillment(
        aid, pid, state="local_recorded", service_id=service_id, last_error=""
    )
    # Re-run the idempotent reservation only to attach the service id to the
    # existing transaction; no second debit can occur.
    agent_db.reserve_wallet_for_payment_once(
        aid, pid, wholesale_price, description="", service_id=service_id
    )
    set_customer_payment_processing_stage(aid, pid, token, "finalizing")
    order_ok, order_note = _mark_fulfillment_order_approved(aid, order_id)
    if not order_ok:
        set_customer_payment_processing_stage(
            aid, pid, token, "order_status_retry", order_note
        )
        return _fulfillment_result(
            "retry", order_note, pay=pay, service=service, is_renew=is_renew,
        )
    finalized_here = finish_customer_payment_processing(
        aid, pid, token, "fulfillment completed"
    )
    if not finalized_here:
        fresh = get_customer_payment_by_id_enriched(aid, pid) or pay
        if str(fresh.get("status") or "").strip().lower() != "approved":
            mark_customer_payment_manual_review(
                aid, pid, processing_token=token,
                note=f"service {service_id} exists but payment finalization failed",
            )
            return _fulfillment_result(
                "manual_review", "service_exists_payment_finalize_failed",
                pay=fresh, service=service, is_renew=is_renew,
            )
    agent_db.update_payment_fulfillment(
        aid, pid, state="completed", service_id=service_id, last_error=""
    )
    if not finalized_here:
        return _fulfillment_result(
            "obsolete", "payment_finalized_by_concurrent_worker", pay=fresh,
            service=service, is_renew=is_renew,
        )
    return _fulfillment_result(
        "success", "approved", pay=get_customer_payment_by_id_enriched(aid, pid) or pay,
        service=service, is_renew=is_renew,
    )


async def _approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int, pay: dict = None) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    requested_status = str((pay or {}).get("status") or "pending").strip().lower()
    if requested_status not in {"pending", "rejected"}:
        requested_status = "pending"
    result = await _fulfill_customer_payment(
        context,
        agent_id,
        pay_id,
        source="manual",
        expected_status=requested_status,
        allow_resume=False,
    )
    outcome = str(result.get("outcome") or "manual_review")
    note = str(result.get("note") or "")
    pay = result.get("pay") or get_customer_payment_by_id_enriched(agent_id, pay_id)
    if not pay:
        await query.answer(i18n.t('❌ پرداخت در لیست در انتظار پیدا نشد.', _lg), show_alert=True)
        return
    if outcome == "obsolete":
        await query.answer(i18n.t('⚠️ این پرداخت قبلاً تعیین‌تکلیف شده است.', _lg), show_alert=True)
        return
    if outcome == "waiting_wallet":
        await query.answer(
            i18n.t('موجودی کیف پول کافی نیست. پرداخت در انتظار مانده و هیچ کسر تکراری انجام نمی‌شود.', _lg),
            show_alert=True,
        )
        return
    if outcome == "retry":
        await query.answer(
            f"{i18n.t('عملیات کامل نشد و برای تلاش امن بعدی نگه داشته شد؛ کسر وجه تکرار نخواهد شد. جزئیات: ', _lg)}{note[:120]}",
            show_alert=True,
        )
        return
    if outcome != "success":
        await query.answer(
            f"{i18n.t('⚠️ وضعیت مالی مبهم است و برای جلوگیری از کسر یا ساخت تکراری قفل شد. لاگ و دفترکل را بررسی کنید. جزئیات: ', _lg)}{note[:120]}",
            show_alert=True,
        )
        return

    user_tg_id = int(pay.get("user_id") or 0)
    svc = result.get("service") or {}
    is_renew = bool(result.get("is_renew"))
    try:
        await _post_fulfillment_customer_delivery(context, agent_id, pay, svc, is_renew)
    except Exception as notify_err:
        logger.warning("Post-approval customer delivery failed for payment %s: %s", pay_id, notify_err)
    try:
        await _delete_pending_customer_message(context, agent_id, pay)
    except Exception as delete_err:
        logger.warning("Failed deleting pending customer message for payment %s: %s", pay_id, delete_err)
    if svc:
        try:
            from Shared.subscription_reports import send_subscription_report
            await send_subscription_report(
                context.bot,
                query.message.chat_id,
                agent_id,
                user_tg_id,
                svc,
                "renew" if is_renew else "create",
                int(pay.get("amount") or 0),
            )
        except Exception as report_err:
            logger.warning("Failed to send subscription report for payment %s: %s", pay_id, report_err)

    amount = int(pay.get("amount") or 0)
    tx_code = str(pay.get("tx_code") or "-")
    customer_name = pay.get("full_name") or pay.get("username") or f"Customer {user_tg_id}"
    done_text = (
        f"{i18n.t('🕊️💸 گزارش تایید پرداخت 💸🕊️\n\n📌 شیوه پرداخت: کارت به کارت\n🔑 شناسه تراکنش: ', _lg)}{tx_code}{i18n.t('\n💰 مبلغ پرداخت: ', _lg)}{amount:f','}{i18n.t(' تومان', _lg)}"
    )
    card_last4 = str(pay.get("_card_last4") or "").strip()
    if not card_last4:
        try:
            from AgentBot.handlers.settings_transactions import _parse_receipt_meta
            card_last4 = str(_parse_receipt_meta(str(pay.get("receipt_image") or "")).get("card_last4") or "").strip()
        except Exception:
            card_last4 = ""
    if card_last4:
        done_text += f"{i18n.t('\n💳 ۴ رقم آخر کارت: <code>', _lg)}{card_last4}</code>"
    if svc:
        done_text += i18n.t('\n\n✅ پرداخت تایید شد؛ ', _lg) + (i18n.t('اشتراک مشتری تمدید شد.', _lg) if is_renew else i18n.t('اشتراک مشتری ساخته شد.', _lg))
    else:
        done_text += i18n.t('\n\n✅ پرداخت تایید شد.', _lg)
    done_kb = _ikb([
        [IButton(f"👤 {customer_name}", callback_data=f"agbot:custpay:profile:{user_tg_id}")],
    ])
    try:
        await query.answer(i18n.t('✅ پرداخت با موفقیت تایید شد.', _lg), show_alert=True)
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


async def process_sms_webhook_queue(context: ContextTypes.DEFAULT_TYPE, limit: int = 5) -> int:
    """پردازش صف تایید خودکار وب‌هوک SMS بانکی برای پرداخت‌های مشتریان نمایندگی‌ها.

    وب‌هوک (پروسه UserBot) فقط پرداخت تطبیق‌یافته را صف می‌کند؛ ساخت سرویس، کسر
    کیف پول عمده و تحویل اشتراک باید در همین پروسه (با رویداد لوپ و توکن ربات
    مشتریِ هر نماینده) انجام شود — دقیقاً مثل تایید دستی ادمین.
    """
    from CustomerBot.database import (
        claim_sms_auto_queue,
        complete_sms_auto_queue,
        dead_letter_sms_auto_queue,
        retry_sms_auto_queue,
    )

    processed = 0
    # First resume operations interrupted by a process restart. This also
    # freezes legacy/ambiguous processing rows instead of assuming success.
    try:
        await recover_stale_customer_payment_fulfillments(context, limit=max(5, int(limit or 5)))
    except Exception as exc:
        logger.exception("stale customer payment recovery failed: %s", exc)
    try:
        rows = claim_sms_auto_queue(limit=limit, lease_seconds=900)
    except Exception as e:
        logger.warning("sms webhook queue claim failed: %s", e)
        return 0
    for row in rows or []:
        qid = int(row.get("id") or 0)
        agent_id = int(row.get("agent_id") or 0)
        pay_id = int(row.get("pay_id") or 0)
        lease_token = str(row.get("lease_token") or "")
        attempt = int(row.get("attempt_count") or 1)
        try:
            result = await _auto_approve_from_sms_webhook(context, agent_id, pay_id, row)
            outcome = str(result.get("outcome") or "manual_review")
            note = str(result.get("note") or outcome)
        except Exception as e:
            outcome, note = "retry", f"{type(e).__name__}: {e}"
            logger.exception("sms auto approve failed (agent=%s pay=%s)", agent_id, pay_id)
        stored = False
        try:
            if outcome in {"success", "obsolete"}:
                stored = complete_sms_auto_queue(
                    qid,
                    lease_token,
                    note=note,
                    state="succeeded" if outcome == "success" else "obsolete",
                )
            elif outcome == "waiting_wallet":
                stored = retry_sms_auto_queue(
                    qid, lease_token, note, 300, state="waiting_wallet"
                )
            elif outcome == "retry" and attempt < 8:
                backoff = (15, 30, 60, 120, 300, 900, 1800, 3600)
                stored = retry_sms_auto_queue(
                    qid, lease_token, note, backoff[min(attempt - 1, len(backoff) - 1)]
                )
            else:
                stored = dead_letter_sms_auto_queue(qid, lease_token, note)
                outcome = "manual_review"
                try:
                    agent_db.update_payment_fulfillment(
                        agent_id, pay_id, state="manual_review", last_error=note
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning("sms queue outcome write failed (id=%s): %s", qid, e)
        if not stored:
            logger.warning("sms queue lease was lost before outcome write (id=%s)", qid)
            processed += 1
            continue
        try:
            from Shared import userbot_db
            event_status = {
                "success": "approved",
                "obsolete": "approved_duplicate",
                "waiting_wallet": "agency_waiting_wallet",
                "retry": "approve_retry",
                "manual_review": "manual_review",
            }.get(outcome, "manual_review")
            userbot_db.update_sms_webhook_event(
                str(row.get("event_id") or ""),
                status=event_status,
                matched_payment_id=pay_id,
                message=note[:500],
                amount_toman=int(row.get("amount_toman") or 0),
            )
        except Exception as exc:
            logger.warning("sms queue event status update failed (id=%s): %s", qid, exc)
        processed += 1
    return processed


async def _auto_approve_from_sms_webhook(
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    pay_id: int,
    queue_row: dict = None,
) -> dict:
    """Run the same replay-safe financial core used by manual approval."""
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    queue_row = queue_row or {}
    result = await _fulfill_customer_payment(
        context,
        agent_id,
        pay_id,
        source="sms",
        expected_status="pending",
        allow_resume=True,
    )
    if result.get("outcome") != "success":
        return result
    pay = result.get("pay") or {}
    svc = result.get("service") or {}
    is_renew = bool(result.get("is_renew"))
    user_tg_id = int(pay.get("user_id") or 0)
    # Telegram is deliberately post-commit. Notification failure can never
    # refund/reopen a fulfilled payment.
    try:
        await _notify_customer(
            context,
            agent_id,
            user_tg_id,
            i18n.t('✅ پرداخت کارت‌به‌کارت شما با پیامک بانکی به‌صورت خودکار تایید شد.', _lg),
        )
        await _post_fulfillment_customer_delivery(context, agent_id, pay, svc, is_renew)
        await _delete_pending_customer_message(context, agent_id, pay)
    except Exception as e:
        logger.warning("sms auto post-approval delivery failed (pay=%s): %s", pay_id, e)
    try:
        await _delete_agent_pending_message(context, agent_id, pay)
    except Exception as e:
        logger.warning("sms auto: delete agent pending message failed (pay=%s): %s", pay_id, e)

    # گزارش تایید خودکار در چت نماینده: عکس رسید بالا + متن گزارش SMS + گزارش اشتراک
    try:
        await _send_sms_auto_approval_report(context, agent_id, pay_id, pay, svc, is_renew, queue_row)
    except Exception as e:
        logger.warning("sms auto approval report failed (agent=%s pay=%s): %s", agent_id, pay_id, e)

    logger.info("sms webhook auto-approved agency payment (agent=%s pay=%s)", agent_id, pay_id)
    return result


async def recover_stale_customer_payment_fulfillments(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    age_minutes: int = 10,
    limit: int = 10,
) -> int:
    """Resume new durable claims and freeze untraceable legacy claims."""
    recovered = 0
    rows = get_stale_customer_processing_payments(age_minutes=age_minutes, limit=limit)
    for pay in rows:
        aid = int(pay.get("agent_id") or 0)
        pid = int(pay.get("id") or 0)
        token = str(pay.get("processing_token") or "").strip()
        fulfillment = agent_db.get_payment_fulfillment(aid, pid)
        if not token or not fulfillment:
            mark_customer_payment_manual_review(
                aid,
                pid,
                processing_token=token,
                note="legacy processing row has no durable token/ledger",
            )
            logger.error(
                "stale payment frozen for manual review (agent=%s pay=%s)", aid, pid
            )
            continue
        result = await _fulfill_customer_payment(
            context,
            aid,
            pid,
            source="recovery",
            allow_resume=True,
        )
        if result.get("outcome") == "success":
            recovered += 1
            try:
                await _post_fulfillment_customer_delivery(
                    context,
                    aid,
                    result.get("pay") or pay,
                    result.get("service") or {},
                    bool(result.get("is_renew")),
                )
            except Exception as exc:
                logger.warning("recovered payment delivery failed pay=%s: %s", pid, exc)
    return recovered


async def _delete_agent_pending_message(context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay: dict) -> None:
    """پیام «رسید پرداخت مشتری» با دکمه‌های تایید/رد را از چت نماینده پاک می‌کند."""
    try:
        raw_receipt = str(pay.get("receipt_image") or "")
        match = re.search(r"agent_pending_message_id:(\d+)", raw_receipt)
        if not match:
            return
        agent = agent_db.get_agent_by_id(agent_id)
        agent_tg_id = int((agent or {}).get("telegram_id") or 0)
        if not agent_tg_id:
            return
        await context.bot.delete_message(chat_id=agent_tg_id, message_id=int(match.group(1)))
    except Exception as e:
        logger.warning("Failed to delete agent pending message for payment %s: %s", pay.get("id"), e)


async def _send_sms_auto_approval_report(
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    pay_id: int,
    pay: dict,
    svc: Optional[dict],
    is_renew: bool,
    queue_row: dict,
) -> None:
    """گزارش تایید خودکار SMS برای چت نماینده (عکس رسید بالا، متن زیر آن)."""
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    from Shared.agent_db import get_agent_by_id

    agent = get_agent_by_id(agent_id)
    agent_tg_id = int((agent or {}).get("telegram_id") or 0)
    if not agent_tg_id:
        return

    sender = "-"
    amount_raw = 0
    currency_raw = "-"
    reference = "-"
    try:
        from Shared import userbot_db
        event = userbot_db.get_sms_webhook_event(str(queue_row.get("event_id") or ""))
        if event:
            sender = str(event.get("sender") or "-")
            amount_raw = int(event.get("amount_raw") or 0)
            currency_raw = str(event.get("currency_raw") or "-") or "-"
            reference = str(event.get("reference") or "-") or "-"
    except Exception as e:
        logger.warning("sms auto report: fetch event failed: %s", e)

    amount = int(pay.get("amount") or 0)
    tx_code = str(pay.get("tx_code") or "-")
    user_tg_id = int(pay.get("user_id") or 0)
    customer_display = str(pay.get("full_name") or pay.get("username") or user_tg_id)

    # receipt_image = JSON متادیتا + markerهای جداشده با | — ابتدا JSON را جدا کن
    file_id = ""
    try:
        json_part = str(pay.get("receipt_image") or "").split("|", 1)[0].strip()
        if json_part:
            meta_obj = json.loads(json_part)
            if isinstance(meta_obj, dict):
                file_id = str(meta_obj.get("file_id") or "")
    except Exception:
        file_id = ""

        caption = (
            f"{i18n.t('✅ <b>پرداخت با SMS بانک تایید شد</b>\n\n🔖 نوع: ', _lg)}{i18n.t('paytype_renew', _lg) if is_renew else i18n.t('paytype_direct', _lg)}{i18n.t('\n👤 کاربر: ', _lg)}{customer_display}{i18n.t('\n💰 مبلغ: <b>', _lg)}{amount:f','}{i18n.t('</b> تومان\n🧾 کد تراکنش: <code>', _lg)}{tx_code}{i18n.t('</code>\n🆔 شناسه پرداخت: ', _lg)}{pay_id}{i18n.t('\n📨 سرشماره SMS: <code>', _lg)}{sender}{i18n.t('</code>\n🏦 مبلغ خام SMS: ', _lg)}{amount_raw} {currency_raw}{i18n.t('\n🔖 پیگیری SMS: ', _lg)}{reference}{i18n.t('\n🖼 رسید کاربر: ', _lg)}{i18n.t('attached_yes', _lg) if file_id else i18n.t('attached_no', _lg)}"
        )

    try:
        profile_kb = _ikb([[IButton(f"👤 {customer_display}", callback_data=f"agbot:custpay:profile:{user_tg_id}")]])
    except Exception:
        profile_kb = None

    # file_id رسید مالِ ربات مشتری است؛ باید فایل با ربات مشتری دانلود و با
    # ربات نماینده (context.bot) دوباره آپلود شود وگرنه ارسال عکس شکست می‌خورد
    sent = False
    if file_id:
        try:
            customer_token = str((get_active_customer_bot(agent_id) or {}).get("bot_token") or "").strip()
            if customer_token:
                from telegram import Bot as _CustomerBot
                import io as _io
                customer_bot = _CustomerBot(token=customer_token)
                tg_file = await customer_bot.get_file(file_id)
                bio = _io.BytesIO()
                await tg_file.download_to_memory(out=bio)
                bio.seek(0)
                bio.name = f"payment_{pay_id}.jpg"
                await context.bot.send_photo(
                    chat_id=agent_tg_id,
                    photo=bio,
                    caption=caption[:1024],
                    parse_mode="HTML",
                    reply_markup=profile_kb,
                )
                sent = True
        except Exception as e:
            logger.warning("sms auto report photo send failed (pay=%s): %s", pay_id, e)
    if not sent:
        await context.bot.send_message(chat_id=agent_tg_id, text=caption, parse_mode="HTML", reply_markup=profile_kb)

    if svc:
        try:
            from Shared.subscription_reports import send_subscription_report
            await send_subscription_report(
                context.bot,
                agent_tg_id,
                agent_id,
                user_tg_id,
                svc,
                "renew" if is_renew else "create",
                amount,
            )
        except Exception as e:
            logger.warning("sms auto report: subscription report failed (pay=%s): %s", pay_id, e)


async def _reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int, pay_id: int, pay: dict = None) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    if pay is None:
        payments = get_customer_pending_card_payments(agent_id)
        pay = next((p for p in payments if p["id"] == pay_id), None)
    if not pay:
        await query.answer(i18n.t('❌ پرداخت در لیست در انتظار پیدا نشد.', _lg), show_alert=True)
        return

    if not update_customer_payment_status(
        agent_id, pay_id, "rejected", expected_status="pending"
    ):
        await query.answer(i18n.t('❌ رد پرداخت انجام نشد.', _lg), show_alert=True)
        return
    order = pay.get("_order") or {}
    if int(order.get("order_id") or pay.get("_order_id") or 0):
        update_order_status(agent_id, int(order.get("order_id") or pay.get("_order_id") or 0), "rejected")

    user_tg_id = pay.get("user_id", 0)
    amount = pay.get("amount", 0)
    notify_text = (
        f"{i18n.t('❌ پرداخت شما به مبلغ ', _lg)}{_fmt_toman(amount)}{i18n.t(' تومان رد شد.\nلطفا برای اطلاعات بیشتر با پشتیبانی تماس بگیرید.', _lg)}"
    )

    await _notify_customer(context, agent_id, user_tg_id, notify_text)
    await _delete_pending_customer_message(context, agent_id, pay)

    done_text = i18n.t('❌ پرداخت رد شد.', _lg)
    customer_name = pay.get("full_name") or pay.get("username") or f"Customer {user_tg_id}"
    done_kb = _ikb([
        [IButton(i18n.t('✏️ تغییر وضعیت', _lg), callback_data=f"agbot:custpay:chg:{pay_id}")],
        [IButton(f"👤 {customer_name}", callback_data=f"agbot:custpay:profile:{user_tg_id}")],
    ])
    try:
        await query.answer(i18n.t('❌ پرداخت رد شد.', _lg), show_alert=True)
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
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    customer = agent_db.get_customer_by_telegram_id(agent_id, user_tg_id)
    if not customer:
        try:
            await query.answer(i18n.t('❌ اطلاعات مشتری یافت نشد.', _lg), show_alert=True)
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


async def _create_subscription_from_order(
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    user_tg_id: int,
    order: dict,
    wholesale_price: int = 0,
    tx_code: str = "",
    payment_id: int = 0,
) -> dict:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    from AgentBot.services.subscription_service import _get_cluster_servers
    from Shared.agent_db import upsert_customer, create_service, add_service_node, get_customer_by_telegram_id, make_service_note
    from AgentBot.database import upsert_customer_user, get_customer_user
    from Shared.database import get_server_by_id

    if int(payment_id or 0) > 0:
        existing_service = agent_db.find_service_by_source_payment(agent_id, payment_id)
        if existing_service:
            if str(existing_service.get("deleted_at") or "").strip():
                raise RuntimeError("source_service_deleted")
            existing_fulfillment = agent_db.get_payment_fulfillment(agent_id, payment_id)
            if str((existing_fulfillment or {}).get("state") or "").strip().lower() in {
                "local_recorded",
                "completed",
            }:
                return existing_service
            # A crash may happen after the service INSERT but before all cluster
            # node mappings are saved. Continue with the stable UUID and rebuild
            # every idempotent mapping before declaring local persistence done.

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

    # UUID is persisted before the network call, so a retry can discover a
    # panel user created immediately before a timeout/crash.
    fulfillment = agent_db.get_payment_fulfillment(agent_id, payment_id) if payment_id else None
    new_uuid = str((fulfillment or {}).get("panel_uuid") or "").strip()
    if not new_uuid:
        new_uuid = (
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"hiddify-sellbot:{agent_id}:{payment_id}"))
            if payment_id
            else str(uuid.uuid4())
        )
        if payment_id:
            agent_db.update_payment_fulfillment(agent_id, payment_id, panel_uuid=new_uuid)
    order_id_num = int(order.get("order_id") or 0)
    if order_id_num:
        panel_name = f"vpn-{order_id_num:07d}"
    else:
        panel_name = f"vpn-{user_tg_id}-{new_uuid[:8]}"
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

    from Shared.multi_panel import create_user as mp_create_user, get_user_by_uuid as mp_get_user
    shared_uuid = new_uuid
    payload["uuid"] = shared_uuid
    created_nodes: list[dict] = []
    panel_user = None
    node_errors: list[str] = []
    for idx, tgt in enumerate(targets):
        created = None
        try:
            created = await mp_get_user(tgt, shared_uuid)
        except Exception as lookup_exc:
            lookup_text = str(lookup_exc).lower()
            explicitly_missing = any(
                marker in lookup_text
                for marker in ("http 404", "not found", i18n.t('یافت نشد', _lg), "does not exist")
            )
            if not explicitly_missing:
                raise RuntimeError(
                    f"panel_lookup_failed server={tgt.get('id')}: {lookup_exc}"
                ) from lookup_exc
        if not created:
            try:
                created = await mp_create_user(tgt, payload)
            except Exception as create_exc:
                try:
                    created = await mp_get_user(tgt, shared_uuid)
                except Exception:
                    created = None
                if not created:
                    node_errors.append(f"server={tgt.get('id')}:{create_exc}")
                    continue
        created_uuid = str(created.get("uuid") or created.get("id") or "").strip()
        if not created_uuid:
            node_errors.append(f"server={tgt.get('id')}:missing_uuid")
            continue
        created_nodes.append(
            {
                "server_id": int(tgt.get("id") or 0),
                "server_title": tgt.get("title") or f"{i18n.t('سرور #', _lg)}{tgt.get('id')}",
                "panel_user_uuid": created_uuid,
                "panel_user_id": str(created.get("id") or "").strip(),
                "is_primary": idx == 0,
            }
        )
        if idx == 0:
            panel_user = created
    if panel_user is None or node_errors:
        raise RuntimeError("panel_nodes_incomplete: " + "; ".join(node_errors or ["primary_missing"]))
    panel_uuid = str(panel_user.get("uuid") or shared_uuid).strip()
    panel_user_id = str(panel_user.get("id") or "").strip()
    if payment_id:
        agent_db.update_payment_fulfillment(
            agent_id, payment_id, state="panel_applied", panel_uuid=panel_uuid, last_error=""
        )

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
        source_payment_id=int(payment_id or 0),
        source_order_id=order_id_num,
    )
    if not svc:
        raise RuntimeError("local_service_not_recorded")
    if svc:
        for item in created_nodes:
            add_service_node(
                service_id=svc["id"],
                server_id=int(item.get("server_id") or 0),
                server_title=item.get("server_title") or "",
                panel_user_uuid=str(item.get("panel_user_uuid") or "").strip(),
                panel_user_id=str(item.get("panel_user_id") or "").strip(),
            )

    if payment_id:
        agent_db.update_payment_fulfillment(
            agent_id, payment_id, state="local_recorded", service_id=int(svc["id"]), last_error=""
        )
    return svc


async def _renew_subscription_from_order(
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    user_tg_id: int,
    order: dict,
    tx_code: str = "",
    payment_id: int = 0,
) -> dict:
    """Apply a renewal using one persisted absolute target.

    Replaying this function patches the same values; it never adds the package
    a second time after a timeout or process crash.
    """
    from datetime import datetime, timedelta, timezone

    from CustomerBot.services import get_service_panel_targets
    from Shared import multi_panel

    service_id = int(order.get("renew_service_id") or 0)
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id") or 0) != int(agent_id):
        raise RuntimeError("service_not_found")
    shared_cust = agent_db.get_customer_by_telegram_id(agent_id, user_tg_id)
    if not shared_cust or int(svc.get("customer_id") or 0) != int(shared_cust.get("id") or 0):
        raise RuntimeError("service_not_owned")

    extra_days = int(order.get("days") or 0)
    extra_gb = float(order.get("volume_gb") or 0)
    if extra_days <= 0 and extra_gb <= 0:
        raise RuntimeError("empty_renew_package")

    fulfillment = agent_db.get_payment_fulfillment(agent_id, payment_id) if payment_id else None
    target = dict((fulfillment or {}).get("target") or {})
    if target:
        if int(target.get("service_id") or 0) != service_id:
            raise RuntimeError("renew_target_service_conflict")
    else:
        vol_mode, time_mode = "add", "add"
        try:
            from Shared.userbot_db import get_renew_modes
            vol_mode, time_mode = get_renew_modes()
        except Exception:
            pass
        vol_mode = str(vol_mode or "add").strip().lower()
        time_mode = str(time_mode or "add").strip().lower()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        old_days_left = int(float(svc.get("days_left") or 0) or 0)
        if time_mode == "add":
            new_days_left = old_days_left + extra_days
            end_date = str(svc.get("end_date") or "").strip()
            if end_date:
                try:
                    current_end = datetime.strptime(end_date, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    current_end = now
            else:
                current_end = now
            new_end = current_end + timedelta(days=extra_days)
        else:
            new_days_left = extra_days
            new_end = now + timedelta(days=extra_days)
        new_usage_limit = (
            float(svc.get("usage_limit") or 0) + extra_gb
            if vol_mode == "add"
            else float(extra_gb)
        )
        target = {
            "service_id": service_id,
            "volume_mode": vol_mode,
            "time_mode": time_mode,
            "days_left": int(new_days_left),
            "usage_limit": float(new_usage_limit),
            "usage_current": 0.0 if vol_mode == "reset" else float(svc.get("usage_current") or 0),
            "start_date": (
                now.strftime("%Y-%m-%d %H:%M:%S")
                if vol_mode == "reset"
                else str(svc.get("start_date") or "")
            ),
            "end_date": new_end.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if payment_id:
            agent_db.update_payment_fulfillment(agent_id, payment_id, target=target)

    vol_mode = str(target.get("volume_mode") or "add")
    time_mode = str(target.get("time_mode") or "add")
    new_days_left = int(target.get("days_left") or 0)
    new_usage_limit = float(target.get("usage_limit") or 0)
    new_end_str = str(target.get("end_date") or "")

    # ── پنل‌ها ── (payload مطابق مسیر تمدید نماینده در AgentBot.services.subscription_service)
    targets = get_service_panel_targets(svc)
    if not targets:
        raise RuntimeError("panel_targets_not_found")
    patch_data = {
        "usage_limit_GB": new_usage_limit,
        "package_days": int(new_days_left),
    }
    if vol_mode == "reset":
        patch_data["current_usage_GB"] = 0
    if time_mode == "reset":
        patch_data["start_date"] = str(target.get("start_date") or "")[:10]
    panel_errors: list[str] = []
    for srv, panel_uuid, _un in targets:
        try:
            await multi_panel.patch_user(srv, panel_uuid, patch_data)
        except Exception as e:
            panel_errors.append(f"patch server={srv.get('id')}:{e}")
    if panel_errors:
        raise RuntimeError("panel_patch_failed: " + "; ".join(panel_errors))
    if payment_id:
        agent_db.update_payment_fulfillment(
            agent_id, payment_id, state="panel_applied", target=target, last_error=""
        )

    enable_errors: list[str] = []
    for srv, panel_uuid, _un in targets:
        try:
            await multi_panel.enable_user(srv, panel_uuid)
        except Exception as e:
            enable_errors.append(f"enable server={srv.get('id')}:{e}")
    if enable_errors:
        raise RuntimeError("panel_enable_failed: " + "; ".join(enable_errors))

    local_updates = {
        "days_left": new_days_left,
        "usage_limit": new_usage_limit,
        "usage_current": float(target.get("usage_current") or 0),
        "start_date": str(target.get("start_date") or ""),
        "end_date": new_end_str,
        "is_active": 1,
    }
    if not agent_db.update_service(service_id, local_updates):
        raise RuntimeError("local_renew_update_failed")
    updated_svc = agent_db.get_service_by_id(service_id) or dict(svc)
    if payment_id:
        agent_db.update_payment_fulfillment(
            agent_id, payment_id, state="local_recorded", service_id=service_id,
            target=target, last_error="",
        )
    return updated_svc


async def _post_fulfillment_customer_delivery(
    context: ContextTypes.DEFAULT_TYPE,
    agent_id: int,
    pay: dict,
    svc: dict,
    is_renew: bool,
) -> None:
    """Best-effort customer messages, strictly after financial commit."""
    if not svc or not int(svc.get("id") or 0):
        return
    user_tg_id = int(pay.get("user_id") or 0)
    if not user_tg_id:
        return
    lang = "fa"
    try:
        from CustomerBot.database import get_user as _cget
        customer = _cget(agent_id, user_tg_id) or {}
        lang = str(customer.get("language") or "fa").strip().lower() or "fa"
    except Exception:
        pass
    tx_code = str(pay.get("tx_code") or "").strip()
    if is_renew:
        notify = (
            "♻️ " + _t(lang, "renew_notify_title") + "\n\n"
            f"📊 {_t(lang, 'volume_label')}: {float(svc.get('usage_limit') or 0):g} {_t(lang, 'gb_unit')}\n"
            f"⏳ {_t(lang, 'expire_label')}: {str(svc.get('end_date') or '')[:10]}\n\n"
            + _t(lang, "renew_notify_hint")
        )
    else:
        notify = _t(lang, "created_notify_title") + "\n" + _t(lang, "created_notify_hint")
    if tx_code:
        notify += "\n\n🎁 " + _t(lang, "tx_id_label") + tx_code
    await _notify_customer(context, agent_id, user_tg_id, notify)
    await _send_subscription_delivery(context, agent_id, user_tg_id, int(svc["id"]))


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
        _cust_lang = "fa"
        try:
            from CustomerBot.database import get_user as _cget
            _cu = _cget(agent_id, user_tg_id) or {}
            _cust_lang = str(_cu.get("language") or "fa").strip().lower() or "fa"
        except Exception:
            _cust_lang = "fa"

        config_items: list[tuple[str, str]] = []
        base_urls = get_service_node_base_urls(svc)
        if base_urls:
            base_url = base_urls[0]
            if subs_settings.get("show_sub_link", True):
                config_items.append(("🔗 " + _t(_cust_lang, "config_sub_link") + ":", f"{base_url}/all.txt"))
            if subs_settings.get("show_auto_sub_link", False):
                config_items.append(("🤖 " + _t(_cust_lang, "auto_sub_link_label") + ":", f"{base_url}/sub/?asn=unknown"))
            if subs_settings.get("show_sub_link_b64", False):
                config_items.append(("🔐 " + _t(_cust_lang, "sub_b64_label") + ":", f"{base_url}/all.txt?base64=1"))
            if subs_settings.get("show_multi_server", False):
                try:
                    managed_link, _ = get_or_create_bot_sub_links(svc)
                    if managed_link:
                        config_items.append((_t(_cust_lang, "config_smart") + ":", managed_link))
                except Exception as e:
                    logger.warning("Failed to build managed sub link after delivery (service_id=%s): %s", service_id, e)
            if subs_settings.get("show_multi_server_b64", False):
                try:
                    _, managed_link_b64 = get_or_create_bot_sub_links(svc)
                    if managed_link_b64:
                        config_items.append(("🌐 " + _t(_cust_lang, "smart_b64_label") + ":", managed_link_b64))
                except Exception as e:
                    logger.warning("Failed to build managed sub b64 link after delivery (service_id=%s): %s", service_id, e)

        if len(config_items) == 1:
            primary_link = config_items[0][1]
            qr_image = make_qr_image(primary_link)
            qr_caption = (
                _t(_cust_lang, "delivery_ready_title") + "\n\n"
                f"{config_items[0][0]}\n"
                f"<code>{escape(primary_link)}</code>\n\n"
                + _t(_cust_lang, "delivery_copy_hint")
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
                    _t(_cust_lang, "direct_configs_title") + "\n"
                    + _t(_cust_lang, "direct_configs_copy_hint") + "\n"
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
                        header = _t(_cust_lang, "direct_configs_title") if len(parts_list) == 1 else _t(_cust_lang, "direct_configs_title") + f" ({idx}/{len(parts_list)})"
                        part_text = (
                            f"{header}\n"
                            + _t(_cust_lang, "direct_configs_copy_hint_paged") + "\n"
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
            svc_text = build_subscription_status_text(svc, subs_settings, br, lang=_cust_lang)
            show_detach = bool(svc.get("comment") == "connected")
            kb = subscription_status_keyboard(
                service_id,
                show_direct_config=subs_settings.get("show_direct_config", True),
                show_sub_link=subs_settings.get("show_sub_link", True),
                show_detach=show_detach,
                lang=_cust_lang,
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


def _get_customer_menu_keyboard( lang: str = "fa"):
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
