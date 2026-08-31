# AdminBot/keyboards.py
from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup

from Shared import i18n
from Shared.tg_button_styles import keyboard_button as KeyboardButton
from Shared.tg_button_styles import inline_button as IButton

# Button labels
BTN_SERVERS = "🖥 مدیریت سرورها"
BTN_SEARCH_USER = "🔍 جستجوی کاربر"
BTN_USERBOT = "🤖 مدیریت ربات کاربران"
BTN_STATUS = "📊 وضعیت سرور"
BTN_BACKUP = "📫 دریافت بکاپ"
BTN_AGENCIES = "🏢 نمایندگی"

# کلیدهای i18n منوی اصلی ادمین (برای مچر چندزبانه)
ADMIN_MENU_KEYS = (
    "adm_menu_servers", "adm_menu_search", "adm_menu_userbot",
    "adm_menu_status", "adm_menu_backup", "adm_menu_agencies",
    "btn_cancel",
)


def _admin_lang() -> str:
    try:
        from Shared import userbot_db
        return userbot_db.get_admin_language()
    except Exception:
        return "fa"


def admin_main_keyboard(lang: str = "") -> ReplyKeyboardMarkup:
    lg = lang or _admin_lang()
    keyboard = [
        [KeyboardButton(i18n.t("adm_menu_servers", lg))],
        [KeyboardButton(i18n.t("adm_menu_search", lg))],
        [KeyboardButton(i18n.t("adm_menu_userbot", lg))],
        [
            KeyboardButton(i18n.t("adm_menu_status", lg)),
            KeyboardButton(i18n.t("adm_menu_agencies", lg)),
            KeyboardButton(i18n.t("adm_menu_backup", lg)),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, selective=True)


def language_keyboard():
    rows = []
    langs = i18n.supported_langs()
    for i in range(0, len(langs), 2):
        rows.append([IButton(i18n.lang_display_name(lg), callback_data=f"lang:set:{lg}") for lg in langs[i:i + 2]])
    return InlineKeyboardMarkup(rows)


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("❌ لغو")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=True,
    )


def confirm_add_user_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("✅ تایید")],
            [KeyboardButton("❌ لغو")],
        ],
        resize_keyboard=True,
        selective=True,
    )
