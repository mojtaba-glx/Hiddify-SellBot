import json
import os
import threading
from typing import Dict, List, Optional, Any
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
USERBOT_SET_WELCOME = "userbot_set_welcome"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "servers.json")

_lock = threading.Lock()


def _load_db() -> Dict[str, Any]:
    """بارگذاری دیتابیس از فایل JSON"""
    if not os.path.exists(DB_PATH):
        return {"servers": []}

    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "servers" not in data or not isinstance(data["servers"], list):
                return {"servers": []}
            return data
    except (json.JSONDecodeError, OSError):
        # اگر فایل خراب بود، از صفر شروع می‌کنیم
        return {"servers": []}


def _save_db(data: Dict[str, Any]) -> None:
    """ذخیره دیتابیس در فایل JSON"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _lock:
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# =========================
#         سرورها
# =========================
def get_servers() -> List[Dict[str, Any]]:
    """برگرداندن لیست همه سرورها"""
    db = _load_db()
    return db.get("servers", [])


def _save_servers(servers: List[Dict[str, Any]]) -> None:
    db = {"servers": servers}
    _save_db(db)


def get_server_by_id(server_id: int) -> Optional[Dict[str, Any]]:
    """گرفتن یک سرور بر اساس ID"""
    servers = get_servers()
    for s in servers:
        if s.get("id") == server_id:
            return s
    return None


def add_server(server: Dict[str, Any]) -> Dict[str, Any]:
    """اضافه کردن سرور جدید و برگرداندن آن با id نهایی"""
    servers = get_servers()

    next_id = max([s.get("id", 0) for s in servers] or [0]) + 1
    server = dict(server)
    server["id"] = next_id
    # اگر users وجود نداشت، خالی تنظیم می‌کنیم
    server.setdefault("users", [])
    # اگر plans وجود نداشت، خالی تنظیم می‌کنیم
    server.setdefault("plans", [])
        # لیست دامنه‌های نمایش برای لینک/کانفیگ کاربران
    server.setdefault("domains", [])

    servers.append(server)
    _save_servers(servers)
    return server


def update_server(server_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """به‌روزرسانی یک سرور"""
    servers = get_servers()
    for idx, s in enumerate(servers):
        if s.get("id") == server_id:
            new_s = {**s, **updates, "id": server_id}
            servers[idx] = new_s
            _save_servers(servers)
            return new_s
    return None


def delete_server(server_id: int) -> bool:
    """حذف سرور"""
    servers = get_servers()
    new_servers = [s for s in servers if s.get("id") != server_id]
    if len(new_servers) == len(servers):
        return False
    _save_servers(new_servers)
    return True


# =========================
#         کاربران سرور
# =========================
def get_users(server_id: int) -> List[Dict[str, Any]]:
    """لیست کاربران یک سرور"""
    servers = get_servers()
    for s in servers:
        if s.get("id") == server_id:
            users = s.get("users") or []
            return users
    return []


def get_user(server_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """گرفتن یک کاربر از روی server_id و user_id"""
    users = get_users(server_id)
    for u in users:
        if u.get("id") == user_id:
            return u
    return None


def add_user(server_id: int, user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """افزودن کاربر جدید به یک سرور"""
    servers = get_servers()
    for idx, s in enumerate(servers):
        if s.get("id") == server_id:
            users = s.get("users") or []
            next_uid = max([u.get("id", 0) for u in users] or [0]) + 1
            user = dict(user)
            user["id"] = next_uid
            users.append(user)
            s["users"] = users
            servers[idx] = s
            _save_servers(servers)
            return user
    return None


def update_user(server_id: int, user_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """به‌روزرسانی اطلاعات یک کاربر"""
    servers = get_servers()
    for s_idx, s in enumerate(servers):
        if s.get("id") == server_id:
            users = s.get("users") or []
            for u_idx, u in enumerate(users):
                if u.get("id") == user_id:
                    new_u = {**u, **updates, "id": user_id}
                    users[u_idx] = new_u
                    s["users"] = users
                    servers[s_idx] = s
                    _save_servers(servers)
                    return new_u
    return None


def delete_user(server_id: int, user_id: int) -> bool:
    """حذف یک کاربر از سرور"""
    servers = get_servers()
    changed = False
    for s_idx, s in enumerate(servers):
        if s.get("id") == server_id:
            users = s.get("users") or []
            new_users = [u for u in users if u.get("id") != user_id]
            if len(new_users) != len(users):
                s["users"] = new_users
                servers[s_idx] = s
                changed = True
            break

    if changed:
        _save_servers(servers)
        return True
    return False


# =========================
#            پلن‌ها
# =========================
def _legacy_get_plans(server_id: int) -> List[Dict[str, Any]]:
    """
    برگرداندن لیست پلن‌های یک سرور.
    اگر برای سرور plans تعریف نشده باشد، لیست خالی برمی‌گرداند.
    """
    servers = get_servers()
    for s in servers:
        if s.get("id") == server_id:
            return s.get("plans") or []
    return []


def _legacy_get_plan(server_id: int, plan_id: int) -> Optional[Dict[str, Any]]:
    """گرفتن یک پلن مشخص از روی server_id و plan_id"""
    plans = _legacy_get_plans(server_id)
    for p in plans:
        if p.get("id") == plan_id:
            return p
    return None


def _legacy_add_plan(server_id: int, plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    افزودن پلن جدید به یک سرور.
    خروجی: پلن با id نهایی، یا None اگر سرور پیدا نشد.
    """
    servers = get_servers()
    for s_idx, s in enumerate(servers):
        if s.get("id") == server_id:
            plans = s.get("plans") or []
            next_pid = max([p.get("id", 0) for p in plans] or [0]) + 1
            plan = dict(plan)
            plan["id"] = next_pid
            plans.append(plan)
            s["plans"] = plans
            servers[s_idx] = s
            _save_servers(servers)
            return plan
    return None


def _legacy_update_plan(server_id: int, plan_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    ویرایش یک پلن موجود.
    """
    servers = get_servers()
    for s_idx, s in enumerate(servers):
        if s.get("id") == server_id:
            plans = s.get("plans") or []
            for p_idx, p in enumerate(plans):
                if p.get("id") == plan_id:
                    new_p = {**p, **updates, "id": plan_id}
                    plans[p_idx] = new_p
                    s["plans"] = plans
                    servers[s_idx] = s
                    _save_servers(servers)
                    return new_p
    return None


def _legacy_delete_plan(server_id: int, plan_id: int) -> bool:
    """
    حذف یک پلن از سرور.
    """
    servers = get_servers()
    changed = False
    for s_idx, s in enumerate(servers):
        if s.get("id") == server_id:
            plans = s.get("plans") or []
            new_plans = [p for p in plans if p.get("id") != plan_id]
            if len(new_plans) != len(plans):
                s["plans"] = new_plans
                servers[s_idx] = s
                changed = True
            break

    if changed:
        _save_servers(servers)
        return True
    return False

# =========================
#       دامنه‌های سرور
# =========================

def get_server_domains(server_id: int) -> List[Dict[str, Any]]:
    """برگرداندن لیست دامنه‌های ثبت‌شده برای یک سرور، نرمال‌شده به:
       {"id": int, "title": str, "domain": str}
    """
    servers = get_servers()
    for s in servers:
        if s.get("id") == server_id:
            raw_domains = s.get("domains") or []
            result: List[Dict[str, Any]] = []
            next_id = 1
            for d in raw_domains:
                if isinstance(d, dict):
                    domain_val = d.get("domain") or d.get("host") or d.get("url")
                    if not domain_val:
                        continue
                    title_val = d.get("title") or d.get("name") or domain_val
                    try:
                        did = int(d.get("id", next_id))
                    except Exception:
                        did = next_id
                    result.append(
                        {"id": did, "title": str(title_val), "domain": str(domain_val)}
                    )
                    next_id = did + 1
                else:
                    domain_val = str(d)
                    result.append(
                        {"id": next_id, "title": domain_val, "domain": domain_val}
                    )
                    next_id += 1
            return result
    return []


def add_server_domain(server_id: int, title: str, domain: str) -> Optional[Dict[str, Any]]:
    """افزودن یک دامنه جدید با عنوان"""
    servers = get_servers()
    for idx, s in enumerate(servers):
        if s.get("id") == server_id:
            raw_domains = s.get("domains") or []

            # اول دامنه‌های قبلی را به شکل دیکشنری نرمال می‌کنیم
            normalized: List[Dict[str, Any]] = []
            next_id = 1
            for d in raw_domains:
                if isinstance(d, dict):
                    domain_val = d.get("domain") or d.get("host") or d.get("url")
                    if not domain_val:
                        continue
                    title_val = d.get("title") or d.get("name") or domain_val
                    try:
                        did = int(d.get("id", next_id))
                    except Exception:
                        did = next_id
                    normalized.append(
                        {
                            "id": did,
                            "title": str(title_val),
                            "domain": str(domain_val),
                        }
                    )
                    next_id = did + 1
                else:
                    domain_val = str(d)
                    normalized.append(
                        {"id": next_id, "title": domain_val, "domain": domain_val}
                    )
                    next_id += 1

            new_item = {
                "id": next_id,
                "title": str(title),
                "domain": str(domain),
            }
            normalized.append(new_item)

            s["domains"] = normalized
            servers[idx] = s
            _save_servers(servers)
            return new_item

    return None


def delete_server_domain(server_id: int, domain_id: int) -> bool:
    """حذف یک دامنه بر اساس id آن"""
    servers = get_servers()
    changed = False

    for s_idx, s in enumerate(servers):
        if s.get("id") == server_id:
            raw_domains = s.get("domains") or []
            new_domains: List[Any] = []

            for d in raw_domains:
                did = None
                if isinstance(d, dict):
                    try:
                        did = int(d.get("id", 0))
                    except Exception:
                        did = None

                if did is not None and did == int(domain_id):
                    changed = True
                    continue

                new_domains.append(d)

            s["domains"] = new_domains
            servers[s_idx] = s
            break

    if changed:
        _save_servers(servers)
    return changed


# ===============================
#   ذخیره‌سازی پلن‌ها و تنظیمات پلن‌ها
#   فایل ذخیره: Shared/plans.json
# ===============================
import json
from pathlib import Path

_PLANS_FILE = Path(__file__).with_name("plans.json")


def _load_plans_data() -> dict:
    if not _PLANS_FILE.exists():
        return {"servers": {}}
    try:
        with _PLANS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"servers": {}}
    if "servers" not in data or not isinstance(data["servers"], dict):
        data["servers"] = {}
    return data


def _save_plans_data(data: dict) -> None:
    _PLANS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _PLANS_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_server_plans_block(data: dict, server_id: int) -> dict:
    servers = data.setdefault("servers", {})
    server = servers.setdefault(str(server_id), {})
    server.setdefault("mode", "dynamic")
    server.setdefault("dynamic_settings", {})
    server.setdefault("categories", [])
    server.setdefault("plans", [])
    server.setdefault("next_cat_id", 1)
    server.setdefault("next_plan_id", 1)
    return server


# ---------- حالت نمایش پلن‌ها ----------
def get_plan_mode(server_id: int, default: str = "dynamic") -> str:
    data = _load_plans_data()
    server = data.get("servers", {}).get(str(server_id))
    if not server:
        return default
    return server.get("mode", default)


def set_plan_mode(server_id: int, mode: str) -> None:
    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    server["mode"] = mode
    _save_plans_data(data)


# ---------- تنظیمات پلن پویا ----------
def get_plan_dynamic_settings(server_id: int) -> dict:
    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    return server.get("dynamic_settings", {}) or {}


def set_plan_dynamic_settings(server_id: int, settings: dict) -> None:
    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    server["dynamic_settings"] = settings or {}
    _save_plans_data(data)


# ---------- دسته‌های پلن (Categories) ----------
def get_plan_categories(server_id: int) -> list[dict]:
    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    cats = list(server.get("categories", []))
    return sorted(cats, key=lambda c: int(c.get("priority", 0)))


def get_plan_category(server_id: int, category_id: int) -> dict | None:
    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    for c in server.get("categories", []):
        if int(c.get("id", 0)) == int(category_id):
            return c
    return None


def add_plan_category(server_id: int, title: str, priority: int) -> dict:
    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    cid = int(server.get("next_cat_id", 1))
    server["next_cat_id"] = cid + 1
    cat = {"id": cid, "title": title, "priority": int(priority)}
    server["categories"].append(cat)
    _save_plans_data(data)
    return cat


def update_plan_category(
    server_id: int, category_id: int, title: str | None = None, priority: int | None = None
) -> None:
    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    for c in server.get("categories", []):
        if int(c.get("id", 0)) == int(category_id):
            if title is not None:
                c["title"] = title
            if priority is not None:
                c["priority"] = int(priority)
            break
    _save_plans_data(data)


def delete_plan_category(server_id: int, category_id: int) -> None:
    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    cid = int(category_id)
    server["categories"] = [c for c in server["categories"] if int(c.get("id", 0)) != cid]
    server["plans"] = [p for p in server["plans"] if int(p.get("category_id", 0)) != cid]
    _save_plans_data(data)


# ---------- پلن‌ها ----------
def get_plans(server_id: int, category_id: int | None = None) -> list[dict]:
    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    plans = list(server.get("plans", []))
    if category_id is not None:
        cid = int(category_id)
        plans = [p for p in plans if int(p.get("category_id", 0)) == cid]
    return plans


def get_plan(server_id: int, plan_id: int) -> dict | None:
    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    pid = int(plan_id)
    for p in server.get("plans", []):
        if int(p.get("id", 0)) == pid:
            return p
    return None


def add_plan(server_id: int, *args: Any, **kwargs: Any) -> dict:
    """
    Canonical plan writer with backward-compatible signatures:
    1) add_plan(server_id, category_id, price, days, gb, title)
    2) add_plan(server_id, plan_dict)
    """
    if len(args) == 1 and isinstance(args[0], dict):
        plan_in = dict(args[0])
        category_id = plan_in.get("category_id")
        price = int(plan_in.get("price") or 0)
        days = int(plan_in.get("days") or 0)
        gb = float(plan_in.get("gb") or 0)
        title = str(plan_in.get("title") or "")
    else:
        if len(args) >= 5:
            category_id, price, days, gb, title = args[:5]
        else:
            category_id = kwargs.get("category_id")
            price = kwargs.get("price")
            days = kwargs.get("days")
            gb = kwargs.get("gb")
            title = kwargs.get("title")

        if price is None or days is None or gb is None or title is None:
            raise ValueError("add_plan requires category_id, price, days, gb, title")

    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    pid = int(server.get("next_plan_id", 1))
    server["next_plan_id"] = pid + 1
    plan = {
        "id": pid,
        "server_id": int(server_id),
        "category_id": int(category_id) if category_id is not None else None,
        "title": str(title),
        "price": int(price),
        "days": int(days),
        "gb": float(gb),
    }
    server["plans"].append(plan)
    _save_plans_data(data)
    return plan


def delete_plan(server_id: int, plan_id: int) -> bool:
    data = _load_plans_data()
    server = _get_server_plans_block(data, server_id)
    pid = int(plan_id)
    before = len(server["plans"])
    server["plans"] = [p for p in server["plans"] if int(p.get("id", 0)) != pid]
    _save_plans_data(data)
    return len(server["plans"]) != before

def get_payment_stats(status: str = None, method: str = None) -> Dict[str, Any]:
    """
    محاسبه آمار تراکنش‌ها برای هدر لیست (تعداد و مبلغ کل، ۳۰ روزه، ماه جاری)
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()

    # شرط‌های پایه
    base_where = "WHERE 1=1"
    params = []
    if status:
        base_where += " AND status = ?"
        params.append(status)
    if method:
        base_where += " AND method = ?"
        params.append(method)

    # 1. آمار کل
    cur.execute(f"SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total FROM userbot_payments {base_where}", params)
    total_row = cur.fetchone()

    # 2. آمار 30 روز گذشته
    # (تاریخ در دیتابیس به صورت String ذخیره شده، با تابع date مقایسه می‌کنیم)
    # فرمت تاریخ باید YYYY-MM-DD ... باشد
    where_30 = base_where + " AND date(created_at) >= date('now', '-30 days')"
    cur.execute(f"SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total FROM userbot_payments {where_30}", params)
    last30_row = cur.fetchone()

    # 3. آمار ماه جاری (میلادی)
    where_month = base_where + " AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
    cur.execute(f"SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total FROM userbot_payments {where_month}", params)
    month_row = cur.fetchone()

    conn.close()

    return {
        "total_count": total_row['cnt'],
        "total_amount": total_row['total'],
        "last30_count": last30_row['cnt'],
        "last30_amount": last30_row['total'],
        "month_count": month_row['cnt'],
        "month_amount": month_row['total'],
    }

def get_payments_list_paginated(status: str = None, method: str = None, page: int = 1, page_size: int = 21) -> List[Dict[str, Any]]:
    """
    گرفتن لیست تراکنش‌ها فقط برای نمایش دکمه‌ها (شناسه و ...)
    """
    init_db()
    if page < 1: page = 1
    offset = (page - 1) * page_size
    
    conn = _get_conn()
    cur = conn.cursor()

    query = "SELECT id, amount, user_id FROM userbot_payments WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)
    
    if method:
        query += " AND method = ?"
        params.append(method)

    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([page_size, offset])
    
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    
    return [dict(r) for r in rows]

# ... (ادامه فایل Shared/database.py) ...

import random

# ===============================
#   تنظیمات و کارت‌های بانکی
# ===============================

def get_settings() -> Dict[str, Any]:
    """دریافت کل تنظیمات (کارت‌ها، روش‌های پرداخت و...)"""
    data = _load_all_plans()  # از همان فایل plans/servers استفاده می‌کنیم یا می‌توان جدا کرد
    # اگر تنظیمات وجود نداشت، پیش‌فرض بساز
    if "bot_settings" not in data:
        data["bot_settings"] = {
            "payment_methods": {
                "card": True,      # کارت به کارت فعال است
                "gateway": False   # درگاه غیرفعال است
            },
            "cards": [
                {
                    "bank": "Mellat",
                    "owner": "نام پیش‌فرض",
                    "number": "6104337000000000"
                }
            ]
        }
        _save_all_plans(data)
    
    return data["bot_settings"]

def get_active_payment_methods() -> Dict[str, bool]:
    """کدام روش‌ها فعال هستند؟"""
    return get_settings().get("payment_methods", {"card": True})

def get_random_card() -> Optional[Dict[str, str]]:
    """انتخاب یک کارت به صورت تصادفی"""
    cards = get_settings().get("cards", [])
    if not cards:
        return None
    return random.choice(cards)

def add_card(owner: str, number: str, bank_name: str = "") -> None:
    """افزودن کارت جدید"""
    data = _load_all_plans()
    settings = data.get("bot_settings", {})
    cards = settings.get("cards", [])
    
    cards.append({"owner": owner, "number": number, "bank": bank_name})
    
    settings["cards"] = cards
    data["bot_settings"] = settings
    _save_all_plans(data)

def get_payment_settings() -> Dict[str, Any]:
    """نسخه هماهنگ با database.py"""
    db = _load_db() # فرض بر اینکه این تابع در database.py هست
    if "settings" not in db:
        db["settings"] = {
            "cards": [
                {"number": "6037991111111111", "owner": "ادمین اصلی", "bank": "Melli"}
            ],
            "card_active": True,
            "gateway_active": False
        }
        _save_db(db)
    return db["settings"]

def get_random_admin_card() -> Optional[Dict[str, str]]:
    settings = get_payment_settings()
    cards = settings.get("cards", [])
    if not cards: return None
    return random.choice(cards)

# Shared/database.py
# (این کدها را جایگزین توابع آخر فایل کنید یا اگر نیستند به آخر فایل اضافه کنید)

def get_settings() -> Dict[str, Any]:
    """تنظیمات کلی (مثل کارت‌ها، متن خوش‌آمد و...) را برمی‌گرداند."""
    # اصلاح مهم: اینجا باید از _load_db استفاده شود چون در فایل database.py هستیم
    data = _load_db() 
    
    # اگر کلید settings وجود نداشت، بساز
    if "settings" not in data:
        data["settings"] = {}
        # ذخیره اولیه
        _save_db(data)
        
    return data["settings"]

def get_welcome_message() -> str:
    """متن خوش‌آمدگویی را می‌خواند."""
    settings = get_settings()
    # متن پیش‌فرض
    default_msg = (
        "سلام {full_name} عزیز 👋\n\n"
        "به ربات خوش آمدید.\n"
        "لطفا از منوی زیر استفاده کنید 👇"
    )
    return settings.get("welcome_message", default_msg)

def set_welcome_message(text: str) -> None:
    """متن خوش‌آمدگویی را ذخیره می‌کند."""
    data = _load_db()
    if "settings" not in data:
        data["settings"] = {}
    
    data["settings"]["welcome_message"] = text
    _save_db(data)

def get_random_card() -> Optional[Dict[str, str]]:
    """انتخاب یک کارت به صورت تصادفی"""
    settings = get_settings()
    cards = settings.get("cards", [])
    if not cards:
        return None
    return __import__("random").choice(cards)


def get_next_card() -> Optional[Dict[str, str]]:
    """انتخاب کارت به صورت چرخشی (round-robin)"""
    data = _load_db()
    settings = data.get("settings", {})
    cards = settings.get("cards", [])
    if not isinstance(cards, list) or not cards:
        return None

    try:
        idx = int(settings.get("card_rr_index", 0))
    except Exception:
        idx = 0
    if idx < 0:
        idx = 0

    pick = cards[idx % len(cards)]
    settings["card_rr_index"] = (idx + 1) % len(cards)
    data["settings"] = settings
    _save_db(data)

    if not isinstance(pick, dict):
        return None
    return {
        "number": str(pick.get("number") or "").strip(),
        "owner": str(pick.get("owner") or "").strip(),
        "bank": str(pick.get("bank") or "").strip(),
    }


def get_cards() -> List[Dict[str, str]]:
    settings = get_settings()
    cards = settings.get("cards", [])
    if not isinstance(cards, list):
        return []
    out: List[Dict[str, str]] = []
    for item in cards:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "number": str(item.get("number") or "").strip(),
                "owner": str(item.get("owner") or "").strip(),
                "bank": str(item.get("bank") or "").strip(),
            }
        )
    return out


def get_card(number: str) -> Optional[Dict[str, str]]:
    clean_number = str(number or "").replace(" ", "").replace("-", "").strip()
    if not clean_number:
        return None
    for item in get_cards():
        n = str(item.get("number") or "").replace(" ", "").replace("-", "").strip()
        if n == clean_number:
            return {
                "number": str(item.get("number") or "").strip(),
                "owner": str(item.get("owner") or "").strip(),
                "bank": str(item.get("bank") or "").strip(),
            }
    return None


def add_or_update_card(owner: str, number: str, bank_name: str = "") -> None:
    clean_number = str(number or "").replace(" ", "").replace("-", "").strip()
    if not clean_number:
        raise ValueError("شماره کارت خالی است")
    data = _load_db()
    settings = data.get("settings", {})
    cards = settings.get("cards", [])
    if not isinstance(cards, list):
        cards = []
    owner_str = str(owner or "").strip()
    bank_str = str(bank_name or "").strip()
    replaced = False
    for item in cards:
        if str(item.get("number") or "").replace(" ", "").replace("-", "").strip() == clean_number:
            item["owner"] = owner_str
            item["bank"] = bank_str
            replaced = True
            break
    if not replaced:
        cards.append({"number": clean_number, "owner": owner_str, "bank": bank_str})
    settings["cards"] = cards
    data["settings"] = settings
    _save_db(data)


def delete_card(number: str) -> bool:
    clean_number = str(number or "").replace(" ", "").replace("-", "").strip()
    if not clean_number:
        return False
    data = _load_db()
    settings = data.get("settings", {})
    cards = settings.get("cards", [])
    if not isinstance(cards, list):
        return False
    before = len(cards)
    cards = [
        item
        for item in cards
        if str((item or {}).get("number") or "").replace(" ", "").replace("-", "").strip() != clean_number
    ]
    if len(cards) == before:
        return False
    settings["cards"] = cards
    data["settings"] = settings
    _save_db(data)
    return True


def update_card_owner(number: str, owner: str) -> bool:
    clean_number = str(number or "").replace(" ", "").replace("-", "").strip()
    if not clean_number:
        return False
    data = _load_db()
    settings = data.get("settings", {})
    cards = settings.get("cards", [])
    if not isinstance(cards, list):
        return False
    owner_str = str(owner or "").strip()
    for item in cards:
        n = str((item or {}).get("number") or "").replace(" ", "").replace("-", "").strip()
        if n == clean_number:
            item["owner"] = owner_str
            settings["cards"] = cards
            data["settings"] = settings
            _save_db(data)
            return True
    return False


def update_card_number(old_number: str, new_number: str) -> bool:
    old_clean = str(old_number or "").replace(" ", "").replace("-", "").strip()
    new_clean = str(new_number or "").replace(" ", "").replace("-", "").strip()
    if not old_clean or not new_clean:
        return False
    data = _load_db()
    settings = data.get("settings", {})
    cards = settings.get("cards", [])
    if not isinstance(cards, list):
        return False

    # جلوگیری از شماره تکراری
    for item in cards:
        n = str((item or {}).get("number") or "").replace(" ", "").replace("-", "").strip()
        if n == new_clean and n != old_clean:
            return False

    for item in cards:
        n = str((item or {}).get("number") or "").replace(" ", "").replace("-", "").strip()
        if n == old_clean:
            item["number"] = new_clean
            settings["cards"] = cards
            data["settings"] = settings
            _save_db(data)
            return True
    return False
