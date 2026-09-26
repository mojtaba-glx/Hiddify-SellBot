"""X-NET panel adapter.

The adapter exposes the same user-management shape the rest of SellBot expects
from Hiddify/X-UI while using X-NET's documented management API.

Important:
- Management calls prefer X-NET's persistent Bearer API token (xnet_api_token).
- Legacy username/password login is kept only as a compatibility fallback for
  existing servers that have not been migrated to an API token yet.
- Client UUID may be supplied on create and may be changed with the client PUT
  endpoint. This lets SellBot keep one canonical UUID across its server cluster.
"""

from __future__ import annotations

import asyncio
import base64
import time
import urllib.parse
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

# X-NET online endpoints are live data. Keep only a very short cache so bulk
# Agent/Admin views do not hit /api/online-users once per subscriber.
_ONLINE_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Dict[str, Any]]]] = {}
_ONLINE_CACHE_LOCK = asyncio.Lock()
_ONLINE_CACHE_TTL = 5.0

# Session-history fallback is used only when X-NET's client object has no
# lastConnectionAt. A short cache prevents repeated detail/status views from
# issuing the same per-client history request over and over.
_LAST_SEEN_CACHE: Dict[Tuple[str, str, str], Tuple[float, Optional[str]]] = {}
_LAST_SEEN_CACHE_LOCK = asyncio.Lock()
_LAST_SEEN_CACHE_TTL = 30.0


def is_xnet_server(server: Dict[str, Any]) -> bool:
    return str((server or {}).get("panel_type") or "").strip().lower() in {
        "xnet",
        "x-net",
    }


def _base_url(server: Dict[str, Any]) -> str:
    """Management/API origin for X-NET.

    panel_url remains the public/browser address. When SellBot runs on the
    same host as X-NET, xnet_api_url can point to loopback so management
    traffic does not depend on public DNS, NAT, TLS termination or firewall
    rules.
    """
    url = str(
        (server or {}).get("xnet_api_url")
        or (server or {}).get("xnet_internal_url")
        or (server or {}).get("panel_url")
        or ""
    ).strip().rstrip("/")
    if not url:
        raise XnetApiError("آدرس API/پنل برای X-NET تنظیم نشده است.")
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


def _api_token(server: Dict[str, Any]) -> str:
    """Return X-NET's persistent management Bearer token."""
    for key in ("xnet_api_token", "xnet_token", "xnet_bearer_token"):
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


async def _management_token(
    server: Dict[str, Any],
    *,
    force_legacy_login: bool = False,
) -> str:
    """Return the credential used for X-NET management API calls.

    A configured persistent API token is authoritative and completely bypasses
    /api/auth/login. Username/password login remains only as a compatibility
    fallback for older server records that do not yet have an API token.
    """
    token = _api_token(server)
    if token:
        return token
    return await _login(server, force=force_legacy_login)


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

    token = await _management_token(server) if auth else ""
    try:
        response = await _send(token)
    except httpx.RequestError as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise XnetApiError(f"خطا در اتصال به X-NET ({type(exc).__name__}): {detail}") from exc

    if auth and response.status_code in {401, 403}:
        # A persistent API token must never fall back to /api/auth/login.
        # If it is invalid/revoked, fail explicitly instead of hammering login.
        if _api_token(server):
            detail = response.text.strip().replace("\n", " ")[:300]
            raise XnetApiError(
                f"توکن API X-NET رد شد (HTTP {response.status_code}): "
                f"{detail or 'توکن را در پنل تولید/کپی و در ربات بروزرسانی کنید.'}"
            )

        # Legacy JWT sessions may expire. Old installations without an API
        # token get one compatibility retry through username/password login.
        if _password(server):
            _TOKEN_CACHE.pop(_cache_key(server), None)
            token = await _management_token(server, force_legacy_login=True)
            try:
                response = await _send(token)
            except httpx.RequestError as exc:
                detail = str(exc).strip() or type(exc).__name__
                raise XnetApiError(
                    f"خطا در اتصال به X-NET ({type(exc).__name__}): {detail}"
                ) from exc

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


async def _request_bytes(
    method: str,
    path: str,
    server: Dict[str, Any],
    *,
    json: Optional[Any] = None,
    params: Optional[Dict[str, Any]] = None,
    auth: bool = True,
    timeout: float = 60.0,
) -> Tuple[bytes, Dict[str, str]]:
    """Authenticated binary request with the same JWT refresh semantics as JSON calls."""
    url = f"{_base_url(server)}/{path.lstrip('/')}"

    async def _send(token: str = "") -> httpx.Response:
        headers = {"Accept": "*/*"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(
                method.upper(), url, headers=headers, json=json, params=params
            )

    token = await _management_token(server) if auth else ""
    try:
        response = await _send(token)
    except httpx.RequestError as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise XnetApiError(f"خطا در اتصال به X-NET ({type(exc).__name__}): {detail}") from exc

    if auth and response.status_code in {401, 403}:
        if _api_token(server):
            detail = response.text.strip().replace("\n", " ")[:300]
            raise XnetApiError(
                f"توکن API X-NET رد شد (HTTP {response.status_code}): "
                f"{detail or 'توکن را در پنل تولید/کپی و در ربات بروزرسانی کنید.'}"
            )
        if _password(server):
            _TOKEN_CACHE.pop(_cache_key(server), None)
            token = await _management_token(server, force_legacy_login=True)
            try:
                response = await _send(token)
            except httpx.RequestError as exc:
                detail = str(exc).strip() or type(exc).__name__
                raise XnetApiError(
                    f"خطا در اتصال به X-NET ({type(exc).__name__}): {detail}"
                ) from exc

    if response.status_code >= 400:
        detail = response.text.strip().replace("\n", " ")[:300]
        raise XnetApiError(
            f"X-NET API HTTP {response.status_code}: {detail or 'request failed'}"
        )

    return bytes(response.content or b""), dict(response.headers)

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
    online: Optional[bool] = None,
    last_online: Optional[str] = None,
    active_sessions: Optional[int] = None,
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

    # X-NET keeps the durable last connection timestamp on the client model.
    # Accept a few historical field names for compatibility with older builds.
    if not last_online:
        for key in (
            "lastConnectionAt",
            "last_connection_at",
            "lastOnline",
            "last_online",
            "lastSeen",
            "last_seen",
        ):
            dt = _parse_dt(client.get(key))
            if dt is not None:
                last_online = dt.isoformat().replace("+00:00", "Z")
                break

    if online is None:
        try:
            online = _to_int(client.get("activeSessions"), 0) > 0
        except Exception:
            online = False
    if online:
        # For an actively connected client, "last online" is effectively now.
        # Keep the explicit online flag authoritative; the timestamp is for
        # relative-time consumers that only know about last_online.
        last_online = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    if active_sessions is None:
        active_sessions = _to_int(client.get("activeSessions"), 0)

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
        "last_online": last_online,
        "lastConnectionAt": last_online,
        "activeSessions": max(0, _to_int(active_sessions, 0)),
        "_user_list_status": "online" if bool(online) else "offline",
        "_source": "xnet",
    }


async def _online_client_map(
    server: Dict[str, Any],
    *,
    force: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Return currently-online X-NET sing-box clients keyed by client id."""
    key = _cache_key(server)
    now = time.monotonic()
    if not force:
        cached = _ONLINE_CACHE.get(key)
        if cached and cached[0] > now:
            return dict(cached[1])

    async with _ONLINE_CACHE_LOCK:
        if not force:
            cached = _ONLINE_CACHE.get(key)
            if cached and cached[0] > time.monotonic():
                return dict(cached[1])

        rows: List[Dict[str, Any]] = []
        got_online_snapshot = False

        # Preferred endpoint: authoritative combined online overview. An empty
        # singbox list is a valid "nobody online" result and must not trigger
        # two more fallback requests.
        try:
            data = await _request_json("GET", "/api/online-users", server)
            raw = data.get("singbox") if isinstance(data, dict) else None
            if isinstance(raw, list):
                rows = [dict(x) for x in raw if isinstance(x, dict)]
                got_online_snapshot = True
        except Exception:
            pass

        # Fallback for older builds: realtime per-client online status.
        if not got_online_snapshot:
            try:
                data = await _request_json(
                    "GET", "/api/traffic/singbox/realtime", server
                )
                raw = data.get("clients") if isinstance(data, dict) else None
                if isinstance(raw, list):
                    rows = [
                        dict(x)
                        for x in raw
                        if isinstance(x, dict) and bool(x.get("isOnline"))
                    ]
                    got_online_snapshot = True
            except Exception:
                pass

        # Last fallback: dedicated online list.
        if not got_online_snapshot:
            try:
                data = await _request_json(
                    "GET", "/api/traffic/singbox/online", server
                )
                raw = data.get("users") if isinstance(data, dict) else None
                if isinstance(raw, list):
                    rows = [dict(x) for x in raw if isinstance(x, dict)]
                    got_online_snapshot = True
            except Exception:
                pass

        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            cid = str(
                row.get("clientId")
                or row.get("client_id")
                or row.get("id")
                or ""
            ).strip()
            if cid:
                result[cid] = row

        _ONLINE_CACHE[key] = (
            time.monotonic() + _ONLINE_CACHE_TTL,
            dict(result),
        )
        return result


def _latest_client_last_online(client: Dict[str, Any]) -> Optional[str]:
    latest: Optional[datetime] = None
    for key in (
        "lastConnectionAt",
        "last_connection_at",
        "lastOnline",
        "last_online",
        "lastSeen",
        "last_seen",
    ):
        dt = _parse_dt(client.get(key))
        if dt is not None and (latest is None or dt > latest):
            latest = dt
    if latest is None:
        return None
    return latest.isoformat().replace("+00:00", "Z")


async def _last_seen_from_sessions(
    server: Dict[str, Any],
    client_id: str,
) -> Optional[str]:
    """Read latest presence timestamp for one client from session history."""
    cid = str(client_id or "").strip()
    if not cid:
        return None
    cache_key = (*_cache_key(server), cid)
    now = time.monotonic()
    cached = _LAST_SEEN_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    try:
        data = await _request_json(
            "GET",
            f"/api/traffic/singbox/clients/{cid}/sessions",
            server,
        )
    except Exception:
        data = {}

    rows = data.get("sessions") if isinstance(data, dict) else []
    latest: Optional[datetime] = None
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in (
                "lastSeen",
                "last_seen",
                "endTime",
                "end_time",
                "startTime",
                "start_time",
            ):
                dt = _parse_dt(row.get(key))
                if dt is not None and (latest is None or dt > latest):
                    latest = dt

    value = (
        latest.isoformat().replace("+00:00", "Z")
        if latest is not None
        else None
    )
    async with _LAST_SEEN_CACHE_LOCK:
        _LAST_SEEN_CACHE[cache_key] = (
            time.monotonic() + _LAST_SEEN_CACHE_TTL,
            value,
        )
    return value


def _runtime_for_pairs(
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    online_map: Dict[str, Dict[str, Any]],
) -> Tuple[bool, Optional[str], int]:
    online = False
    latest: Optional[datetime] = None
    sessions = 0

    for _inbound, client in pairs:
        cid = str(client.get("id") or "").strip()
        online_row = online_map.get(cid) if cid else None
        if online_row is not None:
            online = True
            # /api/online-users exposes device count while realtime can expose
            # one row per connected client. Either way keep a non-zero signal.
            row_sessions = _to_int(
                online_row.get("sessions", online_row.get("devices", 1)), 1
            )
            sessions += max(1, row_sessions)
        else:
            sessions += max(0, _to_int(client.get("activeSessions"), 0))
            if _to_int(client.get("activeSessions"), 0) > 0:
                online = True

        candidate = _parse_dt(_latest_client_last_online(client))
        if candidate is not None and (latest is None or candidate > latest):
            latest = candidate

    if online:
        latest = datetime.now(timezone.utc)

    return (
        online,
        latest.isoformat().replace("+00:00", "Z") if latest is not None else None,
        sessions,
    )


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


def _persist_main_guard_snapshot(
    server: Dict[str, Any],
    users: List[Dict[str, Any]],
) -> None:
    """Best-effort durable snapshot for main X-NET servers only."""
    try:
        server_id = int((server or {}).get("id") or 0)
        if server_id <= 0:
            return
        from Shared import database as _database, userbot_db as _userbot_db
        main_ids = {
            int((row or {}).get("id") or 0)
            for row in (_database.get_main_servers() or [])
        }
        if server_id in main_ids:
            _userbot_db.upsert_xnet_guard_snapshot_users(server_id, users)
    except Exception as exc:
        logger.warning(
            "X-NET guard snapshot save skipped server_id=%s: %s",
            (server or {}).get("id"),
            exc,
        )


async def list_users(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    inbounds, online_map = await asyncio.gather(
        get_inbounds(server),
        _online_client_map(server),
    )
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
        seen_client_ids: set[str] = set()
        used = 0
        for _, row in pairs:
            cid = str(row.get("id") or "").strip()
            dedup_key = cid or f"row-{id(row)}"
            if dedup_key in seen_client_ids:
                continue
            seen_client_ids.add(dedup_key)
            used += _to_int(row.get("trafficUsedBytes"), 0)
        online, last_online, active_sessions = _runtime_for_pairs(
            pairs, online_map
        )
        out.append(
            _normalize_client(
                client,
                inbound,
                server,
                used_bytes=used,
                online=online,
                last_online=last_online,
                active_sessions=active_sessions,
            )
        )

    # Main X-NET servers get a durable recovery snapshot automatically whenever
    # their user list is read. Child-node X-NET servers intentionally skip this.
    _persist_main_guard_snapshot(server, out)
    return out


async def get_user_by_uuid(
    server: Dict[str, Any],
    user_uuid: str,
) -> Dict[str, Any]:
    wanted = str(user_uuid or "").strip()
    if not wanted:
        raise XnetApiError("UUID کاربر X-NET خالی است.")

    inbounds, online_map = await asyncio.gather(
        get_inbounds(server),
        _online_client_map(server),
    )
    pairs = _find_client_records(inbounds, wanted)
    if not pairs:
        raise XnetApiError(f"X-NET subscriber not found (uuid={wanted})")

    inbound, client = pairs[0]
    seen_client_ids: set[str] = set()
    used = 0
    for _, row in pairs:
        cid = str(row.get("id") or "").strip()
        dedup_key = cid or f"row-{id(row)}"
        if dedup_key in seen_client_ids:
            continue
        seen_client_ids.add(dedup_key)
        used += _to_int(row.get("trafficUsedBytes"), 0)

    online, last_online, active_sessions = _runtime_for_pairs(
        pairs, online_map
    )

    # The list/inbound model normally already carries lastConnectionAt. For
    # older X-NET builds that do not, a single-user detail request falls back
    # to the client's session history so "X دقیقه پیش" still works.
    if not online and not last_online:
        history_values = await asyncio.gather(
            *[
                _last_seen_from_sessions(server, cid)
                for cid in sorted(seen_client_ids)
                if cid and not cid.startswith("row-")
            ]
        )
        latest_hist: Optional[datetime] = None
        for value in history_values:
            dt = _parse_dt(value)
            if dt is not None and (latest_hist is None or dt > latest_hist):
                latest_hist = dt
        if latest_hist is not None:
            last_online = latest_hist.isoformat().replace("+00:00", "Z")

    return _normalize_client(
        client,
        inbound,
        server,
        used_bytes=used,
        online=online,
        last_online=last_online,
        active_sessions=active_sessions,
    )


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
        "username": username,
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
        result = await get_user_by_uuid(server, user_uuid)
        _persist_main_guard_snapshot(server, [result])
        return result
    except Exception:
        fallback = dict(client_body)
        fallback.update(created)
        fallback["uuid"] = str(created.get("uuid") or user_uuid)
        inbound = next(
            (i for i in inbounds if str(i.get("id") or "") == target_ids[0]),
            {},
        )
        result = _normalize_client(fallback, inbound, server)
        _persist_main_guard_snapshot(server, [result])
        return result


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
        body["username"] = str(payload.get("name") or "").strip()
    if payload.get("username") is not None:
        body["username"] = str(payload.get("username") or "").strip()
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
    seen_client_ids: set[str] = set()
    for inbound, client in pairs:
        inbound_id = str(inbound.get("id") or "").strip()
        client_id = str(client.get("id") or "").strip()
        if not inbound_id or not client_id or client_id in seen_client_ids:
            continue
        seen_client_ids.add(client_id)
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

    result = await get_user_by_uuid(server, new_uuid)
    _persist_main_guard_snapshot(server, [result])
    return result


async def sync_users_to_inbounds(server: Dict[str, Any]) -> Dict[str, Any]:
    """Attach every existing X-NET subscriber to all configured target inbounds.

    X-NET's own subscription editor models one primary inbound plus
    ``extraInboundIds``. Updating that field lets us add a newly-created
    inbound without recreating the subscriber, changing its UUID, quota, expiry,
    status, or traffic counters.
    """
    inbounds = await get_inbounds(server)
    target_ids = _selected_inbound_ids(server, inbounds)
    if not target_ids:
        return {
            "ok": False,
            "msg": "هیچ Inbound هدف قابل استفاده‌ای در X-NET پیدا نشد.",
            "created": 0,
            "skipped": 0,
            "errors": [],
            "total_users": 0,
            "target_inbounds": 0,
        }

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

    if not groups:
        return {
            "ok": True,
            "created": 0,
            "skipped": 0,
            "errors": [],
            "total_users": 0,
            "target_inbounds": len(target_ids),
        }

    target_set = set(target_ids)
    added = 0
    skipped = 0
    errors: List[str] = []
    requested_missing: Dict[str, List[str]] = {}

    for key in order:
        pairs = groups[key]
        current_ids: List[str] = []
        for inbound, _client in pairs:
            iid = str(inbound.get("id") or "").strip()
            if iid and iid not in current_ids:
                current_ids.append(iid)

        missing = [iid for iid in target_ids if iid not in current_ids]
        if not missing:
            skipped += 1
            continue

        anchor_inbound, anchor_client = pairs[0]
        for inbound, client in pairs:
            iid = str(inbound.get("id") or "").strip()
            if iid in target_set:
                anchor_inbound, anchor_client = inbound, client
                break

        anchor_id = str(anchor_inbound.get("id") or "").strip()
        client_id = str(anchor_client.get("id") or "").strip()
        user_uuid = _client_uuid(anchor_client)
        if not anchor_id or not client_id or not user_uuid:
            errors.append(f"{user_uuid or key}: رکورد اصلی کاربر ناقص است.")
            continue

        connected_ids: List[str] = []
        for iid in current_ids + target_ids:
            if iid and iid not in connected_ids:
                connected_ids.append(iid)

        body = _client_update_body(anchor_client, {})
        body["extraInboundIds"] = [
            iid for iid in connected_ids if iid != anchor_id
        ]

        try:
            await _request_json(
                "PUT",
                f"/api/inbounds/{anchor_id}/clients/{client_id}",
                server,
                json=body,
            )
            requested_missing[user_uuid.lower()] = list(missing)
            added += len(missing)
        except Exception as exc:
            username = str(
                anchor_client.get("username")
                or anchor_client.get("email")
                or user_uuid
            ).strip()
            errors.append(f"{username}: {str(exc)[:160]}")

    if requested_missing:
        try:
            fresh = await get_inbounds(server)
            fresh_presence: Dict[str, set[str]] = {}
            for inbound in fresh:
                iid = str(inbound.get("id") or "").strip()
                for client in inbound.get("clients") or []:
                    if not isinstance(client, dict):
                        continue
                    uid = _client_uuid(client).lower()
                    if uid and iid:
                        fresh_presence.setdefault(uid, set()).add(iid)

            verified_added = 0
            for uid, missing_ids in requested_missing.items():
                present = fresh_presence.get(uid, set())
                absent = [iid for iid in missing_ids if iid not in present]
                verified_added += len(missing_ids) - len(absent)
                if absent:
                    errors.append(
                        f"{uid}: اتصال به {len(absent)} Inbound تأیید نشد."
                    )
            added = verified_added
        except Exception as exc:
            errors.append(f"تأیید نهایی همگام‌سازی انجام نشد: {str(exc)[:160]}")

    return {
        "ok": True,
        "created": added,
        "skipped": skipped,
        "errors": errors,
        "total_users": len(groups),
        "target_inbounds": len(target_ids),
    }


async def reset_user_traffic(
    server: Dict[str, Any],
    user_uuid: str,
) -> Dict[str, Any]:
    inbounds = await get_inbounds(server)
    pairs = _find_client_records(inbounds, user_uuid)
    if not pairs:
        raise XnetApiError(f"X-NET subscriber not found (uuid={user_uuid})")

    count = 0
    seen_client_ids: set[str] = set()
    for inbound, client in pairs:
        iid = str(inbound.get("id") or "").strip()
        cid = str(client.get("id") or "").strip()
        if not iid or not cid or cid in seen_client_ids:
            continue
        seen_client_ids.add(cid)
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


async def _get_traffic_analytics(
    server: Dict[str, Any],
    *,
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    params = {
        "from": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "to": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    data = await _request_json(
        "GET",
        "/api/traffic/singbox/analytics",
        server,
        params=params,
    )
    return data if isinstance(data, dict) else {}


def _analytics_period_stats(data: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """Return (period_bytes, unique_vpn_clients) when window fields are supported."""
    if not isinstance(data, dict):
        return None

    consumers = data.get("consumers")
    if not isinstance(consumers, list):
        consumers = []

    has_period_fields = any(
        key in data for key in ("windowHasData", "periodTotal", "periodUpload", "periodDownload")
    )
    if not has_period_fields:
        has_period_fields = any(
            isinstance(row, dict)
            and any(
                key in row
                for key in ("periodTotal", "periodUpload", "periodDownload")
            )
            for row in consumers
        )
    if not has_period_fields:
        return None

    consumer_period_supported = any(
        isinstance(row, dict)
        and any(
            key in row for key in ("periodTotal", "periodUpload", "periodDownload")
        )
        for row in consumers
    )

    period_bytes = 0
    active_ids: set[str] = set()
    if consumer_period_supported:
        # Analytics can include SSH accounts too. SellBot's X-NET server status
        # is VPN/sing-box oriented, so only VPN consumers belong in these totals.
        for row in consumers:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "vpn").strip().lower()
            if kind == "ssh":
                continue
            if "periodTotal" in row:
                used = max(0, _to_int(row.get("periodTotal"), 0))
            else:
                used = max(
                    0,
                    _to_int(row.get("periodUpload"), 0)
                    + _to_int(row.get("periodDownload"), 0),
                )
            period_bytes += used
            if used <= 0:
                continue
            identity = str(
                row.get("clientId")
                or row.get("client_id")
                or row.get("uuid")
                or row.get("username")
                or ""
            ).strip()
            if identity:
                active_ids.add(identity)
    else:
        # Older builds may expose only top-level period totals.
        if "periodTotal" in data:
            period_bytes = max(0, _to_int(data.get("periodTotal"), 0))
        else:
            period_bytes = max(
                0,
                _to_int(data.get("periodUpload"), 0)
                + _to_int(data.get("periodDownload"), 0),
            )

    return max(0, period_bytes), len(active_ids)


async def _get_realtime_network_mb(server: Dict[str, Any]) -> Tuple[float, float]:
    """Return current (download_mb_s, upload_mb_s) using X-NET live metrics."""
    try:
        tick = await _request_json("GET", "/api/metrics/tick", server)
        if isinstance(tick, dict) and isinstance(tick.get("networkTraffic"), dict):
            net = tick.get("networkTraffic") or {}
            # X-NET frontend exposes these values directly as MB/s.
            return (
                max(0.0, _to_float(net.get("down"), 0.0)),
                max(0.0, _to_float(net.get("up"), 0.0)),
            )
    except Exception:
        pass

    # Compatibility fallback: realtime rates are bytes/s per client.
    try:
        live = await _request_json("GET", "/api/traffic/singbox/realtime", server)
        clients = live.get("clients") if isinstance(live, dict) else []
        if not isinstance(clients, list):
            clients = []
        upload_rate = 0.0
        download_rate = 0.0
        for row in clients:
            if not isinstance(row, dict):
                continue
            upload_rate += max(0.0, _to_float(row.get("uploadRate"), 0.0))
            download_rate += max(0.0, _to_float(row.get("downloadRate"), 0.0))
        mib = float(1024 ** 2)
        return round(download_rate / mib, 3), round(upload_rate / mib, 3)
    except Exception:
        return 0.0, 0.0


async def get_server_stats(server: Dict[str, Any]) -> Dict[str, Any]:
    """Return real X-NET system, traffic, presence and realtime network stats."""
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

    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    month_start = now - timedelta(days=30)

    async def _safe_analytics(start: datetime) -> Dict[str, Any]:
        try:
            return await _get_traffic_analytics(server, start=start, end=now)
        except Exception:
            return {}

    month_analytics, realtime_net = await asyncio.gather(
        _safe_analytics(month_start),
        _get_realtime_network_mb(server),
    )

    month_period = _analytics_period_stats(month_analytics)

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

    current_online = sum(
        1
        for user in users
        if isinstance(user, dict)
        and str(user.get("_user_list_status") or "").strip().lower() == "online"
    )
    users_online = _to_int(
        metrics.get("onlineUsersCount", traffic.get("activeClients")),
        current_online,
    )
    users_online = max(current_online, users_online)

    today_users = 0
    month_users = 0
    for user in users:
        if not isinstance(user, dict):
            continue
        is_online = (
            str(user.get("_user_list_status") or "").strip().lower() == "online"
        )
        last_seen = _parse_dt(
            user.get("last_online")
            or user.get("lastConnectionAt")
            or user.get("last_seen")
        )
        if is_online:
            today_users += 1
            month_users += 1
            continue
        if last_seen is None:
            continue
        if today_start <= last_seen <= now + timedelta(minutes=5):
            today_users += 1
        if month_start <= last_seen <= now + timedelta(minutes=5):
            month_users += 1

    # Analytics is a useful fallback when an older client row has no
    # lastConnectionAt/session timestamp. Never let it reduce presence counts.
    if month_period is not None:
        month_users = max(month_users, month_period[1])
    today_users = max(today_users, users_online)
    month_users = max(month_users, users_online)

    if month_period is not None:
        usage_30days_gb = round(month_period[0] / _GB, 3)
    else:
        # Older X-NET builds did not expose period* analytics fields.
        usage_30days_gb = round((total_upload + total_download) / _GB, 3)

    recv_mb_s, sent_mb_s = realtime_net

    return {
        "cpu_percent": _to_float(metrics.get("cpuUsage"), 0.0),
        "cpu_cores": _to_int(metrics.get("cpuCores"), 1),
        "ram_used": _to_float(ram.get("used"), 0.0),
        "ram_total": max(_to_float(ram.get("total"), 1.0), 1.0),
        "disk_used": _to_float(storage.get("used"), 0.0),
        "disk_total": max(_to_float(storage.get("total"), 1.0), 1.0),
        "users_total": len(users),
        "users_online": users_online,
        "users_today": today_users,
        "users_month": month_users,
        "usage_today_gb": round((today_upload + today_download) / _GB, 3),
        "usage_30days_gb": usage_30days_gb,
        "traffic_dl": round(total_download / _GB, 3),
        "traffic_ul": round(total_upload / _GB, 3),
        "now_net_recv_mb": recv_mb_s,
        "now_net_sent_mb": sent_mb_s,
        "singbox_status": str(metrics.get("singBoxStatus") or ""),
        "_source": "xnet",
    }


async def get_system_info(server: Dict[str, Any]) -> Dict[str, Any]:
    data = await _request_json("GET", "/api/system/info", server)
    return data if isinstance(data, dict) else {}


async def get_panel_config(server: Dict[str, Any]) -> Dict[str, Any]:
    data = await _request_json("GET", "/api/system/panel-config", server)
    return data if isinstance(data, dict) else {}

def _backup_meta_dict(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    for key in ("data", "backup", "result"):
        nested = value.get(key)
        if isinstance(nested, dict):
            return dict(nested)
    return dict(value)


def _backup_rows(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [dict(x) for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in ("data", "backups", "items", "result"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [dict(x) for x in nested if isinstance(x, dict)]
    return []


def _safe_backup_filename(raw: Any, server: Dict[str, Any]) -> str:
    name = str(raw or "").strip().replace("\\", "/").split("/")[-1].strip()
    if name:
        # Keep X-NET's own extension (.db/.zip/...) because the download route
        # returns a binary archive whose exact format is panel-version specific.
        cleaned = "".join(
            ch if (ch.isalnum() or ch in "._- @()") else "_"
            for ch in name
        ).strip(" .")
        if cleaned:
            return cleaned

    host = urllib.parse.urlparse(_base_url(server)).hostname or "xnet"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    return f"xnet-{host}-{ts}.db"


async def download_server_backup(server: Dict[str, Any]) -> Dict[str, Any]:
    """Create and download a fresh X-NET backup archive.

    X-NET creates a server-side backup with POST /api/backups and exposes
    the binary archive through GET /api/backups/{id}/download.
    """
    created_raw = await _request_json("POST", "/api/backups", server)
    meta = _backup_meta_dict(created_raw)
    backup_id = str(
        meta.get("id")
        or meta.get("backupId")
        or meta.get("backup_id")
        or ""
    ).strip()

    # Compatibility fallback for builds that acknowledge creation but do not
    # return the full object. The newest list entry is the backup just made.
    if not backup_id:
        rows = _backup_rows(await _request_json("GET", "/api/backups", server))
        if rows:
            rows.sort(
                key=lambda row: str(
                    row.get("createdAt")
                    or row.get("created_at")
                    or row.get("timestamp")
                    or ""
                ),
                reverse=True,
            )
            meta = rows[0]
            backup_id = str(
                meta.get("id")
                or meta.get("backupId")
                or meta.get("backup_id")
                or ""
            ).strip()

    if not backup_id:
        raise XnetApiError("X-NET بعد از ساخت بکاپ شناسه فایل برنگرداند.")

    quoted_id = urllib.parse.quote(backup_id, safe="")
    download_path = f"/api/backups/{quoted_id}/download"
    body, headers = await _request_bytes(
        "GET",
        download_path,
        server,
        timeout=120.0,
    )
    if not body:
        raise XnetApiError("فایل بکاپ X-NET خالی دریافت شد.")

    filename_raw = (
        meta.get("filename")
        or meta.get("fileName")
        or meta.get("name")
        or ""
    )
    if not filename_raw:
        disposition = str(
            headers.get("content-disposition")
            or headers.get("Content-Disposition")
            or ""
        )
        if "filename=" in disposition.lower():
            filename_raw = disposition.split("filename=", 1)[-1].split(";", 1)[0]
            filename_raw = str(filename_raw).strip().strip('"').strip("'")

    filename = _safe_backup_filename(filename_raw, server)
    result = {
        "filename": filename,
        "content": body,
        "source_url": f"{_base_url(server)}{download_path}",
    }

    # Cleanup is best-effort: after bytes are downloaded, a delete failure
    # must not discard a valid Telegram/full-backup artifact.
    try:
        await _request_json(
            "DELETE",
            f"/api/backups/{quoted_id}",
            server,
        )
    except Exception:
        pass

    return result


def _public_origin(server: Dict[str, Any]) -> str:
    """Public origin used by X-NET's dedicated subscription listener.

    X-NET deliberately serves customer subscriptions on a port separate from
    the management panel.  Current X-NET defaults are 2096 + /sub, and both
    values can be overridden per SellBot server with xnet_sub_port and
    xnet_sub_path.
    """
    custom = str(
        (server or {}).get("xnet_sub_domain")
        or (server or {}).get("xnet_sub_host")
        or ""
    ).strip()
    raw = custom or str((server or {}).get("panel_url") or "").strip()
    if not raw:
        raw = _base_url(server)
    if "://" not in raw:
        raw = "https://" + raw
    raw = raw.rstrip("/")

    try:
        parsed = urllib.parse.urlparse(raw)
        host = str(parsed.hostname or "").strip()
        scheme = str(parsed.scheme or "https").strip().lower() or "https"
        if host:
            configured_port = _to_int((server or {}).get("xnet_sub_port"), 2096)
            sub_port = configured_port if configured_port > 0 else 2096
            # Keep an explicitly configured custom subscription URL port unless
            # it is the management port.  Otherwise use X-NET's subscription
            # listener port (2096 by default).
            explicit_port = parsed.port
            if custom and explicit_port and explicit_port != 8080 and not (server or {}).get("xnet_sub_port"):
                sub_port = explicit_port
            default_port = (scheme == "https" and sub_port == 443) or (scheme == "http" and sub_port == 80)
            return f"{scheme}://{host}" if default_port else f"{scheme}://{host}:{sub_port}"
    except Exception:
        pass

    return raw


def get_admin_web_url(server: Dict[str, Any], section: str = "#/subscriptions") -> str:
    """Build the browser/admin-panel URL for an X-NET UI section.

    This is intentionally separate from get_subscription_url(): the latter is
    the public client subscription endpoint, while this URL opens X-NET's web
    interface for the operator.
    """
    raw = str((server or {}).get("panel_url") or "").strip().rstrip("/")
    if not raw:
        return ""

    web_base_path = str((server or {}).get("xnet_web_base_path") or "").strip("/")
    if web_base_path:
        raw = f"{raw}/{web_base_path}"

    route = str(section or "").strip()
    if not route:
        return raw
    if not route.startswith("#"):
        route = "#" + route.lstrip("#")
    return f"{raw}/{route}"


def get_subscription_url(server: Dict[str, Any], user_uuid: str) -> str:
    """Return X-NET's public customer subscription URL.

    This is intentionally not /api/v1/sub/<uuid>: that route belongs to the
    management/API listener.  The public listener uses /<vpn-path>/<uuid>.
    """
    wanted = str(user_uuid or "").strip()
    if not wanted:
        raise XnetApiError("UUID کاربر X-NET خالی است.")
    sub_path = str(
        (server or {}).get("xnet_sub_path")
        or (server or {}).get("xnet_sub_path_vpn")
        or "sub"
    ).strip().strip("/") or "sub"
    return f"{_public_origin(server)}/{sub_path}/{urllib.parse.quote(wanted, safe='')}"


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



def parse_config_link(link: str) -> Dict[str, Any]:
    """Parse a share URI using SellBot's existing, battle-tested link parser."""
    from Shared import xui_api

    try:
        return xui_api.parse_config_link(link)
    except Exception as exc:
        raise XnetApiError(str(exc)) from exc


async def get_singbox_compatibility(server: Dict[str, Any]) -> Dict[str, Any]:
    data = await _request_json("GET", "/api/singbox/compatibility", server)
    return data if isinstance(data, dict) else {}


def _canonical_transport(raw: Any) -> str:
    value = str(raw or "tcp").strip().lower().replace("-", "").replace("_", "")
    mapping = {
        "tcp": "TCP",
        "ws": "WS",
        "websocket": "WS",
        "grpc": "gRPC",
        "httpupgrade": "HTTPUpgrade",
        "http2": "HTTP/2",
        "h2": "HTTP/2",
        "quic": "QUIC",
    }
    return mapping.get(value, str(raw or "TCP").strip())


def _transport_compat_key(raw: Any) -> str:
    value = _canonical_transport(raw).strip().lower()
    return {
        "http/2": "http2",
        "httpupgrade": "httpupgrade",
        "grpc": "grpc",
        "ws": "ws",
        "tcp": "tcp",
        "quic": "quic",
    }.get(value, value.replace("/", ""))


def _split_alpn(raw: Any, default: Optional[List[str]] = None) -> List[str]:
    if isinstance(raw, (list, tuple, set)):
        values = [str(x).strip() for x in raw if str(x).strip()]
    else:
        values = [
            x.strip()
            for x in str(raw or "").replace(";", ",").split(",")
            if x.strip()
        ]
    return values or list(default or [])


def _link_remark(link: str, protocol: str, port: int) -> str:
    try:
        fragment = str(link or "").split("#", 1)[1]
    except IndexError:
        fragment = ""
    try:
        fragment = urllib.parse.unquote(fragment).strip()
    except Exception:
        fragment = fragment.strip()
    return fragment or f"{protocol}-{port}"


def _local_public_host(server: Dict[str, Any]) -> str:
    raw = str(
        (server or {}).get("xnet_sub_domain")
        or (server or {}).get("xnet_sub_host")
        or (server or {}).get("panel_url")
        or ""
    ).strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        return str(urllib.parse.urlparse(raw).hostname or "").strip()
    except Exception:
        return ""


def _tls_candidate_from_inbound(inbound: Dict[str, Any]) -> Optional[Dict[str, str]]:
    def _from_row(row: Dict[str, Any]) -> Optional[Dict[str, str]]:
        cert = str(
            row.get("certFile")
            or row.get("certPath")
            or row.get("certificateFile")
            or ""
        ).strip()
        key = str(
            row.get("keyFile")
            or row.get("keyPath")
            or row.get("privateKeyPath")
            or ""
        ).strip()
        if not cert or not key:
            return None
        sni = str(
            row.get("sni")
            or row.get("domain")
            or row.get("domainBinding")
            or ""
        ).strip()
        return {"certFile": cert, "keyFile": key, "sni": sni}

    direct = _from_row(inbound)
    if direct:
        return direct

    node_tls = inbound.get("nodeTls")
    if isinstance(node_tls, dict):
        for value in node_tls.values():
            if isinstance(value, dict):
                candidate = _from_row(value)
                if candidate:
                    return candidate
    return None


async def _select_local_tls_material(
    server: Dict[str, Any],
    inbounds: List[Dict[str, Any]],
    *,
    preferred_sni: str = "",
) -> Dict[str, str]:
    """Pick certificate/key already installed on this X-NET server.

    A client link never contains a server private key, so importing a link must
    reuse local TLS material instead of copying the source server identity.
    """
    local_host = _local_public_host(server).lower()
    preferred = str(preferred_sni or "").strip().lower()

    candidates: List[Dict[str, str]] = []
    for inbound in inbounds:
        if not isinstance(inbound, dict):
            continue
        candidate = _tls_candidate_from_inbound(inbound)
        if candidate:
            candidates.append(candidate)

    # Some X-NET builds expose certificate filesystem paths in this response.
    # Use them when available, but do not guess paths from only a domain name.
    if not candidates:
        try:
            data = await _request_json("GET", "/api/certificates", server)
            rows = data if isinstance(data, list) else (
                data.get("certificates") if isinstance(data, dict) else []
            )
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    candidate = _tls_candidate_from_inbound(row)
                    if candidate:
                        candidates.append(candidate)
        except Exception:
            pass

    if not candidates:
        raise XnetApiError(
            "برای ساخت اینباند TLS از روی لینک، گواهی و کلید محلی X-NET پیدا نشد. "
            "ابتدا در X-NET یک گواهی معتبر تنظیم کنید یا یک اینباند TLS معتبر بسازید."
        )

    def _score(item: Dict[str, str]) -> int:
        sni = str(item.get("sni") or "").strip().lower()
        score = 0
        if local_host and sni == local_host:
            score += 4
        if preferred and sni == preferred:
            score += 2
        if sni:
            score += 1
        return score

    chosen = max(candidates, key=_score)
    sni = str(chosen.get("sni") or "").strip() or _local_public_host(server)
    if not sni:
        raise XnetApiError(
            "گواهی محلی پیدا شد ولی دامنه/SNI آن مشخص نیست؛ SNI گواهی را در X-NET تنظیم کنید."
        )
    return {
        "certFile": str(chosen["certFile"]),
        "keyFile": str(chosen["keyFile"]),
        "sni": sni,
    }


def _compat_protocol_entry(data: Dict[str, Any], protocol: str) -> Dict[str, Any]:
    root = data.get("protocols") if isinstance(data, dict) else None
    if not isinstance(root, dict):
        root = data if isinstance(data, dict) else {}
    wanted = str(protocol or "").strip().lower()
    for key, value in root.items():
        if str(key).strip().lower() == wanted and isinstance(value, dict):
            return value
    return {}


async def create_inbound_from_link(
    server: Dict[str, Any],
    link: str,
    *,
    port_override: Optional[int] = None,
    remark: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an X-NET inbound from the connection shape in a share URI.

    The URI is treated as a template. Source-user credentials are deliberately
    not imported. TLS inbounds reuse this X-NET server's existing certificate.
    REALITY is rejected because a client URI does not contain the server private
    key needed to reproduce the inbound safely.
    """
    parsed = parse_config_link(link)
    protocol = str(parsed.get("protocol") or "").strip().lower()
    supported = {"vless", "vmess", "trojan", "hysteria2", "shadowsocks"}
    if protocol not in supported:
        raise XnetApiError(
            "ساخت اینباند X-NET از این نوع لینک فعلاً پشتیبانی نمی‌شود: "
            f"{protocol or 'unknown'}"
        )

    security = str(
        parsed.get("security")
        or ("tls" if protocol == "hysteria2" else "none")
    ).strip().lower()
    if security == "reality":
        raise XnetApiError(
            "لینک REALITY فقط کلید عمومی سمت کاربر را دارد و کلید خصوصی سرور داخل لینک نیست؛ "
            "برای جلوگیری از ساخت کانفیگ خراب، REALITY باید داخل خود X-NET ساخته شود."
        )

    try:
        port = int(port_override) if port_override else int(parsed.get("port") or 443)
    except (TypeError, ValueError) as exc:
        raise XnetApiError("پورت لینک معتبر نیست.") from exc
    if not 1 <= port <= 65535:
        raise XnetApiError("پورت باید بین 1 تا 65535 باشد.")

    inbounds = await get_inbounds(server)
    used_ports = {
        _to_int(row.get("port"), 0)
        for row in inbounds
        if isinstance(row, dict) and _to_int(row.get("port"), 0) > 0
    }

    # Protect panel/subscription ports too. Other system-level conflicts are
    # still authoritatively checked by X-NET when POST /api/inbounds is called.
    try:
        pu = urllib.parse.urlparse(_base_url(server))
        panel_url_port = pu.port or (443 if pu.scheme == "https" else 80)
        if panel_url_port:
            used_ports.add(int(panel_url_port))
    except Exception:
        pass
    try:
        panel_cfg = await get_panel_config(server)
        for key in ("port", "subPort"):
            p = _to_int(panel_cfg.get(key), 0)
            if p > 0:
                used_ports.add(p)
    except Exception:
        pass

    if port in used_ports:
        alt = port + 1
        while alt <= 65535 and alt in used_ports:
            alt += 1
        if alt > 65535:
            alt = 1024
            while alt < port and alt in used_ports:
                alt += 1
        hint = f" مثلاً {alt}" if 1 <= alt <= 65535 and alt not in used_ports else ""
        raise XnetApiError(
            f"پورت {port} قبلاً توسط پنل/ساب/اینباند X-NET استفاده شده است؛ "
            f"یک پورت آزاد انتخاب کنید.{hint}"
        )

    protocol_name = {
        "vless": "VLESS",
        "vmess": "VMess",
        "trojan": "Trojan",
        "hysteria2": "Hysteria2",
        "shadowsocks": "Shadowsocks",
    }[protocol]

    transport = "TCP"
    if protocol in {"vless", "vmess", "trojan"}:
        transport = _canonical_transport(parsed.get("network") or "tcp")
    elif protocol == "shadowsocks":
        transport = "TCP"

    compatibility = {}
    try:
        compatibility = await get_singbox_compatibility(server)
    except Exception:
        # Creation still goes through X-NET's own validation; compatibility is
        # an early guard, not a reason to make older builds unusable.
        compatibility = {}
    entry = _compat_protocol_entry(compatibility, protocol)

    allowed_security = {
        str(x).strip().lower()
        for x in (entry.get("security") or [])
        if str(x).strip()
    }
    if allowed_security and security not in allowed_security:
        raise XnetApiError(
            f"X-NET برای {protocol_name} امنیت «{security}» را پشتیبانی نمی‌کند."
        )

    if protocol in {"vless", "vmess", "trojan"}:
        allowed_transport = {
            str(x).strip().lower()
            for x in (entry.get("transports") or [])
            if str(x).strip()
        }
        transport_key = _transport_compat_key(transport)
        if allowed_transport and transport_key not in allowed_transport:
            raise XnetApiError(
                f"X-NET برای {protocol_name} ترنسپورت «{transport}» را پشتیبانی نمی‌کند."
            )

    final_remark = str(remark or "").strip() or _link_remark(
        link, protocol_name, port
    )
    body: Dict[str, Any] = {
        "remark": final_remark,
        "protocol": protocol_name,
        "port": port,
        "listeningIp": "0.0.0.0",
        "transport": transport,
        "security": "TLS" if security == "tls" else "none",
        "enabled": True,
        "sniffingEnabled": True,
        "sniffDestOverride": ["http", "tls"],
        "clients": [],
    }

    if protocol in {"vless", "vmess", "trojan"}:
        raw_qs = parsed.get("raw_qs")
        raw_qs = raw_qs if isinstance(raw_qs, dict) else {}
        path = str(parsed.get("path") or "").strip()
        if transport == "WS":
            body["wsPath"] = path or "/ws"
        elif transport == "gRPC":
            service_name = str(
                raw_qs.get("serviceName")
                or raw_qs.get("service_name")
                or path.strip("/")
                or "grpc-service"
            ).strip()
            body["grpcServiceName"] = service_name
        elif transport == "HTTPUpgrade":
            body["httpUpgradePath"] = path or "/"

        if security == "tls":
            tls = await _select_local_tls_material(
                server,
                inbounds,
                preferred_sni=str(parsed.get("sni") or ""),
            )
            body.update(
                {
                    "sni": tls["sni"],
                    "domainBinding": tls["sni"],
                    "certFile": tls["certFile"],
                    "keyFile": tls["keyFile"],
                    "fingerprint": str(parsed.get("fp") or "chrome").strip() or "chrome",
                    "alpn": _split_alpn(
                        parsed.get("alpn"),
                        default=["http/1.1"] if transport == "HTTPUpgrade" else ["h2", "http/1.1"],
                    ),
                }
            )

    elif protocol == "hysteria2":
        tls = await _select_local_tls_material(
            server,
            inbounds,
            preferred_sni=str(parsed.get("sni") or ""),
        )
        body.update(
            {
                "transport": "TCP",
                "security": "TLS",
                "sni": tls["sni"],
                "domainBinding": tls["sni"],
                "certFile": tls["certFile"],
                "keyFile": tls["keyFile"],
                "alpn": _split_alpn(parsed.get("alpn"), default=["h3"]),
                "hyUnlimited": True,
            }
        )
        raw_qs = parsed.get("raw_qs")
        raw_qs = raw_qs if isinstance(raw_qs, dict) else {}
        obfs_password = str(parsed.get("obfs_password") or "").strip()
        obfs_type = str(parsed.get("obfs") or raw_qs.get("obfs") or "").strip()
        if obfs_password:
            body["hy2ObfsType"] = obfs_type or "salamander"
            body["hy2ObfsPassword"] = obfs_password

    elif protocol == "shadowsocks":
        body.update(
            {
                "transport": "TCP",
                "security": "none",
                "ssNetwork": "tcp,udp",
            }
        )

    created = await _request_json("POST", "/api/inbounds", server, json=body)
    if not isinstance(created, dict):
        created = {}

    inbound_id = str(created.get("id") or "").strip()
    if inbound_id:
        result = dict(created)
        result.setdefault("port", port)
        result.setdefault("protocol", protocol_name)
        result.setdefault("remark", final_remark)
        return result

    # Some builds return a generic success object. Verify by re-reading the
    # inbounds so the bot never reports success for a missing inbound.
    refreshed = await get_inbounds(server)
    for row in refreshed:
        if (
            _to_int(row.get("port"), 0) == port
            and str(row.get("protocol") or "").strip().lower() == protocol
        ):
            return dict(row)

    raise XnetApiError(
        "X-NET پاسخ موفق برگرداند اما اینباند ساخته‌شده در لیست پیدا نشد."
    )


async def health_probe(server: Dict[str, Any]) -> Dict[str, Any]:
    """Lightweight live reachability probe used only by outage monitoring.

    A server-down alert must represent panel reachability, not slow statistics,
    a large subscriber list, or an admin-authentication problem.  The public
    X-NET ping endpoint is sufficient for that distinction.
    """
    status = await ping(server)
    if str(status.get("status") or "").strip().lower() != "ok":
        raise XnetApiError("X-NET ping پاسخ ok نداد.")
    return status


async def test_connect(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    status = await ping(server)
    if str(status.get("status") or "").strip().lower() != "ok":
        raise XnetApiError("X-NET ping پاسخ ok نداد.")
    # This verifies the management credential. A persistent X-NET API token
    # is preferred; legacy admin login is used only when no token exists.
    await get_inbounds(server)
    return await list_users(server)
