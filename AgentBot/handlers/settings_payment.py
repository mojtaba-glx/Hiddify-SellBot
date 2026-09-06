import logging
import os
import secrets
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
from AgentBot.utils.helpers import _escape
from AgentBot.database import (
    get_setting, set_setting,
    get_cards, get_card, add_card, update_card, delete_card,
)
from Shared import i18n

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


def _mask_secret(secret: str) -> str:
    _lg = "fa"
    text = str(secret or "").strip()
    if not text:
        return i18n.t('تنظیم نشده', _lg)
    if len(text) <= 12:
        return text[:3] + "..." + text[-3:]
    return text[:8] + "..." + text[-6:]


async def _show_card_details(query, card: dict) -> None:
    await _render_card_details(query, card, is_new=True)


async def _render_card_details(query, card: dict, is_new: bool = False) -> None:
    _lg = "fa"
    card_id = int(card.get("id") or 0)
    rows = [
        [IButton(i18n.t('✏️ ویرایش شماره کارت', _lg), callback_data=f"agbot:pay:cardeditnum:{card_id}")],
        [IButton(i18n.t('🧑 ویرایش نام صاحب کارت', _lg), callback_data=f"agbot:pay:cardeditowner:{card_id}")],
        [IButton(i18n.t('➖ حذف کارت', _lg), callback_data=f"agbot:pay:carddel:{card_id}")],
        [IButton(i18n.t('🔙 بازگشت', _lg), callback_data="agbot:pay:cards")],
    ]
    title = i18n.t('✅ کارت با موفقیت افزوده شد.\n\n', _lg) if is_new else i18n.t('💳 <b>مدیریت کارت</b>\n\n', _lg)
    text = (
        f"{title}{i18n.t('❖ شماره کارت: <code>', _lg)}{_escape(str(card.get('card_number') or ''))}{i18n.t('</code>\n❖ نام صاحب کارت: ', _lg)}{_escape(str(card.get('owner_name') or ''))}"
    )
    bank_name = str(card.get("bank_name") or "").strip()
    if bank_name:
        text += f"{i18n.t('\n❖ نام بانک: ', _lg)}{_escape(bank_name)}"
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
    try:
        _lg = i18n.get_agent_lang(int(agent_id or 0))
    except Exception:
        _lg = "fa"
    card_enabled = bool(get_setting(agent_id, "card_payment_enabled", True))
    last4 = bool(get_setting(agent_id, "require_last4", False))
    rand_tx = bool(get_setting(agent_id, "random_tx_code", True))
    sms_auto = bool(get_setting(agent_id, "sms_auto_confirm", False))
    _sync_random_tx_to_customer(agent_id)
    await message.reply_text(
        i18n.t('💳 <b>تنظیمات کارت به کارت</b>', _lg),
        reply_markup=card_settings_keyboard(card_enabled, last4, rand_tx, sms_auto),
        parse_mode="HTML",
    )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    agent_id = get_agent_id(context)
    if not agent_id or not update.message:
        return
    await update.message.reply_text(i18n.t('✅ عملیات لغو شد.', _lg), reply_markup=main_menu_keyboard())
    await _send_payment_menu(update.message, agent_id)


def _setting_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(default)


def _ensure_agent_sms_secret(agent_id: int) -> str:
    """Return this agent's webhook secret, creating it once when necessary."""
    aid = int(agent_id or 0)
    if aid <= 0:
        return ""
    secret = str(get_setting(aid, "sms_webhook_secret", "") or "").strip()
    if secret:
        return secret
    secret = secrets.token_hex(32)
    set_setting(aid, "sms_webhook_secret", secret)
    return secret


def _set_agent_sms_auto_enabled(agent_id: int, enabled: bool) -> str:
    """Change one tenant's switch without changing the global master switch."""
    aid = int(agent_id or 0)
    if aid <= 0:
        return ""
    secret = _ensure_agent_sms_secret(aid) if enabled else str(
        get_setting(aid, "sms_webhook_secret", "") or ""
    ).strip()
    set_setting(aid, "sms_auto_confirm", bool(enabled))
    return secret


def _sms_webhook_status(agent_id: int) -> dict[str, str | bool]:
    aid = int(agent_id or 0)
    env = _read_env_values()
    master_raw = str(
        env.get("SMS_WEBHOOK_ENABLED", os.getenv("SMS_WEBHOOK_ENABLED", "false")) or "false"
    ).strip().strip("\"'").lower()
    enabled = _setting_bool(get_setting(aid, "sms_auto_confirm", False), False) if aid > 0 else False
    secret = str(get_setting(aid, "sms_webhook_secret", "") or "").strip() if aid > 0 else ""
    # Migrate an already-enabled tenant away from the legacy global credential.
    # The agent must paste the newly shown scoped URL and secret into the app once.
    if enabled and not secret:
        secret = _ensure_agent_sms_secret(aid)
    age = str(
        env.get(
            "SMS_WEBHOOK_MAX_PENDING_AGE_MINUTES",
            os.getenv("SMS_WEBHOOK_MAX_PENDING_AGE_MINUTES", "360"),
        )
        or "360"
    ).strip() or "360"
    host = str(env.get("SUB_SERVER_PUBLIC_HOST", os.getenv("SUB_SERVER_PUBLIC_HOST", "")) or "").strip()
    scheme = str(
        env.get("SUB_SERVER_PUBLIC_SCHEME", os.getenv("SUB_SERVER_PUBLIC_SCHEME", "https"))
        or "https"
    ).strip() or "https"
    port = str(
        env.get("SUB_SERVER_PUBLIC_PORT", os.getenv("SUB_SERVER_PUBLIC_PORT", "443")) or "443"
    ).strip() or "443"
    if host:
        default_port = (scheme == "https" and port == "443") or (scheme == "http" and port == "80")
        base_url = f"{scheme}://{host}" if default_port else f"{scheme}://{host}:{port}"
    else:
        base_url = ""
    endpoint_base = (
        f"{base_url}/payment/sms-webhook"
        if base_url
        else "https://YOUR_SUB_DOMAIN/payment/sms-webhook"
    )
    endpoint = f"{endpoint_base}?agent_id={aid}" if aid > 0 else endpoint_base
    return {
        "enabled": enabled,
        "master_enabled": master_raw in {"1", "true", "yes", "on"},
        "secret": secret,
        "age": age,
        "endpoint": endpoint,
    }


async def _show_sms_settings(query, agent_id: int) -> None:
    try:
        _lg = i18n.get_agent_lang(int(agent_id or 0))
    except Exception:
        _lg = "fa"
    status = _sms_webhook_status(agent_id)
    enabled = i18n.t('✅ روشن', _lg) if status.get("enabled") else i18n.t('❌ خاموش', _lg)
    master_enabled = i18n.t('✅ فعال', _lg) if status.get("master_enabled") else i18n.t('❌ غیرفعال', _lg)
    text = (
        f"{i18n.t('🤖 تایید خودکار SMS بانک\n\nوضعیت سرویس مرکزی: ', _lg)}{master_enabled}{i18n.t('\nوضعیت این نماینده: ', _lg)}{enabled}\nSecret Key: {_mask_secret(str(status.get('secret') or ''))}{i18n.t('\nمهلت تطبیق پرداخت: ', _lg)}{status.get('age')}{i18n.t(' دقیقه\n\nآدرس Webhook برای اپ اندروید:\n<code>', _lg)}{_escape(str(status.get('endpoint') or ''))}{i18n.t('</code>\n\nSecret این بخش فقط برای همین نماینده است.\nاگر سرویس مرکزی غیرفعال است، مدیر اصلی باید آن را فعال کند.', _lg)}"
    )
    await query.edit_message_text(
        text,
        reply_markup=sms_webhook_settings_keyboard(bool(status.get("enabled"))),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


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
            i18n.t('💳 <b>تنظیمات کارت به کارت</b>', _lg),
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
            i18n.t('💳 <b>تنظیمات کارت به کارت</b>', _lg),
            reply_markup=card_settings_keyboard(card_enabled, last4, rand_tx, sms_auto),
            parse_mode="HTML",
        )
        return

    if (p2 == "back" and p1 == "pay") or (p2 == "payment" and p3 == "back"):
        from AgentBot.keyboards import config_menu_keyboard
        await query.edit_message_text(
            i18n.t('⚙️ <b>تنظیمات</b>\nگزینه مورد نظر را انتخاب کنید:', _lg),
            reply_markup=config_menu_keyboard(), parse_mode="HTML",
        )
        return

    if p1 == "pay":
        if p2 == "card":
            current = bool(get_setting(agent_id, "card_payment_enabled", True))
            set_setting(agent_id, "card_payment_enabled", not current)
            label = i18n.t('غیرفعال', _lg) if current else i18n.t('فعال', _lg)
            await query.answer(f"{i18n.t('پرداخت کارت به کارت ', _lg)}{label}{i18n.t(' شد.', _lg)}")
            await _refresh_card_settings(update, agent_id)
            return
        if p2 == "last4":
            current = bool(get_setting(agent_id, "require_last4", False))
            set_setting(agent_id, "require_last4", not current)
            label = i18n.t('غیرفعال', _lg) if current else i18n.t('فعال', _lg)
            # هم‌گام کردن با ربات مشتری تا از کاربر 4 رقم آخر کارت خواسته شود
            try:
                from CustomerBot.database import get_payment_settings, set_payment_settings
                cb_ps = get_payment_settings(agent_id) or {}
                cb_ps["require_last4_for_card_receipt"] = not current
                cb_ps.setdefault("enable_card_to_card", True)
                set_payment_settings(agent_id, cb_ps)
            except Exception as e:
                logger.warning("Failed to sync require_last4 to customer bot: %s", e)
            await query.answer(f"{i18n.t('الزام 4 رقم آخر ', _lg)}{label}{i18n.t(' شد.', _lg)}")
            await _refresh_card_settings(update, agent_id)
            return
        if p2 == "randtx":
            current = bool(get_setting(agent_id, "random_tx_code", True))
            new_value = not current
            set_setting(agent_id, "random_tx_code", new_value)
            _sync_random_tx_to_customer(agent_id)
            await query.answer(f"{i18n.t('کد تراکنش تصادفی ', _lg)}{i18n.t('فعال' if new_value else 'غیرفعال', _lg)}{i18n.t(' شد.', _lg)}")
            await _refresh_card_settings(update, agent_id)
            return
        if p2 == "smsauto":
            if p3 == "":
                await _show_sms_settings(query, agent_id)
                return
            if p3 == "toggle":
                status = _sms_webhook_status(agent_id)
                new_enabled = not bool(status.get("enabled"))
                _set_agent_sms_auto_enabled(agent_id, new_enabled)
                await query.answer(i18n.t('ذخیره شد.', _lg), show_alert=True)
                await _show_sms_settings(query, agent_id)
                return
        if p2 == "smsauto" and p3 == "regen":
            new_secret = secrets.token_hex(32)
            set_setting(agent_id, "sms_webhook_secret", new_secret)
            set_setting(agent_id, "sms_auto_confirm", True)
            await query.answer(i18n.t('Secret جدید ساخته شد.', _lg))
            await _show_sms_settings(query, agent_id)
            await query.message.reply_text(
                f"{i18n.t('🔐 Secret Key جدید اپ\nبرای کپی، متن داخل کادر را انتخاب کنید:\n\n<code>', _lg)}{_escape(new_secret)}</code>",
                parse_mode="HTML",
            )
            return
        if p2 == "smsauto" and p3 == "show":
            status = _sms_webhook_status(agent_id)
            secret = str(status.get("secret") or "").strip()
            if not secret:
                await query.answer(i18n.t('Secret هنوز ساخته نشده است. اول «ساخت Secret» را بزنید.', _lg), show_alert=True)
                return
            await query.message.reply_text(
                f"{i18n.t('🔐 Secret Key اپ\nبرای کپی، متن داخل کادر را انتخاب کنید:\n\n<code>', _lg)}{_escape(secret)}</code>\n\nWebhook URL:\n<code>{_escape(str(status.get('endpoint') or ''))}</code>",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        if p2 == "smsauto" and p3 == "help":
            status = _sms_webhook_status(agent_id)
            await query.message.reply_text(
                f"{i18n.t('📱 راهنمای اتصال اپ SMS Verifier\n\nداخل اپ این مقدارها را وارد کنید:\n\nWebhook URL:\n<code>', _lg)}{_escape(str(status.get('endpoint') or ''))}{i18n.t('</code>\n\nSecret Key:\nاز دکمه «👁 نمایش Secret برای اپ» کپی کنید.\n\nسرشماره بانک:\nمثلاً <code>20004861</code>\n\nاگر بانک چهار رقم کارت را داخل SMS می‌فرستد، الزام ۴ رقم آخر را روشن کنید.', _lg)}",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        if p2 == "cards":
            cards = get_cards(agent_id)
            text = i18n.t('📋 <b>لیست کارت‌ها</b>\n', _lg)
            if not cards:
                text += i18n.t('\nهیچ کارتی ثبت نشده.', _lg)
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
                    i18n.t('⬇️ لطفا اطلاعات زیر را برای افزودن کارت وارد کنید\n💳 لطفا شماره کارت را وارد کنید:', _lg),
                    reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
            except Exception:
                pass
            return
        if p2 == "cardedit":
            card_id = int(p3) if p3.isdigit() else 0
            card = get_card(card_id, agent_id)
            if not card:
                await query.answer(i18n.t('کارت پیدا نشد.', _lg), show_alert=True)
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
            field = i18n.t('شماره کارت', _lg) if p2 == "cardeditnum" else i18n.t('صاحب کارت', _lg)
            context.user_data["edit_card_field"] = "card_number" if p2 == "cardeditnum" else "owner_name"
            context.user_data[UD_STATE] = STATE_EDIT_CARD
            try:
                await query.message.reply_text(
                    f"{i18n.t('✏️ مقدار جدید برای ', _lg)}{field}{i18n.t(' را وارد کنید:', _lg)}",
                    reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
            except Exception:
                pass
            return
        if p2 == "carddel":
            card_id = int(p3) if p3.isdigit() else 0
            ok = delete_card(card_id, agent_id)
            await query.answer(i18n.t('حذف شد ✅', _lg) if ok else i18n.t('خطا!', _lg), show_alert=not ok)
            if ok:
                cards = get_cards(agent_id)
                try:
                    text = i18n.t('💳 <b>لیست کارت‌ها</b>\n', _lg)
                    if not cards:
                        text += i18n.t('\nهیچ کارتی ثبت نشده.', _lg)
                    await query.edit_message_text(text, reply_markup=payment_cards_list_keyboard(cards), parse_mode="HTML")
                except Exception:
                    pass
            return
        if p2 == "cardtext":
            current = get_setting(agent_id, "card_to_card_text", "")
            context.user_data[UD_STATE] = STATE_SET_CARD_TEXT
            _empty_label = i18n.t('(خالی)', _lg)
            try:
                await query.message.reply_text(
                    f"{i18n.t('✏️ <b>تنظیم متن کارت به کارت</b>\n\nمتن فعلی:\n<code>', _lg)}{_escape(current) or _empty_label}{i18n.t('</code>\n\nمتن جدید را ارسال کنید (یا برای خالی کردن — بفرستید):', _lg)}",
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
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    state = context.user_data.get(UD_STATE)
    text = update.message.text.strip()

    if state in (STATE_ADD_CARD, STATE_ADD_CARD_NUMBER):
        number = "".join(ch for ch in text if ch.isdigit())
        if len(number) != 16:
            await update.message.reply_text(
                i18n.t('❌ لطفا شماره کارت معتبر 16 رقمی وارد کنید.', _lg),
                reply_markup=cancel_keyboard(),
            )
            return True
        context.user_data[UD_NEW_CARD] = {"card_number": number}
        context.user_data[UD_STATE] = STATE_ADD_CARD_OWNER
        await update.message.reply_text(
            i18n.t('➡️ لطفا نام صاحب کارت را وارد کنید:', _lg),
            reply_markup=cancel_keyboard(),
        )
        return True

    if state == STATE_ADD_CARD_OWNER:
        owner = text.strip()
        if not owner:
            await update.message.reply_text(
                i18n.t('❌ لطفا نام صاحب کارت را وارد کنید.', _lg),
                reply_markup=cancel_keyboard(),
            )
            return True
        draft = dict(context.user_data.get(UD_NEW_CARD) or {})
        draft["owner_name"] = owner
        context.user_data[UD_NEW_CARD] = draft
        context.user_data[UD_STATE] = STATE_ADD_CARD_BANK
        await update.message.reply_text(
            i18n.t('🏦 لطفا نام بانک را وارد کنید:\nبرای رد شدن این مرحله عدد 0 را ارسال کنید.', _lg),
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
            await update.message.reply_text(i18n.t('❌ اطلاعات کارت ناقص است. دوباره تلاش کنید.', _lg))
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
                await update.message.reply_text(i18n.t('✅ بروزرسانی شد.', _lg), reply_markup=main_menu_keyboard(lang=_lg))
                await _show_card_details(type('Q', (), {'message': update.message})(), card)
                return True
        await update.message.reply_text(i18n.t('خطا!', _lg), reply_markup=main_menu_keyboard(lang=_lg))
        return True

    if state == STATE_SET_CARD_TEXT:
        if text == "\u2014":
            text = ""
        set_setting(agent_id, "card_to_card_text", text)
        from Shared import agent_db
        agent_db.sync_customer_bot_text_setting(agent_id, "card_to_card_text", text)
        context.user_data.pop(UD_STATE, None)
        await update.message.reply_text(
            i18n.t('✅ متن کارت به کارت ذخیره شد.', _lg),
            reply_markup=main_menu_keyboard(lang=_lg),
        )
        return True

    return False
