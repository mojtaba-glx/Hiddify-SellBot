import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from Shared import database, hiddify_api, marzban_api, userbot_db

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

# همزمانی واکشی مصرف هر نود — بدون محدودیت، asyncio.gather صدها درخواست
# همزمان به پنل‌ها می‌فرستاد و CPU/شبکه را میخکوب می‌کرد.
ENFORCER_FETCH_CONCURRENCY = _parse_int_env(
    "ENFORCER_FETCH_CONCURRENCY",
    8,
    min_value=1,
    max_value=32,
)

# تعداد خطاهای پیاپی شبکه روی یک نود قبل از اینکه حجمش «یخ‌زده» (فریز) شود.
# پس از این آستانه، حجم آخرین مقدار خوانده‌شده نگه داشته می‌شود و تا زمان
# برگشتن نود یا تمدید اشتراک تغییر نمی‌کند (ماشین‌حساب دقیق).
ENFORCER_NODE_FROZEN_THRESHOLD = _parse_int_env(
    "ENFORCER_NODE_FROZEN_THRESHOLD",
    3,
    min_value=1,
    max_value=20,
)

_ENFORCER_CURSOR = 0
_ENFORCER_FETCH_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_fetch_semaphore() -> asyncio.Semaphore:
    global _ENFORCER_FETCH_SEMAPHORE
    if _ENFORCER_FETCH_SEMAPHORE is None:
        _ENFORCER_FETCH_SEMAPHORE = asyncio.Semaphore(ENFORCER_FETCH_CONCURRENCY)
    return _ENFORCER_FETCH_SEMAPHORE


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
    servers_map: Optional[Dict[int, Dict[str, Any]]] = None,
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

    server: Optional[Dict[str, Any]] = None
    if servers_map is not None and server_id in servers_map:
        server = servers_map[server_id]
    else:
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

    # محدود کردن همزمانی درخواست‌ها به پنل‌ها
    sem = _get_fetch_semaphore()
    async with sem:
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
        marzban_un = str(node.get("marzban_username") or "").strip()
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
        # Also disable on Marzban if applicable
        if marzban_un:
            try:
                await marzban_api.disable_user(server, marzban_un)
            except Exception as e:
                logger.warning(
                    "Failed disabling Marzban user on server_id=%s username=%s: %s",
                    server_id,
                    marzban_un,
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



# گارد ضد اجرای همزمان (job زمان‌بندی‌شده + اجرای دستی نباید روی هم بیفتند)
_enforcer_running = False


async def run_global_usage_enforcer(*, scan_all: bool = False) -> Dict[str, int]:
    global _enforcer_running
    if _enforcer_running:
        logger.warning("usage enforcer: اجرای همزمان شناسایی شد — این دور برای جلوگیری از تداخل رد شد")
        return {"skipped": 1, "reason": "already_running"}
    _enforcer_running = True
    try:
        return await _run_global_usage_enforcer_impl(scan_all=scan_all)
    finally:
        _enforcer_running = False


async def _run_global_usage_enforcer_impl(*, scan_all: False) -> Dict[str, int]:
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
    # کش سرورها برای این چرخه — به‌جای خواندن فایل برای هر نود
    try:
        _all_servers = database.get_servers()
        servers_map: Dict[int, Dict[str, Any]] = {
            int(s.get("id") or 0): s for s in _all_servers if isinstance(s, dict) and int(s.get("id") or 0) > 0
        }
    except Exception:
        servers_map = {}
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
            had_local_active = any(
                int(m.get("is_active") or 0) == 1 and int(m.get("deleted") or 0) == 0
                for m in mappings
            )
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
            not_found_keys: set[tuple[int, str]] = set()
            try:
                primary_server_id = int(service.get("server_id") or 0)
            except (TypeError, ValueError):
                primary_server_id = 0

            node_down_threshold = ENFORCER_NODE_FROZEN_THRESHOLD
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            # نودهای حذف‌شده (سرور پاک شده): حجم یخ‌زده را نگه می‌داریم و در جمع کل حساب می‌کنیم.
            skipped_held: List[Dict[str, Any]] = []
            fetch_tasks = []
            for node in mappings:
                if int(node.get("deleted") or 0) == 1:
                    skipped_held.append(node)
                    continue
                fetch_tasks.append(
                    _fetch_service_node_usage(service_id=service_id, node=node, servers_map=servers_map)
                )

            fetch_results = await asyncio.gather(*fetch_tasks) if fetch_tasks else []

            # ۱) نودهای حذف‌شده (held): مصرف آخرین مقدار خوانده‌شده را نگه می‌داریم.
            for node in skipped_held:
                total_usage += _to_float(node.get("usage_current"), 0.0)
                dl = node.get("days_left")
                if dl is not None:
                    try:
                        d = int(dl)
                        min_days_left = d if min_days_left is None else min(min_days_left, d)
                    except Exception:
                        pass
                got_any_panel_data = True

            # ۲) نودهای فچ‌شده از پنل.
            for result in fetch_results:
                server_id = int(result.get("server_id") or 0)
                user_uuid = str(result.get("user_uuid") or "").strip()
                if not bool(result.get("valid")):
                    continue
                total_nodes += 1

                node_rec = next(
                    (
                        m for m in mappings
                        if int(m.get("server_id") or 0) == server_id
                        and str(m.get("panel_user_uuid") or "").strip() == user_uuid
                    ),
                    None,
                )

                if bool(result.get("ok")):
                    got_any_panel_data = True
                    panel_user = result.get("panel_user") or {}
                    usage = _to_float(panel_user.get("current_usage_GB"), 0.0)
                    total_usage += usage

                    panel_limit = _usage_limit_from_panel_user(panel_user)
                    if panel_limit is not None:
                        if primary_server_id > 0 and server_id == primary_server_id:
                            synced_usage_limit = panel_limit
                        elif fallback_usage_limit is None:
                            fallback_usage_limit = panel_limit

                    days_left = _days_left_from_panel_user(panel_user)
                    if days_left is not None:
                        min_days_left = days_left if min_days_left is None else min(min_days_left, days_left)

                    dt = _parse_dt(panel_user.get("last_online"))
                    if dt and (latest_last_online is None or dt > latest_last_online):
                        latest_last_online = dt

                    # بروزرسانی رکورد نود: مقدار زنده، یخ‌زدایی، ریست شمارنده خطا.
                    userbot_db.update_service_node_runtime(
                        service_id, server_id, user_uuid,
                        usage_current=usage,
                        days_left=days_left,
                        frozen=0,
                        fail_count=0,
                        last_ok_at=now_str,
                    )
                    continue

                # نود در دسترس نیست → حجم قبلی (یخ‌زده) را در جمع نگه می‌داریم.
                prev_usage = _to_float(node_rec.get("usage_current") if node_rec else 0.0, 0.0)
                total_usage += prev_usage
                prev_days = None
                if node_rec and node_rec.get("days_left") is not None:
                    try:
                        prev_days = int(node_rec.get("days_left"))
                    except Exception:
                        prev_days = None
                if prev_days is not None:
                    min_days_left = prev_days if min_days_left is None else min(min_days_left, prev_days)

                if bool(result.get("not_found")) and server_id > 0 and user_uuid:
                    # کاربر از روی پنل پاک شده؛ مصرف قبلی نگه داشته می‌شود و نود غیرفعال می‌گردد.
                    not_found_nodes += 1
                    not_found_keys.add((server_id, user_uuid))
                    try:
                        userbot_db.set_service_node_active(service_id, server_id, user_uuid, 0)
                    except Exception:
                        pass
                    continue

                # خطای شبکه → افزایش شمارنده؛ پس از آستانه حجم یخ‌زده (فریز) می‌شود.
                prev_fail = int(node_rec.get("fail_count") or 0) if node_rec else 0
                new_fail = prev_fail + 1
                frozen = 1 if new_fail >= node_down_threshold else (int(node_rec.get("frozen") or 0) if node_rec else 0)
                if node_rec is not None:
                    try:
                        userbot_db.update_service_node_runtime(
                            service_id, server_id, user_uuid,
                            frozen=frozen,
                            fail_count=new_fail,
                        )
                    except Exception:
                        pass
                logger.warning(
                    "Node unreachable (frozen=%s) service_id=%s server_id=%s uuid=%s fail=%s: %s",
                    frozen, service_id, server_id, user_uuid, new_fail, result.get("error"),
                )

            # همه نودها صراحتاً not_found بوده‌اند و نود حذف‌شده‌ای نداریم → غیرفعال‌سازی محلی.
            if not got_any_panel_data and not_found_nodes > 0 and not skipped_held:
                userbot_db.set_service_nodes_active(service_id, 0)
                continue

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

            had_local_active = any(
                int(m.get("is_active") or 0) == 1 and int(m.get("deleted") or 0) == 0
                for m in mappings
            )
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
            elif got_any_panel_data:
                # فقط نودهای غیرِحذف‌شده و غیرِ not_found را فعال کن تا فلیپ رخ ندهد.
                needs_reenable = any(
                    int(m.get("is_active") or 0) == 0
                    and int(m.get("deleted") or 0) == 0
                    and (int(m.get("server_id") or 0), str(m.get("panel_user_uuid") or "").strip())
                    not in not_found_keys
                    for m in mappings
                )
                if needs_reenable:
                    # فعال‌سازی انتخابی (نه bulk) تا نودهای not_found دوباره فعال نشوند
                    reenabled = 0
                    for m in mappings:
                        if int(m.get("deleted") or 0) == 1:
                            continue
                        sid = int(m.get("server_id") or 0)
                        uuid = str(m.get("panel_user_uuid") or "").strip()
                        if (sid, uuid) in not_found_keys:
                            continue
                        if int(m.get("is_active") or 0) == 0:
                            try:
                                userbot_db.set_service_node_active(service_id, sid, uuid, 1)
                                reenabled += 1
                            except Exception:
                                pass
                    # اگر هیچ نودی نیاز به فعال‌سازی نداشت (همه not_found بودند) چیزی نشمار
                    if reenabled > 0:
                        summary["services_reenabled"] += 1
                    else:
                        # fallback: اگر bulk قدیمی همه را فعال می‌کرد ولی الان چیزی نماند، لاگ نکن
                        pass

        except Exception as e:
            summary["errors"] += 1
            logger.exception("Global enforcer error on service_id=%s: %s", service_id, e)

    return summary
