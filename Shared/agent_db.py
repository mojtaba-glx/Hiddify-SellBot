# Shared/agent_db.py
# دیتابیس SQLite اختصاصی سیستم نمایندگی (Agency/Reseller)

from __future__ import annotations

import sqlite3
import json
import math
import re
import random
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from Shared.secure_io import ensure_private_file

DB_FILE_NAME = "agency.db"
DB_PATH = Path(__file__).with_name(DB_FILE_NAME)

# CustomerBot stores its SQLite database at the project root.  Keep this
# path aligned so cross-bot customer lookups and backups use the same file.
_CUSTOMER_BOT_DB_PATH = Path(__file__).resolve().parent.parent / "customer_bot.db"

_db_initialized = False
_init_db_path = ""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        # چند پروسه/ترد روی یک فایل: WAL + busy_timeout از
        # «database is locked» در عملیات همزمان جلوگیری می‌کند
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=20000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    # agency.db حاوی توکن ربات نماینده و مشتریان است — همیشه 0600.
    ensure_private_file(DB_PATH)
    ensure_private_file(Path(str(DB_PATH) + "-wal"))
    ensure_private_file(Path(str(DB_PATH) + "-shm"))
    return conn


def init_db() -> None:
    global _db_initialized, _init_db_path
    current_path = str(DB_PATH)
    # اگر مسیر دیتابیس تغییر کرده (مثلاً در تست‌ها)، دوباره جداول را می‌سازیم.
    if _db_initialized and current_path == _init_db_path:
        return
    _db_initialized = True
    _init_db_path = current_path
    """ساخت تمام جداول دیتابیس نمایندگی."""
    conn = _get_conn()
    cur = conn.cursor()

    # 1. نماینده‌ها
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
    """)

    # 2. کیف پول نماینده
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_wallets (
            agent_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT '',
            FOREIGN KEY (agent_id) REFERENCES agent_users(id)
        )
    """)

    # 3. مشتریان هر نماینده
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            telegram_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            UNIQUE(agent_id, telegram_id)
        )
    """)

    # 4. سرویس‌های ساخته‌شده توسط نماینده
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            server_id INTEGER NOT NULL,
            server_title TEXT DEFAULT '',
            name TEXT DEFAULT '',
            panel_user_uuid TEXT DEFAULT '',
            usage_current REAL DEFAULT 0,
            usage_limit REAL DEFAULT 0,
            days_left INTEGER DEFAULT 0,
            start_date TEXT DEFAULT '',
            end_date TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            comment TEXT DEFAULT '',
            wholesale_price INTEGER DEFAULT 0,
            sale_price INTEGER DEFAULT 0,
            is_trial INTEGER DEFAULT 0,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            deleted_at TEXT DEFAULT '',
            payment_operation_key TEXT DEFAULT '',
            last_payment_operation_key TEXT DEFAULT '',
            FOREIGN KEY (agent_id) REFERENCES agent_users(id),
            FOREIGN KEY (customer_id) REFERENCES agent_customers(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_services_agent ON agent_services(agent_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_services_customer ON agent_services(customer_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_services_uuid ON agent_services(panel_user_uuid)")

    # 5. نگاشت سرویس به نودها
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_service_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id INTEGER NOT NULL,
            server_id INTEGER NOT NULL,
            server_title TEXT DEFAULT '',
            panel_user_uuid TEXT NOT NULL,
            panel_user_id TEXT DEFAULT '',
            marzban_username TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            UNIQUE(service_id, server_id, panel_user_uuid)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_service_nodes_svc ON agent_service_nodes(service_id)")

    # 6. ردیابی سرویس‌های ناموجود/قدیمی برای حذف خودکار بعد از 14 روز
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_service_probe (
            service_id INTEGER PRIMARY KEY,
            missing_streak INTEGER DEFAULT 0,
            first_missing_at TEXT DEFAULT '',
            last_missing_at TEXT DEFAULT '',
            last_seen_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            FOREIGN KEY (service_id) REFERENCES agent_services(id)
        )
    """)

    # 7. قیمت‌گذاری (عمده + فروش) برای نماینده
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            server_id INTEGER NOT NULL,
            category_id INTEGER DEFAULT 0,
            plan_title TEXT DEFAULT '',
            days INTEGER DEFAULT 0,
            gb REAL DEFAULT 0,
            wholesale_price INTEGER DEFAULT 0,
            sale_price INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            FOREIGN KEY (agent_id) REFERENCES agent_users(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_plans_agent ON agent_plans(agent_id)")

    # 7. ربات‌های اختصاصی مشتریان هر نماینده
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_customer_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            bot_token TEXT DEFAULT '',
            bot_username TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            FOREIGN KEY (agent_id) REFERENCES agent_users(id)
        )
    """)

    # حذف رکوردهای تکراری (همان ربات مشتری چند بار ثبت شده)
    cur.execute("""
        DELETE FROM agent_customer_bots
        WHERE id NOT IN (
            SELECT MIN(id) FROM agent_customer_bots GROUP BY agent_id, bot_token
        )
    """)
    conn.commit()

    # 8. کدهای تخفیف نماینده
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_discount_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            code TEXT UNIQUE NOT NULL,
            percent INTEGER DEFAULT 0,
            max_uses INTEGER DEFAULT 1,
            used_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            expires_at TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            FOREIGN KEY (agent_id) REFERENCES agent_users(id)
        )
    """)

    # 9. تراکنش‌های نماینده
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            amount INTEGER DEFAULT 0,
            tx_type TEXT DEFAULT '',
            description TEXT DEFAULT '',
            service_id INTEGER DEFAULT 0,
            idempotency_key TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            FOREIGN KEY (agent_id) REFERENCES agent_users(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_tx_agent ON agent_transactions(agent_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_tx_type ON agent_transactions(tx_type)")
    try:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tx_idempotency "
            "ON agent_transactions(idempotency_key) WHERE idempotency_key != ''"
        )
    except sqlite3.OperationalError:
        # DB قدیمی بدون ستون idempotency_key: _migrate_db() پایین ستون را
        # اضافه و ایندکس را می‌سازد. اینجا نباید init_db کرش کند.
        pass

    # 10. تنظیمات نماینده (key-value)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_settings (
            agent_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT DEFAULT '',
            PRIMARY KEY (agent_id, key),
            FOREIGN KEY (agent_id) REFERENCES agent_users(id)
        )
    """)

    # 11. وضعیت یادآوری تمدید هر سرویس
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_service_reminder_state (
            service_id INTEGER PRIMARY KEY,
            days_sent INTEGER DEFAULT -1,
            usage_sent INTEGER DEFAULT -1,
            expired_sent INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT ''
        )
    """)

    _backfill_service_codes(cur)

    conn.commit()
    conn.close()

    _migrate_db()


def _migrate_db():
    """اضافه کردن ستون‌های جدید برای دیتابیس‌های قدیمی."""
    conn = _get_conn()
    cur = conn.cursor()

    # is_trial ستون برای agent_services
    try:
        cur.execute("SELECT is_trial FROM agent_services LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE agent_services ADD COLUMN is_trial INTEGER DEFAULT 0")

    # marzban_username ستون برای agent_service_nodes
    try:
        cur.execute("SELECT marzban_username FROM agent_service_nodes LIMIT 1")
    except sqlite3.OperationalError:
        try:
            cur.execute("ALTER TABLE agent_service_nodes ADD COLUMN marzban_username TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

    # deleted_at ستون برای agent_services (soft-delete)
    try:
        cur.execute("SELECT deleted_at FROM agent_services LIMIT 1")
    except sqlite3.OperationalError:
        try:
            cur.execute("ALTER TABLE agent_services ADD COLUMN deleted_at TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

    for column in ("payment_operation_key", "last_payment_operation_key"):
        try:
            cur.execute(f"SELECT {column} FROM agent_services LIMIT 1")
        except sqlite3.OperationalError:
            cur.execute(f"ALTER TABLE agent_services ADD COLUMN {column} TEXT DEFAULT ''")

    try:
        cur.execute("SELECT idempotency_key FROM agent_transactions LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE agent_transactions ADD COLUMN idempotency_key TEXT DEFAULT ''")
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tx_idempotency "
        "ON agent_transactions(idempotency_key) WHERE idempotency_key != ''"
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_service_payment_operation "
        "ON agent_services(payment_operation_key) WHERE payment_operation_key != ''"
    )

    # expired_sent ستون برای agent_service_reminder_state (پیام انقضا)
    try:
        cur.execute("SELECT expired_sent FROM agent_service_reminder_state LIMIT 1")
    except sqlite3.OperationalError:
        try:
            cur.execute("ALTER TABLE agent_service_reminder_state ADD COLUMN expired_sent INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    # Older schemas did not declare a foreign key on service-node mappings.
    # Remove orphan mappings during startup so they cannot be served as real
    # subscriptions, while keeping the migration non-destructive for services.
    cur.execute(
        "DELETE FROM agent_service_nodes WHERE service_id NOT IN (SELECT id FROM agent_services)"
    )
    cur.execute(
        "DELETE FROM agent_service_probe WHERE service_id NOT IN (SELECT id FROM agent_services)"
    )

    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


# ===============================
#   نماینده‌ها (agent_users)
# ===============================

def upsert_agent(telegram_id: int, username: Optional[str] = None, full_name: Optional[str] = None) -> int:
    """
    ثبت یا بروزرسانی نماینده. آیدی دیتابیسی رو برمی‌گردونه.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()

    cur.execute("SELECT id FROM agent_users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()

    if row:
        agent_id = row["id"]
        cur.execute(
            "UPDATE agent_users SET username = ?, full_name = ?, updated_at = ? WHERE id = ?",
            (username or "", full_name or "", now, agent_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO agent_users (telegram_id, username, full_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (telegram_id, username or "", full_name or "", now, now),
        )
        agent_id = cur.lastrowid

    conn.commit()
    conn.close()
    return int(agent_id)


def get_agent_by_id(agent_id: int) -> Optional[Dict[str, Any]]:
    """گرفتن اطلاعات نماینده با آیدی دیتابیس."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_users WHERE id = ?", (agent_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def make_service_note(agent_id: int) -> str:
    """ساخت یادداشت اشتراک: @username|۶رقم (برای ادمین / نمایندگی / مشتری)."""
    import random as _rnd
    random_part = f"{_rnd.randint(0, 999999):06d}"
    try:
        ag = get_agent_by_id(agent_id)
        username = str((ag or {}).get("username") or "").strip().lstrip("@")
        if not username:
            username = str((ag or {}).get("full_name") or "").strip() or f"id{agent_id}"
        return f"@{username}|{random_part}"
    except Exception:
        return random_part


def get_agent_by_telegram_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    """گرفتن اطلاعات نماینده با آیدی تلگرام."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_agents_list(page: int = 1, page_size: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """لیست نماینده‌ها با صفحه‌بندی."""
    init_db()
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM agent_users")
    total = int(cur.fetchone()["c"] or 0)
    cur.execute("SELECT * FROM agent_users ORDER BY id DESC LIMIT ? OFFSET ?", (page_size, offset))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def get_all_active_agents() -> List[Dict[str, Any]]:
    """لیست تمام نماینده‌های فعال."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_users WHERE is_active = 1 ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_agent_active(agent_id: int, is_active: bool) -> bool:
    """فعال/غیرفعال کردن نماینده."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM agent_users WHERE id = ?", (agent_id,))
    if not cur.fetchone():
        conn.close()
        return False
    cur.execute(
        "UPDATE agent_users SET is_active = ?, updated_at = ? WHERE id = ?",
        (1 if is_active else 0, _now(), agent_id),
    )
    conn.commit()
    conn.close()
    return True


def update_agent(agent_id: int, updates: Dict[str, Any]) -> bool:
    """بروزرسانی فیلدهای نماینده."""
    if not updates:
        return False
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    allowed = {"username", "full_name", "phone"}
    set_parts = []
    values = []
    for key in allowed:
        if key in updates:
            set_parts.append(f"{key} = ?")
            values.append(updates[key])
    if not set_parts:
        conn.close()
        return False
    set_parts.append("updated_at = ?")
    values.append(_now())
    values.append(agent_id)
    cur.execute(f"UPDATE agent_users SET {', '.join(set_parts)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def delete_agent(agent_id: int) -> bool:
    """حذف نماینده ( cascade: تمام داده‌های مربوطه پاک می‌شود)."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM agent_users WHERE id = ?", (agent_id,))
    if not cur.fetchone():
        conn.close()
        return False
    # حذف مرتبط‌ها
    cur.execute("DELETE FROM agent_wallets WHERE agent_id = ?", (agent_id,))
    cur.execute("DELETE FROM agent_discount_codes WHERE agent_id = ?", (agent_id,))
    cur.execute("DELETE FROM agent_customer_bots WHERE agent_id = ?", (agent_id,))
    cur.execute("DELETE FROM agent_transactions WHERE agent_id = ?", (agent_id,))
    cur.execute("DELETE FROM agent_settings WHERE agent_id = ?", (agent_id,))
    cur.execute("DELETE FROM agent_plans WHERE agent_id = ?", (agent_id,))
    # سرویس نودها
    cur.execute(
        "DELETE FROM agent_service_nodes WHERE service_id IN (SELECT id FROM agent_services WHERE agent_id = ?)",
        (agent_id,),
    )
    # سرویس‌ها
    cur.execute("DELETE FROM agent_services WHERE agent_id = ?", (agent_id,))
    # مشتریان
    cur.execute("DELETE FROM agent_customers WHERE agent_id = ?", (agent_id,))
    # خود نماینده
    cur.execute("DELETE FROM agent_users WHERE id = ?", (agent_id,))
    conn.commit()
    conn.close()
    return True


# ===============================
#   کیف پول (agent_wallets)
# ===============================

def get_wallet(agent_id: int) -> Dict[str, Any]:
    """دریافت موجودی کیف پول نماینده. اگه نبود می‌سازه."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_wallets WHERE agent_id = ?", (agent_id,))
    row = cur.fetchone()
    if row:
        conn.close()
        return dict(row)
    # ساخت اولیه
    now = _now()
    cur.execute(
        "INSERT OR IGNORE INTO agent_wallets (agent_id, balance, updated_at) VALUES (?, 0, ?)",
        (agent_id, now),
    )
    conn.commit()
    cur.execute("SELECT * FROM agent_wallets WHERE agent_id = ?", (agent_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {"agent_id": agent_id, "balance": 0, "updated_at": now}


def charge_wallet(agent_id: int, amount: int, description: str = "") -> Dict[str, Any]:
    """
    افزایش موجودی کیف پول (شارژ توسط ادمین).
    amount باید مثبت باشد.
    """
    if amount <= 0:
        return get_wallet(agent_id)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    # تضمین وجود رکورد کیف پول
    cur.execute(
        "INSERT OR IGNORE INTO agent_wallets (agent_id, balance, updated_at) VALUES (?, 0, ?)",
        (agent_id, now),
    )
    cur.execute(
        "UPDATE agent_wallets SET balance = balance + ?, updated_at = ? WHERE agent_id = ?",
        (amount, now, agent_id),
    )
    # ثبت تراکنش
    cur.execute(
        """
        INSERT INTO agent_transactions (agent_id, amount, tx_type, description, created_at)
        VALUES (?, ?, 'charge', ?, ?)
        """,
        (agent_id, amount, description or "شارژ توسط ادمین", now),
    )
    conn.commit()
    cur.execute("SELECT * FROM agent_wallets WHERE agent_id = ?", (agent_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else get_wallet(agent_id)


def deduct_wallet(agent_id: int, amount: int, description: str = "", service_id: int = 0) -> Tuple[bool, Dict[str, Any]]:
    """
    کسر از کیف پول (مثلاً موقع خرید سرویس).
    خروجی: (موفقیت, اطلاعات کیف پول)
    """
    if amount <= 0:
        return True, get_wallet(agent_id)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "INSERT OR IGNORE INTO agent_wallets (agent_id, balance, updated_at) VALUES (?, 0, ?)",
        (agent_id, now),
    )
    cur.execute(
        "UPDATE agent_wallets SET balance = balance - ?, updated_at = ? WHERE agent_id = ? AND balance >= ?",
        (amount, now, agent_id, amount),
    )
    if cur.rowcount <= 0:
        conn.close()
        return False, get_wallet(agent_id)
    # ثبت تراکنش
    cur.execute(
        """
        INSERT INTO agent_transactions (agent_id, amount, tx_type, description, service_id, created_at)
        VALUES (?, ?, 'purchase', ?, ?, ?)
        """,
        (agent_id, amount, description or "خرید سرویس", service_id, now),
    )
    tx_id = int(cur.lastrowid or 0)
    conn.commit()
    cur.execute("SELECT * FROM agent_wallets WHERE agent_id = ?", (agent_id,))
    row = cur.fetchone()
    conn.close()
    wallet_info = dict(row) if row else get_wallet(agent_id)
    # شناسه تراکنش برای اتصال قطعی بعدی به سرویس واقعی (مثلاً پس از ساخت سرویس)
    wallet_info["_transaction_id"] = tx_id
    return True, wallet_info


def refund_wallet(agent_id: int, amount: int, description: str = "", service_id: int = 0) -> Dict[str, Any]:
    """بازگرداندن مبلغ به کیف پول نماینده با ثبت تراکنش refund."""
    if amount <= 0:
        return get_wallet(agent_id)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "INSERT OR IGNORE INTO agent_wallets (agent_id, balance, updated_at) VALUES (?, 0, ?)",
        (agent_id, now),
    )
    cur.execute(
        "UPDATE agent_wallets SET balance = balance + ?, updated_at = ? WHERE agent_id = ?",
        (amount, now, agent_id),
    )
    cur.execute(
        """
        INSERT INTO agent_transactions (agent_id, amount, tx_type, description, service_id, created_at)
        VALUES (?, ?, 'refund', ?, ?, ?)
        """,
        (agent_id, amount, description or "بازگشت وجه", service_id, now),
    )
    conn.commit()
    cur.execute("SELECT * FROM agent_wallets WHERE agent_id = ?", (agent_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else get_wallet(agent_id)


def _wallet_transaction_once(
    agent_id: int,
    amount: int,
    tx_type: str,
    idempotency_key: str,
    description: str = "",
    service_id: int = 0,
) -> Tuple[bool, Dict[str, Any]]:
    """Apply one wallet mutation exactly once inside the agency database.

    The idempotency key is deliberately stored in the same SQLite transaction
    as the balance change.  A caller may therefore retry after a process crash
    without charging, deducting, or refunding the wallet twice.
    """
    key = str(idempotency_key or "").strip()
    kind = str(tx_type or "").strip().lower()
    value = int(amount or 0)
    if not key:
        raise ValueError("idempotency_key is required")
    if kind not in {"charge", "purchase", "refund"}:
        raise ValueError("invalid wallet transaction type")
    if value < 0:
        raise ValueError("wallet transaction amount cannot be negative")
    if value == 0:
        return True, get_wallet(agent_id)

    init_db()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            "SELECT agent_id, amount, tx_type, service_id FROM agent_transactions WHERE idempotency_key = ?",
            (key,),
        )
        existing = cur.fetchone()
        if existing:
            if (
                int(existing["agent_id"] or 0) != int(agent_id)
                or int(existing["amount"] or 0) != value
                or str(existing["tx_type"] or "") != kind
                or int(existing["service_id"] or 0) != int(service_id or 0)
            ):
                raise ValueError("idempotency key already belongs to another wallet operation")
            cur.execute("SELECT * FROM agent_wallets WHERE agent_id = ?", (agent_id,))
            wallet_row = cur.fetchone()
            conn.rollback()
            return True, dict(wallet_row) if wallet_row else {"agent_id": agent_id, "balance": 0}

        now = _now()
        cur.execute(
            "INSERT OR IGNORE INTO agent_wallets (agent_id, balance, updated_at) VALUES (?, 0, ?)",
            (agent_id, now),
        )
        if kind == "purchase":
            cur.execute(
                "UPDATE agent_wallets SET balance = balance - ?, updated_at = ? "
                "WHERE agent_id = ? AND balance >= ?",
                (value, now, agent_id, value),
            )
            if cur.rowcount <= 0:
                cur.execute("SELECT * FROM agent_wallets WHERE agent_id = ?", (agent_id,))
                wallet_row = cur.fetchone()
                wallet = dict(wallet_row) if wallet_row else {"agent_id": agent_id, "balance": 0}
                conn.rollback()
                return False, wallet
        else:
            cur.execute(
                "UPDATE agent_wallets SET balance = balance + ?, updated_at = ? WHERE agent_id = ?",
                (value, now, agent_id),
            )

        cur.execute(
            """
            INSERT INTO agent_transactions
                (agent_id, amount, tx_type, description, service_id, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (agent_id, value, kind, description, int(service_id or 0), key, now),
        )
        cur.execute("SELECT * FROM agent_wallets WHERE agent_id = ?", (agent_id,))
        wallet_row = cur.fetchone()
        wallet = dict(wallet_row) if wallet_row else {"agent_id": agent_id, "balance": 0}
        conn.commit()
        return True, wallet
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def charge_wallet_once(
    agent_id: int,
    amount: int,
    idempotency_key: str,
    description: str = "",
) -> Dict[str, Any]:
    ok, wallet = _wallet_transaction_once(
        agent_id, amount, "charge", idempotency_key, description or "شارژ توسط ادمین"
    )
    if not ok:
        raise RuntimeError("wallet charge failed")
    return wallet


def deduct_wallet_once(
    agent_id: int,
    amount: int,
    idempotency_key: str,
    description: str = "",
    service_id: int = 0,
) -> Tuple[bool, Dict[str, Any]]:
    return _wallet_transaction_once(
        agent_id, amount, "purchase", idempotency_key, description or "خرید سرویس", service_id
    )


def refund_wallet_once(
    agent_id: int,
    amount: int,
    idempotency_key: str,
    description: str = "",
    service_id: int = 0,
) -> Dict[str, Any]:
    ok, wallet = _wallet_transaction_once(
        agent_id, amount, "refund", idempotency_key, description or "بازگشت وجه", service_id
    )
    if not ok:
        raise RuntimeError("wallet refund failed")
    return wallet


def get_wallet_transaction_by_key(idempotency_key: str) -> Optional[Dict[str, Any]]:
    key = str(idempotency_key or "").strip()
    if not key:
        return None
    init_db()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM agent_transactions WHERE idempotency_key = ? LIMIT 1", (key,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_wallet_balance(agent_id: int) -> int:
    """دریافت فقط موجودی عددی کیف پول."""
    wallet = get_wallet(agent_id)
    return int(wallet.get("balance", 0))


# ===============================
#   تراکنش‌ها (agent_transactions)
# ===============================

def get_transactions(agent_id: int, page: int = 1, page_size: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """لیست تراکنش‌های یک نماینده."""
    init_db()
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM agent_transactions WHERE agent_id = ?", (agent_id,))
    total = int(cur.fetchone()["c"] or 0)
    cur.execute(
        "SELECT * FROM agent_transactions WHERE agent_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (agent_id, page_size, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def get_all_transactions(page: int = 1, page_size: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """لیست تمام تراکنش‌ها (برای ادمین)."""
    init_db()
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM agent_transactions")
    total = int(cur.fetchone()["c"] or 0)
    cur.execute(
        "SELECT * FROM agent_transactions ORDER BY id DESC LIMIT ? OFFSET ?",
        (page_size, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def add_transaction(agent_id: int, amount: int, tx_type: str, description: str = "", service_id: int = 0) -> int:
    """ثبت تراکنش دستی. آیدی تراکنش رو برمی‌گردونه."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        """
        INSERT INTO agent_transactions (agent_id, amount, tx_type, description, service_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (agent_id, amount, tx_type, description, service_id, now),
    )
    conn.commit()
    tx_id = cur.lastrowid
    conn.close()
    return int(tx_id)


# ===============================
#   مشتریان نماینده (agent_customers)
# ===============================

def upsert_customer(agent_id: int, telegram_id: int, username: str = "", full_name: str = "") -> int:
    """
    ثبت یا بروزرسانی مشتری نماینده.
    آیدی دیتابیسی رو برمی‌گردونه.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()

    cur.execute(
        "SELECT id FROM agent_customers WHERE agent_id = ? AND telegram_id = ?",
        (agent_id, telegram_id),
    )
    row = cur.fetchone()

    if row:
        customer_id = row["id"]
        cur.execute(
            "UPDATE agent_customers SET username = ?, full_name = ?, updated_at = ? WHERE id = ?",
            (username or "", full_name or "", now, customer_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO agent_customers (agent_id, telegram_id, username, full_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (agent_id, telegram_id, username or "", full_name or "", now, now),
        )
        customer_id = cur.lastrowid

    conn.commit()
    conn.close()
    return int(customer_id)


def get_customer_by_id(customer_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_customers WHERE id = ?", (customer_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_customer_by_telegram_id(agent_id: int, telegram_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM agent_customers WHERE agent_id = ? AND telegram_id = ?",
        (agent_id, telegram_id),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_customers_list(agent_id: int, page: int = 1, page_size: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """لیست مشتریان یک نماینده."""
    init_db()
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM agent_customers WHERE agent_id = ?", (agent_id,))
    total = int(cur.fetchone()["c"] or 0)
    cur.execute(
        "SELECT * FROM agent_customers WHERE agent_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (agent_id, page_size, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def search_customers(agent_id: int, query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    جستجوی مشتریان نماینده بر اساس نام، یوزرنیم یا آیدی تلگرام.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    like = f"%{query}%"
    cur.execute(
        """
        SELECT * FROM agent_customers
        WHERE agent_id = ? AND (
            full_name LIKE ? OR username LIKE ? OR CAST(telegram_id AS TEXT) LIKE ?
        )
        ORDER BY id DESC LIMIT ?
        """,
        (agent_id, like, like, like, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_customer(customer_id: int) -> bool:
    """حذف مشتری و سرویس‌هاش."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT agent_id, id FROM agent_customers WHERE id = ?", (customer_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    # حذف سرویس نودها
    cur.execute(
        "DELETE FROM agent_service_nodes WHERE service_id IN (SELECT id FROM agent_services WHERE customer_id = ?)",
        (customer_id,),
    )
    # حذف سرویس‌ها
    cur.execute("DELETE FROM agent_services WHERE customer_id = ?", (customer_id,))
    # حذف مشتری
    cur.execute("DELETE FROM agent_customers WHERE id = ?", (customer_id,))
    conn.commit()
    conn.close()
    return True


# ===============================
#   ربات‌های مشتری (agent_customer_bots)
# ===============================

def add_customer_bot(agent_id: int, bot_token: str, bot_username: str = "") -> Dict[str, Any]:
    """ثبت ربات مشتری جدید برای نماینده (در صورت وجود همان توکن، به‌روزرسانی می‌کند)."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    token = bot_token.strip()
    username = bot_username.strip()
    cur.execute(
        "SELECT * FROM agent_customer_bots WHERE agent_id = ? AND bot_token = ?",
        (agent_id, token),
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE agent_customer_bots SET bot_username = ?, is_active = 1, updated_at = ? WHERE id = ?",
            (username, now, row["id"]),
        )
        conn.commit()
        cur.execute("SELECT * FROM agent_customer_bots WHERE id = ?", (row["id"],))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else {}
    cur.execute(
        """
        INSERT INTO agent_customer_bots (agent_id, bot_token, bot_username, is_active, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (agent_id, token, username, now, now),
    )
    conn.commit()
    bot_id = cur.lastrowid
    cur.execute("SELECT * FROM agent_customer_bots WHERE id = ?", (bot_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def get_customer_bots(agent_id: int) -> List[Dict[str, Any]]:
    """لیست ربات‌های مشتری یک نماینده."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, agent_id, bot_username, is_active, created_at FROM agent_customer_bots WHERE agent_id = ? ORDER BY id",
        (agent_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_customer_bot_by_id(bot_id: int) -> Optional[Dict[str, Any]]:
    """گرفتن اطلاعات ربات مشتری با آیدی."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_customer_bots WHERE id = ?", (bot_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_active_customer_bot(agent_id: int) -> Optional[Dict[str, Any]]:
    """اولین ربات مشتری فعال یک نماینده را برمی‌گرداند."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cb.*, au.telegram_id AS agent_telegram_id
        FROM agent_customer_bots cb
        JOIN agent_users au ON cb.agent_id = au.id
        WHERE cb.agent_id = ? AND cb.is_active = 1 AND au.is_active = 1
        ORDER BY cb.id DESC
        LIMIT 1
        """,
        (agent_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_active_customer_bots() -> List[Dict[str, Any]]:
    """لیست تمام ربات‌های مشتری فعال (برای اجرای instance)."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cb.*, au.telegram_id AS agent_telegram_id
        FROM agent_customer_bots cb
        JOIN agent_users au ON cb.agent_id = au.id
        WHERE cb.is_active = 1 AND au.is_active = 1
        ORDER BY cb.id
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_customer_bot_active(bot_id: int, is_active: bool) -> bool:
    """فعال/غیرفعال کردن ربات مشتری."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM agent_customer_bots WHERE id = ?", (bot_id,))
    if not cur.fetchone():
        conn.close()
        return False
    cur.execute(
        "UPDATE agent_customer_bots SET is_active = ?, updated_at = ? WHERE id = ?",
        (1 if is_active else 0, _now(), bot_id),
    )
    conn.commit()
    conn.close()
    return True


def delete_customer_bot(bot_id: int) -> bool:
    """حذف ربات مشتری."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM agent_customer_bots WHERE id = ?", (bot_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# ===============================
#   سرویس‌ها (agent_services)
# ===============================

def _service_code_from_comment(comment: str) -> str:
    """استخراج شناسه ۷ رقمی سرویس از comment به فرم code:XXXXXXX."""
    for part in str(comment or "").split("|"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        if k.strip().lower() == "code":
            return str(v).strip()
    return ""


def _service_note_from_comment(comment: str) -> str:
    """استخراج یادداشت سرویس از comment به فرم note:... (ممکن است خودش شامل | باشد)."""
    text = str(comment or "")
    idx = text.find("note:")
    if idx == -1:
        return ""
    return text[idx + len("note:"):].strip()


def make_service_note(agent_id: int) -> str:
    """ساخت یادداشت اشتراک: یوزرنیم نماینده | عدد ۷ رقمی رندم."""
    import random as _rnd
    num = f"{_rnd.randint(0, 9999999):07d}"
    try:
        ag = get_agent_by_id(agent_id)
        ag = ag or {}
    except Exception:
        ag = {}
    uname = str(ag.get("username") or "").strip().lstrip("@")
    if not uname:
        uname = str(ag.get("full_name") or "").strip() or f"id{agent_id}"
    return f"{uname}|{num}"


def _generate_service_code(cur) -> str:
    """تولید شناسه ۷ رقمی یکتا برای سرویس."""
    import random

    for _ in range(50):
        code = f"{random.randint(0, 9999999):07d}"
        cur.execute(
            "SELECT 1 FROM agent_services WHERE comment LIKE ? LIMIT 1",
            (f"%code:{code}%",),
        )
        if not cur.fetchone():
            return code
    return f"{random.randint(0, 9999999):07d}"


def _backfill_service_codes(cur) -> int:
    """برای سرویس‌های قدیمی که شناسه ندارند، کد ۷ رقمی بساز و ذخیره کن."""
    try:
        cur.execute(
            "SELECT id, comment FROM agent_services WHERE comment IS NULL OR comment NOT LIKE '%code:%'"
        )
        rows = cur.fetchall()
    except Exception:
        return 0
    fixed = 0
    for row in rows:
        code = _generate_service_code(cur)
        new_comment = str((row["comment"] if "comment" in row.keys() else "") or "").strip()
        if new_comment and not new_comment.endswith("|"):
            new_comment += "|"
        new_comment += f"code:{code}"
        cur.execute(
            "UPDATE agent_services SET comment = ? WHERE id = ?",
            (new_comment, int(row["id"])),
        )
        fixed += 1
    return fixed


def get_service_by_code(service_code: str, agent_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """پیدا کردن سرویس با شناسه ۷ رقمی ذخیره‌شده در comment به فرم code:XXXXXXX."""
    init_db()
    code = str(service_code or "").strip()
    if not code:
        return None
    conn = _get_conn()
    cur = conn.cursor()
    try:
        if agent_id:
            cur.execute(
                "SELECT * FROM agent_services WHERE agent_id = ? AND comment LIKE ? "
                "AND (deleted_at IS NULL OR deleted_at = '') ORDER BY id DESC LIMIT 200",
                (int(agent_id), f"%code:{code}%"),
            )
        else:
            cur.execute(
                "SELECT * FROM agent_services WHERE comment LIKE ? "
                "AND (deleted_at IS NULL OR deleted_at = '') ORDER BY id DESC LIMIT 200",
                (f"%code:{code}%",),
            )
        rows = cur.fetchall()
        for row in rows:
            if _service_code_from_comment(str(row["comment"] or "")) == code:
                return dict(row)
        return None
    finally:
        conn.close()


def create_service(
    agent_id: int,
    customer_id: int,
    server_id: int,
    server_title: str = "",
    name: str = "",
    panel_user_uuid: str = "",
    usage_limit: float = 0,
    days: int = 0,
    wholesale_price: int = 0,
    sale_price: int = 0,
    is_trial: int = 0,
    comment: str = "",
    note: str = "",
    payment_operation_key: str = "",
) -> Dict[str, Any]:
    """
    ساخت سرویس جدید برای مشتری.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    operation_key = str(payment_operation_key or "").strip()

    if operation_key:
        cur.execute(
            "SELECT * FROM agent_services WHERE payment_operation_key = ? LIMIT 1",
            (operation_key,),
        )
        existing = cur.fetchone()
        if existing:
            conn.close()
            return dict(existing)

    end_date = ""
    if days > 0:
        try:
            end_date = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    cur.execute(
        """
        INSERT INTO agent_services (
            agent_id, customer_id, server_id, server_title, name, panel_user_uuid,
            usage_limit, days_left, start_date, end_date, is_active,
            wholesale_price, sale_price, is_trial, created_at, updated_at,
            payment_operation_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
        """,
        (agent_id, customer_id, server_id, server_title, name, panel_user_uuid,
         usage_limit, days, now, end_date, wholesale_price, sale_price, is_trial, now, now,
         operation_key),
    )
    svc_id = cur.lastrowid

    # شناسه ۷ رقمی سرویس در comment (code:XXXXXXX) برای جستجو در ادمین/نمایندگی
    code = _generate_service_code(cur)
    comment_text = str(comment or "").strip()
    if comment_text and not comment_text.endswith("|"):
        comment_text += "|"
    comment_text += f"code:{code}"
    # یادداشت در comment (note:...). اگر ارسال شده باشد از آن استفاده کن، وگرنه عدد ۷ رقمی رندم.
    if str(note or "").strip():
        comment_text += f"|note:{str(note).strip()}"
    else:
        import random as _rnd
        note_val = f"{_rnd.randint(0, 9999999):07d}"
        comment_text += f"|note:{note_val}"
    cur.execute(
        "UPDATE agent_services SET comment = ? WHERE id = ?",
        (comment_text, svc_id),
    )
    conn.commit()
    cur.execute("SELECT * FROM agent_services WHERE id = ?", (svc_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def get_service_by_id(service_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_services WHERE id = ? AND (deleted_at IS NULL OR deleted_at = '')", (service_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_service_by_uuid(panel_user_uuid: str) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_services WHERE panel_user_uuid = ? AND (deleted_at IS NULL OR deleted_at = '')", (panel_user_uuid,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_service_by_payment_operation(payment_operation_key: str) -> Optional[Dict[str, Any]]:
    key = str(payment_operation_key or "").strip()
    if not key:
        return None
    init_db()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM agent_services WHERE payment_operation_key = ? LIMIT 1",
            (key,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_services_by_agent(agent_id: int, page: int = 1, page_size: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """لیست سرویس‌های یک نماینده."""
    init_db()
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM agent_services WHERE agent_id = ? AND (deleted_at IS NULL OR deleted_at = '')", (agent_id,))
    total = int(cur.fetchone()["c"] or 0)
    cur.execute(
        "SELECT * FROM agent_services WHERE agent_id = ? AND (deleted_at IS NULL OR deleted_at = '') ORDER BY id DESC LIMIT ? OFFSET ?",
        (agent_id, page_size, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def search_services_by_name(agent_id: int, name: str, page: int = 1, page_size: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """جستجوی اشتراک‌های یک نماینده بر اساس نام (جستجوی جزئی و بدون حساسیت به حروف بزرگ/کوچک)."""
    init_db()
    term = (name or "").strip()
    if not term:
        return [], 0
    like = f"%{term}%"
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) AS c FROM agent_services "
            "WHERE agent_id = ? AND (deleted_at IS NULL OR deleted_at = '') AND LOWER(name) LIKE LOWER(?)",
            (agent_id, like),
        )
        total = int(cur.fetchone()["c"] or 0)
        cur.execute(
            "SELECT * FROM agent_services "
            "WHERE agent_id = ? AND (deleted_at IS NULL OR deleted_at = '') AND LOWER(name) LIKE LOWER(?) "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (agent_id, like, page_size, offset),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def get_active_services_by_agent_paged(agent_id: int, page: int = 1, page_size: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """سرویس‌های فعال یک نماینده با صفحه‌بندی."""
    init_db()
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) AS c FROM agent_services WHERE agent_id = ? AND is_active = 1 "
            "AND (deleted_at IS NULL OR deleted_at = '')",
            (agent_id,),
        )
        total = int(cur.fetchone()["c"] or 0)
        cur.execute(
            "SELECT * FROM agent_services WHERE agent_id = ? AND is_active = 1 "
            "AND (deleted_at IS NULL OR deleted_at = '') ORDER BY id DESC LIMIT ? OFFSET ?",
            (agent_id, page_size, offset),
        )
        return [dict(r) for r in cur.fetchall()], total
    finally:
        conn.close()


def get_inactive_services_by_agent_paged(agent_id: int, page: int = 1, page_size: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """سرویس‌های غیرفعال یک نماینده با صفحه‌بندی."""
    init_db()
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) AS c FROM agent_services WHERE agent_id = ? AND is_active = 0 "
            "AND (deleted_at IS NULL OR deleted_at = '')",
            (agent_id,),
        )
        total = int(cur.fetchone()["c"] or 0)
        cur.execute(
            "SELECT * FROM agent_services WHERE agent_id = ? AND is_active = 0 "
            "AND (deleted_at IS NULL OR deleted_at = '') ORDER BY id DESC LIMIT ? OFFSET ?",
            (agent_id, page_size, offset),
        )
        return [dict(r) for r in cur.fetchall()], total
    finally:
        conn.close()


def get_agent_services_stats(agent_id: int) -> Dict[str, Any]:
    """آمار سرویس‌های یک نماینده — منطق وضعیت انحصاریِ مشترک با فیلترها.

    وضعیت‌ها هم‌پوشان نیستند:
      - expired     : زمان پایان مشخص و گذشته (UTC) یا در نبود end_date
                      days_left منفی — مستقل از is_active.
      - active      : منقضی نیست و is_active = 1.
      - inactive    : منقضی نیست و is_active = 0.
      - near_expiry : فعال است و اعتبار مثبت آن حداکثر ۳ روز است.
      - مابقی (end_date خالی و days_left=0) → «نامشخص»؛ حدس زده نمی‌شود.

    Returns:
        {
            "total": int,
            "active": int,
            "inactive": int,
            "near_expiry": int,
            "expired": int,
            "top_server": str,      # عنوان پرتکرارترین سرور (به همراه flag) یا ""
            "top_server_count": int,
        }
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        base = "FROM agent_services WHERE agent_id = ? AND (deleted_at IS NULL OR deleted_at = '')"
        cur.execute(f"SELECT COUNT(*) AS c {base}", (agent_id,))
        total = int(cur.fetchone()["c"] or 0)

        now_str = _utcnow_naive().strftime("%Y-%m-%d %H:%M:%S")
        status_sql = _service_status_sql(now_str)
        counts: Dict[str, int] = {}
        for key in ("active", "inactive", "near", "expired"):
            cur.execute(f"SELECT COUNT(*) AS c {base} AND {status_sql[key]}", (agent_id,))
            counts[key] = int(cur.fetchone()["c"] or 0)
        active = counts["active"]
        inactive = counts["inactive"]
        near_expiry = counts["near"]
        expired = counts["expired"]

        # پرتکرارترین سرور
        cur.execute(
            "SELECT server_title, COUNT(*) AS c FROM agent_services "
            "WHERE agent_id = ? AND (deleted_at IS NULL OR deleted_at = '') AND server_title != '' "
            "GROUP BY server_title ORDER BY c DESC, server_title LIMIT 1",
            (agent_id,),
        )
        top_row = cur.fetchone()
        top_server = str(top_row["server_title"] or "") if top_row else ""
        top_server_count = int(top_row["c"] or 0) if top_row else 0

        return {
            "total": total,
            "active": active,
            "inactive": inactive,
            "near_expiry": near_expiry,
            "expired": expired,
            "top_server": top_server,
            "top_server_count": top_server_count,
        }
    finally:
        conn.close()


# -----------------------------------------------
#   ادمین: لیست/جستجو/مرتب‌سازی سرویس‌های یک نماینده
# -----------------------------------------------

def _utcnow_naive() -> datetime:
    """زمان فعلی به‌صورت UTC بدون timezone — هم‌قرارداد با ذخیره‌سازی end_date."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _service_status_sql(now_str: str) -> Dict[str, str]:
    """شرط‌های SQL وضعیت سرویس — منطق مشترک و انحصاری برای آمار/فیلتر/رابط.

    - expired  : زمان پایان مشخص و گذشته، یا در نبود end_date مقدار days_left
                 منفی — مستقل از is_active.
    - inactive : منقضی نباشد و is_active = 0.
    - active   : منقضی نباشد و is_active = 1.
    - near     : فعال و اعتبار مثبت با حداکثر ۳ روز باقی‌مانده.
    unknown (end_date خالی و days_left=0) در هیچ‌کدام حدس زده نمی‌شود.
    """
    expired = (
        "((end_date IS NOT NULL AND end_date != '' AND end_date < {now}) "
        "OR ((end_date IS NULL OR end_date = '') AND days_left < 0))"
    ).format(now="'%s'" % now_str)
    not_expired = f"(NOT {expired})"
    active = f"({not_expired} AND is_active = 1)"
    near = (
        f"({active} AND ("
        f"(end_date IS NOT NULL AND end_date != '' AND end_date <= {{plus3}}) "
        f"OR (days_left > 0 AND days_left <= 3)"
        f"))"
    ).format(plus3="'%s'" % (datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S") + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"))
    return {
        "expired": expired,
        "not_expired": not_expired,
        "active": active,
        "inactive": f"({not_expired} AND is_active = 0)",
        "near": near,
    }


AGENT_SERVICE_SORTS = ("newest", "expiry", "name")


def list_services_by_agent_sorted(
    agent_id: int,
    page: int = 1,
    page_size: int = 8,
    status_filter: str = "all",
    sort: str = "newest",
    search: str = "",
) -> Tuple[List[Dict[str, Any]], int]:
    """لیست سرویس‌های یک نماینده با فیلتر/مرتب‌سازی/جستجو (سمت ادمین).

    status_filter (منطق انحصاریِ مشترک با آمار — _service_status_sql):
      - all      : همه
      - active   : منقضی نیست و is_active = 1
      - inactive : منقضی نیست و is_active = 0 (با منقضی اشتباه گرفته نمی‌شود)
      - expired  : اعتبار تمام‌شده (end_date در گذشته‌ی UTC یا days_left < 0)
    sort:
      - newest : جدیدترین (id نزولی)
      - expiry : نزدیک‌ترین انقضا (سرویس بدون تاریخ انقضا در انتها)
      - name   : بر اساس نام
    search: جستجوی جزئی در نام سرویس، شناسه ۷ رقمی (comment code:)، UUID و
    تطبیق دقیق id داخلی نمایش‌داده‌شده در دکمه‌ها (مثل 66 یا #66).
    سرویس‌های حذف‌شده (deleted_at) هرگز برنمی‌گردند.
    """
    init_db()
    page = max(1, int(page or 1))
    status_filter = str(status_filter or "all").strip().lower()
    if status_filter not in {"all", "active", "inactive", "expired"}:
        status_filter = "all"
    sort = str(sort or "newest").strip().lower()
    if sort not in AGENT_SERVICE_SORTS:
        sort = "newest"
    term = str(search or "").strip()

    now_str = _utcnow_naive().strftime("%Y-%m-%d %H:%M:%S")
    status_sql = _service_status_sql(now_str)

    where = ["agent_id = ?", "(deleted_at IS NULL OR deleted_at = '')"]
    params: List[Any] = [int(agent_id)]

    if status_filter == "active":
        where.append(status_sql["active"])
    elif status_filter == "inactive":
        where.append(status_sql["inactive"])
    elif status_filter == "expired":
        where.append(status_sql["expired"])

    if term:
        like = f"%{term}%"
        condition = (
            "(LOWER(name) LIKE LOWER(?) OR LOWER(panel_user_uuid) LIKE LOWER(?) "
            "OR comment LIKE ?)"
        )
        params.extend([like, like, f"%code:{term}%"])
        # تطبیق دقیق id داخلی (مثل 66 یا #66) — جستجوی «6» نباید 66/106 را برگرداند
        digits = term.lstrip("#").strip()
        if digits.isdigit():
            condition = condition[:-1] + f" OR id = {int(digits)})"
        where.append(condition)

    where_sql = " AND ".join(where)

    if sort == "expiry":
        order_sql = (
            "CASE WHEN end_date IS NULL OR end_date = '' THEN 1 ELSE 0 END, "
            "end_date ASC, id DESC"
        )
    elif sort == "name":
        order_sql = "LOWER(name) ASC, id DESC"
    else:
        order_sql = "id DESC"

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT COUNT(*) AS c FROM agent_services WHERE {where_sql}",
            params,
        )
        total = int(cur.fetchone()["c"] or 0)
        offset = (page - 1) * page_size
        cur.execute(
            f"SELECT * FROM agent_services WHERE {where_sql} "
            f"ORDER BY {order_sql} LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        )
        return [dict(r) for r in cur.fetchall()], total
    finally:
        conn.close()


def attach_transaction_to_service(transaction_id: int, agent_id: int, service_id: int) -> bool:
    """اتصال قطعی یک تراکنش به سرویس واقعی (مثلاً خرید اولیه پس از ساخت سرویس).

    - با شناسه تراکنش انجام می‌شود (نه تطبیق مبلغ/متن) → دقیق و بدون حدس.
    - فقط وقتی تراکنش هنوز service_id=0 دارد متصل می‌شود (ایمن در برابر
      هم‌زمانی و بدون کسر/اتصال دوباره).
    - تراکنش‌های متعلق به نماینده دیگر متصل نمی‌شوند.
    """
    init_db()
    try:
        tx_id = int(transaction_id or 0)
        agent_id = int(agent_id or 0)
        service_id = int(service_id or 0)
    except (TypeError, ValueError):
        return False
    if tx_id <= 0 or agent_id <= 0 or service_id <= 0:
        return False
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE agent_transactions SET service_id = ? "
            "WHERE id = ? AND agent_id = ? AND service_id = 0",
            (service_id, tx_id, agent_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_service_transactions(service_id: int, agent_id: int, limit: int = 8) -> List[Dict[str, Any]]:
    """تراکنش‌های مرتبط با یک سرویس خاص (ستون service_id در agent_transactions).

    فقط تراکنش‌هایی برگردانده می‌شود که صریحاً به همین سرویس از همین نماینده
    متصل هستند؛ تراکنش‌های کلی نماینده (service_id=0) نسبت داده نمی‌شوند.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM agent_transactions "
            "WHERE service_id = ? AND agent_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (int(service_id or 0), int(agent_id or 0), max(1, int(limit or 8))),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _older_than_days(ts: str, days: int) -> bool:
    """بررسی اینکه تاریخ گذشته از بازه‌ی داده‌شده بیشتر است یا نه."""
    if not ts:
        return False
    value = str(ts).strip()
    if not value:
        return False
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            break
        except ValueError:
            dt = None
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() >= days * 86400


def mark_service_seen(service_id: int) -> None:
    """بازنشانی ردیابی حذف سرویس در صورت وجود مجدد."""
    init_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO agent_service_probe (service_id, missing_streak, first_missing_at, last_seen_at, last_missing_at, updated_at)
            VALUES (?, 0, NULL, ?, NULL, ?)
            ON CONFLICT(service_id) DO UPDATE SET
                missing_streak = 0,
                first_missing_at = NULL,
                last_seen_at = excluded.last_seen_at,
                last_missing_at = NULL,
                updated_at = excluded.updated_at
            """,
            (service_id, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def mark_service_missing(service_id: int) -> Dict[str, Any]:
    """ثبت اینکه سرویس در این اجرا ناموجود شد؛ حذف نهایی فقط بعد از TTL."""
    init_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT missing_streak, first_missing_at FROM agent_service_probe WHERE service_id = ? LIMIT 1",
            (service_id,),
        )
        row = cur.fetchone()
        streak = int((row["missing_streak"] if row else 0) or 0) + 1
        first_missing_at = str((row["first_missing_at"] if row else "") or "").strip() or now
        cur.execute(
            """
            INSERT INTO agent_service_probe (service_id, missing_streak, first_missing_at, last_missing_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(service_id) DO UPDATE SET
                missing_streak = excluded.missing_streak,
                first_missing_at = COALESCE(agent_service_probe.first_missing_at, excluded.first_missing_at),
                last_missing_at = excluded.last_missing_at,
                updated_at = excluded.updated_at
            """,
            (service_id, streak, first_missing_at, now, now),
        )
        conn.commit()
        return {"missing_streak": streak, "first_missing_at": first_missing_at, "last_missing_at": now}
    finally:
        conn.close()


def cleanup_stale_agent_services(days: int = 7) -> int:
    """حذف خودکار سرویس‌های طولانی‌مدت ناموجود در پنل پس از 7 روز."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT service_id, first_missing_at FROM agent_service_probe WHERE missing_streak > 0 AND first_missing_at != ''",
        )
        service_ids = [
            int(row["service_id"])
            for row in cur.fetchall()
            if _older_than_days(str(row["first_missing_at"] or ""), days)
        ]
    finally:
        conn.close()

    removed = 0
    for service_id in sorted(set(service_ids)):
        try:
            if delete_service(service_id):
                removed += 1
        except Exception:
            pass
    return removed


def get_services_by_customer(customer_id: int) -> List[Dict[str, Any]]:
    """لیست تمام سرویس‌های یک مشتری (فقط سرویس‌هایی که روی پنل هنوز وجود دارند)."""
    cleanup_stale_agent_services(7)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.* FROM agent_services s
        LEFT JOIN agent_service_probe p ON p.service_id = s.id
        WHERE s.customer_id = ?
          AND (s.deleted_at IS NULL OR s.deleted_at = '')
          AND COALESCE(p.missing_streak, 0) = 0
        ORDER BY s.id DESC
        """,
        (customer_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_active_services_by_agent(agent_id: int) -> List[Dict[str, Any]]:
    """لیست سرویس‌های فعال یک نماینده."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM agent_services WHERE agent_id = ? AND is_active = 1 ORDER BY id DESC",
        (agent_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_active_services() -> List[Dict[str, Any]]:
    """لیست تمام سرویس‌های فعال (برای ادمین)."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_services WHERE is_active = 1 ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_expired_services_by_agent(agent_id: int, page: int = 1, page_size: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """لیست سرویس‌های منقضی شده یک نماینده با صفحه‌بندی."""
    init_db()
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "SELECT COUNT(*) AS c FROM agent_services WHERE agent_id = ? AND end_date != '' AND end_date < ? "
        "AND (deleted_at IS NULL OR deleted_at = '')",
        (agent_id, now_str),
    )
    total = int(cur.fetchone()["c"] or 0)
    cur.execute(
        "SELECT * FROM agent_services WHERE agent_id = ? AND end_date != '' AND end_date < ? "
        "AND (deleted_at IS NULL OR deleted_at = '') ORDER BY id DESC LIMIT ? OFFSET ?",
        (agent_id, now_str, page_size, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def update_service(service_id: int, updates: Dict[str, Any]) -> bool:
    """بروزرسانی فیلدهای سرویس."""
    if not updates:
        return False
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    allowed = {
        "name", "panel_user_uuid", "usage_current", "usage_limit",
        "days_left", "start_date", "end_date", "is_active",
        "comment", "sale_price", "wholesale_price", "server_title",
    }
    set_parts = []
    values = []
    for key in allowed:
        if key in updates:
            set_parts.append(f"{key} = ?")
            values.append(updates[key])
    if not set_parts:
        conn.close()
        return False
    set_parts.append("updated_at = ?")
    values.append(_now())
    values.append(service_id)
    cur.execute(f"UPDATE agent_services SET {', '.join(set_parts)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def renew_service(service_id: int, extra_days: int, extra_gb: float = 0) -> bool:
    """
    تمدید سرویس: اضافه کردن روز و حجم.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT days_left, end_date, usage_limit FROM agent_services WHERE id = ?", (service_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    new_days_left = int(row["days_left"] or 0) + int(extra_days)
    new_usage_limit = float(row["usage_limit"] or 0) + float(extra_gb)
    # محاسبه end_date جدید
    end_date = str(row["end_date"] or "").strip()
    if end_date:
        try:
            current_end = datetime.strptime(end_date, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            current_end = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        current_end = datetime.now(timezone.utc).replace(tzinfo=None)
    new_end = (current_end + timedelta(days=int(extra_days))).strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        "UPDATE agent_services SET days_left = ?, usage_limit = ?, end_date = ?, updated_at = ? WHERE id = ?",
        (new_days_left, new_usage_limit, new_end, _now(), service_id),
    )
    conn.commit()
    conn.close()
    return True


def renew_service_with_policy(service_id: int, extra_days: int, extra_gb: float = 0,
                              volume_mode: str = "add", time_mode: str = "add",
                              payment_operation_key: str = "") -> bool:
    """
    تمدید سرویس با رعایت الگوی تعریف‌شده در ربات ادمین:
      volume_mode: "add" → حجم باقی‌مانده + پلن جدید | "reset" → فقط پلن جدید (ریست مصرف)
      time_mode:   "add" → روز باقی‌مانده + پلن جدید   | "reset" → فقط پلن جدید (شروع از امروز)
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    cur.execute(
        "SELECT days_left, end_date, usage_limit, usage_current, last_payment_operation_key "
        "FROM agent_services WHERE id = ?",
        (service_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.rollback()
        conn.close()
        return False

    operation_key = str(payment_operation_key or "").strip()
    if operation_key and str(row["last_payment_operation_key"] or "") == operation_key:
        conn.rollback()
        conn.close()
        return True

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # ── زمان ──
    if str(time_mode).strip().lower() == "add":
        new_days_left = int(row["days_left"] or 0) + int(extra_days)
        end_date = str(row["end_date"] or "").strip()
        if end_date:
            try:
                current_end = datetime.strptime(end_date, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                current_end = now
        else:
            current_end = now
        new_end = current_end + timedelta(days=int(extra_days))
    else:
        new_days_left = int(extra_days)
        new_end = now + timedelta(days=int(extra_days))

    # ── حجم ──
    if str(volume_mode).strip().lower() == "add":
        new_usage_limit = float(row["usage_limit"] or 0) + float(extra_gb)
    else:
        new_usage_limit = float(extra_gb)

    # ── در حالت ریست حجم، مصرف فعلی هم صفر می‌شود ──
    new_end_str = new_end.strftime("%Y-%m-%d %H:%M:%S")
    if str(volume_mode).strip().lower() == "add":
        cur.execute(
            "UPDATE agent_services SET days_left = ?, usage_limit = ?, end_date = ?, "
            "last_payment_operation_key = ?, updated_at = ? WHERE id = ?",
            (new_days_left, new_usage_limit, new_end_str, operation_key, _now(), service_id),
        )
    else:
        cur.execute(
            "UPDATE agent_services SET days_left = ?, usage_limit = ?, usage_current = 0, "
            "start_date = ?, end_date = ?, last_payment_operation_key = ?, updated_at = ? WHERE id = ?",
            (new_days_left, new_usage_limit, now.strftime("%Y-%m-%d %H:%M:%S"),
             new_end_str, operation_key, _now(), service_id),
        )
    conn.commit()
    conn.close()
    return True


def set_service_active(service_id: int, is_active: bool) -> bool:
    """فعال/غیرفعال کردن سرویس."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM agent_services WHERE id = ?", (service_id,))
    if not cur.fetchone():
        conn.close()
        return False
    cur.execute(
        "UPDATE agent_services SET is_active = ?, updated_at = ? WHERE id = ?",
        (1 if is_active else 0, _now(), service_id),
    )
    conn.commit()
    conn.close()
    return True


def delete_service(service_id: int) -> bool:
    """حذف سرویس و نودهای مرتبط."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM agent_services WHERE id = ?", (service_id,))
    if not cur.fetchone():
        conn.close()
        return False
    cur.execute("DELETE FROM agent_service_nodes WHERE service_id = ?", (service_id,))
    cur.execute("DELETE FROM agent_service_probe WHERE service_id = ?", (service_id,))
    cur.execute("DELETE FROM agent_services WHERE id = ?", (service_id,))
    conn.commit()
    conn.close()
    return True


def _is_soft_deleted(row: Dict[str, Any]) -> bool:
    """" آیا سرویس به‌صورت نرم (توسط ادمین) حذف شده یا نه؟ """
    val = str(row.get("deleted_at") or "").strip()
    return bool(val)


def soft_delete_service_by_uuid(panel_user_uuid: str, server_id: Optional[int] = None) -> int:
    """سرویس‌های مرتبط با یک کاربر پنل را به‌صورت نرم حذف می‌کند (وقتی ادمین حذف می‌کند).
    برمی‌گرداند: تعداد سرویس‌های علامت‌گذاری‌شده."""
    init_db()
    uuid = str(panel_user_uuid or "").strip()
    if not uuid:
        return 0
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    count = 0
    try:
        if server_id:
            cur.execute(
                "SELECT id FROM agent_services WHERE panel_user_uuid = ? AND server_id = ? "
                "AND (deleted_at = '' OR deleted_at IS NULL)",
                (uuid, int(server_id)),
            )
        else:
            cur.execute(
                "SELECT id FROM agent_services WHERE panel_user_uuid = ? "
                "AND (deleted_at = '' OR deleted_at IS NULL)",
                (uuid,),
            )
        rows = cur.fetchall()
        for r in rows:
            cur.execute("UPDATE agent_services SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, int(r["id"])))
            count += 1
        conn.commit()
    except Exception:
        pass
    conn.close()
    return count


def purge_expired_soft_deleted(days: int = 7) -> int:
    """حذف قطعی سرویس‌هایی که بیشتر از `days` روز پیش به‌صورت نرم حذف شده‌اند."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    count = 0
    try:
        cur.execute(
            "SELECT id, deleted_at FROM agent_services WHERE deleted_at IS NOT NULL AND deleted_at != ''"
        )
        rows = cur.fetchall()
        for r in rows:
            try:
                deleted_dt = datetime.strptime(str(r["deleted_at"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if (datetime.now(timezone.utc) - deleted_dt).total_seconds() >= days * 86400:
                cur.execute("DELETE FROM agent_service_nodes WHERE service_id = ?", (int(r["id"]),))
                cur.execute("DELETE FROM agent_services WHERE id = ?", (int(r["id"]),))
                count += 1
        conn.commit()
    except Exception:
        pass
    conn.close()
    return count


# ===============================
#   نگاشت سرویس به نودها (agent_service_nodes)
# ===============================

def add_service_node(
    service_id: int,
    server_id: int,
    server_title: str = "",
    panel_user_uuid: str = "",
    panel_user_id: str = "",
    marzban_username: str = "",
) -> Dict[str, Any]:
    """ثبت نگاشت سرویس به یک نود."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    try:
        cur.execute(
            """
            INSERT INTO agent_service_nodes (service_id, server_id, server_title, panel_user_uuid, panel_user_id, marzban_username, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (service_id, server_id, server_title, panel_user_uuid, panel_user_id, marzban_username or "", now, now),
        )
        conn.commit()
        node_id = cur.lastrowid
        cur.execute("SELECT * FROM agent_service_nodes WHERE id = ?", (node_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else {}
    except sqlite3.IntegrityError:
        # تکراری — بروزرسانی
        update_fields = "is_active = 1, panel_user_id = ?, updated_at = ?"
        update_params = [panel_user_id, now]
        if marzban_username:
            update_fields += ", marzban_username = ?"
            update_params.append(marzban_username)
        update_params.extend([service_id, server_id, panel_user_uuid])
        cur.execute(
            f"""
            UPDATE agent_service_nodes SET {update_fields}
            WHERE service_id = ? AND server_id = ? AND panel_user_uuid = ?
            """,
            update_params,
        )
        conn.commit()
        conn.close()
        return {"service_id": service_id, "server_id": server_id, "panel_user_uuid": panel_user_uuid, "is_active": 1}


def get_service_nodes(service_id: int) -> List[Dict[str, Any]]:
    """لیست نودهای یک سرویس."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_service_nodes WHERE service_id = ?", (service_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_service_node_active(service_id: int, server_id: int, is_active: bool) -> None:
    """فعال/غیرفعال کردن نود یک سرویس."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE agent_service_nodes SET is_active = ?, updated_at = ? "
        "WHERE service_id = ? AND server_id = ?",
        (1 if is_active else 0, _now(), service_id, server_id),
    )
    conn.commit()
    conn.close()


def set_service_nodes_active(service_id: int, is_active: bool) -> None:
    """فعال/غیرفعال کردن همه نودهای یک سرویس."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE agent_service_nodes SET is_active = ?, updated_at = ? WHERE service_id = ?",
        (1 if is_active else 0, _now(), service_id),
    )
    conn.commit()
    conn.close()


def get_agent_services_for_reminder(agent_id: int) -> List[Dict[str, Any]]:
    """سرویس‌های فعال یک نماینده به همراه telegram_id مشتری، برای یادآوری تمدید."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT s.*, c.telegram_id, c.username, c.full_name
            FROM agent_services s
            JOIN agent_customers c ON c.id = s.customer_id
            WHERE s.agent_id = ?
              AND c.telegram_id IS NOT NULL
              AND (s.is_active = 1
                   OR (s.days_left IS NOT NULL AND s.days_left <= 0)
                   OR (s.usage_limit IS NOT NULL
                       AND s.usage_limit > 0
                       AND s.usage_current IS NOT NULL
                       AND s.usage_current >= s.usage_limit))
              AND (s.deleted_at IS NULL OR s.deleted_at = '')
              AND EXISTS (
                SELECT 1 FROM agent_service_nodes n
                WHERE n.service_id = s.id
                  AND (COALESCE(n.is_active, 1) = 1
                       OR (s.days_left IS NOT NULL AND s.days_left <= 0)
                       OR (s.usage_limit IS NOT NULL
                           AND s.usage_limit > 0
                           AND s.usage_current IS NOT NULL
                           AND s.usage_current >= s.usage_limit))
              )
            ORDER BY s.id DESC
            """,
            (agent_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_service_reminder_state(service_id: int) -> Dict[str, Any]:
    """وضعیت یادآوری تمدید یک سرویس."""
    init_db()
    sid = int(service_id or 0)
    if sid <= 0:
        return {"days_sent": -1, "usage_sent": -1, "expired_sent": 0}
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT days_sent, usage_sent, expired_sent FROM agent_service_reminder_state WHERE service_id = ? LIMIT 1",
            (sid,),
        )
        row = cur.fetchone()
        if not row:
            return {"days_sent": -1, "usage_sent": -1, "expired_sent": 0}
        return {
            "days_sent": int(row["days_sent"]) if row["days_sent"] is not None else -1,
            "usage_sent": int(row["usage_sent"]) if row["usage_sent"] is not None else -1,
            "expired_sent": int(row["expired_sent"]) if row["expired_sent"] is not None else 0,
        }
    finally:
        conn.close()


def set_service_reminder_state(
    service_id: int,
    *,
    days_sent: Optional[int] = None,
    usage_sent: Optional[int] = None,
    expired_sent: Optional[int] = None,
) -> None:
    """ثبت وضعیت یادآوری تمدید یک سرویس."""
    init_db()
    sid = int(service_id or 0)
    if sid <= 0:
        return
    current = get_service_reminder_state(sid)
    d_val = int(days_sent) if days_sent is not None else int(current.get("days_sent", -1))
    u_val = int(usage_sent) if usage_sent is not None else int(current.get("usage_sent", -1))
    e_val = int(expired_sent) if expired_sent is not None else int(current.get("expired_sent", 0))
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO agent_service_reminder_state (service_id, days_sent, usage_sent, expired_sent, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(service_id) DO UPDATE SET
                days_sent = excluded.days_sent,
                usage_sent = excluded.usage_sent,
                expired_sent = excluded.expired_sent,
                updated_at = excluded.updated_at
            """,
            (sid, d_val, u_val, e_val, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def update_service_node_uuid(service_id: int, server_id: int, old_uuid: str, new_uuid: str) -> bool:
    """بروزرسانی UUID یک نود سرویس."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE agent_service_nodes SET panel_user_uuid = ?, updated_at = ? WHERE service_id = ? AND server_id = ? AND panel_user_uuid = ?",
        (new_uuid, _now(), service_id, server_id, old_uuid),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def delete_service_node(service_id: int, server_id: int, panel_user_uuid: str = "") -> bool:
    """حذف نگاشت نود."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    if panel_user_uuid:
        cur.execute(
            "DELETE FROM agent_service_nodes WHERE service_id = ? AND server_id = ? AND panel_user_uuid = ?",
            (service_id, server_id, panel_user_uuid),
        )
    else:
        cur.execute(
            "DELETE FROM agent_service_nodes WHERE service_id = ? AND server_id = ?",
            (service_id, server_id),
        )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# ===============================
#   قیمت‌گذاری (agent_plans)
# ===============================

def set_agent_plan(
    agent_id: int,
    server_id: int,
    days: int,
    gb: float,
    wholesale_price: int,
    sale_price: int = 0,
    plan_title: str = "",
    category_id: int = 0,
) -> Dict[str, Any]:
    """
    ثبت/بروزرسانی قیمت‌گذاری برای یک نماینده.
    ادمین wholesale_price تعیین می‌کنه.
    sale_price توسط نماینده تعیین میشه (0 = هنوز تعیین نکرده).
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()

    cur.execute(
        """
        SELECT id FROM agent_plans
        WHERE agent_id = ? AND server_id = ? AND days = ? AND gb = ?
        """,
        (agent_id, server_id, days, gb),
    )
    row = cur.fetchone()

    if row:
        plan_id = row["id"]
        cur.execute(
            """
            UPDATE agent_plans SET wholesale_price = ?, sale_price = ?, plan_title = ?,
                                   category_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (wholesale_price, sale_price, plan_title, category_id, now, plan_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO agent_plans (
                agent_id, server_id, days, gb, wholesale_price, sale_price,
                plan_title, category_id, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (agent_id, server_id, days, gb, wholesale_price, sale_price,
             plan_title, category_id, now, now),
        )
        plan_id = cur.lastrowid

    conn.commit()
    cur.execute("SELECT * FROM agent_plans WHERE id = ?", (plan_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def get_agent_plans(agent_id: int, server_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """لیست قیمت‌گذاری‌های نماینده (اختیاری: فیلتر بر اساس سرور)."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    if server_id is not None:
        cur.execute(
            "SELECT * FROM agent_plans WHERE agent_id = ? AND server_id = ? AND is_active = 1 ORDER BY days, gb",
            (agent_id, server_id),
        )
    else:
        cur.execute(
            "SELECT * FROM agent_plans WHERE agent_id = ? AND is_active = 1 ORDER BY server_id, days, gb",
            (agent_id,),
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_agent_plan_by_spec(agent_id: int, server_id: int, days: int, gb: float) -> Optional[Dict[str, Any]]:
    """یافتن قیمت عمده دقیق قبلی برای سازگاری با مدل قدیمی."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM agent_plans
        WHERE agent_id = ? AND server_id = ? AND days = ? AND ABS(gb - ?) < 0.0001 AND is_active = 1
        LIMIT 1
        """,
        (agent_id, server_id, days, float(gb or 0)),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_agent_plan_by_id(plan_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_plans WHERE id = ?", (plan_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_agent_plan_sale_price(plan_id: int, sale_price: int) -> bool:
    """نماینده قیمت فروش رو تنظیم می‌کنه."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE agent_plans SET sale_price = ?, updated_at = ? WHERE id = ?",
        (sale_price, _now(), plan_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_agent_plan(plan_id: int) -> bool:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM agent_plans WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_all_agent_plans(server_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """لیست تمام قیمت‌گذاری‌ها (برای ادمین)."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    if server_id is not None:
        cur.execute(
            "SELECT ap.*, au.full_name AS agent_name FROM agent_plans ap JOIN agent_users au ON ap.agent_id = au.id WHERE ap.server_id = ? ORDER BY ap.agent_id, ap.days",
            (server_id,),
        )
    else:
        cur.execute(
            "SELECT ap.*, au.full_name AS agent_name FROM agent_plans ap JOIN agent_users au ON ap.agent_id = au.id ORDER BY ap.agent_id, ap.days",
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ===============================
#   کدهای تخفیف (agent_discount_codes)
# ===============================

def generate_discount_code(agent_id: int) -> str:
    """تولید کد تخفیف تصادفی ۸ رقمی."""
    for _ in range(50):
        code = f"AG{random.randint(1000000, 9999999)}"
        if not _discount_code_exists(code):
            return code
    return f"AG{random.randint(1000000, 9999999)}"


def _discount_code_exists(code: str) -> bool:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM agent_discount_codes WHERE code = ? LIMIT 1", (code,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def add_discount_code(
    agent_id: int,
    percent: int,
    max_uses: int = 1,
    expires_at: str = "",
    code: str = "",
) -> Dict[str, Any]:
    """
    ساخت کد تخفیف. اگر code خالی باشه، خودکار تولید میشه.
    """
    if not code:
        code = generate_discount_code(agent_id)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = _now()
    try:
        cur.execute(
            """
            INSERT INTO agent_discount_codes (agent_id, code, percent, max_uses, is_active, expires_at, created_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (agent_id, code, percent, max_uses, expires_at, now),
        )
        conn.commit()
        dc_id = cur.lastrowid
        cur.execute("SELECT * FROM agent_discount_codes WHERE id = ?", (dc_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else {}
    except sqlite3.IntegrityError:
        conn.close()
        return add_discount_code(agent_id, percent, max_uses, expires_at, "")


def get_discount_codes(agent_id: int) -> List[Dict[str, Any]]:
    """لیست کدهای تخفیف یک نماینده."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM agent_discount_codes WHERE agent_id = ? ORDER BY id DESC",
        (agent_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_discount_code(code: str) -> Optional[Dict[str, Any]]:
    """گرفتن اطلاعات کد تخفیف با کد."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_discount_codes WHERE code = ?", (code,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def validate_discount_code(code: str) -> Optional[Dict[str, Any]]:
    """
    اعتبارسنجی کد تخفیف: فعال باشه، منقضی نشده، استفاده نشده.
    """
    dc = get_discount_code(code)
    if not dc:
        return None
    if not int(dc.get("is_active", 0)):
        return None
    if int(dc.get("used_count", 0)) >= int(dc.get("max_uses", 1)):
        return None
    expires = str(dc.get("expires_at", "") or "").strip()
    if expires:
        try:
            exp_dt = datetime.strptime(expires, "%Y-%m-%d %H:%M:%S")
            if datetime.now(timezone.utc).replace(tzinfo=None) > exp_dt:
                return None
        except ValueError:
            pass
    return dc


def use_discount_code(code: str) -> bool:
    """یک بار استفاده از کد تخفیف."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE agent_discount_codes SET used_count = used_count + 1 WHERE code = ?",
        (code,),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def set_discount_code_active(code_id: int, is_active: bool) -> bool:
    """فعال/غیرفعال کردن کد تخفیف."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE agent_discount_codes SET is_active = ? WHERE id = ?",
        (1 if is_active else 0, code_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_discount_code(code_id: int) -> bool:
    """حذف کد تخفیف."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM agent_discount_codes WHERE id = ?", (code_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ===============================
#   تنظیمات نماینده (agent_settings)
# ===============================

def get_agent_setting(agent_id: int, key: str, default: Any = None) -> Any:
    """دریافت یک تنظیم نماینده."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT value FROM agent_settings WHERE agent_id = ? AND key = ?",
        (agent_id, key),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return default
    val = row["value"]
    if val is None or str(val).strip() == "":
        return default
    # تلاش برای parse JSON
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def set_agent_setting(agent_id: int, key: str, value: Any) -> None:
    """تنظیم یک مقدار برای نماینده."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    payload = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    cur.execute(
        """
        INSERT INTO agent_settings (agent_id, key, value) VALUES (?, ?, ?)
        ON CONFLICT(agent_id, key) DO UPDATE SET value = excluded.value
        """,
        (agent_id, key, payload),
    )
    conn.commit()
    conn.close()


def get_agent_settings(agent_id: int) -> Dict[str, Any]:
    """دریافت تمام تنظیمات یک نماینده."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM agent_settings WHERE agent_id = ?", (agent_id,))
    rows = cur.fetchall()
    conn.close()
    result: Dict[str, Any] = {}
    for row in rows:
        key = row["key"]
        val = row["value"]
        try:
            result[key] = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            result[key] = val
    return result


def get_wholesale_pricing(agent_id: int) -> Dict[str, int]:
    """تعرفه عمده نماینده: قیمت هر گیگ و هر ۳۰ روز."""
    try:
        per_gb = int(get_agent_setting(agent_id, "wholesale_price_per_gb", 0) or 0)
    except (TypeError, ValueError):
        per_gb = 0
    try:
        per_30_days = int(get_agent_setting(agent_id, "wholesale_price_per_30_days", 0) or 0)
    except (TypeError, ValueError):
        per_30_days = 0
    return {
        "price_per_gb": max(0, per_gb),
        "price_per_30_days": max(0, per_30_days),
    }


def set_wholesale_pricing(agent_id: int, price_per_gb: int, price_per_30_days: int) -> Dict[str, int]:
    """ثبت تعرفه عمده نماینده بدون تاریخ انقضا برای کیف پول."""
    price_per_gb = max(0, int(price_per_gb or 0))
    price_per_30_days = max(0, int(price_per_30_days or 0))
    set_agent_setting(agent_id, "wholesale_price_per_gb", price_per_gb)
    set_agent_setting(agent_id, "wholesale_price_per_30_days", price_per_30_days)
    return get_wholesale_pricing(agent_id)


def billable_months(days: int) -> int:
    """۱ تا ۳۰ روز یک ماه، ۳۱ تا ۶۰ دو ماه و ..."""
    try:
        days_int = int(days or 0)
    except (TypeError, ValueError):
        days_int = 0
    if days_int <= 0:
        return 0
    return max(1, int(math.ceil(days_int / 30)))


def calculate_wholesale_price(agent_id: int, gb: float, days: int, server_id: int = 0) -> int:
    """محاسبه هزینه عمده: حجم + ماه‌های قابل صورتحساب.

    اگر تعرفه جدید تنظیم نشده باشد، برای سازگاری از قیمت عمده پلن دقیق قدیمی استفاده می‌شود.
    """
    pricing = get_wholesale_pricing(agent_id)
    per_gb = int(pricing.get("price_per_gb", 0) or 0)
    per_30_days = int(pricing.get("price_per_30_days", 0) or 0)
    if per_gb > 0 or per_30_days > 0:
        try:
            volume_cost = int(round(float(gb or 0) * per_gb))
        except (TypeError, ValueError):
            volume_cost = 0
        return max(0, volume_cost + billable_months(days) * per_30_days)

    legacy_plan = get_agent_plan_by_spec(agent_id, int(server_id or 0), int(days or 0), float(gb or 0))
    if legacy_plan:
        try:
            return max(0, int(legacy_plan.get("wholesale_price") or 0))
        except (TypeError, ValueError):
            return 0
    return 0


# ===============================
#   آمار و گزارش‌گیری
# ===============================

def get_agent_stats(agent_id: int) -> Dict[str, Any]:
    """
    آمار کلی یک نماینده: تعداد مشتری، سرویس فعال، فروش، موجودی کیف پول.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()

    # تعداد مشتریان
    cur.execute("SELECT COUNT(*) AS c FROM agent_customers WHERE agent_id = ?", (agent_id,))
    customers_count = int(cur.fetchone()["c"] or 0)

    # تعداد سرویس‌ها و فعال‌ها
    cur.execute("SELECT COUNT(*) AS c FROM agent_services WHERE agent_id = ? AND (deleted_at IS NULL OR deleted_at = '')", (agent_id,))
    services_total = int(cur.fetchone()["c"] or 0)
    cur.execute("SELECT COUNT(*) AS c FROM agent_services WHERE agent_id = ? AND is_active = 1 AND (deleted_at IS NULL OR deleted_at = '')", (agent_id,))
    services_active = int(cur.fetchone()["c"] or 0)

    # تعداد ترایال‌ها
    cur.execute("SELECT COUNT(*) AS c FROM agent_services WHERE agent_id = ? AND is_trial = 1 AND (deleted_at IS NULL OR deleted_at = '')", (agent_id,))
    trials_count = int(cur.fetchone()["c"] or 0)

    # مجموع فروش (sale_price)
    cur.execute(
        "SELECT COALESCE(SUM(sale_price), 0) AS total FROM agent_services WHERE agent_id = ? AND (deleted_at IS NULL OR deleted_at = '')",
        (agent_id,),
    )
    total_sales = int(cur.fetchone()["total"] or 0)

    # مجموع هزینه عمده (wholesale_price)
    cur.execute(
        "SELECT COALESCE(SUM(wholesale_price), 0) AS total FROM agent_services WHERE agent_id = ? AND (deleted_at IS NULL OR deleted_at = '')",
        (agent_id,),
    )
    total_wholesale = int(cur.fetchone()["total"] or 0)

    # اتصال اول قبل از فراخوانی get_wallet بسته شود — get_wallet خودش
    # connection جدا باز می‌کند و می‌نویسد (INSERT OR IGNORE)؛ باز ماندن
    # اتصال خواندن اینجا باعث SQLITE_BUSY خود-قفل‌شدگی می‌شد
    conn.close()
    conn = None

    wallet = get_wallet(agent_id)
    wallet_balance = int(wallet.get("balance", 0))

    return {
        "agent_id": agent_id,
        "customers_count": customers_count,
        "services_total": services_total,
        "services_active": services_active,
        "trials_count": trials_count,
        "total_sales": total_sales,
        "total_wholesale": total_wholesale,
        "total_profit": total_sales - total_wholesale,
        "wallet_balance": wallet_balance,
    }


def get_global_agency_stats() -> Dict[str, Any]:
    """آمار کلی سیستم نمایندگی (برای ادمین)."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()

    # تعداد نماینده‌ها
    cur.execute("SELECT COUNT(*) AS c FROM agent_users")
    agents_total = int(cur.fetchone()["c"] or 0)
    cur.execute("SELECT COUNT(*) AS c FROM agent_users WHERE is_active = 1")
    agents_active = int(cur.fetchone()["c"] or 0)

    # تعداد کل مشتریان
    cur.execute("SELECT COUNT(*) AS c FROM agent_customers")
    customers_total = int(cur.fetchone()["c"] or 0)

    # تعداد کل سرویس‌ها
    cur.execute("SELECT COUNT(*) AS c FROM agent_services WHERE deleted_at IS NULL OR deleted_at = ''")
    services_total = int(cur.fetchone()["c"] or 0)
    cur.execute("SELECT COUNT(*) AS c FROM agent_services WHERE is_active = 1 AND (deleted_at IS NULL OR deleted_at = '')")
    services_active = int(cur.fetchone()["c"] or 0)

    # مجموع فروش کل سیستم
    cur.execute("SELECT COALESCE(SUM(sale_price), 0) AS total FROM agent_services")
    total_sales = int(cur.fetchone()["total"] or 0)
    cur.execute("SELECT COALESCE(SUM(wholesale_price), 0) AS total FROM agent_services")
    total_wholesale = int(cur.fetchone()["total"] or 0)

    # مجموع شارژ کیف پول‌ها
    cur.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM agent_transactions WHERE tx_type = 'charge'")
    total_charges = int(cur.fetchone()["total"] or 0)

    # تعداد ربات‌های مشتری فعال
    cur.execute("SELECT COUNT(*) AS c FROM agent_customer_bots WHERE is_active = 1")
    bots_active = int(cur.fetchone()["c"] or 0)

    conn.close()
    return {
        "agents_total": agents_total,
        "agents_active": agents_active,
        "customers_total": customers_total,
        "services_total": services_total,
        "services_active": services_active,
        "total_sales": total_sales,
        "total_wholesale": total_wholesale,
        "total_profit": total_sales - total_wholesale,
        "total_charges": total_charges,
        "bots_active": bots_active,
    }


# ---- Customer Bot Settings Sync ----

def sync_customer_bot_text_setting(agent_id: int, key: str, value: str) -> bool:
    """همگردانی یک متن تنظیمات به دیتابیس ربات مشتری."""
    try:
        if not _CUSTOMER_BOT_DB_PATH.exists():
            return False
        settings_bucket = "payment_settings" if key == "card_to_card_text" else "text_settings"
        conn = sqlite3.connect(str(_CUSTOMER_BOT_DB_PATH))
        conn.row_factory = sqlite3.Row
        ensure_private_file(_CUSTOMER_BOT_DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT value FROM customer_settings WHERE agent_id = ? AND key = ?",
            (agent_id, settings_bucket),
        )
        row = cur.fetchone()
        current = {}
        if row and row["value"]:
            try:
                current = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                current = {}
        current[key] = value
        payload = json.dumps(current, ensure_ascii=False)
        cur.execute(
            "INSERT INTO customer_settings (agent_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(agent_id, key) DO UPDATE SET value = excluded.value",
            (agent_id, settings_bucket, payload),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False
