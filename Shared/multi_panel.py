"""
Shared/multi_panel.py
=====================
Dispatcher that calls Hiddify API and optionally Marzban API in parallel.

Every function mirrors the hiddify_api signature so callers can simply
import from multi_panel instead of hiddify_api to get dual-panel support.

If a server dict has a ``marzban_config`` key (or ``marzban_panel_url``),
the dispatcher will also call the Marzban API.  Failures on one panel
are logged but do NOT block the other panel — best-effort parallelism.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from Shared import hiddify_api, marzban_api

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def has_marzban(server: Dict[str, Any]) -> bool:
    """Return True if the server dict has Marzban credentials configured."""
    mc = server.get("marzban_config")
    if isinstance(mc, dict) and (mc.get("panel_url") or mc.get("admin_username")):
        return True
    if server.get("marzban_panel_url"):
        return True
    return False


def _make_marzban_username(hiddify_name: str, suffix: str = "") -> str:
    """
    Derive a safe Marzban username from a Hiddify user name.
    Marzban allows: a-zA-Z0-9-_@.  (3-32 chars)
    """
    clean = re.sub(r'[^a-zA-Z0-9_\-@.]', '_', str(hiddify_name or "")).strip("_")
    if suffix:
        clean = f"{clean}_{suffix}"[:32]
    return clean[:32] or f"user_{suffix or 'x'}"


# ---------------------------------------------------------------------------
# CREATE USER
# ---------------------------------------------------------------------------
async def create_user(
    server: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Create user on Hiddify (primary) and optionally on Marzban (secondary).

    Returns the Hiddify response dict enriched with:
      - ``_marzban_created``: the Marzban user response dict (or None)
      - ``_marzban_username``: the username used on Marzban (or "")
      - ``_marzban_error``: error string if Marzban failed (or "")
    """
    # Always call Hiddify first (primary panel)
    hiddify_result = await hiddify_api.create_user(server, payload)

    if not has_marzban(server):
        return hiddify_result

    # Prepare Marzban payload
    hiddify_name = str(
        payload.get("name")
        or hiddify_result.get("name")
        or payload.get("uuid", "")[:8]
        or "user"
    )
    suffix = str(hiddify_result.get("uuid") or payload.get("uuid") or "")[:8]
    marzban_username = _make_marzban_username(hiddify_name, suffix)

    marzban_payload = {
        "username": marzban_username,
        "data_limit_GB": float(payload.get("usage_limit_GB") or 0),
        "expire_days": int(payload.get("package_days") or 0),
        "start_date": str(payload.get("start_date") or ""),
        "is_active": bool(payload.get("is_active", True)),
    }
    # Carry over note if present
    if payload.get("comment"):
        marzban_payload["note"] = str(payload["comment"])

    marzban_result = None
    marzban_error = ""
    try:
        marzban_result = await marzban_api.create_user(server, marzban_payload)
        logger.info("Marzban user created: %s (server_id=%s)", marzban_username, server.get("id"))
    except Exception as e:
        marzban_error = str(e)[:200]
        logger.warning("Marzban create_user failed for server_id=%s: %s", server.get("id"), e)

    # Enrich Hiddify result with Marzban metadata
    hiddify_result["_marzban_created"] = marzban_result
    hiddify_result["_marzban_username"] = marzban_username if marzban_result else ""
    hiddify_result["_marzban_error"] = marzban_error
    if marzban_result:
        hiddify_result["_marzban_subscription_url"] = str(marzban_result.get("subscription_url") or "")

    return hiddify_result


# ---------------------------------------------------------------------------
# PATCH / UPDATE USER
# ---------------------------------------------------------------------------
async def patch_user(
    server: Dict[str, Any],
    user_uuid: str,
    payload: Dict[str, Any],
    *,
    marzban_username: str = "",
) -> Dict[str, Any]:
    """
    Update user on Hiddify (primary) and optionally on Marzban (secondary).

    If ``marzban_username`` is provided, also updates the Marzban user.
    """
    hiddify_result = await hiddify_api.patch_user(server, user_uuid, payload)

    if not has_marzban(server) or not marzban_username:
        return hiddify_result

    # Convert Hiddify patch payload → Marzban update payload
    marzban_update: Dict[str, Any] = {}
    if "name" in payload:
        # Don't rename Marzban user automatically (could break links)
        pass
    if "usage_limit_GB" in payload:
        marzban_update["data_limit_GB"] = float(payload["usage_limit_GB"])
    if "package_days" in payload:
        marzban_update["package_days"] = int(payload["package_days"])
    if "expire_date" in payload:
        marzban_update["expire_date"] = str(payload["expire_date"])
    if "is_active" in payload:
        marzban_update["is_active"] = bool(payload["is_active"])
    if "comment" in payload:
        marzban_update["note"] = str(payload["comment"])

    # Pass through if caller used Marzban-native keys
    for key in ("data_limit", "expire", "status", "proxies", "note", "data_limit_GB", "expire_days"):
        if key in payload and key not in marzban_update:
            marzban_update[key] = payload[key]

    if marzban_update:
        try:
            await marzban_api.update_user(server, marzban_username, marzban_update)
            logger.debug("Marzban user updated: %s", marzban_username)
        except Exception as e:
            logger.warning("Marzban patch_user failed for %s: %s", marzban_username, e)

    return hiddify_result


# ---------------------------------------------------------------------------
# DELETE USER
# ---------------------------------------------------------------------------
async def delete_user(
    server: Dict[str, Any],
    user_uuid: str,
    *,
    marzban_username: str = "",
) -> None:
    """Delete user from Hiddify and optionally from Marzban."""
    await hiddify_api.delete_user(server, user_uuid)

    if has_marzban(server) and marzban_username:
        try:
            await marzban_api.delete_user(server, marzban_username)
            logger.info("Marzban user deleted: %s", marzban_username)
        except Exception as e:
            logger.warning("Marzban delete_user failed for %s: %s", marzban_username, e)


# ---------------------------------------------------------------------------
# DISABLE / ENABLE USER
# ---------------------------------------------------------------------------
async def disable_user(
    server: Dict[str, Any],
    user_uuid: str,
    *,
    marzban_username: str = "",
) -> Dict[str, Any]:
    """Disable user on Hiddify and optionally on Marzban."""
    result = await hiddify_api.disable_user(server, user_uuid)

    if has_marzban(server) and marzban_username:
        try:
            await marzban_api.disable_user(server, marzban_username)
        except Exception as e:
            logger.warning("Marzban disable_user failed for %s: %s", marzban_username, e)

    return result


async def enable_user(
    server: Dict[str, Any],
    user_uuid: str,
    *,
    marzban_username: str = "",
) -> Dict[str, Any]:
    """Enable user on Hiddify and optionally on Marzban."""
    result = await hiddify_api.enable_user(server, user_uuid)

    if has_marzban(server) and marzban_username:
        try:
            await marzban_api.enable_user(server, marzban_username)
        except Exception as e:
            logger.warning("Marzban enable_user failed for %s: %s", marzban_username, e)

    return result


# ---------------------------------------------------------------------------
# GET USER CONFIGS (for smart links)
# ---------------------------------------------------------------------------
async def get_user_configs(
    server: Dict[str, Any],
    user_uuid: str,
    *,
    marzban_username: str = "",
) -> List[Dict[str, Any]]:
    """
    Get user proxy configs from Hiddify.  If Marzban is configured,
    also fetches Marzban configs and merges them.

    Each item in the returned list is a dict with at least ``link`` key.
    Marzban configs are returned as ``{"link": "vless://...", "_source": "marzban"}``.
    """
    configs: List[Dict[str, Any]] = []

    # Hiddify configs (primary)
    try:
        hiddify_configs = await hiddify_api.get_user_configs(server, user_uuid)
        for item in hiddify_configs or []:
            if isinstance(item, dict):
                configs.append(item)
            elif isinstance(item, str) and "://" in item:
                configs.append({"link": item})
    except Exception as e:
        logger.warning("Hiddify get_user_configs failed: %s", e)

    # Marzban configs (secondary)
    if has_marzban(server) and marzban_username:
        try:
            marzban_links = await marzban_api.get_user_configs(server, marzban_username)
            existing_links = {str(c.get("link") or "").strip() for c in configs if isinstance(c, dict)}
            for link in marzban_links or []:
                link_str = str(link or "").strip()
                if link_str and link_str not in existing_links:
                    configs.append({"link": link_str, "_source": "marzban"})
                    existing_links.add(link_str)
        except Exception as e:
            logger.warning("Marzban get_user_configs failed for %s: %s", marzban_username, e)

    return configs


# ---------------------------------------------------------------------------
# GET SUBSCRIPTION URL (Marzban-specific)
# ---------------------------------------------------------------------------
async def get_subscription_url(
    server: Dict[str, Any],
    marzban_username: str,
) -> str:
    """Get the Marzban subscription URL for a user."""
    if not has_marzban(server) or not marzban_username:
        return ""
    try:
        return await marzban_api.get_subscription_url(server, marzban_username)
    except Exception as e:
        logger.warning("Marzban get_subscription_url failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# PASS-THROUGH to hiddify_api (no Marzban equivalent needed)
# ---------------------------------------------------------------------------
# These functions are re-exported for convenience so callers can use
# multi_panel as a single import point.

# ---------------------------------------------------------------------------
# REVOKE USER LINK (regenerate configs / change UUID)
# ---------------------------------------------------------------------------
async def revoke_user_link(
    server: Dict[str, Any],
    user_uuid: str,
    *,
    marzban_username: str = "",
) -> Dict[str, Any]:
    """
    Revoke / regenerate user subscription links.

    - Hiddify: deletes existing user and creates a new one with the same
      name / usage / days so a fresh UUID is generated.
    - Marzban: calls ``revoke_user_subscription`` which rotates the
      subscription token.

    Returns dict with ``new_uuid`` (str, empty if unchanged) and
    ``marzban_revoked`` (bool).
    """
    result: Dict[str, Any] = {"new_uuid": "", "marzban_revoked": False}

    # --- Hiddify: delete + recreate to get a new UUID ---
    try:
        current = await hiddify_api.get_user_by_uuid(server, user_uuid)
        if current:
            payload = {
                "name": str(current.get("name") or ""),
                "usage_limit_GB": float(current.get("usage_limit_GB") or 0),
                "package_days": int(current.get("package_days") or 0),
                "is_active": True,
            }
            new_user = await hiddify_api.create_user(server, payload)
            new_uuid = str((new_user or {}).get("uuid") or "")
            if new_uuid:
                result["new_uuid"] = new_uuid
                await hiddify_api.delete_user(server, user_uuid)
    except Exception as e:
        logger.error("Hiddify revoke_user_link failed: %s", e)
        raise

    # --- Marzban: revoke subscription ---
    if has_marzban(server) and marzban_username:
        try:
            await marzban_api.revoke_user_subscription(server, marzban_username)
            result["marzban_revoked"] = True
        except Exception as e:
            logger.warning("Marzban revoke_user_subscription failed: %s", e)

    return result


list_users = hiddify_api.list_users
get_user_by_uuid = hiddify_api.get_user_by_uuid
get_server_stats = hiddify_api.get_server_stats
download_server_backup = hiddify_api.download_server_backup
