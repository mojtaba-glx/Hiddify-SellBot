import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from Shared import database, hiddify_api, userbot_db

logger = logging.getLogger(__name__)


def _parse_int_env(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        value = int(raw)
    except Exception:
        value = int(default)
    return max(min_value, min(max_value, value))


def _parse_float_env(name: str, default: float, *, min_value: float, max_value: float) -> float:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        value = float(raw)
    except Exception:
        value = float(default)
    return max(min_value, min(max_value, value))


# تعداد سرویس‌هایی که در هر چرخه بررسی می‌شوند.
# اگر تعداد کل سرویس‌ها زیاد باشد، اسکن دسته‌ای باعث می‌شود job دیر نکند و skip نشود.
GLOBAL_ENFORCER_BATCH_SIZE = _parse_int_env(
    "GLOBAL_ENFORCER_BATCH_SIZE",
    30,
    min_value=5,
    max_value=500,
)

# سرویس‌هایی که به این نسبت از سقف نزدیک شده‌اند، هر چرخه در اولویت بررسی قرار می‌گیرند.
GLOBAL_ENFORCER_HOT_USAGE_RATIO = _parse_float_env(
    "GLOBAL_ENFORCER_HOT_USAGE_RATIO",
    0.85,
    min_value=0.5,
    max_value=1.0,
)

_ENFORCER_CURSOR = 0


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        iso_raw = str(dt_str).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f",):
        try:
            return datetime.strptime(str(dt_str), fmt)
        except ValueError:
            continue
    return None


def _extract_uuid_from_comment(comment: Optional[str]) -> str:
    raw = str(comment or "")
    m = re.search(r"(?:^|\|)uuid:([^|]+)", raw)
    if not m:
        return ""
    return m.group(1).strip()


def _optional_int_from_any(value: Any) -> Optional[int]:
    raw = str(value or "").replace(",", "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except Exception:
        return None


def _usage_limit_from_panel_user(user: Dict[str, Any]) -> Optional[float]:
    for key in ("usage_limit_GB", "usage_limit_gb", "usage_limit", "package_traffic"):
        if key not in user:
            continue
        raw = user.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        val = _to_float(raw, -1.0)
        if val >= 0:
            return float(val)
    return None


def _days_left_from_panel_user(user: Dict[str, Any]) -> Optional[int]:
    today_utc = datetime.now(timezone.utc).date()
    for key in ("remaining_days", "remaining_day", "days_left"):
        remaining = _optional_int_from_any(user.get(key))
        if remaining is not None:
            return remaining

    start_dt = _parse_dt(user.get("start_date"))
    package_days = user.get("package_days")
    if start_dt and package_days:
        try:
            end_dt = start_dt + timedelta(days=int(package_days))
            return (end_dt.date() - today_utc).days
        except Exception:
            pass

    for key in ("expire", "expire_date", "end_date", "expiration_date", "expires_at"):
        end_dt = _parse_dt(user.get(key))
        if end_dt:
            return (end_dt.date() - today_utc).days
    return None


def _is_user_not_found_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    if "user not found" in msg:
        return True
    return "http 404" in msg and "not found" in msg and "user" in msg


def _select_services_for_cycle(services: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """
    انتخاب سرویس‌ها برای چرخه فعلی:
    1) اولویت: سرویس‌های منقضی یا نزدیک سقف مصرف
    2) بقیه به‌صورت گردشی (round-robin)
    """
    global _ENFORCER_CURSOR

    total = len(services)
    if total <= 0:
        return []

    batch = max(1, min(GLOBAL_ENFORCER_BATCH_SIZE, total))
    if total <= batch:
        return services

    hot: list[Dict[str, Any]] = []
    normal: list[Dict[str, Any]] = []
    ratio = GLOBAL_ENFORCER_HOT_USAGE_RATIO

    for svc in services:
        limit_gb = _to_float(svc.get("usage_limit"), 0.0)
        usage_gb = _to_float(svc.get("usage_current"), 0.0)
        try:
            days_left = int(svc.get("days_left"))
        except Exception:
            days_left = None

        expired_time = days_left is not None and days_left < 0
        expired_usage = limit_gb > 0 and usage_gb >= limit_gb
        near_usage = limit_gb > 0 and usage_gb >= (limit_gb * ratio)

        if expired_time or expired_usage or near_usage:
            hot.append(svc)
        else:
            normal.append(svc)

    selected: list[Dict[str, Any]] = []
    if hot:
        selected.extend(hot[:batch])
    if len(selected) >= batch:
        return selected[:batch]

    if not normal:
        return selected

    start = _ENFORCER_CURSOR % len(normal)
    ordered = normal[start:] + normal[:start]
    need = batch - len(selected)
    picked = ordered[:need]
    selected.extend(picked)
    _ENFORCER_CURSOR = (start + len(picked)) % len(normal)
    return selected


def _get_or_create_mappings_for_service(service: Dict[str, Any]) -> list[Dict[str, Any]]:
    service_id = int(service.get("id") or 0)
    if service_id <= 0:
        return []
    mappings = userbot_db.get_service_nodes(service_id)
    if mappings:
        return mappings

    # fallback برای داده‌های قدیمی: uuid در comment + server_id خود سرویس
    service_uuid = _extract_uuid_from_comment(service.get("comment"))
    service_server_id = int(service.get("server_id") or 0)
    if service_uuid and service_server_id > 0:
        userbot_db.add_service_node(
            service_id=service_id,
            server_id=service_server_id,
            panel_user_uuid=service_uuid,
            server_title=str(service.get("server_title") or ""),
            is_active=1,
        )
        return userbot_db.get_service_nodes(service_id)
    return []


async def _fetch_service_node_usage(
    *,
    service_id: int,
    node: Dict[str, Any],
) -> Dict[str, Any]:
    server_id = int(node.get("server_id") or 0)
    user_uuid = str(node.get("panel_user_uuid") or "").strip()
    if server_id <= 0 or not user_uuid:
        return {
            "server_id": server_id,
            "user_uuid": user_uuid,
            "valid": False,
            "ok": False,
            "not_found": False,
            "panel_user": None,
            "error": None,
        }

    server = database.get_server_by_id(server_id)
    if not server:
        return {
            "server_id": server_id,
            "user_uuid": user_uuid,
            "valid": True,
            "ok": False,
            "not_found": True,
            "panel_user": None,
            "error": "server not found",
        }

    try:
        panel_user = await hiddify_api.get_user_by_uuid(server, user_uuid)
        return {
            "server_id": server_id,
            "user_uuid": user_uuid,
            "valid": True,
            "ok": True,
            "not_found": False,
            "panel_user": panel_user,
            "error": None,
        }
    except Exception as e:
        return {
            "server_id": server_id,
            "user_uuid": user_uuid,
            "valid": True,
            "ok": False,
            "not_found": _is_user_not_found_error(e),
            "panel_user": None,
            "error": e,
        }


async def _disable_service_on_all_nodes(
    service_id: int,
    mappings: list[Dict[str, Any]],
    *,
    reason: str = "",
) -> tuple[int, int]:
    changed = 0
    failed = 0
    for node in mappings:
        server_id = int(node.get("server_id") or 0)
        user_uuid = str(node.get("panel_user_uuid") or "").strip()
        if server_id <= 0 or not user_uuid:
            continue

        server = database.get_server_by_id(server_id)
        if not server:
            continue
        try:
            await hiddify_api.disable_user(server, user_uuid)
            changed += 1
        except Exception as e:
            failed += 1
            logger.warning(
                "Failed disabling user on server_id=%s uuid=%s: %s",
                server_id,
                user_uuid,
                e,
            )

    # Local lock must be applied even if remote patch partially fails,
    # so user cannot bypass limits through Multi/Direct links.
    userbot_db.set_service_nodes_active(service_id, 0)
    if reason:
        logger.info(
            "Service %s locked locally. reason=%s, remote_changed=%s, remote_failed=%s",
            service_id,
            reason,
            changed,
            failed,
        )
    return changed, failed


async def run_global_usage_enforcer(*, scan_all: bool = False) -> Dict[str, int]:
    """
    جمع مصرف سراسری سرویس‌ها روی همه نودها و قطع خودکار روی سقف.
    """
    summary = {
        "services_scanned": 0,
        "services_total": 0,
        "services_synced": 0,
        "services_disabled": 0,
        "services_reenabled": 0,
        "nodes_disabled": 0,
        "nodes_disable_failed": 0,
        "errors": 0,
    }

    services = userbot_db.get_services_for_enforcement()
    summary["services_total"] = len(services)
    selected_services = services if bool(scan_all) else _select_services_for_cycle(services)
    for service in selected_services:
        summary["services_scanned"] += 1
        service_id = int(service.get("id") or 0)
        if service_id <= 0:
            continue

        try:
            mappings = _get_or_create_mappings_for_service(service)
            if not mappings:
                continue

            # Fast cutoff: اگر مصرف/زمان محلی از سقف رد شده و هنوز نود فعال داریم،
            # بدون انتظار برای اسکن کامل همین چرخه سرویس را قطع کن.
            local_limit = _to_float(service.get("usage_limit"), 0.0)
            local_usage = _to_float(service.get("usage_current"), 0.0)
            try:
                local_days_left = int(service.get("days_left"))
            except Exception:
                local_days_left = None
            had_local_active = any(int(m.get("is_active") or 0) == 1 for m in mappings)
            local_expired_by_usage = local_limit > 0 and local_usage >= local_limit
            local_expired_by_time = local_days_left is not None and local_days_left < 0
            if had_local_active and (local_expired_by_usage or local_expired_by_time):
                reason = []
                if local_expired_by_usage:
                    reason.append("usage_limit_local")
                if local_expired_by_time:
                    reason.append("time_expired_local")
                changed, failed = await _disable_service_on_all_nodes(
                    service_id,
                    mappings,
                    reason="+".join(reason),
                )
                summary["nodes_disabled"] += changed
                summary["nodes_disable_failed"] += failed
                summary["services_disabled"] += 1
                continue

            total_usage = 0.0
            min_days_left: Optional[int] = None
            latest_last_online: Optional[datetime] = None
            synced_usage_limit: Optional[float] = None
            fallback_usage_limit: Optional[float] = None
            got_any_panel_data = False
            fetched_nodes = 0
            total_nodes = 0
            not_found_nodes = 0
            try:
                primary_server_id = int(service.get("server_id") or 0)
            except (TypeError, ValueError):
                primary_server_id = 0

            fetch_tasks = [
                _fetch_service_node_usage(service_id=service_id, node=node)
                for node in mappings
            ]
            fetch_results = await asyncio.gather(*fetch_tasks) if fetch_tasks else []
            for result in fetch_results:
                server_id = int(result.get("server_id") or 0)
                user_uuid = str(result.get("user_uuid") or "").strip()
                if not bool(result.get("valid")):
                    continue
                total_nodes += 1

                if bool(result.get("ok")):
                    panel_user = result.get("panel_user") or {}
                    got_any_panel_data = True
                    fetched_nodes += 1
                    total_usage += _to_float(panel_user.get("current_usage_GB"), 0.0)

                    panel_limit = _usage_limit_from_panel_user(panel_user)
                    if panel_limit is not None:
                        if primary_server_id > 0 and server_id == primary_server_id:
                            synced_usage_limit = panel_limit
                        elif fallback_usage_limit is None:
                            fallback_usage_limit = panel_limit

                    days_left = _days_left_from_panel_user(panel_user)
                    if days_left is not None:
                        if min_days_left is None:
                            min_days_left = days_left
                        else:
                            min_days_left = min(min_days_left, days_left)

                    dt = _parse_dt(panel_user.get("last_online"))
                    if dt and (latest_last_online is None or dt > latest_last_online):
                        latest_last_online = dt
                    continue

                err = result.get("error")
                if err:
                    logger.warning(
                        "Failed reading usage for service_id=%s server_id=%s uuid=%s: %s",
                        service_id,
                        server_id,
                        user_uuid,
                        err,
                    )

                if bool(result.get("not_found")) and server_id > 0 and user_uuid:
                    try:
                        userbot_db.set_service_node_active(service_id, server_id, user_uuid, 0)
                        not_found_nodes += 1
                    except Exception:
                        pass

            if not got_any_panel_data:
                # If all mapped nodes return explicit "user not found",
                # disable local mappings to avoid re-scanning stale services forever.
                if total_nodes > 0 and not_found_nodes >= total_nodes:
                    userbot_db.set_service_nodes_active(service_id, 0)
                continue

            # If some nodes failed, keep conservative local runtime values
            # so partial reads cannot lower usage below already-known total.
            previous_usage = _to_float(service.get("usage_current"), 0.0)
            if fetched_nodes < max(total_nodes, 1):
                total_usage = max(total_usage, previous_usage)

            previous_days_left: Optional[int] = None
            try:
                previous_days_left = int(service.get("days_left"))
            except Exception:
                previous_days_left = None
            if fetched_nodes < max(total_nodes, 1) and previous_days_left is not None:
                if min_days_left is None:
                    min_days_left = previous_days_left
                else:
                    min_days_left = min(min_days_left, previous_days_left)

            if synced_usage_limit is None:
                synced_usage_limit = fallback_usage_limit

            userbot_db.update_service_runtime(
                service_id=service_id,
                usage_current=total_usage,
                usage_limit=synced_usage_limit,
                days_left=min_days_left,
                last_online=(
                    latest_last_online.strftime("%Y-%m-%d %H:%M:%S")
                    if latest_last_online
                    else None
                ),
            )
            summary["services_synced"] += 1

            limit_gb = (
                float(synced_usage_limit)
                if synced_usage_limit is not None
                else _to_float(service.get("usage_limit"), 0.0)
            )
            expired_by_usage = limit_gb > 0 and total_usage >= limit_gb
            expired_by_time = min_days_left is not None and min_days_left < 0

            had_local_active = any(int(m.get("is_active") or 0) == 1 for m in mappings)
            if expired_by_usage or expired_by_time:
                reason = []
                if expired_by_usage:
                    reason.append("usage_limit")
                if expired_by_time:
                    reason.append("time_expired")
                changed, failed = await _disable_service_on_all_nodes(
                    service_id,
                    mappings,
                    reason="+".join(reason),
                )
                summary["nodes_disabled"] += changed
                summary["nodes_disable_failed"] += failed
                if had_local_active or changed > 0:
                    summary["services_disabled"] += 1
            else:
                # Re-enable local node mappings only on successful full read cycle.
                if fetched_nodes >= max(total_nodes, 1) and any(
                    int(m.get("is_active") or 0) == 0 for m in mappings
                ):
                    userbot_db.set_service_nodes_active(service_id, 1)
                    summary["services_reenabled"] += 1

        except Exception as e:
            summary["errors"] += 1
            logger.exception("Global enforcer error on service_id=%s: %s", service_id, e)

    return summary
