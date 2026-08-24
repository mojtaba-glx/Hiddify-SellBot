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
    if not user_uuid:
        return None
    # X-UI: native subscription URL
    try:
        from Shared import xui_api
        if xui_api.is_xui_server(server):
            origin = xui_api._public_origin(server)
            sub_path = xui_api._sub_path(server)
            if origin and sub_path:
                return f"{origin.rstrip('/')}{sub_path}{user_uuid}"
    except Exception:
        pass
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
    """آدرس base_url سرویس روی همه نودهای نگاشت‌شده + سرور اصلی + نودهای زیرمجموعه"""
    return get_service_user_base_urls(svc) or _get_service_node_base_urls(svc)


def _get_service_node_base_urls(svc: dict) -> List[str]:
    """پیاده‌سازی قبلی: user + panel هر دو (برای compatibility برخی مصرف‌کننده‌ها)."""
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

    def _add_server(sid: int, uuid: str) -> None:
        if sid <= 0 or not uuid:
            return
        srv = database.get_server_by_id(sid)
        if not srv:
            return
        for base in (_build_user_base_url(srv, uuid), _build_panel_base_url(srv, uuid)):
            if not base or base in seen:
                continue
            out.append(base)
            seen.add(base)

    for m in sorted(
        mappings,
        key=lambda m: (0 if int((m or {}).get("server_id") or 0) == primary_server_id else 1, int((m or {}).get("server_id") or 0)),
    ):
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        _add_server(sid, str(m.get("panel_user_uuid") or "").strip())

    if not out:
        sid = primary_server_id
        uuid = str(svc.get("panel_user_uuid") or "").strip()
        if sid > 0:
            _add_server(sid, uuid)

    # نودهای زیرمجموعه سرور اصلی (server.nodes[] با target_server_id)
    if primary_server_id > 0:
        primary = database.get_server_by_id(primary_server_id)
        child_mappings = {int((m or {}).get("server_id") or 0): (m or {}) for m in mappings}
        for node in (primary.get("nodes") or []):
            if not isinstance(node, dict):
                continue
            try:
                child_sid = int(node.get("target_server_id") or 0)
            except (TypeError, ValueError):
                child_sid = 0
            if child_sid <= 0:
                continue
            # اول uuid mapping سرویس روی این نود، بعد uuid سرویس اصلی
            child_map = child_mappings.get(child_sid) or {}
            child_uuid = str(child_map.get("panel_user_uuid") or "").strip() or str(svc.get("panel_user_uuid") or "").strip()
            _add_server(child_sid, child_uuid)
    return out


def get_service_user_base_urls(svc: dict) -> List[str]:
    """فقط base_url با دامنه کاربر (user domain) برای هر نود — دقیقاً مثل aggregator.

    بدون افزودن raw panel_url، تا همان تنظیمات «شامل در اشتراک» دامنه کاربر
    رعایت شود و کانفیگ‌های مخفی که فقط از دامنه خام پنل قابل‌دسترس‌اند وارد نشوند.
    """
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

    def _add_server(sid: int, uuid: str) -> None:
        if sid <= 0 or not uuid:
            return
        srv = database.get_server_by_id(sid)
        if not srv:
            return
        base = _build_user_base_url(srv, uuid)
        if not base or base in seen:
            return
        out.append(base)
        seen.add(base)

    for m in sorted(
        mappings,
        key=lambda m: (0 if int((m or {}).get("server_id") or 0) == primary_server_id else 1, int((m or {}).get("server_id") or 0)),
    ):
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        _add_server(sid, str(m.get("panel_user_uuid") or "").strip())

    if not out:
        sid = primary_server_id
        uuid = str(svc.get("panel_user_uuid") or "").strip()
        if sid > 0:
            _add_server(sid, uuid)

    # نودهای زیرمجموعه سرور اصلی (server.nodes[] با target_server_id)
    if primary_server_id > 0:
        primary = database.get_server_by_id(primary_server_id)
        if primary:
            child_mappings = {int((m or {}).get("server_id") or 0): (m or {}) for m in mappings}
            for node in (primary.get("nodes") or []):
                if not isinstance(node, dict):
                    continue
                try:
                    child_sid = int(node.get("target_server_id") or 0)
                except (TypeError, ValueError):
                    child_sid = 0
                if child_sid <= 0:
                    continue
                child_map = child_mappings.get(child_sid) or {}
                child_uuid = str(child_map.get("panel_user_uuid") or "").strip() or str(svc.get("panel_user_uuid") or "").strip()
                _add_server(child_sid, child_uuid)
        # reverse: اگر سرویس روی نود است، parent + sibling nodes را هم اضافه کن
        for parent in database.get_servers() or []:
            is_parent = False
            for node in (parent.get("nodes") or []):
                if not isinstance(node, dict):
                    continue
                try:
                    cid = int(node.get("target_server_id") or 0)
                except (TypeError, ValueError):
                    cid = 0
                if cid == primary_server_id:
                    is_parent = True
                    break
            if not is_parent:
                continue
            try:
                pid = int(parent.get("id") or 0)
            except (TypeError, ValueError):
                pid = 0
            if pid <= 0:
                continue
            # parent itself
            parent_map = next((m for m in mappings if int((m or {}).get("server_id") or 0) == pid), None)
            parent_uuid = str((parent_map or {}).get("panel_user_uuid") or "").strip() or str(svc.get("panel_user_uuid") or "").strip()
            _add_server(pid, parent_uuid)
            # sibling nodes
            child_mappings2 = {int((m or {}).get("server_id") or 0): (m or {}) for m in mappings}
            for node in (parent.get("nodes") or []):
                if not isinstance(node, dict):
                    continue
                try:
                    child_sid = int(node.get("target_server_id") or 0)
                except (TypeError, ValueError):
                    child_sid = 0
                if child_sid <= 0 or child_sid == primary_server_id:
                    continue
                child_map = child_mappings2.get(child_sid) or {}
                child_uuid = str(child_map.get("panel_user_uuid") or "").strip() or str(svc.get("panel_user_uuid") or "").strip()
                _add_server(child_sid, child_uuid)
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
    """(لینک اشتراک هوشمند، لینک b64) مشابه ربات کاربران.

    برای سرویس‌های نمایندگی/مشتری token را به فرم ``panel-srv-{server_id}``
    می‌سازد تا اولویت دوم ساب‌سرور (resolve مستقیم از پنل با uuid) کار کند؛
    همین الگو در AdminBot و UserBot استفاده شده.
    """
    base = _resolve_sub_service_base_url(svc)
    if not base:
        base_urls = get_service_node_base_urls(svc)
        if base_urls:
            base = base_urls[0].rstrip("/")
    if not base:
        return "", ""
    service_uuid = str(svc.get("panel_user_uuid") or "").strip()
    try:
        server_id = int(svc.get("server_id") or 0)
    except (TypeError, ValueError):
        server_id = 0
    if service_uuid and server_id > 0:
        token = f"panel-srv-{server_id}"
        return (
            f"{base}/sub/{token}/{service_uuid}/all.txt",
            f"{base}/sub/{token}/{service_uuid}/all.txt?base64=1",
        )
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
    """لیست (server, uuid, marzban_username) برای همه نودهای سرویس + نودهای زیرمجموعه"""
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
    if not targets:
        try:
            sid = int(svc.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(svc.get("panel_user_uuid") or "").strip()
        srv = database.get_server_by_id(sid) if sid > 0 else None
        if srv and uuid:
            seen.add((sid, uuid))
            targets.append((srv, uuid, ""))
    # نودهای زیرمجموعه سرور اصلی (server.nodes[] با target_server_id)
    try:
        primary_sid = int(svc.get("server_id") or 0)
    except (TypeError, ValueError):
        primary_sid = 0
    if primary_sid > 0:
        primary = database.get_server_by_id(primary_sid)
        if primary:
            child_mappings = {int((m or {}).get("server_id") or 0): (m or {}) for m in mappings}
            for node in (primary.get("nodes") or []):
                if not isinstance(node, dict):
                    continue
                try:
                    child_sid = int(node.get("target_server_id") or 0)
                except (TypeError, ValueError):
                    child_sid = 0
                if child_sid <= 0:
                    continue
                child_map = child_mappings.get(child_sid) or {}
                child_uuid = str(child_map.get("panel_user_uuid") or "").strip() or str(svc.get("panel_user_uuid") or "").strip()
                child_un = str(child_map.get("marzban_username") or "").strip()
                child_srv = database.get_server_by_id(child_sid)
                if not child_srv or not child_uuid:
                    continue
                key = (child_sid, child_uuid)
                if key in seen:
                    continue
                seen.add(key)
                targets.append((child_srv, child_uuid, child_un))
        # اگر سرویس روی نود است، خوشه کامل (parent + sibling nodes) را هم برگردان
        # این حالت برای سرویس‌های قدیمی که server_id آنها خودِ نود است پیش می‌آید
        parent_ids: List[int] = []
        for srv in database.get_servers() or []:
            for node in (srv.get("nodes") or []):
                if not isinstance(node, dict):
                    continue
                try:
                    cid = int(node.get("target_server_id") or 0)
                except (TypeError, ValueError):
                    cid = 0
                if cid == primary_sid:
                    try:
                        pid = int(srv.get("id") or 0)
                    except (TypeError, ValueError):
                        pid = 0
                    if pid > 0 and pid not in parent_ids:
                        parent_ids.append(pid)
        for pid in parent_ids:
            parent_srv = database.get_server_by_id(pid)
            if not parent_srv:
                continue
            parent_uuid = str(svc.get("panel_user_uuid") or "").strip()
            # mapping برای parent اگر وجود داشت
            parent_map = next((m for m in mappings if int((m or {}).get("server_id") or 0) == pid), None)
            if parent_map:
                parent_uuid = str(parent_map.get("panel_user_uuid") or "").strip() or parent_uuid
                parent_un = str(parent_map.get("marzban_username") or "").strip()
            else:
                parent_un = ""
            key = (pid, parent_uuid)
            if parent_uuid and key not in seen:
                seen.add(key)
                targets.append((parent_srv, parent_uuid, parent_un))
            # sibling nodes of this parent
            child_mappings2 = {int((m or {}).get("server_id") or 0): (m or {}) for m in mappings}
            for node in (parent_srv.get("nodes") or []):
                if not isinstance(node, dict):
                    continue
                try:
                    child_sid = int(node.get("target_server_id") or 0)
                except (TypeError, ValueError):
                    child_sid = 0
                if child_sid <= 0 or child_sid == primary_sid:
                    continue
                child_map = child_mappings2.get(child_sid) or {}
                child_uuid = str(child_map.get("panel_user_uuid") or "").strip() or str(svc.get("panel_user_uuid") or "").strip()
                child_un = str(child_map.get("marzban_username") or "").strip()
                child_srv = database.get_server_by_id(child_sid)
                if not child_srv or not child_uuid:
                    continue
                key = (child_sid, child_uuid)
                if key in seen:
                    continue
                seen.add(key)
                targets.append((child_srv, child_uuid, child_un))
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
    """جمع‌آوری کانفیگ‌های مستقیم از لینک‌های all.txt همه نودها (X-UI aware)"""
    out: List[str] = []
    seen_links: set = set()
    for srv, uuid, marzban_un in get_service_panel_targets(svc):
        base_url = _build_user_base_url(srv, uuid)
        node_lines: List[str] = []
        seen_lines: set = set()
        if base_url:
            is_xui = "/sub/" in str(base_url or "")
            if is_xui:
                suffixes = ("", "?base64=1")
            else:
                suffixes = ("all.txt", "all.txt?base64=1")
            for suffix in suffixes:
                url = base_url if is_xui and not suffix else (f"{base_url}{suffix}" if is_xui else f"{base_url}/{suffix}")
                lines = _fetch_remote_lines(url)
                if lines:
                    node_lines = lines
                    break
        for ln in node_lines:
            raw = _sanitize_config_text(ln)
            if not raw or raw in seen_lines:
                continue
            seen_lines.add(raw)
            link = _extract_config_link_from_line(raw)
            if not link or link in seen_links:
                continue
            # فیلتر وضعیت داخلی
            low = link.lower()
            if "fake_ip_for_sub_link" in low or "status.hiddify-sellbot.invalid" in low:
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
