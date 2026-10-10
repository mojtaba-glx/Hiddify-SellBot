"""Formatting helpers for the AdminBot top-buying-customers report."""
from __future__ import annotations

from html import escape
from typing import Any, Iterable


def format_top_buying_customers_report(
    customers: Iterable[dict[str, Any]], days: int = 30
) -> str:
    """Render a compact, Telegram-safe ranked report for approved orders."""
    rows = list(customers or [])
    period = f"{int(days)} روز اخیر" if int(days or 0) > 0 else "کل سابقه"
    lines = [
        "🏆 <b>۱۰ مشتری برتر خرید</b>",
        f"📅 <b>بازه:</b> {escape(period)}",
        "فقط سفارش‌های تأییدشده با وضعیت <code>approved</code> محاسبه شده‌اند.",
        "",
    ]
    if not rows:
        lines.extend(["در این بازه خرید تأییدشده‌ای پیدا نشد.", "", "🎁 یادآوری: پاداش مشتریان به‌صورت دستی ثبت می‌شود."])
        return "\n".join(lines)

    lines.append("<pre>")
    lines.append("رتبه | مشتری / آیدی تلگرام")
    lines.append("     | مجموع خرید (تومان) | سفارش")
    lines.append("─" * 48)
    total_spent = 0
    for rank, row in enumerate(rows[:10], start=1):
        username = str(row.get("username") or "").strip()
        full_name = str(row.get("full_name") or "").strip()
        customer_name = ("@" + username.lstrip("@")) if username else (full_name or "کاربر بدون نام")
        telegram_id = str(row.get("telegram_id") or "نامشخص")
        amount = max(0, int(row.get("total_spent") or 0))
        order_count = max(0, int(row.get("orders_count") or 0))
        total_spent += amount
        # Limit names so one unusual profile cannot overflow Telegram's message limit.
        customer_name = escape(customer_name[:28])
        telegram_id = escape(telegram_id)
        lines.append(f"{rank:>2}   | {customer_name}")
        lines.append(f"     | ID: {telegram_id} | {amount:,} | {order_count}")
    lines.append("</pre>")
    lines.extend([
        f"💰 <b>مجموع خرید این {len(rows[:10])} نفر:</b> {total_spent:,} تومان",
        "",
        "🎁 <b>یادآوری:</b> پاداش مشتریان به‌صورت دستی ثبت می‌شود.",
    ])
    return "\n".join(lines)
