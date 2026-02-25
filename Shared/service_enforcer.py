import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from Shared import database, hiddify_api, userbot_db

logger = logging.getLogger(__name__)


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
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def _extract_uuid_from_comment(comment: Optional[str]) -> str:
    raw = str(comment or "")
    m = re.search(r"(?:^|\|)uuid:([^|]+)", raw)
    if not m:
        return ""
    return m.group(1).strip()


def _days_left_from_panel_user(user: Dict[str, Any]) -> Optional[int]:
    today_utc = datetime.now(timezone.utc).date()
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


async def _disable_service_on_all_nodes(service_id: int, mappings: list[Dict[str, Any]]) -> int:
    changed = 0
    for node in mappings:
        server_id = int(node.get("server_id") or 0)
        user_uuid = str(node.get("panel_user_uuid") or "").strip()
        if server_id <= 0 or not user_uuid:
            continue

        server = database.get_server_by_id(server_id)
        if not server:
            continue
        try:
            await hiddify_api.patch_user(server, user_uuid, {"is_active": False})
            changed += 1
        except Exception as e:
            logger.warning(
                "Failed disabling user on server_id=%s uuid=%s: %s",
                server_id,
                user_uuid,
                e,
            )
    if changed > 0:
        userbot_db.set_service_nodes_active(service_id, 0)
    return changed


async def run_global_usage_enforcer() -> Dict[str, int]:
    """
    جمع مصرف سراسری سرویس‌ها روی همه نودها و قطع خودکار روی سقف.
    """
    summary = {
        "services_scanned": 0,
        "services_synced": 0,
        "services_disabled": 0,
        "nodes_disabled": 0,
        "errors": 0,
    }

    services = userbot_db.get_services_for_enforcement()
    for service in services:
        summary["services_scanned"] += 1
        service_id = int(service.get("id") or 0)
        if service_id <= 0:
            continue

        try:
            mappings = _get_or_create_mappings_for_service(service)
            if not mappings:
                continue

            total_usage = 0.0
            min_days_left: Optional[int] = None
            latest_last_online: Optional[datetime] = None
            got_any_panel_data = False

            for node in mappings:
                server_id = int(node.get("server_id") or 0)
                user_uuid = str(node.get("panel_user_uuid") or "").strip()
                if server_id <= 0 or not user_uuid:
                    continue

                server = database.get_server_by_id(server_id)
                if not server:
                    continue

                try:
                    panel_user = await hiddify_api.get_user_by_uuid(server, user_uuid)
                except Exception as e:
                    logger.warning(
                        "Failed reading usage for service_id=%s server_id=%s uuid=%s: %s",
                        service_id,
                        server_id,
                        user_uuid,
                        e,
                    )
                    continue

                got_any_panel_data = True
                total_usage += _to_float(panel_user.get("current_usage_GB"), 0.0)

                days_left = _days_left_from_panel_user(panel_user)
                if days_left is not None:
                    if min_days_left is None:
                        min_days_left = days_left
                    else:
                        min_days_left = min(min_days_left, days_left)

                dt = _parse_dt(panel_user.get("last_online"))
                if dt and (latest_last_online is None or dt > latest_last_online):
                    latest_last_online = dt

            if not got_any_panel_data:
                continue

            userbot_db.update_service_runtime(
                service_id=service_id,
                usage_current=total_usage,
                days_left=min_days_left,
                last_online=(
                    latest_last_online.strftime("%Y-%m-%d %H:%M:%S")
                    if latest_last_online
                    else None
                ),
            )
            summary["services_synced"] += 1

            limit_gb = _to_float(service.get("usage_limit"), 0.0)
            expired_by_usage = limit_gb > 0 and total_usage >= limit_gb
            expired_by_time = min_days_left is not None and min_days_left < 0

            if expired_by_usage or expired_by_time:
                changed = await _disable_service_on_all_nodes(service_id, mappings)
                if changed > 0:
                    summary["services_disabled"] += 1
                    summary["nodes_disabled"] += changed

        except Exception as e:
            summary["errors"] += 1
            logger.exception("Global enforcer error on service_id=%s: %s", service_id, e)

    return summary
