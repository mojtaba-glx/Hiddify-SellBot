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
import uuid as uuid_mod
from typing import Any, Dict, List, Optional, Tuple

from Shared import hiddify_api, marzban_api

logger = logging.getLogger(__name__)

_MARZBAN_PANEL_TYPES = {"marzban", "pasarguard"}


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


def is_marzban_primary(server: Dict[str, Any]) -> bool:
    """
    True if the server is a Marzban/PasarGuard panel used as the PRIMARY panel
    (panel_type set in the add-server wizard).  On such servers the bot talks
    to Marzban directly — no Hiddify call is attempted.
    """
    return str((server or {}).get("panel_type") or "").strip().lower() in _MARZBAN_PANEL_TYPES


def _resolve_marzban_username(server: Dict[str, Any], user_uuid: str, marzban_username: str = "") -> str:
    """On Marzban-primary servers panel_user_uuid stores the Marzban username."""
    un = str(marzban_username or "").strip()
    if un:
        return un
    return str(user_uuid or "").strip()


def _normalize_marzban_user(raw: Dict[str, Any], fallback_name: str = "", server: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Convert a raw Marzban user response into the hiddify-like shape the bot expects."""
    return marzban_api.normalize_user(raw, fallback_name=fallback_name, server=server)


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

    On Marzban/PasarGuard primary servers (panel_type=marzban/pasarguard)
    only the Marzban API is called and the normalized user dict is returned
    with ``uuid`` == Marzban username.
    """
    # --- Marzban-primary server: talk to Marzban directly ---
    if is_marzban_primary(server):
        raw_name = str(payload.get("name") or payload.get("username") or "user").strip()
        suffix = uuid_mod.uuid4().hex[:6]
        base_un = re.sub(r'[^a-zA-Z0-9_\-@.]', '_', raw_name).strip("_")[:24] or "user"
        marzban_username = f"{base_un}_{suffix}"[:32]

        marzban_payload: Dict[str, Any] = {
            "username": marzban_username,
            "data_limit_GB": float(payload.get("usage_limit_GB") or payload.get("data_limit_GB") or 0),
            "expire_days": int(payload.get("package_days") or payload.get("expire_days") or 0),
            "status": "active" if payload.get("is_active", True) else "disabled",
        }
        if payload.get("start_date"):
            marzban_payload["start_date"] = str(payload["start_date"])
        if payload.get("comment") or payload.get("note"):
            marzban_payload["note"] = str(payload.get("comment") or payload.get("note"))

        try:
            raw_user = await marzban_api.create_user(server, marzban_payload)
        except Exception as e:
            logger.error("Marzban-primary create_user failed for server_id=%s: %s", server.get("id"), e)
            raise

        normalized = _normalize_marzban_user(raw_user, fallback_name=raw_name, server=server)
        normalized["_marzban_created"] = raw_user
        normalized["_marzban_username"] = marzban_username
        normalized["_marzban_error"] = ""
        normalized["_marzban_subscription_url"] = str(raw_user.get("subscription_url") or "")
        logger.info("Marzban-primary user created: %s (server_id=%s)", marzban_username, server.get("id"))
        return normalized

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

    On Marzban-primary servers, ``user_uuid`` is the Marzban username.
    """
    # --- Marzban-primary server ---
    if is_marzban_primary(server):
        un = _resolve_marzban_username(server, user_uuid, marzban_username)
        marzban_update: Dict[str, Any] = {}
        if "usage_limit_GB" in payload:
            marzban_update["data_limit_GB"] = float(payload["usage_limit_GB"])
        if "package_days" in payload:
            marzban_update["expire_days"] = int(payload["package_days"])
        if "expire_date" in payload:
            marzban_update["expire_date"] = str(payload["expire_date"])
        if "is_active" in payload:
            marzban_update["is_active"] = bool(payload["is_active"])
        if "comment" in payload or "note" in payload:
            marzban_update["note"] = str(payload.get("comment") or payload.get("note"))
        if "name" in payload and "name" not in marzban_update:
            # callers often pass name; map to note (username rename is unsafe)
            pass
        for key in ("data_limit", "expire", "status", "proxies", "note",
                    "data_limit_GB", "expire_days", "inbounds",
                    "data_limit_reset_strategy", "on_hold_expire_duration"):
            if key in payload:
                marzban_update[key] = payload[key]
        if marzban_update:
            try:
                await marzban_api.update_user(server, un, marzban_update)
                logger.debug("Marzban-primary user updated: %s", un)
            except Exception as e:
                logger.warning("Marzban-primary patch_user failed for %s: %s", un, e)
        try:
            raw = await marzban_api.get_user(server, un)
            return _normalize_marzban_user(raw)
        except Exception:
            return {"uuid": un, "username": un, "_marzban_username": un}

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
    # --- Marzban-primary server ---
    if is_marzban_primary(server):
        un = _resolve_marzban_username(server, user_uuid, marzban_username)
        await marzban_api.delete_user(server, un)
        logger.info("Marzban-primary user deleted: %s", un)
        return

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
    # --- Marzban-primary server ---
    if is_marzban_primary(server):
        un = _resolve_marzban_username(server, user_uuid, marzban_username)
        return await marzban_api.disable_user(server, un)

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
    # --- Marzban-primary server ---
    if is_marzban_primary(server):
        un = _resolve_marzban_username(server, user_uuid, marzban_username)
        return await marzban_api.enable_user(server, un)

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

    # --- Marzban-primary server ---
    if is_marzban_primary(server):
        un = _resolve_marzban_username(server, user_uuid, marzban_username)
        try:
            links = await marzban_api.get_user_configs(server, un)
            for link in links or []:
                link_str = str(link or "").strip()
                if link_str:
                    configs.append({"link": link_str, "_source": "marzban"})
        except Exception as e:
            logger.warning("Marzban-primary get_user_configs failed for %s: %s", un, e)
        return configs

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
    if not marzban_username:
        return ""
    if not has_marzban(server) and not is_marzban_primary(server):
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

    # --- Marzban-primary server: rotate subscription token ---
    if is_marzban_primary(server):
        un = _resolve_marzban_username(server, user_uuid, marzban_username)
        try:
            await marzban_api.revoke_user_subscription(server, un)
            result["marzban_revoked"] = True
        except Exception as e:
            logger.warning("Marzban-primary revoke failed for %s: %s", un, e)
        return result

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
