import sqlite3
import json
import re
import random
import uuid
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from Shared import i18n

DB_FILE = Path(__file__).with_name("agent_bot.db")
_init_db_lock = threading.RLock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_FILE), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _conn_cm():
    class _ConnectionContext:
        def __enter__(self):
            self.conn = _conn()
            return self.conn
        def __exit__(self, exc_type, exc_val, exc_tb):
            self.conn.close()
            return False
    return _ConnectionContext()



def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _ensure_column(cur: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', table) or not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', column):
        raise ValueError(f"Invalid table or column name: {table}.{column}")
    columns = {str(row[1]) for row in cur.execute(f"PRAGMA table_info({table})")}
    if column in columns:
        return
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        # CustomerBot and AgentBot can start in separate processes and migrate
        # customer_bot.db at the same time. Accept only a proven race winner.
        columns = {str(row[1]) for row in cur.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            raise


def init_db() -> None:
    _init_db_lock.acquire()
    try:
        conn = _conn()
    except Exception:
        _init_db_lock.release()
        raise
    try:
            cur = conn.cursor()

            cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            bank_name TEXT DEFAULT '',
            card_number TEXT NOT NULL,
            owner_name TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ac_agent ON agent_cards(agent_id)")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            customer_id INTEGER DEFAULT 0,
            customer_name TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_at_agent ON agent_tickets(agent_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_at_status ON agent_tickets(status)")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            sender_type TEXT NOT NULL,
            sender_id INTEGER NOT NULL,
            sender_name TEXT DEFAULT '',
            message TEXT DEFAULT '',
            file_id TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_atm_ticket ON agent_ticket_messages(ticket_id)")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            customer_id INTEGER DEFAULT 0,
            customer_name TEXT DEFAULT '',
            service_id INTEGER DEFAULT 0,
            plan_id INTEGER DEFAULT 0,
            amount INTEGER DEFAULT 0,
            volume_gb REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            order_type TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ao_agent ON agent_orders(agent_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ao_status ON agent_orders(status)")
            _ensure_column(cur, "agent_orders", "volume_gb", "REAL DEFAULT 0")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            customer_id INTEGER DEFAULT 0,
            customer_name TEXT DEFAULT '',
            amount INTEGER DEFAULT 0,
            method TEXT DEFAULT 'card_to_card',
            status TEXT DEFAULT 'pending',
            ref_id TEXT DEFAULT '',
            card_last4 TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
            )
            """)
            _ensure_column(cur, "agent_payments", "receipt_image", "TEXT DEFAULT ''")
            _ensure_column(cur, "agent_payments", "base_amount", "INTEGER DEFAULT 0")
            _ensure_column(cur, "agent_payments", "marker_amount", "INTEGER DEFAULT 0")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_agent ON agent_payments(agent_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_status ON agent_payments(status)")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            customer_id INTEGER DEFAULT 0,
            customer_name TEXT DEFAULT '',
            gift_type TEXT DEFAULT '',
            amount INTEGER DEFAULT 0,
            service_id INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            is_used INTEGER DEFAULT 0,
            expires_at TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ag_agent ON agent_gifts(agent_id)")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_bot_settings (
            agent_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT DEFAULT '',
            PRIMARY KEY (agent_id, key)
            )
            """)

            conn.commit()


    finally:
        conn.close()
        _init_db_lock.release()
def get_setting(agent_id: int, key: str, default: Any = None) -> Any:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM agent_bot_settings WHERE agent_id=? AND key=?", (agent_id, key))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return default
    val = row["value"]
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def set_setting(agent_id: int, key: str, value: Any) -> None:
    init_db()
    payload = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute(
            "INSERT INTO agent_bot_settings (agent_id, key, value) VALUES (?,?,?) "
            "ON CONFLICT(agent_id,key) DO UPDATE SET value=excluded.value",
            (agent_id, key, payload),
            )
            conn.commit()


    finally:
        conn.close()
def get_all_settings(agent_id: int) -> Dict[str, Any]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM agent_bot_settings WHERE agent_id=?", (agent_id,))
            rows = cur.fetchall()
    finally:
        conn.close()
    result = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            result[row["key"]] = row["value"]
    return result


def add_card(agent_id: int, card_number: str, owner_name: str = "", bank_name: str = "") -> Dict[str, Any]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            now = _now()
            cur.execute(
            "INSERT INTO agent_cards (agent_id, bank_name, card_number, owner_name, is_active, created_at, updated_at) "
            "VALUES (?,?,?,?,1,?,?)",
            (agent_id, bank_name.strip(), card_number.strip(), owner_name.strip(), now, now),
            )
            conn.commit()
            cid = cur.lastrowid
            cur.execute("SELECT * FROM agent_cards WHERE id=?", (cid,))
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def update_card(card_id: int, agent_id: int, **updates: Any) -> bool:
    allowed = {"bank_name", "card_number", "owner_name", "is_active"}
    set_parts = []
    values = []
    for k, v in updates.items():
        if k not in allowed:
            continue
        if k == "card_number":
            cleaned = "".join(ch for ch in str(v) if ch.isdigit())
            if len(cleaned) != 16:
                return False
            v = cleaned
        elif k == "is_active":
            v = 1 if v else 0
        elif k in ("bank_name", "owner_name"):
            v = str(v).strip()[:100]
        set_parts.append(f"{k}=?")
        values.append(v)
    if not set_parts:
        return False
    set_parts.append("updated_at=?")
    values.append(_now())
    values.append(card_id)
    values.append(agent_id)
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute(
            f"UPDATE agent_cards SET {', '.join(set_parts)} WHERE id=? AND agent_id=?",
            values,
            )
            ok = cur.rowcount > 0
            conn.commit()
    finally:
        conn.close()
    return ok


def get_cards(agent_id: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM agent_cards WHERE agent_id=? ORDER BY id", (agent_id,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_card(card_id: int, agent_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM agent_cards WHERE id=? AND agent_id=?", (card_id, agent_id))
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def delete_card(card_id: int, agent_id: int) -> bool:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute("DELETE FROM agent_cards WHERE id=? AND agent_id=?", (card_id, agent_id))
            ok = cur.rowcount > 0
            conn.commit()
    finally:
        conn.close()
    return ok


def create_ticket(agent_id: int, customer_id: int, customer_name: str, subject: str) -> Dict[str, Any]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            now = _now()
            cur.execute(
            "INSERT INTO agent_tickets (agent_id, customer_id, customer_name, subject, status, created_at, updated_at) "
            "VALUES (?,?,?,?,'open',?,?)",
            (agent_id, customer_id, customer_name, subject.strip(), now, now),
            )
            conn.commit()
            tid = cur.lastrowid
            cur.execute("SELECT * FROM agent_tickets WHERE id=?", (tid,))
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def get_tickets(agent_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    init_db()
    conn = _conn()
    try:
        cur = conn.cursor()
        if status:
            cur.execute(
                "SELECT * FROM agent_tickets WHERE agent_id=? AND status=? ORDER BY id DESC",
                (agent_id, status),
            )
        else:
            cur.execute(
                "SELECT * FROM agent_tickets WHERE agent_id=? ORDER BY id DESC",
                (agent_id,),
            )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_ticket(ticket_id: int, agent_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM agent_tickets WHERE id=? AND agent_id=?", (ticket_id, agent_id))
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def set_ticket_status(ticket_id: int, agent_id: int, status: str) -> bool:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute(
            "UPDATE agent_tickets SET status=?, updated_at=? WHERE id=? AND agent_id=?",
            (status, _now(), ticket_id, agent_id),
            )
            ok = cur.rowcount > 0
            conn.commit()
    finally:
        conn.close()
    return ok


def add_ticket_message(ticket_id: int, sender_type: str, sender_id: int, sender_name: str, message: str = "", file_id: str = "") -> Dict[str, Any]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            now = _now()
            cur.execute(
            "INSERT INTO agent_ticket_messages (ticket_id, sender_type, sender_id, sender_name, message, file_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (ticket_id, sender_type, sender_id, sender_name, message, file_id, now),
            )
            conn.commit()
            mid = cur.lastrowid
            cur.execute("SELECT * FROM agent_ticket_messages WHERE id=?", (mid,))
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def get_ticket_messages(ticket_id: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM agent_ticket_messages WHERE ticket_id=? ORDER BY id", (ticket_id,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def create_order(agent_id: int, customer_id: int, customer_name: str, amount: int, order_type: str, plan_id: int = 0, description: str = "", volume_gb: float = 0) -> Dict[str, Any]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            now = _now()
            cur.execute(
            "INSERT INTO agent_orders (agent_id, customer_id, customer_name, amount, volume_gb, status, order_type, plan_id, description, created_at, updated_at) "
            "VALUES (?,?,?,?,?,'pending',?,?,?,?,?)",
            (agent_id, customer_id, customer_name, amount, volume_gb, order_type, plan_id, description, now, now),
            )
            conn.commit()
            oid = cur.lastrowid
            cur.execute("SELECT * FROM agent_orders WHERE id=?", (oid,))
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def get_orders(agent_id: int, status: Optional[str] = None, page: int = 1, page_size: int = 10) -> Tuple[List[Dict[str, Any]], int]:
    init_db()
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conn = _customer_conn()
    if not conn:
        return [], 0
    try:
        cur = conn.cursor()
        if status:
            cur.execute("SELECT COUNT(*) AS c FROM customer_orders WHERE agent_id=? AND status=?", (agent_id, status))
            total = int(cur.fetchone()["c"] or 0)
            cur.execute(
                "SELECT * FROM customer_orders WHERE agent_id=? AND status=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (agent_id, status, page_size, offset),
            )
        else:
            cur.execute("SELECT COUNT(*) AS c FROM customer_orders WHERE agent_id=?", (agent_id,))
            total = int(cur.fetchone()["c"] or 0)
            cur.execute(
                "SELECT * FROM customer_orders WHERE agent_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (agent_id, page_size, offset),
            )
        rows = cur.fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def get_order_stats(agent_id: int) -> Dict[str, Any]:
    """آمار سفارشات نماینده (کل، ۳۰ روز گذشته و ماه جاری) — از customer_orders."""
    init_db()
    conn = _customer_conn()
    if not conn:
        return {k: 0 for k in ("total_count","total_gb","total_amount","last30_count","last30_gb","last30_amount","month_count","month_gb","month_amount")}
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(volume_gb), 0) AS gb, COALESCE(SUM(price), 0) AS total FROM customer_orders WHERE agent_id=?",
            (agent_id,),
        )
        total_row = cur.fetchone()

        cur.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(volume_gb), 0) AS gb, COALESCE(SUM(price), 0) AS total FROM customer_orders WHERE agent_id=? AND date(created_at) >= date('now', '-30 days')",
            (agent_id,),
        )
        last30_row = cur.fetchone()

        cur.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(volume_gb), 0) AS gb, COALESCE(SUM(price), 0) AS total FROM customer_orders WHERE agent_id=? AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')",
            (agent_id,),
        )
        month_row = cur.fetchone()
    finally:
        conn.close()

    return {
        "total_count": int(total_row["c"] or 0),
        "total_gb": float(total_row["gb"] or 0),
        "total_amount": int(total_row["total"] or 0),
        "last30_count": int(last30_row["c"] or 0),
        "last30_gb": float(last30_row["gb"] or 0),
        "last30_amount": int(last30_row["total"] or 0),
        "month_count": int(month_row["c"] or 0),
        "month_gb": float(month_row["gb"] or 0),
        "month_amount": int(month_row["total"] or 0),
    }


def get_order_by_id(agent_id: int, order_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _customer_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM customer_orders WHERE agent_id=? AND (id=? OR order_id=?)", (agent_id, int(order_id), int(order_id)))
        row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def search_orders(agent_id: int, query: str, limit: int = 20) -> List[Dict[str, Any]]:
    init_db()
    conn = _customer_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        like = f"%{query}%"
        cur.execute(
            "SELECT * FROM customer_orders WHERE agent_id=? AND (full_name LIKE ? OR CAST(order_id AS TEXT) LIKE ? OR plan_title LIKE ?) "
            "ORDER BY id DESC LIMIT ?",
            (agent_id, like, like, like, limit),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def create_payment(agent_id: int, customer_id: int, customer_name: str, amount: int, method: str = "card_to_card", ref_id: str = "", card_last4: str = "") -> Dict[str, Any]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            now = _now()
            cur.execute(
            "INSERT INTO agent_payments (agent_id, customer_id, customer_name, amount, method, status, ref_id, card_last4, created_at, updated_at) "
            "VALUES (?,?,?,?,?,'pending',?,?,?,?)",
            (agent_id, customer_id, customer_name, amount, method, ref_id, card_last4, now, now),
            )
            conn.commit()
            pid = cur.lastrowid
            cur.execute("SELECT * FROM agent_payments WHERE id=?", (pid,))
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def create_wallet_charge_payment(
    agent_id: int,
    agent_name: str,
    base_amount: int,
    marker_amount: int,
    receipt_file_id: str,
    card_last4: str,
) -> Dict[str, Any]:
    """ثبت شارژ کیف پول نماینده در وضعیت انتظار تایید ادمین."""
    try:
        _lg = i18n.get_agent_lang(int(agent_id or 0))
    except Exception:
        _lg = "fa"
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            now = _now()
            final_amount = int(base_amount or 0) + int(marker_amount or 0)
            ref_id = str(random.randint(1000000, 9999999))
            meta = json.dumps(
            {
            "type": "agent_wallet_charge",
            "receipt_file_id": receipt_file_id,
            "base_amount": int(base_amount or 0),
            "marker_amount": int(marker_amount or 0),
            "final_amount": final_amount,
            "card_last4": str(card_last4 or ""),
            },
            ensure_ascii=False,
            )
            cur.execute(
            """
            INSERT INTO agent_payments (
            agent_id, customer_id, customer_name, amount, method, status, ref_id,
            card_last4, description, receipt_image, base_amount, marker_amount,
            created_at, updated_at
            ) VALUES (?, 0, ?, ?, 'card_to_card', 'pending', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
            agent_id,
            agent_name,
            final_amount,
            ref_id,
            str(card_last4 or ""),
            i18n.t('شارژ کیف پول نماینده', _lg),
            meta,
            int(base_amount or 0),
            int(marker_amount or 0),
            now,
            now,
            ),
            )
            conn.commit()
            pid = cur.lastrowid
            cur.execute("SELECT * FROM agent_payments WHERE id=?", (pid,))
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def get_payments(agent_id: int, status: Optional[str] = None, method: Optional[str] = None, page: int = 1, page_size: int = 10) -> Tuple[List[Dict[str, Any]], int]:
    init_db()
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conditions = ["agent_id=?"]
    params: List[Any] = [agent_id]
    if status:
        conditions.append("status=?")
        params.append(status)
    if method:
        conditions.append("method=?")
        params.append(method)
    where = " AND ".join(conditions)
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) AS c FROM agent_payments WHERE {where}", params)
            total = int(cur.fetchone()["c"] or 0)
            cur.execute(
            f"SELECT * FROM agent_payments WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, page_size, offset],
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows], total


def get_payment_stats(agent_id: int, status: Optional[str] = None, method: Optional[str] = None) -> Dict[str, Any]:
    init_db()
    conditions = ["agent_id=?"]
    params: List[Any] = [agent_id]
    if status:
        conditions.append("status=?")
        params.append(status)
    if method:
        conditions.append("method=?")
        params.append(method)
    base_where = " AND ".join(conditions)
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) AS c, COALESCE(SUM(amount), 0) AS total FROM agent_payments WHERE {base_where}",
            params,
        )
        total_row = cur.fetchone()

        where_30 = base_where + " AND date(created_at) >= date('now', '-30 days')"
        cur.execute(
            f"SELECT COUNT(*) AS c, COALESCE(SUM(amount), 0) AS total FROM agent_payments WHERE {where_30}",
            params,
        )
        last30_row = cur.fetchone()

        where_month = base_where + " AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
        cur.execute(
            f"SELECT COUNT(*) AS c, COALESCE(SUM(amount), 0) AS total FROM agent_payments WHERE {where_month}",
            params,
        )
        month_row = cur.fetchone()
    finally:
        conn.close()

    return {
        "total_count": int(total_row["c"] or 0),
        "total_amount": int(total_row["total"] or 0),
        "last30_count": int(last30_row["c"] or 0),
        "last30_amount": int(last30_row["total"] or 0),
        "month_count": int(month_row["c"] or 0),
        "month_amount": int(month_row["total"] or 0),
    }


def get_payment_by_id(payment_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM agent_payments WHERE id=?", (int(payment_id),))
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_pending_wallet_charge_payments(page: int = 1, page_size: int = 10) -> Tuple[List[Dict[str, Any]], int]:
    init_db()
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute(
            "SELECT COUNT(*) AS c FROM agent_payments WHERE status='pending' AND description='شارژ کیف پول نماینده'"
            )
            total = int(cur.fetchone()["c"] or 0)
            cur.execute(
            "SELECT * FROM agent_payments WHERE status='pending' AND description='شارژ کیف پول نماینده' "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (page_size, offset),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows], total


def search_payments(agent_id: int, query: str, limit: int = 20) -> List[Dict[str, Any]]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            like = f"%{query}%"
            cur.execute(
            "SELECT * FROM agent_payments WHERE agent_id=? AND (customer_name LIKE ? OR CAST(id AS TEXT) LIKE ? OR ref_id LIKE ?) "
            "ORDER BY id DESC LIMIT ?",
            (agent_id, like, like, like, limit),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def set_payment_status(
    payment_id: int,
    agent_id: int,
    status: str,
    *,
    expected_status: Optional[str] = None,
) -> bool:
    """Atomically transition an agent payment; approved is terminal."""
    init_db()
    target = str(status or "").strip().lower()
    if not target:
        return False
    conn = _conn()
    try:
        where = (
            "id=? AND agent_id=? "
            "AND NOT (lower(trim(COALESCE(status,'')))='approved' AND ?!='approved')"
        )
        params: List[Any] = [int(payment_id), int(agent_id), target]
        if expected_status is not None:
            where += " AND lower(trim(COALESCE(status,'')))=?"
            params.append(str(expected_status or "").strip().lower())
        cur = conn.execute(
            f"UPDATE agent_payments SET status=?, updated_at=? WHERE {where}",
            [target, _now(), *params],
        )
        ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


def patch_wallet_charge_payment_meta(payment_id: int, patch: Dict[str, Any]) -> bool:
    """Merge operational metadata without discarding the receipt details."""
    pid = int(payment_id or 0)
    if pid <= 0 or not isinstance(patch, dict):
        return False
    init_db()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT receipt_image FROM agent_payments WHERE id=? LIMIT 1", (pid,)
        ).fetchone()
        if not row:
            return False
        try:
            meta = json.loads(str(row["receipt_image"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        for key, value in patch.items():
            clean_key = str(key or "").strip()
            if not clean_key:
                continue
            if value is None or str(value).strip() == "":
                meta.pop(clean_key, None)
            else:
                meta[clean_key] = value
        cur = conn.execute(
            "UPDATE agent_payments SET receipt_image=?,updated_at=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), _now(), pid),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def _normalized_wallet_card_last4(value: Any) -> str:
    translated = str(value or "").strip().translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    )
    digits = "".join(ch for ch in translated if ch in "0123456789")
    return digits if len(digits) == 4 else ""


def _parse_payment_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except ValueError:
            continue
    return None


def _parse_sms_datetime(value: Any) -> Optional[datetime]:
    try:
        stamp = float(str(value or "").strip())
        if stamp <= 0:
            return None
        if stamp > 10_000_000_000:
            stamp /= 1000.0
        return datetime.fromtimestamp(stamp, timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def find_pending_wallet_charge_payments_by_amount(
    amount_toman: int,
    *,
    card_last4: str = "",
    max_age_minutes: int = 360,
    sms_time_ms: int = 0,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Find pending agent-wallet charges matching one main-bank SMS."""
    init_db()
    amount = int(amount_toman or 0)
    incoming_raw = str(card_last4 or "").strip()
    incoming_last4 = _normalized_wallet_card_last4(incoming_raw)
    if amount <= 0 or (incoming_raw and not incoming_last4):
        return []
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        minutes=max(5, int(max_age_minutes or 360))
    )
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM agent_payments WHERE status='pending' "
            "AND method='card_to_card' AND description='شارژ کیف پول نماینده' "
            "AND amount=? AND COALESCE(created_at,'')>=? ORDER BY id DESC LIMIT ?",
            (
                amount,
                cutoff.strftime("%Y-%m-%d %H:%M:%S"),
                max(1, min(100, int(limit or 20))),
            )
        ).fetchall()
    finally:
        conn.close()

    sms_dt = _parse_sms_datetime(sms_time_ms)
    matched: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        payment_last4 = _normalized_wallet_card_last4(item.get("card_last4"))
        marker = int(item.get("marker_amount") or 0)
        if incoming_last4:
            if not payment_last4 or incoming_last4 != payment_last4:
                continue
        elif not (100 <= marker <= 999):
            continue
        if sms_dt is not None:
            payment_dt = _parse_payment_datetime(item.get("created_at"))
            if payment_dt is None or not (
                payment_dt - timedelta(minutes=30)
                <= sms_dt
                <= payment_dt + timedelta(minutes=30)
            ):
                continue
        matched.append(item)
    return matched


def approve_wallet_charge_payment_once(
    payment_id: int,
    *,
    source: str = "manual",
) -> Dict[str, Any]:
    """Approve and credit one agent wallet charge with crash-safe idempotency."""
    _lg = "fa"
    from Shared import agent_db

    pid = int(payment_id or 0)
    payment = get_payment_by_id(pid)
    if not payment:
        return {"ok": False, "reason": "payment_not_found", "payment": {}}
    aid = int(payment.get("agent_id") or 0)
    amount = int(payment.get("amount") or 0)
    status = str(payment.get("status") or "").strip().lower()
    if (
        aid <= 0
        or amount <= 0
        or str(payment.get("method") or "").strip().lower() != "card_to_card"
        or str(payment.get("description") or "").strip() != "شارژ کیف پول نماینده"
    ):
        return {"ok": False, "reason": "invalid_wallet_charge_payment", "payment": payment}
    if status == "rejected":
        return {"ok": False, "reason": "payment_rejected", "payment": payment}
    if status == "approved":
        ledger = agent_db.get_agent_wallet_payment_credit(aid, pid)
        if not ledger:
            return {
                "ok": False,
                "reason": "legacy_approved_payment_without_idempotent_ledger",
                "payment": payment,
            }
        wallet = agent_db.get_wallet(aid)
        return {
            "ok": True,
            "reason": "already_approved",
            "payment": payment,
            "wallet": wallet,
            "credited_now": False,
        }
    if status == "pending":
        if not set_payment_status(pid, aid, "processing", expected_status="pending"):
            payment = get_payment_by_id(pid) or payment
            status = str(payment.get("status") or "").strip().lower()
        else:
            status = "processing"
    if status == "approved":
        ledger = agent_db.get_agent_wallet_payment_credit(aid, pid)
        if ledger:
            return {
                "ok": True,
                "reason": "already_approved",
                "payment": get_payment_by_id(pid) or payment,
                "wallet": agent_db.get_wallet(aid),
                "credited_now": False,
            }
        return {
            "ok": False,
            "reason": "legacy_approved_payment_without_idempotent_ledger",
            "payment": payment,
        }
    if status != "processing":
        return {"ok": False, "reason": f"payment_status_{status or 'unknown'}", "payment": payment}

    try:
        credited, wallet, credited_now = agent_db.charge_wallet_for_agent_payment_once(
            aid,
            pid,
            amount,
            description=(
                f"{i18n.t('شارژ کارت به کارت نماینده - تراکنش ', _lg)}{payment.get('ref_id')}"
                + (i18n.t(' (تایید خودکار SMS)', _lg) if source == "sms" else "")
            ),
        )
    except Exception as exc:
        set_payment_status(pid, aid, "pending", expected_status="processing")
        return {
            "ok": False,
            "reason": f"wallet_credit_failed:{type(exc).__name__}:{exc}"[:500],
            "payment": get_payment_by_id(pid) or payment,
        }
    if not credited:
        set_payment_status(pid, aid, "pending", expected_status="processing")
        return {"ok": False, "reason": "wallet_credit_failed", "payment": payment}

    finalized = set_payment_status(pid, aid, "approved", expected_status="processing")
    latest = get_payment_by_id(pid) or payment
    if not finalized and str(latest.get("status") or "").strip().lower() != "approved":
        return {
            "ok": False,
            "reason": "wallet_credited_payment_finalize_pending",
            "payment": latest,
            "wallet": wallet,
            "credited_now": credited_now,
        }
    return {
        "ok": True,
        "reason": "approved",
        "payment": latest,
        "wallet": wallet,
        "credited_now": credited_now,
    }


def approve_wallet_charge_payment_from_sms_event(
    payment_id: int,
    event: Dict[str, Any],
    *,
    expected_event_status: str,
) -> Dict[str, Any]:
    """Claim one SMS event, credit the wallet once, then finalize the event."""
    from Shared import userbot_db

    pid = int(payment_id or 0)
    event_id = str((event or {}).get("event_id") or "").strip()
    amount_toman = int((event or {}).get("amount_toman") or 0)
    if pid <= 0 or not event_id or event_id.startswith("agency:"):
        return {"ok": False, "reason": "invalid_main_sms_event"}
    if not userbot_db.transition_sms_webhook_event(
        event_id,
        expected_status=expected_event_status,
        new_status="agent_wallet_processing",
        matched_payment_id=pid,
        message="agent wallet payment claimed by SMS",
        amount_toman=amount_toman,
    ):
        return {"ok": False, "reason": "sms_event_claim_failed"}

    result = approve_wallet_charge_payment_once(pid, source="sms")
    if result.get("ok"):
        patch_wallet_charge_payment_meta(
            pid,
            {
                "sms_event_id": event_id,
                "sms_reference": str((event or {}).get("reference") or ""),
                "sms_sender": str((event or {}).get("sender") or ""),
                "sms_amount_raw": int((event or {}).get("amount_raw") or 0),
                "sms_currency": str((event or {}).get("currency_raw") or ""),
            },
        )
        userbot_db.transition_sms_webhook_event(
            event_id,
            expected_status="agent_wallet_processing",
            new_status="agent_wallet_approved",
            matched_payment_id=pid,
            expected_matched_payment_id=pid,
            message="agent wallet payment approved by bank SMS",
            amount_toman=amount_toman,
        )
        result["event"] = event
        return result

    reason = str(result.get("reason") or "")
    if reason != "wallet_credited_payment_finalize_pending":
        userbot_db.transition_sms_webhook_event(
            event_id,
            expected_status="agent_wallet_processing",
            new_status=expected_event_status,
            matched_payment_id=0,
            expected_matched_payment_id=pid,
            message=reason,
            amount_toman=amount_toman,
        )
    result["event"] = event
    return result


def try_approve_wallet_charge_from_unmatched_sms(
    payment_id: int,
    *,
    max_age_minutes: int = 360,
    receipt_lookback_minutes: int = 30,
) -> Dict[str, Any]:
    """Handle the normal ordering where bank SMS arrives before the receipt."""
    from Shared import userbot_db

    payment = get_payment_by_id(int(payment_id or 0))
    if not payment:
        return {"ok": False, "reason": "payment_not_found"}
    if str(payment.get("status") or "").strip().lower() != "pending":
        return {"ok": False, "reason": "payment_not_pending", "payment": payment}
    marker = int(payment.get("marker_amount") or 0)
    events = userbot_db.find_unmatched_main_sms_webhook_events(
        int(payment.get("amount") or 0),
        payment_created_at=str(payment.get("created_at") or ""),
        payment_card_last4=str(payment.get("card_last4") or ""),
        allow_pre_receipt_without_last4=100 <= marker <= 999,
        max_age_minutes=max_age_minutes,
        receipt_lookback_minutes=receipt_lookback_minutes,
    )
    if not events:
        return {"ok": False, "reason": "no_unmatched_sms", "payment": payment}
    if len(events) != 1:
        return {"ok": False, "reason": "ambiguous_unmatched_sms", "payment": payment}
    event = dict(events[0])
    event["amount_toman"] = int(payment.get("amount") or 0)
    return approve_wallet_charge_payment_from_sms_event(
        int(payment["id"]), event, expected_event_status="no_pending_match"
    )


def recover_processing_agent_wallet_sms_payments(limit: int = 100) -> int:
    """Finish SMS approvals interrupted between event claim and finalization.

    The wallet ledger reference is unique, so retrying after either process
    stops cannot add the same charge twice.
    """
    from Shared import userbot_db

    recovered = 0
    events = userbot_db.get_sms_webhook_events_by_status(
        "agent_wallet_processing", limit=limit
    )
    for event in events:
        event_id = str(event.get("event_id") or "").strip()
        payment_id = int(event.get("matched_payment_id") or 0)
        if not event_id or event_id.startswith("agency:") or payment_id <= 0:
            userbot_db.transition_sms_webhook_event(
                event_id,
                expected_status="agent_wallet_processing",
                new_status="agent_wallet_manual_review",
                matched_payment_id=payment_id,
                expected_matched_payment_id=payment_id,
                message="invalid interrupted agent wallet SMS approval",
            )
            continue

        result = approve_wallet_charge_payment_once(payment_id, source="sms")
        if not result.get("ok"):
            reason = str(result.get("reason") or "recovery_failed")
            if reason == "wallet_credited_payment_finalize_pending":
                continue
            userbot_db.transition_sms_webhook_event(
                event_id,
                expected_status="agent_wallet_processing",
                new_status="agent_wallet_manual_review",
                matched_payment_id=payment_id,
                expected_matched_payment_id=payment_id,
                message=reason,
            )
            continue

        patch_wallet_charge_payment_meta(
            payment_id,
            {
                "sms_event_id": event_id,
                "sms_reference": str(event.get("reference") or ""),
                "sms_sender": str(event.get("sender") or ""),
                "sms_amount_raw": int(event.get("amount_raw") or 0),
                "sms_currency": str(event.get("currency_raw") or ""),
            },
        )
        if userbot_db.transition_sms_webhook_event(
            event_id,
            expected_status="agent_wallet_processing",
            new_status="agent_wallet_approved",
            matched_payment_id=payment_id,
            expected_matched_payment_id=payment_id,
            message="interrupted agent wallet SMS approval recovered",
            amount_toman=int(event.get("amount_toman") or 0),
        ):
            recovered += 1
    return recovered


def add_gift(agent_id: int, customer_id: int, customer_name: str, gift_type: str, amount: int = 0, description: str = "") -> Dict[str, Any]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            now = _now()
            cur.execute(
            "INSERT INTO agent_gifts (agent_id, customer_id, customer_name, gift_type, amount, description, is_used, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,0,?,?)",
            (agent_id, customer_id, customer_name, gift_type, amount, description, now, now),
            )
            conn.commit()
            gid = cur.lastrowid
            cur.execute("SELECT * FROM agent_gifts WHERE id=?", (gid,))
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def get_gifts(agent_id: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM agent_gifts WHERE agent_id=? ORDER BY id DESC", (agent_id,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


CUSTOMER_DB_FILE = Path(__file__).resolve().parents[1] / "customer_bot.db"
_customer_schema_ready_path = ""
_customer_schema_lock = threading.Lock()


def _ensure_customer_payment_runtime_schema(conn: sqlite3.Connection) -> None:
    """Install the additive columns used by the durable payment worker."""
    global _customer_schema_ready_path
    current_path = str(CUSTOMER_DB_FILE)
    with _customer_schema_lock:
        if _customer_schema_ready_path == current_path:
            return
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1 FROM customer_payments LIMIT 1")
        except sqlite3.OperationalError:
            return
        for column, definition in (
            ("processing_token", "TEXT DEFAULT ''"),
            ("processing_started_at", "TEXT DEFAULT ''"),
            ("processing_previous_status", "TEXT DEFAULT ''"),
            ("processing_stage", "TEXT DEFAULT ''"),
            ("processing_note", "TEXT DEFAULT ''"),
        ):
            _ensure_column(cur, "customer_payments", column, definition)
        conn.commit()
        _customer_schema_ready_path = current_path


def _customer_conn() -> Optional[sqlite3.Connection]:
    db_path = CUSTOMER_DB_FILE
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=20000")
    _ensure_customer_payment_runtime_schema(conn)
    return conn


def _enrich_payment_row(cur, p: Dict[str, Any]) -> None:
    """متادیتای پرداخت (سفارش، قیمت عمده و...) را از receipt_image پر می‌کند."""
    p["_order_id"] = 0
    p["_pay_type"] = "buy"
    p["_order"] = None
    p["_server_id"] = 0
    p["_gb"] = 0
    p["_days"] = 0
    p["_sale_price"] = int(p.get("amount") or 0)
    p["_wholesale_price"] = 0
    raw = p.get("receipt_image", "")
    try:
        raw_str = raw if isinstance(raw, str) else ""
        json_part = raw_str.split("|", 1)[0].strip() if raw_str else ""
        meta = json.loads(json_part) if json_part.startswith("{") else {}
        if isinstance(meta, dict):
            if meta.get("type") == "buy" or meta.get("order_id"):
                p["_order_id"] = int(meta.get("order_id", 0))
                p["_pay_type"] = "buy"
            p["_server_id"] = meta.get("server_id", 0)
            p["_gb"] = meta.get("gb", 0)
            p["_days"] = meta.get("days", 0)
            p["_sale_price"] = int(meta.get("sale_price") or p.get("amount") or 0)
            p["_wholesale_price"] = int(meta.get("wholesale_price") or 0)
            p["_card_last4"] = str(meta.get("card_last4") or "").strip()
    except (json.JSONDecodeError, TypeError):
        pass
    if not p["_order_id"]:
        ikey = p.get("idempotency_key", "")
        if ikey.startswith("receipt_"):
            parts = ikey.split("_")
            if len(parts) >= 3:
                try:
                    p["_order_id"] = int(parts[2])
                    if p["_order_id"] > 0:
                        p["_pay_type"] = "buy"
                except ValueError:
                    pass
    if p["_order_id"]:
        try:
            cur.execute(
                "SELECT * FROM customer_orders WHERE agent_id=? AND order_id=?",
                (int(p.get("agent_id") or 0), p["_order_id"]),
            )
            order_row = cur.fetchone()
            if order_row:
                p["_order"] = dict(order_row)
                if not int(p["_order"].get("server_id") or 0):
                    p["_order"]["server_id"] = int(p.get("_server_id") or 0)
                if not int(p["_order"].get("wholesale_price") or 0):
                    p["_order"]["wholesale_price"] = int(p.get("_wholesale_price") or 0)
        except Exception:
            pass


def get_customer_pending_card_payments(agent_id: int) -> List[Dict[str, Any]]:
    conn = _customer_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT cp.*, cu.full_name, cu.username FROM customer_payments cp "
            "LEFT JOIN customer_users cu ON cu.agent_id=cp.agent_id AND cu.telegram_id=cp.user_id "
            "WHERE cp.agent_id=? AND cp.status='pending' AND cp.method='card' "
            "ORDER BY cp.created_at DESC LIMIT 50",
            (agent_id,),
        )
        rows = cur.fetchall()
        result = [dict(r) for r in rows]
        for p in result:
            _enrich_payment_row(cur, p)
        return result
    finally:
        conn.close()


def get_customer_payment_by_id_enriched(agent_id: int, payment_id: int) -> Optional[Dict[str, Any]]:
    conn = _customer_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT cp.*, cu.full_name, cu.username FROM customer_payments cp "
            "LEFT JOIN customer_users cu ON cu.agent_id=cp.agent_id AND cu.telegram_id=cp.user_id "
            "WHERE cp.agent_id=? AND cp.id=?",
            (agent_id, int(payment_id)),
        )
        row = cur.fetchone()
        if not row:
            return None
        p = dict(row)
        _enrich_payment_row(cur, p)
        return p
    except Exception:
        return None
    finally:
        conn.close()


def update_customer_payment_status(
    agent_id: int,
    payment_id: int,
    status: str,
    *,
    expected_status: Optional[str] = None,
) -> bool:
    """Atomically transition a customer payment status.

    ``expected_status`` turns the update into a compare-and-swap operation.  It
    is used by payment approval to guarantee that only one worker can claim a
    pending payment.  Approved payments remain immutable for legacy callers.
    """
    conn = _customer_conn()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        new_status = str(status or "").strip().lower()
        if not new_status:
            return False
        if expected_status is not None:
            expected_guard = str(expected_status or "").strip().lower()
            # Approved is a terminal financial state, including for CAS callers.
            if expected_guard == "approved" and new_status != "approved":
                return False
        now = _now()
        if expected_status is not None:
            expected = str(expected_status or "").strip().lower()
            cur.execute(
                "UPDATE customer_payments SET status=?, updated_at=? "
                "WHERE agent_id=? AND id=? AND lower(trim(COALESCE(status, '')))=?",
                (new_status, now, agent_id, payment_id, expected),
            )
        else:
            cur.execute(
                "UPDATE customer_payments SET status=?, updated_at=? "
                "WHERE agent_id=? AND id=? "
                "AND NOT (lower(trim(COALESCE(status, '')))='approved' AND ?!='approved')",
                (new_status, now, agent_id, payment_id, new_status),
            )
        ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


def claim_customer_payment_processing(
    agent_id: int,
    payment_id: int,
    *,
    expected_status: str = "pending",
    source: str = "manual",
) -> str:
    """Atomically claim a payment and return its unguessable processing token."""
    expected = str(expected_status or "").strip().lower()
    if expected not in {"pending", "rejected"}:
        return ""
    token = uuid.uuid4().hex
    conn = _customer_conn()
    if not conn:
        return ""
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = _now()
        cur = conn.execute(
            "UPDATE customer_payments SET status='processing', processing_token=?, "
            "processing_started_at=?, processing_previous_status=?, processing_stage='claimed', "
            "processing_note=?, updated_at=? "
            "WHERE agent_id=? AND id=? AND lower(trim(COALESCE(status, '')))=?",
            (
                token,
                now,
                expected,
                str(source or "manual")[:100],
                now,
                int(agent_id or 0),
                int(payment_id or 0),
                expected,
            ),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return ""
        conn.commit()
        return token
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_customer_payment_processing_stage(
    agent_id: int,
    payment_id: int,
    processing_token: str,
    stage: str,
    note: str = "",
) -> bool:
    """Heartbeat/update a claim; a stale owner cannot mutate the payment."""
    token = str(processing_token or "").strip()
    if not token:
        return False
    conn = _customer_conn()
    if not conn:
        return False
    try:
        now = _now()
        cur = conn.execute(
            "UPDATE customer_payments SET processing_stage=?, processing_note=?, "
            "processing_started_at=?, updated_at=? "
            "WHERE agent_id=? AND id=? AND status='processing' AND processing_token=?",
            (
                str(stage or "")[:80],
                str(note or "")[:500],
                now,
                now,
                int(agent_id or 0),
                int(payment_id or 0),
                token,
            ),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def finish_customer_payment_processing(
    agent_id: int,
    payment_id: int,
    processing_token: str,
    note: str = "",
) -> bool:
    """Commit approved only for the process that owns the current claim."""
    token = str(processing_token or "").strip()
    if not token:
        return False
    conn = _customer_conn()
    if not conn:
        return False
    try:
        now = _now()
        cur = conn.execute(
            "UPDATE customer_payments SET status='approved', processing_stage='completed', "
            "processing_note=?, updated_at=? "
            "WHERE agent_id=? AND id=? AND status='processing' AND processing_token=?",
            (str(note or "")[:500], now, int(agent_id or 0), int(payment_id or 0), token),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def release_customer_payment_processing(
    agent_id: int,
    payment_id: int,
    processing_token: str,
    *,
    note: str = "",
) -> bool:
    """Release to the exact pre-claim state without touching wallet reservation."""
    token = str(processing_token or "").strip()
    if not token:
        return False
    conn = _customer_conn()
    if not conn:
        return False
    try:
        now = _now()
        cur = conn.execute(
            "UPDATE customer_payments SET "
            "status=CASE WHEN processing_previous_status='rejected' THEN 'rejected' ELSE 'pending' END, "
            "processing_token='', processing_stage='retry', processing_note=?, updated_at=? "
            "WHERE agent_id=? AND id=? AND status='processing' AND processing_token=?",
            (str(note or "")[:500], now, int(agent_id or 0), int(payment_id or 0), token),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def mark_customer_payment_manual_review(
    agent_id: int,
    payment_id: int,
    *,
    processing_token: str = "",
    note: str = "",
) -> bool:
    """Freeze an ambiguous processing payment for explicit human review."""
    conn = _customer_conn()
    if not conn:
        return False
    try:
        sql = (
            "UPDATE customer_payments SET processing_stage='manual_review', processing_note=?, updated_at=? "
            "WHERE agent_id=? AND id=? AND status='processing'"
        )
        params: List[Any] = [
            str(note or "")[:500],
            _now(),
            int(agent_id or 0),
            int(payment_id or 0),
        ]
        token = str(processing_token or "").strip()
        if token:
            sql += " AND processing_token=?"
            params.append(token)
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def get_stale_customer_processing_payments(age_minutes: int = 10, limit: int = 20) -> List[Dict[str, Any]]:
    """Return stale processing payments for crash recovery, oldest first."""
    conn = _customer_conn()
    if not conn:
        return []
    try:
        cutoff = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(minutes=max(1, int(age_minutes or 10)))
        ).strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            "SELECT agent_id, id FROM customer_payments "
            "WHERE status='processing' AND (COALESCE(processing_started_at, '')='' "
            "OR processing_started_at<=?) "
            "AND lower(trim(COALESCE(processing_stage, '')))!='manual_review' "
            "ORDER BY processing_started_at, id LIMIT ?",
            (cutoff, max(1, min(100, int(limit or 20)))),
        )
        keys = [(int(row["agent_id"]), int(row["id"])) for row in cur.fetchall()]
    finally:
        conn.close()
    result: List[Dict[str, Any]] = []
    for row_agent_id, row_payment_id in keys:
        enriched = get_customer_payment_by_id_enriched(row_agent_id, row_payment_id)
        if enriched:
            result.append(enriched)
    return result


def get_customer_order_by_id(agent_id: int, order_id: int) -> Optional[Dict[str, Any]]:
    conn = _customer_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM customer_orders WHERE agent_id=? AND order_id=?",
            (agent_id, order_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_customer_user(agent_id: int, telegram_id: int) -> Optional[Dict[str, Any]]:
    conn = _customer_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM customer_users WHERE agent_id=? AND telegram_id=?",
            (agent_id, telegram_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_customer_user(agent_id: int, telegram_id: int, username: str = "", full_name: str = "") -> Optional[int]:
    """ایجاد یا به‌روزرسانی ردیف مشتری در customer_users (در صورت نبود، می‌سازد).

    مشابه upsert_user ربات مشتری؛ برای زمانی استفاده می‌شود که مشتری هنوز
    در customer_users ثبت نشده اما پرداختش در انتظار تایید است.
    """
    conn = _customer_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS customer_users ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " agent_id INTEGER NOT NULL,"
            " telegram_id INTEGER NOT NULL,"
            " username TEXT,"
            " full_name TEXT,"
            " created_at TEXT,"
            " UNIQUE(agent_id, telegram_id))"
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "SELECT id FROM customer_users WHERE agent_id=? AND telegram_id=?",
            (agent_id, telegram_id),
        )
        row = cur.fetchone()
        if row:
            uid = row["id"]
            cur.execute(
                "UPDATE customer_users SET username=?, full_name=? WHERE id=?",
                (username, full_name, uid),
            )
        else:
            cur.execute(
                "INSERT INTO customer_users (agent_id, telegram_id, username, full_name, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (agent_id, telegram_id, username, full_name, now),
            )
            uid = cur.lastrowid
        conn.commit()
        return int(uid)
    except Exception:
        return None
    finally:
        conn.close()


def get_customer_payments(
    agent_id: int,
    status: Optional[str] = None,
    method: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    user_id: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    conn = _customer_conn()
    if not conn:
        return [], 0
    if page < 1:
        page = 1
    offset = (page - 1) * page_size
    conditions = ["cp.agent_id=?"]
    params: List[Any] = [agent_id]
    if status:
        conditions.append("cp.status=?")
        params.append(status)
    if method:
        conditions.append("cp.method=?")
        params.append(method)
    if user_id is not None:
        conditions.append("cp.user_id=?")
        params.append(user_id)
    where = " AND ".join(conditions)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) AS c FROM customer_payments cp WHERE {where}",
            params,
        )
        total = int(cur.fetchone()["c"] or 0)
        cur.execute(
            f"SELECT cp.*, cu.full_name, cu.username FROM customer_payments cp "
            f"LEFT JOIN customer_users cu ON cu.agent_id=cp.agent_id AND cu.telegram_id=cp.user_id "
            f"WHERE {where} ORDER BY cp.id DESC LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows], total


def get_customer_payment_stats(
    agent_id: int,
    status: Optional[str] = None,
    method: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    conn = _customer_conn()
    if not conn:
        return {
            "total_count": 0, "total_amount": 0,
            "last30_count": 0, "last30_amount": 0,
            "month_count": 0, "month_amount": 0,
        }
    conditions = ["cp.agent_id=?"]
    params: List[Any] = [agent_id]
    if status:
        conditions.append("cp.status=?")
        params.append(status)
    if method:
        conditions.append("cp.method=?")
        params.append(method)
    if user_id is not None:
        conditions.append("cp.user_id=?")
        params.append(user_id)
    base_where = " AND ".join(conditions)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) AS c, COALESCE(SUM(cp.amount), 0) AS total "
            f"FROM customer_payments cp WHERE {base_where}",
            params,
        )
        total_row = cur.fetchone()
        where_30 = base_where + " AND date(cp.created_at) >= date('now', '-30 days')"
        cur.execute(
            f"SELECT COUNT(*) AS c, COALESCE(SUM(cp.amount), 0) AS total "
            f"FROM customer_payments cp WHERE {where_30}",
            params,
        )
        last30_row = cur.fetchone()
        where_month = base_where + " AND strftime('%Y-%m', cp.created_at) = strftime('%Y-%m', 'now')"
        cur.execute(
            f"SELECT COUNT(*) AS c, COALESCE(SUM(cp.amount), 0) AS total "
            f"FROM customer_payments cp WHERE {where_month}",
            params,
        )
        month_row = cur.fetchone()
    finally:
        conn.close()
    return {
        "total_count": int(total_row["c"] or 0),
        "total_amount": int(total_row["total"] or 0),
        "last30_count": int(last30_row["c"] or 0),
        "last30_amount": int(last30_row["total"] or 0),
        "month_count": int(month_row["c"] or 0),
        "month_amount": int(month_row["total"] or 0),
    }


def search_customer_payments(agent_id: int, query: str, limit: int = 20) -> List[Dict[str, Any]]:
    conn = _customer_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        like = f"%{query}%"
        cur.execute(
            "SELECT cp.*, cu.full_name, cu.username FROM customer_payments cp "
            "LEFT JOIN customer_users cu ON cu.agent_id=cp.agent_id AND cu.telegram_id=cp.user_id "
            "WHERE cp.agent_id=? AND (cu.full_name LIKE ? OR cu.username LIKE ? OR CAST(cp.id AS TEXT) LIKE ? OR cp.tx_code LIKE ?) "
            "ORDER BY cp.id DESC LIMIT ?",
            (agent_id, like, like, like, like, limit),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_customer_payment_detail(payment_id: int) -> Optional[Dict[str, Any]]:
    conn = _customer_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT cp.*, cu.full_name, cu.username FROM customer_payments cp "
            "LEFT JOIN customer_users cu ON cu.agent_id=cp.agent_id AND cu.telegram_id=cp.user_id "
            "WHERE cp.id=?",
            (int(payment_id),),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_fixed_categories(agent_id: int) -> List[Dict[str, Any]]:
    return get_setting(agent_id, "fixed_categories", [])


def set_fixed_categories(agent_id: int, cats: List[Dict[str, Any]]) -> None:
    set_setting(agent_id, "fixed_categories", cats)


def add_fixed_category(agent_id: int, title: str, priority: int = 0) -> Dict[str, Any]:
    cats = get_fixed_categories(agent_id)
    next_id = max((c["id"] for c in cats), default=0) + 1
    cat = {"id": next_id, "title": title, "priority": priority}
    cats.append(cat)
    set_fixed_categories(agent_id, cats)
    return cat


def edit_fixed_category(agent_id: int, cat_id: int, **kw) -> bool:
    cats = get_fixed_categories(agent_id)
    for c in cats:
        if c["id"] == cat_id:
            c.update(kw)
            set_fixed_categories(agent_id, cats)
            return True
    return False


def delete_fixed_category(agent_id: int, cat_id: int) -> bool:
    cats = get_fixed_categories(agent_id)
    new_cats = [c for c in cats if c["id"] != cat_id]
    if len(new_cats) == len(cats):
        return False
    set_fixed_categories(agent_id, new_cats)
    plans = get_fixed_plans(agent_id)
    plans = [p for p in plans if p.get("category_id") != cat_id]
    set_fixed_plans(agent_id, plans)
    return True


def get_fixed_plans(agent_id: int, category_id: Optional[int] = None) -> List[Dict[str, Any]]:
    all_plans = get_setting(agent_id, "fixed_plans", [])
    if category_id is not None:
        return [p for p in all_plans if p.get("category_id") == category_id]
    return all_plans


def set_fixed_plans(agent_id: int, plans: List[Dict[str, Any]]) -> None:
    set_setting(agent_id, "fixed_plans", plans)


def get_fixed_plan(agent_id: int, plan_id: int) -> Optional[Dict[str, Any]]:
    for p in get_fixed_plans(agent_id):
        if p["id"] == plan_id:
            return p
    return None


def add_fixed_plan(agent_id: int, category_id: int, title: str, price: int, days: int, gb: float) -> Dict[str, Any]:
    plans = get_fixed_plans(agent_id)
    next_id = max((p["id"] for p in plans), default=0) + 1
    plan = {"id": next_id, "category_id": category_id, "title": title, "price": price, "days": days, "gb": gb}
    plans.append(plan)
    set_fixed_plans(agent_id, plans)
    return plan


def delete_fixed_plan(agent_id: int, plan_id: int) -> bool:
    plans = get_fixed_plans(agent_id)
    new_plans = [p for p in plans if p["id"] != plan_id]
    if len(new_plans) == len(plans):
        return False
    set_fixed_plans(agent_id, new_plans)
    return True


# ---- Customer payment approval ----
def get_customer_pending_payments(agent_id: int) -> List[Dict[str, Any]]:
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute("""
            SELECT * FROM agent_payments
            WHERE agent_id=? AND status='pending' AND method='card_to_card'
            ORDER BY created_at DESC
            """, (agent_id,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def update_agent_payment_status(agent_id: int, payment_id: int, new_status: str) -> bool:
    conn = _conn()
    try:
            cur = conn.cursor()
            now = _now()
            cur.execute(
            "UPDATE agent_payments SET status=?, updated_at=? WHERE id=? AND agent_id=?",
            (new_status, now, payment_id, agent_id),
            )
            ok = cur.rowcount > 0
            conn.commit()
    finally:
        conn.close()
    return ok


def get_customer_payment_by_tx(agent_id: int, tx_code: str):
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute(
            "SELECT * FROM agent_payments WHERE agent_id=? AND ref_id=?",
            (agent_id, tx_code),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# ---- Customer ticket cross-DB ----
def get_customer_tickets(agent_id: int, status: str = None) -> List[Dict[str, Any]]:
    conn = _customer_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        if status:
            cur.execute(
                "SELECT * FROM customer_tickets WHERE agent_id=? AND status=? ORDER BY created_at DESC LIMIT 50",
                (agent_id, status),
            )
        else:
            cur.execute(
                "SELECT * FROM customer_tickets WHERE agent_id=? ORDER BY created_at DESC LIMIT 50",
                (agent_id,),
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_customer_ticket(agent_id: int, ticket_code: int) -> Optional[Dict[str, Any]]:
    conn = _customer_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM customer_tickets WHERE agent_id=? AND ticket_code=?",
            (agent_id, ticket_code),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_customer_ticket_messages(agent_id: int, ticket_code: int) -> List[Dict[str, Any]]:
    conn = _customer_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM customer_ticket_messages WHERE agent_id=? AND ticket_code=? ORDER BY id ASC",
            (agent_id, ticket_code),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def add_customer_ticket_message(agent_id: int, ticket_code: int, sender_type: str, sender_name: str, message: str, photo_file_id: str = "") -> bool:
    conn = _customer_conn()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        now = _now()
        cur.execute(
            "INSERT INTO customer_ticket_messages (agent_id, ticket_code, sender_type, sender_name, message_text, photo_file_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (agent_id, ticket_code, sender_type, sender_name, message, photo_file_id, now),
        )
        cur.execute(
            "UPDATE customer_tickets SET status='open', updated_at=? WHERE agent_id=? AND ticket_code=?",
            (now, agent_id, ticket_code),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def set_customer_ticket_status(agent_id: int, ticket_code: int, status: str) -> bool:
    conn = _customer_conn()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        now = _now()
        cur.execute(
            "UPDATE customer_tickets SET status=?, updated_at=? WHERE agent_id=? AND ticket_code=?",
            (status, now, agent_id, ticket_code),
        )
        ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()
