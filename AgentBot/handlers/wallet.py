import logging
import os
import random
from io import BytesIO

from telegram import Bot, Update, InlineKeyboardMarkup
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from telegram.ext import ContextTypes

from Shared import agent_db
from Shared import database as shared_db
from AgentBot.constants import WALLET_VIEW, WALLET_CREATE, WALLET_BACK, MENU_MAIN, UD_STATE, STATE_WALLET_CREATE
from AgentBot import database as agentbot_db
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import wallet_menu_keyboard, back_keyboard, cancel_keyboard
from AgentBot.utils.helpers import _escape, _fmt_toman, _normalize_digits

logger = logging.getLogger(__name__)

STATE_WALLET_CHARGE_AMOUNT = "st:wallet_charge_amount"
STATE_WALLET_CHARGE_RECEIPT = "st:wallet_charge_receipt"
STATE_WALLET_CHARGE_LAST4 = "st:wallet_charge_last4"


def _clear_wallet_charge_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(UD_STATE, None)
    context.user_data.pop("charge_amount", None)
    context.user_data.pop("charge_marker", None)
    context.user_data.pop("charge_final_amount", None)
    context.user_data.pop("charge_receipt_id", None)


def _wallet_back_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="agbot:wallet:back")]])


def _agent_display(agent: dict) -> str:
    return str(agent.get("full_name") or agent.get("username") or agent.get("telegram_id") or "نماینده")


async def _notify_admin_wallet_payment(context: ContextTypes.DEFAULT_TYPE, agent_id: int, payment: dict) -> None:
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
        "🕊 <b>گزارش تایید پرداخت نماینده</b> 🕊\n\n"
        "💸 شیوه پرداخت: کارت به کارت\n"
        f"🔑 شناسه تراکنش: <code>{ref_id}</code>\n"
        f"👤 نماینده: <b>{_escape(agent_name)}</b>\n"
        f"💰 مبلغ پرداخت: <b>{_fmt_toman(amount)}</b> تومان\n"
        f"💳 4 رقم آخر کارت مبدا: <code>{_escape(last4)}</code>"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("رد ❌", callback_data=f"agency:payno:{payment['id']}"),
            InlineKeyboardButton("تایید ✅", callback_data=f"agency:payok:{payment['id']}") ,
        ],
        [InlineKeyboardButton(f"{agent_name} 👤", callback_data=f"agency:view:{agent_id}")],
    ])
    bot = Bot(token=admin_token)
    if receipt_file_id:
        try:
            tg_file = await context.bot.get_file(receipt_file_id)
            bio = BytesIO()
            await tg_file.download_to_memory(out=bio)
            bio.seek(0)
            bio.name = f"agent_wallet_{payment['id']}.jpg"
            await bot.send_photo(chat_id=admin_id, photo=bio, caption=caption[:1024], reply_markup=kb, parse_mode="HTML")
            return
        except Exception as e:
            logger.warning("Failed sending wallet receipt photo to admin: %s", e)
    await bot.send_message(chat_id=admin_id, text=caption, reply_markup=kb, parse_mode="HTML")


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent_id = get_agent_id(context) or 0
    wallet = agent_db.get_wallet(agent_id)
    agent = agent_db.get_agent_by_id(agent_id) or {}
    active = "🟢 فعال" if int(agent.get("is_active", 0) or 0) else "🔴 غیرفعال"
    text = (
        f"💰 <b>کیف پول</b>\n\n"
        f"موجودی کیف پول شما <b>{_fmt_toman(wallet['balance'])}</b> تومان میباشد 🔻\n\n"
        f"وضعیت کاربر: <b>{active}</b> 👤"
    )
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=wallet_menu_keyboard(), parse_mode="HTML")
        except Exception:
            await update.message.reply_text(text, reply_markup=wallet_menu_keyboard(), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=wallet_menu_keyboard(), parse_mode="HTML")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    parts = data.split(":")
    action = parts[2] if len(parts) > 2 else ""
    agent_id = get_agent_id(context)

    if action == "back":
        _clear_wallet_charge_state(context)
        from AgentBot.keyboards import main_menu_keyboard
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="به منوی نمایندگی بازگشتید.",
            reply_markup=main_menu_keyboard(),
        )
        return

    if action == "view":
        await show_menu(update, context)
        return

    if action == "create":
        context.user_data[UD_STATE] = STATE_WALLET_CHARGE_AMOUNT
        try:
            await query.edit_message_text(
                "\U0001f3e6 <b>\u0634\u0627\u0631\u0698 \u06a9\u06cc\u0641 \u067e\u0648\u0644</b>\n\n"
                "\U0001f4b0 \u0645\u0628\u0644\u063a \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0628\u0647 \u062a\u0648\u0645\u0627\u0646 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                reply_markup=_wallet_back_inline_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "charge":
        context.user_data[UD_STATE] = STATE_WALLET_CHARGE_AMOUNT
        try:
            await query.edit_message_text(
                "لطفا مبلغی که قصد شارژ حساب خود دارید را به تومان وارد کنید: 🔻",
                reply_markup=_wallet_back_inline_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "paid":
        context.user_data[UD_STATE] = STATE_WALLET_CHARGE_RECEIPT
        try:
            await query.edit_message_text("⬇️ لطفا رسید پرداخت خود را در زیر این پیام ارسال کنید:", reply_markup=_wallet_back_inline_keyboard())
        except Exception:
            pass
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    state = context.user_data.get(UD_STATE)
    text = (update.message.text or update.message.caption or "").strip()

    if state == STATE_WALLET_CHARGE_AMOUNT:
        raw = _normalize_digits(text)
        try:
            amount = int(raw.replace(",", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("\u274c \u0645\u0628\u0644\u063a \u0646\u0627\u0645\u0639\u062a\u0628\u0631 \u0627\u0633\u062a.")
            return True
        marker = random.randint(100, 999)
        final_amount = amount + marker
        context.user_data["charge_amount"] = amount
        context.user_data["charge_marker"] = marker
        context.user_data["charge_final_amount"] = final_amount
        context.user_data[UD_STATE] = STATE_WALLET_CHARGE_RECEIPT
        card = shared_db.get_random_card() or {}
        card_text = (
            f"مشخصه تراکنش اعمال شد: +{_fmt_toman(marker)} تومان 🔢\n\n"
            f"💰 لطفا دقیقا مبلغ: <b>{_fmt_toman(final_amount * 10)}</b> ریال\n\n"
            f"💰 معادل: <b>{_fmt_toman(final_amount)}</b> تومان\n\n"
            f"💳 به شماره کارت: <code>{card.get('number', '?')}</code>\n\n"
            f"👤 به نام: {_escape(card.get('owner', '?'))}\n\n"
            f"❗ بعد از واریز مبلغ اسکرین شات از تراکنش برای ما ارسال کنید."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ پرداخت کردم، ارسال رسید", callback_data="agbot:wallet:paid")],
            [InlineKeyboardButton("بازگشت", callback_data="agbot:wallet:back")],
        ])
        await update.message.reply_text(card_text, parse_mode="HTML", reply_markup=kb)
        return True

    if state == STATE_WALLET_CHARGE_RECEIPT:
        photo = update.message.photo[-1] if update.message.photo else None
        if not photo:
            await update.message.reply_text("❌ لطفاً عکس رسید پرداخت را ارسال کنید.", reply_markup=cancel_keyboard())
            return True
        context.user_data["charge_receipt_id"] = photo.file_id
        context.user_data[UD_STATE] = STATE_WALLET_CHARGE_LAST4
        await update.message.reply_text("🔢 لطفا 4 رقم آخر کارت مبدا را ارسال کنید:")
        return True

    if state == STATE_WALLET_CHARGE_LAST4:
        last4 = _normalize_digits(text).strip()
        if not (last4.isdigit() and len(last4) == 4):
            await update.message.reply_text("❌ لطفاً دقیقاً 4 رقم آخر کارت مبدا را ارسال کنید.")
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
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop("charge_amount", None)
        context.user_data.pop("charge_marker", None)
        context.user_data.pop("charge_final_amount", None)
        context.user_data.pop("charge_receipt_id", None)
        from AgentBot.keyboards import main_menu_keyboard
        await update.message.reply_text(
            "✅ تراکنش شما در انتظار تایید توسط ادمین است. لطفا صبر کنید و از ارسال رسید تکراری بپرهیزید.",
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
            await update.message.reply_text("\u274c \u0645\u0628\u0644\u063a \u0646\u0627\u0645\u0639\u062a\u0628\u0631 \u0627\u0633\u062a.")
            return True
        if amount > 0:
            agent_db.charge_wallet(agent_id, amount, description="\u0634\u0627\u0631\u0698 \u0627\u0648\u0644\u06cc\u0647 \u06a9\u06cc\u0641 \u067e\u0648\u0644")
        wallet = agent_db.get_wallet(agent_id)
        context.user_data.pop(UD_STATE, None)
        from AgentBot.keyboards import main_menu_keyboard
        await update.message.reply_text(
            f"\u2705 <b>\u06a9\u06cc\u0641 \u067e\u0648\u0644 \u0628\u0627 \u0645\u0648\u0641\u0642\u06cc\u062a \u0633\u0627\u062e\u062a\u0647 \u0634\u062f!</b>\n\n"
            f"\U0001f4b5 \u0645\u0648\u062c\u0648\u062f\u06cc: {_fmt_toman(wallet['balance'])} \u062a\u0648\u0645\u0627\u0646",
            reply_markup=main_menu_keyboard(), parse_mode="HTML",
        )
        return True

    return False
