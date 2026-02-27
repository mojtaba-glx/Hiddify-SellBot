import base64
import asyncio
from typing import List, Optional
from urllib.request import Request, urlopen

from Shared import database, userbot_db, hiddify_api


ALLOWED_CONFIG_SCHEMES = (
    "vless://",
    "vmess://",
    "trojan://",
    "ss://",
    "ssr://",
    "tuic://",
    "hysteria://",
    "hysteria2://",
    "hy2://",
    "wireguard://",
)


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _service_lock_reason(service: dict) -> Optional[str]:
    if not service:
        return "service_not_found"

    usage_current = _to_float(service.get("usage_current"), 0.0)
    usage_limit = _to_float(service.get("usage_limit"), 0.0)
    if usage_limit > 0 and usage_current >= usage_limit:
        return "usage_limit_reached"

    try:
        days_left = int(service.get("days_left"))
    except Exception:
        days_left = None
    if days_left is not None and days_left < 0:
        return "time_expired"

    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0
    if service_id > 0:
        mappings = userbot_db.get_service_nodes(service_id)
        if mappings and not any(int((m or {}).get("is_active") or 0) == 1 for m in mappings):
            return "nodes_inactive"

    return None


def get_service_lock_reason(service_id: int) -> Optional[str]:
    service = userbot_db.get_service_by_id(int(service_id))
    return _service_lock_reason(service or {})


def _is_config_line(line: str) -> bool:
    low = str(line or "").strip().lower()
    return any(low.startswith(s) for s in ALLOWED_CONFIG_SCHEMES)


def _build_user_base_url(server: dict, user_uuid: str) -> Optional[str]:
    panel_url = (server.get("panel_url") or "").rstrip("/")
    user_proxy = (server.get("user_proxy_path") or "").strip("/")
    if not panel_url or not user_proxy or not user_uuid:
        return None

    base_url = f"{panel_url}/{user_proxy}/{user_uuid}"
    domains = server.get("domains") or []
    if not domains:
        return base_url

    best_score = -10**9
    display_domain = ""
    for d in domains:
        if isinstance(d, dict):
            raw_domain = (d.get("domain") or d.get("host") or d.get("url") or "").strip()
            title = (d.get("title") or d.get("name") or "").strip().lower()
        else:
            raw_domain = str(d).strip()
            title = ""
        if not raw_domain:
            continue
        low = raw_domain.lower()
        score = 0
        if "user." in low or low.startswith("user"):
            score += 50
        if "sub" in low:
            score += 15
        if "ساب" in title or "user" in title:
            score += 10
        if "dl." in low or low.startswith("dl"):
            score -= 10
        if score > best_score:
            best_score = score
            display_domain = raw_domain

    if not display_domain:
        return base_url
    if not (display_domain.startswith("http://") or display_domain.startswith("https://")):
        display_domain = "https://" + display_domain
    return base_url.replace(panel_url, display_domain.rstrip("/"), 1)


def _fetch_lines(url: str) -> List[str]:
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (HiddifySellBot-SubAggregator)",
                "Accept": "*/*",
            },
        )
        with urlopen(req, timeout=12) as resp:
            raw = resp.read()
        body = raw.decode("utf-8", errors="ignore")
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        if lines and any("://" in ln for ln in lines):
            return lines
        try:
            decoded = base64.b64decode(body.strip() + "===", validate=False).decode("utf-8", errors="ignore")
            return [ln.strip() for ln in decoded.splitlines() if ln.strip()]
        except Exception:
            return lines
    except Exception:
        return []


def _fetch_subscription_lines(base_url: str) -> List[str]:
    """
    Try common Hiddify subscription endpoints in order and return first non-empty set.
    """
    candidates = [
        f"{base_url}/hiddify.txt",
        f"{base_url}/all.txt",
        f"{base_url}/sub/?format=base64",
    ]
    for url in candidates:
        lines = _fetch_lines(url)
        if lines:
            return lines
    return []


def _extract_uuid_from_comment(comment: str) -> str:
    raw = str(comment or "")
    for part in raw.split("|"):
        p = part.strip()
        if p.startswith("uuid:"):
            return p.split(":", 1)[1].strip()
    return ""


def _service_base_urls(service: dict) -> List[str]:
    out: List[str] = []
    seen = set()

    service_id = int(service.get("id") or 0)
    mappings = userbot_db.get_service_nodes(service_id) if service_id > 0 else []
    if mappings:
        active_mappings = [m for m in mappings if int((m or {}).get("is_active") or 0) == 1]
        if active_mappings:
            mappings = active_mappings
        else:
            return []
    for m in mappings:
        sid = int(m.get("server_id") or 0)
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        srv = database.get_server_by_id(sid)
        if not srv:
            continue
        base = _build_user_base_url(srv, uuid)
        if not base or base in seen:
            continue
        out.append(base)
        seen.add(base)

    if not out:
        sid = int(service.get("server_id") or 0)
        srv = database.get_server_by_id(sid) if sid else None
        uuid = _extract_uuid_from_comment(service.get("comment") or "")
        if srv and uuid:
            base = _build_user_base_url(srv, uuid)
            if base:
                out.append(base)
    return out


def _service_targets(service: dict) -> List[dict]:
    """
    Build targets with server/uuid/base so we can fallback to admin API per node.
    """
    out: List[dict] = []
    seen = set()

    service_id = int(service.get("id") or 0)
    mappings = userbot_db.get_service_nodes(service_id) if service_id > 0 else []
    if mappings:
        active_mappings = [m for m in mappings if int((m or {}).get("is_active") or 0) == 1]
        if active_mappings:
            mappings = active_mappings
        else:
            return []
    for m in mappings:
        sid = int(m.get("server_id") or 0)
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        srv = database.get_server_by_id(sid)
        if not srv:
            continue
        base = _build_user_base_url(srv, uuid)
        key = (sid, uuid)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "server_id": sid,
                "server": srv,
                "uuid": uuid,
                "base_url": base,
            }
        )

    if not out:
        sid = int(service.get("server_id") or 0)
        srv = database.get_server_by_id(sid) if sid else None
        uuid = _extract_uuid_from_comment(service.get("comment") or "")
        if srv and uuid:
            out.append(
                {
                    "server_id": sid,
                    "server": srv,
                    "uuid": uuid,
                    "base_url": _build_user_base_url(srv, uuid),
                }
            )
    return out


def _fetch_lines_from_admin_api(server: dict, user_uuid: str) -> List[str]:
    """
    Fallback: fetch user configs via admin API and extract direct links.
    """
    try:
        configs = asyncio.run(hiddify_api.get_user_configs(server, user_uuid))
    except Exception:
        return []
    lines: List[str] = []
    for item in configs or []:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if "://" in link:
            lines.append(link)
    return lines


def build_subscription_text_for_service(service_id: int) -> str:
    service = userbot_db.get_service_by_id(int(service_id))
    if not service:
        return ""
    if _service_lock_reason(service):
        return ""
    lines: List[str] = []
    seen = set()
    for target in _service_targets(service):
        base = target.get("base_url")
        fetched: List[str] = []
        if base:
            fetched = _fetch_subscription_lines(base)
        if not fetched:
            fetched = _fetch_lines_from_admin_api(
                target.get("server") or {},
                str(target.get("uuid") or ""),
            )
        for ln in fetched:
            if not _is_config_line(ln):
                continue
            if ln in seen:
                continue
            seen.add(ln)
            lines.append(ln)
    return "\n".join(lines)


def build_subscription_b64_for_service(service_id: int) -> str:
    txt = build_subscription_text_for_service(service_id)
    if not txt:
        return ""
    return base64.b64encode(txt.encode("utf-8")).decode("ascii")
