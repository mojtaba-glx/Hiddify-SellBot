# Shared/sub_links.py
# ساخت «لینک اشتراک هوشمند» (managed subscription link) مشترک بین
# ربات مشتری و ربات نمایندگی، طبق همان منطق CustomerBot.

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from Shared import agent_db, database, multi_panel, userbot_db

logger = logging.getLogger(__name__)


def _build_user_base_url(server: dict, user_uuid: str) -> Optional[str]:
    panel_url = str(server.get("panel_url") or "").rstrip("/")
    user_proxy = str(server.get("user_proxy_path") or "").strip("/")
    if not panel_url or not user_proxy or not user_uuid:
        return None

    base_url = f"{panel_url}/{user_proxy}/{user_uuid}"
    domains = server.get("domains") or []
    if not domains:
        return base_url

    best_score = -10 ** 9
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


def _build_panel_base_url(server: dict, user_uuid: str) -> Optional[str]:
    panel_url = str(server.get("panel_url") or "").rstrip("/")
    user_proxy = str(server.get("user_proxy_path") or "").strip("/")
    if not panel_url or not user_proxy or not user_uuid:
        return None
    return f"{panel_url}/{user_proxy}/{user_uuid}"


def get_service_node_base_urls(svc: dict) -> List[str]:
    """آدرس base_url سرویس روی همه نودهای نگاشت‌شده + سرور اصلی"""
    out: List[str] = []
    seen: set = set()
    try:
        service_id = int(svc.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0
    mappings = agent_db.get_service_nodes(service_id) if service_id > 0 else []
    if mappings:
        active_mappings = [m for m in mappings if int((m or {}).get("is_active") or 0) == 1]
        if active_mappings:
            mappings = active_mappings
        else:
            return []
    try:
        primary_server_id = int(svc.get("server_id") or 0)
    except (TypeError, ValueError):
        primary_server_id = 0
    mappings = sorted(mappings, key=lambda m: int((m or {}).get("server_id") or 0) == primary_server_id)
    for m in mappings:
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        server = database.get_server_by_id(sid)
        if not server:
            continue
        for base in (_build_user_base_url(server, uuid), _build_panel_base_url(server, uuid)):
            if not base or base in seen:
                continue
            out.append(base)
            seen.add(base)
    if not out:
        try:
            sid = int(svc.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        server = database.get_server_by_id(sid) if sid else None
        uuid = str(svc.get("panel_user_uuid") or "").strip()
        if server and uuid:
            for base in (_build_user_base_url(server, uuid), _build_panel_base_url(server, uuid)):
                if not base or base in seen:
                    continue
                out.append(base)
                seen.add(base)
    return out


def _resolve_sub_service_base_url(svc: Optional[dict] = None) -> str:
    custom_base = str(userbot_db.get_managed_sub_base_url() or "").strip().rstrip("/")
    if custom_base:
        return custom_base

    env_base = str(os.getenv("SUB_SERVICE_BASE_URL", "") or "").strip().rstrip("/")
    if env_base:
        return env_base

    explicit = str(os.getenv("SUB_SERVER_PUBLIC_HOST", "") or "").strip().rstrip("/")
    if explicit:
        try:
            parsed = urlparse(explicit if "://" in explicit else f"//{explicit}")
            host = (parsed.hostname or "").strip()
            scheme = (parsed.scheme or os.getenv("SUB_SERVER_PUBLIC_SCHEME", "https") or "https").strip().lower()
            port = parsed.port if parsed.port is not None else int(os.getenv("SUB_SERVER_PUBLIC_PORT", "8787") or "8787")
            if host:
                default_port = (scheme == "https" and int(port) == 443) or (scheme == "http" and int(port) == 80)
                if default_port:
                    return f"{scheme}://{host}"
                return f"{scheme}://{host}:{int(port)}"
        except Exception:
            pass

    if svc:
        base_urls = get_service_node_base_urls(svc)
        if base_urls:
            try:
                p = urlparse(base_urls[0])
                host = (p.hostname or "").strip()
                if host:
                    scheme = (os.getenv("SUB_SERVER_PUBLIC_SCHEME", "https") or "https").strip().lower()
                    port = int(os.getenv("SUB_SERVER_PUBLIC_PORT", "8787") or "8787")
                    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
                    if default_port:
                        return f"{scheme}://{host}"
                    return f"{scheme}://{host}:{int(port)}"
            except Exception:
                pass
    return ""


def get_or_create_bot_sub_links(svc: dict) -> Tuple[str, str]:
    """(لینک اشتراک هوشمند، لینک b64) مشابه ربات کاربران"""
    base = _resolve_sub_service_base_url(svc)
    if not base:
        base_urls = get_service_node_base_urls(svc)
        if base_urls:
            base = base_urls[0].rstrip("/")
    service_uuid = str(svc.get("panel_user_uuid") or "").strip()
    if service_uuid:
        return (
            f"{base}/sub/{service_uuid}/all.txt",
            f"{base}/sub/{service_uuid}/all.txt?base64=1",
        )
    token = str(svc.get("id") or "").strip()
    return f"{base}/sub/{token}/all.txt", f"{base}/sub/{token}/all.txt?base64=1"

# ---------------------------------------------------------------------------
# کانفیگ‌های مستقیم (مثل CustomerBot)
# ---------------------------------------------------------------------------

def get_service_panel_targets(svc: dict) -> List[Tuple[dict, str, str]]:
    """لیست (server, uuid, marzban_username) برای همه نودهای سرویس"""
    targets: List[Tuple[dict, str, str]] = []
    seen: set = set()
    try:
        service_id = int(svc.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0
    mappings = agent_db.get_service_nodes(service_id) if service_id > 0 else []
    for m in mappings:
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        srv = database.get_server_by_id(sid)
        if not srv:
            continue
        key = (sid, uuid)
        if key in seen:
            continue
        seen.add(key)
        targets.append((srv, uuid, str(m.get("marzban_username") or "").strip()))
    if targets:
        return targets
    try:
        sid = int(svc.get("server_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    uuid = str(svc.get("panel_user_uuid") or "").strip()
    srv = database.get_server_by_id(sid) if sid > 0 else None
    if srv and uuid:
        targets.append((srv, uuid, ""))
    return targets


def _sanitize_config_text(value: Any) -> str:
    text = str(value or "").strip()
    for ch in ("\ufeff", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c"):
        text = text.replace(ch, "")
    return text.strip()


def _extract_config_link_from_line(value: Any) -> str:
    raw = _sanitize_config_text(value)
    if not raw:
        return ""
    raw = raw.rstrip("'\",;)]}")
    if "://" not in raw:
        return ""
    if re.search(r"\s", raw):
        return ""
    m = re.match(r"(?i)^([a-z][a-z0-9.+\-]{1,24})://", raw)
    if not m:
        return ""
    scheme = str(m.group(1) or "").lower()
    blocked = {"http", "https", "hiddify", "ftp", "file", "mailto", "tg"}
    if scheme in blocked:
        return ""
    if len(raw) < 16:
        return ""
    return raw


def _fetch_remote_lines(url: str) -> List[str]:
    raw_url = str(url or "").strip()
    if not raw_url:
        return []

    def _decode_to_lines(body_text: str) -> List[str]:
        lines = [_sanitize_config_text(ln) for ln in body_text.splitlines() if _sanitize_config_text(ln)]
        if lines and any("://" in ln for ln in lines):
            return lines
        compact = re.sub(r"\s+", "", body_text or "")
        candidates = [body_text.strip(), compact]
        for cand in candidates:
            cand = str(cand or "").strip()
            if not cand:
                continue
            for decoder in (base64.b64decode, base64.urlsafe_b64decode):
                try:
                    padded = cand + ("=" * ((4 - len(cand) % 4) % 4))
                    decoded = decoder(padded).decode("utf-8", errors="ignore")
                    decoded_lines = [_sanitize_config_text(ln) for ln in decoded.splitlines() if _sanitize_config_text(ln)]
                    if decoded_lines:
                        return decoded_lines
                except Exception:
                    continue
        return lines

    urls_to_try = [raw_url]
    low = raw_url.lower()
    if "/all.txt" in low and "asn=" not in low:
        sep = "&" if "?" in raw_url else "?"
        urls_to_try.append(f"{raw_url}{sep}asn=unknown")

    user_agents = [
        "HiddifyNext/1.0",
        "ClashMetaForAndroid/2.11.5",
        "v2rayN/6.45",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    base_headers = {
        "Accept": "text/plain,*/*;q=0.9",
        "Accept-Language": "en-US,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    for target in urls_to_try:
        for ua in user_agents:
            try:
                req = Request(target, headers={**base_headers, "User-Agent": ua})
                with urlopen(req, timeout=15) as resp:
                    raw = resp.read()
                body = raw.decode("utf-8", errors="ignore")
                parsed = _decode_to_lines(body)
                if parsed:
                    return parsed
            except HTTPError:
                continue
            except Exception:
                continue
    return []


def collect_all_direct_configs_for_service(svc: dict) -> List[str]:
    """جمع‌آوری کانفیگ‌های مستقیم از لینک‌های all.txt همه نودها"""
    out: List[str] = []
    seen_links: set = set()
    for base_url in get_service_node_base_urls(svc):
        seen_lines: set = set()
        for suffix in ("all.txt", "all.txt?base64=1"):
            lines = _fetch_remote_lines(f"{base_url}/{suffix}")
            for ln in lines:
                raw = _sanitize_config_text(ln)
                if not raw or raw in seen_lines:
                    continue
                seen_lines.add(raw)
                link = _extract_config_link_from_line(raw)
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                out.append(link)
    return out


async def collect_all_direct_configs_from_api(svc: dict) -> List[str]:
    """پشتیبان: دریافت کانفیگ‌ها از API پنل برای همه نودها"""
    out: List[str] = []
    seen: set = set()
    for srv, uuid, marzban_un in get_service_panel_targets(svc):
        try:
            configs = await multi_panel.get_user_configs(srv, uuid, marzban_username=marzban_un)
            for item in configs or []:
                link = str(item.get("link") or "").strip()
                if link and link not in seen:
                    seen.add(link)
                    out.append(link)
        except Exception as e:
            logger.warning("API config fallback failed svc=%s node=%s: %s", svc.get("id"), uuid[:8], e)
    return out
