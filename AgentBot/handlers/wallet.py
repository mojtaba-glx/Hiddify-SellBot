import logging
import os
import random
from io import BytesIO

from telegram import Bot, Update, InlineKeyboardMarkup, ReplyKeyboardMarkup
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from Shared.tg_button_styles import keyboard_button as KButton
from telegram.ext import ContextTypes

from Shared import agent_db
from Shared import database as shared_db
from AgentBot.constants import WALLET_VIEW, WALLET_CREATE, WALLET_BACK, MENU_MAIN, UD_STATE, STATE_WALLET_CREATE
from AgentBot import database as agentbot_db
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import wallet_menu_keyboard, back_keyboard, cancel_keyboard, agent_lang
from AgentBot.utils.helpers import _escape, _fmt_toman, _normalize_digits
from Shared import i18n

logger = logging.getLogger(__name__)

STATE_WALLET_CHARGE_AMOUNT = "st:wallet_charge_amount"
STATE_WALLET_CHARGE_RECEIPT = "st:wallet_charge_receipt"
STATE_WALLET_CHARGE_LAST4 = "st:wallet_charge_last4"

BTN_BACK_TEXT = "بازگشت"


def _clear_wallet_charge_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(UD_STATE, None)
    context.user_data.pop("charge_amount", None)
    context.user_data.pop("charge_marker", None)
    context.user_data.pop("charge_final_amount", None)
    context.user_data.pop("charge_receipt_id", None)
    context.user_data.pop("charge_prompt_msg_id", None)
    context.user_data.pop("charge_back_kb_msg_id", None)


async def _delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> None:
    """حذف یک پیام با نادیده گرفتن خطاهای احتمالی."""
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _delete_current_prompt(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """حذف پیام/پرامپت مرحله قبل تا چت به صورت مرحله به مرحله تمیز بماند."""
    msg_id = context.user_data.pop("charge_prompt_msg_id", None)
    if msg_id:
        await _delete_message(context, chat_id, msg_id)


async def _delete_back_kb_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """حذف پیام کیبورد بازگشت مرحله قبل."""
    msg_id = context.user_data.pop("charge_back_kb_msg_id", None)
    if msg_id:
        await _delete_message(context, chat_id, msg_id)


async def _send_back_reply_keyboard(context: ContextTypes.DEFAULT_TYPE, chat_id: int, lang: str = "fa") -> int:
    """ارسال یک پیام با کیبورد پایین (دکمه بزرگ قرمز بازگشت) و برگرداندن message_id آن."""
    sent = await context.bot.send_message(
        chat_id=chat_id,
        text="\u200b",
        reply_markup=_wallet_back_reply_keyboard(),
    )
    return sent.message_id


def _wallet_back_reply_keyboard( lang: str = "fa") -> ReplyKeyboardMarkup:
    """کیبورد پایین (زیر منو) با دکمه بزرگ و قرمز «بازگشت»."""
    return ReplyKeyboardMarkup(
        [[KButton(i18n.t("back", lang), style="danger")]],
        resize_keyboard=True,
    )


def _agent_display(agent: dict) -> str:
    _lg = "fa"
    return str(agent.get("full_name") or agent.get("username") or agent.get("telegram_id") or i18n.t('نماینده', _lg))


async def _notify_admin_wallet_payment(context: ContextTypes.DEFAULT_TYPE, agent_id: int, payment: dict) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    admin_id = int(os.getenv("ADMIN_ID", "0") or 0)
    admin_token = os.getenv("ADMIN_BOT_TOKEN", "").strip()
    if not admin_id or not admin_token:
        return
    agent = agent_db.get_agent_by_id(agent_id) or {}
    try:
        import json
        meta = json.loads(payment.get("receipt_image") or "{}")
    except Exception:
        meta = {}
    receipt_file_id = str(meta.get("receipt_file_id") or "")
    amount = int(payment.get("amount") or 0)
    last4 = str(payment.get("card_last4") or meta.get("card_last4") or "")
    ref_id = str(payment.get("ref_id") or payment.get("id") or "")
    agent_name = _agent_display(agent)
    caption = (
        f"{i18n.t('🕊 <b>گزارش تایید پرداخت نماینده</b> 🕊\n\n💸 شیوه پرداخت: کارت به کارت\n🔑 شناسه تراکنش: <code>', _lg)}{ref_id}{i18n.t('</code>\n👤 نماینده: <b>', _lg)}{_escape(agent_name)}{i18n.t('</b>\n💰 مبلغ پرداخت: <b>', _lg)}{_fmt_toman(amount)}{i18n.t('</b> تومان\n💳 4 رقم آخر کارت مبدا: <code>', _lg)}{_escape(last4)}</code>"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(i18n.t('رد ❌', _lg), callback_data=f"agency:payno:{payment['id']}"),
            InlineKeyboardButton(i18n.t('تایید ✅', _lg), callback_data=f"agency:payok:{payment['id']}") ,
        ],
        [InlineKeyboardButton(f"{agent_name} 👤", callback_data=f"agency:view:{agent_id}")],
    ])
    bot = Bot(token=admin_token)
    sent_message = None
    sent_kind = "text"
    if receipt_file_id:
        try:
            tg_file = await context.bot.get_file(receipt_file_id)
            bio = BytesIO()
            await tg_file.download_to_memory(out=bio)
            bio.seek(0)
            bio.name = f"agent_wallet_{payment['id']}.jpg"
            sent_message = await bot.send_photo(
                chat_id=admin_id,
                photo=bio,
                caption=caption[:1024],
                reply_markup=kb,
                parse_mode="HTML",
            )
            sent_kind = "photo"
        except Exception as e:
            logger.warning("Failed sending wallet receipt photo to admin: %s", e)
    if sent_message is None:
        sent_message = await bot.send_message(
            chat_id=admin_id, text=caption, reply_markup=kb, parse_mode="HTML"
        )
    if sent_message is not None:
        agentbot_db.patch_wallet_charge_payment_meta(
            int(payment.get("id") or 0),
            {
                "admin_chat_id": int(admin_id),
                "admin_message_id": int(sent_message.message_id),
                "admin_message_kind": sent_kind,
            },
        )


async def _mark_admin_wallet_payment_auto_approved(
    payment: dict,
    wallet_result: dict,
) -> None:
    """Replace the pending admin report after late SMS reconciliation."""
    _lg = "fa"
    admin_id = int(os.getenv("ADMIN_ID", "0") or 0)
    admin_token = os.getenv("ADMIN_BOT_TOKEN", "").strip()
    if not admin_id or not admin_token:
        return
    latest = agentbot_db.get_payment_by_id(int(payment.get("id") or 0)) or payment
    try:
        import json
        meta = json.loads(str(latest.get("receipt_image") or "{}"))
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    amount = int(latest.get("amount") or 0)
    balance = int((wallet_result.get("wallet") or {}).get("balance") or 0)
    event = wallet_result.get("event") or {}
    text = (
        f"{i18n.t('✅ <b>شارژ کیف پول نماینده با SMS تایید شد</b>\n\n👤 نماینده: <b>', _lg)}{_escape(latest.get('customer_name') or latest.get('agent_id'))}{i18n.t('</b>\n💰 مبلغ: <b>', _lg)}{_fmt_toman(amount)}{i18n.t('</b> تومان\n💼 موجودی جدید: <b>', _lg)}{_fmt_toman(balance)}{i18n.t('</b> تومان\n📨 سرشماره: <code>', _lg)}{_escape(event.get('sender') or '-')}{i18n.t('</code>\n🔖 پیگیری SMS: <code>', _lg)}{_escape(event.get('reference') or '-')}</code>"
    )
    bot = Bot(token=admin_token)
    message_id = int(meta.get("admin_message_id") or 0)
    message_kind = str(meta.get("admin_message_kind") or "").strip().lower()
    if message_id:
        try:
            if message_kind == "photo":
                await bot.edit_message_caption(
                    chat_id=admin_id,
                    message_id=message_id,
                    caption=text[:1024],
                    parse_mode="HTML",
                    reply_markup=None,
                )
            else:
                await bot.edit_message_text(
                    chat_id=admin_id,
                    message_id=message_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            return
        except Exception as exc:
            logger.warning("Failed editing auto-approved agent wallet report: %s", exc)
    await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent_id = get_agent_id(context) or 0
    wallet = agent_db.get_wallet(agent_id)
    agent = agent_db.get_agent_by_id(agent_id) or {}
    from Shared import i18n as _i18n
    _lg = agent_lang(context)
    active = _i18n.t("ag_active", _lg) if int(agent.get("is_active", 0) or 0) else _i18n.t("ag_inactive", _lg)
    text = (
        _i18n.t("ag_wallet_title", _lg) + "\n\n"
        + _i18n.t("ag_wallet_balance", _lg, b=_fmt_toman(wallet['balance'])) + "\n\n"
        + _i18n.t("ag_wallet_status", _lg, s=active)
    )
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=wallet_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML")
        except Exception:
            if update.message:
                await update.message.reply_text(text, reply_markup=wallet_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML")
    else:
        if update.message:
            await update.message.reply_text(text, reply_markup=wallet_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML")


async def _handle_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """بازگشت از هر مرحله شارژ به منوی کیف پول و بازگرداندن کیبورد اصلی."""
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    _clear_wallet_charge_state(context)
    await show_menu(update, context)
    from AgentBot.keyboards import main_menu_keyboard
    if update.message:
        await update.message.reply_text(i18n.t('منوی اصلی:', _lg), reply_markup=main_menu_keyboard())


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
        _clear_wallet_charge_state(context)
        await show_menu(update, context)
        return

    if action == "view":
        await show_menu(update, context)
        return

    if action == "create":
        context.user_data[UD_STATE] = STATE_WALLET_CHARGE_AMOUNT
        chat_id = query.message.chat_id
        try:
            await query.message.delete()
        except Exception:
            pass
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=i18n.t('🏦 <b>شارژ کیف پول</b>\n\n💰 مبلغ مورد نظر به تومان وارد کنید:', _lg),
            reply_markup=_wallet_back_reply_keyboard(), parse_mode="HTML",
        )
        context.user_data["charge_prompt_msg_id"] = sent.message_id
        return

    if action == "charge":
        context.user_data[UD_STATE] = STATE_WALLET_CHARGE_AMOUNT
        chat_id = query.message.chat_id
        try:
            await query.message.delete()
        except Exception:
            pass
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=i18n.t('لطفا مبلغی که قصد شارژ حساب خود دارید را به تومان وارد کنید: 🔻', _lg),
            reply_markup=_wallet_back_reply_keyboard(), parse_mode="HTML",
        )
        context.user_data["charge_prompt_msg_id"] = sent.message_id
        return

    if action == "paid":
        context.user_data[UD_STATE] = STATE_WALLET_CHARGE_RECEIPT
        chat_id = query.message.chat_id
        try:
            await query.edit_message_text(
                i18n.t('⬇️ لطفا رسید پرداخت خود را در زیر این پیام ارسال کنید:', _lg),
                reply_markup=InlineKeyboardMarkup([]),
            )
            context.user_data["charge_prompt_msg_id"] = query.message.message_id
        except Exception:
            pass
        # ابتدا کیبورد پایین را مخفی کن تا پیام قبلی کیبورد دفن نشود
        try:
            from telegram import ReplyKeyboardRemove
            removed = await context.bot.send_message(
                chat_id=chat_id,
                text="\u200b",
                reply_markup=ReplyKeyboardRemove(),
            )
            await _delete_message(context, chat_id, removed.message_id)
        except Exception:
            pass
        # سپس کیبورد بازگشت را با one_time_keyboard=True ارسال کن
        # تا بعد از اینکه کاربر رسید را فرستاد، خودبه‌خود محو شود
        try:
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text="\u200b",
                reply_markup=ReplyKeyboardMarkup(
                    [[KButton(i18n.t("back", _lg), style="danger")]],
                    resize_keyboard=True,
                    one_time_keyboard=True,
                ),
            )
            context.user_data["charge_back_kb_msg_id"] = sent.message_id
        except Exception:
            pass
        return


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
    text = (update.message.text or update.message.caption or "").strip()

    # دکمه «بازگشت» در کیبورد پایین: در هر مرحله از شارژ به منوی کیف پول برمی‌گردد
    if (text == BTN_BACK_TEXT or i18n.resolve_button(text, ("back", "btn_back", "btn_back_plain")) == "back" or text == "/cancel") and state in (
        STATE_WALLET_CHARGE_AMOUNT,
        STATE_WALLET_CHARGE_RECEIPT,
        STATE_WALLET_CHARGE_LAST4,
    ):
        await _handle_back(update, context)
        return True

    if state == STATE_WALLET_CHARGE_AMOUNT:
        raw = _normalize_digits(text)
        try:
            amount = int(raw.replace(",", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(i18n.t('❌ مبلغ نامعتبر است.', _lg))
            return True
        marker = random.randint(100, 999)
        final_amount = amount + marker
        context.user_data["charge_amount"] = amount
        context.user_data["charge_marker"] = marker
        context.user_data["charge_final_amount"] = final_amount
        context.user_data[UD_STATE] = STATE_WALLET_CHARGE_RECEIPT
        card = shared_db.get_random_card() or {}
        card_text = (
            f"{i18n.t('مشخصه تراکنش اعمال شد: +', _lg)}{_fmt_toman(marker)}{i18n.t(' تومان 🔢\n\n💰 لطفا دقیقا مبلغ: <b>', _lg)}{_fmt_toman(final_amount * 10)}{i18n.t('</b> ریال\n\n💰 معادل: <b>', _lg)}{_fmt_toman(final_amount)}{i18n.t('</b> تومان\n\n💳 به شماره کارت: <code>', _lg)}{card.get('number', '?')}{i18n.t('</code>\n\n👤 به نام: ', _lg)}{_escape(card.get('owner', '?'))}{i18n.t('\n\n❗ بعد از واریز مبلغ اسکرین شات از تراکنش برای ما ارسال کنید.', _lg)}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(i18n.t('✅ پرداخت کردم، ارسال رسید', _lg), callback_data="agbot:wallet:paid")],
        ])
        chat_id = update.message.chat_id
        user_msg_id = update.message.message_id
        # ارسال پیام کارت با دکمه اینلاین «پرداخت کردم»
        sent = await context.bot.send_message(chat_id=chat_id, text=card_text, parse_mode="HTML", reply_markup=kb)
        # ارسال کیبورد پایین با دکمه بازگشت
        back_kb_id = await _send_back_reply_keyboard(context, chat_id)
        # پیشروی مرحله به مرحله: حذف پرامپت مبلغ و پیام کاربر
        await _delete_current_prompt(context, chat_id)
        await _delete_message(context, chat_id, user_msg_id)
        context.user_data["charge_prompt_msg_id"] = sent.message_id
        context.user_data["charge_back_kb_msg_id"] = back_kb_id
        return True

    if state == STATE_WALLET_CHARGE_RECEIPT:
        photo = update.message.photo[-1] if update.message.photo else None
        if not photo:
            await update.message.reply_text(i18n.t('❌ لطفاً عکس رسید پرداخت را ارسال کنید.', _lg))
            return True
        context.user_data["charge_receipt_id"] = photo.file_id
        context.user_data[UD_STATE] = STATE_WALLET_CHARGE_LAST4
        chat_id = update.message.chat_id
        user_msg_id = update.message.message_id
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=i18n.t('🔢 لطفا 4 رقم آخر کارت مبدا را ارسال کنید:', _lg),
            reply_markup=_wallet_back_reply_keyboard(),
        )
        # حذف پیام کیبورد بازگشت مرحله قبل
        await _delete_back_kb_message(context, chat_id)
        # پیشروی مرحله به مرحله: حذف پرامپت رسید و پیام عکس کاربر
        await _delete_current_prompt(context, chat_id)
        await _delete_message(context, chat_id, user_msg_id)
        context.user_data["charge_prompt_msg_id"] = sent.message_id
        return True

    if state == STATE_WALLET_CHARGE_LAST4:
        last4 = _normalize_digits(text).strip()
        if not (last4.isdigit() and len(last4) == 4):
            await update.message.reply_text(i18n.t('❌ لطفاً دقیقاً 4 رقم آخر کارت مبدا را ارسال کنید.', _lg))
            return True
        amount = int(context.user_data.get("charge_amount") or 0)
        marker = int(context.user_data.get("charge_marker") or 0)
        receipt_id = str(context.user_data.get("charge_receipt_id") or "")
        agent = agent_db.get_agent_by_id(agent_id) or {}
        payment = agentbot_db.create_wallet_charge_payment(
            agent_id=agent_id,
            agent_name=_agent_display(agent),
            base_amount=amount,
            marker_amount=marker,
            receipt_file_id=receipt_id,
            card_last4=last4,
        )
        await _notify_admin_wallet_payment(context, agent_id, payment)
        try:
            auto_result = agentbot_db.try_approve_wallet_charge_from_unmatched_sms(
                int(payment.get("id") or 0)
            )
        except Exception as exc:
            logger.warning(
                "Agent wallet SMS reconciliation failed payment=%s: %s",
                payment.get("id"),
                exc,
            )
            auto_result = {"ok": False, "reason": "reconciliation_failed"}
        auto_approved = bool(auto_result.get("ok"))
        if auto_approved:
            await _mark_admin_wallet_payment_auto_approved(payment, auto_result)
        chat_id = update.message.chat_id
        user_msg_id = update.message.message_id
        # حذف پیام کیبورد بازگشت مرحله قبل
        await _delete_back_kb_message(context, chat_id)
        # پیشروی مرحله به مرحله: حذف پرامپت 4 رقم و پیام کاربر، سپس ارسال پیام پایانی
        await _delete_current_prompt(context, chat_id)
        await _delete_message(context, chat_id, user_msg_id)
        _clear_wallet_charge_state(context)
        from AgentBot.keyboards import main_menu_keyboard
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"{i18n.t('✅ پرداخت شما با پیامک بانک تایید شد و مبلغ ', _lg)}{_fmt_toman(payment.get('amount'))}{i18n.t(' تومان به کیف پول اضافه شد.', _lg)}"
                if auto_approved
                else i18n.t('✅ تراکنش شما در انتظار تایید توسط ادمین است. لطفا صبر کنید و از ارسال رسید تکراری بپرهیزید.', _lg)
            ),
            reply_markup=main_menu_keyboard(), parse_mode="HTML",
        )
        return True

    if state == STATE_WALLET_CREATE:
        raw = _normalize_digits(text)
        try:
            amount = int(raw)
            if amount < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(i18n.t('❌ مبلغ نامعتبر است.', _lg))
            return True
        if amount > 0:
            agent_db.charge_wallet(agent_id, amount, description=i18n.t('شارژ اولیه کیف پول', _lg))
        wallet = agent_db.get_wallet(agent_id)
        context.user_data.pop(UD_STATE, None)
        from AgentBot.keyboards import main_menu_keyboard
        await update.message.reply_text(
            f"{i18n.t('✅ <b>کیف پول با موفقیت ساخته شد!</b>\n\n💵 موجودی: ', _lg)}{_fmt_toman(wallet['balance'])}{i18n.t(' تومان', _lg)}",
            reply_markup=main_menu_keyboard(), parse_mode="HTML",
        )
        return True

    return False
