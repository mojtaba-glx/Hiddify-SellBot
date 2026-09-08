"""Compatibility wrappers for the supported subscription panels.

The project currently supports Hiddify and X-UI through ``hiddify_api``.
Marzban support was removed deliberately: keeping a single execution path
prevents stale Marzban credentials from creating or mutating accounts.
Optional ``marzban_username`` arguments remain for old database records and
are ignored.
"""

from typing import Any, Dict, List

from Shared import hiddify_api


def has_marzban(server: Dict[str, Any]) -> bool:
    """Always false; retained so old callers cannot re-enable Marzban."""
    return False


async def create_user(server: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    return await hiddify_api.create_user(server, payload)


async def patch_user(
    server: Dict[str, Any],
    user_uuid: str,
    payload: Dict[str, Any],
    *,
    marzban_username: str = "",
) -> Dict[str, Any]:
    return await hiddify_api.patch_user(server, user_uuid, payload)


async def delete_user(
    server: Dict[str, Any],
    user_uuid: str,
    *,
    marzban_username: str = "",
) -> None:
    await hiddify_api.delete_user(server, user_uuid)


async def disable_user(
    server: Dict[str, Any],
    user_uuid: str,
    *,
    marzban_username: str = "",
) -> Dict[str, Any]:
    return await hiddify_api.disable_user(server, user_uuid)


async def enable_user(
    server: Dict[str, Any],
    user_uuid: str,
    *,
    marzban_username: str = "",
) -> Dict[str, Any]:
    return await hiddify_api.enable_user(server, user_uuid)


async def get_user_configs(
    server: Dict[str, Any],
    user_uuid: str,
    *,
    marzban_username: str = "",
) -> List[Dict[str, Any]]:
    configs = await hiddify_api.get_user_configs(server, user_uuid)
    result: List[Dict[str, Any]] = []
    for item in configs or []:
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, str) and "://" in item:
            result.append({"link": item})
    return result


async def get_subscription_url(server: Dict[str, Any], marzban_username: str) -> str:
    """Legacy compatibility endpoint; Marzban subscriptions are unavailable."""
    return ""


async def revoke_user_link(
    server: Dict[str, Any],
    user_uuid: str,
    *,
    marzban_username: str = "",
) -> Dict[str, Any]:
    """Regenerate a Hiddify/X-UI user while retaining the old result shape."""
    current = await hiddify_api.get_user_by_uuid(server, user_uuid)
    if not current:
        return {"new_uuid": "", "marzban_revoked": False}
    payload = {
        "name": str(current.get("name") or ""),
        "usage_limit_GB": float(current.get("usage_limit_GB") or 0),
        "package_days": int(current.get("package_days") or 0),
        "is_active": bool(current.get("is_active", True)),
    }
    new_user = await hiddify_api.create_user(server, payload)
    new_uuid = str((new_user or {}).get("uuid") or "")
    if new_uuid:
        await hiddify_api.delete_user(server, user_uuid)
    return {"new_uuid": new_uuid, "marzban_revoked": False}


list_users = hiddify_api.list_users
get_user_by_uuid = hiddify_api.get_user_by_uuid
get_server_stats = hiddify_api.get_server_stats
download_server_backup = hiddify_api.download_server_backup
