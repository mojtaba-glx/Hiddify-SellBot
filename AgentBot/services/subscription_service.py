import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from Shared import agent_db, multi_panel, hiddify_api, database
from Shared.sub_links import get_or_create_bot_sub_links, get_service_panel_targets
from AgentBot.services.hiddify_service import (
    get_available_servers, get_server_by_id, get_agent_plans,
    create_user_on_panel, disable_user_on_panel, enable_user_on_panel,
    delete_user_on_panel, get_user_configs, revoke_user_link_on_panel,
)
from AgentBot.database import create_order as db_create_order

logger = logging.getLogger(__name__)


def _get_cluster_servers(server_id: int) -> List[Dict[str, Any]]:
    """سرور اصلی + نودهای زیرمجموعه (child) که target_server_id دارند.

    دقیقاً مثل UserBot/AdminBot: هنگام ساخت سرویس، کاربر باید روی کل خوشه
    ساخته شود تا لینک اشتراک هوشمند همه نودها را در بر بگیرد.
    """
    primary = database.get_server_by_id(server_id)
    if not primary:
        return []
    out: List[Dict[str, Any]] = [primary]
    seen: set[int] = {int(server_id)}
    for node in (primary.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        try:
            child_sid = int(node.get("target_server_id") or 0)
        except (TypeError, ValueError):
            child_sid = 0
        if child_sid <= 0 or child_sid in seen:
            continue
        child = database.get_server_by_id(child_sid)
        if not child:
            continue
        out.append(child)
        seen.add(child_sid)
    return out


async def _create_user_on_cluster(targets: List[Dict[str, Any]], payload: Dict[str, Any]) -> tuple[Optional[dict], List[dict]]:
    """روی همه افراد هدف با یک uuid مشترک کاربر می‌سازد (مثل UserBot).

    Returns: (primary_created, created_nodes) که created_nodes هر پیروز شامل
    server_id/server_title/panel_user_uuid/marzban_username/is_primary است.
    """
    shared_uuid = str((payload or {}).get("uuid") or "").strip() or (str(len(targets) and __import__("uuid").uuid4()))
    payload_base = dict(payload or {})
    payload_base["uuid"] = shared_uuid
    created_nodes: List[dict] = []
    primary_created: Optional[Dict[str, Any]] = None
    for idx, srv in enumerate(targets):
        created = None
        last_exc = None
        for attempt in (1, 2):
            try:
                created = await multi_panel.create_user(srv, payload_base)
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                msg = str(e).lower()
                is_transient = any(k in msg for k in ("readerror", "connecterror", "timeout", "timed out", "connection", "temporarily", "read error"))
                if idx == 0:
                    if is_transient and attempt == 1:
                        await asyncio.sleep(0.7)
                        continue
                    raise
                if is_transient and attempt == 1:
                    logger.warning("Cluster node create_user transient retry server=%s attempt=%s: %s", srv.get("id"), attempt, e)
                    await asyncio.sleep(0.7)
                    continue
                logger.warning("Cluster node create_user failed server=%s: %s", srv.get("id"), e)
                break
        if last_exc is not None and created is None:
            continue
        user_uuid = str(created.get("uuid") or created.get("id") or "").strip()
        if not user_uuid:
            if idx == 0:
                raise RuntimeError("uuid \u06a9\u0627\u0631\u0628\u0631 \u0633\u0627\u062e\u062a\u0647\u200c\u0634\u062f\u0647 \u0627\u0632 \u067e\u0646\u0644 \u062f\u0631\u06cc\u0627\u0641\u062a \u0646\u0634\u062f.")
            continue
        if idx == 0:
            primary_created = created
        marzban_username = str(created.get("_marzban_username") or "").strip()
        created_nodes.append(
            {
                "server_id": int(srv.get("id") or 0),
                "server_title": srv.get("title") or f"\u0633\u0631\u0648\u0631 #{srv.get('id')}",
                "panel_user_uuid": user_uuid,
                "marzban_username": marzban_username,
                "is_primary": idx == 0,
            }
        )
    if primary_created is None:
        raise RuntimeError("no primary node created")
    for item in created_nodes:
        await _rollback_node_if_failed(item) if item.get("_failed") else None
    return primary_created, created_nodes


async def _rollback_node_if_failed(item: dict) -> None:
    pass


async def create_subscription(agent_id: int, customer_id: int, server_id: int, plan: Dict[str, Any], name: str, note: str = "") -> Optional[Dict[str, Any]]:
    server = get_server_by_id(server_id)
    if not server:
        return None

    days = int(plan.get("days", 30))
    gb = float(plan.get("gb", 0))
    wholesale = int(plan.get("wholesale_price", 0))
    sale = int(plan.get("sale_price", 0))

    ok, wallet = agent_db.deduct_wallet(agent_id, wholesale, description=f"\u062e\u0631\u06cc\u062f \u0633\u0631\u0648\u06cc\u0633: {name}", service_id=0)
    if not ok:
        return None
    # شناسه تراکنش خرید برای اتصال قطعی به سرویس واقعی پس از ساخت
    purchase_tx_id = 0
    try:
        purchase_tx_id = int((wallet or {}).get("_transaction_id") or 0)
    except (TypeError, ValueError):
        purchase_tx_id = 0

    targets = _get_cluster_servers(server_id)
    if not targets:
        targets = [server]

    payload = {
        "name": name,
        "usage_limit_GB": gb,
        "package_days": days,
    }
    if str(note or "").strip():
        payload["comment"] = str(note).strip()

    try:
        panel_result, created_nodes = await _create_user_on_cluster(targets, payload)
    except Exception as e:
        logger.error("Cluster create failed for %s: %s", name, e)
        agent_db.refund_wallet(agent_id, wholesale, description=f"\u0628\u0627\u0632\u06af\u0631\u062f\u0627\u0646\u062a \u0645\u0648\u062c\u0648\u062f\u06cc \u0628\u0647 \u062f\u0644\u06cc\u0644 \u062e\u0637\u0627\u06cc \u0633\u0627\u062e\u062a \u06a9\u0627\u0631\u0628\u0631: {name}")
        try:
            from Shared.admin_reports import notify_admin_delivery_report
            await notify_admin_delivery_report(
                action_title="ساخت سرویس نماینده",
                agent=agent_db.get_agent_by_id(agent_id),
                service_name=name,
                server_title=server.get("title", f"\u0633\u0631\u0648\u0631 #{server_id}"),
                volume_gb=gb,
                days=days,
                amount=wholesale,
                status="error",
                error=str(e)[:120],
            )
        except Exception as _report_e:
            logger.warning("Failed to send delivery error report: %s", _report_e)
        return None
    panel_uuid = str(panel_result.get("uuid", "") or panel_result.get("id", "") or "").strip()
    primary_marzban = str((panel_result or {}).get("_marzban_username") or "").strip()

    svc = agent_db.create_service(
        agent_id=agent_id,
        customer_id=customer_id,
        server_id=server_id,
        server_title=server.get("title", f"\u0633\u0631\u0648\u0631 #{server_id}"),
        name=name,
        panel_user_uuid=panel_uuid,
        usage_limit=gb,
        days=days,
        wholesale_price=wholesale,
        sale_price=sale,
        note=note,
    )
    if not svc or not panel_uuid:
        # Remote users must not be orphaned when local persistence fails.
        for item in created_nodes:
            try:
                await delete_user_on_panel(
                    str(item.get("panel_user_uuid") or ""),
                    int(item.get("server_id") or 0),
                    marzban_username=str(item.get("marzban_username") or ""),
                )
            except Exception as rollback_error:
                logger.error("Failed rolling back orphan panel user: %s", rollback_error)
        agent_db.refund_wallet(agent_id, wholesale, description=f"بازگشت وجه ساخت ناموفق سرویس: {name}")
        return None
    if svc and panel_uuid:
        for item in created_nodes:
            agent_db.add_service_node(
                service_id=svc["id"],
                server_id=int(item.get("server_id") or 0),
                server_title=item.get("server_title") or "",
                panel_user_uuid=str(item.get("panel_user_uuid") or "").strip(),
                marzban_username=str(item.get("marzban_username") or "").strip(),
            )
        # اتصال قطعی تراکنش خرید اولیه به سرویس واقعی (بدون کسر دوباره؛
        # فقط وقتی تراکنش هنوز service_id=0 دارد)
        try:
            if purchase_tx_id > 0:
                agent_db.attach_transaction_to_service(purchase_tx_id, agent_id, int(svc["id"]))
        except Exception as attach_error:
            logger.warning("Failed to link purchase tx %s to service: %s", purchase_tx_id, attach_error)

    # اگر بعضی نودها در دسترس نبودند → گزارش partial به ادمین + دکمه sync.
    created_set = {int(int(n.get("server_id") or 0)) for n in (created_nodes or [])}
    pending_servers = [
        str(t.get("title") or f"\u0633\u0631\u0648\u0631 #{t.get('id')}")
        for t in targets
        if int(t.get("id") or 0) not in created_set
    ]
    if pending_servers:
        try:
            from Shared.admin_reports import notify_admin_delivery_report
            await notify_admin_delivery_report(
                action_title="ساخت سرویس نماینده",
                agent=agent_db.get_agent_by_id(agent_id),
                customer_name=_customer_display_name(customer_id),
                service_name=name,
                server_title=server.get("title", f"\u0633\u0631\u0648\u0631 #{server_id}"),
                volume_gb=gb,
                days=days,
                amount=wholesale,
                status="partial",
                pending_servers=pending_servers,
                sync_primary_server_id=int(server_id or 0),
            )
        except Exception as _report_e:
            logger.warning("Failed to send partial delivery report: %s", _report_e)
    else:
        try:
            from Shared.admin_reports import notify_admin_delivery_report
            await notify_admin_delivery_report(
                action_title="ساخت سرویس نماینده",
                agent=agent_db.get_agent_by_id(agent_id),
                customer_name=_customer_display_name(customer_id),
                service_name=name,
                server_title=server.get("title", f"\u0633\u0631\u0648\u0631 #{server_id}"),
                volume_gb=gb,
                days=days,
                amount=wholesale,
                status="success",
            )
        except Exception as _report_e:
            logger.warning("Failed to send delivery success report: %s", _report_e)

    return svc


def _customer_display_name(customer_id: int) -> str:
    try:
        cust = agent_db.get_customer_by_id(customer_id) or {}
        return (
            str(cust.get("full_name") or "").strip()
            or str(cust.get("username") or "").strip()
            or f"#{customer_id}"
        )
    except Exception:
        return f"#{customer_id}"


async def renew_subscription(agent_id: int, service_id: int, extra_days: int, extra_gb: float = 0, override_cost: Optional[int] = None, volume_mode: str = None, time_mode: str = None) -> Optional[Dict[str, Any]]:
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        return None
    if not get_server_by_id(int(svc.get("server_id") or 0)):
        logger.error("Cannot renew service %s: primary server is missing", service_id)
        return None

    if volume_mode is None or time_mode is None:
        admin_volume, admin_time, _ = get_admin_renew_policy()
        volume_mode = volume_mode or admin_volume
        time_mode = time_mode or admin_time

    # تمدید نمایندگی همیشه به‌صورت «ریست» است (مثل ربات ادمین):
    # حجم و زمان قبلی صفر و مقدار جدید جایگزین می‌شود.
    volume_mode = "reset"
    time_mode = "reset"

    wholesale = int(svc.get("wholesale_price", 0))
    cost = 0
    if extra_days > 0:
        if override_cost is not None:
            cost = int(override_cost)
        else:
            original_days = int(svc.get("days_left", 30)) or 30
            cost = int(wholesale * extra_days / original_days) if original_days > 0 else wholesale
        ok, _ = agent_db.deduct_wallet(agent_id, cost, description=f"\u062a\u0645\u062f\u06cc\u062f \u0633\u0631\u0648\u06cc\u0633: {svc.get('name', '')}", service_id=service_id)
        if not ok:
            return None

    # Keep the previous local state so a primary-panel failure cannot leave a
    # paid renewal in the database without a real subscription.
    old_state = {
        key: svc.get(key)
        for key in ("days_left", "usage_limit", "usage_current", "start_date", "end_date", "is_active")
    }
    if not agent_db.renew_service_with_policy(service_id, extra_days, extra_gb, volume_mode, time_mode):
        if cost > 0:
            agent_db.refund_wallet(agent_id, cost, description=f"بازگشت وجه تمدید ناموفق سرویس #{service_id}", service_id=service_id)
        return None
    updated = agent_db.get_service_by_id(service_id)

    # Sync with panel (update usage_limit_GB and package_days) on all cluster nodes
    renew_failed: list[str] = []
    if updated:
        sid = int(updated.get("server_id") or 0)
        server = get_server_by_id(sid)
        targets = _get_cluster_servers(sid) if sid > 0 else []
        if not targets and server:
            targets = [server]
        new_usage = float(updated.get("usage_limit", 0) or 0)
        new_days = int(updated.get("days_left", 0) or 0)
        patch_data = {"usage_limit_GB": new_usage, "package_days": new_days}
        if str(volume_mode).strip().lower() == "reset":
            patch_data["current_usage_GB"] = 0
        if str(time_mode).strip().lower() == "reset":
            patch_data["start_date"] = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        primary_ok = False
        for tgt in targets:
            tgt_id = int(tgt.get("id") or 0)
            marzban_un = _lookup_marzban_username(service_id, tgt_id)
            try:
                await multi_panel.patch_user(
                    tgt,
                    updated["panel_user_uuid"],
                    patch_data,
                    marzban_username=marzban_un,
                )
                if tgt_id == sid:
                    primary_ok = True
            except Exception as e:
                logger.warning("renew panel sync failed svc=%s server=%s: %s", service_id, tgt_id, e)
                renew_failed.append(str(tgt.get("title") or f"\u0633\u0631\u0648\u0631 #{tgt_id}"))
                if tgt_id == sid:
                    break

        if targets and not primary_ok:
            # Do not touch secondary nodes after the authoritative node fails.
            agent_db.update_service(service_id, old_state)
            if cost > 0:
                agent_db.refund_wallet(agent_id, cost, description=f"بازگشت وجه تمدید ناموفق سرویس #{service_id}", service_id=service_id)
            logger.error("Primary panel renewal failed; local state and wallet restored (service=%s)", service_id)
            return None

        # فعال‌سازی مجدد اشتراک روی سرور اصلی و همه نودها (اگر غیرفعال بود)
        primary_enable_ok = False
        for tgt in targets:
            tgt_id = int(tgt.get("id") or 0)
            marzban_un = _lookup_marzban_username(service_id, tgt_id)
            try:
                await enable_user_on_panel(updated["panel_user_uuid"], tgt_id, marzban_username=marzban_un)
                if tgt_id == sid:
                    primary_enable_ok = True
            except Exception as e:
                logger.warning("renew re-activate failed svc=%s server=%s: %s", service_id, tgt_id, e)
                renew_failed.append(str(tgt.get("title") or f"سرور #{tgt_id}"))

        if targets and not primary_enable_ok:
            agent_db.set_service_active(service_id, False)
            logger.error("Primary panel renewal applied but re-activation failed (service=%s)", service_id)
        else:
            agent_db.set_service_active(service_id, True)

        # گزارش به ادمین
        try:
            from Shared.admin_reports import notify_admin_delivery_report
            if not primary_ok and renew_failed:
                await notify_admin_delivery_report(
                    action_title="تمدید سرویس نماینده",
                    agent=agent_db.get_agent_by_id(agent_id),
                    customer_name=_customer_display_name(int(svc.get("customer_id") or 0)),
                    service_name=str(svc.get("name") or ""),
                    server_title=server.get("title", f"\u0633\u0631\u0648\u0631 #{sid}") if server else f"\u0633\u0631\u0648\u0631 #{sid}",
                    volume_gb=new_usage,
                    days=new_days,
                    amount=cost if extra_days > 0 else 0,
                    status="error",
                    error="\n".join(renew_failed[:3]),
                )
            elif renew_failed:
                await notify_admin_delivery_report(
                    action_title="تمدید سرویس نماینده",
                    agent=agent_db.get_agent_by_id(agent_id),
                    customer_name=_customer_display_name(int(svc.get("customer_id") or 0)),
                    service_name=str(svc.get("name") or ""),
                    server_title=server.get("title", f"\u0633\u0631\u0648\u0631 #{sid}") if server else f"\u0633\u0631\u0648\u0631 #{sid}",
                    volume_gb=new_usage,
                    days=new_days,
                    amount=cost if extra_days > 0 else 0,
                    status="partial",
                    pending_servers=renew_failed,
                    sync_primary_server_id=int(sid or 0),
                )
        except Exception as _report_e:
            logger.warning("Failed to send renew delivery report: %s", _report_e)

    updated["_renew_volume_mode"] = volume_mode
    updated["_renew_time_mode"] = time_mode
    return updated


def get_admin_renew_policy() -> Tuple[str, str, bool]:
    """الگوی تمدید تعریف‌شده در ربات ادمین: (حجم add/reset، زمان add/reset، enable_renew)."""
    try:
        from Shared import userbot_db
        volume, time = userbot_db.get_renew_modes()
        s = userbot_db.get_buy_renew_settings()
        return volume, time, bool(s.get("enable_renew", True))
    except Exception:
        return "add", "add", True


def _lookup_marzban_username(service_id: int, server_id: int) -> str:
    """Look up the marzban_username for a given service+server from agent_service_nodes."""
    try:
        nodes = agent_db.get_service_nodes(service_id) or []
        for n in nodes:
            if int(n.get("server_id") or 0) == server_id:
                return str(n.get("marzban_username") or "").strip()
    except Exception:
        pass
    return ""


def _panel_user_already_absent(exc: Exception) -> bool:
    """Return true when a delete failed only because the user is already gone."""
    message = str(exc or "").strip().lower()
    return any(
        marker in message
        for marker in (
            "user not found",
            "client not found",
            "no such user",
            "not found (uuid=",
            "کاربر یافت نشد",
        )
    )


async def disable_subscription(agent_id: int, service_id: int) -> bool:
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        return False
    sid = int(svc.get("server_id") or 0)
    marzban_un = _lookup_marzban_username(service_id, sid)
    try:
        await disable_user_on_panel(svc.get("panel_user_uuid", ""), sid, marzban_username=marzban_un)
    except Exception as e:
        logger.error("disable panel API failed svc=%s: %s", service_id, e)
    agent_db.set_service_active(service_id, False)
    return True


async def enable_subscription(agent_id: int, service_id: int) -> bool:
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        return False
    sid = int(svc.get("server_id") or 0)
    marzban_un = _lookup_marzban_username(service_id, sid)
    try:
        await enable_user_on_panel(svc.get("panel_user_uuid", ""), sid, marzban_username=marzban_un)
    except Exception as e:
        logger.error("enable panel API failed svc=%s: %s", service_id, e)
    agent_db.set_service_active(service_id, True)
    return True


async def delete_subscription(agent_id: int, service_id: int) -> bool:
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        return False

    # Use the same complete cluster resolver as subscription delivery/runtime.
    # Besides saved mappings, it includes configured child nodes and therefore
    # also covers legacy services whose X-UI node mapping was never persisted.
    targets = get_service_panel_targets(svc)
    if not targets:
        logger.error("delete_subscription has no panel targets svc=%s", service_id)
        return False

    failures: List[str] = []
    for server, panel_uuid, marzban_username in targets:
        try:
            server_id = int((server or {}).get("id") or 0)
        except (TypeError, ValueError):
            server_id = 0
        panel_uuid = str(panel_uuid or "").strip()
        if server_id <= 0 or not panel_uuid:
            failures.append(f"server={server_id or '?'}: invalid deletion target")
            continue
        try:
            await multi_panel.delete_user(
                server,
                panel_uuid,
                marzban_username=str(marzban_username or "").strip(),
            )
        except Exception as e:
            # Deletion is idempotent: an already-absent panel user is complete.
            if not _panel_user_already_absent(e):
                failures.append(f"server={server_id}: {str(e)[:100]}")
                logger.error(
                    "delete panel node failed svc=%s server=%s: %s",
                    service_id,
                    server_id,
                    e,
                )
                continue
        agent_db.delete_service_node(service_id, server_id, panel_uuid)

    if failures:
        logger.warning("delete_subscription partial failures svc=%s: %s", service_id, "; ".join(failures))
        # Keep the local service and failed mappings so the agent can retry.
        return False

    return agent_db.delete_service(service_id)


async def change_subscription_link(agent_id: int, service_id: int) -> Optional[Dict[str, Any]]:
    """Regenerate user config links (new UUID on Hiddify, revoke sub on Marzban)."""
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        return None

    sid = int(svc.get("server_id") or 0)
    old_uuid = str(svc.get("panel_user_uuid") or "")
    if not old_uuid:
        return None

    marzban_un = _lookup_marzban_username(service_id, sid)
    try:
        result = await revoke_user_link_on_panel(old_uuid, sid, marzban_username=marzban_un)
    except Exception as e:
        logger.error("change_subscription_link panel failed svc=%s: %s", service_id, e)
        return None

    new_uuid = str(result.get("new_uuid") or "")
    if new_uuid and new_uuid != old_uuid:
        agent_db.update_service(service_id, {"panel_user_uuid": new_uuid})
        agent_db.update_service_node_uuid(service_id, sid, old_uuid, new_uuid)

    return agent_db.get_service_by_id(service_id)


async def rename_service_on_panels(agent_id: int, service_id: int, new_name: str) -> Tuple[bool, str]:
    """تغییر نام اشتراک روی همه پنل‌ها (اصلی + نودها) و سپس در DB نماینده."""
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        return False, "❌ سرویس پیدا نشد."
    old_name = str(svc.get("name") or "").strip()
    if new_name == old_name:
        return False, "ℹ️ نام جدید با نام فعلی یکسان است."
    # اعتبارسنجی طول
    if len(new_name) < 3:
        return False, "❌ نام اشتراک خیلی کوتاه است. حداقل 3 کاراکتر وارد کنید."
    if len(new_name) > 64:
        return False, "❌ نام اشتراک خیلی طولانی است. حداکثر 64 کاراکتر وارد کنید."

    from Shared.sub_links import get_service_panel_targets
    targets = get_service_panel_targets(svc)
    if not targets:
        return False, "❌ مسیرهای پنل این اشتراک یافت نشد."

    errors: List[str] = []
    ok_count = 0
    for srv, uuid, marzban_un in targets:
        try:
            await multi_panel.patch_user(srv, uuid, {"name": new_name}, marzban_username=marzban_un)
            ok_count += 1
        except Exception as e:
            title = str(srv.get("title") or f"سرور #{srv.get('id')}")
            errors.append(f"{title}: {str(e)[:80]}")

    if ok_count == 0:
        preview = "\n".join(errors[:3])
        extra = f"\n... و {len(errors) - 3} خطای دیگر" if len(errors) > 3 else ""
        return False, "❌ تغییر نام روی همه سرورها انجام نشد.\n" + preview + extra

    ok_db = agent_db.update_service(service_id, {"name": new_name})
    if not ok_db:
        return False, "❌ بروزرسانی نام در دیتابیس انجام نشد."

    margin = ""
    if errors:
        margin = (
            "\n\n⚠️ نام روی همه نودها اعمال شد اما "
            + str(len(errors))
            + " نود در دسترس نبود (تا برگشتنشان بعداً همگام می‌شود):\n- "
            + "\n- ".join(errors[:3])
        )
    return True, "✅ نام اشتراک با موفقیت بروزرسانی شد." + margin


async def get_configs(agent_id: int, service_id: int) -> list:
    """Aggregated configs from all nodes (Hiddify + X-UI) — fixes X-UI node missing."""
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        return []
    from Shared.sub_links import get_service_panel_targets
    targets = get_service_panel_targets(svc)
    if not targets:
        sid = int(svc.get("server_id") or 0)
        marzban_un = _lookup_marzban_username(service_id, sid)
        return await get_user_configs(svc.get("panel_user_uuid", ""), sid, marzban_username=marzban_un)

    aggregated: list = []
    seen: set = set()
    for srv, uuid, marzban_un in targets:
        try:
            cfgs = await get_user_configs(uuid, int(srv.get("id") or 0), marzban_username=marzban_un)
            for item in cfgs or []:
                link = item if isinstance(item, str) else str((item or {}).get("link") or "").strip()
                if not link or link in seen:
                    continue
                seen.add(link)
                aggregated.append(item if isinstance(item, dict) else {"link": link})
        except Exception as e:
            logger.warning("Agent get_configs node failed svc=%s server=%s: %s", service_id, srv.get("id"), e)
            continue
    return aggregated


def get_managed_sub_link(agent_id: int, service_id: int) -> str:
    """لینک اشتراک هوشمند سرویس (دقیقاً مثل ربات مشتری)."""
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        return ""
    link, _ = get_or_create_bot_sub_links(svc)
    return str(link or "").strip()


def get_subs_link_settings() -> Dict[str, bool]:
    """خواندن تنظیمات «وضعیت نمایش لینک اشتراک» که ادمین در ربات ادمین تعریف کرده."""
    try:
        from Shared import userbot_db
        shared = userbot_db.get_subscription_settings()
        if isinstance(shared, dict) and shared:
            return {k: bool(shared[k]) for k in (
                "show_direct_config", "show_sub_link", "show_auto_sub_link",
                "show_sub_link_b64", "show_multi_server", "show_multi_server_b64",
            )}
    except Exception:
        pass
    return {}


def get_sub_link_for_type(agent_id: int, service_id: int, link_type: str) -> str:
    """ساخت لینک برای هر نوع کانفیگ طبق تنظیمات ادمین (مثل ربات مشتری)."""
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        return ""
    from Shared.sub_links import get_service_node_base_urls
    base_urls = get_service_node_base_urls(svc)
    if not base_urls:
        return ""
    base_url = base_urls[0].rstrip("/")
    link_type = str(link_type or "").strip()
    if link_type == "sub_link":
        return f"{base_url}/all.txt"
    if link_type == "auto_sub":
        return f"{base_url}/sub/?asn=unknown"
    if link_type == "sub_b64":
        return f"{base_url}/all.txt?base64=1"
    if link_type == "multi":
        link, _ = get_or_create_bot_sub_links(svc)
        return str(link or "").strip()
    if link_type == "multi_b64":
        _, link_b64 = get_or_create_bot_sub_links(svc)
        return str(link_b64 or "").strip()
    return ""


def _parse_panel_datetime(value: Any) -> Optional[datetime]:
    """Parse panel timestamps and normalize them to timezone-aware UTC."""
    if value is None or value == "":
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        stamp = float(raw)
        if stamp > 0:
            if stamp > 10_000_000_000:
                stamp /= 1000.0
            return datetime.fromtimestamp(stamp, timezone.utc)
    except (TypeError, ValueError, OSError):
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _panel_expiry_datetime(user: Dict[str, Any], now: Optional[datetime] = None) -> Optional[datetime]:
    for key in ("expire", "expire_date", "end_date", "expires_at", "expiry_date", "expiration_date"):
        end = _parse_panel_datetime((user or {}).get(key))
        if end is not None:
            return end
    start = _parse_panel_datetime((user or {}).get("start_date"))
    try:
        package_days = float((user or {}).get("package_days") or 0)
    except (TypeError, ValueError):
        package_days = 0
    if start is not None and package_days > 0:
        return start + timedelta(days=package_days)
    for key in ("remaining_days", "remaining_day", "days_left"):
        try:
            days = float((user or {}).get(key))
        except (TypeError, ValueError):
            continue
        base = now or datetime.now(timezone.utc)
        return base + timedelta(days=days)
    return None


def _duration_words(value: float) -> str:
    """Return up to two exact units instead of dropping hours after whole days."""
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return "چند لحظه"
    if seconds < 60:
        return "چند ثانیه"
    if seconds < 3600:
        return f"{seconds // 60} دقیقه"
    if seconds < 86400:
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        return f"{hours} ساعت" + (f" و {minutes} دقیقه" if minutes else "")
    days, remainder = divmod(seconds, 86400)
    hours = remainder // 3600
    return f"{days} روز" + (f" و {hours} ساعت" if hours else "")


def _human_duration(value: float) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "نامشخص"
    if seconds < 0:
        seconds = 0
    return f"{_duration_words(seconds)} پیش"


def format_service_expiry(svc: Dict[str, Any], now: Optional[datetime] = None) -> str:
    """Format remaining subscription time from its absolute end timestamp."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    end = _parse_panel_datetime(
        (svc or {}).get("_panel_end_date") or (svc or {}).get("end_date")
    )
    if end is not None:
        remaining = (end - current).total_seconds()
        if remaining >= 0:
            if remaining < 60:
                return "کمتر از یک دقیقه دیگر"
            return f"{_duration_words(remaining)} دیگر"
        return f"منقضی شده ({_human_duration(abs(remaining))})"

    raw_days = (svc or {}).get("_panel_days_left")
    if raw_days is None:
        raw_days = (svc or {}).get("days_left")
    try:
        days = int(float(raw_days))
    except (TypeError, ValueError):
        return "نامشخص"
    if days > 0:
        return f"{days} روز دیگر"
    if days < 0:
        return f"منقضی شده ({abs(days)} روز پیش)"
    return "امروز"


async def get_service_last_online(svc) -> str:
    """وضعیت آخرین اتصال کاربر از پنل:
    «آنلاین» اگر در حال استفاده است، «X پیش» اگر مدتی قبل وصل شده، در غیر این صورت «هرگز»."""
    ONLINE_WINDOW = 15 * 60  # ثانیه
    CLOCK_SKEW = 120
    if not isinstance(svc, dict):
        return "نامشخص"
    targets = get_service_panel_targets(svc)
    if not targets:
        sid = int(svc.get("server_id") or 0)
        server = get_server_by_id(sid)
        uuid = str(svc.get("panel_user_uuid") or "").strip()
        if server and uuid:
            targets = [(server, uuid, "")]
    if not targets:
        return "نامشخص"

    async def _fetch(target):
        server, uuid, _marzban_username = target
        try:
            return target, await hiddify_api.get_user_by_uuid(server, uuid)
        except Exception as exc:
            logger.warning(
                "Agent runtime refresh failed svc=%s server=%s: %s",
                svc.get("id"),
                (server or {}).get("id"),
                type(exc).__name__,
            )
            return target, None

    fetched = await asyncio.gather(*[_fetch(target) for target in targets])
    available = [(target, user) for target, user in fetched if isinstance(user, dict) and user]
    if not available:
        return "نامشخص"

    primary_id = int(svc.get("server_id") or 0)
    authoritative = next(
        (user for (server, _uuid, _name), user in available if int((server or {}).get("id") or 0) == primary_id),
        available[0][1],
    )
    now = datetime.now(timezone.utc)
    updates: Dict[str, Any] = {}
    usage_values = []
    for _target, user in available:
        try:
            usage_values.append(float(user.get("current_usage_GB") or 0))
        except (TypeError, ValueError):
            pass
    if usage_values and len(available) == len(targets):
        updates["usage_current"] = sum(usage_values)
    try:
        if authoritative.get("usage_limit_GB") is not None:
            updates["usage_limit"] = float(authoritative.get("usage_limit_GB") or 0)
    except (TypeError, ValueError):
        pass
    end = _panel_expiry_datetime(authoritative, now)
    if end is not None:
        remaining = (end - now).total_seconds()
        updates["end_date"] = end.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        updates["days_left"] = math.ceil(remaining / 86400) if remaining >= 0 else math.floor(remaining / 86400)
        svc["_panel_end_date"] = end.isoformat()
        svc["_panel_days_left"] = updates["days_left"]
    if "is_active" in authoritative:
        active_raw = authoritative.get("is_active")
        if active_raw is not None:
            if isinstance(active_raw, str):
                active = active_raw.strip().lower() not in {"0", "false", "off", "inactive", "disabled"}
            else:
                active = bool(active_raw)
            updates["is_active"] = 1 if active else 0
    if updates:
        svc.update(updates)
        try:
            service_id = int(svc.get("id") or 0)
            if service_id > 0:
                agent_db.update_service(service_id, updates)
        except Exception as exc:
            logger.warning("Agent runtime cache update failed svc=%s: %s", svc.get("id"), type(exc).__name__)

    latest_dt: Optional[datetime] = None
    latest_source = ""
    for _target, user in available:
        candidate = _parse_panel_datetime(user.get("last_online"))
        if candidate is not None and (latest_dt is None or candidate > latest_dt):
            latest_dt = candidate
            latest_source = str(user.get("_source") or "").strip().lower()
    if latest_dt is None:
        return "هرگز"
    seconds = (now - latest_dt).total_seconds()
    online_window = 90 if latest_source == "xui" else ONLINE_WINDOW
    if -CLOCK_SKEW <= seconds <= online_window:
        return "آنلاین"
    return _human_duration(seconds)
