# Shared/userbot_db.py
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Tuple
import random
from typing import Any, Dict, List, Optional, Tuple

DB_FILE_NAME = "hiddify_sellbot.db"
LEGACY_DB_FILE_NAMES = ("userbot.db",)
DB_PATH = Path(__file__).with_name(DB_FILE_NAME)


def _migrate_legacy_db_name() -> None:
    if DB_PATH.exists():
        return
    for legacy_name in LEGACY_DB_FILE_NAMES:
        legacy_path = Path(__file__).with_name(legacy_name)
        if legacy_path.exists() and legacy_path.is_file():
            legacy_path.rename(DB_PATH)
            return


_migrate_legacy_db_name()
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
DEFAULT_SUB_REMINDER_SETTINGS = {
    "enabled": True,
    "usage_gb": 3,
    "days": 3,
}
DEFAULT_TRIAL_SPEC_SETTINGS = {
    "enabled": True,
    "announce_enabled": True,
    "usage_gb": 1,
    "days": 1,
}
DEFAULT_BUY_RENEW_SETTINGS = {
    "enable_buy": True,
    "enable_renew": True,
    "show_renew_in_main_menu": True,
    "renew_mode": "plans",  # plans | servers
    "plan_columns": 1,      # 1 | 2
    "server_columns": 1,    # 1 | 2 | 3
    "renew_policy": "advanced",  # advanced | default | fair
    "renew_volume_mode": "reset",  # reset | add
    "renew_time_mode": "reset",    # reset | add
    "renew_max_days": 3,
    "renew_max_remaining_gb": 3,
    "renew_unlimited_volume": False,
    "renew_unlimited_time": False,
    "renew_unlimited_volume_from_gb": 1000,
    "renew_unlimited_time_from_days": 365,
    "event_channel_enabled": False,
    "event_channel_id": "",
}
DEFAULT_TX_PLANS_SETTINGS = {
    "random_tx_spec": False,
    "min_transaction_toman": 10000,
    "plan_categories_enabled": True,
    "plan_sort_by_priority": True,
    "plan_sort_mode": "price",  # price | gb | days
    "plan_sort_desc": False,     # False=asc(صعودی) | True=desc(نزولی)
}
DEFAULT_MARKETING_SETTINGS = {
    "enable_discount_code": False,
    "enable_increase_code": False,
    "show_gift_button": False,
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
    "guide_text": (
        "🔒 برای استفاده از ربات، ابتدا در کانال پشتیبانی عضو شوید.\n"
        "پس از عضویت روی «✅ بررسی عضویت» بزنید.\n\n"
        "اگر عضویت شما تایید نشد:\n"
        "1) مطمئن شوید دقیقاً در همان کانال اعلام‌شده عضو شده‌اید.\n"
        "2) ربات کاربران باید در کانال، ادمین باشد تا عضویت را تشخیص دهد."
    ),
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

DEFAULT_REFERRAL_SETTINGS = {
    "referral_enabled": False,
    "trial_reward_enabled": True,
    "trial_reward_amount": 0,
    "purchase_reward_enabled": True,
    "purchase_reward_amount": 0,
    "max_successful_referrals": 0,  # 0 = نامحدود
    "min_purchase_amount": 0,       # حداقل مبلغ برای پاداش خرید
    "invite_intro_text": (
        "🎁 دعوت دوستان\n"
        "❖ ◈━━━━━━━━━━━━━━━◈ ❖\n"
        "دوستت رو دعوت کن و از هر دعوت پاداش بگیر!\n\n"
        "🤝 پاداش تست دوستت برای تو: {trial_reward}\n"
        "🛒 پاداش اولین خرید دوستت: {purchase_reward}\n\n"
        "🔗 لینک دعوت:\n{invite_link}"
    ),
    "trial_reward_notify_text": (
        "🎉 پاداش دعوت!\n"
        "دوست شما «{invitee_name}» اکانت تست رایگان خود را فعال کرد.\n"
        "🎁 {amount} تومان به کیف پول شما اضافه شد.\n"
        "موجودی فعلی: {wallet} تومان"
    ),
    "purchase_reward_notify_text": (
        "🎉 پاداش دعوت!\n"
        "دوست شما «{invitee_name}» اولین خرید خود را انجام داد.\n"
        "🎁 {amount} تومان به کیف پول شما اضافه شد.\n"
        "موجودی فعلی: {wallet} تومان"
    ),
}
REFERRAL_REWARD_KINDS = ("trial", "purchase")
REFERRAL_REWARD_SOURCE_MANUAL = "manual"
REFERRAL_REWARD_LABELS = {
    "trial": "پاداش تست دعوت‌شده",
    "purchase": "پاداش اولین خرید دعوت‌شده",
    REFERRAL_REWARD_SOURCE_MANUAL: "پاداش دستی ادمین",
}
DEFAULT_BACKUP_RESTORE_SETTINGS = {
    "auto_backup_enabled": True,
    "event_channel_enabled": False,
    "event_channel_id": "",
}
DEFAULT_UI_SETTINGS = {
    "colored_buttons": True,
    "button_theme": "smart",
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
DEFAULT_TEXT_SETTINGS = {
    "welcome_message": "سلام {full_name} عزیز 👋\nبه ربات ما خوش آمدید.",
    "faq_text": (
        "❓ سوالات متداول\n\n"
        "1) لینک اشتراک را کجا بزنم؟\n"
        "از بخش «📊وضعیت اشتراک» وارد سرویس شوید و روی «لینک اشتراک» بزنید.\n\n"
        "2) اگر کانفیگ وصل نشد چه کنم؟\n"
        "اول اینترنت و تاریخ/ساعت گوشی را چک کنید، سپس «بروزرسانی اطلاعات» بزنید.\n\n"
        "3) چطور تمدید کنم؟\n"
        "از «♾تمدید اشتراک» سرویس را انتخاب کنید و پلن تمدید را بخرید.\n\n"
        "4) پشتیبانی از کجاست؟\n"
        "از دکمه «📩پشتیبانی» پیام خود را ارسال کنید."
    ),
    "guide_text": "انتخاب سیستم عامل ⬇️",
    "guide_android_text": (
        "📱 راهنمای اندروید\n\n"
        "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
        "2) v2rayNG:\nhttps://github.com/2dust/v2rayNG/releases\n\n"
        "3) NekoBox for Android:\nhttps://github.com/MatsuriDayo/NekoBoxForAndroid/releases\n\n"
        "بعد از نصب، لینک اشتراک را Import کنید و Connect بزنید."
    ),
    "guide_ios_text": (
        "📱 راهنمای iOS\n\n"
        "1) Streisand:\nhttps://apps.apple.com/app/streisand/id6450534064\n\n"
        "2) Hiddify (iOS):\nhttps://apps.apple.com/app/hiddify-proxy-vpn/id6596777532\n\n"
        "بعد از نصب، لینک اشتراک را Import کرده و اتصال را فعال کنید."
    ),
    "guide_windows_text": (
        "🖥️ راهنمای ویندوز\n\n"
        "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
        "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
        "3) v2rayN:\nhttps://github.com/2dust/v2rayN/releases\n\n"
        "پس از نصب، لینک اشتراک را Paste/Import کنید و Connect شوید."
    ),
    "guide_mac_text": (
        "💻 راهنمای مک\n\n"
        "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
        "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
        "پس از نصب، لینک اشتراک را Import کنید و اتصال را فعال کنید."
    ),
    "guide_linux_text": (
        "🖥️ راهنمای لینوکس\n\n"
        "1) Hiddify Next:\nhttps://github.com/hiddify/hiddify-next/releases\n\n"
        "2) Nekoray:\nhttps://github.com/MatsuriDayo/nekoray/releases\n\n"
        "پس از نصب، لینک اشتراک را در برنامه وارد کنید و Connect بزنید."
    ),
    "invite_text": "💌 لینک دعوت شما:\n{invite_link}",
    "invite_info_text": "🎁 دعوت دوستان خود از هدایای ویژه ای بهره مند شوید",
    "invite_banner_text": (
        "🎁 بنر دعوت اختصاصی شما\n\n"
        "🔗 لینک دعوت شما:\n{invite_link}\n\n"
        "دوستانت را دعوت کن و از مزایای ویژه بهره‌مند شو."
    ),
    "invite_banner_photo_id": "",
    "card_to_card_text": "0",
    "servers_list_text": "📡 **لیست سرورها**\nلطفاً لوکیشن مورد نظر خود را انتخاب کنید:",
    "plans_list_text": "🛒 **لطفاً پلن مورد نظر خود را انتخاب کنید:**",
    "ticket_panel_text": "📩 برای ارتباط با پشتیبانی، پیام خود را ارسال کنید.",
    # فعلا برای تنظیم از پنل ادمین ذخیره می‌شود.
    "zarinpal_pro_text": "0",
}

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = _get_conn()
    cur = conn.cursor()

    # 1. کاربران (نسخه پایه)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS userbot_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            full_name TEXT,
            created_at TEXT,
            wallet_balance INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            got_free_trial INTEGER DEFAULT 0,
            invited_by_user_id INTEGER DEFAULT 0,
            referral_code TEXT DEFAULT ''
        )
    """)

    # جداول دیگر بدون تغییر...
    cur.execute("""CREATE TABLE IF NOT EXISTS userbot_services (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, server_id INTEGER,
            server_title TEXT, usage_current REAL, usage_limit REAL, days_left INTEGER,
            last_online TEXT, comment TEXT)""")

    # نگاشت سرویس مرکزی به یوزرهای همان سرویس روی چند سرور (نود)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_service_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id INTEGER NOT NULL,
            server_id INTEGER NOT NULL,
            server_title TEXT,
            panel_user_uuid TEXT NOT NULL,
            panel_user_id TEXT,
            marzban_username TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_userbot_service_nodes_unique
        ON userbot_service_nodes(service_id, server_id, panel_user_uuid)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_sub_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id INTEGER UNIQUE NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_service_probe (
            service_id INTEGER PRIMARY KEY,
            missing_streak INTEGER DEFAULT 0,
            first_missing_at TEXT,
            last_seen_at TEXT,
            last_missing_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_service_reminder_state (
            service_id INTEGER PRIMARY KEY,
            days_sent INTEGER DEFAULT 0,
            usage_sent INTEGER DEFAULT 0,
            expired_sent INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """
    )

    cur.execute("""CREATE TABLE IF NOT EXISTS userbot_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER UNIQUE NOT NULL, user_id INTEGER,
            telegram_id INTEGER, username TEXT, full_name TEXT, created_at TEXT, volume_gb REAL,
            days INTEGER, price INTEGER, plan_title TEXT, server_location TEXT, status TEXT)""")

    cur.execute("""CREATE TABLE IF NOT EXISTS userbot_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tx_code TEXT, user_id INTEGER, amount INTEGER, method TEXT,
            status TEXT, receipt_image TEXT, idempotency_key TEXT, created_at TEXT, updated_at TEXT)""")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_sms_webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE,
            sender TEXT DEFAULT '',
            amount_raw INTEGER DEFAULT 0,
            currency_raw TEXT DEFAULT '',
            amount_toman INTEGER DEFAULT 0,
            reference TEXT DEFAULT '',
            card_last4 TEXT DEFAULT '',
            body TEXT DEFAULT '',
            status TEXT DEFAULT 'received',
            matched_payment_id INTEGER DEFAULT 0,
            message TEXT DEFAULT '',
            received_at INTEGER DEFAULT 0,
            device_time INTEGER DEFAULT 0,
            created_at TEXT DEFAULT ''
        )
        """
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS userbot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_zarin_vouchers (
            code TEXT PRIMARY KEY,
            amount_toman INTEGER NOT NULL DEFAULT 0,
            zarinpal_link TEXT DEFAULT '',
            max_uses INTEGER NOT NULL DEFAULT 1,
            used_count INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_zarin_voucher_redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            redeemed_at TEXT DEFAULT '',
            UNIQUE(code, user_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_code INTEGER UNIQUE NOT NULL,
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
            updated_at TEXT DEFAULT ''
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_code INTEGER NOT NULL,
            sender_type TEXT NOT NULL,
            sender_name TEXT DEFAULT '',
            message_text TEXT DEFAULT '',
            photo_file_id TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_userbot_tickets_status ON userbot_tickets(status)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_userbot_tickets_user_id ON userbot_tickets(user_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_userbot_ticket_messages_code ON userbot_ticket_messages(ticket_code)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_userbot_ticket_messages_code_id ON userbot_ticket_messages(ticket_code, id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_userbot_tickets_updated_id ON userbot_tickets(updated_at, id)"
    )

    # Referral system: Source of Truth رابطه دعوت + دفتر پاداش‌ها
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_id INTEGER NOT NULL,
            invitee_id INTEGER NOT NULL UNIQUE,
            invited_by_code TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            rejection_reason TEXT DEFAULT '',
            fraud_flag INTEGER DEFAULT 0,
            invitee_qualified INTEGER DEFAULT 1,
            first_seen_payload TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_referral_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referral_id INTEGER,
            inviter_id INTEGER NOT NULL,
            invitee_id INTEGER NOT NULL DEFAULT 0,
            reward_type TEXT NOT NULL,
            reward_source TEXT NOT NULL,
            amount_toman INTEGER NOT NULL DEFAULT 0,
            voucher_code TEXT NOT NULL UNIQUE,
            payment_id INTEGER DEFAULT 0,
            status TEXT DEFAULT 'paid',
            revoked_at TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
        """
    )

    conn.commit()
    conn.close()

    # اجرای تابع مایگریشن برای دیتابیس‌های قدیمی
    _migrate_db()

def _migrate_db():
    """اضافه کردن ستون‌های جدید به دیتابیس‌های قدیمی بدون حذف اطلاعات"""
    conn = _get_conn()
    cur = conn.cursor()
    try:
        # چک می‌کنیم اگر ستون نیست اضافه شود
        cur.execute("SELECT is_banned FROM userbot_users LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE userbot_users ADD COLUMN is_banned INTEGER DEFAULT 0")
        print("Migrated: is_banned column added.")
    
    try:
        cur.execute("SELECT got_free_trial FROM userbot_users LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE userbot_users ADD COLUMN got_free_trial INTEGER DEFAULT 0")
        print("Migrated: got_free_trial column added.")

    try:
        cur.execute("SELECT tx_code FROM userbot_payments LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE userbot_payments ADD COLUMN tx_code TEXT")
        print("Migrated: tx_code column added to userbot_payments.")

    try:
        cur.execute("SELECT idempotency_key FROM userbot_payments LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE userbot_payments ADD COLUMN idempotency_key TEXT")
        print("Migrated: idempotency_key column added to userbot_payments.")

    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_userbot_payments_idempotency ON userbot_payments(idempotency_key)"
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_sms_webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE,
            sender TEXT DEFAULT '',
            amount_raw INTEGER DEFAULT 0,
            currency_raw TEXT DEFAULT '',
            amount_toman INTEGER DEFAULT 0,
            reference TEXT DEFAULT '',
            card_last4 TEXT DEFAULT '',
            body TEXT DEFAULT '',
            status TEXT DEFAULT 'received',
            matched_payment_id INTEGER DEFAULT 0,
            message TEXT DEFAULT '',
            received_at INTEGER DEFAULT 0,
            device_time INTEGER DEFAULT 0,
            created_at TEXT DEFAULT ''
        )
        """
    )

    # probe table migrations
    try:
        cur.execute("SELECT first_missing_at FROM userbot_service_probe LIMIT 1")
    except sqlite3.OperationalError:
        try:
            cur.execute("ALTER TABLE userbot_service_probe ADD COLUMN first_missing_at TEXT")
            print("Migrated: first_missing_at column added to userbot_service_probe.")
        except sqlite3.OperationalError:
            pass

    # Marzban integration: add marzban_username column to service_nodes
    try:
        cur.execute("SELECT marzban_username FROM userbot_service_nodes LIMIT 1")
    except sqlite3.OperationalError:
        try:
            cur.execute("ALTER TABLE userbot_service_nodes ADD COLUMN marzban_username TEXT DEFAULT ''")
            print("Migrated: marzban_username column added to userbot_service_nodes.")
        except sqlite3.OperationalError:
            pass

    # Per-node runtime tracking for frozen-node accounting (node down / server deleted).
    # PRAGMA table_info is safer than SELECT ... LIMIT 1 on empty table.
    try:
        cur.execute("PRAGMA table_info(userbot_service_nodes)")
        existing_cols = {str(r[1]) for r in cur.fetchall()}
    except Exception:
        existing_cols = set()
    for _col, _ddl in (
        ("usage_current", "REAL DEFAULT 0"),
        ("days_left", "INTEGER"),
        ("frozen", "INTEGER DEFAULT 0"),
        ("fail_count", "INTEGER DEFAULT 0"),
        ("last_ok_at", "TEXT"),
        ("deleted", "INTEGER DEFAULT 0"),
    ):
        if _col not in existing_cols:
            try:
                cur.execute(f"ALTER TABLE userbot_service_nodes ADD COLUMN {_col} {_ddl}")
                print(f"Migrated: {_col} column added to userbot_service_nodes.")
            except sqlite3.OperationalError as e:
                # race or already exists – log but don't fail
                print(f"Migrate skip {_col}: {e}")

    # Referral system: ensure referral helpers are present on old databases
    try:
        cur.execute("SELECT invited_by_user_id FROM userbot_users LIMIT 1")
    except sqlite3.OperationalError:
        try:
            cur.execute("ALTER TABLE userbot_users ADD COLUMN invited_by_user_id INTEGER DEFAULT 0")
            print("Migrated: invited_by_user_id column added to userbot_users.")
        except sqlite3.OperationalError:
            pass
    try:
        cur.execute("SELECT referral_code FROM userbot_users LIMIT 1")
    except sqlite3.OperationalError:
        try:
            cur.execute("ALTER TABLE userbot_users ADD COLUMN referral_code TEXT DEFAULT ''")
            print("Migrated: referral_code column added to userbot_users.")
        except sqlite3.OperationalError:
            pass

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_userbot_referrals_inviter ON userbot_referrals(inviter_id)"
    )
    try:
        cur.execute("SELECT invitee_qualified FROM userbot_referrals LIMIT 1")
    except sqlite3.OperationalError:
        try:
            cur.execute("ALTER TABLE userbot_referrals ADD COLUMN invitee_qualified INTEGER DEFAULT 1")
            print("Migrated: invitee_qualified column added to userbot_referrals.")
        except sqlite3.OperationalError:
            pass
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_userbot_referral_rewards_pair
        ON userbot_referral_rewards(referral_id, reward_type)
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_userbot_referral_rewards_inviter ON userbot_referral_rewards(inviter_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_userbot_users_referral_code ON userbot_users(referral_code)"
    )

    # Expiry notice: track whether the "subscription expired" notification was sent
    try:
        cur.execute("SELECT expired_sent FROM userbot_service_reminder_state LIMIT 1")
    except sqlite3.OperationalError:
        try:
            cur.execute("ALTER TABLE userbot_service_reminder_state ADD COLUMN expired_sent INTEGER DEFAULT 0")
            print("Migrated: expired_sent column added to userbot_service_reminder_state.")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


def generate_tx_code() -> str:
    """Generate a unique 7-digit transaction code."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        for _ in range(50):
            code = f"{random.randint(0, 9999999):07d}"
            cur.execute("SELECT 1 FROM userbot_payments WHERE tx_code = ? LIMIT 1", (code,))
            if not cur.fetchone():
                return code
        return f"{random.randint(0, 9999999):07d}"
    finally:
        conn.close()


def get_ui_settings() -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'ui_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_UI_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    for key in DEFAULT_UI_SETTINGS.keys():
                        if key in raw:
                            if isinstance(DEFAULT_UI_SETTINGS[key], bool):
                                settings[key] = _as_bool(raw.get(key), bool(DEFAULT_UI_SETTINGS[key]))
                            else:
                                settings[key] = str(raw.get(key) or DEFAULT_UI_SETTINGS[key]).strip()
            except Exception:
                pass
        return settings
    finally:
        conn.close()


def set_ui_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(DEFAULT_UI_SETTINGS)
    if isinstance(settings, dict):
        for key in DEFAULT_UI_SETTINGS.keys():
            if key in settings:
                if isinstance(DEFAULT_UI_SETTINGS[key], bool):
                    current[key] = _as_bool(settings.get(key), bool(DEFAULT_UI_SETTINGS[key]))
                else:
                    current[key] = str(settings.get(key) or DEFAULT_UI_SETTINGS[key]).strip()

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('ui_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def toggle_ui_setting(name: str) -> Dict[str, Any]:
    settings = get_ui_settings()
    if name in DEFAULT_UI_SETTINGS:
        settings[name] = not bool(settings.get(name))
    return set_ui_settings(settings)


def set_ui_setting(name: str, value: Any) -> Dict[str, Any]:
    settings = get_ui_settings()
    if name in DEFAULT_UI_SETTINGS:
        settings[name] = value
    return set_ui_settings(settings)


def get_subscription_settings() -> Dict[str, bool]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'subscription_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_SUBS_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    for k in DEFAULT_SUBS_SETTINGS.keys():
                        if k in raw:
                            settings[k] = bool(raw[k])
            except Exception:
                pass
        return settings
    finally:
        conn.close()


def set_subscription_settings(settings: Dict[str, Any]) -> Dict[str, bool]:
    current = dict(DEFAULT_SUBS_SETTINGS)
    if isinstance(settings, dict):
        for k in DEFAULT_SUBS_SETTINGS.keys():
            if k in settings:
                current[k] = bool(settings[k])

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('subscription_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def set_subscription_setting(name: str, value: bool) -> Dict[str, bool]:
    settings = get_subscription_settings()
    if name in DEFAULT_SUBS_SETTINGS:
        settings[name] = bool(value)
    return set_subscription_settings(settings)


def toggle_subscription_setting(name: str) -> Dict[str, bool]:
    settings = get_subscription_settings()
    if name in DEFAULT_SUBS_SETTINGS:
        settings[name] = not bool(settings.get(name))
    return set_subscription_settings(settings)


def _renew_modes_from_policy(policy: str) -> Tuple[str, str]:
    normalized = str(policy or "").strip().lower()
    if normalized == "fair":
        return "add", "add"
    if normalized == "default":
        return "add", "reset"
    return "reset", "reset"


def _normalize_renew_mode(value: Any, fallback: str) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"add", "reset"} else fallback


def get_renew_modes() -> Tuple[str, str]:
    """الگوی تمدید تعریف‌شده توسط ادمین: (renew_volume_mode, renew_time_mode) ∈ {add, reset}."""
    try:
        settings = get_buy_renew_settings()
    except Exception:
        settings = {}
    volume = str(settings.get("renew_volume_mode") or "reset").strip().lower()
    time = str(settings.get("renew_time_mode") or "reset").strip().lower()
    if volume not in {"add", "reset"}:
        volume = "add"
    if time not in {"add", "reset"}:
        time = "add"
    return volume, time


def get_buy_renew_settings() -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'buy_renew_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_BUY_RENEW_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    for k in DEFAULT_BUY_RENEW_SETTINGS.keys():
                        if k in raw:
                            settings[k] = raw[k]
            except Exception:
                pass

        settings["enable_buy"] = bool(settings.get("enable_buy", True))
        settings["enable_renew"] = bool(settings.get("enable_renew", True))
        settings["show_renew_in_main_menu"] = bool(settings.get("show_renew_in_main_menu", True))
        settings["renew_unlimited_volume"] = bool(settings.get("renew_unlimited_volume", False))
        settings["renew_unlimited_time"] = bool(settings.get("renew_unlimited_time", False))
        try:
            settings["renew_unlimited_volume_from_gb"] = max(1, int(settings.get("renew_unlimited_volume_from_gb") or 1000))
        except Exception:
            settings["renew_unlimited_volume_from_gb"] = 1000
        try:
            settings["renew_unlimited_time_from_days"] = max(1, int(settings.get("renew_unlimited_time_from_days") or 365))
        except Exception:
            settings["renew_unlimited_time_from_days"] = 365
        settings["event_channel_enabled"] = bool(settings.get("event_channel_enabled", False))
        settings["event_channel_id"] = str(settings.get("event_channel_id") or "").strip()
        mode = str(settings.get("renew_mode") or "plans").strip().lower()
        settings["renew_mode"] = mode if mode in {"plans", "servers"} else "plans"
        policy = str(settings.get("renew_policy") or "advanced").strip().lower()
        if policy == "oversell":
            policy = "default"
        settings["renew_policy"] = policy if policy in {"fair", "advanced", "default"} else "advanced"
        fallback_volume_mode, fallback_time_mode = _renew_modes_from_policy(settings["renew_policy"])
        settings["renew_volume_mode"] = _normalize_renew_mode(
            settings.get("renew_volume_mode"), fallback_volume_mode
        )
        settings["renew_time_mode"] = _normalize_renew_mode(
            settings.get("renew_time_mode"), fallback_time_mode
        )
        try:
            settings["renew_max_days"] = max(1, int(settings.get("renew_max_days") or 3))
        except Exception:
            settings["renew_max_days"] = 3
        try:
            settings["renew_max_remaining_gb"] = max(1, int(settings.get("renew_max_remaining_gb") or 3))
        except Exception:
            settings["renew_max_remaining_gb"] = 3
        try:
            settings["plan_columns"] = int(settings.get("plan_columns") or 1)
        except Exception:
            settings["plan_columns"] = 1
        if settings["plan_columns"] not in {1, 2}:
            settings["plan_columns"] = 1
        try:
            settings["server_columns"] = int(settings.get("server_columns") or 1)
        except Exception:
            settings["server_columns"] = 1
        if settings["server_columns"] not in {1, 2, 3}:
            settings["server_columns"] = 1
        return settings
    finally:
        conn.close()


def set_buy_renew_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(DEFAULT_BUY_RENEW_SETTINGS)
    if isinstance(settings, dict):
        for k in DEFAULT_BUY_RENEW_SETTINGS.keys():
            if k in settings:
                current[k] = settings[k]

    current["enable_buy"] = bool(current.get("enable_buy", True))
    current["enable_renew"] = bool(current.get("enable_renew", True))
    current["show_renew_in_main_menu"] = bool(current.get("show_renew_in_main_menu", True))
    current["renew_unlimited_volume"] = bool(current.get("renew_unlimited_volume", False))
    current["renew_unlimited_time"] = bool(current.get("renew_unlimited_time", False))
    try:
        current["renew_unlimited_volume_from_gb"] = max(1, int(current.get("renew_unlimited_volume_from_gb") or 1000))
    except Exception:
        current["renew_unlimited_volume_from_gb"] = 1000
    try:
        current["renew_unlimited_time_from_days"] = max(1, int(current.get("renew_unlimited_time_from_days") or 365))
    except Exception:
        current["renew_unlimited_time_from_days"] = 365
    current["event_channel_enabled"] = bool(current.get("event_channel_enabled", False))
    current["event_channel_id"] = str(current.get("event_channel_id") or "").strip()
    mode = str(current.get("renew_mode") or "plans").strip().lower()
    current["renew_mode"] = mode if mode in {"plans", "servers"} else "plans"
    policy = str(current.get("renew_policy") or "advanced").strip().lower()
    if policy == "oversell":
        policy = "default"
    current["renew_policy"] = policy if policy in {"fair", "advanced", "default"} else "advanced"
    fallback_volume_mode, fallback_time_mode = _renew_modes_from_policy(current["renew_policy"])
    current["renew_volume_mode"] = _normalize_renew_mode(
        current.get("renew_volume_mode"), fallback_volume_mode
    )
    current["renew_time_mode"] = _normalize_renew_mode(
        current.get("renew_time_mode"), fallback_time_mode
    )
    try:
        current["renew_max_days"] = max(1, int(current.get("renew_max_days") or 3))
    except Exception:
        current["renew_max_days"] = 3
    try:
        current["renew_max_remaining_gb"] = max(1, int(current.get("renew_max_remaining_gb") or 3))
    except Exception:
        current["renew_max_remaining_gb"] = 3
    try:
        current["plan_columns"] = int(current.get("plan_columns") or 1)
    except Exception:
        current["plan_columns"] = 1
    if current["plan_columns"] not in {1, 2}:
        current["plan_columns"] = 1
    try:
        current["server_columns"] = int(current.get("server_columns") or 1)
    except Exception:
        current["server_columns"] = 1
    if current["server_columns"] not in {1, 2, 3}:
        current["server_columns"] = 1

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('buy_renew_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def toggle_buy_renew_setting(name: str) -> Dict[str, Any]:
    settings = get_buy_renew_settings()
    if name in {
        "enable_buy",
        "enable_renew",
        "show_renew_in_main_menu",
        "renew_unlimited_volume",
        "renew_unlimited_time",
        "event_channel_enabled",
    }:
        settings[name] = not bool(settings.get(name))
    return set_buy_renew_settings(settings)


def get_tx_plans_settings() -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'tx_plans_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_TX_PLANS_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    for k in DEFAULT_TX_PLANS_SETTINGS.keys():
                        if k in raw:
                            settings[k] = raw[k]
            except Exception:
                pass

        settings["random_tx_spec"] = bool(settings.get("random_tx_spec", False))
        settings["plan_categories_enabled"] = bool(settings.get("plan_categories_enabled", True))
        settings["plan_sort_by_priority"] = bool(settings.get("plan_sort_by_priority", True))
        mode = str(settings.get("plan_sort_mode") or "price").strip().lower()
        settings["plan_sort_mode"] = mode if mode in {"price", "gb", "days"} else "price"
        settings["plan_sort_desc"] = bool(settings.get("plan_sort_desc", False))
        try:
            settings["min_transaction_toman"] = max(1, int(settings.get("min_transaction_toman") or 10000))
        except Exception:
            settings["min_transaction_toman"] = 10000
        return settings
    finally:
        conn.close()


def set_tx_plans_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(DEFAULT_TX_PLANS_SETTINGS)
    if isinstance(settings, dict):
        for k in DEFAULT_TX_PLANS_SETTINGS.keys():
            if k in settings:
                current[k] = settings[k]

    current["random_tx_spec"] = bool(current.get("random_tx_spec", False))
    current["plan_categories_enabled"] = bool(current.get("plan_categories_enabled", True))
    current["plan_sort_by_priority"] = bool(current.get("plan_sort_by_priority", True))
    mode = str(current.get("plan_sort_mode") or "price").strip().lower()
    current["plan_sort_mode"] = mode if mode in {"price", "gb", "days"} else "price"
    current["plan_sort_desc"] = bool(current.get("plan_sort_desc", False))
    try:
        current["min_transaction_toman"] = max(1, int(current.get("min_transaction_toman") or 10000))
    except Exception:
        current["min_transaction_toman"] = 10000

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('tx_plans_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def toggle_tx_plans_setting(name: str) -> Dict[str, Any]:
    settings = get_tx_plans_settings()
    if name in {"random_tx_spec", "plan_categories_enabled", "plan_sort_by_priority", "plan_sort_desc"}:
        settings[name] = not bool(settings.get(name))
    return set_tx_plans_settings(settings)


def set_tx_plans_min_transaction(value: int) -> Dict[str, Any]:
    settings = get_tx_plans_settings()
    settings["min_transaction_toman"] = max(1, int(value))
    return set_tx_plans_settings(settings)


def set_tx_plans_sort_mode(mode: str) -> Dict[str, Any]:
    settings = get_tx_plans_settings()
    m = str(mode or "").strip().lower()
    settings["plan_sort_mode"] = m if m in {"price", "gb", "days"} else "price"
    return set_tx_plans_settings(settings)


def set_buy_renew_mode(mode: str) -> Dict[str, Any]:
    settings = get_buy_renew_settings()
    mode_norm = str(mode or "").strip().lower()
    settings["renew_mode"] = mode_norm if mode_norm in {"plans", "servers"} else "plans"
    return set_buy_renew_settings(settings)


def set_buy_renew_columns(kind: str, columns: int) -> Dict[str, Any]:
    settings = get_buy_renew_settings()
    k = str(kind or "").strip().lower()
    v = int(columns)
    if k == "plans":
        settings["plan_columns"] = v if v in {1, 2} else 1
    elif k == "servers":
        settings["server_columns"] = v if v in {1, 2, 3} else 1
    return set_buy_renew_settings(settings)


def set_buy_renew_policy(policy: str) -> Dict[str, Any]:
    settings = get_buy_renew_settings()
    p = str(policy or "").strip().lower()
    if p == "oversell":
        p = "default"
    settings["renew_policy"] = p if p in {"fair", "advanced", "default"} else "advanced"
    volume_mode, time_mode = _renew_modes_from_policy(settings["renew_policy"])
    settings["renew_volume_mode"] = volume_mode
    settings["renew_time_mode"] = time_mode
    return set_buy_renew_settings(settings)


def set_buy_renew_rollover_mode(kind: str, mode: str) -> Dict[str, Any]:
    settings = get_buy_renew_settings()
    kind_norm = str(kind or "").strip().lower()
    mode_norm = str(mode or "").strip().lower()
    if mode_norm not in {"add", "reset"}:
        mode_norm = "reset"
    if kind_norm == "volume":
        settings["renew_volume_mode"] = mode_norm
    elif kind_norm == "time":
        settings["renew_time_mode"] = mode_norm
    return set_buy_renew_settings(settings)


def set_buy_renew_limit(name: str, value: int) -> Dict[str, Any]:
    settings = get_buy_renew_settings()
    v = max(1, int(value))
    if name == "renew_max_days":
        settings["renew_max_days"] = v
    elif name == "renew_max_remaining_gb":
        settings["renew_max_remaining_gb"] = v
    elif name == "renew_unlimited_volume_from_gb":
        settings["renew_unlimited_volume_from_gb"] = v
        settings["renew_unlimited_volume"] = True
    elif name == "renew_unlimited_time_from_days":
        settings["renew_unlimited_time_from_days"] = v
        settings["renew_unlimited_time"] = True
    return set_buy_renew_settings(settings)


def get_text_settings() -> Dict[str, str]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'text_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_TEXT_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    for k in DEFAULT_TEXT_SETTINGS.keys():
                        if k in raw and raw[k] is not None:
                            settings[k] = str(raw[k])
            except Exception:
                pass
        return settings
    finally:
        conn.close()


def set_text_settings(settings: Dict[str, Any]) -> Dict[str, str]:
    current = dict(DEFAULT_TEXT_SETTINGS)
    if isinstance(settings, dict):
        for k in DEFAULT_TEXT_SETTINGS.keys():
            if k in settings and settings[k] is not None:
                current[k] = str(settings[k])

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('text_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def set_text_setting(name: str, value: str) -> Dict[str, str]:
    if name not in DEFAULT_TEXT_SETTINGS:
        raise ValueError("invalid text setting name")
    settings = get_text_settings()
    raw_value = str(value or "")
    resettable_by_zero = (
        name.startswith("guide_")
        or name in {
            "invite_info_text",
            "invite_banner_text",
            "invite_text",
            "servers_list_text",
            "plans_list_text",
            "ticket_panel_text",
            "zarinpal_pro_text",
            "card_to_card_text",
        }
    )
    if resettable_by_zero and raw_value.strip() == "0":
        settings[name] = str(DEFAULT_TEXT_SETTINGS.get(name) or "")
    else:
        settings[name] = raw_value
    return set_text_settings(settings)


def get_marketing_settings() -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'marketing_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_MARKETING_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    for key in settings.keys():
                        if key in raw and raw[key] is not None:
                            settings[key] = raw[key]
            except Exception:
                pass

        settings["enable_discount_code"] = _as_bool(settings.get("enable_discount_code"), False)
        settings["enable_increase_code"] = _as_bool(settings.get("enable_increase_code"), False)
        settings["show_gift_button"] = _as_bool(settings.get("show_gift_button"), False)
        settings["show_user_status"] = _as_bool(settings.get("show_user_status"), True)
        settings["instant_gift_coupon"] = _as_bool(settings.get("instant_gift_coupon"), False)
        settings["auto_gift_text"] = str(settings.get("auto_gift_text") or "")
        try:
            settings["min_auto_gift_charge"] = max(0, int(settings.get("min_auto_gift_charge") or 0))
        except Exception:
            settings["min_auto_gift_charge"] = int(DEFAULT_MARKETING_SETTINGS["min_auto_gift_charge"])
        return settings
    finally:
        conn.close()


def set_marketing_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(DEFAULT_MARKETING_SETTINGS)
    if isinstance(settings, dict):
        for key in current.keys():
            if key in settings and settings[key] is not None:
                current[key] = settings[key]

    current["enable_discount_code"] = _as_bool(current.get("enable_discount_code"), False)
    current["enable_increase_code"] = _as_bool(current.get("enable_increase_code"), False)
    current["show_gift_button"] = _as_bool(current.get("show_gift_button"), False)
    current["show_user_status"] = _as_bool(current.get("show_user_status"), True)
    current["instant_gift_coupon"] = _as_bool(current.get("instant_gift_coupon"), False)
    current["auto_gift_text"] = str(current.get("auto_gift_text") or "")
    current["min_auto_gift_charge"] = max(0, int(current.get("min_auto_gift_charge") or 0))

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('marketing_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def toggle_marketing_setting(name: str) -> Dict[str, Any]:
    if name not in {
        "enable_discount_code",
        "enable_increase_code",
        "show_gift_button",
        "show_user_status",
        "instant_gift_coupon",
    }:
        raise ValueError("invalid marketing setting name")
    settings = get_marketing_settings()
    settings[name] = not bool(settings.get(name))
    return set_marketing_settings(settings)


def set_marketing_value(name: str, value: Any) -> Dict[str, Any]:
    settings = get_marketing_settings()
    if name == "auto_gift_text":
        settings[name] = str(value or "")
    elif name == "min_auto_gift_charge":
        settings[name] = max(0, int(value))
    else:
        raise ValueError("invalid marketing setting value")
    return set_marketing_settings(settings)


def get_force_join_settings() -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'force_join_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_FORCE_JOIN_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    for key in settings.keys():
                        if key in raw and raw[key] is not None:
                            settings[key] = raw[key]
            except Exception:
                pass
        settings["enabled"] = _as_bool(settings.get("enabled"), False)
        settings["channel_id"] = str(settings.get("channel_id") or "").strip()
        settings["channel_username"] = str(settings.get("channel_username") or "").strip().lstrip("@")
        settings["channel_link"] = str(settings.get("channel_link") or "").strip()
        settings["guide_text"] = str(settings.get("guide_text") or "")
        return settings
    finally:
        conn.close()


def set_force_join_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(DEFAULT_FORCE_JOIN_SETTINGS)
    if isinstance(settings, dict):
        for key in current.keys():
            if key in settings and settings[key] is not None:
                current[key] = settings[key]
    current["enabled"] = _as_bool(current.get("enabled"), False)
    current["channel_id"] = str(current.get("channel_id") or "").strip()
    current["channel_username"] = str(current.get("channel_username") or "").strip().lstrip("@")
    current["channel_link"] = str(current.get("channel_link") or "").strip()
    current["guide_text"] = str(current.get("guide_text") or "")

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('force_join_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def toggle_force_join_enabled() -> Dict[str, Any]:
    settings = get_force_join_settings()
    settings["enabled"] = not bool(settings.get("enabled", False))
    return set_force_join_settings(settings)


def set_force_join_channel(target: str, link: str = "") -> Dict[str, Any]:
    raw = str(target or "").strip()
    if not raw:
        raise ValueError("empty channel target")
    settings = get_force_join_settings()
    if raw.startswith("@"):
        settings["channel_username"] = raw.lstrip("@")
        settings["channel_id"] = ""
    elif raw.lstrip("-").isdigit():
        settings["channel_id"] = raw
        settings["channel_username"] = ""
    else:
        raise ValueError("invalid channel target")
    settings["channel_link"] = str(link or "").strip()
    return set_force_join_settings(settings)


def get_payment_settings() -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'payment_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_PAYMENT_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    for key in settings.keys():
                        if key in raw and raw[key] is not None:
                            settings[key] = raw[key]
            except Exception:
                pass
        settings["enable_card_to_card"] = _as_bool(settings.get("enable_card_to_card"), True)
        settings["require_last4_for_card_receipt"] = _as_bool(settings.get("require_last4_for_card_receipt"), False)
        settings["enable_zarinpal"] = _as_bool(settings.get("enable_zarinpal"), False)
        settings["enable_perfect_money"] = _as_bool(settings.get("enable_perfect_money"), False)
        settings["enable_crypto"] = _as_bool(settings.get("enable_crypto"), False)
        settings["event_channel_enabled"] = _as_bool(settings.get("event_channel_enabled"), False)
        settings["event_channel_id"] = str(settings.get("event_channel_id") or "").strip()
        return settings
    finally:
        conn.close()


def set_payment_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(DEFAULT_PAYMENT_SETTINGS)
    if isinstance(settings, dict):
        for key in current.keys():
            if key in settings and settings[key] is not None:
                current[key] = settings[key]
    current["enable_card_to_card"] = _as_bool(current.get("enable_card_to_card"), True)
    current["require_last4_for_card_receipt"] = _as_bool(current.get("require_last4_for_card_receipt"), False)
    current["enable_zarinpal"] = _as_bool(current.get("enable_zarinpal"), False)
    current["enable_perfect_money"] = _as_bool(current.get("enable_perfect_money"), False)
    current["enable_crypto"] = _as_bool(current.get("enable_crypto"), False)
    current["event_channel_enabled"] = _as_bool(current.get("event_channel_enabled"), False)
    current["event_channel_id"] = str(current.get("event_channel_id") or "").strip()

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('payment_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def toggle_payment_setting(name: str) -> Dict[str, Any]:
    if name not in {
        "enable_card_to_card",
        "require_last4_for_card_receipt",
        "enable_zarinpal",
        "enable_perfect_money",
        "enable_crypto",
        "event_channel_enabled",
    }:
        raise ValueError("invalid payment setting name")
    settings = get_payment_settings()
    settings[name] = not bool(settings.get(name))
    return set_payment_settings(settings)


def get_backup_restore_settings() -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'backup_restore_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_BACKUP_RESTORE_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    for key in settings.keys():
                        if key in raw and raw[key] is not None:
                            settings[key] = raw[key]
            except Exception:
                pass
        settings["auto_backup_enabled"] = _as_bool(settings.get("auto_backup_enabled"), True)
        settings["event_channel_enabled"] = _as_bool(settings.get("event_channel_enabled"), False)
        settings["event_channel_id"] = str(settings.get("event_channel_id") or "").strip()
        return settings
    finally:
        conn.close()


def set_backup_restore_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(DEFAULT_BACKUP_RESTORE_SETTINGS)
    if isinstance(settings, dict):
        for key in current.keys():
            if key in settings and settings[key] is not None:
                current[key] = settings[key]
    current["auto_backup_enabled"] = _as_bool(current.get("auto_backup_enabled"), True)
    current["event_channel_enabled"] = _as_bool(current.get("event_channel_enabled"), False)
    current["event_channel_id"] = str(current.get("event_channel_id") or "").strip()

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('backup_restore_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def toggle_backup_restore_setting(name: str) -> Dict[str, Any]:
    if name not in {"auto_backup_enabled", "event_channel_enabled"}:
        raise ValueError("invalid backup/restore setting name")
    settings = get_backup_restore_settings()
    settings[name] = not _as_bool(settings.get(name), False)
    return set_backup_restore_settings(settings)


def claim_setting_slot_once(key: str, slot_value: str) -> bool:
    """
    Atomically claim a one-time slot in userbot_settings.
    Returns True if claimed now, False if this exact slot was already claimed.
    """
    k = str(key or "").strip()
    v = str(slot_value or "").strip()
    if not k or not v:
        return False

    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("SELECT value FROM userbot_settings WHERE key = ? LIMIT 1", (k,))
        row = cur.fetchone()
        if row and str(row["value"] or "").strip() == v:
            conn.commit()
            return False
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (k, v),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_auto_backup_slot_once(slot_value: str) -> bool:
    return claim_setting_slot_once("auto_backup_last_slot", slot_value)


def get_sub_reminder_settings() -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'sub_reminder_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_SUB_REMINDER_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    if "enabled" in raw:
                        settings["enabled"] = bool(raw["enabled"])
                    if "announce_enabled" in raw:
                        settings["announce_enabled"] = bool(raw["announce_enabled"])
                    if "usage_gb" in raw:
                        settings["usage_gb"] = max(0.1, float(raw["usage_gb"]))
                    if "days" in raw:
                        settings["days"] = max(1, int(raw["days"]))
            except Exception:
                pass
        return settings
    finally:
        conn.close()


def set_sub_reminder_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(DEFAULT_SUB_REMINDER_SETTINGS)
    if isinstance(settings, dict):
        if "enabled" in settings:
            current["enabled"] = bool(settings["enabled"])
        if "announce_enabled" in settings:
            current["announce_enabled"] = bool(settings["announce_enabled"])
        if "usage_gb" in settings:
            current["usage_gb"] = max(0.1, float(settings["usage_gb"]))
        if "days" in settings:
            current["days"] = max(1, int(settings["days"]))

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('sub_reminder_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def toggle_sub_reminder_enabled() -> Dict[str, Any]:
    settings = get_sub_reminder_settings()
    settings["enabled"] = not bool(settings.get("enabled"))
    return set_sub_reminder_settings(settings)


def set_sub_reminder_value(name: str, value: int) -> Dict[str, Any]:
    settings = get_sub_reminder_settings()
    if name == "usage_gb":
        settings["usage_gb"] = max(1, int(value))
    elif name == "days":
        settings["days"] = max(1, int(value))
    return set_sub_reminder_settings(settings)


def get_managed_sub_base_url() -> str:
    """
    Base URL for internal multi-server subscription links.
    Empty value means fallback to automatic resolution/env.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'managed_sub_base_url' LIMIT 1")
        row = cur.fetchone()
        if not row or row["value"] is None:
            return ""
        return str(row["value"]).strip().rstrip("/")
    finally:
        conn.close()


def set_managed_sub_base_url(base_url: str) -> str:
    init_db()
    value = str(base_url or "").strip().rstrip("/")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('managed_sub_base_url', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (value,),
        )
        conn.commit()
        return value
    finally:
        conn.close()


def get_trial_spec_settings() -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'trial_spec_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_TRIAL_SPEC_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    if "enabled" in raw:
                        settings["enabled"] = bool(raw["enabled"])
                    if "announce_enabled" in raw:
                        settings["announce_enabled"] = bool(raw["announce_enabled"])
                    if "usage_gb" in raw:
                        settings["usage_gb"] = max(0.1, float(raw["usage_gb"]))
                    if "days" in raw:
                        settings["days"] = max(1, int(raw["days"]))
            except Exception:
                pass
        return settings
    finally:
        conn.close()


def set_trial_spec_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(DEFAULT_TRIAL_SPEC_SETTINGS)
    if isinstance(settings, dict):
        if "enabled" in settings:
            current["enabled"] = bool(settings["enabled"])
        if "announce_enabled" in settings:
            current["announce_enabled"] = bool(settings["announce_enabled"])
        if "usage_gb" in settings:
            current["usage_gb"] = max(0.1, float(settings["usage_gb"]))
        if "days" in settings:
            current["days"] = max(1, int(settings["days"]))

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('trial_spec_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def toggle_trial_spec_enabled() -> Dict[str, Any]:
    settings = get_trial_spec_settings()
    settings["enabled"] = not bool(settings.get("enabled"))
    return set_trial_spec_settings(settings)


def toggle_trial_spec_announce_enabled() -> Dict[str, Any]:
    settings = get_trial_spec_settings()
    settings["announce_enabled"] = not bool(settings.get("announce_enabled", True))
    return set_trial_spec_settings(settings)


def set_trial_spec_value(name: str, value: Any) -> Dict[str, Any]:
    settings = get_trial_spec_settings()
    if name == "usage_gb":
        settings["usage_gb"] = max(0.1, float(value))
    elif name == "days":
        settings["days"] = max(1, int(value))
    return set_trial_spec_settings(settings)


# ======================== تنظیمات توکن ربات نماینده ========================

DEFAULT_AGENT_BOT_SETTINGS = {
    "agent_bot_token": "",
}


def get_agent_bot_settings() -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'agent_bot_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_AGENT_BOT_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    for key in DEFAULT_AGENT_BOT_SETTINGS.keys():
                        if key in raw:
                            settings[key] = str(raw.get(key) or "").strip()
            except Exception:
                pass
        return settings
    finally:
        conn.close()


def set_agent_bot_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(DEFAULT_AGENT_BOT_SETTINGS)
    if isinstance(settings, dict):
        for key in DEFAULT_AGENT_BOT_SETTINGS.keys():
            if key in settings:
                current[key] = str(settings.get(key) or "").strip()

    payload = json.dumps(current, ensure_ascii=False)
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_settings (key, value) VALUES ('agent_bot_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return current


# ======================== توابع جدید پروفایل ========================

def toggle_ban_user(user_id: int) -> int:
    """تغییر وضعیت بن کاربر و برگرداندن وضعیت جدید (1 یا 0)"""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    # وضعیت فعلی را بگیر
    cur.execute("SELECT is_banned FROM userbot_users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    current = row['is_banned'] if row else 0
    new_status = 0 if current else 1
    
    cur.execute("UPDATE userbot_users SET is_banned = ? WHERE id = ?", (new_status, user_id))
    conn.commit()
    conn.close()
    return new_status

def reset_free_trial(user_id: int) -> None:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE userbot_users SET got_free_trial = 0 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def reset_all_free_trials() -> int:
    """
    بازنشانی تست رایگان برای همه کاربران.
    خروجی: تعداد ردیف‌هایی که آپدیت شده‌اند.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE userbot_users SET got_free_trial = 0 WHERE got_free_trial != 0")
    changed = int(cur.rowcount or 0)
    conn.commit()
    conn.close()
    return changed


def set_free_trial_used(user_id: int, used: int = 1) -> None:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE userbot_users SET got_free_trial = ? WHERE id = ?",
        (1 if int(used) else 0, int(user_id)),
    )
    conn.commit()
    conn.close()

def set_user_wallet(user_id: int, new_amount: int) -> None:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE userbot_users SET wallet_balance = ? WHERE id = ?", (new_amount, user_id))
    conn.commit()
    conn.close()

# ==========================================
#     بخش ۱: کاربران
# ==========================================

def upsert_user(telegram_id: int, username: Optional[str], full_name: Optional[str]) -> int:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id FROM userbot_users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

    if row:
        user_id = row["id"]
        cur.execute(
            "UPDATE userbot_users SET username = ?, full_name = ? WHERE id = ?",
            (username, full_name, user_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO userbot_users (telegram_id, username, full_name, created_at, wallet_balance)
            VALUES (?, ?, ?, ?, 0)
            """,
            (telegram_id, username, full_name, now),
        )
        user_id = cur.lastrowid

    conn.commit()
    conn.close()
    return int(user_id)


def get_users_page(page: int, page_size: int) -> Tuple[List[Dict[str, Any]], int]:
    init_db()
    if page < 1: page = 1
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM userbot_users")
    total = int(cur.fetchone()["c"] or 0)
    cur.execute("SELECT * FROM userbot_users ORDER BY id DESC LIMIT ? OFFSET ?", (page_size, offset))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM userbot_users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_telegram_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM userbot_users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def search_users_by_telegram_id(telegram_id: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM userbot_users WHERE telegram_id = ? ORDER BY id DESC", (telegram_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_users_by_name(query: str, limit: int = 50) -> List[Dict[str, Any]]:
    init_db()
    raw = str(query or "").strip()
    for ch in ("\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u2066", "\u2067", "\u2068", "\u2069"):
        raw = raw.replace(ch, "")
    if not raw:
        return []
    normalized = raw.lstrip("@").strip() or raw

    like_raw = f"%{raw}%"
    like_normalized = f"%{normalized}%"
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM userbot_users
        WHERE
            username LIKE ?
            OR full_name LIKE ?
            OR REPLACE(COALESCE(username, ''), '@', '') LIKE ?
            OR REPLACE(COALESCE(full_name, ''), '@', '') LIKE ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (like_raw, like_raw, like_normalized, like_normalized, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_broadcast_users_snapshot() -> List[Dict[str, Any]]:
    """
    Snapshot سبک برای سگمنت‌بندی ارسال همگانی.
    - هر کاربر یک ردیف
    - تعداد سفارشات
    - تعداد سرویس‌ها
    - بیشترین days_left بین سرویس‌های کاربر (برای تشخیص فعال/منقضی)
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            u.id AS user_id,
            u.telegram_id AS telegram_id,
            COALESCE(o.orders_count, 0) AS orders_count,
            COALESCE(s.services_count, 0) AS services_count,
            s.max_days_left AS max_days_left
        FROM userbot_users u
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS orders_count
            FROM userbot_orders
            GROUP BY user_id
        ) o ON o.user_id = u.id
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS services_count, MAX(days_left) AS max_days_left
            FROM userbot_services
            GROUP BY user_id
        ) s ON s.user_id = u.id
        WHERE u.telegram_id IS NOT NULL
        ORDER BY u.id DESC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _broadcast_is_expired_row(row: Dict[str, Any]) -> bool:
    try:
        svc_count = int(row.get("services_count") or 0)
    except Exception:
        svc_count = 0
    if svc_count <= 0:
        return False
    try:
        max_days_left = int(row.get("max_days_left"))
    except Exception:
        return False
    # اگر هیچ سرویس فعالی باقی نمانده باشد، منقضی در نظر می‌گیریم.
    return max_days_left <= 0


def _broadcast_match_segment(row: Dict[str, Any], segment: str) -> bool:
    seg = str(segment or "all").strip().lower()

    if seg == "all":
        return True

    if seg == "no_order":
        try:
            return int(row.get("orders_count") or 0) <= 0
        except Exception:
            return False

    expired = _broadcast_is_expired_row(row)
    if seg == "expired_all":
        return expired

    if seg in {"expired_1w", "expired_2w", "expired_4w", "expired_8w"}:
        if not expired:
            return False
        thresholds = {
            "expired_1w": 7,
            "expired_2w": 14,
            "expired_4w": 28,
            "expired_8w": 56,
        }
        limit_days = thresholds.get(seg, 0)
        try:
            max_days_left = int(row.get("max_days_left"))
        except Exception:
            return False
        return max_days_left <= -limit_days

    return False


def get_broadcast_stats() -> Dict[str, int]:
    rows = _get_broadcast_users_snapshot()

    def _count(seg: str) -> int:
        return sum(1 for r in rows if _broadcast_match_segment(r, seg))

    return {
        "total_users": _count("all"),
        "expired_users": _count("expired_all"),
        "no_order_users": _count("no_order"),
        "expired_1w_users": _count("expired_1w"),
        "expired_2w_users": _count("expired_2w"),
        "expired_4w_users": _count("expired_4w"),
        "expired_8w_users": _count("expired_8w"),
    }


def get_broadcast_target_telegram_ids(segment: str) -> List[int]:
    rows = _get_broadcast_users_snapshot()
    ids: List[int] = []
    seen = set()
    for row in rows:
        if not _broadcast_match_segment(row, segment):
            continue
        try:
            tg_id = int(row.get("telegram_id") or 0)
        except Exception:
            tg_id = 0
        if tg_id <= 0 or tg_id in seen:
            continue
        seen.add(tg_id)
        ids.append(tg_id)
    return ids


def increase_user_wallet(user_id: int, amount: int) -> None:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE userbot_users SET wallet_balance = wallet_balance + ? WHERE id = ?",
        (amount, user_id)
    )
    conn.commit()
    conn.close()


def decrease_user_wallet(user_id: int, amount: int) -> bool:
    """کسر اتمیک از کیف پول — اگر موجودی کافی نبود چیزی کم نمی‌شود و False برمی‌گردد."""
    init_db()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE userbot_users SET wallet_balance = wallet_balance - ? WHERE id = ? AND wallet_balance >= ?",
            (amount, user_id, amount)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_full_user_stats(user_id: int) -> Dict[str, Any]:
    """دریافت آمار کامل کاربر برای پروفایل (رفع ارور)"""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()

    # 1. آمار سرویس‌ها (تعداد کل و متصل)
    cur.execute("SELECT COUNT(*) FROM userbot_services WHERE user_id = ?", (user_id,))
    res_services = cur.fetchone()
    subs_bought = res_services[0] if res_services else 0
    subs_connected = subs_bought  # فعلاً فرض بر برابری

    # 2. آمار تراکنش‌ها (کل و تایید شده)
    cur.execute("SELECT COUNT(*) FROM userbot_payments WHERE user_id = ?", (user_id,))
    res_tx_total = cur.fetchone()
    tx_total = res_tx_total[0] if res_tx_total else 0

    cur.execute("SELECT COUNT(*) FROM userbot_payments WHERE user_id = ? AND status = 'approved'", (user_id,))
    res_tx_approved = cur.fetchone()
    tx_approved = res_tx_approved[0] if res_tx_approved else 0

    # 3. آمار سفارشات (تعداد، حجم کل، مبلغ کل)
    cur.execute(
        """
        SELECT 
            COUNT(*) as cnt, 
            COALESCE(SUM(volume_gb), 0) as gb, 
            COALESCE(SUM(price), 0) as price 
        FROM userbot_orders 
        WHERE user_id = ?
        """, 
        (user_id,)
    )
    orders_stats = cur.fetchone()

    conn.close()

    return {
        "subs_bought": subs_bought,
        "subs_connected": subs_connected,
        "tx_total": tx_total,
        "tx_approved": tx_approved,
        "orders_count": orders_stats['cnt'] if orders_stats else 0,
        "orders_gb": orders_stats['gb'] if orders_stats else 0,
        "orders_price": orders_stats['price'] if orders_stats else 0
    }


# ==========================================
#     بخش ۲: سرویس‌ها
# ==========================================

def get_services_for_user(user_id: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM userbot_services WHERE user_id = ? ORDER BY id DESC", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_service_by_id(service_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM userbot_services WHERE id = ?", (service_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_service_by_code(service_code: str) -> Optional[Dict[str, Any]]:
    """
    پیدا کردن سرویس با شناسه اشتراک ذخیره‌شده در comment به فرم code:XXXX.
    """
    init_db()
    code = str(service_code or "").strip()
    if not code:
        return None
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM userbot_services
            WHERE comment LIKE ?
            ORDER BY id DESC
            LIMIT 200
            """,
            (f"%code:{code}%",),
        )
        rows = cur.fetchall()
        for row in rows:
            comment = str((row["comment"] if "comment" in row.keys() else "") or "")
            for part in comment.split("|"):
                if ":" not in part:
                    continue
                k, v = part.split(":", 1)
                if k.strip().lower() == "code" and v.strip() == code:
                    return dict(row)
        return None
    finally:
        conn.close()


def get_service_by_panel_uuid(panel_user_uuid: str) -> Optional[Dict[str, Any]]:
    init_db()
    uuid = str(panel_user_uuid or "").strip()
    if not uuid:
        return None
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT s.* FROM userbot_services s JOIN userbot_service_nodes n ON n.service_id = s.id WHERE n.panel_user_uuid = ? LIMIT 1",
            (uuid,),
        )
        row = cur.fetchone()
        if row:
            return dict(row)
        cur.execute(
            "SELECT s.* FROM userbot_services s WHERE s.comment LIKE ? LIMIT 1",
            (f"%uuid:{uuid}%",),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_admin_service(
    panel_user_uuid: str,
    name: str,
    server_id: int,
    server_title: str,
    usage_limit: int,
    days: int,
) -> Optional[int]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        comment = f"uuid:{panel_user_uuid}|admin:1"
        cur.execute(
            "INSERT INTO userbot_services (user_id, name, server_id, server_title, usage_current, usage_limit, days_left, comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (0, name, server_id, server_title, 0.0, float(usage_limit), days, comment),
        )
        service_id = int(cur.lastrowid)
        conn.commit()
        return service_id
    except Exception:
        return None
    finally:
        conn.close()


def fix_admin_services_missing_source_mapping() -> int:
    """
    Migration: برای سرویس‌های ادمینی که فقط mapping نود دارن و mapping سرور اصلی ندارن،
    سرور اصلی (parent) رو به عنوان node mapping اضافه کن.
    تعداد fix های انجام شده رو برمیگرداند.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    fixed = 0
    try:
        # build child_id -> parent_ids map
        child_to_parents: Dict[int, list[int]] = {}
        try:
            from Shared import database
            servers = database.get_servers() or []
            for s in servers:
                pid = int(s.get("id") or 0)
                if pid <= 0:
                    continue
                for n in (s.get("nodes") or []):
                    if not isinstance(n, dict):
                        continue
                    cid = int(n.get("target_server_id") or 0)
                    if cid > 0:
                        child_to_parents.setdefault(cid, []).append(pid)
        except Exception:
            pass

        # پیدا کردن سرویس‌های ادمین (user_id=0) که service_node دارن
        cur.execute("""
            SELECT s.id, s.server_id, s.server_title, s.comment
            FROM userbot_services s
            WHERE s.user_id = 0
              AND EXISTS (
                  SELECT 1 FROM userbot_service_nodes n WHERE n.service_id = s.id
              )
        """)
        services = cur.fetchall()

        for svc in services:
            svc_id = int(svc["id"])
            comment = str(svc["comment"] or "")
            uuid = ""
            for part in comment.split("|"):
                if part.startswith("uuid:"):
                    uuid = part[5:].strip()
                    break
            if not uuid:
                continue

            # همه server_id هایی که از قبل mapping دارن
            cur.execute(
                "SELECT DISTINCT server_id FROM userbot_service_nodes WHERE service_id = ?",
                (svc_id,),
            )
            mapped_ids = {int(r["server_id"]) for r in cur.fetchall() if r["server_id"]}

            # برای هر mapped server_id، parent هاش رو پیدا کن و اضافه کن
            now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
            for mapped_sid in list(mapped_ids):
                for parent_sid in child_to_parents.get(mapped_sid, []):
                    if parent_sid in mapped_ids:
                        continue
                    server_title = ""
                    try:
                        for s in servers:
                            if int(s.get("id") or 0) == parent_sid:
                                server_title = str(s.get("title") or f"سرور #{parent_sid}")
                                break
                    except Exception:
                        server_title = f"سرور #{parent_sid}"
                    cur.execute("""
                        INSERT INTO userbot_service_nodes
                        (service_id, server_id, server_title, panel_user_uuid, panel_user_id, marzban_username, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(service_id, server_id, panel_user_uuid)
                        DO UPDATE SET is_active = excluded.is_active, updated_at = excluded.updated_at
                    """, (svc_id, parent_sid, server_title, uuid, None, "", 1, now, now))
                    mapped_ids.add(parent_sid)
                    fixed += 1

        if fixed > 0:
            conn.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("fix_admin_services failed: %s", e)
    finally:
        conn.close()
    return fixed


def get_service_owner_by_panel_uuid(panel_user_uuid: str) -> Optional[Dict[str, Any]]:
    """
    پیدا کردن مالک فعلی یک UUID در دیتابیس ربات کاربران.
    برای جلوگیری از اتصال یک اشتراک به چند کاربر.
    """
    init_db()
    uuid = str(panel_user_uuid or "").strip()
    if not uuid:
        return None
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT s.id AS service_id, s.user_id, s.name AS service_name,
                   u.telegram_id, u.username, u.full_name
            FROM userbot_service_nodes n
            JOIN userbot_services s ON s.id = n.service_id
            LEFT JOIN userbot_users u ON u.id = s.user_id
            WHERE n.panel_user_uuid = ? OR n.panel_user_id = ?
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (uuid, uuid),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

        # fallback برای سرویس‌های قدیمی که نگاشت نود ندارند و UUID در comment ثبت شده
        cur.execute(
            """
            SELECT s.id AS service_id, s.user_id, s.name AS service_name,
                   u.telegram_id, u.username, u.full_name
            FROM userbot_services s
            LEFT JOIN userbot_users u ON u.id = s.user_id
            WHERE s.comment LIKE ?
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (f"%uuid:{uuid}%",),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_service_by_panel_uuid(user_id: int, panel_user_uuid: str) -> Optional[Dict[str, Any]]:
    """
    پیدا کردن سرویس یک کاربر مشخص با UUID پنل.
    """
    init_db()
    uid = int(user_id)
    uuid = str(panel_user_uuid or "").strip()
    if uid <= 0 or not uuid:
        return None
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT s.*
            FROM userbot_service_nodes n
            JOIN userbot_services s ON s.id = n.service_id
            WHERE s.user_id = ?
              AND (n.panel_user_uuid = ? OR n.panel_user_id = ?)
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (uid, uuid, uuid),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

        cur.execute(
            """
            SELECT *
            FROM userbot_services
            WHERE user_id = ?
              AND comment LIKE ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (uid, f"%uuid:{uuid}%"),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_service(service_id: int) -> None:
    """
    حذف کامل سرویس از دیتابیس محلی (به همراه نگاشت نودها و توکن ساب).
    """
    init_db()
    sid = int(service_id)
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM userbot_service_nodes WHERE service_id = ?", (sid,))
        cur.execute("DELETE FROM userbot_sub_tokens WHERE service_id = ?", (sid,))
        cur.execute("DELETE FROM userbot_service_probe WHERE service_id = ?", (sid,))
        cur.execute("DELETE FROM userbot_service_reminder_state WHERE service_id = ?", (sid,))
        cur.execute("DELETE FROM userbot_services WHERE id = ?", (sid,))
        conn.commit()
    finally:
        conn.close()


def delete_services_by_panel_user(server_id: int, panel_user_uuid: str) -> int:
    """
    حذف سرویس(های) مربوط به یک کاربر پنل روی سرور مشخص از دیتابیس ربات کاربران.
    خروجی: تعداد سرویس‌های حذف‌شده.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    sid = int(server_id)
    uuid = str(panel_user_uuid or "").strip()
    if sid <= 0 or not uuid:
        conn.close()
        return 0

    try:
        service_ids: set[int] = set()

        # مسیر جدید: نگاشت سرویس-نود
        cur.execute(
            """
            SELECT service_id
            FROM userbot_service_nodes
            WHERE server_id = ?
              AND (panel_user_uuid = ? OR panel_user_id = ?)
            """,
            (sid, uuid, uuid),
        )
        for r in cur.fetchall():
            try:
                service_ids.add(int(r["service_id"]))
            except Exception:
                pass

        # مسیر قدیمی: UUID داخل comment
        cur.execute(
            """
            SELECT id
            FROM userbot_services
            WHERE server_id = ?
              AND comment LIKE ?
            """,
            (sid, f"%uuid:{uuid}%"),
        )
        for r in cur.fetchall():
            try:
                service_ids.add(int(r["id"]))
            except Exception:
                pass
    finally:
        conn.close()

    removed = 0
    for service_id in service_ids:
        try:
            delete_service(service_id)
            removed += 1
        except Exception:
            pass
    return removed


def delete_services_by_server(server_id: int) -> int:
    """
    حذف کامل سرویس‌های وابسته به یک سرور:
    - سرویس‌هایی که server_id اصلی‌شان برابر باشد
    - سرویس‌هایی که در نگاشت نودها (userbot_service_nodes) به این server_id وصل‌اند
    خروجی: تعداد سرویس‌های حذف‌شده.
    """
    init_db()
    sid = int(server_id or 0)
    if sid <= 0:
        return 0

    conn = _get_conn()
    cur = conn.cursor()
    try:
        service_ids: set[int] = set()

        cur.execute(
            "SELECT id FROM userbot_services WHERE server_id = ?",
            (sid,),
        )
        for r in cur.fetchall():
            try:
                service_ids.add(int(r["id"]))
            except Exception:
                pass

        cur.execute(
            "SELECT DISTINCT service_id FROM userbot_service_nodes WHERE server_id = ?",
            (sid,),
        )
        for r in cur.fetchall():
            try:
                service_ids.add(int(r["service_id"]))
            except Exception:
                pass
    finally:
        conn.close()

    removed = 0
    for service_id in service_ids:
        try:
            delete_service(service_id)
            removed += 1
        except Exception:
            pass
    return removed


def mark_service_seen(service_id: int) -> None:
    init_db()
    sid = int(service_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_service_probe (service_id, missing_streak, first_missing_at, last_seen_at, last_missing_at, updated_at)
            VALUES (?, 0, NULL, ?, NULL, ?)
            ON CONFLICT(service_id) DO UPDATE SET
                missing_streak = 0,
                first_missing_at = NULL,
                last_seen_at = excluded.last_seen_at,
                last_missing_at = NULL,
                updated_at = excluded.updated_at
            """,
            (sid, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def mark_service_missing(service_id: int) -> Dict[str, Any]:
    init_db()
    sid = int(service_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT missing_streak, first_missing_at FROM userbot_service_probe WHERE service_id = ? LIMIT 1",
            (sid,),
        )
        row = cur.fetchone()
        streak = int((row["missing_streak"] if row else 0) or 0) + 1
        first_missing_at = str((row["first_missing_at"] if row else "") or "").strip() or now
        cur.execute(
            """
            INSERT INTO userbot_service_probe (service_id, missing_streak, first_missing_at, last_missing_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(service_id) DO UPDATE SET
                missing_streak = excluded.missing_streak,
                first_missing_at = COALESCE(userbot_service_probe.first_missing_at, excluded.first_missing_at),
                last_missing_at = excluded.last_missing_at,
                updated_at = excluded.updated_at
            """,
            (sid, streak, first_missing_at, now, now),
        )
        conn.commit()
        return {
            "missing_streak": streak,
            "first_missing_at": first_missing_at,
            "last_missing_at": now,
        }
    finally:
        conn.close()


def get_last_order_price_for_service(user_id: int, service_name: str) -> Optional[int]:
    """
    آخرین قیمت ثبت‌شده سفارش برای این سرویس کاربر.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT price
            FROM userbot_orders
            WHERE user_id = ? AND plan_title = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(user_id), str(service_name)),
        )
        row = cur.fetchone()
        if not row:
            return None
        try:
            return int(row["price"])
        except Exception:
            return None
    finally:
        conn.close()


def add_service_node(
    service_id: int,
    server_id: int,
    panel_user_uuid: str,
    server_title: str = "",
    panel_user_id: Optional[str] = None,
    is_active: int = 1,
    marzban_username: str = "",
) -> None:
    init_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_service_nodes
            (service_id, server_id, server_title, panel_user_uuid, panel_user_id, marzban_username, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(service_id, server_id, panel_user_uuid)
            DO UPDATE SET
                server_title=excluded.server_title,
                panel_user_id=excluded.panel_user_id,
                marzban_username=CASE WHEN excluded.marzban_username != '' THEN excluded.marzban_username ELSE userbot_service_nodes.marzban_username END,
                is_active=excluded.is_active,
                updated_at=excluded.updated_at
            """,
            (
                int(service_id),
                int(server_id),
                server_title or "",
                str(panel_user_uuid).strip(),
                (str(panel_user_id).strip() if panel_user_id is not None else None),
                str(marzban_username or "").strip(),
                int(bool(is_active)),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_service_nodes(service_id: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM userbot_service_nodes
            WHERE service_id = ?
            ORDER BY id ASC
            """,
            (int(service_id),),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_services_for_enforcement() -> List[Dict[str, Any]]:
    """
    لیست سرویس‌های فعال برای جمع مصرف و قطع سراسری.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT s.*
            FROM userbot_services s
            WHERE (
                EXISTS (
                    SELECT 1
                    FROM userbot_service_nodes n
                    WHERE n.service_id = s.id
                )
                OR (
                    COALESCE(s.usage_limit, 0) > 0
                    AND COALESCE(s.usage_current, 0) >= COALESCE(s.usage_limit, 0)
                )
                OR COALESCE(s.days_left, 0) < 0
            )
            ORDER BY
                CASE
                    WHEN COALESCE(s.usage_limit, 0) > 0
                         AND COALESCE(s.usage_current, 0) >= COALESCE(s.usage_limit, 0)
                    THEN 0
                    WHEN COALESCE(s.days_left, 0) < 0
                    THEN 1
                    ELSE 2
                END,
                s.id DESC
            """
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_services_for_reminder() -> List[Dict[str, Any]]:
    """
    سرویس‌ها به همراه شناسه تلگرام کاربر، برای ارسال یادآور تمدید.
    """
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT s.*, u.telegram_id, u.username, u.full_name
            FROM userbot_services s
            JOIN userbot_users u ON u.id = s.user_id
            WHERE u.telegram_id IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM userbot_service_nodes n
                WHERE n.service_id = s.id
                  AND (COALESCE(n.is_active, 1) = 1
                       OR (s.days_left IS NOT NULL AND s.days_left <= 0)
                       OR (s.usage_limit IS NOT NULL
                           AND s.usage_limit > 0
                           AND s.usage_current IS NOT NULL
                           AND s.usage_current >= s.usage_limit))
              )
            ORDER BY s.id DESC
            """
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_service_reminder_state(service_id: int) -> Dict[str, Any]:
    init_db()
    sid = int(service_id or 0)
    if sid <= 0:
        return {"days_sent": -1, "usage_sent": -1, "expired_sent": 0}
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT days_sent, usage_sent, expired_sent
            FROM userbot_service_reminder_state
            WHERE service_id = ?
            LIMIT 1
            """,
            (sid,),
        )
        row = cur.fetchone()
        if not row:
            return {"days_sent": -1, "usage_sent": -1, "expired_sent": 0}
        return {
            "days_sent": int(row["days_sent"] if row["days_sent"] is not None else -1),
            "usage_sent": int(row["usage_sent"] if row["usage_sent"] is not None else -1),
            "expired_sent": int(row["expired_sent"] if row["expired_sent"] is not None else 0),
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
    init_db()
    sid = int(service_id or 0)
    if sid <= 0:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

    current = get_service_reminder_state(sid)
    d_val = int(days_sent) if days_sent is not None else int(current.get("days_sent", -1))
    u_val = int(usage_sent) if usage_sent is not None else int(current.get("usage_sent", -1))
    e_val = int(expired_sent) if expired_sent is not None else int(current.get("expired_sent", 0))

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_service_reminder_state (service_id, days_sent, usage_sent, expired_sent, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(service_id) DO UPDATE SET
                days_sent = excluded.days_sent,
                usage_sent = excluded.usage_sent,
                expired_sent = excluded.expired_sent,
                updated_at = excluded.updated_at
            """,
            (sid, d_val, u_val, e_val, now),
        )
        conn.commit()
    finally:
        conn.close()


def update_service_runtime(
    service_id: int,
    usage_current: Optional[float] = None,
    usage_limit: Optional[float] = None,
    days_left: Optional[int] = None,
    last_online: Optional[str] = None,
) -> None:
    init_db()
    parts: List[str] = []
    params: List[Any] = []
    if usage_current is not None:
        parts.append("usage_current = ?")
        params.append(float(usage_current))
    if usage_limit is not None:
        parts.append("usage_limit = ?")
        params.append(float(usage_limit))
    if days_left is not None:
        parts.append("days_left = ?")
        params.append(int(days_left))
    if last_online is not None:
        parts.append("last_online = ?")
        params.append(str(last_online))
    if not parts:
        return

    params.append(int(service_id))
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE userbot_services SET {', '.join(parts)} WHERE id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def update_service_name(service_id: int, new_name: str) -> bool:
    init_db()
    sid = int(service_id or 0)
    name = str(new_name or "").strip()
    if sid <= 0 or not name:
        return False

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE userbot_services SET name = ? WHERE id = ?",
            (name, sid),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_service_comment(service_id: int, new_comment: str) -> bool:
    init_db()
    sid = int(service_id or 0)
    comment = str(new_comment or "").strip()
    if sid <= 0:
        return False

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE userbot_services SET comment = ? WHERE id = ?",
            (comment, sid),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_service_node_uuid(
    service_id: int,
    server_id: int,
    old_uuid: str,
    new_uuid: str,
) -> int:
    init_db()
    sid = int(service_id or 0)
    srv_id = int(server_id or 0)
    old_uuid_val = str(old_uuid or "").strip()
    new_uuid_val = str(new_uuid or "").strip()
    if sid <= 0 or srv_id <= 0 or not old_uuid_val or not new_uuid_val:
        return 0

    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_service_nodes
            SET
                panel_user_uuid = ?,
                panel_user_id = CASE
                    WHEN panel_user_id IS NULL OR TRIM(panel_user_id) = '' OR panel_user_id = ?
                    THEN ?
                    ELSE panel_user_id
                END,
                updated_at = ?
            WHERE service_id = ?
              AND server_id = ?
              AND (panel_user_uuid = ? OR panel_user_id = ?)
            """,
            (new_uuid_val, old_uuid_val, new_uuid_val, now, sid, srv_id, old_uuid_val, old_uuid_val),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def update_service_note_by_panel_user(
    server_id: int,
    panel_user_uuid: str,
    note_text: str,
) -> int:
    """
    Sync note text into local userbot_services.comment for all matched services.
    Match paths:
    - user_uuid column
    - legacy comment field containing uuid:...
    Returns number of updated rows.
    """
    init_db()
    sid = int(server_id or 0)
    uuid = str(panel_user_uuid or "").strip()
    if sid <= 0 or not uuid:
        return 0

    note = str(note_text or "").replace("|", " ").strip()

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, comment
            FROM userbot_services
            WHERE server_id = ?
              AND (
                user_uuid = ?
                OR comment LIKE ?
              )
            """,
            (sid, uuid, f"%uuid:{uuid}%"),
        )
        rows = cur.fetchall()
        updated = 0

        for r in rows:
            service_id = int(r["id"])
            old_comment = str((r["comment"] if "comment" in r.keys() else "") or "").strip()
            parts = [p.strip() for p in old_comment.split("|") if p.strip()]
            kept: List[str] = []
            for part in parts:
                if ":" in part:
                    k = part.split(":", 1)[0].strip().lower()
                    if k in {"note", "memo", "desc", "comment"}:
                        continue
                kept.append(part)

            if note:
                kept.append(f"note:{note}")

            new_comment = "|".join(kept)
            if new_comment == old_comment:
                continue

            cur.execute(
                "UPDATE userbot_services SET comment = ? WHERE id = ?",
                (new_comment, service_id),
            )
            updated += 1

        if updated:
            conn.commit()
        return updated
    finally:
        conn.close()


def set_service_nodes_active(service_id: int, is_active: int) -> None:
    init_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_service_nodes
            SET is_active = ?, updated_at = ?
            WHERE service_id = ?
            """,
            (int(bool(is_active)), now, int(service_id)),
        )
        conn.commit()
    finally:
        conn.close()


def set_service_node_active(
    service_id: int,
    server_id: int,
    panel_user_uuid: str,
    is_active: int,
) -> None:
    init_db()
    sid = int(service_id or 0)
    srv = int(server_id or 0)
    uuid = str(panel_user_uuid or "").strip()
    if sid <= 0 or srv <= 0 or not uuid:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_service_nodes
            SET is_active = ?, updated_at = ?
            WHERE service_id = ?
              AND server_id = ?
              AND (panel_user_uuid = ? OR panel_user_id = ?)
            """,
            (int(bool(is_active)), now, sid, srv, uuid, uuid),
        )
        conn.commit()
    finally:
        conn.close()


def update_service_node_runtime(
    service_id: int,
    server_id: int,
    panel_user_uuid: str,
    *,
    usage_current: Optional[float] = None,
    days_left: Optional[int] = None,
    frozen: Optional[int] = None,
    fail_count: Optional[int] = None,
    last_ok_at: Optional[str] = None,
) -> None:
    """بروزرسانی وضعیت زمان‌بندی‌شدهٔ یک نود (مصرف/روز/یخ‌زدگی/خطا).
    فقط فیلدهایی که مقدار دارند آپدیت می‌شوند."""
    sid = int(service_id or 0)
    srv = int(server_id or 0)
    uuid = str(panel_user_uuid or "").strip()
    if sid <= 0 or srv <= 0 or not uuid:
        return
    parts: List[str] = []
    params: List[Any] = []
    if usage_current is not None:
        parts.append("usage_current = ?")
        params.append(float(usage_current))
    if days_left is not None:
        parts.append("days_left = ?")
        params.append(int(days_left))
    if frozen is not None:
        parts.append("frozen = ?")
        params.append(int(bool(frozen)))
    if fail_count is not None:
        parts.append("fail_count = ?")
        params.append(int(fail_count))
    if last_ok_at is not None:
        parts.append("last_ok_at = ?")
        params.append(str(last_ok_at))
    if not parts:
        return
    params.extend([sid, srv, uuid])
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE userbot_service_nodes SET {', '.join(parts)} "
            f"WHERE service_id = ? AND server_id = ? AND panel_user_uuid = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def set_service_nodes_active_except_deleted(service_id: int, is_active: int) -> None:
    """مثل set_service_nodes_active ولی نودهای حذف‌شده (deleted=1) را دست‌نخورده می‌گذارد."""
    sid = int(service_id or 0)
    if sid <= 0:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_service_nodes
            SET is_active = ?, updated_at = ?
            WHERE service_id = ? AND COALESCE(deleted, 0) = 0
            """,
            (int(bool(is_active)), now, sid),
        )
        conn.commit()
    finally:
        conn.close()


def hold_deleted_server_nodes(server_id: int) -> List[int]:
    """هنگام حذف یک سرور نود: حجم مصرف کاربران روی آن نود را فریز می‌کند
    (deleted=1, frozen=1, is_active=0) و تا زمان تمدید نگه می‌دارد.
    خروجی: لیست service_idهایی که روی این سرور نود داشتند."""
    srv = int(server_id or 0)
    if srv <= 0:
        return []
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_service_nodes
            SET deleted = 1, frozen = 1, is_active = 0, updated_at = ?
            WHERE server_id = ? AND COALESCE(deleted, 0) = 0
            """,
            (now, srv),
        )
        cur.execute(
            "SELECT DISTINCT service_id FROM userbot_service_nodes WHERE server_id = ? AND deleted = 1",
            (srv,),
        )
        rows = cur.fetchall()
        conn.commit()
        return [int(r["service_id"]) for r in rows]
    finally:
        conn.close()


def reset_service_nodes_on_renew(service_id: int) -> None:
    """در زمان تمدید/ریست اشتراک: رکوردهای نود حذف‌شده (شبح) پاک می‌شوند
    و حجم بقیه نودها صفر و یخ‌زدایی می‌شود (شروع دورهٔ جدید)."""
    sid = int(service_id or 0)
    if sid <= 0:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM userbot_service_nodes WHERE service_id = ? AND COALESCE(deleted, 0) = 1",
            (sid,),
        )
        cur.execute(
            """
            UPDATE userbot_service_nodes
            SET usage_current = 0, days_left = NULL, frozen = 0, fail_count = 0,
                last_ok_at = NULL, is_active = 1, updated_at = ?
            WHERE service_id = ?
            """,
            (now, sid),
        )
        # ریست کامل حجم سرویس (حتی اگر همه نودها حذف‌شده بودند و انفورسر دیگر
        # نمی‌توانست آن را بازخوانی کند). days_left هم NULL تا انفورسر از پنل سینک کند.
        cur.execute(
            "UPDATE userbot_services SET usage_current = 0, days_left = NULL WHERE id = ?",
            (sid,),
        )
        conn.commit()
    finally:
        conn.close()


def get_frozen_nodes_summary() -> Dict[str, int]:
    """خلاصه تعداد نودهای یخ‌زده/حذف‌شده برای داشبورد ادمین."""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM userbot_service_nodes WHERE COALESCE(frozen,0)=1 AND COALESCE(deleted,0)=0"
        )
        frozen = int((cur.fetchone() or [0])[0] or 0)
        cur.execute("SELECT COUNT(*) FROM userbot_service_nodes WHERE COALESCE(deleted,0)=1")
        deleted = int((cur.fetchone() or [0])[0] or 0)
        cur.execute(
            "SELECT COUNT(DISTINCT service_id) FROM userbot_service_nodes WHERE COALESCE(frozen,0)=1 AND COALESCE(deleted,0)=0"
        )
        frozen_services = int((cur.fetchone() or [0])[0] or 0)
        return {"frozen_nodes": frozen, "deleted_nodes": deleted, "frozen_services": frozen_services}
    except Exception:
        return {"frozen_nodes": 0, "deleted_nodes": 0, "frozen_services": 0}
    finally:
        conn.close()


def ensure_service_sub_token(service_id: int) -> str:
    init_db()
    sid = int(service_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    token = f"s{sid}_{random.randint(100000, 999999)}_{random.randint(100000, 999999)}"

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT token FROM userbot_sub_tokens WHERE service_id = ? LIMIT 1", (sid,))
        row = cur.fetchone()
        if row and row["token"]:
            cur.execute(
                "UPDATE userbot_sub_tokens SET updated_at = ? WHERE service_id = ?",
                (now, sid),
            )
            conn.commit()
            return str(row["token"])

        cur.execute(
            """
            INSERT INTO userbot_sub_tokens (service_id, token, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (sid, token, now, now),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def get_service_id_by_sub_token(token: str) -> Optional[int]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT service_id FROM userbot_sub_tokens WHERE token = ? LIMIT 1", (str(token),))
        row = cur.fetchone()
        if not row:
            return None
        return int(row["service_id"])
    finally:
        conn.close()
 


# ==========================================
#     بخش ۳: سفارشات
# ==========================================

def get_orders_stats() -> dict:
    """آمار کلی سفارشات (برای لیست اصلی)"""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(volume_gb), 0) AS gb, COALESCE(SUM(price), 0) AS p FROM userbot_orders")
    total = cur.fetchone()

    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    last30 = (today - timedelta(days=30)).isoformat()
    month_start = today.replace(day=1).isoformat()

    cur.execute(f"SELECT COUNT(*) AS c, COALESCE(SUM(volume_gb), 0) AS gb, COALESCE(SUM(price), 0) AS p FROM userbot_orders WHERE date(created_at) >= date(?)", (last30,))
    l30 = cur.fetchone()

    cur.execute(f"SELECT COUNT(*) AS c, COALESCE(SUM(volume_gb), 0) AS gb, COALESCE(SUM(price), 0) AS p FROM userbot_orders WHERE date(created_at) >= date(?)", (month_start,))
    mon = cur.fetchone()

    conn.close()
    return {
        "total_count": total['c'], "total_gb": total['gb'], "total_price": total['p'],
        "last30_count": l30['c'], "last30_gb": l30['gb'], "last30_price": l30['p'],
        "month_count": mon['c'], "month_gb": mon['gb'], "month_price": mon['p'],
    }


def get_user_orders_stats(user_id: int) -> dict:
    """آمار ساده سفارشات یک کاربر"""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(price), 0) AS p FROM userbot_orders WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return {"total_count": row['c'], "total_price": row['p']}


def get_user_orders_stats_full(user_id: int) -> Dict[str, Any]:
    """آمار کامل سفارشات کاربر (کل، 30 روز، ماه جاری)"""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    
    base_where = "WHERE user_id = ?"
    params = [user_id]

    # کل
    cur.execute(f"SELECT COUNT(*), COALESCE(SUM(volume_gb),0), COALESCE(SUM(price),0) FROM userbot_orders {base_where}", params)
    total = cur.fetchone()

    # 30 روز
    cur.execute(f"SELECT COUNT(*), COALESCE(SUM(volume_gb),0), COALESCE(SUM(price),0) FROM userbot_orders {base_where} AND date(created_at) >= date('now', '-30 days')", params)
    last30 = cur.fetchone()

    # ماه جاری
    cur.execute(f"SELECT COUNT(*), COALESCE(SUM(volume_gb),0), COALESCE(SUM(price),0) FROM userbot_orders {base_where} AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')", params)
    month = cur.fetchone()

    conn.close()
    return {
        "total_count": total[0] if total else 0,
        "total_gb": total[1] if total else 0,
        "total_price": total[2] if total else 0,
        "last30_count": last30[0] if last30 else 0,
        "last30_gb": last30[1] if last30 else 0,
        "last30_price": last30[2] if last30 else 0,
        "month_count": month[0] if month else 0,
        "month_gb": month[1] if month else 0,
        "month_price": month[2] if month else 0,
    }


def get_orders_page(page: int, page_size: int) -> Tuple[List[Dict[str, Any]], int]:
    init_db()
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM userbot_orders")
    total = int(cur.fetchone()["c"] or 0)
    cur.execute("SELECT * FROM userbot_orders ORDER BY created_at DESC LIMIT ? OFFSET ?", (page_size, offset))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def get_user_orders_paginated(user_id: int, page: int = 1, page_size: int = 21) -> List[Dict[str, Any]]:
    """لیست سفارشات کاربر برای دکمه‌ها"""
    init_db()
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM userbot_orders WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?", (user_id, page_size, offset))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_order_by_id(order_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM userbot_orders WHERE order_id = ?", (order_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# ==========================================
#     بخش ۴: تراکنش‌ها
# ==========================================

def get_payment_stats(status: str = None, method: str = None) -> Dict[str, Any]:
    """محاسبه آمار تراکنش‌ها برای لیست اصلی"""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()

    base_where = "WHERE 1=1"
    params = []
    if status:
        base_where += " AND status = ?"
        params.append(status)
    if method:
        base_where += " AND method = ?"
        params.append(method)

    # کل
    cur.execute(f"SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total FROM userbot_payments {base_where}", params)
    total_row = cur.fetchone()

    # 30 روز
    where_30 = base_where + " AND date(created_at) >= date('now', '-30 days')"
    cur.execute(f"SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total FROM userbot_payments {where_30}", params)
    last30_row = cur.fetchone()

    # ماه جاری
    where_month = base_where + " AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
    cur.execute(f"SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total FROM userbot_payments {where_month}", params)
    month_row = cur.fetchone()

    conn.close()
    return {
        "total_count": total_row['cnt'] or 0,
        "total_amount": total_row['total'] or 0,
        "last30_count": last30_row['cnt'] or 0,
        "last30_amount": last30_row['total'] or 0,
        "month_count": month_row['cnt'] or 0,
        "month_amount": month_row['total'] or 0,
    }


def get_user_payments_stats(user_id: int) -> Dict[str, Any]:
    """آمار تراکنش‌های یک کاربر خاص"""
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    
    base_where = "WHERE user_id = ?"
    params = [user_id]

    cur.execute(f"SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM userbot_payments {base_where}", params)
    total = cur.fetchone()

    cur.execute(f"SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM userbot_payments {base_where} AND date(created_at) >= date('now', '-30 days')", params)
    last30 = cur.fetchone()

    cur.execute(f"SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM userbot_payments {base_where} AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')", params)
    month = cur.fetchone()

    conn.close()
    return {
        "total_count": total[0] if total else 0,
        "total_amount": total[1] if total else 0,
        "last30_count": last30[0] if last30 else 0,
        "last30_amount": last30[1] if last30 else 0,
        "month_count": month[0] if month else 0,
        "month_amount": month[1] if month else 0
    }


def get_payments_list_paginated(status: str = None, method: str = None, page: int = 1, page_size: int = 21) -> List[Dict[str, Any]]:
    init_db()
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


def get_user_payments_paginated(user_id: int, page: int = 1, page_size: int = 21) -> List[Dict[str, Any]]:
    """لیست تراکنش‌های کاربر برای دکمه‌ها"""
    init_db()
    offset = (page - 1) * page_size
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM userbot_payments WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?", (user_id, page_size, offset))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_payment_by_id(payment_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.*, u.username, u.full_name, u.telegram_id 
        FROM userbot_payments p 
        LEFT JOIN userbot_users u ON p.user_id = u.id 
        WHERE p.id = ?
        """, 
        (payment_id,)
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_payment_status(payment_id: int, new_status: str) -> None:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "UPDATE userbot_payments SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, now, payment_id)
    )
    conn.commit()
    conn.close()


def change_payment_status_with_wallet(payment_id: int, new_status: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Change payment status and keep wallet consistent.
    Returns: (ok, message, updated_payment_row)
    """
    init_db()
    target = (new_status or "").strip().lower()
    if target not in {"pending", "approved", "rejected"}:
        return False, "وضعیت مقصد نامعتبر است.", None

    conn = _get_conn()
    cur = conn.cursor()
    try:
        # تراکنش اتمیک: از خواندن وضعیت تا آپدیت کیف پول/وضعیت، هیچ نویسنده
        # دیگری (وب‌هوک SMS، ادمین دیگر) نمی‌تواند بین این‌ها دخالت کند — ضد شارژ دوباره
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT p.*, u.wallet_balance, u.username, u.full_name, u.telegram_id
            FROM userbot_payments p
            LEFT JOIN userbot_users u ON p.user_id = u.id
            WHERE p.id = ?
            LIMIT 1
            """,
            (payment_id,),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return False, "تراکنش یافت نشد.", None

        pay = dict(row)
        old_status = (pay.get("status") or "pending").strip().lower()
        amount = int(pay.get("amount") or 0)
        user_id = pay.get("user_id")
        wallet_balance = int(pay.get("wallet_balance") or 0)
        receipt_raw = str(pay.get("receipt_image") or "")
        is_direct_buy = False
        if "|" in receipt_raw and ":" in receipt_raw:
            try:
                meta = {}
                for part in receipt_raw.split("|"):
                    part = part.strip()
                    if ":" not in part:
                        continue
                    k, v = part.split(":", 1)
                    k = str(k).strip()
                    v = str(v).strip()
                    if k and v:
                        meta[k] = v
                is_direct_buy = str(meta.get("pay_flow") or "").strip().lower() == "direct_buy"
            except Exception:
                is_direct_buy = False

        if old_status == target:
            conn.rollback()
            return True, "وضعیت تراکنش تغییری نکرد.", pay

        now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

        # Wallet delta rules:
        # - non-approved -> approved: +amount
        # - approved -> non-approved: -amount (must have enough balance)
        # آپدیت وضعیت همیشه با گارد `AND status = old_status` — اگر فراخوانی
        # موازی قبلاً وضعیت را عوض کرده باشد، rowcount صفر است و rollback می‌شود
        if is_direct_buy:
            cur.execute(
                "UPDATE userbot_payments SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (target, now, payment_id, old_status),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return False, "وضعیت تراکنش همزمان تغییر کرد؛ دوباره تلاش کنید.", None
        elif old_status != "approved" and target == "approved":
            if user_id and amount > 0:
                cur.execute(
                    "UPDATE userbot_users SET wallet_balance = wallet_balance + ? WHERE id = ?",
                    (amount, user_id),
                )
            cur.execute(
                "UPDATE userbot_payments SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (target, now, payment_id, old_status),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return False, "وضعیت تراکنش همزمان تغییر کرد؛ دوباره تلاش کنید.", None
        elif old_status == "approved" and target != "approved":
            if user_id and amount > 0:
                cur.execute(
                    "UPDATE userbot_users SET wallet_balance = wallet_balance - ? WHERE id = ? AND wallet_balance >= ?",
                    (amount, user_id, amount),
                )
                if cur.rowcount == 0:
                    conn.rollback()
                    return False, "موجودی کیف پول کاربر کمتر از مبلغ تراکنش است؛ ابتدا موجودی را اصلاح کنید.", None
            cur.execute(
                "UPDATE userbot_payments SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (target, now, payment_id, old_status),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return False, "وضعیت تراکنش همزمان تغییر کرد؛ دوباره تلاش کنید.", None
        else:
            cur.execute(
                "UPDATE userbot_payments SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (target, now, payment_id, old_status),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return False, "وضعیت تراکنش همزمان تغییر کرد؛ دوباره تلاش کنید.", None
        conn.commit()

        # Referral funnel: every transition into/out of "approved"
        # (admin approval, SMS auto-approval, batch approval) passes here.
        # Grant/revoke are atomic and idempotent; results are attached to the
        # returned payment row so callers can send notifications.
        referral_reward_result = None
        referral_revoked = None
        if user_id and old_status != "approved" and target == "approved":
            try:
                referral_reward_result = try_grant_referral_purchase_reward(int(user_id), int(payment_id))
            except Exception:
                referral_reward_result = None
        if user_id and old_status == "approved" and target != "approved":
            try:
                referral_revoked = revoke_referral_purchase_reward_by_payment(int(payment_id))
            except Exception:
                referral_revoked = None

        cur.execute(
            """
            SELECT p.*, u.username, u.full_name, u.telegram_id
            FROM userbot_payments p
            LEFT JOIN userbot_users u ON u.id = p.user_id
            WHERE p.id = ?
            LIMIT 1
            """,
            (payment_id,),
        )
        updated = cur.fetchone()
        updated_pay = dict(updated) if updated else pay
        if referral_reward_result and referral_reward_result.get("reward"):
            updated_pay["_referral_reward"] = referral_reward_result["reward"]
            updated_pay["_referral_reward_is_new"] = bool(referral_reward_result.get("is_new"))
        if referral_revoked:
            updated_pay["_revoked_referral_reward"] = referral_revoked
        return True, "وضعیت تراکنش با موفقیت تغییر کرد.", updated_pay
    finally:
        conn.close()


def _parse_receipt_meta(raw: str) -> Dict[str, str]:
    raw = str(raw or "").strip()
    if not raw:
        return {}
    if "|" not in raw and ":" not in raw:
        return {"admin_fid": raw}
    data: Dict[str, str] = {}
    for part in raw.split("|"):
        part = part.strip()
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            data[key] = value
    return data


def _build_receipt_meta(meta: Dict[str, Any]) -> str:
    ordered_keys = [
        "admin_fid",
        "user_fid",
        "local_path",
        "code",
        "payer_last4",
        "pay_flow",
        "sid",
        "gb",
        "days",
        "renew_service_id",
        "service_name",
        "renew_snapshot",
        "delivered_service_id",
        "redelivered_at",
        "admin_chat_id",
        "admin_message_id",
        "admin_message_deleted_at",
        "admin_keyboard_cleared_at",
        "sms_event_id",
        "sms_reference",
        "sms_sender",
        "sms_amount_raw",
        "sms_currency",
        "direct_done",
        "direct_done_at",
        "direct_error",
    ]
    seen = set()
    parts: List[str] = []
    for key in ordered_keys:
        value = meta.get(key)
        if value is None or value == "":
            continue
        parts.append(f"{key}:{value}")
        seen.add(key)
    for key, value in (meta or {}).items():
        if key in seen or value is None or value == "":
            continue
        parts.append(f"{key}:{value}")
    return "|".join(parts)


def _patch_payment_receipt_meta(payment_id: int, patch: Dict[str, Any]) -> None:
    if payment_id <= 0 or not isinstance(patch, dict):
        return
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT receipt_image FROM userbot_payments WHERE id = ? LIMIT 1", (int(payment_id),))
        row = cur.fetchone()
        current = str((row["receipt_image"] if row else "") or "")
        meta = _parse_receipt_meta(current)
        for key, value in patch.items():
            key = str(key or "").strip()
            if not key:
                continue
            if value is None or str(value).strip() == "":
                meta.pop(key, None)
            else:
                meta[key] = str(value).strip()
        now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "UPDATE userbot_payments SET receipt_image = ?, updated_at = ? WHERE id = ?",
            (_build_receipt_meta(meta), now, int(payment_id)),
        )
        conn.commit()
    finally:
        conn.close()


def record_sms_webhook_event(event: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
    init_db()
    event_id = str(event.get("event_id") or "").strip()
    if not event_id:
        return False, None
    conn = _get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    try:
        cur.execute("SELECT * FROM userbot_sms_webhook_events WHERE event_id = ? LIMIT 1", (event_id,))
        existing = cur.fetchone()
        if existing:
            return False, dict(existing)
        cur.execute(
            """
            INSERT INTO userbot_sms_webhook_events
            (event_id, sender, amount_raw, currency_raw, amount_toman, reference, card_last4, body,
             status, matched_payment_id, message, received_at, device_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                str(event.get("sender") or "")[:120],
                int(event.get("amount_raw") or 0),
                str(event.get("currency_raw") or "")[:32],
                int(event.get("amount_toman") or 0),
                str(event.get("reference") or "")[:80],
                str(event.get("card_last4") or "")[:8],
                str(event.get("body") or "")[:2000],
                str(event.get("status") or "received")[:40],
                int(event.get("matched_payment_id") or 0),
                str(event.get("message") or "")[:500],
                int(event.get("received_at") or 0),
                int(event.get("device_time") or 0),
                now,
            ),
        )
        conn.commit()
        cur.execute("SELECT * FROM userbot_sms_webhook_events WHERE event_id = ? LIMIT 1", (event_id,))
        row = cur.fetchone()
        return True, dict(row) if row else None
    finally:
        conn.close()


def update_sms_webhook_event(
    event_id: str,
    *,
    status: str,
    matched_payment_id: int = 0,
    message: str = "",
    amount_toman: Optional[int] = None,
) -> None:
    init_db()
    eid = str(event_id or "").strip()
    if not eid:
        return
    conn = _get_conn()
    cur = conn.cursor()
    try:
        if amount_toman is None:
            cur.execute(
                """
                UPDATE userbot_sms_webhook_events
                SET status = ?, matched_payment_id = ?, message = ?
                WHERE event_id = ?
                """,
                (str(status or "")[:40], int(matched_payment_id or 0), str(message or "")[:500], eid),
            )
        else:
            cur.execute(
                """
                UPDATE userbot_sms_webhook_events
                SET status = ?, matched_payment_id = ?, message = ?, amount_toman = ?
                WHERE event_id = ?
                """,
                (
                    str(status or "")[:40],
                    int(matched_payment_id or 0),
                    str(message or "")[:500],
                    int(amount_toman or 0),
                    eid,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def find_pending_card_payments_by_amount(
    amount_toman: int,
    *,
    card_last4: str = "",
    max_age_minutes: int = 360,
    sms_time_ms: int = 0,
    receipt_lookback_minutes: int = 30,
) -> List[Dict[str, Any]]:
    init_db()
    amount = int(amount_toman or 0)
    if amount <= 0:
        return []
    age = max(5, int(max_age_minutes or 360))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=age)
    cutoff_s = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT p.*, u.username, u.full_name, u.telegram_id
            FROM userbot_payments p
            LEFT JOIN userbot_users u ON u.id = p.user_id
            WHERE p.status = 'pending'
              AND p.method = 'card'
              AND p.amount = ?
              AND COALESCE(p.created_at, '') >= ?
            ORDER BY p.id DESC
            LIMIT 10
            """,
            (amount, cutoff_s),
        )
        rows = [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()

    last4 = str(card_last4 or "").strip()
    sms_dt = _parse_sms_epoch_datetime(sms_time_ms)
    lookback = max(1, min(120, int(receipt_lookback_minutes or 30)))
    filtered: List[Dict[str, Any]] = []
    for row in rows:
        meta = _parse_receipt_meta(str(row.get("receipt_image") or ""))
        payer_last4 = str(meta.get("payer_last4") or "").strip()
        if last4:
            if payer_last4 != last4:
                continue
        elif payer_last4 and not _payment_allows_pre_receipt_sms_without_last4(row, meta):
            continue

        payment_dt = _parse_db_datetime(row.get("created_at"))
        if sms_dt is not None and payment_dt is not None and sms_dt < payment_dt:
            if not last4 and not _payment_allows_pre_receipt_sms_without_last4(row, meta):
                continue
            if sms_dt < payment_dt - timedelta(minutes=lookback):
                continue

        filtered.append(row)
    return filtered


def find_prior_approved_sms_webhook_event(
    *,
    event_id: str = "",
    amount_raw: int = 0,
    currency_raw: str = "",
    amount_toman: int = 0,
    sender: str = "",
    reference: str = "",
    body: str = "",
) -> Optional[Dict[str, Any]]:
    init_db()
    amount = int(amount_toman or 0)
    if amount <= 0:
        candidates = _sms_amount_candidates_toman(int(amount_raw or 0), str(currency_raw or ""))
        amount = int(candidates[0]) if candidates else 0
    if amount <= 0:
        return None

    eid = str(event_id or "").strip()
    sender_norm = re.sub(r"\D", "", str(sender or ""))
    ref_norm = re.sub(r"\D", "", str(reference or ""))
    body_norm = str(body or "").strip()
    raw_amount = int(amount_raw or 0)
    currency = str(currency_raw or "").strip().lower()

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM userbot_sms_webhook_events
            WHERE status = 'approved'
              AND amount_toman = ?
              AND event_id != ?
            ORDER BY id DESC
            LIMIT 100
            """,
            (amount, eid),
        )
        rows = [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()

    for row in rows:
        row_sender = re.sub(r"\D", "", str(row.get("sender") or ""))
        row_ref = re.sub(r"\D", "", str(row.get("reference") or ""))
        row_body = str(row.get("body") or "").strip()
        row_raw = int(row.get("amount_raw") or 0)
        row_currency = str(row.get("currency_raw") or "").strip().lower()

        same_sender = bool(sender_norm and row_sender and (sender_norm.endswith(row_sender) or row_sender.endswith(sender_norm)))
        same_raw = raw_amount > 0 and row_raw == raw_amount and (not currency or not row_currency or currency == row_currency)
        same_reference = bool(ref_norm and row_ref and ref_norm == row_ref)
        same_body = bool(body_norm and row_body and body_norm == row_body)

        if same_reference and same_sender:
            return row
        if same_body and (same_sender or not sender_norm):
            return row
        if same_raw and same_sender and not ref_norm and not row_ref and same_body:
            return row
    return None


def find_approved_card_payment_by_sms_event(
    *,
    event_id: str = "",
    amount_raw: int = 0,
    currency_raw: str = "",
    amount_toman: int = 0,
    sender: str = "",
    reference: str = "",
) -> Optional[Dict[str, Any]]:
    init_db()
    eid = str(event_id or "").strip()
    sender_norm = re.sub(r"\D", "", str(sender or ""))
    reference_norm = re.sub(r"\D", "", str(reference or ""))
    raw_amount = int(amount_raw or 0)
    currency = str(currency_raw or "").strip().lower()
    if not eid and raw_amount <= 0 and not reference_norm:
        return None
    amount = int(amount_toman or 0)
    if amount <= 0:
        candidates = _sms_amount_candidates_toman(raw_amount, currency)
        amount = int(candidates[0]) if candidates else 0
    if amount <= 0:
        return None

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT p.*, u.username, u.full_name, u.telegram_id
            FROM userbot_payments p
            LEFT JOIN userbot_users u ON u.id = p.user_id
            WHERE p.status = 'approved'
              AND p.method = 'card'
              AND p.amount = ?
            ORDER BY p.id DESC
            LIMIT 100
            """,
            (amount,),
        )
        rows = [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()

    for row in rows:
        meta = _parse_receipt_meta(str(row.get("receipt_image") or ""))
        meta_event_id = str(meta.get("sms_event_id") or "").strip()
        if eid and meta_event_id == eid:
            return row
        meta_raw_amount = _meta_int(meta, "sms_amount_raw", 0)
        meta_currency = str(meta.get("sms_currency") or "").strip().lower()
        meta_sender = re.sub(r"\D", "", str(meta.get("sms_sender") or ""))
        meta_reference = re.sub(r"\D", "", str(meta.get("sms_reference") or ""))

        same_amount = raw_amount > 0 and meta_raw_amount == raw_amount
        same_currency = not currency or not meta_currency or currency == meta_currency
        same_sender = bool(
            sender_norm
            and meta_sender
            and (sender_norm.endswith(meta_sender) or meta_sender.endswith(sender_norm))
        )
        same_reference = bool(reference_norm and meta_reference and reference_norm == meta_reference)
        if same_amount and same_currency and (same_reference or (same_sender and not reference_norm and not meta_reference)):
            return row
    return None


def _parse_db_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except Exception:
            continue
    return None


def _parse_sms_epoch_datetime(value: Any) -> Optional[datetime]:
    try:
        raw = str(value or "").strip()
        if not raw:
            return None
        stamp = float(raw)
        if stamp <= 0:
            return None
        if stamp > 10_000_000_000:
            stamp = stamp / 1000.0
        return datetime.fromtimestamp(stamp, timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _sms_webhook_event_datetime(event: Dict[str, Any]) -> Optional[datetime]:
    for key in ("received_at", "device_time"):
        dt = _parse_sms_epoch_datetime((event or {}).get(key))
        if dt is not None:
            return dt
    return _parse_db_datetime((event or {}).get("created_at"))


def _meta_int(meta: Dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(float(str((meta or {}).get(key) or "").strip()))
    except Exception:
        return int(default)


def _payment_allows_pre_receipt_sms_without_last4(payment: Dict[str, Any], meta: Dict[str, Any]) -> bool:
    """
    Banks like Blu may not include payer card last4 in deposit SMS.
    When a SMS has no last4, approve only when the payment amount carries our
    random transaction marker. This keeps fake later receipts from reusing old
    ordinary SMS messages while allowing Blu-style messages to match safely.
    """
    marker = _meta_int(meta, "tx_marker", 0)
    return 1 <= marker <= 999


def approve_pending_card_payment_from_sms(
    payment_id: int,
    *,
    event_id: str,
    reference: str = "",
    sender: str = "",
    amount_raw: int = 0,
    currency_raw: str = "",
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    ok, message, updated = change_payment_status_with_wallet(int(payment_id), "approved")
    if ok:
        _patch_payment_receipt_meta(
            int(payment_id),
            {
                "sms_event_id": event_id,
                "sms_reference": reference,
                "sms_sender": sender,
                "sms_amount_raw": amount_raw,
                "sms_currency": currency_raw,
            },
        )
        updated = get_payment_by_id(int(payment_id))
    return ok, message, updated


def _sms_amount_candidates_toman(amount_raw: int, currency_raw: str) -> List[int]:
    amount = int(amount_raw or 0)
    if amount <= 0:
        return []
    currency = str(currency_raw or "").strip().lower()
    if currency in {"rial", "irr", "ریال", "ريال"}:
        candidates = [int(round(amount / 10))]
    elif currency in {"toman", "تومان"}:
        candidates = [amount]
    else:
        candidates = [amount]
        if amount >= 10:
            candidates.append(int(round(amount / 10)))
    out: List[int] = []
    for item in candidates:
        if item > 0 and item not in out:
            out.append(item)
    return out


def try_approve_payment_from_unmatched_sms(
    payment_id: int,
    *,
    max_age_minutes: int = 360,
    receipt_lookback_minutes: int = 30,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    init_db()
    pid = int(payment_id or 0)
    if pid <= 0:
        return False, "payment_id نامعتبر است.", None

    payment = get_payment_by_id(pid)
    if not payment:
        return False, "تراکنش یافت نشد.", None
    if str(payment.get("status") or "").strip().lower() != "pending":
        return False, "تراکنش pending نیست.", payment
    if str(payment.get("method") or "").strip().lower() != "card":
        return False, "روش پرداخت کارت به کارت نیست.", payment

    amount = int(payment.get("amount") or 0)
    meta = _parse_receipt_meta(str(payment.get("receipt_image") or ""))
    payment_last4 = str(meta.get("payer_last4") or "").strip()
    payment_created_at = _parse_db_datetime(payment.get("created_at"))
    if payment_created_at is None:
        return False, "زمان ثبت تراکنش نامعتبر است؛ بررسی دستی لازم است.", payment

    age = max(5, int(max_age_minutes or 360))
    lookback = max(1, min(120, int(receipt_lookback_minutes or 30)))
    sms_not_before = payment_created_at - timedelta(minutes=lookback)
    sms_not_after = payment_created_at + timedelta(minutes=5)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=age)
    cutoff_s = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM userbot_sms_webhook_events
            WHERE status = 'no_pending_match'
              AND COALESCE(created_at, '') >= ?
            ORDER BY id DESC
            LIMIT 100
            """,
            (cutoff_s,),
        )
        events = [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()

    for event in events:
        event_created_at = _parse_db_datetime(event.get("created_at"))
        if event_created_at is None:
            continue
        event_time = _sms_webhook_event_datetime(event) or event_created_at

        candidates = _sms_amount_candidates_toman(
            int(event.get("amount_raw") or 0),
            str(event.get("currency_raw") or ""),
        )
        if amount not in candidates:
            continue

        event_id = str(event.get("event_id") or "").strip()
        prior_event = find_prior_approved_sms_webhook_event(
            event_id=event_id,
            amount_raw=int(event.get("amount_raw") or 0),
            currency_raw=str(event.get("currency_raw") or ""),
            amount_toman=amount,
            sender=str(event.get("sender") or ""),
            reference=str(event.get("reference") or ""),
            body=str(event.get("body") or ""),
        )
        if prior_event:
            update_sms_webhook_event(
                event_id,
                status="sms_reused",
                matched_payment_id=int((prior_event or {}).get("matched_payment_id") or 0),
                message="same bank SMS was already used for another approved payment",
                amount_toman=amount,
            )
            return False, "این SMS قبلاً برای پرداخت دیگری استفاده شده است؛ بررسی دستی لازم است.", payment

        event_last4 = str(event.get("card_last4") or "").strip()
        if event_time < payment_created_at:
            if event_last4:
                if not payment_last4 or payment_last4 != event_last4:
                    continue
            elif not _payment_allows_pre_receipt_sms_without_last4(payment, meta):
                continue
            if event_time < sms_not_before:
                continue
        elif event_time > sms_not_after:
            continue

        if event_last4:
            if not payment_last4 or payment_last4 != event_last4:
                continue
        elif payment_last4 and not _payment_allows_pre_receipt_sms_without_last4(payment, meta):
            continue

        ok, message, updated = approve_pending_card_payment_from_sms(
            pid,
            event_id=event_id,
            reference=str(event.get("reference") or ""),
            sender=str(event.get("sender") or ""),
            amount_raw=int(event.get("amount_raw") or 0),
            currency_raw=str(event.get("currency_raw") or ""),
        )
        update_sms_webhook_event(
            event_id,
            status="approved" if ok else "approve_failed",
            matched_payment_id=pid if ok else 0,
            message=message,
            amount_toman=amount,
        )
        return ok, message, updated

    return False, "SMS بی‌مچ مناسب برای این تراکنش پیدا نشد.", payment


# ==========================================
#   بخش ۵: کوپن‌های زرین پال
# ==========================================

def list_zarin_vouchers(limit: int = 200) -> List[Dict[str, Any]]:
    init_db()
    lim = max(1, int(limit or 200))
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM userbot_zarin_vouchers
            ORDER BY created_at DESC, code ASC
            LIMIT ?
            """,
            (lim,),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def deactivate_unusable_zarin_vouchers() -> int:
    """
    خاموشی خودکار کوپن‌های منقضی، تکمیل ظرفیت شده، یا بدون مبلغ معتبر.
    """
    init_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_zarin_vouchers
            SET is_active = 0, updated_at = ?
            WHERE is_active = 1
              AND (
                    amount_toman <= 0
                 OR used_count >= max_uses
                 OR (expires_at <> '' AND expires_at <= ?)
              )
            """,
            (now, now),
        )
        changed = int(cur.rowcount or 0)
        conn.commit()
        return changed
    finally:
        conn.close()


def get_zarin_vouchers_dashboard() -> Dict[str, Any]:
    init_db()
    deactivate_unusable_zarin_vouchers()
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(amount_toman * max_uses), 0) AS total_amount,
                COALESCE(SUM(used_count), 0) AS used_count,
                COALESCE(SUM(max_uses), 0) AS max_uses
            FROM userbot_zarin_vouchers
            """
        )
        base = dict(cur.fetchone() or {})
        cur.execute(
            """
            SELECT COUNT(*)
            FROM userbot_zarin_vouchers
            WHERE is_active = 1
              AND amount_toman > 0
              AND used_count < max_uses
              AND (expires_at = '' OR expires_at > ?)
            """,
            (now,),
        )
        active = int((cur.fetchone() or [0])[0] or 0)
        cur.execute(
            """
            SELECT COUNT(*)
            FROM userbot_zarin_vouchers
            WHERE expires_at <> '' AND expires_at <= ?
            """,
            (now,),
        )
        expired = int((cur.fetchone() or [0])[0] or 0)
        cur.execute(
            """
            SELECT COUNT(*)
            FROM userbot_zarin_vouchers
            WHERE used_count >= max_uses
            """,
        )
        full = int((cur.fetchone() or [0])[0] or 0)
        cur.execute("SELECT COUNT(*) FROM userbot_zarin_voucher_redemptions")
        redemptions = int((cur.fetchone() or [0])[0] or 0)
        cur.execute(
            """
            SELECT COALESCE(SUM(v.amount_toman), 0)
            FROM userbot_zarin_voucher_redemptions r
            LEFT JOIN userbot_zarin_vouchers v ON v.code = r.code
            """
        )
        redeemed_amount = int((cur.fetchone() or [0])[0] or 0)
        total = int(base.get("total") or 0)
        inactive = max(0, total - active)
        return {
            "total": total,
            "active": active,
            "inactive": inactive,
            "expired": expired,
            "full": full,
            "used_count": int(base.get("used_count") or 0),
            "max_uses": int(base.get("max_uses") or 0),
            "total_amount": int(base.get("total_amount") or 0),
            "redemptions": redemptions,
            "redeemed_amount": redeemed_amount,
        }
    finally:
        conn.close()


def get_zarin_voucher(code: str) -> Optional[Dict[str, Any]]:
    init_db()
    c = str(code or "").strip()
    if not c:
        return None
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM userbot_zarin_vouchers WHERE code = ? LIMIT 1",
            (c,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_zarin_voucher(
    code: str,
    amount_toman: int,
    *,
    max_uses: int = 1,
    expires_at: str = "",
    zarinpal_link: str = "",
    is_active: int = 1,
) -> Dict[str, Any]:
    init_db()
    c = str(code or "").strip()
    if not c:
        raise ValueError("code is required")
    amt = max(0, int(amount_toman or 0))
    mx = max(1, int(max_uses or 1))
    exp = str(expires_at or "").strip()
    link = str(zarinpal_link or "").strip()
    active = 1 if int(is_active or 0) else 0
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_zarin_vouchers
            (code, amount_toman, zarinpal_link, max_uses, used_count, is_active, expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                amount_toman = excluded.amount_toman,
                max_uses = excluded.max_uses,
                expires_at = excluded.expires_at,
                is_active = excluded.is_active,
                updated_at = excluded.updated_at
            """,
            (c, amt, link, mx, active, exp, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_zarin_voucher(c) or {}


def set_zarin_voucher_link(code: str, link: str) -> Dict[str, Any]:
    init_db()
    c = str(code or "").strip()
    if not c:
        raise ValueError("code is required")
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_zarin_vouchers
            SET zarinpal_link = ?, updated_at = ?
            WHERE code = ?
            """,
            (str(link or "").strip(), now, c),
        )
        conn.commit()
    finally:
        conn.close()
    return get_zarin_voucher(c) or {}


def set_zarin_voucher_amount(code: str, amount_toman: int) -> Dict[str, Any]:
    init_db()
    c = str(code or "").strip()
    if not c:
        raise ValueError("code is required")
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    amt = max(0, int(amount_toman or 0))
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_zarin_vouchers
            SET amount_toman = ?, updated_at = ?
            WHERE code = ?
            """,
            (amt, now, c),
        )
        conn.commit()
    finally:
        conn.close()
    return get_zarin_voucher(c) or {}


def set_zarin_voucher_max_uses(code: str, max_uses: int) -> Dict[str, Any]:
    init_db()
    c = str(code or "").strip()
    if not c:
        raise ValueError("code is required")
    mx = max(1, int(max_uses or 1))
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_zarin_vouchers
            SET max_uses = ?, updated_at = ?,
                is_active = CASE WHEN used_count >= ? THEN 0 ELSE 1 END
            WHERE code = ?
            """,
            (mx, now, mx, c),
        )
        conn.commit()
    finally:
        conn.close()
    return get_zarin_voucher(c) or {}


def set_zarin_voucher_expire_hours(code: str, hours: int) -> Dict[str, Any]:
    init_db()
    c = str(code or "").strip()
    if not c:
        raise ValueError("code is required")
    h = int(hours or 0)
    exp = ""
    if h > 0:
        exp = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_zarin_vouchers
            SET expires_at = ?, updated_at = ?, is_active = 1
            WHERE code = ?
            """,
            (exp, now, c),
        )
        conn.commit()
    finally:
        conn.close()
    return get_zarin_voucher(c) or {}


def set_zarin_voucher_active(code: str, active: bool) -> Dict[str, Any]:
    init_db()
    c = str(code or "").strip()
    if not c:
        raise ValueError("code is required")
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_zarin_vouchers
            SET is_active = ?, updated_at = ?
            WHERE code = ?
            """,
            (1 if active else 0, now, c),
        )
        conn.commit()
    finally:
        conn.close()
    return get_zarin_voucher(c) or {}


def rename_zarin_voucher(old_code: str, new_code: str) -> Tuple[bool, str]:
    init_db()
    old_c = str(old_code or "").strip()
    new_c = str(new_code or "").strip()
    if not old_c or not new_c:
        return False, "کد نامعتبر است."
    if old_c == new_c:
        return True, "تغییری انجام نشد."
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM userbot_zarin_vouchers WHERE code = ? LIMIT 1", (old_c,))
        if not cur.fetchone():
            return False, "کد قبلی یافت نشد."
        cur.execute("SELECT 1 FROM userbot_zarin_vouchers WHERE code = ? LIMIT 1", (new_c,))
        if cur.fetchone():
            return False, "کد جدید قبلاً ثبت شده است."
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("UPDATE userbot_zarin_vouchers SET code = ? WHERE code = ?", (new_c, old_c))
        cur.execute("UPDATE userbot_zarin_voucher_redemptions SET code = ? WHERE code = ?", (new_c, old_c))
        conn.commit()
        return True, "کد با موفقیت ویرایش شد."
    except Exception as e:
        conn.rollback()
        return False, f"خطا: {e}"
    finally:
        conn.close()


def delete_zarin_voucher(code: str) -> bool:
    init_db()
    c = str(code or "").strip()
    if not c:
        return False
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM userbot_zarin_vouchers WHERE code = ?", (c,))
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def list_zarin_voucher_redemptions(limit: int = 50, code: str = "") -> List[Dict[str, Any]]:
    init_db()
    lim = max(1, min(300, int(limit or 50)))
    c = str(code or "").strip()
    params: List[Any] = []
    where = ""
    if c:
        where = "WHERE r.code = ?"
        params.append(c)
    params.append(lim)
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT
                r.id,
                r.code,
                r.user_id,
                r.redeemed_at,
                v.amount_toman,
                v.max_uses,
                v.used_count,
                u.telegram_id,
                u.username,
                u.full_name,
                u.wallet_balance
            FROM userbot_zarin_voucher_redemptions r
            LEFT JOIN userbot_zarin_vouchers v ON v.code = r.code
            LEFT JOIN userbot_users u ON u.id = r.user_id
            {where}
            ORDER BY r.id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_active_zarin_vouchers(limit: int = 100) -> List[Dict[str, Any]]:
    init_db()
    deactivate_unusable_zarin_vouchers()
    lim = max(1, int(limit or 100))
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM userbot_zarin_vouchers
            WHERE is_active = 1
              AND amount_toman > 0
              AND zarinpal_link <> ''
              AND (expires_at = '' OR expires_at > ?)
              AND used_count < max_uses
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (now, lim),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def redeem_zarin_voucher(code: str, user_id: int) -> Tuple[bool, str, int]:
    init_db()
    c = str(code or "").strip()
    uid = int(user_id or 0)
    if not c or uid <= 0:
        return False, "کد نامعتبر است.", 0

    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            "SELECT * FROM userbot_zarin_vouchers WHERE code = ? LIMIT 1",
            (c,),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return False, "کد یافت نشد.", 0
        v = dict(row)
        if int(v.get("is_active") or 0) != 1:
            conn.rollback()
            return False, "کد غیرفعال است.", 0
        exp = str(v.get("expires_at") or "").strip()
        if exp and exp <= now:
            conn.rollback()
            return False, "کد منقضی شده است.", 0
        used = int(v.get("used_count") or 0)
        mx = max(1, int(v.get("max_uses") or 1))
        if used >= mx:
            conn.rollback()
            return False, "ظرفیت استفاده از این کد تکمیل شده است.", 0
        cur.execute(
            "SELECT 1 FROM userbot_zarin_voucher_redemptions WHERE code = ? AND user_id = ? LIMIT 1",
            (c, uid),
        )
        if cur.fetchone():
            conn.rollback()
            return False, "این کد قبلاً برای حساب شما ثبت شده است.", 0
        amount = max(0, int(v.get("amount_toman") or 0))
        if amount <= 0:
            conn.rollback()
            return False, "مبلغ هدیه این کد نامعتبر است.", 0

        cur.execute(
            """
            INSERT INTO userbot_zarin_voucher_redemptions (code, user_id, redeemed_at)
            VALUES (?, ?, ?)
            """,
            (c, uid, now),
        )
        cur.execute(
            "UPDATE userbot_users SET wallet_balance = wallet_balance + ? WHERE id = ?",
            (amount, uid),
        )
        cur.execute(
            """
            UPDATE userbot_zarin_vouchers
            SET used_count = used_count + 1,
                updated_at = ?,
                is_active = CASE WHEN used_count + 1 >= max_uses THEN 0 ELSE is_active END
            WHERE code = ?
            """,
            (now, c),
        )
        conn.commit()
        return True, "کد با موفقیت اعمال شد.", amount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


TICKET_STATUSES = {"pending", "open", "closed"}


def _ticket_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_ticket_status(status: Any) -> str:
    s = str(status or "").strip().lower()
    return s if s in TICKET_STATUSES else "pending"


def generate_ticket_code() -> int:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        for _ in range(80):
            code = random.randint(1000000, 9999999)
            cur.execute("SELECT 1 FROM userbot_tickets WHERE ticket_code = ? LIMIT 1", (code,))
            if not cur.fetchone():
                return code
        return random.randint(1000000, 9999999)
    finally:
        conn.close()


def create_ticket(
    *,
    user_id: int,
    telegram_id: int,
    username: str = "",
    full_name: str = "",
    service_name: str = "",
    title: str = "",
    question: str = "",
    receipt_photo_id: str = "",
) -> Dict[str, Any]:
    init_db()
    now = _ticket_now()
    ticket_code = int(generate_ticket_code())
    uname = str(username or "").strip().lstrip("@")
    fname = str(full_name or "").strip()
    svc = str(service_name or "").strip()
    t_title = str(title or "").strip()
    t_question = str(question or "").strip()
    photo_id = str(receipt_photo_id or "").strip()
    sender_name = fname or uname or str(telegram_id)

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO userbot_tickets
            (ticket_code, user_id, telegram_id, username, full_name, service_name, title, question, receipt_photo_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                ticket_code,
                int(user_id),
                int(telegram_id),
                uname,
                fname,
                svc,
                t_title,
                t_question,
                photo_id,
                now,
                now,
            ),
        )
        cur.execute(
            """
            INSERT INTO userbot_ticket_messages
            (ticket_code, sender_type, sender_name, message_text, photo_file_id, created_at)
            VALUES (?, 'user', ?, ?, ?, ?)
            """,
            (ticket_code, sender_name, t_question, photo_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_ticket_by_code(ticket_code) or {}


def get_ticket_by_code(ticket_code: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT t.*, u.id AS db_user_id, u.username AS db_username, u.full_name AS db_full_name, u.telegram_id AS db_telegram_id
            FROM userbot_tickets t
            LEFT JOIN userbot_users u ON u.id = t.user_id
            WHERE t.ticket_code = ?
            LIMIT 1
            """,
            (int(ticket_code),),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_ticket_messages(ticket_code: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM userbot_ticket_messages
            WHERE ticket_code = ?
            ORDER BY id ASC
            """,
            (int(ticket_code),),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_ticket_message(
    ticket_code: int,
    *,
    sender_type: str,
    sender_name: str = "",
    message_text: str = "",
    photo_file_id: str = "",
) -> bool:
    init_db()
    code = int(ticket_code or 0)
    if code <= 0:
        return False
    now = _ticket_now()
    s_type = str(sender_type or "").strip().lower()
    if s_type not in {"user", "admin"}:
        return False
    s_name = str(sender_name or "").strip()
    txt = str(message_text or "").strip()
    photo = str(photo_file_id or "").strip()
    if not txt and not photo:
        return False

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM userbot_tickets WHERE ticket_code = ? LIMIT 1", (code,))
        if not cur.fetchone():
            return False
        cur.execute(
            """
            INSERT INTO userbot_ticket_messages
            (ticket_code, sender_type, sender_name, message_text, photo_file_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (code, s_type, s_name, txt, photo, now),
        )
        cur.execute(
            "UPDATE userbot_tickets SET updated_at = ? WHERE ticket_code = ?",
            (now, code),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def set_ticket_status(
    ticket_code: int,
    status: str,
    *,
    admin_name: str = "",
    admin_telegram_id: int = 0,
) -> bool:
    init_db()
    code = int(ticket_code or 0)
    if code <= 0:
        return False
    s = _normalize_ticket_status(status)
    now = _ticket_now()
    a_name = str(admin_name or "").strip()
    a_tid = int(admin_telegram_id or 0)

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_tickets
            SET status = ?,
                admin_name = CASE WHEN ? != '' THEN ? ELSE admin_name END,
                admin_telegram_id = CASE WHEN ? > 0 THEN ? ELSE admin_telegram_id END,
                updated_at = ?
            WHERE ticket_code = ?
            """,
            (s, a_name, a_name, a_tid, a_tid, now, code),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0
    finally:
        conn.close()


def auto_close_stale_open_tickets(hours: int = 24) -> int:
    """
    Close open tickets when their latest message is older than `hours`.
    This also closes tickets after an admin reply has stayed open past the threshold.
    """
    init_db()
    try:
        h = max(1, int(hours or 24))
    except Exception:
        h = 24
    now = _ticket_now()
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE userbot_tickets
            SET status = 'closed', updated_at = ?
            WHERE status = 'open'
              AND ticket_code IN (
                  SELECT lm.ticket_code
                  FROM userbot_ticket_messages lm
                  INNER JOIN (
                      SELECT ticket_code, MAX(id) AS max_id
                      FROM userbot_ticket_messages
                      GROUP BY ticket_code
                  ) mx ON mx.max_id = lm.id
                  WHERE lm.created_at <= ?
              )
            """,
            (now, cutoff),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def get_tickets_stats() -> Dict[str, int]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed_count
            FROM userbot_tickets
            """
        )
        row = cur.fetchone() or {}
        return {
            "total_count": int((row["total_count"] if "total_count" in row.keys() else 0) or 0),
            "pending_count": int((row["pending_count"] if "pending_count" in row.keys() else 0) or 0),
            "open_count": int((row["open_count"] if "open_count" in row.keys() else 0) or 0),
            "closed_count": int((row["closed_count"] if "closed_count" in row.keys() else 0) or 0),
            "feedback_total": 0,
            "feedback_positive": 0,
            "feedback_negative": 0,
        }
    finally:
        conn.close()


def get_tickets_page(
    status: str = "all",
    page: int = 1,
    page_size: int = 21,
) -> Tuple[List[Dict[str, Any]], int]:
    init_db()
    p = max(1, int(page or 1))
    ps = max(1, int(page_size or 21))
    off = (p - 1) * ps
    s = str(status or "all").strip().lower()
    if s not in {"all", "pending", "open", "closed"}:
        s = "all"

    conn = _get_conn()
    cur = conn.cursor()
    try:
        if s == "all":
            cur.execute("SELECT COUNT(*) AS c FROM userbot_tickets")
            total = int((cur.fetchone() or {"c": 0})["c"] or 0)
            cur.execute(
                """
                SELECT t.*, u.username AS db_username, u.full_name AS db_full_name, u.telegram_id AS db_telegram_id
                FROM userbot_tickets t
                LEFT JOIN userbot_users u ON u.id = t.user_id
                ORDER BY t.updated_at DESC, t.id DESC
                LIMIT ? OFFSET ?
                """,
                (ps, off),
            )
        else:
            cur.execute("SELECT COUNT(*) AS c FROM userbot_tickets WHERE status = ?", (s,))
            total = int((cur.fetchone() or {"c": 0})["c"] or 0)
            cur.execute(
                """
                SELECT t.*, u.username AS db_username, u.full_name AS db_full_name, u.telegram_id AS db_telegram_id
                FROM userbot_tickets t
                LEFT JOIN userbot_users u ON u.id = t.user_id
                WHERE t.status = ?
                ORDER BY t.updated_at DESC, t.id DESC
                LIMIT ? OFFSET ?
                """,
                (s, ps, off),
            )
        rows = cur.fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def get_tickets_for_user(
    user_id: int,
    page: int = 1,
    page_size: int = 21,
) -> Tuple[List[Dict[str, Any]], int]:
    init_db()
    uid = int(user_id or 0)
    if uid <= 0:
        return [], 0
    p = max(1, int(page or 1))
    ps = max(1, int(page_size or 21))
    off = (p - 1) * ps

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS c FROM userbot_tickets WHERE user_id = ?", (uid,))
        total = int((cur.fetchone() or {"c": 0})["c"] or 0)
        cur.execute(
            """
            SELECT t.*, u.username AS db_username, u.full_name AS db_full_name, u.telegram_id AS db_telegram_id
            FROM userbot_tickets t
            LEFT JOIN userbot_users u ON u.id = t.user_id
            WHERE t.user_id = ?
            ORDER BY t.updated_at DESC, t.id DESC
            LIMIT ? OFFSET ?
            """,
            (uid, ps, off),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def get_user_ticket_by_code(user_id: int, ticket_code: int) -> Optional[Dict[str, Any]]:
    init_db()
    uid = int(user_id or 0)
    code = int(ticket_code or 0)
    if uid <= 0 or code <= 0:
        return None
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT t.*, u.username AS db_username, u.full_name AS db_full_name, u.telegram_id AS db_telegram_id
            FROM userbot_tickets t
            LEFT JOIN userbot_users u ON u.id = t.user_id
            WHERE t.user_id = ? AND t.ticket_code = ?
            LIMIT 1
            """,
            (uid, code),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ==========================================
#     بخش رفرال (دعوت دوستان)
# ==========================================

REFERRAL_PAYLOAD_PREFIX = "ref_"
REFERRAL_CODE_RE = re.compile(r"^[a-z0-9_]{6,20}$")


def _referral_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _invitee_display_name(cur_or_conn, invitee_user_id: int) -> str:
    """Best-effort display name of an invitee (username > full_name)."""
    try:
        cur = cur_or_conn.cursor() if hasattr(cur_or_conn, "cursor") else cur_or_conn
        cur.execute(
            "SELECT username, full_name FROM userbot_users WHERE id = ? LIMIT 1",
            (int(invitee_user_id or 0),),
        )
        row = cur.fetchone()
        if not row:
            return "دوست شما"
        username = str(row["username"] or "").strip()
        full_name = str(row["full_name"] or "").strip()
        return full_name or username or "دوست شما"
    except Exception:
        return "دوست شما"


def _format_referral_reward_text(reward: Dict[str, Any], settings: Dict[str, Any], inviter_wallet: int, invitee_name: str) -> str:
    reward_type = str(reward.get("reward_type") or "").strip().lower()
    amount = int(reward.get("amount_toman") or 0)
    template = ""
    if reward_type == "trial":
        template = str(settings.get("trial_reward_notify_text") or DEFAULT_REFERRAL_SETTINGS["trial_reward_notify_text"])
    elif reward_type == "purchase":
        template = str(settings.get("purchase_reward_notify_text") or DEFAULT_REFERRAL_SETTINGS["purchase_reward_notify_text"])
    else:
        template = "🎉 پاداش دعوت دریافت شد."
    try:
        return template.format(
            invitee_name=invitee_name,
            amount=f"{amount:,}",
            wallet=f"{int(inviter_wallet):,}",
        )
    except Exception:
        return (
            f"🎉 پاداش دعوت!\n🎁 {amount:,} تومان به کیف پول شما اضافه شد.\n"
            f"موجودی فعلی: {int(inviter_wallet):,} تومان"
        )


def _send_referral_reward_notice_background(reward: Dict[str, Any]) -> None:
    """
    Fire-and-forget Telegram notice to the inviter when a referral reward is paid.
    Uses only stdlib so it never blocks or breaks the reward transaction.
    """
    try:
        token = str(os.getenv("USER_BOT_TOKEN", "") or os.getenv("BOT_TOKEN", "") or "").strip()
        if not token:
            return
        inviter_id = int(reward.get("inviter_id") or 0)
        if inviter_id <= 0:
            return
        conn = _get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT telegram_id, wallet_balance FROM userbot_users WHERE id = ? LIMIT 1", (inviter_id,))
            inviter = cur.fetchone()
            if not inviter:
                return
            telegram_id = int(inviter["telegram_id"] or 0)
            if telegram_id <= 0:
                return
            invitee_name = _invitee_display_name(cur, int(reward.get("invitee_id") or 0))
            settings = get_referral_settings()
            text = _format_referral_reward_text(reward, settings, int(inviter["wallet_balance"] or 0), invitee_name)
        finally:
            conn.close()

        api_url = f"https://api.telegram.org/bot{urllib.parse.quote(token)}/sendMessage"

        def _post() -> None:
            try:
                payload = json.dumps({"chat_id": telegram_id, "text": text}).encode("utf-8")
                req = urllib.request.Request(
                    api_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15):
                    pass
            except Exception:
                pass

        t = threading.Thread(target=_post, daemon=True)
        t.start()
    except Exception:
        pass


def get_referral_settings() -> Dict[str, Any]:
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM userbot_settings WHERE key = 'referral_settings' LIMIT 1")
        row = cur.fetchone()
        settings = dict(DEFAULT_REFERRAL_SETTINGS)
        if row and row["value"]:
            try:
                raw = json.loads(row["value"])
                if isinstance(raw, dict):
                    for key in settings.keys():
                        if key in raw and raw[key] is not None:
                            settings[key] = raw[key]
            except Exception:
                pass
        settings["referral_enabled"] = _as_bool(settings.get("referral_enabled"), False)
        settings["trial_reward_enabled"] = _as_bool(settings.get("trial_reward_enabled"), True)
        settings["trial_reward_amount"] = max(0, int(settings.get("trial_reward_amount") or 0))
        settings["purchase_reward_enabled"] = _as_bool(settings.get("purchase_reward_enabled"), True)
        settings["purchase_reward_amount"] = max(0, int(settings.get("purchase_reward_amount") or 0))
        settings["max_successful_referrals"] = max(0, int(settings.get("max_successful_referrals") or 0))
        settings["min_purchase_amount"] = max(0, int(settings.get("min_purchase_amount") or 0))
        settings["invite_intro_text"] = str(
            settings.get("invite_intro_text") or DEFAULT_REFERRAL_SETTINGS["invite_intro_text"]
        )
        return settings
    finally:
        conn.close()


def set_referral_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    current = get_referral_settings()
    if isinstance(settings, dict):
        for key in current.keys():
            if key in settings and settings[key] is not None:
                current[key] = settings[key]
    current["referral_enabled"] = _as_bool(current.get("referral_enabled"), False)
    current["trial_reward_enabled"] = _as_bool(current.get("trial_reward_enabled"), True)
    current["trial_reward_amount"] = max(0, int(current.get("trial_reward_amount") or 0))
    current["purchase_reward_enabled"] = _as_bool(current.get("purchase_reward_enabled"), True)
    current["purchase_reward_amount"] = max(0, int(current.get("purchase_reward_amount") or 0))
    current["max_successful_referrals"] = max(0, int(current.get("max_successful_referrals") or 0))
    current["min_purchase_amount"] = max(0, int(current.get("min_purchase_amount") or 0))
    current["invite_intro_text"] = str(
        current.get("invite_intro_text") or DEFAULT_REFERRAL_SETTINGS["invite_intro_text"]
    )
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO userbot_settings (key, value) VALUES ('referral_settings', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(current, ensure_ascii=False),),
        )
        conn.commit()
    finally:
        conn.close()
    return current


def toggle_referral_setting(name: str) -> Dict[str, Any]:
    if name not in {"referral_enabled", "trial_reward_enabled", "purchase_reward_enabled"}:
        raise ValueError("invalid referral setting name")
    settings = get_referral_settings()
    settings[name] = not bool(settings.get(name))
    return set_referral_settings(settings)


def set_referral_value(name: str, value: Any) -> Dict[str, Any]:
    settings = get_referral_settings()
    if name == "invite_intro_text":
        settings[name] = str(value or "")
    elif name in {"trial_reward_amount", "purchase_reward_amount", "max_successful_referrals", "min_purchase_amount"}:
        settings[name] = max(0, int(value))
    else:
        raise ValueError("invalid referral setting name")
    return set_referral_settings(settings)


def normalize_referral_payload(payload: str) -> str:
    """
    Extract referral code from a /start deep-link payload.
    Returns an empty string when the payload is not a valid referral payload.
    """
    p = str(payload or "").strip().lower()
    if not p.startswith(REFERRAL_PAYLOAD_PREFIX):
        return ""
    code = p[len(REFERRAL_PAYLOAD_PREFIX):].strip()
    if not code or not REFERRAL_CODE_RE.fullmatch(code):
        return ""
    return code


def get_or_create_user_referral_code(user_id: int) -> str:
    """Return the user's public referral code, creating one if needed."""
    init_db()
    uid = int(user_id or 0)
    if uid <= 0:
        return ""
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT referral_code FROM userbot_users WHERE id = ? LIMIT 1", (uid,))
        row = cur.fetchone()
        if row and str(row["referral_code"] or "").strip():
            return str(row["referral_code"]).strip()
        alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
        code = ""
        for _ in range(200):
            candidate = "".join(random.choice(alphabet) for _ in range(8))
            cur.execute("SELECT 1 FROM userbot_users WHERE referral_code = ? LIMIT 1", (candidate,))
            if not cur.fetchone():
                code = candidate
                break
        if not code:
            code = f"u{uid}x"
        cur.execute(
            "UPDATE userbot_users SET referral_code = ? WHERE id = ? AND (referral_code IS NULL OR referral_code = '')",
            (code, uid),
        )
        if cur.rowcount > 0:
            conn.commit()
            return code
        cur.execute("SELECT referral_code FROM userbot_users WHERE id = ? LIMIT 1", (uid,))
        row = cur.fetchone()
        return str((row["referral_code"] if row else "") or "").strip()
    finally:
        conn.close()


def get_user_by_referral_code(code: str) -> Optional[Dict[str, Any]]:
    init_db()
    c = str(code or "").strip().lower()
    if not c:
        return None
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM userbot_users WHERE referral_code = ? LIMIT 1", (c,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def register_referral(invitee_user_id: int, inviter_code: str, payload: str = "") -> Tuple[bool, str, int]:
    """
    Register an invitee under an inviter (source of truth: userbot_referrals).
    Returns: (created, status, referral_id)
    status: ok | invalid_code | not_found | self_referral | already_referred | banned_inviter | invalid_invitee
    """
    uid = int(invitee_user_id or 0)
    if uid <= 0:
        return False, "invalid_invitee", 0
    code = str(inviter_code or "").strip().lower()
    if not code:
        return False, "invalid_code", 0
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("SELECT * FROM userbot_referrals WHERE invitee_id = ? LIMIT 1", (uid,))
        existing = cur.fetchone()
        if existing:
            conn.rollback()
            # یک invitee فقط یک inviter اصلی دارد؛ کد جدید نادیده گرفته می‌شود.
            return False, "already_referred", int(existing["id"] or 0)
        cur.execute("SELECT id, is_banned FROM userbot_users WHERE referral_code = ? LIMIT 1", (code,))
        inviter = cur.fetchone()
        if not inviter:
            conn.rollback()
            return False, "not_found", 0
        inviter_id = int(inviter["id"] or 0)
        if inviter_id == uid:
            conn.rollback()
            return False, "self_referral", 0
        if int(inviter["is_banned"] or 0) == 1:
            conn.rollback()
            return False, "banned_inviter", 0
        # Anti-fraud snapshot: invitees with prior approved purchases never earn rewards.
        cur.execute(
            "SELECT COUNT(*) FROM userbot_payments WHERE user_id = ? AND status = 'approved'",
            (uid,),
        )
        qualified = 1 if int((cur.fetchone() or [0])[0] or 0) == 0 else 0
        now = _referral_now()
        cur.execute(
            """
            INSERT INTO userbot_referrals
            (inviter_id, invitee_id, invited_by_code, status, rejection_reason, fraud_flag, invitee_qualified, first_seen_payload, created_at, updated_at)
            VALUES (?, ?, ?, 'active', '', 0, ?, ?, ?, ?)
            """,
            (inviter_id, uid, code, qualified, str(payload or "")[:120], now, now),
        )
        referral_id = int(cur.lastrowid or 0)
        cur.execute("UPDATE userbot_users SET invited_by_user_id = ? WHERE id = ?", (inviter_id, uid))
        conn.commit()
        return True, "ok", referral_id
    except sqlite3.IntegrityError:
        # race condition: ثبت موازی زودتر انجام شده است
        conn.rollback()
        cur2 = conn.cursor()
        cur2.execute("SELECT id FROM userbot_referrals WHERE invitee_id = ? LIMIT 1", (uid,))
        row = cur2.fetchone()
        return False, "already_referred", int(row["id"] or 0) if row else 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_referral_by_invitee(invitee_user_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    uid = int(invitee_user_id or 0)
    if uid <= 0:
        return None
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM userbot_referrals WHERE invitee_id = ? LIMIT 1", (uid,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_referral_by_id(referral_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    rid = int(referral_id or 0)
    if rid <= 0:
        return None
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM userbot_referrals WHERE id = ? LIMIT 1", (rid,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_referral_status(referral_id: int, status: str, reason: str = "") -> bool:
    init_db()
    rid = int(referral_id or 0)
    st = str(status or "").strip().lower()
    if rid <= 0 or st not in {"active", "rejected"}:
        return False
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE userbot_referrals SET status = ?, rejection_reason = ?, updated_at = ? WHERE id = ?",
            (st, str(reason or "")[:200], _referral_now(), rid),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_referral_fraud_flag(referral_id: int, flag: bool) -> bool:
    init_db()
    rid = int(referral_id or 0)
    if rid <= 0:
        return False
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE userbot_referrals SET fraud_flag = ?, updated_at = ? WHERE id = ?",
            (1 if flag else 0, _referral_now(), rid),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def try_grant_referral_trial_reward(invitee_user_id: int) -> Optional[Dict[str, Any]]:
    """
    Grant the trial reward to the inviter after the invitee's valid free trial.
    Idempotent and atomic (wallet credit + voucher ledger + reward row in one tx).
    Returns a result dict {"reward": row, "is_new": bool} on success, None otherwise.
    """
    return _grant_referral_reward(int(invitee_user_id or 0), "trial", 0)


def try_grant_referral_purchase_reward(invitee_user_id: int, payment_id: int = 0) -> Optional[Dict[str, Any]]:
    """
    Grant the first-purchase reward to the inviter.
    Idempotent and atomic; only the first qualifying approved payment counts.
    Returns a result dict {"reward": row, "is_new": bool} on success, None otherwise.
    """
    return _grant_referral_reward(int(invitee_user_id or 0), "purchase", int(payment_id or 0))


def _has_approved_payments(cur: sqlite3.Cursor, user_id: int) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM userbot_payments WHERE user_id = ? AND status = 'approved'",
        (int(user_id or 0),),
    )
    return int((cur.fetchone() or [0])[0] or 0) > 0


def _grant_referral_reward(
    invitee_user_id: int, reward_type: str, payment_id: int
) -> Optional[Dict[str, Any]]:
    """
    Atomic, idempotent grant of a referral reward.
    Anti-fraud eligibility:
      - invitee must have no prior approved payments (prevents self-accounts).
      - only the invitee's first qualifying approved payment triggers a reward.
    Returns {"reward": dict, "is_new": bool} or None.
    """
    if reward_type not in REFERRAL_REWARD_KINDS:
        return None
    uid = int(invitee_user_id or 0)
    if uid <= 0:
        return None
    settings = get_referral_settings()
    if not bool(settings.get("referral_enabled", False)):
        return None
    if reward_type == "trial":
        if not bool(settings.get("trial_reward_enabled", True)):
            return None
        amount = int(settings.get("trial_reward_amount") or 0)
    else:
        if not bool(settings.get("purchase_reward_enabled", True)):
            return None
        amount = int(settings.get("purchase_reward_amount") or 0)
    if amount <= 0:
        return None

    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            "SELECT * FROM userbot_referrals WHERE invitee_id = ? AND status = 'active' LIMIT 1",
            (uid,),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return None
        ref = dict(row)
        if int(ref.get("fraud_flag") or 0) == 1:
            conn.rollback()
            return None
        if int(ref.get("invitee_qualified", 1) or 0) != 1:
            # Invitee already had an approved purchase before joining (anti-fraud snapshot).
            conn.rollback()
            return None
        inviter_id = int(ref.get("inviter_id") or 0)
        if inviter_id <= 0:
            conn.rollback()
            return None

        cur.execute(
            "SELECT id, is_banned, telegram_id FROM userbot_users WHERE id = ? LIMIT 1",
            (inviter_id,),
        )
        inviter = cur.fetchone()
        if not inviter or int(inviter["is_banned"] or 0) == 1:
            conn.rollback()
            return None

        # Idempotency: only one reward of each type per invitee.
        cur.execute(
            "SELECT * FROM userbot_referral_rewards WHERE referral_id = ? AND reward_type = ? LIMIT 1",
            (int(ref["id"] or 0), reward_type),
        )
        existing = cur.fetchone()
        if existing:
            conn.rollback()
            return {"reward": dict(existing), "is_new": False}

        # Anti-fraud: a reward is only paid once per (referral, reward_type);
        # the invitee must not already have an approved purchase.
        if reward_type == "purchase":
            pid = int(payment_id or 0)
            if pid <= 0:
                conn.rollback()
                return None
            cur.execute("SELECT * FROM userbot_payments WHERE id = ? LIMIT 1", (pid,))
            pay = cur.fetchone()
            if not pay:
                conn.rollback()
                return None
            if str(pay["status"] or "").strip().lower() != "approved":
                conn.rollback()
                return None
            if int(pay["user_id"] or 0) != uid:
                conn.rollback()
                return None
            pay_amount = int(pay["amount"] or 0)
            if pay_amount <= 0:
                conn.rollback()
                return None
            min_amount = max(0, int(settings.get("min_purchase_amount") or 0))
            if pay_amount < min_amount:
                conn.rollback()
                return None
            # Only the FIRST approved payment of the invitee triggers a purchase reward.
            cur.execute(
                "SELECT id FROM userbot_payments WHERE user_id = ? AND status = 'approved' ORDER BY id ASC LIMIT 1",
                (uid,),
            )
            first_row = cur.fetchone()
            if not first_row or int(first_row["id"] or 0) != pid:
                conn.rollback()
                return None
        elif _has_approved_payments(cur, uid):
            # Trial reward: the invitee must not already have an approved purchase.
            conn.rollback()
            return None

        max_allowed = max(0, int(settings.get("max_successful_referrals") or 0))
        if max_allowed > 0:
            cur.execute(
                "SELECT COUNT(*) FROM userbot_referral_rewards WHERE inviter_id = ? AND reward_type = ? AND status = 'paid'",
                (inviter_id, reward_type),
            )
            paid_count = int((cur.fetchone() or [0])[0] or 0)
            if paid_count >= max_allowed:
                conn.rollback()
                return None

        voucher_prefix = "REF-TRIAL" if reward_type == "trial" else "REF-BUY"
        voucher_code = f"{voucher_prefix}-{int(ref['id'] or 0)}"
        now = _referral_now()

        cur.execute("SELECT code FROM userbot_zarin_vouchers WHERE code = ? LIMIT 1", (voucher_code,))
        if not cur.fetchone():
            cur.execute(
                """
                INSERT INTO userbot_zarin_vouchers
                (code, amount_toman, zarinpal_link, max_uses, used_count, is_active, expires_at, created_at, updated_at)
                VALUES (?, ?, '', 1, 0, 1, '', ?, ?)
                """,
                (voucher_code, amount, now, now),
            )
        cur.execute(
            "SELECT 1 FROM userbot_zarin_voucher_redemptions WHERE code = ? AND user_id = ? LIMIT 1",
            (voucher_code, inviter_id),
        )
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO userbot_zarin_voucher_redemptions (code, user_id, redeemed_at) VALUES (?, ?, ?)",
                (voucher_code, inviter_id, now),
            )
            cur.execute(
                "UPDATE userbot_users SET wallet_balance = wallet_balance + ? WHERE id = ?",
                (amount, inviter_id),
            )
            cur.execute(
                """
                UPDATE userbot_zarin_vouchers
                SET used_count = used_count + 1,
                    updated_at = ?,
                    is_active = CASE WHEN used_count + 1 >= max_uses THEN 0 ELSE is_active END
                WHERE code = ?
                """,
                (now, voucher_code),
            )

        cur.execute(
            """
            INSERT INTO userbot_referral_rewards
            (referral_id, inviter_id, invitee_id, reward_type, reward_source, amount_toman, voucher_code, payment_id, status, revoked_at, created_at)
            VALUES (?, ?, ?, ?, 'system', ?, ?, ?, 'paid', '', ?)
            """,
            (
                int(ref["id"] or 0),
                inviter_id,
                uid,
                reward_type,
                amount,
                voucher_code,
                int(payment_id or 0),
                now,
            ),
        )
        conn.commit()

        cur.execute(
            "SELECT * FROM userbot_referral_rewards WHERE referral_id = ? AND reward_type = ? LIMIT 1",
            (int(ref["id"] or 0), reward_type),
        )
        new_row = cur.fetchone()
        if not new_row:
            return None
        try:
            _send_referral_reward_notice_background(dict(new_row))
        except Exception:
            pass
        return {"reward": dict(new_row), "is_new": True}
    except sqlite3.IntegrityError:
        conn.rollback()
        try:
            cur.execute(
                """
                SELECT r.* FROM userbot_referral_rewards r
                JOIN userbot_referrals ref ON ref.id = r.referral_id
                WHERE ref.invitee_id = ? AND r.reward_type = ? LIMIT 1
                """,
                (uid, reward_type),
            )
            row = cur.fetchone()
            if row:
                return {"reward": dict(row), "is_new": False}
            return None
        except Exception:
            return None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revoke_referral_reward_by_id(reward_id: int) -> Optional[Dict[str, Any]]:
    """
    Admin rollback: revoke a paid referral reward.
    Deducts up to zero from the inviter's wallet, deactivates the voucher row and
    flags the referral for admin review. Trial rewards are untouched.
    Returns the revoked reward row or None.
    """
    rid = int(reward_id or 0)
    if rid <= 0:
        return None
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            "SELECT * FROM userbot_referral_rewards WHERE id = ? AND status = 'paid' LIMIT 1",
            (rid,),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return None
        reward = dict(row)
        inviter_id = int(reward.get("inviter_id") or 0)
        amount = int(reward.get("amount_toman") or 0)
        now = _referral_now()
        if inviter_id > 0 and amount > 0:
            # کسر تا صفر؛ کیف پول هرگز منفی نمی‌شود
            cur.execute(
                "UPDATE userbot_users SET wallet_balance = CASE WHEN wallet_balance >= ? THEN wallet_balance - ? ELSE 0 END WHERE id = ?",
                (amount, amount, inviter_id),
            )
        cur.execute(
            "UPDATE userbot_referral_rewards SET status = 'revoked', revoked_at = ? WHERE id = ?",
            (now, rid),
        )
        cur.execute(
            "UPDATE userbot_zarin_vouchers SET is_active = 0, updated_at = ? WHERE code = ?",
            (now, str(reward.get("voucher_code") or "")),
        )
        if int(reward.get("referral_id") or 0) > 0:
            cur.execute(
                "UPDATE userbot_referrals SET fraud_flag = 1, updated_at = ? WHERE id = ?",
                (now, int(reward["referral_id"])),
            )
        conn.commit()
        return reward
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revoke_referral_purchase_reward_by_payment(payment_id: int) -> Optional[Dict[str, Any]]:
    """Revoke the purchase reward tied to a refunded/rejected payment (if any)."""
    pid = int(payment_id or 0)
    if pid <= 0:
        return None
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM userbot_referral_rewards WHERE payment_id = ? AND reward_type = 'purchase' AND status = 'paid' LIMIT 1",
            (pid,),
        )
        row = cur.fetchone()
        reward_id = int(row["id"] or 0) if row else 0
    finally:
        conn.close()
    if reward_id <= 0:
        return None
    return revoke_referral_reward_by_id(reward_id)


def grant_manual_referral_reward(user_id: int, amount_toman: int) -> Optional[Dict[str, Any]]:
    """Admin manual reward: gift-system voucher + wallet credit + reward ledger row."""
    uid = int(user_id or 0)
    amount = max(0, int(amount_toman or 0))
    if uid <= 0 or amount <= 0:
        return None
    init_db()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("SELECT id FROM userbot_users WHERE id = ? LIMIT 1", (uid,))
        if not cur.fetchone():
            conn.rollback()
            return None
        now = _referral_now()
        voucher_code = f"REF-MAN-{uid}-{random.randint(1000, 9999)}"
        for _ in range(100):
            cur.execute("SELECT 1 FROM userbot_zarin_vouchers WHERE code = ? LIMIT 1", (voucher_code,))
            if not cur.fetchone():
                break
            voucher_code = f"REF-MAN-{uid}-{random.randint(1000, 9999)}"
        cur.execute(
            """
            INSERT INTO userbot_zarin_vouchers
            (code, amount_toman, zarinpal_link, max_uses, used_count, is_active, expires_at, created_at, updated_at)
            VALUES (?, ?, '', 1, 1, 0, '', ?, ?)
            """,
            (voucher_code, amount, now, now),
        )
        cur.execute(
            "INSERT INTO userbot_zarin_voucher_redemptions (code, user_id, redeemed_at) VALUES (?, ?, ?)",
            (voucher_code, uid, now),
        )
        cur.execute(
            "UPDATE userbot_users SET wallet_balance = wallet_balance + ? WHERE id = ?",
            (amount, uid),
        )
        cur.execute(
            """
            INSERT INTO userbot_referral_rewards
            (referral_id, inviter_id, invitee_id, reward_type, reward_source, amount_toman, voucher_code, payment_id, status, revoked_at, created_at)
            VALUES (NULL, ?, 0, 'manual', ?, ?, ?, 0, 'paid', '', ?)
            """,
            (uid, REFERRAL_REWARD_SOURCE_MANUAL, amount, voucher_code, now),
        )
        reward_id = int(cur.lastrowid or 0)
        conn.commit()
        cur.execute("SELECT * FROM userbot_referral_rewards WHERE id = ? LIMIT 1", (reward_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_referral_reward(reward_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    rid = int(reward_id or 0)
    if rid <= 0:
        return None
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM userbot_referral_rewards WHERE id = ? LIMIT 1", (rid,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_referrals(limit: int = 50, offset: int = 0, inviter_id: int = 0, status: str = "") -> Tuple[List[Dict[str, Any]], int]:
    init_db()
    lim = max(1, int(limit or 50))
    off = max(0, int(offset or 0))
    inviter = int(inviter_id or 0)
    st = str(status or "").strip().lower()
    where = ["1=1"]
    params: List[Any] = []
    if inviter > 0:
        where.append("r.inviter_id = ?")
        params.append(inviter)
    if st in {"active", "rejected"}:
        where.append("r.status = ?")
        params.append(st)
    where_sql = " AND ".join(where)
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM userbot_referrals r WHERE {where_sql}", params)
        total = int((cur.fetchone() or [0])[0] or 0)
        cur.execute(
            f"""
            SELECT r.*,
                   inv.username AS inviter_username, inv.full_name AS inviter_full_name, inv.telegram_id AS inviter_telegram_id,
                   invt.username AS invitee_username, invt.full_name AS invitee_full_name, invt.telegram_id AS invitee_telegram_id
            FROM userbot_referrals r
            LEFT JOIN userbot_users inv ON inv.id = r.inviter_id
            LEFT JOIN userbot_users invt ON invt.id = r.invitee_id
            WHERE {where_sql}
            ORDER BY r.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, lim, off),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def list_referral_rewards(limit: int = 50, offset: int = 0, inviter_id: int = 0, reward_type: str = "", status: str = "") -> Tuple[List[Dict[str, Any]], int]:
    init_db()
    lim = max(1, int(limit or 50))
    off = max(0, int(offset or 0))
    inviter = int(inviter_id or 0)
    rtype = str(reward_type or "").strip().lower()
    st = str(status or "").strip().lower()
    where = ["1=1"]
    params: List[Any] = []
    if inviter > 0:
        where.append("rw.inviter_id = ?")
        params.append(inviter)
    if rtype in {*REFERRAL_REWARD_KINDS, "manual"}:
        where.append("rw.reward_type = ?")
        params.append(rtype)
    if st in {"paid", "revoked"}:
        where.append("rw.status = ?")
        params.append(st)
    where_sql = " AND ".join(where)
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM userbot_referral_rewards rw WHERE {where_sql}", params)
        total = int((cur.fetchone() or [0])[0] or 0)
        cur.execute(
            f"""
            SELECT rw.*,
                   inv.username AS inviter_username, inv.full_name AS inviter_full_name,
                   invt.username AS invitee_username, invt.full_name AS invitee_full_name
            FROM userbot_referral_rewards rw
            LEFT JOIN userbot_users inv ON inv.id = rw.inviter_id
            LEFT JOIN userbot_users invt ON invt.id = rw.invitee_id
            WHERE {where_sql}
            ORDER BY rw.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, lim, off),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def get_referral_user_stats(inviter_user_id: int) -> Dict[str, Any]:
    init_db()
    uid = int(inviter_user_id or 0)
    result = {
        "total_referrals": 0,
        "active_referrals": 0,
        "successful_referrals": 0,
        "pending_purchase": 0,
        "total_rewards": 0,
        "paid_rewards_count": 0,
        "trial_rewards_count": 0,
        "purchase_rewards_count": 0,
    }
    if uid <= 0:
        return result
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM userbot_referrals WHERE inviter_id = ?", (uid,))
        result["total_referrals"] = int((cur.fetchone() or [0])[0] or 0)
        cur.execute("SELECT COUNT(*) FROM userbot_referrals WHERE inviter_id = ? AND status = 'active'", (uid,))
        result["active_referrals"] = int((cur.fetchone() or [0])[0] or 0)
        cur.execute(
            """
            SELECT COUNT(DISTINCT referral_id) FROM userbot_referral_rewards
            WHERE inviter_id = ? AND reward_type = 'purchase' AND status = 'paid'
            """,
            (uid,),
        )
        result["successful_referrals"] = int((cur.fetchone() or [0])[0] or 0)
        result["pending_purchase"] = max(0, result["active_referrals"] - result["successful_referrals"])
        cur.execute(
            "SELECT COALESCE(SUM(amount_toman), 0), COUNT(*), "
            "COALESCE(SUM(CASE WHEN reward_type = 'trial' THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN reward_type = 'purchase' THEN 1 ELSE 0 END), 0) "
            "FROM userbot_referral_rewards WHERE inviter_id = ? AND status = 'paid'",
            (uid,),
        )
        row = cur.fetchone()
        if row:
            result["total_rewards"] = int(row[0] or 0)
            result["paid_rewards_count"] = int(row[1] or 0)
            result["trial_rewards_count"] = int(row[2] or 0)
            result["purchase_rewards_count"] = int(row[3] or 0)
        return result
    finally:
        conn.close()


def get_referral_admin_stats() -> Dict[str, Any]:
    init_db()
    result = {
        "total_referrals": 0,
        "active_referrals": 0,
        "rejected_referrals": 0,
        "fraud_flagged": 0,
        "trial_rewards_count": 0,
        "trial_rewards_amount": 0,
        "purchase_rewards_count": 0,
        "purchase_rewards_amount": 0,
        "revoked_rewards_count": 0,
        "total_reward_cost": 0,
        "revenue_generated": 0,
        "conversion_rate": 0.0,
        "top_referrers": [],
    }
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM userbot_referrals")
        result["total_referrals"] = int((cur.fetchone() or [0])[0] or 0)
        cur.execute("SELECT COUNT(*) FROM userbot_referrals WHERE status = 'active'")
        result["active_referrals"] = int((cur.fetchone() or [0])[0] or 0)
        cur.execute("SELECT COUNT(*) FROM userbot_referrals WHERE status = 'rejected'")
        result["rejected_referrals"] = int((cur.fetchone() or [0])[0] or 0)
        cur.execute("SELECT COUNT(*) FROM userbot_referrals WHERE fraud_flag = 1")
        result["fraud_flagged"] = int((cur.fetchone() or [0])[0] or 0)
        cur.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN reward_type = 'trial' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN reward_type = 'trial' AND status = 'paid' THEN amount_toman ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN reward_type = 'purchase' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN reward_type = 'purchase' AND status = 'paid' THEN amount_toman ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status = 'revoked' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status = 'paid' THEN amount_toman ELSE 0 END), 0)
            FROM userbot_referral_rewards
            WHERE reward_type IN ('trial', 'purchase')
            """
        )
        row = cur.fetchone()
        if row:
            result["trial_rewards_count"] = int(row[0] or 0)
            result["trial_rewards_amount"] = int(row[1] or 0)
            result["purchase_rewards_count"] = int(row[2] or 0)
            result["purchase_rewards_amount"] = int(row[3] or 0)
            result["revoked_rewards_count"] = int(row[4] or 0)
            result["total_reward_cost"] = int(row[5] or 0)
        cur.execute(
            """
            SELECT COALESCE(SUM(p.amount), 0)
            FROM userbot_referral_rewards rw
            JOIN userbot_payments p ON p.id = rw.payment_id
            WHERE rw.reward_type = 'purchase' AND rw.status = 'paid' AND p.status = 'approved'
            """
        )
        result["revenue_generated"] = int((cur.fetchone() or [0])[0] or 0)
        cur.execute(
            "SELECT COUNT(*) FROM userbot_referral_rewards WHERE reward_type = 'purchase' AND status = 'paid'"
        )
        paid_purchase_rewards = int((cur.fetchone() or [0])[0] or 0)
        result["paid_purchase_rewards"] = paid_purchase_rewards
        if result["total_referrals"] > 0:
            result["conversion_rate"] = round(
                (paid_purchase_rewards / int(result["total_referrals"])) * 100, 1
            )
        cur.execute(
            """
            SELECT rw.inviter_id AS inviter_id,
                   u.username AS inviter_username,
                   u.full_name AS inviter_full_name,
                   COUNT(*) AS referrals,
                   COALESCE(SUM(CASE WHEN rw.reward_type = 'purchase' AND rw.status = 'paid' THEN 1 ELSE 0 END), 0) AS successful,
                   COALESCE(SUM(CASE WHEN rw.status = 'paid' THEN rw.amount_toman ELSE 0 END), 0) AS rewards
            FROM userbot_referrals r
            JOIN userbot_referral_rewards rw ON rw.referral_id = r.id
            LEFT JOIN userbot_users u ON u.id = rw.inviter_id
            WHERE rw.reward_type IN ('trial', 'purchase')
            GROUP BY rw.inviter_id
            ORDER BY successful DESC, rewards DESC
            LIMIT 10
            """
        )
        result["top_referrers"] = [dict(r) for r in cur.fetchall()]
        return result
    finally:
        conn.close()


# اطمینان از وجود جداول در اولین اجرا
init_db()
