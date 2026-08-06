import asyncio
from html import escape
from typing import Optional

from telegram import Update, InlineKeyboardMarkup
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from CustomerBot.constants import (
    UD_STATE, UD_BUY_GB, UD_BUY_MONTHS, UD_BUY_SERVER_ID, UD_BUY_PLAN_ID,
    UD_TICKET_QUESTION, UD_TICKET_MODE,
    STATE_RECEIPT_WAITING, STATE_TICKET_WAITING_TEXT, STATE_TICKET_WAITING_TITLE,
    STATE_TICKET_WAITING_PHOTO, STATE_TICKET_CONFIRM,
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
    BTN_PAY_DONE, BTN_BACK,
)
from CustomerBot.database import (
    get_buy_renew_settings, get_text_settings, get_subs_settings,
    get_payment_settings, get_marketing_settings, get_trial_spec_settings,
    get_force_join_settings, get_user, create_order, create_payment,
    get_user_tickets, get_ticket, get_ticket_messages,
    create_ticket, add_ticket_message, update_ticket_status, get_pending_payments,
    update_payment_status, get_payment_by_tx_code, set_got_free_trial,
    get_tx_plans_settings,
)
from Shared.agent_db import (
    upsert_customer, get_customer_by_telegram_id, get_services_by_customer,
    get_service_by_id, create_service, add_service_node,
    renew_service, set_service_active, calculate_wholesale_price,
    update_service,
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
    renew_wizard_keyboard,
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
    collect_all_direct_configs_from_api, get_or_create_bot_sub_links,
    sync_service_status_from_panels, regenerate_service_uuid,
    _resolve_live_server_title,
)
from Shared.qr_utils import make_qr_image


def _calc_dynamic_price(gb, months, dyn_settings) -> tuple[int, int]:
    settings = dyn_settings or {}
    gb_val = max(0, safe_int(gb, 0))
    months_val = max(0, safe_int(months, 0))
    price_per_gb = max(0, safe_int(settings.get("price_per_gb"), 2000))
    price_per_month = max(0, safe_int(settings.get("price_per_month"), 30000))
    base_price = (gb_val * price_per_gb) + (months_val * price_per_month)

    discount_step_gb = max(0, safe_int(settings.get("discount_step_gb"), 0))
    discount_percent_step = max(0, safe_int(settings.get("discount_percent_step"), 0))
    discount_percent_max = max(0, safe_int(settings.get("discount_percent_max"), 0))
    discount_tiered_enabled = bool(settings.get("discount_tiered_enabled", False))
    discount_simple_enabled = bool(settings.get("discount_simple_enabled", False))

    off_percent = 0
    if discount_tiered_enabled and settings.get("discount_tiers"):
        tiered_off = 0
        tiers = sorted(settings.get("discount_tiers", []), key=lambda t: int(t.get("gb", 0)))
        for tier in tiers:
            try:
                if gb_val >= int(tier.get("gb", 0)):
                    tiered_off = int(tier.get("percent", 0))
                else:
                    break
            except Exception:
                continue
        off_percent = max(off_percent, max(0, min(tiered_off, 100)))

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
        await query.answer("❌ خطا در پیکربندی", show_alert=True)
        return

    if is_rate_limited(f"cb_{user.id}_{data}"):
        await query.answer("⏳ لطفاً کمی صبر کنید.")
        return

    try:
        await query.answer()
    except Exception:
        pass

    # ---- Force Join ----
    if data == CB_FORCEJOIN_CHECK:
        await _handle_force_join_check(query, context, agent_id, user)

    # ---- Guide ----
    elif data.startswith("guide:"):
        await _handle_guide(query, context, agent_id, data)

    # ---- Support ----
    elif data.startswith("support:"):
        await _handle_support(query, context, agent_id, user, data)

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

async def _back_to_main_menu(msg, text: str = "🔙 بازگشت به منوی اصلی"):
    """Callback messages cannot be edited with ReplyKeyboardMarkup, so send it separately."""
    try:
        await msg.edit_text(text)
    except Exception:
        pass
    try:
        await msg.reply_text("منوی اصلی:", reply_markup=main_menu_keyboard())
    except Exception:
        pass


async def _handle_force_join_check(query, context, agent_id, user):
    fjs = get_force_join_settings(agent_id)
    if not fjs.get("enabled") or not fjs.get("channel_username"):
        await _back_to_main_menu(query.message, "✅ عضویت شما تایید شد!")
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
                fjs.get("guide_text", "شما هنوز عضو نشده‌اید."),
                reply_markup=force_join_keyboard(link),
            )
            return
    except Exception:
        await query.edit_message_text(
            fjs.get("guide_text", "شما هنوز عضو نشده‌اید."),
            reply_markup=force_join_keyboard(link),
        )
        return
    try:
        await query.message.delete()
    except Exception:
        try:
            await query.edit_message_text("✅ عضویت شما تایید شد!")
        except Exception:
            pass
    try:
        await query.message.reply_text("✅ عضویت شما تایید شد!", reply_markup=main_menu_keyboard())
    except Exception:
        pass


async def _handle_guide(query, context, agent_id, data):
    text_settings = get_text_settings(agent_id)
    parts = data.split(":", 2)
    if len(parts) < 2:
        return
    action = parts[1]
    back_token = parts[2] if len(parts) > 2 else "m"

    guide_map = {
        "android": text_settings.get("guide_android_text", ""),
        "ios": text_settings.get("guide_ios_text", ""),
        "windows": text_settings.get("guide_windows_text", ""),
        "mac": text_settings.get("guide_mac_text", ""),
        "linux": text_settings.get("guide_linux_text", ""),
    }

    if action == "back":
        if back_token == "m":
            await _back_to_main_menu(query.message, text_settings.get("guide_text", "انتخاب سیستم عامل ⬇️"))
        elif back_token.startswith("s:"):
            try:
                svc_id = int(back_token.split(":", 1)[1])
            except (IndexError, ValueError):
                svc_id = 0
            svc = get_service_by_id(svc_id)
            if svc:
                await query.message.edit_text(
                    _build_service_status_text(svc),
                    parse_mode="Markdown",
                    reply_markup=subscription_status_keyboard(svc_id),
                )
        return

    if action in guide_map and guide_map[action]:
        await query.edit_message_text(
            guide_map[action],
            reply_markup=guide_os_keyboard(back_token),
        )


async def _handle_support(query, context, agent_id, user, data):
    text_settings = get_text_settings(agent_id)
    msg = query.message

    if data == CB_SUPPORT_FAQ:
        faq = text_settings.get("faq_text", "❗️ سوالات متداول\n\nبه‌زودی تکمیل می‌شود.")
        await msg.edit_text(faq, reply_markup=support_panel_keyboard())

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
            await msg.edit_text("📬 شما هیچ تیکتی ندارید.", reply_markup=support_panel_keyboard())
            return
        await msg.edit_text(
            f"📬 تیکت‌های شما (صفحه {page}/{total_pages}):",
            reply_markup=user_tickets_list_keyboard(page_tickets, page, total_pages),
        )

    elif data == CB_SUPPORT_NEW:
        context.user_data[UD_STATE] = STATE_TICKET_WAITING_TITLE
        context.user_data[UD_TICKET_MODE] = "new"
        await msg.edit_text(
            "📩 <b>ایجاد تیکت جدید</b>\n\n"
            "لطفا موضوع تیکت خود را ارسال کنید (مثال: مشکل اتصال):",
            parse_mode="HTML",
        )
        await msg.reply_text(
            "✍️ موضوع تیکت:",
            reply_markup=cancel_keyboard(),
        )

    elif data.startswith(CB_SUPPORT_VIEW):
        parts = data.split(":")
        if len(parts) < 3:
            return
        code = int(parts[2])
        ticket = get_ticket(agent_id, code)
        if not ticket:
            await msg.edit_text("❌ تیکت یافت نشد.", reply_markup=support_panel_keyboard())
            return
        is_closed = ticket["status"] == "closed"
        can_reply = not is_closed
        messages = get_ticket_messages(agent_id, code)
        status_map = {"pending": "⏳ در انتظار", "open": "📬 باز", "closed": "✅ بسته"}
        status_fa = status_map.get(ticket.get("status", ""), ticket.get("status", ""))
        title = ticket.get("title", "") or ticket.get("question", "")[:50] or "بدون موضوع"
        text = (
            f"📩 <b>تیکت #{code}</b>\n"
            f"📋 موضوع: {title}\n"
            f"📅 {str(ticket.get('created_at', ''))[:16]}\n"
            f"📌 وضعیت: {status_fa}\n\n"
            f"━━━ پیام‌ها ━━━\n"
        )
        if not messages:
            text += "(پیامی وجود ندارد)"
        else:
            for m in messages:
                sender = "👤 شما" if m.get("sender_type") == "user" else f"🤖 {m.get('sender_name', 'پشتیبان')}"
                msg_text = m.get("message_text", "")
                has_photo = "📷 [عکس]" if m.get("photo_file_id") else ""
                ts = str(m.get("created_at", ""))[:16]
                text += f"\n{sender} ({ts}):\n{msg_text}\n{has_photo}\n"
        await msg.edit_text(text, reply_markup=user_ticket_detail_keyboard(code, can_reply, is_closed), parse_mode="HTML")
        for m in messages:
            photo_fid = m.get("photo_file_id", "")
            if photo_fid:
                sender = "شما" if m.get("sender_type") == "user" else m.get("sender_name", "پشتیبان")
                caption = f"📷 عکس تیکت #{code} - {sender}\n{m.get('message_text', '') or ''}"
                try:
                    await msg.reply_photo(photo=photo_fid, caption=caption[:1024])
                except Exception:
                    pass

    elif data.startswith(CB_SUPPORT_REPLY):
        code = int(data.split(":")[-1])
        context.user_data[UD_STATE] = STATE_TICKET_WAITING_TEXT
        context.user_data[UD_TICKET_MODE] = f"reply:{code}"
        await msg.edit_text(
            "📩 <b>پاسخ به تیکت</b>\n\n"
            "متن یا عکس خود را ارسال کنید:",
            parse_mode="HTML",
        )
        await msg.reply_text("✍️ پاسخ خود را بنویسید:", reply_markup=cancel_keyboard())

    elif data.startswith(CB_SUPPORT_CLOSE):
        code = int(data.split(":")[-1])
        update_ticket_status(agent_id, code, "closed")
        await msg.edit_text("✅ تیکت بسته شد.", reply_markup=support_panel_keyboard())

    elif data == "support:new:skip":
        pending = context.user_data.get("pending_ticket", {})
        if not pending:
            await query.answer("اطلاعات تیکت پیدا نشد.", show_alert=True)
            return
        context.user_data[UD_STATE] = STATE_TICKET_CONFIRM
        from CustomerBot.handlers.receipt import _format_ticket_confirm_text
        await msg.edit_text(_format_ticket_confirm_text(pending), reply_markup=ticket_confirm_keyboard("new"), parse_mode="HTML")

    elif data == "support:new:send":
        pending = context.user_data.get("pending_ticket", {})
        if not pending:
            await query.answer("اطلاعات تیکت پیدا نشد.", show_alert=True)
            return
        from CustomerBot.handlers.receipt import _notify_agent_new_ticket
        title = pending.get("title", "بدون موضوع")
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
                sender_name=user.full_name or user.username or "کاربر",
                message_text=question,
                photo_file_id=photo_fid,
            )
            await _notify_agent_new_ticket(context, agent_id, ticket, question, photo_fid)
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_TICKET_MODE, None)
            context.user_data.pop(UD_TICKET_QUESTION, None)
            context.user_data.pop("pending_ticket", None)
            await msg.edit_text(
                f"✅ تیکت شما با کد <b>{ticket['ticket_code']}</b> ثبت شد.\n"
                f"📋 موضوع: {title}\n\n"
                f"به زودی پاسخ داده می‌شود.",
                reply_markup=user_ticket_detail_keyboard(ticket["ticket_code"], can_reply=True, is_closed=False),
                parse_mode="HTML",
            )
        else:
            await query.answer("خطا در ثبت تیکت.", show_alert=True)

    elif data == "support:new:edit":
        context.user_data[UD_STATE] = STATE_TICKET_WAITING_TITLE
        context.user_data[UD_TICKET_MODE] = "new"
        context.user_data.pop("pending_ticket", None)
        await msg.edit_text("✏️ موضوع تیکت را دوباره ارسال کنید:", reply_markup=cancel_keyboard())

    elif data == "support:new:cancel":
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_TICKET_MODE, None)
        context.user_data.pop(UD_TICKET_QUESTION, None)
        context.user_data.pop("pending_ticket", None)
        await msg.edit_text("❌ ایجاد تیکت لغو شد.", reply_markup=support_panel_keyboard())

    elif data == CB_SUPPORT_BACK_MAIN or data == CB_SUPPORT_MENU:
        await msg.edit_text(
            text_settings.get("ticket_panel_text", "📩 برای ارتباط با پشتیبانی، پیام خود را ارسال کنید."),
            reply_markup=support_panel_keyboard(),
        )


async def _show_subscription_status(msg, agent_id, svc_id):
    """نمایش «📄اطلاعات اشتراک شما» + کیبورد وضعیت (مطابق ربات کاربران)"""
    subs_settings = get_subs_settings(agent_id)
    br = get_buy_renew_settings(agent_id)
    svc = get_service_by_id(svc_id)
    if not svc:
        await msg.edit_text("❌ سرویس یافت نشد.", reply_markup=main_menu_keyboard())
        return None
    show_detach = bool(svc.get("comment") == "connected")
    svc_text = build_subscription_status_text(svc, subs_settings, br)
    kb = subscription_status_keyboard(
        svc_id,
        show_direct_config=subs_settings.get("show_direct_config", True),
        show_sub_link=subs_settings.get("show_sub_link", True),
        show_detach=show_detach,
    )
    try:
        await msg.edit_text(svc_text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        try:
            await msg.reply_text(svc_text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
    return svc


async def _send_service_direct_configs(msg, svc):
    """کانفیگ‌های مستقیم: استخراج از لینک all.txt همه نودها + پشتیبان API پنل"""
    # دریافت لینک‌ها در thread جدا تا event loop ربات بلاک نشود
    links = await asyncio.to_thread(collect_all_direct_configs_for_service, svc)
    source_hint = ""
    if not links:
        links = await collect_all_direct_configs_from_api(svc)
        if links:
            source_hint = (
                "⚠️ دریافت مستقیم از لینک اشتراک محدود بود؛ "
                "کانفیگ‌ها از API پنل خوانده شد.\n\n"
            )
    base_urls = get_service_node_base_urls(svc)
    fallback_base = base_urls[0] if base_urls else ""

    if not links:
        msg_text = "❌ کانفیگی از لینک اشتراک استخراج نشد."
        if fallback_base:
            msg_text += f"\nمی‌توانید از لینک اشتراک استفاده کنید:\n{fallback_base}/all.txt"
        await msg.reply_text(msg_text, disable_web_page_preview=True)
        return

    server_title = _resolve_live_server_title(svc, default="")
    header = "🔗 کانفیگ‌های مستقیم"
    if server_title:
        header = f"{header} | {server_title}"
    clean_links = [str(x).strip() for x in links if str(x).strip()]

    all_links_text = "\n".join(clean_links)
    one_block_text = (
        f"{source_hint}{header}\n"
        "برای کپی، کل باکس زیر را یکجا کپی کنید:\n"
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
            "برای کپی، باکس زیر را کپی کنید:\n"
            f"<pre><code class=\"language-shell\">{escape(chr(10).join(chunk))}</code></pre>"
        )
        await msg.reply_text(part_text, parse_mode="HTML", disable_web_page_preview=True)


async def _send_subscription_link_with_qr(query, agent_id, svc, data):
    """ارسال لینک‌های اشتراک همراه با QR بارکد (مثل ربات کاربران)"""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    svc_id = int(svc.get("id") or 0)
    ss = get_subs_settings(agent_id)
    msg = query.message

    base_urls = get_service_node_base_urls(svc)
    if not base_urls:
        await msg.edit_text("❌ برای این سرویس لینک کانفیگ در دسترس نیست.", reply_markup=main_menu_keyboard())
        return
    base_url = base_urls[0]

    config_items = []
    if action == "sub_link":
        if not ss.get("show_sub_link", True):
            await msg.edit_text("❌ نمایش لینک اشتراک خاموش است.", reply_markup=main_menu_keyboard())
            return
        config_items.append(("🔗 لینک اشتراک:", f"{base_url}/all.txt"))
    elif action == "auto_sub":
        if not ss.get("show_auto_sub_link", False):
            await msg.edit_text("❌ نمایش اشتراک خودکار خاموش است.", reply_markup=main_menu_keyboard())
            return
        config_items.append(("🤖 لینک اشتراک خودکار:", f"{base_url}/sub/?asn=unknown"))
    elif action == "sub_b64":
        if not ss.get("show_sub_link_b64", False):
            await msg.edit_text("❌ نمایش لینک b64 خاموش است.", reply_markup=main_menu_keyboard())
            return
        config_items.append(("🔐 لینک اشتراک b64:", f"{base_url}/all.txt?base64=1"))
    elif action == "multi":
        if not ss.get("show_multi_server", False):
            await msg.edit_text("❌ نمایش لینک اشتراک هوشمند خاموش است.", reply_markup=main_menu_keyboard())
            return
        managed_link, _ = get_or_create_bot_sub_links(svc)
        config_items.append(("🌐 لینک اشتراک هوشمند:", managed_link))
    elif action == "multi_b64":
        if not ss.get("show_multi_server_b64", False):
            await msg.edit_text("❌ نمایش لینک اشتراک هوشمند b64 خاموش است.", reply_markup=main_menu_keyboard())
            return
        _, managed_link_b64 = get_or_create_bot_sub_links(svc)
        config_items.append(("🌐 لینک اشتراک هوشمند b64:", managed_link_b64))

    if not config_items:
        await msg.edit_text("❌ در حال حاضر هیچ لینکی برای نمایش فعال نیست.", reply_markup=main_menu_keyboard())
        return

    primary_link = config_items[0][1]
    qr_image = make_qr_image(primary_link)
    qr_caption = (
        "📄 جهت کپی شدن لینک اشتراک کافیست یک بار لینک زیر را لمس کنید 👇\n\n"
        f"<code>{escape(primary_link)}</code>"
    )
    try:
        await msg.reply_photo(
            photo=qr_image,
            caption=qr_caption,
            parse_mode="HTML",
            reply_markup=subscription_links_keyboard(svc_id),
        )
    except Exception:
        try:
            await msg.reply_text(
                qr_caption,
                parse_mode="HTML",
                reply_markup=subscription_links_keyboard(svc_id),
                disable_web_page_preview=True,
            )
        except Exception:
            pass

    config_text_lines = ["📝 لینک‌های اشتراک", ""]
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

    if data.startswith(CB_STATUS_LIST):
        svc_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if svc_id == 0:
            cust = get_customer_by_telegram_id(agent_id, user.id)
            if not cust:
                return
            from CustomerBot.services import refresh_service_status, is_customer_service_visible
            services = get_services_by_customer(cust["id"])
            for svc in services:
                await refresh_service_status(svc.get("id", 0))
            services = get_services_by_customer(cust["id"])
            visible = [s for s in services if is_customer_service_visible(s)]
            if not visible:
                await msg.edit_text("❌ سرویس فعالی ندارید.", reply_markup=main_menu_keyboard())
                return
            await msg.edit_text(
                "👇 یکی از اشتراک‌ها را انتخاب کنید:",
                reply_markup=services_list_keyboard(visible),
            )
            return
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text("❌ سرویس یافت نشد.", reply_markup=main_menu_keyboard())
            return
        await sync_service_status_from_panels(svc_id)
        await _show_subscription_status(msg, agent_id, svc_id)
        return

    elif data.startswith(CB_STATUS_CONFIGS):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        ss = get_subs_settings(agent_id)
        await msg.edit_text(
            "🔗 لطفا نوع اتصال را انتخاب کنید:",
            reply_markup=subscription_configs_keyboard(
                svc_id,
                show_direct_config=ss.get("show_direct_config", True),
                show_sub_link=ss.get("show_sub_link", True),
                show_auto_sub_link=ss.get("show_auto_sub_link", False),
                show_sub_link_b64=ss.get("show_sub_link_b64", False),
                show_multi_server=ss.get("show_multi_server", False),
                show_multi_server_b64=ss.get("show_multi_server_b64", False),
            ),
        )

    elif data.startswith(CB_STATUS_DIRECT) or data.startswith(CB_STATUS_DIRECTCFG):
        # کانفیگ مستقیم: استخراج از لینک اشتراک همه نودها (در غیر این صورت API پنل)
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text("❌ سرویس یافت نشد.")
            return
        await _send_service_direct_configs(msg, svc)

    elif data.startswith(CB_STATUS_SUB_LINK) or data.startswith(CB_STATUS_AUTO_SUB) \
            or data.startswith(CB_STATUS_SUB_B64) or data.startswith(CB_STATUS_MULTI) \
            or data.startswith(CB_STATUS_MULTI_B64):
        # لینک‌های اشتراک: ارسال QR بارکد + لینک (مثل ربات کاربران)
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text("❌ سرویس یافت نشد.")
            return
        await _send_subscription_link_with_qr(query, agent_id, svc, data)

    elif data.startswith(CB_STATUS_MENU):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        await _show_subscription_status(msg, agent_id, svc_id)

    elif data.startswith(CB_STATUS_REFRESH):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text("❌ سرویس یافت نشد.", reply_markup=main_menu_keyboard())
            return
        try:
            await msg.edit_text("� در حال به‌روزرسانی اطلاعات اشتراک...")
        except Exception:
            pass
        await sync_service_status_from_panels(svc_id)
        await _show_subscription_status(msg, agent_id, svc_id)

    elif data.startswith(CB_STATUS_RENAME):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text("❌ سرویس یافت نشد.", reply_markup=main_menu_keyboard())
            return
        context.user_data[UD_STATE] = f"rename:{svc_id}"
        try:
            await msg.edit_text("✏️ نام جدید اشتراک را وارد کنید:")
        except Exception:
            pass
        await msg.reply_text(
            "نام جدید را بفرستید:\n"
            "• حداقل 3 و حداکثر 64 کاراکتر\n"
            "برای انصراف، دکمه «بازگشت» را بزنید.",
            reply_markup=cancel_keyboard(),
        )

    elif data.startswith(CB_STATUS_REPLACE_LINK):
        if "confirm" in data:
            svc_id = int(parts[2]) if len(parts) > 2 else 0
            svc = get_service_by_id(svc_id)
            if not svc:
                await msg.edit_text("❌ سرویس یافت نشد.", reply_markup=main_menu_keyboard())
                return
            try:
                await msg.edit_text("⏳ در حال تغییر لینک اشتراک...")
            except Exception:
                pass
            ok, result_text, _new_uuid = await regenerate_service_uuid(svc)
            await msg.reply_text(result_text, reply_markup=main_menu_keyboard())
            if ok:
                await _show_subscription_status(msg, agent_id, svc_id)
        else:
            svc_id = int(parts[2]) if len(parts) > 2 else 0
            await msg.edit_text(
                "⚠️ هشدار تغییر لینک اشتراک\n\n"
                "با تغییر لینک اشتراک، لینک و کانفیگ قبلی از کار می‌افتد و باید لینک جدید را دوباره در برنامه وارد کنید.\n\n"
                "اگر مطمئن هستید، تایید تغییر لینک را بزنید.",
                reply_markup=replace_subscription_link_confirm_keyboard(svc_id),
            )

    elif data.startswith(CB_STATUS_DETACH):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if svc:
            set_service_active(svc_id, False)
            await msg.edit_text("✅ اشتراک جدا شد.", reply_markup=main_menu_keyboard())

    elif data.startswith(CB_STATUS_GUIDE):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        text_settings = get_text_settings(agent_id)
        await msg.edit_text(
            text_settings.get("guide_text", "راهنمای اتصال:\nلینک اشتراک را در کلاینت مورد نظر import کنید."),
            reply_markup=guide_os_keyboard(f"s:{svc_id}"),
        )

    elif data.startswith(CB_STATUS_RENEW):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.edit_text("❌ سرویس یافت نشد.", reply_markup=main_menu_keyboard())
            return
        br = get_buy_renew_settings(agent_id)
        if not br.get("enable_renew", True):
            await msg.edit_text("🚫 تمدید غیرفعال است.", reply_markup=main_menu_keyboard())
            return
        context.user_data["renew_target_service_id"] = int(svc_id)
        server_id = svc.get("server_id", 0)
        mode = str(get_setting(agent_id, "plan_display_mode", "dynamic") or "dynamic").strip().lower()
        if mode == "fixed":
            plans = get_fixed_plans(agent_id)
            if not plans:
                await msg.edit_text("❌ هیچ پلنی برای این نماینده تعریف نشده است.", reply_markup=main_menu_keyboard())
                return
            await msg.edit_text(
                "📋 پلن تمدید را انتخاب کنید:",
                reply_markup=plans_keyboard(plans, server_id, 0, callback_prefix="renew"),
            )
        else:
            dyn = get_plan_dynamic_settings(server_id)
            gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
            months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_months", 1), 1)))
            gb = min(max(gb, safe_float(dyn.get("min_gb", 1), 1.0)), safe_int(dyn.get("max_gb", 1000), 1000))
            months = max(months, 1)
            context.user_data[UD_BUY_GB] = gb
            context.user_data[UD_BUY_MONTHS] = months
            price, off_pct = _calc_dynamic_price(gb, months, dyn)
            await msg.edit_text(
                "🎛 بسته تمدید را انتخاب کنید:",
                reply_markup=renew_wizard_keyboard(server_id, gb, months, price, off_pct),
            )

    elif data.startswith(CB_STATUS_LIST_BACK):
        await _back_to_main_menu(msg, "🔙 بازگشت")


async def _handle_renew(query, context, agent_id, user, data):
    msg = query.message
    parts = data.split(":")

    if data.startswith(CB_RENEW_SVC):
        svc_id = int(parts[2]) if len(parts) > 2 else 0
        svc = get_service_by_id(svc_id)
        if not svc:
            await msg.reply_text("❌ سرویس یافت نشد.", reply_markup=main_menu_keyboard())
            return
        context.user_data["renew_target_service_id"] = int(svc_id)
        server_id = svc.get("server_id", 0)
        mode = str(get_setting(agent_id, "plan_display_mode", "dynamic") or "dynamic").strip().lower()
        if mode == "fixed":
            plans = get_fixed_plans(agent_id)
            if not plans:
                await msg.reply_text("❌ هیچ پلنی برای این نماینده تعریف نشده است.", reply_markup=main_menu_keyboard())
                return
            await msg.reply_text(
                "📋 پلن تمدید را انتخاب کنید:",
                reply_markup=plans_keyboard(plans, server_id, 0, callback_prefix="renew"),
            )
        else:
            dyn = get_plan_dynamic_settings(server_id)
            gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
            months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_months", 1), 1)))
            gb = min(max(gb, safe_float(dyn.get("min_gb", 1), 1.0)), safe_int(dyn.get("max_gb", 1000), 1000))
            months = max(months, 1)
            context.user_data[UD_BUY_GB] = gb
            context.user_data[UD_BUY_MONTHS] = months
            price, off_pct = _calc_dynamic_price(gb, months, dyn)
            await msg.reply_text(
                "🎛 بسته تمدید را انتخاب کنید:",
                reply_markup=renew_wizard_keyboard(server_id, gb, months, price, off_pct),
            )

    elif data.startswith("rwiz:"):
        # wizard تمدید پویا — تنظیم حجم/ماه
        server_id = int(parts[1]) if len(parts) > 1 else 0
        wiz_action = parts[2] if len(parts) > 2 else ""
        dyn = get_plan_dynamic_settings(server_id)
        gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
        months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_months", 1), 1)))
        step_gb = safe_int(dyn.get("step_gb", 1), 1)
        max_gb = safe_int(dyn.get("max_gb", 1000), 1000)
        min_gb = safe_float(dyn.get("min_gb", 1), 1.0)
        if wiz_action == "gb_inc":
            gb = min(gb + step_gb, max_gb)
        elif wiz_action == "gb_dec":
            gb = max(min_gb, gb - step_gb)
        elif wiz_action == "month_inc":
            months += 1
        elif wiz_action == "month_dec":
            months = max(1, months - 1)
        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = months
        price, off_pct = _calc_dynamic_price(gb, months, dyn)
        try:
            await msg.edit_text(
                "🎛 بسته تمدید را انتخاب کنید:",
                reply_markup=renew_wizard_keyboard(server_id, gb, months, price, off_pct),
            )
        except Exception:
            pass

    elif data.startswith("renew:confirm_dyn:"):
        # تایید تمدید پویا
        server_id = int(parts[2]) if len(parts) > 2 else 0
        service_id = int(context.user_data.get("renew_target_service_id") or 0)
        if not service_id:
            await msg.reply_text("❌ سرویس مورد نظر برای تمدید پیدا نشد.", reply_markup=main_menu_keyboard())
            return
        svc = get_service_by_id(service_id)
        if not svc:
            await msg.reply_text("❌ سرویس یافت نشد.", reply_markup=main_menu_keyboard())
            return
        dyn = get_plan_dynamic_settings(server_id)
        gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
        months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_months", 1), 1)))
        days = months * 30
        extra_gb = float(gb)
        ok = await asyncio.to_thread(renew_service, service_id, days, extra_gb)
        context.user_data.pop("renew_target_service_id", None)
        context.user_data.pop(UD_BUY_GB, None)
        context.user_data.pop(UD_BUY_MONTHS, None)
        if not ok:
            await msg.reply_text("❌ تمدید اشتراک انجام نشد. لطفاً دوباره تلاش کنید.", reply_markup=main_menu_keyboard())
            return
        await _show_subscription_status(msg, agent_id, service_id)

    elif data.startswith("renew:plan:"):
        plan_id = int(parts[3]) if len(parts) > 3 else 0
        service_id = int(context.user_data.get("renew_target_service_id") or 0)
        if not service_id:
            await msg.reply_text("❌ سرویس مورد نظر برای تمدید پیدا نشد.", reply_markup=main_menu_keyboard())
            return
        svc = get_service_by_id(service_id)
        if not svc:
            await msg.reply_text("❌ سرویس یافت نشد.", reply_markup=main_menu_keyboard())
            return
        plan = get_fixed_plan(agent_id, plan_id)
        if not plan:
            await msg.reply_text("❌ پلن انتخابی نامعتبر است.", reply_markup=main_menu_keyboard())
            return
        extra_days = int(plan.get("days") or 0)
        if extra_days <= 0:
            extra_days = 30
        extra_gb = float(plan.get("gb") or 0)
        ok = await asyncio.to_thread(renew_service, service_id, extra_days, extra_gb)
        context.user_data.pop("renew_target_service_id", None)
        if not ok:
            await msg.reply_text("❌ تمدید اشتراک انجام نشد. لطفاً دوباره تلاش کنید.", reply_markup=main_menu_keyboard())
            return
        await _show_subscription_status(msg, agent_id, service_id)

    elif data.startswith(CB_RENEW_BACK):
        context.user_data.pop("renew_target_service_id", None)
        await _back_to_main_menu(msg, "🔙 بازگشت")


async def _handle_pay(query, context, agent_id, user, data):
    msg = query.message

    if data == CB_PAY_RECEIPT_DONE:
        context.user_data[UD_STATE] = "wallet_receipt_photo"
        # حذف پیام دارای دکمه اینلاین و ارسال پیام جدید با دکمه بازگشت در کیبورد پایین (مثل ربات کاربران)
        try:
            await msg.delete()
        except Exception:
            pass
        await query.get_bot().send_message(
            chat_id=query.from_user.id,
            text="⬇️ لطفا رسید پرداخت خود را در زیر این پیام ارسال کنید:",
            reply_markup=receipt_cancel_keyboard(),
        )

    elif data == CB_PAY_CANCEL:
        context.user_data.pop(UD_STATE, None)
        await _back_to_main_menu(msg, "❌ پرداخت لغو شد.")


async def _handle_trial(query, context, agent_id, user, data):
    msg = query.message
    parts = data.split(":")

    if data.startswith(CB_TRIAL_LOC):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        u_db = get_user(agent_id, user.id)
        if u_db and u_db.get("got_free_trial"):
            await msg.edit_text("🚫 شما قبلا تست رایگان دریافت کرده‌اید.", reply_markup=main_menu_keyboard())
            return
        server = get_server_by_id(server_id)
        if not server:
            await msg.edit_text("❌ سرور یافت نشد.", reply_markup=main_menu_keyboard())
            return
        trial = get_trial_spec_settings(agent_id)
        gb = trial.get("usage_gb", 1)
        days = trial.get("days", 1)
        from Shared.hiddify_api import create_user
        import uuid
        new_uuid = str(uuid.uuid4())
        payload = {
            "name": f"تست رایگان {gb}GB",
            "usage_limit_GB": gb,
            "package_days": days,
            "uuid": new_uuid,
            "is_active": True,
        }
        result = await create_user(server, payload)
        if not result:
            await msg.edit_text("❌ خطا در ایجاد سرویس تست.", reply_markup=main_menu_keyboard())
            return
        cust = get_customer_by_telegram_id(agent_id, user.id)
        if not cust:
            cust_id = upsert_customer(agent_id, user.id, user.username or "", user.full_name or "")
        else:
            cust_id = cust["id"]
        svc = create_service(
            agent_id=agent_id,
            customer_id=cust_id,
            server_id=server_id,
            server_title=server.get("title", ""),
            name=f"تست رایگان {gb}GB",
            panel_user_uuid=new_uuid,
            usage_limit=float(gb),
            days=days,
            sale_price=0,
            is_trial=1,
        )
        add_service_node(
            service_id=svc["id"],
            server_id=server_id,
            server_title=server.get("title", ""),
            panel_user_uuid=new_uuid,
            panel_user_id=str(result.get("id", "")),
        )
        set_got_free_trial(agent_id, user.id)
        domains = server.get("domains", [])
        domain = domains[0]["domain"] if domains else server.get("host", "?")
        link = f"https://{domain}/{new_uuid}"
        await msg.edit_text(
            f"🎉 سرویس تست {gb}GB - {days} روزه با موفقیت ایجاد شد!\n\n"
            f"🔗 لینک اشتراک:\n`{link}`\n\n"
            f"(لینک را در کلاینت کپی کنید)",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )

    elif data == CB_TRIAL_BACK:
        await _back_to_main_menu(msg, "🔙 بازگشت")


async def _handle_buy(query, context, agent_id, user, data):
    msg = query.message
    parts = data.split(":")
    action = parts[0]
    text_settings = get_text_settings(agent_id)
    br = get_buy_renew_settings(agent_id)

    if action == "wiz":
        server_id = int(parts[1]) if len(parts) > 1 else 0
        wiz_action = parts[2] if len(parts) > 2 else ""
        dyn = get_plan_dynamic_settings(server_id)
        gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
        months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_months", 1), 1)))
        step_gb = safe_int(dyn.get("step_gb", 1), 1)
        max_gb = safe_int(dyn.get("max_gb", 1000), 1000)

        if wiz_action == "gb_inc":
            gb = min(gb + step_gb, max_gb)
        elif wiz_action == "gb_dec":
            gb = max(safe_float(dyn.get("min_gb", 1), 1.0), gb - step_gb)
        elif wiz_action == "month_inc":
            months += 1
        elif wiz_action == "month_dec":
            months = max(1, months - 1)
        elif wiz_action == "show_fixed":
            # استفاده از پلن‌های نماینده
            plans = get_fixed_plans(agent_id)
            if not plans:
                await msg.edit_text(
                    "❌ هیچ پلنی برای این نماینده تعریف نشده است.",
                    reply_markup=_ikb([[InlineKeyboardButton("🔙 بازگشت", callback_data=CB_BUY_BACK_MAIN)]])
                )
                return
            await msg.edit_text(
                "📋 پلن مورد نظر را انتخاب کنید:",
                reply_markup=plans_keyboard(plans, server_id, 0),
            )
            return

        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = months
        pgb = safe_int(dyn.get("price_per_gb", 0), 0)
        pmo = safe_int(dyn.get("price_per_month", 0), 0)
        total = int(gb * pgb) + (months * pmo)

        mode = str(get_setting(agent_id, "plan_display_mode", "dynamic") or "dynamic").strip().lower()
        if mode == "mixed":
            await msg.edit_text(
                "🎛 بسته دلخواه خود را بسازید:",
                reply_markup=mixed_buy_keyboard(server_id, gb, months, total),
            )
        else:
            await msg.edit_text(
                "🎛 بسته دلخواه خود را بسازید:",
                reply_markup=buy_wizard_keyboard(server_id, gb, months, total),
            )

    elif data.startswith(CB_BUY_LOC):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        mode = str(get_setting(agent_id, "plan_display_mode", "dynamic") or "dynamic").strip().lower()

        if mode == "fixed":
            plans = get_fixed_plans(agent_id)
            if not plans:
                await msg.edit_text(
                    "❌ هیچ پلنی برای این نماینده تعریف نشده است.",
                    reply_markup=_ikb([[InlineKeyboardButton("🔙 بازگشت", callback_data=CB_BUY_BACK_MAIN)]])
                )
                return
            await msg.edit_text(
                text_settings.get("plans_list_text", "🛒 لطفاً پلن مورد نظر را انتخاب کنید:"),
                reply_markup=plans_keyboard(plans, server_id, 0),
            )
        elif mode == "mixed":
            await msg.edit_text(
                text_settings.get("plans_list_text", "🛒 لطفاً روش خرید را انتخاب کنید:"),
                reply_markup=mixed_mode_keyboard(server_id),
            )
        else:
            dyn = get_plan_dynamic_settings(server_id)
            gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
            months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_months", 1), 1)))
            step_gb = safe_int(dyn.get("step_gb", 1), 1)
            max_gb = safe_int(dyn.get("max_gb", 1000), 1000)
            gb = min(max(gb, safe_float(dyn.get("min_gb", 1), 1.0)), max_gb)
            months = max(months, 1)
            context.user_data[UD_BUY_GB] = gb
            context.user_data[UD_BUY_MONTHS] = months
            pgb = safe_int(dyn.get("price_per_gb", 0), 0)
            pmo = safe_int(dyn.get("price_per_month", 0), 0)
            total = int(gb * pgb) + (months * pmo)
            await msg.edit_text(
                "🎛 بسته دلخواه خود را بسازید:",
                reply_markup=buy_wizard_keyboard(server_id, gb, months, total),
            )

    elif data.startswith(CB_BUY_CAT):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        cat_id = int(parts[3]) if len(parts) > 3 else 0
        # استفاده از پلن‌های نماینده با فیلتر دسته‌بندی
        all_plans = get_fixed_plans(agent_id, category_id=cat_id)
        tx = get_tx_plans_settings(agent_id)
        await msg.edit_text(
            "📋 پلن مورد نظر را انتخاب کنید:",
            reply_markup=plans_keyboard(
                all_plans, server_id, cat_id,
                sort_by_priority=tx.get("plan_sort_by_priority", True),
            ),
        )

    elif data.startswith(CB_BUY_PLAN):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        plan_id = int(parts[3]) if len(parts) > 3 else 0
        # جستجو در پلن‌های نماینده
        p = get_fixed_plan(agent_id, plan_id)
        if not p:
            await msg.edit_text("❌ پلن یافت نشد.", reply_markup=main_menu_keyboard())
            return
        price = safe_int(p.get("price", 0))
        gb = safe_float(p.get("gb", 0))
        days = safe_int(p.get("days", 0))
        context.user_data[UD_BUY_SERVER_ID] = server_id
        context.user_data[UD_BUY_PLAN_ID] = plan_id
        await msg.edit_text(
            f"📄 اطلاعات پلن انتخاب شده\n\n"
            f"📊 حجم: {gb:g} گیگ\n"
            f"⏳ زمان: {days} روز\n"
            f"💰 قیمت: {price:,} تومان\n\n"
            "💳 لطفاً روش پرداخت را انتخاب کنید:",
            reply_markup=selected_plan_keyboard(server_id, int(gb), days, price),
        )

    elif data.startswith(CB_BUY_CONFIRM_DYN):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        dyn = get_plan_dynamic_settings(server_id) or {}
        gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)) or 0)
        months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_months", 1), 1)) or 0)
        gb = max(gb, int(safe_float(dyn.get("min_gb", 1), 1.0)))
        months = max(months, 1)
        days = months * 30
        price, _ = _calc_dynamic_price(gb, months, dyn)
        context.user_data[UD_BUY_SERVER_ID] = server_id
        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = months
        await msg.edit_text(
            f"📄 اطلاعات پلن انتخاب شده\n\n"
            f"📊 حجم: {gb:g} گیگ\n"
            f"⏳ زمان: {days} روز\n"
            f"💰 قیمت: {price:,} تومان\n\n"
            "💳 لطفاً روش پرداخت را انتخاب کنید:",
            reply_markup=selected_plan_keyboard(server_id, int(gb), days, price),
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
            f"📄 اطلاعات پلن انتخاب شده\n\n"
            f"📊 حجم: {gb:g} گیگ\n"
            f"⏳ زمان: {days} روز\n"
            f"💰 قیمت: {price:,} تومان\n\n"
            "💳 لطفاً روش پرداخت را انتخاب کنید:",
            reply_markup=selected_plan_keyboard(server_id, int(gb), days, price),
        )

    elif data.startswith(CB_BUY_PAY_DIRECT):
        server_id = int(parts[2]) if len(parts) > 2 else 0
        gb = int(parts[3]) if len(parts) > 3 else 0
        days = int(parts[4]) if len(parts) > 4 else 0
        price = int(parts[5]) if len(parts) > 5 else 0
        from Shared.database import get_random_card
        card = get_random_card()
        if not card:
            await msg.edit_text("❌ کارتی برای پرداخت وجود ندارد.", reply_markup=main_menu_keyboard())
            return
        wholesale_price = calculate_wholesale_price(agent_id, gb, days, server_id)
        order = create_order(
            agent_id=agent_id,
            telegram_id=user.id,
            volume_gb=float(gb),
            days=days,
            price=price,
            plan_title=f"بسته {gb}GB-{days}D",
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
        card_text = ps.get("card_to_card_text", "0")
        if card_text == "0":
            rial_price = price * 10
            card_text = (
                f"💰 لطفا دقیقا مبلغ: `{rial_price}` ریال\n"
                f"💰 معادل: `{price}` تومان\n"
                f"💳 به شماره کارت: `{card.get('number', '?')}`\n"
                f"👤 به نام: {card.get('owner', '?')}\n"
                f"❗️ بعد از واریز مبلغ اسکرین شات از تراکنش برای ما ارسال کنید.\n\n"
                f"⚡️ پس از تایید پرداخت، اشتراک شما به‌صورت خودکار ساخته و ارسال می‌شود."
            )
        await msg.edit_text(card_text, parse_mode="Markdown", reply_markup=confirm_payment_inline_keyboard())

    elif data == CB_BUY_BACK_MAIN:
        servers = get_main_servers()
        if servers:
            sc = int(br.get("server_columns", 1))
            await msg.edit_text(
                text_settings.get("servers_list_text", "📡 لطفاً لوکیشن را انتخاب کنید:"),
                parse_mode="Markdown",
                reply_markup=location_keyboard(servers, columns=sc),
            )

    elif data == CB_BUY_EXIT_MAIN:
        await _back_to_main_menu(msg, "🔙 بازگشت به منوی اصلی")

    elif data.startswith(CB_BUY_MIXED_FIXED):
        server_id = int(parts[3]) if len(parts) > 3 else 0
        # استفاده از پلن‌های نماینده
        plans = get_fixed_plans(agent_id)
        if not plans:
            await msg.edit_text(
                "❌ هیچ پلنی برای این نماینده تعریف نشده است.",
                reply_markup=_ikb([[InlineKeyboardButton("🔙 بازگشت", callback_data=CB_BUY_BACK_MAIN)]])
            )
            return
        await msg.edit_text(
            "📋 پلن مورد نظر را انتخاب کنید:",
            reply_markup=plans_keyboard(plans, server_id, 0),
        )

    elif data.startswith(CB_BUY_MIXED_DYN):
        server_id = int(parts[3]) if len(parts) > 3 else 0
        dyn = get_plan_dynamic_settings(server_id)
        gb = int(context.user_data.get(UD_BUY_GB, safe_float(dyn.get("min_gb", 1), 1.0)))
        months = int(context.user_data.get(UD_BUY_MONTHS, safe_int(dyn.get("min_months", 1), 1)))
        step_gb = safe_int(dyn.get("step_gb", 1), 1)
        max_gb = safe_int(dyn.get("max_gb", 1000), 1000)
        gb = min(max(gb, safe_float(dyn.get("min_gb", 1), 1.0)), max_gb)
        months = max(months, 1)
        context.user_data[UD_BUY_GB] = gb
        context.user_data[UD_BUY_MONTHS] = months
        total = int(gb * safe_int(dyn.get("price_per_gb", 0), 0)) + (months * safe_int(dyn.get("price_per_month", 0), 0))
        await msg.edit_text(
            "🎛 بسته دلخواه خود را بسازید:",
            reply_markup=mixed_buy_keyboard(server_id, gb, months, total),
        )


def _build_service_status_text(svc: dict) -> str:
    """متن «📄اطلاعات اشتراک شما» با فرمت ربات کاربران"""
    return build_subscription_status_text(svc, {}, {})
