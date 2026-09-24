"""X-NET panel adapter.

The adapter exposes the same user-management shape the rest of SellBot expects
from Hiddify/X-UI while using X-NET's documented management API.

Important:
- Management calls authenticate with the admin JWT returned by /api/auth/login.
- The static xnet_* API token is for node-to-panel traffic and is deliberately
  not used for admin automation.
- Client UUID may be supplied on create and may be changed with the client PUT
  endpoint. This lets SellBot keep one canonical UUID across its server cluster.
"""

from __future__ import annotations

import asyncio
import base64
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import httpx


class XnetApiError(RuntimeError):
    """Raised when an X-NET API request fails."""


_TOKEN_CACHE: Dict[Tuple[str, str], Tuple[float, str]] = {}
_TOKEN_LOCK = asyncio.Lock()
_DEFAULT_TOKEN_TTL = 15 * 60
_GB = 1024 ** 3


def is_xnet_server(server: Dict[str, Any]) -> bool:
    return str((server or {}).get("panel_type") or "").strip().lower() in {
        "xnet",
        "x-net",
    }


def _base_url(server: Dict[str, Any]) -> str:
    url = str((server or {}).get("panel_url") or "").strip().rstrip("/")
    if not url:
        raise XnetApiError("panel_url برای X-NET تنظیم نشده است.")
    return url


def _username(server: Dict[str, Any]) -> str:
    for key in ("xnet_username", "admin_username", "username"):
        value = str((server or {}).get(key) or "").strip()
        if value:
            return value
    return "admin"


def _password(server: Dict[str, Any]) -> str:
    for key in ("xnet_password", "admin_password", "password"):
        value = str((server or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def _configured_jwt(server: Dict[str, Any]) -> str:
    # Optional escape hatch for tests/manual setups. Normally JWT is obtained
    # from /api/auth/login and refreshed automatically.
    for key in ("xnet_jwt_token", "admin_jwt"):
        value = str((server or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def _cache_key(server: Dict[str, Any]) -> Tuple[str, str]:
    return (_base_url(server), _username(server))


async def _login(server: Dict[str, Any], *, force: bool = False) -> str:
    configured = _configured_jwt(server)
    if configured and not force:
        return configured

    key = _cache_key(server)
    now = time.monotonic()
    if not force:
        cached = _TOKEN_CACHE.get(key)
        if cached and cached[0] > now and cached[1]:
            return cached[1]

    password = _password(server)
    if not password:
        raise XnetApiError(
            "برای مدیریت X-NET باید نام کاربری و رمز ادمین پنل در تنظیمات سرور "
            "ثبت شود (xnet_username/xnet_password)."
        )

    async with _TOKEN_LOCK:
        if not force:
            cached = _TOKEN_CACHE.get(key)
            if cached and cached[0] > time.monotonic() and cached[1]:
                return cached[1]

        url = f"{_base_url(server)}/api/auth/login"
        payload = {"username": _username(server), "password": password}
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Accept": "application/json"},
                )
        except httpx.RequestError as exc:
            raise XnetApiError(f"خطا در اتصال به X-NET: {exc}") from exc

        if response.status_code >= 400:
            raise XnetApiError(
                f"ورود به X-NET ناموفق بود (HTTP {response.status_code})."
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise XnetApiError("پاسخ ورود X-NET JSON معتبر نیست.") from exc

        if bool(data.get("requires2fa")):
            raise XnetApiError(
                "اکانت ادمین X-NET نیاز به 2FA دارد. برای اتصال خودکار ربات "
                "یک اکانت مدیریتی بدون TOTP اختصاص دهید."
            )

        token = str(data.get("token") or "").strip()
        if not token:
            raise XnetApiError("X-NET بعد از ورود JWT برنگرداند.")

        _TOKEN_CACHE[key] = (time.monotonic() + _DEFAULT_TOKEN_TTL, token)
        return token


async def _request_json(
    method: str,
    path: str,
    server: Dict[str, Any],
    *,
    json: Optional[Any] = None,
    params: Optional[Dict[str, Any]] = None,
    auth: bool = True,
) -> Any:
    url = f"{_base_url(server)}/{path.lstrip('/')}"

    async def _send(token: str = "") -> httpx.Response:
        headers = {"Accept": "application/json"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=18.0) as client:
            return await client.request(
                method.upper(), url, headers=headers, json=json, params=params
            )

    token = await _login(server) if auth else ""
    try:
        response = await _send(token)
    except httpx.RequestError as exc:
        raise XnetApiError(f"خطا در اتصال به X-NET: {exc}") from exc

    # JWTs are session credentials and can expire. Retry exactly once after
    # a fresh login when password credentials are available.
    if auth and response.status_code in {401, 403} and _password(server):
        _TOKEN_CACHE.pop(_cache_key(server), None)
        token = await _login(server, force=True)
        try:
            response = await _send(token)
        except httpx.RequestError as exc:
            raise XnetApiError(f"خطا در اتصال به X-NET: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text.strip().replace("\n", " ")[:300]
        raise XnetApiError(
            f"X-NET API HTTP {response.status_code}: {detail or 'request failed'}"
        )

    if not response.content:
        return {}

    try:
        return response.json()
    except ValueError as exc:
        raise XnetApiError("پاسخ X-NET JSON معتبر نیست.") from exc


async def _request_text(
    path: str,
    server: Dict[str, Any],
    *,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    url = f"{_base_url(server)}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=18.0) as client:
            response = await client.get(url, params=params)
    except httpx.RequestError as exc:
        raise XnetApiError(f"خطا در دریافت اشتراک X-NET: {exc}") from exc
    if response.status_code >= 400:
        raise XnetApiError(f"X-NET subscription HTTP {response.status_code}")
    return response.text


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_dt(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _payload_gb(payload: Dict[str, Any]) -> Optional[float]:
    for key in ("usage_limit_GB", "usage_limit_gb", "quota_gb"):
        if key in payload and payload.get(key) is not None:
            return max(0.0, _to_float(payload.get(key), 0.0))
    return None


def _payload_status(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("is_active", "active", "enable", "enabled"):
        if key in payload:
            return "active" if bool(payload.get(key)) else "disabled"
    if "status" in payload:
        raw = str(payload.get("status") or "").strip().lower()
        if raw in {"active", "enabled", "enable", "on"}:
            return "active"
        if raw in {"disabled", "disable", "inactive", "off"}:
            return "disabled"
    if "mode" in payload:
        raw = str(payload.get("mode") or "").strip().lower()
        if raw in {"disable", "disabled", "inactive"}:
            return "disabled"
    return None


def _expiry_from_payload(
    payload: Dict[str, Any],
    *,
    current: Optional[datetime] = None,
) -> Optional[str]:
    for key in ("expireDate", "expire_date", "expiration_date", "expires_at"):
        if key in payload:
            raw = payload.get(key)
            if raw in (None, "", 0):
                return None
            dt = _parse_dt(raw)
            if dt is not None:
                return dt.isoformat().replace("+00:00", "Z")

    if "package_days" in payload:
        days = _to_int(payload.get("package_days"), 0)
        if days <= 0:
            return None
        # SellBot callers pass the desired remaining days, not a delta.
        base = datetime.now(timezone.utc)
        return (base + timedelta(days=days)).isoformat().replace("+00:00", "Z")

    return (
        current.isoformat().replace("+00:00", "Z")
        if current is not None
        else None
    )


def _selected_inbound_ids(
    server: Dict[str, Any],
    inbounds: List[Dict[str, Any]],
) -> List[str]:
    active = [
        str(i.get("id") or "").strip()
        for i in inbounds
        if str(i.get("id") or "").strip()
        and bool(i.get("enabled", True))
    ]
    if not active:
        active = [
            str(i.get("id") or "").strip()
            for i in inbounds
            if str(i.get("id") or "").strip()
        ]
    if not active:
        return []

    raw = (
        (server or {}).get("xnet_inbound_ids")
        if (server or {}).get("xnet_inbound_ids") not in (None, "")
        else (server or {}).get("xnet_inbound_id")
    )
    if raw in (None, "", "skip"):
        return [active[0]]

    if isinstance(raw, (list, tuple, set)):
        requested = [str(x).strip() for x in raw if str(x).strip()]
    else:
        text = str(raw).strip()
        if text == "0":
            return active
        requested = [x.strip() for x in text.split(",") if x.strip()]

    allowed = set(active)
    selected = [x for x in requested if x in allowed]
    return selected or [active[0]]


def _client_uuid(client: Dict[str, Any]) -> str:
    return str(
        client.get("uuid")
        or client.get("subscriptionUuid")
        or client.get("subscription_uuid")
        or ""
    ).strip()


def _client_status(client: Dict[str, Any]) -> str:
    status = str(client.get("status") or "").strip().lower()
    if status:
        return status
    enabled = client.get("enabled")
    if enabled is None:
        enabled = client.get("enable")
    return "active" if enabled is not False else "disabled"


def _normalize_client(
    client: Dict[str, Any],
    inbound: Dict[str, Any],
    server: Dict[str, Any],
    *,
    used_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    user_uuid = _client_uuid(client)
    traffic_used = (
        _to_int(used_bytes, 0)
        if used_bytes is not None
        else _to_int(client.get("trafficUsedBytes"), 0)
    )
    traffic_limit = _to_int(client.get("trafficLimitBytes"), 0)
    expiry = _parse_dt(client.get("expireDate"))
    now = datetime.now(timezone.utc)
    days_left = None
    if expiry is not None:
        days_left = max(0, (expiry.date() - now.date()).days)
    status = _client_status(client)
    active = status not in {"disabled", "inactive", "expired", "blocked"}

    return {
        "uuid": user_uuid,
        "id": user_uuid,
        "xnet_client_id": str(client.get("id") or "").strip(),
        "name": str(
            client.get("username")
            or client.get("email")
            or client.get("remark")
            or user_uuid
        ).strip(),
        "username": str(client.get("username") or "").strip(),
        "email": str(client.get("email") or "").strip(),
        "comment": str(client.get("remark") or "").strip(),
        "is_active": active,
        "active": active,
        "enable": active,
        "enabled": active,
        "status": "active" if active else "disabled",
        "mode": "active" if active else "disabled",
        "used_traffic": traffic_used,
        "current_usage_GB": round(traffic_used / _GB, 3),
        "usage_limit_GB": round(traffic_limit / _GB, 3),
        "usage_limit_gb": round(traffic_limit / _GB, 3),
        "trafficLimitBytes": traffic_limit,
        "trafficUsedBytes": traffic_used,
        "expireDate": (
            expiry.isoformat().replace("+00:00", "Z")
            if expiry is not None
            else None
        ),
        "expire": (
            expiry.strftime("%Y-%m-%d %H:%M:%S")
            if expiry is not None
            else None
        ),
        "expire_date": expiry.strftime("%Y-%m-%d") if expiry is not None else None,
        "days_left": days_left,
        "remaining_days": days_left,
        "package_days": days_left,
        "inbound_id": str(inbound.get("id") or "").strip(),
        "protocol": str(inbound.get("protocol") or "").strip().lower(),
        "port": _to_int(inbound.get("port"), 0),
        "server_id": (server or {}).get("id"),
        "_source": "xnet",
    }


async def ping(server: Dict[str, Any]) -> Dict[str, Any]:
    data = await _request_json("GET", "/api/v1/ping", server, auth=False)
    if not isinstance(data, dict):
        raise XnetApiError("پاسخ ping X-NET معتبر نیست.")
    return data


async def get_inbounds(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = await _request_json("GET", "/api/inbounds", server)
    if isinstance(data, list):
        return [dict(x) for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("inbounds", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [dict(x) for x in value if isinstance(x, dict)]
    raise XnetApiError("فرمت پاسخ inbounds در X-NET شناخته نشد.")


def _find_client_records(
    inbounds: List[Dict[str, Any]],
    user_uuid: str,
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    wanted = str(user_uuid or "").strip().lower()
    if not wanted:
        return []
    result: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for inbound in inbounds or []:
        clients = inbound.get("clients") or []
        if not isinstance(clients, list):
            continue
        for client in clients:
            if not isinstance(client, dict):
                continue
            identifiers = {
                _client_uuid(client).lower(),
                str(client.get("id") or "").strip().lower(),
            }
            if wanted in identifiers:
                result.append((inbound, client))
    return result


async def list_users(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    inbounds = await get_inbounds(server)
    groups: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    order: List[str] = []

    for inbound in inbounds:
        clients = inbound.get("clients") or []
        if not isinstance(clients, list):
            continue
        for client in clients:
            if not isinstance(client, dict):
                continue
            uid = _client_uuid(client)
            if not uid:
                continue
            key = uid.lower()
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append((inbound, client))

    out: List[Dict[str, Any]] = []
    for key in order:
        pairs = groups[key]
        inbound, client = pairs[0]
        used = sum(_to_int(c.get("trafficUsedBytes"), 0) for _, c in pairs)
        out.append(_normalize_client(client, inbound, server, used_bytes=used))
    return out


async def get_user_by_uuid(
    server: Dict[str, Any],
    user_uuid: str,
) -> Dict[str, Any]:
    wanted = str(user_uuid or "").strip()
    if not wanted:
        raise XnetApiError("UUID کاربر X-NET خالی است.")

    inbounds = await get_inbounds(server)
    pairs = _find_client_records(inbounds, wanted)
    if not pairs:
        raise XnetApiError(f"X-NET subscriber not found (uuid={wanted})")

    inbound, client = pairs[0]
    used = sum(_to_int(c.get("trafficUsedBytes"), 0) for _, c in pairs)
    return _normalize_client(client, inbound, server, used_bytes=used)


async def create_user(
    server: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Create an X-NET subscriber while preserving SellBot's requested UUID."""
    inbounds = await get_inbounds(server)
    target_ids = _selected_inbound_ids(server, inbounds)
    if not target_ids:
        raise XnetApiError("هیچ Inbound قابل استفاده‌ای در X-NET پیدا نشد.")

    requested_uuid = str((payload or {}).get("uuid") or "").strip()
    user_uuid = requested_uuid or str(uuid4())
    if " " in user_uuid or len(user_uuid) < 8:
        raise XnetApiError("UUID درخواستی برای X-NET معتبر نیست.")

    username = str(
        payload.get("name")
        or payload.get("username")
        or payload.get("email")
        or user_uuid
    ).strip()
    if not username:
        username = user_uuid

    gb = _payload_gb(payload)
    traffic_limit = int(round((gb or 0.0) * _GB))
    expire_date = _expiry_from_payload(payload)
    status = _payload_status(payload) or "active"

    client_body: Dict[str, Any] = {
        "username": username.replace(" ", "_"),
        "uuid": user_uuid,
        "status": status,
        "trafficLimitBytes": traffic_limit,
        "autoDisableOnTrafficExhaust": True,
        "expireDate": expire_date,
        "autoDisableOnExpiration": True,
        "maxConnections": _to_int(
            payload.get("maxConnections", payload.get("limitIp", 0)), 0
        ),
    }

    comment = str(payload.get("comment") or "").strip()
    if comment:
        client_body["remark"] = comment
    if payload.get("email"):
        client_body["email"] = str(payload.get("email") or "").strip()

    # X-NET's subscriber form sends the primary inbound plus all additional
    # selected inbounds in one request, preserving one subscription UUID.
    if len(target_ids) > 1:
        client_body["extraInboundIds"] = target_ids[1:]

    created = await _request_json(
        "POST",
        f"/api/inbounds/{target_ids[0]}/clients",
        server,
        json=client_body,
    )
    if not isinstance(created, dict):
        created = {}

    # Always verify by the UUID requested by SellBot; this is what protects
    # cluster UUID consistency when main + child servers are provisioned.
    try:
        return await get_user_by_uuid(server, user_uuid)
    except Exception:
        fallback = dict(client_body)
        fallback.update(created)
        fallback["uuid"] = str(created.get("uuid") or user_uuid)
        inbound = next(
            (i for i in inbounds if str(i.get("id") or "") == target_ids[0]),
            {},
        )
        return _normalize_client(fallback, inbound, server)


def _client_update_body(
    client: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    body: Dict[str, Any] = {}

    # Keep the same fields the official X-NET frontend sends when editing.
    for key in (
        "username",
        "email",
        "uuid",
        "password",
        "remark",
        "status",
        "trafficLimitBytes",
        "expireDate",
        "maxConnections",
        "deviceLimit",
        "restrictByIp",
        "flow",
        "cipher",
        "wgPublicKey",
        "wgPrivateKey",
        "wgAddress",
        "wgPresharedKey",
        "extraInboundIds",
    ):
        if key in client:
            body[key] = client.get(key)

    if payload.get("name") is not None:
        body["username"] = str(payload.get("name") or "").strip().replace(" ", "_")
    if payload.get("username") is not None:
        body["username"] = str(payload.get("username") or "").strip().replace(" ", "_")
    if payload.get("email") is not None:
        body["email"] = str(payload.get("email") or "").strip()
    if payload.get("comment") is not None:
        body["remark"] = str(payload.get("comment") or "").strip()

    requested_uuid = str(payload.get("uuid") or "").strip()
    if requested_uuid:
        body["uuid"] = requested_uuid

    gb = _payload_gb(payload)
    if gb is not None:
        body["trafficLimitBytes"] = int(round(gb * _GB))

    current_expiry = _parse_dt(client.get("expireDate"))
    if any(
        key in payload
        for key in (
            "package_days",
            "expireDate",
            "expire_date",
            "expiration_date",
            "expires_at",
        )
    ):
        body["expireDate"] = _expiry_from_payload(payload, current=current_expiry)

    status = _payload_status(payload)
    if status is not None:
        body["status"] = status

    if "maxConnections" in payload:
        body["maxConnections"] = _to_int(payload.get("maxConnections"), 0)
    elif "limitIp" in payload:
        body["maxConnections"] = _to_int(payload.get("limitIp"), 0)

    return body


async def patch_user(
    server: Dict[str, Any],
    user_uuid: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Update quota/time/status/UUID for every X-NET record of this subscriber."""
    old_uuid = str(user_uuid or "").strip()
    if not old_uuid:
        raise XnetApiError("UUID کاربر X-NET خالی است.")

    inbounds = await get_inbounds(server)
    pairs = _find_client_records(inbounds, old_uuid)
    if not pairs:
        raise XnetApiError(f"X-NET subscriber not found (uuid={old_uuid})")

    new_uuid = str(payload.get("uuid") or "").strip() or old_uuid
    updated_any = False
    for inbound, client in pairs:
        inbound_id = str(inbound.get("id") or "").strip()
        client_id = str(client.get("id") or "").strip()
        if not inbound_id or not client_id:
            continue
        body = _client_update_body(client, payload)
        await _request_json(
            "PUT",
            f"/api/inbounds/{inbound_id}/clients/{client_id}",
            server,
            json=body,
        )
        updated_any = True

    if not updated_any:
        raise XnetApiError("X-NET client record برای بروزرسانی پیدا نشد.")

    # Existing SellBot renewal flows use current_usage_GB=0 as reset semantics.
    if "current_usage_GB" in payload and _to_float(payload.get("current_usage_GB"), -1) == 0:
        try:
            await reset_user_traffic(server, new_uuid)
        except Exception:
            # Do not turn an otherwise successful renewal into a hard failure;
            # callers can retry/reset separately.
            pass

    return await get_user_by_uuid(server, new_uuid)


async def reset_user_traffic(
    server: Dict[str, Any],
    user_uuid: str,
) -> Dict[str, Any]:
    inbounds = await get_inbounds(server)
    pairs = _find_client_records(inbounds, user_uuid)
    if not pairs:
        raise XnetApiError(f"X-NET subscriber not found (uuid={user_uuid})")

    count = 0
    for inbound, client in pairs:
        iid = str(inbound.get("id") or "").strip()
        cid = str(client.get("id") or "").strip()
        if not iid or not cid:
            continue
        await _request_json(
            "POST",
            f"/api/inbounds/{iid}/clients/{cid}/reset-traffic",
            server,
        )
        count += 1
    return {"success": True, "reset_records": count}


async def delete_user(server: Dict[str, Any], user_uuid: str) -> None:
    # UUID-level delete is documented and removes the subscriber irrespective
    # of which inbound is its primary one.
    await _request_json(
        "DELETE",
        f"/api/v1/subscribers/{str(user_uuid or '').strip()}",
        server,
    )


async def enable_user(
    server: Dict[str, Any],
    user_uuid: str,
) -> Dict[str, Any]:
    return await patch_user(server, user_uuid, {"status": "active"})


async def disable_user(
    server: Dict[str, Any],
    user_uuid: str,
) -> Dict[str, Any]:
    return await patch_user(server, user_uuid, {"status": "disabled"})


async def get_traffic_summary(server: Dict[str, Any]) -> Dict[str, Any]:
    data = await _request_json("GET", "/api/traffic/singbox/summary", server)
    if not isinstance(data, dict):
        raise XnetApiError("فرمت traffic summary در X-NET معتبر نیست.")
    return data


async def get_server_stats(server: Dict[str, Any]) -> Dict[str, Any]:
    """Return X-NET metrics in the legacy SellBot server-stats shape."""
    users = await list_users(server)
    try:
        metrics = await _request_json("GET", "/api/metrics", server)
        if not isinstance(metrics, dict):
            metrics = {}
    except Exception:
        metrics = {}
    try:
        traffic = await get_traffic_summary(server)
    except Exception:
        traffic = {}

    ram = metrics.get("ramUsage") if isinstance(metrics.get("ramUsage"), dict) else {}
    storage = (
        metrics.get("storageUsage")
        if isinstance(metrics.get("storageUsage"), dict)
        else {}
    )

    total_upload = _to_int(traffic.get("totalUpload"), 0)
    total_download = _to_int(traffic.get("totalDownload"), 0)
    today_upload = _to_int(traffic.get("todayUpload"), 0)
    today_download = _to_int(traffic.get("todayDownload"), 0)

    return {
        "cpu_percent": _to_float(metrics.get("cpuUsage"), 0.0),
        "cpu_cores": _to_int(metrics.get("cpuCores"), 1),
        "ram_used": _to_float(ram.get("used"), 0.0),
        "ram_total": max(_to_float(ram.get("total"), 1.0), 1.0),
        "disk_used": _to_float(storage.get("used"), 0.0),
        "disk_total": max(_to_float(storage.get("total"), 1.0), 1.0),
        "users_total": len(users),
        "users_online": _to_int(
            metrics.get("onlineUsersCount", traffic.get("activeClients")), 0
        ),
        "users_today": 0,
        "users_month": 0,
        "usage_today_gb": round((today_upload + today_download) / _GB, 3),
        "usage_30days_gb": round((total_upload + total_download) / _GB, 3),
        "traffic_dl": round(total_download / _GB, 3),
        "traffic_ul": round(total_upload / _GB, 3),
        "now_net_recv_mb": 0.0,
        "now_net_sent_mb": 0.0,
        "singbox_status": str(metrics.get("singBoxStatus") or ""),
        "_source": "xnet",
    }


async def get_system_info(server: Dict[str, Any]) -> Dict[str, Any]:
    data = await _request_json("GET", "/api/system/info", server)
    return data if isinstance(data, dict) else {}


async def get_panel_config(server: Dict[str, Any]) -> Dict[str, Any]:
    data = await _request_json("GET", "/api/system/panel-config", server)
    return data if isinstance(data, dict) else {}


def get_subscription_url(server: Dict[str, Any], user_uuid: str) -> str:
    wanted = str(user_uuid or "").strip()
    if not wanted:
        raise XnetApiError("UUID کاربر X-NET خالی است.")
    return f"{_base_url(server)}/api/v1/sub/{wanted}"


async def get_subscription_body(
    server: Dict[str, Any],
    user_uuid: str,
    *,
    format: str = "",
) -> str:
    params = {"format": format} if format else None
    return await _request_text(
        f"/api/v1/sub/{str(user_uuid).strip()}",
        server,
        params=params,
    )


def _decode_base64_subscription(body: str) -> str:
    raw = str(body or "").strip()
    if not raw:
        return ""
    padded = raw + ("=" * ((4 - len(raw) % 4) % 4))
    try:
        return base64.b64decode(padded, validate=False).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return raw


async def get_user_configs(
    server: Dict[str, Any],
    user_uuid: str,
) -> List[Dict[str, Any]]:
    body = await get_subscription_body(server, user_uuid)
    decoded = _decode_base64_subscription(body)
    result: List[Dict[str, Any]] = []
    for line in decoded.splitlines():
        link = line.strip()
        if "://" not in link:
            continue
        protocol = link.split("://", 1)[0].lower()
        result.append({"link": link, "protocol": protocol})
    return result


async def test_connect(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    status = await ping(server)
    if str(status.get("status") or "").strip().lower() != "ok":
        raise XnetApiError("X-NET ping پاسخ ok نداد.")
    # This call intentionally requires a management JWT and therefore also
    # verifies the stored admin credentials.
    await get_inbounds(server)
    return await list_users(server)
