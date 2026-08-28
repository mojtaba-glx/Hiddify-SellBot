import base64
import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from Shared import database, userbot_db, hiddify_api, marzban_api, multi_panel, xui_api


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
STATUS_CONFIG_ENABLED_ENV = "SUB_STATUS_CONFIG_ENABLED"
STATUS_CONFIG_HOST_ENV = "SUB_STATUS_CONFIG_HOST"
STATUS_CONFIG_SNI_ENV = "SUB_STATUS_CONFIG_SNI"
STATUS_CONFIG_PORT_ENV = "SUB_STATUS_CONFIG_PORT"
SUB_AGGREGATOR_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on", "enable", "enabled", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "disable", "disabled", "n"}:
        return False
    return bool(default)


def _format_status_number(value: float) -> str:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    if abs(number) < 1:
        text = f"{number:.3f}"
    elif abs(number) < 100:
        text = f"{number:.1f}"
    else:
        text = f"{number:.0f}"
    return text.rstrip("0").rstrip(".") or "0"


def _clean_status_host(raw: str, default: str) -> str:
    value = str(raw or "").strip() or default
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        value = parsed.netloc or parsed.path or default
    value = value.strip().strip("/").split("/", 1)[0].strip()
    if ":" in value:
        value = value.split(":", 1)[0].strip()
    return value or default


def _status_config_name(service: dict, lock_reason: str = "") -> str:
    if lock_reason:
        reason_map = {
            "usage_limit_reached": "📊 حجم اشتراک شما به پایان رسیده است",
            "time_expired": "📅 زمان اشتراک شما به پایان رسیده است",
            "nodes_inactive": "⚠️ اشتراک شما غیرفعال شده است",
            "service_not_found": "❌ اشتراک یافت نشد",
        }
        base_reason = reason_map.get(lock_reason, "🔒 اشتراک غیرفعال")

        # جمع‌آوری جزییات استفاده از همه سرورها برای نمایش
        usage_parts = []
        total_usage_current = 0.0
        total_usage_limit = 0.0
        for target in _service_targets(service):
            server_info = target.get("server", {})
            server_name = server_info.get("name", "سرور") if server_info else "سرور"
            
            server_usage_current = _to_float((server_info or {}).get("usage_current", 0.0), 0.0)
            server_usage_limit = _to_float((server_info or {}).get("usage_limit", 0.0), 0.0)
            
            total_usage_current += server_usage_current
            total_usage_limit += server_usage_limit
            
            if server_usage_limit > 0:
                usage_text = f"{_format_status_number(server_usage_current)}/{_format_status_number(server_usage_limit)}GB"
                usage_parts.append(f"{server_name}: {usage_text}")
        
        global_usage_text = f"{_format_status_number(total_usage_current)}/{_format_status_number(total_usage_limit)}GB" if total_usage_limit > 0 else f"{_format_status_number(total_usage_current)}GB/نامحدود"
        
        usage_info = f" | ⏳ {global_usage_text}"
        if usage_parts:
            usage_info += f" | ({', '.join(usage_parts)})"
        
        return f"🚫 {base_reason}{usage_info}"

    service_name = str((service or {}).get("name") or "اشتراک").strip() or "اشتراک"
    usage_current = max(_to_float((service or {}).get("usage_current"), 0.0), 0.0)
    usage_limit = max(_to_float((service or {}).get("usage_limit"), 0.0), 0.0)

    if usage_limit > 0:
        usage_text = f"{_format_status_number(usage_current)}/{_format_status_number(usage_limit)}GB"
    else:
        usage_text = f"{_format_status_number(usage_current)}GB/نامحدود"

    try:
        days_left = int((service or {}).get("days_left"))
    except Exception:
        days_left = None
    days_text = f"{days_left} روز" if days_left is not None else "نامشخص"

    return f"📊 {service_name} | ⏳ {usage_text} | 📅 {days_text}"


def _build_status_config_line(service: dict, lock_reason: str = "") -> str:
    if not _env_bool(STATUS_CONFIG_ENABLED_ENV, True):
        return ""

    default_host = "status.hiddify-sellbot.invalid"
    host = _clean_status_host(os.getenv(STATUS_CONFIG_HOST_ENV, default_host), default_host)
    sni = _clean_status_host(os.getenv(STATUS_CONFIG_SNI_ENV, host), host)
    try:
        port = int(os.getenv(STATUS_CONFIG_PORT_ENV, "443") or "443")
        if port <= 0 or port > 65535:
            port = 443
    except Exception:
        port = 443

    return (
        f"trojan://1@{host}:{port}"
        f"?security=tls&sni={quote(sni, safe='')}&insecure=0&allowInsecure=0&type=tcp&headerType=none"
        f"#{quote(_status_config_name(service, lock_reason), safe='')}"
    )


def _parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        iso_raw = str(dt_str).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(dt_str), fmt)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f",):
        try:
            return datetime.strptime(str(dt_str), fmt)
        except ValueError:
            continue
    return None


def _optional_int_from_any(value: Any) -> Optional[int]:
    raw = str(value or "").replace(",", "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except Exception:
        return None


def _usage_limit_from_panel_user(user: Dict[str, Any]) -> Optional[float]:
    for key in ("usage_limit_GB", "usage_limit_gb", "usage_limit", "package_traffic"):
        if key not in user:
            continue
        raw = user.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        val = _to_float(raw, -1.0)
        if val >= 0:
            return float(val)
    return None


def _days_left_from_panel_user(user: Dict[str, Any]) -> Optional[int]:
    today_utc = datetime.now(timezone.utc).date()
    for key in ("remaining_days", "remaining_day", "days_left"):
        remaining = _optional_int_from_any(user.get(key))
        if remaining is not None:
            return remaining

    start_dt = _parse_dt(user.get("start_date"))
    package_days = user.get("package_days")
    if start_dt and package_days:
        try:
            end_dt = start_dt + timedelta(days=int(package_days))
            return (end_dt.date() - today_utc).days
        except Exception:
            pass

    for key in ("expire", "expire_date", "end_date", "expiration_date", "expires_at"):
        end_dt = _parse_dt(user.get(key))
        if end_dt:
            return (end_dt.date() - today_utc).days
    return None


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


async def _fetch_target_runtime(target: dict) -> dict:
    server = target.get("server") or {}
    user_uuid = str(target.get("uuid") or "").strip()
    server_id = int(target.get("server_id") or 0)
    if not server or not user_uuid:
        return {"ok": False, "server_id": server_id, "panel_user": None}
    try:
        return {
            "ok": True,
            "server_id": server_id,
            "panel_user": await hiddify_api.get_user_by_uuid(server, user_uuid),
        }
    except Exception:
        return {"ok": False, "server_id": server_id, "panel_user": None}


async def _sync_service_runtime_from_panels_async(service_id: int) -> dict:
    service = userbot_db.get_service_by_id(int(service_id)) or {}
    if not service:
        return {}

    targets = _service_targets(service)
    if not targets:
        return service

    try:
        primary_server_id = int(service.get("server_id") or 0)
    except (TypeError, ValueError):
        primary_server_id = 0

    results = await asyncio.gather(*[_fetch_target_runtime(target) for target in targets])
    total_nodes = len(results)
    fetched_nodes = 0
    total_usage = 0.0
    min_days_left: Optional[int] = None
    latest_last_online: Optional[datetime] = None
    synced_usage_limit: Optional[float] = None
    fallback_usage_limit: Optional[float] = None

    for result in results:
        if not result.get("ok"):
            continue
        panel_user = result.get("panel_user") or {}
        fetched_nodes += 1
        total_usage += _to_float(panel_user.get("current_usage_GB"), 0.0)

        panel_limit = _usage_limit_from_panel_user(panel_user)
        if panel_limit is not None:
            server_id = int(result.get("server_id") or 0)
            if primary_server_id > 0 and server_id == primary_server_id:
                synced_usage_limit = panel_limit
            elif fallback_usage_limit is None:
                fallback_usage_limit = panel_limit

        days_left = _days_left_from_panel_user(panel_user)
        if days_left is not None:
            min_days_left = days_left if min_days_left is None else min(min_days_left, days_left)

        dt = _parse_dt(panel_user.get("last_online"))
        if dt and (latest_last_online is None or dt > latest_last_online):
            latest_last_online = dt

    if fetched_nodes <= 0:
        return service

    if fetched_nodes < max(total_nodes, 1):
        total_usage = max(total_usage, _to_float(service.get("usage_current"), 0.0))
        try:
            previous_days_left = int(service.get("days_left"))
        except Exception:
            previous_days_left = None
        if previous_days_left is not None:
            min_days_left = previous_days_left if min_days_left is None else min(min_days_left, previous_days_left)

    if synced_usage_limit is None:
        synced_usage_limit = fallback_usage_limit

    userbot_db.update_service_runtime(
        int(service_id),
        usage_current=total_usage,
        usage_limit=synced_usage_limit,
        days_left=min_days_left,
        last_online=latest_last_online.isoformat(sep=" ") if latest_last_online else None,
    )
    return userbot_db.get_service_by_id(int(service_id)) or service


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def sync_service_runtime_from_panels(service_id: int) -> dict:
    try:
        return _run_async(_sync_service_runtime_from_panels_async(int(service_id)))
    except Exception:
        return userbot_db.get_service_by_id(int(service_id)) or {}


def _is_config_line(line: str) -> bool:
    low = str(line or "").strip().lower()
    return any(low.startswith(s) for s in ALLOWED_CONFIG_SCHEMES)


def _is_panel_status_config_line(line: str) -> bool:
    raw = str(line or "").strip().lower()
    if not raw.startswith("trojan://"):
        return False
    return "fake_ip_for_sub_link" in raw or "status.hiddify-sellbot.invalid" in raw


def _build_user_base_url(server: dict, user_uuid: str) -> Optional[str]:
    if not user_uuid:
        return None
    # X-UI: build native subscription URL directly
    try:
        if xui_api.is_xui_server(server):
            origin = xui_api._public_origin(server)
            sub_path = xui_api._sub_path(server)
            if origin and sub_path:
                return f"{origin.rstrip('/')}{sub_path}{user_uuid}"
    except Exception:
        pass
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
                "User-Agent": SUB_AGGREGATOR_USER_AGENT,
                "Accept": "text/plain,*/*;q=0.9",
                "Accept-Language": "en-US,en;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
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
    X-UI base_url is already full sub URL, so try it directly first.
    """
    # X-UI heuristic: contains /sub/ (native X-UI sub path)
    if "/sub/" in str(base_url or ""):
        # Try the URL itself, then with base64 param
        for url in [str(base_url).rstrip("/"), f"{str(base_url).rstrip('/')}?base64=1"]:
            lines = _fetch_lines(url)
            if lines:
                return lines
    candidates = [
        f"{base_url}/hiddify.txt",
        f"{base_url}/all.txt",
        f"{base_url}/sub/?format=base64",
    ]
    for url in candidates:
        lines = _fetch_lines(url)
        if lines:
            return lines
    # Last try: base_url itself (covers X-UI when not detected above but still works)
    lines = _fetch_lines(str(base_url).rstrip("/"))
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
        marzban_un = str(m.get("marzban_username") or "").strip()
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
                "marzban_username": marzban_un,
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


def _fetch_lines_from_admin_api(server: dict, user_uuid: str, marzban_username: str = "") -> List[str]:
    """
    Fallback: fetch user configs via admin API and extract direct links.
    Also fetches from Marzban if marzban_username is provided.
    """
    lines: List[str] = []
    # X-UI: native subscription is the most faithful source of config lines
    if xui_api.is_xui_server(server):
        try:
            xui_lines = xui_api.sync_fetch_subscription_lines(server, user_uuid)
            existing = set(lines)
            for ln in xui_lines or []:
                raw = str(ln or "").strip()
                if raw and "://" in raw and raw not in existing:
                    lines.append(raw)
                    existing.add(raw)
        except Exception:
            pass
    # Hiddify configs
    try:
        configs = _run_async(hiddify_api.get_user_configs(server, user_uuid))
        for item in configs or []:
            if not isinstance(item, dict):
                continue
            link = str(item.get("link") or "").strip()
            if "://" in link:
                lines.append(link)
    except Exception:
        pass

    # Marzban configs
    if marzban_username:
        try:
            marzban_links = _run_async(marzban_api.get_user_configs(server, marzban_username))
            existing = set(lines)
            for link in marzban_links or []:
                link_str = str(link or "").strip()
                if link_str and "://" in link_str and link_str not in existing:
                    lines.append(link_str)
                    existing.add(link_str)
        except Exception:
            pass

    return lines


def build_subscription_text_for_service(service_id: int) -> str:
    service = userbot_db.get_service_by_id(int(service_id))
    if not service:
        return ""
    lock_reason = _service_lock_reason(service)
    if lock_reason:
        status_line = _build_status_config_line(service, lock_reason)
        return status_line or ""
    lines: List[str] = []
    seen = set()
    for target in _service_targets(service):
        base = target.get("base_url")
        fetched: List[str] = []
        srv = target.get("server") or {}
        # برای همه سرورها، اول admin API را امتحان کن که حتی اگر یک اینباند (hysteria/ss) خراب بود، بقیه بیاید
        # اینجوری sub هوشمند فرانسه هیچ وقت کامل قایم نمیشه
        try:
            fetched = _fetch_lines_from_admin_api(srv, str(target.get("uuid") or ""), marzban_username=str(target.get("marzban_username") or ""))
        except Exception:
            fetched = []
        if not fetched and base:
            try:
                fetched = _fetch_subscription_lines(base)
            except Exception:
                fetched = []
        # اگر باز هم خالی بود و X-UI بود، یک بار دیگر مستقیم از xui_api بگیر (fallback)
        if not fetched:
            try:
                from Shared import xui_api as _xai2
                if _xai2.is_xui_server(srv):
                    # برای X-UI، حتی اگر یک اینباند خراب بود، بقیه را از طریق list_users بگیر
                    import asyncio as _aio
                    try:
                        xui_lines = _aio.run(_xai2.fetch_subscription_lines(srv, str(target.get("uuid") or "")))
                        if xui_lines:
                            fetched = [str(x) for x in xui_lines if str(x).strip()]
                    except Exception:
                        pass
            except Exception:
                pass
        for ln in fetched:
            if not _is_config_line(ln):
                continue
            if _is_panel_status_config_line(ln):
                continue
            if ln in seen:
                continue
            seen.add(ln)
            lines.append(ln)
    status_line = _build_status_config_line(service)
    if status_line and lines:
        lines.insert(0, status_line)
    return "\n".join(lines)


def build_subscription_b64_for_service(service_id: int) -> str:
    txt = build_subscription_text_for_service(service_id)
    if not txt:
        return ""
    return base64.b64encode(txt.encode("utf-8")).decode("ascii")
