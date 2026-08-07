from typing import Any
from html import escape


def _escape(text: Any) -> str:
    return escape(str(text or ""))


def _fmt_toman(amount: int) -> str:
    try:
        return f"{int(amount or 0):,}"
    except Exception:
        return str(amount or 0)


def _fmt_gb(value: float) -> str:
    try:
        v = float(value)
    except Exception:
        v = 0.0
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.1f}"


def _normalize_digits(text: str) -> str:
    fa_digits = str.maketrans(
        "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"
        "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9",
        "0123456789" * 2,
    )
    return str(text).translate(fa_digits).replace(",", "").replace("\u060c", "").replace(" ", "")


def _status_icon(status: str) -> str:
    icons = {"active": "\u2705", "open": "\U0001f4ec", "pending": "\u23f3", "closed": "\u2705", "approved": "\u2705", "rejected": "\u274c", "confirmed": "\u2705", "cancelled": "\u274c"}
    return icons.get(status, "\u2753")


def _toggle_label(key: str, enabled: bool) -> str:
    icon = "\u2705" if enabled else "\u274c"
    return f"{key} {icon}"
