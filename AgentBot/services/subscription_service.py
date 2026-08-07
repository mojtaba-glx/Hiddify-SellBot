import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from Shared import agent_db, multi_panel, hiddify_api
from Shared.sub_links import get_or_create_bot_sub_links
from AgentBot.services.hiddify_service import (
    get_available_servers, get_server_by_id, get_agent_plans,
    create_user_on_panel, disable_user_on_panel, enable_user_on_panel,
    delete_user_on_panel, get_user_configs, revoke_user_link_on_panel,
)
from AgentBot.database import create_order as db_create_order

logger = logging.getLogger(__name__)


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

    try:
        panel_result = await create_user_on_panel(server_id, server, name, gb, days, comment=note)
    except Exception:
        agent_db.charge_wallet(agent_id, wholesale, description=f"\u0628\u0627\u0632\u06af\u0631\u062f\u0627\u0646\u062a \u0645\u0648\u062c\u0648\u062f\u06cc \u0628\u0647 \u062f\u0644\u06cc\u0644 \u062e\u0637\u0627\u06cc \u0633\u0627\u062e\u062a \u06a9\u0627\u0631\u0628\u0631: {name}")
        return None
    panel_uuid = panel_result.get("uuid", "") if panel_result else ""
    marzban_username = str((panel_result or {}).get("_marzban_username") or "").strip()

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
    if svc and panel_uuid:
        agent_db.add_service_node(
            service_id=svc["id"],
            server_id=server_id,
            server_title=server.get("title", ""),
            panel_user_uuid=panel_uuid,
            marzban_username=marzban_username,
        )

    return svc


async def renew_subscription(agent_id: int, service_id: int, extra_days: int, extra_gb: float = 0, override_cost: Optional[int] = None, volume_mode: str = None, time_mode: str = None) -> Optional[Dict[str, Any]]:
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        return None

    if volume_mode is None or time_mode is None:
        admin_volume, admin_time, _ = get_admin_renew_policy()
        volume_mode = volume_mode or admin_volume
        time_mode = time_mode or admin_time

    wholesale = int(svc.get("wholesale_price", 0))
    if extra_days > 0:
        if override_cost is not None:
            cost = int(override_cost)
        else:
            original_days = int(svc.get("days_left", 30)) or 30
            cost = int(wholesale * extra_days / original_days) if original_days > 0 else wholesale
        ok, _ = agent_db.deduct_wallet(agent_id, cost, description=f"\u062a\u0645\u062f\u06cc\u062f \u0633\u0631\u0648\u06cc\u0633: {svc.get('name', '')}", service_id=service_id)
        if not ok:
            return None

    agent_db.renew_service_with_policy(service_id, extra_days, extra_gb, volume_mode, time_mode)
    updated = agent_db.get_service_by_id(service_id)

    # Sync with panel (update usage_limit_GB and package_days)
    if updated:
        sid = int(updated.get("server_id") or 0)
        server = get_server_by_id(sid)
        if server and updated.get("panel_user_uuid"):
            marzban_un = _lookup_marzban_username(service_id, sid)
            new_usage = float(updated.get("usage_limit", 0) or 0)
            new_days = int(updated.get("days_left", 0) or 0)
            patch_data = {"usage_limit_GB": new_usage, "package_days": new_days}
            if str(volume_mode).strip().lower() == "reset":
                patch_data["current_usage_GB"] = 0
            if str(time_mode).strip().lower() == "reset":
                patch_data["start_date"] = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            try:
                await multi_panel.patch_user(
                    server,
                    updated["panel_user_uuid"],
                    patch_data,
                    marzban_username=marzban_un,
                )
            except Exception as e:
                logger.error("panel sync failed on renew svc=%s: %s", service_id, e)

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
    sid = int(svc.get("server_id") or 0)
    marzban_un = _lookup_marzban_username(service_id, sid)
    try:
        await delete_user_on_panel(svc.get("panel_user_uuid", ""), sid, marzban_username=marzban_un)
    except Exception as e:
        logger.error("delete panel API failed svc=%s: %s", service_id, e)
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


async def get_configs(agent_id: int, service_id: int) -> list:
    svc = agent_db.get_service_by_id(service_id)
    if not svc or int(svc.get("agent_id", 0)) != agent_id:
        return []
    sid = int(svc.get("server_id") or 0)
    marzban_un = _lookup_marzban_username(service_id, sid)
    return await get_user_configs(svc.get("panel_user_uuid", ""), sid, marzban_username=marzban_un)


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


def _human_duration(value: float) -> str:
    """تبدیل ثانیه به بازه‌ی انسانی (مثال: «1 ساعت پیش»)."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "چند لحظه پیش"
    if seconds < 0:
        return "چند لحظه پیش"
    if seconds < 60:
        return "چند ثانیه پیش"
    if seconds < 3600:
        return f"{int(seconds // 60)} دقیقه پیش"
    if seconds < 86400:
        return f"{int(seconds // 3600)} ساعت پیش"
    days = seconds / 86400
    if days < 30:
        return f"{int(days)} روز پیش"
    if days < 365:
        return f"{int(days // 30)} ماه پیش"
    return f"{int(days // 365)} سال پیش"


async def get_service_last_online(svc) -> str:
    """وضعیت آخرین اتصال کاربر از پنل:
    «آنلاین» اگر در حال استفاده است، «X پیش» اگر مدتی قبل وصل شده، در غیر این صورت «هرگز»."""
    ONLINE_WINDOW = 15 * 60  # ثانیه
    CLOCK_SKEW = 120
    try:
        sid = int(svc.get("server_id") or 0)
        server = get_server_by_id(sid)
        uuid = str(svc.get("panel_user_uuid") or "").strip()
        if not server or not uuid:
            return "هرگز"
        panel_user = await hiddify_api.get_user_by_uuid(server, uuid)
        raw = (panel_user or {}).get("last_online")
        if not raw:
            return "هرگز"
        last_dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                last_dt = datetime.strptime(str(raw), fmt)
                break
            except ValueError:
                continue
        if last_dt is None:
            return "هرگز"
    except Exception:
        return "هرگز"

    try:
        now = datetime.now()
        seconds = (now - last_dt).total_seconds()
    except Exception:
        return "هرگز"
    if -CLOCK_SKEW <= seconds <= ONLINE_WINDOW:
        return "آنلاین"
    return _human_duration(seconds)
