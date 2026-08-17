"""
UserBot/delivery.py
===================
عملیات مشترک «تحویل/حذف/بازگرداندن» سرویس روی پنل‌ها و دیتابیس.

این ماژول وابسته به main ربات کاربران نیست تا ربات ادمین بتواند بدون
بالا آوردن UserBot، اشتراک را حذف/بازگرداند/تحویل مجدد کند.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from Shared import userbot_db, database, multi_panel

logger = logging.getLogger(__name__)


def get_service_targets(service: Dict[str, Any]) -> List[Tuple[Dict[str, Any], str]]:
    """
    تارگت‌های (سرور، uuid) یک سرویس: سرور اصلی + همه نودهای نگاشت‌شده.
    """
    targets: List[Tuple[Dict[str, Any], str]] = []
    seen: set = set()

    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0
    try:
        sid = int(service.get("server_id") or 0)
    except (TypeError, ValueError):
        sid = 0

    uuid = ""
    comment = str(service.get("comment") or "")
    for part in comment.split("|"):
        part = part.strip()
        if part.lower().startswith("uuid:"):
            uuid = part[5:].strip()
            break

    if sid > 0 and uuid:
        srv = database.get_server_by_id(sid)
        if srv:
            targets.append((srv, uuid))
            seen.add((sid, uuid))

    if service_id > 0:
        try:
            mappings = userbot_db.get_service_nodes(service_id) or []
        except Exception:
            mappings = []
        for m in mappings:
            try:
                n_sid = int(m.get("server_id") or 0)
            except (TypeError, ValueError):
                n_sid = 0
            n_uuid = str(m.get("panel_user_uuid") or "").strip()
            if n_sid <= 0 or not n_uuid or (n_sid, n_uuid) in seen:
                continue
            srv = database.get_server_by_id(n_sid)
            if not srv:
                continue
            seen.add((n_sid, n_uuid))
            targets.append((srv, n_uuid))

    return targets


def _marzban_username_for(service_id: int, server_id: int) -> str:
    if service_id <= 0 or server_id <= 0:
        return ""
    try:
        for node in userbot_db.get_service_nodes(service_id) or []:
            if int(node.get("server_id") or 0) == int(server_id):
                return str(node.get("marzban_username") or "").strip()
    except Exception:
        pass
    return ""


async def delete_service_from_panels(service: Dict[str, Any]) -> Tuple[int, List[str]]:
    """
    حذف/غیرفعال‌کردن کاربر پنلی این سرویس روی سرور اصلی + همه نودها.
    خروجی: (تعداد موفق, لیست عنوان سرورهای ناموفق)
    """
    targets = get_service_targets(service)
    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0

    deleted = 0
    failed: List[str] = []
    for srv, uuid in targets:
        title = str(srv.get("title") or f"سرور #{srv.get('id')}")
        marzban_un = _marzban_username_for(service_id, int(srv.get("id") or 0))
        try:
            await multi_panel.delete_user(srv, uuid, marzban_username=marzban_un)
            deleted += 1
        except Exception as e:
            logger.warning(
                "Failed deleting service user on server_id=%s (uuid=%s): %s",
                srv.get("id"), uuid, e,
            )
            failed.append(title)
    return deleted, failed


async def patch_service_on_panels(
    service: Dict[str, Any],
    payload: Dict[str, Any],
) -> Tuple[int, List[str]]:
    """
    اعمال payload روی کاربر پنلی این سرویس در سرور اصلی + همه نودها.
    خروجی: (تعداد تغییر موفق, لیست عنوان سرورهای ناموفق)
    """
    targets = get_service_targets(service)
    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0

    changed = 0
    failed: List[str] = []
    for srv, uuid in targets:
        title = str(srv.get("title") or f"سرور #{srv.get('id')}")
        marzban_un = _marzban_username_for(service_id, int(srv.get("id") or 0))
        try:
            await multi_panel.patch_user(srv, uuid, payload, marzban_username=marzban_un)
            try:
                await multi_panel.enable_user(srv, uuid, marzban_username=marzban_un)
            except Exception:
                pass
            changed += 1
        except Exception as e:
            logger.warning(
                "Failed patching service user on server_id=%s (uuid=%s): %s",
                srv.get("id"), uuid, e,
            )
            failed.append(title)
    return changed, failed


def create_service_in_db(
    *,
    internal_user_id: int,
    server_id: int,
    panel_user_uuid: str,
    name: str,
    server_title: str,
    usage_limit: float,
    days_left: int,
    price: int,
    service_code: str,
    nodes: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """
    ثبت سرویس جدید در userbot_services (+ نگاشت نودها) و برگرداندن service_id.
    """
    comment_parts = []
    if panel_user_uuid:
        comment_parts.append(f"uuid:{panel_user_uuid}")
    comment_parts.append(f"price:{int(price or 0)}")
    comment_parts.append(f"code:{service_code}")
    comment = "|".join(comment_parts)

    conn = userbot_db._get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO userbot_services
            (user_id, name, server_id, server_title, usage_current, usage_limit, days_left, last_online, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(internal_user_id),
                str(name or ""),
                int(server_id),
                str(server_title or ""),
                0.0,
                float(usage_limit or 0),
                int(days_left or 0),
                None,
                comment,
            ),
        )
        service_db_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    for node in nodes or []:
        try:
            node_sid = int(node.get("server_id") or 0)
            node_uuid = str(node.get("panel_user_uuid") or "").strip()
            if node_sid <= 0 or not node_uuid:
                continue
            userbot_db.add_service_node(
                service_id=int(service_db_id),
                server_id=node_sid,
                panel_user_uuid=node_uuid,
                server_title=str(node.get("server_title") or ""),
                panel_user_id=(
                    str(node.get("panel_user_id"))
                    if node.get("panel_user_id") is not None
                    else None
                ),
                marzban_username=str(node.get("marzban_username") or "").strip(),
                is_active=1,
            )
        except Exception as e:
            logger.warning(
                "Failed to add service node (service_id=%s, server_id=%s): %s",
                service_db_id, node.get("server_id"), e,
            )
    return int(service_db_id or 0)
