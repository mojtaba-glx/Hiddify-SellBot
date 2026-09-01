"""
Shared/multi_panel.py
=====================
Thin dispatcher over the Hiddify API.

Every function mirrors the hiddify_api signature so callers can simply
import from multi_panel instead of hiddify_api.

Kept as a stable import point: callers previously using multi_panel keep
working unchanged.
"""

import logging
from typing import Any, Dict, List

from Shared import hiddify_api

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Panel module resolver
# ---------------------------------------------------------------------------
def panel_api(server: Dict[str, Any]):
    """
    Return the panel client module for the given server.

      panel_type in xui variants → Shared.xui_api (hiddify_api routes these too)
      otherwise                  → Shared.hiddify_api
    """
    return hiddify_api


# ---------------------------------------------------------------------------
# CREATE USER
# ---------------------------------------------------------------------------
async def create_user(server: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    return await hiddify_api.create_user(server, payload)


# ---------------------------------------------------------------------------
# PATCH / UPDATE USER
# ---------------------------------------------------------------------------
async def patch_user(
    server: Dict[str, Any],
    user_uuid: str,
    payload: Dict[str, Any],
    **_: Any,
) -> Dict[str, Any]:
    return await hiddify_api.patch_user(server, user_uuid, payload)


# ---------------------------------------------------------------------------
# DELETE USER
# ---------------------------------------------------------------------------
async def delete_user(server: Dict[str, Any], user_uuid: str, **_: Any) -> None:
    await hiddify_api.delete_user(server, user_uuid)


# ---------------------------------------------------------------------------
# DISABLE / ENABLE USER
# ---------------------------------------------------------------------------
async def disable_user(server: Dict[str, Any], user_uuid: str, **_: Any) -> Dict[str, Any]:
    return await hiddify_api.disable_user(server, user_uuid)


async def enable_user(server: Dict[str, Any], user_uuid: str, **_: Any) -> Dict[str, Any]:
    return await hiddify_api.enable_user(server, user_uuid)


# ---------------------------------------------------------------------------
# GET USER CONFIGS (for smart links)
# ---------------------------------------------------------------------------
async def get_user_configs(
    server: Dict[str, Any],
    user_uuid: str,
    **_: Any,
) -> List[Dict[str, Any]]:
    configs: List[Dict[str, Any]] = []
    try:
        hiddify_configs = await hiddify_api.get_user_configs(server, user_uuid)
        for item in hiddify_configs or []:
            if isinstance(item, dict):
                configs.append(item)
            elif isinstance(item, str) and "://" in item:
                configs.append({"link": item})
    except Exception as e:
        logger.warning("Hiddify get_user_configs failed: %s", e)
    return configs


# ---------------------------------------------------------------------------
# REVOKE USER LINK (regenerate configs / change UUID)
# ---------------------------------------------------------------------------
async def revoke_user_link(
    server: Dict[str, Any],
    user_uuid: str,
    **_: Any,
) -> Dict[str, Any]:
    """
    Revoke / regenerate user subscription links.

    Hiddify: deletes existing user and creates a new one with the same
    name / usage / days so a fresh UUID is generated.

    Returns dict with ``new_uuid`` (str, empty if unchanged).
    """
    result: Dict[str, Any] = {"new_uuid": ""}
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
    return result


list_users = hiddify_api.list_users
get_user_by_uuid = hiddify_api.get_user_by_uuid
get_server_stats = hiddify_api.get_server_stats
download_server_backup = hiddify_api.download_server_backup
