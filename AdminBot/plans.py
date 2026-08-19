# AdminBot/plans.py

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from telegram import (
    InlineKeyboardMarkup,
    Update,
    ReplyKeyboardMarkup,
)
from telegram.ext import ContextTypes

from AdminBot.keyboards import admin_main_keyboard
from Shared import database, plans_storage
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from Shared.tg_button_styles import keyboard_button as KeyboardButton

logger = logging.getLogger(__name__)

# حالت‌های نمایش پلن برای هر سرور
PLAN_MODE_FIXED = "fixed"
PLAN_MODE_DYNAMIC = "dynamic"
PLAN_MODE_MIXED = "mixed"

# استیت‌های مربوط به پیام‌های متنی
PLANS_STATE_ADD_CAT_TITLE = "plans:add_cat_title"
PLANS_STATE_ADD_CAT_PRIORITY = "plans:add_cat_priority"
PLANS_STATE_EDIT_CAT_TITLE = "plans:edit_cat_title"
PLANS_STATE_EDIT_CAT_PRIORITY = "plans:edit_cat_priority"

PLANS_STATE_ADD_PLAN_TITLE = "plans:add_plan_title"
PLANS_STATE_ADD_PLAN_PRICE = "plans:add_plan_price"
PLANS_STATE_ADD_PLAN_DAYS = "plans:add_plan_days"
PLANS_STATE_ADD_PLAN_GB = "plans:add_plan_gb"

PLANS_STATE_EDIT_DYNAMIC_FIELD = "plans:edit_dynamic_field"

CANCEL_WORDS = {"لغو❌", "لغو", "/cancel"}

_PERSIAN_DIGITS_TRANS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def _normalize_digit_text(value: Any) -> str:
    return str(value or "").translate(_PERSIAN_DIGITS_TRANS)


def _cancel_kb() -> ReplyKeyboardMarkup:
    """کیبورد لغو برای هنگام دریافت ورودی متنی."""
    return ReplyKeyboardMarkup([[KeyboardButton("لغو❌")]], resize_keyboard=True)


def _finish_reply_kb() -> ReplyKeyboardMarkup:
    return admin_main_keyboard()


def _format_discount_tiers(tiers: List[Dict[str, int]]) -> str:
    normalized = plans_storage.normalize_discount_tiers(tiers)
    if not normalized:
        return "غیرفعال"
    return " | ".join(f"از {item['gb']} گیگ: {item['percent']}٪" for item in normalized)


def _is_simple_discount_enabled(settings: Dict[str, Any]) -> bool:
    if "discount_simple_enabled" in settings:
        return plans_storage.is_simple_discount_active(settings)
    discount_tiers = plans_storage.normalize_discount_tiers(settings.get("discount_tiers", []))
    return (
        not discount_tiers
        and int(settings.get("discount_step_gb", 0)) > 0
        and int(settings.get("discount_percent_step", 0)) > 0
        and int(settings.get("discount_percent_max", 0)) > 0
    )


def _is_tiered_discount_enabled(settings: Dict[str, Any]) -> bool:
    if "discount_tiered_enabled" in settings:
        return bool(settings.get("discount_tiered_enabled"))
    return bool(plans_storage.normalize_discount_tiers(settings.get("discount_tiers", [])))


def _parse_discount_tiers_text(text: str) -> List[Dict[str, int]]:
    raw = _normalize_digit_text(text).strip()
    if raw in {"0", "۰", "خاموش", "غیرفعال"}:
        return []

    items = []
    normalized = raw.replace("،", ",").replace("\n", ",").replace("؛", ",")
    for part in normalized.split(","):
        part = part.strip()
        if not part:
            continue
        separator = next((sep for sep in (":", "=", "-") if sep in part), None)
        if not separator:
            raise ValueError
        gb_text, percent_text = part.split(separator, 1)
        gb = int(
            _normalize_digit_text(
                gb_text.replace("گیگ", "").replace("gb", "").replace("GB", "")
            ).replace(",", "").strip()
        )
        percent = int(
            _normalize_digit_text(percent_text)
            .replace("%", "")
            .replace("٪", "")
            .replace(",", "")
            .strip()
        )
        if gb <= 0 or percent <= 0:
            raise ValueError
        items.append({"gb": gb, "percent": percent})

    tiers = plans_storage.normalize_discount_tiers(items)
    if not tiers:
        raise ValueError
    return tiers


# ===============================
#   منوی ریشه‌ی مدیریت پلن‌ها
# ===============================

async def send_plans_root_menu(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """
    منوی اصلی مدیریت پلن‌های یک سرور.
    بسته به حالت نمایش:
      - در حالت ثابت/ترکیبی: لیست دسته‌های پلن + تنظیمات
      - در حالت پویا: فقط تنظیمات پلن‌ها
    """
    server = database.get_server_by_id(server_id)
    server_title = server.get("title") if server else f"سرور #{server_id}"

    mode = plans_storage.get_plan_display_mode(server_id)
    if mode == PLAN_MODE_FIXED:
        mode_txt = "فقط پلن‌های ثابت"
    elif mode == PLAN_MODE_DYNAMIC:
        mode_txt = "فقط پلن پویا"
    elif mode == PLAN_MODE_MIXED:
        mode_txt = "حالت ترکیبی (ثابت + پویا)"
    else:
        mode_txt = "نامشخص"

    text = (
        f"مدیریت پلن‌ها برای سرور 🖥 {server_title}\n"
        "━━━━━━━━━━━━━━\n"
        f"حالت نمایش فعلی در ربات کاربران: {mode_txt}\n\n"
        "یکی از گزینه‌های زیر را انتخاب کنید:"
    )

    rows: List[List[InlineKeyboardButton]] = []

    # فقط اگر حالت ثابت یا ترکیبی باشد، لیست دسته‌های پلن را نشان بده
    if mode in (PLAN_MODE_FIXED, PLAN_MODE_MIXED):
        rows.append(
            [
                InlineKeyboardButton(
                    "📂 لیست دسته‌های پلن",
                    callback_data=f"plans:{server_id}:cats",
                )
            ]
        )

    # همیشه تنظیمات پلن‌ها
    rows.append(
        [
            InlineKeyboardButton(
                "⚙️تنظیمات پلن‌ها",
                callback_data=f"plans:{server_id}:settings",
            )
        ]
    )

    # مدیریت حرفه‌ای تخفیف‌ها (فقط در حالت پویا/ترکیبی که معنادار است)
    if mode in (PLAN_MODE_DYNAMIC, PLAN_MODE_MIXED):
        rows.append(
            [
                InlineKeyboardButton(
                    "🎛 مدیریت حرفه‌ای تخفیف‌ها",
                    callback_data=f"plans:{server_id}:dyn_discount_settings",
                )
            ]
        )

    # دکمه بازگشت
    rows.append(
        [
            InlineKeyboardButton(
                "بازگشت🔙",
                callback_data=f"server:{server_id}",
            )
        ]
    )

    kb = InlineKeyboardMarkup(rows)

    if message:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


# ===============================
#   دسته‌ها و پلن‌های ثابت
# ===============================

async def _send_categories_menu(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """
    منوی «لیست دسته‌های پلن».

    در حالت ترکیبی (mixed) علاوه بر دسته‌ها،
    یک دکمه برای «تنظیم مقادیر پلن پویا📈» هم نمایش داده می‌شود.
    """
    cats = plans_storage.get_plan_categories(server_id)
    mode = plans_storage.get_plan_display_mode(server_id)

    if not cats:
        text = (
            "📂 لیست دسته‌های پلن\n"
            "برای این سرور هنوز هیچ دسته‌ای ثبت نشده است.\n"
            "می‌توانید یک دسته جدید اضافه کنید."
        )
        rows: List[List[InlineKeyboardButton]] = []
    else:
        lines: List[str] = ["📂 لیست دسته‌های پلن", ""]
        rows = []
        for c in cats:
            cid = c["id"]
            title = c.get("title") or f"دسته #{cid}"
            prio = c.get("priority", 0)
            lines.append(f"• {title} (اولویت: {prio})")
            rows.append(
                [
                    InlineKeyboardButton(
                        title,
                        callback_data=f"plans:{server_id}:cat:{cid}",
                    )
                ]
            )
        text = "\n".join(lines)

    # افزودن / حذف دسته
    rows.append(
        [
            InlineKeyboardButton(
                "➕ افزودن دسته",
                callback_data=f"plans:{server_id}:cat_add",
            )
        ]
    )
    if cats:
        rows.append(
            [
                InlineKeyboardButton(
                    "➖ حذف دسته",
                    callback_data=f"plans:{server_id}:cat_del_menu",
                )
            ]
        )

    # فقط در حالت ترکیبی، دکمه‌ی مستقیم برای تنظیم پلن پویا نشان بده
    if mode == PLAN_MODE_MIXED:
        rows.append(
            [
                InlineKeyboardButton(
                    "تنظیم مقادیر پلن پویا📈",
                    callback_data=f"plans:{server_id}:dyn_settings",
                )
            ]
        )

    # تنظیمات کلی پلن‌ها
    rows.append(
        [
            InlineKeyboardButton(
                "⚙️تنظیمات پلن‌ها",
                callback_data=f"plans:{server_id}:settings",
            )
        ]
    )

    # بازگشت
    rows.append(
        [
            InlineKeyboardButton(
                "بازگشت🔙",
                callback_data=f"plans:{server_id}:root",
            )
        ]
    )

    kb = InlineKeyboardMarkup(rows)
    if message:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def _send_category_detail(
    server_id: int,
    category_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """جزئیات یک دسته و دکمه‌های مدیریت آن."""
    cats = plans_storage.get_plan_categories(server_id)
    cat = next((c for c in cats if int(c["id"]) == int(category_id)), None)
    if not cat:
        txt = "❌ این دسته پیدا نشد."
        if message:
            await message.edit_text(txt)
        else:
            await context.bot.send_message(chat_id, txt)
        return

    title = cat.get("title") or f"دسته #{category_id}"
    prio = cat.get("priority", 0)
    plans = plans_storage.get_plans(server_id, category_id=category_id)

    text = (
        f"📂 دسته: {title}\n"
        "━━━━━━━━━━━━━━\n"
        f"🔢 اولویت: {prio}\n"
        f"📋 تعداد پلن: {len(plans)}\n\n"
        "از دکمه‌های زیر برای مدیریت این دسته استفاده کنید."
    )

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📋 لیست پلن‌ها",
                    callback_data=f"plans:{server_id}:plans:{category_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "📝 ویرایش عنوان",
                    callback_data=f"plans:{server_id}:cat_edit_title:{category_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔢 ویرایش اولویت",
                    callback_data=f"plans:{server_id}:cat_edit_prio:{category_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "بازگشت🔙",
                    callback_data=f"plans:{server_id}:cats",
                )
            ],
        ]
    )

    if message:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def _send_plans_list(
    server_id: int,
    category_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """لیست پلن‌های یک دسته (با ستون ردیف و مرتب)."""
    plans = plans_storage.get_plans(server_id, category_id=category_id)

    if not plans:
        text = (
            "📋 لیست پلن‌های موجود\n"
            "هنوز هیچ پلنی در این دسته ثبت نشده است."
        )
        rows: List[List[InlineKeyboardButton]] = []
    else:
        lines: List[str] = [
            "📋 لیست پلن‌های موجود",
            "",
            "ستون‌ها:",
            "ردیف | عنوان پلن | 💰 قیمت | ⌛ زمان (روز) | 📊 حجم (گیگابایت)",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        rows: List[List[InlineKeyboardButton]] = []

        for idx, p in enumerate(plans, start=1):
            pid = p["id"]
            title = p.get("title") or f"پلن #{pid}"
            price = p.get("price", 0)
            days = p.get("days", 0)
            gb = p.get("gb", 0)

            price_txt = f"{price:,} تومان"
            days_txt = f"{days} روز"
            gb_txt = f"{gb} گیگ"

            # متن ردیف
            lines.append(
                f"{idx} | {title} | {price_txt} | {days_txt} | {gb_txt}"
            )

            # دکمه برای مدیریت همان پلن
            rows.append(
                [
                    InlineKeyboardButton(
                        title,
                        callback_data=f"plans:{server_id}:plan:{pid}",
                    )
                ]
            )

        text = "\n".join(lines)

    # دکمه‌های پایین لیست
    rows.append(
        [
            InlineKeyboardButton(
                "➕ افزودن پلن",
                callback_data=f"plans:{server_id}:plan_add:{category_id}",
            )
        ]
    )
    if plans:
        rows.append(
            [
                InlineKeyboardButton(
                    "🗑️ حذف پلن",
                    callback_data=f"plans:{server_id}:plan_del_menu:{category_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "بازگشت🔙",
                callback_data=f"plans:{server_id}:cat:{category_id}",
            )
        ]
    )

    kb = InlineKeyboardMarkup(rows)
    if message:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def _send_plan_detail(
    server_id: int,
    plan_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """نمایش جزئیات یک پلن ثابت."""
    plan = plans_storage.get_plan(server_id, plan_id)
    if not plan:
        txt = "❌ پلن مورد نظر پیدا نشد."
        if message:
            await message.edit_text(txt)
        else:
            await context.bot.send_message(chat_id, txt)
        return

    title = plan.get("title") or f"پلن #{plan_id}"
    price = plan.get("price", 0)
    days = plan.get("days", 0)
    gb = plan.get("gb", 0)
    cat_id = plan.get("category_id")

    text = (
        f"📦 {title}\n"
        "━━━━━━━━━━━━━━\n"
        f"💰 قیمت: {price:,} تومان\n"
        f"⌛ زمان: {days} روز\n"
        f"📊 حجم: {gb} گیگابایت\n"
    )

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🗑️ حذف این پلن",
                    callback_data=f"plans:{server_id}:plan_del:{plan_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "بازگشت🔙",
                    callback_data=f"plans:{server_id}:plans:{cat_id}",
                )
            ],
        ]
    )

    if message:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


# ===============================
#   تنظیمات پلن‌ها (منوی میانی)
# ===============================

async def _send_plans_settings_menu(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """
    منوی تنظیمات پلن‌ها:
      - نوع نمایش پلن‌ها (ثابت/پویا/ترکیبی)
      - تنظیمات پلن پویا
      - بازگشت
    """
    text = "⚙️تنظیمات پلن‌ها\n\nیکی از گزینه‌های زیر را انتخاب کنید:"
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "نوع نمایش پلن‌ها📋",
                    callback_data=f"plans:{server_id}:mode_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    "تنظیم پلن پویا📈",
                    callback_data=f"plans:{server_id}:dyn_settings",
                )
            ],
            [
                InlineKeyboardButton(
                    "بازگشت🔙",
                    callback_data=f"plans:{server_id}:root",
                )
            ],
        ]
    )
    if message:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def _send_display_mode_menu(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """
    منوی انتخاب نوع نمایش پلن‌ها (ثابت / پویا / ترکیبی).
    یکی از موارد با تیک سبز نمایش داده می‌شود.
    """
    mode = plans_storage.get_plan_display_mode(server_id)

    def _mode_label(value: str, title: str) -> str:
        mark = "✅" if mode == value else "❌"
        return f"{mark} {title}"

    text = "⚙️تنظیمات پلن‌ها\n\nحالت نمایش پلن‌ها را انتخاب کنید:"

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _mode_label(PLAN_MODE_FIXED, "ثابت"),
                    callback_data=f"plans:{server_id}:mode_fixed",
                ),
                InlineKeyboardButton(
                    _mode_label(PLAN_MODE_DYNAMIC, "پویا"),
                    callback_data=f"plans:{server_id}:mode_dynamic",
                ),
                InlineKeyboardButton(
                    _mode_label(PLAN_MODE_MIXED, "ترکیبی"),
                    callback_data=f"plans:{server_id}:mode_mixed",
                ),
            ],
            [
                InlineKeyboardButton(
                    "بازگشت🔙",
                    callback_data=f"plans:{server_id}:settings",
                )
            ],
        ]
    )

    if message:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


# ===============================
#   تنظیمات پلن پویا
# ===============================

async def _send_dynamic_settings_menu(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """نمایش و ویرایش تنظیمات پلن پویا."""
    s = plans_storage.get_plan_dynamic_settings(server_id)
    discount_tiers = plans_storage.normalize_discount_tiers(s.get("discount_tiers", []))
    simple_enabled = _is_simple_discount_enabled(s)
    tiered_enabled = _is_tiered_discount_enabled(s)
    if discount_tiers:
        discount_line = f"🎚 تخفیف پلاکانی: {_format_discount_tiers(discount_tiers)}"
    else:
        discount_line = (
            f"🎁 تخفیف حجمی ساده: هر {s['discount_step_gb']} گیگ +{s['discount_percent_step']}٪ "
            f"تا سقف {s['discount_percent_max']}٪"
        )

    lines = [
        "📈 تنظیم مقادیر پلن پویا",
        "",
        f"💰 قیمت هر گیگ: {s['price_per_gb']:,} تومان",
        f"💰 قیمت هر ماه: {s['price_per_month']:,} تومان",
        "",
        f"📊 حجم قابل فروش: از {s['min_gb']} تا {s['max_gb']} گیگ (گام: {s['step_gb']})",
        f"⌛ زمان اشتراک: از {s['min_month']} تا {s['max_month']} ماه (گام: {s['step_month']})",
        "",
        discount_line,
        "",
        "برای تغییر هر مقدار از دکمه‌های زیر استفاده کنید.",
        "برای مدیریت و ویرایش تنظیمات تخفیف‌ها، از دکمه‌ی اختصاصی استفاده کنید.",
    ]

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💰 قیمت هر گیگ",
                    callback_data=f"plans:{server_id}:dyn_edit:price_per_gb",
                )
            ],
            [
                InlineKeyboardButton(
                    "💰 قیمت هر ماه",
                    callback_data=f"plans:{server_id}:dyn_edit:price_per_month",
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 حداقل/حداکثر حجم و گام",
                    callback_data=f"plans:{server_id}:dyn_edit:volume_range",
                )
            ],
            [
                InlineKeyboardButton(
                    "⌛ حداقل/حداکثر زمان و گام",
                    callback_data=f"plans:{server_id}:dyn_edit:time_range",
                )
            ],
            [
                InlineKeyboardButton(
                    "بازگشت🔙",
                    callback_data=f"plans:{server_id}:settings",
                )
            ],
        ]
    )

    text = "\n".join(lines)
    if message:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


async def _send_discount_settings_menu(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """منوی اختصاصی مدیریت روشن/خاموش کردن تخفیف‌ها."""
    s = plans_storage.get_plan_dynamic_settings(server_id)
    discount_tiers = plans_storage.normalize_discount_tiers(s.get("discount_tiers", []))
    simple_enabled = _is_simple_discount_enabled(s)
    tiered_enabled = _is_tiered_discount_enabled(s)

    # اگر تایمر منقضی شده، خودکار غیرفعال کن و تنظیم را به حالت قبل برگردان
    expire_at = s.get("discount_simple_expire_at") or 0
    try:
        expire_at = float(expire_at)
    except (TypeError, ValueError):
        expire_at = 0
    if expire_at > 0 and time.time() >= expire_at:
        plans_storage.set_plan_dynamic_settings(
            server_id,
            discount_simple_enabled=False,
            discount_simple_expire_at=0,
        )
        s = plans_storage.get_plan_dynamic_settings(server_id)
        simple_enabled = False
        expire_at = 0

    if expire_at > 0:
        remaining = int(expire_at - time.time())
        days = remaining // 86400
        hours = (remaining % 86400) // 3600
        minutes = (remaining % 3600) // 60
        parts = []
        if days > 0:
            parts.append(f"{days} روز")
        if hours > 0:
            parts.append(f"{hours} ساعت")
        if minutes > 0:
            parts.append(f"{minutes} دقیقه")
        remaining_txt = " و ".join(parts) if parts else "کمتر از یک دقیقه"
        timer_line = (
            f"⏱ تایمر تخفیف حجمی ساده: {remaining_txt} مانده "
            f"(پایان: {datetime.fromtimestamp(expire_at).strftime('%Y-%m-%d %H:%M')})"
        )
    else:
        timer_line = ""

    lines = [
        "🎛 مدیریت حرفه‌ای تخفیف‌ها",
        "",
        f"🎁 تخفیف حجمی ساده: {'فعال ✅' if simple_enabled else 'غیرفعال ❌'}",
        f"🎚 تخفیف پلاکانی: {'فعال ✅' if tiered_enabled else 'غیرفعال ❌'}",
        "",
        "در این بخش می‌توانی تنظیمات ذخیره‌شده هر نوع تخفیف را ببینی و تنها در صورت نیاز آن را تغییر بدهی.",
    ]
    if timer_line:
        lines.append(timer_line)

    if simple_enabled:
        lines.append(
            f"• تخفیف حجمی ساده: از {s['discount_step_gb']} گیگ به بالا، {s['discount_percent_step']}٪ تا سقف {s['discount_percent_max']}٪"
        )
    elif int(s.get('discount_step_gb', 0)) > 0 and int(s.get('discount_percent_step', 0)) > 0:
        lines.append(
            f"• تنظیمات ذخیره‌شده تخفیف حجمی ساده: از {s['discount_step_gb']} گیگ به بالا، {s['discount_percent_step']}٪ تا سقف {s['discount_percent_max']}٪ (غیرفعال)"
        )

    if tiered_enabled:
        lines.append(f"• پله‌های تخفیف پلاکانی: {_format_discount_tiers(discount_tiers)}")
    elif discount_tiers:
        lines.append(
            f"• پله‌های تخفیف پلاکانی ذخیره شده: {_format_discount_tiers(discount_tiers)} (غیرفعال)"
        )

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{'خاموش کن' if simple_enabled else 'روشن کن'} تخفیف حجمی ساده",
                    callback_data=f"plans:{server_id}:dyn_toggle:discount",
                    style="danger" if simple_enabled else "success",
                )
            ],
            [
                InlineKeyboardButton(
                    f"{'خاموش کن' if tiered_enabled else 'روشن کن'} تخفیف پلاکانی",
                    callback_data=f"plans:{server_id}:dyn_toggle:discount_tiers",
                    style="danger" if tiered_enabled else "success",
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ ویرایش تخفیف حجمی ساده",
                    callback_data=f"plans:{server_id}:dyn_edit:discount",
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ ویرایش تخفیف پله‌ای",
                    callback_data=f"plans:{server_id}:dyn_edit:discount_tiers",
                )
            ],
            [
                InlineKeyboardButton(
                    "⏱ تنظیم تایمر تخفیف حجمی ساده",
                    callback_data=f"plans:{server_id}:dyn_edit:discount_timer",
                )
            ],
            [
                InlineKeyboardButton(
                    "بازگشت🔙",
                    callback_data=f"plans:{server_id}:root",
                )
            ],
        ]
    )

    text = "\n".join(lines)
    if message:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


# ===============================
#   Callback handler اصلی
# ===============================

async def handle_plans_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    این تابع تمامی callback_dataهایی که با "plans:" شروع می‌شوند را مدیریت می‌کند.
    """
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    msg = query.message
    chat_id = msg.chat_id
    parts = data.split(":")

    # data مثل plans:SERVER_ID:...
    if len(parts) < 3:
        await query.answer("داده نامعتبر است.")
        return

    _, sid_str, *rest = parts
    try:
        server_id = int(sid_str)
    except ValueError:
        await query.answer("شناسه سرور نامعتبر است.")
        return

    action = rest[0] if rest else "root"
    await query.answer()

    # ----- منوی ریشه -----
    if action == "root":
        await send_plans_root_menu(server_id, chat_id, context, message=msg)
        return

    # ----- تنظیم حالت نمایش -----
    if action == "mode_menu":
        await _send_display_mode_menu(server_id, chat_id, context, message=msg)
        return

    if action == "mode_fixed":
        plans_storage.set_plan_display_mode(server_id, PLAN_MODE_FIXED)
        await _send_display_mode_menu(server_id, chat_id, context, message=msg)
        return

    if action == "mode_dynamic":
        plans_storage.set_plan_display_mode(server_id, PLAN_MODE_DYNAMIC)
        await _send_display_mode_menu(server_id, chat_id, context, message=msg)
        return

    if action == "mode_mixed":
        plans_storage.set_plan_display_mode(server_id, PLAN_MODE_MIXED)
        await _send_display_mode_menu(server_id, chat_id, context, message=msg)
        return

    # ----- منوی تنظیمات پلن‌ها -----
    if action == "settings":
        await _send_plans_settings_menu(server_id, chat_id, context, message=msg)
        return

    # ----- تنظیمات پویا -----
    if action == "dyn_settings":
        await _send_dynamic_settings_menu(server_id, chat_id, context, message=msg)
        return

    if action == "dyn_discount_settings":
        await _send_discount_settings_menu(server_id, chat_id, context, message=msg)
        return

    if action == "dyn_toggle":
        dyn_action = rest[1] if len(rest) > 1 else ""
        s = plans_storage.get_plan_dynamic_settings(server_id)
        discount_tiers = plans_storage.normalize_discount_tiers(s.get("discount_tiers", []))
        simple_enabled = _is_simple_discount_enabled(s)
        tiered_enabled = _is_tiered_discount_enabled(s)

        if dyn_action == "discount":
            if simple_enabled:
                plans_storage.set_plan_dynamic_settings(
                    server_id,
                    discount_simple_enabled=False,
                    discount_simple_expire_at=0,
                )
            else:
                update_kwargs = {"discount_simple_enabled": True, "discount_simple_expire_at": 0}
                if (
                    int(s.get("discount_step_gb", 0)) <= 0
                    or int(s.get("discount_percent_step", 0)) <= 0
                    or int(s.get("discount_percent_max", 0)) <= 0
                ):
                    update_kwargs["discount_step_gb"] = s.get("discount_step_gb", 50) or 50
                    update_kwargs["discount_percent_step"] = s.get("discount_percent_step", 5) or 5
                    update_kwargs["discount_percent_max"] = s.get("discount_percent_max", 50) or 50
                plans_storage.set_plan_dynamic_settings(
                    server_id,
                    **update_kwargs,
                )

            await _send_discount_settings_menu(server_id, chat_id, context, message=msg)
            return

        if dyn_action == "discount_tiers":
            if tiered_enabled:
                plans_storage.set_plan_dynamic_settings(
                    server_id,
                    discount_tiered_enabled=False,
                )
                await _send_discount_settings_menu(server_id, chat_id, context, message=msg)
                return

            if discount_tiers:
                plans_storage.set_plan_dynamic_settings(
                    server_id,
                    discount_tiered_enabled=True,
                )
                await _send_discount_settings_menu(server_id, chat_id, context, message=msg)
                return

            await context.bot.send_message(
                chat_id,
                "⚠️ هیچ پله‌ای برای تخفیف پلاکانی تنظیم نشده است. برای فعال کردن ابتدا روی «🎚 ویرایش تخفیف پله‌ای» بزن و پله‌ها را وارد کن.",
                reply_markup=_cancel_kb(),
            )
            await _send_discount_settings_menu(server_id, chat_id, context, message=msg)
            return

    if action == "dyn_edit":
        dyn_action = rest[1] if len(rest) > 1 else ""
        context.user_data["state"] = PLANS_STATE_EDIT_DYNAMIC_FIELD
        context.user_data["plans_server_id"] = server_id
        context.user_data["plans_dyn_action"] = dyn_action
        await msg.edit_reply_markup(reply_markup=None)

        if dyn_action == "price_per_gb":
            prompt = "💰 قیمت هر گیگ را (تومان) ارسال کنید:"
        elif dyn_action == "price_per_month":
            prompt = "💰 قیمت هر ماه اشتراک را (تومان) ارسال کنید:"
        elif dyn_action == "volume_range":
            prompt = (
                "📊 تنظیم حجم به صورت: حداقل_حجم-حداکثر_حجم-گام\n"
                "مثال: 20-200-20"
            )
        elif dyn_action == "time_range":
            prompt = (
                "⌛ تنظیم زمان به صورت: حداقل_ماه-حداکثر_ماه-گام\n"
                "مثال: 1-12-1"
            )
        elif dyn_action == "discount":
            # مرحله اول: پرسیدن آستانه‌ی حجم
            context.user_data["plans_dyn_discount_phase"] = "threshold"
            prompt = (
                "🎁 تنظیم تخفیف حجمی\n"
                "ابتدا بنویس از چه حجمی به بالا تخفیف فعال شود (بر حسب گیگ).\n"
                "مثال: 50\n"
                "برای خاموش کردن کامل تخفیف، عدد 0 بفرست."
            )
        elif dyn_action == "discount_tiers":
            prompt = (
                "🎚 تنظیم تخفیف پله‌ای\n"
                "هر پله را با فرمت `حجم:درصد` وارد کن و پله‌ها را با کاما یا خط جدید جدا کن.\n"
                "مثال: 50:5, 100:10, 200:15\n"
                "یعنی: از ۵۰ گیگ ۵٪، از ۱۰۰ گیگ ۱۰٪ و از ۲۰۰ گیگ ۱۵٪ تخفیف.\n"
                "برای خاموش کردن تخفیف، عدد 0 بفرست.\n"
                "می‌توانی از `-` یا `=` هم به جای `:` استفاده کنی."
            )
        elif dyn_action == "discount_timer":
            prompt = (
                "⏱ تنظیم تایمر تخفیف حجمی ساده\n"
                "مدت زمان را به ساعت ارسال کنید (مثلاً 12 یا 24).\n"
                "برای اتمام تایمر و خاموش شدن خودکار تخفیف، عدد 0 بفرستید."
            )
        else:
            prompt = "لطفاً مقدار جدید را ارسال کنید:"

        await context.bot.send_message(chat_id, prompt, reply_markup=_cancel_kb())
        return

    # ----- دسته‌ها -----
    if action == "cats":
        await _send_categories_menu(server_id, chat_id, context, message=msg)
        return

    if action == "cat_add":
        context.user_data["state"] = PLANS_STATE_ADD_CAT_TITLE
        context.user_data["plans_server_id"] = server_id
        await msg.edit_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id,
            "➕ افزودن دسته جدید\nلطفاً عنوان دسته را وارد کنید:",
            reply_markup=_cancel_kb(),
        )
        return

    if action == "cat":
        if len(rest) < 2:
            await msg.edit_text("❌ شناسه دسته نامعتبر است.")
            return
        cid = int(rest[1])
        await _send_category_detail(server_id, cid, chat_id, context, message=msg)
        return

    if action == "cat_edit_title":
        cid = int(rest[1])
        context.user_data["state"] = PLANS_STATE_EDIT_CAT_TITLE
        context.user_data["plans_server_id"] = server_id
        context.user_data["plans_edit_cat_id"] = cid
        await msg.edit_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id,
            "📝 ویرایش عنوان دسته\nعنوان جدید را ارسال کنید:",
            reply_markup=_cancel_kb(),
        )
        return

    if action == "cat_edit_prio":
        cid = int(rest[1])
        context.user_data["state"] = PLANS_STATE_EDIT_CAT_PRIORITY
        context.user_data["plans_server_id"] = server_id
        context.user_data["plans_edit_cat_id"] = cid
        await msg.edit_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id,
            "🔢 لطفاً عدد اولویت این دسته را ارسال کنید (عدد کمتر = اولویت بالاتر):",
            reply_markup=_cancel_kb(),
        )
        return

    if action == "cat_del_menu":
        cats = plans_storage.get_plan_categories(server_id)
        if not cats:
            await msg.edit_text("هیچ دسته‌ای برای حذف وجود ندارد.")
            return
        rows = []
        for c in cats:
            cid = c["id"]
            title = c.get("title") or f"دسته #{cid}"
            rows.append(
                [
                    InlineKeyboardButton(
                        f"❌ {title}",
                        callback_data=f"plans:{server_id}:cat_del:{cid}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    "بازگشت🔙",
                    callback_data=f"plans:{server_id}:cats",
                )
            ]
        )
        kb = InlineKeyboardMarkup(rows)
        await msg.edit_text("یک دسته را برای حذف انتخاب کنید:", reply_markup=kb)
        return

    if action == "cat_del":
        cid = int(rest[1])
        ok = plans_storage.delete_plan_category(server_id, cid)
        if ok:
            await msg.edit_text("✅ دسته حذف شد (پلن‌هایش بدون دسته شدند).")
        else:
            await msg.edit_text("❌ حذف دسته ناموفق بود.")
        await _send_categories_menu(server_id, chat_id, context)
        return

    # ----- پلن‌ها -----
    if action == "plans":
        cid = int(rest[1])
        await _send_plans_list(server_id, cid, chat_id, context, message=msg)
        return

    if action == "plan":
        pid = int(rest[1])
        await _send_plan_detail(server_id, pid, chat_id, context, message=msg)
        return

    if action == "plan_add":
        cid = int(rest[1])
        context.user_data["state"] = PLANS_STATE_ADD_PLAN_TITLE
        context.user_data["plans_server_id"] = server_id
        context.user_data["plans_category_id"] = cid
        context.user_data["plans_new_plan"] = {}
        await msg.edit_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id,
            "➕ افزودن پلن جدید\nلطفاً عنوان پلن را ارسال کنید:",
            reply_markup=_cancel_kb(),
        )
        return

    if action == "plan_del_menu":
        cid = int(rest[1])
        plans = plans_storage.get_plans(server_id, category_id=cid)
        if not plans:
            await msg.edit_text("در این دسته هیچ پلنی برای حذف وجود ندارد.")
            return
        rows = []
        for p in plans:
            pid = p["id"]
            title = p.get("title") or f"پلن #{pid}"
            rows.append(
                [
                    InlineKeyboardButton(
                        f"🗑️ {title}",
                        callback_data=f"plans:{server_id}:plan_del:{pid}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    "بازگشت🔙",
                    callback_data=f"plans:{server_id}:plans:{cid}",
                )
            ]
        )
        kb = InlineKeyboardMarkup(rows)
        await msg.edit_text("یک پلن را برای حذف انتخاب کنید:", reply_markup=kb)
        return

    if action == "plan_del":
        pid = int(rest[1])
        plan = plans_storage.get_plan(server_id, pid)
        if not plan:
            await msg.edit_text("❌ پلن پیدا نشد.")
            return
        cat_id = plan.get("category_id")
        plans_storage.delete_plan(server_id, pid)
        await msg.edit_text("✅ پلن حذف شد.")
        if cat_id is not None:
            await _send_plans_list(server_id, int(cat_id), chat_id, context)
        return

    # اگر به اینجا رسید یعنی دکمه ناشناخته
    await msg.edit_text("❌ این دکمه هنوز پیاده‌سازی نشده است.")


# ===============================
#   Message handler برای استیت‌ها
# ===============================

async def handle_plans_message(
    state: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    این تابع زمانی صدا زده می‌شود که context.user_data['state'] یکی از
    استیت‌های مربوط به مدیریت پلن‌ها باشد و پیام متنی از ادمین برسد.
    """
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()

    # لغو
    if text in CANCEL_WORDS:
        cancel_server_id = context.user_data.get("plans_server_id")
        return_to_dynamic_menu = state == PLANS_STATE_EDIT_DYNAMIC_FIELD and cancel_server_id
        for key in (
            "state",
            "plans_server_id",
            "plans_category_id",
            "plans_new_plan",
            "plans_edit_cat_id",
            "plans_dyn_action",
            "plans_dyn_discount_phase",
            "plans_dyn_discount_threshold",
            "plans_new_cat_title",
        ):
            context.user_data.pop(key, None)
        await message.reply_text("❌ عملیات لغو شد.", reply_markup=_finish_reply_kb())
        if return_to_dynamic_menu:
            await _send_dynamic_settings_menu(int(cancel_server_id), message.chat_id, context)
        return

    server_id = context.user_data.get("plans_server_id")
    if not server_id:
        context.user_data.pop("state", None)
        await message.reply_text("❌ وضعیت مدیریت پلن‌ها نامشخص است.")
        return

    chat_id = message.chat_id

    # ----- دسته‌ها: افزودن یا ویرایش عنوان -----
    if state == PLANS_STATE_ADD_CAT_TITLE:
        # افزودن دسته جدید: عنوان را گرفته و برای اولویت به استیت بعد می‌رویم
        context.user_data["plans_new_cat_title"] = text
        context.user_data["state"] = PLANS_STATE_ADD_CAT_PRIORITY
        await message.reply_text(
            "🔢 حالا عدد اولویت این دسته را ارسال کنید (عدد کمتر = بالاتر):",
            reply_markup=_cancel_kb(),
        )
        return

    if state == PLANS_STATE_EDIT_CAT_TITLE:
        cat_id = context.user_data.get("plans_edit_cat_id")
        if not cat_id:
            await message.reply_text("❌ دسته برای ویرایش مشخص نیست.")
            return
        plans_storage.edit_plan_category(server_id, int(cat_id), title=text)
        await message.reply_text("✅ عنوان دسته بروزرسانی شد.", reply_markup=_finish_reply_kb())
        # برگشت به صفحه همان دسته
        await _send_category_detail(server_id, int(cat_id), chat_id, context)
        context.user_data.pop("plans_edit_cat_id", None)
        context.user_data.pop("state", None)
        return

    if state == PLANS_STATE_ADD_CAT_PRIORITY:
        try:
            prio = int(text)
        except ValueError:
            await message.reply_text(
                "❌ لطفاً یک عدد صحیح برای اولویت ارسال کنید.",
                reply_markup=_cancel_kb(),
            )
            return
        title = context.user_data.pop("plans_new_cat_title", "بدون عنوان")
        cat = plans_storage.add_plan_category(server_id, title, priority=prio)
        await message.reply_text(
            f"✅ دسته با موفقیت اضافه شد.\nعنوان: {cat['title']}\nاولویت: {cat['priority']}",
            reply_markup=_finish_reply_kb(),
        )
        # برگشت به لیست دسته‌ها
        await _send_categories_menu(server_id, chat_id, context)
        context.user_data.pop("state", None)
        return

    if state == PLANS_STATE_EDIT_CAT_PRIORITY:
        cat_id = context.user_data.get("plans_edit_cat_id")
        if not cat_id:
            await message.reply_text("❌ دسته برای ویرایش مشخص نیست.")
            return
        try:
            prio = int(text)
        except ValueError:
            await message.reply_text(
                "❌ لطفاً یک عدد صحیح برای اولویت ارسال کنید.",
                reply_markup=_cancel_kb(),
            )
            return
        plans_storage.edit_plan_category(server_id, int(cat_id), priority=prio)
        await message.reply_text("✅ اولویت دسته بروزرسانی شد.", reply_markup=_finish_reply_kb())
        # برگشت به صفحه همان دسته
        await _send_category_detail(server_id, int(cat_id), chat_id, context)
        context.user_data.pop("plans_edit_cat_id", None)
        context.user_data.pop("state", None)
        return

    # ----- افزودن پلن -----
    if state == PLANS_STATE_ADD_PLAN_TITLE:
        new_plan = context.user_data.get("plans_new_plan", {})
        new_plan["title"] = text
        context.user_data["plans_new_plan"] = new_plan
        context.user_data["state"] = PLANS_STATE_ADD_PLAN_PRICE
        await message.reply_text(
            "💰 قیمت پلن (تومان) را ارسال کنید:",
            reply_markup=_cancel_kb(),
        )
        return

    if state == PLANS_STATE_ADD_PLAN_PRICE:
        try:
            price = int(text.replace(",", ""))
        except ValueError:
            await message.reply_text(
                "❌ لطفاً قیمت را به صورت عددی ارسال کنید.",
                reply_markup=_cancel_kb(),
            )
            return
        new_plan = context.user_data.get("plans_new_plan", {})
        new_plan["price"] = price
        context.user_data["plans_new_plan"] = new_plan
        context.user_data["state"] = PLANS_STATE_ADD_PLAN_DAYS
        await message.reply_text(
            "⌛ مدت پلن را به روز ارسال کنید (مثال: 30):",
            reply_markup=_cancel_kb(),
        )
        return

    if state == PLANS_STATE_ADD_PLAN_DAYS:
        try:
            days = int(text)
        except ValueError:
            await message.reply_text(
                "❌ لطفاً مدت را به صورت عدد روز ارسال کنید.",
                reply_markup=_cancel_kb(),
            )
            return
        new_plan = context.user_data.get("plans_new_plan", {})
        new_plan["days"] = days
        context.user_data["plans_new_plan"] = new_plan
        context.user_data["state"] = PLANS_STATE_ADD_PLAN_GB
        await message.reply_text(
            "📊 حجم پلن را به گیگابایت ارسال کنید (برای نامحدود 0 بفرستید):",
            reply_markup=_cancel_kb(),
        )
        return

    if state == PLANS_STATE_ADD_PLAN_GB:
        try:
            gb = float(text.replace(",", "."))
        except ValueError:
            await message.reply_text(
                "❌ لطفاً حجم را به صورت عددی ارسال کنید.",
                reply_markup=_cancel_kb(),
            )
            return

        cat_id = context.user_data.get("plans_category_id")
        new_plan = context.user_data.pop("plans_new_plan", {})
        title = new_plan.get("title", "پلن جدید")
        price = new_plan.get("price", 0)
        days = new_plan.get("days", 0)

        plan = plans_storage.add_plan(
            server_id,
            category_id=int(cat_id) if cat_id is not None else None,
            title=title,
            price=price,
            days=days,
            gb=gb,
        )
        await message.reply_text(
            "✅ پلن با موفقیت اضافه شد.\n"
            f"عنوان: {plan['title']}\n"
            f"قیمت: {plan['price']:,} تومان\n"
            f"مدت: {plan['days']} روز\n"
            f"حجم: {plan['gb']} گیگ",
            reply_markup=_finish_reply_kb(),
        )

        # بعد از افزودن پلن، برگرد به لیست پلن‌های همان دسته
        if cat_id is not None:
            await _send_plans_list(server_id, int(cat_id), chat_id, context)

        context.user_data.pop("state", None)
        context.user_data.pop("plans_category_id", None)
        return

    # ----- تنظیمات پویا -----
    if state == PLANS_STATE_EDIT_DYNAMIC_FIELD:
        dyn_action = context.user_data.get("plans_dyn_action")

        if dyn_action == "discount_timer":
            try:
                hours = int(text)
            except ValueError:
                await message.reply_text(
                    "❌ لطفاً مدت زمان را به ساعت به صورت عددی ارسال کنید (مثلاً 12).",
                    reply_markup=_cancel_kb(),
                )
                return

            if hours <= 0:
                plans_storage.set_plan_dynamic_settings(
                    server_id,
                    discount_simple_enabled=False,
                    discount_simple_expire_at=0,
                )
                await message.reply_text(
                    "✅ تایمر تخفیف حذف شد و تخفیف حجمی ساده خاموش شد.",
                    reply_markup=_finish_reply_kb(),
                )
            else:
                expire_at = int(time.time()) + hours * 3600
                plans_storage.set_plan_dynamic_settings(
                    server_id,
                    discount_simple_enabled=True,
                    discount_simple_expire_at=expire_at,
                )
                await message.reply_text(
                    "✅ تایمر تخفیف حجمی ساده تنظیم شد.\n"
                    f"تخفیف به مدت {hours} ساعت (تا {datetime.fromtimestamp(expire_at).strftime('%Y-%m-%d %H:%M')}) فعال است "
                    "و پس از اتمام، به‌صورت خودکار خاموش می‌شود.",
                    reply_markup=_finish_reply_kb(),
                )

            await _send_discount_settings_menu(server_id, chat_id, context)
            context.user_data.pop("plans_dyn_action", None)
            context.user_data.pop("state", None)
            return

        if dyn_action == "discount_tiers":
            try:
                tiers = _parse_discount_tiers_text(text)
            except ValueError:
                await message.reply_text(
                    "❌ فرمت پله‌ها معتبر نیست. مثال درست: 50:5,100:10,200:15",
                    reply_markup=_cancel_kb(),
                )
                return

            plans_storage.set_plan_dynamic_settings(
                server_id,
                discount_tiers=tiers,
                discount_tiered_enabled=True,
            )
            if tiers:
                await message.reply_text(
                    f"✅ تخفیف پلاکانی ذخیره شد.\n{_format_discount_tiers(tiers)}",
                    reply_markup=_finish_reply_kb(),
                )
            else:
                await message.reply_text(
                    "✅ تخفیف حجمی غیرفعال شد.",
                    reply_markup=_finish_reply_kb(),
                )

            await _send_discount_settings_menu(server_id, chat_id, context)
            context.user_data.pop("plans_dyn_action", None)
            context.user_data.pop("state", None)
            return

        # --- تخفیف حجمی (دو مرحله‌ای، طبق ایده ساده) ---
        if dyn_action == "discount":
            phase = context.user_data.get("plans_dyn_discount_phase", "threshold")

            # مرحله اول: گرفتن آستانه‌ی حجم
            if phase == "threshold":
                try:
                    threshold = int(text.replace(",", ""))
                except ValueError:
                    await message.reply_text(
                        "❌ لطفاً عدد حجم را به صورت صحیح وارد کنید (مثلاً 50).",
                        reply_markup=_cancel_kb(),
                    )
                    return

                if threshold < 0:
                    threshold = 0

                context.user_data["plans_dyn_discount_threshold"] = threshold
                context.user_data["plans_dyn_discount_phase"] = "percent"

                await message.reply_text(
                    "الان درصد تخفیف را ارسال کن (مثلاً 25).\n"
                    "برای خاموش کردن کامل تخفیف 0 بفرست.",
                    reply_markup=_cancel_kb(),
                )
                return

            # مرحله دوم: گرفتن درصد تخفیف
            else:
                try:
                    percent = int(
                        text.replace("%", "").replace(",", "")
                    )
                except ValueError:
                    await message.reply_text(
                        "❌ لطفاً درصد تخفیف را به صورت عددی بفرست (مثلاً 25).",
                        reply_markup=_cancel_kb(),
                    )
                    return

                threshold = int(context.user_data.pop("plans_dyn_discount_threshold", 0))
                context.user_data.pop("plans_dyn_discount_phase", None)

                if percent <= 0 or threshold <= 0:
                    # خاموش کردن کامل تخفیف
                    plans_storage.set_plan_dynamic_settings(
                        server_id,
                        discount_step_gb=0,
                        discount_percent_step=0,
                        discount_percent_max=0,
                        discount_tiers=[],
                        discount_simple_enabled=False,
                        discount_simple_expire_at=0,
                    )
                    await message.reply_text(
                        "✅ تخفیف حجمی غیرفعال شد.",
                        reply_markup=_finish_reply_kb(),
                    )
                else:
                    plans_storage.set_plan_dynamic_settings(
                        server_id,
                        discount_step_gb=threshold,
                        discount_percent_step=percent,
                        discount_percent_max=percent,
                        discount_tiers=[],
                        discount_simple_enabled=True,
                        discount_simple_expire_at=0,
                    )
                    await message.reply_text(
                        f"✅ تخفیف ذخیره شد.\n"
                        f"از {threshold} گیگ به بالا، {percent}٪ تخفیف روی قیمت نهایی اعمال می‌شود.",
                        reply_markup=_finish_reply_kb(),
                    )

                # بعد از ذخیره، دوباره منوی مدیریت حرفه‌ای تخفیف‌ها را نمایش بده
                await _send_discount_settings_menu(server_id, chat_id, context)

                # پاک کردن state
                context.user_data.pop("plans_dyn_action", None)
                context.user_data.pop("state", None)
                return

        # --- بقیه تنظیمات پویا مثل قبل ---
        try:
            if dyn_action in ("price_per_gb", "price_per_month"):
                value = int(text.replace(",", ""))
                plans_storage.set_plan_dynamic_settings(server_id, **{dyn_action: value})

            elif dyn_action == "volume_range":
                parts = text.split("-")
                if len(parts) != 3:
                    raise ValueError
                min_gb, max_gb, step_gb = map(int, parts)
                plans_storage.set_plan_dynamic_settings(
                    server_id,
                    min_gb=min_gb,
                    max_gb=max_gb,
                    step_gb=step_gb,
                )

            elif dyn_action == "time_range":
                parts = text.split("-")
                if len(parts) != 3:
                    raise ValueError
                min_m, max_m, step_m = map(int, parts)
                plans_storage.set_plan_dynamic_settings(
                    server_id,
                    min_month=min_m,
                    max_month=max_m,
                    step_month=step_m,
                )

            else:
                await message.reply_text("❌ نوع تنظیم پویا نامعتبر است.")
                return

        except ValueError:
            await message.reply_text(
                "❌ مقدار ارسال‌شده معتبر نیست. لطفاً طبق فرمت خواسته‌شده ارسال کنید.",
                reply_markup=_cancel_kb(),
            )
            return

        await message.reply_text(
            "✅ تنظیمات با موفقیت ذخیره شد.",
            reply_markup=_finish_reply_kb(),
        )

        # بعد از ذخیره سایر مقادیر پویا، دوباره منوی تنظیم پلن پویا را بفرست
        await _send_dynamic_settings_menu(server_id, chat_id, context)

        context.user_data.pop("plans_dyn_action", None)
        context.user_data.pop("state", None)
        return

    # اگر استیت ناشناخته بود
    await message.reply_text("❌ وضعیت نامعتبر است.")
    context.user_data.pop("state", None)
