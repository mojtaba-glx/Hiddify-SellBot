from __future__ import annotations

import io
import logging
import re
from html import escape as html_escape, unescape as html_unescape
from typing import Any
from urllib.parse import urlparse

from telegram import Bot, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from AgentBot.constants import (
    UD_STATE,
    STATE_CHANNEL_CONTENT,
    STATE_CHANNEL_EDIT_TEXT,
    STATE_CHANNEL_EDIT_MEDIA,
    STATE_CHANNEL_BUTTON_TEXT,
    STATE_CHANNEL_BUTTON_URL,
)
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import settings_menu_keyboard
from CustomerBot.database import get_force_join_settings
from Shared.agent_db import get_active_customer_bot
from Shared.tg_button_styles import inline_button as IButton

logger = logging.getLogger(__name__)

DRAFT_KEY = "agent_channel_post_draft"
BUTTON_TEXT_KEY = "agent_channel_post_button_text"
CB = "agbot:channel:"
MAX_BUTTONS = 8
MAX_TEXT_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024
CANCEL_WORDS = {"❌ لغو", "/cancel", "لغو"}


def _empty_draft(agent_id: int) -> dict[str, Any]:
    return {
        "agent_id": int(agent_id or 0),
        "kind": "",
        "text": "",
        "file_id": "",
        "buttons": [],
    }


def _draft(context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> dict[str, Any]:
    value = context.user_data.get(DRAFT_KEY)
    if (
        not isinstance(value, dict)
        or int(value.get("agent_id") or 0) != int(agent_id or 0)
    ):
        value = _empty_draft(agent_id)
        context.user_data[DRAFT_KEY] = value
    return value


def _clear_flow(context: ContextTypes.DEFAULT_TYPE, *, clear_draft: bool = False) -> None:
    context.user_data.pop(UD_STATE, None)
    context.user_data.pop(BUTTON_TEXT_KEY, None)
    if clear_draft:
        context.user_data.pop(DRAFT_KEY, None)


def _channel_target(agent_id: int) -> str:
    """Use this representative's own CustomerBot channel setting."""
    try:
        settings = get_force_join_settings(int(agent_id or 0)) or {}
    except Exception:
        settings = {}

    raw = str(settings.get("channel_username") or "").strip()
    if raw:
        if raw.lstrip("-").isdigit():
            return raw
        return "@" + raw.lstrip("@")

    link = str(settings.get("channel_link") or "").strip()
    if link:
        try:
            parsed = urlparse(link if "://" in link else "https://" + link)
            if parsed.netloc.lower() in {"t.me", "telegram.me", "www.t.me"}:
                username = parsed.path.strip("/").split("/", 1)[0]
                if username:
                    return "@" + username.lstrip("@")
        except Exception:
            pass
    return ""


def _customer_bot_token(agent_id: int) -> str:
    try:
        row = get_active_customer_bot(int(agent_id or 0)) or {}
    except Exception:
        row = {}
    return str(row.get("bot_token") or "").strip()


def _normalize_button_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    if raw.startswith("@"):
        username = raw[1:].strip()
        if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
            return f"https://t.me/{username}"
        return ""

    if raw.lower().startswith("t.me/"):
        raw = "https://" + raw

    try:
        parsed = urlparse(raw)
    except Exception:
        return ""

    scheme = str(parsed.scheme or "").lower()
    if scheme in {"http", "https"} and parsed.netloc:
        return raw
    if scheme == "tg" and (parsed.netloc or parsed.path):
        return raw
    return ""


def _visible_html_length(value: str) -> int:
    raw = str(value or "")
    plain = re.sub(r"<[^>]+>", "", raw)
    return len(html_unescape(plain))


def _content_length_error(kind: str, plain_text: str) -> str:
    limit = MAX_TEXT_LENGTH if str(kind or "") == "text" else MAX_CAPTION_LENGTH
    size = len(str(plain_text or ""))
    if size <= limit:
        return ""
    label = "متن" if str(kind or "") == "text" else "کپشن"
    return f"❌ {label} بیش از حد طولانی است. حداکثر {limit} کاراکتر مجاز است."


def _post_markup(draft: dict[str, Any]) -> InlineKeyboardMarkup | None:
    buttons = draft.get("buttons") or []
    if not buttons:
        return None
    rows = []
    for item in buttons[:MAX_BUTTONS]:
        rows.append([
            IButton(
                str(item.get("text") or "لینک"),
                url=str(item.get("url") or ""),
                style=str(item.get("style") or "primary"),
            )
        ])
    return InlineKeyboardMarkup(rows)


def _menu_markup(has_draft: bool) -> InlineKeyboardMarkup:
    rows = [[IButton("➕ ساخت پست جدید", callback_data=CB + "new", style="success")]]
    if has_draft:
        rows.extend([
            [IButton("✏️ ویرایش پست", callback_data=CB + "edit", style="primary")],
            [IButton("🔘 افزودن دکمه", callback_data=CB + "button", style="primary")],
            [IButton("👁 پیش‌نمایش", callback_data=CB + "preview", style="primary")],
            [IButton("🚀 انتشار در کانال", callback_data=CB + "publish", style="success")],
            [IButton("🧹 پاک کردن دکمه‌ها", callback_data=CB + "clear_buttons", style="danger")],
        ])
    rows.append([IButton("🔙 بازگشت به مدیریت ربات", callback_data=CB + "back", style="primary")])
    return InlineKeyboardMarkup(rows)


def _edit_markup(draft: dict[str, Any]) -> InlineKeyboardMarkup:
    kind = str(draft.get("kind") or "").strip().lower()
    media_title = "🖼 افزودن عکس / ویدئو" if kind == "text" else "🖼 ویرایش عکس / ویدئو"
    return InlineKeyboardMarkup([
        [IButton("📝 ویرایش متن / کپشن", callback_data=CB + "edit_text", style="primary")],
        [IButton(media_title, callback_data=CB + "edit_media", style="primary")],
        [IButton("🔄 جایگزینی کامل پست", callback_data=CB + "edit_replace", style="danger")],
        [IButton("🔙 بازگشت", callback_data=CB + "menu", style="primary")],
    ])


def _summary(agent_id: int, draft: dict[str, Any]) -> str:
    kind = str(draft.get("kind") or "")
    kind_fa = {
        "text": "متنی",
        "photo": "عکس + کپشن",
        "video": "ویدئو + کپشن",
    }.get(kind, "هنوز ساخته نشده")
    target = _channel_target(agent_id) or "تنظیم نشده"
    bot_ready = bool(_customer_bot_token(agent_id))
    bot_status = "✅ فعال" if bot_ready else "❌ تنظیم نشده"
    return (
        "📢 <b>مدیریت پست کانال</b>\n\n"
        f"📝 نوع پست: <b>{kind_fa}</b>\n"
        f"🔘 تعداد دکمه‌ها: <b>{len(draft.get('buttons') or [])}</b>\n"
        f"🎯 کانال نماینده: <code>{html_escape(target)}</code>\n"
        f"🤖 ربات مشتری: <b>{bot_status}</b>\n\n"
        "پست با ربات مشتری فعال همین نماینده در کانال خودش منتشر می‌شود."
    )


async def _show_settings_root(message, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False) -> None:
    text = "⚙️ <b>مدیریت ربات</b>"
    kb = settings_menu_keyboard()
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent_id = get_agent_id(context)
    if not agent_id:
        return
    context.user_data.pop(UD_STATE, None)
    draft = _draft(context, agent_id)
    text = _summary(agent_id, draft)
    kb = _menu_markup(bool(draft.get("kind")))

    if update.callback_query:
        query = update.callback_query
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except BadRequest:
            pass
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _send_preview(message, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    draft = _draft(context, agent_id)
    markup = _post_markup(draft)
    kind = str(draft.get("kind") or "")
    text = str(draft.get("text") or "")

    if kind == "text":
        await message.reply_text(text or " ", parse_mode="HTML", reply_markup=markup)
    elif kind == "photo":
        await message.reply_photo(
            photo=str(draft.get("file_id") or ""),
            caption=text or None,
            parse_mode="HTML",
            reply_markup=markup,
        )
    elif kind == "video":
        await message.reply_video(
            video=str(draft.get("file_id") or ""),
            caption=text or None,
            parse_mode="HTML",
            reply_markup=markup,
        )
    else:
        await message.reply_text("❌ هنوز محتوای پست ساخته نشده است.")


async def _download_media(agent_bot: Bot, file_id: str) -> io.BytesIO:
    tg_file = await agent_bot.get_file(file_id)
    raw = await tg_file.download_as_bytearray()
    stream = io.BytesIO(bytes(raw))
    stream.name = "channel-media"
    stream.seek(0)
    return stream


def _target_for_telegram(raw_target: str) -> Any:
    target = str(raw_target or "").strip()
    if target.lstrip("-").isdigit():
        return int(target)
    return target


async def _publish(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    message = update.effective_message
    if not message:
        return

    draft = _draft(context, agent_id)
    if not draft.get("kind"):
        await message.reply_text("❌ ابتدا یک پست بساز.")
        return

    target_raw = _channel_target(agent_id)
    if not target_raw:
        await message.reply_text(
            "❌ کانال نماینده تنظیم نشده است.\n"
            "از «⚙️ تنظیمات → عضویت اجباری → تنظیم کانال» کانال را ثبت کن."
        )
        return

    token = _customer_bot_token(agent_id)
    if not token:
        await message.reply_text(
            "❌ ربات مشتری فعال برای این نماینده پیدا نشد.\n"
            "ابتدا ربات مشتری را فعال/تنظیم کن."
        )
        return

    markup = _post_markup(draft)
    kind = str(draft.get("kind") or "")
    text = str(draft.get("text") or "")
    sender_bot = Bot(token=token)
    target = _target_for_telegram(target_raw)

    try:
        if kind == "text":
            sent = await sender_bot.send_message(
                chat_id=target,
                text=text or " ",
                parse_mode="HTML",
                reply_markup=markup,
            )
        elif kind in {"photo", "video"}:
            file_id = str(draft.get("file_id") or "").strip()
            if not file_id:
                await message.reply_text("❌ فایل رسانه پیش‌نویس پیدا نشد؛ رسانه را دوباره انتخاب کن.")
                return
            media = await _download_media(context.bot, file_id)
            if kind == "photo":
                sent = await sender_bot.send_photo(
                    chat_id=target,
                    photo=media,
                    caption=text or None,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            else:
                sent = await sender_bot.send_video(
                    chat_id=target,
                    video=media,
                    caption=text or None,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
        else:
            await message.reply_text("❌ نوع محتوای پست نامعتبر است.")
            return
    except Exception as exc:
        logger.exception(
            "Agent channel publish failed (agent_id=%s, target=%s)",
            agent_id,
            target_raw,
        )
        await message.reply_text(
            "❌ انتشار پست انجام نشد.\n"
            "بررسی کن ربات مشتری همین نماینده در کانال ادمین باشد و اجازه ارسال پست داشته باشد.\n"
            f"خطا: <code>{html_escape(type(exc).__name__)}</code>",
            parse_mode="HTML",
        )
        return
    finally:
        try:
            await sender_bot.shutdown()
        except Exception:
            pass

    _clear_flow(context, clear_draft=True)
    await message.reply_text(
        "✅ پست با موفقیت توسط ربات مشتری نماینده در کانال منتشر شد.\n"
        f"🆔 Message ID: <code>{int(sent.message_id)}</code>",
        parse_mode="HTML",
        reply_markup=settings_menu_keyboard(),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    agent_id = get_agent_id(context)
    if not agent_id:
        return

    data = str(query.data or "").strip()

    if data == "agbot:set:channel":
        await show_menu(update, context)
        return

    if not data.startswith(CB):
        return

    action = data[len(CB):]
    draft = _draft(context, agent_id)

    if action == "menu":
        context.user_data.pop(UD_STATE, None)
        await show_menu(update, context)
        return

    if action == "back":
        _clear_flow(context, clear_draft=True)
        await _show_settings_root(query.message, context, edit=True)
        return

    if action == "new":
        context.user_data[DRAFT_KEY] = _empty_draft(agent_id)
        context.user_data[UD_STATE] = STATE_CHANNEL_CONTENT
        await query.message.reply_text(
            "📝 متن پست را بفرست، یا عکس/ویدئو را همراه کپشن ارسال کن.\n\n"
            "برای انصراف «❌ لغو» را بفرست."
        )
        return

    if action == "edit":
        if not draft.get("kind"):
            await query.message.reply_text("❌ هنوز پستی برای ویرایش وجود ندارد.")
            return
        context.user_data.pop(UD_STATE, None)
        await query.message.reply_text(
            "✏️ <b>ویرایش پست</b>\n\n"
            "بخشی که می‌خواهی تغییر کند را انتخاب کن.\n"
            "متن، رسانه و دکمه‌ها مستقل از هم نگه داشته می‌شوند.",
            parse_mode="HTML",
            reply_markup=_edit_markup(draft),
        )
        return

    if action == "edit_text":
        if not draft.get("kind"):
            await query.message.reply_text("❌ هنوز پستی برای ویرایش وجود ندارد.")
            return
        context.user_data[UD_STATE] = STATE_CHANNEL_EDIT_TEXT
        await query.message.reply_text(
            "📝 متن یا کپشن جدید را بفرست.\n\n"
            "عکس/ویدئو و دکمه‌های فعلی تغییر نمی‌کنند.\n"
            "برای پاک‌کردن کامل متن، عدد 0 را بفرست."
        )
        return

    if action == "edit_media":
        if not draft.get("kind"):
            await query.message.reply_text("❌ هنوز پستی برای ویرایش وجود ندارد.")
            return
        context.user_data[UD_STATE] = STATE_CHANNEL_EDIT_MEDIA
        await query.message.reply_text(
            "🖼 عکس یا ویدئوی جدید را بفرست.\n\n"
            "متن/کپشن و دکمه‌های فعلی تغییر نمی‌کنند."
        )
        return

    if action == "edit_replace":
        if not draft.get("kind"):
            await query.message.reply_text("❌ هنوز پستی برای ویرایش وجود ندارد.")
            return
        context.user_data[UD_STATE] = STATE_CHANNEL_CONTENT
        await query.message.reply_text(
            "🔄 نسخه کامل جدید پست را بفرست.\n\n"
            "می‌تواند متن، عکس + کپشن یا ویدئو + کپشن باشد.\n"
            "🔘 دکمه‌های فعلی همچنان حفظ می‌شوند."
        )
        return

    if action == "button":
        if not draft.get("kind"):
            await query.message.reply_text("❌ ابتدا محتوای پست را بساز.")
            return
        if len(draft.get("buttons") or []) >= MAX_BUTTONS:
            await query.message.reply_text(f"❌ حداکثر {MAX_BUTTONS} دکمه مجاز است.")
            return
        context.user_data[UD_STATE] = STATE_CHANNEL_BUTTON_TEXT
        await query.message.reply_text("🔘 عنوان دکمه را بفرست؛ مثلاً: 🛒 خرید سرویس")
        return

    if action == "preview":
        await _send_preview(query.message, context, agent_id)
        return

    if action == "clear_buttons":
        draft["buttons"] = []
        await query.message.reply_text("✅ همه دکمه‌های پیش‌نویس پاک شدند.")
        await show_menu(update, context)
        return

    if action == "publish":
        await _publish(update, context, agent_id)
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    if not message:
        return False

    state = str(context.user_data.get(UD_STATE) or "")
    supported_states = {
        STATE_CHANNEL_CONTENT,
        STATE_CHANNEL_EDIT_TEXT,
        STATE_CHANNEL_EDIT_MEDIA,
        STATE_CHANNEL_BUTTON_TEXT,
        STATE_CHANNEL_BUTTON_URL,
    }
    if state not in supported_states:
        return False

    agent_id = get_agent_id(context)
    if not agent_id:
        return False

    plain_text = str(message.text or message.caption or "").strip()
    if plain_text in CANCEL_WORDS:
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(BUTTON_TEXT_KEY, None)
        await message.reply_text("❌ عملیات لغو شد.")
        await show_menu(update, context)
        return True

    draft = _draft(context, agent_id)

    if state == STATE_CHANNEL_CONTENT:
        if message.photo:
            length_error = _content_length_error("photo", message.caption or "")
            if length_error:
                await message.reply_text(length_error)
                return True
            draft.update(
                kind="photo",
                file_id=message.photo[-1].file_id,
                text=message.caption_html or "",
            )
        elif message.video:
            length_error = _content_length_error("video", message.caption or "")
            if length_error:
                await message.reply_text(length_error)
                return True
            draft.update(
                kind="video",
                file_id=message.video.file_id,
                text=message.caption_html or "",
            )
        elif message.text:
            length_error = _content_length_error("text", message.text or "")
            if length_error:
                await message.reply_text(length_error)
                return True
            draft.update(
                kind="text",
                file_id="",
                text=message.text_html or message.text,
            )
        else:
            await message.reply_text("❌ فقط متن، عکس یا ویدئو ارسال کن.")
            return True
        context.user_data.pop(UD_STATE, None)
        await message.reply_text("✅ محتوای پست ذخیره/ویرایش شد.")
        await show_menu(update, context)
        return True

    if state == STATE_CHANNEL_EDIT_TEXT:
        if not message.text:
            await message.reply_text(
                "❌ برای ویرایش متن/کپشن فقط متن بفرست.\n"
                "برای پاک‌کردن کامل متن، عدد 0 را بفرست."
            )
            return True
        new_text = message.text_html or message.text or ""
        if str(message.text or "").strip() in {"0", "-", "—"}:
            new_text = ""
        else:
            current_kind = str(draft.get("kind") or "text")
            length_error = _content_length_error(current_kind, message.text or "")
            if length_error:
                await message.reply_text(length_error)
                return True
        draft["text"] = new_text
        context.user_data.pop(UD_STATE, None)
        await message.reply_text("✅ متن/کپشن پست ویرایش شد.")
        await show_menu(update, context)
        return True

    if state == STATE_CHANNEL_EDIT_MEDIA:
        # Converting a text-only post to media also converts its text to a
        # caption, so enforce Telegram's caption limit before changing kind.
        if _visible_html_length(str(draft.get("text") or "")) > MAX_CAPTION_LENGTH:
            await message.reply_text(
                f"❌ متن فعلی برای کپشن طولانی است. ابتدا آن را به کمتر از {MAX_CAPTION_LENGTH} کاراکتر کاهش بده."
            )
            return True
        if message.photo:
            draft["kind"] = "photo"
            draft["file_id"] = message.photo[-1].file_id
        elif message.video:
            draft["kind"] = "video"
            draft["file_id"] = message.video.file_id
        else:
            await message.reply_text(
                "❌ فقط عکس یا ویدئو بفرست.\n"
                "متن/کپشن فعلی و دکمه‌ها بدون تغییر می‌مانند."
            )
            return True
        context.user_data.pop(UD_STATE, None)
        await message.reply_text("✅ عکس/ویدئوی پست ویرایش شد.")
        await show_menu(update, context)
        return True

    if state == STATE_CHANNEL_BUTTON_TEXT:
        title = str(message.text or "").strip()
        if not title or len(title) > 64:
            await message.reply_text("❌ عنوان دکمه باید بین ۱ تا ۶۴ کاراکتر باشد.")
            return True
        context.user_data[BUTTON_TEXT_KEY] = title
        context.user_data[UD_STATE] = STATE_CHANNEL_BUTTON_URL
        await message.reply_text(
            "🔗 حالا لینک دکمه را بفرست.\n"
            "می‌توانی لینک کامل یا آیدی تلگرام بفرستی؛ مثال:\n"
            "• https://t.me/YourBot\n"
            "• @YourBot"
        )
        return True

    if state == STATE_CHANNEL_BUTTON_URL:
        normalized = _normalize_button_url(str(message.text or "").strip())
        if not normalized:
            await message.reply_text(
                "❌ لینک معتبر نیست.\n"
                "لینک کامل مثل https://example.com یا https://t.me/YourBot، "
                "یا آیدی تلگرام مثل @YourBot بفرست."
            )
            return True
        label = str(context.user_data.pop(BUTTON_TEXT_KEY, "") or "لینک")
        buttons = list(draft.get("buttons") or [])
        if len(buttons) >= MAX_BUTTONS:
            await message.reply_text(f"❌ حداکثر {MAX_BUTTONS} دکمه برای هر پست مجاز است.")
        else:
            buttons.append({"text": label, "url": normalized, "style": "primary"})
            draft["buttons"] = buttons
            await message.reply_text("✅ دکمه اضافه شد.")
        context.user_data.pop(UD_STATE, None)
        await show_menu(update, context)
        return True

    return False
