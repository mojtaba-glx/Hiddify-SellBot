from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from Shared import agent_db

ROOT_DIR = Path(__file__).resolve().parents[1]
CUSTOMER_DB = ROOT_DIR / "customer_bot.db"


def _bounds(days: Optional[int], tz_name: str = "Asia/Tehran") -> tuple[str, str]:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Tehran")
    now = datetime.now(tz)
    end = now.astimezone(timezone.utc).replace(tzinfo=None)
    if days is None:
        start = datetime(2000, 1, 1)
    elif days == 0:
        local_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        start = (now - timedelta(days=days)).astimezone(timezone.utc).replace(tzinfo=None)
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def get_agent_financial_report(agent_id: int, days: Optional[int] = 0, tz_name: str = "Asia/Tehran") -> Dict[str, Any]:
    """Read-only financial summary for one reseller.

    CustomerBot revenue uses approved customer_orders. Direct AgentBot creations
    use reseller-owned services (customer_id IS NULL). Wallet movements are
    reported separately so wallet charges are never counted as sales income.
    """
    agent_db.init_db()
    start, end = _bounds(days, tz_name)
    out: Dict[str, Any] = {
        "customer_sales": 0, "customer_cost": 0, "customer_buy_count": 0,
        "customer_renew_count": 0, "direct_sales": 0, "direct_cost": 0,
        "direct_count": 0, "wallet_charges": 0, "wallet_purchases": 0,
        "wallet_refunds": 0, "direct_renew_cost": 0,
    }

    # CustomerBot: approved orders contain both actual retail price and wholesale.
    if CUSTOMER_DB.exists():
        conn = sqlite3.connect(str(CUSTOMER_DB), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """SELECT
                    COALESCE(SUM(price),0) revenue,
                    COALESCE(SUM(wholesale_price),0) cost,
                    SUM(CASE WHEN COALESCE(renew_service_id,0)=0 THEN 1 ELSE 0 END) buys,
                    SUM(CASE WHEN COALESCE(renew_service_id,0)>0 THEN 1 ELSE 0 END) renews
                   FROM customer_orders
                   WHERE agent_id=? AND lower(COALESCE(status,''))='approved'
                     AND created_at>=? AND created_at<?""",
                (int(agent_id), start, end),
            ).fetchone()
            if row:
                out["customer_sales"] = int(row["revenue"] or 0)
                out["customer_cost"] = int(row["cost"] or 0)
                out["customer_buy_count"] = int(row["buys"] or 0)
                out["customer_renew_count"] = int(row["renews"] or 0)
        finally:
            conn.close()

    # Direct reseller-created services. CustomerBot-created services have customer_id.
    conn = sqlite3.connect(str(agent_db.DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT COUNT(*) c, COALESCE(SUM(sale_price),0) revenue,
                      COALESCE(SUM(wholesale_price),0) cost
               FROM agent_services
               WHERE agent_id=? AND customer_id IS NULL
                 AND created_at>=? AND created_at<?""",
            (int(agent_id), start, end),
        ).fetchone()
        if row:
            out["direct_count"] = int(row["c"] or 0)
            out["direct_sales"] = int(row["revenue"] or 0)
            out["direct_cost"] = int(row["cost"] or 0)

        rows = conn.execute(
            """SELECT tx_type, COALESCE(SUM(amount),0) amount
               FROM agent_transactions
               WHERE agent_id=? AND created_at>=? AND created_at<?
               GROUP BY tx_type""",
            (int(agent_id), start, end),
        ).fetchall()
        for r in rows:
            kind = str(r["tx_type"] or "").lower()
            amount = int(r["amount"] or 0)
            if kind == "charge":
                out["wallet_charges"] = amount
            elif kind == "purchase":
                out["wallet_purchases"] = amount
            elif kind == "refund":
                out["wallet_refunds"] = amount

        row = conn.execute(
            """SELECT COALESCE(SUM(amount),0) amount
               FROM agent_transactions
               WHERE agent_id=? AND tx_type='purchase'
                 AND description LIKE 'تمدید سرویس:%'
                 AND created_at>=? AND created_at<?""",
            (int(agent_id), start, end),
        ).fetchone()
        out["direct_renew_cost"] = int((row["amount"] if row else 0) or 0)
    finally:
        conn.close()

    out["sales_total"] = out["customer_sales"] + out["direct_sales"]
    out["known_cost"] = out["customer_cost"] + out["direct_cost"]
    out["known_profit"] = out["sales_total"] - out["known_cost"]
    out["wallet_balance"] = int(agent_db.get_wallet_balance(int(agent_id)))
    out["start"] = start
    out["end"] = end
    return out
