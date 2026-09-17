"""
Shared/xui_api.py
=================
Facade / Dispatcher for X-UI panels.
Routes to:
  - xui_alireza.py  -> /xui/API  (Alireza / S0R0SH legacy)
  - xui_sanaei.py   -> /panel/api (MHSanaei 3x-ui modern - clients API)

Keeps 100% backward compatibility for `from Shared import xui_api` imports.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from Shared.xui_common import (
    _get_api_token,
    _is_sanaei_token_auth,
    _public_origin,
    _sub_path,
    is_xui_server,
    SUPPORTED_PROTOCOLS,
)

# Re-export common symbols for external callers
from Shared.xui_common import _public_origin as _public_origin_re  # noqa
from Shared.xui_common import _sub_path as _sub_path_re  # noqa

logger = logging.getLogger(__name__)

# Keep constants for compatibility
API_PREFIX = "/xui/API"
API_PREFIX_SANAEI = "/panel/api"

# Unified error - either backend's error is caught and re-raised as this
class XuiApiError(Exception):
    pass


def _use_sanaei(server: Dict[str, Any]) -> bool:
    """True if server should use Sanaei (MHSanaei) adapter"""
    try:
        return _is_sanaei_token_auth(server)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Direct re-exports that are identical for both (from common)
# ---------------------------------------------------------------------------
# is_xui_server already imported

# Helpers that external code uses directly (keep backward compat)
def _get_panel_url(server: Dict[str, Any]) -> str:
    from Shared.xui_common import _get_panel_url as _gpu
    return _gpu(server)

# parse_config_link is protocol parsing - identical, use alireza version
def parse_config_link(link: str) -> Dict[str, Any]:
    from Shared import xui_alireza
    return xui_alireza.parse_config_link(link)


# ---------------------------------------------------------------------------
# Dispatched public API (mirrors hiddify_api signatures)
# ---------------------------------------------------------------------------

async def test_connect(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.test_connect(server)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.test_connect(server)
        except Exception as e:
            # alireza already raises XuiApiError, normalize
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def list_users(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.list_users(server)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.list_users(server)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def get_user_by_uuid(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.get_user_by_uuid(server, user_uuid)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.get_user_by_uuid(server, user_uuid)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def create_user(server: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.create_user(server, payload)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.create_user(server, payload)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def patch_user(server: Dict[str, Any], user_uuid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.patch_user(server, user_uuid, payload)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.patch_user(server, user_uuid, payload)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def enable_user(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.enable_user(server, user_uuid)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.enable_user(server, user_uuid)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def disable_user(server: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.disable_user(server, user_uuid)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.disable_user(server, user_uuid)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def delete_user(server: Dict[str, Any], user_uuid: str) -> None:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.delete_user(server, user_uuid)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.delete_user(server, user_uuid)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def get_subscription_url(server: Dict[str, Any], user_uuid: str) -> str:
    # Same for both
    if _use_sanaei(server):
        from Shared import xui_sanaei
        return await xui_sanaei.get_subscription_url(server, user_uuid)
    else:
        from Shared import xui_alireza
        return await xui_alireza.get_subscription_url(server, user_uuid)


async def get_user_configs(server: Dict[str, Any], user_uuid: str) -> List[Dict[str, Any]]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.get_user_configs(server, user_uuid)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.get_user_configs(server, user_uuid)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def get_server_stats(server: Dict[str, Any]) -> Dict[str, Any]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.get_server_stats(server)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.get_server_stats(server)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def download_server_backup(server: Dict[str, Any]) -> Dict[str, Any]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.download_server_backup(server)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.download_server_backup(server)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def create_inbound_from_link(
    server: Dict[str, Any],
    link: str,
    *,
    port_override: Optional[int] = None,
    remark: Optional[str] = None,
) -> Dict[str, Any]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.create_inbound_from_link(server, link, port_override=port_override, remark=remark)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.create_inbound_from_link(server, link, port_override=port_override, remark=remark)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


async def sync_users_to_inbounds(server: Dict[str, Any]) -> Dict[str, Any]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        try:
            return await xui_sanaei.sync_users_to_inbounds(server)
        except Exception as e:
            raise XuiApiError(str(e)) from e
    else:
        from Shared import xui_alireza
        try:
            return await xui_alireza.sync_users_to_inbounds(server)
        except Exception as e:
            if isinstance(e, xui_alireza.XuiApiError):
                raise XuiApiError(str(e)) from e
            raise XuiApiError(str(e)) from e


# Subscription lines (used by sub_http_server)
async def fetch_subscription_lines(server: Dict[str, Any], user_uuid: str) -> List[str]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        return await xui_sanaei.fetch_subscription_lines(server, user_uuid)
    else:
        from Shared import xui_alireza
        return await xui_alireza.fetch_subscription_lines(server, user_uuid)


def sync_fetch_subscription_lines(server: Dict[str, Any], user_uuid: str) -> List[str]:
    if _use_sanaei(server):
        from Shared import xui_sanaei
        return xui_sanaei.sync_fetch_subscription_lines(server, user_uuid)
    else:
        from Shared import xui_alireza
        return xui_alireza.sync_fetch_subscription_lines(server, user_uuid)


# Keep backward compat for any direct helper access
# Re-export helpers that external code may import via xui_api
from Shared.xui_common import (
    _sanitize_xui_email,
    _existing_xui_emails,
    _unique_xui_email,
    _to_int,
    _to_float,
    _bytes_to_gb,
    _gb_to_bytes,
)
