import asyncio
import logging
from io import BytesIO
from typing import Any, Dict, List, Tuple

from telegram import Bot, ReplyKeyboardRemove, Update
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import ContextTypes

from AgentBot.constants import STATE_BROADCAST_MESSAGE, UD_STATE
from AgentBot.handlers.base import clear_state, get_agent_id
from AgentBot.keyboards import (
    broadcast_menu_keyboard,
    broadcast_skip_cancel_keyboard,
    broadcast_preview_keyboard,
    cancel_keyboard,
    main_menu_keyboard,
)
from CustomerBot.database import get_broadcast_stats, get_broadcast_target_telegram_ids
from Shared.agent_db import get_active_customer_bot
from Shared import secure_io

logger = logging.getLogger(__name__)
CANCEL_WORDS = {"❌ لغو", "/cancel"}


async def _restore_main_menu(message) -> None:
    await message.reply_text(
        "📊 <b>پنل نمایندگی</b>\nاز منوی زیر گزینه مورد نظر را انتخاب کنید.",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


def _build_broadcast_stats_text(stats: Dict[str, Any]) -> str:
    return (
        f"◈ تعداد کاربران تلگرام: {int(stats.get('total_users') or 0)}\n"
        f"◈ تعداد کاربران منقضی: {int(stats.get('expired_users') or 0)}\n"
        f"◈ تعداد کاربران بدون سفارش: {int(stats.get('no_order_users') or 0)}\n"
        f"◈ تعداد کاربران منقضی شده بیش از یک هفته: {int(stats.get('expired_1w_users') or 0)}\n"
        f"◈ تعداد کاربران منقضی شده بیش از دو هفته: {int(stats.get('expired_2w_users') or 0)}\n"
        f"◈ تعداد کاربران منقضی شده بیش از چهار هفته: {int(stats.get('expired_4w_users') or 0)}\n"
        f"◈ تعداد کاربران منقضی شده بیش از هشت هفته: {int(stats.get('expired_8w_users') or 0)}"
    )


def _broadcast_segment_label(segment: str) -> str:
    seg = str(segment or "").strip().lower()
    mapping = {
        "all": "تمام کاربران",
        "expired_all": "تمام کاربران منقضی شده",
        "no_order": "کاربران بدون سفارش",
        "expired_1w": "کاربران منقضی شده بیش از یک هفته",
        "expired_2w": "کاربران منقضی شده بیش از دو هفته",
        "expired_4w": "کاربران منقضی شده بیش از چهار هفته",
        "expired_8w": "کاربران منقضی شده بیش از هشت هفته",
    }
    return mapping.get(seg, "تمام کاربران")


def _is_skip_text(text: str) -> bool:
    raw = str(text or "").strip().replace(" ", "")
    return raw in {"⏩ردکردن", "ردکردن", "⏭️ردکردن", "▶️ردکردن"}


async def _send_broadcast_preview(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    agent_id: int,
    payload: Dict[str, Any],
) -> None:
    body = str(payload.get("text") or "").strip()
    photo_file_id = str(payload.get("photo_file_id") or "").strip()
    segment = str(payload.get("segment") or "all").strip().lower()

    # Preview must never fail just because the live recipient counter could not
    # be calculated. Delivery will calculate the target list again on confirm.
    try:
        target_count = len(get_broadcast_target_telegram_ids(agent_id, segment))
        target_count_text = str(target_count)
    except Exception as exc:
        logger.warning(
            "agent broadcast preview target count failed agent=%s error=%s",
            agent_id,
            type(exc).__name__,
        )
        target_count_text = "نامشخص"

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "👁 <b>پیش‌نمایش انتشار</b>\n"
            f"گروه: {_broadcast_segment_label(segment)}\n"
            f"تعداد گیرنده فعلی: {target_count_text}\n\n"
            "پیام زیر هنوز ارسال نشده است. در صورت تایید روی «✅ انتشار و ارسال» بزنید."
        ),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )

    kb = broadcast_preview_keyboard()
    if photo_file_id:
        # Re-upload bytes instead of relying on media-type-sensitive file_id reuse.
        tg_file = await context.bot.get_file(photo_file_id)
        preview_data = BytesIO()
        await tg_file.download_to_memory(out=preview_data)
        preview_data.seek(0)
        preview_data.name = "broadcast-preview.jpg"

        if len(body) <= 1024:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=preview_data,
                caption=body,
                reply_markup=kb,
            )
        else:
            await context.bot.send_photo(chat_id=chat_id, photo=preview_data)
            await context.bot.send_message(chat_id=chat_id, text=body, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id=chat_id, text=body, reply_markup=kb)


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


def _broadcast_retry_after_seconds(exc: Exception, fallback: float = 1.0) -> float:
    value = getattr(exc, "retry_after", None)
    try:
        if hasattr(value, "total_seconds"):
            value = value.total_seconds()
        seconds = float(value)
        if seconds > 0:
            return min(seconds + 0.25, 30.0)
    except (TypeError, ValueError):
        pass
    return max(0.25, float(fallback))


async def _broadcast_send_with_retry(factory, *, max_attempts: int = 3):
    retries = 0
    last_error = None
    for attempt in range(max(1, int(max_attempts))):
        try:
            result = await factory()
            return True, result, "", retries, None
        except RetryAfter as exc:
            last_error = exc
            if attempt + 1 >= max_attempts:
                return False, None, "temporary", retries, exc
            retries += 1
            await asyncio.sleep(_broadcast_retry_after_seconds(exc, fallback=1.0))
        except (TimedOut, NetworkError) as exc:
            last_error = exc
            if attempt + 1 >= max_attempts:
                return False, None, "temporary", retries, exc
            retries += 1
            await asyncio.sleep(0.75 * (attempt + 1))
        except (Forbidden, BadRequest) as exc:
            return False, None, "unreachable", retries, exc
        except TelegramError as exc:
            return False, None, "telegram", retries, exc
        except Exception as exc:
            return False, None, "other", retries, exc
    return False, None, "other", retries, last_error


async def _send_broadcast_to_targets(
    context: ContextTypes.DEFAULT_TYPE,
    token: str,
    telegram_ids: List[int],
    text: str,
    photo_file_id: str = "",
) -> Tuple[int, int, Dict[str, int]]:
    sender_bot = Bot(token=token)
    body = str(text or "").strip()
    photo_id = str(photo_file_id or "").strip()
    sent_count = 0
    fail_count = 0
    photo_data = b""
    details: Dict[str, int] = {
        "unreachable": 0,
        "temporary": 0,
        "telegram": 0,
        "other": 0,
        "recovered": 0,
    }

    if photo_id:
        try:
            tg_file = await context.bot.get_file(photo_id)
            bio = BytesIO()
            await tg_file.download_to_memory(out=bio)
            photo_data = bio.getvalue()
        except Exception:
            photo_data = b""

    for tg_id in telegram_ids:
        recipient_retried = False
        category = ""
        final_error = None

        if photo_id and photo_data:
            async def _send_photo():
                outgoing = BytesIO(photo_data)
                outgoing.name = "broadcast.jpg"
                if len(body) <= 1024:
                    return await sender_bot.send_photo(
                        chat_id=tg_id,
                        photo=outgoing,
                        caption=body,
                    )
                return await sender_bot.send_photo(chat_id=tg_id, photo=outgoing)

            ok, _, category, retries, final_error = await _broadcast_send_with_retry(_send_photo)
            recipient_retried = recipient_retried or retries > 0

            if ok and len(body) > 1024:
                async def _send_long_text():
                    return await sender_bot.send_message(chat_id=tg_id, text=body)

                ok, _, category, retries, final_error = await _broadcast_send_with_retry(_send_long_text)
                recipient_retried = recipient_retried or retries > 0
        else:
            fallback_text = body
            if photo_id and not photo_data and not fallback_text:
                fallback_text = "📷 تصویر ضمیمه شده بود ولی ارسال تصویر ممکن نشد."

            async def _send_text():
                return await sender_bot.send_message(chat_id=tg_id, text=fallback_text)

            ok, _, category, retries, final_error = await _broadcast_send_with_retry(_send_text)
            recipient_retried = recipient_retried or retries > 0

        if ok:
            sent_count += 1
            if recipient_retried:
                details["recovered"] += 1
        else:
            fail_count += 1
            details[category if category in details else "other"] += 1
            logger.warning(
                "agent broadcast delivery failed tg_id=%s category=%s error=%s",
                tg_id,
                category or "other",
                type(final_error).__name__ if final_error else "unknown",
            )

        await asyncio.sleep(0.06)

    return sent_count, fail_count, details


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    if data.startswith("agbot:broadcast:preview:"):
        action = str(data.rsplit(":", 1)[-1] or "").strip().lower()
        payload = context.user_data.get("broadcast_state")
        if not isinstance(payload, dict):
            await query.answer("پیش‌نمایش منقضی شده است.", show_alert=True)
            return

        if action == "cancel":
            clear_state(context)
            await query.answer("ارسال لغو شد.")
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="❌ ارسال همگانی لغو شد.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await _restore_main_menu(query.message)
            return

        if action == "edit":
            payload["step"] = "wait_text"
            context.user_data["broadcast_state"] = payload
            context.user_data[UD_STATE] = STATE_BROADCAST_MESSAGE
            await query.answer("ویرایش پیام")
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    "✏️ متن جدید پیام را ارسال کنید.\n"
                    "بعد از متن، دوباره می‌توانید عکس را انتخاب یا رد کنید و پیش‌نمایش جدید می‌بینید."
                ),
                reply_markup=cancel_keyboard(),
            )
            return

        if action == "send":
            if str(payload.get("step") or "").strip().lower() != "preview":
                await query.answer("این پیش‌نمایش قبلاً پردازش شده است.", show_alert=True)
                return

            body_text = str(payload.get("text") or "").strip()
            if not body_text:
                payload["step"] = "wait_text"
                context.user_data["broadcast_state"] = payload
                await query.answer("متن پیام خالی است.", show_alert=True)
                return

            customer_bot = get_active_customer_bot(agent_id)
            token = str((customer_bot or {}).get("bot_token") or "").strip()
            if not token:
                clear_state(context)
                await query.answer("ربات مشتری فعال پیدا نشد.", show_alert=True)
                await _restore_main_menu(query.message)
                return

            segment = str(payload.get("segment") or "all").strip().lower()
            telegram_ids = get_broadcast_target_telegram_ids(agent_id, segment)
            if not telegram_ids:
                clear_state(context)
                await query.answer("کاربری برای ارسال پیدا نشد.", show_alert=True)
                await _restore_main_menu(query.message)
                return

            payload["step"] = "sending"
            context.user_data["broadcast_state"] = payload
            await query.answer("ارسال شروع شد…")
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            sent_count, fail_count, delivery_details = await _send_broadcast_to_targets(
                context,
                token,
                telegram_ids,
                body_text,
                str(payload.get("photo_file_id") or ""),
            )

            clear_state(context)
            result_lines = [
                "✅ پیام همگانی ارسال شد.",
                "",
                f"گروه: {_broadcast_segment_label(segment)}",
                f"موفق: {sent_count}",
                f"ناموفق: {fail_count}",
            ]
            if int(delivery_details.get("unreachable") or 0):
                result_lines.append(f"🚫 غیرقابل دسترس/مسدود: {delivery_details['unreachable']}")
            if int(delivery_details.get("temporary") or 0):
                result_lines.append(f"🌐 خطای موقت پس از تلاش مجدد: {delivery_details['temporary']}")
            if int(delivery_details.get("telegram") or 0):
                result_lines.append(f"⚠️ سایر خطاهای تلگرام: {delivery_details['telegram']}")
            if int(delivery_details.get("other") or 0):
                result_lines.append(f"🛠 سایر خطاها: {delivery_details['other']}")
            if int(delivery_details.get("recovered") or 0):
                result_lines.append(f"🔁 بازیابی‌شده با تلاش مجدد: {delivery_details['recovered']}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="\n".join(result_lines),
                reply_markup=ReplyKeyboardRemove(),
            )
            await _restore_main_menu(query.message)
            return

        await query.answer("گزینه نامعتبر است.", show_alert=True)
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
            await query.answer("گروه ارسال نامعتبر است.", show_alert=True)
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
            f"✍ لطفا پیام خود را برای ارسال به «{_broadcast_segment_label(segment)}» وارد کنید:",
            reply_markup=cancel_keyboard(),
        )
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
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
        await update.message.reply_text("❌ وضعیت ارسال همگانی نامعتبر است.", reply_markup=ReplyKeyboardRemove())
        await _restore_main_menu(update.message)
        return True

    segment = str(payload.get("segment") or "all").strip().lower()
    text = (update.message.text or update.message.caption or "").strip()
    image_document = update.message.document if (
        update.message.document
        and str(update.message.document.mime_type or "").lower().startswith("image/")
    ) else None
    photo_file_id = (
        update.message.photo[-1].file_id
        if update.message.photo
        else (image_document.file_id if image_document else "")
    )
    step = str(payload.get("step") or "wait_text").strip().lower()

    if text in CANCEL_WORDS:
        clear_state(context)
        await update.message.reply_text("❌ عملیات ارسال همگانی لغو شد.", reply_markup=ReplyKeyboardRemove())
        await _restore_main_menu(update.message)
        return True

    if step == "wait_text":
        if not text:
            await update.message.reply_text("❌ لطفاً متن پیام را کامل ارسال کنید.", reply_markup=cancel_keyboard())
            return True
        payload["text"] = text
        payload["step"] = "wait_photo"
        context.user_data["broadcast_state"] = payload
        await update.message.reply_text(
            "🖼️ لطفا عکس خود را برای ارسال به کاربران ارسال کنید یا روی دکمه [⏩رد کردن] کلیک کنید:",
            reply_markup=broadcast_skip_cancel_keyboard(),
        )
        return True

    if step in {"preview", "sending"}:
        await update.message.reply_text(
            "👁 پیش‌نمایش آماده است. از دکمه‌های همان پیش‌نمایش برای ارسال، ویرایش یا لغو استفاده کنید.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return True

    if step != "wait_photo":
        payload["step"] = "wait_text"
        context.user_data["broadcast_state"] = payload
        await update.message.reply_text(
            f"✍ لطفا پیام خود را برای ارسال به «{_broadcast_segment_label(segment)}» وارد کنید:",
            reply_markup=cancel_keyboard(),
        )
        return True

    if photo_file_id:
        payload["photo_file_id"] = photo_file_id
    elif _is_skip_text(text):
        payload["photo_file_id"] = ""
    else:
        await update.message.reply_text(
            "❌ لطفا عکس ارسال کنید یا روی دکمه [⏩رد کردن] بزنید.",
            reply_markup=broadcast_skip_cancel_keyboard(),
        )
        return True

    body_text = str(payload.get("text") or "").strip()
    if not body_text:
        payload["step"] = "wait_text"
        context.user_data["broadcast_state"] = payload
        await update.message.reply_text(
            "❌ متن پیام خالی است. لطفاً دوباره متن را ارسال کنید.",
            reply_markup=cancel_keyboard(),
        )
        return True

    payload["step"] = "preview"
    context.user_data["broadcast_state"] = payload
    try:
        await _send_broadcast_preview(context, update.message.chat_id, agent_id, payload)
    except Exception as e:
        safe_detail = secure_io.redact_sensitive_text(str(e)).strip()
        if len(safe_detail) > 500:
            safe_detail = safe_detail[:500] + "…"
        logger.warning(
            "agent broadcast preview failed agent=%s error=%s detail=%s",
            agent_id,
            type(e).__name__,
            safe_detail or "-",
        )
        payload["step"] = "wait_photo"
        context.user_data["broadcast_state"] = payload
        await update.message.reply_text(
            "❌ ساخت پیش‌نمایش ناموفق بود.\n"
            f"نوع خطا: {type(e).__name__}\n"
            f"جزئیات: {safe_detail or '-'}\n\n"
            "ℹ️ این خطا الزاماً مربوط به عکس نیست؛ خطای واقعی مرحله پیش‌نمایش بالا نمایش داده شده است.\n"
            "می‌توانید دوباره عکس بفرستید یا «⏩رد کردن» را بزنید.",
            reply_markup=broadcast_skip_cancel_keyboard(),
        )
    return True