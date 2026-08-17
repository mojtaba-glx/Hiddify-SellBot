import logging
from html import escape

from Shared import agent_db

logger = logging.getLogger(__name__)


def _fmt_amount(amount) -> str:
    try:
        return f"{int(amount or 0):,}"
    except Exception:
        return str(amount or 0)


def _report_volume(gb) -> str:
    try:
        return f"{float(gb or 0):.1f}"
    except Exception:
        return "0.0"


def build_subscription_report_text(action: str, svc: dict, amount: int) -> str:
    """متن گزارش ایجاد/تمدید اشتراک برای نماینده."""
    title = "\u06af\u0632\u0627\u0631\u0634 \u0627\u06cc\u062c\u0627\u062f \u0627\u0634\u062a\u0631\u0627\u06a9" if action == "create" else "\u06af\u0632\u0627\u0631\u0634 \u062a\u0645\u062f\u06cc\u062f \u0627\u0634\u062a\u0631\u0627\u06a9"
    name = escape(str(svc.get("name") or "\u0633\u0631\u0648\u06cc\u0633"))
    server = escape(str(svc.get("server_title") or "\u2014"))
    gb = _report_volume(svc.get("usage_limit"))
    days = int(svc.get("days_left") or svc.get("days") or 0)
    code = agent_db._service_code_from_comment(svc.get("comment") or "") or str(svc.get("id") or "")
    return (
        f"\U0001f4c4 {title}\n\n"
        f"\U0001f464\u0627\u0634\u062a\u0631\u0627\u06a9: {name}\n"
        f"\U0001f6f0\u0633\u0631\u0648\u0631: {server}\n"
        f"\U0001f4ca\u062d\u062c\u0645: {gb} \u06af\u06cc\u06af\u0627\u0628\u0627\u06cc\u062a\n"
        f"\u23f3\u0632\u0645\u0627\u0646: {days} \u0631\u0648\u0632\n"
        f"\U0001f4b0\u0645\u0628\u0644\u063a \u067e\u0631\u062f\u0627\u062e\u062a\u06cc: {_fmt_amount(amount)} \u062a\u0648\u0645\u0627\u0646\n"
        f"\U0001f511\u0634\u0646\u0627\u0633\u0647 \u0627\u0634\u062a\u0631\u0627\u06a9:{escape(code)}"
    )


async def send_subscription_report(bot, chat_id: int, agent_id: int, user_tg_id: int, svc: dict, action: str, amount: int) -> None:
    """نمایش پروفایل مشتری + گزارش ایجاد/تمدید اشتراک + دکمه «پروفایل کاربر» برای نماینده."""
    try:
        from telegram import InlineKeyboardMarkup
        from Shared.tg_button_styles import inline_button as IButton

        text = ""
        customer = None
        try:
            customer = agent_db.get_customer_by_telegram_id(agent_id, user_tg_id)
        except Exception as e:
            logger.warning("subscription report: customer lookup failed user=%s: %s", user_tg_id, e)
        if customer:
            try:
                from AgentBot.handlers.settings_users import _build_profile_text
                text += _build_profile_text(agent_id, customer) + "\n\n"
            except Exception as e:
                logger.warning("subscription report: profile text failed user=%s: %s", user_tg_id, e)
        text += build_subscription_report_text(action, svc, amount)

        kb = None
        if customer:
            customer_id = int(customer.get("id") or 0)
            kb = InlineKeyboardMarkup(
                [[IButton("\U0001f464 \u067e\u0631\u0648\u0641\u0627\u06cc\u0644 \u06a9\u0627\u0631\u0628\u0631", callback_data=f"agbot:set:users:detail:{customer_id}")]]
            )
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.warning("send_subscription_report failed agent=%s user=%s: %s", agent_id, user_tg_id, e)
