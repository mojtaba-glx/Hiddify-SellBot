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
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from Shared import hiddify_api, agent_db, database
from Shared.sub_links import get_service_panel_targets

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 20) -> int:
    try:
        value = int(str(os.getenv(name, default) or default).strip())
    except Exception:
        value = int(default)
    return max(minimum, min(maximum, value))


# مثل UserBot: پس از ۳ خطای پیاپی شبکه، snapshot مصرف نود frozen می‌شود.
AGENT_ENFORCER_NODE_FROZEN_THRESHOLD = _env_int(
    "AGENT_ENFORCER_NODE_FROZEN_THRESHOLD", 3
)


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


async def _disable_on_all_targets(svc: dict, mappings: List[dict]) -> int:
    """غیرفعال کردن سرویس روی همه نودهای موجود؛ نود حذف‌شده فقط محلی می‌ماند."""
    disabled = 0
    for node in mappings:
        if int(node.get("deleted") or 0) == 1:
            continue
        server_id = _to_int(node.get("server_id"), 0)
        uuid = str(node.get("panel_user_uuid") or "").strip()
        if server_id <= 0 or not uuid:
            continue
        srv = database.get_server_by_id(server_id)
        if not srv:
            continue
        try:
            await hiddify_api.disable_user(srv, uuid)
            disabled += 1
        except Exception as e:
            logger.warning(
                "agent enforcer disable svc=%s server=%s uuid=%s failed: %s",
                svc.get("id"), server_id, uuid[:8], e,
            )
    return disabled


def _is_user_not_found_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    return (
        "user not found" in msg
        or ("http 404" in msg and "not found" in msg and "user" in msg)
        or "empty client" in msg
    )


async def _get_user_with_list_fallback(
    server: Dict[str, Any],
    user_uuid: str,
) -> Dict[str, Any]:
    """Read one user without trusting Hiddify's direct UUID endpoint alone."""
    try:
        return await hiddify_api.get_user_by_uuid(server, user_uuid)
    except Exception as exc:
        if not _is_user_not_found_error(exc):
            raise
        try:
            users = await hiddify_api.list_users(server)
        except Exception:
            # نبودن کاربر قطعی نشده؛ خطای اصلی را به‌عنوان خطای پنل نگه دار.
            raise exc
        for candidate in users or []:
            if not isinstance(candidate, dict):
                continue
            candidate_uuid = str(
                candidate.get("uuid") or candidate.get("id") or ""
            ).strip()
            if candidate_uuid == user_uuid:
                return candidate
        raise


def _service_mappings(svc: dict) -> List[dict]:
    """Ensure old agency services also have per-node rows before accounting."""
    service_id = _to_int(svc.get("id"), 0)
    if service_id <= 0:
        return []
    mappings = agent_db.get_service_nodes(service_id) or []
    if mappings:
        return mappings

    # Legacy fallback: discover the old targets once and persist them.
    for srv, uuid, marzban_un in get_service_panel_targets(svc) or []:
        try:
            sid = int(srv.get("id") or 0)
        except Exception:
            sid = 0
        uuid = str(uuid or "").strip()
        if sid <= 0 or not uuid:
            continue
        try:
            agent_db.add_service_node(
                service_id=service_id,
                server_id=sid,
                server_title=str(srv.get("title") or f"سرور #{sid}"),
                panel_user_uuid=uuid,
                marzban_username=str(marzban_un or ""),
            )
        except Exception as e:
            logger.warning(
                "agent enforcer legacy mapping failed svc=%s server=%s: %s",
                service_id, sid, e,
            )
    return agent_db.get_service_nodes(service_id) or []


def _renew_pending_payload(svc: dict, reason: str) -> Dict[str, Any]:
    """Build the panel patch needed before a pending renewed node can thaw."""
    reason = str(reason or "")
    usage_reset = "usage_reset=1" in reason
    time_reset = "time_reset=1" in reason
    payload: Dict[str, Any] = {}

    usage_limit = _to_float(svc.get("usage_limit"), 0.0)
    if usage_limit > 0:
        payload["usage_limit_GB"] = usage_limit

    days_left = _to_int(svc.get("days_left"), 0)
    end_raw = str(svc.get("end_date") or "").strip()
    if end_raw:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                end_dt = datetime.strptime(end_raw[:19] if "%S" in fmt else end_raw[:10], fmt)
                seconds_left = (end_dt - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
                days_left = max(0, int((seconds_left + 86399) // 86400)) if seconds_left >= 0 else 0
                break
            except ValueError:
                continue
    if days_left > 0:
        payload["package_days"] = days_left

    if usage_reset:
        payload["current_usage_GB"] = 0

    if time_reset:
        start_date = str(svc.get("start_date") or "").strip()
        payload["start_date"] = (
            start_date[:10]
            if start_date
            else datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        )
    else:
        end_date = str(svc.get("end_date") or "").strip()
        if end_date:
            payload["expire_date"] = end_date[:10]

    return payload


async def _process_service(svc: dict) -> Dict[str, str]:
    """جمع مصرف زنده + snapshot نودهای قطع/حذف‌شده و اعمال سقف سرویس."""
    result: Dict[str, str] = {}
    if not svc or not isinstance(svc, dict):
        logger.warning("agent enforcer skip: svc is None or not dict: %r", svc)
        return result

    service_id = _to_int(svc.get("id"), 0)
    if service_id <= 0:
        return result

    mappings = _service_mappings(svc)
    if not mappings:
        return result

    total_usage = 0.0
    live_success = 0
    frozen_count = 0
    now_str = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

    for node in mappings:
        server_id = _to_int(node.get("server_id"), 0)
        uuid = str(node.get("panel_user_uuid") or "").strip()
        if server_id <= 0 or not uuid:
            continue

        prev_usage = _to_float(node.get("usage_current"), 0.0)

        # سروری که از تنظیمات حذف شده، snapshot مصرفش تا تمدید حفظ می‌شود.
        if int(node.get("deleted") or 0) == 1:
            total_usage += prev_usage
            frozen_count += 1
            continue

        srv = database.get_server_by_id(server_id)
        if not srv:
            total_usage += prev_usage
            frozen_count += 1
            try:
                agent_db.update_service_node_runtime(
                    service_id,
                    server_id,
                    uuid,
                    frozen=1,
                    deleted=1,
                    is_active=0,
                    frozen_at=str(node.get("frozen_at") or "").strip() or now_str,
                    frozen_reason="server_deleted",
                )
            except Exception:
                pass
            continue

        try:
            user_data = await _get_user_with_list_fallback(srv, uuid)

            frozen_reason = str(node.get("frozen_reason") or "").strip()
            if frozen_reason.startswith("renew_pending:"):
                # The node came back after missing a renewal. Apply the current
                # service period before trusting its stale usage counter.
                pending_payload = _renew_pending_payload(svc, frozen_reason)
                try:
                    if pending_payload:
                        await hiddify_api.patch_user(srv, uuid, pending_payload)
                    user_data = await _get_user_with_list_fallback(srv, uuid)
                except Exception as pending_err:
                    total_usage += prev_usage
                    frozen_count += 1
                    agent_db.update_service_node_runtime(
                        service_id,
                        server_id,
                        uuid,
                        frozen=1,
                        is_active=0,
                        frozen_at=str(node.get("frozen_at") or "").strip() or now_str,
                        frozen_reason=frozen_reason,
                    )
                    logger.warning(
                        "agent renew-pending node still unsynced svc=%s server=%s uuid=%s: %s",
                        service_id, server_id, uuid[:8], pending_err,
                    )
                    continue

            usage = _to_float(user_data.get("current_usage_GB"), 0.0)
            total_usage += usage
            live_success += 1
            try:
                agent_db.update_service_node_runtime(
                    service_id,
                    server_id,
                    uuid,
                    usage_current=usage,
                    days_left=(
                        _to_int(user_data.get("remaining_days"), 0)
                        if user_data.get("remaining_days") is not None
                        else None
                    ),
                    frozen=0,
                    fail_count=0,
                    last_ok_at=now_str,
                    frozen_at="",
                    frozen_reason="",
                    deleted=0,
                    is_active=1,
                )
            except Exception as db_err:
                logger.warning(
                    "agent enforcer runtime save svc=%s server=%s failed: %s",
                    service_id, server_id, db_err,
                )
            continue
        except Exception as e:
            # حتی قبل از رسیدن به threshold، آخرین مصرف این نود از جمع حذف نمی‌شود.
            total_usage += prev_usage
            prev_fail = _to_int(node.get("fail_count"), 0)
            new_fail = prev_fail + 1
            was_frozen = int(node.get("frozen") or 0) == 1

            if _is_user_not_found_error(e):
                frozen = 1 if prev_usage > 0.0 else 0
                reason = "user_not_found" if frozen else ""
                active = 0
            else:
                frozen = (
                    1
                    if prev_usage > 0.0 and new_fail >= AGENT_ENFORCER_NODE_FROZEN_THRESHOLD
                    else (int(was_frozen) if prev_usage > 0.0 else 0)
                )
                reason = "network_error" if frozen else ""
                active = int(node.get("is_active") if node.get("is_active") is not None else 1)

            if frozen:
                frozen_count += 1
            try:
                agent_db.update_service_node_runtime(
                    service_id,
                    server_id,
                    uuid,
                    frozen=frozen,
                    fail_count=new_fail,
                    frozen_at=(
                        str(node.get("frozen_at") or "").strip() or now_str
                        if frozen
                        else str(node.get("frozen_at") or "")
                    ),
                    frozen_reason=reason,
                    is_active=active,
                )
            except Exception:
                pass
            logger.warning(
                "agent node unavailable svc=%s server=%s uuid=%s fail=%s frozen=%s kept_usage=%.3f: %s",
                service_id, server_id, uuid[:8], new_fail, frozen, prev_usage, e,
            )

    # اگر کل خوشه در یک دور از دسترس بود و snapshot قدیمی ناقص بود،
    # هرگز مصرف سرویس را کمتر از آخرین مقدار سراسری ثبت‌شده نکن.
    if live_success == 0:
        total_usage = max(total_usage, _to_float(svc.get("usage_current"), 0.0))

    usage_limit = _to_float(svc.get("usage_limit"), 0.0)
    updates: Dict[str, Any] = {"usage_current": total_usage}
    if usage_limit > 0:
        updates["usage_limit"] = usage_limit

    usage_exceeded = usage_limit > 0 and total_usage >= usage_limit
    time_expired = False
    try:
        days_left = _to_int(svc.get("days_left"), None) if svc.get("days_left") is not None else None
        if days_left is not None and days_left < 0:
            time_expired = True
    except Exception:
        days_left = None

    if not time_expired:
        end_raw = str(svc.get("end_date") or "").strip()
        if end_raw:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    end_dt = datetime.strptime(end_raw[:19], fmt)
                    if end_dt < datetime.now(timezone.utc).replace(tzinfo=None):
                        time_expired = True
                    break
                except ValueError:
                    continue

    if usage_exceeded or time_expired:
        disabled = await _disable_on_all_targets(svc, mappings)
        agent_db.set_service_active(service_id, False)
        agent_db.set_service_nodes_active(service_id, False)
        updates["is_active"] = 0
        reason = "usage_limit_reached" if usage_exceeded else "time_expired"
        result["status"] = "disabled"
        result["reason"] = reason
        result["nodes_disabled"] = str(disabled)
        logger.info(
            "agent enforcer DISABLED svc=%s reason=%s usage=%s/%s disabled_nodes=%s frozen_nodes=%s",
            service_id, reason, total_usage, usage_limit, disabled, frozen_count,
        )
    else:
        updates["is_active"] = 1
        result["status"] = "synced"
        result["reason"] = "ok"
        result["frozen_nodes"] = str(frozen_count)

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