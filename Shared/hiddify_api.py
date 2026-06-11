import asyncio
import ssl
import os
import logging
import re
from typing import Any, Dict, List, Optional
from datetime import datetime
from urllib.parse import urlparse, urljoin, quote
import httpx

logger = logging.getLogger(__name__)

SSL_MODE_ENV = "HIDDIFY_SSL_MODE"
SSL_MODE_AUTO = "auto"
SSL_MODE_SECURE = "secure"
SSL_MODE_INSECURE = "insecure"
API_TIMEOUT_ENV = "HIDDIFY_API_TIMEOUT_SECONDS"
BACKUP_RETRY_ATTEMPTS_ENV = "HIDDIFY_BACKUP_RETRY_ATTEMPTS"
BACKUP_RETRY_DELAY_ENV = "HIDDIFY_BACKUP_RETRY_DELAY_SECONDS"
CREATE_USER_STABILIZE_ENV = "HIDDIFY_CREATE_USER_STABILIZE_MODE"
CREATE_USER_STABILIZE_OFF = "off"
CREATE_USER_STABILIZE_UPDATE = "update"
CREATE_USER_STABILIZE_TOGGLE = "toggle"


class HiddifyApiError(Exception):
    """خطای عمومی برای تماس با Hiddify API."""
    pass


def _safe_filename(name: str) -> str:
    raw = str(name or "").strip().replace("\\", "/")
    raw = raw.split("/")[-1]
    raw = raw.replace("\x00", "")
    raw = re.sub(r'[^A-Za-z0-9._\- @()\u0600-\u06FF]+', "_", raw)
    raw = raw.strip(" .")
    return raw or "backup.json"


def _extract_filename_from_headers(headers: Dict[str, str]) -> str:
    cd = str(headers.get("content-disposition") or headers.get("Content-Disposition") or "").strip()
    if not cd:
        return ""

    # RFC 5987: filename*=UTF-8''...
    m = re.search(r"filename\*\s*=\s*([^;]+)", cd, flags=re.IGNORECASE)
    if m:
        value = m.group(1).strip().strip('"').strip("'")
        if "''" in value:
            value = value.split("''", 1)[-1]
        try:
            from urllib.parse import unquote
            value = unquote(value)
        except Exception:
            pass
        return _safe_filename(value)

    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, flags=re.IGNORECASE)
    if m:
        return _safe_filename(m.group(1))

    m = re.search(r"filename\s*=\s*([^;]+)", cd, flags=re.IGNORECASE)
    if m:
        return _safe_filename(m.group(1).strip().strip('"').strip("'"))

    return ""


def _default_server_backup_filename(server: Dict[str, Any]) -> str:
    panel_url = str(server.get("panel_url") or "").strip()
    host = ""
    domains = server.get("domains") or []
    if isinstance(domains, list):
        for d in domains:
            dom = ""
            if isinstance(d, dict):
                dom = str(d.get("domain") or "").strip()
            else:
                dom = str(d or "").strip()
            if dom:
                host = urlparse(dom).hostname or dom
                if host:
                    break
    if not host:
        host = urlparse(panel_url).hostname or f"server-{server.get('id','x')}"

    ts = datetime.now().strftime("%Y-%m-%d %H_%M_%S.%f")
    # نمونه: hiddify-user.example.com-2026-02-09 22_02_54.465460.json
    return _safe_filename(f"hiddify-{host}-{ts}.json")


def _get_backup_retry_attempts() -> int:
    try:
        value = int(os.getenv(BACKUP_RETRY_ATTEMPTS_ENV, "3") or "3")
    except Exception:
        value = 3
    return max(1, min(value, 5))


def _get_backup_retry_delay() -> float:
    try:
        value = float(os.getenv(BACKUP_RETRY_DELAY_ENV, "2.0") or "2.0")
    except Exception:
        value = 2.0
    return max(0.2, min(value, 10.0))


def _parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def _get_panel_url(server: Dict[str, Any]) -> str:
    """
    گرفتن آدرس پنل از دیکشنری سرور.
    """
    url = (server.get("panel_url") or "").rstrip("/")
    if not url:
        raise HiddifyApiError("panel_url برای سرور تنظیم نشده است.")
    return url


def _get_admin_proxy(server: Dict[str, Any]) -> str:
    """
    مسیر پروکسی ادمین (admin_proxy_path)
    """
    proxy = (server.get("admin_proxy_path") or "").strip("/")
    if not proxy:
        raise HiddifyApiError("admin_proxy_path برای سرور خالی است.")
    return proxy


def _get_user_proxy(server: Dict[str, Any]) -> str:
    """
    مسیر پروکسی یوزر (user_proxy_path)
    """
    proxy = (server.get("user_proxy_path") or "").strip("/")
    if not proxy:
        raise HiddifyApiError("user_proxy_path برای سرور خالی است.")
    return proxy


def _get_api_key(server: Dict[str, Any]) -> str:
    """
    گرفتن Hiddify-API-Key (معمولاً همون admin_uuid).
    """
    api_key = server.get("admin_uuid") or server.get("api_key")
    if not api_key:
        raise HiddifyApiError("admin_uuid یا api_key برای سرور تنظیم نشده است.")
    return str(api_key)


def _get_ssl_mode() -> str:
    """
    SSL mode:
      - auto (default): secure first, fallback to insecure on TLS errors
      - secure: strict certificate validation
      - insecure: disable certificate validation
    """
    raw = (os.getenv(SSL_MODE_ENV, SSL_MODE_AUTO) or "").strip().lower()
    aliases = {
        "on": SSL_MODE_SECURE,
        "true": SSL_MODE_SECURE,
        "1": SSL_MODE_SECURE,
        "off": SSL_MODE_INSECURE,
        "false": SSL_MODE_INSECURE,
        "0": SSL_MODE_INSECURE,
    }
    mode = aliases.get(raw, raw)
    if mode not in {SSL_MODE_AUTO, SSL_MODE_SECURE, SSL_MODE_INSECURE}:
        return SSL_MODE_AUTO
    return mode


def _build_insecure_ssl_context() -> ssl.SSLContext:
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return ssl_context


def _looks_like_tls_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "certificate",
            "self signed",
            "tls",
            "ssl",
            "hostname",
            "wrong version number",
            "cert",
        )
    )


def _get_api_timeout_seconds() -> float:
    raw = str(os.getenv(API_TIMEOUT_ENV, "8") or "8").strip()
    try:
        val = float(raw)
    except Exception:
        return 8.0
    return min(max(val, 2.0), 60.0)


def _get_create_user_stabilize_mode() -> str:
    raw = str(os.getenv(CREATE_USER_STABILIZE_ENV, CREATE_USER_STABILIZE_TOGGLE) or "").strip().lower()
    aliases = {
        "0": CREATE_USER_STABILIZE_OFF,
        "false": CREATE_USER_STABILIZE_OFF,
        "no": CREATE_USER_STABILIZE_OFF,
        "disable": CREATE_USER_STABILIZE_OFF,
        "disabled": CREATE_USER_STABILIZE_OFF,
        "1": CREATE_USER_STABILIZE_TOGGLE,
        "true": CREATE_USER_STABILIZE_TOGGLE,
        "yes": CREATE_USER_STABILIZE_TOGGLE,
        "enable": CREATE_USER_STABILIZE_TOGGLE,
        "enabled": CREATE_USER_STABILIZE_TOGGLE,
        "patch": CREATE_USER_STABILIZE_UPDATE,
    }
    mode = aliases.get(raw, raw)
    if mode not in {
        CREATE_USER_STABILIZE_OFF,
        CREATE_USER_STABILIZE_UPDATE,
        CREATE_USER_STABILIZE_TOGGLE,
    }:
        return CREATE_USER_STABILIZE_TOGGLE
    return mode


async def _request(
    method: str,
    url: str,
    server: Dict[str, Any],
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Any] = None,
) -> Any:
    """
    درخواست عمومی به Hiddify API.
    حالت SSL با متغیر HIDDIFY_SSL_MODE کنترل می‌شود (auto/secure/insecure).
    """
    headers: Dict[str, str] = {
        "Accept": "application/json",
        "Hiddify-API-Key": _get_api_key(server),
    }
    mode = _get_ssl_mode()

    timeout_seconds = _get_api_timeout_seconds()

    async def _send_request(verify: Any) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout_seconds, verify=verify) as client:
            return await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
            )

    try:
        if mode == SSL_MODE_SECURE:
            resp = await _send_request(True)
        elif mode == SSL_MODE_INSECURE:
            resp = await _send_request(_build_insecure_ssl_context())
        else:
            try:
                resp = await _send_request(True)
            except httpx.RequestError as e:
                if not _looks_like_tls_error(e):
                    raise
                logger.warning(
                    "TLS verification failed for %s; retrying insecure because %s=%s",
                    url,
                    SSL_MODE_ENV,
                    SSL_MODE_AUTO,
                )
                resp = await _send_request(_build_insecure_ssl_context())
    except httpx.RequestError as e:
        msg = str(e).strip() or e.__class__.__name__
        raise HiddifyApiError(f"خطا در اتصال به Hiddify API: {msg}") from e

    if resp.status_code >= 400:
        raise HiddifyApiError(f"HTTP {resp.status_code}: {resp.text}")

    ct = resp.headers.get("content-type", "")
    if "application/json" in ct.lower():
        try:
            return resp.json()
        except ValueError as e:
            raise HiddifyApiError("پاسخ JSON معتبر نیست.") from e
    return resp.text


async def _request_bytes(
    method: str,
    url: str,
    server: Dict[str, Any],
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Any] = None,
) -> tuple[bytes, Dict[str, str]]:
    """
    درخواست خام (byte) برای endpoint هایی مثل backup download.
    """
    headers: Dict[str, str] = {
        "Accept": "*/*",
        "Hiddify-API-Key": _get_api_key(server),
    }
    mode = _get_ssl_mode()

    async def _send_request(verify: Any) -> httpx.Response:
        async with httpx.AsyncClient(timeout=60.0, verify=verify) as client:
            return await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
            )

    attempts = _get_backup_retry_attempts()
    delay = _get_backup_retry_delay()
    last_error: Optional[Exception] = None
    resp: Optional[httpx.Response] = None

    for attempt in range(1, attempts + 1):
        try:
            if mode == SSL_MODE_SECURE:
                resp = await _send_request(True)
            elif mode == SSL_MODE_INSECURE:
                resp = await _send_request(_build_insecure_ssl_context())
            else:
                try:
                    resp = await _send_request(True)
                except httpx.RequestError as e:
                    if not _looks_like_tls_error(e):
                        raise
                    logger.warning(
                        "TLS verification failed for %s; retrying insecure because %s=%s",
                        url,
                        SSL_MODE_ENV,
                        SSL_MODE_AUTO,
                    )
                    resp = await _send_request(_build_insecure_ssl_context())

            if resp.status_code in {408, 425, 429, 500, 502, 503, 504} and attempt < attempts:
                last_error = HiddifyApiError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                await asyncio.sleep(delay * attempt)
                continue
            break
        except httpx.RequestError as e:
            last_error = e
            if attempt >= attempts:
                break
            await asyncio.sleep(delay * attempt)

    if resp is None:
        raise HiddifyApiError(f"خطا در اتصال به Hiddify API بعد از {attempts} تلاش: {last_error}") from last_error

    if resp.status_code >= 400:
        raise HiddifyApiError(f"HTTP {resp.status_code}: {resp.text}")

    return bytes(resp.content or b""), dict(resp.headers)


async def download_server_backup(server: Dict[str, Any]) -> Dict[str, Any]:
    """
    دانلود بکاپ JSON از پنل هیدیفای.
    روی چند endpoint کاندید تلاش می‌کند تا با نسخه‌های مختلف پنل سازگار باشد.
    خروجی:
      {
        "filename": "...json",
        "content": b"...",
        "source_url": "..."
      }
    """
    base = _get_panel_url(server)
    proxy = _get_admin_proxy(server)

    candidate_urls = [
        f"{base}/{proxy}/backup",
        f"{base}/{proxy}/admin/backup",
        f"{base}/{proxy}/api/v2/admin/backup/",
        f"{base}/{proxy}/api/v2/admin/backup",
        f"{base}/{proxy}/api/v2/admin/user/backup/",
        f"{base}/{proxy}/api/v2/admin/user/backup",
    ]

    errors: List[str] = []
    for url in candidate_urls:
        try:
            body, headers = await _request_bytes("GET", url, server)
        except Exception as e:
            errors.append(f"{url} -> {e}")
            continue

        if not body:
            errors.append(f"{url} -> empty response")
            continue

        # غالباً بکاپ پنل به شکل JSON است؛ پاسخ HTML خطا را رد می‌کنیم.
        head = body[:256].lower()
        content_type = str(headers.get("content-type") or "").lower()
        if (b"<html" in head or b"<!doctype html" in head) and "json" not in content_type:
            errors.append(f"{url} -> html response")
            continue

        filename = _extract_filename_from_headers(headers)
        if not filename:
            filename = _default_server_backup_filename(server)
        if not filename.lower().endswith(".json"):
            filename = f"{filename}.json"

        return {
            "filename": filename,
            "content": body,
            "source_url": url,
        }

    # Fallback: /admin/backup ممکن است صفحه HTML داشته باشد که لینک/فرم دانلود واقعی داخلش است.
    try:
        html_result = await _download_server_backup_from_admin_html(server)
        if html_result:
            return html_result
    except Exception as e:
        errors.append(f"admin-html-fallback -> {e}")

    raise HiddifyApiError(
        "دریافت بکاپ پنل ناموفق بود. endpoint های تست‌شده:\n"
        + "\n".join(errors[-6:])
    )


def _looks_like_html(body: bytes, content_type: str) -> bool:
    ctype = str(content_type or "").lower()
    if "text/html" in ctype:
        return True
    head = bytes(body[:256] or b"").lower()
    return b"<html" in head or b"<!doctype html" in head


def _extract_candidate_urls_from_html(html: str, base_url: str) -> List[str]:
    candidates: List[str] = []
    patterns = [
        r'href\s*=\s*"([^"]+)"',
        r"href\s*=\s*'([^']+)'",
        r'action\s*=\s*"([^"]+)"',
        r"action\s*=\s*'([^']+)'",
        r'fetch\(\s*"([^"]+)"',
        r"fetch\(\s*'([^']+)'",
    ]
    for pat in patterns:
        for m in re.findall(pat, html, flags=re.IGNORECASE):
            u = str(m or "").strip()
            if not u:
                continue
            abs_url = urljoin(base_url, u)
            if abs_url and abs_url not in candidates:
                candidates.append(abs_url)
    return candidates


def _extract_backup_filenames_from_html(html: str) -> List[str]:
    out: List[str] = []
    for m in re.findall(r'([A-Za-z0-9._\- ]+\.json)', html, flags=re.IGNORECASE):
        fn = _safe_filename(m)
        if fn and fn.lower().endswith(".json") and fn not in out:
            out.append(fn)
    return out


def _extract_forms_from_html(html: str, base_url: str) -> List[Dict[str, Any]]:
    forms: List[Dict[str, Any]] = []
    for fm in re.finditer(r"<form\b([^>]*)>(.*?)</form>", html, flags=re.IGNORECASE | re.DOTALL):
        attrs = fm.group(1) or ""
        body = fm.group(2) or ""
        action_m = re.search(r'action\s*=\s*"([^"]+)"|action\s*=\s*\'([^\']+)\'', attrs, flags=re.IGNORECASE)
        method_m = re.search(r'method\s*=\s*"([^"]+)"|method\s*=\s*\'([^\']+)\'', attrs, flags=re.IGNORECASE)
        action = ""
        if action_m:
            action = action_m.group(1) or action_m.group(2) or ""
        action_url = urljoin(base_url, action) if action else base_url
        method = (method_m.group(1) or method_m.group(2) or "post").strip().lower() if method_m else "post"

        payload: Dict[str, str] = {}
        for im in re.finditer(r"<input\b([^>]*)>", body, flags=re.IGNORECASE):
            iattrs = im.group(1) or ""
            name_m = re.search(r'name\s*=\s*"([^"]+)"|name\s*=\s*\'([^\']+)\'', iattrs, flags=re.IGNORECASE)
            if not name_m:
                continue
            name = (name_m.group(1) or name_m.group(2) or "").strip()
            if not name:
                continue
            value_m = re.search(r'value\s*=\s*"([^"]*)"|value\s*=\s*\'([^\']*)\'', iattrs, flags=re.IGNORECASE)
            value = (value_m.group(1) or value_m.group(2) or "") if value_m else ""
            payload[name] = value

        forms.append({"action_url": action_url, "method": method, "payload": payload})
    return forms


async def _download_server_backup_from_admin_html(server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = _get_panel_url(server)
    proxy = _get_admin_proxy(server)
    page_url = f"{base}/{proxy}/admin/backup"
    headers = {
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Hiddify-API-Key": _get_api_key(server),
    }

    mode = _get_ssl_mode()
    verify_opts: List[Any] = []
    if mode == SSL_MODE_SECURE:
        verify_opts = [True]
    elif mode == SSL_MODE_INSECURE:
        verify_opts = [_build_insecure_ssl_context()]
    else:
        verify_opts = [True, _build_insecure_ssl_context()]

    last_err: Optional[Exception] = None
    for verify in verify_opts:
        try:
            async with httpx.AsyncClient(timeout=60.0, verify=verify, follow_redirects=True) as client:
                resp = await client.get(page_url, headers=headers)
                if resp.status_code >= 400:
                    raise HiddifyApiError(f"HTTP {resp.status_code}: {resp.text[:300]}")

                body = bytes(resp.content or b"")
                ctype = str(resp.headers.get("content-type") or "")
                if not _looks_like_html(body, ctype):
                    filename = _extract_filename_from_headers(dict(resp.headers)) or _default_server_backup_filename(server)
                    if not filename.lower().endswith(".json"):
                        filename = f"{filename}.json"
                    return {"filename": filename, "content": body, "source_url": str(resp.url)}

                html = resp.text or ""
                discovered_urls = _extract_candidate_urls_from_html(html, str(resp.url))

                # Try discovered URLs that likely point to backup/file download first.
                ranked = []
                for u in discovered_urls:
                    lu = u.lower()
                    score = 0
                    if "backup" in lu:
                        score += 3
                    if "download" in lu:
                        score += 2
                    if ".json" in lu:
                        score += 2
                    if "logout" in lu or "login" in lu:
                        score -= 5
                    ranked.append((score, u))
                ranked.sort(key=lambda x: x[0], reverse=True)

                tried: set[str] = set()
                for score, u in ranked[:30]:
                    if score <= 0 or u in tried:
                        continue
                    tried.add(u)
                    rr = await client.get(u, headers={"Hiddify-API-Key": _get_api_key(server), "Accept": "*/*"})
                    if rr.status_code >= 400:
                        continue
                    rb = bytes(rr.content or b"")
                    if not rb:
                        continue
                    if _looks_like_html(rb, str(rr.headers.get("content-type") or "")):
                        continue
                    filename = _extract_filename_from_headers(dict(rr.headers)) or _safe_filename(u.split("/")[-1] or _default_server_backup_filename(server))
                    if not filename.lower().endswith(".json"):
                        filename = f"{filename}.json"
                    return {"filename": filename, "content": rb, "source_url": str(rr.url)}

                # Try posting forms in backup page (some versions use CSRF + POST to trigger file download).
                forms = _extract_forms_from_html(html, str(resp.url))
                for fm in forms[:10]:
                    action_url = str(fm.get("action_url") or "").strip() or str(resp.url)
                    payload = dict(fm.get("payload") or {})
                    method = str(fm.get("method") or "post").lower()
                    rr = await client.request(
                        "POST" if method not in {"get", "post"} else method.upper(),
                        action_url,
                        headers={"Hiddify-API-Key": _get_api_key(server), "Accept": "*/*"},
                        data=payload if method != "get" else None,
                        params=payload if method == "get" else None,
                    )
                    if rr.status_code >= 400:
                        continue
                    rb = bytes(rr.content or b"")
                    if not rb:
                        continue
                    if _looks_like_html(rb, str(rr.headers.get("content-type") or "")):
                        continue
                    filename = _extract_filename_from_headers(dict(rr.headers)) or _default_server_backup_filename(server)
                    if not filename.lower().endswith(".json"):
                        filename = f"{filename}.json"
                    return {"filename": filename, "content": rb, "source_url": str(rr.url)}

                # If filenames are visible in HTML, try direct download URL guesses.
                filenames = _extract_backup_filenames_from_html(html)
                for fname in filenames[:20]:
                    quoted = quote(fname)
                    guesses = [
                        urljoin(str(resp.url), fname),
                        urljoin(str(resp.url), quoted),
                        f"{base}/{proxy}/admin/backup/{quoted}",
                        f"{base}/{proxy}/admin/backup?file={quoted}",
                        f"{base}/{proxy}/backup/{quoted}",
                    ]
                    for g in guesses:
                        rr = await client.get(g, headers={"Hiddify-API-Key": _get_api_key(server), "Accept": "*/*"})
                        if rr.status_code >= 400:
                            continue
                        rb = bytes(rr.content or b"")
                        if not rb:
                            continue
                        if _looks_like_html(rb, str(rr.headers.get("content-type") or "")):
                            continue
                        filename = _extract_filename_from_headers(dict(rr.headers)) or fname
                        if not filename.lower().endswith(".json"):
                            filename = f"{filename}.json"
                        return {"filename": filename, "content": rb, "source_url": str(rr.url)}

                return None
        except httpx.RequestError as e:
            last_err = e
            if mode == SSL_MODE_AUTO and _looks_like_tls_error(e) and verify is True:
                continue
            break
        except Exception as e:
            last_err = e
            break

    if last_err:
        raise HiddifyApiError(str(last_err))
    return None


# ===============================
#   کاربران (Admin API)
# ===============================
async def list_users(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    GET /{admin_proxy}/api/v2/admin/user/
    لیست کاربران برای ادمین فعلی.
    """
    base = _get_panel_url(server)
    proxy = _get_admin_proxy(server)
    url = f"{base}/{proxy}/api/v2/admin/user/"

    data = await _request("GET", url, server)
    if not isinstance(data, list):
        raise HiddifyApiError("پاسخ لیست کاربران شکل لیست (array) ندارد.")
    return data


async def get_user_by_uuid(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    """
    GET /{admin_proxy}/api/v2/admin/user/{uuid}/
    دریافت اطلاعات یک کاربر با UUID/ID.
    """
    base = _get_panel_url(server)
    proxy = _get_admin_proxy(server)
    url = f"{base}/{proxy}/api/v2/admin/user/{user_uuid}/"

    data = await _request("GET", url, server)
    if not isinstance(data, dict):
        raise HiddifyApiError("پاسخ get_user_by_uuid شکل دیکشنری ندارد.")
    return data


async def patch_user(
    server: Dict[str, Any],
    user_uuid: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    PATCH /{admin_proxy}/api/v2/admin/user/{uuid}/
    بروزرسانی اطلاعات یک کاربر (نام، حجم، روز، کامنت، ...).
    """
    base = _get_panel_url(server)
    proxy = _get_admin_proxy(server)
    url = f"{base}/{proxy}/api/v2/admin/user/{user_uuid}/"

    data = await _request("PATCH", url, server, json=payload)
    if not isinstance(data, dict):
        raise HiddifyApiError("پاسخ patch_user شکل دیکشنری ندارد.")
    return data


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(int(value))
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on", "active", "enabled", "y"}:
        return True
    if s in {"0", "false", "no", "off", "inactive", "disabled", "n"}:
        return False
    return None


def _user_is_disabled(data: Dict[str, Any]) -> bool:
    mode = str(data.get("mode") or "").strip().lower()
    status = str(data.get("status") or "").strip().lower()
    is_active = _coerce_bool(data.get("is_active"))
    active = _coerce_bool(data.get("active"))
    enabled = _coerce_bool(data.get("enabled"))
    enable = _coerce_bool(data.get("enable"))
    if mode in {"disable", "disabled", "inactive"}:
        return True
    if status in {"disable", "disabled", "inactive", "deactive", "off"}:
        return True
    if is_active is False:
        return True
    if active is False:
        return True
    if enabled is False:
        return True
    if enable is False:
        return True
    return False


def _user_is_enabled(data: Dict[str, Any]) -> bool:
    mode = str(data.get("mode") or "").strip().lower()
    status = str(data.get("status") or "").strip().lower()
    is_active = _coerce_bool(data.get("is_active"))
    active = _coerce_bool(data.get("active"))
    enabled = _coerce_bool(data.get("enabled"))
    enable = _coerce_bool(data.get("enable"))
    if mode in {"disable", "disabled", "inactive"}:
        return False
    if status in {"disable", "disabled", "inactive", "deactive", "off"}:
        return False
    if mode in {"no_reset", "active", "enabled", "enable"}:
        return True
    if status in {"active", "enabled", "enable", "on"}:
        return True
    if is_active is True:
        return True
    if active is True:
        return True
    if enabled is True:
        return True
    if enable is True:
        return True
    return False


def _has_disable_markers(data: Dict[str, Any]) -> bool:
    for key in ("is_active", "active", "enabled", "enable", "mode", "status"):
        if key in data:
            return True
    mode = str(data.get("mode") or "").strip()
    status = str(data.get("status") or "").strip()
    return bool(mode or status)


async def enable_user(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    """
    فعال‌سازی کاربر به‌صورت سازگار با نسخه‌های مختلف پنل.
    """
    attempts = (
        {"is_active": True},
        {"is_active": 1},
        {"enable": True},
        {"enable": 1},
        {"enable": "y"},
        {"enabled": True},
        {"enabled": 1},
        {"status": "active"},
        {"mode": "no_reset"},
        {"is_active": True, "enable": "y"},
        {"is_active": True, "mode": "no_reset"},
        {"enable": "y", "status": "active"},
    )
    last_err: Optional[Exception] = None
    last_data: Optional[Dict[str, Any]] = None

    for payload in attempts:
        try:
            patched = await patch_user(server, user_uuid, payload)
            last_data = patched
        except Exception as e:
            last_err = e
            continue

        try:
            current = await get_user_by_uuid(server, user_uuid)
            if _user_is_enabled(current) and not _user_is_disabled(current):
                return current
            last_data = current
        except Exception as e:
            last_err = e
            if _has_disable_markers(patched) and _user_is_enabled(patched) and not _user_is_disabled(patched):
                return patched
            continue

        if _has_disable_markers(patched) and _user_is_enabled(patched) and not _user_is_disabled(patched):
            return patched

    if isinstance(last_data, dict):
        if _user_is_enabled(last_data) and not _user_is_disabled(last_data):
            return last_data
        if _has_disable_markers(last_data):
            raise HiddifyApiError(
                "enable_user did not activate user: "
                f"is_active={last_data.get('is_active')} "
                f"active={last_data.get('active')} "
                f"enabled={last_data.get('enabled')} "
                f"enable={last_data.get('enable')} "
                f"mode={last_data.get('mode')} "
                f"status={last_data.get('status')}"
            )
    if last_err:
        raise HiddifyApiError(f"enable_user failed: {last_err}") from last_err
    if isinstance(last_data, dict):
        raise HiddifyApiError(
            "enable_user failed: panel response has no enable markers and activation could not be verified"
        )
    raise HiddifyApiError("enable_user failed: unknown error")


async def disable_user(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    """
    غیرفعال‌سازی کاربر به‌صورت سازگار با نسخه‌های مختلف پنل:
    1) is_active=False
    2) mode=disable
    3) هر دو با هم
    سپس verify با GET.
    """
    attempts = (
        {"is_active": False},
        {"is_active": 0},
        {"enable": False},
        {"enable": 0},
        {"enable": "n"},
        {"enabled": False},
        {"enabled": 0},
        {"mode": "disable"},
        {"status": "disable"},
        {"enable": "n", "status": "disable"},
        {"enable": "n", "mode": "disable"},
        {"is_active": False, "mode": "disable"},
        {"is_active": False, "status": "disable"},
        {"is_active": False, "enable": "n"},
    )
    last_err: Optional[Exception] = None
    last_data: Optional[Dict[str, Any]] = None

    for payload in attempts:
        try:
            patched = await patch_user(server, user_uuid, payload)
            last_data = patched
        except Exception as e:
            last_err = e
            continue

        try:
            current = await get_user_by_uuid(server, user_uuid)
            if _user_is_disabled(current):
                return current
            last_data = current
        except Exception as e:
            last_err = e
            # اگر verify شکست خورد، فقط وقتی موفق می‌دانیم که پاسخ PATCH
            # صراحتا وضعیت غیرفعال را نشان دهد.
            if _has_disable_markers(patched) and _user_is_disabled(patched):
                return patched
            continue

        # برخی پنل‌ها پاسخ PATCH کامل نمی‌دهند؛
        # بنابراین حتی اگر PATCH موفق بود، تا زمانی که current یا patched
        # وضعیت غیرفعال را ندهد، این تلاش را ناموفق در نظر می‌گیریم.
        if _has_disable_markers(patched) and _user_is_disabled(patched):
            return patched

    if isinstance(last_data, dict):
        if _user_is_disabled(last_data):
            return last_data
        if _has_disable_markers(last_data):
            raise HiddifyApiError(
                "disable_user did not deactivate user: "
                f"is_active={last_data.get('is_active')} "
                f"active={last_data.get('active')} "
                f"enabled={last_data.get('enabled')} "
                f"enable={last_data.get('enable')} "
                f"mode={last_data.get('mode')} "
                f"status={last_data.get('status')}"
            )
    if last_err:
        raise HiddifyApiError(f"disable_user failed: {last_err}") from last_err
    if isinstance(last_data, dict):
        raise HiddifyApiError(
            "disable_user failed: panel response has no disable markers and deactivation could not be verified"
        )
    raise HiddifyApiError("disable_user failed: unknown error")


async def create_user(
    server: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    POST /{admin_proxy}/api/v2/admin/user/
    ساخت کاربر جدید.

    payload مثلاً:
    {
        "name": "...",
        "usage_limit_GB": 30,
        "package_days": 30,
        "start_date": "2025-12-24",
        "current_usage_GB": 0,
        "last_reset_time": "...",
        "is_active": True,
        ...
    }
    """
    base = _get_panel_url(server)
    proxy = _get_admin_proxy(server)
    url = f"{base}/{proxy}/api/v2/admin/user/"

    data = await _request("POST", url, server, json=payload)
    if not isinstance(data, dict):
        raise HiddifyApiError("پاسخ create_user شکل دیکشنری ندارد.")

    user_uuid = str(data.get("uuid") or payload.get("uuid") or "").strip()
    if not user_uuid:
        logger.warning(
            "Created Hiddify user cannot be stabilized because panel response has no uuid: %s",
            data,
        )
        return data

    mode = _get_create_user_stabilize_mode()
    if mode == CREATE_USER_STABILIZE_OFF:
        return data

    update_payload = dict(payload)
    update_payload["is_active"] = True
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            await patch_user(server, user_uuid, update_payload)
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(0.35)

    if last_error is not None:
        logger.warning(
            "Post-create Hiddify user stabilization update failed for uuid=%s: %s",
            user_uuid,
            last_error,
        )
        return data

    if mode != CREATE_USER_STABILIZE_TOGGLE:
        return data

    await asyncio.sleep(0.2)
    disable_payloads = (
        {"is_active": False, "enable": "n", "mode": "disable", "status": "disable"},
        {"is_active": False},
        {"mode": "disable"},
    )
    enable_payload = dict(payload)
    enable_payload.update(
        {
            "is_active": True,
            "enable": "y",
            "mode": "no_reset",
            "status": "active",
        }
    )
    enable_payloads = (
        enable_payload,
        {"is_active": True, "enable": "y", "mode": "no_reset", "status": "active"},
        {"is_active": True},
        {"mode": "no_reset"},
    )

    def _payload_error(exc: Exception) -> str:
        return str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__

    async def _try_patch_payloads(payloads: tuple[Dict[str, Any], ...]) -> Optional[str]:
        last: Optional[Exception] = None
        for patch_payload in payloads:
            try:
                await patch_user(server, user_uuid, patch_payload)
                return None
            except Exception as exc:
                last = exc
        return _payload_error(last) if last is not None else "unknown"

    disable_error = await _try_patch_payloads(disable_payloads)
    if disable_error:
        logger.warning(
            "Post-create Hiddify user activation refresh disable step failed for uuid=%s: %s",
            user_uuid,
            disable_error,
        )
        return data

    await asyncio.sleep(0.25)
    enable_error = await _try_patch_payloads(enable_payloads)
    if enable_error:
        logger.warning(
            "Post-create Hiddify user activation refresh enable step failed for uuid=%s: %s",
            user_uuid,
            enable_error,
        )
    return data


async def delete_user(server: Dict[str, Any], user_uuid: str) -> None:
    """
    حذف کاربر.
    ابتدا تلاش می‌کند DELETE بزند؛
    اگر API پشتیبانی نکند، به صورت fallback فقط is_active=False می‌کند.
    """
    base = _get_panel_url(server)
    proxy = _get_admin_proxy(server)
    url = f"{base}/{proxy}/api/v2/admin/user/{user_uuid}/"

    # اول تلاش با DELETE
    try:
        await _request("DELETE", url, server)
        return
    except HiddifyApiError:
        # اگر پنل DELETE نداشت، می‌ریم سراغ غیرفعال‌کردن کاربر
        try:
            await disable_user(server, user_uuid)
        except Exception as e:
            raise HiddifyApiError(f"خطا در delete_user (fallback is_active=False): {e}") from e


# ===============================
#   کانفیگ‌های کاربر (User API by UUID)
# ===============================
async def get_user_configs(
    server: Dict[str, Any],
    user_uuid: str,
) -> List[Dict[str, Any]]:
    """
    GET /{user_proxy}/{secret_uuid}/api/v2/user/all-configs/

    لیست تمام کانفیگ‌های کاربر (vless, vmess, ...)

    مقدار برگشتی هر آیتم چیزی شبیه به این است:
    {
        "name": "🇳🇱WFI reality grpc direct vless",
        "link": "vless://....",
        "protocol": "vless",
        "security": "...",
        "transport": "...",
        "type": "...",
        ...
    }
    """
    base = _get_panel_url(server)
    proxy = _get_user_proxy(server)
    url = f"{base}/{proxy}/{user_uuid}/api/v2/user/all-configs/"

    data = await _request("GET", url, server)
    if not isinstance(data, list):
        raise HiddifyApiError("پاسخ get_user_configs شکل لیست ندارد.")
    return data

# در Shared/hiddify_api.py جایگزین تابع قبلی شود

async def get_server_stats(server: Dict[str, Any]) -> Dict[str, Any]:
    """دریافت آمار سیستم (System Stats) و کاربران"""

    def _to_int(v: Any, default: int = 0) -> int:
        try:
            if v is None:
                return default
            return int(float(v))
        except Exception:
            return default

    def _to_float(v: Any, default: float = 0.0) -> float:
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    def _bytes_to_gb(v: Any) -> float:
        try:
            b = float(v or 0)
            return b / (1024 ** 3)
        except Exception:
            return 0.0

    # fallback users info
    try:
        users = await list_users(server)
    except Exception:
        users = []

    total_usage_fallback_gb = 0.0
    online_now_fallback = 0
    active_today_fallback = 0
    active_month_fallback = 0
    now = datetime.now()
    for u in users:
        total_usage_fallback_gb += _to_float(u.get("current_usage_GB"), 0.0)
        dt = _parse_dt(u.get("last_online"))
        if dt:
            diff = (now - dt).total_seconds()
            if diff < 300:
                online_now_fallback += 1
            if diff < 86400:
                active_today_fallback += 1
            if diff < 2592000:
                active_month_fallback += 1

    # آمار واقعی سیستم از API وضعیت سرور
    base = _get_panel_url(server)
    proxy = _get_admin_proxy(server)

    # defaults (fallbacks)
    out = {
        "cpu_percent": 0.0,
        "cpu_cores": 1,
        "ram_used": 0.0,
        "ram_total": 1.0,
        "disk_used": 0.0,
        "disk_total": 20.0,
        "users_total": len(users),
        "users_online": online_now_fallback,
        "users_today": active_today_fallback,
        "users_month": active_month_fallback,
        "usage_today_gb": 0.0,
        "usage_30days_gb": total_usage_fallback_gb,
        "traffic_dl": 0.0,
        "traffic_ul": 0.0,
        "now_net_recv_mb": 0.0,
        "now_net_sent_mb": 0.0,
    }

    try:
        url = f"{base}/{proxy}/api/v2/admin/server_status/"
        data = await _request("GET", url, server)
        if not isinstance(data, dict):
            return out

        stats = data.get("stats") or {}
        usage_history = data.get("usage_history") or {}
        system = stats.get("system") or {}

        out["cpu_percent"] = _to_float(system.get("cpu_percent"), out["cpu_percent"])
        out["cpu_cores"] = _to_int(system.get("num_cpus"), out["cpu_cores"])
        out["ram_used"] = _to_float(system.get("ram_used"), out["ram_used"])
        out["ram_total"] = _to_float(system.get("ram_total"), out["ram_total"])
        out["disk_used"] = _to_float(system.get("disk_used"), out["disk_used"])
        out["disk_total"] = _to_float(system.get("disk_total"), out["disk_total"])

        out["now_net_recv_mb"] = _to_float(system.get("bytes_recv"), 0.0) / (1024 ** 2)
        out["now_net_sent_mb"] = _to_float(system.get("bytes_sent"), 0.0) / (1024 ** 2)

        # usage_history blocks
        total_block = usage_history.get("total") or {}
        today_block = usage_history.get("today") or {}
        d30_block = usage_history.get("last_30_days") or {}
        m5_block = usage_history.get("m5") or {}

        out["users_total"] = _to_int(total_block.get("users"), out["users_total"])
        out["users_online"] = _to_int(m5_block.get("online"), out["users_online"])
        out["users_today"] = _to_int(today_block.get("online"), out["users_today"])
        out["users_month"] = _to_int(d30_block.get("online"), out["users_month"])

        out["usage_today_gb"] = _bytes_to_gb(today_block.get("usage"))
        out["usage_30days_gb"] = _bytes_to_gb(d30_block.get("usage"))

        # cumulative traffic from system
        sent_gb = _to_float(system.get("net_sent_cumulative_GB"), 0.0)
        recv_gb = _bytes_to_gb(system.get("bytes_recv_cumulative"))
        out["traffic_ul"] = sent_gb
        out["traffic_dl"] = recv_gb

        # اگر یکی نبود، از total fallback
        if out["traffic_dl"] <= 0 and _to_float(system.get("net_total_cumulative_GB"), 0.0) > 0:
            out["traffic_dl"] = max(0.0, _to_float(system.get("net_total_cumulative_GB"), 0.0) - sent_gb)

    except Exception:
        # fallback values stay in out
        pass

    return out
