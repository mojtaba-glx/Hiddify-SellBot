"""
Shared/xui_common.py
====================
Common helpers shared between Alireza and Sanaei X-UI adapters.
No HTTP calls here - only pure data helpers, parsing and building.
"""

from __future__ import annotations

import base64
import json
import random
import re
import uuid as _uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
from urllib.parse import urlparse

GB = 1024 ** 3
_MS = 1000

SUPPORTED_PROTOCOLS = ("vless", "trojan", "vmess", "shadowsocks", "hysteria", "hysteria2")

_ONLINE_EMAIL_RE = re.compile(r"email\s*[:=]\s*['\"]?([^'\",}\]]+)", re.IGNORECASE)


def uuid4() -> str:
    return str(_uuid_mod.uuid4())


# ---------------------------------------------------------------------------
# Email / uniqueness
# ---------------------------------------------------------------------------

def _sanitize_xui_email(raw: str, fallback: str) -> str:
    """ساخت email ایمن برای X-UI Sanaei/Alireza — فقط a-zA-Z0-9._- مجاز است.
    فارسی/عربی به لاتین تبدیل و ایموجی/فاصله/کاراکتر نامعتبر به '-' تبدیل می‌شود.
    Sanaei خطا می‌دهد اگر email حاوی فارسی/ایموجی/فاصله باشد (نمونه: '🖥 pc5100')."""
    text = str(raw or "").strip()
    fallback = str(fallback or "").strip()
    if not text:
        return fallback

    # نگاشت فارسی/عربی به لاتین برای حفظ یکتایی و خوانایی
    _fa_map = {
        'ا': 'a', 'آ': 'a', 'أ': 'a', 'إ': 'e', 'ب': 'b', 'پ': 'p', 'ت': 't', 'ث': 's',
        'ج': 'j', 'چ': 'ch', 'ح': 'h', 'خ': 'kh', 'د': 'd', 'ذ': 'z', 'ر': 'r', 'ز': 'z',
        'ژ': 'zh', 'س': 's', 'ش': 'sh', 'ص': 's', 'ض': 'z', 'ط': 't', 'ظ': 'z',
        'ع': 'a', 'غ': 'gh', 'ف': 'f', 'ق': 'gh', 'ک': 'k', 'ك': 'k', 'گ': 'g',
        'ل': 'l', 'م': 'm', 'ن': 'n', 'و': 'v', 'ه': 'h', 'ة': 'h', 'ی': 'y', 'ي': 'y',
        'ى': 'y', 'ئ': 'e', 'ؤ': 'o', 'ء': '', '‌': '-', 'ـ': '', ' ': '-', '\t': '-',
        '\n': '-', '\r': '-',
    }

    # مرحله ۱: تبدیل هر کاراکتر
    tmp_chars = []
    for ch in text:
        if ch in _fa_map:
            tmp_chars.append(_fa_map[ch])
        elif '0' <= ch <= '9' or 'a' <= ch <= 'z' or 'A' <= ch <= 'Z' or ch in ('-', '_', '.', '@'):
            tmp_chars.append(ch)
        elif ord(ch) < 128:
            # سایر ascii قابل چاپ (مثل فاصله، ایموجی ascii) -> به '-' تبدیل
            if ch.isspace():
                tmp_chars.append('-')
            elif ch.isprintable():
                tmp_chars.append('-')
            else:
                tmp_chars.append('-')
        else:
            # فارسی/عربی که در map نبود یا ایموجی/سایر unicode -> حذف یا '-'
            # اگر قبلا چیزی اضافه کردیم و آخرین کاراکتر '-' نیست، یک '-' اضافه کن تا جداسازی حفظ شود
            if tmp_chars and tmp_chars[-1] != '-':
                tmp_chars.append('-')
            # در غیر این صورت حذف
            continue

    text = ''.join(tmp_chars)

    # مرحله ۲: پاکسازی regex
    # فقط a-zA-Z0-9._- و @ نگه دار، بقیه به '-'
    text = re.sub(r'[^a-zA-Z0-9._@-]', '-', text)
    # ادغام '-' و '_' و '.' تکراری
    text = re.sub(r'[-_]{2,}', '-', text)
    text = re.sub(r'\.{2,}', '.', text)
    # حذف '-' و '.' و '_' از ابتدا و انتها
    text = text.strip('-._@')
    # اگر @ دارد، فقط بخش قبل @ را نگه دار (X-UI email نیازی به دامنه ندارد)
    if '@' in text:
        text = text.split('@')[0]

    if not text or len(text) < 2:
        return fallback

    # طول مجاز 64
    if len(text) > 64:
        text = text[:64].strip('-._')

    # نباید با عدد خالی یا '-' شروع شود؛ اگر با '-' یا '.' شروع شد، پیشوند اضافه کن
    if text and not re.match(r'^[a-zA-Z0-9]', text):
        text = f"u-{text}"
        if len(text) > 64:
            text = text[:64].strip('-._')

    # اگر بعد از همه هنوز نامعتبر یا خالی است، fallback
    if not text or not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$', text):
        return fallback

    return text


def _existing_xui_emails(inbounds: List[Dict[str, Any]]) -> set:
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
    if base not in existing and base.lower() not in existing and not _bot_has_service_name(base):
        return base
    for _ in range(12):
        rnd = str(random.randint(100, 9999))
        max_len = 64 - len(rnd)
        b = base[:max_len] if len(base) > max_len else base
        cand = f"{b}{rnd}"
        if cand not in existing and cand.lower() not in existing and not _bot_has_service_name(cand):
            return cand
    suffix = str(fallback or uuid4())[:8]
    max_len = 64 - len(suffix) - 1
    b = base[:max_len] if len(base) > max_len else base
    cand = f"{b}-{suffix}"
    if cand not in existing and cand.lower() not in existing and not _bot_has_service_name(cand):
        return cand
    return f"{fallback}"


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


def _new_client_dict(protocol: str, *, uuid: str, template: Dict[str, Any]) -> Dict[str, Any]:
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
    raw_name = str(payload.get("name") or "").strip()
    if raw_name:
        client["tgId"] = raw_name
    # Handle comment separately (do not overwrite tgId when only comment changes)
    if "comment" in payload:
        client["comment"] = str(payload.get("comment") or "").strip()
    client.setdefault("totalGB", 0)
    client.setdefault("expiryTime", 0)
    client.setdefault("enable", True)
    client.setdefault("subId", client.get("subId") or client.get("email") or "")
    client.setdefault("tgId", client.get("tgId") or "")
    client.setdefault("comment", client.get("comment") or "")
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
        "comment": str(client.get("comment") or client.get("tgId") or client.get("remark") or "").strip(),
        "_source": "xui",
    }


# ---------------------------------------------------------------------------
# Server helpers
# ---------------------------------------------------------------------------

def is_xui_server(server: Dict[str, Any]) -> bool:
    return str((server or {}).get("panel_type") or "").strip().lower() in {"xui", "x-ui"}


def _get_panel_url(server: Dict[str, Any]) -> str:
    url = str((server or {}).get("panel_url") or "").strip().rstrip("/")
    if not url:
        raise ValueError("panel_url برای سرور X-UI تنظیم نشده است.")
    return url


def _public_origin(server: Dict[str, Any]) -> str:
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


def _get_api_token(server: Dict[str, Any]) -> str:
    for key in ("xui_api_token", "xui_token", "api_token", "xui_secret"):
        val = str((server or {}).get(key) or "").strip()
        if val:
            return val
    return ""


def _is_sanaei_token_auth(server: Dict[str, Any]) -> bool:
    return bool(_get_api_token(server))


def _inbound_id(server: Dict[str, Any]) -> Optional[int]:
    try:
        raw = str((server or {}).get("xui_inbound_id") or "").strip()
        if not raw or raw == "0" or "," in raw:
            return None
        return int(raw or 0) or None
    except (TypeError, ValueError):
        return None


def _target_inbound_ids(server: Dict[str, Any]) -> Optional[List[int]]:
    raw = str((server or {}).get("xui_inbound_id") or "").strip()
    if not raw:
        return None
    raw = raw.replace("،", ",")
    if raw.strip() == "0":
        return []
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
    if not inbounds:
        return []
    wanted = _target_inbound_ids(server)
    if wanted is None:
        ib = _select_sell_inbound(inbounds, server)
        return [ib] if ib else []
    if wanted == []:
        return _candidate_inbounds(inbounds)
    by_id = {_to_int(ib.get("id"), 0): ib for ib in inbounds}
    out: List[Dict[str, Any]] = []
    for nid in wanted:
        ib = by_id.get(nid)
        if ib and bool(ib.get("enable", True)):
            out.append(ib)
        else:
            raise ValueError(f"اینباند با شناسه {nid} در پنل X-UI یافت نشد یا غیرفعال است.")
    return out


def _select_sell_inbound(inbounds: List[Dict[str, Any]], server: Dict[str, Any]) -> Dict[str, Any]:
    pinned = _inbound_id(server)
    if pinned is not None:
        for ib in inbounds or []:
            if _to_int(ib.get("id"), 0) == pinned:
                return ib
        raise ValueError(f"اینباند با شناسه {pinned} در پنل X-UI یافت نشد یا غیرفعال است.")
    candidates = _candidate_inbounds(inbounds)
    if not candidates:
        raise ValueError("هیچ اینباند فعالِ قابل‌فروش روی پنل X-UI یافت نشد.")
    return candidates[0]


# ---------------------------------------------------------------------------
# Direct link helpers
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
    return "&".join(f"{k}={urllib.parse.quote(str(v or ''), safe='')}" for k, v in pairs if v)


# ---------------------------------------------------------------------------
# Subscription body decode
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


def _find_all_clients_for_uuid(
    inbounds: List[Dict[str, Any]], user_uuid: str
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
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
