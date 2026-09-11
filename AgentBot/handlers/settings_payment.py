import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from AgentBot.constants import (
    UD_STATE, UD_SELECTED_CARD,
    STATE_ADD_CARD, STATE_ADD_CARD_NUMBER, STATE_ADD_CARD_OWNER, STATE_ADD_CARD_BANK,
    STATE_EDIT_CARD, STATE_SET_CARD_TEXT,
)
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import (
    card_settings_keyboard,
    cancel_keyboard,
    main_menu_keyboard,
    payment_cards_list_keyboard,
    sms_webhook_settings_keyboard,
    _ikb,
)
from Shared.tg_button_styles import inline_button as IButton
from Shared import agent_sms_webhook
from AgentBot.utils.helpers import _escape
from AgentBot.database import (
    get_setting, set_setting,
    get_cards, get_card, add_card, update_card, delete_card,
)

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
UD_NEW_CARD = "new_card_draft"


def _mask_secret(secret: str) -> str:
    text = str(secret or "").strip()
    if not text:
        return "تنظیم نشده"
    if len(text) <= 12:
        return text[:3] + "..." + text[-3:]
    return text[:8] + "..." + text[-6:]


async def _show_card_details(query, card: dict) -> None:
    await _render_card_details(query, card, is_new=True)


async def _render_card_details(query, card: dict, is_new: bool = False) -> None:
    card_id = int(card.get("id") or 0)
    rows = [
        [IButton("✏️ ویرایش شماره کارت", callback_data=f"agbot:pay:cardeditnum:{card_id}")],
        [IButton("🧑 ویرایش نام صاحب کارت", callback_data=f"agbot:pay:cardeditowner:{card_id}")],
        [IButton("➖ حذف کارت", callback_data=f"agbot:pay:carddel:{card_id}")],
        [IButton("🔙 بازگشت", callback_data="agbot:pay:cards")],
    ]
    title = "✅ کارت با موفقیت افزوده شد.\n\n" if is_new else "💳 <b>مدیریت کارت</b>\n\n"
    text = (
        f"{title}"
        f"❖ شماره کارت: <code>{_escape(str(card.get('card_number') or ''))}</code>\n"
        f"❖ نام صاحب کارت: {_escape(str(card.get('owner_name') or ''))}"
    )
    bank_name = str(card.get("bank_name") or "").strip()
    if bank_name:
        text += f"\n❖ نام بانک: {_escape(bank_name)}"
    if hasattr(query, "edit_message_text"):
        await query.edit_message_text(text, reply_markup=_ikb(rows), parse_mode="HTML")
    else:
        await query.message.reply_text(text, reply_markup=_ikb(rows), parse_mode="HTML")


def _sync_random_tx_to_customer(agent_id: int) -> bool:
    try:
        from CustomerBot.database import get_tx_plans_settings, set_tx_plans_settings
        txp = get_tx_plans_settings(agent_id) or {}
        txp["random_tx_spec"] = bool(get_setting(agent_id, "random_tx_code", True))
        set_tx_plans_settings(agent_id, txp)
        return True
    except Exception as e:
        logger.warning("Failed to sync random_tx_spec to customer bot: %s", e)
        return False


async def _send_payment_menu(message, agent_id: int) -> None:
    card_enabled = bool(get_setting(agent_id, "card_payment_enabled", True))
    last4 = bool(get_setting(agent_id, "require_last4", False))
    rand_tx = bool(get_setting(agent_id, "random_tx_code", True))
    sms_auto = bool(get_setting(agent_id, "sms_auto_confirm", False))
    _sync_random_tx_to_customer(agent_id)
    await message.reply_text(
        "💳 <b>تنظیمات کارت به کارت</b>",
        reply_markup=card_settings_keyboard(card_enabled, last4, rand_tx, sms_auto),
        parse_mode="HTML",
    )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent_id = get_agent_id(context)
    if not agent_id or not update.message:
        return
    await update.message.reply_text("✅ عملیات لغو شد.", reply_markup=main_menu_keyboard())
    await _send_payment_menu(update.message, agent_id)


def _agent_sms_webhook_status(agent_id: int) -> dict[str, str | bool]:
    """تنظیمات اختصاصی SMS همین نماینده — هرگز SMS_WEBHOOK_* مرکزی ادمین را
    نمی‌خواند، نشان نمی‌دهد یا تغییر نمی‌دهد. آدرس از دامنهٔ مدیریت‌شده یا
    در نبود آن از تنظیمات عمومی SUB_SERVER_PUBLIC_HOST/SCHEME/PORT ساخته
    میشود (فقط آدرس — بدون دسترسی به Secret مرکزی)."""
    from Shared import agent_sms_webhook
    settings = agent_sms_webhook.ensure_agent_sms_settings(agent_id)
    base_url = _resolve_public_base_url()
    if base_url:
        endpoint = agent_sms_webhook.agent_webhook_url(agent_id, base_url)
    else:
        # هیچ دامنه‌ای تنظیم نشده: مسیر نسبی به‌عنوان آدرس آمادهٔ اپ
        # معرفی نمیشود؛ فقط برای اطلاعات نمایش داده میشود.
        endpoint = ""
    return {
        "agent_id": agent_id,
        "enabled": bool(settings.get("enabled")),
        "secret": str(settings.get("secret") or ""),
        "endpoint": endpoint,
        "base_url_configured": bool(base_url),
        "webhook_path": agent_sms_webhook.agent_webhook_path(agent_id),
    }


def _rotate_agent_sms_secret(agent_id: int) -> dict:
    """Rotate only this agent's secret and preserve its current on/off state."""
    from Shared import agent_sms_webhook

    rotated = agent_sms_webhook.regenerate_agent_secret(agent_id)
    set_setting(agent_id, "sms_auto_confirm", bool(rotated.get("enabled")))
    return rotated


def _resolve_public_base_url() -> str:
    """دامنهٔ مدیریت‌شده؛ در نبود آن SUB_SERVER_PUBLIC_HOST/SCHEME/PORT."""
    from Shared import userbot_db
    managed = str(userbot_db.get_managed_sub_base_url() or "").strip()
    if managed:
        return managed.rstrip("/")
    host = str(os.getenv("SUB_SERVER_PUBLIC_HOST", "") or "").strip()
    if not host:
        return ""
    scheme = str(os.getenv("SUB_SERVER_PUBLIC_SCHEME", "https") or "https").strip().lower() or "https"
    port = str(os.getenv("SUB_SERVER_PUBLIC_PORT", "443") or "443").strip() or "443"
    default_port = (scheme == "https" and port == "443") or (scheme == "http" and port == "80")
    return f"{scheme}://{host}" if default_port else f"{scheme}://{host}:{port}"


async def _show_sms_settings(query, agent_id: int) -> None:
    status = _agent_sms_webhook_status(agent_id)
    enabled = "✅ روشن" if status.get("enabled") else "❌ خاموش"
    if status.get("base_url_configured"):
        endpoint_section = (
            "آدرس Webhook اختصاصی شما برای اپ اندروید:\n"
            f"<code>{_escape(str(status.get('endpoint') or ''))}</code>\n\n"
        )
    else:
        endpoint_section = (
            "⚠️ هنوز دامنهٔ عمومی تنظیم نشده است.\n"
            "برای اتصال اپ، از ادمین بخواهید دامنهٔ عمومی (Managed Domain یا "
            "SUB_SERVER_PUBLIC_HOST) را تنظیم کند.\n"
            f"مسیر اختصاصی شما: <code>{_escape(str(status.get('webhook_path') or ''))}</code>\n\n"
        )
    text = (
        "🤖 تایید خودکار SMS بانک (اختصاصی نمایندگی)\n\n"
        f"وضعیت: {enabled}\n"
        f"Secret Key اختصاصی: {_mask_secret(str(status.get('secret') or ''))}\n\n"
        f"{endpoint_section}"
        "این Secret و آدرس فقط متعلق به نمایندگی شماست و پرداخت مشتریان شما "
        "را تایید می‌کند؛ کیف پول عمده شما از طریق Webhook ادمین پردازش می‌شود."
    )
    await query.edit_message_text(
        text,
        reply_markup=sms_webhook_settings_keyboard(bool(status.get("enabled"))),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    parts = data.split(":")
    p1 = parts[1] if len(parts) > 1 else ""
    p2 = parts[2] if len(parts) > 2 else ""
    p3 = parts[3] if len(parts) > 3 else ""
    p4 = parts[4] if len(parts) > 4 else ""
    agent_id = get_agent_id(context)

    if p1 == "set" and p2 == "cfg" and p3 == "payment":
        card_enabled = bool(get_setting(agent_id, "card_payment_enabled", True))
        last4 = bool(get_setting(agent_id, "require_last4", False))
        rand_tx = bool(get_setting(agent_id, "random_tx_code", True))
        sms_auto = bool(get_setting(agent_id, "sms_auto_confirm", False))
        _sync_random_tx_to_customer(agent_id)
        await query.edit_message_text(
            "💳 <b>تنظیمات کارت به کارت</b>",
            reply_markup=card_settings_keyboard(card_enabled, last4, rand_tx, sms_auto),
            parse_mode="HTML",
        )
        return

    if p1 == "pay" and p2 == "menu":
        card_enabled = bool(get_setting(agent_id, "card_payment_enabled", True))
        last4 = bool(get_setting(agent_id, "require_last4", False))
        rand_tx = bool(get_setting(agent_id, "random_tx_code", True))
        sms_auto = bool(get_setting(agent_id, "sms_auto_confirm", False))
        _sync_random_tx_to_customer(agent_id)
        await query.edit_message_text(
            "\U0001f4b3 <b>\u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a</b>",
            reply_markup=card_settings_keyboard(card_enabled, last4, rand_tx, sms_auto),
            parse_mode="HTML",
        )
        return

    if (p2 == "back" and p1 == "pay") or (p2 == "payment" and p3 == "back"):
        from AgentBot.keyboards import config_menu_keyboard
        await query.edit_message_text(
            "\u2699\ufe0f <b>\u062a\u0646\u0638\u06cc\u0645\u0627\u062a</b>\n\u06af\u0632\u06cc\u0646\u0647 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f:",
            reply_markup=config_menu_keyboard(), parse_mode="HTML",
        )
        return

    if p1 == "pay":
        if p2 == "card":
            current = bool(get_setting(agent_id, "card_payment_enabled", True))
            set_setting(agent_id, "card_payment_enabled", not current)
            label = '\u063a\u06cc\u0631\u0641\u0639\u0627\u0644' if current else '\u0641\u0639\u0627\u0644'
            await query.answer(f"\u067e\u0631\u062f\u0627\u062e\u062a \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a {label} \u0634\u062f.")
            await _refresh_card_settings(update, agent_id)
            return
        if p2 == "last4":
            current = bool(get_setting(agent_id, "require_last4", False))
            set_setting(agent_id, "require_last4", not current)
            label = '\u063a\u06cc\u0631\u0641\u0639\u0627\u0644' if current else '\u0641\u0639\u0627\u0644'
            # هم‌گام کردن با ربات مشتری تا از کاربر 4 رقم آخر کارت خواسته شود
            try:
                from CustomerBot.database import get_payment_settings, set_payment_settings
                cb_ps = get_payment_settings(agent_id) or {}
                cb_ps["require_last4_for_card_receipt"] = not current
                cb_ps.setdefault("enable_card_to_card", True)
                set_payment_settings(agent_id, cb_ps)
            except Exception as e:
                logger.warning("Failed to sync require_last4 to customer bot: %s", e)
            await query.answer(f"\u0627\u0644\u0632\u0627\u0645 4 \u0631\u0642\u0645 \u0622\u062e\u0631 {label} \u0634\u062f.")
            await _refresh_card_settings(update, agent_id)
            return
        if p2 == "randtx":
            current = bool(get_setting(agent_id, "random_tx_code", True))
            new_value = not current
            set_setting(agent_id, "random_tx_code", new_value)
            _sync_random_tx_to_customer(agent_id)
            await query.answer(f"کد تراکنش تصادفی {'فعال' if new_value else 'غیرفعال'} شد.")
            await _refresh_card_settings(update, agent_id)
            return
        if p2 == "smsauto":
            if p3 == "":
                await _show_sms_settings(query, agent_id)
                return
            if p3 == "toggle":
                status = _agent_sms_webhook_status(agent_id)
                new_enabled = not bool(status.get("enabled"))
                # فقط تنظیمات خودِ نماینده — هرگز SMS_WEBHOOK_ENABLED یا
                # SMS_WEBHOOK_SECRET مرکزی ادمین تغییر نمیکند.
                agent_sms_webhook.set_agent_sms_enabled(agent_id, new_enabled)
                set_setting(agent_id, "sms_auto_confirm", new_enabled)
                await query.answer("ذخیره شد.", show_alert=True)
                await _show_sms_settings(query, agent_id)
                return
        if p2 == "smsauto" and p3 == "regen":
            # چرخش Secret شخصی نماینده — مسیر و Secret ادمین دست‌نخورده میماند.
            rotated = _rotate_agent_sms_secret(agent_id)
            new_secret = rotated.get("secret") or ""
            await query.answer("Secret جدید ساخته شد.")
            await _show_sms_settings(query, agent_id)
            await query.message.reply_text(
                "🔐 Secret Key جدید اختصاصی نمایندگی\nبرای کپی، متن داخل کادر را انتخاب کنید:\n\n"
                f"<code>{_escape(str(new_secret))}</code>",
                parse_mode="HTML",
            )
            return
        if p2 == "smsauto" and p3 == "show":
            status = _agent_sms_webhook_status(agent_id)
            secret = str(status.get("secret") or "").strip()
            if not secret:
                await query.answer("Secret هنوز ساخته نشده است. اول «ساخت Secret» را بزنید.", show_alert=True)
                return
            await query.message.reply_text(
                "🔐 Secret Key اختصاصی نمایندگی\nبرای کپی، متن داخل کادر را انتخاب کنید:\n\n"
                f"<code>{_escape(secret)}</code>\n\n"
                "Webhook URL اختصاصی:\n"
                f"<code>{_escape(str(status.get('endpoint') or ''))}</code>",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        if p2 == "smsauto" and p3 == "help":
            status = _agent_sms_webhook_status(agent_id)
            await query.message.reply_text(
                "📱 راهنمای اتصال اپ SMS Verifier (اختصاصی نمایندگی)\n\n"
                "داخل اپ این مقدارها را وارد کنید:\n\n"
                "Webhook URL (آدرس اختصاصی شما):\n"
                f"<code>{_escape(str(status.get('endpoint') or ''))}</code>\n\n"
                "Secret Key:\nاز دکمه «👁 نمایش Secret برای اپ» کپی کنید.\n\n"
                "سرشماره بانک:\nمثلاً <code>20004861</code>\n\n"
                "⚠️ توجه: از این پس آدرس و Secret اختصاصی خودتان (با شناسه "
                "نمایندگی در مسیر) را وارد کنید؛ آدرس و Secret قدیمی ادمین "
                "پرداخت مشتریان شما را تایید نمیکند.",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        if p2 == "cards":
            cards = get_cards(agent_id)
            text = "\U0001f4cb <b>\u0644\u06cc\u0633\u062a \u06a9\u0627\u0631\u062a\u200c\u0647\u0627</b>\n"
            if not cards:
                text += "\n\u0647\u06cc\u0686 \u06a9\u0627\u0631\u062a\u06cc \u062b\u0628\u062a \u0646\u0634\u062f\u0647."
            try:
                await query.edit_message_text(text, reply_markup=payment_cards_list_keyboard(cards), parse_mode="HTML")
            except Exception:
                pass
            return
        if p2 == "cardadd":
            context.user_data[UD_STATE] = STATE_ADD_CARD_NUMBER
            context.user_data.pop(UD_NEW_CARD, None)
            try:
                await query.message.reply_text(
                    "⬇️ لطفا اطلاعات زیر را برای افزودن کارت وارد کنید\n"
                    "💳 لطفا شماره کارت را وارد کنید:",
                    reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
            except Exception:
                pass
            return
        if p2 == "cardedit":
            card_id = int(p3) if p3.isdigit() else 0
            card = get_card(card_id, agent_id)
            if not card:
                await query.answer("\u06a9\u0627\u0631\u062a \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f.", show_alert=True)
                return
            context.user_data[UD_SELECTED_CARD] = card_id
            try:
                await _render_card_details(query, card, is_new=False)
            except Exception:
                pass
            return
        if p2 in ("cardeditnum", "cardeditowner"):
            card_id = int(p3) if p3.isdigit() else 0
            context.user_data[UD_SELECTED_CARD] = card_id
            field = "\u0634\u0645\u0627\u0631\u0647 \u06a9\u0627\u0631\u062a" if p2 == "cardeditnum" else "\u0635\u0627\u062d\u0628 \u06a9\u0627\u0631\u062a"
            context.user_data["edit_card_field"] = "card_number" if p2 == "cardeditnum" else "owner_name"
            context.user_data[UD_STATE] = STATE_EDIT_CARD
            try:
                await query.message.reply_text(
                    f"\u270f\ufe0f \u0645\u0642\u062f\u0627\u0631 \u062c\u062f\u06cc\u062f \u0628\u0631\u0627\u06cc {field} \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                    reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
            except Exception:
                pass
            return
        if p2 == "carddel":
            card_id = int(p3) if p3.isdigit() else 0
            ok = delete_card(card_id, agent_id)
            await query.answer("\u062d\u0630\u0641 \u0634\u062f \u2705" if ok else "\u062e\u0637\u0627!", show_alert=not ok)
            if ok:
                cards = get_cards(agent_id)
                try:
                    text = "💳 <b>لیست کارت‌ها</b>\n"
                    if not cards:
                        text += "\nهیچ کارتی ثبت نشده."
                    await query.edit_message_text(text, reply_markup=payment_cards_list_keyboard(cards), parse_mode="HTML")
                except Exception:
                    pass
            return
        if p2 == "cardtext":
            current = get_setting(agent_id, "card_to_card_text", "")
            context.user_data[UD_STATE] = STATE_SET_CARD_TEXT
            _empty_label = '(\u062e\u0627\u0644\u06cc)'
            try:
                await query.message.reply_text(
                    "\u270f\ufe0f <b>\u062a\u0646\u0638\u06cc\u0645 \u0645\u062a\u0646 \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a</b>\n\n"
                    f"\u0645\u062a\u0646 \u0641\u0639\u0644\u06cc:\n<code>{_escape(current) or _empty_label}</code>\n\n"
                    "\u0645\u062a\u0646 \u062c\u062f\u06cc\u062f \u0631\u0627 \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f (\u06cc\u0627 \u0628\u0631\u0627\u06cc \u062e\u0627\u0644\u06cc \u06a9\u0631\u062f\u0646 \u2014 \u0628\u0641\u0631\u0633\u062a\u06cc\u062f):",
                    reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
            except Exception:
                pass
            return


async def _refresh_card_settings(update: Update, agent_id: int) -> None:
    card_enabled = bool(get_setting(agent_id, "card_payment_enabled", True))
    last4 = bool(get_setting(agent_id, "require_last4", False))
    rand_tx = bool(get_setting(agent_id, "random_tx_code", True))
    sms_auto = bool(get_setting(agent_id, "sms_auto_confirm", False))
    _sync_random_tx_to_customer(agent_id)
    try:
        await update.callback_query.edit_message_reply_markup(
            reply_markup=card_settings_keyboard(card_enabled, last4, rand_tx, sms_auto)
        )
    except Exception:
        pass


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    state = context.user_data.get(UD_STATE)
    text = update.message.text.strip()

    if state in (STATE_ADD_CARD, STATE_ADD_CARD_NUMBER):
        number = "".join(ch for ch in text if ch.isdigit())
        if len(number) != 16:
            await update.message.reply_text(
                "❌ لطفا شماره کارت معتبر 16 رقمی وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return True
        context.user_data[UD_NEW_CARD] = {"card_number": number}
        context.user_data[UD_STATE] = STATE_ADD_CARD_OWNER
        await update.message.reply_text(
            "➡️ لطفا نام صاحب کارت را وارد کنید:",
            reply_markup=cancel_keyboard(),
        )
        return True

    if state == STATE_ADD_CARD_OWNER:
        owner = text.strip()
        if not owner:
            await update.message.reply_text(
                "❌ لطفا نام صاحب کارت را وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return True
        draft = dict(context.user_data.get(UD_NEW_CARD) or {})
        draft["owner_name"] = owner
        context.user_data[UD_NEW_CARD] = draft
        context.user_data[UD_STATE] = STATE_ADD_CARD_BANK
        await update.message.reply_text(
            "🏦 لطفا نام بانک را وارد کنید:\nبرای رد شدن این مرحله عدد 0 را ارسال کنید.",
            reply_markup=cancel_keyboard(),
        )
        return True

    if state == STATE_ADD_CARD_BANK:
        draft = dict(context.user_data.get(UD_NEW_CARD) or {})
        number = str(draft.get("card_number") or "").strip()
        owner = str(draft.get("owner_name") or "").strip()
        if not number or not owner:
            context.user_data.pop(UD_NEW_CARD, None)
            context.user_data.pop(UD_STATE, None)
            await update.message.reply_text("❌ اطلاعات کارت ناقص است. دوباره تلاش کنید.")
            return True
        bank = "" if text.strip() == "0" else text.strip()
        card = add_card(agent_id, number, owner, bank)
        context.user_data.pop(UD_NEW_CARD, None)
        context.user_data.pop(UD_STATE, None)
        card.setdefault("card_number", number)
        card.setdefault("owner_name", owner)
        card.setdefault("bank_name", bank)
        await _show_card_details(type('Q', (), {'message': update.message})(), card)
        return True

    if state == STATE_EDIT_CARD:
        card_id = context.user_data.get(UD_SELECTED_CARD)
        field = context.user_data.get("edit_card_field", "card_number")
        if not card_id:
            return False
        ok = update_card(card_id, agent_id, **{field: text})
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_SELECTED_CARD, None)
        context.user_data.pop("edit_card_field", None)
        if ok:
            card = get_card(card_id, agent_id)
            if card:
                await update.message.reply_text("✅ بروزرسانی شد.", reply_markup=main_menu_keyboard())
                await _show_card_details(type('Q', (), {'message': update.message})(), card)
                return True
        await update.message.reply_text("خطا!", reply_markup=main_menu_keyboard())
        return True

    if state == STATE_SET_CARD_TEXT:
        if text == "\u2014":
            text = ""
        set_setting(agent_id, "card_to_card_text", text)
        from Shared import agent_db
        agent_db.sync_customer_bot_text_setting(agent_id, "card_to_card_text", text)
        context.user_data.pop(UD_STATE, None)
        await update.message.reply_text(
            "\u2705 \u0645\u062a\u0646 \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a \u0630\u062e\u06cc\u0631\u0647 \u0634\u062f.",
            reply_markup=main_menu_keyboard(),
        )
        return True

    return False
