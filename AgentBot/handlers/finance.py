from __future__ import annotations

from typing import Optional

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from AgentBot.handlers.base import get_agent_id
from AgentBot.utils.helpers import _fmt_toman
from Shared.agent_financial_report import get_agent_financial_report
from Shared.tg_button_styles import inline_button as IButton


_LABELS = {0: "امروز", 7: "7 روز اخیر", 30: "30 روز اخیر", 90: "3 ماه اخیر", None: "کل دوره"}


def _kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IButton("📅 امروز", callback_data="agbot:finance:0"), IButton("7 روز", callback_data="agbot:finance:7")],
        [IButton("30 روز", callback_data="agbot:finance:30"), IButton("3 ماه", callback_data="agbot:finance:90")],
        [IButton("📊 کل دوره", callback_data="agbot:finance:all")],
        [IButton("🔙 بازگشت", callback_data="agbot:menu")],
    ])


def _text(agent_id: int, days: Optional[int]) -> str:
    s = get_agent_financial_report(agent_id, days)
    label = _LABELS.get(days, "گزارش")
    profit = int(s["known_profit"])
    return (
        f"📊 <b>گزارش مالی نمایندگی — {label}</b>\n"
        "❖ • ────────────────────── • ❖\n\n"
        f"💰 موجودی کیف پول: <b>{_fmt_toman(s['wallet_balance'])}</b> تومان\n\n"
        "🤖 <b>فروش از ربات مشتری</b>\n"
        f"• خرید جدید: <b>{s['customer_buy_count']}</b> مورد\n"
        f"• تمدید: <b>{s['customer_renew_count']}</b> مورد\n"
        f"• مبلغ فروش: <b>{_fmt_toman(s['customer_sales'])}</b> تومان\n"
        f"• هزینه عمده: <b>{_fmt_toman(s['customer_cost'])}</b> تومان\n\n"
        "👤 <b>فروش مستقیم نماینده</b>\n"
        f"• سرویس جدید: <b>{s['direct_count']}</b> مورد\n"
        f"• مبلغ فروش ثبت‌شده: <b>{_fmt_toman(s['direct_sales'])}</b> تومان\n"
        f"• هزینه عمده: <b>{_fmt_toman(s['direct_cost'])}</b> تومان\n\n"
        "💵 <b>جمع فروش و سود قابل محاسبه</b>\n"
        f"• فروش: <b>{_fmt_toman(s['sales_total'])}</b> تومان\n"
        f"• هزینه عمده: <b>{_fmt_toman(s['known_cost'])}</b> تومان\n"
        f"• سود فروش: <b>{_fmt_toman(profit)}</b> تومان\n\n"
        "💳 <b>گردش کیف پول</b>\n"
        f"• شارژ کیف پول: <b>{_fmt_toman(s['wallet_charges'])}</b> تومان\n"
        f"• کسر بابت خرید/تمدید: <b>{_fmt_toman(s['wallet_purchases'])}</b> تومان\n"
        f"• برگشت وجه: <b>{_fmt_toman(s['wallet_refunds'])}</b> تومان\n\n"
        "ℹ️ شارژ کیف پول درآمد فروش محسوب نمی‌شود. "
        "برای تمدیدهای مستقیم قدیمی، مبلغ فروش تاریخی جداگانه ذخیره نشده؛ "
        "بنابراین سود نمایش‌داده‌شده فقط از فروش‌هایی است که قیمت فروش و عمده آنها قابل اثبات است."
    )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, days: Optional[int] = 0) -> None:
    agent_id = int(get_agent_id(context) or 0)
    if not agent_id:
        return
    text = _text(agent_id, days)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=_kb(), parse_mode="HTML")
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=_kb(), parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(text, reply_markup=_kb(), parse_mode="HTML")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = str((update.callback_query.data if update.callback_query else "") or "")
    raw = data.split(":")[-1]
    days = None if raw == "all" else int(raw) if raw.isdigit() else 0
    await show_menu(update, context, days)
