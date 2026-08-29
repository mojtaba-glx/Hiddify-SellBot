# Shared/plans_storage.py
# ذخیره‌سازی پلن‌ها و تنظیمات‌شان در plans.json

from pathlib import Path
import fcntl
import json
import os
import time
from contextlib import contextmanager
from typing import Dict, Any, List, Optional

_PLANS_FILE = Path(__file__).with_name("plans.json")

_PERSIAN_DIGITS_TRANS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)

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


def _resolve_display_mode(block: Dict[str, Any]) -> str:
    raw_mode = str(block.get("display_mode") or block.get("mode") or "").strip().lower()
    if raw_mode in {"fixed", "dynamic", "mixed"}:
        return raw_mode
    return "dynamic"


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
    """ذخیره اتمیک: ابتدا در فایل موقت، سپس جایگزینی اتمی — وسط نوشتن هرگز
    فایل اصلی را خراب نمی‌کند (کرش/قطع برق = دیتای قبلی سالم می‌ماند)."""
    _PLANS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _PLANS_FILE.with_name(_PLANS_FILE.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, _PLANS_FILE)


@contextmanager
def _plans_file_lock():
    """قفل بین-پروسه‌ای روی plans.json — چرخه خواندن→تغییر→نوشتن اتمیک می‌شود
    تا AdminBot و CustomerBot تغییرات هم را بازنویسی نکنند (lost update)."""
    lock_path = _PLANS_FILE.with_name(_PLANS_FILE.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _get_server_block(
    data: Dict[str, Any], server_id: int, create: bool = False
) -> Optional[Dict[str, Any]]:
    key = str(server_id)
    servers = data.setdefault("servers", {})
    if key in servers:
        block = servers[key]
        if isinstance(block, dict):
            block.setdefault("display_mode", _resolve_display_mode(block))
        return block
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
    return _resolve_display_mode(block)


def set_plan_display_mode(server_id: int, mode: str) -> None:
    if mode not in {"fixed", "dynamic", "mixed"}:
        raise ValueError("Invalid plan display mode")
    with _plans_file_lock():
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
    with _plans_file_lock():
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
    with _plans_file_lock():
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
    with _plans_file_lock():
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
    data = None
    with _plans_file_lock():
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
    with _plans_file_lock():
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
    with _plans_file_lock():
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
    "discount_simple_enabled": True,
    "discount_step_gb": 50,
    "discount_percent_step": 5,
    "discount_percent_max": 50,
    "discount_tiered_enabled": False,
    "discount_tiers": [],
    "discount_simple_expire_at": 0,
}


def _normalize_digit_text(value: Any) -> str:
    return str(value or "").translate(_PERSIAN_DIGITS_TRANS)


def is_simple_discount_active(settings: Dict[str, Any]) -> bool:
    """تخفیف حجمی ساده را فعال تلقی می‌کند مگر اینکه تایمر آن منقضی شده باشد."""
    if not settings or not bool(settings.get("discount_simple_enabled", False)):
        return False
    expire_at = settings.get("discount_simple_expire_at") or 0
    try:
        expire_at = float(expire_at)
    except (TypeError, ValueError):
        return True
    if expire_at <= 0:
        return True
    return time.time() < expire_at


def is_simple_discount_enabled(settings: Dict[str, Any]) -> bool:
    """تشخیص فعال بودن تخفیف حجمی ساده با احترام به تایمر و سازگاری با حالت قدیمی."""
    if not settings:
        return False
    if "discount_simple_enabled" in settings:
        return is_simple_discount_active(settings)
    discount_tiers = normalize_discount_tiers(settings.get("discount_tiers", []))
    return (
        not discount_tiers
        and int(settings.get("discount_step_gb", 0)) > 0
        and int(settings.get("discount_percent_step", 0)) > 0
        and int(settings.get("discount_percent_max", 0)) > 0
    )


def is_tiered_discount_enabled(settings: Dict[str, Any]) -> bool:
    """تشخیص فعال بودن تخفیف پلاکانی با سازگاری با حالت قدیمی."""
    if not settings:
        return False
    if "discount_tiered_enabled" in settings:
        return bool(settings.get("discount_tiered_enabled"))
    return bool(normalize_discount_tiers(settings.get("discount_tiers", [])))


def format_discount_tiers(tiers: Any) -> str:
    """نمایش متنی پله‌های تخفیف، مثل: «از 50 گیگ: 5٪ | از 100 گیگ: 10٪»."""
    normalized = normalize_discount_tiers(tiers)
    if not normalized:
        return "غیرفعال"
    return " | ".join(f"از {item['gb']} گیگ: {item['percent']}٪" for item in normalized)


def parse_discount_tiers_text(text: Any) -> List[Dict[str, int]]:
    """تبدیل متن پله‌های تخفیف («50:5,100:10» یا «50=5-100=10») به لیست پله‌ها."""
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

    tiers = normalize_discount_tiers(items)
    if not tiers:
        raise ValueError
    return tiers


def normalize_discount_tiers(raw: Any) -> List[Dict[str, int]]:
    tiers_by_gb: Dict[int, int] = {}
    if isinstance(raw, dict):
        raw = raw.items()
    if not isinstance(raw, list) and not isinstance(raw, tuple):
        return []

    for item in raw:
        try:
            if isinstance(item, dict):
                gb = item.get("gb", item.get("threshold_gb", item.get("threshold")))
                percent = item.get("percent", item.get("discount_percent"))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                gb, percent = item[0], item[1]
            else:
                continue
            gb = int(float(_normalize_digit_text(gb).replace(",", "").strip()))
            percent = int(
                float(
                    _normalize_digit_text(percent)
                    .replace("%", "")
                    .replace("٪", "")
                    .replace(",", "")
                    .strip()
                )
            )
        except (TypeError, ValueError):
            continue
        if gb <= 0 or percent <= 0:
            continue
        tiers_by_gb[gb] = max(0, min(percent, 100))

    return [
        {"gb": gb, "percent": tiers_by_gb[gb]}
        for gb in sorted(tiers_by_gb)
    ]


def get_plan_dynamic_settings(server_id: int) -> Dict[str, Any]:
    data = _load_all_plans()
    block = _get_server_block(data, server_id, create=True)
    dyn = block.get("dynamic_settings") or {}
    for k, v in _DEFAULT_DYNAMIC_SETTINGS.items():
        dyn.setdefault(k, v)
    dyn["discount_tiers"] = normalize_discount_tiers(dyn.get("discount_tiers", []))
    block["dynamic_settings"] = dyn
    _save_all_plans(data)
    return dyn


def set_plan_dynamic_settings(server_id: int, **kwargs: Any) -> Dict[str, Any]:
    with _plans_file_lock():
        data = _load_all_plans()
        block = _get_server_block(data, server_id, create=True)
        dyn = block.get("dynamic_settings") or {}

        for k, v in kwargs.items():
            if k not in _DEFAULT_DYNAMIC_SETTINGS:
                continue
            if isinstance(_DEFAULT_DYNAMIC_SETTINGS[k], list):
                dyn[k] = normalize_discount_tiers(v)
            elif isinstance(_DEFAULT_DYNAMIC_SETTINGS[k], bool):
                if isinstance(v, bool):
                    dyn[k] = v
                else:
                    dyn[k] = str(v).strip().lower() in {"1", "true", "yes", "on"}
            elif isinstance(_DEFAULT_DYNAMIC_SETTINGS[k], int):
                dyn[k] = int(v)
            else:
                dyn[k] = float(v)

        block["dynamic_settings"] = dyn
        _save_all_plans(data)
        return dyn
