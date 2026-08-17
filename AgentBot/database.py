import sqlite3
import json
import re
import random
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

DB_FILE = Path(__file__).with_name("agent_bot.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table) or not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', column):
        raise ValueError(f"Invalid table or column name: {table}.{column}")
    try:
        cur.execute(f"SELECT {column} FROM {table} LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    conn = _conn()
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
            "شارژ کیف پول نماینده",
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


def set_payment_status(payment_id: int, agent_id: int, status: str) -> bool:
    init_db()
    conn = _conn()
    try:
            cur = conn.cursor()
            cur.execute(
            "UPDATE agent_payments SET status=?, updated_at=? WHERE id=? AND agent_id=?",
            (status, _now(), payment_id, agent_id),
            )
            ok = cur.rowcount > 0
            conn.commit()
    finally:
        conn.close()
    return ok


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


CUSTOMER_DB_FILE = Path(__file__).resolve().parents[0] / "customer_bot.db"


def _customer_conn() -> Optional[sqlite3.Connection]:
    db_path = Path(__file__).resolve().parents[1] / "customer_bot.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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


def update_customer_payment_status(agent_id: int, payment_id: int, status: str) -> bool:
    conn = _customer_conn()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT status FROM customer_payments WHERE agent_id=? AND id=?",
            (agent_id, payment_id),
        )
        row = cur.fetchone()
        if not row:
            return False
        if str(dict(row).get("status") or "").strip().lower() == "approved" and str(status).strip().lower() != "approved":
            return False
        now = _now()
        cur.execute(
            "UPDATE customer_payments SET status=?, updated_at=? WHERE agent_id=? AND id=?",
            (status, now, agent_id, payment_id),
        )
        ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


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
