"""Callback branch handlers extracted verbatim from UserBot/main.py inline_handler."""

from Shared import userbot_db
from Shared.qr_utils import make_qr_image
from UserBot.utils.helpers import _parse_service_comment
from html import escape
from UserBot.keyboards import InlineKeyboardButton, InlineKeyboardMarkup, cancel_keyboard, guide_os_keyboard, i18n, location_keyboard, receipt_cancel_keyboard, replace_subscription_link_confirm_keyboard, subscription_configs_keyboard, subscription_links_keyboard, subscription_status_keyboard, support_panel_keyboard, ticket_confirm_keyboard, user_ticket_detail_keyboard, user_tickets_list_keyboard


def bind_main_namespace(ns: dict) -> None:
    """Bind main-module names needed by extracted branches (called once from main.py)."""
    globals().update(ns)


async def _cb_lang_set(update, context, query, data, user_id):
    new_lang = data.split(":")[2].strip().lower()
    if not i18n.is_supported(new_lang):
        new_lang = "fa"
    try:
        userbot_db.set_user_language(user_id, new_lang)
    except Exception as e:
        logger.warning("set_user_language failed user=%s: %s", user_id, e)
    try:
        await query.answer()
    except Exception:
        pass
    lang_name = i18n.lang_display_name(new_lang)
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=user_id,
        text=i18n.t("lang_changed", new_lang, lang_name=lang_name),
        reply_markup=_main_menu_keyboard(lang=new_lang),
    )
    return


async def _cb_guide(update, context, query, data, user_id, text_settings):
    await query.answer()
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    back_token = parts[2] if len(parts) > 2 else "m"

    if action == "back":
        if back_token.startswith("s") and back_token[1:].isdigit():
            service_id = int(back_token[1:])
            service = userbot_db.get_service_by_id(service_id)
            settings = _get_subscription_settings()
            if not service:
                await _safe_edit_message_text(query, "❌ سرویس موردنظر یافت نشد یا حذف شده است.")
                return
            service = await _sync_service_runtime_from_panels(service)
            await _safe_edit_message_text(
                query,
                _build_subscription_status_text(service),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(
                    service_id,
                    show_direct_config=settings.get("show_direct_config", True),
                    show_sub_link=settings.get("show_sub_link", True),
                    show_configs=_should_show_configs_button(settings),
                    show_detach=_is_connected_service(service),
                ),
            )
            return
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if action in {"android", "ios", "windows", "mac", "linux"}:
        await context.bot.send_message(
            chat_id=user_id,
            text=_guide_platform_text(action, text_settings),
        )
        return

    await query.answer("گزینه نامعتبر است.", show_alert=True)
    return


async def _cb_invite(update, context, query, data, user_id, text_settings):
    if True:
        await query.answer()
        action = data.split(":", 1)[1].strip().lower() if ":" in data else ""
        u_db = userbot_db.get_user_by_telegram_id(user_id) or {}
        internal_uid = int(u_db.get("id") or 0)

        if action == "get_banner":
            bot_username = await _get_user_bot_username(context)
            code = ""
            try:
                code = userbot_db.get_or_create_user_referral_code(internal_uid)
            except Exception:
                code = ""
            invite_link = (
                f"https://t.me/{bot_username}?start=ref_{code}"
                if bot_username and code
                else "لینک دعوت هنوز تنظیم نشده است."
            )
            banner_text = _format_text_template(
                text_settings.get("invite_banner_text")
                or "🎁 بنر دعوت اختصاصی شما\n\n🔗 لینک دعوت شما:\n{invite_link}",
                invite_link=invite_link,
            )
            photo_id = str(text_settings.get("invite_banner_photo_id") or "").strip()
            if photo_id:
                try:
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=photo_id,
                        caption=banner_text,
                    )
                    return
                except Exception:
                    pass
            await context.bot.send_message(chat_id=user_id, text=banner_text)
            return

        if action == "rewards":
            try:
                stats = userbot_db.get_referral_user_stats(internal_uid)
            except Exception:
                stats = {}
            settings = userbot_db.get_referral_settings()
            text = (
                "🎁 جوایز من\n"
                "❖ ◈━━━━━━━━━━━━━━━◈ ❖\n"
                f"🤝 پاداش تست: {int(settings.get('trial_reward_amount') or 0):,} تومان\n"
                f"🛒 پاداش خرید اول: {int(settings.get('purchase_reward_amount') or 0):,} تومان\n"
                f"🎯 جوایز دریافتی: {int(stats.get('paid_rewards_count') or 0)}\n"
                f"💰 مجموع پاداش‌ها: {int(stats.get('total_rewards') or 0):,} تومان\n"
            )
            await context.bot.send_message(chat_id=user_id, text=text)
            return

        if action == "list":
            try:
                refs, total = userbot_db.list_referrals(limit=20, inviter_id=internal_uid)
            except Exception:
                refs, total = [], 0
            if not refs:
                await context.bot.send_message(chat_id=user_id, text="👥 هنوز کسی را دعوت نکرده‌اید.")
                return
            lines = [f"👥 دعوت‌های من ({total} نفر)\n❖ ◈━━━━━━━━━━━━━━━◈ ❖"]
            for idx, ref in enumerate(refs, start=1):
                invitee_name = str(ref.get("invitee_full_name") or ref.get("invitee_username") or "—").strip()
                status_icon = "✅" if str(ref.get("status") or "") == "active" else "❌"
                lines.append(f"{idx}. {invitee_name} | {status_icon}")
            await context.bot.send_message(chat_id=user_id, text="\n".join(lines))
            return

        if action == "stats":
            try:
                stats = userbot_db.get_referral_user_stats(internal_uid)
            except Exception:
                stats = {}
            text = (
                "📊 آمار دعوت\n"
                "❖ ◈━━━━━━━━━━━━━━━◈ ❖\n"
                f"👥 کل دعوت‌ها: {int(stats.get('total_referrals') or 0)}\n"
                f"✅ دعوت‌های موفق: {int(stats.get('successful_referrals') or 0)}\n"
                f"⏳ در انتظار خرید: {int(stats.get('pending_purchase') or 0)}\n"
                f"🧪 پاداش‌های تست: {int(stats.get('trial_rewards_count') or 0)}\n"
                f"🛒 پاداش‌های خرید: {int(stats.get('purchase_rewards_count') or 0)}\n"
            )
            await context.bot.send_message(chat_id=user_id, text=text)
            return

        if action == "history":
            try:
                rewards, total = userbot_db.list_referral_rewards(limit=20, inviter_id=internal_uid)
            except Exception:
                rewards, total = [], 0
            if not rewards:
                await context.bot.send_message(chat_id=user_id, text="📜 هنوز پاداشی دریافت نکرده‌اید.")
                return
            labels = userbot_db.REFERRAL_REWARD_LABELS
            lines = [f"📜 تاریخچه جوایز ({total} مورد)\n❖ ◈━━━━━━━━━━━━━━━◈ ❖"]
            for idx, rw in enumerate(rewards, start=1):
                rtype = str(rw.get("reward_type") or "")
                label = labels.get(rtype, rtype)
                amount = int(rw.get("amount_toman") or 0)
                status_icon = "✅" if str(rw.get("status") or "") == "paid" else "❌"
                created = str(rw.get("created_at") or "")[:10]
                lines.append(f"{idx}. {label} | {amount:,} تومان | {status_icon} {created}")
            await context.bot.send_message(chat_id=user_id, text="\n".join(lines))
            return
        return


async def _cb_support(update, context, query, data, user_id, text_settings):
    if True:
        await query.answer()
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""

        if action == "noop":
            return

        if action == "adminmsg":
            sub = parts[2] if len(parts) > 2 else ""
            if sub == "reply":
                set_user_step(context, user_id, "WAIT_ADMIN_DIRECT_REPLY_TEXT")
                await context.bot.send_message(
                    chat_id=user_id,
                    text="📩 لطفا پاسخ خود را ارسال کنید:",
                )
                return
            await query.answer("گزینه نامعتبر است.", show_alert=True)
            return

        if action == "back_main":
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=user_id,
                text="🏠 منوی اصلی",
                reply_markup=_main_menu_keyboard(),
            )
            return

        if action == "menu":
            await _send_support_panel(context=context, user_id=user_id, message=query.message, text_settings=text_settings)
            return

        if action == "faq":
            faq_text = str(text_settings.get("faq_text") or "").strip()
            if (not faq_text) or ("به‌زودی تکمیل می‌شود" in faq_text) or ("به زودی تکمیل می شود" in faq_text):
                faq_text = _default_faq_text()
            try:
                await query.message.edit_text(faq_text, reply_markup=support_panel_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=user_id, text=faq_text, reply_markup=support_panel_keyboard())
            return

        if action == "new" and len(parts) == 2:
            set_user_step(context, user_id, "WAIT_TICKET_TITLE")
            context.user_data[f"pending_ticket_{user_id}"] = {}
            await context.bot.send_message(
                chat_id=user_id,
                text="✍️ لطفا موضوع درخواست خود را ارسال نمایید:",
                reply_markup=_ticket_text_cancel_keyboard(),
            )
            return

        if action == "my":
            try:
                page = int(parts[2]) if len(parts) > 2 else 1
            except Exception:
                page = 1
            page = max(1, page)
            user_row = userbot_db.get_user_by_telegram_id(user_id)
            if not user_row:
                uid = userbot_db.upsert_user(user_id, query.from_user.username, query.from_user.full_name)
                user_row = userbot_db.get_user_by_id(uid) or {}
            internal_uid = int((user_row or {}).get("id") or 0)
            tickets, total = userbot_db.get_tickets_for_user(internal_uid, page=page, page_size=18)
            total_pages = max(1, (int(total) + 17) // 18)
            if page > total_pages:
                page = total_pages
                tickets, total = userbot_db.get_tickets_for_user(internal_uid, page=page, page_size=18)
            header = (
                "📬 تیکت‌های من\n"
                f"🔸 تعداد کل تیکت‌ها: {int(total)}\n"
                "شماره تیکت موردنظر را انتخاب کنید:"
            )
            kb = user_tickets_list_keyboard(tickets, page, total_pages)
            try:
                await query.message.edit_text(header, reply_markup=kb)
            except Exception:
                await context.bot.send_message(chat_id=user_id, text=header, reply_markup=kb)
            return

        if action == "view":
            code = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 0
            if code <= 0:
                await query.answer("تیکت نامعتبر است.", show_alert=True)
                return
            user_row = userbot_db.get_user_by_telegram_id(user_id)
            internal_uid = int((user_row or {}).get("id") or 0)
            ticket = userbot_db.get_user_ticket_by_code(internal_uid, code)
            if not ticket:
                await query.answer("تیکت یافت نشد.", show_alert=True)
                return
            messages = userbot_db.get_ticket_messages(code)
            shot_links = await _build_user_ticket_screenshot_links(context, code, messages)
            text = _ticket_detail_text(ticket, messages, screenshot_links=shot_links)
            is_closed = str(ticket.get("status") or "").strip().lower() == "closed"
            can_reply = not is_closed
            try:
                await query.message.edit_text(
                    text,
                    reply_markup=user_ticket_detail_keyboard(code, can_reply=can_reply, is_closed=is_closed),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=user_ticket_detail_keyboard(code, can_reply=can_reply, is_closed=is_closed),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            return

        if action == "reopen":
            await query.answer("⛔️ این تیکت توسط شما بسته شده و قابل بازکردن نیست.", show_alert=True)
            return

        if action == "close":
            code = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 0
            if code <= 0:
                await query.answer("تیکت نامعتبر است.", show_alert=True)
                return
            user_row = userbot_db.get_user_by_telegram_id(user_id)
            internal_uid = int((user_row or {}).get("id") or 0)
            ticket = userbot_db.get_user_ticket_by_code(internal_uid, code)
            if not ticket:
                await query.answer("تیکت یافت نشد.", show_alert=True)
                return
            current_status = str(ticket.get("status") or "").strip().lower()
            if current_status == "closed":
                await query.answer("این تیکت قبلا بسته شده است.")
                return
            ok = userbot_db.set_ticket_status(code, "closed")
            if not ok:
                await query.answer("تغییر وضعیت انجام نشد.", show_alert=True)
                return
            fresh = userbot_db.get_user_ticket_by_code(internal_uid, code) or ticket
            messages = userbot_db.get_ticket_messages(code)
            shot_links = await _build_user_ticket_screenshot_links(context, code, messages)
            detail_text = _ticket_detail_text(fresh, messages, screenshot_links=shot_links)
            is_closed = str(fresh.get("status") or "").strip().lower() == "closed"
            can_reply = not is_closed
            notice = "✅ تیکت بسته شد."
            try:
                await query.answer(notice)
            except Exception:
                pass
            try:
                await query.message.edit_text(
                    detail_text,
                    reply_markup=user_ticket_detail_keyboard(code, can_reply=can_reply, is_closed=is_closed),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=detail_text,
                    reply_markup=user_ticket_detail_keyboard(code, can_reply=can_reply, is_closed=is_closed),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            return

        if action == "reply":
            reply_key = f"ticket_reply_{user_id}"
            sub = parts[2] if len(parts) > 2 else ""

            if str(sub).isdigit():
                code = int(sub)
                if code <= 0:
                    await query.answer("تیکت نامعتبر است.", show_alert=True)
                    return
                user_row = userbot_db.get_user_by_telegram_id(user_id)
                internal_uid = int((user_row or {}).get("id") or 0)
                ticket = userbot_db.get_user_ticket_by_code(internal_uid, code)
                if not ticket:
                    await query.answer("تیکت یافت نشد.", show_alert=True)
                    return
                if str(ticket.get("status") or "").strip().lower() == "closed":
                    await query.answer("این تیکت بسته شده است.", show_alert=True)
                    return
                set_user_step(context, user_id, "WAIT_TICKET_REPLY_TEXT")
                context.user_data[reply_key] = {
                    "ticket_code": code,
                    "reply_text": "",
                    "receipt_photo_id": "",
                }
                await context.bot.send_message(
                    chat_id=user_id,
                    text="✍️ لطفا پاسخ خود را به صورت کامل ارسال نمایید:",
                    reply_markup=_ticket_text_cancel_keyboard("reply"),
                )
                return

            state = context.user_data.get(reply_key) or {}
            ticket_code = int(state.get("ticket_code") or 0)

            if sub == "cancel":
                context.user_data.pop(reply_key, None)
                set_user_step(context, user_id, None)
                await _send_support_panel(context=context, user_id=user_id, message=query.message, text_settings=text_settings)
                return

            if ticket_code <= 0:
                await query.answer("اطلاعات تیکت نامعتبر است.", show_alert=True)
                return

            if sub == "edit":
                state["reply_text"] = ""
                state["receipt_photo_id"] = ""
                context.user_data[reply_key] = state
                set_user_step(context, user_id, "WAIT_TICKET_REPLY_TEXT")
                await context.bot.send_message(
                    chat_id=user_id,
                    text="✍️ لطفا پاسخ خود را به صورت کامل ارسال نمایید:",
                    reply_markup=_ticket_text_cancel_keyboard("reply"),
                )
                return

            if sub == "skip":
                if get_user_step(context, user_id) != "WAIT_TICKET_REPLY_SCREENSHOT":
                    await query.answer("در این مرحله قابل استفاده نیست.", show_alert=True)
                    return
                state["receipt_photo_id"] = ""
                context.user_data[reply_key] = state
                set_user_step(context, user_id, "WAIT_TICKET_REPLY_CONFIRM")
                preview_text = _ticket_reply_preview_text(state)
                try:
                    await query.message.edit_text(
                        preview_text,
                        reply_markup=ticket_confirm_keyboard("reply"),
                    )
                except Exception:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=preview_text,
                        reply_markup=ticket_confirm_keyboard("reply"),
                    )
                return

            if sub == "send":
                if get_user_step(context, user_id) != "WAIT_TICKET_REPLY_CONFIRM":
                    await query.answer("ابتدا مراحل ارسال پاسخ را کامل کنید.", show_alert=True)
                    return
                reply_text = str(state.get("reply_text") or "").strip()
                photo_file_id = str(state.get("receipt_photo_id") or "").strip()
                if not reply_text:
                    await query.answer("متن پاسخ خالی است.", show_alert=True)
                    return

                user_row = userbot_db.get_user_by_telegram_id(user_id)
                internal_uid = int((user_row or {}).get("id") or 0)
                ticket = userbot_db.get_user_ticket_by_code(internal_uid, ticket_code)
                if not ticket:
                    context.user_data.pop(reply_key, None)
                    set_user_step(context, user_id, None)
                    await query.answer("تیکت موردنظر یافت نشد.", show_alert=True)
                    return
                if str(ticket.get("status") or "").strip().lower() == "closed":
                    await query.answer("این تیکت بسته شده است.", show_alert=True)
                    return

                sender_name = str(query.from_user.full_name or query.from_user.username or user_id)
                ok = userbot_db.add_ticket_message(
                    ticket_code,
                    sender_type="user",
                    sender_name=sender_name,
                    message_text=reply_text,
                    photo_file_id=photo_file_id,
                )
                if not ok:
                    await query.answer("ثبت پاسخ انجام نشد.", show_alert=True)
                    return
                userbot_db.set_ticket_status(ticket_code, "open")
                set_user_step(context, user_id, None)
                context.user_data.pop(reply_key, None)
                fresh_ticket = userbot_db.get_ticket_by_code(ticket_code) or ticket
                await _notify_admin_ticket_reply(fresh_ticket)

                detail_messages = userbot_db.get_ticket_messages(ticket_code)
                detail_links = await _build_user_ticket_screenshot_links(context, ticket_code, detail_messages)
                detail = _ticket_detail_text(fresh_ticket, detail_messages, screenshot_links=detail_links)
                out_text = "✅ پاسخ شما ثبت شد.\n\n" + detail
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=user_id,
                    text=out_text,
                    reply_markup=user_ticket_detail_keyboard(ticket_code, can_reply=True, is_closed=False),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return

            await query.answer("گزینه نامعتبر است.", show_alert=True)
            return

        if action == "new" and len(parts) > 2:
            sub = parts[2]
            pending_key = f"pending_ticket_{user_id}"
            if sub == "cancel":
                context.user_data.pop(pending_key, None)
                set_user_step(context, user_id, None)
                await _send_support_panel(context=context, user_id=user_id, message=query.message, text_settings=text_settings)
                return
            if sub == "edit":
                set_user_step(context, user_id, "WAIT_TICKET_TITLE")
                await context.bot.send_message(
                    chat_id=user_id,
                    text="✍️ لطفا موضوع درخواست خود را ارسال نمایید:",
                    reply_markup=_ticket_text_cancel_keyboard(),
                )
                return
            if sub == "skip":
                if get_user_step(context, user_id) != "WAIT_TICKET_SCREENSHOT":
                    await query.answer("در این مرحله قابل استفاده نیست.", show_alert=True)
                    return
                pending = context.user_data.get(pending_key) or {}
                pending["receipt_photo_id"] = ""
                context.user_data[pending_key] = pending
                set_user_step(context, user_id, "WAIT_TICKET_CONFIRM")
                try:
                    await query.message.edit_text(
                        _ticket_compose_preview_text(pending),
                        reply_markup=ticket_confirm_keyboard(),
                    )
                except Exception:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=_ticket_compose_preview_text(pending),
                        reply_markup=ticket_confirm_keyboard(),
                    )
                return
            if sub == "send":
                pending = context.user_data.get(pending_key) or {}
                title = str(pending.get("title") or "").strip()
                question = str(pending.get("question") or "").strip()
                receipt_photo_id = str(pending.get("receipt_photo_id") or "").strip()
                if not title or not question:
                    await query.answer("اطلاعات تیکت ناقص است.", show_alert=True)
                    return
                user_row = userbot_db.get_user_by_telegram_id(user_id)
                if not user_row:
                    uid = userbot_db.upsert_user(user_id, query.from_user.username, query.from_user.full_name)
                    user_row = userbot_db.get_user_by_id(uid) or {}
                internal_uid = int((user_row or {}).get("id") or 0)
                if internal_uid <= 0:
                    await query.answer("خطا در شناسایی کاربر.", show_alert=True)
                    return
                ticket = userbot_db.create_ticket(
                    user_id=internal_uid,
                    telegram_id=int(user_id),
                    username=str(query.from_user.username or ""),
                    full_name=str(query.from_user.full_name or ""),
                    title=title,
                    question=question,
                    receipt_photo_id=receipt_photo_id,
                )
                context.user_data.pop(pending_key, None)
                set_user_step(context, user_id, None)
                code = int(ticket.get("ticket_code") or 0)
                await _notify_admin_new_ticket(ticket)
                if code > 0:
                    detail_messages = userbot_db.get_ticket_messages(code)
                    detail_links = await _build_user_ticket_screenshot_links(context, code, detail_messages)
                    detail = (
                        "✅ تیکت شما با موفقیت ثبت شد.\n\n"
                        "به زودی پاسخ داده می‌شود\n\n"
                        + _ticket_detail_text(ticket, detail_messages, screenshot_links=detail_links)
                    )
                    try:
                        await query.message.edit_text(
                            detail,
                            reply_markup=user_ticket_detail_keyboard(code, can_reply=True, is_closed=False),
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                    except Exception:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=detail,
                            reply_markup=user_ticket_detail_keyboard(code, can_reply=True, is_closed=False),
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                else:
                    try:
                        await query.message.edit_text(
                            "✅ تیکت شما با موفقیت ثبت شد.",
                            reply_markup=InlineKeyboardMarkup(
                                [[InlineKeyboardButton("📬 تیکت‌های من", callback_data="support:my:1")]]
                            ),
                        )
                    except Exception:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text="✅ تیکت شما با موفقیت ثبت شد.",
                            reply_markup=InlineKeyboardMarkup(
                                [[InlineKeyboardButton("📬 تیکت‌های من", callback_data="support:my:1")]]
                            ),
                        )
                return

        await query.answer("گزینه نامعتبر است.", show_alert=True)
        return


async def _cb_status(update, context, query, data, user_id, br, text_settings):
    if True:
        await query.answer()
        parts = data.split(":")
        if len(parts) < 3:
            return

        action = parts[1]

        if action == "list_back":
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=user_id,
                text="🏠 منوی اصلی",
                reply_markup=_main_menu_keyboard(),
            )
            return

        if action == "list":
            service_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            service = userbot_db.get_service_by_id(service_id) if service_id else None
            if not service:
                await context.bot.send_message(chat_id=user_id, text="❌ اشتراک انتخاب‌شده یافت نشد.", reply_markup=_main_menu_keyboard())
                return
            service = await _sync_service_runtime_from_panels(service)
            settings = _get_subscription_settings()
            # لیست انتخاب اشتراک باید باقی بماند؛ جزئیات را به‌صورت پیام جدید ارسال می‌کنیم.
            await context.bot.send_message(
                chat_id=user_id,
                text=_build_subscription_status_text(service),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(
                    service_id,
                    show_direct_config=settings.get("show_direct_config", True),
                    show_sub_link=settings.get("show_sub_link", True),
                    show_configs=_should_show_configs_button(settings),
                    show_detach=_is_connected_service(service),
                ),
            )
            return

        service_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        settings = _get_subscription_settings()
        service = userbot_db.get_service_by_id(service_id) if service_id else None

        if not service:
            await _safe_edit_message_text(
                query,
                "❌ سرویس موردنظر یافت نشد یا حذف شده است.",
            )
            return

        probe_state = await _service_probe_state(service)
        if probe_state != "exists":
            if probe_state == "unreachable":
                await _safe_edit_message_text(
                    query,
                    "⏳ این اشتراک موقتاً در دسترس نیست.\nپس از رفع مشکل سرور دوباره در لیست نمایش داده می‌شود.",
                )
                return
            sid = int(service.get("id") or 0)
            missing_info = {"missing_streak": 1, "first_missing_at": "", "last_missing_at": ""}
            try:
                missing_info = userbot_db.mark_service_missing(sid)
            except Exception:
                missing_info = {"missing_streak": 1, "first_missing_at": "", "last_missing_at": ""}

            if _older_than_days(
                str(missing_info.get("first_missing_at") or ""),
                USERBOT_MISSING_SERVICE_DELETE_DAYS,
            ):
                try:
                    userbot_db.delete_service(sid)
                except Exception as e:
                    logger.warning("Failed deleting stale service id=%s from callback: %s", service.get("id"), e)
            await _safe_edit_message_text(
                query,
                "❌ اشتراک وجود ندارد.",
            )
            return
        try:
            userbot_db.mark_service_seen(int(service.get("id") or 0))
        except Exception:
            pass

        if action in {"configs", "direct", "directcfg", "sub_link", "auto_sub", "sub_b64", "multi", "multi_b64"}:
            service, lock_reason = await _resolve_service_access_lock(service)
            if lock_reason:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=_service_local_lock_text(lock_reason),
                    reply_markup=_main_menu_keyboard(),
                )
                return

        if action in {"menu", "refresh"}:
            service = await _sync_service_runtime_from_panels(service)
            await _safe_edit_message_text(
                query,
                _build_subscription_status_text(service),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(
                    service_id,
                    show_direct_config=settings.get("show_direct_config", True),
                    show_sub_link=settings.get("show_sub_link", True),
                    show_configs=_should_show_configs_button(settings),
                    show_detach=_is_connected_service(service),
                ),
            )
            return

        if action == "rename":
            context.user_data[f"pending_rename_service_{user_id}"] = {
                "service_id": int(service_id or 0),
            }
            set_user_step(context, user_id, "WAIT_RENAME_SERVICE_NAME")
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "✍️ لطفاً نام جدید اشتراک را ارسال کنید:\n"
                    "• حداقل 3 و حداکثر 64 کاراکتر\n"
                    "برای انصراف، روی دکمه «بازگشت» بزنید."
                ),
                reply_markup=cancel_keyboard(),
            )
            return

        if action == "replace_link":
            confirmed = len(parts) > 3 and parts[3] == "confirm"
            if not confirmed:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "⚠️ هشدار تغییر لینک اشتراک\n\n"
                        "با تغییر لینک اشتراک، لینک و کانفیگ قبلی از کار می‌افتد و باید لینک جدید را دوباره در برنامه وارد کنید.\n\n"
                        "اگر مطمئن هستید، تایید تغییر لینک را بزنید."
                    ),
                    reply_markup=replace_subscription_link_confirm_keyboard(service_id),
                )
                return

            await context.bot.send_message(chat_id=user_id, text="⏳ در حال تغییر لینک اشتراک...")
            ok, result_text, new_uuid = await _regenerate_service_uuid_for_service(service)
            if not ok:
                await context.bot.send_message(chat_id=user_id, text=result_text, reply_markup=_main_menu_keyboard())
                return

            refreshed = userbot_db.get_service_by_id(service_id) or service
            refreshed = await _sync_service_runtime_from_panels(refreshed)
            settings = _get_subscription_settings()
            await context.bot.send_message(chat_id=user_id, text=result_text, reply_markup=_main_menu_keyboard())
            await context.bot.send_message(
                chat_id=user_id,
                text=_build_subscription_status_text(refreshed),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(
                    service_id,
                    show_direct_config=settings.get("show_direct_config", True),
                    show_sub_link=settings.get("show_sub_link", True),
                    show_configs=_should_show_configs_button(settings),
                    show_detach=_is_connected_service(refreshed),
                ),
            )
            return

        if action == "copy_id":
            comment_meta = _parse_service_comment(service.get("comment") or "")
            service_code = str(comment_meta.get("code") or service.get("id") or "").strip()
            if not service_code:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ شناسه اشتراک پیدا نشد.",
                    reply_markup=_main_menu_keyboard(),
                )
                return
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📋 شناسه اشتراک شما:\n`{service_code}`",
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(
                    service_id,
                    show_direct_config=settings.get("show_direct_config", True),
                    show_sub_link=settings.get("show_sub_link", True),
                    show_configs=_should_show_configs_button(settings),
                    show_detach=_is_connected_service(service),
                ),
            )
            return

        if action == "configs":
            await _safe_edit_message_reply_markup(
                query,
                reply_markup=subscription_configs_keyboard(
                    service_id,
                    show_direct_config=settings.get("show_direct_config", True),
                    show_sub_link=settings.get("show_sub_link", True),
                    show_auto_sub_link=settings.get("show_auto_sub_link", False),
                    show_sub_link_b64=settings.get("show_sub_link_b64", False),
                    show_multi_server=settings.get("show_multi_server", False),
                    show_multi_server_b64=settings.get("show_multi_server_b64", False),
                ),
            )
            return

        if action == "back":
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=user_id,
                text="🏠 منوی اصلی",
                reply_markup=_main_menu_keyboard(),
            )
            return

        if action == "direct":
            await _send_service_direct_configs_shell(
                context,
                user_id=user_id,
                service_id=int(service_id or 0),
                service=service,
            )
            return

        if action in {"sub_link", "auto_sub", "sub_b64", "multi", "multi_b64"}:
            base_urls = _get_service_node_base_urls(service)
            if not base_urls:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ برای این سرویس لینک کانفیگ در دسترس نیست.",
                    reply_markup=_main_menu_keyboard(),
                )
                return

            base_url = base_urls[0]

            config_items = []
            if action == "sub_link":
                if not settings.get("show_sub_link", True):
                    await context.bot.send_message(chat_id=user_id, text="❌ نمایش لینک اشتراک خاموش است.", reply_markup=_main_menu_keyboard())
                    return
                config_items.append(("🔗 لینک اشتراک:", f"{base_url}/all.txt"))
            elif action == "auto_sub":
                if not settings.get("show_auto_sub_link", False):
                    await context.bot.send_message(chat_id=user_id, text="❌ نمایش اشتراک خودکار خاموش است.", reply_markup=_main_menu_keyboard())
                    return
                config_items.append(("🤖 لینک اشتراک خودکار:", f"{base_url}/sub/?asn=unknown"))
            elif action == "sub_b64":
                if not settings.get("show_sub_link_b64", False):
                    await context.bot.send_message(chat_id=user_id, text="❌ نمایش لینک b64 خاموش است.", reply_markup=_main_menu_keyboard())
                    return
                config_items.append(("🔐 لینک اشتراک b64:", f"{base_url}/all.txt?base64=1"))
            elif action == "multi":
                if not settings.get("show_multi_server", False):
                    await context.bot.send_message(chat_id=user_id, text="❌ نمایش لینک اشتراک هوشمند خاموش است.", reply_markup=_main_menu_keyboard())
                    return
                managed_link, _ = _get_or_create_bot_sub_links(int(service_id), service=service)
                config_items.append(("🌐 لینک اشتراک هوشمند:", managed_link))
            elif action == "multi_b64":
                if not settings.get("show_multi_server_b64", False):
                    await context.bot.send_message(chat_id=user_id, text="❌ نمایش لینک اشتراک هوشمند b64 خاموش است.", reply_markup=_main_menu_keyboard())
                    return
                _, managed_link_b64 = _get_or_create_bot_sub_links(int(service_id), service=service)
                config_items.append(("🌐 لینک اشتراک هوشمند b64:", managed_link_b64))

            if not config_items:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ در حال حاضر هیچ لینکی برای نمایش فعال نیست.",
                    reply_markup=_main_menu_keyboard(),
                )
                return

            primary_link = config_items[0][1]
            qr_image = make_qr_image(primary_link)
            qr_caption = (
                "📄 جهت کپی شدن لینک اشتراک کافیست یک بار لینک زیر را لمس کنید 👇\n\n"
                f"<code>{escape(primary_link)}</code>"
            )
            try:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=qr_image,
                    caption=qr_caption,
                    parse_mode="HTML",
                    reply_markup=subscription_links_keyboard(service_id),
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=qr_caption,
                    parse_mode="HTML",
                    reply_markup=subscription_links_keyboard(service_id),
                    disable_web_page_preview=True,
                )

            config_text_lines = ["📝 لینک‌های اشتراک", ""]
            for title, value in config_items:
                config_text_lines.append(title)
                config_text_lines.append(f"<code>{escape(value)}</code>")
                config_text_lines.append("")

            if len(config_items) > 1:
                await _send_long_message(
                    context,
                    user_id,
                    "\n".join(config_text_lines).strip(),
                    parse_mode="HTML",
                )
            return

        if action == "guide":
            await context.bot.send_message(
                chat_id=user_id,
                text=text_settings.get("guide_text") or _default_guide_intro_text(),
                reply_markup=guide_os_keyboard("m"),
            )
            return

        if action == "directcfg":
            # Backward compatibility for old/stale protocol buttons in old messages.
            await _send_service_direct_configs_shell(
                context,
                user_id=user_id,
                service_id=int(service_id or 0),
                service=service,
            )
            return

        if action == "renew":
            if not bool(br.get("enable_renew", True)):
                await context.bot.send_message(
                    chat_id=user_id,
                    text="🚫 تمدید اشتراک در حال حاضر غیرفعال است.",
                    reply_markup=_main_menu_keyboard(),
                )
                return
            if not await _service_is_renewable_live(service):
                await context.bot.send_message(
                    chat_id=user_id,
                    text=_renew_not_allowed_text(),
                    reply_markup=_main_menu_keyboard(),
                )
                return
            try:
                renew_sid = int(service.get("server_id") or 0)
            except (TypeError, ValueError):
                renew_sid = 0
            if renew_sid <= 0:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ سرور این اشتراک نامعتبر است.",
                    reply_markup=_main_menu_keyboard(),
                )
                return
            context.user_data[f"renew_target_{user_id}"] = int(service_id)
            await _send_buy_flow_for_server(user_id, renew_sid, user_id, context, is_renew=True)
            return

        if action == "detach":
            if not _is_connected_service(service):
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⛔ این اشتراک قابل جداسازی نیست.",
                    reply_markup=_main_menu_keyboard(),
                )
                return
            userbot_db.delete_service(int(service_id))
            await context.bot.send_message(
                chat_id=user_id,
                text="✅اشتراک از ربات جداسازی شد",
                reply_markup=_main_menu_keyboard(),
            )
            return

        return


async def _cb_renew(update, context, query, data, user_id):
    await query.answer()
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "back":
        context.user_data.pop(f"renew_target_{user_id}", None)
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, text="🏠 منوی اصلی", reply_markup=_main_menu_keyboard())
        return

    if action == "svc":
        service_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        service = userbot_db.get_service_by_id(service_id) if service_id > 0 else None
        if not service:
            await context.bot.send_message(chat_id=user_id, text="❌ اشتراک انتخاب‌شده یافت نشد.", reply_markup=_main_menu_keyboard())
            return
        if not await _service_is_renewable_live(service):
            await context.bot.send_message(chat_id=user_id, text=_renew_not_allowed_text(), reply_markup=_main_menu_keyboard())
            return
        try:
            renew_sid = int(service.get("server_id") or 0)
        except (TypeError, ValueError):
            renew_sid = 0
        if renew_sid <= 0:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ سرور این اشتراک نامعتبر است.",
                reply_markup=_main_menu_keyboard(),
            )
            return
        context.user_data[f"renew_target_{user_id}"] = int(service_id)
        await _send_buy_flow_for_server(user_id, renew_sid, user_id, context, is_renew=True)
        return

    return


async def _cb_wallet(update, context, query, data, user_id):
    await query.answer()
    action = data.split(":", 1)[1]
    if action == "back":
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, text="🏠 منوی اصلی", reply_markup=_main_menu_keyboard())
        return
    if action == "card":
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_card_to_card", True)):
            await context.bot.send_message(
                chat_id=user_id,
                text="🚫 کارت به کارت در حال حاضر غیرفعال است.",
                reply_markup=_main_menu_keyboard(),
            )
            return
        set_user_step(context, user_id, "WAIT_WALLET_TOPUP_AMOUNT")
        context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text="🔻 لطفا مبلغی که قصد شارژ حساب خود دارید را به تومان وارد کنید:",
            reply_markup=cancel_keyboard(),
        )
        return
    if action == "zarinpal":
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_zarinpal", False)):
            await context.bot.send_message(chat_id=user_id, text="🚫 زرین پال غیرفعال است.", reply_markup=_main_menu_keyboard())
            return
        text_settings = _get_text_settings()
        ztxt = str(text_settings.get("zarinpal_pro_text") or "").strip()
        if ztxt.lower() in {"none", "null"}:
            ztxt = ""
        if not ztxt or ztxt == "0":
            ztxt = _default_zarinpal_text()
        vouchers = userbot_db.list_active_zarin_vouchers(limit=20)
        if not vouchers:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"{ztxt}\n\n❌ در حال حاضر لینک پرداخت فعالی ثبت نشده است.",
                reply_markup=_main_menu_keyboard(),
            )
            return
        await context.bot.send_message(
            chat_id=user_id,
            text=ztxt,
            reply_markup=_build_zarinpal_links_keyboard(vouchers),
            disable_web_page_preview=True,
        )
        return
    if action == "perfect":
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_perfect_money", False)):
            await context.bot.send_message(chat_id=user_id, text="🚫 پرفکت مانی غیرفعال است.", reply_markup=_main_menu_keyboard())
            return
        await context.bot.send_message(chat_id=user_id, text="🧰 پرداخت پرفکت مانی به‌زودی فعال می‌شود.", reply_markup=_main_menu_keyboard())
        return
    if action == "crypto":
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_crypto", False)):
            await context.bot.send_message(chat_id=user_id, text="🚫 پرداخت ارز دیجیتال غیرفعال است.", reply_markup=_main_menu_keyboard())
            return
        await context.bot.send_message(chat_id=user_id, text="🔗 پرداخت ارز دیجیتال به‌زودی فعال می‌شود.", reply_markup=_main_menu_keyboard())
        return
    if action == "coupon":
        mkt = _get_marketing_settings()
        if not (
            bool(mkt.get("enable_discount_code", False))
            or bool(mkt.get("enable_increase_code", False))
        ):
            await context.bot.send_message(
                chat_id=user_id,
                text="🚫 استفاده از کد هدیه در حال حاضر غیرفعال است.",
                reply_markup=_main_menu_keyboard(),
            )
            return
        set_user_step(context, user_id, "WAIT_COUPON_CODE")
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text="⬇️ لطفا کد کوپن خود را ارسال کنید:",
            reply_markup=cancel_keyboard(),
        )
        return
    return


async def _cb_pay(update, context, query, data, user_id):
    await query.answer()
    action = data.split(":", 1)[1]
    if action == "cancel":
        set_user_step(context, user_id, None)
        context.user_data.pop(f"pending_pay_{user_id}", None)
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, text="عملیات لغو شد.", reply_markup=_main_menu_keyboard())
        return

    if action == "receipt_done":
        set_user_step(context, user_id, "WAIT_RECEIPT_IMAGE")
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text="⬇️ لطفا رسید پرداخت خود را در زیر این پیام ارسال کنید:",
            reply_markup=receipt_cancel_keyboard(),
        )
        return
    return


async def _cb_trial_back(update, context, query, data, user_id):
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass
    await context.bot.send_message(chat_id=user_id, text="🏠 منوی اصلی", reply_markup=_main_menu_keyboard())
    return


async def _cb_trial_loc(update, context, query, data, user_id):
    await query.answer()
    try:
        sid = int(data.split(":")[2])
    except Exception:
        await query.answer("❌ داده نامعتبر است.", show_alert=True)
        return

    u_db = userbot_db.get_user_by_telegram_id(user_id)
    if not u_db:
        internal_user_id = userbot_db.upsert_user(
            query.from_user.id,
            query.from_user.username,
            query.from_user.full_name,
        )
        u_db = userbot_db.get_user_by_id(internal_user_id) or {}

    if int(u_db.get("got_free_trial") or 0) == 1:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text="🚫 شما قبلا اکانت تست رایگان خود را دریافت نموده‌اید!",
            reply_markup=_main_menu_keyboard(),
        )
        return

    trial_settings = userbot_db.get_trial_spec_settings()
    if not bool(trial_settings.get("enabled", True)):
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text="🚫 دریافت تست رایگان در حال حاضر غیرفعال است.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    context.user_data[f"pending_trial_{user_id}"] = {
        "sid": sid,
        "internal_user_id": int(u_db.get("id") or 0),
    }
    set_user_step(context, user_id, "WAIT_TRIAL_SERVICE_NAME")

    try:
        await query.message.delete()
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=user_id,
        text="⬇️ لطفا نام خود را ارسال کنید:",
        reply_markup=cancel_keyboard(),
    )
    return


async def _cb_buy_back_main(update, context, query, data, user_id, br, text_settings):
    if not bool(br.get("enable_buy", True)):
        await query.answer("🚫 خرید غیرفعال است.", show_alert=True)
        return
    context.user_data.pop(f"buy_menu_open_until_{user_id}", None)
    servers = _get_location_servers()
    server_columns = int(br.get("server_columns") or 1)
    await _safe_edit_message_text(
        query,
        text_settings.get("servers_list_text") or "📡 **لیست سرورها**\nلطفاً لوکیشن مورد نظر خود را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=location_keyboard(servers, columns=server_columns)
    )
    return


async def _cb_buy_exit_main(update, context, query, data, user_id):
    await query.answer()
    context.user_data.pop(f"buy_menu_open_until_{user_id}", None)
    try:
        await query.message.delete()
    except Exception:
        pass
    # بدون ارسال پیام اضافه؛ فقط خروج از جریان خرید
    return
