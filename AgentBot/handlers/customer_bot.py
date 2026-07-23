import logging
import os
import signal
import subprocess
import time
from pathlib import Path

from telegram import Update, InlineKeyboardMarkup
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from telegram.ext import ContextTypes

from Shared import agent_db
from AgentBot.constants import CBOT_ACTIVATE, CBOT_TOKEN, CBOT_BACK, MENU_MAIN, UD_STATE, STATE_CBOT_TOKEN
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import cbot_menu_keyboard, back_keyboard, cancel_keyboard
from AgentBot.utils.helpers import _escape

logger = logging.getLogger(__name__)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent_id = get_agent_id(context)
    bots = agent_db.get_customer_bots(agent_id)
    active = any(int(b.get("is_active", 0)) for b in bots) if bots else False
    text = "\U0001f916 <b>\u0631\u0628\u0627\u062a \u0645\u0634\u062a\u0631\u06cc</b>\n\n"
    if bots:
        for b in bots:
            status = "\u2705" if int(b.get("is_active", 0)) else "\u274c"
            text += f"{status} @{_escape(b.get('bot_username', '—'))}\n"
    else:
        text += "\u0647\u06cc\u0686 \u0631\u0628\u0627\u062a\u06cc \u062b\u0628\u062a \u0646\u0634\u062f\u0647."
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=cbot_menu_keyboard(active), parse_mode="HTML")
        except Exception:
            await update.message.reply_text(text, reply_markup=cbot_menu_keyboard(active), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=cbot_menu_keyboard(active), parse_mode="HTML")


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
        from AgentBot.keyboards import main_menu_keyboard
        try:
            await query.edit_message_text(
                "📊 <b>پانل نمایندگی</b>\nاز منوی زیر گزینه مورد نظر را انتخاب کنید.",
                reply_markup=None, parse_mode="HTML",
            )
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="به منوی نمایندگی بازگشتید.",
            reply_markup=main_menu_keyboard(),
        )
        return

    if action == "activate":
        bots = agent_db.get_customer_bots(agent_id)
        active = any(int(b.get("is_active", 0)) for b in bots)
        new_active = not active
        for b in bots:
            agent_db.set_customer_bot_active(b["id"], new_active)
        await query.answer("\u0641\u0639\u0627\u0644 \u0634\u062f \u2705" if new_active else "\u063a\u06cc\u0631\u0641\u0639\u0627\u0644 \u0634\u062f \u274c")
        active_bots = agent_db.get_customer_bots(agent_id)
        still_active = any(int(b.get("is_active", 0)) for b in active_bots)
        try:
            await query.edit_message_reply_markup(reply_markup=cbot_menu_keyboard(still_active))
        except Exception:
            pass
        return

    if action == "token":
        context.user_data[UD_STATE] = STATE_CBOT_TOKEN
        try:
            await query.edit_message_text(
                "\U0001f511 <b>\u062b\u0628\u062a \u062a\u0648\u06a9\u0646 \u0631\u0628\u0627\u062a</b>\n\n"
                "\u062a\u0648\u06a9\u0646 \u0631\u0628\u0627\u062a \u0631\u0627 \u0627\u0632 @BotFather \u062f\u0631\u06cc\u0627\u0641\u062a \u06a9\u0631\u062f\u0647 \u0648 \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f.\n\n"
                "\u0641\u0631\u0645\u0627\u062a: <code>1234567890:ABCdef...</code>",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if action == "restart":
        await query.answer()
        await query.edit_message_text(
            "\U0001f504 <b>\u062f\u0631 \u062d\u0627\u0644 \u0631\u06cc\u0633\u062a\u0627\u0631\u062a \u0631\u0628\u0627\u062a \u0645\u0634\u062a\u0631\u06cc...</b>",
            parse_mode="HTML",
        )
        success = _restart_customer_bot()
        text = (
            "\u2705 <b>\u0631\u0628\u0627\u062a \u0645\u0634\u062a\u0631\u06cc \u0628\u0627 \u0645\u0648\u0641\u0642\u06cc\u062a \u0631\u06cc\u0633\u062a\u0627\u0631\u062a \u0634\u062f.</b>"
            if success
            else "\u274c <b>\u062e\u0637\u0627 \u062f\u0631 \u0631\u06cc\u0633\u062a\u0627\u0631\u062a \u0631\u0628\u0627\u062a \u0645\u0634\u062a\u0631\u06cc.</b>\n\u0644\u0637\u0641\u0627\u064b \u0627\u0632 \u062a\u0631\u0645\u06cc\u0646\u0627\u0644 \u062f\u0633\u062a\u06cc \u0631\u06cc\u0633\u062a\u0627\u0631\u062a \u06a9\u0646\u06cc\u062f."
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f519 \u0628\u0627\u0632\u06af\u0634\u062a", callback_data="agbot:cbot:back")],
            ]),
        )
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    state = context.user_data.get(UD_STATE)
    if state != STATE_CBOT_TOKEN:
        return False
    text = update.message.text.strip()
    if ":" not in text or len(text) < 30:
        await update.message.reply_text("\u0641\u0631\u0645\u0627\u062a \u062a\u0648\u06a9\u0646 \u0646\u0627\u0645\u0639\u062a\u0628\u0631 \u0627\u0633\u062a.")
        return True
    try:
        from telegram import Bot
        bot = Bot(token=text)
        me = await bot.get_me()
        username = me.username or ""
    except Exception:
        await update.message.reply_text("\u062a\u0648\u06a9\u0646 \u0646\u0627\u0645\u0639\u062a\u0628\u0631 \u0627\u0633\u062a. \u0644\u0637\u0641\u0627 \u062a\u0648\u06a9\u0646 \u0635\u062d\u06cc\u062d \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f.")
        return True
    agent_db.add_customer_bot(agent_id, text, username)
    context.user_data.pop(UD_STATE, None)
    from AgentBot.keyboards import main_menu_keyboard
    await update.message.reply_text(
        f"\u2705 \u0631\u0628\u0627\u062a @{_escape(username)} \u0628\u0627 \u0645\u0648\u0641\u0642\u06cc\u062a \u062b\u0628\u062a \u0634\u062f!",
        reply_markup=main_menu_keyboard(), parse_mode="HTML",
    )
    return True


def _restart_customer_bot() -> bool:
    try:
        root_dir = Path(__file__).resolve().parents[2]
        venv_python = root_dir / "venv" / "bin" / "python"
        customer_main = root_dir / "CustomerBot" / "main.py"
        log_file = root_dir / "logs" / "customerbot.log"

        try:
            result = subprocess.run(
                ["pgrep", "-f", "CustomerBot/main.py"],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                pids = result.stdout.strip().split("\n")
                for pid in pids:
                    try:
                        os.kill(int(pid.strip()), signal.SIGTERM)
                    except (ProcessLookupError, ValueError):
                        pass
                logger.info("Killed old CustomerBot processes: %s", pids)
                time.sleep(2)
        except Exception as e:
            logger.warning("Could not kill old CustomerBot: %s", e)

        cmd = f"cd {root_dir} && {venv_python} {customer_main} >> {log_file} 2>&1 &"
        subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("CustomerBot restarted successfully")
        return True
    except Exception as e:
        logger.error("Failed to restart CustomerBot: %s", e)
        return False
