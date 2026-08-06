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
    UD_SELECTED_SERVICE, UD_PAGE,
    STATE_CREATE_SERVICE_NAME, STATE_RENEW_DAYS, STATE_RENEW_GB,
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
from AgentBot.database import create_order as db_create_order, get_setting as db_get_setting

logger = logging.getLogger(__name__)

_PAGE_SIZE = 8


def _calc_dynamic_price(agent_id: int, server_id: int, gb: int, months: int):
    settings = db_get_setting(agent_id, "dynamic_plan_settings", {})
    price_per_gb = settings.get("price_per_gb", 0)
    price_per_month = settings.get("price_per_month", 0)
    discount_pct = settings.get("discount_pct", 0)
    wholesale_price = agent_db.calculate_wholesale_price(agent_id, gb, months * 30, server_id)
    sale_price = (gb * price_per_gb) + (months * price_per_month)
    if discount_pct:
        sale_price = int(sale_price * (1 - discount_pct / 100))
    return wholesale_price, sale_price, discount_pct


def _get_wizard_defaults(agent_id: int):
    settings = db_get_setting(agent_id, "dynamic_plan_settings", {})
    min_gb = settings.get("min_gb", 1)
    return min_gb, 1


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
        gb, months = _get_wizard_defaults(agent_id)
        context.user_data["wiz_gb"] = gb
        context.user_data["wiz_months"] = months
        wholesale, sale, off_pct = _calc_dynamic_price(agent_id, server_id, gb, months)
        context.user_data["wiz_wholesale"] = wholesale
        context.user_data["wiz_sale"] = sale
        context.user_data["wiz_off"] = off_pct
        from AgentBot.keyboards import agent_dynamic_wizard_keyboard
        kb = agent_dynamic_wizard_keyboard(server_id, gb, months, sale, off_pct, wholesale)
        try:
            await query.edit_message_text(
                "\U0001f3af <b>\u0633\u06cc\u0633\u062a\u0645 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u062c\u0647\u062a \u062e\u0631\u06cc\u062f \u0637\u0631\u0627\u062d\u06cc \u06a9\u0646\u06cc\u062f:</b>",
                reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "picksrv_back":
        context.user_data.pop(UD_SELECTED_SERVER, None)
        context.user_data.pop("wiz_gb", None)
        context.user_data.pop("wiz_months", None)
        context.user_data.pop("wiz_wholesale", None)
        context.user_data.pop("wiz_sale", None)
        context.user_data.pop("wiz_off", None)
        query = update.callback_query
        query.data = "agbot:subs:create"
        await handle_callback(update, context)
        return

    if action == "wiz":
        sub = parts[3] if len(parts) > 3 else ""
        server_id = context.user_data.get(UD_SELECTED_SERVER, 0) or 0
        gb = context.user_data.get("wiz_gb", 1)
        months = context.user_data.get("wiz_months", 1)

        if sub == "confirm":
            wholesale = context.user_data.get("wiz_wholesale", 0)
            sale = context.user_data.get("wiz_sale", 0)
            plan = {
                "gb": gb,
                "days": months * 30,
                "wholesale_price": wholesale,
                "sale_price": sale,
                "price": sale,
            }
            context.user_data[UD_SELECTED_PLAN] = plan
            context.user_data[UD_STATE] = STATE_CREATE_SERVICE_NAME
            try:
                await query.edit_message_text(
                    "\U0001f4e1 <b>\u0646\u0627\u0645 \u0633\u0631\u0648\u06cc\u0633</b>\n\n\u062d\u0627\u0644\u0627 \u0646\u0627\u0645 \u0633\u0631\u0648\u06cc\u0633 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return

        settings = db_get_setting(agent_id, "dynamic_plan_settings", {})
        step_gb = settings.get("step_gb", 1)
        step_month = settings.get("step_month", 1)
        min_gb = settings.get("min_gb", 1)
        max_gb = settings.get("max_gb", 999)
        min_month = settings.get("min_month", 1)
        max_month = settings.get("max_month", 12)
        if sub == "gb_inc":
            gb = min(gb + step_gb, max_gb)
        elif sub == "gb_dec":
            gb = max(gb - step_gb, min_gb)
        elif sub == "month_inc":
            months = min(months + step_month, max_month)
        elif sub == "month_dec":
            months = max(months - step_month, min_month)
        context.user_data["wiz_gb"] = gb
        context.user_data["wiz_months"] = months
        wholesale, sale, off_pct = _calc_dynamic_price(agent_id, server_id, gb, months)
        context.user_data["wiz_wholesale"] = wholesale
        context.user_data["wiz_sale"] = sale
        context.user_data["wiz_off"] = off_pct
        from AgentBot.keyboards import agent_dynamic_wizard_keyboard
        kb = agent_dynamic_wizard_keyboard(server_id, gb, months, sale, off_pct, wholesale)
        try:
            await query.edit_message_text(
                "\U0001f3af <b>\u0633\u06cc\u0633\u062a\u0645 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u062c\u0647\u062a \u062e\u0631\u06cc\u062f \u0637\u0631\u0627\u062d\u06cc \u06a9\u0646\u06cc\u062f:</b>",
                reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            pass
        await query.answer()
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
        context.user_data[UD_STATE] = STATE_CREATE_SERVICE_NAME
        try:
            await query.edit_message_text(
                "\U0001f4e1 <b>\u0646\u0627\u0645 \u0633\u0631\u0648\u06cc\u0633</b>\n\n\u062d\u0627\u0644\u0627 \u0646\u0627\u0645 \u0633\u0631\u0648\u06cc\u0633 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                parse_mode="HTML",
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

    if state == STATE_CREATE_SERVICE_NAME:
        plan = context.user_data.get(UD_SELECTED_PLAN)
        server_id = context.user_data.get(UD_SELECTED_SERVER)
        if not plan or not server_id:
            await update.message.reply_text("\u062e\u0637\u0627: \u0627\u0637\u0644\u0627\u0639\u0627\u062a \u06af\u0645 \u0634\u062f. \u062f\u0648\u0628\u0627\u0631\u0647 \u0634\u0631\u0648\u0639 \u06a9\u0646\u06cc\u062f.")
            context.user_data.pop(UD_STATE, None)
            return True
        svc = await create_subscription(agent_id, 0, server_id, plan, text)
        if not svc:
            await update.message.reply_text(
                "\u062e\u0637\u0627 \u062f\u0631 \u0633\u0627\u062e\u062a \u0633\u0631\u0648\u06cc\u0633. \u0645\u0648\u062c\u0648\u062f\u06cc \u06a9\u0627\u0641\u06cc \u0646\u06cc\u0633\u062a \u06cc\u0627 \u062e\u0637\u0627\u06cc \u0633\u06cc\u0633\u062a\u0645.",
                reply_markup=cancel_keyboard(),
            )
            return True
        plan_title = f"{plan['days']} \u0631\u0648\u0632 / {_fmt_gb(plan['gb'])}GB"
        db_create_order(agent_id, 0, "", plan.get("wholesale_price", 0), "new", plan.get("id", 0), text)
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_SELECTED_PLAN, None)
        context.user_data.pop(UD_SELECTED_SERVER, None)
        await update.message.reply_text(
            f"\u2705 <b>\u0633\u0631\u0648\u06cc\u0633 \u0628\u0627 \u0645\u0648\u0641\u0642\u06cc\u062a \u0633\u0627\u062e\u062a\u0647 \u0634\u062f!</b>\n\n"
            f"\U0001f4e1 \u0646\u0627\u0645: {_escape(text)}\n"
            f"\U0001f4cb \u067e\u0644\u0646: {plan_title}\n"
            f"\U0001f4b0 \u0642\u06cc\u0645\u062a \u0639\u0645\u062f\u0647: {_fmt_toman(plan['wholesale_price'])} \u062a\u0648\u0645\u0627\u0646\n"
            f"\U0001f4b8 \u0642\u06cc\u0645\u062a \u0641\u0631\u0648\u0634: {_fmt_toman(plan['sale_price'])} \u062a\u0648\u0645\u0627\u0646",
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
