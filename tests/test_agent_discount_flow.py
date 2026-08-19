"""تست یکپارچه فلوی ذخیره‌سازی تخفیف در AgentBot (روی agent_bot.db موقت).

کل مسیر را تست می‌کند: ذخیره از طریق set_setting مشابه Handlerهای
AgentBot، خواندن توسط CustomerBot، و رفتار با تایمر.
"""
import time

import pytest

import AgentBot.database as agent_db
import Shared.plans_storage as plans_storage


@pytest.fixture()
def agent_store(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_db, "DB_FILE", tmp_path / "agent_test.db")


def _agent_dyn_settings(agent_id):
    """ترجمه‌ی `_agent_dyn_settings` از CustomerBot."""
    s = agent_db.get_setting(agent_id, "dynamic_plan_settings", {}) or {}
    if isinstance(s, dict) and s:
        return s
    return plans_storage.get_plan_dynamic_settings(agent_id)


def _merge_defaults(settings):
    merged = dict(settings or {})
    for k, v in {
        "discount_simple_enabled": False,
        "discount_step_gb": 50,
        "discount_percent_step": 5,
        "discount_percent_max": 50,
        "discount_tiered_enabled": False,
        "discount_tiers": [],
        "discount_simple_expire_at": 0,
    }.items():
        merged.setdefault(k, v)
    merged["discount_tiers"] = plans_storage.normalize_discount_tiers(merged.get("discount_tiers", []))
    return merged


def test_agent_store_roundtrip_simple_discount(agent_store):
    agent_id = 42
    # مثل شاخه‌ی discount_simple (مرحله دوم) در AgentBot
    settings = _merge_defaults(agent_db.get_setting(agent_id, "dynamic_plan_settings", {}))
    settings["discount_step_gb"] = 70
    settings["discount_percent_step"] = 15
    settings["discount_percent_max"] = 15
    settings["discount_simple_enabled"] = True
    settings["discount_simple_expire_at"] = 0
    settings["discount_tiers"] = []
    agent_db.set_setting(agent_id, "dynamic_plan_settings", settings)

    dyn = _agent_dyn_settings(agent_id)
    assert dyn["discount_simple_enabled"] is True
    assert dyn["discount_step_gb"] == 70
    assert plans_storage.is_simple_discount_enabled(dyn) is True


def test_agent_store_roundtrip_timated_expiry(agent_store):
    agent_id = 43
    settings = _merge_defaults(agent_db.get_setting(agent_id, "dynamic_plan_settings", {}))
    settings["discount_simple_enabled"] = True
    settings["discount_simple_expire_at"] = int(time.time()) + 5 * 3600
    agent_db.set_setting(agent_id, "dynamic_plan_settings", settings)

    dyn = _agent_dyn_settings(agent_id)
    assert plans_storage.is_simple_discount_active(dyn) is True

    # شبیه‌سازی انقضا
    dyn["discount_simple_expire_at"] = int(time.time()) - 5 * 3600
    assert plans_storage.is_simple_discount_active(dyn) is False


def test_agent_store_tiers_enabled(agent_store):
    agent_id = 44
    settings = _merge_defaults(agent_db.get_setting(agent_id, "dynamic_plan_settings", {}))
    settings["discount_tiers"] = plans_storage.parse_discount_tiers_text("100:10, 200:15")
    settings["discount_tiered_enabled"] = True
    agent_db.set_setting(agent_id, "dynamic_plan_settings", settings)

    dyn = _agent_dyn_settings(agent_id)
    assert plans_storage.is_tiered_discount_enabled(dyn) is True
    assert len(dyn["discount_tiers"]) == 2


def test_agent_store_toggle_off_simple(agent_store):
    agent_id = 45
    settings = _merge_defaults(agent_db.get_setting(agent_id, "dynamic_plan_settings", {}))
    settings["discount_simple_enabled"] = True
    agent_db.set_setting(agent_id, "dynamic_plan_settings", settings)

    # مثل تابع _discount_simple_toggle وقتی فعال بود
    settings = _merge_defaults(agent_db.get_setting(agent_id, "dynamic_plan_settings", {}))
    settings["discount_simple_enabled"] = False
    settings["discount_simple_expire_at"] = 0
    agent_db.set_setting(agent_id, "dynamic_plan_settings", settings)

    dyn = _agent_dyn_settings(agent_id)
    assert plans_storage.is_simple_discount_enabled(dyn) is False


def test_customer_reads_agent_settings(agent_store):
    """مطمئن می‌شویم CustomerBot تنظیمات ذخیره‌شده توسط AgentBot را می‌بیند."""
    agent_id = 46
    settings = _merge_defaults(agent_db.get_setting(agent_id, "dynamic_plan_settings", {}))
    settings["discount_step_gb"] = 50
    settings["discount_percent_step"] = 10
    settings["discount_percent_max"] = 30
    settings["discount_simple_enabled"] = True
    settings["discount_tiers"] = [{"gb": 100, "percent": 15}]
    settings["discount_tiered_enabled"] = True
    agent_db.set_setting(agent_id, "dynamic_plan_settings", settings)

    dyn = _agent_dyn_settings(agent_id)
    assert plans_storage.is_simple_discount_enabled(dyn) is True
    assert plans_storage.is_tiered_discount_enabled(dyn) is True
    assert len(dyn["discount_tiers"]) == 1


def test_discount_menu_text_simple_and_tiers(agent_store):
    """تولید متن منوی مدیریت تخفیف معادل تابع _render/_roleme_discount_menu."""
    agent_id = 47
    settings = _merge_defaults(agent_db.get_setting(agent_id, "dynamic_plan_settings", {}))
    settings["discount_step_gb"] = 50
    settings["discount_percent_step"] = 5
    settings["discount_percent_max"] = 30
    settings["discount_simple_enabled"] = True
    settings["discount_tiers"] = [{"gb": 100, "percent": 10}]
    settings["discount_tiered_enabled"] = True
    agent_db.set_setting(agent_id, "dynamic_plan_settings", settings)

    dyn = _agent_dyn_settings(agent_id)
    simple_enabled = plans_storage.is_simple_discount_enabled(dyn)
    tiered_enabled = plans_storage.is_tiered_discount_enabled(dyn)

    lines = [
        "🎛 مدیریت حرفه‌ای تخفیف‌ها",
        f"🎁 تخفیف حجمی ساده: {'فعال ✅' if simple_enabled else 'غیرفعال ❌'}",
        f"🎚 تخفیف پلاکانی: {'فعال ✅' if tiered_enabled else 'غیرفعال ❌'}",
    ]
    if simple_enabled:
        lines.append(
            f"• تخفیف حجمی ساده: از {dyn['discount_step_gb']} گیگ به بالا، "
            f"{dyn['discount_percent_step']}٪ تا سقف {dyn['discount_percent_max']}٪"
        )
    if tiered_enabled:
        lines.append(f"• پله‌های تخفیف پلاکانی: {plans_storage.format_discount_tiers(dyn.get('discount_tiers', []))}")

    text = "\n".join(lines)
    assert "تخفیف حجمی ساده: فعال ✅" in text
    assert "تخفیف پلاکانی: فعال ✅" in text
    assert "از 50 گیگ به بالا" in text
    assert "از 100 گیگ: 10٪" in text


def test_discount_menu_text_empty(agent_store):
    """وقتی تخفیفی تنظیم نشده، هر دو غیرفعال نمایش داده می‌شوند."""
    agent_id = 48
    settings = _merge_defaults(agent_db.get_setting(agent_id, "dynamic_plan_settings", {}))
    agent_db.set_setting(agent_id, "dynamic_plan_settings", settings)

    dyn = _agent_dyn_settings(agent_id)
    assert plans_storage.is_simple_discount_enabled(dyn) is False
    assert plans_storage.is_tiered_discount_enabled(dyn) is False
    assert plans_storage.format_discount_tiers(dyn.get("discount_tiers", [])) == "غیرفعال"