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
import os
from typing import Any, Dict

from Shared import database, hiddify_api
from Shared.admin_notify import notify_admin

logger = logging.getLogger(__name__)

SERVER_HEALTH_DOWN_THRESHOLD = int(os.getenv("SERVER_HEALTH_DOWN_THRESHOLD", "2") or "2")
SERVER_HEALTH_TIMEOUT = float(os.getenv("SERVER_HEALTH_TIMEOUT", "10") or "10")
SERVER_HEALTH_CONCURRENCY = int(os.getenv("SERVER_HEALTH_CONCURRENCY", "4") or "4")

# وضعیت درون‌ریز: server_id -> {"status": "up"|"down", "fails": int}
_state: Dict[int, Dict[str, Any]] = {}


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

    async def _check_one(srv: Dict[str, Any]) -> tuple[int, str, bool, str]:
        sid = int(srv.get("id") or 0)
        title = str(srv.get("title") or f"سرور #{sid}")
        if sid <= 0:
            return sid, title, False, "invalid id"
        async with sem:
            try:
                # hiddify_api.test_connect وجود ندارد؛ برای هر دو نوع پنل list_users کافیست
                # (برای X-UI داخل hiddify_api به xui_api واگذار می‌شود)
                await asyncio.wait_for(
                    hiddify_api.list_users(srv), timeout=max(3.0, SERVER_HEALTH_TIMEOUT)
                )
                return sid, title, True, ""
            except Exception as e:
                return sid, title, False, str(e)[:200]

    tasks = [_check_one(s) for s in (servers or []) if int((s or {}).get("id") or 0) > 0]
    results = await asyncio.gather(*tasks) if tasks else []

    for sid, title, ok, err in results:
        if sid <= 0:
            continue
        summary["servers_scanned"] += 1
        st = _state.get(sid) or {"status": "up", "fails": 0}
        if ok:
            if st.get("status") == "down":
                st["status"] = "up"
                st["fails"] = 0
                await notify_admin(f"✅ سرور دوباره آنلاین شد:\n<b>{title}</b> (#{sid})")
                summary["alerts"] += 1
            else:
                st["status"] = "up"
                st["fails"] = 0
            summary["servers_up"] += 1
        else:
            st["fails"] = int(st.get("fails") or 0) + 1
            if st["fails"] >= SERVER_HEALTH_DOWN_THRESHOLD:
                if st.get("status") != "down":
                    st["status"] = "down"
                    await notify_admin(
                        f"⚠️ سرور از دسترس خارج شد:\n<b>{title}</b> (#{sid})\n"
                        f"<i>{err}</i>"
                    )
                    summary["alerts"] += 1
                summary["servers_down"] += 1
            else:
                summary["servers_up"] += 1
        _state[sid] = st

    return summary
