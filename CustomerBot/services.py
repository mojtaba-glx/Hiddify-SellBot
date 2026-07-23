# CustomerBot/services.py
# Business logic: buy service, renew, get configs
# All actions go through agent_db + Hiddify API

import logging
from typing import Any, Dict, Optional
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from Shared import agent_db, database, hiddify_api, multi_panel
from AgentBot.database import get_fixed_plan

logger = logging.getLogger(__name__)


def _extract_days_left(user_data: Dict[str, Any], fallback_days: int = 0) -> Optional[int]:
    for key in ("remaining_days", "remaining_day", "days_left", "package_days"):
        if user_data.get(key) is None:
            continue
        try:
            return int(float(user_data.get(key) or 0))
        except (TypeError, ValueError):
            continue

    expire_raw = str(user_data.get("expire_date") or user_data.get("expiry_date") or "").strip()
    if expire_raw:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                expire_dt = datetime.strptime(expire_raw, fmt)
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                delta = expire_dt - now
                return max(0, delta.days)
            except ValueError:
                continue

    return fallback_days if fallback_days > 0 else None


async def buy_service(
    agent_id: int,
    customer_id: int,
    plan_id: int,
    service_name: str = "",
) -> Dict[str, Any]:
    """Customer buys a service: deduct from agent wallet, create on panel."""
    # استفاده از پلن‌های نماینده
    plan = get_fixed_plan(agent_id, plan_id)
    if not plan:
        return {"ok": False, "error": "plan_not_found"}

    sale_price = int(plan.get("price", 0))
    wholesale = agent_db.calculate_wholesale_price(
        agent_id,
        float(plan.get("gb", 0)),
        int(plan.get("days", 0)),
        int(plan.get("server_id", 0)) if plan.get("server_id") else 0,
    )
    days = int(plan.get("days", 0))
    gb = float(plan.get("gb", 0))
    server_id = int(plan.get("server_id", 0)) if plan.get("server_id") else 0

    # Sale price must be set by agent
    if sale_price <= 0:
        return {"ok": False, "error": "sale_price_not_set"}

    # Check agent wallet
    wallet = agent_db.get_wallet(agent_id)
    if int(wallet.get("balance", 0)) < wholesale:
        return {"ok": False, "error": "agent_no_balance"}

    # Deduct wholesale from agent wallet
    ok, wallet = agent_db.deduct_wallet(
        agent_id, wholesale,
        description=f"Customer purchase: {service_name or plan.get('title', '')}",
    )
    if not ok:
        return {"ok": False, "error": "deduct_failed"}

    # Get server
    server = database.get_server_by_id(server_id)
    if not server:
        agent_db.charge_wallet(agent_id, wholesale, description="Refund: server not found")
        return {"ok": False, "error": "server_not_found"}

    # Create user on Hiddify panel
    user_uuid = str(uuid4())
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start_date = now.strftime("%Y-%m-%d")

    payload = {
        "name": service_name or f"cust_{customer_id}_svc",
        "usage_limit_GB": gb,
        "package_days": days,
        "start_date": start_date,
        "current_usage_GB": 0,
        "is_active": True,
    }

    try:
        panel_user = await multi_panel.create_user(server, payload)
        panel_uuid = str(panel_user.get("uuid") or user_uuid).strip()
        panel_user_id = str(panel_user.get("id") or "").strip()
        marzban_username = str(panel_user.get("_marzban_username") or "").strip()
    except Exception as e:
        logger.error("buy_service create_user failed agent=%s: %s", agent_id, e)
        agent_db.charge_wallet(agent_id, wholesale, description=f"Refund: API error")
        return {"ok": False, "error": f"api_error: {str(e)[:100]}"}

    # Save in DB
    svc_name = service_name or f"{plan.get('title', '')}"
    svc = agent_db.create_service(
        agent_id=agent_id,
        customer_id=customer_id,
        server_id=server_id,
        server_title=server.get("title", ""),
        name=svc_name,
        panel_user_uuid=panel_uuid,
        usage_limit=gb,
        days=days,
        wholesale_price=wholesale,
        sale_price=sale_price,
    )

    agent_db.add_service_node(
        service_id=svc["id"],
        server_id=server_id,
        server_title=server.get("title", ""),
        panel_user_uuid=panel_uuid,
        panel_user_id=panel_user_id,
        marzban_username=marzban_username,
    )

    return {
        "ok": True,
        "service": svc,
        "panel_uuid": panel_uuid,
        "wallet_balance": wallet.get("balance", 0),
    }


async def renew_service(service_id: int, extra_days: int = 30) -> Dict[str, Any]:
    """Renew: proportional cost deducted from agent wallet."""
    svc = agent_db.get_service_by_id(service_id)
    if not svc:
        return {"ok": False, "error": "service_not_found"}

    agent_id = int(svc["agent_id"])
    server_id = int(svc["server_id"])
    panel_uuid = str(svc.get("panel_user_uuid", "")).strip()
    wholesale = int(svc.get("wholesale_price", 0))
    original_days = int(svc.get("days_left", 0)) or int(svc.get("days", 0)) or 30

    cost = wholesale  # full period
    if extra_days < original_days:
        cost = int(wholesale * extra_days / max(original_days, 1))

    wallet = agent_db.get_wallet(agent_id)
    if int(wallet.get("balance", 0)) < cost:
        return {"ok": False, "error": "agent_no_balance"}

    ok, wallet = agent_db.deduct_wallet(agent_id, cost, description=f"Renew svc #{service_id}")
    if not ok:
        return {"ok": False, "error": "deduct_failed"}

    agent_db.renew_service(service_id, extra_days=extra_days)

    if panel_uuid:
        server = database.get_server_by_id(server_id)
        if server:
            try:
                current_svc = agent_db.get_service_by_id(service_id)
                new_end = str(current_svc.get("end_date", "")).strip()
                if new_end:
                    # Find marzban_username from service_nodes
                    marzban_un = ""
                    try:
                        from Shared import userbot_db
                        for node in userbot_db.get_service_nodes(service_id):
                            if int(node.get("server_id") or 0) == server_id:
                                marzban_un = str(node.get("marzban_username") or "").strip()
                                break
                    except Exception:
                        pass
                    await multi_panel.patch_user(
                        server, panel_uuid,
                        {"expire_date": new_end.split(" ")[0]},
                        marzban_username=marzban_un,
                    )
            except Exception as e:
                logger.warning("renew patch failed svc=%s: %s", service_id, e)

    return {"ok": True, "wallet_balance": wallet.get("balance", 0)}


async def get_configs(service_id: int) -> Dict[str, Any]:
    """Get subscription configs from Hiddify panel."""
    svc = agent_db.get_service_by_id(service_id)
    if not svc:
        return {"ok": False, "error": "service_not_found"}

    panel_uuid = str(svc.get("panel_user_uuid", "")).strip()
    server_id = int(svc["server_id"])
    if not panel_uuid:
        return {"ok": False, "error": "no_uuid"}

    server = database.get_server_by_id(server_id)
    if not server:
        return {"ok": False, "error": "server_not_found"}

    try:
        # Find marzban_username from service_nodes
        marzban_un = ""
        try:
            from Shared import userbot_db
            for node in userbot_db.get_service_nodes(service_id):
                if int(node.get("server_id") or 0) == server_id:
                    marzban_un = str(node.get("marzban_username") or "").strip()
                    break
        except Exception:
            pass
        configs = await multi_panel.get_user_configs(
            server, panel_uuid, marzban_username=marzban_un,
        )
        return {"ok": True, "configs": configs}
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


async def sync_service_usage(service_id: int) -> Dict[str, Any]:
    """Sync usage from Hiddify panel to local DB."""
    return await refresh_service_status(service_id)


async def refresh_service_status(service_id: int) -> Dict[str, Any]:
    """Refresh service runtime from Hiddify panel and disable stale local rows."""
    svc = agent_db.get_service_by_id(service_id)
    if not svc:
        return {"ok": False, "error": "service_not_found"}

    panel_uuid = str(svc.get("panel_user_uuid", "")).strip()
    server_id = int(svc["server_id"])
    if not panel_uuid:
        return {"ok": False}

    server = database.get_server_by_id(server_id)
    if not server:
        return {"ok": False}

    try:
        user_data = await hiddify_api.get_user_by_uuid(server, panel_uuid)
        usage = float(user_data.get("current_usage_GB", 0) or 0)
        updates = {"usage_current": usage, "is_active": 1}
        if user_data.get("usage_limit_GB") is not None:
            updates["usage_limit"] = float(user_data.get("usage_limit_GB") or 0)
        derived_days = _extract_days_left(user_data, int(svc.get("days_left") or 0))
        if derived_days is not None:
            updates["days_left"] = int(derived_days)
        agent_db.update_service(service_id, updates)
        return {"ok": True, "usage_current": usage, "service": agent_db.get_service_by_id(service_id)}
    except Exception as e:
        try:
            users = await hiddify_api.list_users(server)
            exists = any(str(u.get("uuid") or u.get("id") or "").strip() == panel_uuid for u in users)
            if not exists:
                agent_db.update_service(service_id, {"is_active": 0, "days_left": 0})
                logger.info("disabled stale customer service svc=%s uuid=%s", service_id, panel_uuid)
                return {"ok": False, "error": "panel_user_not_found", "disabled": True}
        except Exception as list_error:
            logger.warning("verify stale service failed svc=%s: %s", service_id, list_error)
        logger.warning("sync_usage failed svc=%s: %s", service_id, e)
        return {"ok": False, "error": str(e)[:100]}
