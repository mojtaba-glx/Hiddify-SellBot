import pathlib
import time

import pytest

import Shared.plans_storage as plans_storage


@pytest.fixture()
def fresh_plans(monkeypatch, tmp_path):
    target = tmp_path / "plans.json"
    monkeypatch.setattr(plans_storage, "_PLANS_FILE", target)
    if target.exists():
        target.unlink()
    return plans_storage


def test_default_no_timer_active(fresh_plans):
    s = fresh_plans.get_plan_dynamic_settings(1)
    assert s.get("discount_simple_expire_at") == 0
    assert fresh_plans.is_simple_discount_active(s) is True


def test_future_timer_active(fresh_plans):
    future = int(time.time()) + 12 * 3600
    fresh_plans.set_plan_dynamic_settings(
        1, discount_simple_enabled=True, discount_simple_expire_at=future
    )
    s = fresh_plans.get_plan_dynamic_settings(1)
    assert fresh_plans.is_simple_discount_active(s) is True


def test_expired_timer_inactive(fresh_plans):
    past = int(time.time()) - 3600
    fresh_plans.set_plan_dynamic_settings(
        1, discount_simple_enabled=True, discount_simple_expire_at=past
    )
    s = fresh_plans.get_plan_dynamic_settings(1)
    assert fresh_plans.is_simple_discount_active(s) is False


def test_disabled_inactive_even_with_future_timer(fresh_plans):
    future = int(time.time()) + 12 * 3600
    fresh_plans.set_plan_dynamic_settings(
        1, discount_simple_enabled=False, discount_simple_expire_at=future
    )
    s = fresh_plans.get_plan_dynamic_settings(1)
    assert fresh_plans.is_simple_discount_active(s) is False


def test_timer_zero_means_infinite(fresh_plans):
    fresh_plans.set_plan_dynamic_settings(
        1, discount_simple_enabled=True, discount_simple_expire_at=0
    )
    s = fresh_plans.get_plan_dynamic_settings(1)
    assert s.get("discount_simple_expire_at") == 0
    assert fresh_plans.is_simple_discount_active(s) is True