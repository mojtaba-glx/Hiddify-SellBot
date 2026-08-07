import logging
from typing import Any, Dict, List, Optional

from Shared import hiddify_api, multi_panel, database, agent_db

logger = logging.getLogger(__name__)


def get_available_servers() -> List[Dict[str, Any]]:
    return database.get_servers() or []


def get_server_by_id(server_id: int) -> Optional[Dict[str, Any]]:
    return database.get_server_by_id(server_id)


def get_agent_plans(agent_id: int, server_id: Optional[int] = None) -> List[Dict[str, Any]]:
    return agent_db.get_agent_plans(agent_id, server_id)


async def revoke_user_link_on_panel(panel_user_uuid: str, server_id: int, marzban_username: str = "") -> Dict[str, Any]:
    """Revoke / regenerate user subscription links."""
    server = database.get_server_by_id(server_id)
    if not server:
        return {"new_uuid": "", "marzban_revoked": False}
    return await multi_panel.revoke_user_link(server, panel_user_uuid, marzban_username=marzban_username)


async def create_user_on_panel(server_id: int, server: Dict[str, Any], name: str, usage_limit_gb: float, days: int, comment: str = "") -> Dict[str, Any]:
    payload = {
        "name": name,
        "usage_limit_GB": usage_limit_gb,
        "package_days": days,
    }
    if str(comment or "").strip():
        payload["comment"] = str(comment).strip()
    result = await multi_panel.create_user(server, payload)
    if result and result.get("uuid"):
        return result
    raise RuntimeError(f"panel returned no uuid for user {name}")


async def disable_user_on_panel(panel_user_uuid: str, server_id: int, marzban_username: str = "") -> bool:
    server = database.get_server_by_id(server_id)
    if not server:
        return False
    await multi_panel.disable_user(server, panel_user_uuid, marzban_username=marzban_username)
    return True


async def enable_user_on_panel(panel_user_uuid: str, server_id: int, marzban_username: str = "") -> bool:
    server = database.get_server_by_id(server_id)
    if not server:
        return False
    await multi_panel.enable_user(server, panel_user_uuid, marzban_username=marzban_username)
    return True


async def delete_user_on_panel(panel_user_uuid: str, server_id: int, marzban_username: str = "") -> bool:
    server = database.get_server_by_id(server_id)
    if not server:
        return False
    await multi_panel.delete_user(server, panel_user_uuid, marzban_username=marzban_username)
    return True


async def get_user_configs(panel_user_uuid: str, server_id: int, marzban_username: str = "") -> List[str]:
    server = database.get_server_by_id(server_id)
    if not server:
        return []
    try:
        configs = await multi_panel.get_user_configs(server, panel_user_uuid, marzban_username=marzban_username)
        if configs and isinstance(configs, list):
            return configs
        return []
    except Exception as e:
        logger.error("Failed to get user configs: %s", e)
        return []
