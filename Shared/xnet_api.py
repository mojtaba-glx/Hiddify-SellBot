"""Async adapter for the X-NET panel API.

This module intentionally contains only endpoints verified against X-NET's
official health-check/docs. Write operations (create/update/delete/reset) are
added only after their schemas are verified from the panel API playground.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx


class XnetApiError(RuntimeError):
    """Raised when an X-NET API request fails."""


_TOKEN_CACHE: Dict[Tuple[str, str], Tuple[float, str]] = {}
_TOKEN_LOCK = asyncio.Lock()
_DEFAULT_TOKEN_TTL = 15 * 60


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


def _static_token(server: Dict[str, Any]) -> str:
    for key in ("xnet_api_token", "api_token", "token"):
        value = str((server or {}).get(key) or "").strip()
        if value:
            return value
    return ""


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


def _cache_key(server: Dict[str, Any]) -> Tuple[str, str]:
    return (_base_url(server), _username(server))


async def _login(server: Dict[str, Any], *, force: bool = False) -> str:
    key = _cache_key(server)
    now = time.monotonic()

    if not force:
        cached = _TOKEN_CACHE.get(key)
        if cached and cached[0] > now and cached[1]:
            return cached[1]

    password = _password(server)
    if not password:
        raise XnetApiError(
            "برای ورود X-NET باید xnet_password/admin_password تنظیم شود "
            "یا یک xnet_api_token معتبر ثبت شود."
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
                "ورود X-NET نیاز به 2FA دارد؛ برای ربات یک API Token معتبر بسازید."
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
    base = _base_url(server)
    url = f"{base}/{path.lstrip('/')}"

    async def _send(token: str = "") -> httpx.Response:
        headers = {"Accept": "application/json"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await client.request(
                method.upper(), url, headers=headers, json=json, params=params
            )

    token = ""
    used_static = False
    if auth:
        token = _static_token(server)
        used_static = bool(token)
        if not token:
            token = await _login(server)

    try:
        response = await _send(token)
    except httpx.RequestError as exc:
        raise XnetApiError(f"خطا در اتصال به X-NET: {exc}") from exc

    # JWT may expire. Refresh once when username/password credentials exist.
    if auth and response.status_code in {401, 403} and _password(server):
        if not used_static:
            _TOKEN_CACHE.pop(_cache_key(server), None)
        token = await _login(server, force=True)
        try:
            response = await _send(token)
        except httpx.RequestError as exc:
            raise XnetApiError(f"خطا در اتصال به X-NET: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text.strip().replace("\n", " ")[:240]
        raise XnetApiError(
            f"X-NET API HTTP {response.status_code}: {detail or 'request failed'}"
        )

    if not response.content:
        return {}

    try:
        return response.json()
    except ValueError as exc:
        raise XnetApiError("پاسخ X-NET JSON معتبر نیست.") from exc


async def _request_text(path: str, server: Dict[str, Any], *, params=None) -> str:
    url = f"{_base_url(server)}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
    except httpx.RequestError as exc:
        raise XnetApiError(f"خطا در دریافت اشتراک X-NET: {exc}") from exc
    if response.status_code >= 400:
        raise XnetApiError(f"X-NET subscription HTTP {response.status_code}")
    return response.text


def _subscriber_array(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [dict(x) for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("subscribers", "users", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [dict(x) for x in value if isinstance(x, dict)]
    raise XnetApiError("فرمت پاسخ لیست Subscribers در X-NET شناخته نشد.")


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


async def list_users(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = await _request_json("GET", "/api/v1/subscribers", server)
    return _subscriber_array(data)


async def get_user_by_uuid(
    server: Dict[str, Any], user_uuid: str
) -> Dict[str, Any]:
    wanted = str(user_uuid or "").strip()
    if not wanted:
        raise XnetApiError("UUID کاربر X-NET خالی است.")

    for user in await list_users(server):
        candidate = str(
            user.get("uuid")
            or user.get("id")
            or user.get("subscription_uuid")
            or ""
        ).strip()
        if candidate == wanted:
            return user
    raise XnetApiError("X-NET subscriber not found")


async def get_traffic_summary(server: Dict[str, Any]) -> Dict[str, Any]:
    data = await _request_json("GET", "/api/traffic/singbox/summary", server)
    if not isinstance(data, dict):
        raise XnetApiError("فرمت traffic summary در X-NET معتبر نیست.")
    return data


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
    return await _request_text(f"/api/v1/sub/{str(user_uuid).strip()}", server, params=params)


def _decode_base64_subscription(body: str) -> str:
    raw = str(body or "").strip()
    if not raw:
        return ""
    padded = raw + ("=" * ((4 - len(raw) % 4) % 4))
    try:
        return base64.b64decode(padded, validate=False).decode("utf-8", errors="replace")
    except Exception:
        return raw


async def get_user_configs(
    server: Dict[str, Any], user_uuid: str
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
    return await list_users(server)
