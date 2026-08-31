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
        # Fallback: standard panel_url field (used by the AdminBot add-server wizard)
        url = str(server.get("panel_url") or "").strip().rstrip("/")
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
        # Hiddify-style payloads pass "name" — sanitize it into a valid username
        import re as _re
        raw_name = str(payload.get("name") or "").strip()
        username = _re.sub(r'[^a-zA-Z0-9_\-@.]', '_', raw_name).strip("_")[:32]
    if not username:
        # Non-Latin names (e.g. Persian) sanitize to empty — generate a safe fallback
        import uuid as _uuid
        username = f"user_{_uuid.uuid4().hex[:6]}"
        logger.info("Marzban create_user: name sanitized to empty, generated username=%s", username)

    # Build Marzban payload
    gb_limit = payload.get("data_limit_GB")
    if gb_limit is None:
        # Hiddify-style key
        gb_limit = payload.get("usage_limit_GB")
    data_limit_bytes = int(float(gb_limit or 0) * 1024 * 1024 * 1024)
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

    if payload.get("note") or payload.get("comment"):
        marzban_payload["note"] = str(payload.get("note") or payload.get("comment"))

    if payload.get("status"):
        marzban_payload["status"] = str(payload["status"])

    result = await _request("POST", url, server, json=marzban_payload)
    if not isinstance(result, dict):
        raise MarzbanApiError("Marzban create_user returned non-dict response.")
    logger.info("Marzban user created: username=%s", username)
    return normalize_user(result, server=server)


async def get_user(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    """
    GET /api/user/{username}
    Returns a hiddify-like normalized dict (see normalize_user) with the raw
    response attached under ``_marzban_raw``.
    """
    base = _get_panel_url(server)
    url = f"{base}/api/user/{username}"
    result = await _request("GET", url, server)
    if not isinstance(result, dict):
        raise MarzbanApiError("Marzban get_user returned non-dict response.")
    return normalize_user(result, server=server)


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
    # NOTE: "usage_limit_GB" is the key every bot module (AdminBot/UserBot/
    # AgentBot/CustomerBot) sends when editing volume — it MUST be mapped.
    if "usage_limit_GB" in payload:
        try:
            marzban_payload["data_limit"] = int(float(payload["usage_limit_GB"]) * 1024 * 1024 * 1024)
        except (TypeError, ValueError):
            marzban_payload["data_limit"] = 0
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
    if "comment" in payload:
        marzban_payload["note"] = str(payload["comment"])
    # Hiddify-style rename: Marzban has no "name" field; map it to note
    # (renaming the username itself is unsafe — it would break sub links)
    if "name" in payload and "note" not in marzban_payload:
        new_name = str(payload["name"]).strip()
        if new_name:
            marzban_payload["note"] = new_name
    if "username" in payload:
        marzban_payload["username"] = str(payload["username"])

    # Pass through any raw marzban keys
    for key in ("data_limit_reset_strategy", "inbounds", "on_hold_expire_duration",
                 "next_plan", "auto_delete_in_days"):
        if key in payload:
            marzban_payload[key] = payload[key]

    if not marzban_payload:
        # Payload only contained unmappable keys (e.g. current_usage_GB,
        # last_reset_time) — do NOT raise; just return the current user.
        logger.debug("Marzban update_user called with no mappable fields for %s", username)
        return await get_user(server, username)

    result = await _request("PUT", url, server, json=marzban_payload)
    if not isinstance(result, dict):
        raise MarzbanApiError("Marzban update_user returned non-dict response.")
    return normalize_user(result, server=server)


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
    Returns hiddify-like normalized user dicts (see normalize_user).
    """
    base = _get_panel_url(server)
    url = f"{base}/api/users"
    result = await _request("GET", url, server)
    raw_users: List[Dict[str, Any]] = []
    if isinstance(result, dict) and "users" in result:
        raw_users = list(result["users"])
    elif isinstance(result, list):
        raw_users = result
    else:
        raise MarzbanApiError("Unexpected response from list_users.")
    return [normalize_user(u, server=server) for u in raw_users if isinstance(u, dict)]


async def get_user_configs(server: Dict[str, Any], username: str) -> List[str]:
    """
    Return the list of proxy links (vless://, vmess://, etc.) for a user.
    Marzban returns these as `links` in the user response.
    """
    user = await get_user(server, username)
    return list(user.get("links") or [])


async def get_subscription_url(server: Dict[str, Any], username: str) -> str:
    """
    Get the subscription URL for a Marzban user (absolute URL).
    Marzban returns a relative path like /sub/{token}; we join it with the
    panel base URL so the bot can hand it to users directly.
    """
    user = await get_user(server, username)
    sub = str(user.get("subscription_url") or "").strip()
    if not sub:
        return ""
    if sub.startswith(("http://", "https://")):
        return sub
    base = _get_panel_url(server)
    return f"{base}{sub if sub.startswith('/') else '/' + sub}"


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
    if "comment" in hiddify_payload:
        result["note"] = str(hiddify_payload["comment"])

    return result


# ---------------------------------------------------------------------------
# Panel info / inbounds / system stats / backup
# ---------------------------------------------------------------------------
async def test_connect(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Verify credentials + connectivity by listing inbounds.
    Raises MarzbanApiError on failure.  Returns the inbound list.
    """
    try:
        inbounds = await get_inbounds(server)
        logger.info("Marzban test_connect OK: server_id=%s inbounds=%s", server.get("id"), len(inbounds))
        return inbounds
    except MarzbanApiError:
        raise
    except Exception as e:
        raise MarzbanApiError(f"Marzban test_connect failed: {e}") from e


async def get_inbounds(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    GET /api/inbounds  → grouped by node:  { "": [...], "node_name": [...] }

    Flattens into a list of dicts:
      {"id", "node", "tag", "protocol", "port" (int), "raw": {...}}
    """
    base = _get_panel_url(server)
    url = f"{base}/api/inbounds"
    data = await _request("GET", url, server)
    if not isinstance(data, dict):
        raise MarzbanApiError("Unexpected response from /api/inbounds.")

    out: List[Dict[str, Any]] = []
    for node_name, items in (data or {}).items():
        for inb in (items or []):
            if not isinstance(inb, dict):
                continue
            try:
                port = int(inb.get("port") or 0)
            except (TypeError, ValueError):
                port = 0
            out.append({
                "id": inb.get("id"),
                "node": str(node_name or "master"),
                "tag": str(inb.get("tag") or ""),
                "protocol": str(inb.get("protocol") or ""),
                "port": port,
                "raw": inb,
            })
    return out


async def get_system_info(server: Dict[str, Any]) -> Dict[str, Any]:
    """
    GET /api/system  → panel/system stats.

    Response fields (Marzban v0.8+):
      version, mem_total, mem_used, cpu_cores, cpu_usage, total_user,
      users_active, incoming_bandwidth, outgoing_bandwidth, incoming_bandwidth_speed,
      outgoing_bandwidth_speed, etc.
    """
    base = _get_panel_url(server)
    url = f"{base}/api/system"
    data = await _request("GET", url, server)
    if not isinstance(data, dict):
        raise MarzbanApiError("Unexpected response from /api/system.")
    return data


# ---------------------------------------------------------------------------
# Native nodes (marzban-node)
# ---------------------------------------------------------------------------
NODE_STATUS_EMOJI = {
    "connected": "🟢",
    "connecting": "🟡",
    "error": "🔴",
    "disabled": "⚪",
    "created": "🔵",
}


async def get_nodes(server: Dict[str, Any], *, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    GET /api/nodes  → marzban-node instances with live status.

    Node response fields (Marzban v0.8+):
      id, name, address, port, api_port, status (connected/connecting/error/
      disabled/created), message, xray_version, node_version, cores,
      mem_total, mem_used, uptime, usage, created_at
    """
    base = _get_panel_url(server)
    url = f"{base}/api/nodes"
    params = {"status": status} if status else None
    data = await _request("GET", url, server, params=params)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("nodes"), list):
        return list(data["nodes"])
    raise MarzbanApiError("Unexpected response from /api/nodes.")


async def get_node(server: Dict[str, Any], node_id: Any) -> Dict[str, Any]:
    """GET /api/node/{node_id} → single node with live status."""
    base = _get_panel_url(server)
    url = f"{base}/api/node/{node_id}"
    data = await _request("GET", url, server)
    if not isinstance(data, dict):
        raise MarzbanApiError("Unexpected response from /api/node.")
    return data


def format_node_line(node: Dict[str, Any]) -> str:
    """One-line human summary of a marzban node (used by AdminBot)."""
    status = str(node.get("status") or "created").strip().lower()
    emoji = NODE_STATUS_EMOJI.get(status, "🔵")
    name = str(node.get("name") or f"#{node.get('id') or '?'}")
    address = str(node.get("address") or "")
    port = node.get("port")
    head = f"{emoji} {name}" + (f" ({address}:{port})" if address else "")
    extras: List[str] = []
    if node.get("xray_version"):
        extras.append(f"xray {node['xray_version']}")
    if node.get("node_version"):
        extras.append(f"node {node['node_version']}")
    cores = _to_int(node.get("cores"), 0)
    if cores > 0:
        extras.append(f"{cores} core")
    mem_total = _to_float(node.get("mem_total"), 0.0)
    if mem_total > 0:
        mem_used = _to_float(node.get("mem_used"), 0.0)
        extras.append(f"RAM {mem_used / (1024 ** 3):.1f}/{mem_total / (1024 ** 3):.1f} GB")
    uptime = _to_int(node.get("uptime"), 0)
    if uptime > 0:
        d, rem = divmod(uptime, 86400)
        h, _ = divmod(rem, 3600)
        extras.append(f"up {d}d{h}h" if d else f"up {h}h")
    line = head
    if extras:
        line += "\n    └ " + " | ".join(extras)
    msg = str(node.get("message") or "").strip()
    if status == "error" and msg:
        line += f"\n    └ ⚠️ {msg[:120]}"
    return line


def _bytes_to_gb(v: Any) -> float:
    try:
        return float(v or 0) / (1024 ** 3)
    except Exception:
        return 0.0


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _to_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(float(v))
    except Exception:
        return default


async def get_server_stats(server: Dict[str, Any]) -> Dict[str, Any]:
    """
    System + user stats, same output shape as hiddify_api.get_server_stats /
    xui get_server_stats so callers can treat all panels uniformly.
    """
    users = []
    try:
        users = await list_users(server)
    except Exception:
        users = []

    online_now = 0
    active_today = 0
    active_month = 0
    total_usage_gb = 0.0
    now = datetime.now(timezone.utc)
    for u in users:
        total_usage_gb += _bytes_to_gb(u.get("used_traffic"))
        online = str(u.get("online_at") or "").strip()
        if online:
            dt = _parse_online_dt(online)
            if dt:
                diff = (now - dt).total_seconds()
                if diff < 300:
                    online_now += 1
                if diff < 86400:
                    active_today += 1
                if diff < 2592000:
                    active_month += 1

    out = {
        "cpu_percent": 0.0,
        "cpu_cores": 1,
        "ram_used": 0.0,
        "ram_total": 1.0,
        "disk_used": 0.0,
        "disk_total": 20.0,
        "users_total": len(users),
        "users_online": online_now,
        "users_today": active_today,
        "users_month": active_month,
        "usage_today_gb": 0.0,
        "usage_30days_gb": 0.0,
        "traffic_dl": 0.0,
        "traffic_ul": 0.0,
        "now_net_recv_mb": 0.0,
        "now_net_sent_mb": 0.0,
    }

    try:
        sysinfo = await get_system_info(server)
    except Exception:
        sysinfo = {}

    if sysinfo:
        out["cpu_percent"] = _to_float(sysinfo.get("cpu_usage"), out["cpu_percent"])
        out["cpu_cores"] = max(_to_int(sysinfo.get("cpu_cores"), 1), 1)
        out["ram_used"] = _to_float(sysinfo.get("mem_used"), 0.0) / (1024 ** 2)  # → MB
        out["ram_total"] = _to_float(sysinfo.get("mem_total"), 1.0) / (1024 ** 2)
        out["disk_total"] = 20.0  # Marzban /api/system does not expose disk
        out["users_total"] = _to_int(sysinfo.get("total_user"), out["users_total"])
        out["users_month"] = _to_int(sysinfo.get("users_active"), out["users_month"])
        out["traffic_dl"] = _bytes_to_gb(sysinfo.get("incoming_bandwidth"))
        out["traffic_ul"] = _bytes_to_gb(sysinfo.get("outgoing_bandwidth"))
        # live speeds in bytes/s → MB
        out["now_net_recv_mb"] = _to_float(sysinfo.get("incoming_bandwidth_speed"), 0.0) / (1024 ** 2)
        out["now_net_sent_mb"] = _to_float(sysinfo.get("outgoing_bandwidth_speed"), 0.0) / (1024 ** 2)

        # users_active on Marzban means currently-active accounts, use for online fallback
        if out["users_online"] == 0:
            out["users_online"] = _to_int(sysinfo.get("users_active"), 0)

    return out


def _parse_online_dt(raw: str) -> Optional[datetime]:
    """Parse Marzban online_at / created_at ISO strings (UTC)."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def normalize_user(
    raw: Dict[str, Any],
    fallback_name: str = "",
    server: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Convert a raw Marzban user response into the hiddify-like shape the bot
    expects everywhere (AdminBot/UserBot/AgentBot/CustomerBot).

    Guarantees:
      - ``uuid`` / ``username`` == Marzban username (bot stores it as uuid)
      - ``expire`` is a parseable ``YYYY-MM-DD`` string (NOT a raw timestamp)
        so AdminBot._parse_dt / _compute_package_info work safely
      - ``days_left`` is pre-computed
      - ``subscription_url`` is absolute (joined with panel base URL)
    """
    raw = raw or {}
    username = str(raw.get("username") or "").strip()

    data_limit_gb = 0.0
    try:
        data_limit_gb = round(float(raw.get("data_limit") or 0) / (1024 ** 3), 3)
    except Exception:
        pass
    used_gb = 0.0
    try:
        used_gb = round(float(raw.get("used_traffic") or 0) / (1024 ** 3), 3)
    except Exception:
        pass

    expire = raw.get("expire")
    expire_date = ""
    days_left: Optional[int] = None
    if isinstance(expire, (int, float)) and expire > 0:
        try:
            expire_dt = datetime.fromtimestamp(int(expire), tz=timezone.utc)
            expire_date = expire_dt.strftime("%Y-%m-%d")
            days_left = (expire_dt.date() - datetime.now(timezone.utc).date()).days
        except Exception:
            expire_date = ""
    elif isinstance(expire, str) and expire.strip():
        # Some Marzban forks already return an ISO string
        expire_date = str(expire).strip()[:10]

    status = str(raw.get("status") or "").strip()

    # online_at (ISO, UTC) → naive "%Y-%m-%d %H:%M:%S" so _parse_dt works
    online_dt = _parse_online_dt(raw.get("online_at"))
    last_online = online_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if online_dt else ""

    # Absolute subscription URL
    subscription = str(raw.get("subscription_url") or "").strip()
    if subscription and not subscription.startswith(("http://", "https://")):
        base = ""
        if server:
            try:
                base = _get_panel_url(server)
            except Exception:
                base = ""
        if base:
            subscription = f"{base}{subscription if subscription.startswith('/') else '/' + subscription}"

    return {
        "uuid": username,
        "username": username,
        "id": username,
        "name": str(raw.get("note") or fallback_name or username),
        "comment": str(raw.get("note") or ""),
        "usage_limit_GB": data_limit_gb,
        "current_usage_GB": used_gb,
        "data_limit": raw.get("data_limit"),
        "used_traffic": raw.get("used_traffic"),
        "expire": expire_date,
        "expire_date": expire_date,
        "days_left": days_left,
        "status": status,
        "is_active": status == "active",
        "online_at": str(raw.get("online_at") or ""),
        "last_online": last_online,
        "subscription_url": subscription,
        "links": list(raw.get("links") or []),
        "inbounds": list(raw.get("inbounds") or []),
        "data_limit_reset_strategy": str(raw.get("data_limit_reset_strategy") or ""),
        "_source": "marzban",
        "_marzban_raw": raw,
    }


def _backup_filename(server: Dict[str, Any]) -> str:
    title = str((server or {}).get("title") or "marzban").strip().replace(" ", "_")
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"{title}_{stamp}.json"


async def download_server_backup(server: Dict[str, Any]) -> Dict[str, Any]:
    """
    Download a full database backup from the panel.

    Marzban: GET /api/system/backup  (admin JWT required, returns the SQLite dump).
    Response mirrors hiddify_api.download_server_backup:
      {"filename": "...", "content": b"...", "source_url": "..."}
    """
    base = _get_panel_url(server)
    url = f"{base}/api/system/backup"

    token = await _get_token(server)
    headers = {
        "Accept": "application/octet-stream",
        "Authorization": f"Bearer {token}",
    }
    timeout = _get_timeout()
    mode = _get_ssl_mode()

    async def _send(verify: Any) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout, verify=verify) as client:
            return await client.get(url, headers=headers)

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
        raise MarzbanApiError(f"Connection error downloading Marzban backup: {e}") from e

    if resp.status_code >= 400:
        raise MarzbanApiError(f"Marzban backup failed (HTTP {resp.status_code}): {resp.text[:200]}")

    body = resp.content or b""
    if not body:
        raise MarzbanApiError("Marzban backup returned an empty body.")

    return {
        "filename": _backup_filename(server),
        "content": body,
        "source_url": url,
    }
