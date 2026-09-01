"""
Shared/pasarguard_api.py
========================
PasarGuard panel REST API client.

PasarGuard (https://github.com/PasarGuard/panel) is a Marzban fork and keeps
the Marzban REST contract:

    POST   /api/admin/token              → JWT (form: username/password)
    POST   /api/user                     → create user
    GET    /api/user/{username}          → get user
    PUT    /api/user/{username}          → modify user
    DELETE /api/user/{username}          → delete user
    GET    /api/users                    → list users
    POST   /api/user/{username}/reset    → reset data usage
    POST   /api/user/{username}/revoke_sub → revoke subscription
    GET    /api/inbounds                 → inbounds grouped by node
    GET    /api/system                   → system stats
    GET    /api/system/backup            → database backup

This module delegates to marzban_api (single HTTP implementation) and exists
so that any future PasarGuard-specific difference can be patched HERE without
touching the Marzban client.  Payload/response handling is identical, so all
callers use the same normalized user shape (see marzban_api.normalize_user).

Panel type in DB:  ``panel_type == "pasarguard"``
Routing happens via ``multi_panel.panel_api(server)`` and
``hiddify_api._marzban_dispatch``.
"""

import logging
from typing import Any, Dict, List

from Shared import marzban_api

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors — subclass so every ``except MarzbanApiError`` also catches these
# ---------------------------------------------------------------------------
class PasarGuardApiError(marzban_api.MarzbanApiError):
    """Generic PasarGuard API error (Marzban-compatible)."""
    pass


# ---------------------------------------------------------------------------
# Endpoint override table
# ---------------------------------------------------------------------------
# PasarGuard currently matches Marzban paths 1:1.  If a future PasarGuard
# release renames an endpoint, override it here (relative to panel base URL).
_ENDPOINTS: Dict[str, str] = {
    "token":        "/api/admin/token",
    "create_user":  "/api/user",
    "get_user":     "/api/user/{username}",
    "list_users":   "/api/users",
    "reset_usage":  "/api/user/{username}/reset",
    "revoke_sub":   "/api/user/{username}/revoke_sub",
    "active_next":  "/api/user/{username}/active-next",
    "usage":        "/api/user/{username}/usage",
    "inbounds":     "/api/inbounds",
    "system":       "/api/system",
    "system_backup": "/api/system/backup",
}


def _ep(name: str, username: str = "") -> str:
    """Resolve endpoint path (relative to panel base URL) with username substitution."""
    path = _ENDPOINTS.get(name, "")
    return path.format(username=username) if "{username}" in path else path


def _translate(exc: Exception) -> Exception:
    """Re-raise PasarGuard-aware errors."""
    if isinstance(exc, marzban_api.MarzbanApiError):
        return PasarGuardApiError(str(exc))
    return exc


def _call(marzban_func_name: str, *args: Any, **kwargs: Any):
    """Await a marzban_api coroutine and translate its errors."""
    async def _runner():
        try:
            return await getattr(marzban_api, marzban_func_name)(*args, **kwargs)
        except Exception as e:
            raise _translate(e) from e
    return _runner()


def _panel_url(server: Dict[str, Any]) -> str:
    try:
        return marzban_api._get_panel_url(server)
    except marzban_api.MarzbanApiError as e:
        raise PasarGuardApiError(str(e)) from e


# ---------------------------------------------------------------------------
# Normalization (shared with Marzban — identical response shape)
# ---------------------------------------------------------------------------
def normalize_user(raw: Dict[str, Any], fallback_name: str = "", server: Dict[str, Any] = None) -> Dict[str, Any]:
    return marzban_api.normalize_user(raw, fallback_name=fallback_name, server=server)


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------
async def create_user(server: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("create_user", server, payload)


async def get_user(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    return await _call("get_user", server, username)


async def update_user(server: Dict[str, Any], username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("update_user", server, username, payload)


async def delete_user(server: Dict[str, Any], username: str) -> None:
    await _call("delete_user", server, username)


async def disable_user(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    return await _call("disable_user", server, username)


async def enable_user(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    return await _call("enable_user", server, username)


async def list_users(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await _call("list_users", server)


async def get_user_configs(server: Dict[str, Any], username: str) -> List[str]:
    return await _call("get_user_configs", server, username)


async def get_subscription_url(server: Dict[str, Any], username: str) -> str:
    return await _call("get_subscription_url", server, username)


async def get_user_usage(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    return await _call("get_user_usage", server, username)


async def reset_user_usage(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    return await _call("reset_user_usage", server, username)


async def revoke_user_subscription(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    return await _call("revoke_user_subscription", server, username)


async def active_next_plan(server: Dict[str, Any], username: str) -> Dict[str, Any]:
    return await _call("active_next_plan", server, username)


# ---------------------------------------------------------------------------
# Panel info / stats / backup
# ---------------------------------------------------------------------------
async def test_connect(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        inbounds = await get_inbounds(server)
        logger.info("PasarGuard test_connect OK: server_id=%s inbounds=%s", server.get("id"), len(inbounds))
        return inbounds
    except PasarGuardApiError:
        raise
    except Exception as e:
        raise PasarGuardApiError(f"PasarGuard test_connect failed: {e}") from e


async def get_inbounds(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await _call("get_inbounds", server)


async def get_system_info(server: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("get_system_info", server)


async def get_server_stats(server: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("get_server_stats", server)


async def download_server_backup(server: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("download_server_backup", server)


# ---------------------------------------------------------------------------
# Native nodes (PasarGuard keeps the Marzban /api/nodes contract)
# ---------------------------------------------------------------------------
async def get_nodes(server: Dict[str, Any], *, status: str = None) -> List[Dict[str, Any]]:
    return await _call("get_nodes", server, status=status)


async def get_node(server: Dict[str, Any], node_id: Any) -> Dict[str, Any]:
    return await _call("get_node", server, node_id)


# ---------------------------------------------------------------------------
# Core (xray) config — create inbound from link (same contract as Marzban)
# ---------------------------------------------------------------------------
async def get_core_config(server: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("get_core_config", server)


async def update_core_config(server: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("update_core_config", server, config)


def build_inbound_from_link(link: str, *, port: int = None, private_key: str = "") -> Dict[str, Any]:
    return marzban_api.build_inbound_from_link(link, port=port, private_key=private_key)


def add_inbound_to_core_config(config: Dict[str, Any], inbound: Dict[str, Any]) -> Dict[str, Any]:
    return marzban_api.add_inbound_to_core_config(config, inbound)
