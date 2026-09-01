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
from Shared import database, plans_storage, userbot_db
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from Shared.tg_button_styles import keyboard_button as KeyboardButton

logger = logging.getLogger(__name__)

# ---- چندزبانه: زبان ادمین + شورت‌کات ترجمه ----
from Shared import i18n as _i18n


def _admin_bot_lang() -> str:
    try:
        return userbot_db.get_admin_language()
    except Exception:
        return "fa"


def _T(lang: str, key: str, **kw) -> str:
    return _i18n.t(key, lang, **kw)

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
    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)
    return ReplyKeyboardMarkup([[KeyboardButton(_t("btn_cancel") + "❌")]], resize_keyboard=True)


def _finish_reply_kb() -> ReplyKeyboardMarkup:
    return admin_main_keyboard()


def _format_discount_tiers(tiers: List[Dict[str, int]]) -> str:
    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)
    normalized = plans_storage.normalize_discount_tiers(tiers)
    if not normalized:
        return _t("adm_pl_discount_off")
    return " | ".join(_t("adm_pl_tier_item", n=item['gb'], v=item['percent']) for item in normalized)


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
    server_title = server.get("title") if server else _T(_admin_bot_lang(), "adm_pl_server_fallback", n=server_id)

    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)

    mode = plans_storage.get_plan_display_mode(server_id)
    if mode == PLAN_MODE_FIXED:
        mode_txt = _t("adm_pl_mode_fixed")
    elif mode == PLAN_MODE_DYNAMIC:
        mode_txt = _t("adm_pl_mode_dynamic")
    elif mode == PLAN_MODE_MIXED:
        mode_txt = _t("adm_pl_mode_mixed")
    else:
        mode_txt = _t("adm_pl_mode_unknown")

    text = (
        _t("adm_pl_root_title", v=server_title) + "\n"
        "━━━━━━━━━━━━━━\n"
        +         _t("adm_pl_root_mode", v=mode_txt) + "\n\n"
        + _t("adm_pl_choose_option")
    )

    rows: List[List[InlineKeyboardButton]] = []

    # فقط اگر حالت ثابت یا ترکیبی باشد، لیست دسته‌های پلن را نشان بده
    if mode in (PLAN_MODE_FIXED, PLAN_MODE_MIXED):
        rows.append(
            [
                InlineKeyboardButton(
                    _t("adm_pl_cats_btn"),
                    callback_data=f"plans:{server_id}:cats",
                )
            ]
        )

    # همیشه تنظیمات پلن‌ها
    rows.append(
        [
            InlineKeyboardButton(
                _t("adm_pl_settings_btn"),
                callback_data=f"plans:{server_id}:settings",
            )
        ]
    )

    # مدیریت حرفه‌ای تخفیف‌ها (فقط در حالت پویا/ترکیبی که معنادار است)
    if mode in (PLAN_MODE_DYNAMIC, PLAN_MODE_MIXED):
        rows.append(
            [
                InlineKeyboardButton(
                    _t("adm_pl_pro_discount_btn"),
                    callback_data=f"plans:{server_id}:dyn_discount_settings",
                )
            ]
        )

    # دکمه بازگشت
    rows.append(
        [
            InlineKeyboardButton(
                _t("btn_back") + "🔙",
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

    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)

    if not cats:
        text = (
            _t("adm_pl_cats_btn") + "\n"
            + _t("adm_pl_cats_empty") + "\n"
            + _t("adm_pl_cats_empty_hint")
        )
        rows: List[List[InlineKeyboardButton]] = []
    else:
        lines: List[str] = [_t("adm_pl_cats_btn"), ""]
        rows = []
        for c in cats:
            cid = c["id"]
            title = c.get("title") or _t("adm_pl_cat_fallback", n=cid)
            prio = c.get("priority", 0)
            lines.append(_t("adm_pl_cat_line", v=title, n=prio))
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
                _t("adm_pl_cat_add_btn"),
                callback_data=f"plans:{server_id}:cat_add",
            )
        ]
    )
    if cats:
        rows.append(
            [
                InlineKeyboardButton(
                    _t("adm_pl_cat_del_btn"),
                    callback_data=f"plans:{server_id}:cat_del_menu",
                )
            ]
        )

    # فقط در حالت ترکیبی، دکمه‌ی مستقیم برای تنظیم پلن پویا نشان بده
    if mode == PLAN_MODE_MIXED:
        rows.append(
            [
                InlineKeyboardButton(
                    _t("adm_pl_dyn_values_btn"),
                    callback_data=f"plans:{server_id}:dyn_settings",
                )
            ]
        )

    # تنظیمات کلی پلن‌ها
    rows.append(
        [
            InlineKeyboardButton(
                _t("adm_pl_settings_btn"),
                callback_data=f"plans:{server_id}:settings",
            )
        ]
    )

    # بازگشت
    rows.append(
        [
            InlineKeyboardButton(
                _t("btn_back") + "🔙",
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
    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)

    cats = plans_storage.get_plan_categories(server_id)
    cat = next((c for c in cats if int(c["id"]) == int(category_id)), None)
    if not cat:
        txt = _t("adm_pl_cat_notfound")
        if message:
            await message.edit_text(txt)
        else:
            await context.bot.send_message(chat_id, txt)
        return

    title = cat.get("title") or _t("adm_pl_cat_fallback", n=category_id)
    prio = cat.get("priority", 0)
    plans = plans_storage.get_plans(server_id, category_id=category_id)

    text = (
        _t("adm_pl_cat_detail_title", v=title) + "\n"
        "━━━━━━━━━━━━━━\n"
        + _t("adm_pl_cat_prio_line", n=prio) + "\n"
        + _t("adm_pl_cat_count_line", n=len(plans)) + "\n\n"
        + _t("adm_pl_cat_manage_hint")
    )

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _t("adm_pl_plans_list_btn"),
                    callback_data=f"plans:{server_id}:plans:{category_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("adm_pl_cat_edit_title_btn"),
                    callback_data=f"plans:{server_id}:cat_edit_title:{category_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("adm_pl_cat_edit_prio_btn"),
                    callback_data=f"plans:{server_id}:cat_edit_prio:{category_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("btn_back") + "🔙",
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
    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)

    plans = plans_storage.get_plans(server_id, category_id=category_id)

    if not plans:
        text = (
            _t("adm_pl_list_title") + "\n"
            + _t("adm_pl_list_empty")
        )
        rows: List[List[InlineKeyboardButton]] = []
    else:
        lines: List[str] = [
            _t("adm_pl_list_title"),
            "",
            _t("adm_pl_list_cols"),
            _t("adm_pl_list_header"),
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        rows: List[List[InlineKeyboardButton]] = []

        for idx, p in enumerate(plans, start=1):
            pid = p["id"]
            title = p.get("title") or _t("adm_pl_fallback", n=pid)
            price = p.get("price", 0)
            days = p.get("days", 0)
            gb = p.get("gb", 0)

            price_txt = _t("adm_pl_price_val", v=f"{price:,}")
            days_txt = _t("adm_pl_days_val", n=days)
            gb_txt = _t("adm_pl_gb_val", n=gb)

            # متن ردیف
            lines.append(
                _t("adm_pl_list_row", n=idx, t=title, p=price_txt, d=days_txt, g=gb_txt)
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
                _t("adm_pl_add_btn"),
                callback_data=f"plans:{server_id}:plan_add:{category_id}",
            )
        ]
    )
    if plans:
        rows.append(
            [
                InlineKeyboardButton(
                    _t("adm_pl_del_btn"),
                    callback_data=f"plans:{server_id}:plan_del_menu:{category_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                _t("btn_back") + "🔙",
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
    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)

    plan = plans_storage.get_plan(server_id, plan_id)
    if not plan:
        txt = _t("adm_pl_plan_notfound")
        if message:
            await message.edit_text(txt)
        else:
            await context.bot.send_message(chat_id, txt)
        return

    title = plan.get("title") or _t("adm_pl_fallback", n=plan_id)
    price = plan.get("price", 0)
    days = plan.get("days", 0)
    gb = plan.get("gb", 0)
    cat_id = plan.get("category_id")

    text = (
        _t("adm_pl_plan_title_line", v=title) + "\n"
        "━━━━━━━━━━━━━━\n"
        + _t("adm_pl_plan_price_line", v=f"{price:,}") + "\n"
        + _t("adm_pl_plan_days_line", n=days) + "\n"
        + _t("adm_pl_plan_gb_line", n=gb) + "\n"
    )

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _t("adm_pl_del_this_btn"),
                    callback_data=f"plans:{server_id}:plan_del:{plan_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("btn_back") + "🔙",
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
    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)

    text = _t("adm_pl_settings_btn") + "\n\n" + _t("adm_pl_choose_option")
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _t("adm_pl_display_mode_btn"),
                    callback_data=f"plans:{server_id}:mode_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("adm_pl_dyn_set_btn"),
                    callback_data=f"plans:{server_id}:dyn_settings",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("btn_back") + "🔙",
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

    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)

    def _mode_label(value: str, title: str) -> str:
        mark = "✅" if mode == value else "❌"
        return f"{mark} {title}"

    text = _t("adm_pl_settings_btn") + "\n\n" + _t("adm_pl_mode_choose")

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _mode_label(PLAN_MODE_FIXED, _t("adm_pl_mode_lbl_fixed")),
                    callback_data=f"plans:{server_id}:mode_fixed",
                ),
                InlineKeyboardButton(
                    _mode_label(PLAN_MODE_DYNAMIC, _t("adm_pl_mode_lbl_dynamic")),
                    callback_data=f"plans:{server_id}:mode_dynamic",
                ),
                InlineKeyboardButton(
                    _mode_label(PLAN_MODE_MIXED, _t("adm_pl_mode_lbl_mixed")),
                    callback_data=f"plans:{server_id}:mode_mixed",
                ),
            ],
            [
                InlineKeyboardButton(
                    _t("btn_back") + "🔙",
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
    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)

    s = plans_storage.get_plan_dynamic_settings(server_id)
    discount_tiers = plans_storage.normalize_discount_tiers(s.get("discount_tiers", []))
    simple_enabled = _is_simple_discount_enabled(s)
    tiered_enabled = _is_tiered_discount_enabled(s)
    if discount_tiers:
        discount_line = _t("adm_pl_tiered_line", v=_format_discount_tiers(discount_tiers))
    else:
        discount_line = _t(
            "adm_pl_simple_line",
            a=s['discount_step_gb'],
            b=s['discount_percent_step'],
            c=s['discount_percent_max'],
        )

    lines = [
        _t("adm_pl_dyn_title"),
        "",
        _t("adm_pl_price_gb_line", v=f"{s['price_per_gb']:,}"),
        _t("adm_pl_price_month_line", v=f"{s['price_per_month']:,}"),
        "",
        _t("adm_pl_volume_line", a=s['min_gb'], b=s['max_gb'], c=s['step_gb']),
        _t("adm_pl_time_line", a=s['min_month'], b=s['max_month'], c=s['step_month']),
        "",
        discount_line,
        "",
        _t("adm_pl_dyn_change_hint"),
        _t("adm_pl_dyn_discount_hint"),
    ]

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _t("adm_pl_price_gb_btn"),
                    callback_data=f"plans:{server_id}:dyn_edit:price_per_gb",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("adm_pl_price_month_btn"),
                    callback_data=f"plans:{server_id}:dyn_edit:price_per_month",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("adm_pl_volume_btn"),
                    callback_data=f"plans:{server_id}:dyn_edit:volume_range",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("adm_pl_time_btn"),
                    callback_data=f"plans:{server_id}:dyn_edit:time_range",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("btn_back") + "🔙",
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
    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)

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
            parts.append(_t("adm_pl_days_val", n=days))
        if hours > 0:
            parts.append(_t("adm_pl_hours_val", n=hours))
        if minutes > 0:
            parts.append(_t("adm_pl_minutes_val", n=minutes))
        remaining_txt = _t("adm_pl_and").join(parts) if parts else _t("adm_pl_lt_minute")
        timer_line = _t(
            "adm_pl_timer_line",
            v=remaining_txt,
            e=datetime.fromtimestamp(expire_at).strftime('%Y-%m-%d %H:%M'),
        )

    else:
        timer_line = ""

    lines = [
        _t("adm_pl_pro_discount_title"),
        "",
        _t("adm_pl_simple_state", v=(_t("adm_pl_on") if simple_enabled else _t("adm_pl_off"))),
        _t("adm_pl_tiered_line", v=(_t("adm_pl_on") if tiered_enabled else _t("adm_pl_off"))),
        "",
        _t("adm_pl_discount_hint"),
    ]
    if timer_line:
        lines.append(timer_line)

    if simple_enabled:
        lines.append(
            _t("adm_pl_simple_detail", a=s['discount_step_gb'], b=s['discount_percent_step'], c=s['discount_percent_max'])
        )
    elif int(s.get('discount_step_gb', 0)) > 0 and int(s.get('discount_percent_step', 0)) > 0:
        lines.append(
            _t("adm_pl_simple_saved", a=s['discount_step_gb'], b=s['discount_percent_step'], c=s['discount_percent_max'])
        )

    if tiered_enabled:
        lines.append(_t("adm_pl_tiered_detail", v=_format_discount_tiers(discount_tiers)))
    elif discount_tiers:
        lines.append(
            _t("adm_pl_tiered_saved", v=_format_discount_tiers(discount_tiers))
        )

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    (_t("adm_pl_turn_off") if simple_enabled else _t("adm_pl_turn_on")) + " " + _t("adm_pl_simple_short"),
                    callback_data=f"plans:{server_id}:dyn_toggle:discount",
                    style="danger" if simple_enabled else "success",
                )
            ],
            [
                InlineKeyboardButton(
                    (_t("adm_pl_turn_off") if tiered_enabled else _t("adm_pl_turn_on")) + " " + _t("adm_pl_tiered_short"),
                    callback_data=f"plans:{server_id}:dyn_toggle:discount_tiers",
                    style="danger" if tiered_enabled else "success",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("adm_pl_edit_simple_btn"),
                    callback_data=f"plans:{server_id}:dyn_edit:discount",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("adm_pl_edit_tiered_btn"),
                    callback_data=f"plans:{server_id}:dyn_edit:discount_tiers",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("adm_pl_timer_btn"),
                    callback_data=f"plans:{server_id}:dyn_edit:discount_timer",
                )
            ],
            [
                InlineKeyboardButton(
                    _t("btn_back") + "🔙",
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

    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)

    data = query.data
    msg = query.message
    chat_id = msg.chat_id
    parts = data.split(":")

    # data مثل plans:SERVER_ID:...
    if len(parts) < 3:
        await query.answer(_t("adm_pl_err_invalid_data"))
        return

    _, sid_str, *rest = parts
    try:
        server_id = int(sid_str)
    except ValueError:
        await query.answer(_t("adm_pl_err_invalid_server"))
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
                _t("adm_pl_no_tiers_warn"),
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
            prompt = _t("adm_pl_prompt_price_gb")
        elif dyn_action == "price_per_month":
            prompt = _t("adm_pl_prompt_price_month")
        elif dyn_action == "volume_range":
            prompt = _t("adm_pl_prompt_volume")
        elif dyn_action == "time_range":
            prompt = _t("adm_pl_prompt_time")
        elif dyn_action == "discount":
            # مرحله اول: پرسیدن آستانه‌ی حجم
            context.user_data["plans_dyn_discount_phase"] = "threshold"
            prompt = _t("adm_pl_prompt_discount")
        elif dyn_action == "discount_tiers":
            prompt = _t("adm_pl_prompt_tiers")
        elif dyn_action == "discount_timer":
            prompt = _t("adm_pl_prompt_timer")
        else:
            prompt = _t("adm_pl_prompt_value")

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
            _t("adm_pl_prompt_cat_title"),
            reply_markup=_cancel_kb(),
        )
        return

    if action == "cat":
        if len(rest) < 2:
            await msg.edit_text(_t("adm_pl_err_invalid_cat"))
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
            _t("adm_pl_prompt_cat_edit_title"),
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
            _t("adm_pl_prompt_cat_prio"),
            reply_markup=_cancel_kb(),
        )
        return

    if action == "cat_del_menu":
        cats = plans_storage.get_plan_categories(server_id)
        if not cats:
            await msg.edit_text(_t("adm_pl_no_cats_del"))
            return
        rows = []
        for c in cats:
            cid = c["id"]
            title = c.get("title") or _t("adm_pl_cat_fallback", n=cid)
            rows.append(
                [
                    InlineKeyboardButton(
                        "❌ " + title,
                        callback_data=f"plans:{server_id}:cat_del:{cid}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    _t("btn_back") + "🔙",
                    callback_data=f"plans:{server_id}:cats",
                )
            ]
        )
        kb = InlineKeyboardMarkup(rows)
        await msg.edit_text(_t("adm_pl_select_cat_del"), reply_markup=kb)
        return

    if action == "cat_del":
        cid = int(rest[1])
        ok = plans_storage.delete_plan_category(server_id, cid)
        if ok:
            await msg.edit_text(_t("adm_pl_cat_deleted"))
        else:
            await msg.edit_text(_t("adm_pl_cat_del_failed"))
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
            _t("adm_pl_prompt_plan_title"),
            reply_markup=_cancel_kb(),
        )
        return

    if action == "plan_del_menu":
        cid = int(rest[1])
        plans = plans_storage.get_plans(server_id, category_id=cid)
        if not plans:
            await msg.edit_text(_t("adm_pl_no_plans_del"))
            return
        rows = []
        for p in plans:
            pid = p["id"]
            title = p.get("title") or _t("adm_pl_fallback", n=pid)
            rows.append(
                [
                    InlineKeyboardButton(
                        "🗑️ " + title,
                        callback_data=f"plans:{server_id}:plan_del:{pid}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    _t("btn_back") + "🔙",
                    callback_data=f"plans:{server_id}:plans:{cid}",
                )
            ]
        )
        kb = InlineKeyboardMarkup(rows)
        await msg.edit_text(_t("adm_pl_select_plan_del"), reply_markup=kb)
        return

    if action == "plan_del":
        pid = int(rest[1])
        plan = plans_storage.get_plan(server_id, pid)
        if not plan:
            await msg.edit_text(_t("adm_pl_err_plan_notfound"))
            return
        cat_id = plan.get("category_id")
        plans_storage.delete_plan(server_id, pid)
        await msg.edit_text(_t("adm_pl_plan_deleted"))
        if cat_id is not None:
            await _send_plans_list(server_id, int(cat_id), chat_id, context)
        return

    # اگر به اینجا رسید یعنی دکمه ناشناخته
    await msg.edit_text(_t("adm_pl_btn_not_impl"))


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

    _lg = _admin_bot_lang()
    _t = lambda k, **kw: _T(_lg, k, **kw)

    text = (message.text or "").strip()

    # لغو
    if text in CANCEL_WORDS or text == (_t("btn_cancel") + "❌") or text == _t("btn_cancel"):
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
        await message.reply_text(_t("adm_pl_cancelled"), reply_markup=_finish_reply_kb())
        if return_to_dynamic_menu:
            await _send_dynamic_settings_menu(int(cancel_server_id), message.chat_id, context)
        return

    server_id = context.user_data.get("plans_server_id")
    if not server_id:
        context.user_data.pop("state", None)
        await message.reply_text(_t("adm_pl_err_state_unknown"))
        return

    chat_id = message.chat_id

    # ----- دسته‌ها: افزودن یا ویرایش عنوان -----
    if state == PLANS_STATE_ADD_CAT_TITLE:
        # افزودن دسته جدید: عنوان را گرفته و برای اولویت به استیت بعد می‌رویم
        context.user_data["plans_new_cat_title"] = text
        context.user_data["state"] = PLANS_STATE_ADD_CAT_PRIORITY
        await message.reply_text(
            _t("adm_pl_prompt_cat_prio2"),
            reply_markup=_cancel_kb(),
        )
        return

    if state == PLANS_STATE_EDIT_CAT_TITLE:
        cat_id = context.user_data.get("plans_edit_cat_id")
        if not cat_id:
            await message.reply_text(_t("adm_pl_err_cat_unknown"))
            return
        plans_storage.edit_plan_category(server_id, int(cat_id), title=text)
        await message.reply_text(_t("adm_pl_cat_title_updated"), reply_markup=_finish_reply_kb())
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
                _t("adm_pl_err_not_int"),
                reply_markup=_cancel_kb(),
            )
            return
        title = context.user_data.pop("plans_new_cat_title", _t("adm_pl_unnamed"))
        cat = plans_storage.add_plan_category(server_id, title, priority=prio)
        await message.reply_text(
            _t("adm_pl_cat_added", t=cat['title'], n=cat['priority']),
            reply_markup=_finish_reply_kb(),
        )
        # برگشت به لیست دسته‌ها
        await _send_categories_menu(server_id, chat_id, context)
        context.user_data.pop("state", None)
        return

    if state == PLANS_STATE_EDIT_CAT_PRIORITY:
        cat_id = context.user_data.get("plans_edit_cat_id")
        if not cat_id:
            await message.reply_text(_t("adm_pl_err_cat_unknown"))
            return
        try:
            prio = int(text)
        except ValueError:
            await message.reply_text(
                _t("adm_pl_err_not_int"),
                reply_markup=_cancel_kb(),
            )
            return
        plans_storage.edit_plan_category(server_id, int(cat_id), priority=prio)
        await message.reply_text(_t("adm_pl_cat_prio_updated"), reply_markup=_finish_reply_kb())
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
            _t("adm_pl_prompt_plan_price"),
            reply_markup=_cancel_kb(),
        )
        return

    if state == PLANS_STATE_ADD_PLAN_PRICE:
        try:
            price = int(text.replace(",", ""))
        except ValueError:
            await message.reply_text(
                _t("adm_pl_err_not_numeric"),
                reply_markup=_cancel_kb(),
            )
            return
        new_plan = context.user_data.get("plans_new_plan", {})
        new_plan["price"] = price
        context.user_data["plans_new_plan"] = new_plan
        context.user_data["state"] = PLANS_STATE_ADD_PLAN_DAYS
        await message.reply_text(
            _t("adm_pl_prompt_plan_days"),
            reply_markup=_cancel_kb(),
        )
        return

    if state == PLANS_STATE_ADD_PLAN_DAYS:
        try:
            days = int(text)
        except ValueError:
            await message.reply_text(
                _t("adm_pl_err_not_days"),
                reply_markup=_cancel_kb(),
            )
            return
        new_plan = context.user_data.get("plans_new_plan", {})
        new_plan["days"] = days
        context.user_data["plans_new_plan"] = new_plan
        context.user_data["state"] = PLANS_STATE_ADD_PLAN_GB
        await message.reply_text(
            _t("adm_pl_prompt_plan_gb"),
            reply_markup=_cancel_kb(),
        )
        return

    if state == PLANS_STATE_ADD_PLAN_GB:
        try:
            gb = float(text.replace(",", "."))
        except ValueError:
            await message.reply_text(
                _t("adm_pl_err_not_gb"),
                reply_markup=_cancel_kb(),
            )
            return

        cat_id = context.user_data.get("plans_category_id")
        new_plan = context.user_data.pop("plans_new_plan", {})
        title = new_plan.get("title", _t("adm_pl_new_plan_default"))
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
            _t("adm_pl_plan_added",
               t=plan['title'],
               p=f"{plan['price']:,}",
               d=plan['days'],
               g=plan['gb']),
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
                    _t("adm_pl_err_not_hours"),
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
                    _t("adm_pl_timer_removed"),
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
                    _t("adm_pl_timer_set",
                       n=hours,
                       e=datetime.fromtimestamp(expire_at).strftime('%Y-%m-%d %H:%M')),
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
                    _t("adm_pl_err_bad_tiers"),
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
                    _t("adm_pl_tiered_saved_ok", v=_format_discount_tiers(tiers)),
                    reply_markup=_finish_reply_kb(),
                )
            else:
                await message.reply_text(
                    _t("adm_pl_discount_disabled"),
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
                        _t("adm_pl_err_not_gb_int"),
                        reply_markup=_cancel_kb(),
                    )
                    return

                if threshold < 0:
                    threshold = 0

                context.user_data["plans_dyn_discount_threshold"] = threshold
                context.user_data["plans_dyn_discount_phase"] = "percent"

                await message.reply_text(
                    _t("adm_pl_prompt_percent"),
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
                        _t("adm_pl_err_not_percent"),
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
                        _t("adm_pl_discount_disabled"),
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
                        _t("adm_pl_discount_saved_ok", a=threshold, b=percent),
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
                await message.reply_text(_t("adm_pl_err_bad_dyn_type"))
                return

        except ValueError:
            await message.reply_text(
                _t("adm_pl_err_invalid_value"),
                reply_markup=_cancel_kb(),
            )
            return

        await message.reply_text(
            _t("adm_pl_saved_ok"),
            reply_markup=_finish_reply_kb(),
        )

        # بعد از ذخیره سایر مقادیر پویا، دوباره منوی تنظیم پلن پویا را بفرست
        await _send_dynamic_settings_menu(server_id, chat_id, context)

        context.user_data.pop("plans_dyn_action", None)
        context.user_data.pop("state", None)
        return

    # اگر استیت ناشناخته بود
    await message.reply_text(_t("adm_pl_err_invalid_state"))
    context.user_data.pop("state", None)
