from telegram import Update
from telegram.ext import ContextTypes

from CustomerBot.constants import UD_STATE, STATE_START
from CustomerBot.database import (
    upsert_user,
    get_user,
    get_text_settings,
    get_marketing_settings,
    get_tx_plans_settings,
    get_force_join_settings,
)
from Shared.agent_db import upsert_customer
from Shared import i18n
from CustomerBot.keyboards import main_menu_keyboard, force_join_keyboard, language_keyboard
from CustomerBot.utils.helpers import is_rate_limited, parse_deep_link


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    agent_id = context.bot_data.get("agent_id", 0)
    if not agent_id:
        await update.message.reply_text("❌ ربات به درستی پیکربندی نشده است.")
        return

    if is_rate_limited(f"start_{user.id}"):
        return

    upsert_user(agent_id, user.id, user.username or "", user.full_name or "")
    upsert_customer(agent_id, user.id, user.username or "", user.full_name or "")

    fjs = get_force_join_settings(agent_id)
    if fjs.get("enabled") and fjs.get("channel_username"):
        ch = str(fjs["channel_username"])
        chat_target = ch if ch.lstrip("-").isdigit() else f"@{ch}"
        link = fjs.get("channel_link") or (f"https://t.me/{ch}" if not ch.lstrip("-").isdigit() else "")
        guide = fjs.get("guide_text", "🔒 لطفاً ابتدا عضو شوید.")
        try:
            member = await context.bot.get_chat_member(chat_target, user.id)
            if member.status in ("left", "kicked"):
                await update.message.reply_text(guide, reply_markup=force_join_keyboard(link))
                return
        except Exception:
            # خطا در چک عضویت — اجازه ورود بده (ربات ادمین نیست یا کانال نامعتبر)
            pass

    text_settings = get_text_settings(agent_id)
    lang = i18n.get_customer_lang(agent_id, user.id)
    if str(text_settings.get("welcome_message", "") or "").strip():
        welcome = text_settings.get("welcome_message", "").format(
            full_name=user.full_name or "",
            username=f"@{user.username}" if user.username else "",
            id=user.id,
        )
    else:
        welcome = i18n.t("welcome", lang, full_name=user.full_name or "")

    await update.message.reply_text(
        welcome,
        reply_markup=main_menu_keyboard(lang=lang),
    )

    start_payload = _extract_start_payload(update)
    if start_payload:
        parsed = parse_deep_link(start_payload)
        if parsed:
            kind, data = parsed
            if kind == "ticket_shot":
                from CustomerBot.handlers.receipt import handle_ticket_shot_start
                await handle_ticket_shot_start(update, context, start_payload)
            elif kind == "voucher":
                from CustomerBot.database import get_zarin_voucher, redeem_zarin_voucher
                ok, amount = redeem_zarin_voucher(agent_id, data, user.id)
                if ok:
                    await update.message.reply_text(
                        f"🎉 موجودی کیف پول شما افزایش یافت.\nمبلغ: {int(amount):,} تومان"
                    )
                else:
                    await update.message.reply_text(f"⚠️ {amount}")


def _extract_start_payload(update: Update) -> str:
    if not update.message or not update.message.text:
        return ""
    parts = update.message.text.split()
    return parts[1] if len(parts) > 1 else ""
