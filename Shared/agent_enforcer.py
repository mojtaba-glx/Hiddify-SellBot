"""
محرک مصرف سرویس‌های نمایندگی (agent_services).

- لیست همه سرویس‌های فعال نمایندگی را برمی‌دارد.
- برای هر سرویس، حجم مصرف را از سرور اصلی + همه نودها جمع می‌کند
  (get_service_panel_targets) و مجموع را در usage_current می‌نویسد.
- اگر مجموع مصرف از سقف (usage_limit) بگذرد یا مدت گذشته باشد،
  کاربر را روی سرور اصلی + همه نودها غیرفعال می‌کند و is_active را صفر
  می‌کند تا هم در ربات نمایندگی هم در ربات مشتری "غیر فعال" نشان داده شود.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from Shared import hiddify_api, agent_db
from Shared.sub_links import get_service_panel_targets

logger = logging.getLogger(__name__)


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


async def _disable_on_all_targets(svc: dict, targets: list) -> int:
    """غیرفعال کردن کاربر روی سرور اصلی + همه نودها. تعداد موفق را برمی‌گرداند."""
    disabled = 0
    for srv, uuid, marzban_un in targets:
        if not srv or not uuid:
            continue
        try:
            await hiddify_api.disable_user(srv, uuid)
            disabled += 1
        except Exception as e:
            logger.warning("agent enforcer disable svc=%s node=%s failed: %s", svc.get("id"), uuid[:8], e)
    return disabled


async def _process_service(svc: dict) -> Dict[str, str]:
    """بررسی و در صورت نیاز قطع یک سرویس. خلاصه نتیجه برمی‌گرداند."""
    result: Dict[str, str] = {}
    # Guard: DB may return None or corrupted rows (seen in logs svc=2..30)
    if not svc or not isinstance(svc, dict):
        logger.warning("agent enforcer skip: svc is None or not dict: %r", svc)
        return result
    service_id = _to_int(svc.get("id"), 0)
    if service_id <= 0:
        return result

    targets = get_service_panel_targets(svc)
    if not targets:
        return result

    total_usage = 0.0
    found_any = False
    for srv, uuid, marzban_un in targets:
        try:
            user_data = await hiddify_api.get_user_by_uuid(srv, uuid)
            total_usage += _to_float(user_data.get("current_usage_GB"), 0.0)
            found_any = True
        except Exception as e:
            logger.warning("agent enforcer sync svc=%s node=%s failed: %s", service_id, uuid[:8], e)

    if not found_any:
        return result

    usage_limit = _to_float(svc.get("usage_limit"), 0.0)
    updates: Dict[str, Any] = {"usage_current": total_usage}
    if usage_limit > 0:
        updates["usage_limit"] = usage_limit

    # تشخیص قطع: مصرف از سقف رد شده یا زمان گذشته
    usage_exceeded = usage_limit > 0 and total_usage >= usage_limit

    time_expired = False
    try:
        days_left = _to_int(svc.get("days_left"), None) if svc.get("days_left") is not None else None
        if days_left is not None and days_left < 0:
            time_expired = True
    except Exception:
        days_left = None

    if not time_expired:
        try:
            end_raw = str(svc.get("end_date") or "").strip()
            if end_raw:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                    try:
                        end_dt = datetime.strptime(end_raw[:19], fmt)
                        now = datetime.now(timezone.utc).replace(tzinfo=None)
                        if end_dt < now:
                            time_expired = True
                        break
                    except ValueError:
                        continue
        except Exception:
            pass

    if usage_exceeded or time_expired:
        disabled = await _disable_on_all_targets(svc, targets)
        agent_db.set_service_active(service_id, False)
        agent_db.set_service_nodes_active(service_id, False)
        updates["is_active"] = 0
        reason = "usage_limit_reached" if usage_exceeded else "time_expired"
        logger.info("agent enforcer DISABLED svc=%s reason=%s usage=%s/%s disabled_nodes=%s",
                    service_id, reason, total_usage, usage_limit, disabled)
        result["status"] = "disabled"
        result["reason"] = reason
        result["nodes_disabled"] = str(disabled)
    else:
        updates["is_active"] = 1
        result["status"] = "synced"
        result["reason"] = "ok"

    agent_db.update_service(service_id, updates)
    return result



# گارد ضد اجرای همزمان (job زمان‌بندی‌شده + اجرای دستی نباید روی هم بیفتند)
_enforcer_running = False


async def run_agent_usage_enforcer(*, scan_all: bool = True) -> Dict[str, int]:
    global _enforcer_running
    if _enforcer_running:
        logger.warning("agent enforcer: اجرای همزمان شناسایی شد — این دور برای جلوگیری از تداخل رد شد")
        return {"skipped": 1, "reason": "already_running"}
    _enforcer_running = True
    try:
        return await _run_agent_usage_enforcer_impl(scan_all=scan_all)
    finally:
        _enforcer_running = False


async def _run_agent_usage_enforcer_impl(*, scan_all: bool = True) -> Dict[str, int]:
    """اجرای دورهای/دستی چک مصرف سرویس‌های فعال نمایندگی."""
    summary = {
        "services_total": 0,
        "services_scanned": 0,
        "services_synced": 0,
        "services_disabled": 0,
        "nodes_disabled": 0,
        "errors": 0,
    }

    try:
        services = agent_db.get_all_active_services()
    except Exception as e:
        logger.error("agent enforcer list services failed: %s", e)
        summary["errors"] += 1
        return summary

    summary["services_total"] = len([s for s in services if s and isinstance(s, dict)])
    candidates = [s for s in services if s and isinstance(s, dict)] if scan_all else [s for s in services if s and isinstance(s, dict)]
    # Log and skip any None/corrupted rows that previously caused 'NoneType' errors
    none_count = len(services) - len(candidates)
    if none_count > 0:
        logger.warning("agent enforcer: skipping %s None/corrupted service rows out of %s", none_count, len(services))
    for svc in candidates:
        summary["services_scanned"] += 1
        try:
            res = await _process_service(svc)
            if res.get("status") == "disabled":
                summary["services_disabled"] += 1
                summary["nodes_disabled"] += _to_int(res.get("nodes_disabled"), 0)
            elif res.get("status") == "synced":
                summary["services_synced"] += 1
        except Exception as e:
            svc_id = None
            try:
                svc_id = (svc or {}).get("id") if isinstance(svc, dict) else None
            except Exception:
                svc_id = None
            logger.exception("agent enforcer svc=%s failed: %s", svc_id, e)
            summary["errors"] += 1

    return summary