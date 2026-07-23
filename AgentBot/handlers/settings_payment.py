import logging
import os
import secrets
import fcntl
from pathlib import Path

from dotenv import load_dotenv

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
from AgentBot.utils.helpers import _escape
from AgentBot.database import (
    get_setting, set_setting,
    get_cards, get_card, add_card, update_card, delete_card,
)

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
UD_NEW_CARD = "new_card_draft"


def _read_env_values() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    data: dict[str, str] = {}
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _write_env_values(updates: dict[str, str]) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ENV_FILE, "a+") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        lock_f.seek(0)
        existing_raw = lock_f.read().splitlines()
        lines = []
        seen: set[str] = set()
        for raw_line in existing_raw:
            stripped = raw_line.lstrip()
            if not stripped or stripped.startswith("#") or "=" not in raw_line:
                lines.append(raw_line)
                continue
            key, _value = raw_line.split("=", 1)
            clean_key = key.strip()
            if clean_key in updates:
                lines.append(f"{clean_key}={updates[clean_key]}")
                seen.add(clean_key)
            else:
                lines.append(raw_line)
        for key, value in updates.items():
            if key not in seen:
                lines.append(f"{key}={value}")
        lock_f.seek(0)
        lock_f.truncate()
        lock_f.write("\n".join(lines).rstrip() + "\n")
        fcntl.flock(lock_f, fcntl.LOCK_UN)
    for key, value in updates.items():
        os.environ[key] = value
    load_dotenv(dotenv_path=ENV_FILE, override=True)


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


async def _send_payment_menu(message, agent_id: int) -> None:
    card_enabled = bool(get_setting(agent_id, "card_payment_enabled", True))
    last4 = bool(get_setting(agent_id, "require_last4", False))
    rand_tx = bool(get_setting(agent_id, "random_tx_code", True))
    sms_auto = bool(get_setting(agent_id, "sms_auto_confirm", False))
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


def _sms_webhook_status() -> dict[str, str | bool]:
    env = _read_env_values()
    enabled_raw = str(env.get("SMS_WEBHOOK_ENABLED", os.getenv("SMS_WEBHOOK_ENABLED", "false")) or "false").strip().lower()
    secret = str(env.get("SMS_WEBHOOK_SECRET", os.getenv("SMS_WEBHOOK_SECRET", "")) or "").strip()
    age = str(env.get("SMS_WEBHOOK_MAX_PENDING_AGE_MINUTES", os.getenv("SMS_WEBHOOK_MAX_PENDING_AGE_MINUTES", "360")) or "360").strip() or "360"
    host = str(env.get("SUB_SERVER_PUBLIC_HOST", os.getenv("SUB_SERVER_PUBLIC_HOST", "")) or "").strip()
    scheme = str(env.get("SUB_SERVER_PUBLIC_SCHEME", os.getenv("SUB_SERVER_PUBLIC_SCHEME", "https")) or "https").strip() or "https"
    port = str(env.get("SUB_SERVER_PUBLIC_PORT", os.getenv("SUB_SERVER_PUBLIC_PORT", "443")) or "443").strip() or "443"
    if host:
        default_port = (scheme == "https" and port == "443") or (scheme == "http" and port == "80")
        base_url = f"{scheme}://{host}" if default_port else f"{scheme}://{host}:{port}"
    else:
        base_url = ""
    endpoint = f"{base_url}/payment/sms-webhook" if base_url else "https://YOUR_SUB_DOMAIN/payment/sms-webhook"
    return {
        "enabled": enabled_raw in {"1", "true", "yes", "on"},
        "secret": secret,
        "age": age,
        "endpoint": endpoint,
    }


async def _show_sms_settings(query) -> None:
    status = _sms_webhook_status()
    enabled = "✅ روشن" if status.get("enabled") else "❌ خاموش"
    text = (
        "🤖 تایید خودکار SMS بانک\n\n"
        f"وضعیت: {enabled}\n"
        f"Secret Key: {_mask_secret(str(status.get('secret') or ''))}\n"
        f"مهلت تطبیق پرداخت: {status.get('age')} دقیقه\n\n"
        "آدرس Webhook برای اپ اندروید:\n"
        f"<code>{_escape(str(status.get('endpoint') or ''))}</code>\n\n"
        "Secret و وضعیت روشن/خاموش از همین منو مدیریت می‌شود."
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
            await query.answer(f"\u067e\u0631\u062f\u0627\u062e\u062a \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a {'\u0641\u0639\u0627\u0644' if not current else '\u063a\u06cc\u0631\u0641\u0639\u0627\u0644'} \u0634\u062f.")
            await _refresh_card_settings(update, agent_id)
            return
        if p2 == "last4":
            current = bool(get_setting(agent_id, "require_last4", False))
            set_setting(agent_id, "require_last4", not current)
            await query.answer(f"\u0627\u0644\u0632\u0627\u0645 4 \u0631\u0642\u0645 \u0622\u062e\u0631 {'\u0641\u0639\u0627\u0644' if not current else '\u063a\u06cc\u0631\u0641\u0639\u0627\u0644'} \u0634\u062f.")
            await _refresh_card_settings(update, agent_id)
            return
        if p2 == "randtx":
            current = bool(get_setting(agent_id, "random_tx_code", True))
            set_setting(agent_id, "random_tx_code", not current)
            await query.answer(f"کد تراکنش تصادفی {'فعال' if not current else 'غیرفعال'} شد.")
            await _refresh_card_settings(update, agent_id)
            return
        if p2 == "smsauto":
            if p3 == "":
                await _show_sms_settings(query)
                return
            if p3 == "toggle":
                status = _sms_webhook_status()
                new_enabled = not bool(status.get("enabled"))
                updates = {"SMS_WEBHOOK_ENABLED": "true" if new_enabled else "false"}
                if new_enabled and not str(status.get("secret") or "").strip():
                    updates["SMS_WEBHOOK_SECRET"] = secrets.token_hex(32)
                if new_enabled:
                    updates["SMS_WEBHOOK_MAX_PENDING_AGE_MINUTES"] = "360"
                _write_env_values(updates)
                set_setting(agent_id, "sms_auto_confirm", new_enabled)
                await query.answer("ذخیره شد.", show_alert=True)
                await _show_sms_settings(query)
                return
        if p2 == "smsauto" and p3 == "regen":
            new_secret = secrets.token_hex(32)
            _write_env_values(
                {
                    "SMS_WEBHOOK_ENABLED": "true",
                    "SMS_WEBHOOK_SECRET": new_secret,
                    "SMS_WEBHOOK_MAX_PENDING_AGE_MINUTES": "360",
                }
            )
            set_setting(agent_id, "sms_auto_confirm", True)
            await query.answer("Secret جدید ساخته شد.")
            await _show_sms_settings(query)
            await query.message.reply_text(
                "🔐 Secret Key جدید اپ\nبرای کپی، متن داخل کادر را انتخاب کنید:\n\n"
                f"<code>{_escape(new_secret)}</code>",
                parse_mode="HTML",
            )
            return
        if p2 == "smsauto" and p3 == "show":
            status = _sms_webhook_status()
            secret = str(status.get("secret") or "").strip()
            if not secret:
                await query.answer("Secret هنوز ساخته نشده است. اول «ساخت Secret» را بزنید.", show_alert=True)
                return
            await query.message.reply_text(
                "🔐 Secret Key اپ\nبرای کپی، متن داخل کادر را انتخاب کنید:\n\n"
                f"<code>{_escape(secret)}</code>\n\n"
                "Webhook URL:\n"
                f"<code>{_escape(str(status.get('endpoint') or ''))}</code>",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        if p2 == "smsauto" and p3 == "help":
            status = _sms_webhook_status()
            await query.message.reply_text(
                "📱 راهنمای اتصال اپ SMS Verifier\n\n"
                "داخل اپ این مقدارها را وارد کنید:\n\n"
                "Webhook URL:\n"
                f"<code>{_escape(str(status.get('endpoint') or ''))}</code>\n\n"
                "Secret Key:\nاز دکمه «👁 نمایش Secret برای اپ» کپی کنید.\n\n"
                "سرشماره بانک:\nمثلاً <code>20004861</code>\n\n"
                "اگر بانک چهار رقم کارت را داخل SMS می‌فرستد، الزام ۴ رقم آخر را روشن کنید.",
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
            try:
                await query.message.reply_text(
                    "\u270f\ufe0f <b>\u062a\u0646\u0638\u06cc\u0645 \u0645\u062a\u0646 \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a</b>\n\n"
                    f"\u0645\u062a\u0646 \u0641\u0639\u0644\u06cc:\n<code>{_escape(current) or '(\u062e\u0627\u0644\u06cc)'}</code>\n\n"
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
