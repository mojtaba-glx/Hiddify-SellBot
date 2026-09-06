from __future__ import annotations

import sqlite3
import json
import random
import re
import threading
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from Shared import i18n

DB_FILE_NAME = "customer_bot.db"
DB_PATH = Path(__file__).resolve().parent.parent / DB_FILE_NAME
_AGENCY_DB_PATH = Path(__file__).resolve().parent.parent / "Shared" / "agency.db"

_db_initialized = False
_init_db_path = ""
_init_db_lock = threading.RLock()

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

# FAQ is stored per language. Legacy installations may still contain a single
# string; the accessors below treat that value as the Persian entry.
DEFAULT_FAQ_TEXTS = {
    "fa": DEFAULT_TEXT_SETTINGS["faq_text"],
    "en": "❗️ FAQ\n\n1) Where can I find my subscription link?\nOpen Subscription status, select a service, then choose Subscription link.\n\n2) What should I do if the config does not connect?\nCheck your internet connection and phone date/time, then refresh the service.\n\n3) How do I renew?\nOpen Renew subscription, select a service, and purchase a renewal plan.\n\n4) How can I contact support?\nOpen Support and send your message.",
    "ru": "❗️ Частые вопросы\n\n1) Где найти ссылку подписки?\nОткройте Статус подписки, выберите сервис и нажмите Ссылка подписки.\n\n2) Что делать, если конфигурация не подключается?\nПроверьте интернет и дату/время телефона, затем обновите сервис.\n\n3) Как продлить подписку?\nОткройте Продление подписки, выберите сервис и купите тариф продления.\n\n4) Как связаться с поддержкой?\nОткройте Поддержку и отправьте сообщение.",
}

# Per-language DEFAULTS for agent-customizable texts.
# get_localized_text() returns the agent's custom text when set,
# otherwise the default in the customer's language (fa/en/ru).
DEFAULT_TEXT_I18N = {
    "welcome_message": {
        "en": "Hello {full_name} 👋\nWelcome to our bot.",
        "ru": "Привет, {full_name} 👋\nДобро пожаловать в наш бот.",
    },
    "guide_text": {
        "en": "Choose your OS ⬇️",
        "ru": "Выберите ОС ⬇️",
    },
    "guide_android_text": {
        "en": "📱 Android Guide\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) v2rayNG:\nhttps://github.com/2dust/v2rayNG/releases\n\n3) NekoBox for Android:\nhttps://github.com/MatsuriDayo/NekoBoxForAndroid/releases",
        "ru": "📱 Инструкция для Android\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) v2rayNG:\nhttps://github.com/2dust/v2rayNG/releases\n\n3) NekoBox for Android:\nhttps://github.com/MatsuriDayo/NekoBoxForAndroid/releases",
    },
    "guide_ios_text": {
        "en": "📱 iOS Guide\n\n1) Streisand:\nhttps://apps.apple.com/app/streisand/id6450534064\n\n2) Hiddify (iOS):\nhttps://apps.apple.com/app/hiddify-proxy-vpn/id6596777532",
        "ru": "📱 Инструкция для iOS\n\n1) Streisand:\nhttps://apps.apple.com/app/streisand/id6450534064\n\n2) Hiddify (iOS):\nhttps://apps.apple.com/app/hiddify-proxy-vpn/id6596777532",
    },
    "guide_windows_text": {
        "en": "🖥️ Windows Guide\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n3) v2rayN:\nhttps://github.com/2dust/v2rayN/releases",
        "ru": "🖥️ Инструкция для Windows\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n3) v2rayN:\nhttps://github.com/2dust/v2rayN/releases",
    },
    "guide_mac_text": {
        "en": "💻 macOS Guide\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases",
        "ru": "💻 Инструкция для macOS\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases",
    },
    "guide_linux_text": {
        "en": "🖥️ Linux Guide\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases",
        "ru": "🖥️ Инструкция для Linux\n\n1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases",
    },
    "servers_list_text": {
        "en": "📡 **Server list**\nPlease choose your desired location:",
        "ru": "📡 **Список серверов**\nВыберите нужную локацию:",
    },
    "plans_list_text": {
        "en": "🛒 **Please choose your desired plan:**",
        "ru": "🛒 **Выберите нужный тариф:**",
    },
    "ticket_panel_text": {
        "en": "📩 To contact support, send your message.",
        "ru": "📩 Для связи с поддержкой отправьте сообщение.",
    },
}

DEFAULT_FORCEJOIN_GUIDE_I18N = {
    "en": "🔒 To use the bot, first join the support channel.\nAfter joining, tap «✅ Check Membership».",
    "ru": "🔒 Чтобы пользоваться ботом, сначала подпишитесь на канал.\nЗатем нажмите «✅ Проверить».",
}


def get_localized_text(agent_id: int, name: str, lang: str = "fa") -> str:
    """Agent-customizable text in the customer's language.

    Returns the agent's custom text when they set one; otherwise the
    built-in default translated to ``lang`` (fa/en/ru). This keeps the
    ``or i18n.t(...)`` fallbacks in handlers meaningful: merged settings
    always contain the Persian default, which would otherwise shadow them.
    """
    lg = str(lang or "fa").strip().lower()
    if lg not in ("fa", "en", "ru"):
        lg = "fa"
    fa_default = str(DEFAULT_TEXT_SETTINGS.get(name, "") or "")
    try:
        stored = _get_setting(agent_id, "text_settings", {}) or {}
    except Exception:
        stored = {}
    if isinstance(stored, dict):
        custom = str(stored.get(name) or "")
        if custom.strip() and (lg == "fa" or custom.strip() != fa_default.strip()):
            return str(stored.get(name))
    if lg == "fa":
        return fa_default
    return str((DEFAULT_TEXT_I18N.get(name) or {}).get(lg) or fa_default)


def get_localized_forcejoin_guide(agent_id: int, lang: str = "fa") -> str:
    """Force-join guide text: agent custom text wins, else localized default."""
    lg = str(lang or "fa").strip().lower()
    if lg not in ("fa", "en", "ru"):
        lg = "fa"
    fa_default = str(DEFAULT_FORCE_JOIN_SETTINGS.get("guide_text", "") or "")
    try:
        stored = _get_setting(agent_id, "force_join_settings", {}) or {}
    except Exception:
        stored = {}
    if isinstance(stored, dict):
        custom = str(stored.get("guide_text") or "")
        if custom.strip() and (lg == "fa" or custom.strip() != fa_default.strip()):
            return str(stored.get("guide_text"))
    if lg == "fa":
        return fa_default
    return str(DEFAULT_FORCEJOIN_GUIDE_I18N.get(lg) or fa_default)

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
    conn = sqlite3.connect(str(DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn


def _ensure_column(cur: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise ValueError(f"invalid table name: {table!r}")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column):
        raise ValueError(f"invalid column name: {column!r}")
    columns = {str(row[1]) for row in cur.execute(f"PRAGMA table_info({table})")}
    if column in columns:
        return
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        # A second process may complete the same additive migration after the
        # PRAGMA check but before this connection obtains the schema lock.
        columns = {str(row[1]) for row in cur.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            raise


def init_db() -> None:
    global _db_initialized, _init_db_path
    current_path = str(DB_PATH)
    with _init_db_lock:
        # اگر مسیر دیتابیس تغییر کرده (مثلاً در تست‌ها)، دوباره جداول را می‌سازیم.
        if _db_initialized and current_path == _init_db_path:
            return
        _initialize_db()
        # Publish readiness only after every table, column and index committed.
        _db_initialized = True
        _init_db_path = current_path


def _initialize_db() -> None:
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
    _ensure_column(cur, "customer_users", "updated_at", "TEXT")
    _ensure_column(cur, "customer_users", "language", "TEXT DEFAULT 'fa'")

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
    _ensure_column(cur, "customer_orders", "renew_service_id", "INTEGER DEFAULT 0")

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
    for column, definition in (
        ("processing_token", "TEXT DEFAULT ''"),
        ("processing_started_at", "TEXT DEFAULT ''"),
        ("processing_previous_status", "TEXT DEFAULT ''"),
        ("processing_stage", "TEXT DEFAULT ''"),
        ("processing_note", "TEXT DEFAULT ''"),
    ):
        _ensure_column(cur, "customer_payments", column, definition)

    # صف تایید خودکار وب‌هوک SMS بانکی: وب‌هوک (پروسه UserBot) پرداخت تطبیق‌یافته را
    # اینجا صف می‌کند و پروسه AgentBot با رویداد لوپ خودش سرویس را می‌سازد و تحویل می‌دهد
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_payment_sms_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            pay_id INTEGER NOT NULL,
            event_id TEXT NOT NULL UNIQUE,
            amount_toman INTEGER DEFAULT 0,
            card_last4 TEXT DEFAULT '',
            created_at TEXT,
            processed INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            processed_at TEXT DEFAULT '',
            state TEXT DEFAULT 'pending',
            attempt_count INTEGER DEFAULT 0,
            next_attempt_at TEXT DEFAULT '',
            lease_token TEXT DEFAULT '',
            lease_expires_at TEXT DEFAULT '',
            last_attempt_at TEXT DEFAULT '',
            last_error TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT ''
        )
    """)
    for column, definition in (
        ("state", "TEXT DEFAULT 'pending'"),
        ("attempt_count", "INTEGER DEFAULT 0"),
        ("next_attempt_at", "TEXT DEFAULT ''"),
        ("lease_token", "TEXT DEFAULT ''"),
        ("lease_expires_at", "TEXT DEFAULT ''"),
        ("last_attempt_at", "TEXT DEFAULT ''"),
        ("last_error", "TEXT DEFAULT ''"),
        ("updated_at", "TEXT DEFAULT ''"),
        ("completed_at", "TEXT DEFAULT ''"),
    ):
        _ensure_column(cur, "customer_payment_sms_queue", column, definition)
    # One-way compatibility migration from the old processed flag.
    cur.execute(
        "UPDATE customer_payment_sms_queue SET state='succeeded', "
        "completed_at=CASE WHEN completed_at='' THEN processed_at ELSE completed_at END "
        "WHERE processed=1 AND COALESCE(state, '') IN ('', 'pending')"
    )
    cur.execute(
        "UPDATE customer_payment_sms_queue SET state='pending' "
        "WHERE processed=0 AND COALESCE(state, '')=''"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_cust_sms_queue_ready "
        "ON customer_payment_sms_queue(processed, state, next_attempt_at, id)"
    )

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


def set_customer_language(agent_id: int, telegram_id: int, lang: str) -> bool:
    """ذخیره زبان رابط کاربری مشتری نماینده."""
    lg = str(lang or "fa").strip().lower()
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE customer_users SET language = ? WHERE agent_id = ? AND telegram_id = ?",
            (lg, int(agent_id or 0), int(telegram_id or 0)),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


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


def get_full_customer_stats(agent_id: int, telegram_id: int) -> Dict[str, Any]:
    """آمار کامل مشتری برای پروفایل نماینده (کیف پول، سرویس‌ها، تراکنش‌ها، سفارشات، تیکت‌ها)."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM customer_users WHERE agent_id = ? AND telegram_id = ?",
        (agent_id, telegram_id),
    )
    row = cur.fetchone()
    conn.close()

    user = dict(row) if row else {}

    # سرویس‌های مشتری (از دیتابیس نماینده)
    services = []
    services_total = 0
    services_active = 0
    services_trial = 0
    try:
        from Shared import agent_db
        cust = agent_db.get_customer_by_telegram_id(agent_id, telegram_id)
        if cust:
            services = agent_db.get_services_by_customer(cust["id"])
    except Exception:
        services = []
    services_total = len(services)
    services_active = sum(1 for s in services if int(s.get("is_active", 0) or 0) == 1)
    services_trial = sum(1 for s in services if int(s.get("is_trial", 0) or 0) == 1)

    # تراکنش‌ها (وضعیت تایید/رد/در انتظار)
    tx_total = 0
    tx_approved = 0
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM customer_payments WHERE agent_id = ? AND user_id = ?",
            (agent_id, telegram_id),
        )
        row = cur.fetchone()
        tx_total = int(row["c"] or 0) if row else 0
        cur.execute(
            "SELECT COUNT(*) AS c FROM customer_payments WHERE agent_id = ? AND status = 'approved' AND user_id = ?",
            (agent_id, telegram_id),
        )
        row = cur.fetchone()
        tx_approved = int(row["c"] or 0) if row else 0
        conn.close()
    except Exception:
        tx_total = 0
        tx_approved = 0

    # سفارشات (تعداد، حجم، مبلغ)
    orders_count = 0
    orders_gb = 0.0
    orders_price = 0
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(volume_gb), 0) AS gb, COALESCE(SUM(price), 0) AS price FROM customer_orders WHERE agent_id = ? AND telegram_id = ?",
            (agent_id, telegram_id),
        )
        row = cur.fetchone()
        if row:
            orders_count = int(row["c"] or 0)
            orders_gb = float(row["gb"] or 0)
            orders_price = int(row["price"] or 0)
        conn.close()
    except Exception:
        orders_count = 0
        orders_gb = 0.0
        orders_price = 0

    # تیکت‌ها
    tickets_count = 0
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM customer_tickets WHERE agent_id = ? AND telegram_id = ?",
            (agent_id, telegram_id),
        )
        row = cur.fetchone()
        tickets_count = int(row["c"] or 0) if row else 0
        conn.close()
    except Exception:
        tickets_count = 0

    return {
        "user": user,
        "wallet_balance": int(user.get("wallet_balance") or 0),
        "is_banned": int(user.get("is_banned") or 0),
        "got_free_trial": int(user.get("got_free_trial") or 0),
        "services_total": services_total,
        "services_active": services_active,
        "services_trial": services_trial,
        "tx_total": tx_total,
        "tx_approved": tx_approved,
        "orders_count": orders_count,
        "orders_gb": orders_gb,
        "orders_price": orders_price,
        "tickets_count": tickets_count,
    }


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
                 server_id: int = 0, plan_id: int = 0, wholesale_price: int = 0,
                 renew_service_id: int = 0) -> Dict[str, Any]:
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
        "server_id=?, plan_id=?, wholesale_price=?, renew_service_id=?, status='pending' "
        "WHERE agent_id=? AND order_id=?",
        (user_id, telegram_id, username, full_name, now,
         volume_gb, days, price, plan_title, server_location,
         int(server_id or 0), int(plan_id or 0), int(wholesale_price or 0),
         int(renew_service_id or 0),
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


def get_user_orders_stats(agent_id: int, telegram_id: int) -> Dict[str, Any]:
    """آمار سفارشات یک مشتری (کل، ۳۰ روز گذشته، ماه جاری)."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()

    def _q(extra: str = ""):
        where = "agent_id = ? AND telegram_id = ?" + extra
        cur.execute(
            f"SELECT COUNT(*) AS c, COALESCE(SUM(volume_gb), 0) AS gb, COALESCE(SUM(price), 0) AS price "
            f"FROM customer_orders WHERE {where}",
            (agent_id, telegram_id),
        )
        return cur.fetchone()

    total_row = _q()
    last30_row = _q(" AND date(created_at) >= date('now', '-30 days')")
    month_row = _q(" AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')")
    conn.close()

    return {
        "total_count": int(total_row["c"] or 0),
        "total_gb": float(total_row["gb"] or 0),
        "total_price": int(total_row["price"] or 0),
        "last30_count": int(last30_row["c"] or 0),
        "last30_gb": float(last30_row["gb"] or 0),
        "last30_price": int(last30_row["price"] or 0),
        "month_count": int(month_row["c"] or 0),
        "month_gb": float(month_row["gb"] or 0),
        "month_price": int(month_row["price"] or 0),
    }


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


def update_payment_status(
    agent_id: int,
    payment_id: int,
    status: str,
    receipt_image: str = "",
    *,
    expected_status: Optional[str] = None,
) -> bool:
    """Update a mutable payment using an optional compare-and-swap guard.

    Customer-side receipt handling must never modify a payment after an
    approval worker has claimed it. Therefore approved and processing rows
    are protected even when a legacy caller omits expected_status.
    """
    target_status = str(status or "").strip().lower()
    if not target_status:
        return False

    init_db()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        now = _now()
        where = (
            "agent_id = ? AND id = ? "
            "AND lower(trim(COALESCE(status, ''))) NOT IN ('approved', 'processing')"
        )
        where_params: list[Any] = [agent_id, payment_id]
        if expected_status is not None:
            expected = str(expected_status or "").strip().lower()
            where += " AND lower(trim(COALESCE(status, ''))) = ?"
            where_params.append(expected)

        if receipt_image:
            cur.execute(
                f"UPDATE customer_payments SET status = ?, receipt_image = ?, updated_at = ? WHERE {where}",
                [target_status, receipt_image, now, *where_params],
            )
        else:
            cur.execute(
                f"UPDATE customer_payments SET status = ?, updated_at = ? WHERE {where}",
                [target_status, now, *where_params],
            )
        affected = cur.rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


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


# ---- SMS webhook auto-approval queue ----

def enqueue_sms_auto_approval(agent_id: int, pay_id: int, event_id: str, amount_toman: int = 0, card_last4: str = "") -> bool:
    """Reserve one pending payment for one SMS event, atomically.

    The ownership check and the active-job check intentionally live in the
    same BEGIN IMMEDIATE transaction as the insert.  This prevents two webhook
    processes from reserving one customer payment at the same time.
    """
    init_db()
    aid = int(agent_id or 0)
    pid = int(pay_id or 0)
    eid = str(event_id or "").strip()
    if aid <= 0 or pid <= 0 or not eid:
        return False
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        pay = conn.execute(
            "SELECT status, method FROM customer_payments WHERE agent_id=? AND id=? LIMIT 1",
            (aid, pid),
        ).fetchone()
        if not pay or str(pay["status"] or "").strip().lower() != "pending":
            conn.rollback()
            return False
        if str(pay["method"] or "").strip().lower() not in {"card", "card_to_card"}:
            conn.rollback()
            return False
        active = conn.execute(
            "SELECT 1 FROM customer_payment_sms_queue "
            "WHERE agent_id=? AND pay_id=? AND processed=0 "
            "AND state IN ('pending', 'retry', 'waiting_wallet', 'processing') LIMIT 1",
            (aid, pid),
        ).fetchone()
        if active:
            conn.rollback()
            return False
        now = _now()
        cur = conn.execute(
            "INSERT OR IGNORE INTO customer_payment_sms_queue "
            "(agent_id, pay_id, event_id, amount_toman, card_last4, created_at, "
            "state, next_attempt_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (aid, pid, eid, int(amount_toman or 0), str(card_last4 or ""), now, now, now),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_pending_sms_auto_queue(limit: int = 10) -> List[Dict[str, Any]]:
    """Compatibility/read-only view. Workers must use claim_sms_auto_queue."""
    init_db()
    conn = _get_conn()
    try:
        now = _now()
        cur = conn.execute(
            "SELECT * FROM customer_payment_sms_queue WHERE processed=0 "
            "AND state IN ('pending', 'retry', 'waiting_wallet') "
            "AND (COALESCE(next_attempt_at, '')='' OR next_attempt_at<=?) "
            "ORDER BY id LIMIT ?",
            (now, max(1, min(50, int(limit or 10)))),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def claim_sms_auto_queue(limit: int = 10, lease_seconds: int = 120) -> List[Dict[str, Any]]:
    """Atomically lease ready jobs. Each returned row has its own lease token."""
    init_db()
    take = max(1, min(50, int(limit or 10)))
    lease_for = max(30, min(900, int(lease_seconds or 120)))
    now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    expires = (now_dt + timedelta(seconds=lease_for)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE customer_payment_sms_queue SET state='retry', lease_token='', "
            "lease_expires_at='', next_attempt_at=?, updated_at=?, "
            "last_error=CASE WHEN last_error='' THEN 'worker lease expired' ELSE last_error END "
            "WHERE processed=0 AND state='processing' AND lease_expires_at!='' AND lease_expires_at<=?",
            (now, now, now),
        )
        candidates = conn.execute(
            "SELECT id FROM customer_payment_sms_queue WHERE processed=0 "
            "AND state IN ('pending', 'retry', 'waiting_wallet') "
            "AND (COALESCE(next_attempt_at, '')='' OR next_attempt_at<=?) "
            "ORDER BY id LIMIT ?",
            (now, take),
        ).fetchall()
        claimed: List[Dict[str, Any]] = []
        for candidate in candidates:
            qid = int(candidate["id"])
            token = uuid.uuid4().hex
            cur = conn.execute(
                "UPDATE customer_payment_sms_queue SET state='processing', lease_token=?, "
                "lease_expires_at=?, last_attempt_at=?, updated_at=?, attempt_count=attempt_count+1 "
                "WHERE id=? AND processed=0 AND state IN ('pending', 'retry', 'waiting_wallet') "
                "AND (COALESCE(next_attempt_at, '')='' OR next_attempt_at<=?)",
                (token, expires, now, now, qid, now),
            )
            if cur.rowcount == 1:
                row = conn.execute(
                    "SELECT * FROM customer_payment_sms_queue WHERE id=?",
                    (qid,),
                ).fetchone()
                if row:
                    claimed.append(dict(row))
        conn.commit()
        return claimed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_sms_auto_queue(queue_id: int, lease_token: str, note: str = "", *, state: str = "succeeded") -> bool:
    terminal_state = str(state or "succeeded").strip().lower()
    if terminal_state not in {"succeeded", "obsolete"}:
        return False
    token = str(lease_token or "").strip()
    if not token:
        return False
    init_db()
    conn = _get_conn()
    try:
        now = _now()
        cur = conn.execute(
            "UPDATE customer_payment_sms_queue SET processed=1, state=?, note=?, "
            "processed_at=?, completed_at=?, updated_at=?, lease_token='', lease_expires_at='' "
            "WHERE id=? AND processed=0 AND state='processing' AND lease_token=?",
            (
                terminal_state,
                str(note or "")[:500],
                now,
                now,
                now,
                int(queue_id or 0),
                token,
            ),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def retry_sms_auto_queue(
    queue_id: int,
    lease_token: str,
    error: str,
    delay_seconds: int,
    *,
    state: str = "retry",
) -> bool:
    retry_state = str(state or "retry").strip().lower()
    if retry_state not in {"retry", "waiting_wallet"}:
        return False
    token = str(lease_token or "").strip()
    if not token:
        return False
    init_db()
    now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    next_at = (now_dt + timedelta(seconds=max(1, int(delay_seconds or 1)))).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE customer_payment_sms_queue SET state=?, note=?, last_error=?, "
            "next_attempt_at=?, updated_at=?, lease_token='', lease_expires_at='' "
            "WHERE id=? AND processed=0 AND state='processing' AND lease_token=?",
            (
                retry_state,
                str(error or "")[:500],
                str(error or "")[:500],
                next_at,
                now,
                int(queue_id or 0),
                token,
            ),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def dead_letter_sms_auto_queue(queue_id: int, lease_token: str, error: str) -> bool:
    token = str(lease_token or "").strip()
    if not token:
        return False
    init_db()
    conn = _get_conn()
    try:
        now = _now()
        cur = conn.execute(
            "UPDATE customer_payment_sms_queue SET processed=1, state='dead', note=?, "
            "last_error=?, processed_at=?, completed_at=?, updated_at=?, "
            "lease_token='', lease_expires_at='' "
            "WHERE id=? AND processed=0 AND state='processing' AND lease_token=?",
            (
                str(error or "")[:500],
                str(error or "")[:500],
                now,
                now,
                now,
                int(queue_id or 0),
                token,
            ),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def mark_sms_auto_queue_processed(queue_id: int, note: str = "") -> None:
    """Legacy terminal marker retained for old callers."""
    init_db()
    conn = _get_conn()
    try:
        now = _now()
        conn.execute(
            "UPDATE customer_payment_sms_queue SET processed=1, state='succeeded', note=?, "
            "processed_at=?, completed_at=?, updated_at=?, lease_token='', lease_expires_at='' WHERE id=?",
            (str(note or "")[:500], now, now, now, int(queue_id or 0)),
        )
        conn.commit()
    finally:
        conn.close()


def get_sms_auto_queue_by_event(
    event_id: str,
    *,
    agent_id: Optional[int] = None,
    legacy_event_id: str = "",
) -> Optional[Dict[str, Any]]:
    """Return an event scoped to its agent, optionally checking its legacy raw id."""
    init_db()
    eid = str(event_id or "").strip()
    if not eid:
        return None
    conn = _get_conn()
    try:
        ids = [eid]
        legacy = str(legacy_event_id or "").strip()
        if legacy and legacy not in ids:
            ids.append(legacy)
        placeholders = ",".join("?" for _ in ids)
        params: List[Any] = list(ids)
        sql = f"SELECT * FROM customer_payment_sms_queue WHERE event_id IN ({placeholders})"
        if agent_id is not None:
            sql += " AND agent_id=?"
            params.append(int(agent_id or 0))
        sql += " ORDER BY id DESC LIMIT 1"
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_payment_status(agent_id: int, pay_id: int) -> str:
    init_db()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT status FROM customer_payments WHERE agent_id=? AND id=? LIMIT 1",
            (int(agent_id or 0), int(pay_id or 0)),
        ).fetchone()
        return str(row["status"] or "") if row else ""
    finally:
        conn.close()


def get_payment_status_any_agent(pay_id: int) -> str:
    """Deprecated: use get_payment_status so tenant ownership is explicit."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT status FROM customer_payments WHERE id = ? LIMIT 1", (int(pay_id or 0),))
        row = cur.fetchone()
        return str(dict(row).get("status") or "") if row else ""
    finally:
        conn.close()


def _normalized_card_last4(value: Any) -> str:
    translated = str(value or "").strip().translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    )
    digits = "".join(ch for ch in translated if ch in "0123456789")
    return digits if len(digits) == 4 else ""


def _receipt_card_last4(receipt_image: Any) -> tuple[str, bool]:
    try:
        json_part = str(receipt_image or "").split("|", 1)[0].strip()
        meta = json.loads(json_part) if json_part.startswith("{") else {}
        raw = str(meta.get("card_last4") or "").strip() if isinstance(meta, dict) else ""
        return _normalized_card_last4(raw), bool(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "", False


def find_pending_card_payments_by_amount(
    amount_toman: int,
    *,
    agent_id: int,
    card_last4: str = "",
    require_last4: bool = False,
    max_age_minutes: int = 360,
    sms_time_ms: int = 0,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Find only this agent's unreserved pending card payments."""
    init_db()
    amount = int(amount_toman or 0)
    aid = int(agent_id or 0)
    incoming_raw = str(card_last4 or "").strip()
    incoming_last4 = _normalized_card_last4(incoming_raw)
    if amount <= 0 or aid <= 0 or (incoming_raw and not incoming_last4):
        return []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=max(5, int(max_age_minutes or 360)))
    params: List[Any] = [aid, amount, cutoff.strftime("%Y-%m-%d %H:%M:%S")]
    sms_window_sql = ""
    if sms_time_ms and int(sms_time_ms) > 0:
        try:
            sms_dt = datetime.fromtimestamp(int(sms_time_ms) / 1000, tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            sms_dt = None
        if sms_dt is not None:
            not_before = (sms_dt - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            not_after = (sms_dt + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
            sms_window_sql = " AND cp.created_at >= ? AND cp.created_at <= ? "
            params += [not_before, not_after]
    wanted = max(1, min(50, int(limit or 20)))
    params.append(max(50, min(250, wanted * 5)))
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT cp.* FROM customer_payments cp "
            "WHERE cp.agent_id=? AND cp.status='pending' "
            "AND cp.method IN ('card', 'card_to_card') AND cp.amount=? "
            "AND COALESCE(cp.created_at, '') >= ? "
            "AND NOT EXISTS (SELECT 1 FROM customer_payment_sms_queue q "
            "WHERE q.agent_id=cp.agent_id AND q.pay_id=cp.id AND q.processed=0 "
            "AND q.state IN ('pending', 'retry', 'waiting_wallet', 'processing')) "
            + sms_window_sql +
            "ORDER BY cp.created_at DESC LIMIT ?",
            params,
        )
        matched: List[Dict[str, Any]] = []
        for row in cur.fetchall():
            item = dict(row)
            receipt_last4, receipt_had_value = _receipt_card_last4(item.get("receipt_image"))
            if receipt_had_value and not receipt_last4:
                continue
            if require_last4:
                if not incoming_last4 or not receipt_last4 or incoming_last4 != receipt_last4:
                    continue
            elif incoming_last4 or receipt_last4:
                if not incoming_last4 or not receipt_last4 or incoming_last4 != receipt_last4:
                    continue
            matched.append(item)
            if len(matched) >= wanted:
                break
        return matched
    finally:
        conn.close()


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


def get_faq_text(agent_id: int, lang: str = "fa") -> str:
    """Return the FAQ for a language, migrating legacy string values safely."""
    lg = str(lang or "fa").strip().lower()
    if lg not in DEFAULT_FAQ_TEXTS:
        lg = "fa"
    settings = get_text_settings(agent_id)
    value = settings.get("faq_text")
    if isinstance(value, dict):
        selected = str(value.get(lg) or "").strip()
        if selected:
            return selected
        for fallback in ("fa", "en", "ru"):
            selected = str(value.get(fallback) or "").strip()
            if selected:
                return selected
    elif str(value or "").strip():
        # Keep old custom FAQs useful while the agent starts filling locales.
        return str(value).strip() if lg == "fa" else DEFAULT_FAQ_TEXTS[lg]
    return DEFAULT_FAQ_TEXTS[lg]


def set_faq_text(agent_id: int, lang: str, value: str) -> Dict[str, Any]:
    """Save one localized FAQ without overwriting the other languages."""
    lg = str(lang or "fa").strip().lower()
    if lg not in DEFAULT_FAQ_TEXTS:
        raise ValueError("unsupported FAQ language")
    settings = get_text_settings(agent_id)
    current = settings.get("faq_text")
    localized = dict(DEFAULT_FAQ_TEXTS)
    if isinstance(current, dict):
        localized.update({str(k): str(v) for k, v in current.items() if k in localized})
    elif str(current or "").strip():
        localized["fa"] = str(current)
    localized[lg] = str(value or "")
    settings["faq_text"] = localized
    return set_text_settings(agent_id, settings)


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


def redeem_zarin_voucher(agent_id: int, code: str, user_id: int, lang: str = "fa") -> Tuple[bool, str]:
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
        return False, i18n.t("voucher_invalid", lang)
    if not row["is_active"]:
        conn.close()
        return False, i18n.t("voucher_inactive", lang)
    if row["used_count"] >= row["max_uses"]:
        conn.close()
        return False, i18n.t("voucher_capacity", lang)
    expires = str(row["expires_at"] or "").strip()
    if expires:
        try:
            exp_dt = datetime.strptime(expires, "%Y-%m-%d %H:%M:%S")
            if datetime.now(timezone.utc).replace(tzinfo=None) > exp_dt:
                conn.close()
                return False, i18n.t("voucher_expired", lang)
        except ValueError:
            pass

    cur.execute(
        "UPDATE customer_zarin_vouchers SET used_count = used_count + 1, updated_at = ? WHERE agent_id = ? AND code = ? AND used_count < max_uses",
        (_now(), agent_id, code),
    )
    if cur.rowcount == 0:
        conn.close()
        return False, i18n.t("voucher_capacity", lang)

    cur.execute(
        "INSERT INTO customer_zarin_voucher_redemptions (agent_id, code, user_id, redeemed_at) VALUES (?, ?, ?, ?)",
        (agent_id, code, user_id, _now()),
    )
    amount = int(row["amount_toman"])
    conn.commit()
    conn.close()
    return True, str(amount)
