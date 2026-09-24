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


def _public_origin(server: Dict[str, Any]) -> str:
    """Public origin used for X-NET subscription URLs."""
    custom = str(
        (server or {}).get("xnet_sub_domain")
        or (server or {}).get("xnet_sub_host")
        or ""
    ).strip()
    if custom:
        if "://" not in custom:
            custom = "https://" + custom
        return custom.rstrip("/")
    return _base_url(server)


def get_subscription_url(server: Dict[str, Any], user_uuid: str) -> str:
    wanted = str(user_uuid or "").strip()
    if not wanted:
        raise XnetApiError("UUID کاربر X-NET خالی است.")
    return f"{_public_origin(server)}/api/v1/sub/{wanted}"


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


async def test_connect(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    status = await ping(server)
    if str(status.get("status") or "").strip().lower() != "ok":
        raise XnetApiError("X-NET ping پاسخ ok نداد.")
    # This call intentionally requires a management JWT and therefore also
    # verifies the stored admin credentials.
    await get_inbounds(server)
    return await list_users(server)
