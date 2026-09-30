from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
USER_DB = ROOT_DIR / "Shared" / "hiddify_sellbot.db"
AGENCY_DB = ROOT_DIR / "Shared" / "agency.db"
CUSTOMER_DB = ROOT_DIR / "customer_bot.db"
AGENT_BOT_DB = ROOT_DIR / "AgentBot" / "agent_bot.db"


def _connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone() is not None


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return False
    return any(str(row[1]) == str(column) for row in rows)


def _one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> tuple[int, int]:
    row = conn.execute(sql, params).fetchone()
    return (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)


def _utc_bounds(day, tz: ZoneInfo) -> tuple[str, str]:
    start_local = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None).strftime(fmt),
        end_local.astimezone(timezone.utc).replace(tzinfo=None).strftime(fmt),
    )


def _fmt_money(value: int) -> str:
    return f"{int(value or 0):,} تومان"


def _userbot_cash(start: str, end: str) -> dict:
    """Real external money received through UserBot.

    Spending an existing UserBot wallet balance is deliberately excluded:
    wallet spend is a service sale, but it is not new cash received today.
    """
    out = {"approved": 0, "amount": 0, "auto": 0, "manual": 0, "failed": 0}
    conn = _connect(USER_DB)
    if not conn:
        return out
    try:
        if not _has_table(conn, "userbot_payments"):
            return out
        external = "LOWER(COALESCE(method,'')) != 'wallet'"
        out["approved"], out["amount"] = _one(
            conn,
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM userbot_payments "
            f"WHERE status='approved' AND {external} "
            "AND COALESCE(updated_at,created_at)>=? AND COALESCE(updated_at,created_at)<?",
            (start, end),
        )
        out["auto"], _ = _one(
            conn,
            "SELECT COUNT(*), 0 FROM userbot_payments WHERE status='approved' "
            f"AND {external} "
            "AND COALESCE(receipt_image,'') LIKE '%sms_event_id:%' "
            "AND COALESCE(updated_at,created_at)>=? AND COALESCE(updated_at,created_at)<?",
            (start, end),
        )
        out["manual"] = max(0, out["approved"] - out["auto"])
        out["failed"], _ = _one(
            conn,
            "SELECT COUNT(*), 0 FROM userbot_payments "
            f"WHERE {external} AND status IN ('rejected','failed','cancelled') "
            "AND COALESCE(updated_at,created_at)>=? AND COALESCE(updated_at,created_at)<?",
            (start, end),
        )
    except Exception:
        logger.exception("daily report: userbot cash query failed")
    finally:
        conn.close()
    return out


def _agent_wallet_cash(start: str, end: str) -> dict:
    """Actual representative wallet top-ups paid to the system."""
    out = {"approved": 0, "amount": 0, "auto": 0, "manual": 0, "failed": 0}
    conn = _connect(AGENT_BOT_DB)
    if not conn:
        return out
    try:
        if not _has_table(conn, "agent_payments"):
            return out
        base = "description='شارژ کیف پول نماینده'"
        out["approved"], out["amount"] = _one(
            conn,
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM agent_payments "
            f"WHERE status='approved' AND {base} "
            "AND COALESCE(updated_at,created_at)>=? AND COALESCE(updated_at,created_at)<?",
            (start, end),
        )
        if _has_column(conn, "agent_payments", "sms_event_id"):
            out["auto"], _ = _one(
                conn,
                "SELECT COUNT(*), 0 FROM agent_payments "
                f"WHERE status='approved' AND {base} "
                "AND COALESCE(sms_event_id,'')!='' "
                "AND COALESCE(updated_at,created_at)>=? AND COALESCE(updated_at,created_at)<?",
                (start, end),
            )
        out["manual"] = max(0, out["approved"] - out["auto"])
        out["failed"], _ = _one(
            conn,
            "SELECT COUNT(*), 0 FROM agent_payments "
            f"WHERE {base} AND status IN ('rejected','failed','cancelled') "
            "AND COALESCE(updated_at,created_at)>=? AND COALESCE(updated_at,created_at)<?",
            (start, end),
        )
    except Exception:
        logger.exception("daily report: agent wallet cash query failed")
    finally:
        conn.close()
    return out


def _userbot_sales(start: str, end: str) -> dict:
    """Successful UserBot service operations, including wallet-funded sales."""
    out = {
        "buy_count": 0,
        "buy_amount": 0,
        "renew_count": 0,
        "renew_amount": 0,
    }
    conn = _connect(USER_DB)
    if not conn:
        return out
    try:
        if not _has_table(conn, "userbot_orders"):
            return out
        has_renew = _has_column(conn, "userbot_orders", "renew_service_id")
        if has_renew:
            out["buy_count"], out["buy_amount"] = _one(
                conn,
                "SELECT COUNT(*), COALESCE(SUM(price),0) FROM userbot_orders "
                "WHERE LOWER(COALESCE(status,''))='approved' "
                "AND COALESCE(renew_service_id,0)=0 AND created_at>=? AND created_at<?",
                (start, end),
            )
            out["renew_count"], out["renew_amount"] = _one(
                conn,
                "SELECT COUNT(*), COALESCE(SUM(price),0) FROM userbot_orders "
                "WHERE LOWER(COALESCE(status,''))='approved' "
                "AND COALESCE(renew_service_id,0)>0 AND created_at>=? AND created_at<?",
                (start, end),
            )
        else:
            # Legacy DB: orders were inserted only after successful fulfillment,
            # but old schema did not persist whether the operation was a renewal.
            out["buy_count"], out["buy_amount"] = _one(
                conn,
                "SELECT COUNT(*), COALESCE(SUM(price),0) FROM userbot_orders "
                "WHERE LOWER(COALESCE(status,''))='approved' "
                "AND created_at>=? AND created_at<?",
                (start, end),
            )
    except Exception:
        logger.exception("daily report: userbot sales query failed")
    finally:
        conn.close()
    return out


def _customer_wholesale_fallback(start: str, end: str) -> dict[int, int]:
    """Map CustomerBot order_id -> wholesale wallet debit for legacy orders."""
    result: dict[int, int] = {}
    conn = _connect(AGENCY_DB)
    if not conn:
        return result
    try:
        if not _has_table(conn, "agent_transactions"):
            return result
        rows = conn.execute(
            "SELECT amount, description FROM agent_transactions "
            "WHERE tx_type='purchase' AND description LIKE 'کسر عمده سفارش مشتری #%'" 
            "AND created_at>=? AND created_at<?",
            (start, end),
        ).fetchall()
        for row in rows:
            match = re.search(r"#(\d+)", str(row["description"] or ""))
            if not match:
                continue
            oid = int(match.group(1))
            result[oid] = result.get(oid, 0) + int(row["amount"] or 0)
    except Exception:
        logger.exception("daily report: customer wholesale fallback query failed")
    finally:
        conn.close()
    return result


def _customer_sales(start: str, end: str) -> dict:
    """CustomerBot retail sales and the system's wholesale share."""
    out = {
        "buy_count": 0,
        "buy_retail": 0,
        "buy_wholesale": 0,
        "renew_count": 0,
        "renew_retail": 0,
        "renew_wholesale": 0,
    }
    conn = _connect(CUSTOMER_DB)
    if not conn:
        return out
    try:
        if not _has_table(conn, "customer_orders"):
            return out
        has_updated = _has_column(conn, "customer_orders", "updated_at")
        has_wholesale = _has_column(conn, "customer_orders", "wholesale_price")
        has_renew = _has_column(conn, "customer_orders", "renew_service_id")
        stamp = "COALESCE(NULLIF(updated_at,''),created_at)" if has_updated else "created_at"
        wholesale_col = "wholesale_price" if has_wholesale else "0 AS wholesale_price"
        renew_col = "renew_service_id" if has_renew else "0 AS renew_service_id"
        rows = conn.execute(
            f"SELECT order_id, price, {wholesale_col}, {renew_col} FROM customer_orders "
            "WHERE LOWER(COALESCE(status,''))='approved' "
            f"AND {stamp}>=? AND {stamp}<?",
            (start, end),
        ).fetchall()
        fallback = _customer_wholesale_fallback(start, end)
        for row in rows:
            retail = int(row["price"] or 0)
            wholesale = int(row["wholesale_price"] or 0)
            if wholesale <= 0:
                wholesale = int(fallback.get(int(row["order_id"] or 0), 0) or 0)
            is_renew = int(row["renew_service_id"] or 0) > 0
            if is_renew:
                out["renew_count"] += 1
                out["renew_retail"] += retail
                out["renew_wholesale"] += wholesale
            else:
                out["buy_count"] += 1
                out["buy_retail"] += retail
                out["buy_wholesale"] += wholesale
    except Exception:
        logger.exception("daily report: customer sales query failed")
    finally:
        conn.close()
    return out


def _agent_activity(start: str, end: str) -> dict:
    """Successful direct representative operations at wholesale cost.

    CustomerBot wholesale debits are intentionally excluded here because those
    are already counted from customer_orders. This prevents double counting.
    """
    out = {"buy_count": 0, "buy_wholesale": 0, "renew_count": 0, "renew_wholesale": 0}
    conn = _connect(AGENCY_DB)
    if not conn:
        return out
    try:
        if _has_table(conn, "agent_services"):
            out["buy_count"], out["buy_wholesale"] = _one(
                conn,
                "SELECT COUNT(*), COALESCE(SUM(wholesale_price),0) FROM agent_services "
                "WHERE customer_id IS NULL AND COALESCE(is_trial,0)=0 "
                "AND created_at>=? AND created_at<?",
                (start, end),
            )
        if _has_table(conn, "agent_transactions"):
            out["renew_count"], out["renew_wholesale"] = _one(
                conn,
                """
                SELECT COUNT(*), COALESCE(SUM(t.amount),0)
                FROM agent_transactions t
                WHERE t.tx_type='purchase'
                  AND t.description LIKE 'تمدید سرویس:%'
                  AND t.created_at>=? AND t.created_at<?
                  AND NOT EXISTS (
                      SELECT 1 FROM agent_transactions r
                      WHERE r.tx_type='refund'
                        AND r.service_id=t.service_id
                        AND r.amount=t.amount
                        AND r.created_at>=t.created_at
                        AND r.created_at<datetime(t.created_at, '+1 hour')
                        AND r.description LIKE 'بازگشت وجه تمدید ناموفق%'
                  )
                """,
                (start, end),
            )
    except Exception:
        logger.exception("daily report: agent direct activity query failed")
    finally:
        conn.close()
    return out


def build_daily_report(*, tz_name: str = "Asia/Tehran", now: datetime | None = None) -> tuple[str, str]:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Tehran")
        tz_name = "Asia/Tehran"

    local_now = now.astimezone(tz) if now and now.tzinfo else datetime.now(tz)
    report_day = local_now.date() - timedelta(days=1)
    start, end = _utc_bounds(report_day, tz)

    direct_cash = _userbot_cash(start, end)
    agent_wallet_cash = _agent_wallet_cash(start, end)
    user_sales = _userbot_sales(start, end)
    customer = _customer_sales(start, end)
    agent = _agent_activity(start, end)

    cash_count = direct_cash["approved"] + agent_wallet_cash["approved"]
    cash_amount = direct_cash["amount"] + agent_wallet_cash["amount"]
    cash_auto = direct_cash["auto"] + agent_wallet_cash["auto"]
    cash_manual = direct_cash["manual"] + agent_wallet_cash["manual"]
    cash_failed = direct_cash["failed"] + agent_wallet_cash["failed"]

    user_amount = user_sales["buy_amount"] + user_sales["renew_amount"]
    customer_retail = customer["buy_retail"] + customer["renew_retail"]
    customer_wholesale = customer["buy_wholesale"] + customer["renew_wholesale"]
    agent_wholesale = agent["buy_wholesale"] + agent["renew_wholesale"]

    service_count = (
        user_sales["buy_count"] + customer["buy_count"] + agent["buy_count"]
    )
    renew_count = (
        user_sales["renew_count"] + customer["renew_count"] + agent["renew_count"]
    )
    system_service_revenue = user_amount + customer_wholesale + agent_wholesale

    lines = [
        "📊 <b>گزارش کامل روزانه فروش</b>",
        f"📅 تاریخ: <b>{report_day.isoformat()}</b>",
        "",
        "💰 <b>دریافتی واقعی به سیستم</b>",
        f"• پرداخت خارجی UserBot: {direct_cash['approved']} مورد — {_fmt_money(direct_cash['amount'])}",
        f"• شارژ کیف پول نمایندگان: {agent_wallet_cash['approved']} مورد — {_fmt_money(agent_wallet_cash['amount'])}",
        f"• جمع ورودی نقدی: <b>{cash_count} پرداخت — {_fmt_money(cash_amount)}</b>",
        f"• تأیید خودکار SMS: {cash_auto}",
        f"• سایر تأییدها: {cash_manual}",
        f"• رد/ناموفق: {cash_failed}",
        "",
        "🛒 <b>عملیات موفق سرویس</b>",
        f"• ساخت سرویس: <b>{service_count}</b> مورد",
        f"• تمدید سرویس: <b>{renew_count}</b> مورد",
        "",
        "👤 <b>فروش مستقیم UserBot</b>",
        f"• خرید: {user_sales['buy_count']} مورد — {_fmt_money(user_sales['buy_amount'])}",
        f"• تمدید: {user_sales['renew_count']} مورد — {_fmt_money(user_sales['renew_amount'])}",
        f"• جمع فروش UserBot: <b>{_fmt_money(user_amount)}</b>",
        "",
        "🤖 <b>ربات مشتری نمایندگان</b>",
        f"• خرید مشتری: {customer['buy_count']} مورد — فروش {_fmt_money(customer['buy_retail'])} | عمده سیستم {_fmt_money(customer['buy_wholesale'])}",
        f"• تمدید مشتری: {customer['renew_count']} مورد — فروش {_fmt_money(customer['renew_retail'])} | عمده سیستم {_fmt_money(customer['renew_wholesale'])}",
        f"• جمع فروش نمایندگان به مشتری: {_fmt_money(customer_retail)}",
        f"• سهم عمده سیستم: <b>{_fmt_money(customer_wholesale)}</b>",
        "",
        "🤝 <b>عملیات مستقیم نمایندگان</b>",
        f"• خرید: {agent['buy_count']} مورد — عمده {_fmt_money(agent['buy_wholesale'])}",
        f"• تمدید: {agent['renew_count']} مورد — عمده {_fmt_money(agent['renew_wholesale'])}",
        f"• جمع سهم عمده سیستم: <b>{_fmt_money(agent_wholesale)}</b>",
        "",
        "💵 <b>درآمد سرویس‌های سیستم</b>",
        f"• UserBot: {_fmt_money(user_amount)}",
        f"• عمده ربات مشتری نمایندگان: {_fmt_money(customer_wholesale)}",
        f"• عمده عملیات مستقیم نمایندگان: {_fmt_money(agent_wholesale)}",
        f"• جمع درآمد سرویس: <b>{_fmt_money(system_service_revenue)}</b>",
        "",
        "ℹ️ پرداخت از کیف پول کاربر «فروش سرویس» محسوب می‌شود، اما ورودی نقدی جدید نیست.",
        "ℹ️ شارژ کیف پول نماینده در «دریافتی واقعی» ثبت می‌شود؛ مصرف همان اعتبار برای سرویس دوباره به ورودی نقدی اضافه نمی‌شود.",
        f"🕛 گزارش خودکار پایان روز ({tz_name})",
    ]
    return "\n".join(lines), report_day.isoformat()
