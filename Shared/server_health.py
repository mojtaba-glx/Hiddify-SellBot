"""
Shared/server_health.py
========================
مانیتورینگ سلامت همهٔ سرورها (سرور اصلی + نودها، شامل نودهای زیرساخت Hetzner)
و ارسال هشدار به ادمین هنگام قطع/برگشت هر سرور.

تفاوت با node_ops.monitor_and_recover_nodes:
- اینجا فقط چک سلامت + هشدار انجام می‌شود (بدون ری‌بوت خودکار).
- تمام سرورهای ثبت‌شده در servers.json را پوشش می‌دهد (نه فقط نودهای
  provisioning‌شده).

وضعیت با hysteresis (آستانهٔ خطای پیاپی) نگه داشته می‌شود تا در صورت
فلپ شبکهٔ لحظیفه‌ای هشدار تکراری ارسال نشود.
"""

import asyncio
import logging
from html import escape as html_escape
from typing import Any, Dict

from Shared import database, hiddify_api
from Shared.admin_notify import notify_admin
from Shared.env_utils import env_int, env_float
from Shared.secure_io import redact_sensitive_text

logger = logging.getLogger(__name__)

SERVER_HEALTH_DOWN_THRESHOLD = env_int("SERVER_HEALTH_DOWN_THRESHOLD", 2, minimum=1)
SERVER_HEALTH_TIMEOUT = env_float("SERVER_HEALTH_TIMEOUT", 10.0, minimum=0)
SERVER_HEALTH_CONCURRENCY = env_int("SERVER_HEALTH_CONCURRENCY", 4, minimum=1)

# وضعیت درون‌ریز: server_id -> {"status": "up"|"down", "fails": int}
_state: Dict[int, Dict[str, Any]] = {}


def _panel_label(server: Dict[str, Any]) -> str:
    if hiddify_api._is_xui_server(server):
        return "X-UI"
    return "Hiddify"


async def _probe_server(server: Dict[str, Any]) -> None:
    """Perform an uncached panel probe suitable for outage detection."""
    if hiddify_api._is_xui_server(server):
        # X-UI list_users may be served from its short-lived inbounds/clients
        # cache.  test_connect forces a live API request in both adapters.
        from Shared import xui_api

        await xui_api.test_connect(server)
        return
    await hiddify_api.list_users(server)


async def run_server_health_check() -> Dict[str, int]:
    summary = {
        "servers_scanned": 0,
        "servers_up": 0,
        "servers_down": 0,
        "alerts": 0,
        "errors": 0,
    }
    try:
        servers = database.get_servers()
    except Exception as e:
        logger.warning("server health: cannot read servers: %s", e)
        return summary

    # اجرای موازی با timeout تا یک سرور کند کل سیکل را بلوکه نکند
    sem = asyncio.Semaphore(max(1, SERVER_HEALTH_CONCURRENCY))

    async def _check_one(srv: Dict[str, Any]) -> tuple[int, str, str, bool, str]:
        sid = int(srv.get("id") or 0)
        title = str(srv.get("title") or f"سرور #{sid}")
        panel = _panel_label(srv)
        if sid <= 0:
            return sid, title, panel, False, "invalid id"
        async with sem:
            try:
                await asyncio.wait_for(
                    _probe_server(srv), timeout=max(3.0, SERVER_HEALTH_TIMEOUT)
                )
                return sid, title, panel, True, ""
            except Exception as e:
                safe_error = redact_sensitive_text(str(e)).strip() or e.__class__.__name__
                return sid, title, panel, False, safe_error[:200]

    tasks = [_check_one(s) for s in (servers or []) if int((s or {}).get("id") or 0) > 0]
    results = await asyncio.gather(*tasks) if tasks else []

    for sid, title, panel, ok, err in results:
        if sid <= 0:
            continue
        summary["servers_scanned"] += 1
        st = _state.get(sid) or {"status": "up", "fails": 0}
        if ok:
            if st.get("status") == "down":
                st["fails"] = 0
                notified = await notify_admin(
                    "✅ سرور دوباره آنلاین شد:\n"
                    f"<b>{html_escape(title)}</b> (#{sid})\n"
                    f"پنل: <b>{panel}</b>"
                )
                if notified:
                    st["status"] = "up"
                    summary["alerts"] += 1
                else:
                    # Keep the previous state so recovery notification is
                    # retried on the next healthy cycle.
                    summary["errors"] += 1
            else:
                st["status"] = "up"
                st["fails"] = 0
            summary["servers_up"] += 1
        else:
            st["fails"] = int(st.get("fails") or 0) + 1
            if st["fails"] >= SERVER_HEALTH_DOWN_THRESHOLD:
                if st.get("status") != "down":
                    notified = await notify_admin(
                        f"⚠️ سرور از دسترس خارج شد:\n<b>{html_escape(title)}</b> (#{sid})\n"
                        f"پنل: <b>{panel}</b>\n"
                        f"<i>{html_escape(err)}</i>"
                    )
                    if notified:
                        st["status"] = "down"
                        summary["alerts"] += 1
                    else:
                        # Do not mark the alert as delivered.  A later cycle
                        # will retry instead of losing the notification.
                        summary["errors"] += 1
                summary["servers_down"] += 1
            else:
                summary["servers_up"] += 1
        _state[sid] = st

    return summary
