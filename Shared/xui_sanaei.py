"""
Shared/xui_sanaei.py
====================
Adapter for MHSanaei 3x-ui (Sanaei) - modern API: /panel/api/*
Uses Bearer token (Settings -> Security -> API Token) as primary auth.
Falls back to cookie login if token not provided but Sanaei also supports it.

API docs: /panel/api/openapi.json and frontend/src/pages/api-docs/endpoints.ts
Key difference vs Alireza: clients are first-class at /panel/api/clients/*
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx

from Shared import hiddify_api
from Shared.xui_common import (
    _bytes_to_gb,
    _compute_expiry_ms,
    _gb_limit_from_payload,
    _enable_from_payload,
    _find_all_clients_for_uuid,
    _get_api_token,
    _get_panel_url,
    _public_origin,
    _sanitize_xui_email,
    _sub_path,
    _to_float,
    _to_int,
    _unique_xui_email,
    _existing_xui_emails,
    _settings_clients,
    _client_key_value,
    _should_reset_traffic,
    sync_run,
    uuid4,
)

logger = logging.getLogger(__name__)

class XuiApiError(Exception):
    pass

# ---------------------------------------------------------------------------
# Session cache (Bearer token - no expiry, but keep loop-bound cache)
# ---------------------------------------------------------------------------
_XUI_SESSION_CACHE: Dict[Any, Tuple[float, "httpx.AsyncClient", Any, int]] = {}
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
    token = _get_api_token(server)
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
        # Verify token with lightweight call
        base = _get_panel_url(server)
        # Try modern endpoint first
        for api_path in ["/panel/api/inbounds/list", "/panel/api/server/status"]:
            try:
                resp = await client.get(base + api_path)
                if resp.status_code == 200:
                    return
                elif resp.status_code == 401:
                    raise XuiApiError("API Token نامعتبر است (Bearer).")
            except XuiApiError:
                raise
            except Exception:
                continue
        return
    # Fallback to cookie login (Sanaei also supports it)
    base = _get_panel_url(server)
    username = str((server or {}).get("xui_username") or "").strip()
    password = str((server or {}).get("xui_password") or "").strip()
    if not username or not password:
        raise XuiApiError("برای پنل Sanaei یا توکن یا username/password لازم است.")
    try:
        resp = await client.post(base + "/login", json={"username": username, "password": password})
    except httpx.RequestError as exc:
        raise XuiApiError(f"عدم دسترسی به پنل Sanaei ({base}/login): {exc}") from exc
    text = (resp.text or "").strip()
    if not text:
        raise XuiApiError("پاسخ لاگین پنل Sanaei خالی بود.")
    try:
        data = json.loads(text)
    except ValueError:
        data = {}
    if isinstance(data, dict) and data.get("success") is False:
        raise XuiApiError("احراز هویت در پنل Sanaei ناموفق بود.")
    if resp.status_code >= 400:
        raise XuiApiError(f"خطا در ورود به پنل Sanaei: HTTP {resp.status_code} {text[:300]}")


async def _acquire_xui_client(server: Dict[str, Any]) -> "httpx.AsyncClient":
    key = _server_cache_key(server)
    async_lock = _xui_client_async_locks.get(key)
    if async_lock is None:
        async_lock = asyncio.Lock()
        _xui_client_async_locks[key] = async_lock
    async with async_lock:
        now = time.monotonic()
        try:
            cur_loop = asyncio.get_running_loop()
        except RuntimeError:
            cur_loop = None
        cur_thread = threading.get_ident()
        with _xui_cache_lock:
            cached = _XUI_SESSION_CACHE.get(key)
            if cached is not None:
                try:
                    exp, c_cli, c_loop, c_thread = cached
                except ValueError:
                    exp, c_cli = cached
                    c_loop, c_thread = None, None
                if now < exp and not c_cli.is_closed and c_loop is cur_loop and c_thread == cur_thread:
                    return c_cli
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
            existing = _XUI_SESSION_CACHE.get(key)
            if existing is not None:
                try:
                    exp2, e_cli, e_loop, e_thread = existing
                except ValueError:
                    exp2, e_cli = existing
                    e_loop, e_thread = None, None
                if time.monotonic() < exp2 and not e_cli.is_closed and e_loop is cur_loop and e_thread == cur_thread and e_cli is not client:
                    try:
                        await client.aclose()
                    except Exception:
                        pass
                    return e_cli
            old = existing
            _XUI_SESSION_CACHE[key] = (time.monotonic() + _XUI_SESSION_TTL_SECONDS, client, cur_loop, cur_thread)
            superseded = None
            if old is not None:
                try:
                    _, old_cli, _, _ = old
                    superseded = old_cli if old_cli is not client else None
                except ValueError:
                    try:
                        _, old_cli = old
                        superseded = old_cli if old_cli is not client else None
                    except Exception:
                        superseded = None
        if superseded is not None and not superseded.is_closed:
            try:
                await superseded.aclose()
            except Exception:
                pass
        return client


async def _refresh_xui_client(server: Dict[str, Any], *, insecure: bool = False) -> "httpx.AsyncClient":
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
    try:
        cur_loop = asyncio.get_running_loop()
    except RuntimeError:
        cur_loop = None
    cur_thread = threading.get_ident()
    with _xui_cache_lock:
        old = _XUI_SESSION_CACHE.get(key)
        _XUI_SESSION_CACHE[key] = (time.monotonic() + _XUI_SESSION_TTL_SECONDS, client, cur_loop, cur_thread)
        superseded = None
        if old is not None:
            try:
                _, old_cli, _, _ = old
                superseded = old_cli if old_cli is not client else None
            except ValueError:
                try:
                    _, old_cli = old
                    superseded = old_cli if old_cli is not client else None
                except Exception:
                    superseded = None
    if superseded is not None and not superseded.is_closed:
        try:
            await superseded.aclose()
        except Exception:
            pass
    return client


# ---------------------------------------------------------------------------
# Inbounds / Clients caches
# ---------------------------------------------------------------------------
_XUI_INBOUNDS_CACHE: Dict[Any, Tuple[float, List[Dict[str, Any]]]] = {}
_XUI_CLIENTS_CACHE: Dict[Any, Tuple[float, List[Dict[str, Any]]]] = {}
_XUI_ONLINES_CACHE: Dict[Any, Tuple[float, set]] = {}
_XUI_LASTONLINE_CACHE: Dict[Any, Tuple[float, Dict[str, str]]] = {}
_XUI_INBOUNDS_TTL = float(os.getenv("XUI_INBOUNDS_CACHE_SECONDS", "15") or "15")
_XUI_CLIENTS_TTL = float(os.getenv("XUI_CLIENTS_CACHE_SECONDS", "15") or "15")
_XUI_ONLINES_TTL = float(os.getenv("XUI_ONLINES_CACHE_SECONDS", "15") or "15")
_XUI_LASTONLINE_TTL = float(os.getenv("XUI_LASTONLINE_CACHE_SECONDS", "30") or "30")
_xui_inbounds_locks: Dict[Any, asyncio.Lock] = {}
_xui_clients_locks: Dict[Any, asyncio.Lock] = {}
_xui_onlines_locks: Dict[Any, asyncio.Lock] = {}
_xui_lastonline_locks: Dict[Any, asyncio.Lock] = {}


def _invalidate_caches(server: Dict[str, Any]) -> None:
    key = _server_cache_key(server)
    with _xui_cache_lock:
        _XUI_INBOUNDS_CACHE.pop(key, None)
        _XUI_CLIENTS_CACHE.pop(key, None)
        _XUI_ONLINES_CACHE.pop(key, None)
        _XUI_LASTONLINE_CACHE.pop(key, None)
    # همچنین کش hiddify_api برای همین سرور را پاک کن
    try:
        from Shared.hiddify_api import _hiddify_cache_lock, _HIDDIFY_CACHE
        with _hiddify_cache_lock:
            # پاک کردن هر کشی که مربوط به این سرور است
            for k in list(_HIDDIFY_CACHE.keys()):
                try:
                    if k[0] == key:
                        _HIDDIFY_CACHE.pop(k, None)
                except Exception:
                    pass
    except Exception:
        pass


async def _last_online_map(server: Dict[str, Any], *, _force_refresh: bool = False) -> Dict[str, str]:
    """Fetch lastOnline map: email -> 'YYYY-MM-DD HH:MM:SS' for Sanaei"""
    key = _server_cache_key(server)
    if not _force_refresh:
        with _xui_cache_lock:
            cached = _XUI_LASTONLINE_CACHE.get(key)
            if cached is not None and time.monotonic() < cached[0]:
                return cached[1]
            lock = _xui_lastonline_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _xui_lastonline_locks[key] = lock
    else:
        with _xui_cache_lock:
            lock = _xui_lastonline_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _xui_lastonline_locks[key] = lock
    async with lock:
        if not _force_refresh:
            with _xui_cache_lock:
                cached = _XUI_LASTONLINE_CACHE.get(key)
                if cached is not None and time.monotonic() < cached[0]:
                    return cached[1]
        try:
            async with _XuiContext(server) as ctx:
                data = await ctx.request("POST", "clients/lastOnline", allow_login_retry=False)
        except Exception as exc:
            logger.debug("sanaei lastOnline fetch failed: %s", exc)
            return {}
        out: Dict[str, str] = {}
        if isinstance(data, dict):
            for email, ts in data.items():
                try:
                    em = str(email).strip()
                    if not em:
                        continue
                    # ts may be int ms or sec
                    ts_int = int(ts)
                    if ts_int <= 0:
                        continue
                    # detect ms vs sec
                    if ts_int > 1000000000000:  # ms
                        dt = datetime.fromtimestamp(ts_int / 1000, tz=timezone.utc).replace(tzinfo=None)
                    elif ts_int > 1000000000:  # sec
                        dt = datetime.fromtimestamp(ts_int, tz=timezone.utc).replace(tzinfo=None)
                    else:
                        continue
                    out[em.lower()] = dt.strftime("%Y-%m-%d %H:%M:%S")
                    out[em] = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
        with _xui_cache_lock:
            _XUI_LASTONLINE_CACHE[key] = (time.monotonic() + _XUI_LASTONLINE_TTL, out)
        return out


async def _online_emails(server: Dict[str, Any], *, _force_refresh: bool = False) -> set:
    """Fetch online emails via Sanaei POST /panel/api/clients/onlines"""
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
                data = await ctx.request("POST", "clients/onlines", allow_login_retry=False)
        except Exception as exc:
            # Fallback to old inbounds/onlines for compat
            try:
                async with _XuiContext(server) as ctx2:
                    data = await ctx2.request("POST", "inbounds/onlines", allow_login_retry=False)
            except Exception as exc2:
                logger.debug("sanaei onlines fetch failed: %s / %s", exc, exc2)
                return set()
        out: set = set()
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    out.add(item.strip())
                    out.add(item.strip().lower())
                elif isinstance(item, dict):
                    # sometimes returns dict with email
                    for v in item.values():
                        if isinstance(v, str) and "@" in v:
                            out.add(v.strip())
                            out.add(v.strip().lower())
        elif isinstance(data, dict):
            # onlinesByNode style: { "0": ["a"], "1": ["b"] }
            for v in data.values():
                if isinstance(v, list):
                    for em in v:
                        if isinstance(em, str):
                            out.add(em.strip())
                            out.add(em.strip().lower())
        with _xui_cache_lock:
            _XUI_ONLINES_CACHE[key] = (time.monotonic() + _XUI_ONLINES_TTL, out)
        return out


class _XuiContext:
    def __init__(self, server: Dict[str, Any]):
        self.server = server
        self.base = _get_panel_url(server)
        self.api = f"{self.base}/panel/api"
        self.client: Optional[httpx.AsyncClient] = None
        self._mode = hiddify_api._get_ssl_mode()

    async def __aenter__(self) -> "_XuiContext":
        self.client = await _acquire_xui_client(self.server)
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.client = None

    async def close(self) -> None:
        self.client = None

    @staticmethod
    def _make_client(verify: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=hiddify_api._get_api_timeout_seconds(),
            verify=verify,
            follow_redirects=False,
        )

    async def _ensure_login(self) -> None:
        assert self.client is not None
        await _login_xui_client(self.client, self.server)

    async def request(self, method: str, path: str, *, json_body: Any = None, allow_login_retry: bool = True) -> Any:
        url = f"{self.api}/{path.lstrip('/')}"
        headers = {"Accept": "application/json, text/plain, */*"}
        assert self.client is not None
        for attempt in (1, 3):
            try:
                if self.client.is_closed:
                    self.client = await _acquire_xui_client(self.server)
                resp = await self.client.request(method, url, headers=headers, json=json_body)
            except RuntimeError as exc:
                low = str(exc).lower()
                if ("has been closed" in low or "event loop is closed" in low or "closed" in low) and attempt == 1:
                    try:
                        self.client = await _refresh_xui_client(self.server)
                    except Exception:
                        self.client = await _acquire_xui_client(self.server)
                    continue
                raise XuiApiError(f"خطا در ارتباط با پنل Sanaei: {exc}") from exc
            except httpx.RequestError as exc:
                msg = str(exc).strip() or exc.__class__.__name__
                if hiddify_api._is_transient_network_error(exc) or isinstance(exc, (httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)):
                    if attempt == 1:
                        await asyncio.sleep(0.6)
                        try:
                            self.client = await _acquire_xui_client(self.server)
                        except Exception:
                            pass
                        continue
                    raise XuiApiError(f"خطای شبکه در ارتباط با پنل Sanaei: {msg}") from exc
                if self._mode == hiddify_api.SSL_MODE_AUTO and hiddify_api._looks_like_tls_error(exc) and attempt == 1:
                    self.client = await _refresh_xui_client(self.server, insecure=True)
                    continue
                raise XuiApiError(f"خطا در ارتباط با پنل Sanaei: {msg}") from exc
            if resp.status_code == 401 and allow_login_retry and attempt == 1:
                await self._ensure_login()
                continue
            break
        if resp.status_code >= 400:
            raise XuiApiError(f"Sanaei API HTTP {resp.status_code} {path}: {resp.text[:400]}")
        ct = (resp.headers.get("content-type") or "").lower()
        if "json" not in ct:
            return resp.text
        try:
            data = resp.json()
        except ValueError:
            return resp.text
        if isinstance(data, dict):
            if data.get("success") is False:
                raise XuiApiError(f"Sanaei API error ({path}): {data.get('msg') or 'unknown'}")
            if "obj" in data:
                return data.get("obj")
        return data

    async def raw_request(self, method: str, path: str, *, allow_login_retry: bool = True) -> "httpx.Response":
        url = f"{self.api}/{path.lstrip('/')}"
        headers = {"Accept": "*/*"}
        assert self.client is not None
        resp: Optional[httpx.Response] = None
        for attempt in (1, 3):
            try:
                if self.client.is_closed:
                    self.client = await _acquire_xui_client(self.server)
                resp = await self.client.request(method, url, headers=headers)
            except RuntimeError as exc:
                low = str(exc).lower()
                if ("has been closed" in low or "event loop is closed" in low or "closed" in low) and attempt == 1:
                    try:
                        self.client = await _refresh_xui_client(self.server)
                    except Exception:
                        self.client = await _acquire_xui_client(self.server)
                    continue
                raise XuiApiError(f"خطا در ارتباط با پنل Sanaei: {exc}") from exc
            except httpx.RequestError as exc:
                msg = str(exc).strip() or exc.__class__.__name__
                if hiddify_api._is_transient_network_error(exc) or isinstance(exc, (httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)):
                    if attempt == 1:
                        await asyncio.sleep(0.6)
                        try:
                            self.client = await _acquire_xui_client(self.server)
                        except Exception:
                            pass
                        continue
                    raise XuiApiError(f"خطای شبکه در ارتباط با پنل Sanaei: {msg}") from exc
                if self._mode == hiddify_api.SSL_MODE_AUTO and hiddify_api._looks_like_tls_error(exc) and attempt == 1:
                    self.client = await _refresh_xui_client(self.server, insecure=True)
                    continue
                raise XuiApiError(f"خطا در ارتباط با پنل Sanaei: {msg}") from exc
            if resp.status_code == 401 and allow_login_retry and attempt == 1:
                await self._ensure_login()
                continue
            break
        if resp is None:
            raise XuiApiError("Sanaei raw request produced no response.")
        if resp.status_code >= 400:
            raise XuiApiError(f"Sanaei API HTTP {resp.status_code} {path}: {resp.text[:400]}")
        return resp


# ---------------------------------------------------------------------------
# Caches helpers
# ---------------------------------------------------------------------------

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
            data = await ctx.request("GET", "inbounds/list")
        if not isinstance(data, list):
            raise XuiApiError("پاسخ لیست اینباندهای Sanaei شکل آرایه ندارد.")
        ttl = 2 if len(data) == 0 else _XUI_INBOUNDS_TTL
        with _xui_cache_lock:
            _XUI_INBOUNDS_CACHE[key] = (time.monotonic() + ttl, data)
        return data


async def _list_clients(server: Dict[str, Any], *, _force_refresh: bool = False) -> List[Dict[str, Any]]:
    """List all clients via new Sanaei endpoint GET /panel/api/clients/list"""
    key = _server_cache_key(server)
    if not _force_refresh:
        with _xui_cache_lock:
            cached = _XUI_CLIENTS_CACHE.get(key)
            if cached is not None and time.monotonic() < cached[0]:
                return cached[1]
            lock = _xui_clients_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _xui_clients_locks[key] = lock
    else:
        with _xui_cache_lock:
            lock = _xui_clients_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _xui_clients_locks[key] = lock
    async with lock:
        if not _force_refresh:
            with _xui_cache_lock:
                cached = _XUI_CLIENTS_CACHE.get(key)
                if cached is not None and time.monotonic() < cached[0]:
                    return cached[1]
        async with _XuiContext(server) as ctx:
            try:
                data = await ctx.request("GET", "clients/list")
            except XuiApiError as e:
                # Fallback for older Sanaei that still uses inbounds only
                if "404" in str(e):
                    # fallback to inbounds aggregation
                    return []
                raise
        if not isinstance(data, list):
            raise XuiApiError("پاسخ لیست کلاینت‌های Sanaei شکل آرایه ندارد.")
        # اگر پنل خالیه (بعد DELETE دستی)، کش را کوتاه نگه دار تا سریع درست شود
        ttl = 2 if len(data) == 0 else _XUI_CLIENTS_TTL
        with _xui_cache_lock:
            _XUI_CLIENTS_CACHE[key] = (time.monotonic() + ttl, data)
        return data


# ---------------------------------------------------------------------------
# Sanaei client helpers
# ---------------------------------------------------------------------------

def _sanaei_find_client(clients: List[Dict[str, Any]], user_uuid: str) -> Optional[Dict[str, Any]]:
    needle = str(user_uuid or "").strip().lower()
    if not needle:
        return None
    for c in clients:
        if not isinstance(c, dict):
            continue
        # Prioritize uuid/subId/email over numeric DB id
        hay = (
            str(c.get("email") or "").strip().lower(),
            str(c.get("subId") or "").strip().lower(),
            str(c.get("uuid") or "").strip().lower(),
            str(c.get("password") or "").strip().lower(),
        )
        # Only include numeric id if it looks like uuid (not pure int)
        id_val = c.get("id")
        if id_val is not None:
            id_str = str(id_val).strip()
            # if id contains '-' it's uuid-like, include; if pure digits it's DB pk, skip for uuid lookup
            if "-" in id_str or len(id_str) > 20:
                hay = hay + (id_str.lower(),)
        if needle in hay:
            return c
    return None


def _sanaei_normalize(client: Dict[str, Any], server: Dict[str, Any], *, onlines: Optional[set] = None, last_online_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Normalize Sanaei client record to bot's user shape"""
    email = str(client.get("email") or "").strip()
    # Sanaei: numeric id is DB pk, uuid is Xray UUID, subId is subscription UUID
    # Priority: uuid (Xray) -> subId (sub) -> id if uuid-like -> email
    # For our bot, subId == uuid (generated), so use subId first for sub link
    sub_id = str(client.get("subId") or "").strip()
    uuid_field = str(client.get("uuid") or "").strip()
    id_val = client.get("id")
    id_str = str(id_val).strip() if id_val is not None else ""
    # Prefer uuid/subId that look like uuid
    if uuid_field and "-" in uuid_field:
        uuid = uuid_field
    elif sub_id and "-" in sub_id:
        uuid = sub_id
    elif id_str and "-" in id_str:
        uuid = id_str
    elif sub_id:
        uuid = sub_id
    elif uuid_field:
        uuid = uuid_field
    else:
        uuid = email or id_str
    # traffic
    traffic = client.get("traffic") or {}
    up = _to_int(traffic.get("up"), 0) if isinstance(traffic, dict) else 0
    down = _to_int(traffic.get("down"), 0) if isinstance(traffic, dict) else 0
    # Fallback to direct up/down if not in traffic
    if up == 0 and down == 0:
        up = _to_int(client.get("up"), 0)
        down = _to_int(client.get("down"), 0)
    used_gb = _bytes_to_gb(up + down)
    limit_gb = _bytes_to_gb(client.get("totalGB"))
    enable = bool(client.get("enable", True))
    expiry_ms = _to_int(client.get("expiryTime"), 0)
    # Sanaei expiry is ms
    from Shared.xui_common import _ms_to_datetime
    expire_dt = _ms_to_datetime(expiry_ms)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    days_left = (expire_dt.date() - now.date()).days if expire_dt else None
    # inboundIds
    inbound_ids = client.get("inboundIds") or []
    first_inbound = int(inbound_ids[0]) if isinstance(inbound_ids, list) and inbound_ids else 0
    # online detection via POST /panel/api/clients/onlines
    online = False
    last_online_str = None
    if onlines:
        # check email, uuid, subId in onlines (case-insensitive)
        candidates = {email.lower(), uuid.lower(), sub_id.lower(), str(client.get("uuid") or "").strip().lower()}
        # also add email without lower for direct match
        candidates |= {email, uuid, sub_id}
        if any(c and c.lower() in {x.lower() for x in onlines} for c in candidates if c):
            online = True
        elif any(c in onlines for c in candidates if c):
            online = True
    if online:
        last_online_str = now.strftime("%Y-%m-%d %H:%M:%S")
    else:
        # Try lastOnline map first (most accurate for Sanaei)
        if last_online_map:
            for cand in (email, email.lower(), uuid, uuid.lower(), sub_id, sub_id.lower()):
                if cand and cand in last_online_map:
                    last_online_str = last_online_map[cand]
                    break
                if cand and cand.lower() in last_online_map:
                    last_online_str = last_online_map[cand.lower()]
                    break
        # Fallback to client's own lastOnline field
        if not last_online_str:
            lo = client.get("lastOnline") or client.get("last_online") or client.get("onlineAt")
            if lo:
                try:
                    lo_int = int(lo)
                    if lo_int > 1000000000:
                        if lo_int > 1000000000000:
                            lo_dt = datetime.fromtimestamp(lo_int / 1000, tz=timezone.utc).replace(tzinfo=None)
                        else:
                            lo_dt = datetime.fromtimestamp(lo_int, tz=timezone.utc).replace(tzinfo=None)
                        last_online_str = lo_dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

    return {
        "uuid": uuid,
        "id": uuid,
        "name": str(email or client.get("comment") or client.get("tgId") or uuid).strip(),
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
        "last_online": last_online_str,
        "_user_list_status": "online" if online else "offline",
        "subId": str(client.get("subId") or "").strip() or uuid,
        "inbound_id": first_inbound,
        "inboundIds": inbound_ids if isinstance(inbound_ids, list) else [],
        "protocol": "",
        "server_id": (server or {}).get("id"),
        "comment": str(client.get("comment") or "").strip() or str(client.get("tgId") or "").strip(),
        "_source": "xui",
        "_sanaei_raw": client,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def test_connect(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Try clients/list then inbounds/list for verification
    try:
        clients = await _list_clients(server, _force_refresh=True)
        # also get inbounds for wizard
        inbounds = await _list_inbounds(server, _force_refresh=True)
        return inbounds
    except Exception:
        return await _list_inbounds(server, _force_refresh=True)


async def list_users(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    """All clients via Sanaei clients/list, normalized"""
    clients = await _list_clients(server)
    # fetch onlines + lastOnline once for all users
    try:
        onlines = await _online_emails(server)
    except Exception:
        onlines = set()
    try:
        last_map = await _last_online_map(server)
    except Exception:
        last_map = {}
    if clients:
        out = []
        for c in clients:
            if not isinstance(c, dict):
                continue
            email = str(c.get("email") or "").strip()
            if not email:
                continue
            out.append(_sanaei_normalize(c, server, onlines=onlines, last_online_map=last_map))
        return out
    # Fallback: aggregate from inbounds (for older Sanaei without clients/list)
    inbounds = await _list_inbounds(server)
    from Shared.xui_common import _settings_clients, _candidate_inbounds, SUPPORTED_PROTOCOLS, _normalize_user, _find_stats_by_email, _bytes_to_gb as _b2g, _to_int as _ti
    # reuse Alireza aggregation logic
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
        total_up = 0
        total_down = 0
        for ib, cl in pairs:
            email = str(cl.get("email") or "").strip()
            stats = _find_stats_by_email(ib, email)
            total_up += _ti(stats.get("up"), 0)
            total_down += _ti(stats.get("down"), 0)
        inbound, client = pairs[0]
        norm = _normalize_user(client, inbound, server)
        norm["up"] = total_up
        norm["down"] = total_down
        norm["used_traffic"] = total_up + total_down
        norm["current_usage_GB"] = round(_b2g(total_up + total_down), 3)
        out.append(norm)
    return out


async def get_user_by_uuid(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    user_uuid = str(user_uuid or "").strip()
    if not user_uuid:
        raise XuiApiError("user uuid خالی است.")
    clients = await _list_clients(server)
    if clients:
        found = _sanaei_find_client(clients, user_uuid)
        if found:
            try:
                onlines = await _online_emails(server)
            except Exception:
                onlines = set()
            try:
                last_map = await _last_online_map(server)
            except Exception:
                last_map = {}
            return _sanaei_normalize(found, server, onlines=onlines, last_online_map=last_map)
        raise XuiApiError(f"user not found (uuid={user_uuid})")
    # Fallback to inbounds search
    inbounds = await _list_inbounds(server)
    pairs = _find_all_clients_for_uuid(inbounds, user_uuid)
    if not pairs:
        raise XuiApiError(f"user not found (uuid={user_uuid})")
    total_up = 0
    total_down = 0
    for ib, _cl in pairs:
        from Shared.xui_common import _find_stats_by_email
        email = str(_cl.get("email") or "").strip()
        stats = _find_stats_by_email(ib, email)
        total_up += _to_int(stats.get("up"), 0)
        total_down += _to_int(stats.get("down"), 0)
    inbound, client = pairs[0]
    from Shared.xui_common import _normalize_user, _bytes_to_gb as _b2g
    norm = _normalize_user(client, inbound, server)
    norm["up"] = total_up
    norm["down"] = total_down
    norm["used_traffic"] = total_up + total_down
    norm["current_usage_GB"] = round(_b2g(total_up + total_down), 3)
    return norm


async def create_user(server: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create client via POST /panel/api/clients/add"""
    # Resolve inbound targets
    inbounds = await _list_inbounds(server)
    from Shared.xui_common import _resolve_target_inbounds
    targets = _resolve_target_inbounds(server, inbounds)
    if not targets:
        raise XuiApiError("هیچ اینباند قابل‌فروشی برای ساخت کاربر یافت نشد.")
    inbound_ids = [int(ib.get("id") or 0) for ib in targets]

    requested = str((payload or {}).get("uuid") or "").strip()
    if requested and len(requested) >= 8 and " " not in requested:
        user_uuid = requested
    else:
        user_uuid = uuid4()

    raw_name = str(payload.get("name") or payload.get("email") or "").strip()
    base_email = _sanitize_xui_email(raw_name, user_uuid) if raw_name else user_uuid
    if raw_name:
        # Check existing emails via clients/list
        try:
            clients = await _list_clients(server)
            existing = {str(c.get("email") or "").strip() for c in clients if isinstance(c, dict)}
            existing_lower = {e.lower() for e in existing}
            # also add lower set for _unique check
            existing_set = existing | existing_lower
        except Exception:
            existing_set = _existing_xui_emails(inbounds)
        base_email = _unique_xui_email(base_email, existing_set, user_uuid)

    # Build Sanaei client payload
    # Sanaei expects: client {email, id, subId, totalGB, expiryTime, enable, tgId, limitIp, comment}
    gb = _gb_limit_from_payload(payload)
    total_bytes = 0
    if gb is not None:
        from Shared.xui_common import _gb_to_bytes
        total_bytes = _gb_to_bytes(gb)
    else:
        total_bytes = 0

    expiry_ms = _compute_expiry_ms(payload)
    enable = _enable_from_payload(payload)
    if enable is None:
        enable = True

    # tgId must be int64 (Sanaei) - store display name in email, note in comment
    tg_id_val = 0
    for k in ("tgId", "telegram_id", "tg_id"):
        if k in payload:
            try:
                tg_id_val = int(str(payload.get(k) or 0).strip() or 0)
                break
            except Exception:
                tg_id_val = 0
    # comment from payload (HiddifyBot:telegram_id) must be used, not raw_name
    comment_val = str(payload.get("comment") or "").strip()
    if not comment_val:
        comment_val = raw_name or base_email
    client_payload: Dict[str, Any] = {
        "email": base_email,
        "id": user_uuid,
        "subId": user_uuid,
        "totalGB": int(total_bytes),
        "expiryTime": int(expiry_ms),
        "enable": bool(enable),
        "tgId": int(tg_id_val),
        "limitIp": 0,
        "comment": comment_val,
    }
    # Optional: flow, limitIp from payload?
    if "limitIp" in payload:
        try:
            client_payload["limitIp"] = int(payload.get("limitIp") or 0)
        except Exception:
            pass

    body = {
        "client": client_payload,
        "inboundIds": inbound_ids,
    }

    async with _XuiContext(server) as ctx:
        try:
            await ctx.request("POST", "clients/add", json_body=body)
        except XuiApiError as e:
            # If already exists, try to find and return
            if "already" in str(e).lower() or "exists" in str(e).lower():
                # generate unique email and retry once
                suffix = uuid4()[:4]
                client_payload["email"] = f"{base_email[:60-len(suffix)]}{suffix}"
                body["client"] = client_payload
                await ctx.request("POST", "clients/add", json_body=body)
            else:
                raise

    _invalidate_caches(server)
    # Return normalized
    # Fetch newly created
    clients = await _list_clients(server, _force_refresh=True)
    found = _sanaei_find_client(clients, user_uuid)
    if found:
        return _sanaei_normalize(found, server)
    # Fallback: construct from payload
    dummy_inbound = targets[0] if targets else {}
    from Shared.xui_common import _normalize_user, _new_client_dict, _apply_payload_to_client
    # Build dummy for normalize compatibility
    return _sanaei_normalize(client_payload, server)


async def patch_user(server: Dict[str, Any], user_uuid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_uuid = str(user_uuid or "").strip()
    if not user_uuid:
        raise XuiApiError("user uuid خالی است.")

    clients = await _list_clients(server)
    target = _sanaei_find_client(clients, user_uuid)
    if not target:
        # Fallback to inbounds search then try update via clients/update anyway
        inbounds = await _list_inbounds(server)
        pairs = _find_all_clients_for_uuid(inbounds, user_uuid)
        if not pairs:
            raise XuiApiError(f"user not found (uuid={user_uuid})")
        # use first found email
        _, cl = pairs[0]
        email = str(cl.get("email") or "").strip()
        if not email:
            email = user_uuid
        target = {"email": email, "id": user_uuid, "subId": user_uuid}
        # need to fetch full via clients/get if possible
        try:
            async with _XuiContext(server) as ctx:
                data = await ctx.request("GET", f"clients/get/{quote(email, safe='')}")
                if isinstance(data, dict) and "client" in data:
                    target = data.get("client") or target
        except Exception:
            pass
    else:
        email = str(target.get("email") or "").strip()

    # Handle name change -> email change uniqueness
    raw_name_global = str(payload.get("name") or "").strip()
    new_email = email
    if raw_name_global:
        sanitized_base = _sanitize_xui_email(raw_name_global, user_uuid)
        # check uniqueness excluding current
        try:
            all_clients = await _list_clients(server)
            existing = {str(c.get("email") or "").strip() for c in all_clients if isinstance(c, dict) and str(c.get("email") or "").strip().lower() != email.lower()}
            existing_lower = {e.lower() for e in existing}
            existing_set = existing | existing_lower
        except Exception:
            inbounds = await _list_inbounds(server)
            existing_set = _existing_xui_emails(inbounds)
            existing_set.discard(email)
            existing_set.discard(email.lower())
        new_email = _unique_xui_email(sanitized_base, existing_set, user_uuid)

    # Build clean payload for Sanaei - only fields expected by Go struct
    # Do NOT copy full target (contains traffic/inboundIds/numeric id which cause unmarshal errors)
    gb = _gb_limit_from_payload(payload)
    cur_ms = _to_int(target.get("expiryTime"), 0)
    new_expiry = _compute_expiry_ms(payload, current_ms=cur_ms)
    enable_val = _enable_from_payload(payload)
    # Determine Xray UUID - prefer uuid/subId with dashes, fallback to user_uuid
    orig_uuid = str(target.get("uuid") or "").strip()
    orig_subId = str(target.get("subId") or "").strip()
    # orig id may be numeric DB pk - ignore if no dashes
    orig_id_str = str(target.get("id") or "").strip()
    xray_uuid = ""
    if orig_uuid and "-" in orig_uuid:
        xray_uuid = orig_uuid
    elif orig_subId and "-" in orig_subId:
        xray_uuid = orig_subId
    elif orig_id_str and "-" in orig_id_str:
        xray_uuid = orig_id_str
    else:
        xray_uuid = orig_uuid or orig_subId or orig_id_str or user_uuid
    # Ensure xray_uuid is uuid-like, fallback to user_uuid
    if not xray_uuid or "-" not in xray_uuid:
        xray_uuid = user_uuid

    # tgId must be int
    try:
        orig_tg = int(str(target.get("tgId") or 0).strip() or 0)
    except Exception:
        orig_tg = 0
    # limitIp
    try:
        orig_limitIp = int(str(target.get("limitIp") or 0).strip() or 0)
    except Exception:
        orig_limitIp = 0

    # Determine final values
    if gb is not None:
        from Shared.xui_common import _gb_to_bytes
        final_total = int(_gb_to_bytes(gb))
    else:
        try:
            final_total = int(str(target.get("totalGB") or 0).strip() or 0)
        except Exception:
            final_total = 0

    if enable_val is not None:
        final_enable = bool(enable_val)
    else:
        final_enable = bool(target.get("enable", True))

    # comment - handle separately from name (do not overwrite note when name changes)
    if "comment" in payload:
        final_comment = str(payload.get("comment") or "").strip()
        final_email = email  # changing note should not change email
        # if name also changed, use new_email
        if raw_name_global:
            final_email = new_email
            # keep comment as provided comment, not name
            final_comment = str(payload.get("comment") or "").strip()
    elif raw_name_global:
        # name change without explicit comment -> keep old comment, only email changes
        final_comment = str(target.get("comment") or "").strip()
        final_email = new_email
        # fallback: if old comment was same as old email/name, update to new name? No, keep old
    else:
        final_comment = str(target.get("comment") or "").strip()
        final_email = email

    # Allow numeric tgId override from payload
    final_tg = orig_tg
    for k in ("tgId", "telegram_id", "tg_id"):
        if k in payload:
            try:
                final_tg = int(str(payload.get(k) or orig_tg).strip() or 0)
                break
            except Exception:
                pass

    # Build minimal clean body - Sanaei update expects email as key, plus other fields
    # Do not include numeric DB id, traffic, inboundIds
    updated = {
        "email": final_email,
        "subId": orig_subId or xray_uuid,
        "uuid": xray_uuid,
        "id": xray_uuid,  # for VLESS/VMess, id is Xray UUID string (not numeric)
        "totalGB": int(final_total),
        "expiryTime": int(new_expiry),
        "enable": bool(final_enable),
        "tgId": int(final_tg),
        "limitIp": int(orig_limitIp),
        "comment": str(final_comment or ""),
    }
    # Preserve limitHwid if present
    if "limitHwid" in target:
        try:
            updated["limitHwid"] = int(str(target.get("limitHwid") or 0).strip() or 0)
        except Exception:
            pass
    # Preserve flow if present (VLESS flow)
    if target.get("flow"):
        updated["flow"] = str(target.get("flow") or "").strip()

    async with _XuiContext(server) as ctx:
        await ctx.request("POST", f"clients/update/{quote(email, safe='')}", json_body=updated)
        if _should_reset_traffic(payload):
            # Sanaei: no direct reset per client, but we can try traffic reset if endpoint exists
            try:
                await ctx.request("POST", f"clients/resetTraffic/{quote(new_email, safe='')}", allow_login_retry=False)
            except Exception:
                pass

    _invalidate_caches(server)
    # Fetch updated
    clients = await _list_clients(server, _force_refresh=True)
    found = _sanaei_find_client(clients, new_email) or _sanaei_find_client(clients, user_uuid)
    if found:
        return _sanaei_normalize(found, server)
    # fallback return updated normalized
    return _sanaei_normalize(updated, server)


async def enable_user(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    return await patch_user(server, user_uuid, {"is_active": True, "enable": True})


async def disable_user(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    return await patch_user(server, user_uuid, {"is_active": False, "enable": False})


async def delete_user(server: Dict[str, Any], user_uuid: str) -> None:
    user_uuid = str(user_uuid or "").strip()
    if not user_uuid:
        raise XuiApiError("user uuid خالی است.")
    clients = await _list_clients(server)
    target = _sanaei_find_client(clients, user_uuid)
    if not target:
        inbounds = await _list_inbounds(server)
        pairs = _find_all_clients_for_uuid(inbounds, user_uuid)
        if not pairs:
            raise XuiApiError(f"user not found (uuid={user_uuid})")
        _, cl = pairs[0]
        email = str(cl.get("email") or "").strip() or user_uuid
    else:
        email = str(target.get("email") or "").strip()
    async with _XuiContext(server) as ctx:
        await ctx.request("POST", f"clients/del/{quote(email, safe='')}")
    _invalidate_caches(server)


async def get_subscription_url(server: Dict[str, Any], user_uuid: str) -> str:
    user_uuid = str(user_uuid or "").strip()
    if not user_uuid:
        return ""
    origin = _public_origin(server)
    return f"{origin}{_sub_path(server)}{user_uuid}"


async def get_user_configs(server: Dict[str, Any], user_uuid: str) -> List[Dict[str, Any]]:
    user_uuid = str(user_uuid or "").strip()
    items: List[Dict[str, Any]] = []
    if not user_uuid:
        return items
    sub_url = await get_subscription_url(server, user_uuid)
    if sub_url:
        items.append({"link": sub_url, "name": "Subscription", "protocol": "sub", "security": "", "transport": "", "_source": "xui"})
    # Try to get direct links via Sanaei clients/links endpoint
    try:
        clients = await _list_clients(server)
        target = _sanaei_find_client(clients, user_uuid)
        email = str(target.get("email") or "").strip() if target else ""
        if email:
            async with _XuiContext(server) as ctx:
                try:
                    links = await ctx.request("GET", f"clients/links/{quote(email, safe='')}")
                    if isinstance(links, list):
                        for ln in links:
                            if isinstance(ln, str) and ln.strip():
                                items.append({"link": ln.strip(), "protocol": "auto", "_source": "xui"})
                                continue
                            if isinstance(ln, dict):
                                link = str(ln.get("link") or ln.get("url") or "").strip()
                                if link:
                                    items.append({"link": link, "protocol": str(ln.get("protocol") or "auto"), "_source": "xui"})
                except Exception:
                    # fallback to subLinks
                    try:
                        sub_id = str(target.get("subId") or user_uuid).strip()
                        links2 = await ctx.request("GET", f"clients/subLinks/{quote(sub_id, safe='')}")
                        if isinstance(links2, list):
                            for ln in links2:
                                if isinstance(ln, str) and "://" in ln:
                                    items.append({"link": ln, "protocol": "auto", "_source": "xui"})
                    except Exception:
                        pass
    except Exception as exc:
        logger.debug("sanaei get_user_configs fallback: %s", exc)
    # If still only subscription, try inbound direct links fallback
    if len(items) <= 1:
        try:
            inbounds = await _list_inbounds(server)
            pairs = _find_all_clients_for_uuid(inbounds, user_uuid)
            from Shared.xui_common import _settings_clients
            # Build direct links using common builders
            for inbound, client in pairs:
                links = _build_direct_links(server, inbound, client)
                for link in links:
                    items.append({"link": link, "protocol": (inbound.get("protocol") or "").lower(), "_source": "xui"})
        except Exception:
            pass
    return items


def _build_direct_links(server: Dict[str, Any], inbound: Dict[str, Any], client: Dict[str, Any]) -> List[str]:
    # Reuse builders from common logic (duplicate from alireza)
    import base64 as _b64
    import json as _json
    from Shared.xui_common import _parse_json, _to_int as _ti, _inbound_host_and_port, _first_sni, _network_query_extras, _query_string

    protocol = (inbound.get("protocol") or "").strip().lower()

    def _vless():
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
        for k, v in _network_query_extras(network, stream).items():
            pairs.append((k, v))
        query = _query_string(pairs)
        name = f"{inbound.get('remark') or server.get('title') or 'xui'}-vless"
        return f"vless://{cid}@{host}:{port}?{query}#{urllib.parse.quote(name, safe='')}"

    def _vmess():
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
        return "vmess://" + _b64.urlsafe_b64encode(_json.dumps(vmess, ensure_ascii=False).encode("utf-8")).decode("ascii")

    def _trojan():
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
        pairs.append(("type", network))
        for k, v in _network_query_extras(network, stream).items():
            pairs.append((k, v))
        query = _query_string(pairs)
        name = f"{inbound.get('remark') or server.get('title') or 'xui'}-trojan"
        return f"trojan://{urllib.parse.quote(password, safe='')}@{host}:{port}?{query}#{urllib.parse.quote(name, safe='')}"

    builders = {"vless": _vless, "vmess": _vmess, "trojan": _trojan}
    b = builders.get(protocol)
    if not b:
        return []
    try:
        link = b()
    except Exception:
        return []
    return [link] if link else []


async def get_server_stats(server: Dict[str, Any]) -> Dict[str, Any]:
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
    # users count from clients/list
    try:
        clients = await _list_clients(server)
        out["users_total"] = len([c for c in clients if isinstance(c, dict) and str(c.get("email") or "").strip()])
        # sum traffic
        total_up = 0
        total_down = 0
        for c in clients:
            if not isinstance(c, dict):
                continue
            tr = c.get("traffic") or {}
            if isinstance(tr, dict):
                total_up += _to_int(tr.get("up"), 0)
                total_down += _to_int(tr.get("down"), 0)
        out["usage_30days_gb"] = round(_bytes_to_gb(total_up + total_down), 3)
    except Exception:
        pass

    try:
        async with _XuiContext(server) as ctx:
            data = await ctx.request("GET", "server/status")
    except Exception as exc:
        logger.warning("sanaei server status failed: %s", exc)
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
    return out


async def download_server_backup(server: Dict[str, Any]) -> Dict[str, Any]:
    source_url = f"{_get_panel_url(server)}/panel/api/server/getDb"
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
# Subscription fetch
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
            logger.debug("sanaei sub fetch HTTP %s for %s", resp.status_code, sub_url)
            raise XuiApiError(f"subscription not available: HTTP {resp.status_code}")
        text = resp.text or ""
    lines = _decode_sub_body(text)
    if not lines:
        raise XuiApiError("subscription body empty or undecodable")
    return lines


async def fetch_subscription_lines(server: Dict[str, Any], user_uuid: str) -> List[str]:
    return await _fetch_subscription_lines(server, user_uuid)


def sync_fetch_subscription_lines(server: Dict[str, Any], user_uuid: str) -> List[str]:
    return sync_run(_fetch_subscription_lines(server, user_uuid))


# ---------------------------------------------------------------------------
# Inbound helpers (create from link, sync)
# ---------------------------------------------------------------------------

async def create_inbound_from_link(
    server: Dict[str, Any],
    link: str,
    *,
    port_override: Optional[int] = None,
    remark: Optional[str] = None,
) -> Dict[str, Any]:
    # Reuse alireza logic but via Sanaei endpoint /panel/api/inbounds/add
    # Import parse helpers from alireza to avoid duplication
    try:
        from Shared.xui_alireza import parse_config_link, _build_inbound_json, _stream_settings_for_parsed
    except Exception:
        raise XuiApiError("create_inbound_from_link not available for Sanaei without alireza module")

    parsed = parse_config_link(link)
    panel_domain = ""
    try:
        origin = _public_origin(server)
        panel_domain = urlparse(origin).hostname or ""
    except Exception:
        panel_domain = ""
    orig_host = (parsed.get("host") or "").strip()
    if panel_domain and orig_host and orig_host.lower() != panel_domain.lower():
        if parsed.get("sni") and parsed["sni"].lower() == orig_host.lower():
            parsed["sni"] = panel_domain
        if parsed.get("host_header") and parsed["host_header"].lower() == orig_host.lower():
            parsed["host_header"] = panel_domain
    port = int(port_override) if port_override else int(parsed.get("port") or 443)
    inbounds = await _list_inbounds(server)
    used_ports = {_to_int(ib.get("port"), 0) for ib in inbounds}
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
        alt = port + 1
        while alt in used_ports and alt < 65535:
            alt += 1
        raise XuiApiError(f"پورت {port} قبلاً استفاده شده. پورت دیگری بدهید (مثلاً {alt}).")
    protocol = (parsed.get("protocol") or "").strip().lower()
    if not remark:
        try:
            if "#" in link:
                remark = urllib.parse.unquote(link.split("#", 1)[1]).strip()
        except Exception:
            remark = ""
        if not remark:
            remark = f"{protocol}-{port}"
    inbound_json = _build_inbound_json(protocol, port, parsed, remark, server=server)
    # For Sanaei, settings/streamSettings may be sent as objects, not strings
    # Convert back if needed: try to json.loads if string
    for k in ("settings", "streamSettings", "sniffing"):
        if isinstance(inbound_json.get(k), str):
            try:
                inbound_json[k] = json.loads(inbound_json[k])
            except Exception:
                pass
    # لاگ payload برای دیباگ validation
    try:
        logger.debug("Sanaei create inbound payload port=%s proto=%s remark=%s json=%s", port, protocol, remark, json.dumps(inbound_json, ensure_ascii=False)[:3000])
    except Exception:
        pass
    async with _XuiContext(server) as ctx:
        try:
            resp = await ctx.request("POST", "inbounds/add", json_body=inbound_json)
        except XuiApiError as e:
            msg = str(e).lower()
            # لاگ کامل validation برای دیباگ
            try:
                logger.warning("Sanaei inbound add failed port=%s proto=%s err=%s payload=%s", port, protocol, e, json.dumps(inbound_json, ensure_ascii=False)[:4000])
            except Exception:
                pass
            if "timed out" in msg or "timeout" in msg:
                await asyncio.sleep(2)
                try:
                    inbounds2 = await _list_inbounds(server)
                    for ib in inbounds2:
                        if _to_int(ib.get("port"), 0) == port and (ib.get("protocol") or "").lower() == protocol:
                            return {"id": ib.get("id"), "port": port, "recovered_after_timeout": True}
                except Exception:
                    pass
            raise
    return resp if isinstance(resp, dict) else {"raw": resp}


async def sync_users_to_inbounds(server: Dict[str, Any]) -> Dict[str, Any]:
    """Sync users to all target inbounds via Sanaei attach API"""
    inbounds = await _list_inbounds(server)
    from Shared.xui_common import _resolve_target_inbounds
    targets = _resolve_target_inbounds(server, inbounds)
    if not targets:
        return {"ok": False, "msg": "هیچ اینباند هدف یافت نشد.", "created": 0, "skipped": 0}
    clients = await _list_clients(server)
    # map email -> client
    email_to_client = {str(c.get("email") or "").strip().lower(): c for c in clients if isinstance(c, dict) and str(c.get("email") or "").strip()}
    # For fallback inbounds aggregation if clients empty, collect from inbounds
    if not email_to_client:
        seen: Dict[str, Any] = {}
        for ib in inbounds:
            for cl in _settings_clients(ib.get("settings")):
                email = str(cl.get("email") or "").strip()
                if not email:
                    continue
                low = email.lower()
                if low not in seen:
                    seen[low] = cl
        # Need to create these via clients/add then attach? Skip for now
        return {"ok": True, "created": 0, "skipped": len(seen), "errors": [], "total_users": len(seen), "target_inbounds": len(targets)}

    target_ids = [int(t.get("id") or 0) for t in targets]
    created = 0
    skipped = 0
    errors: List[str] = []

    for email_low, client in email_to_client.items():
        current_ids = client.get("inboundIds") or []
        if not isinstance(current_ids, list):
            current_ids = []
        missing = [tid for tid in target_ids if tid not in current_ids]
        if not missing:
            skipped += 1
            continue
        email = str(client.get("email") or "").strip()
        # برای اینکه inbounds.settings هم آپدیت شود، بعد از attach یک بار هم inbounds را مستقیم چک کن
        # و اگر باز هم در inbounds.settings نبود، fallback به addClient
        try:
            async with _XuiContext(server) as ctx:
                await ctx.request("POST", f"clients/{quote(email, safe='')}/attach", json_body={"inboundIds": missing})
            # بعد از attach، کش را پاک کن و دوباره چک کن که واقعا در inbounds.settings هم آمده
            _invalidate_caches(server)
            # یک بار inbounds را دوباره بخوان و ببین کدام inbound هنوز ندارد
            try:
                inbounds_fresh = await _list_inbounds(server)
                for tid in missing[:]:
                    # پیدا کن inbound با این id
                    ib = next((x for x in inbounds_fresh if int(x.get("id") or 0) == tid), None)
                    if not ib:
                        continue
                    found_in_settings = False
                    for cl in _settings_clients(ib.get("settings")):
                        if str(cl.get("email") or "").strip().lower() == email_low:
                            found_in_settings = True
                            break
                        # همچنین subId را چک کن
                        if str(cl.get("subId") or "").strip().lower() == str(client.get("subId") or "").strip().lower():
                            found_in_settings = True
                            break
                    if not found_in_settings:
                        # fallback: مستقیم به inbounds/addClient
                        try:
                            # برای X-UI Sanaei، addClient via inbounds/addClient
                            # از client اصلی به عنوان template استفاده کن
                            from Shared.xui_common import _new_client_dict
                            proto = str(ib.get("protocol") or "").strip().lower() or "vless"
                            # پیدا کن uuid اصلی
                            uuid = str(client.get("subId") or client.get("id") or client.get("email") or "").strip()
                            if not uuid:
                                uuid = str(client.get("uuid") or "").strip()
                            if uuid:
                                new_cl = _new_client_dict(proto, uuid=uuid, template=client)
                                # کپی quota/expiry
                                for k in ("totalGB", "expiryTime", "enable", "limitIp"):
                                    if k in client:
                                        new_cl[k] = client[k]
                                # برای inbounds/addClient
                                async with _XuiContext(server) as ctx2:
                                    await ctx2.request("POST", f"inbounds/{tid}/addClient", json_body={"client": new_cl})
                                created += 1
                                # از missing کم کن چون با fallback ساخته شد
                                # (attach قبلا 1 شمرده، پس اینجا دوباره نشمار)
                        except Exception as e2:
                            errors.append(f"{email}->{tid} fallback addClient: {e2}")
            except Exception:
                pass
            created += len(missing)
        except Exception as e:
            errors.append(f"{email}->{missing}: {e}")

    _invalidate_caches(server)
    return {"ok": True, "created": created, "skipped": skipped, "errors": errors, "total_users": len(email_to_client), "target_inbounds": len(targets)}
