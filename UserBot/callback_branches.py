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
        reply_markup=_main_menu_keyboard(user_id=user_id, lang=new_lang),
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
                await _safe_edit_message_text(query, i18n.t("service_missing_deleted", _user_lang(user_id)))
                return
            service = await _sync_service_runtime_from_panels(service)
            await _safe_edit_message_text(
                query,
                _build_subscription_status_text(service, lang=_user_lang(user_id)),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(service_id, show_direct_config=settings.get('show_direct_config', True), show_sub_link=settings.get('show_sub_link', True), show_configs=_should_show_configs_button(settings), show_detach=_is_connected_service(service), lang=_user_lang(user_id)),
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
            text=_guide_platform_text(action, text_settings, lang=_user_lang(user_id)),
        )
        return

    await query.answer(i18n.t("invalid_option", _user_lang(user_id)), show_alert=True)
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
                else i18n.t("invite_link_not_ready", _user_lang(user_id))
            )
            banner_text = _format_text_template(
                _cfg_text(user_id, "invite_banner_text", "cfg_invite_banner", text_settings),
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
                i18n.t("invite_rewards_header", _user_lang(user_id))
                + f"{int(settings.get('trial_reward_amount') or 0):,}"
                + i18n.t("invite_reward_buy_first", _user_lang(user_id))
                + f"{int(settings.get('purchase_reward_amount') or 0):,}"
                + "\n"
                + i18n.t("invite_rewards_given", _user_lang(user_id))
                + f"{int(stats.get('paid_rewards_count') or 0)}"
                + i18n.t("invite_rewards_total", _user_lang(user_id))
                + f"{int(stats.get('total_rewards') or 0):,}"
                + "\n"
            )
            await context.bot.send_message(chat_id=user_id, text=text)
            return

        if action == "list":
            try:
                refs, total = userbot_db.list_referrals(limit=20, inviter_id=internal_uid)
            except Exception:
                refs, total = [], 0
            if not refs:
                await context.bot.send_message(chat_id=user_id, text=i18n.t("no_invites_yet", _user_lang(user_id)))
                return
            lines = [i18n.t("invite_list_header", _user_lang(user_id), count=total)]
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
                i18n.t("invite_stats_header", _user_lang(user_id))
                + f"{int(stats.get('total_referrals') or 0)}"
                + i18n.t("invite_stats_success", _user_lang(user_id))
                + f"{int(stats.get('successful_referrals') or 0)}"
                + i18n.t("invite_stats_awaiting", _user_lang(user_id))
                + f"{int(stats.get('pending_purchase') or 0)}"
                + i18n.t("invite_stats_trial_rewards", _user_lang(user_id))
                + f"{int(stats.get('trial_rewards_count') or 0)}"
                + i18n.t("invite_stats_buy_rewards", _user_lang(user_id))
                + f"{int(stats.get('purchase_rewards_count') or 0)}"
                + "\n"
            )
            await context.bot.send_message(chat_id=user_id, text=text)
            return

        if action == "history":
            try:
                rewards, total = userbot_db.list_referral_rewards(limit=20, inviter_id=internal_uid)
            except Exception:
                rewards, total = [], 0
            if not rewards:
                await context.bot.send_message(chat_id=user_id, text=i18n.t("no_rewards_yet", _user_lang(user_id)))
                return
            labels = userbot_db.REFERRAL_REWARD_LABELS
            lines = [i18n.t("rewards_list_header", _user_lang(user_id), count=total)]
            for idx, rw in enumerate(rewards, start=1):
                rtype = str(rw.get("reward_type") or "")
                label = labels.get(rtype, rtype)
                amount = int(rw.get("amount_toman") or 0)
                status_icon = "✅" if str(rw.get("status") or "") == "paid" else "❌"
                created = str(rw.get("created_at") or "")[:10]
                lines.append(f"{idx}. {label} | {amount:,}" + i18n.t("word_toman2", _user_lang(user_id)) + f" | {status_icon} {created}")
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
                    text=i18n.t("reply_send_prompt", _user_lang(user_id)),
                )
                return
            await query.answer(i18n.t("invalid_option", _user_lang(user_id)), show_alert=True)
            return

        if action == "back_main":
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("main_menu_btn", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return

        if action == "menu":
            await _send_support_panel(context=context, user_id=user_id, message=query.message, text_settings=text_settings)
            return

        if action == "faq":
            faq_text = str(text_settings.get("faq_text") or "").strip()
            _faq_is_placeholder = (not faq_text) or ("به‌زودی تکمیل می‌شود" in faq_text) or ("به زودی تکمیل می شود" in faq_text)
            if _faq_is_placeholder:
                faq_text = _cfg_text(user_id, "faq_text", "cfg_faq_default", text_settings)
            try:
                await query.message.edit_text(faq_text, reply_markup=support_panel_keyboard(lang=_user_lang(user_id)))
            except Exception:
                await context.bot.send_message(chat_id=user_id, text=faq_text, reply_markup=support_panel_keyboard(lang=_user_lang(user_id)))
            return

        if action == "new" and len(parts) == 2:
            set_user_step(context, user_id, "WAIT_TICKET_TITLE")
            context.user_data[f"pending_ticket_{user_id}"] = {}
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("ticket_title_prompt", _user_lang(user_id)),
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
            header = i18n.t("tickets_list_header", _user_lang(user_id), total=int(total))
            kb = user_tickets_list_keyboard(tickets, page, total_pages, lang=_user_lang(user_id))
            try:
                await query.message.edit_text(header, reply_markup=kb)
            except Exception:
                await context.bot.send_message(chat_id=user_id, text=header, reply_markup=kb)
            return

        if action == "view":
            code = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 0
            if code <= 0:
                await query.answer(i18n.t("ticket_invalid", _user_lang(user_id)), show_alert=True)
                return
            user_row = userbot_db.get_user_by_telegram_id(user_id)
            internal_uid = int((user_row or {}).get("id") or 0)
            ticket = userbot_db.get_user_ticket_by_code(internal_uid, code)
            if not ticket:
                await query.answer(i18n.t("ticket_missing", _user_lang(user_id)), show_alert=True)
                return
            messages = userbot_db.get_ticket_messages(code)
            shot_links = await _build_user_ticket_screenshot_links(context, code, messages)
            text = _ticket_detail_text(ticket, messages, screenshot_links=shot_links, lang=_user_lang(user_id))
            is_closed = str(ticket.get("status") or "").strip().lower() == "closed"
            can_reply = not is_closed
            try:
                await query.message.edit_text(
                    text,
                    reply_markup=user_ticket_detail_keyboard(code, can_reply=can_reply, is_closed=is_closed, lang=_user_lang(user_id)),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=user_ticket_detail_keyboard(code, can_reply=can_reply, is_closed=is_closed, lang=_user_lang(user_id)),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            return

        if action == "reopen":
            await query.answer(i18n.t("ticket_closed_by_you", _user_lang(user_id)), show_alert=True)
            return

        if action == "close":
            code = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 0
            if code <= 0:
                await query.answer(i18n.t("ticket_invalid", _user_lang(user_id)), show_alert=True)
                return
            user_row = userbot_db.get_user_by_telegram_id(user_id)
            internal_uid = int((user_row or {}).get("id") or 0)
            ticket = userbot_db.get_user_ticket_by_code(internal_uid, code)
            if not ticket:
                await query.answer(i18n.t("ticket_missing", _user_lang(user_id)), show_alert=True)
                return
            current_status = str(ticket.get("status") or "").strip().lower()
            if current_status == "closed":
                await query.answer(i18n.t("ticket_already_closed", _user_lang(user_id)))
                return
            ok = userbot_db.set_ticket_status(code, "closed")
            if not ok:
                await query.answer(i18n.t("status_change_failed", _user_lang(user_id)), show_alert=True)
                return
            fresh = userbot_db.get_user_ticket_by_code(internal_uid, code) or ticket
            messages = userbot_db.get_ticket_messages(code)
            shot_links = await _build_user_ticket_screenshot_links(context, code, messages)
            detail_text = _ticket_detail_text(fresh, messages, screenshot_links=shot_links, lang=_user_lang(user_id))
            is_closed = str(fresh.get("status") or "").strip().lower() == "closed"
            can_reply = not is_closed
            notice = i18n.t("ticket_closed_ok", _user_lang(user_id))
            try:
                await query.answer(notice)
            except Exception:
                pass
            try:
                await query.message.edit_text(
                    detail_text,
                    reply_markup=user_ticket_detail_keyboard(code, can_reply=can_reply, is_closed=is_closed, lang=_user_lang(user_id)),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=detail_text,
                    reply_markup=user_ticket_detail_keyboard(code, can_reply=can_reply, is_closed=is_closed, lang=_user_lang(user_id)),
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
                    await query.answer(i18n.t("ticket_invalid", _user_lang(user_id)), show_alert=True)
                    return
                user_row = userbot_db.get_user_by_telegram_id(user_id)
                internal_uid = int((user_row or {}).get("id") or 0)
                ticket = userbot_db.get_user_ticket_by_code(internal_uid, code)
                if not ticket:
                    await query.answer(i18n.t("ticket_missing", _user_lang(user_id)), show_alert=True)
                    return
                if str(ticket.get("status") or "").strip().lower() == "closed":
                    await query.answer(i18n.t("ticket_closed", _user_lang(user_id)), show_alert=True)
                    return
                set_user_step(context, user_id, "WAIT_TICKET_REPLY_TEXT")
                context.user_data[reply_key] = {
                    "ticket_code": code,
                    "reply_text": "",
                    "receipt_photo_id": "",
                }
                await context.bot.send_message(
                    chat_id=user_id,
                    text=i18n.t("ticket_reply_prompt", _user_lang(user_id)),
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
                await query.answer(i18n.t("ticket_info_invalid", _user_lang(user_id)), show_alert=True)
                return

            if sub == "edit":
                state["reply_text"] = ""
                state["receipt_photo_id"] = ""
                context.user_data[reply_key] = state
                set_user_step(context, user_id, "WAIT_TICKET_REPLY_TEXT")
                await context.bot.send_message(
                    chat_id=user_id,
                    text=i18n.t("ticket_reply_prompt", _user_lang(user_id)),
                    reply_markup=_ticket_text_cancel_keyboard("reply"),
                )
                return

            if sub == "skip":
                if get_user_step(context, user_id) != "WAIT_TICKET_REPLY_SCREENSHOT":
                    await query.answer(i18n.t("not_available_now", _user_lang(user_id)), show_alert=True)
                    return
                state["receipt_photo_id"] = ""
                context.user_data[reply_key] = state
                set_user_step(context, user_id, "WAIT_TICKET_REPLY_CONFIRM")
                preview_text = _ticket_reply_preview_text(state)
                try:
                    await query.message.edit_text(
                        preview_text,
                        reply_markup=ticket_confirm_keyboard('reply', lang=_user_lang(user_id)),
                    )
                except Exception:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=preview_text,
                        reply_markup=ticket_confirm_keyboard('reply', lang=_user_lang(user_id)),
                    )
                return

            if sub == "send":
                if get_user_step(context, user_id) != "WAIT_TICKET_REPLY_CONFIRM":
                    await query.answer(i18n.t("reply_steps_incomplete", _user_lang(user_id)), show_alert=True)
                    return
                reply_text = str(state.get("reply_text") or "").strip()
                photo_file_id = str(state.get("receipt_photo_id") or "").strip()
                if not reply_text:
                    await query.answer(i18n.t("reply_empty", _user_lang(user_id)), show_alert=True)
                    return

                user_row = userbot_db.get_user_by_telegram_id(user_id)
                internal_uid = int((user_row or {}).get("id") or 0)
                ticket = userbot_db.get_user_ticket_by_code(internal_uid, ticket_code)
                if not ticket:
                    context.user_data.pop(reply_key, None)
                    set_user_step(context, user_id, None)
                    await query.answer(i18n.t("ticket_not_found", _user_lang(user_id)), show_alert=True)
                    return
                if str(ticket.get("status") or "").strip().lower() == "closed":
                    await query.answer(i18n.t("ticket_closed", _user_lang(user_id)), show_alert=True)
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
                    await query.answer(i18n.t("reply_submit_failed", _user_lang(user_id)), show_alert=True)
                    return
                userbot_db.set_ticket_status(ticket_code, "open")
                set_user_step(context, user_id, None)
                context.user_data.pop(reply_key, None)
                fresh_ticket = userbot_db.get_ticket_by_code(ticket_code) or ticket
                await _notify_admin_ticket_reply(fresh_ticket)

                detail_messages = userbot_db.get_ticket_messages(ticket_code)
                detail_links = await _build_user_ticket_screenshot_links(context, ticket_code, detail_messages)
                detail = _ticket_detail_text(fresh_ticket, detail_messages, screenshot_links=detail_links, lang=_user_lang(user_id))
                out_text = i18n.t("ticket_reply_saved", _user_lang(user_id)) + detail
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=user_id,
                    text=out_text,
                    reply_markup=user_ticket_detail_keyboard(ticket_code, can_reply=True, is_closed=False, lang=_user_lang(user_id)),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return

            await query.answer(i18n.t("invalid_option", _user_lang(user_id)), show_alert=True)
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
                    text=i18n.t("ticket_title_prompt", _user_lang(user_id)),
                    reply_markup=_ticket_text_cancel_keyboard(),
                )
                return
            if sub == "skip":
                if get_user_step(context, user_id) != "WAIT_TICKET_SCREENSHOT":
                    await query.answer(i18n.t("not_available_now", _user_lang(user_id)), show_alert=True)
                    return
                pending = context.user_data.get(pending_key) or {}
                pending["receipt_photo_id"] = ""
                context.user_data[pending_key] = pending
                set_user_step(context, user_id, "WAIT_TICKET_CONFIRM")
                try:
                    await query.message.edit_text(
                        _ticket_compose_preview_text(pending),
                        reply_markup=ticket_confirm_keyboard(lang=_user_lang(user_id)),
                    )
                except Exception:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=_ticket_compose_preview_text(pending),
                        reply_markup=ticket_confirm_keyboard(lang=_user_lang(user_id)),
                    )
                return
            if sub == "send":
                pending = context.user_data.get(pending_key) or {}
                title = str(pending.get("title") or "").strip()
                question = str(pending.get("question") or "").strip()
                receipt_photo_id = str(pending.get("receipt_photo_id") or "").strip()
                if not title or not question:
                    await query.answer(i18n.t("ticket_info_incomplete", _user_lang(user_id)), show_alert=True)
                    return
                user_row = userbot_db.get_user_by_telegram_id(user_id)
                if not user_row:
                    uid = userbot_db.upsert_user(user_id, query.from_user.username, query.from_user.full_name)
                    user_row = userbot_db.get_user_by_id(uid) or {}
                internal_uid = int((user_row or {}).get("id") or 0)
                if internal_uid <= 0:
                    await query.answer(i18n.t("user_identify_error", _user_lang(user_id)), show_alert=True)
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
                        i18n.t("ticket_created_body", _user_lang(user_id))
                        + _ticket_detail_text(ticket, detail_messages, screenshot_links=detail_links, lang=_user_lang(user_id))
                    )
                    try:
                        await query.message.edit_text(
                            detail,
                            reply_markup=user_ticket_detail_keyboard(code, can_reply=True, is_closed=False, lang=_user_lang(user_id)),
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                    except Exception:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=detail,
                            reply_markup=user_ticket_detail_keyboard(code, can_reply=True, is_closed=False, lang=_user_lang(user_id)),
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                else:
                    try:
                        await query.message.edit_text(
                            i18n.t("ticket_created_ok", _user_lang(user_id)),
                            reply_markup=InlineKeyboardMarkup(
                                [[InlineKeyboardButton(i18n.t("btn_my_tickets", _user_lang(user_id)), callback_data="support:my:1")]]
                            ),
                        )
                    except Exception:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=i18n.t("ticket_created_ok", _user_lang(user_id)),
                            reply_markup=InlineKeyboardMarkup(
                                [[InlineKeyboardButton(i18n.t("btn_my_tickets", _user_lang(user_id)), callback_data="support:my:1")]]
                            ),
                        )
                return

        await query.answer(i18n.t("invalid_option", _user_lang(user_id)), show_alert=True)
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
                text=i18n.t("main_menu_btn", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return

        if action == "list":
            service_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            service = userbot_db.get_service_by_id(service_id) if service_id else None
            if not service:
                await context.bot.send_message(chat_id=user_id, text=i18n.t("sub_selected_missing", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
                return
            service = await _sync_service_runtime_from_panels(service)
            settings = _get_subscription_settings()
            # لیست انتخاب اشتراک باید باقی بماند؛ جزئیات را به‌صورت پیام جدید ارسال می‌کنیم.
            await context.bot.send_message(
                chat_id=user_id,
                text=_build_subscription_status_text(service, lang=_user_lang(user_id)),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(service_id, show_direct_config=settings.get('show_direct_config', True), show_sub_link=settings.get('show_sub_link', True), show_configs=_should_show_configs_button(settings), show_detach=_is_connected_service(service), lang=_user_lang(user_id)),
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
                    i18n.t("sub_temp_unavailable", _user_lang(user_id)),
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
                    text=_service_local_lock_text(lock_reason, lang=_user_lang(user_id)),
                    reply_markup=_main_menu_keyboard(user_id=user_id),
                )
                return

        if action in {"menu", "refresh"}:
            service = await _sync_service_runtime_from_panels(service)
            await _safe_edit_message_text(
                query,
                _build_subscription_status_text(service, lang=_user_lang(user_id)),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(service_id, show_direct_config=settings.get('show_direct_config', True), show_sub_link=settings.get('show_sub_link', True), show_configs=_should_show_configs_button(settings), show_detach=_is_connected_service(service), lang=_user_lang(user_id)),
            )
            return

        if action == "rename":
            context.user_data[f"pending_rename_service_{user_id}"] = {
                "service_id": int(service_id or 0),
            }
            set_user_step(context, user_id, "WAIT_RENAME_SERVICE_NAME")
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("rename_prompt", _user_lang(user_id)),
                reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
            )
            return

        if action == "replace_link":
            confirmed = len(parts) > 3 and parts[3] == "confirm"
            if not confirmed:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=i18n.t("link_change_warning", _user_lang(user_id)),
                    reply_markup=replace_subscription_link_confirm_keyboard(service_id, lang=_user_lang(user_id)),
                )
                return

            await context.bot.send_message(chat_id=user_id, text=i18n.t("link_changing", _user_lang(user_id)))
            ok, result_text, new_uuid = await _regenerate_service_uuid_for_service(service, lang=_user_lang(user_id))
            if not ok:
                await context.bot.send_message(chat_id=user_id, text=result_text, reply_markup=_main_menu_keyboard(user_id=user_id))
                return

            refreshed = userbot_db.get_service_by_id(service_id) or service
            refreshed = await _sync_service_runtime_from_panels(refreshed)
            settings = _get_subscription_settings()
            await context.bot.send_message(chat_id=user_id, text=result_text, reply_markup=_main_menu_keyboard(user_id=user_id))
            await context.bot.send_message(
                chat_id=user_id,
                text=_build_subscription_status_text(refreshed, lang=_user_lang(user_id)),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(service_id, show_direct_config=settings.get('show_direct_config', True), show_sub_link=settings.get('show_sub_link', True), show_configs=_should_show_configs_button(settings), show_detach=_is_connected_service(refreshed), lang=_user_lang(user_id)),
            )
            return

        if action == "copy_id":
            comment_meta = _parse_service_comment(service.get("comment") or "")
            service_code = str(comment_meta.get("code") or service.get("id") or "").strip()
            if not service_code:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=i18n.t("service_code_missing", _user_lang(user_id)),
                    reply_markup=_main_menu_keyboard(user_id=user_id),
                )
                return
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("service_code_yours", _user_lang(user_id), code=service_code),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(service_id, show_direct_config=settings.get('show_direct_config', True), show_sub_link=settings.get('show_sub_link', True), show_configs=_should_show_configs_button(settings), show_detach=_is_connected_service(service), lang=_user_lang(user_id)),
            )
            return

        if action == "configs":
            await _safe_edit_message_reply_markup(
                query,
                reply_markup=subscription_configs_keyboard(service_id, show_direct_config=settings.get('show_direct_config', True), show_sub_link=settings.get('show_sub_link', True), show_auto_sub_link=settings.get('show_auto_sub_link', False), show_sub_link_b64=settings.get('show_sub_link_b64', False), show_multi_server=settings.get('show_multi_server', False), show_multi_server_b64=settings.get('show_multi_server_b64', False), lang=_user_lang(user_id)),
            )
            return

        if action == "back":
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("main_menu_btn", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
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
                    text=i18n.t("no_config_link", _user_lang(user_id)),
                    reply_markup=_main_menu_keyboard(user_id=user_id),
                )
                return

            base_url = base_urls[0]

            config_items = []
            if action == "sub_link":
                if not settings.get("show_sub_link", True):
                    await context.bot.send_message(chat_id=user_id, text=i18n.t("sub_link_hidden", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
                    return
                config_items.append((i18n.t("label_sub_link", _user_lang(user_id)), f"{base_url}/all.txt"))
            elif action == "auto_sub":
                if not settings.get("show_auto_sub_link", False):
                    await context.bot.send_message(chat_id=user_id, text=i18n.t("auto_sub_hidden", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
                    return
                config_items.append((i18n.t("label_auto_sub", _user_lang(user_id)), f"{base_url}/sub/?asn=unknown"))
            elif action == "sub_b64":
                if not settings.get("show_sub_link_b64", False):
                    await context.bot.send_message(chat_id=user_id, text=i18n.t("b64_hidden", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
                    return
                config_items.append((i18n.t("label_sub_b64", _user_lang(user_id)), f"{base_url}/all.txt?base64=1"))
            elif action == "multi":
                if not settings.get("show_multi_server", False):
                    await context.bot.send_message(chat_id=user_id, text=i18n.t("smart_hidden", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
                    return
                managed_link, _ = _get_or_create_bot_sub_links(int(service_id), service=service)
                config_items.append((i18n.t("label_smart_link", _user_lang(user_id)), managed_link))
            elif action == "multi_b64":
                if not settings.get("show_multi_server_b64", False):
                    await context.bot.send_message(chat_id=user_id, text=i18n.t("smart_b64_hidden", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
                    return
                _, managed_link_b64 = _get_or_create_bot_sub_links(int(service_id), service=service)
                config_items.append((i18n.t("label_smart_b64", _user_lang(user_id)), managed_link_b64))

            if not config_items:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=i18n.t("no_active_links", _user_lang(user_id)),
                    reply_markup=_main_menu_keyboard(user_id=user_id),
                )
                return

            primary_link = config_items[0][1]
            qr_image = make_qr_image(primary_link)
            qr_caption = (
                i18n.t("qr_copy_hint", _user_lang(user_id))
                + f"{escape(primary_link)}</code>"
            )
            try:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=qr_image,
                    caption=qr_caption,
                    parse_mode="HTML",
                    reply_markup=subscription_links_keyboard(service_id, lang=_user_lang(user_id)),
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=qr_caption,
                    parse_mode="HTML",
                    reply_markup=subscription_links_keyboard(service_id, lang=_user_lang(user_id)),
                    disable_web_page_preview=True,
                )

            config_text_lines = [i18n.t("links_page_title", _user_lang(user_id)), ""]
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
                text=_cfg_text(user_id, "guide_text", "cfg_guide_intro", text_settings),
                reply_markup=guide_os_keyboard('m', lang=_user_lang(user_id)),
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
                    text=i18n.t("renew_disabled", _user_lang(user_id)),
                    reply_markup=_main_menu_keyboard(user_id=user_id),
                )
                return
            if not await _service_is_renewable_live(service):
                await context.bot.send_message(
                    chat_id=user_id,
                    text=_renew_not_allowed_text(lang=_user_lang(user_id)),
                    reply_markup=_main_menu_keyboard(user_id=user_id),
                )
                return
            try:
                renew_sid = int(service.get("server_id") or 0)
            except (TypeError, ValueError):
                renew_sid = 0
            if renew_sid <= 0:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=i18n.t("sub_server_invalid", _user_lang(user_id)),
                    reply_markup=_main_menu_keyboard(user_id=user_id),
                )
                return
            context.user_data[f"renew_target_{user_id}"] = int(service_id)
            await _send_buy_flow_for_server(user_id, renew_sid, user_id, context, is_renew=True)
            return

        if action == "detach":
            if not _is_connected_service(service):
                await context.bot.send_message(
                    chat_id=user_id,
                    text=i18n.t("sub_undetachable", _user_lang(user_id)),
                    reply_markup=_main_menu_keyboard(user_id=user_id),
                )
                return
            userbot_db.delete_service(int(service_id))
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("sub_detached_ok", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
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
        await context.bot.send_message(chat_id=user_id, text=i18n.t("main_menu_btn", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
        return

    if action == "svc":
        service_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        service = userbot_db.get_service_by_id(service_id) if service_id > 0 else None
        if not service:
            await context.bot.send_message(chat_id=user_id, text=i18n.t("sub_selected_missing", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        if not await _service_is_renewable_live(service):
            await context.bot.send_message(chat_id=user_id, text=_renew_not_allowed_text(lang=_user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        try:
            renew_sid = int(service.get("server_id") or 0)
        except (TypeError, ValueError):
            renew_sid = 0
        if renew_sid <= 0:
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("sub_server_invalid", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
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
        await context.bot.send_message(chat_id=user_id, text=i18n.t("main_menu_btn", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
        return
    if action == "card":
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_card_to_card", True)):
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("card_pay_disabled", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
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
            text=i18n.t("topup_amount_prompt", _user_lang(user_id)),
            reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
        )
        return
    if action == "zarinpal":
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_zarinpal", False)):
            await context.bot.send_message(chat_id=user_id, text=i18n.t("zarinpal_disabled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        text_settings = _get_text_settings()
        ztxt = str(text_settings.get("zarinpal_pro_text") or "").strip()
        if ztxt.lower() in {"none", "null"}:
            ztxt = ""
        if not ztxt or ztxt == "0":
            ztxt = _default_zarinpal_text(_user_lang(user_id))
        vouchers = userbot_db.list_active_zarin_vouchers(limit=20)
        if not vouchers:
            await context.bot.send_message(
                chat_id=user_id,
                text=ztxt + i18n.t("zarpal_no_links", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return
        await context.bot.send_message(
            chat_id=user_id,
            text=ztxt,
            reply_markup=_build_zarinpal_links_keyboard(vouchers, lang=_user_lang(user_id)),
            disable_web_page_preview=True,
        )
        return
    if action == "perfect":
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_perfect_money", False)):
            await context.bot.send_message(chat_id=user_id, text=i18n.t("perfectmoney_disabled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        await context.bot.send_message(chat_id=user_id, text=i18n.t("perfectmoney_coming", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
        return
    if action == "crypto":
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_crypto", False)):
            await context.bot.send_message(chat_id=user_id, text=i18n.t("crypto_disabled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        await context.bot.send_message(chat_id=user_id, text=i18n.t("crypto_coming", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
        return
    if action == "coupon":
        mkt = _get_marketing_settings()
        if not (
            bool(mkt.get("enable_discount_code", False))
            or bool(mkt.get("enable_increase_code", False))
        ):
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("gift_disabled", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return
        set_user_step(context, user_id, "WAIT_COUPON_CODE")
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text=i18n.t("coupon_prompt", _user_lang(user_id)),
            reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
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
        await context.bot.send_message(chat_id=user_id, text=i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
        return

    if action == "receipt_done":
        set_user_step(context, user_id, "WAIT_RECEIPT_IMAGE")
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text=i18n.t("pay_receipt_prompt", _user_lang(user_id)),
            reply_markup=receipt_cancel_keyboard(lang=_user_lang(user_id)),
        )
        return
    return


async def _cb_trial_back(update, context, query, data, user_id):
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass
    await context.bot.send_message(chat_id=user_id, text=i18n.t("main_menu_btn", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
    return


async def _cb_trial_loc(update, context, query, data, user_id):
    await query.answer()
    try:
        sid = int(data.split(":")[2])
    except Exception:
        await query.answer(i18n.t("invalid_data", _user_lang(user_id)), show_alert=True)
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
            text=i18n.t("trial_already_used", _user_lang(user_id)),
            reply_markup=_main_menu_keyboard(user_id=user_id),
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
            text=i18n.t("trial_disabled", _user_lang(user_id)),
            reply_markup=_main_menu_keyboard(user_id=user_id),
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
        text=i18n.t("trial_name_prompt", _user_lang(user_id)),
        reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
    )
    return


async def _cb_buy_back_main(update, context, query, data, user_id, br, text_settings):
    if not bool(br.get("enable_buy", True)):
        await query.answer(i18n.t("buy_disabled", _user_lang(user_id)), show_alert=True)
        return
    context.user_data.pop(f"buy_menu_open_until_{user_id}", None)
    servers = _get_location_servers()
    server_columns = int(br.get("server_columns") or 1)
    await _safe_edit_message_text(
        query,
        _cfg_text(user_id, "servers_list_text", "cfg_servers_list", text_settings),
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


async def _cb_buy_router(update, context, query, data, user_id, br, text_settings):
    if data.startswith("buy:loc:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer(i18n.t("buy_disabled", _user_lang(user_id)), show_alert=True)
            return
        context.user_data.pop(f"buy_menu_open_until_{user_id}", None)
        sid = int(data.split(":")[2])
        data_plans = plans_storage._load_all_plans()
        server_block = data_plans.get("servers", {}).get(str(sid), {})
        
        display_mode = _resolve_plan_display_mode(server_block)
        
        if display_mode == "mixed":
            # نمایش صفحه اصلی خرید با ویزارد و دکمه پلن‌های آماده
            await show_main_buy_menu(query, sid, server_block, user_id, context)
        elif display_mode == "dynamic":
            await start_dynamic_wizard(query, context, sid, user_id, server_block)
        else:
            await show_fixed_categories(query, sid, server_block)

    # انتخاب‌های حالت ترکیبی
    elif data.startswith("buy:mixed:fixed:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer(i18n.t("buy_disabled", _user_lang(user_id)), show_alert=True)
            return
        sid = int(data.split(":")[3])
        server_block = plans_storage._load_all_plans().get("servers", {}).get(str(sid), {})
        await show_fixed_categories(query, sid, server_block)

    elif data.startswith("buy:mixed:dyn:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer(i18n.t("buy_disabled", _user_lang(user_id)), show_alert=True)
            return
        sid = int(data.split(":")[3])
        server_block = plans_storage._load_all_plans().get("servers", {}).get(str(sid), {})
        await start_dynamic_wizard(query, context, sid, user_id, server_block)

    # دسته‌بندی و پلن‌های ثابت
    elif data.startswith("buy:cat:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer(i18n.t("buy_disabled", _user_lang(user_id)), show_alert=True)
            return
        parts = data.split(":")
        sid, cat_id = int(parts[2]), int(parts[3])
        server_block = plans_storage._load_all_plans().get("servers", {}).get(str(sid), {})
        plans = [p for p in server_block.get("plans", []) if p.get("category_id") == cat_id]
        txp = _get_tx_plans_settings()
        plans = _sort_plans(plans, txp)
        plan_columns = int(br.get("plan_columns") or 1)
        uv = bool(br.get("renew_unlimited_volume", False))
        ut = bool(br.get("renew_unlimited_time", False))
        uv_from = int(br.get("renew_unlimited_volume_from_gb") or 1000)
        ut_from = int(br.get("renew_unlimited_time_from_days") or 365)
        
        await _safe_edit_message_text(
            query,
            _cfg_text(user_id, "plans_list_text", "cfg_plans_list", text_settings), parse_mode="Markdown",
            reply_markup=plans_keyboard(plans, sid, cat_id, columns=plan_columns, unlimited_volume=uv, unlimited_volume_from=uv_from, unlimited_time=ut, unlimited_time_from=ut_from, sort_by_priority=False, rtl_rows=bool(txp.get('plan_sort_desc', False)), lang=_user_lang(user_id))
        )

    elif data.startswith("buy:plan:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer(i18n.t("buy_disabled", _user_lang(user_id)), show_alert=True)
            return
        parts = data.split(":")
        sid, plan_id = int(parts[2]), int(parts[3])
        server_block = plans_storage._load_all_plans().get("servers", {}).get(str(sid), {})
        plan = next((p for p in server_block.get("plans", []) if p.get("id") == plan_id), None)
        
        if not plan: return
        # نمایش اطلاعات پلن انتخاب شده (طبق اسکرین‌شات)
        plan_gb = float(plan["gb"])
        plan_days = int(plan["days"])
        plan_gb_text = i18n.t("word_unlimited", _user_lang(user_id)) if _is_unlimited_volume(plan_gb) else f"{plan_gb:g}{i18n.t('unit_gb_p', _user_lang(user_id))}"
        plan_days_text = i18n.t("word_unlimited", _user_lang(user_id)) if _is_unlimited_time(plan_days) else f"{plan_days}{i18n.t('unit_days_p', _user_lang(user_id))}"
        text = (
            i18n.t("plan_info_header", _user_lang(user_id))
            + f"{plan_gb_text}\n"
            + i18n.t("plan_info_time", _user_lang(user_id))
            + f"{plan_days_text}\n"
            + i18n.t("plan_info_price", _user_lang(user_id))
            + f"{plan['price']:,}"
        )
        await _safe_edit_message_text(
            query,
            text,
            reply_markup=selected_plan_keyboard(sid, int(plan['gb']), int(plan['days']), int(plan['price']), plan_id=int(plan.get('id') or 0), lang=_user_lang(user_id))
        )

    # ویزارد پویا (دکمه‌های مثبت و منفی)
    elif data.startswith("wiz:"):
        async with _USER_WIZARD_LOCKS[user_id]:
            parts = data.split(":")
            sid, action = int(parts[1]), parts[2]

            wiz_data = context.user_data.get(f"wiz_{user_id}")
            if not wiz_data:
                data_plans = plans_storage._load_all_plans()
                server_block = data_plans.get("servers", {}).get(str(sid), {})
                dyn_settings = server_block.get("dynamic_settings", {})
                default_gb = dyn_settings.get("min_gb", 20)
                default_months = dyn_settings.get("min_month", 1)
                wiz_data = {"gb": default_gb, "months": default_months}
                context.user_data[f"wiz_{user_id}"] = wiz_data

            data_plans = plans_storage._load_all_plans()
            server_block = data_plans.get("servers", {}).get(str(sid), {})
            dyn_settings = server_block.get("dynamic_settings", {})
            display_mode = _resolve_plan_display_mode(server_block)
            gb, months = wiz_data['gb'], wiz_data['months']

            min_gb = max(1, int(dyn_settings.get('min_gb', 10) or 10))
            max_gb = max(min_gb, int(dyn_settings.get('max_gb', 500) or 500))
            min_month = max(1, int(dyn_settings.get('min_month', 1) or 1))
            max_month = max(min_month, int(dyn_settings.get('max_month', 12) or 12))
            step_gb = max(1, int(dyn_settings.get('step_gb', 10) or 10))
            step_month = max(1, int(dyn_settings.get('step_month', 1) or 1))

            if action == "gb_inc":
                if gb >= max_gb:
                    await query.answer(i18n.t("max_volume_alert", _user_lang(user_id), value=max_gb), show_alert=True)
                    return
                gb = min(max_gb, gb + step_gb)
            elif action == "gb_dec":
                if gb <= min_gb:
                    await query.answer(i18n.t("min_volume_alert", _user_lang(user_id), value=min_gb), show_alert=True)
                    return
                gb = max(min_gb, gb - step_gb)
            elif action == "month_inc":
                if months >= max_month:
                    await query.answer(i18n.t("max_period_alert", _user_lang(user_id), value=max_month), show_alert=True)
                    return
                months = min(max_month, months + step_month)
            elif action == "month_dec":
                if months <= min_month:
                    await query.answer(i18n.t("min_period_alert", _user_lang(user_id), value=min_month), show_alert=True)
                    return
                months = max(min_month, months - step_month)
            elif action == "show_fixed":
                if display_mode != "mixed":
                    await query.answer(i18n.t("mixed_mode_only", _user_lang(user_id)), show_alert=True)
                    return
                plans = server_block.get("plans", [])
                if not plans:
                    await query.answer(i18n.t("no_ready_plans", _user_lang(user_id)), show_alert=True)
                    return
                txp = _get_tx_plans_settings()
                ordered = _sort_plans(plans, txp)
                plan_columns = int(br.get("plan_columns") or 1)
                uv = bool(br.get("renew_unlimited_volume", False))
                ut = bool(br.get("renew_unlimited_time", False))
                uv_from = int(br.get("renew_unlimited_volume_from_gb") or 1000)
                ut_from = int(br.get("renew_unlimited_time_from_days") or 365)
                await _safe_edit_message_text(
                    query,
                    _cfg_text(user_id, "plans_list_text", "cfg_plans_list", text_settings),
                    parse_mode="Markdown",
                    reply_markup=plans_keyboard(ordered, sid, 0, columns=plan_columns, unlimited_volume=uv, unlimited_volume_from=uv_from, unlimited_time=ut, unlimited_time_from=ut_from, sort_by_priority=False, back_to_categories=False, rtl_rows=bool(txp.get('plan_sort_desc', False)), lang=_user_lang(user_id)),
                )
                return

            wiz_data['gb'], wiz_data['months'] = gb, months
            context.user_data[f"wiz_{user_id}"] = wiz_data

            price, off_percent = _calc_dynamic_price(gb, months, dyn_settings)
            if display_mode == "mixed":
                markup = mixed_buy_keyboard(sid, gb, months, price, off_percent=off_percent, lang=_user_lang(user_id))
            else:
                markup = buy_wizard_keyboard(sid, gb, months, price, off_percent=off_percent, lang=_user_lang(user_id))
            await _safe_edit_message_reply_markup(query, reply_markup=markup)

    # تایید نهایی و هدایت به پرداخت
    elif data.startswith("buy:confirm_dyn:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer(i18n.t("buy_disabled", _user_lang(user_id)), show_alert=True)
            return
        # نمایش اطلاعات پلن انتخاب شده بعد از خرید بسته دلخواه
        parts = data.split(":")
        sid = int(parts[2])
        wiz_data = context.user_data.get(f"wiz_{user_id}")
        if not wiz_data:
            await query.answer(i18n.t("session_expired", _user_lang(user_id)), show_alert=True)
            return
        gb = int(wiz_data.get('gb') or 0)
        days = int(wiz_data.get('months') or 0) * 30
        dyn_settings = plans_storage._load_all_plans().get("servers", {}).get(str(sid), {}).get("dynamic_settings", {})
        price, off_percent = _calc_dynamic_price(gb, wiz_data.get("months"), dyn_settings)

        text = (
            i18n.t("plan_info_header", _user_lang(user_id))
            + f"{gb}{i18n.t('unit_gb_p', _user_lang(user_id))}\n"
            + i18n.t("plan_info_time", _user_lang(user_id))
            + f"{days}{i18n.t('unit_days_p', _user_lang(user_id))}\n"
            + i18n.t("plan_info_price", _user_lang(user_id))
            + f"{price:,}"
        )
        if off_percent > 0:
            text += i18n.t("plan_discount_line", _user_lang(user_id), percent=off_percent)
        await _safe_edit_message_text(query, text, reply_markup=selected_plan_keyboard(sid, gb, days, price, lang=_user_lang(user_id)))
        return

    elif data.startswith("buy:pay_wallet:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer(i18n.t("buy_disabled", _user_lang(user_id)), show_alert=True)
            return
        # پرداخت از کیف پول (طبق اسکرین‌شات)
        await query.answer()
        parts = data.split(":")
        sid = int(parts[2])
        gb = int(parts[3])
        days = int(parts[4])
        price = int(parts[5])
        plan_id = int(parts[6]) if len(parts) > 6 else 0
        # قیمت همیشه سمت سرور دوباره محاسبه می‌شود — ضد دستکاری callback data
        expected_price = _expected_server_price(sid, gb, days, plan_id)
        if expected_price is None:
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("btn_expired_restart", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return
        price = expected_price

        u_db = userbot_db.get_user_by_telegram_id(user_id)
        balance = int((u_db or {}).get('wallet_balance') or 0)
        internal_user_id = (u_db or {}).get('id')
        renew_target_service_id = int(context.user_data.get(f"renew_target_{user_id}") or 0)

        if balance >= price:
            context.user_data[f"pending_wallet_{user_id}"] = {
                "internal_user_id": internal_user_id,
                "amount": price,
                "sid": sid,
                "gb": gb,
                "days": days,
                "renew_service_id": renew_target_service_id,
            }

            # در تمدید: نام سرویس قبلی حفظ می‌شود و نباید دوباره از کاربر پرسیده شود.
            if renew_target_service_id > 0:
                renew_service = userbot_db.get_service_by_id(renew_target_service_id) or {}
                service_name = (renew_service.get("name") or "").strip() or "سرویس"
                try:
                    await query.message.delete()
                except Exception:
                    pass
                ok = await _process_wallet_purchase(
                    context=context,
                    user_id=user_id,
                    tg_user=query.from_user,
                    chat_id=user_id,
                    pending_wallet=context.user_data.get(f"pending_wallet_{user_id}") or {},
                    service_name=service_name,
                )
                context.user_data.pop(f"pending_wallet_{user_id}", None)
                context.user_data.pop(f"renew_target_{user_id}", None)
                set_user_step(context, user_id, None)
                if not ok:
                    await context.bot.send_message(chat_id=user_id, text=i18n.t("renew_failed", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
                return

            # در خرید عادی: نام سرویس از کاربر گرفته می‌شود.
            set_user_step(context, user_id, "WAIT_SERVICE_NAME")
            await query.message.delete()
            await context.bot.send_message(chat_id=user_id, text=i18n.t("service_name_prompt", _user_lang(user_id)), reply_markup=cancel_keyboard(lang=_user_lang(user_id)))
            return

        # موجودی کافی نیست -> کارت به کارت
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_card_to_card", True)):
            await query.message.delete()
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("wallet_low_no_card", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return
        try:
            card_info = database.get_next_card()
        except Exception:
            card_info = None
        if not card_info:
            try:
                card_info = database.get_random_card()
            except Exception:
                card_info = None
        card_number = (card_info or {}).get('number') or "-"
        card_owner = (card_info or {}).get('owner') or "-"
        card_bank = (card_info or {}).get('bank') or ""

        pay_amount_toman, tx_marker = _apply_random_tx_marker(price, _get_tx_plans_settings())
        msg = _build_card_to_card_payment_text(
            amount_toman=pay_amount_toman,
            card_number=card_number,
            card_owner=card_owner,
            card_bank=card_bank,
            text_settings=text_settings,
            lang=_user_lang(user_id),
        )
        if tx_marker > 0:
            msg = i18n.t("tx_marker_applied", _user_lang(user_id), marker=f"{tx_marker:,}", msg=msg)

        context.user_data[f"pending_pay_{user_id}"] = {
            "amount": pay_amount_toman,
            "sid": sid,
            "gb": gb,
            "days": days,
            "plan_id": None,
            "renew_service_id": renew_target_service_id,
            "base_amount": price,
            "tx_marker": tx_marker,
        }
        set_user_step(context, user_id, "WAIT_RECEIPT_CONFIRM")
        await query.message.delete()
        await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="HTML", reply_markup=confirm_payment_inline_keyboard())
        return

    elif data.startswith("buy:pay_direct:"):
        if not bool(br.get("enable_buy", True)):
            await query.answer(i18n.t("buy_disabled", _user_lang(user_id)), show_alert=True)
            return
        await query.answer()
        parts = data.split(":")
        sid = int(parts[2])
        gb = int(parts[3])
        days = int(parts[4])
        price = int(parts[5])
        plan_id = int(parts[6]) if len(parts) > 6 else 0
        # قیمت همیشه سمت سرور دوباره محاسبه می‌شود — ضد دستکاری callback data
        expected_price = _expected_server_price(sid, gb, days, plan_id)
        if expected_price is None:
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("btn_expired_restart", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return
        price = expected_price

        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_card_to_card", True)):
            await query.message.delete()
            await context.bot.send_message(
                chat_id=user_id,
                text=i18n.t("direct_pay_disabled", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return

        renew_target_service_id = int(context.user_data.get(f"renew_target_{user_id}") or 0)
        if renew_target_service_id > 0:
            renew_service = userbot_db.get_service_by_id(renew_target_service_id) or {}
            direct_service_name = (renew_service.get("name") or "").strip() or _generate_random_service_name()
        else:
            direct_service_name = _generate_random_service_name()

        try:
            card_info = database.get_next_card()
        except Exception:
            card_info = None
        if not card_info:
            try:
                card_info = database.get_random_card()
            except Exception:
                card_info = None
        card_number = (card_info or {}).get("number") or "-"
        card_owner = (card_info or {}).get("owner") or "-"
        card_bank = (card_info or {}).get("bank") or ""

        pay_amount_toman, tx_marker = _apply_random_tx_marker(price, _get_tx_plans_settings())
        msg = _build_card_to_card_payment_text(
            amount_toman=pay_amount_toman,
            card_number=card_number,
            card_owner=card_owner,
            card_bank=card_bank,
            text_settings=text_settings,
            lang=_user_lang(user_id),
        )
        if tx_marker > 0:
            msg = i18n.t("tx_marker_applied", _user_lang(user_id), marker=f"{tx_marker:,}", msg=msg)
        msg += i18n.t("card_receipt_done_short", _user_lang(user_id))

        context.user_data[f"pending_pay_{user_id}"] = {
            "amount": pay_amount_toman,
            "sid": sid,
            "gb": gb,
            "days": days,
            "plan_id": None,
            "renew_service_id": renew_target_service_id,
            "base_amount": price,
            "tx_marker": tx_marker,
            "direct_buy": True,
            "direct_service_name": direct_service_name,
        }
        set_user_step(context, user_id, "WAIT_RECEIPT_CONFIRM")
        await query.message.delete()
        await context.bot.send_message(
            chat_id=user_id,
            text=msg,
            parse_mode="HTML",
            reply_markup=confirm_payment_inline_keyboard(),
        )
        return


async def _rs_admin_direct_reply(update, context, user_id, text, step):
    if step == "WAIT_ADMIN_DIRECT_REPLY_TEXT":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        reply_text = str(text or "").strip()
        if not reply_text:
            await update.message.reply_text(i18n.t("reply_as_text", _user_lang(user_id)))
            return

        user_row = userbot_db.get_user_by_telegram_id(user_id)
        if not user_row:
            internal_uid = userbot_db.upsert_user(
                update.effective_user.id,
                update.effective_user.username,
                update.effective_user.full_name,
            )
            user_row = userbot_db.get_user_by_id(internal_uid) or {}
        internal_uid = int((user_row or {}).get("id") or 0)
        display_name = str(
            (user_row or {}).get("full_name")
            or update.effective_user.full_name
            or (user_row or {}).get("username")
            or update.effective_user.username
            or user_id
        ).strip()

        if not (ADMIN_ID and ADMIN_BOT_TOKEN):
            set_user_step(context, user_id, None)
            await update.message.reply_text(
                i18n.t("support_settings_incomplete", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return

        admin_text = (
            "📬 تیکت جدیدی دریافت شد\n"
            f"📄 متن تیکت: {reply_text}"
        )

        rows = []
        if internal_uid > 0:
            rows.append([InlineKeyboardButton(display_name, callback_data=f"userbot:user:{internal_uid}")])
            rows.append([InlineKeyboardButton(i18n.t("btn_reply_ticket", _user_lang(user_id)), callback_data=f"userbot:user:{internal_uid}:message")])
        admin_kb = InlineKeyboardMarkup(rows) if rows else None

        try:
            admin_bot = Bot(token=ADMIN_BOT_TOKEN)
            await admin_bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_text,
                reply_markup=admin_kb,
            )
        except Exception as e:
            logger.warning("Failed to forward direct admin message reply (tg=%s): %s", user_id, e)
            await update.message.reply_text(
                i18n.t("send_failed_retry", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return

        set_user_step(context, user_id, None)
        await update.message.reply_text(
            i18n.t("support_msg_sent", _user_lang(user_id)),
            reply_markup=_main_menu_keyboard(user_id=user_id),
        )
        return


async def _rs_ticket_title(update, context, user_id, text, step):
    if step == "WAIT_TICKET_TITLE":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_ticket_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        title = str(text or "").strip()
        if not title:
            await update.message.reply_text(i18n.t("title_required", _user_lang(user_id)), reply_markup=_ticket_text_cancel_keyboard())
            return
        pending = context.user_data.get(f"pending_ticket_{user_id}") or {}
        pending["title"] = title
        context.user_data[f"pending_ticket_{user_id}"] = pending
        set_user_step(context, user_id, "WAIT_TICKET_QUESTION")
        await update.message.reply_text(i18n.t("ticket_question_prompt", _user_lang(user_id)), reply_markup=_ticket_text_cancel_keyboard())
        return


async def _rs_ticket_question(update, context, user_id, text, step):
    if step == "WAIT_TICKET_QUESTION":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_ticket_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        question = str(text or "").strip()
        if not question:
            await update.message.reply_text(i18n.t("question_empty", _user_lang(user_id)), reply_markup=_ticket_text_cancel_keyboard())
            return
        pending = context.user_data.get(f"pending_ticket_{user_id}") or {}
        pending["question"] = question
        context.user_data[f"pending_ticket_{user_id}"] = pending
        set_user_step(context, user_id, "WAIT_TICKET_SCREENSHOT")
        await update.message.reply_text(
            i18n.t("screenshot_or_skip", _user_lang(user_id)),
            reply_markup=ticket_skip_screenshot_keyboard(lang=_user_lang(user_id)),
        )
        return


async def _rs_ticket_screenshot(update, context, user_id, text, step):
    if step == "WAIT_TICKET_SCREENSHOT":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_ticket_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        pending = context.user_data.get(f"pending_ticket_{user_id}") or {}
        skip_text = _normalize_action_text(text or "")
        if update.message.photo:
            pending["receipt_photo_id"] = update.message.photo[-1].file_id
        elif skip_text in {"▶️رد کردن", "▶️ رد کردن", "رد کردن", "⏭️رد کردن", "⏭️ رد کردن"}:
            pending["receipt_photo_id"] = ""
        else:
            await update.message.reply_text(
                i18n.t("photo_or_skip", _user_lang(user_id)),
                reply_markup=ticket_skip_screenshot_keyboard(lang=_user_lang(user_id)),
            )
            return

        context.user_data[f"pending_ticket_{user_id}"] = pending
        set_user_step(context, user_id, "WAIT_TICKET_CONFIRM")
        await update.message.reply_text(
            _ticket_compose_preview_text(pending),
            reply_markup=ticket_confirm_keyboard(lang=_user_lang(user_id)),
        )
        return


async def _rs_ticket_confirm(update, context, user_id, text, step):
    if step == "WAIT_TICKET_CONFIRM":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_ticket_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        await update.message.reply_text(
            i18n.t("ticket_use_send_edit", _user_lang(user_id)),
            reply_markup=ticket_confirm_keyboard(lang=_user_lang(user_id)),
        )
        return


async def _rs_ticket_reply(update, context, user_id, text, step):
    if step in {"WAIT_TICKET_REPLY", "WAIT_TICKET_REPLY_TEXT"}:
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        state = context.user_data.get(f"ticket_reply_{user_id}") or {}
        ticket_code = int(state.get("ticket_code") or 0)
        if ticket_code <= 0:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text(i18n.t("ticket_info_invalid", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        user_row = userbot_db.get_user_by_telegram_id(user_id)
        internal_uid = int((user_row or {}).get("id") or 0)
        ticket = userbot_db.get_user_ticket_by_code(internal_uid, ticket_code)
        if not ticket:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text(i18n.t("ticket_not_found", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        message_text = str(text or "").strip()
        if not message_text:
            await update.message.reply_text(
                i18n.t("reply_full_required", _user_lang(user_id)),
                reply_markup=_ticket_text_cancel_keyboard("reply"),
            )
            return

        state["reply_text"] = message_text
        context.user_data[f"ticket_reply_{user_id}"] = state
        set_user_step(context, user_id, "WAIT_TICKET_REPLY_SCREENSHOT")
        await update.message.reply_text(
            i18n.t("screenshot_or_skip", _user_lang(user_id)),
            reply_markup=ticket_skip_screenshot_keyboard('reply', lang=_user_lang(user_id)),
        )
        return


async def _rs_ticket_reply_screenshot(update, context, user_id, text, step):
    if step == "WAIT_TICKET_REPLY_SCREENSHOT":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        state = context.user_data.get(f"ticket_reply_{user_id}") or {}
        ticket_code = int(state.get("ticket_code") or 0)
        if ticket_code <= 0:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text(i18n.t("ticket_info_invalid", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        skip_text = _normalize_action_text(text or "")
        if update.message.photo:
            state["receipt_photo_id"] = update.message.photo[-1].file_id
        elif skip_text in {"▶️رد کردن", "▶️ رد کردن", "رد کردن", "⏭️رد کردن", "⏭️ رد کردن"}:
            state["receipt_photo_id"] = ""
        else:
            await update.message.reply_text(
                i18n.t("photo_or_skip", _user_lang(user_id)),
                reply_markup=ticket_skip_screenshot_keyboard('reply', lang=_user_lang(user_id)),
            )
            return

        context.user_data[f"ticket_reply_{user_id}"] = state
        set_user_step(context, user_id, "WAIT_TICKET_REPLY_CONFIRM")
        preview_text = _ticket_reply_preview_text(state)
        preview_photo_id = str(state.get("receipt_photo_id") or "").strip()
        if preview_photo_id:
            try:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=preview_photo_id,
                    caption=preview_text,
                    reply_markup=ticket_confirm_keyboard('reply', lang=_user_lang(user_id)),
                )
            except Exception:
                await update.message.reply_text(
                    preview_text,
                    reply_markup=ticket_confirm_keyboard('reply', lang=_user_lang(user_id)),
                )
        else:
            await update.message.reply_text(
                preview_text,
                reply_markup=ticket_confirm_keyboard('reply', lang=_user_lang(user_id)),
            )
        return


async def _rs_ticket_reply_confirm(update, context, user_id, text, step):
    if step == "WAIT_TICKET_REPLY_CONFIRM":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"ticket_reply_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        await update.message.reply_text(
            i18n.t("reply_use_send_edit", _user_lang(user_id)),
            reply_markup=ticket_confirm_keyboard('reply', lang=_user_lang(user_id)),
        )
        return


async def _rs_connect_sub_input(update, context, user_id, text, step):
    if step == "WAIT_CONNECT_SUB_INPUT":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        parsed_uuid = _extract_uuid_from_user_input(text or "")
        if not parsed_uuid:
            await update.message.reply_text(
                i18n.t("uuid_invalid", _user_lang(user_id)),
                reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
            )
            return

        u_db = userbot_db.get_user_by_telegram_id(user_id)
        if not u_db:
            internal_user_id = userbot_db.upsert_user(
                update.effective_user.id,
                update.effective_user.username,
                update.effective_user.full_name,
            )
            u_db = userbot_db.get_user_by_id(internal_user_id) or {}
        internal_user_id = int((u_db or {}).get("id") or 0)
        if internal_user_id <= 0:
            await update.message.reply_text(i18n.t("user_not_found", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            set_user_step(context, user_id, None)
            return

        # امنیت: UUID فقط برای یک کاربر قابل اتصال باشد.
        owner = userbot_db.get_service_owner_by_panel_uuid(parsed_uuid)
        if owner and int(owner.get("user_id") or 0) != int(internal_user_id):
            await update.message.reply_text(
                i18n.t("sub_linked_other", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            set_user_step(context, user_id, None)
            return

        existing_self = userbot_db.get_user_service_by_panel_uuid(internal_user_id, parsed_uuid)
        if existing_self:
            set_user_step(context, user_id, None)
            service = await _sync_service_runtime_from_panels(existing_self)
            settings = _get_subscription_settings()
            await update.message.reply_text(
                i18n.t("sub_already_linked_self", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            await update.message.reply_text(
                _build_subscription_status_text(service, lang=_user_lang(user_id)),
                parse_mode="Markdown",
                reply_markup=subscription_status_keyboard(service.get('id'), show_direct_config=settings.get('show_direct_config', True), show_sub_link=settings.get('show_sub_link', True), show_configs=_should_show_configs_button(settings), show_detach=_is_connected_service(service), lang=_user_lang(user_id)),
            )
            return

        await update.message.reply_text(i18n.t("sub_checking", _user_lang(user_id)))
        targets = await _find_panel_user_targets_by_uuid(parsed_uuid)
        if not targets:
            await update.message.reply_text(
                i18n.t("uuid_not_found_panels", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            set_user_step(context, user_id, None)
            return

        # سرور اصلی اتصال: اولین سرور پیدا‌شده
        primary_server, primary_user = targets[0]
        service_name = str(primary_user.get("name") or "اشتراک متصل‌شده").strip() or "اشتراک متصل‌شده"
        usage_limit = _to_float(primary_user.get("usage_limit_GB"), 0.0)
        total_usage = 0.0
        min_days_left: Optional[int] = None
        latest_last_online: Optional[datetime] = None
        for _srv, pu in targets:
            total_usage += _to_float(pu.get("current_usage_GB"), 0.0)
            dleft = _days_left_from_panel_user(pu)
            if dleft is not None:
                min_days_left = dleft if min_days_left is None else min(min_days_left, dleft)
            dt = _parse_panel_datetime(pu.get("last_online"))
            if dt and (latest_last_online is None or dt > latest_last_online):
                latest_last_online = dt

        server_id = int(primary_server.get("id") or 0)
        server_title = str(primary_server.get("title") or f"سرور #{server_id}").strip()
        service_code = _generate_service_code()
        service_comment = f"uuid:{parsed_uuid}|code:{service_code}|linked:1|source:connect"
        service_db_id = None

        try:
            with userbot_db._get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO userbot_services
                    (user_id, name, server_id, server_title, usage_current, usage_limit, days_left, last_online, comment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(internal_user_id),
                        service_name,
                        int(server_id),
                        server_title,
                        float(total_usage),
                        float(usage_limit),
                        int(min_days_left if min_days_left is not None else 0),
                        (latest_last_online.strftime("%Y-%m-%d %H:%M:%S") if latest_last_online else None),
                        service_comment,
                    ),
                )
                service_db_id = int(cur.lastrowid)
        except Exception as e:
            logger.exception("Failed persisting connected subscription (telegram_id=%s)", user_id)
            await update.message.reply_text(
                i18n.t("sub_connect_failed", _user_lang(user_id), error=e),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            set_user_step(context, user_id, None)
            return

        for srv, pu in targets:
            try:
                sid = int(srv.get("id") or 0)
                if sid <= 0:
                    continue
                userbot_db.add_service_node(
                    service_id=int(service_db_id),
                    server_id=sid,
                    panel_user_uuid=parsed_uuid,
                    server_title=str(srv.get("title") or ""),
                    panel_user_id=(str(pu.get("id")).strip() if pu.get("id") is not None else None),
                    is_active=1,
                )
            except Exception:
                pass

        set_user_step(context, user_id, None)
        service = userbot_db.get_service_by_id(int(service_db_id)) or {}
        service = await _sync_service_runtime_from_panels(service)
        settings = _get_subscription_settings()
        await update.message.reply_text(
            i18n.t("sub_linked_ok", _user_lang(user_id)),
            reply_markup=_main_menu_keyboard(user_id=user_id),
        )
        await update.message.reply_text(
            _build_subscription_status_text(service, lang=_user_lang(user_id)),
            parse_mode="Markdown",
            reply_markup=subscription_status_keyboard(service.get('id'), show_direct_config=settings.get('show_direct_config', True), show_sub_link=settings.get('show_sub_link', True), show_configs=_should_show_configs_button(settings), show_detach=_is_connected_service(service), lang=_user_lang(user_id)),
        )
        return


async def _rs_rename_service(update, context, user_id, text, step):
    if step == "WAIT_RENAME_SERVICE_NAME":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_rename_service_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        pending_rename = context.user_data.get(f"pending_rename_service_{user_id}", None) or {}
        service_id = int(pending_rename.get("service_id") or 0)
        if service_id <= 0:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_rename_service_{user_id}", None)
            await update.message.reply_text(i18n.t("service_info_invalid", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        u_db = userbot_db.get_user_by_telegram_id(user_id) or {}
        internal_user_id = int(u_db.get("id") or 0)
        service = userbot_db.get_service_by_id(service_id) or {}
        if not service or internal_user_id <= 0 or int(service.get("user_id") or 0) != internal_user_id:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_rename_service_{user_id}", None)
            await update.message.reply_text(i18n.t("sub_target_missing", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        new_name = _normalize_service_name_input(text or "")
        if len(new_name) < 3:
            await update.message.reply_text(
                i18n.t("name_too_short", _user_lang(user_id)),
                reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
            )
            return
        if len(new_name) > 64:
            await update.message.reply_text(
                i18n.t("name_too_long", _user_lang(user_id)),
                reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
            )
            return

        old_name = str(service.get("name") or "").strip()
        if new_name == old_name:
            await update.message.reply_text(
                i18n.t("rename_same_name", _user_lang(user_id)),
                reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
            )
            return

        await update.message.reply_text(i18n.t("rename_in_progress", _user_lang(user_id)))
        ok, result_text = await _rename_service_across_panels_and_db(service, new_name, lang=_user_lang(user_id))
        if not ok:
            await update.message.reply_text(result_text, reply_markup=cancel_keyboard(lang=_user_lang(user_id)))
            return

        set_user_step(context, user_id, None)
        context.user_data.pop(f"pending_rename_service_{user_id}", None)
        refreshed = userbot_db.get_service_by_id(service_id) or service
        refreshed = await _sync_service_runtime_from_panels(refreshed)
        settings = _get_subscription_settings()
        await update.message.reply_text(result_text, reply_markup=_main_menu_keyboard(user_id=user_id))
        await update.message.reply_text(
            _build_subscription_status_text(refreshed, lang=_user_lang(user_id)),
            parse_mode="Markdown",
            reply_markup=subscription_status_keyboard(refreshed.get('id'), show_direct_config=settings.get('show_direct_config', True), show_sub_link=settings.get('show_sub_link', True), show_configs=_should_show_configs_button(settings), show_detach=_is_connected_service(refreshed), lang=_user_lang(user_id)),
        )
        return


async def _rs_trial_service_name(update, context, user_id, text, step):
    if step == "WAIT_TRIAL_SERVICE_NAME":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        service_name = (text or "").strip()
        if not service_name:
            await update.message.reply_text(i18n.t("name_required", _user_lang(user_id)), reply_markup=cancel_keyboard(lang=_user_lang(user_id)))
            return

        pending_trial = context.user_data.get(f"pending_trial_{user_id}", None) or {}
        internal_user_id = int(pending_trial.get("internal_user_id") or 0)
        sid = int(pending_trial.get("sid") or 0)

        if not internal_user_id:
            u_db = userbot_db.get_user_by_telegram_id(user_id)
            internal_user_id = int((u_db or {}).get("id") or 0)

        if not internal_user_id or sid <= 0:
            await update.message.reply_text(
                i18n.t("trial_info_incomplete", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            return

        user_row = userbot_db.get_user_by_id(internal_user_id) or {}
        if int(user_row.get("got_free_trial") or 0) == 1:
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            await update.message.reply_text(
                i18n.t("trial_already_used", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return

        trial_settings = userbot_db.get_trial_spec_settings()
        if not bool(trial_settings.get("enabled", True)):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            await update.message.reply_text(
                i18n.t("trial_disabled", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return

        gb = float(trial_settings.get("usage_gb") or 0.5)
        days = int(trial_settings.get("days") or 1)
        if gb <= 0:
            gb = 0.5
        if days <= 0:
            days = 1

        server = database.get_server_by_id(sid)
        if not server:
            await update.message.reply_text(
                i18n.t("server_missing_retry", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            return

        payload = {
            "name": service_name,
            "usage_limit_GB": float(gb),
            "package_days": int(days),
            "start_date": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d"),
            "current_usage_GB": 0,
            "last_reset_time": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
            "is_active": True,
            "comment": _build_panel_user_comment(int(user_id), is_test=True),
        }

        created_nodes: list[dict] = []
        targets = _get_target_servers_for_sale(server)
        if not targets:
            targets = [server]

        try:
            created, created_nodes = await _create_service_users_on_targets(targets, payload)
        except Exception as e:
            logger.exception("Failed creating free-trial user(s) for telegram_id=%s", user_id)
            if created_nodes:
                await _deactivate_created_users(created_nodes)
            await update.message.reply_text(
                i18n.t("trial_panel_failed", _user_lang(user_id), error=e),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            return

        panel_user_uuid = str(created.get("uuid") or created.get("id") or "").strip()
        panel_user_id = created.get("id")
        server_title = server.get("title") or f"سرور #{sid}"
        usage_limit = float(created.get("usage_limit_GB") or gb)
        usage_current = float(created.get("current_usage_GB") or 0)

        days_left = int(days)
        try:
            start_raw = created.get("start_date")
            package_days = int(created.get("package_days") or days)
            if start_raw:
                start_dt = None
                for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        start_dt = datetime.strptime(start_raw, fmt)
                        break
                    except ValueError:
                        continue
                if start_dt:
                    end_dt = start_dt + timedelta(days=package_days)
                    days_left = (end_dt.date() - datetime.now(timezone.utc).replace(tzinfo=None).date()).days
        except Exception:
            days_left = int(days)

        service_code = _generate_service_code()
        service_db_id = None
        try:
            with userbot_db._get_conn() as conn:
                cur = conn.cursor()
                comment_parts = []
                if panel_user_uuid:
                    comment_parts.append(f"uuid:{panel_user_uuid}")
                comment_parts.append("price:0")
                comment_parts.append(f"code:{service_code}")
                comment_parts.append("test")
                service_comment = "|".join(comment_parts)
                cur.execute(
                    """
                    INSERT INTO userbot_services
                    (user_id, name, server_id, server_title, usage_current, usage_limit, days_left, last_online, comment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        internal_user_id,
                        service_name,
                        sid,
                        server_title,
                        usage_current,
                        usage_limit,
                        days_left,
                        created.get("last_online"),
                        service_comment,
                    ),
                )
                service_db_id = cur.lastrowid
            userbot_db.set_free_trial_used(internal_user_id, 1)
            try:
                userbot_db.try_grant_referral_trial_reward(internal_user_id)
            except Exception as e:
                logger.warning(
                    "Failed to process referral trial reward (invitee user=%s): %s",
                    internal_user_id,
                    e,
                )
        except Exception as e:
            logger.exception("Failed persisting free trial for telegram_id=%s", user_id)
            await update.message.reply_text(
                i18n.t("trial_db_error", _user_lang(user_id), error=e),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_trial_{user_id}", None)
            return

        if service_db_id and created_nodes:
            for node_item in created_nodes:
                try:
                    node_sid = int(node_item.get("server_id") or 0)
                    node_uuid = str(node_item.get("panel_user_uuid") or "").strip()
                    if node_sid <= 0 or not node_uuid:
                        continue
                    userbot_db.add_service_node(
                        service_id=int(service_db_id),
                        server_id=node_sid,
                        panel_user_uuid=node_uuid,
                        server_title=str(node_item.get("server_title") or ""),
                        panel_user_id=(
                            str(node_item.get("panel_user_id"))
                            if node_item.get("panel_user_id") is not None
                            else None
                        ),
                        is_active=1,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to create trial service-node mapping (service_id=%s, server_id=%s): %s",
                        service_db_id,
                        node_item.get("server_id"),
                        e,
                    )

        set_user_step(context, user_id, None)
        context.user_data.pop(f"pending_trial_{user_id}", None)

        announce_enabled = bool(trial_settings.get("announce_enabled", True))
        if announce_enabled:
            await update.message.reply_text(
                i18n.t("trial_created_ok", _user_lang(user_id)),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
        else:
            await update.message.reply_text(i18n.t("main_menu_btn", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))

        delivered_service = {
            "id": service_db_id or panel_user_id or "—",
            "name": service_name,
            "server_title": server_title,
            "usage_current": usage_current,
            "usage_limit": usage_limit,
            "days_left": days_left,
            "comment": f"price:0|code:{service_code}",
            "user_id": internal_user_id,
        }
        settings = _get_subscription_settings()
        await update.message.reply_text(
            _build_subscription_status_text(delivered_service, lang=_user_lang(user_id)),
            parse_mode="Markdown",
            reply_markup=subscription_status_keyboard(service_db_id, show_direct_config=settings.get('show_direct_config', True), show_sub_link=settings.get('show_sub_link', True), show_configs=_should_show_configs_button(settings), show_detach=_is_connected_service(delivered_service), lang=_user_lang(user_id)),
        )

        # گزارش به ادمین (ربات ادمین): ایجاد اشتراک تستی
        if ADMIN_ID and ADMIN_BOT_TOKEN:
            try:
                admin_bot = Bot(token=ADMIN_BOT_TOKEN)
                user_btn_title = (update.effective_user.full_name or update.effective_user.username or str(user_id)).strip()
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"👤 {user_btn_title}", callback_data=f"userbot:user:{internal_user_id}")]
                ])

                await admin_bot.send_message(
                    chat_id=ADMIN_ID,
                    text=_build_subscription_created_caption(
                        service_name=service_name,
                        server_title=server_title,
                        gb=gb,
                        days=days,
                        service_code=service_code,
                        amount=None,
                        is_trial=True,
                    ),
                    reply_markup=kb,
                )
            except Exception as e:
                logger.warning(f"Failed to notify admin for free-trial creation (user={user_id}): {e}")

        await _send_event_channel_subscription_report(
            context,
            action_title="ایجاد تست رایگان",
            telegram_id=int(user_id),
            display_name=(update.effective_user.full_name or update.effective_user.username or str(user_id)).strip(),
            service_name=service_name,
            server_title=server_title,
            gb=float(gb),
            days=int(days),
            service_code=service_code,
            amount=None,
        )
        return


async def _rs_service_name(update, context, user_id, text, step):
    if step == "WAIT_SERVICE_NAME":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_{user_id}", None)
            context.user_data.pop(f"renew_target_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        service_name = (text or "").strip()
        if not service_name:
            await update.message.reply_text(i18n.t("service_name_required", _user_lang(user_id)), reply_markup=cancel_keyboard(lang=_user_lang(user_id)))
            return

        pending_wallet = context.user_data.get(f"pending_wallet_{user_id}", None) or {}
        ok = await _process_wallet_purchase(
            context=context,
            user_id=user_id,
            tg_user=update.effective_user,
            chat_id=user_id,
            pending_wallet=pending_wallet,
            service_name=service_name,
        )
        set_user_step(context, user_id, None)
        context.user_data.pop(f"pending_wallet_{user_id}", None)
        context.user_data.pop(f"renew_target_{user_id}", None)
        if not ok:
            await update.message.reply_text(i18n.t("buy_renew_failed", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
        return


async def _rs_wallet_topup_amount(update, context, user_id, text, step):
    if step == "WAIT_WALLET_TOPUP_AMOUNT":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        pay_settings = _get_payment_settings()
        if not bool(pay_settings.get("enable_card_to_card", True)):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
            await update.message.reply_text(i18n.t("card_pay_disabled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        raw = (text or "").replace(",", "").strip()
        if not raw.isdigit():
            await update.message.reply_text(i18n.t("topup_amount_prompt", _user_lang(user_id)), reply_markup=cancel_keyboard(lang=_user_lang(user_id)))
            return

        amount_toman = int(raw)
        if amount_toman <= 0:
            await update.message.reply_text(i18n.t("topup_amount_prompt", _user_lang(user_id)), reply_markup=cancel_keyboard(lang=_user_lang(user_id)))
            return
        txp = _get_tx_plans_settings()
        min_tx = int(txp.get("min_transaction_toman") or 1)
        if amount_toman < min_tx:
            await update.message.reply_text(
                i18n.t("min_tx_alert", _user_lang(user_id), amount=f"{min_tx:,}"),
                reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
            )
            return

        try:
            card_info = database.get_next_card()
        except Exception:
            card_info = None
        if not card_info:
            try:
                card_info = database.get_random_card()
            except Exception:
                card_info = None
        card_number = (card_info or {}).get('number') or "-"
        card_owner = (card_info or {}).get('owner') or "-"
        card_bank = (card_info or {}).get('bank') or ""

        pay_amount_toman, tx_marker = _apply_random_tx_marker(amount_toman, txp)

        context.user_data[f"pending_wallet_topup_{user_id}"] = {
            "amount": pay_amount_toman,
            "card_number": card_number,
            "card_owner": card_owner,
            "card_bank": card_bank,
            "base_amount": amount_toman,
            "tx_marker": tx_marker,
        }

        msg = _build_card_to_card_payment_text(
            amount_toman=pay_amount_toman,
            card_number=card_number,
            card_owner=card_owner,
            card_bank=card_bank,
            text_settings=_get_text_settings(), lang=_user_lang(user_id),
        )
        if tx_marker > 0:
            msg = i18n.t("tx_marker_applied", _user_lang(user_id), marker=f"{tx_marker:,}", msg=msg)
        set_user_step(context, user_id, "WAIT_WALLET_TOPUP_CONFIRM")
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=confirm_payment_keyboard(_user_lang(user_id), lang=_user_lang(user_id)))
        return


async def _rs_wallet_topup_confirm(update, context, user_id, text, step):
    if step == "WAIT_WALLET_TOPUP_CONFIRM":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        if text == "✅ پرداخت کردم، ارسال رسید" or i18n.resolve_button(text, ("btn_pay_done",)) == "btn_pay_done":
            set_user_step(context, user_id, "WAIT_WALLET_TOPUP_IMAGE")
            await update.message.reply_text(i18n.t("pay_receipt_prompt", _user_lang(user_id)), reply_markup=receipt_cancel_keyboard(lang=_user_lang(user_id)))
            return


async def _rs_wallet_topup_image(update, context, user_id, text, step):
    if step == "WAIT_WALLET_TOPUP_IMAGE":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        if update.message.photo:
            photo_file_id = update.message.photo[-1].file_id
            pending = context.user_data.get(f"pending_wallet_topup_{user_id}", {})
            amount = int(pending.get("amount") or 0)
            pay_settings = _get_payment_settings()
            require_last4 = bool(pay_settings.get("require_last4_for_card_receipt", False))
            if require_last4:
                pending["receipt_photo_file_id"] = photo_file_id
                context.user_data[f"pending_wallet_topup_{user_id}"] = pending
                set_user_step(context, user_id, "WAIT_WALLET_TOPUP_LAST4")
                await update.message.reply_text(
                    i18n.t("last4_prompt", _user_lang(user_id)),
                    reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
                )
                return

            pending = context.user_data.pop(f"pending_wallet_topup_{user_id}", {})
            amount = int(pending.get("amount") or 0)
            auto_approved = False
            payment_result = "pending"
            if amount > 0:
                auto_approved, payment_result = await _finalize_pending_card_payment(
                    update=update,
                    context=context,
                    user_id=user_id,
                    amount=int(amount),
                    photo_file_id=photo_file_id,
                    flow="wallet_topup",
                    payer_last4="",
                    extra_meta={
                        "pay_flow": "wallet_topup",
                        "base_amount": int(pending.get("base_amount") or amount),
                        "tx_marker": int(pending.get("tx_marker") or 0),
                    },
                )

            await update.message.reply_text(
                _card_payment_result_user_text(amount, payment_result, user_id=user_id),
                reply_markup=_main_menu_keyboard(user_id=user_id),
            )
            set_user_step(context, user_id, None)
        else:
            await update.message.reply_text(i18n.t("receipt_photo_only", _user_lang(user_id)))
        return


async def _rs_wallet_topup_last4(update, context, user_id, text, step):
    if step == "WAIT_WALLET_TOPUP_LAST4":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_wallet_topup_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        payer_last4 = _parse_exact_card_last4(text)
        if not payer_last4:
            await update.message.reply_text(
                i18n.t("last4_invalid_short", _user_lang(user_id)),
                reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
            )
            return

        pending = context.user_data.pop(f"pending_wallet_topup_{user_id}", {})
        amount = int(pending.get("amount") or 0)
        photo_file_id = str(pending.get("receipt_photo_file_id") or "").strip()
        if amount <= 0 or not photo_file_id:
            set_user_step(context, user_id, None)
            await update.message.reply_text(i18n.t("payment_info_incomplete", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        auto_approved, payment_result = await _finalize_pending_card_payment(
            update=update,
            context=context,
            user_id=user_id,
            amount=int(amount),
            photo_file_id=photo_file_id,
            flow="wallet_topup",
            payer_last4=payer_last4,
            extra_meta={
                "pay_flow": "wallet_topup",
                "base_amount": int(pending.get("base_amount") or amount),
                "tx_marker": int(pending.get("tx_marker") or 0),
            },
        )
        await update.message.reply_text(
            _card_payment_result_user_text(amount, payment_result, user_id=user_id),
            reply_markup=_main_menu_keyboard(user_id=user_id),
        )
        set_user_step(context, user_id, None)
        return


async def _rs_coupon_code(update, context, user_id, text, step):
    if step == "WAIT_COUPON_CODE":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        mkt = _get_marketing_settings()
        if not (
            bool(mkt.get("enable_discount_code", False))
            or bool(mkt.get("enable_increase_code", False))
        ):
            set_user_step(context, user_id, None)
            await update.message.reply_text(i18n.t("gift_disabled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        code = (text or "").strip()
        if not code:
            await update.message.reply_text(i18n.t("coupon_prompt", _user_lang(user_id)), reply_markup=cancel_keyboard(lang=_user_lang(user_id)))
            return
        set_user_step(context, user_id, None)
        u_db = userbot_db.get_user_by_telegram_id(user_id)
        internal_user_id = int((u_db or {}).get("id") or 0)
        if internal_user_id <= 0:
            await update.message.reply_text(i18n.t("user_not_found", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        try:
            ok, result_text, amount = userbot_db.redeem_zarin_voucher(code, internal_user_id)
        except Exception as e:
            logger.warning(f"Failed to redeem coupon in WAIT_COUPON_CODE user={user_id}: {e}")
            await update.message.reply_text(i18n.t("coupon_check_error", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        if not ok:
            await update.message.reply_text(f"⚠️ {result_text}", reply_markup=_main_menu_keyboard(user_id=user_id))
            return
        fresh = userbot_db.get_user_by_id(internal_user_id) or {}
        balance = int(fresh.get("wallet_balance") or 0)
        await update.message.reply_text(
            i18n.t("coupon_applied", _user_lang(user_id), amount=f"{int(amount):,}", balance=f"{balance:,}"),
            reply_markup=_main_menu_keyboard(user_id=user_id),
        )
        return


async def _rs_receipt_confirm(update, context, user_id, text, step):
    if step == "WAIT_RECEIPT_CONFIRM":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_pay_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return
            
        if text == "✅ پرداخت کردم، ارسال رسید" or i18n.resolve_button(text, ("btn_pay_done",)) == "btn_pay_done":
            set_user_step(context, user_id, "WAIT_RECEIPT_IMAGE")
            await update.message.reply_text(i18n.t("pay_receipt_prompt", _user_lang(user_id)), reply_markup=receipt_cancel_keyboard(lang=_user_lang(user_id)))
            return


async def _rs_receipt_image(update, context, user_id, text, step):
    if step == "WAIT_RECEIPT_IMAGE":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_pay_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        if update.message.photo:
            photo_file_id = update.message.photo[-1].file_id
            pending = context.user_data.get(f"pending_pay_{user_id}", {})
            amount = int(pending.get("amount") or 0)
            pay_settings = _get_payment_settings()
            require_last4 = bool(pay_settings.get("require_last4_for_card_receipt", False))
            if require_last4:
                pending["receipt_photo_file_id"] = photo_file_id
                context.user_data[f"pending_pay_{user_id}"] = pending
                set_user_step(context, user_id, "WAIT_RECEIPT_LAST4")
                await update.message.reply_text(
                    i18n.t("last4_prompt2", _user_lang(user_id)),
                    reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
                )
                return

            pending = context.user_data.pop(f"pending_pay_{user_id}", {})
            amount = int(pending.get("amount") or 0)
            auto_approved = False
            payment_result = "pending"
            is_direct_buy = bool(pending.get("direct_buy"))
            if amount > 0:
                flow_kind = "direct_buy_payment" if is_direct_buy else "buy_payment"
                extra_meta = {
                    "pay_flow": "direct_buy" if is_direct_buy else "buy",
                    "base_amount": int(pending.get("base_amount") or amount),
                    "tx_marker": int(pending.get("tx_marker") or 0),
                }
                if is_direct_buy:
                    extra_meta.update({
                        "sid": int(pending.get("sid") or 0),
                        "gb": float(pending.get("gb") or 0),
                        "days": int(pending.get("days") or 0),
                        "renew_service_id": int(pending.get("renew_service_id") or 0),
                        "service_name": str(pending.get("direct_service_name") or "").strip(),
                    })
                auto_approved, payment_result = await _finalize_pending_card_payment(
                    update=update,
                    context=context,
                    user_id=user_id,
                    amount=int(amount),
                    photo_file_id=photo_file_id,
                    flow=flow_kind,
                    payer_last4="",
                    extra_meta=extra_meta,
                )
            
            await update.message.reply_text(
                _card_payment_result_user_text(amount, payment_result, direct_note=True, user_id=user_id),
                reply_markup=_main_menu_keyboard(user_id=user_id)
            )
            await _deliver_direct_buy_after_sms_notice(context, auto_approved and is_direct_buy)
            set_user_step(context, user_id, None)
        else:
             await update.message.reply_text(i18n.t("receipt_photo_only", _user_lang(user_id)))
        return


async def _rs_receipt_last4(update, context, user_id, text, step):
    if step == "WAIT_RECEIPT_LAST4":
        if _is_back_or_cancel_text(text):
            set_user_step(context, user_id, None)
            context.user_data.pop(f"pending_pay_{user_id}", None)
            await update.message.reply_text(i18n.t("op_cancelled", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        payer_last4 = _parse_exact_card_last4(text)
        if not payer_last4:
            await update.message.reply_text(
                i18n.t("last4_invalid_short", _user_lang(user_id)),
                reply_markup=cancel_keyboard(lang=_user_lang(user_id)),
            )
            return

        pending = context.user_data.pop(f"pending_pay_{user_id}", {})
        amount = int(pending.get("amount") or 0)
        photo_file_id = str(pending.get("receipt_photo_file_id") or "").strip()
        if amount <= 0 or not photo_file_id:
            set_user_step(context, user_id, None)
            await update.message.reply_text(i18n.t("payment_info_incomplete", _user_lang(user_id)), reply_markup=_main_menu_keyboard(user_id=user_id))
            return

        is_direct_buy = bool(pending.get("direct_buy"))
        flow_kind = "direct_buy_payment" if is_direct_buy else "buy_payment"
        extra_meta = {
            "pay_flow": "direct_buy" if is_direct_buy else "buy",
            "base_amount": int(pending.get("base_amount") or amount),
            "tx_marker": int(pending.get("tx_marker") or 0),
        }
        if is_direct_buy:
            extra_meta.update({
                "sid": int(pending.get("sid") or 0),
                "gb": float(pending.get("gb") or 0),
                "days": int(pending.get("days") or 0),
                "renew_service_id": int(pending.get("renew_service_id") or 0),
                "service_name": str(pending.get("direct_service_name") or "").strip(),
            })
        auto_approved, payment_result = await _finalize_pending_card_payment(
            update=update,
            context=context,
            user_id=user_id,
            amount=int(amount),
            photo_file_id=photo_file_id,
            flow=flow_kind,
            payer_last4=payer_last4,
            extra_meta=extra_meta,
        )
        await update.message.reply_text(
            _card_payment_result_user_text(amount, payment_result, direct_note=True, user_id=user_id),
            reply_markup=_main_menu_keyboard(user_id=user_id),
        )
        await _deliver_direct_buy_after_sms_notice(context, auto_approved and is_direct_buy)
        set_user_step(context, user_id, None)
        return
