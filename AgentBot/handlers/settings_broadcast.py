import asyncio
import logging
from io import BytesIO
from typing import Any, Dict, List, Tuple

from telegram import Bot, ReplyKeyboardRemove, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from AgentBot.constants import STATE_BROADCAST_MESSAGE, UD_STATE
from AgentBot.handlers.base import clear_state, get_agent_id
from AgentBot.keyboards import (
    broadcast_menu_keyboard,
    broadcast_skip_cancel_keyboard,
    cancel_keyboard,
    main_menu_keyboard,
    agent_lang,
)
from CustomerBot.database import get_broadcast_stats, get_broadcast_target_telegram_ids
from Shared.agent_db import get_active_customer_bot
from Shared import i18n

logger = logging.getLogger(__name__)
CANCEL_WORDS = {"❌ لغو", "/cancel"}


def _is_cancel_word(text: str) -> bool:
    """دکمه لغو/انصراف در هر ۳ زبان + /cancel."""
    t = str(text or "").strip()
    if t in CANCEL_WORDS:
        return True
    try:
        return i18n.resolve_button(t, ("cancel_btn2", "btn_cancel", "btn_back", "back")) is not None
    except Exception:
        return False


async def _restore_main_menu(message, lang: str = "fa") -> None:
    _lg = lang
    await message.reply_text(
        i18n.t('📊 <b>پنل نمایندگی</b>\nاز منوی زیر گزینه مورد نظر را انتخاب کنید.', _lg),
        reply_markup=main_menu_keyboard(lang=_lg),
        parse_mode="HTML",
    )


def _build_broadcast_stats_text(stats: Dict[str, Any]) -> str:
    _lg = "fa"
    return (
        f"{i18n.t('◈ تعداد کاربران تلگرام: ', _lg)}{int(stats.get('total_users') or 0)}{i18n.t('\n◈ تعداد کاربران منقضی: ', _lg)}{int(stats.get('expired_users') or 0)}{i18n.t('\n◈ تعداد کاربران بدون سفارش: ', _lg)}{int(stats.get('no_order_users') or 0)}{i18n.t('\n◈ تعداد کاربران منقضی شده بیش از یک هفته: ', _lg)}{int(stats.get('expired_1w_users') or 0)}{i18n.t('\n◈ تعداد کاربران منقضی شده بیش از دو هفته: ', _lg)}{int(stats.get('expired_2w_users') or 0)}{i18n.t('\n◈ تعداد کاربران منقضی شده بیش از چهار هفته: ', _lg)}{int(stats.get('expired_4w_users') or 0)}{i18n.t('\n◈ تعداد کاربران منقضی شده بیش از هشت هفته: ', _lg)}{int(stats.get('expired_8w_users') or 0)}"
    )


def _broadcast_segment_label(segment: str) -> str:
    _lg = "fa"
    seg = str(segment or "").strip().lower()
    mapping = {
        "all": i18n.t('تمام کاربران', _lg),
        "expired_all": i18n.t('تمام کاربران منقضی شده', _lg),
        "no_order": i18n.t('کاربران بدون سفارش', _lg),
        "expired_1w": i18n.t('کاربران منقضی شده بیش از یک هفته', _lg),
        "expired_2w": i18n.t('کاربران منقضی شده بیش از دو هفته', _lg),
        "expired_4w": i18n.t('کاربران منقضی شده بیش از چهار هفته', _lg),
        "expired_8w": i18n.t('کاربران منقضی شده بیش از هشت هفته', _lg),
    }
    return mapping.get(seg, i18n.t('تمام کاربران', _lg))


def _is_skip_text(text: str) -> bool:
    raw = str(text or "").strip().replace(" ", "")
    return raw in {"⏩ردکردن", "ردکردن", "⏭️ردکردن", "▶️ردکردن"}


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent_id = get_agent_id(context)
    if not agent_id:
        return
    clear_state(context)
    stats = get_broadcast_stats(agent_id)
    text = _build_broadcast_stats_text(stats)
    if update.callback_query:
        query = update.callback_query
        try:
            await query.edit_message_text(text, reply_markup=broadcast_menu_keyboard())
            return
        except BadRequest:
            pass
        await query.message.reply_text(text, reply_markup=broadcast_menu_keyboard())
        return
    if update.message:
        await update.message.reply_text(text, reply_markup=broadcast_menu_keyboard())


async def _send_broadcast_to_targets(
    context: ContextTypes.DEFAULT_TYPE,
    token: str,
    telegram_ids: List[int],
    text: str,
    photo_file_id: str = "",
) -> Tuple[int, int]:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    sender_bot = Bot(token=token)
    body = str(text or "").strip()
    photo_id = str(photo_file_id or "").strip()
    sent_count = 0
    fail_count = 0
    photo_bytes = None

    if photo_id:
        try:
            tg_file = await context.bot.get_file(photo_id)
            bio = BytesIO()
            await tg_file.download_to_memory(out=bio)
            bio.seek(0)
            bio.name = "broadcast.jpg"
            photo_bytes = bio
        except Exception:
            photo_bytes = None

    for tg_id in telegram_ids:
        try:
            if photo_id and photo_bytes is not None:
                photo_bytes.seek(0)
                if len(body) <= 1024:
                    await sender_bot.send_photo(chat_id=tg_id, photo=photo_bytes, caption=body)
                else:
                    await sender_bot.send_photo(chat_id=tg_id, photo=photo_bytes)
                    await sender_bot.send_message(chat_id=tg_id, text=body)
            elif photo_id:
                fallback_text = body or i18n.t('📷 تصویر ضمیمه شده بود ولی ارسال تصویر ممکن نشد.', _lg)
                await sender_bot.send_message(chat_id=tg_id, text=fallback_text)
            else:
                await sender_bot.send_message(chat_id=tg_id, text=body)
            sent_count += 1
        except Exception:
            fail_count += 1
        await asyncio.sleep(0.03)

    return sent_count, fail_count


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    agent_id = get_agent_id(context)
    if not agent_id:
        return

    if data == "agbot:set:broadcast":
        await query.answer()
        await show_menu(update, context)
        return

    if data.startswith("agbot:broadcast:segment:"):
        segment = str(data.rsplit(":", 1)[-1] or "").strip().lower()
        allowed_segments = {
            "all",
            "expired_all",
            "no_order",
            "expired_1w",
            "expired_2w",
            "expired_4w",
            "expired_8w",
        }
        if segment not in allowed_segments:
            await query.answer(i18n.t('گروه ارسال نامعتبر است.', _lg), show_alert=True)
            return
        context.user_data["broadcast_state"] = {
            "segment": segment,
            "step": "wait_text",
            "text": "",
            "photo_file_id": "",
        }
        context.user_data[UD_STATE] = STATE_BROADCAST_MESSAGE
        await query.answer()
        await query.message.reply_text(
            f"{i18n.t('✍ لطفا پیام خود را برای ارسال به «', _lg)}{_broadcast_segment_label(segment)}{i18n.t('» وارد کنید:', _lg)}",
            reply_markup=cancel_keyboard(lang=_lg),
        )
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    if not update.message:
        return False
    if context.user_data.get(UD_STATE) != STATE_BROADCAST_MESSAGE:
        return False

    agent_id = get_agent_id(context)
    if not agent_id:
        return False

    payload = context.user_data.get("broadcast_state") or {}
    if not isinstance(payload, dict):
        clear_state(context)
        await update.message.reply_text(i18n.t('❌ وضعیت ارسال همگانی نامعتبر است.', _lg), reply_markup=ReplyKeyboardRemove())
        await _restore_main_menu(update.message, lang=agent_lang(context))
        return True

    segment = str(payload.get("segment") or "all").strip().lower()
    text = (update.message.text or update.message.caption or "").strip()
    photo_file_id = update.message.photo[-1].file_id if update.message.photo else ""
    step = str(payload.get("step") or "wait_text").strip().lower()

    if _is_cancel_word(text):
        clear_state(context)
        await update.message.reply_text(i18n.t('❌ عملیات ارسال همگانی لغو شد.', _lg), reply_markup=ReplyKeyboardRemove())
        await _restore_main_menu(update.message, lang=agent_lang(context))
        return True

    if step == "wait_text":
        if not text:
            await update.message.reply_text(i18n.t('❌ لطفاً متن پیام را کامل ارسال کنید.', _lg), reply_markup=cancel_keyboard(lang=_lg))
            return True
        payload["text"] = text
        payload["step"] = "wait_photo"
        context.user_data["broadcast_state"] = payload
        await update.message.reply_text(
            i18n.t('🖼️ لطفا عکس خود را برای ارسال به کاربران ارسال کنید یا روی دکمه [⏩رد کردن] کلیک کنید:', _lg),
            reply_markup=broadcast_skip_cancel_keyboard(lang=_lg),
        )
        return True

    if step != "wait_photo":
        payload["step"] = "wait_text"
        context.user_data["broadcast_state"] = payload
        await update.message.reply_text(
            f"{i18n.t('✍ لطفا پیام خود را برای ارسال به «', _lg)}{_broadcast_segment_label(segment)}{i18n.t('» وارد کنید:', _lg)}",
            reply_markup=cancel_keyboard(lang=_lg),
        )
        return True

    if photo_file_id:
        payload["photo_file_id"] = photo_file_id
    elif _is_skip_text(text):
        payload["photo_file_id"] = ""
    else:
        await update.message.reply_text(
            i18n.t('❌ لطفا عکس ارسال کنید یا روی دکمه [⏩رد کردن] بزنید.', _lg),
            reply_markup=broadcast_skip_cancel_keyboard(lang=_lg),
        )
        return True

    body_text = str(payload.get("text") or "").strip()
    customer_bot = get_active_customer_bot(agent_id)
    token = str((customer_bot or {}).get("bot_token") or "").strip()

    if not token:
        clear_state(context)
        await update.message.reply_text(
            i18n.t('ربات مشتری فعال برای این نماینده پیدا نشد. ارسال پیام همگانی فقط از طریق ربات مشتری همان نماینده انجام می‌شود.', _lg),
            reply_markup=ReplyKeyboardRemove(),
        )
        await _restore_main_menu(update.message, lang=agent_lang(context))
        return True

    telegram_ids = get_broadcast_target_telegram_ids(agent_id, segment)

    if not telegram_ids:
        clear_state(context)
        await update.message.reply_text(
            i18n.t('هیچ کاربری برای ارسال پیام همگانی پیدا نشد.', _lg),
            reply_markup=ReplyKeyboardRemove(),
        )
        await _restore_main_menu(update.message, lang=agent_lang(context))
        return True

    sent_count, fail_count = await _send_broadcast_to_targets(
        context,
        token,
        telegram_ids,
        body_text,
        str(payload.get("photo_file_id") or ""),
    )

    clear_state(context)
    await update.message.reply_text(
        f"{i18n.t('✅ پیام همگانی ارسال شد.\n\nگروه: ', _lg)}{_broadcast_segment_label(segment)}{i18n.t('\nموفق: ', _lg)}{sent_count}{i18n.t('\nناموفق: ', _lg)}{fail_count}",
        reply_markup=ReplyKeyboardRemove(),
    )
    await _restore_main_menu(update.message, lang=agent_lang(context))
    return True
