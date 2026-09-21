from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
USER_DB = ROOT_DIR / "Shared" / "hiddify_sellbot.db"
AGENCY_DB = ROOT_DIR / "Shared" / "agency.db"
CUSTOMER_DB = ROOT_DIR / "customer_bot.db"


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
    out = {"approved": 0, "amount": 0, "auto": 0, "manual": 0, "failed": 0}
    conn = _connect(USER_DB)
    if not conn:
        return out
    try:
        if not _has_table(conn, "userbot_payments"):
            return out
        out["approved"], out["amount"] = _one(
            conn,
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM userbot_payments "
            "WHERE status='approved' AND COALESCE(updated_at,created_at)>=? "
            "AND COALESCE(updated_at,created_at)<?",
            (start, end),
        )
        out["auto"], _ = _one(
            conn,
            "SELECT COUNT(*), 0 FROM userbot_payments WHERE status='approved' "
            "AND COALESCE(receipt_image,'') LIKE '%sms_event_id:%' "
            "AND COALESCE(updated_at,created_at)>=? AND COALESCE(updated_at,created_at)<?",
            (start, end),
        )
        out["manual"] = max(0, out["approved"] - out["auto"])
        out["failed"], _ = _one(
            conn,
            "SELECT COUNT(*), 0 FROM userbot_payments "
            "WHERE status IN ('rejected','failed','cancelled') "
            "AND COALESCE(updated_at,created_at)>=? AND COALESCE(updated_at,created_at)<?",
            (start, end),
        )
    except Exception:
        logger.exception("daily report: userbot cash query failed")
    finally:
        conn.close()
    return out


def _userbot_sales(start: str, end: str) -> dict:
    out = {"count": 0, "amount": 0}
    conn = _connect(USER_DB)
    if not conn:
        return out
    try:
        if not _has_table(conn, "userbot_orders"):
            return out
        out["count"], out["amount"] = _one(
            conn,
            "SELECT COUNT(*), COALESCE(SUM(price),0) FROM userbot_orders "
            "WHERE created_at>=? AND created_at<?",
            (start, end),
        )
    except Exception:
        logger.exception("daily report: userbot sales query failed")
    finally:
        conn.close()
    return out


def _customer_sales(start: str, end: str) -> dict:
    out = {"buy_count": 0, "buy_amount": 0, "renew_count": 0, "renew_amount": 0}
    conn = _connect(CUSTOMER_DB)
    if not conn:
        return out
    try:
        if not _has_table(conn, "customer_orders"):
            return out
        out["buy_count"], out["buy_amount"] = _one(
            conn,
            "SELECT COUNT(*), COALESCE(SUM(price),0) FROM customer_orders "
            "WHERE status='approved' AND COALESCE(renew_service_id,0)=0 "
            "AND created_at>=? AND created_at<?",
            (start, end),
        )
        out["renew_count"], out["renew_amount"] = _one(
            conn,
            "SELECT COUNT(*), COALESCE(SUM(price),0) FROM customer_orders "
            "WHERE status='approved' AND COALESCE(renew_service_id,0)>0 "
            "AND created_at>=? AND created_at<?",
            (start, end),
        )
    except Exception:
        logger.exception("daily report: customer sales query failed")
    finally:
        conn.close()
    return out


def _agent_activity(start: str, end: str) -> dict:
    out = {"buy_count": 0, "buy_amount": 0, "renew_count": 0, "renew_amount": 0}
    conn = _connect(AGENCY_DB)
    if not conn:
        return out
    try:
        if not _has_table(conn, "agent_transactions"):
            return out
        out["renew_count"], out["renew_amount"] = _one(
            conn,
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM agent_transactions "
            "WHERE tx_type='purchase' AND description LIKE 'تمدید سرویس:%' "
            "AND created_at>=? AND created_at<?",
            (start, end),
        )
        out["buy_count"], out["buy_amount"] = _one(
            conn,
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM agent_transactions "
            "WHERE tx_type='purchase' AND description NOT LIKE 'تمدید سرویس:%' "
            "AND created_at>=? AND created_at<?",
            (start, end),
        )
    except Exception:
        logger.exception("daily report: agent activity query failed")
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

    cash = _userbot_cash(start, end)
    user_sales = _userbot_sales(start, end)
    customer = _customer_sales(start, end)
    agent = _agent_activity(start, end)

    service_count = user_sales["count"] + customer["buy_count"] + agent["buy_count"]
    renew_count = customer["renew_count"] + agent["renew_count"]
    activity_amount = (
        user_sales["amount"] + customer["buy_amount"] + customer["renew_amount"]
        + agent["buy_amount"] + agent["renew_amount"]
    )

    lines = [
        "📊 <b>گزارش کامل روزانه فروش</b>",
        f"📅 تاریخ: <b>{report_day.isoformat()}</b>",
        "",
        "💰 <b>دریافتی تأییدشده</b>",
        f"• مبلغ: <b>{_fmt_money(cash['amount'])}</b>",
        f"• تعداد پرداخت: {cash['approved']}",
        f"• تأیید خودکار SMS: {cash['auto']}",
        f"• سایر تأییدها: {cash['manual']}",
        f"• رد/ناموفق: {cash['failed']}",
        "",
        "🛒 <b>فروش و تمدید سرویس</b>",
        f"• ساخت سرویس: <b>{service_count}</b> مورد",
        f"• تمدید سرویس: <b>{renew_count}</b> مورد",
        "",
        "👤 <b>فروش مستقیم UserBot</b>",
        f"• سفارش ثبت‌شده: {user_sales['count']} مورد — {_fmt_money(user_sales['amount'])}",
        "",
        "🤖 <b>ربات مشتری نمایندگان</b>",
        f"• خرید: {customer['buy_count']} مورد — {_fmt_money(customer['buy_amount'])}",
        f"• تمدید: {customer['renew_count']} مورد — {_fmt_money(customer['renew_amount'])}",
        "",
        "🤝 <b>عملیات مستقیم نمایندگان</b>",
        f"• خرید: {agent['buy_count']} مورد — {_fmt_money(agent['buy_amount'])}",
        f"• تمدید: {agent['renew_count']} مورد — {_fmt_money(agent['renew_amount'])}",
        f"• جمع گردش ثبت‌شده سرویس‌ها: <b>{_fmt_money(activity_amount)}</b>",
        "",
        "ℹ️ دریافتی بانکی و گردش سرویس‌ها جدا نمایش داده شده‌اند تا شارژ کیف پول و مصرف آن دوبار به‌عنوان درآمد جمع نشوند.",
        f"🕛 گزارش خودکار پایان روز ({tz_name})",
    ]
    return "\n".join(lines), report_day.isoformat()
