# Shared/plans_storage.py
# ذخیره‌سازی پلن‌ها و تنظیمات‌شان در plans.json

from pathlib import Path
import json
from typing import Dict, Any, List, Optional

_PLANS_FILE = Path(__file__).with_name("plans.json")

# ساختار plans.json:
# {
#   "servers": {
#     "1": {
#       "display_mode": "fixed|dynamic|mixed",
#       "categories": [
#         {"id": 1, "title": "سرویس نامحدود", "priority": 0}
#       ],
#       "plans": [
#         {"id": 1, "category_id": 1, "title": "۳۰ روزه نامحدود",
#          "price": 270000, "days": 30, "gb": 0, "priority": 0}
#       ],
#       "dynamic_settings": {...},
#       "next_category_id": 2,
#       "next_plan_id": 2
#     }
#   }
# }

# ------------------ IO کمکی ------------------


def _load_all_plans() -> Dict[str, Any]:
    if not _PLANS_FILE.exists():
        return {"servers": {}}
    try:
        with _PLANS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"servers": {}}

    if not isinstance(data, dict):
        return {"servers": {}}
    data.setdefault("servers", {})
    return data


def _save_all_plans(data: Dict[str, Any]) -> None:
    _PLANS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _PLANS_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_server_block(
    data: Dict[str, Any], server_id: int, create: bool = False
) -> Optional[Dict[str, Any]]:
    key = str(server_id)
    servers = data.setdefault("servers", {})
    if key in servers:
        return servers[key]
    if not create:
        return None

    block: Dict[str, Any] = {
        "display_mode": "dynamic",
        "categories": [],
        "plans": [],
        "dynamic_settings": {},
        "next_category_id": 1,
        "next_plan_id": 1,
    }
    servers[key] = block
    return block


# ------------------ حالت نمایش پلن‌ها ------------------

def get_plan_display_mode(server_id: int) -> str:
    """fixed / dynamic / mixed (پیش‌فرض dynamic)"""
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=True)
    return block.get("display_mode", "dynamic")


def set_plan_display_mode(server_id: int, mode: str) -> None:
    if mode not in {"fixed", "dynamic", "mixed"}:
        raise ValueError("Invalid plan display mode")
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=True)
    block["display_mode"] = mode
    _save_all_plans(data)


# ------------------ دسته‌بندی پلن‌ها ------------------

def get_plan_categories(server_id: int) -> List[Dict[str, Any]]:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=True)
    cats = block.get("categories") or []
    return sorted(
        cats,
        key=lambda c: (int(c.get("priority", 0)), int(c.get("id", 0))),
    )


def add_plan_category(server_id: int, title: str, priority: int = 0) -> Dict[str, Any]:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=True)

    cid = int(block.get("next_category_id") or 1)
    block["next_category_id"] = cid + 1

    cat = {"id": cid, "title": title, "priority": int(priority)}
    block.setdefault("categories", []).append(cat)

    _save_all_plans(data)
    return cat


def edit_plan_category(
    server_id: int,
    category_id: int,
    *,
    title: Optional[str] = None,
    priority: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=False)
    if not block:
        return None

    for c in block.get("categories", []):
        if int(c.get("id")) == int(category_id):
            if title is not None:
                c["title"] = title
            if priority is not None:
                c["priority"] = int(priority)
            _save_all_plans(data)
            return c
    return None


def delete_plan_category(server_id: int, category_id: int) -> bool:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=False)
    if not block:
        return False

    cats = block.get("categories", [])
    new_cats = [c for c in cats if int(c.get("id")) != int(category_id)]
    if len(new_cats) == len(cats):
        return False

    block["categories"] = new_cats

    # category_id را روی پلن‌ها پاک می‌کنیم
    for p in block.get("plans", []):
        if int(p.get("category_id") or 0) == int(category_id):
            p["category_id"] = None

    _save_all_plans(data)
    return True


# ------------------ پلن‌های ثابت ------------------

def get_plans(server_id: int, *, category_id: Optional[int] = None) -> List[Dict[str, Any]]:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=True)
    plans = block.get("plans") or []

    if category_id is not None:
        # وقتی دسته مشخص شده، فقط همون دسته
        plans = [
            p
            for p in plans
            if int(p.get("category_id") or 0) == int(category_id)
        ]
    else:
        # وقتی دسته مشخص نشده (مثل ربات کاربران)،
        # پلن‌های بدون دسته (category_id is None) را نشان نده
        plans = [
            p
            for p in plans
            if p.get("category_id") is not None
        ]

    return sorted(
        plans,
        key=lambda p: (int(p.get("priority", 0)), int(p.get("id", 0))),
    )


def get_plan(server_id: int, plan_id: int) -> Optional[Dict[str, Any]]:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=False)
    if not block:
        return None
    for p in block.get("plans", []):
        if int(p.get("id")) == int(plan_id):
            return p
    return None


def add_plan(
    server_id: int,
    category_id: Optional[int],
    title: str,
    price: int,
    days: int,
    gb: float,
    priority: int = 0,
) -> Dict[str, Any]:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=True)

    pid = int(block.get("next_plan_id") or 1)
    block["next_plan_id"] = pid + 1

    plan = {
        "id": pid,
        "category_id": int(category_id) if category_id is not None else None,
        "title": title,
        "price": int(price),
        "days": int(days),
        "gb": float(gb),
        "priority": int(priority),
    }
    block.setdefault("plans", []).append(plan)
    _save_all_plans(data)
    return plan


def edit_plan(
    server_id: int,
    plan_id: int,
    *,
    category_id: Optional[int] = None,
    title: Optional[str] = None,
    price: Optional[int] = None,
    days: Optional[int] = None,
    gb: Optional[float] = None,
    priority: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=False)
    if not block:
        return None

    for p in block.get("plans", []):
        if int(p.get("id")) == int(plan_id):
            if category_id is not None:
                p["category_id"] = int(category_id)
            if title is not None:
                p["title"] = title
            if price is not None:
                p["price"] = int(price)
            if days is not None:
                p["days"] = int(days)
            if gb is not None:
                p["gb"] = float(gb)
            if priority is not None:
                p["priority"] = int(priority)
            _save_all_plans(data)
            return p
    return None


def delete_plan(server_id: int, plan_id: int) -> bool:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=False)
    if not block:
        return False

    plans = block.get("plans", [])
    new_plans = [p for p in plans if int(p.get("id")) != int(plan_id)]
    if len(new_plans) == len(plans):
        return False

    block["plans"] = new_plans
    _save_all_plans(data)
    return True


# ------------------ تنظیمات پلن پویا ------------------

_DEFAULT_DYNAMIC_SETTINGS = {
    "price_per_gb": 2000,
    "price_per_month": 30000,
    "min_gb": 20,
    "max_gb": 200,
    "step_gb": 20,
    "min_month": 1,
    "max_month": 12,
    "step_month": 1,
    "discount_step_gb": 50,
    "discount_percent_step": 5,
    "discount_percent_max": 50,
}


def get_plan_dynamic_settings(server_id: int) -> Dict[str, Any]:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=True)
    dyn = block.get("dynamic_settings") or {}
    for k, v in _DEFAULT_DYNAMIC_SETTINGS.items():
        dyn.setdefault(k, v)
    block["dynamic_settings"] = dyn
    _save_all_plans(data)
    return dyn


def set_plan_dynamic_settings(server_id: int, **kwargs: Any) -> Dict[str, Any]:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=True)
    dyn = block.get("dynamic_settings") or {}

    for k, v in kwargs.items():
        if k not in _DEFAULT_DYNAMIC_SETTINGS:
            continue
        if isinstance(_DEFAULT_DYNAMIC_SETTINGS[k], int):
            dyn[k] = int(v)
        else:
            dyn[k] = float(v)

    block["dynamic_settings"] = dyn
    _save_all_plans(data)
    return dyn
