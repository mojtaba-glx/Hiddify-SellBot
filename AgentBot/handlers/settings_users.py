import logging

from telegram import Update
from telegram.ext import ContextTypes

from Shared import agent_db
from AgentBot.constants import UD_STATE
from AgentBot.handlers.base import get_agent_id
from AgentBot.utils.helpers import _escape
from AgentBot.keyboards import settings_sub_menu_keyboard, back_keyboard, cancel_keyboard

logger = logging.getLogger(__name__)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    parts = data.split(":")
    agent_id = get_agent_id(context)
    p1 = parts[1] if len(parts) > 1 else ""
    p2 = parts[2] if len(parts) > 2 else ""
    p3 = parts[3] if len(parts) > 3 else ""

    if p1 == "set" and p2 == "users" and not p3:
        await query.edit_message_text(
            "\U0001f465 <b>\u0645\u062f\u06cc\u0631\u06cc\u062a \u06a9\u0627\u0631\u0628\u0631\u0627\u0646 \u0631\u0628\u0627\u062a</b>",
            reply_markup=settings_sub_menu_keyboard("set:users"), parse_mode="HTML",
        )
        return

    if (p2 == "back" and p1 == "set") or (p2 == "users" and p3 == "back"):
        from AgentBot.keyboards import settings_menu_keyboard
        await query.edit_message_text(
            "\u2699\ufe0f <b>\u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u0631\u0628\u0627\u062a</b>",
            reply_markup=settings_menu_keyboard(), parse_mode="HTML",
        )
        return

    if p3 == "list":
        customers, total = agent_db.get_customers_list(agent_id, page=1, page_size=15)
        lines = [f"\U0001f4cb <b>\u0644\u06cc\u0633\u062a \u06a9\u0627\u0631\u0628\u0631\u0627\u0646 \u0631\u0628\u0627\u062a</b> ({total})\n"]
        if not customers:
            lines.append("\u0647\u06cc\u0686 \u06a9\u0627\u0631\u0628\u0631\u06cc \u0648\u062c\u0648\u062f \u0646\u062f\u0627\u0631\u062f.")
        else:
            for c in customers:
                name = _escape(c.get("full_name", "") or c.get("username", "")) or f"\u06a9\u0627\u0631\u0628\u0631 #{c['id']}"
                lines.append(f"\U0001f464 {name} \u2022 <code>{c.get('telegram_id', '')}</code>")
        try:
            await query.edit_message_text("\n".join(lines), reply_markup=back_keyboard("agbot:set:users"), parse_mode="HTML")
        except Exception:
            pass
            return

    if p3 == "search":
        context.user_data[UD_STATE] = "st:search_user"
        try:
            await query.edit_message_text(
                "\U0001f50d \u0646\u0627\u0645 \u06cc\u0627 \u0622\u06cc\u062f\u06cc \u06a9\u0627\u0631\u0628\u0631 \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if p3 == "detail":
        customer_id = int(p4) if len(parts) > 4 and p4.isdigit() else 0
        if not customer_id:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        customer = agent_db.get_customer_by_id(agent_id, customer_id)
        if not customer:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        name = _escape(customer.get("full_name", "") or customer.get("username", "")) or f"کاربر #{customer_id}"
        tg_id = customer.get("telegram_id", "—")
        text = (
            f"👤 <b>{name}</b>\n\n"
            f"🆔 آیدی: <code>{tg_id}</code>\n"
        )
        await query.edit_message_text(
            text,
            reply_markup=back_keyboard("agbot:set:users"),
            parse_mode="HTML",
        )
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    state = context.user_data.get(UD_STATE)
    if state not in ("st:search_user",):
        return False
    text = update.message.text.strip()
    if text in {"بازگشت", "❌ لغو", "لغو", "/cancel"}:
        context.user_data.pop(UD_STATE, None)
        from AgentBot.keyboards import settings_menu_keyboard
        await update.message.reply_text("⚙️ <b>تنظیمات ربات</b>", reply_markup=settings_menu_keyboard(), parse_mode="HTML")
        return True
    customers = agent_db.search_customers(agent_id, text, limit=10)
    if not customers:
        await update.message.reply_text("\u0647\u06cc\u0686 \u06a9\u0627\u0631\u0628\u0631\u06cc \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f.")
        return True
    from AgentBot.keyboards import _ikb
    from Shared.tg_button_styles import inline_button as IButton
    from AgentBot.keyboards import main_menu_keyboard
    rows = []
    for c in customers:
        name = _escape(c.get("full_name", "") or c.get("username", "")) or f"\u06a9\u0627\u0631\u0628\u0631 #{c['id']}"
        rows.append([IButton(name, callback_data=f"agbot:set:users:detail:{c['id']}")])
    await update.message.reply_text(
        f"\U0001f50d \u0646\u062a\u0627\u06cc\u062c \u0628\u0631\u0627\u06cc \"{_escape(text)}\":",
        reply_markup=_ikb(rows), parse_mode="HTML",
    )
    context.user_data.pop(UD_STATE, None)
    return True
