import logging
import time
from datetime import datetime
from typing import Dict, Any

from telegram import Update
from telegram.ext import ContextTypes

from AgentBot.constants import (
    PLANS_MODE, PLANS_DYNAMIC_SETTINGS, PLANS_BACK, MENU_MAIN,
    UD_STATE, UD_DYN_FIELD, STATE_DYNAMIC_SETTINGS, STATE_DYN_EDIT_FIELD,
    STATE_FIXED_ADD_CAT_TITLE, STATE_FIXED_EDIT_CAT_TITLE,
    STATE_FIXED_ADD_PLAN_TITLE, STATE_FIXED_ADD_PLAN_PRICE,
    STATE_FIXED_ADD_PLAN_DAYS, STATE_FIXED_ADD_PLAN_GB,
    UD_DYN_DISCOUNT_PHASE, UD_DYN_DISCOUNT_THRESHOLD,
)
from AgentBot.handlers.base import get_agent_id
from AgentBot.keyboards import (
    agent_lang,
    _ikb, IButton, BTN_BACK,
    plans_menu_keyboard, plans_mode_keyboard, dyn_settings_keyboard,
    discount_settings_keyboard,
    plans_cats_keyboard, plans_cat_del_keyboard,
    plans_cat_detail_keyboard, plans_cat_del_confirm_keyboard,
    plans_plans_keyboard, plans_plan_del_keyboard,
    back_keyboard, cancel_keyboard, main_menu_keyboard,
)
from AgentBot.utils.helpers import _fmt_toman, _normalize_digits
from AgentBot.database import (
    get_setting, set_setting,
    get_fixed_categories, set_fixed_categories,
    add_fixed_category, edit_fixed_category, delete_fixed_category,
    get_fixed_plans, get_fixed_plan, add_fixed_plan, delete_fixed_plan,
)
from Shared import plans_storage
from Shared import i18n

logger = logging.getLogger(__name__)

DISCOUNT_DEFAULTS = {
    "discount_simple_enabled": False,
    "discount_step_gb": 50,
    "discount_percent_step": 5,
    "discount_percent_max": 50,
    "discount_tiered_enabled": False,
    "discount_tiers": [],
    "discount_simple_expire_at": 0,
}


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent_id = get_agent_id(context)
    current_mode = get_setting(agent_id, "plan_display_mode", "dynamic")
    kb = plans_menu_keyboard(current_mode, lang=agent_lang(context))
    from Shared import i18n as _i18n
    text = _i18n.t("ag_plans_title", agent_lang(context))
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


def _get_discount_settings(agent_id: int) -> Dict[str, Any]:
    settings = get_setting(agent_id, "dynamic_plan_settings", {}) or {}
    if not isinstance(settings, dict):
        settings = {}
    for k, v in DISCOUNT_DEFAULTS.items():
        settings.setdefault(k, v)
    settings["discount_tiers"] = plans_storage.normalize_discount_tiers(settings.get("discount_tiers", []))
    return settings


def _set_discount_settings(agent_id: int, **kwargs: Any) -> None:
    settings = get_setting(agent_id, "dynamic_plan_settings", {}) or {}
    if not isinstance(settings, dict):
        settings = {}
    for k, v in kwargs.items():
        if k not in DISCOUNT_DEFAULTS:
            continue
        if isinstance(DISCOUNT_DEFAULTS[k], list):
            settings[k] = plans_storage.normalize_discount_tiers(v)
        elif isinstance(DISCOUNT_DEFAULTS[k], bool):
            settings[k] = str(v).strip().lower() in {"1", "true", "yes", "on"} if not isinstance(v, bool) else v
        else:
            settings[k] = int(v)
    set_setting(agent_id, "dynamic_plan_settings", settings)


def _discount_timer_line(settings: Dict[str, Any]) -> str:
    _lg = "fa"
    expire_at = settings.get("discount_simple_expire_at") or 0
    try:
        expire_at = float(expire_at)
    except (TypeError, ValueError):
        expire_at = 0
    if expire_at <= 0 or time.time() >= expire_at:
        return ""
    remaining = int(expire_at - time.time())
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    minutes = (remaining % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days}{i18n.t(' روز', _lg)}")
    if hours > 0:
        parts.append(f"{hours}{i18n.t(' ساعت', _lg)}")
    if minutes > 0:
        parts.append(f"{minutes}{i18n.t(' دقیقه', _lg)}")
    remaining_txt = i18n.t(' و ', _lg).join(parts) if parts else i18n.t('کمتر از یک دقیقه', _lg)
    return (
        f"{i18n.t('⏱ تایمر تخفیف حجمی ساده: ', _lg)}{remaining_txt}{i18n.t(' مانده (پایان: ', _lg)}{datetime.fromtimestamp(expire_at).strftime('%Y-%m-%d %H:%M')})"
    )


async def _render_discount_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    agent_id = get_agent_id(context)
    settings = _get_discount_settings(agent_id)

    # اگر تایمر منقضی شده، خودکار غیرفعال کن
    expire_at = settings.get("discount_simple_expire_at") or 0
    try:
        expire_at = float(expire_at)
    except (TypeError, ValueError):
        expire_at = 0
    if expire_at > 0 and time.time() >= expire_at:
        _set_discount_settings(
            agent_id,
            discount_simple_enabled=False,
            discount_simple_expire_at=0,
        )
        settings = _get_discount_settings(agent_id)

    simple_enabled = plans_storage.is_simple_discount_enabled(settings)
    tiered_enabled = plans_storage.is_tiered_discount_enabled(settings)
    timer_line = _discount_timer_line(settings)

    lines = [
        i18n.t('🎛 <b>مدیریت حرفه‌ای تخفیف‌ها</b>', _lg),
        "",
        f"{i18n.t('🎁 تخفیف حجمی ساده: ', _lg)}{i18n.t('state_on', _lg) if simple_enabled else i18n.t('state_off', _lg)}",
        f"{i18n.t('🎚 تخفیف پلاکانی: ', _lg)}{i18n.t('state_on', _lg) if tiered_enabled else i18n.t('state_off', _lg)}",
        "",
        i18n.t('برای تغییر وضعیت هر نوع تخفیف روی دکمه‌ی همان نوع بزنید، یا از دکمه‌های ویرایش برای تنظیم مقادیر استفاده کنید.', _lg),
    ]
    if timer_line:
        lines.append(timer_line)

    if simple_enabled:
        lines.append(
            f"{i18n.t('• تخفیف حجمی ساده: از ', _lg)}{settings['discount_step_gb']}{i18n.t(' گیگ به بالا، ', _lg)}{settings['discount_percent_step']}{i18n.t('٪ تا سقف ', _lg)}{settings['discount_percent_max']}{i18n.t('٪', _lg)}"
        )
    elif int(settings.get('discount_step_gb', 0)) > 0 and int(settings.get('discount_percent_step', 0)) > 0:
        lines.append(
            f"{i18n.t('• تنظیمات ذخیره‌شده تخفیف حجمی ساده: از ', _lg)}{settings['discount_step_gb']}{i18n.t(' گیگ به بالا، ', _lg)}{settings['discount_percent_step']}{i18n.t('٪ تا سقف ', _lg)}{settings['discount_percent_max']}{i18n.t('٪ (غیرفعال)', _lg)}"
        )

    if tiered_enabled:
        lines.append(f"{i18n.t('• پله‌های تخفیف پلاکانی: ', _lg)}{plans_storage.format_discount_tiers(settings.get('discount_tiers', []))}")
    elif settings.get("discount_tiers"):
        lines.append(
            f"{i18n.t('• پله‌های تخفیف پلاکانی ذخیره‌شده: ', _lg)}{plans_storage.format_discount_tiers(settings.get('discount_tiers', []))}{i18n.t(' (غیرفعال)', _lg)}"
        )

    text = "\n".join(lines)
    if query:
        try:
            await query.edit_message_text(text, reply_markup=discount_settings_keyboard(simple_enabled, tiered_enabled), parse_mode="HTML")
            return
        except Exception:
            pass
    try:
        await update.message.reply_text(text, reply_markup=discount_settings_keyboard(simple_enabled, tiered_enabled), parse_mode="HTML")
    except Exception:
        pass


async def _roleme_discount_menu(context: ContextTypes.DEFAULT_TYPE, update: Update, agent_id: int) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    settings = _get_discount_settings(agent_id)

    expire_at = settings.get("discount_simple_expire_at") or 0
    try:
        expire_at = float(expire_at)
    except (TypeError, ValueError):
        expire_at = 0
    if expire_at > 0 and time.time() >= expire_at:
        _set_discount_settings(agent_id, discount_simple_enabled=False, discount_simple_expire_at=0)
        settings = _get_discount_settings(agent_id)

    simple_enabled = plans_storage.is_simple_discount_enabled(settings)
    tiered_enabled = plans_storage.is_tiered_discount_enabled(settings)
    timer_line = _discount_timer_line(settings)

    lines = [
        i18n.t('🎛 <b>مدیریت حرفه‌ای تخفیف‌ها</b>', _lg),
        "",
        f"{i18n.t('🎁 تخفیف حجمی ساده: ', _lg)}{i18n.t('state_on', _lg) if simple_enabled else i18n.t('state_off', _lg)}",
        f"{i18n.t('🎚 تخفیف پلاکانی: ', _lg)}{i18n.t('state_on', _lg) if tiered_enabled else i18n.t('state_off', _lg)}",
        "",
        i18n.t('برای تغییر وضعیت هر نوع تخفیف روی دکمه‌ی همان نوع بزنید، یا از دکمه‌های ویرایش برای تنظیم مقادیر استفاده کنید.', _lg),
    ]
    if timer_line:
        lines.append(timer_line)

    if simple_enabled:
        lines.append(
            f"{i18n.t('• تخفیف حجمی ساده: از ', _lg)}{settings['discount_step_gb']}{i18n.t(' گیگ به بالا، ', _lg)}{settings['discount_percent_step']}{i18n.t('٪ تا سقف ', _lg)}{settings['discount_percent_max']}{i18n.t('٪', _lg)}"
        )
    elif int(settings.get('discount_step_gb', 0)) > 0 and int(settings.get('discount_percent_step', 0)) > 0:
        lines.append(
            f"{i18n.t('• تنظیمات ذخیره‌شده تخفیف حجمی ساده: از ', _lg)}{settings['discount_step_gb']}{i18n.t(' گیگ به بالا، ', _lg)}{settings['discount_percent_step']}{i18n.t('٪ تا سقف ', _lg)}{settings['discount_percent_max']}{i18n.t('٪ (غیرفعال)', _lg)}"
        )

    if tiered_enabled:
        lines.append(f"{i18n.t('• پله‌های تخفیف پلاکانی: ', _lg)}{plans_storage.format_discount_tiers(settings.get('discount_tiers', []))}")
    elif settings.get("discount_tiers"):
        lines.append(
            f"{i18n.t('• پله‌های تخفیف پلاکانی ذخیره‌شده: ', _lg)}{plans_storage.format_discount_tiers(settings.get('discount_tiers', []))}{i18n.t(' (غیرفعال)', _lg)}"
        )

    text = "\n".join(lines)
    try:
        await update.message.reply_text(text, reply_markup=discount_settings_keyboard(simple_enabled, tiered_enabled), parse_mode="HTML")
    except Exception:
        pass


async def _discount_simple_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    agent_id = get_agent_id(context)
    settings = _get_discount_settings(agent_id)
    simple_enabled = plans_storage.is_simple_discount_enabled(settings)

    if simple_enabled:
        _set_discount_settings(
            agent_id,
            discount_simple_enabled=False,
            discount_simple_expire_at=0,
        )
    else:
        update_kwargs = {"discount_simple_enabled": True, "discount_simple_expire_at": 0}
        if (
            int(settings.get("discount_step_gb", 0)) <= 0
            or int(settings.get("discount_percent_step", 0)) <= 0
            or int(settings.get("discount_percent_max", 0)) <= 0
        ):
            update_kwargs["discount_step_gb"] = settings.get("discount_step_gb", 50) or 50
            update_kwargs["discount_percent_step"] = settings.get("discount_percent_step", 5) or 5
            update_kwargs["discount_percent_max"] = settings.get("discount_percent_max", 50) or 50
        _set_discount_settings(agent_id, **update_kwargs)

    await _render_discount_menu(update, context)


async def _discount_tiers_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    query = update.callback_query
    agent_id = get_agent_id(context)
    settings = _get_discount_settings(agent_id)
    tiered_enabled = plans_storage.is_tiered_discount_enabled(settings)

    if tiered_enabled:
        _set_discount_settings(agent_id, discount_tiered_enabled=False)
        await _render_discount_menu(update, context)
        return

    if settings.get("discount_tiers"):
        _set_discount_settings(agent_id, discount_tiered_enabled=True)
        await _render_discount_menu(update, context)
        return

    try:
        await query.answer(
            i18n.t('⚠️ هیچ پله‌ای برای تخفیف پلاکانی تنظیم نشده است. ابتدا از «ویرایش تخفیف پلکانی» استفاده کنید.', _lg),
            show_alert=True,
        )
    except Exception:
        pass
    await _render_discount_menu(update, context)


async def _send_fixed_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, msg=None) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    agent_id = get_agent_id(context)
    cats = get_fixed_categories(agent_id)
    total_plans = 0
    for c in cats:
        count = len(get_fixed_plans(agent_id, category_id=c["id"]))
        c["plan_count"] = count
        total_plans += count
    if not cats:
        text = (
            i18n.t('📋 <b>پلن‌های ثابت</b>\n\nهنوز دسته‌ای ساخته نشده است.\nبا «➕ افزودن دسته جدید» شروع کنید:', _lg)
        )
    else:
        text = (
            f"{i18n.t('📋 <b>پلن‌های ثابت</b>\n\n🗂 تعداد دسته: ', _lg)}{len(cats)}{i18n.t('   📦 مجموع پلن‌ها: ', _lg)}{total_plans}{i18n.t('\n\nدسته مورد نظر را انتخاب کنید:', _lg)}"
        )
    kb = plans_cats_keyboard(cats)
    if msg:
        try:
            await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    elif update.message:
        try:
            await context.bot.send_message(update.message.chat_id, text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


async def _send_cat_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    agent_id = get_agent_id(context)
    cats = get_fixed_categories(agent_id)
    cat = next((c for c in cats if c["id"] == cat_id), None)
    if not cat:
        return
    plans = get_fixed_plans(agent_id, category_id=cat_id)
    text = (
        f"📁 <b>{cat['title']}{i18n.t('</b>\n\n📦 تعداد پلن: ', _lg)}{len(plans)}{i18n.t('   🔢 اولویت نمایش: ', _lg)}{cat.get('priority', 0)}"
    )
    kb = plans_cat_detail_keyboard(cat_id)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    elif update.message:
        try:
            await context.bot.send_message(update.message.chat_id, text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


async def _send_plans_list(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    agent_id = get_agent_id(context)
    plans = get_fixed_plans(agent_id, category_id=cat_id)
    cats = get_fixed_categories(agent_id)
    cat = next((c for c in cats if c["id"] == cat_id), None)
    cat_title = cat.get("title", i18n.t('دسته', _lg)) if cat else i18n.t('دسته', _lg)
    if not plans:
        text = (
            f"{i18n.t('📋 <b>پلن‌های «', _lg)}{cat_title}{i18n.t('»</b>\n\nهنوز پلنی در این دسته نیست.\nبا «➕ افزودن پلن» اولین پلن را بسازید.', _lg)}"
        )
    else:
        text = (
            f"{i18n.t('📋 <b>پلن‌های «', _lg)}{cat_title}{i18n.t('»</b>\n\nبرای مدیریت، یک پلن را انتخاب کنید:', _lg)}"
        )
    kb = plans_plans_keyboard(plans, cat_id)
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    elif update.message:
        try:
            await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


async def _send_plan_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: int) -> None:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    agent_id = get_agent_id(context)
    plan = get_fixed_plan(agent_id, plan_id)
    if not plan:
        return
    cats = get_fixed_categories(agent_id)
    cat = next((c for c in cats if c["id"] == plan.get("category_id")), {})
    cat_title = cat.get("title", i18n.t('نامشناس', _lg))
    gb_txt = i18n.t('نامحدود', _lg) if plan["gb"] == 0 else f"{plan['gb']}{i18n.t(' گیگابایت', _lg)}"
    text = (
        f"📦 <b>{plan['title']}{i18n.t('</b>\n\n📁 دسته: ', _lg)}{cat_title}{i18n.t('\n📊 حجم: ', _lg)}{gb_txt}{i18n.t('\n⏰ زمان: ', _lg)}{plan['days']}{i18n.t(' روز\n💰 قیمت: ', _lg)}{plan['price']:f','}{i18n.t(' تومان', _lg)}"
    )
    kb = _ikb([
        [IButton(i18n.t('🗑 حذف این پلن', _lg), callback_data=f"agbot:plans:fixed:plan_del:{plan_id}")],
        [IButton(BTN_BACK, callback_data=f"agbot:plans:fixed:plans:{plan['category_id']}")],
    ])
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


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
    parts = data.split(":")
    action = parts[2] if len(parts) > 2 else ""
    sub = parts[3] if len(parts) > 3 else ""
    agent_id = get_agent_id(context)

    if action == "fixed":
        sub_sub = sub

        if not sub_sub:
            await _send_fixed_menu(update, context)
            return

        if sub_sub == "cat_add":
            context.user_data[UD_STATE] = STATE_FIXED_ADD_CAT_TITLE
            await query.answer()
            await context.bot.send_message(
                query.message.chat_id,
                i18n.t('➕ <b>افزودن دسته جدید</b>\n\nعنوان دسته را ارسال کنید:', _lg),
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
            return

        if sub_sub == "cat_del_menu":
            cats = get_fixed_categories(agent_id)
            await query.edit_message_text(
                i18n.t('🗑 <b>حذف دسته</b>\n\nیک دسته را برای حذف انتخاب کنید:', _lg),
                reply_markup=plans_cat_del_keyboard(cats), parse_mode="HTML",
            )
            return

        if sub_sub == "cat_del":
            cid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            if cid:
                delete_fixed_category(agent_id, cid)
            await query.answer(i18n.t('دسته حذف شد.', _lg))
            await _send_fixed_menu(update, context)
            return

        if sub_sub == "cat_del_ask":
            cid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            if cid:
                cats = get_fixed_categories(agent_id)
                cat = next((c for c in cats if c["id"] == cid), None)
                title = cat.get("title", "?") if cat else "?"
                await query.edit_message_text(
                    f"{i18n.t('⚠️ <b>حذف دسته</b>\n\nدسته «', _lg)}{title}{i18n.t('» همراه با پلن‌هایش حذف می‌شود.\nمطمئن هستید؟', _lg)}",
                    reply_markup=plans_cat_del_confirm_keyboard(cid), parse_mode="HTML",
                )
            return

        if sub_sub == "cat_edit":
            cid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            context.user_data[UD_STATE] = STATE_FIXED_EDIT_CAT_TITLE
            context.user_data["edit_cat_id"] = cid
            await query.answer()
            await context.bot.send_message(
                query.message.chat_id,
                i18n.t('✏️ <b>ویرایش عنوان</b>\n\nعنوان جدید را ارسال کنید:', _lg),
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
            return

        if sub_sub == "cat":
            cid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            await _send_cat_detail(update, context, cid)
            return

        if sub_sub == "plans":
            cid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            await _send_plans_list(update, context, cid)
            return

        if sub_sub == "plan":
            pid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            await _send_plan_detail(update, context, pid)
            return

        if sub_sub == "plan_add":
            cid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            context.user_data[UD_STATE] = STATE_FIXED_ADD_PLAN_TITLE
            context.user_data["fixed_cat_id"] = cid
            context.user_data["fixed_new_plan"] = {}
            await query.answer()
            await context.bot.send_message(
                query.message.chat_id,
                i18n.t('➕ <b>افزودن پلن جدید</b>\n\nعنوان پلن را ارسال کنید:', _lg),
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
            return

        if sub_sub == "plan_del_menu":
            cid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            plans = get_fixed_plans(agent_id, category_id=cid)
            await query.edit_message_text(
                i18n.t('🗑 <b>حذف پلن</b>\n\nیک پلن را برای حذف انتخاب کنید:', _lg),
                reply_markup=plans_plan_del_keyboard(plans, cid), parse_mode="HTML",
            )
            return

        if sub_sub == "plan_del":
            pid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            plan = get_fixed_plan(agent_id, pid)
            delete_fixed_plan(agent_id, pid)
            await query.answer(i18n.t('پلن حذف شد.', _lg))
            if plan:
                await _send_plans_list(update, context, int(plan.get("category_id") or 0))
            else:
                await _send_fixed_menu(update, context)
            return

    if action == "back":
        context.user_data.pop(UD_STATE, None)
        await show_menu(update, context)
        return

    if action == "mode":
        if sub == "toggle" and len(parts) > 4:
            toggle_mode = parts[4]
            if toggle_mode in ("dynamic", "fixed"):
                set_setting(agent_id, "plan_display_mode", toggle_mode)
                await query.answer(f"{i18n.t('حالت نمایش به ', _lg)}{toggle_mode}{i18n.t(' تغییر کرد.', _lg)}")
                current = get_setting(agent_id, "plan_display_mode", "dynamic")
                try:
                    await query.edit_message_reply_markup(reply_markup=plans_mode_keyboard(current, lang=agent_lang(context)))
                except Exception:
                    pass
                return
        current = get_setting(agent_id, "plan_display_mode", "dynamic")
        try:
            await query.edit_message_text(
                i18n.t('📋 <b>نوع نمایش پلن‌ها</b>\n\nفقط یکی از دو حالت می‌تواند فعال باشد:', _lg),
                reply_markup=plans_mode_keyboard(current, lang=agent_lang(context)), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    # ---- Fixed plan handlers ----
    if sub == "fixed":
        if action == "plans":
            await _send_fixed_menu(update, context)
            return

        sub_sub = parts[4] if len(parts) > 4 else ""

        if sub_sub == "cat_add":
            context.user_data[UD_STATE] = STATE_FIXED_ADD_CAT_TITLE
            await query.answer()
            chat_id = query.message.chat_id
            await context.bot.send_message(
                chat_id,
                i18n.t('➕ <b>افزودن دسته جدید</b>\n\nعنوان دسته را ارسال کنید:', _lg),
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
            return

        if sub_sub == "cat_del_menu":
            cats = get_fixed_categories(agent_id)
            try:
                await query.edit_message_text(
                    i18n.t('🗑 <b>حذف دسته</b>\n\nیک دسته را برای حذف انتخاب کنید:', _lg),
                    reply_markup=plans_cat_del_keyboard(cats), parse_mode="HTML",
                )
            except Exception:
                pass
            return

        if sub_sub == "cat_del":
            cid = int(parts[5])
            delete_fixed_category(agent_id, cid)
            await query.answer(i18n.t('دسته حذف شد.', _lg))
            await _send_fixed_menu(update, context)
            return

        if sub_sub == "cat_edit":
            cid = int(parts[5])
            context.user_data[UD_STATE] = STATE_FIXED_EDIT_CAT_TITLE
            context.user_data["edit_cat_id"] = cid
            await query.answer()
            await context.bot.send_message(
                query.message.chat_id,
                i18n.t('✏️ <b>ویرایش عنوان</b>\n\nعنوان جدید را ارسال کنید:', _lg),
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
            return

        if sub_sub == "cat":
            cid = int(parts[5])
            await _send_cat_detail(update, context, cid)
            return

        if sub_sub == "plans":
            cid = int(parts[5])
            await _send_plans_list(update, context, cid)
            return

        if sub_sub == "plan":
            pid = int(parts[5])
            await _send_plan_detail(update, context, pid)
            return

        if sub_sub == "plan_add":
            cid = int(parts[5])
            context.user_data[UD_STATE] = STATE_FIXED_ADD_PLAN_TITLE
            context.user_data["fixed_cat_id"] = cid
            context.user_data["fixed_new_plan"] = {}
            await query.answer()
            await context.bot.send_message(
                query.message.chat_id,
                i18n.t('➕ <b>افزودن پلن جدید</b>\n\nعنوان پلن را ارسال کنید:', _lg),
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
            return

        if sub_sub == "plan_del_menu":
            cid = int(parts[5])
            plans = get_fixed_plans(agent_id, category_id=cid)
            try:
                await query.edit_message_text(
                    i18n.t('🗑 <b>حذف پلن</b>\n\nیک پلن را برای حذف انتخاب کنید:', _lg),
                    reply_markup=plans_plan_del_keyboard(plans, cid), parse_mode="HTML",
                )
            except Exception:
                pass
            return

        if sub_sub == "plan_del":
            pid = int(parts[5])
            plan = get_fixed_plan(agent_id, pid)
            delete_fixed_plan(agent_id, pid)
            await query.answer(i18n.t('پلن حذف شد.', _lg))
            if plan:
                await _send_plans_list(update, context, plan["category_id"])
            return

        await _send_fixed_menu(update, context)
        return

    # ---- Dynamic plan handlers ----
    if action == "discount":
        sub = parts[3] if len(parts) > 3 else ""

        if not sub:
            await _render_discount_menu(update, context)
            return

        if sub == "toggle":
            which = parts[4] if len(parts) > 4 else ""
            if which == "simple":
                await _discount_simple_toggle(update, context)
            elif which == "tiers":
                await _discount_tiers_toggle(update, context)
            return

        if sub == "edit":
            which = parts[4] if len(parts) > 4 else ""
            if which == "simple":
                context.user_data[UD_STATE] = STATE_DYN_EDIT_FIELD
                context.user_data[UD_DYN_FIELD] = "discount_simple"
                context.user_data[UD_DYN_DISCOUNT_PHASE] = "threshold"
                await query.answer()
                await context.bot.send_message(
                    query.message.chat_id,
                    i18n.t('🎁 <b>تخفیف حجمی (ساده)</b>\n\nاز چه حجمی به بالا تخفیف فعال شود؟ (بر حسب گیگ)\nمثال: 50\nبرای خاموش کردن کامل تخفیف، عدد 0 بفرست.', _lg),
                    reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
                return
            if which == "tiers":
                context.user_data[UD_STATE] = STATE_DYN_EDIT_FIELD
                context.user_data[UD_DYN_FIELD] = "discount_tiers"
                await query.answer()
                await context.bot.send_message(
                    query.message.chat_id,
                    i18n.t('🎚 <b>تخفیف پلکانی</b>\n\nهر پله را با فرمت <code>حجم:درصد</code> وارد کن و پله‌ها را با کاما یا خط جدید جدا کن.\nمثال: <code>50:5, 100:10, 200:15</code>\nیعنی: از ۵۰ گیگ ۵٪، از ۱۰۰ گیگ ۱۰٪ و از ۲۰۰ گیگ ۱۵٪ تخفیف.\nبرای خاموش کردن تخفیف، عدد 0 بفرست.', _lg),
                    reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
                return
            if which == "timer":
                context.user_data[UD_STATE] = STATE_DYN_EDIT_FIELD
                context.user_data[UD_DYN_FIELD] = "discount_timer"
                await query.answer()
                await context.bot.send_message(
                    query.message.chat_id,
                    i18n.t('⏱ <b>تایمر تخفیف حجمی (ساده)</b>\n\nمدت زمان را به ساعت ارسال کن (مثلاً 12 یا 24).\nبرای اتمام تایمر و خاموش شدن خودکار تخفیف، عدد 0 بفرست.', _lg),
                    reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
                return

    if action == "dyn_edit":
        field = parts[3] if len(parts) > 3 else ""
        if field not in ("price_per_gb", "price_per_month", "volume_range", "time_range"):
            return
        context.user_data[UD_STATE] = STATE_DYN_EDIT_FIELD
        context.user_data[UD_DYN_FIELD] = field
        prompts = {
            "price_per_gb": i18n.t('قیمت هر گیگ را (تومان) ارسال کنید:', _lg),
            "price_per_month": i18n.t('قیمت هر ماه اشتراک را (تومان) ارسال کنید:', _lg),
            "volume_range": (
                i18n.t('تنظیم حجم به صورت: حداقل_حجم-حداکثر_حجم-گام\nمثال: 20-200-20', _lg)
            ),
            "time_range": (
                i18n.t('تنظیم زمان به صورت: حداقل_ماه-حداکثر_ماه-گام\nمثال: 1-12-1', _lg)
            ),
        }
        await query.answer()
        await context.bot.send_message(
            query.message.chat_id,
            prompts.get(field, i18n.t('لطفا مقدار جدید را ارسال کنید:', _lg)),
            reply_markup=cancel_keyboard(),
        )
        return

    if action == "dynset":
        settings = _get_discount_settings(agent_id)
        simple_enabled = plans_storage.is_simple_discount_enabled(settings)
        tiered_enabled = plans_storage.is_tiered_discount_enabled(settings)
        discount_info = []
        if simple_enabled:
            discount_info.append(
                f"{i18n.t('• تخفیف حجمی ساده: از ', _lg)}{settings.get('discount_step_gb', 0)}{i18n.t(' گیگ به بالا، ', _lg)}{settings.get('discount_percent_step', 0)}{i18n.t('٪ تا سقف ', _lg)}{settings.get('discount_percent_max', 0)}{i18n.t('٪', _lg)}"
            )
        if tiered_enabled:
            discount_info.append(
                f"{i18n.t('• تخفیف پلکانی: ', _lg)}{plans_storage.format_discount_tiers(settings.get('discount_tiers', []))}"
            )
        if not discount_info:
            discount_info.append(i18n.t('• تخفیفی فعال نیست', _lg))
        text = (
            i18n.t('⚙️ <b>تنظیم پلن پویا</b>\n\n💰 قیمت هر گیگ: {}\n💰 قیمت هر ماه: {}\n\n📊 حجم قابل فروش: از {} تا {} گیگ (گام: {})\n⏰ زمان اشتراک: از {} تا {} ماه (گام: {})\n\n🎟 <b>تخفیف ها:</b>\n{}', _lg)
        ).format(
            _fmt_toman(settings.get("price_per_gb", 0)),
            _fmt_toman(settings.get("price_per_month", 0)),
            settings.get("min_gb", 1), settings.get("max_gb", 999), settings.get("step_gb", 1),
            settings.get("min_month", 1), settings.get("max_month", 12), settings.get("step_month", 1),
            "\n".join(discount_info),
        )
        try:
            await query.edit_message_text(text, reply_markup=dyn_settings_keyboard(), parse_mode="HTML")
        except Exception:
            pass
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        from AgentBot.keyboards import agent_lang as _ag_lang_fn
        _lg = _ag_lang_fn(context)
    except Exception:
        _lg = "fa"
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    state = context.user_data.get(UD_STATE)
    text = update.message.text.strip()

    CANCEL_TEXTS = {i18n.t('لغو', _lg), i18n.t('بازگشت', _lg), "/cancel"}

    if text in CANCEL_TEXTS:
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_DYN_FIELD, None)
        context.user_data.pop(UD_DYN_DISCOUNT_PHASE, None)
        context.user_data.pop(UD_DYN_DISCOUNT_THRESHOLD, None)
        context.user_data.pop("edit_cat_id", None)
        context.user_data.pop("fixed_cat_id", None)
        context.user_data.pop("fixed_new_plan", None)
        return False

    # ---- Fixed: add category title ----
    if state == STATE_FIXED_ADD_CAT_TITLE:
        add_fixed_category(agent_id, text)
        context.user_data.pop(UD_STATE, None)
        await update.message.reply_text(i18n.t('✅ دسته اضافه شد.', _lg), reply_markup=main_menu_keyboard())
        await _send_fixed_menu(update, context)
        return True

    # ---- Fixed: edit category title ----
    if state == STATE_FIXED_EDIT_CAT_TITLE:
        cid = context.user_data.get("edit_cat_id")
        if cid:
            edit_fixed_category(agent_id, cid, title=text)
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop("edit_cat_id", None)
        await update.message.reply_text(i18n.t('✅ عنوان بروز شد.', _lg), reply_markup=main_menu_keyboard())
        await _send_fixed_menu(update, context)
        return True

    # ---- Fixed: add plan wizard ----
    if state == STATE_FIXED_ADD_PLAN_TITLE:
        context.user_data["fixed_new_plan"] = {"title": text}
        context.user_data[UD_STATE] = STATE_FIXED_ADD_PLAN_GB
        await update.message.reply_text(i18n.t('📊 حجم پلن را به گیگابایت ارسال کنید:', _lg), reply_markup=cancel_keyboard())
        return True

    if state == STATE_FIXED_ADD_PLAN_GB:
        try:
            gb = float(_normalize_digits(text).replace(",", "."))
        except ValueError:
            await update.message.reply_text(i18n.t('❌ لطفا یک عدد معتبر بفرستید.', _lg), reply_markup=cancel_keyboard())
            return True
        np = context.user_data.get("fixed_new_plan", {})
        np["gb"] = gb
        context.user_data["fixed_new_plan"] = np
        context.user_data[UD_STATE] = STATE_FIXED_ADD_PLAN_DAYS
        await update.message.reply_text(i18n.t('⏰ تعداد روزهای پلن را ارسال کنید:', _lg), reply_markup=cancel_keyboard())
        return True

    if state == STATE_FIXED_ADD_PLAN_DAYS:
        try:
            days = int(_normalize_digits(text))
        except ValueError:
            await update.message.reply_text(i18n.t('❌ لطفا یک عدد معتبر بفرستید.', _lg), reply_markup=cancel_keyboard())
            return True
        np = context.user_data.get("fixed_new_plan", {})
        np["days"] = days
        context.user_data["fixed_new_plan"] = np
        context.user_data[UD_STATE] = STATE_FIXED_ADD_PLAN_PRICE
        await update.message.reply_text(i18n.t('💰 قیمت پلن را به تومان ارسال کنید:', _lg), reply_markup=cancel_keyboard())
        return True

    if state == STATE_FIXED_ADD_PLAN_PRICE:
        try:
            price = int(_normalize_digits(text))
        except ValueError:
            await update.message.reply_text(i18n.t('❌ لطفا یک عدد معتبر بفرستید.', _lg), reply_markup=cancel_keyboard())
            return True
        cid = context.user_data.get("fixed_cat_id")
        np = context.user_data.get("fixed_new_plan", {})
        if cid and np.get("title") and np.get("gb") is not None and np.get("days"):
            add_fixed_plan(agent_id, cid, np["title"], price, np["days"], np["gb"])
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop("fixed_cat_id", None)
            context.user_data.pop("fixed_new_plan", None)
            await update.message.reply_text(i18n.t('✅ پلن اضافه شد.', _lg), reply_markup=main_menu_keyboard())
            await _send_plans_list(update, context, cid)
        return True

    # ---- Dynamic plan text handlers ----
    if state == STATE_DYN_EDIT_FIELD:
        field = context.user_data.get(UD_DYN_FIELD, "")
        raw = _normalize_digits(text)

        if field == "discount_simple":
            phase = context.user_data.get(UD_DYN_DISCOUNT_PHASE, "threshold")

            if phase == "threshold":
                try:
                    threshold = int(raw)
                except ValueError:
                    await update.message.reply_text(i18n.t('❌ لطفاً عدد حجم را به صورت صحیح وارد کنید (مثلاً 50).', _lg), reply_markup=cancel_keyboard())
                    return True
                threshold = max(0, threshold)
                context.user_data[UD_DYN_DISCOUNT_THRESHOLD] = threshold
                context.user_data[UD_DYN_DISCOUNT_PHASE] = "percent"
                await update.message.reply_text(
                    i18n.t('الان درصد تخفیف را ارسال کن (مثلاً 25).\nبرای خاموش کردن کامل تخفیف 0 بفرست.', _lg),
                    reply_markup=cancel_keyboard(),
                )
                return True

            try:
                percent = int(raw.replace("%", ""))
            except ValueError:
                await update.message.reply_text(i18n.t('❌ لطفاً درصد تخفیف را به صورت عددی بفرست (مثلاً 25).', _lg), reply_markup=cancel_keyboard())
                return True
            threshold = int(context.user_data.pop(UD_DYN_DISCOUNT_THRESHOLD, 0))
            context.user_data.pop(UD_DYN_DISCOUNT_PHASE, None)

            if percent <= 0 or threshold <= 0:
                _set_discount_settings(
                    agent_id,
                    discount_step_gb=0,
                    discount_percent_step=0,
                    discount_percent_max=0,
                    discount_tiers=[],
                    discount_simple_enabled=False,
                    discount_simple_expire_at=0,
                )
                await update.message.reply_text(i18n.t('✅ تخفیف حجمی خاموش شد.', _lg), reply_markup=main_menu_keyboard())
            else:
                _set_discount_settings(
                    agent_id,
                    discount_step_gb=threshold,
                    discount_percent_step=percent,
                    discount_percent_max=percent,
                    discount_tiers=[],
                    discount_simple_enabled=True,
                    discount_simple_expire_at=0,
                )
                await update.message.reply_text(
                    f"{i18n.t('✅ تخفیف ذخیره شد.\nاز ', _lg)}{threshold}{i18n.t(' گیگ به بالا، ', _lg)}{percent}{i18n.t('٪ تخفیف روی قیمت نهایی اعمال می‌شود.', _lg)}",
                    reply_markup=main_menu_keyboard(),
                )
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_DYN_FIELD, None)
            await _roleme_discount_menu(context, update, agent_id)
            return True

        if field == "discount_tiers":
            try:
                tiers = plans_storage.parse_discount_tiers_text(text)
            except ValueError:
                await update.message.reply_text(
                    i18n.t('❌ فرمت پله‌ها معتبر نیست. مثال درست: 50:5,100:10,200:15', _lg),
                    reply_markup=cancel_keyboard(),
                )
                return True
            _set_discount_settings(
                agent_id,
                discount_tiers=tiers,
                discount_tiered_enabled=True,
            )
            if tiers:
                await update.message.reply_text(
                    f"{i18n.t('✅ تخفیف پلکانی ذخیره شد.\n', _lg)}{plans_storage.format_discount_tiers(tiers)}",
                    reply_markup=main_menu_keyboard(),
                )
            else:
                await update.message.reply_text(i18n.t('✅ تخفیف پلکانی خاموش شد.', _lg), reply_markup=main_menu_keyboard())
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_DYN_FIELD, None)
            await _roleme_discount_menu(context, update, agent_id)
            return True

        if field == "discount_timer":
            try:
                hours = int(raw)
            except ValueError:
                await update.message.reply_text(i18n.t('❌ لطفاً مدت زمان را به ساعت به صورت عددی ارسال کنید (مثلاً 12).', _lg), reply_markup=cancel_keyboard())
                return True

            if hours <= 0:
                _set_discount_settings(
                    agent_id,
                    discount_simple_enabled=False,
                    discount_simple_expire_at=0,
                )
                await update.message.reply_text(i18n.t('✅ تایمر تخفیف حذف شد و تخفیف حجمی ساده خاموش شد.', _lg), reply_markup=main_menu_keyboard())
            else:
                expire_at = int(time.time()) + hours * 3600
                _set_discount_settings(
                    agent_id,
                    discount_simple_enabled=True,
                    discount_simple_expire_at=expire_at,
                )
                await update.message.reply_text(
                    f"{i18n.t('✅ تایمر تخفیف حجمی ساده تنظیم شد.\nتخفیف به مدت ', _lg)}{hours}{i18n.t(' ساعت (تا ', _lg)}{datetime.fromtimestamp(expire_at).strftime('%Y-%m-%d %H:%M')}{i18n.t(') فعال است و پس از اتمام، به‌صورت خودکار خاموش می‌شود.', _lg)}",
                    reply_markup=main_menu_keyboard(),
                )
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_DYN_FIELD, None)
            await _roleme_discount_menu(context, update, agent_id)
            return True

        settings = get_setting(agent_id, "dynamic_plan_settings", {})
        if field == "price_per_gb":
            try:
                settings["price_per_gb"] = int(raw)
            except ValueError:
                await update.message.reply_text(i18n.t('❌ عدد نامعتبر.', _lg))
                return True
        elif field == "price_per_month":
            try:
                settings["price_per_month"] = int(raw)
            except ValueError:
                await update.message.reply_text(i18n.t('❌ عدد نامعتبر.', _lg))
                return True
        elif field == "volume_range":
            vals = raw.replace("\u2212", "-").replace("\u2013", "-").split("-")
            if len(vals) == 3:
                try:
                    settings["min_gb"], settings["max_gb"], settings["step_gb"] = int(vals[0]), int(vals[1]), int(vals[2])
                except ValueError:
                    await update.message.reply_text(i18n.t('❌ فرمت نامعتبر. از فرمت حداقل-حداکثر-گام استفاده کنید.', _lg))
                    return True
            else:
                await update.message.reply_text(i18n.t('❌ دقیقا 3 مقدار با خط تیره جدا کنید.', _lg))
                return True
        elif field == "time_range":
            vals = raw.replace("\u2212", "-").replace("\u2013", "-").split("-")
            if len(vals) == 3:
                try:
                    settings["min_month"], settings["max_month"], settings["step_month"] = int(vals[0]), int(vals[1]), int(vals[2])
                except ValueError:
                    await update.message.reply_text(i18n.t('❌ فرمت نامعتبر.', _lg))
                    return True
            else:
                await update.message.reply_text(i18n.t('❌ دقیقا 3 مقدار با خط تیره جدا کنید.', _lg))
                return True
        else:
            return False
        set_setting(agent_id, "dynamic_plan_settings", settings)
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_DYN_FIELD, None)
        await update.message.reply_text(i18n.t('✅ به ‌روزرسانی شد.', _lg), reply_markup=main_menu_keyboard())
        await show_menu(update, context)
        return True

    if state == STATE_DYNAMIC_SETTINGS:
        raw = _normalize_digits(text)
        vals = raw.split()
        if len(vals) != 8:
            await update.message.reply_text(i18n.t('❌ دقیقا 8 مقدار وارد کنید.', _lg))
            return True
        try:
            nums = [int(v) for v in vals]
        except ValueError:
            await update.message.reply_text(i18n.t('❌ همه مقادیر باید عدد باشند.', _lg))
            return True
        set_setting(agent_id, "dynamic_plan_settings", {
            "price_per_gb": nums[0], "price_per_month": nums[1],
            "min_gb": nums[2], "max_gb": nums[3], "step_gb": nums[4],
            "min_month": nums[5], "max_month": nums[6], "step_month": nums[7],
        })
        context.user_data.pop(UD_STATE, None)
        await update.message.reply_text(i18n.t('✅ تنظیمات پویا ذخیره شد.', _lg), reply_markup=main_menu_keyboard())
        return True

    return False
