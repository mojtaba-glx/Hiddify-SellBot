"""Compatibility wrappers for the supported subscription panels.

The project currently supports Hiddify and X-UI through ``hiddify_api``.
Marzban support was removed deliberately: keeping a single execution path
prevents stale Marzban credentials from creating or mutating accounts.
Optional ``marzban_username`` arguments remain for old database records and
are ignored.
"""

from typing import Any, Dict, List
import asyncio

from Shared import hiddify_api


def has_marzban(server: Dict[str, Any]) -> bool:
    """Always false; retained so old callers cannot re-enable Marzban."""
    return False


async def create_user(server: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    return await hiddify_api.create_user(server, payload)


class PanelUuidMismatchError(RuntimeError):
    """The panel did not persist the UUID requested by the bot."""


def _looks_like_panel_uuid(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and len(text) >= 8 and " " not in text and not text.isdigit())


async def _probe_requested_user(
    server: Dict[str, Any],
    requested_uuid: str,
    *,
    is_xui: bool,
    attempts: int = 4,
) -> Dict[str, Any] | None:
    """Read a freshly-created user without issuing another create POST."""
    for attempt in range(max(1, int(attempts))):
        try:
            candidate = await get_user_by_uuid(server, requested_uuid)
            if isinstance(candidate, dict):
                explicit_uuid = str(candidate.get("uuid") or "").strip()
                fallback_id = str(candidate.get("id") or "").strip()
                if explicit_uuid == requested_uuid or fallback_id == requested_uuid:
                    result = dict(candidate)
                    result["uuid"] = requested_uuid
                    return result
                # Hiddify's UUID-addressed endpoint can return a numeric DB id
                # without echoing uuid. A successful GET by requested UUID is
                # still authoritative for that row.
                if not is_xui and not explicit_uuid:
                    result = dict(candidate)
                    result["uuid"] = requested_uuid
                    return result
        except Exception:
            pass
        if attempt + 1 < max(1, int(attempts)):
            await asyncio.sleep(0.25 * (attempt + 1))
    return None


async def _recover_primary_by_identity(
    server: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any] | None:
    """Recover a primary Hiddify create whose POST response was lost."""
    requested_uuid = str((payload or {}).get("uuid") or "").strip()
    if requested_uuid:
        recovered = await _probe_requested_user(
            server,
            requested_uuid,
            is_xui=False,
            attempts=4,
        )
        if recovered is not None:
            return recovered

    name = str((payload or {}).get("name") or "").strip()
    comment = str((payload or {}).get("comment") or "").strip()
    if not name or not comment:
        return None

    # Agent/customer service notes are random per purchase, so exact
    # name+comment is a safe recovery key for a lost create response.
    for attempt in range(3):
        try:
            users = await list_users(server)
            matches: List[Dict[str, Any]] = []
            for user in users or []:
                if not isinstance(user, dict):
                    continue
                if str(user.get("name") or "").strip() != name:
                    continue
                if str(user.get("comment") or "").strip() != comment:
                    continue
                matches.append(user)
            if len(matches) == 1:
                row = dict(matches[0])
                explicit_uuid = str(row.get("uuid") or "").strip()
                fallback_id = str(row.get("id") or "").strip()
                canonical_uuid = explicit_uuid
                if not canonical_uuid and _looks_like_panel_uuid(fallback_id):
                    canonical_uuid = fallback_id
                if canonical_uuid:
                    row["uuid"] = canonical_uuid
                    return row
        except Exception:
            pass
        if attempt < 2:
            await asyncio.sleep(0.35 * (attempt + 1))
    return None


async def create_primary_user(
    server: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Create the authoritative primary user and return the panel's real UUID.

    Hiddify may ignore a client-supplied UUID and generate its own. That is
    valid for the primary node: its persisted UUID becomes the canonical UUID
    that every child node and the smart subscription link must reuse.
    """
    try:
        is_xui = bool(hiddify_api._is_xui_server(server))
    except Exception:
        is_xui = False

    try:
        created = await create_user(server, dict(payload or {}))
    except Exception:
        if is_xui:
            requested_uuid = str((payload or {}).get("uuid") or "").strip()
            if requested_uuid:
                try:
                    await delete_user(server, requested_uuid)
                except Exception:
                    pass
            raise

        recovered = await _recover_primary_by_identity(server, payload)
        if recovered is not None:
            return recovered
        raise

    if not isinstance(created, dict):
        raise PanelUuidMismatchError("panel returned an invalid primary create response")

    explicit_uuid = str(created.get("uuid") or "").strip()
    fallback_id = str(created.get("id") or "").strip()
    canonical_uuid = explicit_uuid
    if not canonical_uuid and _looks_like_panel_uuid(fallback_id):
        canonical_uuid = fallback_id

    if not canonical_uuid:
        requested_uuid = str((payload or {}).get("uuid") or "").strip()
        if requested_uuid:
            recovered = await _probe_requested_user(
                server,
                requested_uuid,
                is_xui=is_xui,
                attempts=3,
            )
            if recovered is not None:
                canonical_uuid = requested_uuid
                created = {**created, **recovered}

    if not canonical_uuid:
        raise PanelUuidMismatchError("primary panel returned no usable UUID")

    result = dict(created)
    result["uuid"] = canonical_uuid
    return result


async def create_user_with_uuid(
    server: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a panel user while keeping one canonical UUID across the cluster.

    A lost create response is recovered by reading the requested UUID instead
    of repeating POST. Successful creates are verified with a short grace
    period so Hiddify's eventual persistence does not turn a valid creation
    into a false failure.
    """
    requested_uuid = str((payload or {}).get("uuid") or "").strip()
    if not requested_uuid:
        raise ValueError("create_user_with_uuid requires payload.uuid")

    try:
        is_xui = bool(hiddify_api._is_xui_server(server))
    except Exception:
        is_xui = False

    try:
        created = await create_user(server, dict(payload or {}))
    except Exception:
        if is_xui:
            # X-UI may create a subset of configured inbounds before an error.
            # Best-effort cleanup is safer than retrying the same create POST.
            try:
                await delete_user(server, requested_uuid)
            except Exception:
                pass
            raise

        recovered = await _probe_requested_user(
            server,
            requested_uuid,
            is_xui=False,
            attempts=4,
        )
        if recovered is not None:
            return recovered
        raise

    if not isinstance(created, dict):
        raise PanelUuidMismatchError("panel returned an invalid create response")

    # First try the UUID we explicitly asked the panel to persist. This avoids
    # false negatives when Hiddify needs a moment before GET sees the new row.
    verified = await _probe_requested_user(
        server,
        requested_uuid,
        is_xui=is_xui,
        attempts=3,
    )
    if verified is not None:
        result = dict(created)
        result.update(verified)
        result["uuid"] = requested_uuid
        return result

    explicit_uuid = str(created.get("uuid") or "").strip()
    fallback_id = str(created.get("id") or "").strip()
    returned_uuid = explicit_uuid
    if not returned_uuid and _looks_like_panel_uuid(fallback_id):
        returned_uuid = fallback_id

    # If the panel response itself explicitly confirms the requested UUID,
    # accept it even when the immediate read endpoint is still catching up.
    if returned_uuid == requested_uuid:
        result = dict(created)
        result["uuid"] = requested_uuid
        return result

    cleanup_uuids: List[str] = []
    if returned_uuid and _looks_like_panel_uuid(returned_uuid):
        cleanup_uuids.append(returned_uuid)

    try:
        if returned_uuid and returned_uuid != requested_uuid:
            await patch_user(server, returned_uuid, {"uuid": requested_uuid})
            verified = await _probe_requested_user(
                server,
                requested_uuid,
                is_xui=is_xui,
                attempts=3,
            )
            if verified is not None:
                result = dict(created)
                result.update(verified)
                result["uuid"] = requested_uuid
                return result

        raise PanelUuidMismatchError(
            "panel UUID mismatch "
            f"(requested={requested_uuid}, returned={returned_uuid or 'unverified'})"
        )
    except Exception as exc:
        # This operation is being failed/refunded, so remove any identity that
        # may have been created. Never use a numeric DB id as a UUID cleanup key.
        cleanup_uuids.append(requested_uuid)
        seen = set()
        for candidate in cleanup_uuids:
            if not _looks_like_panel_uuid(candidate) or candidate in seen:
                continue
            seen.add(candidate)
            try:
                await delete_user(server, candidate)
            except Exception:
                pass
        if isinstance(exc, PanelUuidMismatchError):
            raise
        raise PanelUuidMismatchError(
            f"could not verify requested UUID {requested_uuid}: {exc}"
        ) from exc


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
