#!/usr/bin/env bash
# run_tests.sh - اجرای همه تست‌های hiddify_sellbot_tests با PYTHONPATH صحیح
#
# چرا PYTHONPATH لازم است؟
#   - پکیج python-telegram-bot در venv پروژه نصب است
#   - pytest ممکن است فقط در python سیستم باشد (به‌خاطر مالکیت root روی venv)
#   بنابراین site-packages های venv را به PYTHONPATH اضافه می‌کنیم تا
#   `telegram` از نسخه درست (python-telegram-bot) بارگذاری شود.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

TEST_DIR="$ROOT_DIR/hiddify_sellbot_tests"
if [ ! -d "$TEST_DIR" ]; then
    echo "❌ پوشه تست‌ها پیدا نشد: $TEST_DIR" >&2
    exit 1
fi

# ---------------------------------------------------------------
# 1) پیدا کردن interpreter که pytest دارد
#    اولویت با venv است؛ اگر نداشت، از python سیستم استفاده می‌کنیم.
# ---------------------------------------------------------------
PYTHON_BIN=""
for cand in "$ROOT_DIR/venv/bin/python" python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import pytest' >/dev/null 2>&1; then
        PYTHON_BIN="$cand"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "❌ pytest در هیچ interpreter پیدا نشد." >&2
    echo "   نصب کنید:  pip install pytest" >&2
    exit 1
fi

# ---------------------------------------------------------------
# 2) افزودن site-packages های venv به PYTHONPATH
#    (برای اینکه telegram از python-telegram-bot نسخه درست بارگذاری شود)
# ---------------------------------------------------------------
EXTRA_SITE=""
if [ -d "$ROOT_DIR/venv/lib" ]; then
    EXTRA_SITE="$(find "$ROOT_DIR/venv/lib" -maxdepth 4 -type d -name site-packages 2>/dev/null | head -n 1)"
fi

if [ -n "$EXTRA_SITE" ]; then
    if [ -n "${PYTHONPATH:-}" ]; then
        export PYTHONPATH="$EXTRA_SITE:$PYTHONPATH"
    else
        export PYTHONPATH="$EXTRA_SITE"
    fi
    echo "🔧 PYTHONPATH += $EXTRA_SITE"
fi

echo "🐍 interpreter: $PYTHON_BIN"
echo "🚀 در حال اجرای تست‌ها..."

# اگر کاربر فایل/مسیر تست خاصی داده (غیر از فلگ)، همان را اجرا کن
if [ $# -gt 0 ] && [[ "$1" != -* ]]; then
    exec "$PYTHON_BIN" -m pytest -v "$@"
fi

# در غیر این صورت کل پوشه تست‌ها به همراه فلگ‌های داده‌شده اجرا می‌شود
exec "$PYTHON_BIN" -m pytest -v "$TEST_DIR" "$@"
