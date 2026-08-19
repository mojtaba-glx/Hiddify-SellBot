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
    kb = plans_menu_keyboard(current_mode)
    text = "\U0001f4b5 <b>\u067e\u0644\u0646\u200c\u0647\u0627</b>"
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
        parts.append(f"{days} روز")
    if hours > 0:
        parts.append(f"{hours} ساعت")
    if minutes > 0:
        parts.append(f"{minutes} دقیقه")
    remaining_txt = " و ".join(parts) if parts else "کمتر از یک دقیقه"
    return (
        f"⏱ تایمر تخفیف حجمی ساده: {remaining_txt} مانده "
        f"(پایان: {datetime.fromtimestamp(expire_at).strftime('%Y-%m-%d %H:%M')})"
    )


async def _render_discount_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        "🎛 <b>مدیریت حرفه‌ای تخفیف‌ها</b>",
        "",
        f"🎁 تخفیف حجمی ساده: {'فعال ✅' if simple_enabled else 'غیرفعال ❌'}",
        f"🎚 تخفیف پلاکانی: {'فعال ✅' if tiered_enabled else 'غیرفعال ❌'}",
        "",
        "برای تغییر وضعیت هر نوع تخفیف روی دکمه‌ی همان نوع بزنید، یا از دکمه‌های ویرایش برای تنظیم مقادیر استفاده کنید.",
    ]
    if timer_line:
        lines.append(timer_line)

    if simple_enabled:
        lines.append(
            f"• تخفیف حجمی ساده: از {settings['discount_step_gb']} گیگ به بالا، "
            f"{settings['discount_percent_step']}٪ تا سقف {settings['discount_percent_max']}٪"
        )
    elif int(settings.get('discount_step_gb', 0)) > 0 and int(settings.get('discount_percent_step', 0)) > 0:
        lines.append(
            f"• تنظیمات ذخیره‌شده تخفیف حجمی ساده: از {settings['discount_step_gb']} گیگ به بالا، "
            f"{settings['discount_percent_step']}٪ تا سقف {settings['discount_percent_max']}٪ (غیرفعال)"
        )

    if tiered_enabled:
        lines.append(f"• پله‌های تخفیف پلاکانی: {plans_storage.format_discount_tiers(settings.get('discount_tiers', []))}")
    elif settings.get("discount_tiers"):
        lines.append(
            f"• پله‌های تخفیف پلاکانی ذخیره‌شده: {plans_storage.format_discount_tiers(settings.get('discount_tiers', []))} (غیرفعال)"
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
        "🎛 <b>مدیریت حرفه‌ای تخفیف‌ها</b>",
        "",
        f"🎁 تخفیف حجمی ساده: {'فعال ✅' if simple_enabled else 'غیرفعال ❌'}",
        f"🎚 تخفیف پلاکانی: {'فعال ✅' if tiered_enabled else 'غیرفعال ❌'}",
        "",
        "برای تغییر وضعیت هر نوع تخفیف روی دکمه‌ی همان نوع بزنید، یا از دکمه‌های ویرایش برای تنظیم مقادیر استفاده کنید.",
    ]
    if timer_line:
        lines.append(timer_line)

    if simple_enabled:
        lines.append(
            f"• تخفیف حجمی ساده: از {settings['discount_step_gb']} گیگ به بالا، "
            f"{settings['discount_percent_step']}٪ تا سقف {settings['discount_percent_max']}٪"
        )
    elif int(settings.get('discount_step_gb', 0)) > 0 and int(settings.get('discount_percent_step', 0)) > 0:
        lines.append(
            f"• تنظیمات ذخیره‌شده تخفیف حجمی ساده: از {settings['discount_step_gb']} گیگ به بالا، "
            f"{settings['discount_percent_step']}٪ تا سقف {settings['discount_percent_max']}٪ (غیرفعال)"
        )

    if tiered_enabled:
        lines.append(f"• پله‌های تخفیف پلاکانی: {plans_storage.format_discount_tiers(settings.get('discount_tiers', []))}")
    elif settings.get("discount_tiers"):
        lines.append(
            f"• پله‌های تخفیف پلاکانی ذخیره‌شده: {plans_storage.format_discount_tiers(settings.get('discount_tiers', []))} (غیرفعال)"
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
            "⚠️ هیچ پله‌ای برای تخفیف پلاکانی تنظیم نشده است. ابتدا از «ویرایش تخفیف پلکانی» استفاده کنید.",
            show_alert=True,
        )
    except Exception:
        pass
    await _render_discount_menu(update, context)


async def _send_fixed_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, msg=None) -> None:
    agent_id = get_agent_id(context)
    cats = get_fixed_categories(agent_id)
    total_plans = 0
    for c in cats:
        count = len(get_fixed_plans(agent_id, category_id=c["id"]))
        c["plan_count"] = count
        total_plans += count
    if not cats:
        text = (
            "\U0001f4cb <b>\u067e\u0644\u0646\u200c\u0647\u0627\u06cc \u062b\u0627\u0628\u062a</b>\n\n"
            "\u0647\u0646\u0648\u0632 \u062f\u0633\u062a\u0647\u200c\u0627\u06cc \u0633\u0627\u062e\u062a\u0647 \u0646\u0634\u062f\u0647 \u0627\u0633\u062a.\n"
            "\u0628\u0627 \u00ab\u2795 \u0627\u0641\u0632\u0648\u062f\u0646 \u062f\u0633\u062a\u0647 \u062c\u062f\u06cc\u062f\u00bb \u0634\u0631\u0648\u0639 \u06a9\u0646\u06cc\u062f:"
        )
    else:
        text = (
            "\U0001f4cb <b>\u067e\u0644\u0646\u200c\u0647\u0627\u06cc \u062b\u0627\u0628\u062a</b>\n\n"
            f"\U0001f5c2 \u062a\u0639\u062f\u0627\u062f \u062f\u0633\u062a\u0647: {len(cats)}   \U0001f4e6 \u0645\u062c\u0645\u0648\u0639 \u067e\u0644\u0646\u200c\u0647\u0627: {total_plans}\n\n"
            "\u062f\u0633\u062a\u0647 \u0645\u0648\u0631\u062f \u0646\u0638\u0631 \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f:"
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
    agent_id = get_agent_id(context)
    cats = get_fixed_categories(agent_id)
    cat = next((c for c in cats if c["id"] == cat_id), None)
    if not cat:
        return
    plans = get_fixed_plans(agent_id, category_id=cat_id)
    text = (
        f"\U0001f4c1 <b>{cat['title']}</b>\n\n"
        f"\U0001f4e6 \u062a\u0639\u062f\u0627\u062f \u067e\u0644\u0646: {len(plans)}   \U0001f522 \u0627\u0648\u0644\u0648\u06cc\u062a \u0646\u0645\u0627\u06cc\u0634: {cat.get('priority', 0)}"
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
    agent_id = get_agent_id(context)
    plans = get_fixed_plans(agent_id, category_id=cat_id)
    cats = get_fixed_categories(agent_id)
    cat = next((c for c in cats if c["id"] == cat_id), None)
    cat_title = cat.get("title", "\u062f\u0633\u062a\u0647") if cat else "\u062f\u0633\u062a\u0647"
    if not plans:
        text = (
            f"\U0001f4cb <b>\u067e\u0644\u0646\u200c\u0647\u0627\u06cc \u00ab{cat_title}\u00bb</b>\n\n"
            "\u0647\u0646\u0648\u0632 \u067e\u0644\u0646\u06cc \u062f\u0631 \u0627\u06cc\u0646 \u062f\u0633\u062a\u0647 \u0646\u06cc\u0633\u062a.\n"
            "\u0628\u0627 \u00ab\u2795 \u0627\u0641\u0632\u0648\u062f\u0646 \u067e\u0644\u0646\u00bb \u0627\u0648\u0644\u06cc\u0646 \u067e\u0644\u0646 \u0631\u0627 \u0628\u0633\u0627\u0632\u06cc\u062f."
        )
    else:
        text = (
            f"\U0001f4cb <b>\u067e\u0644\u0646\u200c\u0647\u0627\u06cc \u00ab{cat_title}\u00bb</b>\n\n"
            "\u0628\u0631\u0627\u06cc \u0645\u062f\u06cc\u0631\u06cc\u062a\u060c \u06cc\u06a9 \u067e\u0644\u0646 \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f:"
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
    agent_id = get_agent_id(context)
    plan = get_fixed_plan(agent_id, plan_id)
    if not plan:
        return
    cats = get_fixed_categories(agent_id)
    cat = next((c for c in cats if c["id"] == plan.get("category_id")), {})
    cat_title = cat.get("title", "\u0646\u0627\u0645\u0634\u0646\u0627\u0633")
    gb_txt = "\u0646\u0627\u0645\u062d\u062f\u0648\u062f" if plan["gb"] == 0 else f"{plan['gb']} \u06af\u06cc\u06af\u0627\u0628\u0627\u06cc\u062a"
    text = (
        f"\U0001f4e6 <b>{plan['title']}</b>\n\n"
        f"\U0001f4c1 \u062f\u0633\u062a\u0647: {cat_title}\n"
        f"\U0001f4ca \u062d\u062c\u0645: {gb_txt}\n"
        f"\u23f0 \u0632\u0645\u0627\u0646: {plan['days']} \u0631\u0648\u0632\n"
        f"\U0001f4b0 \u0642\u06cc\u0645\u062a: {plan['price']:,} \u062a\u0648\u0645\u0627\u0646"
    )
    kb = _ikb([
        [IButton("\U0001f5d1 \u062d\u0630\u0641 \u0627\u06cc\u0646 \u067e\u0644\u0646", callback_data=f"agbot:plans:fixed:plan_del:{plan_id}")],
        [IButton(BTN_BACK, callback_data=f"agbot:plans:fixed:plans:{plan['category_id']}")],
    ])
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                "➕ <b>افزودن دسته جدید</b>\n\nعنوان دسته را ارسال کنید:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
            return

        if sub_sub == "cat_del_menu":
            cats = get_fixed_categories(agent_id)
            await query.edit_message_text(
                "🗑 <b>حذف دسته</b>\n\nیک دسته را برای حذف انتخاب کنید:",
                reply_markup=plans_cat_del_keyboard(cats), parse_mode="HTML",
            )
            return

        if sub_sub == "cat_del":
            cid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            if cid:
                delete_fixed_category(agent_id, cid)
            await query.answer("\u062f\u0633\u062a\u0647 \u062d\u0630\u0641 \u0634\u062f.")
            await _send_fixed_menu(update, context)
            return

        if sub_sub == "cat_del_ask":
            cid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            if cid:
                cats = get_fixed_categories(agent_id)
                cat = next((c for c in cats if c["id"] == cid), None)
                title = cat.get("title", "?") if cat else "?"
                await query.edit_message_text(
                    f"\u26a0\ufe0f <b>\u062d\u0630\u0641 \u062f\u0633\u062a\u0647</b>\n\n\u062f\u0633\u062a\u0647 \u00ab{title}\u00bb \u0647\u0645\u0631\u0627\u0647 \u0628\u0627 \u067e\u0644\u0646\u200c\u0647\u0627\u06cc\u0634 \u062d\u0630\u0641 \u0645\u06cc\u200c\u0634\u0648\u062f.\n\u0645\u0637\u0645\u0626\u0646 \u0647\u0633\u062a\u06cc\u062f\u061f",
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
                "✏️ <b>ویرایش عنوان</b>\n\nعنوان جدید را ارسال کنید:",
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
                "➕ <b>افزودن پلن جدید</b>\n\nعنوان پلن را ارسال کنید:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
            return

        if sub_sub == "plan_del_menu":
            cid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            plans = get_fixed_plans(agent_id, category_id=cid)
            await query.edit_message_text(
                "🗑 <b>حذف پلن</b>\n\nیک پلن را برای حذف انتخاب کنید:",
                reply_markup=plans_plan_del_keyboard(plans, cid), parse_mode="HTML",
            )
            return

        if sub_sub == "plan_del":
            pid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            plan = get_fixed_plan(agent_id, pid)
            delete_fixed_plan(agent_id, pid)
            await query.answer("پلن حذف شد.")
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
                await query.answer(f"\u062d\u0627\u0644\u062a \u0646\u0645\u0627\u06cc\u0634 \u0628\u0647 {toggle_mode} \u062a\u063a\u06cc\u06cc\u0631 \u06a9\u0631\u062f.")
                current = get_setting(agent_id, "plan_display_mode", "dynamic")
                try:
                    await query.edit_message_reply_markup(reply_markup=plans_mode_keyboard(current))
                except Exception:
                    pass
                return
        current = get_setting(agent_id, "plan_display_mode", "dynamic")
        try:
            await query.edit_message_text(
                "\U0001f4cb <b>\u0646\u0648\u0639 \u0646\u0645\u0627\u06cc\u0634 \u067e\u0644\u0646\u200c\u0647\u0627</b>\n\n"
                "\u0641\u0642\u0637 \u06cc\u06a9\u06cc \u0627\u0632 \u062f\u0648 \u062d\u0627\u0644\u062a \u0645\u06cc\u200c\u062a\u0648\u0627\u0646\u062f \u0641\u0639\u0627\u0644 \u0628\u0627\u0634\u062f:",
                reply_markup=plans_mode_keyboard(current), parse_mode="HTML",
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
                "\u2795 <b>\u0627\u0641\u0632\u0648\u062f\u0646 \u062f\u0633\u062a\u0647 \u062c\u062f\u06cc\u062f</b>\n\n\u0639\u0646\u0648\u0627\u0646 \u062f\u0633\u062a\u0647 \u0631\u0627 \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
            return

        if sub_sub == "cat_del_menu":
            cats = get_fixed_categories(agent_id)
            try:
                await query.edit_message_text(
                    "\U0001f5d1 <b>\u062d\u0630\u0641 \u062f\u0633\u062a\u0647</b>\n\n\u06cc\u06a9 \u062f\u0633\u062a\u0647 \u0631\u0627 \u0628\u0631\u0627\u06cc \u062d\u0630\u0641 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f:",
                    reply_markup=plans_cat_del_keyboard(cats), parse_mode="HTML",
                )
            except Exception:
                pass
            return

        if sub_sub == "cat_del":
            cid = int(parts[5])
            delete_fixed_category(agent_id, cid)
            await query.answer("\u062f\u0633\u062a\u0647 \u062d\u0630\u0641 \u0634\u062f.")
            await _send_fixed_menu(update, context)
            return

        if sub_sub == "cat_edit":
            cid = int(parts[5])
            context.user_data[UD_STATE] = STATE_FIXED_EDIT_CAT_TITLE
            context.user_data["edit_cat_id"] = cid
            await query.answer()
            await context.bot.send_message(
                query.message.chat_id,
                "\u270f\ufe0f <b>\u0648\u06cc\u0631\u0627\u06cc\u0634 \u0639\u0646\u0648\u0627\u0646</b>\n\n\u0639\u0646\u0648\u0627\u0646 \u062c\u062f\u06cc\u062f \u0631\u0627 \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f:",
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
                "\u2795 <b>\u0627\u0641\u0632\u0648\u062f\u0646 \u067e\u0644\u0646 \u062c\u062f\u06cc\u062f</b>\n\n\u0639\u0646\u0648\u0627\u0646 \u067e\u0644\u0646 \u0631\u0627 \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f:",
                reply_markup=cancel_keyboard(), parse_mode="HTML",
            )
            return

        if sub_sub == "plan_del_menu":
            cid = int(parts[5])
            plans = get_fixed_plans(agent_id, category_id=cid)
            try:
                await query.edit_message_text(
                    "\U0001f5d1 <b>\u062d\u0630\u0641 \u067e\u0644\u0646</b>\n\n\u06cc\u06a9 \u067e\u0644\u0646 \u0631\u0627 \u0628\u0631\u0627\u06cc \u062d\u0630\u0641 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f:",
                    reply_markup=plans_plan_del_keyboard(plans, cid), parse_mode="HTML",
                )
            except Exception:
                pass
            return

        if sub_sub == "plan_del":
            pid = int(parts[5])
            plan = get_fixed_plan(agent_id, pid)
            delete_fixed_plan(agent_id, pid)
            await query.answer("\u067e\u0644\u0646 \u062d\u0630\u0641 \u0634\u062f.")
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
                    "🎁 <b>تخفیف حجمی (ساده)</b>\n\n"
                    "از چه حجمی به بالا تخفیف فعال شود؟ (بر حسب گیگ)\n"
                    "مثال: 50\n"
                    "برای خاموش کردن کامل تخفیف، عدد 0 بفرست.",
                    reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
                return
            if which == "tiers":
                context.user_data[UD_STATE] = STATE_DYN_EDIT_FIELD
                context.user_data[UD_DYN_FIELD] = "discount_tiers"
                await query.answer()
                await context.bot.send_message(
                    query.message.chat_id,
                    "🎚 <b>تخفیف پلکانی</b>\n\n"
                    "هر پله را با فرمت <code>حجم:درصد</code> وارد کن و پله‌ها را با کاما یا خط جدید جدا کن.\n"
                    "مثال: <code>50:5, 100:10, 200:15</code>\n"
                    "یعنی: از ۵۰ گیگ ۵٪، از ۱۰۰ گیگ ۱۰٪ و از ۲۰۰ گیگ ۱۵٪ تخفیف.\n"
                    "برای خاموش کردن تخفیف، عدد 0 بفرست.",
                    reply_markup=cancel_keyboard(), parse_mode="HTML",
                )
                return
            if which == "timer":
                context.user_data[UD_STATE] = STATE_DYN_EDIT_FIELD
                context.user_data[UD_DYN_FIELD] = "discount_timer"
                await query.answer()
                await context.bot.send_message(
                    query.message.chat_id,
                    "⏱ <b>تایمر تخفیف حجمی (ساده)</b>\n\n"
                    "مدت زمان را به ساعت ارسال کن (مثلاً 12 یا 24).\n"
                    "برای اتمام تایمر و خاموش شدن خودکار تخفیف، عدد 0 بفرست.",
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
            "price_per_gb": "\u0642\u06cc\u0645\u062a \u0647\u0631 \u06af\u06cc\u06af \u0631\u0627 (\u062a\u0648\u0645\u0627\u0646) \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f:",
            "price_per_month": "\u0642\u06cc\u0645\u062a \u0647\u0631 \u0645\u0627\u0647 \u0627\u0634\u062a\u0631\u0627\u06a9 \u0631\u0627 (\u062a\u0648\u0645\u0627\u0646) \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f:",
            "volume_range": (
                "\u062a\u0646\u0638\u06cc\u0645 \u062d\u062c\u0645 \u0628\u0647 \u0635\u0648\u0631\u062a: \u062d\u062f\u0627\u0642\u0644_\u062d\u062c\u0645-\u062d\u062f\u0627\u06a9\u062b\u0631_\u062d\u062c\u0645-\u06af\u0627\u0645\n"
                "\u0645\u062b\u0627\u0644: 20-200-20"
            ),
            "time_range": (
                "\u062a\u0646\u0638\u06cc\u0645 \u0632\u0645\u0627\u0646 \u0628\u0647 \u0635\u0648\u0631\u062a: \u062d\u062f\u0627\u0642\u0644_\u0645\u0627\u0647-\u062d\u062f\u0627\u06a9\u062b\u0631_\u0645\u0627\u0647-\u06af\u0627\u0645\n"
                "\u0645\u062b\u0627\u0644: 1-12-1"
            ),
        }
        await query.answer()
        await context.bot.send_message(
            query.message.chat_id,
            prompts.get(field, "\u0644\u0637\u0641\u0627 \u0645\u0642\u062f\u0627\u0631 \u062c\u062f\u06cc\u062f \u0631\u0627 \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f:"),
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
                f"• تخفیف حجمی ساده: از {settings.get('discount_step_gb', 0)} گیگ به بالا، "
                f"{settings.get('discount_percent_step', 0)}٪ تا سقف {settings.get('discount_percent_max', 0)}٪"
            )
        if tiered_enabled:
            discount_info.append(
                f"• تخفیف پلکانی: {plans_storage.format_discount_tiers(settings.get('discount_tiers', []))}"
            )
        if not discount_info:
            discount_info.append("• تخفیفی فعال نیست")
        text = (
            "\u2699\ufe0f <b>\u062a\u0646\u0638\u06cc\u0645 \u067e\u0644\u0646 \u067e\u0648\u06cc\u0627</b>\n\n"
            "\U0001f4b0 \u0642\u06cc\u0645\u062a \u0647\u0631 \u06af\u06cc\u06af: {}\n"
            "\U0001f4b0 \u0642\u06cc\u0645\u062a \u0647\u0631 \u0645\u0627\u0647: {}\n\n"
            "\U0001f4ca \u062d\u062c\u0645 \u0642\u0627\u0628\u0644 \u0641\u0631\u0648\u0634: \u0627\u0632 {} \u062a\u0627 {} \u06af\u06cc\u06af (\u06af\u0627\u0645: {})\n"
            "\u23f0 \u0632\u0645\u0627\u0646 \u0627\u0634\u062a\u0631\u0627\u06a9: \u0627\u0632 {} \u062a\u0627 {} \u0645\u0627\u0647 (\u06af\u0627\u0645: {})\n\n"
            "\U0001f39f <b>\u062a\u062e\u0641\u06cc\u0641 \u0647\u0627:</b>\n{}"
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
    agent_id = get_agent_id(context)
    if not agent_id:
        return False
    state = context.user_data.get(UD_STATE)
    text = update.message.text.strip()

    CANCEL_TEXTS = {"\u0644\u063a\u0648", "\u0628\u0627\u0632\u06af\u0634\u062a", "/cancel"}

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
        await update.message.reply_text("\u2705 \u062f\u0633\u062a\u0647 \u0627\u0636\u0627\u0641\u0647 \u0634\u062f.", reply_markup=main_menu_keyboard())
        await _send_fixed_menu(update, context)
        return True

    # ---- Fixed: edit category title ----
    if state == STATE_FIXED_EDIT_CAT_TITLE:
        cid = context.user_data.get("edit_cat_id")
        if cid:
            edit_fixed_category(agent_id, cid, title=text)
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop("edit_cat_id", None)
        await update.message.reply_text("\u2705 \u0639\u0646\u0648\u0627\u0646 \u0628\u0631\u0648\u0632 \u0634\u062f.", reply_markup=main_menu_keyboard())
        await _send_fixed_menu(update, context)
        return True

    # ---- Fixed: add plan wizard ----
    if state == STATE_FIXED_ADD_PLAN_TITLE:
        context.user_data["fixed_new_plan"] = {"title": text}
        context.user_data[UD_STATE] = STATE_FIXED_ADD_PLAN_GB
        await update.message.reply_text("\U0001f4ca \u062d\u062c\u0645 \u067e\u0644\u0646 \u0631\u0627 \u0628\u0647 \u06af\u06cc\u06af\u0627\u0628\u0627\u06cc\u062a \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f:", reply_markup=cancel_keyboard())
        return True

    if state == STATE_FIXED_ADD_PLAN_GB:
        try:
            gb = float(_normalize_digits(text).replace(",", "."))
        except ValueError:
            await update.message.reply_text("\u274c \u0644\u0637\u0641\u0627 \u06cc\u06a9 \u0639\u062f\u062f \u0645\u0639\u062a\u0628\u0631 \u0628\u0641\u0631\u0633\u062a\u06cc\u062f.", reply_markup=cancel_keyboard())
            return True
        np = context.user_data.get("fixed_new_plan", {})
        np["gb"] = gb
        context.user_data["fixed_new_plan"] = np
        context.user_data[UD_STATE] = STATE_FIXED_ADD_PLAN_DAYS
        await update.message.reply_text("\u23f0 \u062a\u0639\u062f\u0627\u062f \u0631\u0648\u0632\u0647\u0627\u06cc \u067e\u0644\u0646 \u0631\u0627 \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f:", reply_markup=cancel_keyboard())
        return True

    if state == STATE_FIXED_ADD_PLAN_DAYS:
        try:
            days = int(_normalize_digits(text))
        except ValueError:
            await update.message.reply_text("\u274c \u0644\u0637\u0641\u0627 \u06cc\u06a9 \u0639\u062f\u062f \u0645\u0639\u062a\u0628\u0631 \u0628\u0641\u0631\u0633\u062a\u06cc\u062f.", reply_markup=cancel_keyboard())
            return True
        np = context.user_data.get("fixed_new_plan", {})
        np["days"] = days
        context.user_data["fixed_new_plan"] = np
        context.user_data[UD_STATE] = STATE_FIXED_ADD_PLAN_PRICE
        await update.message.reply_text("\U0001f4b0 \u0642\u06cc\u0645\u062a \u067e\u0644\u0646 \u0631\u0627 \u0628\u0647 \u062a\u0648\u0645\u0627\u0646 \u0627\u0631\u0633\u0627\u0644 \u06a9\u0646\u06cc\u062f:", reply_markup=cancel_keyboard())
        return True

    if state == STATE_FIXED_ADD_PLAN_PRICE:
        try:
            price = int(_normalize_digits(text))
        except ValueError:
            await update.message.reply_text("\u274c \u0644\u0637\u0641\u0627 \u06cc\u06a9 \u0639\u062f\u062f \u0645\u0639\u062a\u0628\u0631 \u0628\u0641\u0631\u0633\u062a\u06cc\u062f.", reply_markup=cancel_keyboard())
            return True
        cid = context.user_data.get("fixed_cat_id")
        np = context.user_data.get("fixed_new_plan", {})
        if cid and np.get("title") and np.get("gb") is not None and np.get("days"):
            add_fixed_plan(agent_id, cid, np["title"], price, np["days"], np["gb"])
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop("fixed_cat_id", None)
            context.user_data.pop("fixed_new_plan", None)
            await update.message.reply_text("\u2705 \u067e\u0644\u0646 \u0627\u0636\u0627\u0641\u0647 \u0634\u062f.", reply_markup=main_menu_keyboard())
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
                    await update.message.reply_text("❌ لطفاً عدد حجم را به صورت صحیح وارد کنید (مثلاً 50).", reply_markup=cancel_keyboard())
                    return True
                threshold = max(0, threshold)
                context.user_data[UD_DYN_DISCOUNT_THRESHOLD] = threshold
                context.user_data[UD_DYN_DISCOUNT_PHASE] = "percent"
                await update.message.reply_text(
                    "الان درصد تخفیف را ارسال کن (مثلاً 25).\n"
                    "برای خاموش کردن کامل تخفیف 0 بفرست.",
                    reply_markup=cancel_keyboard(),
                )
                return True

            try:
                percent = int(raw.replace("%", ""))
            except ValueError:
                await update.message.reply_text("❌ لطفاً درصد تخفیف را به صورت عددی بفرست (مثلاً 25).", reply_markup=cancel_keyboard())
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
                await update.message.reply_text("✅ تخفیف حجمی خاموش شد.", reply_markup=main_menu_keyboard())
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
                    f"✅ تخفیف ذخیره شد.\n"
                    f"از {threshold} گیگ به بالا، {percent}٪ تخفیف روی قیمت نهایی اعمال می‌شود.",
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
                    "❌ فرمت پله‌ها معتبر نیست. مثال درست: 50:5,100:10,200:15",
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
                    f"✅ تخفیف پلکانی ذخیره شد.\n{plans_storage.format_discount_tiers(tiers)}",
                    reply_markup=main_menu_keyboard(),
                )
            else:
                await update.message.reply_text("✅ تخفیف پلکانی خاموش شد.", reply_markup=main_menu_keyboard())
            context.user_data.pop(UD_STATE, None)
            context.user_data.pop(UD_DYN_FIELD, None)
            await _roleme_discount_menu(context, update, agent_id)
            return True

        if field == "discount_timer":
            try:
                hours = int(raw)
            except ValueError:
                await update.message.reply_text("❌ لطفاً مدت زمان را به ساعت به صورت عددی ارسال کنید (مثلاً 12).", reply_markup=cancel_keyboard())
                return True

            if hours <= 0:
                _set_discount_settings(
                    agent_id,
                    discount_simple_enabled=False,
                    discount_simple_expire_at=0,
                )
                await update.message.reply_text("✅ تایمر تخفیف حذف شد و تخفیف حجمی ساده خاموش شد.", reply_markup=main_menu_keyboard())
            else:
                expire_at = int(time.time()) + hours * 3600
                _set_discount_settings(
                    agent_id,
                    discount_simple_enabled=True,
                    discount_simple_expire_at=expire_at,
                )
                await update.message.reply_text(
                    "✅ تایمر تخفیف حجمی ساده تنظیم شد.\n"
                    f"تخفیف به مدت {hours} ساعت (تا {datetime.fromtimestamp(expire_at).strftime('%Y-%m-%d %H:%M')}) فعال است "
                    "و پس از اتمام، به‌صورت خودکار خاموش می‌شود.",
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
                await update.message.reply_text("\u274c \u0639\u062f\u062f \u0646\u0627\u0645\u0639\u062a\u0628\u0631.")
                return True
        elif field == "price_per_month":
            try:
                settings["price_per_month"] = int(raw)
            except ValueError:
                await update.message.reply_text("\u274c \u0639\u062f\u062f \u0646\u0627\u0645\u0639\u062a\u0628\u0631.")
                return True
        elif field == "volume_range":
            vals = raw.replace("\u2212", "-").replace("\u2013", "-").split("-")
            if len(vals) == 3:
                try:
                    settings["min_gb"], settings["max_gb"], settings["step_gb"] = int(vals[0]), int(vals[1]), int(vals[2])
                except ValueError:
                    await update.message.reply_text("\u274c \u0641\u0631\u0645\u062a \u0646\u0627\u0645\u0639\u062a\u0628\u0631. \u0627\u0632 \u0641\u0631\u0645\u062a \u062d\u062f\u0627\u0642\u0644-\u062d\u062f\u0627\u06a9\u062b\u0631-\u06af\u0627\u0645 \u0627\u0633\u062a\u0641\u0627\u062f\u0647 \u06a9\u0646\u06cc\u062f.")
                    return True
            else:
                await update.message.reply_text("\u274c \u062f\u0642\u06cc\u0642\u0627 3 \u0645\u0642\u062f\u0627\u0631 \u0628\u0627 \u062e\u0637 \u062a\u06cc\u0631\u0647 \u062c\u062f\u0627 \u06a9\u0646\u06cc\u062f.")
                return True
        elif field == "time_range":
            vals = raw.replace("\u2212", "-").replace("\u2013", "-").split("-")
            if len(vals) == 3:
                try:
                    settings["min_month"], settings["max_month"], settings["step_month"] = int(vals[0]), int(vals[1]), int(vals[2])
                except ValueError:
                    await update.message.reply_text("\u274c \u0641\u0631\u0645\u062a \u0646\u0627\u0645\u0639\u062a\u0628\u0631.")
                    return True
            else:
                await update.message.reply_text("\u274c \u062f\u0642\u06cc\u0642\u0627 3 \u0645\u0642\u062f\u0627\u0631 \u0628\u0627 \u062e\u0637 \u062a\u06cc\u0631\u0647 \u062c\u062f\u0627 \u06a9\u0646\u06cc\u062f.")
                return True
        else:
            return False
        set_setting(agent_id, "dynamic_plan_settings", settings)
        context.user_data.pop(UD_STATE, None)
        context.user_data.pop(UD_DYN_FIELD, None)
        await update.message.reply_text("\u2705 \u0628\u0647 \u200c\u0631\u0648\u0632\u0631\u0633\u0627\u0646\u06cc \u0634\u062f.", reply_markup=main_menu_keyboard())
        await show_menu(update, context)
        return True

    if state == STATE_DYNAMIC_SETTINGS:
        raw = _normalize_digits(text)
        vals = raw.split()
        if len(vals) != 8:
            await update.message.reply_text("\u274c \u062f\u0642\u06cc\u0642\u0627 8 \u0645\u0642\u062f\u0627\u0631 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f.")
            return True
        try:
            nums = [int(v) for v in vals]
        except ValueError:
            await update.message.reply_text("\u274c \u0647\u0645\u0647 \u0645\u0642\u0627\u062f\u06cc\u0631 \u0628\u0627\u06cc\u062f \u0639\u062f\u062f \u0628\u0627\u0634\u0646\u062f.")
            return True
        set_setting(agent_id, "dynamic_plan_settings", {
            "price_per_gb": nums[0], "price_per_month": nums[1],
            "min_gb": nums[2], "max_gb": nums[3], "step_gb": nums[4],
            "min_month": nums[5], "max_month": nums[6], "step_month": nums[7],
        })
        context.user_data.pop(UD_STATE, None)
        await update.message.reply_text("\u2705 \u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u067e\u0648\u06cc\u0627 \u0630\u062e\u06cc\u0631\u0647 \u0634\u062f.", reply_markup=main_menu_keyboard())
        return True

    return False
