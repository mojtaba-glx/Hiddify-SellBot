from __future__ import annotations

import io
import logging
import os
from typing import Any
from urllib.parse import urlparse

from telegram import Bot, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from AdminBot.keyboards import admin_main_keyboard
from Shared import userbot_db
from Shared.tg_button_styles import inline_button as InlineKeyboardButton

logger = logging.getLogger(__name__)

BTN_CHANNEL_POSTS = "📢 مدیریت کانال"
STATE_KEY = "_channel_post_state"
DRAFT_KEY = "_channel_post_draft"
CB = "channelpost:"
MAX_BUTTONS = 8


def _admin_id() -> int:
    try:
        return int(os.getenv("ADMIN_ID", "0") or 0)
    except Exception:
        return 0


def _authorized(update: Update) -> bool:
    user = update.effective_user
    return bool(user and _admin_id() > 0 and int(user.id) == _admin_id())


def _draft(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    value = context.user_data.get(DRAFT_KEY)
    if not isinstance(value, dict):
        value = {"kind": "", "text": "", "file_id": "", "buttons": []}
        context.user_data[DRAFT_KEY] = value
    return value


def _clear(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(STATE_KEY, None)
    context.user_data.pop(DRAFT_KEY, None)


def _channel_target() -> str:
    explicit = str(os.getenv("CHANNEL_POST_TARGET", "") or "").strip()
    if explicit:
        return explicit
    try:
        settings = userbot_db.get_force_join_settings() or {}
    except Exception:
        settings = {}
    channel_id = str(settings.get("channel_id") or "").strip()
    if channel_id:
        return channel_id
    username = str(settings.get("channel_username") or "").strip().lstrip("@")
    return f"@{username}" if username else ""


def _valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https", "tg"} and bool(parsed.netloc or parsed.scheme == "tg")
    except Exception:
        return False


def _post_markup(draft: dict[str, Any]) -> InlineKeyboardMarkup | None:
    buttons = draft.get("buttons") or []
    if not buttons:
        return None
    rows = []
    for item in buttons[:MAX_BUTTONS]:
        rows.append([
            InlineKeyboardButton(
                str(item.get("text") or "لینک"),
                url=str(item.get("url") or ""),
                style=str(item.get("style") or "primary"),
            )
        ])
    return InlineKeyboardMarkup(rows)


def _menu_markup(has_draft: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("➕ ساخت پست جدید", callback_data=CB + "new", style="success")]]
    if has_draft:
        rows.extend([
            [InlineKeyboardButton("🔘 افزودن دکمه", callback_data=CB + "button", style="primary")],
            [InlineKeyboardButton("👁 پیش‌نمایش", callback_data=CB + "preview", style="primary")],
            [InlineKeyboardButton("🚀 انتشار در کانال", callback_data=CB + "publish", style="success")],
            [InlineKeyboardButton("🧹 پاک کردن دکمه‌ها", callback_data=CB + "clear_buttons", style="danger")],
        ])
    rows.append([InlineKeyboardButton("❌ بستن", callback_data=CB + "close", style="danger")])
    return InlineKeyboardMarkup(rows)


def _summary(draft: dict[str, Any]) -> str:
    kind = draft.get("kind") or ""
    kind_fa = {"text": "متنی", "photo": "عکس + کپشن", "video": "ویدئو + کپشن"}.get(kind, "هنوز ساخته نشده")
    return (
        "📢 <b>مدیریت پست کانال</b>\n\n"
        f"📝 نوع پست: <b>{kind_fa}</b>\n"
        f"🔘 تعداد دکمه‌ها: <b>{len(draft.get('buttons') or [])}</b>\n"
        f"🎯 مقصد: <code>{_channel_target() or 'تنظیم نشده'}</code>\n\n"
        "پیام از ربات ادمین ساخته می‌شود و هنگام انتشار با توکن ربات کاربران به کانال ارسال می‌شود."
    )


async def _show_menu(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    draft = _draft(context)
    await message.reply_text(
        _summary(draft),
        parse_mode="HTML",
        reply_markup=_menu_markup(bool(draft.get("kind"))),
    )


async def _send_preview(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    draft = _draft(context)
    markup = _post_markup(draft)
    kind = draft.get("kind")
    text = draft.get("text") or ""
    if kind == "text":
        await message.reply_text(text or " ", parse_mode="HTML", reply_markup=markup)
    elif kind == "photo":
        await message.reply_photo(photo=draft["file_id"], caption=text or None, parse_mode="HTML", reply_markup=markup)
    elif kind == "video":
        await message.reply_video(video=draft["file_id"], caption=text or None, parse_mode="HTML", reply_markup=markup)
    else:
        await message.reply_text("❌ هنوز محتوای پست ساخته نشده است.")


async def _download_media(admin_bot: Bot, file_id: str) -> io.BytesIO:
    tg_file = await admin_bot.get_file(file_id)
    data = await tg_file.download_as_bytearray()
    stream = io.BytesIO(bytes(data))
    stream.name = "channel-media"
    stream.seek(0)
    return stream


async def _publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    draft = _draft(context)
    target = _channel_target()
    token = str(os.getenv("USER_BOT_TOKEN", "") or "").strip()
    if not target:
        await update.effective_message.reply_text(
            "❌ کانال مقصد تنظیم نشده است.\n"
            "اگر کانال عضویت اجباری در ربات کاربران تنظیم شده باشد همان کانال خودکار استفاده می‌شود؛ "
            "در غیر این صورت CHANNEL_POST_TARGET را در .env قرار بده."
        )
        return
    if not token:
        await update.effective_message.reply_text("❌ USER_BOT_TOKEN تنظیم نشده است.")
        return

    markup = _post_markup(draft)
    kind = draft.get("kind")
    text = draft.get("text") or ""
    user_bot = Bot(token=token)
    try:
        if kind == "text":
            sent = await user_bot.send_message(chat_id=target, text=text or " ", parse_mode="HTML", reply_markup=markup)
        elif kind in {"photo", "video"}:
            media = await _download_media(context.bot, str(draft.get("file_id") or ""))
            if kind == "photo":
                sent = await user_bot.send_photo(chat_id=target, photo=media, caption=text or None, parse_mode="HTML", reply_markup=markup)
            else:
                sent = await user_bot.send_video(chat_id=target, video=media, caption=text or None, parse_mode="HTML", reply_markup=markup)
        else:
            await update.effective_message.reply_text("❌ ابتدا یک پست بساز.")
            return
    except Exception as exc:
        logger.exception("Channel publish failed")
        await update.effective_message.reply_text(
            "❌ انتشار ناموفق بود. دسترسی ادمین ربات کاربران به کانال و شناسه کانال را بررسی کن.\n"
            f"خطا: <code>{type(exc).__name__}</code>",
            parse_mode="HTML",
        )
        return
    finally:
        try:
            await user_bot.shutdown()
        except Exception:
            pass

    _clear(context)
    await update.effective_message.reply_text(
        f"✅ پست با موفقیت توسط ربات کاربران در کانال منتشر شد.\n🆔 Message ID: <code>{sent.message_id}</code>",
        parse_mode="HTML",
        reply_markup=admin_main_keyboard(),
    )


async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pre-router in group -1. Stops propagation only while channel-post flow owns the update."""
    if not _authorized(update) or not update.effective_message:
        return

    message = update.effective_message
    text = str(message.text or "").strip()
    state = str(context.user_data.get(STATE_KEY) or "")

    if text == BTN_CHANNEL_POSTS:
        context.user_data.pop(STATE_KEY, None)
        await _show_menu(message, context)
        raise ApplicationHandlerStop

    if not state:
        return

    if text == "❌ لغو":
        context.user_data.pop(STATE_KEY, None)
        await _show_menu(message, context)
        raise ApplicationHandlerStop

    draft = _draft(context)
    if state == "content":
        if message.photo:
            draft.update(kind="photo", file_id=message.photo[-1].file_id, text=message.caption_html or "")
        elif message.video:
            draft.update(kind="video", file_id=message.video.file_id, text=message.caption_html or "")
        elif message.text:
            draft.update(kind="text", file_id="", text=message.text_html or message.text)
        else:
            await message.reply_text("❌ فقط متن، عکس یا ویدئو ارسال کن.")
            raise ApplicationHandlerStop
        context.user_data.pop(STATE_KEY, None)
        await message.reply_text("✅ محتوای پست ذخیره شد.")
        await _show_menu(message, context)
        raise ApplicationHandlerStop

    if state == "button_text":
        if not text or len(text) > 64:
            await message.reply_text("❌ عنوان دکمه باید بین ۱ تا ۶۴ کاراکتر باشد.")
            raise ApplicationHandlerStop
        context.user_data["_channel_post_button_text"] = text
        context.user_data[STATE_KEY] = "button_url"
        await message.reply_text("🔗 حالا لینک دکمه را بفرست؛ مثال: https://t.me/YourBot")
        raise ApplicationHandlerStop

    if state == "button_url":
        if not _valid_url(text):
            await message.reply_text("❌ لینک معتبر نیست. لینک باید با https:// ، http:// یا tg:// شروع شود.")
            raise ApplicationHandlerStop
        label = str(context.user_data.pop("_channel_post_button_text", "") or "لینک")
        buttons = list(draft.get("buttons") or [])
        if len(buttons) >= MAX_BUTTONS:
            await message.reply_text(f"❌ حداکثر {MAX_BUTTONS} دکمه برای هر پست مجاز است.")
        else:
            buttons.append({"text": label, "url": text, "style": "primary"})
            draft["buttons"] = buttons
            await message.reply_text("✅ دکمه اضافه شد.")
        context.user_data.pop(STATE_KEY, None)
        await _show_menu(message, context)
        raise ApplicationHandlerStop


async def handle_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update) or not update.callback_query:
        return
    query = update.callback_query
    data = str(query.data or "")
    if not data.startswith(CB):
        return
    await query.answer()
    action = data[len(CB):]
    draft = _draft(context)

    if action == "new":
        context.user_data[DRAFT_KEY] = {"kind": "", "text": "", "file_id": "", "buttons": []}
        context.user_data[STATE_KEY] = "content"
        await query.message.reply_text(
            "📝 متن پست را بفرست، یا عکس/ویدئو را همراه کپشن ارسال کن.\n\nبرای انصراف «❌ لغو» را بفرست."
        )
        return

    if action == "button":
        if not draft.get("kind"):
            await query.message.reply_text("❌ ابتدا محتوای پست را بساز.")
            return
        if len(draft.get("buttons") or []) >= MAX_BUTTONS:
            await query.message.reply_text(f"❌ حداکثر {MAX_BUTTONS} دکمه مجاز است.")
            return
        context.user_data[STATE_KEY] = "button_text"
        await query.message.reply_text("🔘 عنوان دکمه را بفرست؛ مثلاً: 🛒 خرید سرویس")
        return

    if action == "preview":
        await _send_preview(query.message, context)
        return

    if action == "clear_buttons":
        draft["buttons"] = []
        await query.message.reply_text("✅ همه دکمه‌های پیش‌نویس پاک شدند.")
        await _show_menu(query.message, context)
        return

    if action == "publish":
        await _publish(update, context)
        return

    if action == "close":
        _clear(context)
        await query.message.reply_text("✅ مدیریت کانال بسته شد.", reply_markup=admin_main_keyboard())
        return
