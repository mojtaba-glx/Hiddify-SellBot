"""تست‌های بخش «مدیریت حرفه‌ای تخفیف‌ها» در ربات نمایندگی (AgentBot).

این تست‌ها روی منطق مشترک (Shared/plans_storage) و محاسبه قیمت
(ترجمه‌ی `_calc_dynamic_price` در CustomerBot) تمرکز دارند تا بدون
نیاز به launch ربات، رفتار بخش را تضمین کنند.
"""
import time

import pytest

import Shared.plans_storage as plans_storage


# ------- parsing پله‌ها -------
def test_parse_tiers_basic():
    tiers = plans_storage.parse_discount_tiers_text("50:5,100:10,200:15")
    assert tiers == [
        {"gb": 50, "percent": 5},
        {"gb": 100, "percent": 10},
        {"gb": 200, "percent": 15},
    ]


def test_parse_tiers_alt_separators():
    tiers = plans_storage.parse_discount_tiers_text("50=5\n100-10، 200:20")
    assert [t["gb"] for t in tiers] == [50, 100, 200]
    assert [t["percent"] for t in tiers] == [5, 10, 20]
    tiers = plans_storage.parse_discount_tiers_text("۵۰:۵، ۱۰۰:۱۰")
    assert tiers == [{"gb": 50, "percent": 5}, {"gb": 100, "percent": 10}]


def test_parse_tiers_zero_disables():
    assert plans_storage.parse_discount_tiers_text("0") == []
    assert plans_storage.parse_discount_tiers_text("خاموش") == []


def test_parse_tiers_invalid_raises():
    with pytest.raises(ValueError):
        plans_storage.parse_discount_tiers_text("بدون فرمت")


# ------- format / enable -------
def test_format_tiers_empty():
    assert plans_storage.format_discount_tiers([]) == "غیرفعال"


def test_is_simple_discount_enabled_legacy():
    # بدون کلید صریح، اگر step/percent/max تنظیم شده و tier خالی باشد فعال است
    s = {"discount_step_gb": 50, "discount_percent_step": 5, "discount_percent_max": 50}
    assert plans_storage.is_simple_discount_enabled(s) is True


def test_is_simple_discount_enabled_explicit_false():
    s = {"discount_simple_enabled": False, "discount_step_gb": 50}
    assert plans_storage.is_simple_discount_enabled(s) is False


def test_is_simple_discount_enabled_timer_expired():
    s = {
        "discount_simple_enabled": True,
        "discount_simple_expire_at": time.time() - 100,
    }
    assert plans_storage.is_simple_discount_enabled(s) is False


def test_is_tiered_discount_enabled_legacy():
    s = {"discount_tiers": [{"gb": 50, "percent": 5}]}
    assert plans_storage.is_tiered_discount_enabled(s) is True


def test_is_tiered_discount_enabled_explicit_false():
    s = {"discount_tiered_enabled": False, "discount_tiers": [{"gb": 50, "percent": 5}]}
    assert plans_storage.is_tiered_discount_enabled(s) is False


# ------- price calculation (مثل CustomerBot) -------
def _calc(gb, months, dyn):
    """ترجمه‌ی `_calc_dynamic_price` از CustomerBot/handlers/callback.py."""
    settings = dyn or {}
    gb_val = max(0, int(gb))
    months_val = max(0, int(months))
    price_per_gb = max(0, int(settings.get("price_per_gb", 0)) or 0)
    price_per_month = max(0, int(settings.get("price_per_month", 0)) or 0)
    base_price = (gb_val * price_per_gb) + (months_val * price_per_month)

    discount_step_gb = max(0, int(settings.get("discount_step_gb", 0)) or 0)
    discount_percent_step = max(0, int(settings.get("discount_percent_step", 0)) or 0)
    discount_percent_max = max(0, int(settings.get("discount_percent_max", 0)) or 0)
    discount_tiered_enabled = bool(settings.get("discount_tiered_enabled", False))

    off_percent = 0
    tiers = plans_storage.normalize_discount_tiers(settings.get("discount_tiers", []))
    if discount_tiered_enabled and tiers:
        tiered_off = 0
        for tier in tiers:
            if gb_val >= int(tier["gb"]):
                tiered_off = int(tier["percent"])
            else:
                break
        off_percent = max(off_percent, max(0, min(tiered_off, 100)))

    discount_simple_enabled = plans_storage.is_simple_discount_active(settings)
    if discount_simple_enabled and discount_step_gb > 0 and discount_percent_step > 0 and gb_val >= discount_step_gb:
        stages = gb_val // discount_step_gb
        simple_off = stages * discount_percent_step
        if discount_percent_max > 0:
            simple_off = min(simple_off, discount_percent_max)
        off_percent = max(off_percent, max(0, min(simple_off, 100)))

    final_price = int(round(base_price * (100 - off_percent) / 100))
    return max(0, final_price), off_percent


def test_price_no_discount():
    s = {"price_per_gb": 1000, "price_per_month": 20000}
    price, off = _calc(10, 1, s)
    assert price == (10 * 1000) + 20000
    assert off == 0


def test_price_simple_discount_enabled():
    s = {
        "price_per_gb": 1000, "price_per_month": 0,
        "discount_simple_enabled": True,
        "discount_step_gb": 50, "discount_percent_step": 10, "discount_percent_max": 30,
    }
    price, off = _calc(50, 1, s)  # 50000 * 10% off = 45000, off=10
    assert off == 10
    assert price == 45000


def test_price_simple_discount_expired_timer():
    s = {
        "price_per_gb": 1000, "price_per_month": 0,
        "discount_simple_enabled": True,
        "discount_simple_expire_at": time.time() - 100,  # منقضی شده
        "discount_step_gb": 50, "discount_percent_step": 10, "discount_percent_max": 30,
    }
    price, off = _calc(50, 1, s)
    assert off == 0  # تایمر منقضی -> بدون تخفیف
    assert price == 50000


def test_price_tiered_discount():
    s = {
        "price_per_gb": 1000, "price_per_month": 0,
        "discount_tiered_enabled": True,
        "discount_tiers": [{"gb": 50, "percent": 5}, {"gb": 100, "percent": 10}],
    }
    price, off = _calc(60, 1, s)
    assert off == 5  # بالاترین پله زیر 60 گیگ = 5%
    assert price == 57000


def test_price_tiers_win_over_simple():
    # وقتی tiered فعال است و tiers >= step باشد، مقدار بزرگ‌تر اعمال می‌شود
    s = {
        "price_per_gb": 1000, "price_per_month": 0,
        "discount_simple_enabled": True,
        "discount_step_gb": 50, "discount_percent_step": 5, "discount_percent_max": 30,
        "discount_tiered_enabled": True,
        "discount_tiers": [{"gb": 50, "percent": 8}],
    }
    price, off = _calc(50, 1, s)
    assert off == 8  # پلکانی 8% بیشتر از ساده 5%
    assert price == 46000