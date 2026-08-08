from __future__ import annotations

import sqlite3
import json
import random
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

DB_FILE_NAME = "customer_bot.db"
DB_PATH = Path(__file__).resolve().parent.parent / DB_FILE_NAME
_AGENCY_DB_PATH = Path(__file__).resolve().parent.parent / "Shared" / "agency.db"

_db_initialized = False
_init_db_path = ""

DEFAULT_BUY_RENEW_SETTINGS = {
    "enable_buy": True,
    "enable_renew": True,
    "show_renew_in_main_menu": True,
    "renew_mode": "plans",
    "plan_columns": 1,
    "server_columns": 1,
    "renew_policy": "advanced",
    "renew_volume_mode": "reset",
    "renew_time_mode": "reset",
    "renew_max_days": 3,
    "renew_max_remaining_gb": 3,
    "renew_unlimited_volume": False,
    "renew_unlimited_time": False,
    "renew_unlimited_volume_from_gb": 1000,
    "renew_unlimited_time_from_days": 365,
    "event_channel_enabled": False,
    "event_channel_id": "",
}

DEFAULT_SUBS_SETTINGS = {
    "show_user_page_link": True,
    "show_username": True,
    "shuffle_configs": True,
    "shuffle_server_layout": True,
    "shuffle_config_layout": True,
    "show_direct_config": True,
    "show_auto_sub_link": False,
    "show_sub_link": True,
    "show_sub_link_b64": False,
    "show_multi_server": False,
    "show_multi_server_b64": False,
}

DEFAULT_TX_PLANS_SETTINGS = {
    "random_tx_spec": False,
    "min_transaction_toman": 10000,
    "plan_categories_enabled": True,
    "plan_sort_by_priority": True,
    "plan_sort_mode": "price",
    "plan_sort_desc": False,
}

DEFAULT_PAYMENT_SETTINGS = {
    "enable_card_to_card": True,
    "require_last4_for_card_receipt": False,
    "enable_zarinpal": False,
    "enable_perfect_money": False,
    "enable_crypto": False,
    "event_channel_enabled": False,
    "event_channel_id": "",
}

DEFAULT_TEXT_SETTINGS = {
    "welcome_message": "سلام {full_name} عزیز 👋\nبه ربات ما خوش آمدید.",
    "faq_text": "❓ سوالات متداول\n\n1) لینک اشتراک را کجا بزنم؟\nاز بخش «📊وضعیت اشتراک» وارد سرویس شوید و روی «لینک اشتراک» بزنید.\n\n2) اگر کانفیگ وصل نشد چه کنم؟\nاول اینترنت و تاریخ/ساعت گوشی را چک کنید، سپس «بروزرسانی اطلاعات» بزنید.\n\n3) چطور تمدید کنم؟\nاز «♾تمدید اشتراک» سرویس را انتخاب کنید و پلن تمدید را بخرید.\n\n4) پشتیبانی از کجاست؟\nاز دکمه «📩پشتیبانی» پیام خود را ارسال کنید.",
    "guide_text": "انتخاب سیستم عامل ⬇️",
    "guide_android_text": "📱 راهنمای اندروید\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) v2rayNG:\nhttps://github.com/2dust/v2rayNG/releases\n\n3) NekoBox for Android:\nhttps://github.com/MatsuriDayo/NekoBoxForAndroid/releases",
    "guide_ios_text": "📱 راهنمای iOS\n\n1) Streisand:\nhttps://apps.apple.com/app/streisand/id6450534064\n\n2) Hiddify (iOS):\nhttps://apps.apple.com/app/hiddify-proxy-vpn/id6596777532",
    "guide_windows_text": "🖥️ راهنمای ویندوز\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n3) v2rayN:\nhttps://github.com/2dust/v2rayN/releases",
    "guide_mac_text": "💻 راهنمای مک\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases",
    "guide_linux_text": "🖥️ راهنمای لینوکس\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases",
    "card_to_card_text": "0",
    "servers_list_text": "📡 **لیست سرورها**\nلطفاً لوکیشن مورد نظر خود را انتخاب کنید:",
    "plans_list_text": "🛒 **لطفاً پلن مورد نظر خود را انتخاب کنید:**",
    "ticket_panel_text": "📩 برای ارتباط با پشتیبانی، پیام خود را ارسال کنید.",
    "zarinpal_pro_text": "0",
}

DEFAULT_MARKETING_SETTINGS = {
    "enable_discount_code": False,
    "enable_increase_code": False,
    "show_user_status": True,
    "instant_gift_coupon": False,
    "auto_gift_text": "🎁 هدیه شما فعال شد. از همراهی‌تان متشکریم.",
    "min_auto_gift_charge": 100000,
}

DEFAULT_FORCE_JOIN_SETTINGS = {
    "enabled": False,
    "channel_id": "",
    "channel_username": "",
    "channel_link": "",
    "guide_text": "🔒 برای استفاده از ربات، ابتدا در کانال پشتیبانی عضو شوید.\nپس از عضویت روی «✅ بررسی عضویت» بزنید.",
}

DEFAULT_TRIAL_SPEC_SETTINGS = {
    "enabled": True,
    "announce_enabled": True,
    "usage_gb": 1,
    "days": 1,
}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on", "y", "t"}:
        return True
    if s in {"0", "false", "no", "off", "n", "f", ""}:
        return False
    return default


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(cur: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    try:
        cur.execute(f"SELECT {column} FROM {table} LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    global _db_initialized, _init_db_path
    current_path = str(DB_PATH)
    # اگر مسیر دیتابیس تغییر کرده (مثلاً در تست‌ها)، دوباره جداول را می‌سازیم.
    if _db_initialized and current_path == _init_db_path:
        return
    _db_initialized = True
    _init_db_path = current_path
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            telegram_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            created_at TEXT,
            wallet_balance INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            got_free_trial INTEGER DEFAULT 0,
            UNIQUE(agent_id, telegram_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            order_id INTEGER NOT NULL,
            user_id INTEGER,
            telegram_id INTEGER,
            username TEXT,
            full_name TEXT,
            created_at TEXT,
            volume_gb REAL,
            days INTEGER,
            price INTEGER,
            plan_title TEXT,
            server_location TEXT,
            status TEXT,
            UNIQUE(agent_id, order_id)
        )
    """)
    _ensure_column(cur, "customer_orders", "server_id", "INTEGER DEFAULT 0")
    _ensure_column(cur, "customer_orders", "plan_id", "INTEGER DEFAULT 0")
    _ensure_column(cur, "customer_orders", "wholesale_price", "INTEGER DEFAULT 0")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            tx_code TEXT,
            user_id INTEGER,
            amount INTEGER,
            method TEXT,
            status TEXT,
            receipt_image TEXT,
            idempotency_key TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cust_pay_idem
        ON customer_payments(agent_id, idempotency_key)
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            ticket_code INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            telegram_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            service_name TEXT DEFAULT '',
            title TEXT DEFAULT '',
            question TEXT DEFAULT '',
            receipt_photo_id TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            admin_name TEXT DEFAULT '',
            admin_telegram_id INTEGER DEFAULT 0,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            UNIQUE(agent_id, ticket_code)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            ticket_code INTEGER NOT NULL,
            sender_type TEXT NOT NULL,
            sender_name TEXT DEFAULT '',
            message_text TEXT DEFAULT '',
            photo_file_id TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ctm_agent_code ON customer_ticket_messages(agent_id, ticket_code)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_settings (
            agent_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            PRIMARY KEY (agent_id, key)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_zarin_vouchers (
            agent_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            amount_toman INTEGER NOT NULL DEFAULT 0,
            zarinpal_link TEXT DEFAULT '',
            max_uses INTEGER NOT NULL DEFAULT 1,
            used_count INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            PRIMARY KEY (agent_id, code)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_zarin_voucher_redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            redeemed_at TEXT DEFAULT '',
            UNIQUE(agent_id, code, user_id)
        )
    """)

    conn.commit()
    conn.close()


def _get_setting(agent_id: int, key: str, default: Any = None) -> Any:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT value FROM customer_settings WHERE agent_id = ? AND key = ?",
        (agent_id, key),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return default
    val = row["value"]
    if val is None:
        return default
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def _set_setting(agent_id: int, key: str, value: Any) -> None:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    payload = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    cur.execute(
        "INSERT INTO customer_settings (agent_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(agent_id, key) DO UPDATE SET value = excluded.value",
        (agent_id, key, payload),
    )
    conn.commit()
    conn.close()


def _load_settings_dict(agent_id: int, key: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    stored = _get_setting(agent_id, key, {})
    if not isinstance(stored, dict):
        stored = {}
    result = dict(defaults)
    for k in defaults:
        if k in stored:
            result[k] = stored[k]
    return result


def _save_settings_dict(agent_id: int, key: str, settings: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(defaults)
    if isinstance(settings, dict):
        for k in defaults:
            if k in settings:
                current[k] = settings[k]
    _set_setting(agent_id, key, current)
    return current


# ---- Users ----

def upsert_user(agent_id: int, telegram_id: int, username: str = "", full_name: str = "") -> int:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "SELECT id FROM customer_users WHERE agent_id = ? AND telegram_id = ?",
        (agent_id, telegram_id),
    )
    row = cur.fetchone()
    if row:
        uid = row["id"]
        cur.execute(
            "UPDATE customer_users SET username = ?, full_name = ?, created_at = ? WHERE id = ?",
            (username, full_name, now, uid),
        )
    else:
        cur.execute(
            "INSERT INTO customer_users (agent_id, telegram_id, username, full_name, created_at) VALUES (?, ?, ?, ?, ?)",
            (agent_id, telegram_id, username, full_name, now),
        )
        uid = cur.lastrowid
    conn.commit()
    conn.close()
    return int(uid)


def get_user(agent_id: int, telegram_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_users WHERE agent_id = ? AND telegram_id = ?",
        (agent_id, telegram_id),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_broadcast_stats(agent_id: int) -> Dict[str, Any]:
    target_sets = _get_broadcast_target_sets(agent_id)
    return {
        "total_users": len(target_sets["all"]),
        "expired_users": len(target_sets["expired_all"]),
        "no_order_users": len(target_sets["no_order"]),
        "expired_1w_users": len(target_sets["expired_1w"]),
        "expired_2w_users": len(target_sets["expired_2w"]),
        "expired_4w_users": len(target_sets["expired_4w"]),
        "expired_8w_users": len(target_sets["expired_8w"]),
    }


def get_broadcast_target_telegram_ids(agent_id: int, segment: str = "all") -> List[int]:
    seg = str(segment or "all").strip().lower()
    target_sets = _get_broadcast_target_sets(agent_id)
    return sorted(target_sets.get(seg, set()), reverse=True)


def _agency_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_AGENCY_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _get_broadcast_target_sets(agent_id: int) -> Dict[str, set[int]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT telegram_id FROM customer_users WHERE agent_id = ? AND COALESCE(is_banned, 0) = 0 AND COALESCE(telegram_id, 0) > 0",
        (agent_id,),
    )
    user_rows = cur.fetchall()
    conn.close()

    all_users = {int(r["telegram_id"]) for r in user_rows if int(r["telegram_id"] or 0) > 0}
    target_sets: Dict[str, set[int]] = {
        "all": set(all_users),
        "expired_all": set(),
        "no_order": set(),
        "expired_1w": set(),
        "expired_2w": set(),
        "expired_4w": set(),
        "expired_8w": set(),
    }
    if not all_users:
        return target_sets

    target_sets["no_order"] = set(all_users)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        agency_conn = _agency_conn()
    except sqlite3.Error:
        return target_sets

    try:
        cur = agency_conn.cursor()
        cur.execute(
            """
            SELECT c.telegram_id, s.days_left, s.end_date, s.is_active
            FROM agent_customers c
            JOIN agent_services s ON s.customer_id = c.id
            WHERE c.agent_id = ? AND COALESCE(c.telegram_id, 0) > 0
            """,
            (agent_id,),
        )
        service_rows = cur.fetchall()
    finally:
        agency_conn.close()

    service_map: Dict[int, List[sqlite3.Row]] = {}
    for row in service_rows:
        tg_id = _safe_int(row["telegram_id"])
        if tg_id and tg_id in all_users:
            service_map.setdefault(tg_id, []).append(row)

    for tg_id in service_map:
        target_sets["no_order"].discard(tg_id)

    for tg_id, services in service_map.items():
        has_active = False
        most_expired_days: Optional[int] = None
        for svc in services:
            is_active = int(svc["is_active"] or 0) == 1
            days_left = _safe_int(svc["days_left"])
            end_dt = _parse_dt(svc["end_date"])
            expired_days: Optional[int] = None

            if days_left is not None:
                if days_left >= 0 and is_active:
                    has_active = True
                elif days_left < 0:
                    expired_days = abs(days_left)

            if expired_days is None and end_dt is not None:
                delta_days = (now - end_dt).days
                if delta_days < 0 and is_active:
                    has_active = True
                elif delta_days >= 0:
                    expired_days = delta_days

            if expired_days is not None:
                if most_expired_days is None or expired_days > most_expired_days:
                    most_expired_days = expired_days

        if has_active:
            continue
        if most_expired_days is None:
            continue
        target_sets["expired_all"].add(tg_id)
        if most_expired_days >= 7:
            target_sets["expired_1w"].add(tg_id)
        if most_expired_days >= 14:
            target_sets["expired_2w"].add(tg_id)
        if most_expired_days >= 28:
            target_sets["expired_4w"].add(tg_id)
        if most_expired_days >= 56:
            target_sets["expired_8w"].add(tg_id)

    return target_sets


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM customer_users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def set_user_banned(agent_id: int, telegram_id: int, banned: bool) -> bool:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE customer_users SET is_banned = ? WHERE agent_id = ? AND telegram_id = ?",
        (1 if banned else 0, agent_id, telegram_id),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def set_got_free_trial(agent_id: int, telegram_id: int) -> bool:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "SELECT id FROM customer_users WHERE agent_id = ? AND telegram_id = ?",
        (agent_id, telegram_id),
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE customer_users SET got_free_trial = 1, updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )
    else:
        cur.execute(
            "INSERT INTO customer_users (agent_id, telegram_id, got_free_trial, created_at) VALUES (?, ?, 1, ?)",
            (agent_id, telegram_id, now),
        )
    conn.commit()
    conn.close()
    return True


def clear_got_free_trial(agent_id: int, telegram_id: int) -> bool:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE customer_users SET got_free_trial = 0 WHERE agent_id = ? AND telegram_id = ?",
        (agent_id, telegram_id),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def reset_all_free_trials(agent_id: int) -> int:
    """بازنشانی فلگ تست رایگان همه مشتریان یک نماینده (برای امکان تست مجدد)."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE customer_users SET got_free_trial = 0 WHERE agent_id = ?",
        (agent_id,),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return int(affected or 0)


def count_free_trial_users(agent_id: int) -> int:
    """تعداد مشتریانی که تست رایگان گرفته‌اند (برای نمایش در ربات ادمین)."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS c FROM customer_users WHERE agent_id = ? AND got_free_trial = 1",
        (agent_id,),
    )
    row = cur.fetchone()
    count = int(row["c"] or 0) if row else 0
    conn.close()
    return count


# ---- Wallet ----

def get_wallet_balance(agent_id: int, telegram_id: int) -> int:
    user = get_user(agent_id, telegram_id)
    if user:
        return int(user.get("wallet_balance", 0))
    return 0


def charge_wallet(agent_id: int, telegram_id: int, amount: int) -> bool:
    if amount <= 0:
        return False
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE customer_users SET wallet_balance = wallet_balance + ? WHERE agent_id = ? AND telegram_id = ?",
        (amount, agent_id, telegram_id),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def deduct_wallet(agent_id: int, telegram_id: int, amount: int) -> bool:
    if amount <= 0:
        return False
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE customer_users SET wallet_balance = wallet_balance - ? WHERE agent_id = ? AND telegram_id = ? AND wallet_balance >= ?",
        (amount, agent_id, telegram_id, amount),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# ---- Orders ----

def generate_order_id(agent_id: int) -> int:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    for _ in range(50):
        oid = random.randint(100000, 999999)
        try:
            cur.execute(
                "INSERT INTO customer_orders (agent_id, order_id, created_at, status) VALUES (?, ?, '', 'pending')",
                (agent_id, oid),
            )
            conn.commit()
            return oid
        except sqlite3.IntegrityError:
            conn.rollback()
            continue
    conn.close()
    return 0


def create_order(agent_id: int, telegram_id: int, volume_gb: float, days: int, price: int,
                 plan_title: str, server_location: str, username: str = "", full_name: str = "",
                 server_id: int = 0, plan_id: int = 0, wholesale_price: int = 0) -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    oid = generate_order_id(agent_id)
    if not oid:
        conn.close()
        return {}
    user = get_user(agent_id, telegram_id)
    user_id = user["id"] if user else 0
    cur.execute(
        "UPDATE customer_orders SET user_id=?, telegram_id=?, username=?, full_name=?, "
        "created_at=?, volume_gb=?, days=?, price=?, plan_title=?, server_location=?, "
        "server_id=?, plan_id=?, wholesale_price=?, status='pending' "
        "WHERE agent_id=? AND order_id=?",
        (user_id, telegram_id, username, full_name, now,
         volume_gb, days, price, plan_title, server_location,
         int(server_id or 0), int(plan_id or 0), int(wholesale_price or 0),
         agent_id, oid),
    )
    conn.commit()
    cur.execute("SELECT * FROM customer_orders WHERE agent_id=? AND order_id=?", (agent_id, oid))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def get_order(agent_id: int, order_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_orders WHERE agent_id = ? AND order_id = ?",
        (agent_id, order_id),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_order_status(agent_id: int, order_id: int, status: str) -> bool:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE customer_orders SET status = ? WHERE agent_id = ? AND order_id = ?",
        (status, agent_id, order_id),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_user_orders(agent_id: int, telegram_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_orders WHERE agent_id = ? AND telegram_id = ? ORDER BY id DESC LIMIT ?",
        (agent_id, telegram_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---- Payments ----

def generate_tx_code(agent_id: int) -> str:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    for _ in range(50):
        code = f"{random.randint(0, 9999999):07d}"
        cur.execute(
            "SELECT 1 FROM customer_payments WHERE agent_id = ? AND tx_code = ? LIMIT 1",
            (agent_id, code),
        )
        if not cur.fetchone():
            conn.close()
            return code
    conn.close()
    return f"{random.randint(0, 9999999):07d}"


def create_payment(agent_id: int, user_id: int, amount: int, method: str, idempotency_key: str = "") -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    tx_code = generate_tx_code(agent_id)
    cur.execute(
        "INSERT INTO customer_payments (agent_id, tx_code, user_id, amount, method, status, idempotency_key, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
        (agent_id, tx_code, user_id, amount, method, idempotency_key, now),
    )
    conn.commit()
    pay_id = cur.lastrowid
    cur.execute("SELECT * FROM customer_payments WHERE id = ?", (pay_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def get_payment(agent_id: int, payment_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_payments WHERE agent_id = ? AND id = ?",
        (agent_id, payment_id),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_payment_by_tx_code(agent_id: int, tx_code: str) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_payments WHERE agent_id = ? AND tx_code = ?",
        (agent_id, tx_code),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_payment_by_idempotency_key(agent_id: int, idempotency_key: str) -> Optional[Dict[str, Any]]:
    if not idempotency_key:
        return None
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_payments WHERE agent_id = ? AND idempotency_key = ? ORDER BY id DESC LIMIT 1",
        (agent_id, idempotency_key),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_payment_status(agent_id: int, payment_id: int, status: str, receipt_image: str = "") -> bool:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    if receipt_image:
        cur.execute(
            "UPDATE customer_payments SET status = ?, receipt_image = ?, updated_at = ? WHERE agent_id = ? AND id = ?",
            (status, receipt_image, now, agent_id, payment_id),
        )
    else:
        cur.execute(
            "UPDATE customer_payments SET status = ?, updated_at = ? WHERE agent_id = ? AND id = ?",
            (status, now, agent_id, payment_id),
        )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_pending_payments(agent_id: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_payments WHERE agent_id = ? AND status = 'pending' ORDER BY id",
        (agent_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---- Tickets ----

def generate_ticket_code(agent_id: int) -> int:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    for _ in range(50):
        code = random.randint(10000, 99999)
        cur.execute(
            "SELECT 1 FROM customer_tickets WHERE agent_id = ? AND ticket_code = ? LIMIT 1",
            (agent_id, code),
        )
        if not cur.fetchone():
            conn.close()
            return code
    conn.close()
    return random.randint(10000, 99999)


def create_ticket(agent_id: int, telegram_id: int, username: str, full_name: str,
                  question: str, title: str = "", service_name: str = "") -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    code = generate_ticket_code(agent_id)
    user = get_user(agent_id, telegram_id)
    user_id = user["id"] if user else 0
    cur.execute(
        "INSERT INTO customer_tickets (agent_id, ticket_code, user_id, telegram_id, username, full_name, "
        "service_name, title, question, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (agent_id, code, user_id, telegram_id, username, full_name,
         service_name, title, question, now, now),
    )
    conn.commit()
    cur.execute("SELECT * FROM customer_tickets WHERE id = ?", (cur.lastrowid,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def get_ticket(agent_id: int, ticket_code: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_tickets WHERE agent_id = ? AND ticket_code = ?",
        (agent_id, ticket_code),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_tickets(agent_id: int, telegram_id: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_tickets WHERE agent_id = ? AND telegram_id = ? ORDER BY id DESC",
        (agent_id, telegram_id),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_tickets(agent_id: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_tickets WHERE agent_id = ? AND status = 'pending' ORDER BY id",
        (agent_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_ticket_status(agent_id: int, ticket_code: int, status: str,
                         admin_name: str = "", admin_telegram_id: int = 0) -> bool:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "UPDATE customer_tickets SET status = ?, admin_name = ?, admin_telegram_id = ?, updated_at = ? "
        "WHERE agent_id = ? AND ticket_code = ?",
        (status, admin_name, admin_telegram_id, now, agent_id, ticket_code),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def add_ticket_message(agent_id: int, ticket_code: int, sender_type: str,
                       sender_name: str, message_text: str = "", photo_file_id: str = "") -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "INSERT INTO customer_ticket_messages (agent_id, ticket_code, sender_type, sender_name, "
        "message_text, photo_file_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (agent_id, ticket_code, sender_type, sender_name, message_text, photo_file_id, now),
    )
    conn.commit()
    cur.execute("SELECT * FROM customer_ticket_messages WHERE id = ?", (cur.lastrowid,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def get_ticket_messages(agent_id: int, ticket_code: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_ticket_messages WHERE agent_id = ? AND ticket_code = ? ORDER BY id",
        (agent_id, ticket_code),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---- Settings wrappers ----

def get_buy_renew_settings(agent_id: int) -> Dict[str, Any]:
    return _load_settings_dict(agent_id, "buy_renew_settings", DEFAULT_BUY_RENEW_SETTINGS)


def set_buy_renew_settings(agent_id: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    return _save_settings_dict(agent_id, "buy_renew_settings", settings, DEFAULT_BUY_RENEW_SETTINGS)


def toggle_buy_renew_setting(agent_id: int, name: str) -> Dict[str, Any]:
    s = get_buy_renew_settings(agent_id)
    if name in {"enable_buy", "enable_renew", "show_renew_in_main_menu",
                 "renew_unlimited_volume", "renew_unlimited_time", "event_channel_enabled"}:
        s[name] = not bool(s.get(name))
    return set_buy_renew_settings(agent_id, s)


def get_subs_settings(agent_id: int) -> Dict[str, bool]:
    """تنظیمات لینک اشتراک را از userbot_db می‌خواند (منبع اصلی که ادمین تغییر می‌دهد).
    در صورت خطا به تنظیمات محلی customer_bot.db برمی‌گردد."""
    try:
        from Shared import userbot_db as _userbot_db
        shared = _userbot_db.get_subscription_settings()
        if isinstance(shared, dict) and shared:
            # تنظیمات محلی CustomerBot را هم مرج کن تا کلیدهای اضافه (مثل show_auto_sub_link) پوشش داده شود
            result = dict(DEFAULT_SUBS_SETTINGS)
            for k in DEFAULT_SUBS_SETTINGS:
                if k in shared:
                    result[k] = bool(shared[k])
            return result
    except Exception:
        pass
    return _load_settings_dict(agent_id, "subscription_settings", DEFAULT_SUBS_SETTINGS)


def set_subs_settings(agent_id: int, settings: Dict[str, Any]) -> Dict[str, bool]:
    return _save_settings_dict(agent_id, "subscription_settings", settings, DEFAULT_SUBS_SETTINGS)


def toggle_subs_setting(agent_id: int, name: str) -> Dict[str, bool]:
    s = get_subs_settings(agent_id)
    if name in DEFAULT_SUBS_SETTINGS:
        s[name] = not bool(s.get(name))
    return set_subs_settings(agent_id, s)


def get_tx_plans_settings(agent_id: int) -> Dict[str, Any]:
    return _load_settings_dict(agent_id, "tx_plans_settings", DEFAULT_TX_PLANS_SETTINGS)


def set_tx_plans_settings(agent_id: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    return _save_settings_dict(agent_id, "tx_plans_settings", settings, DEFAULT_TX_PLANS_SETTINGS)


def get_payment_settings(agent_id: int) -> Dict[str, Any]:
    return _load_settings_dict(agent_id, "payment_settings", DEFAULT_PAYMENT_SETTINGS)


def set_payment_settings(agent_id: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    return _save_settings_dict(agent_id, "payment_settings", settings, DEFAULT_PAYMENT_SETTINGS)


def toggle_payment_setting(agent_id: int, name: str) -> Dict[str, Any]:
    s = get_payment_settings(agent_id)
    if name in DEFAULT_PAYMENT_SETTINGS:
        s[name] = not bool(s.get(name))
    return set_payment_settings(agent_id, s)


def get_text_settings(agent_id: int) -> Dict[str, str]:
    return _load_settings_dict(agent_id, "text_settings", DEFAULT_TEXT_SETTINGS)


def set_text_settings(agent_id: int, settings: Dict[str, Any]) -> Dict[str, str]:
    return _save_settings_dict(agent_id, "text_settings", settings, DEFAULT_TEXT_SETTINGS)


def set_text_setting(agent_id: int, name: str, value: str) -> Dict[str, str]:
    if name not in DEFAULT_TEXT_SETTINGS:
        raise ValueError("invalid text setting name")
    s = get_text_settings(agent_id)
    raw_value = str(value or "")
    resettable_by_zero = (
        name.startswith("guide_")
        or name in {"servers_list_text", "plans_list_text", "ticket_panel_text",
                     "zarinpal_pro_text", "card_to_card_text"}
    )
    if resettable_by_zero and raw_value.strip() == "0":
        s[name] = str(DEFAULT_TEXT_SETTINGS.get(name, ""))
    else:
        s[name] = raw_value
    return set_text_settings(agent_id, s)


def get_marketing_settings(agent_id: int) -> Dict[str, Any]:
    return _load_settings_dict(agent_id, "marketing_settings", DEFAULT_MARKETING_SETTINGS)


def set_marketing_settings(agent_id: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    return _save_settings_dict(agent_id, "marketing_settings", settings, DEFAULT_MARKETING_SETTINGS)


def toggle_marketing_setting(agent_id: int, name: str) -> Dict[str, Any]:
    s = get_marketing_settings(agent_id)
    if name in {"enable_discount_code", "enable_increase_code",
                 "show_user_status", "instant_gift_coupon"}:
        s[name] = not bool(s.get(name))
    return set_marketing_settings(agent_id, s)


def get_force_join_settings(agent_id: int) -> Dict[str, Any]:
    return _load_settings_dict(agent_id, "force_join_settings", DEFAULT_FORCE_JOIN_SETTINGS)


def set_force_join_settings(agent_id: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    return _save_settings_dict(agent_id, "force_join_settings", settings, DEFAULT_FORCE_JOIN_SETTINGS)


def toggle_force_join_enabled(agent_id: int) -> Dict[str, Any]:
    s = get_force_join_settings(agent_id)
    s["enabled"] = not bool(s.get("enabled", False))
    return set_force_join_settings(agent_id, s)


def get_trial_spec_settings(agent_id: int) -> Dict[str, Any]:
    return _load_settings_dict(agent_id, "trial_spec_settings", DEFAULT_TRIAL_SPEC_SETTINGS)


def set_trial_spec_settings(agent_id: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    return _save_settings_dict(agent_id, "trial_spec_settings", settings, DEFAULT_TRIAL_SPEC_SETTINGS)


# ---- Zarinpal Vouchers ----

def add_zarin_voucher(agent_id: int, code: str, amount_toman: int, zarinpal_link: str = "",
                      max_uses: int = 1, expires_at: str = "") -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "INSERT OR REPLACE INTO customer_zarin_vouchers "
        "(agent_id, code, amount_toman, zarinpal_link, max_uses, used_count, is_active, expires_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?, ?)",
        (agent_id, code, amount_toman, zarinpal_link, max_uses, expires_at, now, now),
    )
    conn.commit()
    conn.close()
    return {"code": code, "amount_toman": amount_toman, "zarinpal_link": zarinpal_link,
            "max_uses": max_uses, "is_active": 1}


def get_zarin_voucher(agent_id: int, code: str) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_zarin_vouchers WHERE agent_id = ? AND code = ?",
        (agent_id, code),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def redeem_zarin_voucher(agent_id: int, code: str, user_id: int) -> Tuple[bool, str]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT used_count, max_uses, is_active, expires_at, amount_toman FROM customer_zarin_vouchers WHERE agent_id = ? AND code = ?",
        (agent_id, code),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return False, "کد نامعتبر است"
    if not row["is_active"]:
        conn.close()
        return False, "این کد غیرفعال شده است"
    if row["used_count"] >= row["max_uses"]:
        conn.close()
        return False, "ظرفیت استفاده از این کد به پایان رسیده"
    expires = str(row["expires_at"] or "").strip()
    if expires:
        try:
            exp_dt = datetime.strptime(expires, "%Y-%m-%d %H:%M:%S")
            if datetime.now(timezone.utc).replace(tzinfo=None) > exp_dt:
                conn.close()
                return False, "این کد منقضی شده است"
        except ValueError:
            pass

    cur.execute(
        "UPDATE customer_zarin_vouchers SET used_count = used_count + 1, updated_at = ? WHERE agent_id = ? AND code = ? AND used_count < max_uses",
        (_now(), agent_id, code),
    )
    if cur.rowcount == 0:
        conn.close()
        return False, "ظرفیت استفاده از این کد به پایان رسیده"

    cur.execute(
        "INSERT INTO customer_zarin_voucher_redemptions (agent_id, code, user_id, redeemed_at) VALUES (?, ?, ?, ?)",
        (agent_id, code, user_id, _now()),
    )
    amount = int(row["amount_toman"])
    conn.commit()
    conn.close()
    return True, str(amount)
