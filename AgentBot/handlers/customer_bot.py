import asyncio
import logging
import os
import shlex
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
from AgentBot.keyboards import cbot_menu_keyboard, back_keyboard, cancel_keyboard, agent_lang
from AgentBot.utils.helpers import _escape

logger = logging.getLogger(__name__)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent_id = get_agent_id(context)
    bots = agent_db.get_customer_bots(agent_id)
    active = any(int(b.get("is_active", 0)) for b in bots) if bots else False
    from Shared import i18n as _i18n
    _lg = agent_lang(context)
    text = _i18n.t("ag_cbot_title", _lg) + "\n\n"
    if bots:
        seen = set()
        for b in bots:
            key = b.get("bot_token") or b.get("bot_username") or b.get("id")
            if key in seen:
                continue
            seen.add(key)
            status = "\u2705" if int(b.get("is_active", 0)) else "\u274c"
            text += f"{status} @{_escape(b.get('bot_username', '—'))}\n"
    else:
        text += "\u0647\u06cc\u0686 \u0631\u0628\u0627\u062a\u06cc \u062b\u0628\u062a \u0646\u0634\u062f\u0647."
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=cbot_menu_keyboard(active, lang=agent_lang(context)), parse_mode="HTML")
        except Exception:
            await update.message.reply_text(text, reply_markup=cbot_menu_keyboard(active, lang=agent_lang(context)), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=cbot_menu_keyboard(active, lang=agent_lang(context)), parse_mode="HTML")


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
            await query.edit_message_reply_markup(reply_markup=cbot_menu_keyboard(still_active, lang=agent_lang(context)))
        except Exception:
            pass
        # اعمال تغییر وضعیت: ریستارت پروسه ربات مشتری تا لیست ربات‌های فعال دوباره خوانده شود
        if bots:
            _ok = await _restart_customer_bot_async()
            if not _ok:
                try:
                    from Shared import i18n as _i18n
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=_i18n.t("ag_cbot_auto_start_failed", agent_lang(context)),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        return

    if action == "token":
        context.user_data[UD_STATE] = STATE_CBOT_TOKEN
        from Shared import i18n as _i18n
        _lg = agent_lang(context)
        _prompt = _i18n.t("ag_cbot_token_prompt", _lg)
        _chat_id = update.effective_chat.id if update.effective_chat else query.message.chat_id
        try:
            await context.bot.send_message(
                chat_id=_chat_id,
                text=_prompt, reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
        except Exception:
            try:
                await query.message.reply_text(
                    _prompt, reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
            except Exception:
                pass
        return

    if action == "restart":
        await query.answer()
        from Shared import i18n as _i18n
        _lg = agent_lang(context)
        try:
            await query.edit_message_text(
                _i18n.t("ag_cbot_restarting", _lg), parse_mode="HTML",
            )
        except Exception:
            pass
        success = await _restart_customer_bot_async()
        text = _i18n.t("ag_cbot_restart_ok", _lg) if success else _i18n.t("ag_cbot_restart_fail", _lg)
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
        parse_mode="HTML",
    )
    # استارت خودکار ربات مشتری بعد از ثبت توکن (بدون نیاز به ریستارت کل ربات)
    from Shared import i18n as _i18n
    _lg = agent_lang(context)
    _starting = _i18n.t("ag_cbot_auto_starting", _lg, username=_escape(username))
    try:
        await update.message.reply_text(_starting, parse_mode="HTML")
    except Exception:
        pass
    _ok = await _restart_customer_bot_async()
    if _ok:
        _done = _i18n.t("ag_cbot_auto_started", _lg, username=_escape(username))
    else:
        _done = _i18n.t("ag_cbot_auto_start_failed", _lg)
    try:
        await update.message.reply_text(_done, parse_mode="HTML")
    except Exception:
        pass
    await update.message.reply_text(
        "\U0001f4ca <b>\u067e\u0627\u0646\u0644 \u0646\u0645\u0627\u06cc\u0646\u062f\u06af\u06cc</b>\n"
        "\u0627\u0632 \u0645\u0646\u0648\u06cc \u0632\u06cc\u0631 \u06af\u0632\u06cc\u0646\u0647 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f.",
        reply_markup=main_menu_keyboard(), parse_mode="HTML",
    )
    return True


SYSTEMD_CBOT_UNIT = "hiddify-sellbot-customer.service"
_SYSTEMD_ACTIVE_MARK = "/run/systemd/system"


def _systemd_available() -> bool:
    try:
        has_systemctl = subprocess.run(
            ["sh", "-lc", "command -v systemctl >/dev/null 2>&1 && echo yes"],
            capture_output=True, text=True, timeout=5,
        )
        return has_systemctl.returncode == 0 and "yes" in has_systemctl.stdout and os.path.isdir(_SYSTEMD_ACTIVE_MARK)
    except Exception:
        return False


def _systemd_unit_active(unit: str) -> bool:
    try:
        res = subprocess.run(["systemctl", "is-active", "--quiet", unit], capture_output=True, timeout=10)
        return res.returncode == 0
    except Exception:
        return False


def _systemd_unit_installed(unit: str) -> bool:
    try:
        res = subprocess.run(["systemctl", "cat", unit], capture_output=True, timeout=10)
        return res.returncode == 0
    except Exception:
        return False


def _systemd_restart_unit(unit: str) -> bool:
    try:
        res = subprocess.run(["systemctl", "restart", unit], capture_output=True, timeout=30)
        return res.returncode == 0
    except Exception as e:
        logger.warning("systemctl restart failed: %s", e)
        return False


def _sudo_systemd_restart_unit(unit: str) -> bool:
    """در صورت نبود مجوز restart مستقیم، با sudo بدون رمز تلاش می‌کند."""
    try:
        res = subprocess.run(["sudo", "-n", "systemctl", "restart", unit], capture_output=True, timeout=30)
        return res.returncode == 0
    except Exception as e:
        logger.warning("sudo systemctl restart failed: %s", e)
        return False


def _pick_cbot_log_file(root_dir: Path) -> Path:
    """اولین فایل لاگ قابل‌نوشتن را انتخاب می‌کند (فایل root-owned باعث شکست spawn می‌شد)."""
    candidates = [
        root_dir / "logs" / "customerbot.log",
        root_dir / "logs" / "customerbot-agent.log",
        Path("/tmp") / "hiddify-customerbot.log",
    ]
    for p in candidates:
        try:
            if p.exists():
                if os.access(p, os.W_OK):
                    return p
                continue
            if p.parent.exists() and os.access(p.parent, os.W_OK):
                return p
        except Exception:
            continue
    return Path("/tmp") / "hiddify-customerbot.log"


def _pgrep_cbot_pids() -> list:
    try:
        res = subprocess.run(
            ["pgrep", "-f", "CustomerBot/main.py"],
            capture_output=True, text=True, timeout=5,
        )
        return [p.strip() for p in res.stdout.strip().splitlines() if p.strip().isdigit()]
    except Exception:
        return []


def _wait_cbot_gone(timeout: int = 20) -> bool:
    for _ in range(timeout):
        if not _pgrep_cbot_pids():
            return True
        time.sleep(1)
    return not _pgrep_cbot_pids()


def _kill_cbot_processes() -> None:
    for pid in _pgrep_cbot_pids():
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ProcessLookupError, ValueError, PermissionError):
            pass
    if not _wait_cbot_gone(20):
        for pid in _pgrep_cbot_pids():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ProcessLookupError, ValueError, PermissionError):
                pass
        _wait_cbot_gone(5)


def _restart_customer_bot() -> bool:
    try:
        # 1) اگر unit systemd نصب است اول از همان مسیر تلاش کن (حتی اگر inactive باشد)
        if _systemd_available() and _systemd_unit_installed(SYSTEMD_CBOT_UNIT):
            if _systemd_restart_unit(SYSTEMD_CBOT_UNIT):
                logger.info("CustomerBot restarted via systemctl (%s)", SYSTEMD_CBOT_UNIT)
                return True
            if _sudo_systemd_restart_unit(SYSTEMD_CBOT_UNIT):
                logger.info("CustomerBot restarted via sudo systemctl (%s)", SYSTEMD_CBOT_UNIT)
                return True
            # اگر unit در حال اجراست و مجوز ریستارت نداریم، نباید دستی duplicate بسازیم
            if _systemd_unit_active(SYSTEMD_CBOT_UNIT):
                logger.error("CustomerBot systemd unit active but restart denied (no permission)")
                return False
            logger.warning("CustomerBot systemd unit installed but inactive and restart denied; falling back to manual start")

        root_dir = Path(__file__).resolve().parents[2]
        venv_python = root_dir / "venv" / "bin" / "python"
        customer_main = root_dir / "CustomerBot" / "main.py"
        log_file = _pick_cbot_log_file(root_dir)

        _kill_cbot_processes()
        if _pgrep_cbot_pids():
            logger.error("CustomerBot processes still alive after kill; aborting restart to avoid duplicate instances")
            return False

        cmd = (
            f"cd {shlex.quote(str(root_dir))} && "
            f"{shlex.quote(str(venv_python))} {shlex.quote(str(customer_main))} "
            f">> {shlex.quote(str(log_file))} 2>&1 &"
        )
        subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(3)
        if not _pgrep_cbot_pids():
            logger.warning("No CustomerBot process found shortly after start; check log file %s", log_file)
            return False
        logger.info("CustomerBot restarted successfully (log: %s)", log_file)
        return True
    except Exception as e:
        logger.error("Failed to restart CustomerBot (via %s when active): %s", SYSTEMD_CBOT_UNIT, e)
        return False


async def _restart_customer_bot_async() -> bool:
    """اجرای ریستارت بلاک‌کننده در thread تا event loop ربات قفل نشود."""
    return await asyncio.to_thread(_restart_customer_bot)
