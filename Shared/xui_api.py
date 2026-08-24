"""
Shared/xui_api.py
=================
Adapter for X-UI panels (alireza0/x-ui, and Sanai/Sanayi forks built on the
same core).  Provides the same public function signatures as
``Shared/hiddify_api.py`` so the rest of the bot can transparently manage
users on X-UI servers.

X-UI model mapping
------------------
A X-UI "server" holds one or more *inbounds*; each inbound holds *clients*.
The bot treats each X-UI panel as one standalone sell server.  Per-plan
fields are mapped directly:

  * traffic quota   -> ``client.totalGB`` (bytes; 0 = unlimited)
  * expiry          -> ``client.expiryTime`` (epoch **milliseconds**)
  * active flag     -> ``client.enable``
  * stable identity -> ``email`` (and ``subId``) set to the bot user uuid

Credentials may be stored on the server dict as ``xui_username`` /
``xui_password``; optionally ``xui_secret`` for the ``XUI-Xray-App-Secret-Key``
header (used by some forks).  ``xui_inbound_id`` optionally pins the inbound
used for selling.  ``xui_sub_domain`` / ``xui_sub_path`` control the native
subscription URL returned by ``get_user_configs``.
"""

from __future__ import annotations

import asyncio
import base64
import os
import threading
import time
import json
import logging
import random
import re
import uuid as _uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
from urllib.parse import quote, urlparse, parse_qsl, unquote

import httpx

from Shared import hiddify_api

logger = logging.getLogger(__name__)


GB = 1024 ** 3
_MS = 1000

# X-UI API base path (fixed by the panel router).
API_PREFIX = "/xui/API"

# These are the protocols we can sell on.
SUPPORTED_PROTOCOLS = ("vless", "trojan", "vmess", "shadowsocks", "hysteria", "hysteria2")

# Header used by some Sanai/Sanayi forks (XUI_SECRET) in addition to cookies.
_XUI_SECRET_HEADER = "XUI-Xray-App-Secret-Key"

_ONLINE_EMAIL_RE = re.compile(r"email\s*[:=]\s*['\"]?([^'\",}\]]+)", re.IGNORECASE)

# X-UI email cannot contain emojis (panel rejects). Strip them and keep Persian/ASCII.
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U00002500-\U00002BEF"
    "\U0001F004"
    "\U0001F0CF"
    "]+",
    flags=re.UNICODE,
)


def _sanitize_xui_email(raw: str, fallback: str) -> str:
    """Sanitize X-UI email while preserving emoji (panel now accepts it).

    Keeps emoji/Persian/Arabic/English/numbers and -_.@, falls back to uuid if empty.
    """
    text = str(raw or "").strip()
    if not text:
        return str(fallback or "").strip()
    # Keep emoji as-is (panel now accepts MrAlfa🖤); only remove control chars
    text = "".join(ch for ch in text if ch.isprintable() or ch in (" ", "-", "_", ".", "@") or ord(ch) > 127)
    text = text.strip()
    # Collapse multiple spaces
    text = re.sub(r"\s+", " ", text)
    if not text:
        return str(fallback or "").strip()
    # X-UI email must be reasonable length; truncate (keep emoji counted as 1)
    if len(text) > 64:
        text = text[:64].strip()
    return text


def _existing_xui_emails(inbounds: List[Dict[str, Any]]) -> set:
    """Collect all existing X-UI client emails (case-sensitive + lower for dupe check)."""
    out: set = set()
    for ib in inbounds or []:
        for cl in _settings_clients(ib.get("settings")):
            email = str(cl.get("email") or "").strip()
            if not email:
                continue
            out.add(email)
            out.add(email.lower())
    return out


def _bot_has_service_name(name: str) -> bool:
    """Check if any service in bot DB already uses this name (cross-server dupe)."""
    try:
        from Shared import userbot_db as _ub
        from Shared import agent_db as _ag
        conn = _ub._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM userbot_services WHERE lower(name)=lower(?) LIMIT 1", (name,))
        row = cur.fetchone()
        conn.close()
        if row:
            return True
        conn2 = _ag._get_conn()
        cur2 = conn2.cursor()
        cur2.execute("SELECT 1 FROM agent_services WHERE lower(name)=lower(?) LIMIT 1", (name,))
        row2 = cur2.fetchone()
        conn2.close()
        if row2:
            return True
    except Exception:
        pass
    return False


def _unique_xui_email(base: str, existing: set, fallback: str) -> str:
    """Ensure email is unique on X-UI panel and in bot DB; if duplicate, append random digits.

    Hiddify allows duplicate names, X-UI does not — duplicate causes panel error/crash.
    Also covers cross-server dupe (e.g. customer creates MrAlfa🖤 on server A then
    again on server B with same name) which panel check alone would miss.
    """
    if base not in existing and base.lower() not in existing and not _bot_has_service_name(base):
        return base
    # If base is fallback uuid it is already unique, but still handle fallback
    for _ in range(12):
        rnd = str(random.randint(100, 9999))
        max_len = 64 - len(rnd)
        b = base[:max_len] if len(base) > max_len else base
        cand = f"{b}{rnd}"
        if cand not in existing and cand.lower() not in existing and not _bot_has_service_name(cand):
            return cand
    # Last resort: uuid suffix
    suffix = str(fallback or uuid4())[:8]
    max_len = 64 - len(suffix) - 1
    b = base[:max_len] if len(base) > max_len else base
    cand = f"{b}-{suffix}"
    if cand not in existing and cand.lower() not in existing and not _bot_has_service_name(cand):
        return cand
    return f"{fallback}"


def uuid4() -> str:
    return str(_uuid_mod.uuid4())


class XuiApiError(Exception):
    """خطای عمومی برای تماس با پنل X-UI."""


# ---------------------------------------------------------------------------
# Server dict helpers
# ---------------------------------------------------------------------------
def is_xui_server(server: Dict[str, Any]) -> bool:
    """True if the server dict is marked as an X-UI panel."""
    return str((server or {}).get("panel_type") or "").strip().lower() in {"xui", "x-ui"}


def _require_field(server: Dict[str, Any], key: str, hint: str = "") -> str:
    value = str((server or {}).get(key) or "").strip()
    if not value:
        raise XuiApiError(f"فیلد «{key}» برای سرور X-UI تنظیم نشده است.{hint}")
    return value


def _get_panel_url(server: Dict[str, Any]) -> str:
    return _require_field(server, "panel_url", " آدرس کامل پنل را در بخش افزودن سرور وارد کنید.").rstrip("/")


def _get_credentials(server: Dict[str, Any]) -> Tuple[str, str]:
    username = _require_field(server, "xui_username")
    password = str((server or {}).get("xui_password") or "").strip()
    if not password:
        raise XuiApiError("فیلد «xui_password» برای سرور X-UI تنظیم نشده است.")
    return username, password


def _api_base(server: Dict[str, Any]) -> str:
    return f"{_get_panel_url(server)}{API_PREFIX}"


def _inbound_id(server: Dict[str, Any]) -> Optional[int]:
    """Legacy single-inbound helper (kept for backward compat)."""
    try:
        raw = str((server or {}).get("xui_inbound_id") or "").strip()
        if not raw or raw == "0" or "," in raw:
            return None
        return int(raw or 0) or None
    except (TypeError, ValueError):
        return None


def _target_inbound_ids(server: Dict[str, Any]) -> Optional[List[int]]:
    """Parse xui_inbound_id field.

    Returns:
      None  -> خالی/skip = اولین اینباند فعال (رفتار قدیم)
      []    -> "0" = همه اینباندهای فعال
      [1,2] -> لیست مشخص
    """
    raw = str((server or {}).get("xui_inbound_id") or "").strip()
    if not raw:
        return None
    # normalize Persian comma and spaces
    raw = raw.replace("،", ",")
    if raw.strip() == "0":
        return []
    # split by comma / space
    parts = [p.strip() for p in raw.replace(" ", ",").split(",") if p.strip()]
    if not parts:
        return None
    if parts == ["0"]:
        return []
    ids: List[int] = []
    for p in parts:
        try:
            n = int(p)
            if n > 0:
                ids.append(n)
        except (TypeError, ValueError):
            continue
    return ids if ids else None


def _resolve_target_inbounds(server: Dict[str, Any], inbounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resolve which inbounds to operate on for this server."""
    if not inbounds:
        return []
    wanted = _target_inbound_ids(server)
    if wanted is None:
        # قدیم: اولین کاندید
        ib = _select_sell_inbound(inbounds, server)
        return [ib] if ib else []
    if wanted == []:
        # 0 = همه کاندیدها
        return _candidate_inbounds(inbounds)
    # لیست مشخص
    by_id = {_to_int(ib.get("id"), 0): ib for ib in inbounds}
    out: List[Dict[str, Any]] = []
    for nid in wanted:
        ib = by_id.get(nid)
        if ib and bool(ib.get("enable", True)):
            out.append(ib)
        else:
            raise XuiApiError(f"اینباند با شناسه {nid} در پنل X-UI یافت نشد یا غیرفعال است.")
    return out


def _public_origin(server: Dict[str, Any]) -> str:
    """Best-effort public origin (scheme://host or scheme://host:port)
    used for building subscription / direct config links."""
    sub_domain = str((server or {}).get("xui_sub_domain") or server.get("xui_sub_host") or "").strip()
    if sub_domain:
        if "://" in sub_domain:
            return sub_domain.rstrip("/")
        return f"https://{sub_domain.rstrip('/')}"
    panel = _get_panel_url(server)
    try:
        parsed = urlparse(panel)
    except Exception:
        parsed = None
    if parsed and parsed.hostname:
        scheme = parsed.scheme or "http"
        host = parsed.hostname
        if parsed.port and not ((scheme == "https" and parsed.port == 443) or (scheme == "http" and parsed.port == 80)):
            return f"{scheme}://{host}:{parsed.port}"
        return f"{scheme}://{host}"
    return panel


def _sub_path(server: Dict[str, Any]) -> str:
    raw = str((server or {}).get("xui_sub_path") or "/sub/").strip()
    if not raw.startswith("/"):
        raw = "/" + raw
    if not raw.endswith("/"):
        raw += "/"
    return raw


# ---------------------------------------------------------------------------
# JSON / number helpers
# ---------------------------------------------------------------------------
def _parse_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return {}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _bytes_to_gb(value: Any) -> float:
    try:
        return _to_float(value, 0.0) / GB
    except Exception:
        return 0.0


def _gb_to_bytes(gb: Any) -> int:
    try:
        val = float(gb or 0)
    except (TypeError, ValueError):
        val = 0.0
    if val <= 0:
        return 0
    return int(val * GB)


def _ms_to_datetime(ms: Any) -> Optional[datetime]:
    try:
        if ms is None:
            return None
        ms = int(ms)
        if ms <= 0:
            return None
        return datetime.fromtimestamp(ms / _MS, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _parse_date(dt_str: Any) -> Optional[datetime]:
    if not dt_str:
        return None
    raw = str(dt_str).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = None
    if dt is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(raw, fmt)
            except ValueError:
                pass
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _end_of_day(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(hour=23, minute=59, second=59, microsecond=0)


def _days_from_payload(payload: Dict[str, Any]) -> Optional[int]:
    for key in ("package_days", "days", "remaining_days", "remaining_day"):
        if key in payload:
            try:
                val = int(float(payload.get(key) or 0))
                return val
            except (TypeError, ValueError):
                pass
    return None


def _gb_limit_from_payload(payload: Dict[str, Any]) -> Optional[float]:
    for key in ("usage_limit_GB", "usage_limit_gb", "usage_limit", "package_traffic", "data_limit_GB"):
        if key not in payload:
            continue
        try:
            val = _to_float(payload.get(key), -1.0)
        except Exception:
            continue
        if val >= 0:
            return val
    return None


def _enable_from_payload(payload: Dict[str, Any]) -> Optional[bool]:
    if "is_active" in payload:
        return bool(payload.get("is_active"))
    if "enable" in payload:
        return bool(payload.get("enable"))
    if "active" in payload:
        return bool(payload.get("active"))
    return None


def _compute_expiry_ms(payload: Dict[str, Any], current_ms: Optional[int] = None) -> int:
    """Compute client expiryTime (epoch ms) from a Hiddify-style payload.

    Priority: explicit ``expiryTime`` -> ``expire_date`` -> ``start_date``+days
    -> days from today -> keep current.
    """
    if "expiryTime" in payload:
        return _to_int(payload.get("expiryTime"), 0)

    if payload.get("expire_date") or payload.get("expire") or payload.get("end_date"):
        dt = _parse_date(payload.get("expire_date") or payload.get("expire") or payload.get("end_date"))
        dt = _end_of_day(dt)
        if dt:
            return int(dt.timestamp() * _MS)

    days = _days_from_payload(payload)
    if days is not None and days > 0:
        base: Optional[datetime] = _parse_date(payload.get("start_date"))
        if base is None:
            base = datetime.now(timezone.utc).replace(tzinfo=None)
        base = base.replace(hour=0, minute=0, second=0, microsecond=0)
        end = _end_of_day(base + timedelta(days=int(days)))
        if end:
            return int(end.timestamp() * _MS)

    if days is not None and days <= 0:
        return 0

    if current_ms is not None:
        return current_ms
    return 0


def _should_reset_traffic(payload: Dict[str, Any]) -> bool:
    if "current_usage_GB" in payload:
        try:
            if _to_float(payload.get("current_usage_GB"), -1) == 0:
                return True
        except Exception:
            pass
    if payload.get("last_reset_time"):
        return True
    if "reset_traffic" in payload and bool(payload.get("reset_traffic")):
        return True
    return False


# ---------------------------------------------------------------------------
# Client helpers (protocol-aware)
# ---------------------------------------------------------------------------
def _client_id_key(protocol: str) -> str:
    """The JSON key that uniquely identifies a client in an inbound of this
    protocol (used in *delClient* and *updateClient* URL params)."""
    protocol = (protocol or "").strip().lower()
    if protocol == "trojan":
        return "password"
    if protocol == "shadowsocks":
        return "email"
    if protocol == "hysteria":
        return "auth"
    return "id"


def _client_key_value(client: Dict[str, Any], protocol: str) -> str:
    key = _client_id_key(protocol)
    return str(client.get(key) or client.get("email") or "").strip()


def _settings_clients(settings: Any) -> List[Dict[str, Any]]:
    parsed = _parse_json(settings) or {}
    clients = parsed.get("clients")
    if not isinstance(clients, list):
        return []
    out: List[Dict[str, Any]] = []
    for c in clients:
        if isinstance(c, dict):
            out.append(c)
    return out


def _find_client_across_inbounds(
    inbounds: List[Dict[str, Any]], user_uuid: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Return (inbound, client) for a client matching email/subId/id/password."""
    needle = str(user_uuid or "").strip().lower()
    if not needle:
        return None, None
    for inbound in inbounds or []:
        for client in _settings_clients(inbound.get("settings")):
            hay = (
                str(client.get("email") or "").strip().lower(),
                str(client.get("subId") or "").strip().lower(),
                str(client.get("id") or "").strip().lower(),
                str(client.get("password") or "").strip().lower(),
            )
            if needle in hay:
                return inbound, client
    return None, None


def _client_id_for_url(client: Dict[str, Any], protocol: str) -> str:
    return _client_key_value(client, protocol)


def _find_stats_by_email(inbound: Dict[str, Any], email: str) -> Dict[str, Any]:
    stats = inbound.get("clientStats")
    if isinstance(stats, list):
        needle = str(email or "").strip().lower()
        for row in stats:
            if isinstance(row, dict) and str(row.get("email") or "").strip().lower() == needle:
                return row
    return {}


def _candidate_inbounds(inbounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enabled = [ib for ib in inbounds or [] if bool(ib.get("enable", True))]
    protocol_rank = {p: i for i, p in enumerate(SUPPORTED_PROTOCOLS)}

    def _rank(ib: Dict[str, Any]) -> Tuple[int, int]:
        proto = (ib.get("protocol") or "").strip().lower()
        return (protocol_rank.get(proto, len(SUPPORTED_PROTOCOLS)), _to_int(ib.get("id"), 0))

    return sorted(enabled, key=_rank)


def _select_sell_inbound(inbounds: List[Dict[str, Any]], server: Dict[str, Any]) -> Dict[str, Any]:
    pinned = _inbound_id(server)
    if pinned is not None:
        for ib in inbounds or []:
            if _to_int(ib.get("id"), 0) == pinned:
                return ib
        raise XuiApiError(f"اینباند با شناسه {pinned} در پنل X-UI یافت نشد یا غیرفعال است.")
    candidates = _candidate_inbounds(inbounds)
    if not candidates:
        raise XuiApiError(
            "هیچ اینباند فعالِ قابل‌فروش (vless/trojan/vmess/shadowsocks) روی پنل X-UI یافت نشد."
        )
    return candidates[0]


def _new_client_dict(protocol: str, *, uuid: str, template: Dict[str, Any]) -> Dict[str, Any]:
    """Build a client dict for the chosen protocol, cloning the format of an
    existing template client (so stream/extra fields stay compatible)."""
    protocol = (protocol or "").strip().lower()
    client: Dict[str, Any] = {}
    if isinstance(template, dict):
        client = dict(template)

    for key in ("email", "id", "subId", "password", "auth", "tgId"):
        client.pop(key, None)

    if protocol == "trojan":
        client["password"] = uuid
    elif protocol == "shadowsocks":
        client["email"] = uuid
    elif protocol == "hysteria":
        client["auth"] = uuid
    else:
        client["id"] = uuid

    client["email"] = uuid
    client["subId"] = uuid
    client["tgId"] = ""
    client["reset"] = 0
    return client


def _apply_payload_to_client(
    client: Dict[str, Any], protocol: str, payload: Dict[str, Any], cur_ms: Optional[int] = None
) -> Dict[str, Any]:
    protocol = (protocol or "").strip().lower()

    gb = _gb_limit_from_payload(payload)
    if gb is not None:
        client["totalGB"] = _gb_to_bytes(gb)

    client["expiryTime"] = _compute_expiry_ms(payload, current_ms=cur_ms)

    enable = _enable_from_payload(payload)
    if enable is not None:
        client["enable"] = bool(enable)

    # نام کاربر اگر در payload آمد، بعداً در patch_user به صورت یونیک ست می‌شود
    # اینجا فقط tgId را برای نمایش ست می‌کنیم
    raw_name = str(payload.get("name") or "").strip()
    if raw_name:
        client["tgId"] = raw_name

    client.setdefault("totalGB", 0)
    client.setdefault("expiryTime", 0)
    client.setdefault("enable", True)
    client.setdefault("subId", client.get("subId") or client.get("email") or "")
    client.setdefault("tgId", client.get("tgId") or "")
    client.setdefault("reset", client.get("reset") or 0)
    client.setdefault("limitIp", 0)
    return client


def _normalize_user(
    client: Dict[str, Any],
    inbound: Dict[str, Any],
    server: Dict[str, Any],
    *,
    onlines: Optional[set] = None,
) -> Dict[str, Any]:
    protocol = (inbound.get("protocol") or "").strip().lower()
    email = str(client.get("email") or "").strip()
    uuid = _client_key_value(client, protocol) or email
    stats = _find_stats_by_email(inbound, email)
    up = _to_int(stats.get("up"), 0)
    down = _to_int(stats.get("down"), 0)
    used_gb = _bytes_to_gb(up + down)
    limit_gb = _bytes_to_gb(client.get("totalGB"))
    enable = bool(client.get("enable", True))
    expiry_ms = _to_int(client.get("expiryTime"), 0)
    expire_dt = _ms_to_datetime(expiry_ms)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    days_left = (expire_dt.date() - now.date()).days if expire_dt else None
    online = bool(onlines) and (email in onlines or uuid in onlines)

    return {
        "uuid": uuid,
        "id": uuid,
        "name": str(client.get("tgId") or client.get("remark") or email or uuid).strip(),
        "email": email,
        "is_active": enable,
        "enable": enable,
        "active": enable,
        "mode": "active" if enable else "disabled",
        "status": "active" if enable else "disabled",
        "used_traffic": up + down,
        "totalGB": _to_int(client.get("totalGB"), 0),
        "limitIp": _to_int(client.get("limitIp"), 0),
        "up": up,
        "down": down,
        "current_usage_GB": round(used_gb, 3),
        "usage_limit_GB": round(limit_gb, 3),
        "usage_limit_gb": round(limit_gb, 3),
        "expiryTime": expiry_ms,
        "expire": expire_dt.strftime("%Y-%m-%d %H:%M:%S") if expire_dt else None,
        "expire_date": expire_dt.strftime("%Y-%m-%d") if expire_dt else None,
        "days_left": days_left,
        "remaining_days": days_left,
        "package_days": None,
        "start_date": None,
        "last_online": now.strftime("%Y-%m-%d %H:%M:%S") if online else None,
        "subId": str(client.get("subId") or "").strip() or uuid,
        "inbound_id": _to_int(inbound.get("id"), 0),
        "protocol": protocol,
        "port": _to_int(inbound.get("port"), 0),
        "server_id": (server or {}).get("id"),
        "comment": str(client.get("tgId") or client.get("remark") or "").strip(),
        "_source": "xui",
    }


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

# X-UI login session cache
# ------------------------
# Previously every user/node lookup opened a *fresh* X-UI login. The global
# enforcer fans these out via asyncio.gather across all services x nodes,
# which produced hundreds of concurrent login POSTs per cycle and pinned the
# CPU. We cache the authenticated httpx.AsyncClient per server for a short TTL
# so repeated calls reuse the same session instead of re-logging-in each time.
_XUI_SESSION_CACHE: Dict[Any, Tuple[float, "httpx.AsyncClient"]] = {}
_XUI_SESSION_TTL_SECONDS = float(os.getenv("XUI_SESSION_TTL_SECONDS", "60") or "60")
_xui_cache_lock = threading.Lock()
_xui_client_async_locks: Dict[Any, asyncio.Lock] = {}


def _server_cache_key(server: Dict[str, Any]) -> Any:
    return server.get("id")


async def _build_xui_client(server: Dict[str, Any], *, insecure: bool = False) -> "httpx.AsyncClient":
    if insecure or hiddify_api._get_ssl_mode() == hiddify_api.SSL_MODE_INSECURE:
        return _XuiContext._make_client(hiddify_api._build_insecure_ssl_context())
    try:
        return _XuiContext._make_client(True)
    except Exception:
        return _XuiContext._make_client(hiddify_api._build_insecure_ssl_context())


async def _login_xui_client(client: "httpx.AsyncClient", server: Dict[str, Any]) -> None:
    base = _get_panel_url(server)
    username, password = _get_credentials(server)
    try:
        resp = await client.post(base + "/login", json={"username": username, "password": password})
    except httpx.RequestError as exc:
        raise XuiApiError(f"عدم دسترسی به پنل X-UI ({base}/login): {exc}") from exc

    text = (resp.text or "").strip()
    if not text:
        raise XuiApiError("پاسخ لاگین پنل X-UI خالی بود.")
    try:
        data = json.loads(text)
    except ValueError:
        data = {}
    if isinstance(data, dict) and data.get("success") is False:
        raise XuiApiError("احراز هویت در پنل X-UI ناموفق بود (username/password اشتباه است).")
    if resp.status_code >= 400:
        raise XuiApiError(f"خطا در ورود به پنل X-UI: HTTP {resp.status_code} {text[:300]}")


async def _acquire_xui_client(server: Dict[str, Any]) -> "httpx.AsyncClient":
    key = _server_cache_key(server)
    # async per-server lock to avoid race where two coroutines both create a client and one closes the other's
    async_lock = _xui_client_async_locks.get(key)
    if async_lock is None:
        async_lock = asyncio.Lock()
        _xui_client_async_locks[key] = async_lock
    async with async_lock:
        now = time.monotonic()
        with _xui_cache_lock:
            cached = _XUI_SESSION_CACHE.get(key)
            if cached is not None and now < cached[0] and not cached[1].is_closed:
                return cached[1]
        client = await _build_xui_client(server)
        try:
            await _login_xui_client(client, server)
        except Exception:
            try:
                await client.aclose()
            except Exception:
                pass
            raise
        with _xui_cache_lock:
            # re-check: another waiter may have already inserted a fresh client while we were logging in
            existing = _XUI_SESSION_CACHE.get(key)
            if existing is not None and time.monotonic() < existing[0] and not existing[1].is_closed and existing[1] is not client:
                # keep the existing one, close the one we just created
                try:
                    await client.aclose()
                except Exception:
                    pass
                return existing[1]
            old = existing
            _XUI_SESSION_CACHE[key] = (time.monotonic() + _XUI_SESSION_TTL_SECONDS, client)
            superseded = old[1] if (old is not None and old[1] is not client) else None
        if superseded is not None and not superseded.is_closed:
            try:
                await superseded.aclose()
            except Exception:
                pass
        return client


async def _refresh_xui_client(server: Dict[str, Any], *, insecure: bool = False) -> "httpx.AsyncClient":
    """Force a fresh authenticated client, replacing any cached entry."""
    key = _server_cache_key(server)
    client = await _build_xui_client(server, insecure=insecure)
    try:
        await _login_xui_client(client, server)
    except Exception:
        try:
            await client.aclose()
        except Exception:
            pass
        raise
    with _xui_cache_lock:
        old = _XUI_SESSION_CACHE.get(key)
        _XUI_SESSION_CACHE[key] = (time.monotonic() + _XUI_SESSION_TTL_SECONDS, client)
        superseded = old[1] if (old is not None and old[1] is not client) else None
    if superseded is not None and not superseded.is_closed:
        try:
            await superseded.aclose()
        except Exception:
            pass
    return client


# Per-server inbounds/onlines caches (short TTL) to collapse N×M per-cycle
# HTTP GETs into ~1 GET per server per cycle.
_XUI_INBOUNDS_CACHE: Dict[Any, Tuple[float, List[Dict[str, Any]]]] = {}
_XUI_ONLINES_CACHE: Dict[Any, Tuple[float, set]] = {}
_XUI_INBOUNDS_TTL = float(os.getenv("XUI_INBOUNDS_CACHE_SECONDS", "15") or "15")
_XUI_ONLINES_TTL = float(os.getenv("XUI_ONLINES_CACHE_SECONDS", "15") or "15")
_xui_inbounds_locks: Dict[Any, asyncio.Lock] = {}
_xui_onlines_locks: Dict[Any, asyncio.Lock] = {}


def _invalidate_xui_inbounds_cache(server: Dict[str, Any]) -> None:
    key = _server_cache_key(server)
    with _xui_cache_lock:
        _XUI_INBOUNDS_CACHE.pop(key, None)
        _XUI_ONLINES_CACHE.pop(key, None)


class _XuiContext:
    """Bound async HTTP session for one server (login cookie preserved)."""

    def __init__(self, server: Dict[str, Any]):
        self.server = server
        self.base = _get_panel_url(server)
        self.api = _api_base(server)
        self.client: Optional[httpx.AsyncClient] = None
        self._mode = hiddify_api._get_ssl_mode()

    async def __aenter__(self) -> "_XuiContext":
        # Reuse the per-server cached, already-authenticated client instead of
        # logging in on every call (see _acquire_xui_client).
        self.client = await _acquire_xui_client(self.server)
        return self

    async def __aexit__(self, *args: Any) -> None:
        # The client is owned by the session cache and shared; never close it here.
        self.client = None

    async def close(self) -> None:
        # Cached clients are managed by the session cache; nothing to close.
        self.client = None

    @staticmethod
    def _make_client(verify: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=hiddify_api._get_api_timeout_seconds(),
            verify=verify,
            follow_redirects=False,
        )

    async def _ensure_login(self) -> None:
        # (Re)authenticate the shared cached client in place.
        assert self.client is not None
        await _login_xui_client(self.client, self.server)

    async def request(self, method: str, path: str, *, json_body: Any = None, allow_login_retry: bool = True) -> Any:
        url = f"{self.api}/{path.lstrip('/')}"
        secret = str((self.server or {}).get("xui_secret") or "").strip()
        headers = {"Accept": "application/json, text/plain, */*"}
        if secret:
            headers[_XUI_SECRET_HEADER] = secret
        assert self.client is not None
        for attempt in (1, 3):
            try:
                # اگر کلاینت در فاصله بین _acquire و اینجا بسته شد، تازه‌سازی کن
                if self.client.is_closed:
                    self.client = await _acquire_xui_client(self.server)
                resp = await self.client.request(method, url, headers=headers, json=json_body)
            except RuntimeError as exc:
                # httpx raises RuntimeError: Cannot send a request, as the client has been closed.
                if "has been closed" in str(exc).lower() and attempt == 1:
                    try:
                        self.client = await _refresh_xui_client(self.server)
                    except Exception:
                        self.client = await _acquire_xui_client(self.server)
                    continue
                raise XuiApiError(f"خطا در ارتباط با پنل X-UI: {exc}") from exc
            except httpx.RequestError as exc:
                msg = str(exc).strip() or exc.__class__.__name__
                if hiddify_api._is_transient_network_error(exc):
                    raise XuiApiError(f"خطای شبکه در ارتباط با پنل X-UI: {msg}") from exc
                if (
                    self._mode == hiddify_api.SSL_MODE_AUTO
                    and hiddify_api._looks_like_tls_error(exc)
                    and attempt == 1
                ):
                    self.client = await _refresh_xui_client(self.server, insecure=True)
                    continue
                raise XuiApiError(f"خطا در ارتباط با پنل X-UI: {msg}") from exc

            if resp.status_code == 401 and allow_login_retry and attempt == 1:
                await self._ensure_login()
                continue
            break

        if resp.status_code >= 400:
            raise XuiApiError(f"X-UI API HTTP {resp.status_code} {path}: {resp.text[:400]}")

        ct = (resp.headers.get("content-type") or "").lower()
        if "json" not in ct:
            return resp.text

        try:
            data = resp.json()
        except ValueError:
            return resp.text

        if isinstance(data, dict):
            if data.get("success") is False:
                raise XuiApiError(f"X-UI API error ({path}): {data.get('msg') or 'unknown'}")
            if "obj" in data:
                return data.get("obj")
        return data

    async def raw_request(self, method: str, path: str, *, allow_login_retry: bool = True) -> "httpx.Response":
        """Like ``request`` but returns the raw response (binary-safe)."""
        url = f"{self.api}/{path.lstrip('/')}"
        secret = str((self.server or {}).get("xui_secret") or "").strip()
        headers = {"Accept": "*/*"}
        if secret:
            headers[_XUI_SECRET_HEADER] = secret
        assert self.client is not None
        resp: Optional[httpx.Response] = None
        for attempt in (1, 3):
            try:
                if self.client.is_closed:
                    self.client = await _acquire_xui_client(self.server)
                resp = await self.client.request(method, url, headers=headers)
            except RuntimeError as exc:
                if "has been closed" in str(exc).lower() and attempt == 1:
                    try:
                        self.client = await _refresh_xui_client(self.server)
                    except Exception:
                        self.client = await _acquire_xui_client(self.server)
                    continue
                raise XuiApiError(f"خطا در ارتباط با پنل X-UI: {exc}") from exc
            if resp.status_code == 401 and allow_login_retry and attempt == 1:
                await self._ensure_login()
                continue
            break
        if resp is None:
            raise XuiApiError("X-UI raw request produced no response.")
        if resp.status_code >= 400:
            raise XuiApiError(f"X-UI API HTTP {resp.status_code} {path}: {resp.text[:400]}")
        return resp


async def _list_inbounds(server: Dict[str, Any], *, _force_refresh: bool = False) -> List[Dict[str, Any]]:
    key = _server_cache_key(server)
    if not _force_refresh:
        with _xui_cache_lock:
            cached = _XUI_INBOUNDS_CACHE.get(key)
            if cached is not None and time.monotonic() < cached[0]:
                return cached[1]
            lock = _xui_inbounds_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _xui_inbounds_locks[key] = lock
    else:
        with _xui_cache_lock:
            lock = _xui_inbounds_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _xui_inbounds_locks[key] = lock
    async with lock:
        if not _force_refresh:
            with _xui_cache_lock:
                cached = _XUI_INBOUNDS_CACHE.get(key)
                if cached is not None and time.monotonic() < cached[0]:
                    return cached[1]
        async with _XuiContext(server) as ctx:
            data = await ctx.request("GET", "inbounds/")
        if not isinstance(data, list):
            raise XuiApiError("پاسخ لیست اینباندهای X-UI شکل آرایه ندارد.")
        with _xui_cache_lock:
            _XUI_INBOUNDS_CACHE[key] = (time.monotonic() + _XUI_INBOUNDS_TTL, data)
        return data


async def _online_emails(server: Dict[str, Any], *, _force_refresh: bool = False) -> set:
    key = _server_cache_key(server)
    if not _force_refresh:
        with _xui_cache_lock:
            cached = _XUI_ONLINES_CACHE.get(key)
            if cached is not None and time.monotonic() < cached[0]:
                return cached[1]
            lock = _xui_onlines_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _xui_onlines_locks[key] = lock
    else:
        with _xui_cache_lock:
            lock = _xui_onlines_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _xui_onlines_locks[key] = lock
    async with lock:
        if not _force_refresh:
            with _xui_cache_lock:
                cached = _XUI_ONLINES_CACHE.get(key)
                if cached is not None and time.monotonic() < cached[0]:
                    return cached[1]
        try:
            async with _XuiContext(server) as ctx:
                data = await ctx.request("POST", "inbounds/onlines", allow_login_retry=False)
        except Exception as exc:
            logger.debug("onlines fetch failed for xui: %s", exc)
            return set()
        out: set = set()
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    out.add(item.strip())
                elif isinstance(item, dict):
                    m = _ONLINE_EMAIL_RE.search(str(item))
                    if m:
                        out.add(m.group(1).strip())
        with _xui_cache_lock:
            _XUI_ONLINES_CACHE[key] = (time.monotonic() + _XUI_ONLINES_TTL, out)
        return out


# ---------------------------------------------------------------------------
# Public API (mirrors Shared/hiddify_api.py signatures)
# ---------------------------------------------------------------------------
async def test_connect(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Login + list inbounds.  Returns the raw inbound list (wizard uses it)."""
    return await _list_inbounds(server)


async def list_users(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    """All clients across all (supported) inbounds, normalized and deduplicated.

    Users created with xui_inbound_id=0 (all inbounds) exist on every inbound
    with same subId/uuid but different email (base_email vs base_email-2).
    Without dedup the admin list shows 4× duplicates. We group by subId/uuid
    and aggregate traffic, like get_user_by_uuid does.
    """
    inbounds = await _list_inbounds(server)
    onlines = await _online_emails(server)
    # Group by stable uuid (subId preferred, fallback to id/password)
    groups: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    order: List[str] = []
    for inbound in inbounds:
        proto = (inbound.get("protocol") or "").strip().lower()
        if proto not in SUPPORTED_PROTOCOLS:
            continue
        for client in _settings_clients(inbound.get("settings")):
            email = str(client.get("email") or "").strip()
            if not email:
                continue
            # stable key: subId (always uuid) -> id/password -> email
            key = str(client.get("subId") or _client_key_value(client, proto) or email).strip()
            if not key:
                continue
            low = key.strip().lower()
            if low not in groups:
                groups[low] = []
                order.append(low)
            groups[low].append((inbound, client))

    out: List[Dict[str, Any]] = []
    for low in order:
        pairs = groups[low]
        if not pairs:
            continue
        # Aggregate traffic across all inbounds for this user
        total_up = 0
        total_down = 0
        for ib, cl in pairs:
            email = str(cl.get("email") or "").strip()
            stats = _find_stats_by_email(ib, email)
            total_up += _to_int(stats.get("up"), 0)
            total_down += _to_int(stats.get("down"), 0)
        inbound, client = pairs[0]
        norm = _normalize_user(client, inbound, server, onlines=onlines)
        # Override aggregated traffic and online if any inbound is online
        norm["up"] = total_up
        norm["down"] = total_down
        norm["used_traffic"] = total_up + total_down
        norm["current_usage_GB"] = round(_bytes_to_gb(total_up + total_down), 3)
        # Online if any email in group is in onlines set
        if onlines:
            for _, cl in pairs:
                em = str(cl.get("email") or "").strip()
                uid = _client_key_value(cl, (pairs[0][0].get("protocol") or "").strip().lower()) or em
                if em in onlines or uid in onlines:
                    # Force online appearance
                    if not norm.get("last_online"):
                        from datetime import datetime as _dt
                        norm["last_online"] = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
                    break
        out.append(norm)
    return out


async def get_user_by_uuid(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    user_uuid = str(user_uuid or "").strip()
    if not user_uuid:
        raise XuiApiError("user uuid خالی است.")
    inbounds = await _list_inbounds(server)
    pairs = _find_all_clients_for_uuid(inbounds, user_uuid)
    if not pairs:
        raise XuiApiError(f"user not found (uuid={user_uuid})")
    # جمع ترافیک از همه اینباندها (هوشمند)
    total_up = 0
    total_down = 0
    for ib, _cl in pairs:
        email = str(_cl.get("email") or "").strip()
        stats = _find_stats_by_email(ib, email)
        total_up += _to_int(stats.get("up"), 0)
        total_down += _to_int(stats.get("down"), 0)
    inbound, client = pairs[0]
    onlines = await _online_emails(server)
    norm = _normalize_user(client, inbound, server, onlines=onlines)
    norm["up"] = total_up
    norm["down"] = total_down
    norm["used_traffic"] = total_up + total_down
    norm["current_usage_GB"] = round(_bytes_to_gb(total_up + total_down), 3)
    return norm


def _find_all_clients_for_uuid(
    inbounds: List[Dict[str, Any]], user_uuid: str
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return list of (inbound, client) for all inbounds containing this uuid."""
    needle = str(user_uuid or "").strip().lower()
    if not needle:
        return []
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for inbound in inbounds or []:
        for client in _settings_clients(inbound.get("settings")):
            hay = (
                str(client.get("email") or "").strip().lower(),
                str(client.get("subId") or "").strip().lower(),
                str(client.get("id") or "").strip().lower(),
                str(client.get("password") or "").strip().lower(),
            )
            if needle in hay:
                out.append((inbound, client))
                break
    return out


async def create_user(server: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a client on the sell inbound(s) of the X-UI panel.

    Supports:
      0          -> همه اینباندهای فعال
      "1"        -> تک اینباند
      "1,2,3"    -> چند اینباند مشخص
      خالی/skip  -> اولین کاندید (رفتار قدیم)
    A fresh uuid is generated and used as the client id / email / subId so
    the rest of the bot can address the user by uuid.
    """
    inbounds = await _list_inbounds(server)
    targets = _resolve_target_inbounds(server, inbounds)
    if not targets:
        raise XuiApiError("هیچ اینباند قابل‌فروشی برای ساخت کاربر یافت نشد.")

    # Use shared UUID from payload if provided (ensures multi-node consistency);
    # otherwise generate a fresh one. Validate format to avoid junk.
    requested = str((payload or {}).get("uuid") or "").strip()
    if requested and len(requested) >= 8 and " " not in requested:
        user_uuid = requested
    else:
        user_uuid = uuid4()
    # نام کاربر برای نمایش در پنل (به جای uuid) - از payload می‌آید مثل "test"
    raw_name = str(payload.get("name") or payload.get("email") or "").strip()
    # X-UI email نمی‌تواند ایموجی داشته باشد؛ فیلتر کن وگرنه پنل ارور می‌دهد
    # اگر نام ایموجی‌دار بود، به صورت sanitized (بدون ایموجی) نمایش بده تا پنل قبول کند
    base_email = _sanitize_xui_email(raw_name, user_uuid) if raw_name else user_uuid
    # X-UI email must be unique panel-wide (unlike Hiddify). If duplicate, append random digits.
    if raw_name:
        existing = _existing_xui_emails(inbounds)
        base_email = _unique_xui_email(base_email, existing, user_uuid)
    # برای اطمینان از یونیک بودن پنل‌واید، اگر نام تکراری بود بعداً suffix می‌خورد
    first_client: Optional[Dict[str, Any]] = None
    first_inbound: Optional[Dict[str, Any]] = None

    created: List[Tuple[int, str]] = []  # (inbound_id, client_email_or_id)
    async with _XuiContext(server) as ctx:
        try:
            for idx, inbound in enumerate(targets):
                protocol = (inbound.get("protocol") or "").strip().lower()
                clients = _settings_clients(inbound.get("settings"))
                template = clients[0] if clients else {}
                client = _new_client_dict(protocol, uuid=user_uuid, template=template)
                client = _apply_payload_to_client(client, protocol, payload)
                # نمایش در پنل: به جای uuid، نام کاربر را بگذار (درخواست کاربر)
                # برای چنداینبانده، اولی دقیقاً نام، بقیه نام-اینباند تا یونیک بماند
                if len(targets) == 1:
                    client["email"] = base_email
                else:
                    if idx == 0:
                        client["email"] = base_email
                    else:
                        client["email"] = f"{base_email}-{_to_int(inbound.get('id'), 0)}"
                # subId را همان uuid نگه می‌داریم تا /sub/uuid همه را جمع کند
                if "subId" in client:
                    client["subId"] = user_uuid
                # tgId/remark را هم برای نمایش بهتر ست می‌کنیم
                if "tgId" in client:
                    client["tgId"] = base_email
                settings_body = json.dumps({"clients": [client]})
                await ctx.request(
                    "POST",
                    "inbounds/addClient",
                    json_body={"id": _to_int(inbound.get("id"), 0), "settings": settings_body},
                )
                # برای رول‌بک در صورت خطا در اینباند بعدی
                created.append((_to_int(inbound.get("id"), 0), _client_id_for_url(client, protocol)))
                if first_client is None:
                    first_client = client
                    first_inbound = inbound
        except Exception:
            # رول‌بک: هر کلاینتی که تا الان ساخته شد را پاک کن
            for iid, cid in created:
                try:
                    await ctx.request(
                        "POST",
                        f"inbounds/{iid}/delClient/{quote(cid, safe='')}",
                        allow_login_retry=False,
                    )
                except Exception:
                    pass
            raise

    assert first_client is not None and first_inbound is not None
    _invalidate_xui_inbounds_cache(server)
    return _normalize_user(first_client, first_inbound, server)


async def patch_user(server: Dict[str, Any], user_uuid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Update a client's quota / expiry / active state on X-UI (همه اینباندها)."""
    user_uuid = str(user_uuid or "").strip()
    if not user_uuid:
        raise XuiApiError("user uuid خالی است.")
    inbounds = await _list_inbounds(server)
    pairs = _find_all_clients_for_uuid(inbounds, user_uuid)
    if not pairs:
        raise XuiApiError(f"user not found (uuid={user_uuid})")

    # Prepare unique sanitized name for X-UI (panel rejects duplicate email)
    raw_name_global = str(payload.get("name") or "").strip()
    unique_base = ""
    if raw_name_global:
        sanitized_base = _sanitize_xui_email(raw_name_global, user_uuid)
        existing = _existing_xui_emails(inbounds)
        for _, cl in pairs:
            e = str(cl.get("email") or "").strip()
            if e:
                existing.discard(e)
                existing.discard(e.lower())
        unique_base = _unique_xui_email(sanitized_base, existing, user_uuid)

    last_norm = None
    async with _XuiContext(server) as ctx:
        for idx, (inbound, client) in enumerate(pairs):
            protocol = (inbound.get("protocol") or "").strip().lower()
            cur_ms = _to_int(client.get("expiryTime"), 0)
            new_client = dict(client)
            new_client = _apply_payload_to_client(new_client, protocol, payload, cur_ms=cur_ms)
            # اگر نام جدید آمد، ایمیل را هم با الگوی یونیک به‌روز کن
            if raw_name_global:
                if len(pairs) == 1:
                    new_client["email"] = unique_base
                else:
                    new_client["email"] = unique_base if idx == 0 else f"{unique_base}-{_to_int(inbound.get('id'), 0)}"
            client_id = _client_id_for_url(client, protocol)
            settings_body = json.dumps({"clients": [new_client]})
            await ctx.request(
                "POST",
                f"inbounds/updateClient/{quote(client_id, safe='')}",
                json_body={"id": _to_int(inbound.get("id"), 0), "settings": settings_body},
            )
            if _should_reset_traffic(payload):
                try:
                    await ctx.request(
                        "POST",
                        f"inbounds/{_to_int(inbound.get('id'), 0)}/resetClientTraffic/{quote(client_id, safe='')}",
                        allow_login_retry=False,
                    )
                except Exception:
                    pass
            last_norm = _normalize_user(new_client, inbound, server)
    assert last_norm is not None
    _invalidate_xui_inbounds_cache(server)
    return last_norm


async def enable_user(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    return await patch_user(server, user_uuid, {"is_active": True, "enable": True})


async def disable_user(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    return await patch_user(server, user_uuid, {"is_active": False, "enable": False})


async def delete_user(server: Dict[str, Any], user_uuid: str) -> None:
    user_uuid = str(user_uuid or "").strip()
    if not user_uuid:
        raise XuiApiError("user uuid خالی است.")
    inbounds = await _list_inbounds(server)
    pairs = _find_all_clients_for_uuid(inbounds, user_uuid)
    if not pairs:
        raise XuiApiError(f"user not found (uuid={user_uuid})")
    async with _XuiContext(server) as ctx:
        for inbound, client in pairs:
            client_id = _client_id_for_url(client, (inbound.get("protocol") or "").strip().lower())
            try:
                await ctx.request(
                    "POST",
                    f"inbounds/{_to_int(inbound.get('id'), 0)}/delClient/{quote(client_id, safe='')}",
                )
            except Exception as exc:
                logger.warning("xui delete on inbound %s failed: %s", _to_int(inbound.get("id"), 0), exc)
    _invalidate_xui_inbounds_cache(server)


async def get_subscription_url(server: Dict[str, Any], user_uuid: str) -> str:
    user_uuid = str(user_uuid or "").strip()
    if not user_uuid:
        return ""
    origin = _public_origin(server)
    return f"{origin}{_sub_path(server)}{user_uuid}"


async def get_user_configs(server: Dict[str, Any], user_uuid: str) -> List[Dict[str, Any]]:
    """Config list for a user.

    Always includes the native X-UI subscription URL; direct per-protocol
    links are added when the inbound settings can be parsed.
    """
    user_uuid = str(user_uuid or "").strip()
    items: List[Dict[str, Any]] = []
    if not user_uuid:
        return items

    sub_url = await get_subscription_url(server, user_uuid)
    if sub_url:
        items.append(
            {
                "link": sub_url,
                "name": "Subscription",
                "protocol": "sub",
                "security": "",
                "transport": "",
                "_source": "xui",
            }
        )

    try:
        inbounds = await _list_inbounds(server)
        pairs = _find_all_clients_for_uuid(inbounds, user_uuid)
    except Exception as exc:
        logger.warning("xui get_user_configs inbound lookup failed: %s", exc)
        pairs = []

    for inbound, client in pairs:
        links = _build_direct_links(server, inbound, client)
        for link in links:
            items.append(
                {
                    "link": link,
                    "protocol": (inbound.get("protocol") or "").lower(),
                    "_source": "xui",
                }
            )
    return items


async def get_server_stats(server: Dict[str, Any]) -> Dict[str, Any]:
    """System + traffic stats (same shape as hiddify_api.get_server_stats)."""
    out = {
        "cpu_percent": 0.0,
        "cpu_cores": 1,
        "ram_used": 0,
        "ram_total": 1,
        "disk_used": 0,
        "disk_total": 0,
        "users_total": 0,
        "users_online": 0,
        "users_today": 0,
        "users_month": 0,
        "usage_today_gb": 0.0,
        "usage_30days_gb": 0.0,
        "traffic_dl": 0.0,
        "traffic_ul": 0.0,
        "now_net_recv_mb": 0.0,
        "now_net_sent_mb": 0.0,
        "uptime": 0,
        "xray_state": "unknown",
        "xray_version": "",
    }

    inbounds: List[Dict[str, Any]] = []
    try:
        inbounds = await _list_inbounds(server)
    except Exception as exc:
        logger.warning("xui get_server_stats inbounds failed: %s", exc)

    total_up = 0
    total_down = 0
    users_total = 0
    for inbound in inbounds or []:
        if (inbound.get("protocol") or "").strip().lower() not in SUPPORTED_PROTOCOLS:
            continue
        for client in _settings_clients(inbound.get("settings")):
            email = str(client.get("email") or "").strip()
            if not email:
                continue
            users_total += 1
            stats = _find_stats_by_email(inbound, email)
            total_up += _to_int(stats.get("up"), 0)
            total_down += _to_int(stats.get("down"), 0)
    out["users_total"] = users_total
    out["usage_30days_gb"] = round(_bytes_to_gb(total_up + total_down), 3)

    try:
        async with _XuiContext(server) as ctx:
            data = await ctx.request("GET", "server/status")
    except Exception as exc:
        logger.warning("xui server status failed: %s", exc)
        return out

    if not isinstance(data, dict):
        return out

    out["cpu_percent"] = _to_float(data.get("cpu"), 0.0)
    out["cpu_cores"] = _to_int(data.get("cpuCount"), 1)
    mem = data.get("mem") or {}
    out["ram_used"] = _to_int(mem.get("current"), 0)
    out["ram_total"] = _to_int(mem.get("total"), 1)
    disk = data.get("disk") or {}
    out["disk_used"] = _to_int(disk.get("current"), 0)
    out["disk_total"] = _to_int(disk.get("total"), 0)
    net_io = data.get("netIO") or {}
    out["traffic_dl"] = round(_bytes_to_gb(net_io.get("down")), 3)
    out["traffic_ul"] = round(_bytes_to_gb(net_io.get("up")), 3)
    out["uptime"] = _to_int(data.get("uptime"), 0)
    xray = data.get("xray") or {}
    out["xray_state"] = str(xray.get("state") or "unknown")
    out["xray_version"] = str(xray.get("version") or "")

    try:
        online = await _online_emails(server)
        out["users_online"] = len(online)
    except Exception:
        pass
    return out


async def download_server_backup(server: Dict[str, Any]) -> Dict[str, Any]:
    """Download the X-UI sqlite database as a backup artifact.

    Returns the caller-expected shape: ``{"filename", "content", "source_url"}``.
    """
    source_url = f"{_api_base(server)}/server/getDb"
    async with _XuiContext(server) as ctx:
        resp = await ctx.raw_request("GET", "server/getDb")
    return {
        "filename": _backup_filename(server),
        "content": resp.content,
        "source_url": source_url,
    }


def _backup_filename(server: Dict[str, Any]) -> str:
    host = ""
    try:
        host = urlparse(_get_panel_url(server)).hostname or "x-ui"
    except Exception:
        host = "x-ui"
    ts = datetime.now().strftime("%Y-%m-%d %H_%M_%S.%f")
    safe_host = re.sub(r"[^A-Za-z0-9_.\-]+", "_", host)
    return f"xui-{safe_host}-{ts}.db"


# ---------------------------------------------------------------------------
# Native subscription fetch (used by the bot's managed sub server)
# ---------------------------------------------------------------------------
def _sanitize_line(line: str) -> str:
    return str(line or "").strip()


def _decode_sub_body(body_text: str) -> List[str]:
    lines = [_sanitize_line(ln) for ln in str(body_text or "").splitlines() if _sanitize_line(ln)]
    if lines and any("://" in ln for ln in lines):
        return lines
    compact = re.sub(r"\s+", "", str(body_text or ""))
    if "://" in compact:
        return [_sanitize_line(compact)]
    for cand in (str(body_text or "").strip(), compact):
        try:
            padded = cand + ("=" * ((4 - len(cand) % 4) % 4))
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
        except Exception:
            continue
        dl = [_sanitize_line(ln) for ln in decoded.splitlines() if _sanitize_line(ln)]
        if dl:
            return dl
    return []


async def _fetch_subscription_lines(server: Dict[str, Any], user_uuid: str) -> List[str]:
    sub_url = await get_subscription_url(server, user_uuid)
    if not sub_url:
        return []
    async with _XuiContext(server) as ctx:
        if ctx.client is None:
            return []
        resp = await ctx.client.get(sub_url)
        if resp.status_code >= 400:
            logger.debug("xui sub fetch HTTP %s for %s", resp.status_code, sub_url)
            raise XuiApiError(f"subscription not available: HTTP {resp.status_code}")
        text = resp.text or ""
    lines = _decode_sub_body(text)
    if not lines:
        raise XuiApiError("subscription body empty or undecodable")
    return lines


async def fetch_subscription_lines(server: Dict[str, Any], user_uuid: str) -> List[str]:
    """Config lines for a user (used by sub_http_server for X-UI panels)."""
    return await _fetch_subscription_lines(server, user_uuid)


def sync_fetch_subscription_lines(server: Dict[str, Any], user_uuid: str) -> List[str]:
    """Blocking wrapper for threads (sub_http_server workers)."""
    return asyncio.run(_fetch_subscription_lines(server, user_uuid))


# ---------------------------------------------------------------------------
# Direct protocol link builder
# ---------------------------------------------------------------------------
def _inbound_host_and_port(server: Dict[str, Any], inbound: Dict[str, Any]) -> Tuple[str, int]:
    origin = _public_origin(server)
    parsed = urlparse(origin)
    host = parsed.hostname or ""
    port = _to_int(inbound.get("port"), 0)
    if not port and parsed.port:
        port = parsed.port
    return host, port


def _first_sni(tls: Dict[str, Any]) -> str:
    server_names = tls.get("serverName") or tls.get("serverNames") or []
    if isinstance(server_names, list) and server_names:
        return str(server_names[0]).strip()
    if isinstance(server_names, str) and server_names:
        return server_names.strip()
    return ""


def _network_query_extras(network: str, stream: Dict[str, Any]) -> Dict[str, str]:
    extras: Dict[str, str] = {}
    network = (network or "").strip().lower() or "tcp"
    ws = stream.get("wsSettings") or {}
    grpc = stream.get("grpcSettings") or {}
    httpup = stream.get("httpupgradeSettings") or {}
    xhttp = stream.get("xhttpSettings") or {}
    if network == "ws":
        ws_path = str(ws.get("path") or "").strip()
        if ws_path:
            extras["path"] = ws_path
        headers = ws.get("headers") or {}
        host = str(headers.get("Host") or "").strip()
        if host:
            extras["host"] = host
    elif network == "grpc":
        svc = str(grpc.get("serviceName") or "").strip()
        if svc:
            extras["serviceName"] = svc
        authority = str(grpc.get("authority") or "").strip()
        if authority:
            extras["authority"] = authority
    elif network == "httpupgrade":
        hp = str(httpup.get("path") or "").strip()
        if hp:
            extras["path"] = hp
        headers = httpup.get("headers") or {}
        host = str(headers.get("Host") or "").strip()
        if host:
            extras["host"] = host
    elif network == "xhttp":
        hp = str(xhttp.get("path") or "").strip()
        if hp:
            extras["path"] = hp
        headers = xhttp.get("headers") or {}
        host = str(headers.get("Host") or "").strip()
        if host:
            extras["host"] = host
    return extras


def _query_string(pairs: List[Tuple[str, str]]) -> str:
    return "&".join(f"{k}={quote(str(v or ''), safe='')}" for k, v in pairs if v)


def _vless_link(server: Dict[str, Any], inbound: Dict[str, Any], client: Dict[str, Any]) -> Optional[str]:
    stream = _parse_json(inbound.get("streamSettings")) or {}
    network = (stream.get("network") or "tcp").strip().lower() or "tcp"
    security = (stream.get("security") or "none").strip().lower()
    tls = stream.get("tls") or {}
    host, port = _inbound_host_and_port(server, inbound)
    if not host or not port:
        return None
    cid = str(client.get("id") or "").strip()
    if not cid:
        return None

    pairs: List[Tuple[str, str]] = [("encryption", "none"), ("type", network)]
    flow = str(client.get("flow") or "").strip()
    if flow:
        pairs.append(("flow", flow))
    if security == "reality":
        pairs.append(("security", "reality"))
        reality = tls.get("reality") or {}
        fp = str(tls.get("fingerprint") or "").strip()
        if fp:
            pairs.append(("fp", fp))
        pk = str(reality.get("publicKey") or "").strip()
        if pk:
            pairs.append(("pbk", pk))
        sid = str(reality.get("shortId") or "").strip()
        if sid:
            pairs.append(("sid", sid))
        sni = _first_sni(tls)
        if sni:
            pairs.append(("sni", sni))
    elif security == "tls":
        pairs.append(("security", "tls"))
        fp = str(tls.get("fingerprint") or "").strip()
        if fp:
            pairs.append(("fp", fp))
        sni = _first_sni(tls)
        if sni:
            pairs.append(("sni", sni))
        if tls.get("allowInsecure"):
            pairs.append(("allowInsecure", "1"))
    for k, v in _network_query_extras(network, stream).items():
        pairs.append((k, v))
    query = _query_string(pairs)
    name = f"{inbound.get('remark') or server.get('title') or 'xui'}-vless"
    return f"vless://{cid}@{host}:{port}?{query}#{quote(name, safe='')}"


def _vmess_link(server: Dict[str, Any], inbound: Dict[str, Any], client: Dict[str, Any]) -> Optional[str]:
    stream = _parse_json(inbound.get("streamSettings")) or {}
    network = (stream.get("network") or "tcp").strip().lower() or "tcp"
    security = (stream.get("security") or "none").strip().lower()
    tls = stream.get("tls") or {}
    host, port = _inbound_host_and_port(server, inbound)
    if not host or not port:
        return None
    cid = str(client.get("id") or "").strip()
    if not cid:
        return None

    extras = _network_query_extras(network, stream)
    vmess = {
        "v": "2",
        "ps": str(inbound.get("remark") or server.get("title") or "xui"),
        "add": host,
        "port": str(port),
        "id": cid,
        "aid": "0",
        "scy": "auto",
        "net": network,
        "type": "none",
        "host": extras.get("host") or "",
        "path": extras.get("path") or extras.get("serviceName") or "",
        "tls": "tls" if security in ("tls", "reality") else "none",
        "sni": _first_sni(tls) if security in ("tls", "reality") else "",
    }
    if security == "reality" and tls.get("reality"):
        reality = tls.get("reality") or {}
        vmess["fp"] = str(tls.get("fingerprint") or "")
        vmess["pbk"] = str(reality.get("publicKey") or "")
        vmess["sid"] = str(reality.get("shortId") or "")
    return "vmess://" + base64.urlsafe_b64encode(json.dumps(vmess, ensure_ascii=False).encode("utf-8")).decode("ascii")


def _trojan_link(server: Dict[str, Any], inbound: Dict[str, Any], client: Dict[str, Any]) -> Optional[str]:
    stream = _parse_json(inbound.get("streamSettings")) or {}
    network = (stream.get("network") or "tcp").strip().lower() or "tcp"
    security = (stream.get("security") or "none").strip().lower()
    tls = stream.get("tls") or {}
    host, port = _inbound_host_and_port(server, inbound)
    if not host or not port:
        return None
    password = str(client.get("password") or "").strip()
    if not password:
        return None
    pairs: List[Tuple[str, str]] = []
    if security in ("tls", "reality"):
        pairs.append(("security", security))
        fp = str(tls.get("fingerprint") or "").strip()
        if fp:
            pairs.append(("fp", fp))
        sni = _first_sni(tls)
        if sni:
            pairs.append(("sni", sni))
        if security == "reality" and tls.get("reality"):
            reality = tls.get("reality") or {}
            pk = str(reality.get("publicKey") or "").strip()
            if pk:
                pairs.append(("pbk", pk))
            sid = str(reality.get("shortId") or "").strip()
            if sid:
                pairs.append(("sid", sid))
    pairs.append(("type", network))
    for k, v in _network_query_extras(network, stream).items():
        pairs.append((k, v))
    query = _query_string(pairs)
    name = f"{inbound.get('remark') or server.get('title') or 'xui'}-trojan"
    return f"trojan://{quote(password, safe='')}@{host}:{port}?{query}#{quote(name, safe='')}"


def _shadowsocks_link(server: Dict[str, Any], inbound: Dict[str, Any], client: Dict[str, Any]) -> Optional[str]:
    stream = _parse_json(inbound.get("streamSettings")) or {}
    settings = _parse_json(inbound.get("settings")) or {}
    host, port = _inbound_host_and_port(server, inbound)
    if not host or not port:
        return None
    email = str(client.get("email") or "").strip()
    if not email:
        return None
    method = str(settings.get("method") or "aes-256-gcm").strip()
    password = str(settings.get("password") or "").strip()
    if not password:
        return None
    userpass = base64.urlsafe_b64encode(f"{method}:{password}".encode("utf-8")).decode("ascii")
    name = f"{inbound.get('remark') or server.get('title') or 'xui'}-ss"
    return f"ss://{userpass}@{host}:{port}#{quote(name, safe='')}"


def _build_direct_links(server: Dict[str, Any], inbound: Dict[str, Any], client: Dict[str, Any]) -> List[str]:
    protocol = (inbound.get("protocol") or "").strip().lower()
    builders = {
        "vless": _vless_link,
        "vmess": _vmess_link,
        "trojan": _trojan_link,
        "shadowsocks": _shadowsocks_link,
    }
    builder = builders.get(protocol)
    if not builder:
        return []
    try:
        link = builder(server, inbound, client)
    except Exception as exc:
        logger.warning("xui direct link build failed for %s: %s", protocol, exc)
        return []
    return [link] if link else []


# ---------------------------------------------------------------------------
# Config link parser + inbound creation from link (هوشمند)
# ---------------------------------------------------------------------------
def parse_config_link(link: str) -> Dict[str, Any]:
    """Parse a client config link (vless/vmess/hysteria2/trojan) into a dict.

    Returns dict with: protocol, uuid/password, host, port, network, security,
    sni, host_header, path, fp, alpn, etc.
    Raises XuiApiError on unsupported / invalid link.
    """
    link = (link or "").strip()
    if not link:
        raise XuiApiError("لینک خالی است.")
    if link.startswith("vless://"):
        return _parse_vless_link(link)
    if link.startswith("vmess://"):
        return _parse_vmess_link(link)
    if link.startswith("hysteria2://") or link.startswith("hy2://"):
        return _parse_hysteria2_link(link)
    if link.startswith("trojan://"):
        return _parse_trojan_link(link)
    if link.startswith("ss://"):
        return _parse_ss_link(link)
    raise XuiApiError(f"پروتکل لینک پشتیبانی نمی‌شود: {link[:20]}")


def _parse_vless_link(link: str) -> Dict[str, Any]:
    # vless://uuid@host:port?params#name
    try:
        u = urlparse(link)
        uuid = u.username or ""
        host = u.hostname or ""
        port = u.port or 443
        qs = dict(urllib.parse.parse_qsl(u.query))
        # hash part is remark, ignore
        return {
            "protocol": "vless",
            "uuid": uuid,
            "host": host,
            "port": int(port),
            "network": (qs.get("type") or "tcp").lower(),
            "security": (qs.get("security") or "none").lower(),
            "sni": qs.get("sni") or "",
            "host_header": qs.get("host") or qs.get("headerType") or host,
            "path": urllib.parse.unquote(qs.get("path") or ""),
            "fp": qs.get("fp") or "chrome",
            "alpn": urllib.parse.unquote(qs.get("alpn") or ""),
            "flow": qs.get("flow") or "",
            "allowInsecure": qs.get("allowInsecure") or qs.get("insecure") or "0",
            "raw_qs": qs,
        }
    except Exception as e:
        raise XuiApiError(f"پارس vless ناموفق: {e}")


def _parse_vmess_link(link: str) -> Dict[str, Any]:
    try:
        b64 = link[8:]  # after vmess://
        # add padding if needed
        b64 += "=" * (-len(b64) % 4)
        data = json.loads(base64.b64decode(b64).decode())
        return {
            "protocol": "vmess",
            "uuid": data.get("id") or "",
            "host": data.get("add") or "",
            "port": int(data.get("port") or 443),
            "network": (data.get("net") or "tcp").lower(),
            "security": "tls" if (data.get("tls") or "none").lower() == "tls" else "none",
            "sni": data.get("sni") or "",
            "host_header": data.get("host") or data.get("add") or "",
            "path": data.get("path") or "",
            "fp": data.get("fp") or "chrome",
            "alpn": data.get("alpn") or "",
            "raw": data,
        }
    except Exception as e:
        raise XuiApiError(f"پارس vmess ناموفق: {e}")


def _parse_hysteria2_link(link: str) -> Dict[str, Any]:
    try:
        # hysteria2://uuid@host:port?obfs=...&sni=...#name  or hy2://
        # urlparse needs to handle hy2:// as scheme
        norm = link.replace("hy2://", "hysteria2://", 1)
        u = urlparse(norm)
        uuid = u.username or ""
        host = u.hostname or ""
        port = u.port or 443
        qs = dict(urllib.parse.parse_qsl(u.query))
        return {
            "protocol": "hysteria2",
            "uuid": uuid,
            "password": uuid,  # hysteria2 uses auth as uuid
            "host": host,
            "port": int(port),
            "obfs": qs.get("obfs") or "none",
            "obfs_password": qs.get("obfs-password") or qs.get("obfs_password") or "",
            "sni": qs.get("sni") or host,
            "insecure": qs.get("insecure") or qs.get("allowInsecure") or "1",
            "raw_qs": qs,
        }
    except Exception as e:
        raise XuiApiError(f"پارس hysteria2 ناموفق: {e}")


def _parse_trojan_link(link: str) -> Dict[str, Any]:
    try:
        u = urlparse(link)
        pwd = u.username or ""
        host = u.hostname or ""
        port = u.port or 443
        qs = dict(urllib.parse.parse_qsl(u.query))
        return {
            "protocol": "trojan",
            "password": pwd,
            "uuid": pwd,
            "host": host,
            "port": int(port),
            "network": (qs.get("type") or "tcp").lower(),
            "security": (qs.get("security") or "tls").lower(),
            "sni": qs.get("sni") or host,
            "host_header": qs.get("host") or host,
            "path": urllib.parse.unquote(qs.get("path") or ""),
            "raw_qs": qs,
        }
    except Exception as e:
        raise XuiApiError(f"پارس trojan ناموفق: {e}")


def _parse_ss_link(link: str) -> Dict[str, Any]:
    raise XuiApiError("shadowsocks از لینک فعلاً پشتیبانی نمی‌شود - دستی بسازید.")


async def create_inbound_from_link(
    server: Dict[str, Any],
    link: str,
    *,
    port_override: Optional[int] = None,
    remark: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new inbound on X-UI panel from a client config link.

    - دامنهٔ اصلی لینک (مثلاً direct.example.com) با دامنهٔ پنل (eu.example.com) جایگزین می‌شود
    - اگر پورت تکراری بود، خطا می‌دهد تا کاربر پورت دیگر بدهد
    - remark اگر داده نشود از hash لینک یا protocol می‌آید
    """
    parsed = parse_config_link(link)
    # دامنه پنل را از server بگیر
    panel_domain = ""
    try:
        origin = _public_origin(server)
        # origin is https://eu.example.com[:port]
        panel_domain = urlparse(origin).hostname or ""
    except Exception:
        panel_domain = ""
    orig_host = (parsed.get("host") or "").strip()
    # اگر دامنه لینک با دامنه پنل فرق داشت، SNI/host را به دامنه پنل تغییر بده
    if panel_domain and orig_host and orig_host.lower() != panel_domain.lower():
        if parsed.get("sni") and parsed["sni"].lower() == orig_host.lower():
            parsed["sni"] = panel_domain
        if parsed.get("host_header") and parsed["host_header"].lower() == orig_host.lower():
            parsed["host_header"] = panel_domain
        # host لینک هم برای ساخت کانفیگ جدید، پورت مقصد پنل است، ولی inbound روی پنل باید روی 0.0.0.0 گوش دهد
        # پس host لینک را نگه می‌داریم برای SNI، ولی inbound host نیازی نیست

    # پورت
    port = int(port_override) if port_override else int(parsed.get("port") or 443)

    # بررسی تکراری بودن پورت (شامل پورت خود پنل)
    inbounds = await _list_inbounds(server)
    used_ports = {_to_int(ib.get("port"), 0) for ib in inbounds}
    # پورت خود پنل (443 یا 2056) را هم جزو اشغالی حساب کن
    try:
        panel_url = str(server.get("panel_url") or "")
        if panel_url:
            pu = urlparse(panel_url)
            panel_port = pu.port or (443 if pu.scheme == "https" else 80)
            if panel_port:
                used_ports.add(panel_port)
    except Exception:
        pass
    if port in used_ports:
        # پیشنهاد پورت جایگزین
        alt = port + 1
        while alt in used_ports and alt < 65535:
            alt += 1
        raise XuiApiError(f"پورت {port} قبلاً استفاده شده (پنل یا اینباند دیگر). پورت دیگری بدهید (مثلاً {alt}).")

    protocol = (parsed.get("protocol") or "").strip().lower()
    # نگاشت پروتکل hysteria2 به hysteria برای X-UI قدیم
    if protocol == "hysteria2":
        protocol = "hysteria2"
        if protocol not in SUPPORTED_PROTOCOLS and "hysteria" in SUPPORTED_PROTOCOLS:
            # اگر نسخه قدیم فقط hysteria می‌شناسد، همان را بگذار
            pass

    # remark
    if not remark:
        try:
            # از hash لینک
            if "#" in link:
                remark = urllib.parse.unquote(link.split("#", 1)[1]).strip()
        except Exception:
            remark = ""
        if not remark:
            remark = f"{protocol}-{port}"

    # ساخت JSON اینباند بر اساس پروتکل
    inbound_json = _build_inbound_json(protocol, port, parsed, remark)
    async with _XuiContext(server) as ctx:
        try:
            resp = await ctx.request("POST", "inbounds/add", json_body=inbound_json)
        except XuiApiError as e:
            msg = str(e).lower()
            # اگر تایم‌اوت بود ولی اینباند ساخته شده، دوباره لیست بگیر
            if "timed out" in msg or "timeout" in msg or "timed" in msg:
                await asyncio.sleep(2)
                try:
                    inbounds2 = await _list_inbounds(server)
                    for ib in inbounds2:
                        if _to_int(ib.get("port"), 0) == port and (ib.get("protocol") or "").lower() == protocol:
                            # به نظر ساخته شده
                            return {"id": ib.get("id"), "port": port, "recovered_after_timeout": True}
                except Exception:
                    pass
            raise
    # resp معمولاً شامل id اینباند جدید است
    return resp if isinstance(resp, dict) else {"raw": resp}


def _build_inbound_json(protocol: str, port: int, parsed: Dict[str, Any], remark: str) -> Dict[str, Any]:
    # پایه مشترک
    base: Dict[str, Any] = {
        "port": port,
        "protocol": protocol,
        "tag": f"inbound-{port}",
        "remark": remark,
        "enable": True,
        "expiryTime": 0,
        "settings": json.dumps({"clients": [], "decryption": "none", "fallbacks": []} if protocol in ("vless", "vmess") else {"clients": [], "password": ""}),
        "streamSettings": json.dumps(_stream_settings_for_parsed(parsed)),
        "sniffing": json.dumps({"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": False}),
    }
    # برای hysteria2 تنظیمات فرق دارد
    if protocol in ("hysteria", "hysteria2"):
        base["settings"] = json.dumps({
            "clients": [],
            "obfs": {"type": parsed.get("obfs") or "salamander", "salamander": {"password": parsed.get("obfs_password") or ""}},
        })
        # streamSettings برای hysteria معمولاً خالی یا با tls
        base["streamSettings"] = json.dumps({
            "network": "udp",
            "security": "tls",
            "tlsSettings": {"serverName": parsed.get("sni") or "", "alpn": ["h3"], "certificates": [{"ocspStapling": 3600}]},
        })
    return base


def _stream_settings_for_parsed(parsed: Dict[str, Any]) -> Dict[str, Any]:
    network = (parsed.get("network") or "tcp").lower()
    security = (parsed.get("security") or "none").lower()
    out: Dict[str, Any] = {"network": network, "security": security}
    if security == "tls":
        tls: Dict[str, Any] = {
            "serverName": parsed.get("sni") or parsed.get("host") or "",
            "alpn": [a.strip() for a in (parsed.get("alpn") or "h2,http/1.1").split(",") if a.strip()] or ["h2", "http/1.1"],
            "fingerprint": parsed.get("fp") or "chrome",
            "certificates": [
                {
                    "certificateFile": "/root/cert/eu.example.com/fullchain.pem",
                    "keyFile": "/root/cert/eu.example.com/privkey.pem",
                    "ocspStapling": 3600,
                }
            ],
        }
        out["tlsSettings"] = tls
    elif security == "reality":
        out["realitySettings"] = {
            "serverNames": [parsed.get("sni") or ""],
            "privateKey": "",
            "publicKey": "",
            "shortIds": [""],
        }
    # network-specific
    if network == "ws":
        out["wsSettings"] = {
            "path": parsed.get("path") or "/",
            "headers": {"Host": parsed.get("host_header") or ""},
        }
    elif network == "httpupgrade":
        out["httpupgradeSettings"] = {
            "path": parsed.get("path") or "/",
            "host": parsed.get("host_header") or "",
        }
    elif network == "grpc":
        out["grpcSettings"] = {"serviceName": parsed.get("path") or "", "multiMode": False}
    return out


async def sync_users_to_inbounds(server: Dict[str, Any]) -> Dict[str, Any]:
    """همگام‌سازی یوزرها روی همه اینباندهای هدف (برای وقتی اینباند جدید ساخته شد).

    هر کاربری که در حداقل یک اینباند وجود دارد، در همه اینباندهای هدف (0=همه یا لیست مشخص)
    ساخته می‌شود (با همان UUID/subId، email یونیک per inbound).
    """
    inbounds = await _list_inbounds(server)
    targets = _resolve_target_inbounds(server, inbounds)
    if not targets:
        return {"ok": False, "msg": "هیچ اینباند هدف یافت نشد.", "created": 0, "skipped": 0}

    # همه یوزرهای یکتا (بر اساس uuid/subId) از کل پنل
    all_users = await list_users(server)
    # dedup by uuid (list_users may return duplicates per inbound for multi-inbound users, but after our fix it still returns per inbound)
    # بهتر است از خود inbounds همه کلاینت‌ها را جمع کنیم
    seen_uuid: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}  # uuid -> (inbound, client) نمونه
    for ib in inbounds:
        for cl in _settings_clients(ib.get("settings")):
            # uuid را از id/password/email/subId بگیر
            proto = (ib.get("protocol") or "").lower()
            uid = _client_key_value(cl, proto) or str(cl.get("subId") or cl.get("email") or "").strip()
            if not uid:
                continue
            low = uid.lower()
            if low not in seen_uuid:
                seen_uuid[low] = (ib, cl)

    created = 0
    skipped = 0
    errors: List[str] = []

    # برای هر یوزر، چک کن در کدام تارگت‌ها نیست
    for uuid_key, (sample_ib, sample_client) in seen_uuid.items():
        # uuid اصلی برای ساب
        # sample_client را به عنوان تمپلیت برای تنظیمات quota/expiry استفاده کن
        for target_ib in targets:
            # آیا این یوزر در این تارگت وجود دارد؟
            found = False
            for cl in _settings_clients(target_ib.get("settings")):
                hay = (
                    str(cl.get("email") or "").lower(),
                    str(cl.get("subId") or "").lower(),
                    str(cl.get("id") or "").lower(),
                    str(cl.get("password") or "").lower(),
                )
                if uuid_key in hay or str(sample_client.get("subId") or "").lower() in hay:
                    found = True
                    break
            if found:
                skipped += 1
                continue
            # نیست → بساز در این تارگت با همان uuid
            try:
                proto = (target_ib.get("protocol") or "").lower()
                # از sample_client به عنوان template برای totalGB/expiry
                # ولی برای inbound جدید، باید client جدید بسازی با همان uuid
                # از _new_client_dict با template خالی یا sample
                # برای حفظ quota/expiry، از sample_client بگیر
                tgt_clients = _settings_clients(target_ib.get("settings"))
                template = tgt_clients[0] if tgt_clients else {}
                # اگر sample_client وجود دارد، مقادیر quota/expiry را از آن بردار
                new_client = _new_client_dict(proto, uuid=uuid_key, template=template)
                # کپی quota/expiry/enable از sample
                for k in ("totalGB", "expiryTime", "enable", "limitIp"):
                    if k in sample_client:
                        new_client[k] = sample_client[k]
                # email یونیک
                base_email = str(sample_client.get("email") or uuid_key).strip()
                # اگر قبلاً email یونیک بود (uuid-1), base را از قبل بگیر
                # ساده: اگر چنداینبانده بود، email را base + -inboundId کن
                # برای هماهنگی با create_user: اولی base، بقیه base-inboundId
                # اینجا چون target مشخص است، اگر این یوزر قبلاً در یک inbound با email base وجود داشت، برای inbound جدید باید base-inboundId باشد
                # تشخیص: اگر uuid_key == base_email (یعنی email==uuid), آنگاه برای target جدید باید base_email-inboundId باشد
                # ساده‌تر: همیشه برای sync، email = base_email اگر target اولین تارگت بود، وگرنه base_email-inboundId
                # ولی برای سادگی، اگر target != sample_ib, suffix بزن
                if target_ib.get("id") != sample_ib.get("id"):
                    # For multi-inbound sync, keep full base_email unique per user
                    # (base_email itself is already unique panel-wide, e.g. vpn-773159)
                    # and suffix with target inbound id to keep inbound-unique.
                    # Do NOT strip trailing -number (that caused vpn-12 collisions for all users).
                    new_client["email"] = f"{base_email}-{int(target_ib.get('id') or 0)}"
                else:
                    new_client["email"] = base_email
                new_client["subId"] = sample_client.get("subId") or uuid_key
                new_client["tgId"] = sample_client.get("tgId") or base_email.split("-")[0]
                settings_body = json.dumps({"clients": [new_client]})
                async with _XuiContext(server) as ctx:
                    await ctx.request(
                        "POST",
                        "inbounds/addClient",
                        json_body={"id": int(target_ib.get("id") or 0), "settings": settings_body},
                    )
                created += 1
            except Exception as e:
                errors.append(f"{uuid_key}→{target_ib.get('id')}: {e}")

    return {"ok": True, "created": created, "skipped": skipped, "errors": errors, "total_users": len(seen_uuid), "target_inbounds": len(targets)}