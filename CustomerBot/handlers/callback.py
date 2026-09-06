import asyncio
import time
import random
from html import escape
from typing import Optional

from telegram import Update, InlineKeyboardMarkup
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from Shared import i18n

from CustomerBot.constants import (
    UD_STATE, UD_BUY_GB, UD_BUY_MONTHS, UD_BUY_SERVER_ID, UD_BUY_PLAN_ID,
    UD_TICKET_QUESTION, UD_TICKET_MODE,
    STATE_RECEIPT_WAITING, STATE_CARD_LAST4, STATE_TICKET_WAITING_TEXT, STATE_TICKET_WAITING_TITLE,
    STATE_TICKET_WAITING_PHOTO, STATE_TICKET_CONFIRM,
    STATE_TRIAL_WAITING_NAME,
    STATE_AGENT_MSG_WAITING,
    CB_BUY_LOC, CB_BUY_CAT, CB_BUY_PLAN, CB_BUY_CONFIRM,
    CB_BUY_CONFIRM_DYN, CB_BUY_PAY_DIRECT,
    CB_BUY_BACK_MAIN, CB_BUY_EXIT_MAIN, CB_BUY_MIXED_FIXED, CB_BUY_MIXED_DYN,
    CB_WIZ_GB_INC, CB_WIZ_GB_DEC, CB_WIZ_MONTH_INC, CB_WIZ_MONTH_DEC,
    CB_WIZ_SHOW_FIXED,
    CB_STATUS_LIST, CB_STATUS_CONFIGS, CB_STATUS_RENEW, CB_STATUS_RENAME,
    CB_STATUS_REPLACE_LINK, CB_STATUS_DETACH, CB_STATUS_DIRECT, CB_STATUS_SUB_LINK,
    CB_STATUS_AUTO_SUB, CB_STATUS_SUB_B64, CB_STATUS_MULTI, CB_STATUS_MULTI_B64,
    CB_STATUS_DIRECTCFG, CB_STATUS_REFRESH, CB_STATUS_GUIDE, CB_STATUS_MENU,
    CB_STATUS_LIST_BACK,
    CB_RENEW_SVC, CB_RENEW_BACK,
    CB_PAY_RECEIPT_DONE, CB_PAY_CANCEL,
    CB_SUPPORT_FAQ, CB_SUPPORT_MY, CB_SUPPORT_NEW, CB_SUPPORT_VIEW,
    CB_SUPPORT_REPLY, CB_SUPPORT_CLOSE, CB_SUPPORT_BACK_MAIN, CB_SUPPORT_MENU,
    CB_TRIAL_LOC, CB_TRIAL_BACK,
    CB_GUIDE_ANDROID, CB_GUIDE_IOS, CB_GUIDE_WINDOWS, CB_GUIDE_MAC, CB_GUIDE_LINUX,
    CB_GUIDE_BACK, CB_FORCEJOIN_CHECK,
    CB_AGENT_MSG_REPLY,
    BTN_PAY_DONE, BTN_BACK,
    ACTION_COOLDOWN,
)
from CustomerBot.database import (
    get_buy_renew_settings, get_text_settings, get_subs_settings, get_faq_text,
    get_localized_text, get_localized_forcejoin_guide,
    get_payment_settings, get_marketing_settings, get_trial_spec_settings,
    get_force_join_settings, get_user, create_order, create_payment,
    get_user_tickets, get_ticket, get_ticket_messages,
    create_ticket, add_ticket_message, update_ticket_status, get_pending_payments,
    update_payment_status, get_payment_by_tx_code, set_got_free_trial,
    upsert_user,
    get_tx_plans_settings,
)
from Shared.agent_db import (
    upsert_customer, get_customer_by_telegram_id, get_services_by_customer,
    get_service_by_id, create_service, add_service_node,
    set_service_active, calculate_wholesale_price,
    update_service, make_service_note,
)
from Shared.database import (
    get_servers, get_main_servers, get_server_by_id, get_plan_categories, get_plans, get_plan,
    get_plan_dynamic_settings,
)
from AgentBot.database import (
    get_fixed_plans, get_fixed_plan, get_setting,
)
from CustomerBot.keyboards import (
    main_menu_keyboard, cancel_keyboard, location_keyboard, trial_location_keyboard,
    category_keyboard, plans_keyboard, confirm_buy_keyboard, selected_plan_keyboard,
    buy_wizard_keyboard, mixed_buy_keyboard, mixed_mode_keyboard,
    renew_wizard_keyboard, renew_payment_keyboard,
    confirm_payment_keyboard, confirm_payment_inline_keyboard,
    cancel_inline_keyboard, subscription_status_keyboard,
    replace_subscription_link_confirm_keyboard,
    subscription_configs_keyboard, subscription_links_keyboard,
    services_list_keyboard, renew_services_keyboard, support_panel_keyboard,
    receipt_cancel_keyboard, receipt_back_inline_keyboard,
    ticket_skip_screenshot_keyboard, ticket_confirm_keyboard,
    user_tickets_list_keyboard, user_ticket_detail_keyboard,
    force_join_keyboard, guide_os_keyboard,
)
from telegram import ReplyKeyboardRemove
from CustomerBot.utils.helpers import (
    is_rate_limited, format_price, escape_markdown, safe_int, safe_float,
)
from CustomerBot.services import (
    build_subscription_status_text, get_service_node_base_urls,
    get_service_panel_targets, collect_all_direct_configs_for_service,
    get_or_create_bot_sub_links,
    sync_service_status_from_panels, regenerate_service_uuid,
    service_is_renewable, renew_not_allowed_text, service_is_renewable_live,
    _resolve_live_server_title,
)
from Shared.qr_utils import make_qr_image

_plans_storage = None


def _ikb(rows):
    return InlineKeyboardMarkup(rows)


async def _edit_or_reply(msg, text: str, **kwargs):
    """همان پیام را ویرایش می‌کند تا جریان تمیز بماند؛ اگر ممکن نبود پیام جدید می‌فرستد."""
    try:
        return await msg.edit_text(text, **kwargs)
    except Exception:
        return await msg.reply_text(text, **kwargs)


def _active_discount_simple(settings) -> bool:
    """تخفیف حجمی ساده را با احترام به تایمر و حالت ذخیره‌شده فعال محسوب می‌کند."""
    global _plans_storage
    if _plans_storage is None:
        from Shared import plans_storage as _plans_storage
    return _plans_storage.is_simple_discount_active(settings) if settings else False


def _normalized_discount_tiers(settings) -> list:
    global _plans_storage
    if _plans_storage is None:
        from Shared import plans_storage as _plans_storage
    return _plans_storage.normalize_discount_tiers(settings.get("discount_tiers", [])) if settings else []


def _agent_dyn_settings(agent_id: int, server_id: int = 0) -> dict:
    """قیمت‌گذاری پویای خودِ نماینده را می‌خواند؛ اگر تنظیم نشده به سراسری برمی‌گردد."""
    try:
        s = get_setting(agent_id, "dynamic_plan_settings", {}) or {}
        if isinstance(s, dict) and s:
            return s
    except Exception:
        pass
    from Shared.database import get_plan_dynamic_settings as _global_dyn
    return _global_dyn(server_id)


def _dyn_month_limits(dyn: dict) -> tuple[int, int, int]:
    """حداقل/حداکثر ماه و گام از تنظیمات پویا (پشتیبانی از min_month و min_months)."""
    try:
        min_month = int(dyn.get("min_month", dyn.get("min_months", 1)) or 1)
    except (TypeError, ValueError):
        min_month = 1
    try:
        max_month = int(dyn.get("max_month", dyn.get("max_months", 12)) or 12)
    except (TypeError, ValueError):
        max_month = 12
    try:
        step_month = int(dyn.get("step_month", 1) or 1)
    except (TypeError, ValueError):
        step_month = 1
    min_month = max(1, min_month)
    max_month = max(min_month, max_month)
    step_month = max(1, step_month)
    return min_month, max_month, step_month


def _clamp_months(months, min_month: int, max_month: int) -> int:
    return max(min_month, min(months, max_month))


WIZARD_TTL_SECONDS = 600  # 10 دقیقه

UD_WIZARD_START_TS = "wizard_start_ts"


def _wizard_expired(context) -> bool:
    """اگر زمان شروع ویزارد گذشته و از سقف TTL گذشته باشد True برمی‌گرداند."""
    start_ts = context.user_data.get(UD_WIZARD_START_TS)
    if not start_ts:
        return False
    try:
        return (time.time() - float(start_ts)) >= WIZARD_TTL_SECONDS
    except (TypeError, ValueError):
        return False


def _start_buy_wizard(context) -> None:
    """زمان شروع ویزارد خرید را ثبت می‌کند (برای سنجش انقضای نشست)."""
    context.user_data[UD_WIZARD_START_TS] = time.time()


def _reset_buy_wizard(context) -> None:
    """تمام داده‌های ویزارد خرید/تمدید را پاک می‌کند تا از نو شروع شود."""
    for key in (UD_WIZARD_START_TS, UD_BUY_GB, UD_BUY_MONTHS, UD_BUY_SERVER_ID, UD_BUY_PLAN_ID):
        context.user_data.pop(key, None)


def _calc_dynamic_price(gb, months, dyn_settings) -> tuple[int, int]:
    settings = dyn_settings or {}
    gb_val = max(0, safe_int(gb, 0))
    months_val = max(0, safe_int(months, 0))
    price_per_gb = max(0, safe_int(settings.get("price_per_gb"), 0))
    price_per_month = max(0, safe_int(settings.get("price_per_month"), 0))
    base_price = (gb_val * price_per_gb) + (months_val * price_per_month)

    discount_step_gb = max(0, safe_int(settings.get("discount_step_gb"), 0))
    discount_percent_step = max(0, safe_int(settings.get("discount_percent_step"), 0))
    discount_percent_max = max(0, safe_int(settings.get("discount_percent_max"), 0))
    discount_tiered_enabled = bool(settings.get("discount_tiered_enabled", False))

    off_percent = 0
    tiers = _normalized_discount_tiers(settings)
    if discount_tiered_enabled and tiers:
        tiered_off = 0
        for tier in tiers:
            try:
                if gb_val >= int(tier.get("gb", 0)):
                    tiered_off = int(tier.get("percent", 0))
                else:
                    break
            except Exception:
                continue
        off_percent = max(off_percent, max(0, min(tiered_off, 100)))

    discount_simple_enabled = _active_discount_simple(settings)
    if discount_simple_enabled and discount_step_gb > 0 and discount_percent_step > 0 and gb_val >= discount_step_gb:
        stages = gb_val // discount_step_gb
        simple_off = stages * discount_percent_step
        if discount_percent_max > 0:
            simple_off = min(simple_off, discount_percent_max)
        off_percent = max(off_percent, max(0, min(simple_off, 100)))

    final_price = int(round(base_price * (100 - off_percent) / 100))
    return max(0, final_price), off_percent


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    data = query.data
    user = query.from_user
    if not user:
        return

    agent_id = context.bot_data.get("agent_id", 0)
    if not agent_id:
        await query.answer(i18n.t("cb_config_error", "fa"), show_alert=True)
        return

    # دکمه‌های ویزارد (+/-) را نباید با rate limit قوی بلاک کرد؛ کاربر باید سریع بزند
    is_wizard_tap = data.startswith("wiz:") or data.startswith("rwiz:")
    if is_rate_limited(
        f"cb_{user.id}_{data}",
        cooldown=0.25 if is_wizard_tap else ACTION_COOLDOWN,
    ):
        await query.answer(i18n.t("rate_limit_wait", i18n.get_customer_lang(agent_id, user.id)))
        return

    try:
        await query.answer()
    except Exception:
        pass

    # ---- تغییر زبان رابط کاربری (چندزبانه) ----
    if data.startswith("lang:set:"):
        new_lang = data.split(":")[2].strip().lower()
        if not i18n.is_supported(new_lang):
            new_lang = "fa"
        try:
            from CustomerBot.database import set_customer_language
            set_customer_language(agent_id, user.id, new_lang)
        except Exception as e:
            logger.warning("set_customer_language failed user=%s: %s", user.id, e)
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=i18n.t("lang_changed", new_lang, lang_name=i18n.lang_display_name(new_lang)),
                reply_markup=main_menu_keyboard(lang=new_lang),
            )
        except Exception:
            pass
        return

    # ---- Force Join ----
    if data == CB_FORCEJOIN_CHECK:
        await _handle_force_join_check(query, context, agent_id, user)

    # ---- Guide ----
    elif data.startswith("guide:"):
        await _handle_guide(query, context, agent_id, data)

    # ---- Support ----
    elif data.startswith("support:"):
        await _handle_support(query, context, agent_id, user, data)

    # ---- Direct agent message reply ----
    elif data == CB_AGENT_MSG_REPLY:
        _lg = i18n.get_customer_lang(agent_id, user.id)
        context.user_data[UD_STATE] = STATE_AGENT_MSG_WAITING
        try:
            await query.message.edit_text(
                i18n.t("agent_msg_edit_prompt", _lg),
                reply_markup=None,
            )
        except Exception:
            pass
        await query.message.reply_text(
            i18n.t("agent_msg_prompt", _lg),
        )

    # ---- Status ----
    elif data.startswith("status:"):
        await _handle_status(query, context, agent_id, user, data)

    # ---- Renew ----
    elif data.startswith("renew:") or data.startswith("rwiz:"):
        await _handle_renew(query, context, agent_id, user, data)

    # ---- Pay ----
    elif data.startswith("pay:"):
        await _handle_pay(query, context, agent_id, user, data)

    # ---- Trial ----
    elif data.startswith("trial:"):
        await _handle_trial(query, context, agent_id, user, data)

    elif data == CB_BUY_EXIT_MAIN:
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop("wallet_card_amount", None)
        await _back_to_main_menu(query.message)

    # ---- Buy ----
    elif data.startswith("buy:") or data.startswith("wiz:"):
        await _handle_buy(query, context, agent_id, user, data)


# ==================== HANDLERS ====================

async def _back_to_main_menu(msg, text: str = "", lang: str = "fa"):
    """Callback messages cannot be edited with ReplyKeyboardMarkup, so send it separately."""
    if not text:
        text = i18n.t("main_menu_title", lang)
    try:
        await msg.edit_text(text)
    except Exception:
        pass
    try:
        await msg.reply_text(i18n.t("welcome_back", lang), reply_markup=main_menu_keyboard(lang=lang))
    except Exception:
        pass


async def _handle_force_join_check(query, context, agent_id, user):
    _lg = i18n.get_customer_lang(agent_id, user.id)
    fjs = get_force_join_settings(agent_id)
    if not fjs.get("enabled") or not fjs.get("channel_username"):
        await _back_to_main_menu(query.message, i18n.t("member_confirmed", _lg), lang=_lg)
        return
    ch = str(fjs["channel_username"])
    chat_target = ch if ch.lstrip("-").isdigit() else f"@{ch}"
    link = fjs.get("channel_link") or (f"https://t.me/{ch}" if not ch.lstrip("-").isdigit() else "")
    allowed_statuses = {"member", "administrator", "creator", "owner"}
    try:
        member = await context.bot.get_chat_member(chat_target, user.id)
        status = str(getattr(member, "status", "")).lower()
        if status not in allowed_statuses:
            await query.edit_message_text(
                get_localized_forcejoin_guide(agent_id, _lg) or i18n.t("not_member_yet", _lg),
                reply_markup=force_join_keyboard(link, lang=_lg),
            )
            return
    except Exception:
        await query.edit_message_text(
            get_localized_forcejoin_guide(agent_id, _lg) or i18n.t("not_member_yet", _lg),
            reply_markup=force_join_keyboard(link, lang=_lg),
        )
        return
    try:
        await query.message.delete()
    except Exception:
        try:
            await query.edit_message_text(i18n.t("member_confirmed", _lg))
        except Exception:
            pass
    try:
        await query.message.reply_text(i18n.t("member_confirmed", _lg), reply_markup=main_menu_keyboard(lang=_lg))
    except Exception:
        pass


async def _handle_guide(query, context, agent_id, data, lang: str = "fa"):
    text_settings = get_text_settings(agent_id)
    parts = data.split(":", 2)
    if len(parts) < 2:
        return
    action = parts[1]
    back_token = parts[2] if len(parts) > 2 else "m"

    _gl = i18n.get_customer_lang(agent_id, user.id)
    guide_map = {
        "android": get_localized_text(agent_id, "guide_android_text", _gl),
        "ios": get_localized_text(agent_id, "guide_ios_text", _gl),
        "windows": get_localized_text(agent_id, "guide_windows_text", _gl),
        "mac": get_localized_text(agent_id, "guide_mac_text", _gl),
        "linux": get_localized_text(agent_id, "guide_linux_text", _gl),
    }

    if action == "back":
        if back_token == "m":
            await _back_to_main_menu(query.message, get_localized_text(agent_id, "guide_text", lang), lang=lang)
        elif back_token.startswith("s:"):
            try:
                svc_id = int(back_token.split(":", 1)[1])
            except (IndexError, ValueError):
                svc_id = 0
            svc = get_service_by_id(svc_id)
            if svc:
                await query.message.edit_text(
                    _build_service_status_text(svc, lang=lang),
                    parse_mode="Markdown",
                    reply_markup=subscription_status_keyboard(svc_id, lang=lang),
                )
        return

    if action in guide_map and guide_map[action]:
        await query.edit_message_text(
            guide_map[action],
            reply_markup=guide_os_keyboard(back_token, lang=lang),
        )


async def _handle_support(query, context, agent_id, user, data):
    text_settings = get_text_settings(agent_id)
    msg = query.message
    _lg = i18n.get_customer_lang(agent_id, user.id)

    if data == CB_SUPPORT_FAQ:
        faq = get_faq_text(agent_id, _lg)
        await msg.edit_text(faq, reply_markup=support_panel_keyboard(_lg))

    elif data.startswith(CB_SUPPORT_MY):
        page = int(data.split(":")[-1]) if data.split(":")[-1].isdigit() else 1
        page_size = 9
        tickets = get_user_tickets(agent_id, user.id)
        total = len(tickets)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        start = (page - 1) * page_size
        page_tickets = tickets[start:start + page_size]
        if not page_tickets:
            await msg.edit_text(i18n.t("no_tickets", _lg), reply_markup=support_panel_keyboard(lang=_lg))
            return
        await msg.edit_text(
            i18n.t("my_tickets_page", _lg, p=page, tp=total_pages),
            reply_markup=user_tickets_list_keyboard(page_tickets, page, total_pages, lang=_lg),
        )

    elif data == CB_SUPPORT_NEW:
        context.user_data[UD_STATE] = STATE_TICKET_WAITING_TITLE
        context.user_data[UD_TICKET_MODE] = "new"
        await msg.edit_text(
            i18n.t("new_ticket_prompt", _lg),
            parse_mode="HTML",
        )
        await msg.reply_text(
            i18n.t("ticket_send_confirm", _lg),
            reply_markup=cancel_keyboard(lang=_lg),
        )

    elif data.startswith(CB_SUPPORT_VIEW):
        parts = data.split(":")
        if len(parts) < 3:
            return
        code = int(parts[2])
        ticket = get_ticket(agent_id, code)
        if not ticket:
            await msg.edit_text(i18n.t("ticket_not_found_local", _lg), reply_markup=support_panel_keyboard(_lg))
            return
        is_closed = ticket["status"] == "closed"
        can_reply = not is_closed
        messages = get_ticket_messages(agent_id, code)
        status_map = {"pending": i18n.t("status_pending", _lg), "open": i18n.t("status_open", _lg), "closed": i18n.t("status_closed", _lg)}
        status_fa = status_map.get(ticket.get("status", ""), ticket.get("status", ""))
        title = ticket.get("title", "") or ticket.get("question", "")[:50] or i18n.t("no_subject_label", _lg)
        text = (
            f"📩 <b>{i18n.t('ticket_id_label', _lg)}#{code}</b>\n"
            f"📋 {i18n.t('ticket_subject_label', _lg)}{title}\n"
            f"📅 {str(ticket.get('created_at', ''))[:16]}\n"
            f"📌 {i18n.t('ticket_status_label', _lg)}{status_fa}\n\n"
            f"{i18n.t('msgs_header', _lg)}\n"
        )
        if not messages:
            text += i18n.t("no_messages_yet", _lg)
        else:
            for m in messages:
                sender = "👤 " + i18n.t("you_label", _lg) if m.get("sender_type") == "user" else f"🤖 {m.get('sender_name') or i18n.t('support_label', _lg)}"
                msg_text = m.get("message_text", "")
                has_photo = i18n.t("photo_label", _lg) if m.get("photo_file_id") else ""
                ts = str(m.get("created_at", ""))[:16]
                text += f"\n{sender} ({ts}):\n{msg_text}\n{has_photo}\n"
        await msg.edit_text(text, reply_markup=user_ticket_detail_keyboard(code, can_reply, is_closed, lang=_lg), parse_mode="HTML")
        for m in messages:
            photo_fid = m.get("photo_file_id", "")
            if photo_fid:
                sender = i18n.t("you_label", _lg) if m.get("sender_type") == "user" else m.get("sender_name") or i18n.t("support_label", _lg)
                caption = f"📷 {i18n.t('ticket_photo_of_label', _lg, c=code, s=sender)}\n{m.get('message_text', '') or ''}"
                try:
                    await msg.reply_photo(photo=photo_fid, caption=caption[:1024])
                except Exception:
                    pass

    elif data.startswith("support:reply:"):
        _reply_sub = data.split(":")[2] if len(data.split(":")) > 2 else ""
        if _reply_sub in ("skip", "send", "edit", "cancel"):
            if _reply_sub == "skip":
                pending = context.user_data.get("pending_reply", {})
                if not pending or not pending.get("ticket_code"):
                    await query.answer(i18n.t("reply_info_missing", _lg), show_alert=True)
                    return
                pending["photo_file_id"] = ""
                context.user_data["pending_reply"] = pending
                context.user_data[UD_STATE] = STATE_TICKET_CONFIRM
                preview_text = i18n.t("ticket_reply_confirm", _lg, t=pending.get('reply_text', ''))
                try:
                    await msg.edit_text(preview_text, reply_markup=ticket_confirm_keyboard("reply", lang=_lg), parse_mode="HTML")
                except Exception:
                    await msg.reply_text(preview_text, reply_markup=ticket_confirm_keyboard("reply", lang=_lg), parse_mode="HTML")
            elif _reply_sub == "edit":
                pending = context.user_data.get("pending_reply", {})
                code = int((pending or {}).get("ticket_code") or 0)
                context.user_data[UD_STATE] = STATE_TICKET_WAITING_TEXT
                context.user_data[UD_TICKET_MODE] = f"reply:{code}"
                context.user_data.pop("pending_reply", None)
                try:
                    await msg.edit_text(i18n.t("reply_prompt_full", _lg), parse_mode="HTML")
                except Exception:
                    pass
                await msg.reply_text(i18n.t("reply_prompt", _lg), reply_markup=cancel_keyboard(lang=_lg))
            elif _reply_sub == "cancel":
                context.user_data.pop(UD_STATE, None)
                context.user_data.pop(UD_TICKET_MODE, None)
                context.user_data.pop("pending_reply", None)
                await msg.edit_text(i18n.t("reply_cancelled", _lg), reply_markup=support_panel_keyboard(_lg))
            else:  # send
                from CustomerBot.handlers.receipt import _build_ticket_detail_text, _notify_agent_ticket_reply
                pending = context.user_data.get("pending_reply", {})
                if not pending or not pending.get("ticket_code"):
                    await query.answer(i18n.t("reply_info_missing", _lg), show_alert=True)
                    return
                code = int(pending["ticket_code"])
                reply_text = str(pending.get("reply_text") or "").strip()
                photo_fid = str(pending.get("photo_file_id") or "").strip()
                ticket = get_ticket(agent_id, code)
                if not ticket:
                    await query.answer(i18n.t("ticket_not_found_local", _lg), show_alert=True)
                    return
                if str(ticket.get("status") or "").strip().lower() == "closed":
                    await query.answer(i18n.t("ticket_closed_local", _lg), show_alert=True)
                    return
                add_ticket_message(
                    agent_id=agent_id,
                    ticket_code=code,
                    sender_type="user",
                    sender_name=user.full_name or user.username or i18n.t("user_label", _lg),
                    message_text=reply_text or i18n.t("photo_label", _lg),
                    photo_file_id=photo_fid,
                )
                try:
                    update_ticket_status(agent_id, code, "open")
                except Exception:
                    pass
                try:
                    fresh = get_ticket(agent_id, code)
                    if fresh:
                        await _notify_agent_ticket_reply(context, agent_id, fresh, reply_text, photo_fid)
                except Exception:
                    pass
                context.user_data.pop(UD_STATE, None)
                context.user_data.pop(UD_TICKET_MODE, None)
                context.user_data.pop("pending_reply", None)
                try:
                    fresh = get_ticket(agent_id, code)
                    msgs = get_ticket_messages(agent_id, code) if fresh else []
                    detail_text = _build_ticket_detail_text(fresh, msgs)
                    out_text = i18n.t("ticket_reply_sent", _lg) + detail_text
                    await msg.edit_text(out_text, reply_markup=user_ticket_detail_keyboard(code, can_reply=True, is_closed=False, lang=_lg), parse_mode="HTML")
                    for m in msgs:
                        pfid = m.get("photo_file_id", "")
                        if pfid:
                            try:
                                await msg.reply_photo(photo=pfid, caption=i18n.t("photo_ticket_label", _lg, c=code))
                            except Exception:
                                pass
                except Exception:
                    await msg.edit_text(i18n.t("ticket_reply_sent", _lg).strip(), reply_markup=user_ticket_detail_keyboard(code, can_reply=True, is_closed=False, lang=_lg))
            return
        else:
            try:
                code = int(_reply_sub)
            except (TypeError, ValueError):
                await query.answer(i18n.t("invalid_request", _lg), show_alert=True)
                return
            context.user_data[UD_STATE] = STATE_TICKET_WAITING_TEXT
            context.user_data[UD_TICKET_MODE] = f"reply:{code}"
            await msg.edit_text(
                i18n.t("ticket_reply_header", _lg),
                parse_mode="HTML",
            )
            await msg.reply_text(i18n.t("reply_prompt", _lg), reply_markup=cancel_keyboard(lang=_lg))
            return

    elif data.startswith(CB_SUPPORT_CLOSE):
        code = int(data.split(":")[-1])
        update_ticket_status(agent_id, code, "closed")
        await msg.edit_text(i18n.t("ticket_closed_ok", _lg), reply_markup=support_panel_keyboard(_lg))

    elif data == "support:new:skip":
        pending = context.user_data.get("pending_ticket", {})
        if not pending:
            await query.answer(i18n.t("ticket_info_missing", _lg), show_alert=True)
            return
        context.user_data[UD_STATE] = STATE_TICKET_CONFIRM
        from CustomerBot.handlers.receipt import _format_ticket_confirm_text
        await msg.edit_text(_format_ticket_confirm_text(pending, lang=_lg), reply_markup=ticket_confirm_keyboard("new", lang=_lg), parse_mode="HTML")

    elif data == "support:new:send":
        pending = context.user_data.get("pending_ticket", {})
        if not pending:
            await query.answer(i18n.t("ticket_info_missing", _lg), show_alert=True)
            return
        from CustomerBot.handlers.receipt import _notify_agent_new_ticket
        title = pending.get("title") or i18n.t("no_subject_label", _lg)
        question = pending.get("question", "")
        photo_fid = pending.get("photo_file_id", "")
        ticket = create_ticket(
            agent_id=agent_id,
            telegram_id=user.id,
            username=user.username or "",
            full_name=user.full_name or "",
            question=question,
            title=title,
        )
        if ticket:
            add_ticket_message(
                agent_id=agent_id,
                ticket_code=ticket["ticket_code"],
                sender_type="user",
                sender_name=user.full_name or user.username or i18n.t("user_label", _lg),
                message_text=question,
                photo_file_id=photo_fid,
            )
            await _notify_agent_new_ticket(context, agent_id, ticket, question, photo_fid)
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_TICKET_MODE, None)
            context.user_data.pop(UD_TICKET_QUESTION, None)
            context.user_data.pop("pending_ticket", None)
            from CustomerBot.handlers.receipt import _build_ticket_detail_text, build_ticket_screenshot_links
            try:
                fresh = get_ticket(agent_id, ticket["ticket_code"])
                msgs = get_ticket_messages(agent_id, ticket["ticket_code"])
                links = await build_ticket_screenshot_links(context, ticket["ticket_code"], msgs)
                detail_text = _build_ticket_detail_text(fresh, msgs, screenshot_links=links, lang=_lg)
            except Exception:
                detail_text = ""
            header = i18n.t("ticket_created_header", _lg)
            out_text = header + detail_text
            await msg.edit_text(
                out_text,
                reply_markup=user_ticket_detail_keyboard(ticket["ticket_code"], can_reply=True, is_closed=False, lang=_lg),
                parse_mode="HTML",
            )
        else:
            await query.answer(i18n.t("ticket_create_error", _lg), show_alert=True)

    elif data == "support:new:edit":
        context.user_data[UD_STATE] = STATE_TICKET_WAITING_TITLE
        context.user_data[UD_TICKET_MODE] = "new"
        context.user_data.pop("pending_ticket", None)
        await msg.edit_text(i18n.t("ticket_title_prompt", i18n.get_customer_lang(agent_id, user.id)), reply_markup=cancel_keyboard(lang=_lg))

    elif data == "support:new:cancel":
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_TICKET_MODE, None)
        context.user_data.pop(UD_TICKET_QUESTION, None)
        context.user_data.pop("pending_ticket", None)
        await msg.edit_text(i18n.t("ticket_cancelled", _lg), reply_markup=support_panel_keyboard(_lg))

    elif data == CB_SUPPORT_BACK_MAIN or data == CB_SUPPORT_MENU:
        await msg.edit_text(
            get_localized_text(agent_id, "ticket_panel_text", _lg),
            reply_markup=support_panel_keyboard(_lg),
        )


async def _show_subscription_status(msg, agent_id, svc_id, lang: str = "fa"):
    """نمایش «📄اطلاعات اشتراک شما» + کیبورد وضعیت (مطابق ربات کاربران)"""
    subs_settings = get_subs_settings(agent_id)
    br = get_buy_renew_settings(agent_id)
    svc = get_service_by_id(svc_id)
    if not svc:
        await msg.edit_text(i18n.t("service_not_found_local", lang))
        return None
    show_detach = bool(svc.get("comment") == "connected")
    svc_text = build_subscription_status_text(svc, subs_settings, br, lang=lang)
    kb = subscription_status_keyboard(
        svc_id,
        show_direct_config=subs_settings.get("show_direct_config", True),
        show_sub_link=subs_settings.get("show_sub_link", True),
        show_detach=show_detach,
        lang=lang,
    )
    try:
        await msg.edit_text(svc_text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        try:
            await msg.reply_text(svc_text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
    return svc


async def _send_service_direct_configs(msg, svc, lang: str = "fa"):
    """کانفیگ‌های مستقیم: استخراج از لینک اشتراک همه نودها (بدون API پنل).

    فقط از sub-link واقعی هر نود استخراج می‌شود تا همان تنظیمات
    «شامل در اشتراک» که در پنل تعریف شده رعایت شود؛ API پنل استفاده
    نمی‌شود چون همه کانفیگ‌ها (حتی غیرفعال/مخفی) را برمی‌گرداند.
    """
    # دریافت لینک‌ها در thread جدا تا event loop ربات بلاک نشود
    links = await asyncio.to_thread(collect_all_direct_configs_for_service, svc)
    source_hint = ""
    base_urls = get_service_node_base_urls(svc)
    fallback_base = base_urls[0] if base_urls else ""
    is_xui_fallback = "/sub/" in str(fallback_base or "")

    # X-UI standalone HTTP اغلب خالی است؛ حتی اگر links (هیدیفای) پر باشد، X-UI را از API تکمیل کن
    needs_xui_supplement = any("/sub/" in str(b or "") for b in (get_service_node_base_urls(svc) or []))
    if needs_xui_supplement:
        try:
            from Shared.sub_links import get_service_panel_targets
            from Shared.sub_aggregator import _fetch_lines_from_admin_api, _is_config_line, _is_panel_status_config_line
            from Shared import xui_api
            seen_api = set(str(l).strip() for l in links or [])
            api_links = list(links or [])
            supplemented = False
            for srv, uuid, _un in get_service_panel_targets(svc):
                if not srv or not uuid:
                    continue
                # فقط نودهای X-UI که HTTP شان خالی مانده را از API بگیر
                try:
                    is_xui_node = xui_api.is_xui_server(srv)
                except Exception:
                    is_xui_node = False
                if not is_xui_node:
                    continue
                try:
                    # در thread جدا اجرا می‌شود (event loop بلاک نشود)؛ قفل‌های
                    # xui حالا loop-scoped هستند پس خطای cross-loop رخ نمی‌دهد
                    api_lines = await asyncio.to_thread(_fetch_lines_from_admin_api, srv, uuid)
                except Exception:
                    api_lines = []
                added_here = 0
                for ln in api_lines or []:
                    raw = str(ln or "").strip()
                    if not raw or raw in seen_api:
                        continue
                    if not _is_config_line(raw) or _is_panel_status_config_line(raw):
                        continue
                    seen_api.add(raw)
                    api_links.append(raw)
                    added_here += 1
                if added_here:
                    supplemented = True
            if supplemented:
                links = api_links
                if not source_hint:
                    source_hint = i18n.t("xui_api_supplement", lang)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("X-UI supplement failed: %s", e)
    if not links and is_xui_fallback:
        # قدیمی: اگر هنوز خالی بود، همان فال‌بک کلی
        try:
            from Shared.sub_links import get_service_panel_targets as _g2
            from Shared.sub_aggregator import _fetch_lines_from_admin_api as _f2, _is_config_line as _ic2, _is_panel_status_config_line as _ipc2
            seen_api = set()
            api_links = []
            for srv, uuid, _un in _g2(svc):
                try:
                    api_lines = _f2(srv, uuid)
                except Exception:
                    api_lines = []
                for ln in api_lines or []:
                    raw = str(ln or "").strip()
                    if not raw or raw in seen_api:
                        continue
                    if not _ic2(raw) or _ipc2(raw):
                        continue
                    seen_api.add(raw)
                    api_links.append(raw)
            if api_links:
                links = api_links
                source_hint = i18n.t("xui_api_fallback", lang)
        except Exception:
            pass
        if not links:
            msg_text = i18n.t("direct_config_extract_failed", lang)
            if fallback_base:
                if is_xui_fallback:
                    msg_text += i18n.t("can_use_sub_link", lang, u=fallback_base)
                else:
                    msg_text += i18n.t("can_use_sub_link", lang, u=f"{fallback_base}/all.txt")
            await msg.reply_text(msg_text, disable_web_page_preview=True)
            return

    server_title = _resolve_live_server_title(svc, default="")
    header = i18n.t("direct_configs_title", lang)
    if server_title:
        header = f"{header} | {server_title}"
    clean_links = [str(x).strip() for x in links if str(x).strip()]

    all_links_text = "\n".join(clean_links)
    one_block_text = (
        f"{source_hint}{header}\n"
        f"{i18n.t('direct_configs_hint', lang)}\n"
        f"<pre><code class=\"language-shell\">{escape(all_links_text)}</code></pre>"
    )
    if len(one_block_text) <= 3900:
        await msg.reply_text(one_block_text, parse_mode="HTML", disable_web_page_preview=True)
        return

    max_payload = 2800
    parts_list = []
    cur = []
    cur_len = 0
    for link in clean_links:
        add = len(link) + 1
        if cur and (cur_len + add > max_payload):
            parts_list.append(cur)
            cur = [link]
            cur_len = add
        else:
            cur.append(link)
            cur_len += add
    if cur:
        parts_list.append(cur)
    for idx, chunk in enumerate(parts_list, start=1):
        part_header = header if len(parts_list) == 1 else f"{header} ({idx}/{len(parts_list)})"
        part_text = (
            f"{source_hint if idx == 1 else ''}{part_header}\n"
            f"{i18n.t('direct_configs_hint_paged', lang)}\n"
            f"<pre><code class=\"language-shell\">{escape(chr(10).join(chunk))}</code></pre>"
        )
        await msg.reply_text(part_text, parse_mode="HTML", disable_web_page_preview=True)


async def _send_subscription_link_with_qr(query, agent_id, svc, data, lang: str = "fa"):
    """ارسال لینک‌های اشتراک همراه با QR بارکد (مثل ربات کاربران)"""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    svc_id = int(svc.get("id") or 0)
    ss = get_subs_settings(agent_id)
    msg = query.message

    base_urls = get_service_node_base_urls(svc)
    if not base_urls:
        await msg.edit_text(i18n.t("no_config_link", lang))
        return
    base_url = base_urls[0]

    config_items = []
    if action == "sub_link":
        if not ss.get("show_sub_link", True):
            await msg.edit_text(i18n.t("sub_link_disabled", lang))
            return
        config_items.append((i18n.t("label_sub_link", lang), f"{base_url}/all.txt"))
    elif action == "auto_sub":
        if not ss.get("show_auto_sub_link", False):
            await msg.edit_text(i18n.t("auto_sub_disabled", lang))
            return
        config_items.append((i18n.t("auto_sub_link_label", lang), f"{base_url}/sub/?asn=unknown"))
    elif action == "sub_b64":
        if not ss.get("show_sub_link_b64", False):
            await msg.edit_text(i18n.t("sub_b64_disabled", lang))
            return
        config_items.append((i18n.t("sub_b64_label", lang), f"{base_url}/all.txt?base64=1"))
    elif action == "multi":
        if not ss.get("show_multi_server", False):
            await msg.edit_text(i18n.t("multi_disabled", lang))
            return
        managed_link, _ = get_or_create_bot_sub_links(svc)
        config_items.append((i18n.t("config_smart", lang) + ":", managed_link))
    elif action == "multi_b64":
        if not ss.get("show_multi_server_b64", False):
            await msg.edit_text(i18n.t("multi_b64_disabled", lang))
            return
        _, managed_link_b64 = get_or_create_bot_sub_links(svc)
        config_items.append((i18n.t("smart_sub_b64_btn", lang) + ":", managed_link_b64))

    if not config_items:
        await msg.edit_text(i18n.t("no_links_available", lang))
        return

    primary_link = config_items[0][1]
    qr_image = make_qr_image(primary_link)
    qr_caption = (
        f"{i18n.t('copy_link_hint', lang)}\n\n"
        f"<code>{escape(primary_link)}</code>"
    )
    try:
        await msg.reply_photo(
            photo=qr_image,
            caption=qr_caption,
            parse_mode="HTML",
            reply_markup=subscription_links_keyboard(svc_id, lang=lang),
        )
    except Exception:
        try:
            await msg.reply_text(
                qr_caption,
                parse_mode="HTML",
                reply_markup=subscription_links_keyboard(svc_id, lang=lang),
                disable_web_page_preview=True,
            )
        except Exception:
            pass

    config_text_lines = [i18n.t("sub_links_title", lang), ""]
    for title, value in config_items:
        config_text_lines.append(title)
        config_text_lines.append(f"<code>{escape(value)}</code>")
        config_text_lines.append("")
    if len(config_items) > 1:
        try:
            await msg.reply_text("\n".join(config_text_lines).strip(), parse_mode="HTML")
        except Exception:
            pass


async def _handle_status(query, context, agent_id, user, data):
    msg = query.message
    parts = data.split(":")
    _lg = i18n.get_customer_lang(agent_id, user.id)

    if data.startswith(CB_STATUS_LIST):
        svc_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if svc_id == 0:
            cust = get_customer_by_telegram_id(agent_id, user.id)
            if not cust:
                return
            from CustomerBot.services import sync_service_status_from_panels, is_customer_service_visible
            services = get_services_by_customer(cust["id"])
            for svc in services:
                await sync_service_status_from_panels(svc.get("id", 0))
            services = get_services_by_customer(cust["id"])
            visible = [s for s in services if is_customer_service_visible(s)]
            if not visible:
                await msg.edit_text(i18n.t("no_active_service", _lg))
                return
            await msg.edit_text(
                i18n.t("status_pick_prompt", _lg),
                reply_markup=services_list_keyboard(visible, lang=_lg),
            )
            return
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text(i18n.t("service_not_found_local", _lg))
            return
        await sync_service_status_from_panels(svc_id)
        await _show_subscription_status(msg, agent_id, svc_id, lang=_lg)
        return

    elif data.startswith(CB_STATUS_CONFIGS):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        ss = get_subs_settings(agent_id)
        # مثل ربات کاربران: متن «📄اطلاعات اشتراک شما» بالای کیبورد باقی بماند
        # و فقط کیبورد عوض شود (نه کل متن).
        try:
            await query.edit_message_reply_markup(
                reply_markup=subscription_configs_keyboard(
                    svc_id,
                    show_direct_config=ss.get("show_direct_config", True),
                    show_sub_link=ss.get("show_sub_link", True),
                    show_auto_sub_link=ss.get("show_auto_sub_link", False),
                    show_sub_link_b64=ss.get("show_sub_link_b64", False),
                    show_multi_server=ss.get("show_multi_server", False),
                    show_multi_server_b64=ss.get("show_multi_server_b64", False),
                    lang=_lg,
                )
            )
        except Exception:
            try:
                await msg.edit_text(
                    i18n.t("kb_choose_connection", _lg),
                    reply_markup=subscription_configs_keyboard(
                        svc_id,
                        show_direct_config=ss.get("show_direct_config", True),
                        show_sub_link=ss.get("show_sub_link", True),
                        show_auto_sub_link=ss.get("show_auto_sub_link", False),
                        show_sub_link_b64=ss.get("show_sub_link_b64", False),
                        show_multi_server=ss.get("show_multi_server", False),
                        show_multi_server_b64=ss.get("show_multi_server_b64", False),
                        lang=_lg,
                    ),
                )
            except Exception:
                pass

    elif data.startswith(CB_STATUS_DIRECT) or data.startswith(CB_STATUS_DIRECTCFG):
        # کانفیگ مستقیم: استخراج از لینک اشتراک همه نودها (در غیر این صورت API پنل)
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text(i18n.t("service_not_found_local", _lg))
            return
        await _send_service_direct_configs(msg, svc, lang=_lg)

    elif data.startswith(CB_STATUS_SUB_LINK) or data.startswith(CB_STATUS_AUTO_SUB) \
            or data.startswith(CB_STATUS_SUB_B64) or data.startswith(CB_STATUS_MULTI) \
            or data.startswith(CB_STATUS_MULTI_B64):
        # لینک‌های اشتراک: ارسال QR بارکد + لینک (مثل ربات کاربران)
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text(i18n.t("service_not_found_local", _lg))
            return
        await _send_subscription_link_with_qr(query, agent_id, svc, data, lang=_lg)

    elif data.startswith(CB_STATUS_MENU):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        await _show_subscription_status(msg, agent_id, svc_id, lang=_lg)

    elif data.startswith(CB_STATUS_REFRESH):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text(i18n.t("service_not_found_local", _lg))
            return
        try:
            await msg.edit_text(i18n.t("st_update", _lg))
        except Exception:
            pass
        await sync_service_status_from_panels(svc_id)
        await _show_subscription_status(msg, agent_id, svc_id, lang=_lg)

    elif data.startswith(CB_STATUS_RENAME):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text(i18n.t("service_not_found_local", _lg))
            return
        context.user_data[UD_STATE] = f"rename:{svc_id}"
        _lg = i18n.get_customer_lang(agent_id, user.id)
        try:
            await msg.edit_text(i18n.t("rename_prompt_short", _lg))
        except Exception:
            pass
        await msg.reply_text(
            i18n.t("rename_prompt", _lg),
            reply_markup=cancel_keyboard(lang=_lg),
        )

    elif data.startswith(CB_STATUS_REPLACE_LINK):
        if "confirm" in data:
            svc_id = int(parts[2]) if len(parts) > 2 else 0
            svc = get_service_by_id(svc_id)
            if not svc:
                await msg.edit_text(i18n.t("service_not_found_local", _lg))
                return
            try:
                await msg.edit_text(i18n.t("changing_link", _lg))
            except Exception:
                pass
            ok, result_text, _new_uuid = await regenerate_service_uuid(svc, lang=_lg)
            await msg.reply_text(result_text, reply_markup=main_menu_keyboard(lang=_lg))
            if ok:
                await _show_subscription_status(msg, agent_id, svc_id, lang=_lg)
        else:
            svc_id = int(parts[2]) if len(parts) > 2 else 0
            _lg = i18n.get_customer_lang(agent_id, user.id)
            await msg.edit_text(
                i18n.t("replace_link_warning", _lg),
                reply_markup=replace_subscription_link_confirm_keyboard(svc_id, lang=_lg),
            )

    elif data.startswith(CB_STATUS_DETACH):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if svc:
            set_service_active(svc_id, False)
            await msg.edit_text(i18n.t("svc_detached", _lg))

    elif data.startswith(CB_STATUS_GUIDE):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        text_settings = get_text_settings(agent_id)
        _lg = i18n.get_customer_lang(agent_id, user.id)
        guide_txt = get_localized_text(agent_id, "guide_text", _lg)
        # دکمه «راهنمای اتصال» روی پیام عکس (QR) قرار دارد؛ به همین دلیل نمی‌توان
        # با edit_text ویرایشش کرد و باید پیام جدیدی ارسال شود.
        try:
            await msg.reply_text(
                guide_txt,
                reply_markup=guide_os_keyboard(f"s:{svc_id}", lang=_lg),
            )
        except Exception:
            await msg.edit_text(
                guide_txt,
                reply_markup=guide_os_keyboard(f"s:{svc_id}", lang=_lg),
            )

    elif data.startswith(CB_STATUS_RENEW):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text(i18n.t("service_not_found_local", _lg))
            return
        _lg = i18n.get_customer_lang(agent_id, user.id)
        br = get_buy_renew_settings(agent_id)
        if not br.get("enable_renew", True):
            await msg.edit_text(i18n.t("renew_disabled", _lg))
            return
        if not await service_is_renewable_live(int(svc.get("id") or 0), agent_id):
            await _edit_or_reply(msg, renew_not_allowed_text(agent_id, _lg))
            return
        context.user_data["renew_target_service_id"] = int(svc_id)
        server_id = svc.get("server_id", 0)
        mode = str(get_setting(agent_id, "plan_display_mode", "dynamic") or "dynamic").strip().lower()
        if mode == "fixed":
            plans = get_fixed_plans(agent_id)
            if not plans:
                await _edit_or_reply(msg, i18n.t("no_plans", _lg))
                return
            await _edit_or_reply(
                msg,
                i18n.t("renew_choose_plan", _lg),
                reply_markup=plans_keyboard(plans, server_id, 0, callback_prefix="renew", lang=_lg),
            )
        else:
            context.user_data.pop(UD_BUY_PLAN_ID, None)
            dyn = _agent_dyn_settings(agent_id, server_id)
            _start_buy_wizard(context)
            gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
            months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_month", 1), 1)))
            gb = min(max(gb, safe_float(dyn.get("min_gb", 1), 1.0)), safe_int(dyn.get("max_gb", 1000), 1000))
            min_month, max_month, _ = _dyn_month_limits(dyn)
            months = _clamp_months(months, min_month, max_month)
            context.user_data[UD_BUY_GB] = gb
            context.user_data[UD_BUY_MONTHS] = months
            price, off_pct = _calc_dynamic_price(gb, months, dyn)
            await _edit_or_reply(
                msg,
                i18n.t("renew_choose_package", _lg),
                reply_markup=renew_wizard_keyboard(server_id, gb, months, price, off_pct, lang=_lg),
            )

    elif data.startswith(CB_STATUS_LIST_BACK):
        await _back_to_main_menu(msg, i18n.t("back", _lg), lang=_lg)


async def _handle_renew(query, context, agent_id, user, data):
    msg = query.message
    parts = data.split(":")
    _lg0 = i18n.get_customer_lang(agent_id, user.id)

    if data.startswith(CB_RENEW_SVC):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.reply_text(i18n.t("service_not_found_local", _lg0), reply_markup=main_menu_keyboard(lang=_lg0))
            return
        # قوانین تمدید (حالت پیشرفته): حجم/زمان باقی‌مانده باید کمتر از حد مجاز باشد
        if not await service_is_renewable_live(int(svc.get("id") or 0), agent_id):
            await _edit_or_reply(msg, renew_not_allowed_text(agent_id, _lg0), reply_markup=main_menu_keyboard(lang=_lg0))
            return
        context.user_data["renew_target_service_id"] = int(svc_id)
        context.user_data.pop(UD_BUY_GB, None)
        context.user_data.pop(UD_BUY_MONTHS, None)
        server_id = svc.get("server_id", 0)
        mode = str(get_setting(agent_id, "plan_display_mode", "dynamic") or "dynamic").strip().lower()
        if mode == "fixed":
            plans = get_fixed_plans(agent_id)
            if not plans:
                await _edit_or_reply(msg, i18n.t("no_plans_for_agent", _lg0), reply_markup=main_menu_keyboard(lang=_lg0))
                return
            await _edit_or_reply(
                msg,
                i18n.t("renew_plan_choose", _lg0),
                reply_markup=plans_keyboard(plans, server_id, 0, callback_prefix="renew", lang=_lg0),
            )
        else:
            context.user_data.pop(UD_BUY_PLAN_ID, None)
            dyn = _agent_dyn_settings(agent_id, server_id)
            _start_buy_wizard(context)
            gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
            months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_month", 1), 1)))
            gb = min(max(gb, safe_float(dyn.get("min_gb", 1), 1.0)), safe_int(dyn.get("max_gb", 1000), 1000))
            min_month, max_month, _ = _dyn_month_limits(dyn)
            months = _clamp_months(months, min_month, max_month)
            context.user_data[UD_BUY_GB] = gb
            context.user_data[UD_BUY_MONTHS] = months
            price, off_pct = _calc_dynamic_price(gb, months, dyn)
            await _edit_or_reply(
                msg,
                i18n.t("renew_pkg_choose", _lg0),
                reply_markup=renew_wizard_keyboard(server_id, gb, months, price, off_pct, lang=_lg0),
            )

    elif data.startswith("rwiz:"):
        # wizard تمدید پویا — تنظیم حجم/ماه
        server_id = int(parts[1]) if len(parts) > 1 else 0
        wiz_action = parts[2] if len(parts) > 2 else ""
        dyn = _agent_dyn_settings(agent_id, server_id)

        if _wizard_expired(context):
            _reset_buy_wizard(context)
            _start_buy_wizard(context)
            _lg = i18n.get_customer_lang(agent_id, user.id)
            gb = int(safe_float(dyn.get("min_gb", 1), 1.0))
            min_month, max_month, _ = _dyn_month_limits(dyn)
            months = min_month
            price, off_pct = _calc_dynamic_price(gb, months, dyn)
            await msg.edit_text(
                i18n.t("renew_session_expired", _lg) + "\n\n" + i18n.t("renew_choose_package", _lg),
                reply_markup=renew_wizard_keyboard(server_id, gb, months, price, off_pct, lang=_lg),
            )
            context.user_data[UD_BUY_GB] = gb
            context.user_data[UD_BUY_MONTHS] = months
            return

        _start_buy_wizard(context)

        gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
        months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_month", 1), 1)))
        step_gb = safe_int(dyn.get("step_gb", 1), 1)
        max_gb = safe_int(dyn.get("max_gb", 1000), 1000)
        min_gb = safe_float(dyn.get("min_gb", 1), 1.0)
        min_month, max_month, step_month = _dyn_month_limits(dyn)
        months = _clamp_months(months, min_month, max_month)
        if wiz_action == "gb_inc":
            gb = min(gb + step_gb, max_gb)
        elif wiz_action == "gb_dec":
            gb = max(min_gb, gb - step_gb)
        elif wiz_action == "month_inc":
            if months >= max_month:
                await msg.answer(i18n.t("max_period_alert", _lg0, m=max_month), show_alert=True)
                return
            months = min(max_month, months + step_month)
        elif wiz_action == "month_dec":
            if months <= min_month:
                await msg.answer(i18n.t("min_period_alert", _lg0, m=min_month), show_alert=True)
                return
            months = max(min_month, months - step_month)
        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = months
        price, off_pct = _calc_dynamic_price(gb, months, dyn)
        try:
            _lg = i18n.get_customer_lang(agent_id, user.id)
            await msg.edit_text(
                i18n.t("renew_choose_package", _lg),
                reply_markup=renew_wizard_keyboard(server_id, gb, months, price, off_pct, lang=_lg),
            )
        except Exception:
            pass

    elif data.startswith("renew:confirm_dyn:"):
        # تایید تمدید پویا → هدایت به صفحه انتخاب روش پرداخت (مثل مسیر خرید).
        # تمدید فقط پس از تایید پرداخت توسط نماینده انجام می‌شود.
        server_id_cb = int(parts[2]) if len(parts) > 2 else 0
        service_id = int(context.user_data.get("renew_target_service_id") or 0)
        if not service_id:
            await msg.reply_text(i18n.t("renew_service_not_found", _lg0), reply_markup=main_menu_keyboard(lang=_lg0))
            return
        svc = get_service_by_id(service_id)
        if not svc:
            await msg.reply_text(i18n.t("service_not_found_local", _lg0), reply_markup=main_menu_keyboard(lang=_lg0))
            return
        if not await service_is_renewable_live(int(svc.get("id") or 0), agent_id):
            await _edit_or_reply(msg, renew_not_allowed_text(agent_id, _lg0), reply_markup=main_menu_keyboard(lang=_lg0))
            return
        if _wizard_expired(context):
            _reset_buy_wizard(context)
            await msg.edit_text(
                i18n.t("renew_session_expired_btn", _lg0),
                reply_markup=_ikb([[InlineKeyboardButton(i18n.t("back", _lg0), callback_data=f"status:menu:{service_id}")]]),
            )
            return
        server_id = int(svc.get("server_id") or 0) or server_id_cb
        dyn = _agent_dyn_settings(agent_id, server_id)
        gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)) or 0)
        months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_month", 1), 1)) or 0)
        gb = max(gb, int(safe_float(dyn.get("min_gb", 1), 1.0)))
        min_month, max_month, _ = _dyn_month_limits(dyn)
        months = _clamp_months(months, min_month, max_month)
        days = months * 30
        price, _ = _calc_dynamic_price(gb, months, dyn)
        context.user_data[UD_BUY_SERVER_ID] = server_id
        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = months
        context.user_data.pop(UD_BUY_PLAN_ID, None)
        context.user_data.pop(UD_WIZARD_START_TS, None)
        _lg = i18n.get_customer_lang(agent_id, user.id)
        await msg.edit_text(
            i18n.t("renew_confirm_title", _lg) + "\n\n"
            + i18n.t("pkg_volume_line", _lg, g=gb) + "\n"
            + i18n.t("pkg_days_line", _lg, d=days) + "\n"
            + i18n.t("pkg_price_line", _lg, p=f"{price:,}") + "\n\n"
            + i18n.t("buy_choose_method", _lg),
            reply_markup=renew_payment_keyboard(service_id, server_id, int(gb), days, price),
        )

    elif data.startswith("renew:plan:"):
        plan_id = int(parts[3]) if len(parts) > 3 else 0
        service_id = int(context.user_data.get("renew_target_service_id") or 0)
        if not service_id:
            await msg.reply_text(i18n.t("renew_service_not_found", _lg0), reply_markup=main_menu_keyboard(lang=_lg0))
            return
        svc = get_service_by_id(service_id)
        if not svc:
            await msg.reply_text(i18n.t("service_not_found_local", _lg0), reply_markup=main_menu_keyboard(lang=_lg0))
            return
        if not await service_is_renewable_live(int(svc.get("id") or 0), agent_id):
            await _edit_or_reply(msg, renew_not_allowed_text(agent_id, _lg0), reply_markup=main_menu_keyboard(lang=_lg0))
            return
        plan = get_fixed_plan(agent_id, plan_id)
        if not plan:
            await msg.reply_text(i18n.t("invalid_plan", _lg0), reply_markup=main_menu_keyboard(lang=_lg0))
            return
        price = safe_int(plan.get("price", 0))
        gb = int(safe_float(plan.get("gb", 0)))
        days = safe_int(plan.get("days", 0))
        if days <= 0:
            days = 30
        server_id = int(svc.get("server_id") or 0)
        context.user_data[UD_BUY_SERVER_ID] = server_id
        context.user_data[UD_BUY_PLAN_ID] = plan_id
        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = max(1, days // 30)
        context.user_data.pop(UD_WIZARD_START_TS, None)
        _lg = i18n.get_customer_lang(agent_id, user.id)
        await msg.edit_text(
            i18n.t("renew_confirm_title", _lg) + "\n\n"
            + i18n.t("pkg_volume_line", _lg, g=gb) + "\n"
            + i18n.t("pkg_days_line", _lg, d=days) + "\n"
            + i18n.t("pkg_price_line", _lg, p=f"{price:,}") + "\n\n"
            + i18n.t("buy_choose_method", _lg),
            reply_markup=renew_payment_keyboard(service_id, server_id, gb, days, price),
        )

    elif data.startswith("renew:pay_direct:"):
        # پرداخت تمدید — مطابق مسیر خرید: ساخت سفارش + رسید کارت به کارت.
        service_id = int(parts[2]) if len(parts) > 2 else 0
        server_id = int(parts[3]) if len(parts) > 3 else 0
        gb = int(parts[4]) if len(parts) > 4 else 0
        days = int(parts[5]) if len(parts) > 5 else 0
        price = int(parts[6]) if len(parts) > 6 else 0
        svc = get_service_by_id(service_id)
        _lg = i18n.get_customer_lang(agent_id, user.id)
        if not svc:
            await msg.reply_text(i18n.t("service_not_found", _lg), reply_markup=main_menu_keyboard(lang=_lg))
            return
        # بررسی مالکیت سرویس (ضد دستکاری callback data)
        cust = get_customer_by_telegram_id(agent_id, user.id)
        if not cust or int(svc.get("customer_id") or 0) != int(cust.get("id") or 0):
            await msg.reply_text(i18n.t("svc_not_owned", _lg), reply_markup=main_menu_keyboard(lang=_lg))
            return
        # قوانین تمدید — گیت نهایی سمت سرور (ضد پرش مراحل)
        if not await service_is_renewable_live(int(svc.get("id") or 0), agent_id):
            try:
                await query.answer(i18n.t("st_renew_not_allowed", _lg), show_alert=True)
            except Exception:
                pass
            await _back_to_main_menu(msg, renew_not_allowed_text(agent_id, _lg), lang=_lg)
            return
        # قیمت نمایشی سفارش همیشه سمت سرور دوباره محاسبه می‌شود —
        # ضد دستکاری callback data (قیمت جعلی می‌تواند نماینده را در تایید رسید گول بزند)
        recomputed_price = None
        plan_id_cached = int(context.user_data.get(UD_BUY_PLAN_ID, 0) or 0)
        if plan_id_cached > 0:
            cached_plan = get_fixed_plan(agent_id, plan_id_cached)
            if cached_plan and int(float(cached_plan.get("gb") or 0)) == gb and int(cached_plan.get("days") or 0) == days:
                recomputed_price = safe_int(cached_plan.get("price", 0))
        if recomputed_price is None and gb > 0 and days > 0 and days % 30 == 0:
            dyn_settings_renew = _agent_dyn_settings(agent_id, int(svc.get("server_id") or 0) or server_id)
            recomputed_price, _off = _calc_dynamic_price(gb, days // 30, dyn_settings_renew)
        if recomputed_price is None or recomputed_price <= 0:
            try:
                await query.answer(i18n.t("btn_expired_renew", _lg), show_alert=True)
            except Exception:
                pass
            await _back_to_main_menu(msg, i18n.t("btn_expired_renew", _lg), lang=_lg)
            return
        price = int(recomputed_price)
        card = _random_active_agent_card(agent_id)
        if not card.get("number"):
            try:
                await query.answer(i18n.t("no_card", _lg), show_alert=True)
            except Exception:
                pass
            await _back_to_main_menu(msg, i18n.t("no_card", _lg), lang=_lg)
            return
        wholesale_price = calculate_wholesale_price(agent_id, gb, days, server_id)
        order = create_order(
            agent_id=agent_id,
            telegram_id=user.id,
            volume_gb=float(gb),
            days=days,
            price=price,
            plan_title=i18n.t("order_title_renew", _lg, g=gb, d=days),
            server_location=(get_server_by_id(server_id) or {}).get("title", ""),
            username=user.username or "",
            full_name=user.full_name or "",
            server_id=server_id,
            plan_id=int(context.user_data.get(UD_BUY_PLAN_ID, 0) or 0),
            wholesale_price=wholesale_price,
            renew_service_id=service_id,
        )
        context.user_data[UD_BUY_SERVER_ID] = server_id
        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = max(1, days // 30) if days > 0 else 1
        context.user_data["last_order_id"] = order.get("order_id", 0)
        context.user_data[UD_STATE] = STATE_RECEIPT_WAITING
        ps = get_payment_settings(agent_id)
        tx_settings = get_tx_plans_settings(agent_id)
        tx_marker = 0
        pay_price = price
        if bool((tx_settings or {}).get("random_tx_spec")):
            tx_marker = random.randint(101, 997)
            if tx_marker % 10 == 0:
                tx_marker += 1
            pay_price = price + tx_marker
            context.user_data["pending_tx_marker"] = tx_marker
        else:
            context.user_data.pop("pending_tx_marker", None)
        context.user_data["pending_pay_price"] = pay_price
        card_text = ps.get("card_to_card_text", "0")
        _lg = i18n.get_customer_lang(agent_id, user.id)
        if card_text == "0":
            rial_price = pay_price * 10
            card_text = (
                i18n.t("pay_exact_rial", _lg, r=f"{rial_price:,}") + "\n"
                + i18n.t("pay_equiv_toman", _lg, p=f"{pay_price:,}") + "\n"
                + i18n.t("pay_card_number", _lg, c=card.get("number", "?")) + "\n"
                + i18n.t("pay_card_owner", _lg, o=card.get("owner", "?")) + "\n"
                + i18n.t("card_receipt_intro", _lg) + "\n\n"
                + i18n.t("renew_auto_done", _lg)
            )
            if tx_marker > 0:
                card_text = (
                    i18n.t("tx_spec_applied", _lg, m=tx_marker) + "\n\n"
                    f"{card_text}"
                )
        context.user_data.pop("renew_target_service_id", None)
        await msg.edit_text(card_text, parse_mode="Markdown", reply_markup=confirm_payment_inline_keyboard(lang=_lg))

    elif data.startswith(CB_RENEW_BACK):
        context.user_data.pop("renew_target_service_id", None)
        await _back_to_main_menu(msg, i18n.t("back", _lg0), lang=_lg0)


async def _handle_pay(query, context, agent_id, user, data):
    msg = query.message
    _lg = i18n.get_customer_lang(agent_id, user.id)

    if data == CB_PAY_RECEIPT_DONE:
        context.user_data[UD_STATE] = "wallet_receipt_photo"
        # حذف پیام دارای دکمه اینلاین و ارسال پیام جدید با دکمه بازگشت در کیبورد پایین (مثل ربات کاربران)
        try:
            await msg.delete()
        except Exception:
            pass
        await query.get_bot().send_message(
            chat_id=query.from_user.id,
            text=i18n.t("receipt_prompt", _lg),
            reply_markup=receipt_cancel_keyboard(lang=_lg),
        )

    elif data == CB_PAY_CANCEL:
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop("pending_pay_price", None)
        context.user_data.pop("pending_tx_marker", None)
        context.user_data.pop("pending_receipt_meta", None)
        context.user_data.pop("pending_amount", None)
        context.user_data.pop("pending_order_id", None)
        context.user_data.pop("renew_target_service_id", None)
        await _back_to_main_menu(msg, i18n.t("purchase_cancelled", _lg), lang=_lg)


async def _handle_trial(query, context, agent_id, user, data):
    msg = query.message
    parts = data.split(":")
    _lg = i18n.get_customer_lang(agent_id, user.id)

    if data.startswith(CB_TRIAL_LOC):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        u_db = get_user(agent_id, user.id)
        if u_db and u_db.get("got_free_trial"):
            await msg.edit_text(i18n.t("trial_already_used", _lg))
            return
        server = get_server_by_id(server_id)
        if not server:
            await msg.edit_text(i18n.t("service_not_found_local", _lg))
            return
        trial = get_trial_spec_settings(agent_id)
        if not trial.get("enabled", True):
            await msg.edit_text(i18n.t("trial_disabled_msg", _lg))
            return
        context.user_data[UD_STATE] = STATE_TRIAL_WAITING_NAME
        context.user_data["pending_trial_server_id"] = server_id
        context.user_data["pending_trial_server"] = server
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            from CustomerBot.keyboards import cancel_keyboard
            await query.get_bot().send_message(
                chat_id=query.from_user.id,
                text=i18n.t("trial_name_prompt", _lg),
                reply_markup=cancel_keyboard(lang=_lg),
            )
        except Exception:
            pass
        return

    elif data == CB_TRIAL_BACK:
        await _back_to_main_menu(msg, i18n.t("back", _lg), lang=_lg)


async def _notify_agent_new_trial(agent_id: int, user, service_id: int, customer_id: int, service_name: str, gb: float, days: int, server_title: str) -> None:
    """گزارش ساخت اکانت تست رایگان به ربات نماینده."""
    try:
        import os
        from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
        from Shared.agent_db import get_agent_by_id, get_customer_by_id
        agent = get_agent_by_id(agent_id)
        agent_tg_id = int((agent or {}).get("telegram_id") or 0)
        token = os.getenv("AGENT_BOT_TOKEN", "").strip()
        if not agent_tg_id or not token:
            return
        _alg = i18n.get_agent_lang(int(agent_id or 0))
        name = getattr(user, "full_name", "") or getattr(user, "username", "") or str(getattr(user, "id", ""))
        btn_custom = str((get_customer_by_id(customer_id) or {}).get("full_name") or name or f"#{customer_id}").strip()
        btn_label = f"\U0001f464 {btn_custom} | \U0001f511 {customer_id}"
        text = (
            i18n.t("ag_trial_report_title", _alg) + "\n\n"
            + i18n.t("ag_trial_report_svc", _alg, s=service_name) + "\n"
            + i18n.t("ag_trial_report_srv", _alg, s=server_title or "-") + "\n"
            + i18n.t("ag_trial_report_gb", _alg, g=f"{float(gb):g}") + "\n"
            + i18n.t("ag_trial_report_days", _alg, d=int(days)) + "\n"
            + i18n.t("ag_trial_report_sid", _alg, s=service_id)
        )
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(btn_label, callback_data=f"agbot:set:users:detail:{customer_id}")]]
        )

        # کپی گزارش تست رایگان برای ادمین / کانال رویداد نمایندگی
        try:
            from Shared.admin_reports import send_agency_event_report
            agent_name = (
                str((agent or {}).get("full_name") or "").strip()
                or str((agent or {}).get("username") or "").strip()
                or (str(agent_tg_id) if agent_tg_id else "")
                or "—"
            )
            event_text = (
                i18n.t("ag_trial_event_title", _alg) + "\n"
                + i18n.t("ag_trial_event_agent", _alg, n=escape(agent_name)) + "\n"
                + i18n.t("ag_trial_event_customer", _alg, n=escape(btn_custom)) + "\n"
                + i18n.t("ag_trial_event_body", _alg) + "\n\n"
                + i18n.t("ag_trial_report_svc", _alg, s=escape(str(service_name))) + "\n"
                + i18n.t("ag_trial_report_srv", _alg, s=escape(str(server_title or "-"))) + "\n"
                + i18n.t("ag_trial_event_gb", _alg, g=f"{float(gb):g}") + "\n"
                + i18n.t("ag_trial_event_days", _alg, d=int(days)) + "\n"
                + i18n.t("ag_trial_report_sid", _alg, s=service_id)
            )
            await send_agency_event_report(event_text)
        except Exception:
            pass

        await Bot(token=token).send_message(chat_id=agent_tg_id, text=text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        return


async def _build_trial_service(update, context, agent_id, user, service_name: str):
    """ساخت سرویس تست رایگان با نام کاربری ارسال‌شده (مثل UserBot) و نمایش اشتراک."""
    from CustomerBot.keyboards import subscription_status_keyboard
    server_id = int(context.user_data.get("pending_trial_server_id") or 0)
    server = context.user_data.get("pending_trial_server")
    if not server:
        server = get_server_by_id(server_id)
    context.user_data.pop(UD_STATE, None)
    context.user_data.pop("pending_trial_server_id", None)
    context.user_data.pop("pending_trial_server", None)

    service_name = (service_name or "").strip()
    _lg = i18n.get_customer_lang(agent_id, user.id)
    if not server or not server_id:
        await update.message.reply_text(i18n.t("trial_info_incomplete", _lg), reply_markup=main_menu_keyboard(lang=_lg))
        return

    u_db = get_user(agent_id, user.id)
    if u_db and u_db.get("got_free_trial"):
        await update.message.reply_text(i18n.t("trial_already_used", _lg), reply_markup=main_menu_keyboard(lang=_lg))
        return

    # ثبت کاربر در جدول متون و فلگ تست رایگان (upsert) تا از ساخت دوباره جلوگیری شود.
    if not u_db:
        u_db = {}
        u_db["id"] = upsert_user(agent_id, user.id, user.username or "", user.full_name or "")

    trial = get_trial_spec_settings(agent_id)
    gb = trial.get("usage_gb", 1)
    days = trial.get("days", 1)

    from Shared.hiddify_api import create_user
    import uuid
    new_uuid = str(uuid.uuid4())
    note = make_service_note(agent_id)
    payload = {
        "name": service_name or i18n.t("trial_default_name", _lg, g=gb),
        "usage_limit_GB": gb,
        "package_days": days,
        "uuid": new_uuid,
        "is_active": True,
        "comment": note,
    }
    try:
        result = await create_user(server, payload)
    except Exception as exc:
        logger.exception("customer trial create_user failed uid=%s: %s", user.id, exc)
        result = None
    if not result:
        await update.message.reply_text(i18n.t("trial_create_failed", _lg), reply_markup=main_menu_keyboard(lang=_lg))
        return

    cust_id = None
    cust = get_customer_by_telegram_id(agent_id, user.id)
    if cust:
        cust_id = cust["id"]
    if not cust_id:
        cust_id = upsert_customer(agent_id, user.id, user.username or "", user.full_name or "")

    svc = create_service(
        agent_id=agent_id,
        customer_id=cust_id,
        server_id=server_id,
        server_title=server.get("title", ""),
        name=service_name or i18n.t("trial_default_name", _lg, g=gb),
        panel_user_uuid=new_uuid,
        usage_limit=float(gb),
        days=days,
        sale_price=0,
        is_trial=1,
        note=note,
    )
    add_service_node(
        service_id=svc["id"],
        server_id=server_id,
        server_title=server.get("title", ""),
        panel_user_uuid=new_uuid,
        panel_user_id=str(result.get("id", "")),
    )

    try:
        set_got_free_trial(agent_id, user.id)
    except Exception as e:
        logger.exception("customer trial mark used failed uid=%s: %s", user.id, e)

    svc = get_service_by_id(svc["id"])
    link = ""
    try:
        managed_link, _ = get_or_create_bot_sub_links(svc or {})
        if managed_link:
            link = managed_link
    except Exception:
        pass
    if not link:
        domains = server.get("domains", [])
        domain = domains[0]["domain"] if domains else server.get("host", "?")
        if domain and not (domain.startswith("http://") or domain.startswith("https://")):
            domain = f"https://{domain}"
        link = f"{domain.rstrip('/')}/{new_uuid}"

    # گزارش ساخت اکانت تست رایگان به ربات نماینده
    await _notify_agent_new_trial(
        agent_id,
        user,
        svc.get("id"),
        cust_id,
        service_name or i18n.t("trial_default_name", _lg, g=gb),
        gb,
        days,
        server.get("title", ""),
    )

    await update.message.reply_text(
        i18n.t("trial_created_ok", _lg),
        reply_markup=main_menu_keyboard(lang=_lg),
    )

    if svc:
        from CustomerBot.database import get_subs_settings
        try:
            subs_settings = get_subs_settings(agent_id)
            await update.message.reply_text(
                build_subscription_status_text(svc, subs_settings, None, lang=_lg),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(
                    svc["id"],
                    show_direct_config=subs_settings.get("show_direct_config", True),
                    show_sub_link=subs_settings.get("show_sub_link", True),
                    lang=_lg,
                ),
            )
        except Exception:
            pass


def _random_active_agent_card(agent_id: int) -> dict:
    """انتخاب تصادفی یک کارت فعال از کارت‌های نماینده برای پرداخت کارت به کارت."""
    from AgentBot.database import get_cards as get_agent_cards
    try:
        agent_cards = get_agent_cards(agent_id) or []
    except Exception:
        agent_cards = []

    def _card_is_active(c):
        try:
            return int(c.get("is_active", 1) or 1) != 0
        except (TypeError, ValueError):
            return True

    active_cards = [c for c in agent_cards if _card_is_active(c)]
    if not active_cards:
        return {}
    chosen = random.choice(active_cards)
    return {
        "number": str(chosen.get("card_number") or "").strip(),
        "owner": str(chosen.get("owner_name") or "").strip(),
        "bank": str(chosen.get("bank_name") or "").strip(),
    }


async def _handle_buy(query, context, agent_id, user, data):
    msg = query.message
    parts = data.split(":")
    action = parts[0]
    text_settings = get_text_settings(agent_id)
    br = get_buy_renew_settings(agent_id)
    _lg = i18n.get_customer_lang(agent_id, user.id)

    if action == "wiz":
        server_id = int(parts[1]) if len(parts) > 1 else 0
        wiz_action = parts[2] if len(parts) > 2 else ""
        dyn = _agent_dyn_settings(agent_id, server_id)

        if _wizard_expired(context):
            _reset_buy_wizard(context)
            _start_buy_wizard(context)
            gb = int(safe_float(dyn.get("min_gb", 1), 1.0))
            min_month, max_month, _ = _dyn_month_limits(dyn)
            months = min_month
            total, off_pct = _calc_dynamic_price(gb, months, dyn)
            mode = str(get_setting(agent_id, "plan_display_mode", "dynamic") or "dynamic").strip().lower()
            kb = (mixed_buy_keyboard(server_id, gb, months, total, off_percent=off_pct, lang=_lg)
                  if mode == "mixed" else buy_wizard_keyboard(server_id, gb, months, total, off_percent=off_pct, lang=_lg))
            await msg.edit_text(
                i18n.t("buy_session_expired", _lg) + i18n.t("buy_hint", _lg),
                reply_markup=kb,
            )
            context.user_data[UD_BUY_GB] = gb
            context.user_data[UD_BUY_MONTHS] = months
            return

        _start_buy_wizard(context)

        gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
        months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_month", 1), 1)))
        step_gb = safe_int(dyn.get("step_gb", 1), 1)
        max_gb = safe_int(dyn.get("max_gb", 1000), 1000)
        min_month, max_month, step_month = _dyn_month_limits(dyn)
        months = _clamp_months(months, min_month, max_month)

        if wiz_action == "gb_inc":
            gb = min(gb + step_gb, max_gb)
        elif wiz_action == "gb_dec":
            gb = max(safe_float(dyn.get("min_gb", 1), 1.0), gb - step_gb)
        elif wiz_action == "month_inc":
            if months >= max_month:
                await msg.answer(i18n.t("max_period_alert", _lg, m=max_month), show_alert=True)
                return
            months = min(max_month, months + step_month)
        elif wiz_action == "month_dec":
            if months <= min_month:
                await msg.answer(i18n.t("min_period_alert", _lg, m=min_month), show_alert=True)
                return
            months = max(min_month, months - step_month)
        elif wiz_action == "show_fixed":
            # استفاده از پلن‌های نماینده
            plans = get_fixed_plans(agent_id)
            if not plans:
                await msg.edit_text(
                    i18n.t("no_plans_for_agent", _lg),
                    reply_markup=_ikb([[InlineKeyboardButton(i18n.t("back", _lg), callback_data=CB_BUY_BACK_MAIN)]])
                )
                return
            await msg.edit_text(
                i18n.t("choose_plan", _lg),
                reply_markup=plans_keyboard(plans, server_id, 0, lang=_lg),
            )
            return

        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = months
        total, off_pct = _calc_dynamic_price(gb, months, dyn)

        mode = str(get_setting(agent_id, "plan_display_mode", "dynamic") or "dynamic").strip().lower()
        if mode == "mixed":
            await msg.edit_text(
                i18n.t("buy_hint", _lg),
                reply_markup=mixed_buy_keyboard(server_id, gb, months, total, off_percent=off_pct, lang=_lg),
            )
        else:
            await msg.edit_text(
                i18n.t("buy_hint", _lg),
                reply_markup=buy_wizard_keyboard(server_id, gb, months, total, off_percent=off_pct, lang=_lg),
            )

    elif data.startswith(CB_BUY_LOC):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        mode = str(get_setting(agent_id, "plan_display_mode", "dynamic") or "dynamic").strip().lower()
        context.user_data.pop(UD_BUY_GB, None)
        context.user_data.pop(UD_BUY_MONTHS, None)

        if mode == "fixed":
            plans = get_fixed_plans(agent_id)
            if not plans:
                await msg.edit_text(
                    i18n.t("no_plans_for_agent", _lg),
                    reply_markup=_ikb([[InlineKeyboardButton(i18n.t("back", _lg), callback_data=CB_BUY_BACK_MAIN)]])
                )
                return
            await msg.edit_text(
                get_localized_text(agent_id, "plans_list_text", _lg),
                reply_markup=plans_keyboard(plans, server_id, 0, lang=_lg),
            )
        elif mode == "mixed":
            await msg.edit_text(
                get_localized_text(agent_id, "plans_list_text", _lg),
                reply_markup=mixed_mode_keyboard(server_id, lang=_lg),
            )
        else:
            dyn = _agent_dyn_settings(agent_id, server_id)
            _start_buy_wizard(context)
            gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
            months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_month", 1), 1)))
            step_gb = safe_int(dyn.get("step_gb", 1), 1)
            max_gb = safe_int(dyn.get("max_gb", 1000), 1000)
            gb = min(max(gb, safe_float(dyn.get("min_gb", 1), 1.0)), max_gb)
            min_month, max_month, _ = _dyn_month_limits(dyn)
            months = _clamp_months(months, min_month, max_month)
            context.user_data[UD_BUY_GB] = gb
            context.user_data[UD_BUY_MONTHS] = months
            total, off_pct = _calc_dynamic_price(gb, months, dyn)
            await msg.edit_text(
                i18n.t("buy_hint", _lg),
                reply_markup=buy_wizard_keyboard(server_id, gb, months, total, off_percent=off_pct, lang=_lg),
            )

    elif data.startswith(CB_BUY_CAT):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        cat_id = int(parts[3]) if len(parts) > 3 else 0
        # استفاده از پلن‌های نماینده با فیلتر دسته‌بندی
        all_plans = get_fixed_plans(agent_id, category_id=cat_id)
        tx = get_tx_plans_settings(agent_id)
        await msg.edit_text(
            i18n.t("choose_plan", _lg),
            reply_markup=plans_keyboard(
                all_plans, server_id, cat_id,
                sort_by_priority=tx.get("plan_sort_by_priority", True),
                lang=_lg,
            ),
        )

    elif data.startswith(CB_BUY_PLAN):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        plan_id = int(parts[3]) if len(parts) > 3 else 0
        # جستجو در پلن‌های نماینده
        p = get_fixed_plan(agent_id, plan_id)
        if not p:
            await msg.edit_text(i18n.t("invalid_plan", _lg))
            return
        price = safe_int(p.get("price", 0))
        gb = safe_float(p.get("gb", 0))
        days = safe_int(p.get("days", 0))
        context.user_data[UD_BUY_SERVER_ID] = server_id
        context.user_data[UD_BUY_PLAN_ID] = plan_id
        await msg.edit_text(
            i18n.t("buy_plan_title", _lg) + "\n\n"
            + i18n.t("pkg_volume_line", _lg, g=gb) + "\n"
            + i18n.t("pkg_days_line", _lg, d=days) + "\n"
            + i18n.t("pkg_price_line", _lg, p=f"{price:,}") + "\n\n"
            + i18n.t("buy_choose_method", _lg),
            reply_markup=selected_plan_keyboard(server_id, int(gb), days, price, lang=_lg),
        )

    elif data.startswith(CB_BUY_CONFIRM_DYN):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        dyn = _agent_dyn_settings(agent_id, server_id) or {}

        if _wizard_expired(context):
            _reset_buy_wizard(context)
            await msg.edit_text(
                i18n.t("pkg_session_expired", _lg),
                reply_markup=_ikb([[InlineKeyboardButton(i18n.t("back", _lg), callback_data=CB_BUY_BACK_MAIN)]]),
            )
            return

        gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)) or 0)
        months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_month", 1), 1)) or 0)
        gb = max(gb, int(safe_float(dyn.get("min_gb", 1), 1.0)))
        min_month, max_month, _ = _dyn_month_limits(dyn)
        months = _clamp_months(months, min_month, max_month)
        days = months * 30
        price, _ = _calc_dynamic_price(gb, months, dyn)
        context.user_data[UD_BUY_SERVER_ID] = server_id
        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = months
        context.user_data.pop(UD_WIZARD_START_TS, None)
        await msg.edit_text(
            i18n.t("buy_plan_title", _lg) + "\n\n"
            + i18n.t("pkg_volume_line", _lg, g=gb) + "\n"
            + i18n.t("pkg_days_line", _lg, d=days) + "\n"
            + i18n.t("pkg_price_line", _lg, p=f"{price:,}") + "\n\n"
            + i18n.t("buy_choose_method", _lg),
            reply_markup=selected_plan_keyboard(server_id, int(gb), days, price, lang=_lg),
        )

    elif data.startswith(CB_BUY_CONFIRM):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        plan_id = int(parts[3]) if len(parts) > 3 else 0
        # جستجو در پلن‌های نماینده
        p = get_fixed_plan(agent_id, plan_id)
        if not p:
            return
        price = safe_int(p.get("price", 0))
        gb = safe_float(p.get("gb", 0))
        days = safe_int(p.get("days", 0))
        await msg.edit_text(
            i18n.t("buy_plan_title", _lg) + "\n\n"
            + i18n.t("pkg_volume_line", _lg, g=gb) + "\n"
            + i18n.t("pkg_days_line", _lg, d=days) + "\n"
            + i18n.t("pkg_price_line", _lg, p=f"{price:,}") + "\n\n"
            + i18n.t("buy_choose_method", _lg),
            reply_markup=selected_plan_keyboard(server_id, int(gb), days, price, lang=_lg),
        )

    elif data.startswith(CB_BUY_PAY_DIRECT):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        gb = int(parts[3]) if len(parts) > 3 else 0
        days = int(parts[4]) if len(parts) > 4 else 0
        price = int(parts[5]) if len(parts) > 5 else 0
        # قیمت نمایشی سفارش همیشه سمت سرور دوباره محاسبه می‌شود —
        # ضد دستکاری callback data (شارژ واقعی از wholesale سمت سرور است ولی
        # قیمت جعلی می‌تواند نماینده را در تایید رسید گول بزند)
        recomputed_price = None
        plan_id_cached = int(context.user_data.get(UD_BUY_PLAN_ID, 0) or 0)
        if plan_id_cached > 0:
            cached_plan = get_fixed_plan(agent_id, plan_id_cached)
            if cached_plan and int(float(cached_plan.get("gb") or 0)) == gb and int(cached_plan.get("days") or 0) == days:
                recomputed_price = safe_int(cached_plan.get("price", 0))
        if recomputed_price is None and gb > 0 and days > 0 and days % 30 == 0:
            dyn_settings_buy = _agent_dyn_settings(agent_id, server_id)
            recomputed_price, _off = _calc_dynamic_price(gb, days // 30, dyn_settings_buy)
        if recomputed_price is None or recomputed_price <= 0:
            try:
                await query.answer(i18n.t("btn_expired_buy", _lg), show_alert=True)
            except Exception:
                pass
            await _back_to_main_menu(msg, i18n.t("btn_expired_buy", _lg), lang=_lg)
            return
        price = int(recomputed_price)
        card = _random_active_agent_card(agent_id)
        if not card.get("number"):
            try:
                await query.answer(i18n.t("no_card_registered", _lg), show_alert=True)
            except Exception:
                pass
            await _back_to_main_menu(msg, i18n.t("no_card_registered", _lg), lang=_lg)
            return
        wholesale_price = calculate_wholesale_price(agent_id, gb, days, server_id)
        order = create_order(
            agent_id=agent_id,
            telegram_id=user.id,
            volume_gb=float(gb),
            days=days,
            price=price,
            plan_title=i18n.t("order_title_buy", _lg, g=gb, d=days),
            server_location=(get_server_by_id(server_id) or {}).get("title", ""),
            username=user.username or "",
            full_name=user.full_name or "",
            server_id=server_id,
            plan_id=int(context.user_data.get(UD_BUY_PLAN_ID, 0) or 0),
            wholesale_price=wholesale_price,
        )
        context.user_data[UD_BUY_SERVER_ID] = server_id
        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = max(1, days // 30) if days > 0 else 1
        context.user_data["last_order_id"] = order.get("order_id", 0)
        context.user_data[UD_STATE] = STATE_RECEIPT_WAITING
        ps = get_payment_settings(agent_id)
        tx_settings = get_tx_plans_settings(agent_id)
        tx_marker = 0
        pay_price = price
        if bool((tx_settings or {}).get("random_tx_spec")):
            tx_marker = random.randint(101, 997)
            if tx_marker % 10 == 0:
                tx_marker += 1
            pay_price = price + tx_marker
            context.user_data["pending_tx_marker"] = tx_marker
        else:
            context.user_data.pop("pending_tx_marker", None)
        context.user_data["pending_pay_price"] = pay_price
        card_text = ps.get("card_to_card_text", "0")
        if card_text == "0":
            rial_price = pay_price * 10
            card_text = (
                i18n.t("pay_exact_rial", _lg, r=f"{rial_price:,}") + "\n"
                + i18n.t("pay_equiv_toman", _lg, p=f"{pay_price:,}") + "\n"
                + i18n.t("pay_card_number", _lg, c=card.get("number", "?")) + "\n"
                + i18n.t("pay_card_owner", _lg, o=card.get("owner", "?")) + "\n"
                + i18n.t("card_receipt_intro", _lg) + "\n\n"
                + i18n.t("buy_auto_done", _lg)
            )
            if tx_marker > 0:
                card_text = (
                    i18n.t("tx_spec_applied", _lg, m=tx_marker) + "\n\n"
                    f"{card_text}"
                )
        await msg.edit_text(card_text, parse_mode="Markdown", reply_markup=confirm_payment_inline_keyboard(lang=_lg))

    elif data == CB_BUY_BACK_MAIN:
        context.user_data.pop(UD_BUY_GB, None)
        context.user_data.pop(UD_BUY_MONTHS, None)
        servers = get_main_servers()
        if servers:
            sc = int(br.get("server_columns", 1))
            await msg.edit_text(
                get_localized_text(agent_id, "servers_list_text", _lg),
                parse_mode="Markdown",
                reply_markup=location_keyboard(servers, columns=sc, lang=_lg),
            )

    elif data == CB_BUY_EXIT_MAIN:
        context.user_data.pop(UD_BUY_GB, None)
        context.user_data.pop(UD_BUY_MONTHS, None)
        await _back_to_main_menu(msg, i18n.t("main_menu_title", _lg), lang=_lg)

    elif data.startswith(CB_BUY_MIXED_FIXED):
        server_id = int(parts[3]) if len(parts) > 3 else 0
        # استفاده از پلن‌های نماینده
        plans = get_fixed_plans(agent_id)
        if not plans:
            await msg.edit_text(
                i18n.t("no_plans_for_agent", _lg),
                reply_markup=_ikb([[InlineKeyboardButton(i18n.t("back", _lg), callback_data=CB_BUY_BACK_MAIN)]])
            )
            return
        await msg.edit_text(
            i18n.t("choose_plan", _lg),
            reply_markup=plans_keyboard(plans, server_id, 0, lang=_lg),
        )

    elif data.startswith(CB_BUY_MIXED_DYN):
        server_id = int(parts[3]) if len(parts) > 3 else 0
        dyn = _agent_dyn_settings(agent_id, server_id)
        _start_buy_wizard(context)
        gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
        months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_month", 1), 1)))
        step_gb = safe_int(dyn.get("step_gb", 1), 1)
        max_gb = safe_int(dyn.get("max_gb", 1000), 1000)
        gb = min(max(gb, safe_float(dyn.get("min_gb", 1), 1.0)), max_gb)
        min_month, max_month, _ = _dyn_month_limits(dyn)
        months = _clamp_months(months, min_month, max_month)
        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = months
        total, off_pct = _calc_dynamic_price(gb, months, dyn)
        await msg.edit_text(
            i18n.t("buy_hint", _lg),
            reply_markup=mixed_buy_keyboard(server_id, gb, months, total, off_percent=off_pct, lang=_lg),
        )


def _build_service_status_text(svc: dict, lang: str = "fa") -> str:
    """متن «📄اطلاعات اشتراک شما» با فرمت ربات کاربران"""
    return build_subscription_status_text(svc, {}, {}, lang=lang)
