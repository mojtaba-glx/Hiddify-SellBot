from Shared import i18n
import json
import logging
from typing import Any, Dict

from telegram import Update
from telegram.ext import ContextTypes

from AgentBot.constants import UD_STATE, STATE_SEARCH_TX
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import tx_menu_keyboard, back_keyboard, cancel_keyboard, tx_list_keyboard, tx_search_results_keyboard, agent_lang
from AgentBot.utils.helpers import _escape, _fmt_toman, _status_icon
from AgentBot.database import (
    get_customer_payments,
    get_customer_payment_stats,
    search_customer_payments,
    get_customer_payment_detail,
)

logger = logging.getLogger(__name__)

_PAGE_SIZE = 8

def _tx_status_title(status: str, lang: str = "fa") -> str:
    """عنوان وضعیت تراکنش به زبان نماینده."""
    key = {"approved": "tx_approved", "rejected": "tx_rejected", "pending": "tx_pending"}.get(
        str(status or "").strip().lower(), "")
    if key:
        return i18n.t(key, lang)
    return str(status or "")


def _tx_method_title(method: str, lang: str = "fa") -> str:
    """عنوان روش پرداخت به زبان نماینده."""
    if str(method or "").strip().lower() == "card":
        return i18n.t("pay_card2card", lang)
    return str(method or "") or "-"


def _payment_customer_name(pay: Dict[str, Any]) -> str:
    _lg = "fa"
    name = (pay.get("full_name") or pay.get("username") or "").strip()
    if not name:
        name = f"{i18n.t('کاربر #', _lg)}{pay.get('user_id', '-')}"
    return name


def _payment_method_title(pay: Dict[str, Any], lang: str = "fa") -> str:
    return _tx_method_title(str(pay.get("method") or "").strip(), lang)


def _parse_receipt_meta(raw: str) -> Dict[str, Any]:
    """پارس receipt_image: file_id ساده | JSON خالص | \"{json}|key:val|...\"."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    if "|" not in raw and ":" not in raw:
        return {"file_id": raw}
    if "{" in raw and "|" in raw:
        json_part, _, rest = raw.partition("|")
        meta: Dict[str, Any] = {}
        try:
            parsed = json.loads(json_part)
            if isinstance(parsed, dict):
                meta.update(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        for seg in rest.split("|"):
            seg = seg.strip()
            if ":" not in seg:
                continue
            k, _, v = seg.partition(":")
            k, v = k.strip(), v.strip()
            if k and v:
                meta[k] = v
        return meta
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def _parse_receipt_card_last4(pay: Dict[str, Any]) -> str:
    meta = _parse_receipt_meta(str(pay.get("receipt_image") or ""))
    return str(meta.get("card_last4") or "").strip()


def _build_payment_detail_text(pay: Dict[str, Any]) -> str:
    _lg = "fa"
    tx_code = str(pay.get("tx_code") or "").strip() or str(pay.get("id") or "-")
    username = (str(pay.get("username") or "").strip()) or "-"
    if username != "-":
        username = f"@{username}"
    lines = [
        f"{i18n.t('◈ شناسه تراکنش: ', _lg)}{_escape(tx_code)}",
        f"{i18n.t('👤 کاربر: ', _lg)}{_escape((pay.get('full_name') or '-').strip() or '-')}",
        f"{i18n.t('◈ نام کاربری: ', _lg)}{_escape(username)}",
        f"{i18n.t('◈ شناسه کاربر: ', _lg)}{_escape(str(pay.get('user_id') or '-'))}",
        f"{i18n.t('◈ تاریخ تراکنش: ', _lg)}{_escape(pay.get('created_at', '') or '-')}",
        f"{i18n.t('◈ مبلغ تراکنش: ', _lg)}{_fmt_toman(pay.get('amount', 0))}{i18n.t(' تومان', _lg)}",
        "\u2756 \u2022 -------------------------- \u2022 \u2756",
        f"{i18n.t('◈ وضعیت: ', _lg)}{_tx_status_title(str(pay.get('status')), _lg)}",
        f"{i18n.t('◈ روش تراکنش: ', _lg)}{_payment_method_title(pay, lang=_lg)}",
    ]
    card_last4 = _parse_receipt_card_last4(pay)
    if card_last4:
        lines.append(f"{i18n.t('◈ ۴ رقم آخر کارت مبدا: ', _lg)}{_escape(card_last4)}")
    return "\n".join(lines)


def _payment_receipt_fid(pay: Dict[str, Any]) -> str:
    meta = _parse_receipt_meta(str(pay.get("receipt_image") or ""))
    if meta.get("file_id"):
        return str(meta["file_id"]).strip()
    return ""


def _payment_user_button_title(pay: Dict[str, Any]) -> str:
    _lg = "fa"
    name = (str(pay.get("full_name") or pay.get("username") or "").strip())
    if name:
        return name
    uid = pay.get("user_id")
    return f"{i18n.t('کاربر #', _lg)}{uid}" if uid else i18n.t('پروفایل کاربر', _lg)


def _tx_detail_keyboard(pay: Dict[str, Any], lang: str = "fa"):
    _lg = lang
    from AgentBot.keyboards import _ikb, IButton

    rows = []
    if str(pay.get("status") or "").strip().lower() != "approved":
        rows.append([IButton(i18n.t('✏️ تغییر وضعیت', _lg), callback_data=f"agbot:custpay:chg:{pay.get('id', 0)}")])
    rows.append([IButton(f"\U0001f464 {_payment_user_button_title(pay)}", callback_data=f"agbot:custpay:profile:{pay.get('user_id', 0)}")])
    return _ikb(rows)


async def _receipt_file_via_customer_bot(agent_id: int, file_id: str):
    """فایل عکس رسید را از طریق ربات مشتری دانلود می‌کند.

    چون file_id با توکن ربات مشتری ساخته شده و ربات نماینده نمی‌تواند
    مستقیم از آن استفاده کند، ابتدا دانلود و سپس به‌صورت بایت ارسال می‌شود.
    """
    from io import BytesIO
    from telegram import Bot
    from Shared.agent_db import get_active_customer_bot

    token = ""
    try:
        bot_row = get_active_customer_bot(agent_id)
        token = str((bot_row or {}).get("bot_token") or "").strip()
    except Exception:
        token = ""
    if not token or not file_id:
        return None
    try:
        customer_bot = Bot(token=token)
        tg_file = await customer_bot.get_file(file_id)
        bio = BytesIO()
        await tg_file.download_to_memory(out=bio)
        bio.seek(0)
        bio.name = f"receipt_{str(file_id)[:12]}.jpg"
        return bio
    except Exception as e:
        logger.warning("receipt download via customer bot failed agent=%s: %s", agent_id, e)
        return None


async def _send_receipt_photo_fallback(context: ContextTypes.DEFAULT_TYPE, agent_id: int, chat_id: int, receipt_fid: str, caption: str, kb, send_message_fallback) -> bool:
    """ارسال عکس رسید؛ در صورت خطای file_id، دانلود+آپلود مجدد. True در صورت موفقیت."""
    try:
        await context.bot.send_photo(chat_id=chat_id, photo=receipt_fid, caption=caption, reply_markup=kb, parse_mode="HTML")
        return True
    except Exception as e:
        logger.warning("send photo by file_id failed agent=%s: %s — trying reupload", agent_id, e)
    bio = await _receipt_file_via_customer_bot(agent_id, receipt_fid)
    if bio is None:
        return False
    try:
        await context.bot.send_photo(chat_id=chat_id, photo=bio, caption=caption, reply_markup=kb, parse_mode="HTML")
        return True
    except Exception as e:
        logger.warning("send reuploaded receipt photo failed agent=%s: %s", agent_id, e)
    return False


async def _send_payment_detail(context: ContextTypes.DEFAULT_TYPE, agent_id: int, chat_id: int, pay: Dict[str, Any], source_message=None) -> None:
    caption = _build_payment_detail_text(pay)
    kb = _tx_detail_keyboard(pay)
    receipt = _payment_receipt_fid(pay)

    if receipt:
        ok = await _send_receipt_photo_fallback(context, agent_id, chat_id, receipt, caption, kb, None)
        if ok:
            if source_message is not None:
                try:
                    await source_message.delete()
                except Exception:
                    pass
            return
        logger.warning("tx detail photo unavailable pay=%s", pay.get("id"))

    if source_message is not None:
        await source_message.reply_text(caption, reply_markup=kb, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=caption, reply_markup=kb, parse_mode="HTML")


def _filter_params(p3: str) -> Dict[str, Any]:
    status = None
    method = None
    if p3 == "card":
        method = "card"
    elif p3 in ("approved", "rejected", "pending"):
        status = p3
    return {"status": status, "method": method}


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
    agent_id = get_agent_id(context)
    p1 = parts[1] if len(parts) > 1 else ""
    p2 = parts[2] if len(parts) > 2 else ""
    p3 = parts[3] if len(parts) > 3 else ""
    p4 = parts[4] if len(parts) > 4 else ""

    if p1 == "set" and p2 == "tx" and not p3:
        await query.edit_message_text(
            i18n.t('💳 <b>مدیریت تراکنشات</b>', _lg),
            reply_markup=tx_menu_keyboard(), parse_mode="HTML",
        )
        return

    if (p2 == "back" and p1 == "set") or (p2 == "tx" and p3 == "back"):
        from AgentBot.keyboards import settings_menu_keyboard
        await query.edit_message_text(
            i18n.t('⚙️ <b>تنظیمات ربات</b>', _lg),
            reply_markup=settings_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML",
        )
        return

    if p3 in ("approved", "rejected", "pending", "card"):
        page = int(p4) if p4 and p4.isdigit() else 1
        fp = _filter_params(p3)
        stats = get_customer_payment_stats(agent_id, status=fp["status"], method=fp["method"])
        total_pages = max(1, (stats["total_count"] + _PAGE_SIZE - 1) // _PAGE_SIZE)
        if page > total_pages:
            page = total_pages
        payments, _ = get_customer_payments(
            agent_id, status=fp["status"], method=fp["method"], page=page, page_size=_PAGE_SIZE,
        )
        header_titles = {
            "approved": i18n.t('لیست تراکنشات تایید شده ✅', _lg),
            "rejected": i18n.t('لیست تراکنشات رد شده ❌', _lg),
            "pending": i18n.t('لیست تراکنشات در انتظار ⏳', _lg),
            "card": i18n.t('لیست تراکنشات کارت به کارت 💳', _lg),
        }
        text = (
            f"🔹 {header_titles[p3]}{i18n.t('\n🔸 تعداد تراکنشات: ', _lg)}{stats['total_count']}{i18n.t('\n🔸 مبلغ تراکنشات: ', _lg)}{_fmt_toman(stats['total_amount'])}{i18n.t(' تومان\n❖ ⬬----------------------------------⬬ ❖\n🔸 تراکنشات 30 روز گذشته: ', _lg)}{stats['last30_count']}{i18n.t('\n🔸 مبلغ تراکنشات 30 روز گذشته: ', _lg)}{_fmt_toman(stats['last30_amount'])}{i18n.t(' تومان\n❖ ⬬----------------------------------⬬ ❖\n🔸 تراکنشات این ماه: ', _lg)}{stats['month_count']}{i18n.t('\n🔸 مبلغ تراکنشات این ماه: ', _lg)}{_fmt_toman(stats['month_amount'])}{i18n.t(' تومان', _lg)}"
        )
        try:
            await query.edit_message_text(
                text,
                reply_markup=tx_list_keyboard(payments, p3, page, total_pages),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if p3 == "noop":
        await query.answer()
        return

    if p3 == "detail":
        pay_id = int(p4) if p4 and p4.isdigit() else 0
        pay = get_customer_payment_detail(pay_id)
        if not pay:
            await query.answer(i18n.t('❌ تراکنش یافت نشد.', _lg), show_alert=True)
            return
        await _send_payment_detail(context, agent_id, query.message.chat_id, pay, source_message=query.message)
        return

    if p3 == "search":
        context.user_data[UD_STATE] = STATE_SEARCH_TX
        try:
            await query.answer()
        except Exception:
            pass
        try:
            await query.message.reply_text(
                i18n.t('🔍 <b>جستجوی تراکنش</b>\nشناسه تراکنش (عدد) یا نام مشتری را وارد کنید:', _lg),
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
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
    if state != STATE_SEARCH_TX:
        return False
    text = update.message.text.strip()
    if text in {"بازگشت", "❌ لغو", "لغو", "/cancel"}:
        context.user_data.pop(UD_STATE, None)
        from AgentBot.keyboards import settings_menu_keyboard
        await update.message.reply_text(i18n.t("ag_settings_title", agent_lang(context)), reply_markup=settings_menu_keyboard(lang=agent_lang(context)), parse_mode="HTML")
        return True

    if text.isdigit():
        pid = int(text)
        pay = get_customer_payment_detail(pid)
        context.user_data.pop(UD_STATE, None)
        if not pay or int(pay.get("agent_id") or 0) != agent_id:
            await update.message.reply_text(f"{i18n.t('❌ تراکنشی با شناسه ', _lg)}{pid}{i18n.t(' یافت نشد.', _lg)}", reply_markup=tx_menu_keyboard(), parse_mode="HTML")
            return True
        await _send_payment_detail(context, agent_id, update.message.chat_id, pay)
        return True

    payments = search_customer_payments(agent_id, text, limit=15)
    context.user_data.pop(UD_STATE, None)
    if not payments:
        await update.message.reply_text(
            i18n.t('❌ هیچ تراکنشی با این مشخصات پیدا نشد.', _lg),
            reply_markup=tx_menu_keyboard(), parse_mode="HTML",
        )
        return True
    lines = [f"{i18n.t('🔍 <b>نتایج جستجو برای "', _lg)}{_escape(text)}\"</b> ({len(payments)}):\n"]
    for p in payments:
        lines.append(
            f"{_status_icon(p.get('status', ''))} #{p.get('id')} • {_escape(_payment_customer_name(p))} • {_fmt_toman(p.get('amount', 0))}{i18n.t(' تومان', _lg)}"
        )
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=tx_search_results_keyboard(payments),
        parse_mode="HTML",
    )
    return True
