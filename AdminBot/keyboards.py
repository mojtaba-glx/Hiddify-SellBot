# AdminBot/keyboards.py
from telegram import ReplyKeyboardMarkup, KeyboardButton

# متن دکمه‌ها
BTN_SERVERS = "🖥️مدیریت سرورها"
BTN_SEARCH_USER = "🔍جستجوی کاربر"
BTN_USERBOT = "🤖مدیریت ربات کاربران"
BTN_STATUS = "📈وضعیت سرور"
BTN_BACKUP = "📬دریافت بکاپ"


def admin_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(BTN_SERVERS)],
        [KeyboardButton(BTN_SEARCH_USER)],
        [KeyboardButton(BTN_USERBOT)],
        [KeyboardButton(BTN_STATUS), KeyboardButton(BTN_BACKUP)],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, selective=True)


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("❌لغو")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=True,
    )


def confirm_add_user_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("✅تایید")],
            [KeyboardButton("❌لغو")],
        ],
        resize_keyboard=True,
        selective=True,
    )
