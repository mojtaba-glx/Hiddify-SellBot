import logging
from datetime import datetime
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from Shared import agent_db, database as shared_db
from AgentBot.constants import (
    SUBS_CREATE, SUBS_SEARCH, SUBS_EXPIRED, SUBS_DETAIL,
    SUBS_CFG, SUBS_RENEW, SUBS_DISABLE, SUBS_ENABLE, SUBS_DELETE, SUBS_DODELETE,
    SUBS_BACK, MENU_MAIN, UD_STATE, UD_SELECTED_SERVER, UD_SELECTED_PLAN,
    UD_SELECTED_CUSTOMER, UD_SELECTED_SERVICE, UD_PAGE,
    STATE_ADD_CUSTOMER_TG, STATE_ADD_CUSTOMER_NAME, STATE_CREATE_SERVICE_NAME,
    STATE_SEARCH_CUSTOMER, STATE_RENEW_DAYS, STATE_RENEW_GB,
)
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import (
    subs_menu_keyboard, service_detail_keyboard, back_keyboard, cancel_keyboard,
    pagination_keyboard,
)
from AgentBot.utils.helpers import _escape, _fmt_toman, _fmt_gb, _normalize_digits
from AgentBot.services.subscription_service import (
    create_subscription, renew_subscription,
    disable_subscription, enable_subscription, delete_subscription, get_configs,
    change_subscription_link,
)
from AgentBot.database import create_order as db_create_order

logger = logging.getLogger(__name__)

_PAGE_SIZE = 8


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "\U0001f4ca <b>\u0645\u062f\u06cc\u0631\u06cc\u062a \u0627\u0634\u062a\u0631\u0627\u06a9\u200c\u0647\u0627</b>"
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=subs_menu_keyboard(), parse_mode="HTML")
        except Exception:
            await update.message.reply_text(text, reply_markup=subs_menu_keyboard(), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=subs_menu_keyboard(), parse_mode="HTML")


async def _send_expired_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1) -> None:
    agent_id = get_agent_id(context)
    if page < 1:
        page = 1
    page_expired, total_expired = agent_db.get_expired_services_by_agent(agent_id, page=page, page_size=_PAGE_SIZE)
    total_pages = max(1, (total_expired + _PAGE_SIZE - 1) // _PAGE_SIZE)
    if page > total_pages:
        page = total_pages

    lines = [f"\U0001f51c <b>\u06a9\u0627\u0631\u0628\u0631\u0627\u0646 \u0645\u0646\u0642\u0636\u06cc \u0634\u062f\u0647</b> (\u0635\u0641\u062d\u0647 {page}/{total_pages})\n"]
    if not page_expired:
        lines.append("\u0647\u06cc\u0686 \u06a9\u0627\u0631\u0628\u0631 \u0645\u0646\u0642\u0636\u06cc \u0634\u062f\u0647\u200c\u0627\u06cc \u0646\u06cc\u0633\u062a.")
    else:
        _no_name = '\u0628\u06cc\u200c\u0646\u0627\u0645'
        for s in page_expired:
            lines.append(
                f"\u274c <b>{_escape(s.get('name', _no_name))}</b>\n"
                f"   \U0001f4c5 \u0627\u0646\u0642\u0636\u0627: {_escape(s.get('end_date', '—'))}\n"
                f"   \U0001f4e1 \u0634\u0646\u0627\u0633\u0647: <code>{s['id']}</code>"
            )
    query = update.callback_query
    kb = pagination_keyboard("agbot:subs:expired", page, total_pages, "agbot:subs:back")
    try:
        await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


def _format_customer_info(c: dict) -> str:
    name = _escape(c.get("full_name") or "") or _escape(c.get("username") or "") or f"\u06a9\u0627\u0631\u0628\u0631 #{c['id']}"
    tg_id = c.get("telegram_id", "")
    return f"\U0001f464 {name} (<code>{tg_id}</code>)"


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    parts = data.split(":")
    action = parts[2] if len(parts) > 2 else ""

    agent_id = get_agent_id(context)

    if action == "back":
        context.user_data.pop(UD_STATE, None)
        await show_menu(update, context)
        return

    if action == "detail":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        svc = agent_db.get_service_by_id(svc_id)
        if not svc:
            await query.answer("سرویس پیدا نشد.", show_alert=True)
            return
        is_active = bool(int(svc.get("is_active", 0) or 0))
        text = (
            f"📦 <b>{_escape(svc.get('name') or 'سرویس')}</b>\n\n"
            f"🌍 سرور: {_escape(svc.get('server_title') or '')}\n"
            f"📊 حجم: {_fmt_gb(svc.get('usage_limit', 0))}GB\n"
            f"⏰ روز: {svc.get('days_left') or svc.get('days') or 0}\n"
            f"🆔 <code>{svc.get('panel_user_uuid') or ''}</code>"
        )
        await query.edit_message_text(text, reply_markup=service_detail_keyboard(svc_id, is_active), parse_mode="HTML")
        return

    if action == "create":
        servers = shared_db.get_servers() or []
        if not servers:
            await query.answer("\u0647\u06cc\u0686 \u0633\u0631\u0648\u0631\u06cc \u062b\u0628\u062a \u0646\u0634\u062f\u0647.", show_alert=True)
            return
        from AgentBot.keyboards import _ikb
        from Shared.tg_button_styles import inline_button as IButton
        rows = [[IButton(_escape(s.get("title") or f"\u0633\u0631\u0648\u0631 #{s['id']}"),
                         callback_data=f"agbot:subs:picksrv:{s['id']}")] for s in servers]
        rows.append([IButton("\U0001f519 \u0628\u0627\u0632\u06af\u0634\u062a", callback_data="agbot:subs:back")])
        try:
            await query.edit_message_text(
                "\U0001f5a5 <b>\u0627\u0646\u062a\u062e\u0627\u0628 \u0633\u0631\u0648\u0631</b>\n\n\u0644\u0637\u0641\u0627 \u0633\u0631\u0648\u0631 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f:",
                reply_markup=_ikb(rows), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "picksrv":
        server_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        context.user_data[UD_SELECTED_SERVER] = server_id
        from AgentBot.database import get_fixed_plans
        plans = get_fixed_plans(agent_id)
        if not plans:
            await query.answer("\u0627\u0628\u062a\u062f\u0627 \u0627\u0632 \u0628\u062e\u0634 \u067e\u0644\u0646\u200c\u0647\u0627 \u067e\u0644\u0646 \u062b\u0627\u0628\u062a \u0627\u06cc\u062c\u0627\u062f \u06a9\u0646\u06cc\u062f.", show_alert=True)
            return
        from AgentBot.keyboards import _ikb
        from Shared.tg_button_styles import inline_button as IButton
        rows = []
        for p in plans:
            label = f"{p['title']} - {p['days']} \u0631\u0648\u0632 / {_fmt_gb(p['gb'])}GB - {_fmt_toman(p['price'])} \u062a\u0648\u0645\u0627\u0646"
            rows.append([IButton(label, callback_data=f"agbot:subs:pickplan:{p['id']}")])
        rows.append([IButton("\U0001f519 \u0628\u0627\u0632\u06af\u0634\u062a", callback_data="agbot:subs:create")])
        try:
            await query.edit_message_text(
                "\U0001f4cb <b>\u0627\u0646\u062a\u062e\u0627\u0628 \u067e\u0644\u0646</b>\n\n\u067e\u0644\u0646 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f:",
                reply_markup=_ikb(rows), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "pickplan":
        plan_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        from AgentBot.database import get_fixed_plan
        plan = get_fixed_plan(agent_id, plan_id)
        if not plan:
            await query.answer("\u067e\u0644\u0646 \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f.", show_alert=True)
            return
        server_id = context.user_data.get(UD_SELECTED_SERVER, 0) or 0
        plan["wholesale_price"] = agent_db.calculate_wholesale_price(agent_id, plan.get("gb", 0), plan.get("days", 30), server_id)
        plan["sale_price"] = plan.get("price", 0)
        context.user_data[UD_SELECTED_PLAN] = plan
        context.user_data[UD_STATE] = STATE_ADD_CUSTOMER_TG
        try:
            await query.edit_message_text(
                "\u0645\u0631\u062d\u0644\u0647 1: \u0622\u06cc\u062f\u06cc \u062a\u0644\u06af\u0631\u0627\u0645 \u06a9\u0627\u0631\u0628\u0631 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f.\n\n"
                "\u06cc\u0627 \u0627\u0632 \u062f\u06a9\u0645\u0647 \u0632\u06cc\u0631 \u0628\u0631\u0627\u06cc \u062c\u0633\u062a\u062c\u0648\u06cc \u06a9\u0627\u0631\u0628\u0631 \u0627\u0633\u062a\u0641\u0627\u062f\u0647 \u06a9\u0646\u06cc\u062f.",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "search":
        context.user_data[UD_STATE] = STATE_SEARCH_CUSTOMER
        try:
            await query.edit_message_text(
                "\U0001f50d \u0646\u0627\u0645 \u06a9\u0627\u0631\u0628\u0631 \u06cc\u0627 \u0622\u06cc\u062f\u06cc \u062a\u0644\u06af\u0631\u0627\u0645 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "expired":
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        await _send_expired_list(update, context, page)
        return

    if action == "cfg":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        configs = await get_configs(agent_id, svc_id)
        if not configs:
            await query.answer("\u06a9\u0627\u0646\u0641\u06cc\u06af\u06cc \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f.", show_alert=True)
            return
        text = "\U0001f4e1 <b>\u0644\u06cc\u0633\u062a \u06a9\u0627\u0646\u0641\u06cc\u06af\u200c\u0647\u0627</b>\n\n" + "\n".join(configs)
        if len(text) > 4000:
            text = text[:4000] + "\n\n... (\u0627\u062f\u0627\u0645\u0647 \u062d\u0630\u0641 \u0634\u062f)"
        try:
            await query.edit_message_text(text, parse_mode="HTML")
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML")
        return

    if action == "renew":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        context.user_data[UD_SELECTED_SERVICE] = svc_id
        context.user_data[UD_STATE] = STATE_RENEW_DAYS
        try:
            await query.edit_message_text(
                "\u062a\u0639\u062f\u0627\u062f \u0631\u0648\u0632\u0647\u0627\u06cc \u062a\u0645\u062f\u06cc\u062f \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "disable":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        ok = await disable_subscription(agent_id, svc_id)
        await query.answer("\u063a\u06cc\u0631\u0641\u0639\u0627\u0644 \u0634\u062f \u2705" if ok else "\u062e\u0637\u0627!", show_alert=not ok)
        if ok:
            svc = agent_db.get_service_by_id(svc_id)
            if svc:
                try:
                    await query.edit_message_reply_markup(reply_markup=service_detail_keyboard(svc_id, False))
                except Exception:
                    pass
        return

    if action == "enable":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        ok = await enable_subscription(agent_id, svc_id)
        await query.answer("\u0641\u0639\u0627\u0644 \u0634\u062f \u2705" if ok else "\u062e\u0637\u0627!", show_alert=not ok)
        if ok:
            svc = agent_db.get_service_by_id(svc_id)
            if svc:
                try:
                    await query.edit_message_reply_markup(reply_markup=service_detail_keyboard(svc_id, True))
                except Exception:
                    pass
        return

    if action == "delete":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        from AgentBot.keyboards import _ikb
        from Shared.tg_button_styles import inline_button as IButton
        kb = _ikb([
            [IButton("\U0001f5d1 \u0628\u0644\u0647\u060c \u062d\u0630\u0641 \u06a9\u0646", callback_data=f"agbot:subs:dodelete:{svc_id}")],
            [IButton("\u274c \u0644\u063a\u0648", callback_data=f"agbot:subs:detail:{svc_id}")],
        ])
        try:
            await query.edit_message_text(
                f"\u26a0\ufe0f \u0627\u0632 \u062d\u0630\u0641 \u0633\u0631\u0648\u06cc\u0633 \u0627\u0637\u0645\u06cc\u0646\u0627\u0646 \u062f\u0627\u0631\u06cc\u062f\u061f\n\n"
                f"\u0627\u06cc\u0646 \u0639\u0645\u0644 \u0642\u0627\u0628\u0644 \u0628\u0627\u0632\u06af\u0634\u062a \u0646\u06cc\u0633\u062a.",
                reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "dodelete":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        ok = await delete_subscription(agent_id, svc_id)
        await query.answer("\u062d\u0630\u0641 \u0634\u062f \u2705" if ok else "\u062e\u0637\u0627!", show_alert=not ok)
        if ok:
            await show_menu(update, context)
        return

    if action == "newlink":
        svc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        svc = agent_db.get_service_by_id(svc_id)
        if not svc:
            await query.answer("\u0633\u0631\u0648\u06cc\u0633 \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f.", show_alert=True)
            return
        await query.edit_message_text(
            "\u23f3 \u062f\u0631 \u062d\u0627\u0644 \u062a\u063a\u06cc\u06cc\u0631 \u0644\u06cc\u0646\u06a9... \u0644\u0637\u0641\u0627 \u0635\u0628\u0631 \u06a9\u0646\u06cc\u062f.",
            parse_mode="HTML",
        )
        result = await change_subscription_link(agent_id, svc_id)
        if result:
            is_active = bool(int(result.get("is_active", 0) or 0))
            text = (
                f"\u2705 <b>\u0644\u06cc\u0646\u06a9 \u0628\u0627 \u0645\u0648\u0641\u0642\u06cc\u062a \u062a\u063a\u06cc\u06cc\u0631 \u06a9\u0631\u062f!</b>\n\n"
                f"\U0001f4e1 \u0633\u0631\u0648\u06cc\u0633: {_escape(result.get('name') or '')}\n"
                f"\U0001f4e1 \u06cc\u0648\u06cc\u06cc\u062f\u06cc \u062c\u062f\u06cc\u062f: <code>{_escape(result.get('panel_user_uuid') or '')}</code>\n\n"
                "\U0001f447 \u0627\u0632 \u062f\u06a9\u0645\u0647 \u0632\u06cc\u0631 \u06a9\u0627\u0646\u0641\u06cc\u06af \u0647\u0627\u06cc \u062c\u062f\u06cc\u062f \u0631\u0627 \u062f\u0631\u06cc\u0627\u0641\u062a \u06a9\u0646\u06cc\u062f."
            )
            try:
                await query.edit_message_text(text, reply_markup=service_detail_keyboard(svc_id, is_active), parse_mode="HTML")
            except Exception:
                pass
        else:
            await query.edit_message_text(
                "\u274c \u062e\u0637\u0627 \u062f\u0631 \u062a\u063a\u06cc\u06cc\u0631 \u0644\u06cc\u0646\u06a9. \u0644\u0637\u0641\u0627 \u062f\u0648\u0628\u0627\u0631\u0647 \u062a\u0644\u0627\u0634 \u06a9\u0646\u06cc\u062f.",
                reply_markup=service_detail_keyboard(svc_id, bool(int(svc.get("is_active", 0) or 0))),
                parse_mode="HTML",
            )
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    text = update.message.text.strip()
    state = context.user_data.get(UD_STATE)

    if state == STATE_SEARCH_CUSTOMER:
        customers = agent_db.search_customers(agent_id, text, limit=10)
        if not customers:
            await update.message.reply_text("\u0647\u06cc\u0686 \u06a9\u0627\u0631\u0628\u0631\u06cc \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f.")
        else:
            from AgentBot.keyboards import _ikb
            from Shared.tg_button_styles import inline_button as IButton
            rows = []
            for c in customers:
                label = _escape(c.get("full_name") or "") or _escape(c.get("username") or "") or f"\u06a9\u0627\u0631\u0628\u0631 #{c['id']}"
                rows.append([IButton(label, callback_data=f"agbot:subs:detail:{c['id']}")])
            rows.append([IButton("\U0001f519 \u0628\u0627\u0632\u06af\u0634\u062a", callback_data="agbot:subs:back")])
            await update.message.reply_text(
                f"\U0001f50d \u0646\u062a\u0627\u06cc\u062c \u062c\u0633\u062a\u062c\u0648 \u0628\u0631\u0627\u06cc \"{_escape(text)}\":",
                reply_markup=_ikb(rows), parse_mode="HTML",
            )
        context.user_data.pop(UD_STATE, None)
        return True

    if state == STATE_ADD_CUSTOMER_TG:
        raw = _normalize_digits(text)
        try:
            tg_id = int(raw)
        except ValueError:
            await update.message.reply_text("\u0622\u06cc\u062f\u06cc \u0646\u0627\u0645\u0639\u062a\u0628\u0631 \u0627\u0633\u062a.")
            return True
        existing = agent_db.get_customer_by_telegram_id(agent_id, tg_id)
        if existing:
            context.user_data[UD_SELECTED_CUSTOMER] = existing
            context.user_data[UD_STATE] = STATE_CREATE_SERVICE_NAME
            await update.message.reply_text(
                f"\u06a9\u0627\u0631\u0628\u0631 \u0642\u0628\u0644\u0627 \u062b\u0628\u062a \u0634\u062f\u0647: {_escape(existing.get('full_name', ''))}\n"
                "\u062d\u0627\u0644\u0627 \u0646\u0627\u0645 \u0633\u0631\u0648\u06cc\u0633 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
            return True
        context.user_data["new_customer_tg"] = tg_id
        context.user_data[UD_STATE] = STATE_ADD_CUSTOMER_NAME
        await update.message.reply_text(
            "\u0646\u0627\u0645 \u06a9\u0627\u0631\u0628\u0631 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f (\u06cc\u0627 \u0628\u0631\u0627\u06cc \u0635\u0631\u0641\u200c\u0646\u0638\u0631\u06cc \u2014 \u0628\u0641\u0631\u0633\u062a\u06cc\u062f):",
            reply_markup=cancel_keyboard(), parse_mode="HTML",
        )
        return True

    if state == STATE_ADD_CUSTOMER_NAME:
        tg_id = context.user_data.get("new_customer_tg")
        if not tg_id:
            return False
        name = text if text != "\u2014" else ""
        customer = agent_db.upsert_customer(agent_id, tg_id, full_name=name)
        context.user_data[UD_SELECTED_CUSTOMER] = customer
        context.user_data.pop("new_customer_tg", None)
        context.user_data[UD_STATE] = STATE_CREATE_SERVICE_NAME
        _name_display = _escape(name) or '\u0628\u062f\u0648\u0646 \u0646\u0627\u0645'
        await update.message.reply_text(
            f"\u06a9\u0627\u0631\u0628\u0631 \u062b\u0628\u062a \u0634\u062f: {_name_display}\n"
            "\u062d\u0627\u0644\u0627 \u0646\u0627\u0645 \u0633\u0631\u0648\u06cc\u0633 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
            reply_markup=cancel_keyboard(), parse_mode="HTML",
        )
        return True

    if state == STATE_CREATE_SERVICE_NAME:
        plan = context.user_data.get(UD_SELECTED_PLAN)
        server_id = context.user_data.get(UD_SELECTED_SERVER)
        customer_data = context.user_data.get(UD_SELECTED_CUSTOMER)
        if not plan or not server_id or not customer_data:
            await update.message.reply_text("\u062e\u0637\u0627: \u0627\u0637\u0644\u0627\u0639\u0627\u062a \u06af\u0645 \u0634\u062f. \u062f\u0648\u0628\u0627\u0631\u0647 \u0634\u0631\u0648\u0639 \u06a9\u0646\u06cc\u062f.")
            context.user_data.pop(UD_STATE, None)
            return True
        customer_id = customer_data["id"] if isinstance(customer_data, dict) else customer_data
        customer_name = customer_data.get("full_name", "") if isinstance(customer_data, dict) else ""
        svc = await create_subscription(agent_id, customer_id, server_id, plan, text)
        if not svc:
            await update.message.reply_text(
                "\u062e\u0637\u0627 \u062f\u0631 \u0633\u0627\u062e\u062a \u0633\u0631\u0648\u06cc\u0633. \u0645\u0648\u062c\u0648\u062f\u06cc \u06a9\u0627\u0641\u06cc \u0646\u06cc\u0633\u062a \u06cc\u0627 \u062e\u0637\u0627\u06cc \u0633\u06cc\u0633\u062a\u0645.",
                reply_markup=cancel_keyboard(),
            )
            return True
        plan_title = f"{plan['days']} \u0631\u0648\u0632 / {_fmt_gb(plan['gb'])}GB"
        db_create_order(agent_id, customer_id, customer_name, plan.get("wholesale_price", 0), "new", plan.get("id", 0), text)
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_SELECTED_PLAN, None)
        context.user_data.pop(UD_SELECTED_SERVER, None)
        context.user_data.pop(UD_SELECTED_CUSTOMER, None)
        await update.message.reply_text(
            f"\u2705 <b>\u0633\u0631\u0648\u06cc\u0633 \u0628\u0627 \u0645\u0648\u0641\u0642\u06cc\u062a \u0633\u0627\u062e\u062a\u0647 \u0634\u062f!</b>\n\n"
            f"\U0001f4e1 \u0646\u0627\u0645: {_escape(text)}\n"
            f"\U0001f4cb \u067e\u0644\u0646: {plan_title}\n"
            f"\U0001f4b0 \u0642\u06cc\u0645\u062a \u0639\u0645\u062f\u0647: {_fmt_toman(plan['wholesale_price'])} \u062a\u0648\u0645\u0627\u0646\n"
            f"\U0001f4b8 \u0642\u06cc\u0645\u062a \u0641\u0631\u0648\u0634: {_fmt_toman(plan['sale_price'])} \u062a\u0648\u0645\u0627\u0646\n"
            f"\U0001f464 \u06a9\u0627\u0631\u0628\u0631: {_escape(customer_name)}",
            reply_markup=subs_menu_keyboard(), parse_mode="HTML",
        )
        return True

    if state == STATE_RENEW_DAYS:
        days = int(_normalize_digits(text))
        svc_id = context.user_data.get(UD_SELECTED_SERVICE)
        if days <= 0:
            await update.message.reply_text("\u062a\u0639\u062f\u0627\u062f \u0631\u0648\u0632 \u0628\u0627\u06cc\u062f \u0645\u062b\u0628\u062a \u0628\u0627\u0634\u062f.")
            return True
        svc = await renew_subscription(agent_id, svc_id, days)
        if svc:
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_SELECTED_SERVICE, None)
            await update.message.reply_text(
                f"\u2705 \u0633\u0631\u0648\u06cc\u0633 \u0628\u0631\u0627\u06cc {days} \u0631\u0648\u0632 \u062a\u0645\u062f\u06cc\u062f \u0634\u062f.",
                reply_markup=subs_menu_keyboard(),
            )
        else:
            await update.message.reply_text("\u062e\u0637\u0627 \u062f\u0631 \u062a\u0645\u062f\u06cc\u062f. \u0645\u0648\u062c\u0648\u062f\u06cc \u06a9\u0627\u0641\u06cc \u0646\u06cc\u0633\u062a.")
        return True

    return False
