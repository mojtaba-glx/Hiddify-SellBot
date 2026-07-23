"""
Shared/marzban_api.py
=====================
Marzban panel REST API client.

Mirrors the same async pattern as hiddify_api.py so callers can use
either module interchangeably.  Authentication uses Marzban's JWT flow
(POST /api/admin/token → Bearer token).

Environment variables:
  MARZBAN_API_TIMEOUT_SECONDS   (default 8)
  MARZBAN_SSL_MODE              (default auto; same semantics as HIDDIFY_SSL_MODE)
"""

import asyncio
import logging
import os
import ssl
import time
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment knobs
# ---------------------------------------------------------------------------
SSL_MODE_ENV = "MARZBAN_SSL_MODE"
SSL_MODE_AUTO = "auto"
SSL_MODE_SECURE = "secure"
SSL_MODE_INSECURE = "insecure"
API_TIMEOUT_ENV = "MARZBAN_API_TIMEOUT_SECONDS"

# In-memory token cache: key = (server_id, admin_username) → {"token": str, "expires": float}
_token_cache: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class MarzbanApiError(Exception):
    """Generic Marzban API error."""
    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _get_panel_url(server: Dict[str, Any]) -> str:
    """Return the Marzban panel base URL (no trailing slash)."""
    url = ""
    mc = server.get("marzban_config") if isinstance(server.get("marzban_config"), dict) else {}
    if mc:
        url = str(mc.get("panel_url") or "").strip().rstrip("/")
    if not url:
        url = str(server.get("marzban_panel_url") or "").strip().rstrip("/")
    if not url:
        raise MarzbanApiError("Marzban panel_url is not configured for this server.")
    return url


def _get_admin_credentials(server: Dict[str, Any]) -> tuple[str, str]:
    """Return (username, password) for Marzban admin."""
    mc = server.get("marzban_config") if isinstance(server.get("marzban_config"), dict) else {}
    username = ""
    password = ""
    if mc:
        username = str(mc.get("admin_username") or "").strip()
        password = str(mc.get("admin_password") or "").strip()
    if not username:
        username = str(server.get("marzban_admin_username") or "").strip()
    if not password:
        password = str(server.get("marzban_admin_password") or "").strip()
    if not username or not password:
        raise MarzbanApiError("Marzban admin credentials (username/password) are not configured.")
    return username, password


def _get_ssl_mode() -> str:
    raw = (os.getenv(SSL_MODE_ENV, SSL_MODE_AUTO) or "").strip().lower()
    aliases = {
        "on": SSL_MODE_SECURE, "true": SSL_MODE_SECURE, "1": SSL_MODE_SECURE,
        "off": SSL_MODE_INSECURE, "false": SSL_MODE_INSECURE, "0": SSL_MODE_INSECURE,
    }
    mode = aliases.get(raw, raw)
    if mode not in {SSL_MODE_AUTO, SSL_MODE_SECURE, SSL_MODE_INSECURE}:
        return SSL_MODE_AUTO
    return mode


def _build_insecure_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _looks_like_tls_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(t in text for t in ("certificate", "self signed", "tls", "ssl", "hostname", "wrong version number", "cert"))


def _get_timeout() -> float:
    raw = str(os.getenv(API_TIMEOUT_ENV, "8") or "8").strip()
    try:
        val = float(raw)
    except Exception:
        return 8.0
    return min(max(val, 2.0), 60.0)


def _server_cache_key(server: Dict[str, Any]) -> str:
    sid = str(server.get("id") or "x")
    username, _ = _get_admin_credentials(server)
    return f"{sid}:{username}"


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------
async def _fetch_token(server: Dict[str, Any]) -> str:
    """POST /api/admin/token → obtain a fresh JWT."""
    base = _get_panel_url(server)
    username, password = _get_admin_credentials(server)
    url = f"{base}/api/admin/token"
    timeout = _get_timeout()
    mode = _get_ssl_mode()

    async def _post(verify: Any) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout, verify=verify) as client:
            return await client.post(url, data={"username": username, "password": password})

    try:
        if mode == SSL_MODE_SECURE:
            resp = await _post(True)
        elif mode == SSL_MODE_INSECURE:
            resp = await _post(_build_insecure_ssl_context())
        else:
            try:
                resp = await _post(True)
            except httpx.RequestError as e:
                if not _looks_like_tls_error(e):
                    raise
                logger.warning("Marzban TLS failed for %s; retrying insecure", url)
                resp = await _post(_build_insecure_ssl_context())
    except httpx.RequestError as e:
        raise MarzbanApiError(f"Connection error fetching Marzban token: {e}") from e

    if resp.status_code >= 400:
        raise MarzbanApiError(f"Marzban auth failed (HTTP {resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    token = data.get("access_token") or ""
    if not token:
        raise MarzbanApiError("Marzban token response missing access_token.")
    return token


async def _get_token(server: Dict[str, Any]) -> str:
    """Return a cached token, refreshing if expired."""
    key = _server_cache_key(server)
    cached = _token_cache.get(key)
    if cached and cached.get("expires", 0) > time.time() and cached.get("token"):
        return str(cached["token"])

    token = await _fetch_token(server)
    # Cache for 55 minutes (Marzban default JWT expiry is 60 min)
    _token_cache[key] = {"token": token, "expires": time.time() + 55 * 60}
    return token


# ---------------------------------------------------------------------------
# Core HTTP request
# ---------------------------------------------------------------------------
async def _request(
    method: str,
    url: str,
    server: Dict[str, Any],
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Any] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Any:
    """Authenticated request to Marzban API."""
    token = await _get_token(server)
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    timeout = _get_timeout()
    mode = _get_ssl_mode()

    async def _send(verify: Any) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout, verify=verify) as client:
            return await client.request(method, url, headers=headers, params=params, json=json, data=data)

    try:
        if mode == SSL_MODE_SECURE:
            resp = await _send(True)
        elif mode == SSL_MODE_INSECURE:
            resp = await _send(_build_insecure_ssl_context())
        else:
            try:
                resp = await _send(True)
            except httpx.RequestError as e:
                if not _looks_like_tls_error(e):
                    raise
                resp = await _send(_build_insecure_ssl_context())
    except httpx.RequestError as e:
        raise MarzbanApiError(f"Connection error to Marzban API: {e}") from e

    # If 401, retry once with a fresh token
    if resp.status_code == 401:
        key = _server_cache_key(server)
        _token_cache.pop(key, None)
        token = await _get_token(server)
        headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=timeout, verify=(mode == SSL_MODE_SECURE) or _build_insecure_ssl_context() if mode == SSL_MODE_INSECURE else True) as client:
                resp = await client.request(method, url, headers=headers, params=params, json=json, data=data)
        except httpx.RequestError as e:
            raise MarzbanApiError(f"Connection error to Marzban API (retry): {e}") from e

    if resp.status_code >= 400:
        raise MarzbanApiError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    ct = resp.headers.get("content-type", "")
    if "application/json" in ct.lower():
        try:
            return resp.json()
        except ValueError as e:
            raise MarzbanApiError("Invalid JSON response from Marzban.") from e
    return resp.text


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

async def create_user(
    server: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    POST /api/user

    Payload keys (standardized):
      - username:       str (required, 3-32 chars)
      - data_limit_GB:  float (traffic limit in GB; converted to bytes)
      - expire_days:    int (days from now; converted to UTC timestamp)
      - proxies:        dict (optional; e.g. {"vmess":{}, "vless":{}})
      - status:         str (optional; default "active")
      - note:           str (optional)

    Returns the full Marzban user response dict (includes subscription_url, etc.)
    """
    base = _get_panel_url(server)
    url = f"{base}/api/user"

    username = str(payload.get("username") or "").strip()
    if not username:
        raise MarzbanApiError("username is required for Marzban create_user.")

    # Build Marzban payload
    data_limit_bytes = int(float(payload.get("data_limit_GB") or 0) * 1024 * 1024 * 1024)
    expire_ts: Optional[int] = None
    expire_days = payload.get("expire_days") or payload.get("package_days")
    if expire_days:
        try:
            expire_dt = datetime.now(timezone.utc) + timedelta(days=int(expire_days))
            expire_ts = int(expire_dt.timestamp())
        except Exception:
            pass
    # Also accept raw expire timestamp
    if expire_ts is None and payload.get("expire"):
        try:
            expire_ts = int(payload["expire"])
        except Exception:
            pass

    # Start date handling
    start_date = payload.get("start_date")
    on_hold_expire_duration: Optional[int] = None
    if start_date and not expire_ts:
        try:
            start_dt = datetime.strptime(str(start_date)[:10], "%Y-%m-%d")
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            if start_dt > now_utc:
                # Future start → on_hold
                delta = (start_dt - now_utc).total_seconds()
                on_hold_expire_duration = max(int(delta), 0)
        except Exception:
            pass

    marzban_payload: Dict[str, Any] = {
        "username": username,
        "data_limit": data_limit_bytes,
        "status": "active",
    }

    if expire_ts is not None:
        marzban_payload["expire"] = expire_ts
    elif on_hold_expire_duration is not None:
        marzban_payload["status"] = "on_hold"
        marzban_payload["on_hold_expire_duration"] = on_hold_expire_duration

    # Proxies — default to common protocols if not specified
    proxies = payload.get("proxies")
    if isinstance(proxies, dict) and proxies:
        marzban_payload["proxies"] = proxies
    else:
        marzban_payload["proxies"] = {
            "vmess": {},
            "vless": {},
            "trojan": {},
            "shadowsocks": {},
        }

    if payload.get("note"):
        marzban_payload["note"] = str(payload["note"])

    if payload.get("status"):
        marzban_payload["status"] = str(payload["status"])

    result = await _request("POST", url, server, json=marzban_payload)
    if not isinstance(result, dict):
        raise MarzbanApiError("Marzban create_user returned non-dict response.")
    logger.info("Marzban user created: username=%s", username)
    return result


async def get_user(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    """
    GET /api/user/{username}
    """
    base = _get_panel_url(server)
    url = f"{base}/api/user/{username}"
    result = await _request("GET", url, server)
    if not isinstance(result, dict):
        raise MarzbanApiError("Marzban get_user returned non-dict response.")
    return result


async def update_user(
    server: Dict[str, Any],
    username: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    PUT /api/user/{username}

    Accepts the same standardized keys as create_user plus any raw Marzban fields.
    """
    base = _get_panel_url(server)
    url = f"{base}/api/user/{username}"

    marzban_payload: Dict[str, Any] = {}

    # Standardized conversions
    if "data_limit_GB" in payload:
        marzban_payload["data_limit"] = int(float(payload["data_limit_GB"]) * 1024 * 1024 * 1024)
    if "data_limit" in payload:
        marzban_payload["data_limit"] = int(payload["data_limit"])
    if "expire_days" in payload or "package_days" in payload:
        days = payload.get("expire_days") or payload.get("package_days")
        if days:
            try:
                expire_dt = datetime.now(timezone.utc) + timedelta(days=int(days))
                marzban_payload["expire"] = int(expire_dt.timestamp())
            except Exception:
                pass
    if "expire" in payload:
        marzban_payload["expire"] = int(payload["expire"])
    if "expire_date" in payload:
        # Convert date string to timestamp
        try:
            dt = datetime.strptime(str(payload["expire_date"])[:10], "%Y-%m-%d")
            marzban_payload["expire"] = int(dt.replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            pass
    if "status" in payload:
        marzban_payload["status"] = str(payload["status"])
    if "is_active" in payload:
        marzban_payload["status"] = "active" if payload["is_active"] else "disabled"
    if "proxies" in payload:
        marzban_payload["proxies"] = payload["proxies"]
    if "note" in payload:
        marzban_payload["note"] = str(payload["note"])
    if "username" in payload:
        marzban_payload["username"] = str(payload["username"])

    # Pass through any raw marzban keys
    for key in ("data_limit_reset_strategy", "inbounds", "on_hold_expire_duration",
                 "next_plan", "auto_delete_in_days"):
        if key in payload:
            marzban_payload[key] = payload[key]

    if not marzban_payload:
        raise MarzbanApiError("No fields to update.")

    result = await _request("PUT", url, server, json=marzban_payload)
    if not isinstance(result, dict):
        raise MarzbanApiError("Marzban update_user returned non-dict response.")
    return result


async def delete_user(server: Dict[str, Any], username: str) -> None:
    """
    DELETE /api/user/{username}
    """
    base = _get_panel_url(server)
    url = f"{base}/api/user/{username}"
    await _request("DELETE", url, server)


async def disable_user(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    """Set user status to disabled."""
    return await update_user(server, username, {"status": "disabled"})


async def enable_user(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    """Set user status to active."""
    return await update_user(server, username, {"status": "active"})


async def list_users(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    GET /api/users
    """
    base = _get_panel_url(server)
    url = f"{base}/api/users"
    result = await _request("GET", url, server)
    if isinstance(result, dict) and "users" in result:
        return list(result["users"])
    if isinstance(result, list):
        return result
    raise MarzbanApiError("Unexpected response from list_users.")


async def get_user_configs(server: Dict[str, Any], username: str) -> List[str]:
    """
    Return the list of proxy links (vless://, vmess://, etc.) for a user.
    Marzban returns these as `links` in the user response.
    """
    user = await get_user(server, username)
    return list(user.get("links") or [])


async def get_subscription_url(server: Dict[str, Any], username: str) -> str:
    """
    Get the subscription URL for a Marzban user.
    """
    user = await get_user(server, username)
    return str(user.get("subscription_url") or "")


async def get_user_usage(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    """
    GET /api/user/{username}/usage
    """
    base = _get_panel_url(server)
    url = f"{base}/api/user/{username}/usage"
    return await _request("GET", url, server)


async def reset_user_usage(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    """
    POST /api/user/{username}/reset
    """
    base = _get_panel_url(server)
    url = f"{base}/api/user/{username}/reset"
    result = await _request("POST", url, server)
    if not isinstance(result, dict):
        raise MarzbanApiError("reset_user_usage returned non-dict.")
    return result


async def revoke_user_subscription(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    """
    POST /api/user/{username}/revoke_sub
    """
    base = _get_panel_url(server)
    url = f"{base}/api/user/{username}/revoke_sub"
    result = await _request("POST", url, server)
    if not isinstance(result, dict):
        raise MarzbanApiError("revoke_user_subscription returned non-dict.")
    return result


async def active_next_plan(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    """
    POST /api/user/{username}/active-next
    """
    base = _get_panel_url(server)
    url = f"{base}/api/user/{username}/active-next"
    result = await _request("POST", url, server)
    if not isinstance(result, dict):
        raise MarzbanApiError("active_next_plan returned non-dict.")
    return result


# ---------------------------------------------------------------------------
# Utility: convert Hiddify-style payload → Marzban-style payload
# ---------------------------------------------------------------------------
def hiddify_payload_to_marzban(hiddify_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a Hiddify create_user payload dict into the standardized
    intermediate format that create_user / update_user accept.
    """
    result: Dict[str, Any] = {}

    name = hiddify_payload.get("name") or hiddify_payload.get("username") or ""
    # Marzban username: sanitize to [a-zA-Z0-9-_@.] max 32 chars
    import re
    clean = re.sub(r'[^a-zA-Z0-9_\-@.]', '_', str(name)).strip("_")[:32]
    result["username"] = clean or f"user_{int(time.time())}"

    if "usage_limit_GB" in hiddify_payload:
        result["data_limit_GB"] = float(hiddify_payload["usage_limit_GB"])
    if "package_days" in hiddify_payload:
        result["expire_days"] = int(hiddify_payload["package_days"])
    if "start_date" in hiddify_payload:
        result["start_date"] = str(hiddify_payload["start_date"])
    if "is_active" in hiddify_payload:
        result["is_active"] = bool(hiddify_payload["is_active"])

    return result
