# CustomerBot/services.py
# Business logic: buy service, renew, get configs
# All actions go through agent_db + Hiddify API

import asyncio
import base64
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

from Shared import agent_db, database, hiddify_api, multi_panel
from Shared import i18n as i18n_mod
from AgentBot.database import get_fixed_plan
from CustomerBot.utils.helpers import escape_markdown

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
    note = agent_db.make_service_note(agent_id)

    payload = {
        "name": service_name or f"cust_{customer_id}_svc",
        "usage_limit_GB": gb,
        "package_days": days,
        "start_date": start_date,
        "current_usage_GB": 0,
        "is_active": True,
        "comment": note,
    }

    try:
        targets = _get_cluster_servers(server_id)
        if not targets:
            targets = [server]
        shared_uuid = user_uuid
        payload["uuid"] = shared_uuid
        created_nodes: List[dict] = []
        panel_user = None
        primary_marzban = ""
        for idx, tgt in enumerate(targets):
            created = None
            last_exc = None
            for attempt in (1, 2):
                try:
                    created = await multi_panel.create_user(tgt, payload)
                    last_exc = None
                    break
                except Exception as e:
                    last_exc = e
                    msg = str(e).lower()
                    is_transient = any(k in msg for k in ("readerror", "connecterror", "timeout", "timed out", "connection", "temporarily"))
                    if idx == 0:
                        if is_transient and attempt == 1:
                            await asyncio.sleep(0.7)
                            continue
                        raise
                    if is_transient and attempt == 1:
                        logger.warning("Cluster node create_user transient retry server=%s attempt=%s: %s", tgt.get("id"), attempt, e)
                        await asyncio.sleep(0.7)
                        continue
                    logger.warning("Cluster node create_user failed server=%s: %s", tgt.get("id"), e)
                    break
            if last_exc is not None and created is None:
                continue
            created_uuid = str(created.get("uuid") or created.get("id") or "").strip()
            if not created_uuid:
                if idx == 0:
                    raise RuntimeError("uuid کاربر ساخته‌شده از پنل دریافت نشد.")
                continue
            created_nodes.append(
                {
                    "server_id": int(tgt.get("id") or 0),
                    "server_title": tgt.get("title") or f"سرور #{tgt.get('id')}",
                    "panel_user_uuid": created_uuid,
                    "panel_user_id": str(created.get("id") or "").strip(),
                    "marzban_username": str(created.get("_marzban_username") or "").strip(),
                    "is_primary": idx == 0,
                }
            )
            if idx == 0:
                panel_user = created
                primary_marzban = str(created.get("_marzban_username") or "").strip()
        if panel_user is None:
            raise RuntimeError("no primary node created")
        panel_uuid = str(panel_user.get("uuid") or user_uuid).strip()
        panel_user_id = str(panel_user.get("id") or "").strip()
        marzban_username = primary_marzban
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
        note=note,
    )

    if svc:
        for item in created_nodes:
            agent_db.add_service_node(
                service_id=svc["id"],
                server_id=int(item.get("server_id") or 0),
                server_title=item.get("server_title") or "",
                panel_user_uuid=str(item.get("panel_user_uuid") or "").strip(),
                panel_user_id=str(item.get("panel_user_id") or "").strip(),
                marzban_username=str(item.get("marzban_username") or "").strip(),
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

    if panel_uuid:
        server = database.get_server_by_id(server_id)
        if server:
            try:
                current_svc = agent_db.get_service_by_id(service_id) or svc
                new_end = str(current_svc.get("end_date", "")).strip()
                if new_end:
                    targets = get_service_panel_targets(current_svc)
                    if not targets:
                        targets = [(server, panel_uuid, "")]

                    # سرور اصلی (primary) اول و جدا؛ اگر در دسترس نبود پول کسر نشود.
                    primary_sid = server_id
                    primary_target = next(
                        (t for t in targets if int(t[0].get("id") or 0) == primary_sid),
                        targets[0],
                    )
                    try:
                        await multi_panel.patch_user(
                            primary_target[0], primary_target[1],
                            {"expire_date": new_end.split(" ")[0]},
                            marzban_username=primary_target[2],
                        )
                    except Exception as e:
                        logger.warning("renew primary patch failed svc=%s: %s", service_id, e)
                        agent_db.charge_wallet(agent_id, cost, description=f"Refund: renew svc #{service_id}")
                        return {"ok": False, "error": f"api_error: {str(e)[:100]}"}

                    # بقیه نودها: best-effort؛ نود down نباید تمدید را خراب کند.
                    failed_nodes: List[str] = []
                    for srv, uuid, marzban_un in targets:
                        if int(srv.get("id") or 0) == primary_sid:
                            continue
                        try:
                            await multi_panel.patch_user(
                                srv, uuid,
                                {"expire_date": new_end.split(" ")[0]},
                                marzban_username=marzban_un,
                            )
                        except Exception as e:
                            failed_nodes.append(str(srv.get("title") or f"سرور #{srv.get('id')}"))
                            logger.warning("renew node patch failed svc=%s server=%s: %s", service_id, srv.get("id"), e)
                    if failed_nodes:
                        logger.warning(
                            "Renew applied on primary but some nodes are pending sync (service_id=%s): %s",
                            service_id,
                            ", ".join(failed_nodes),
                        )
            except Exception as e:
                logger.warning("renew patch failed svc=%s: %s", service_id, e)

    agent_db.renew_service(service_id, extra_days=extra_days)

    return {"ok": True, "wallet_balance": wallet.get("balance", 0)}


async def get_configs(service_id: int) -> Dict[str, Any]:
    """Get subscription configs from all panels (main + X-UI nodes) — aggregated."""
    svc = agent_db.get_service_by_id(service_id)
    if not svc:
        return {"ok": False, "error": "service_not_found"}

    panel_uuid = str(svc.get("panel_user_uuid", "")).strip()
    if not panel_uuid:
        return {"ok": False, "error": "no_uuid"}

    targets = get_service_panel_targets(svc)
    if not targets:
        server_id = int(svc.get("server_id") or 0)
        server = database.get_server_by_id(server_id) if server_id else None
        if not server:
            return {"ok": False, "error": "server_not_found"}
        targets = [(server, panel_uuid, "")]

    aggregated: List[Dict[str, Any]] = []
    seen_links: set = set()
    last_error: str = ""
    for srv, uuid, marzban_un in targets:
        try:
            # برای نود X-UI که uuid mapping ندارد، از uuid سرویس اصلی استفاده کن
            fetch_uuid = str(uuid or panel_uuid).strip()
            if not fetch_uuid:
                continue
            configs = await multi_panel.get_user_configs(
                srv, fetch_uuid, marzban_username=marzban_un,
            )
            for item in configs or []:
                if isinstance(item, dict):
                    link = str(item.get("link") or "").strip()
                    if not link:
                        continue
                    if link in seen_links:
                        continue
                    seen_links.add(link)
                    aggregated.append(item)
                elif isinstance(item, str) and "://" in item:
                    if item.strip() in seen_links:
                        continue
                    seen_links.add(item.strip())
                    aggregated.append({"link": item.strip()})
        except Exception as e:
            last_error = str(e)[:120]
            logger.warning("get_configs node fetch failed svc=%s server=%s: %s", service_id, srv.get("id"), e)
            continue

    if aggregated:
        return {"ok": True, "configs": aggregated}
    if last_error:
        return {"ok": False, "error": last_error[:100]}
    return {"ok": False, "error": "no_configs"}


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
        agent_db.mark_service_seen(service_id)
        return {"ok": True, "usage_current": usage, "service": agent_db.get_service_by_id(service_id)}
    except Exception as e:
        try:
            users = await hiddify_api.list_users(server)
            exists = any(str(u.get("uuid") or u.get("id") or "").strip() == panel_uuid for u in users)
            if not exists:
                agent_db.update_service(service_id, {"is_active": 0, "days_left": 0})
                agent_db.mark_service_missing(service_id)
                agent_db.cleanup_stale_agent_services(7)
                logger.info("disabled stale customer service svc=%s uuid=%s", service_id, panel_uuid)
                return {"ok": False, "error": "panel_user_not_found", "disabled": True}
        except Exception as list_error:
            logger.warning("verify stale service failed svc=%s: %s", service_id, list_error)
        logger.warning("sync_usage failed svc=%s: %s", service_id, e)
        return {"ok": False, "error": str(e)[:100]}


# =====================================================================
# وضعیت اشتراک — مطابق ربات کاربران (UserBot)
# =====================================================================

def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _is_user_missing_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return ("http 404" in text) or ("not found" in text) or (" پیدا نشد" in text)


def _to_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _parse_service_comment(comment: str) -> dict:
    """پارس کامنت سرویس به صورت key:value جدا شده با |"""
    parsed = {}
    raw = (comment or "").strip()
    if not raw:
        return parsed
    for part in raw.split("|"):
        part = part.strip()
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        if k and v:
            parsed[k] = v
    return parsed


def _resolve_live_server_title(svc: dict, default: str = "نامشخص") -> str:
    stored_title = str(svc.get("server_title") or "").strip()
    try:
        server_id = int(svc.get("server_id") or 0)
    except (TypeError, ValueError):
        server_id = 0
    if server_id > 0:
        try:
            server = database.get_server_by_id(server_id)
        except Exception:
            server = None
        if server:
            live_title = str(server.get("title") or "").strip()
            if live_title:
                return live_title
        if stored_title:
            return stored_title
        return f"سرور #{server_id}"
    return stored_title or default


def _is_unlimited_volume(limit_gb: float, br: dict) -> bool:
    if not bool(br.get("renew_unlimited_volume", False)):
        return False
    try:
        threshold = float(br.get("renew_unlimited_volume_from_gb") or 1000)
    except (TypeError, ValueError):
        threshold = 1000.0
    return limit_gb >= threshold


def _is_unlimited_time(days_val: int, br: dict) -> bool:
    if not bool(br.get("renew_unlimited_time", False)):
        return False
    try:
        threshold = int(br.get("renew_unlimited_time_from_days") or 365)
    except (TypeError, ValueError):
        threshold = 365
    return int(days_val) >= threshold


def _renew_br_settings(agent_id: int) -> Dict[str, Any]:
    """تنظیمات قوانین تمدید: اول تنظیمات ادمین (ربات کاربران)، در غیر این صورت تنظیمات محلی نماینده."""
    try:
        from Shared import userbot_db
        s = userbot_db.get_buy_renew_settings()
        if isinstance(s, dict) and s:
            return s
    except Exception:
        pass
    try:
        from CustomerBot.database import get_buy_renew_settings
        return get_buy_renew_settings(agent_id) or {}
    except Exception:
        return {}


def service_is_renewable(svc: Optional[Dict[str, Any]], agent_id: int) -> bool:
    """شرط مجاز بودن تمدید — مطابق ربات کاربران (UserBot):

    - default / fair: بدون محدودیت ورود به تمدید
    - advanced: باید یکی از شروط برقرار باشد:
        1) کمتر از renew_max_days روز تا اتمام اشتراک باقی مانده باشد
        2) حجم باقی‌مانده کمتر از renew_max_remaining_gb گیگابایت باشد
    """
    if not isinstance(svc, dict):
        return False
    br = _renew_br_settings(agent_id)
    try:
        max_days = int(br.get("renew_max_days") or 3)
    except (TypeError, ValueError):
        max_days = 3
    try:
        max_remaining_gb = int(br.get("renew_max_remaining_gb") or 3)
    except (TypeError, ValueError):
        max_remaining_gb = 3

    # days_left نامشخص (NULL/غیرعددی) → اجازه تمدید داده نمی‌شود (fail-closed)
    raw_days = svc.get("days_left")
    try:
        days_left = int(float(raw_days)) if raw_days is not None else None
    except (TypeError, ValueError):
        days_left = None
    days_ok = days_left is not None and days_left < max_days

    usage_limit = float(svc.get("usage_limit") or 0)
    usage_current = float(svc.get("usage_current") or 0)
    usage_ok = False
    if usage_limit > 0:
        usage_ok = (usage_limit - usage_current) < max_remaining_gb

    return days_ok or usage_ok


async def service_is_renewable_live(service_id: int, agent_id: int) -> bool:
    """بررسی مجاز بودن تمدید با داده لحظه‌ای پنل (سینک مصرف/روز قبل از چک).

    اگر سینک پنل ممکن نشود، با همان مقادیر دیتابیس و به‌صورت fail-closed تصمیم گرفته می‌شود.
    """
    try:
        await sync_service_status_from_panels(int(service_id))
    except Exception:
        pass
    svc = agent_db.get_service_by_id(int(service_id))
    ok = service_is_renewable(svc, agent_id)
    if svc:
        logger.info(
            "renew policy svc=%s days_left=%s usage=%s/%sGB → renewable=%s",
            svc.get("id"), svc.get("days_left"),
            svc.get("usage_current"), svc.get("usage_limit"), ok,
        )
    return ok


def renew_not_allowed_text(agent_id: int, lang: str = "fa") -> str:
    if (lang or "fa").strip().lower() != "fa":
        return i18n_mod.t(
            "renew_not_allowed", lang,
            max_days=_renew_limits(agent_id)[0], max_gb=_renew_limits(agent_id)[1],
        )
    br = _renew_br_settings(agent_id)
    try:
        max_days = int(br.get("renew_max_days") or 3)
    except (TypeError, ValueError):
        max_days = 3
    try:
        max_remaining_gb = int(br.get("renew_max_remaining_gb") or 3)
    except (TypeError, ValueError):
        max_remaining_gb = 3
    return (
        "🛑 در حال حاضر شما امکان تمدید اشتراک خود را ندارید.\n"
        f"1- کمتر از {max_days} روز تا اتمام اشتراک شما باقی مانده باشد.\n"
        f"2- حجم باقی مانده اشتراک شما کمتر از {max_remaining_gb} گیگابایت باشد."
    )


def _renew_limits(agent_id: int) -> tuple:
    br = _renew_br_settings(agent_id)
    try:
        max_days = int(br.get("renew_max_days") or 3)
    except (TypeError, ValueError):
        max_days = 3
    try:
        max_remaining_gb = int(br.get("renew_max_remaining_gb") or 3)
    except (TypeError, ValueError):
        max_remaining_gb = 3
    return max_days, max_remaining_gb


def is_customer_service_visible(svc: Optional[Dict[str, Any]]) -> bool:
    """بررسی اینکه سرویس هنوز معتبر و باید در لیست مشتری نمایش داده شود."""
    if not isinstance(svc, dict):
        return False

    panel_uuid = str(svc.get("panel_user_uuid") or "").strip()
    server_id = int(svc.get("server_id") or 0)
    if not panel_uuid or server_id <= 0:
        return False

    try:
        is_active = int(svc.get("is_active") or 0) == 1
    except (TypeError, ValueError):
        is_active = False

    try:
        days_left = int(svc.get("days_left") or 0)
    except (TypeError, ValueError):
        days_left = 0

    if not is_active and days_left <= 0:
        return False

    return is_active or days_left > -30


def build_subscription_status_text(svc: dict, subs_settings: Optional[dict] = None, br: Optional[dict] = None) -> str:
    """متن «📄اطلاعات اشتراک شما» با فرمت دقیق ربات کاربران"""
    service_name = svc.get("name") or "سرویس"
    server_title = _resolve_live_server_title(svc, default="نامشخص")
    usage_current = _to_float(svc.get("usage_current"), 0.0)
    usage_limit = _to_float(svc.get("usage_limit"), 0.0)
    days_left = _to_int(svc.get("days_left"), 0)
    comment_meta = _parse_service_comment(svc.get("comment") or "")
    service_code = str(comment_meta.get("code") or "").strip() or str(svc.get("id") or "—")

    price_raw = svc.get("sale_price")
    if not price_raw:
        price_raw = svc.get("wholesale_price", 0)
    price_toman = max(0, _to_int(price_raw, 0))

    subs = subs_settings or {}
    br = br or {}
    unlimited_volume = _is_unlimited_volume(usage_limit, br)
    unlimited_time = _is_unlimited_time(days_left, br)

    if usage_limit > 0:
        usage_line = f"{usage_current:.1f} از {'نامحدود' if unlimited_volume else f'{usage_limit:.1f} گیگ'}"
    else:
        usage_line = f"{usage_current:.1f} گیگ"

    lines = ["📄اطلاعات اشتراک شما", ""]
    if subs.get("show_username", True):
        lines.append(f"👤نام: {service_name}")
    lines.extend([
        f"📡سرور: {server_title}",
        f"📊میزان استفاده: {usage_line}",
        f"⏳زمان باقی مانده: {'نامحدود' if unlimited_time else f'{days_left} روز'}",
        f"💰قیمت اشتراک: {price_toman:,} تومان",
        f"🔑شناسه: `{service_code}`",
    ])
    return "\n".join(lines)


# ---------- ساخت لینک‌های اشتراک (مثل UserBot) ----------

def _build_user_base_url(server: dict, user_uuid: str) -> Optional[str]:
    if not user_uuid:
        return None
    try:
        from Shared import xui_api
        if xui_api.is_xui_server(server):
            origin = xui_api._public_origin(server)
            sub_path = xui_api._sub_path(server)
            if origin and sub_path:
                return f"{origin.rstrip('/')}{sub_path}{user_uuid}"
    except Exception:
        pass
    panel_url = str(server.get("panel_url") or "").rstrip("/")
    user_proxy = str(server.get("user_proxy_path") or "").strip("/")
    if not panel_url or not user_proxy or not user_uuid:
        return None

    base_url = f"{panel_url}/{user_proxy}/{user_uuid}"
    domains = server.get("domains") or []
    if not domains:
        return base_url

    best_score = -10 ** 9
    display_domain = ""
    for d in domains:
        if isinstance(d, dict):
            raw_domain = (d.get("domain") or d.get("host") or d.get("url") or "").strip()
            title = (d.get("title") or d.get("name") or "").strip().lower()
        else:
            raw_domain = str(d).strip()
            title = ""
        if not raw_domain:
            continue
        low = raw_domain.lower()
        score = 0
        if "user." in low or low.startswith("user"):
            score += 50
        if "sub" in low:
            score += 15
        if "ساب" in title or "user" in title:
            score += 10
        if "dl." in low or low.startswith("dl"):
            score -= 10
        if score > best_score:
            best_score = score
            display_domain = raw_domain

    if not display_domain:
        return base_url
    if not (display_domain.startswith("http://") or display_domain.startswith("https://")):
        display_domain = "https://" + display_domain
    return base_url.replace(panel_url, display_domain.rstrip("/"), 1)


def _build_panel_base_url(server: dict, user_uuid: str) -> Optional[str]:
    if not user_uuid:
        return None
    try:
        from Shared import xui_api
        if xui_api.is_xui_server(server):
            origin = xui_api._public_origin(server)
            sub_path = xui_api._sub_path(server)
            if origin and sub_path:
                return f"{origin.rstrip('/')}{sub_path}{user_uuid}"
    except Exception:
        pass
    panel_url = str(server.get("panel_url") or "").rstrip("/")
    user_proxy = str(server.get("user_proxy_path") or "").strip("/")
    if not panel_url or not user_proxy or not user_uuid:
        return None
    return f"{panel_url}/{user_proxy}/{user_uuid}"


def get_service_node_base_urls(svc: dict) -> List[str]:
    """آدرس base_url سرویس روی همه نودهای نگاشت‌شده + نودهای زیرمجموعه"""
    try:
        from Shared.sub_links import get_service_node_base_urls as _g
        return _g(svc) or []
    except Exception:
        pass
    out: List[str] = []
    seen: set = set()
    try:
        service_id = int(svc.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0
    mappings = agent_db.get_service_nodes(service_id) if service_id > 0 else []
    if mappings:
        active_mappings = [m for m in mappings if int((m or {}).get("is_active") or 0) == 1]
        if active_mappings:
            mappings = active_mappings
        else:
            return []
    try:
        primary_server_id = int(svc.get("server_id") or 0)
    except (TypeError, ValueError):
        primary_server_id = 0
    mappings = sorted(
        mappings,
        key=lambda m: (0 if int((m or {}).get("server_id") or 0) == primary_server_id else 1, int((m or {}).get("server_id") or 0)),
    )
    for m in mappings:
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        server = database.get_server_by_id(sid)
        if not server:
            continue
        for base in (_build_user_base_url(server, uuid), _build_panel_base_url(server, uuid)):
            if not base or base in seen:
                continue
            out.append(base)
            seen.add(base)
    if not out:
        try:
            sid = int(svc.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        server = database.get_server_by_id(sid) if sid else None
        uuid = str(svc.get("panel_user_uuid") or "").strip()
        if server and uuid:
            for base in (_build_user_base_url(server, uuid), _build_panel_base_url(server, uuid)):
                if not base or base in seen:
                    continue
                out.append(base)
                seen.add(base)
    return out


def get_service_panel_targets(svc: dict) -> List[Tuple[dict, str, str]]:
    """لیست (server, uuid, marzban_username) برای همه نودهای سرویس + نودهای زیرمجموعه"""
    try:
        from Shared.sub_links import get_service_panel_targets as _g
        return list(_g(svc) or [])
    except Exception:
        pass
    targets: List[Tuple[dict, str, str]] = []
    seen: set = set()
    try:
        service_id = int(svc.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0
    mappings = agent_db.get_service_nodes(service_id) if service_id > 0 else []
    for m in mappings:
        try:
            sid = int(m.get("server_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        uuid = str(m.get("panel_user_uuid") or "").strip()
        if sid <= 0 or not uuid:
            continue
        srv = database.get_server_by_id(sid)
        if not srv:
            continue
        key = (sid, uuid)
        if key in seen:
            continue
        seen.add(key)
        targets.append((srv, uuid, str(m.get("marzban_username") or "").strip()))
    if targets:
        return targets
    try:
        sid = int(svc.get("server_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    uuid = str(svc.get("panel_user_uuid") or "").strip()
    srv = database.get_server_by_id(sid) if sid > 0 else None
    if srv and uuid:
        targets.append((srv, uuid, ""))
    return targets


# ---------- استخراج کانفیگ‌های مستقیم ----------

def _sanitize_config_text(value: Any) -> str:
    text = str(value or "").strip()
    for ch in ("\ufeff", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c"):
        text = text.replace(ch, "")
    return text.strip()


def _extract_config_link_from_line(value: Any) -> str:
    raw = _sanitize_config_text(value)
    if not raw:
        return ""
    raw = raw.rstrip("'\",;)]}")
    if "://" not in raw:
        return ""
    if re.search(r"\s", raw):
        return ""
    m = re.match(r"(?i)^([a-z][a-z0-9.+\-]{1,24})://", raw)
    if not m:
        return ""
    scheme = str(m.group(1) or "").lower()
    blocked = {"http", "https", "hiddify", "ftp", "file", "mailto", "tg"}
    if scheme in blocked:
        return ""
    if len(raw) < 16:
        return ""
    return raw


def _fetch_remote_lines(url: str) -> List[str]:
    raw_url = str(url or "").strip()
    if not raw_url:
        return []

    def _decode_to_lines(body_text: str) -> List[str]:
        lines = [_sanitize_config_text(ln) for ln in body_text.splitlines() if _sanitize_config_text(ln)]
        if lines and any("://" in ln for ln in lines):
            return lines
        compact = re.sub(r"\s+", "", body_text or "")
        candidates = [body_text.strip(), compact]
        for cand in candidates:
            cand = str(cand or "").strip()
            if not cand:
                continue
            for decoder in (base64.b64decode, base64.urlsafe_b64decode):
                try:
                    padded = cand + ("=" * ((4 - len(cand) % 4) % 4))
                    decoded = decoder(padded).decode("utf-8", errors="ignore")
                    decoded_lines = [_sanitize_config_text(ln) for ln in decoded.splitlines() if _sanitize_config_text(ln)]
                    if decoded_lines:
                        return decoded_lines
                except Exception:
                    continue
        return lines

    urls_to_try = [raw_url]
    low = raw_url.lower()
    if "/all.txt" in low and "asn=" not in low:
        sep = "&" if "?" in raw_url else "?"
        urls_to_try.append(f"{raw_url}{sep}asn=unknown")

    user_agents = [
        "HiddifyNext/1.0",
        "ClashMetaForAndroid/2.11.5",
        "v2rayN/6.45",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    base_headers = {
        "Accept": "text/plain,*/*;q=0.9",
        "Accept-Language": "en-US,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    for target in urls_to_try:
        for ua in user_agents:
            try:
                req = Request(target, headers={**base_headers, "User-Agent": ua})
                with urlopen(req, timeout=15) as resp:
                    raw = resp.read()
                body = raw.decode("utf-8", errors="ignore")
                parsed = _decode_to_lines(body)
                if parsed:
                    return parsed
            except HTTPError:
                continue
            except Exception:
                continue
    return []


def collect_all_direct_configs_for_service(svc: dict) -> List[str]:
    """جمع‌آوری کانفیگ‌های مستقیم — اکنون directly به Shared واگذار می‌شود تا per-node fallback (HTTP→API) برای X-UI اعمال شود."""
    try:
        from Shared.sub_links import collect_all_direct_configs_for_service as _shared_collect
        return _shared_collect(svc)
    except Exception as e:
        logger.warning("CustomerBot collect fallback to local impl: %s", e)
        def _is_internal_status(raw: str) -> bool:
            low = raw.lower()
            return ("fake_ip_for_sub_link" in low) or ("status.hiddify-sellbot.invalid" in low) or ("hiddify-sellbot.invalid" in low and "fake_ip" in low)
        out: List[str] = []
        seen_links: set = set()
        for base_url in get_service_node_base_urls(svc):
            seen_lines: set = set()
            is_xui = "/sub/" in str(base_url or "")
            if is_xui:
                suffixes = ("", "?base64=1")
            else:
                suffixes = ("all.txt", "all.txt?base64=1")
            for suffix in suffixes:
                url = base_url if is_xui and not suffix else (f"{base_url}{suffix}" if is_xui else f"{base_url}/{suffix}")
                lines = _fetch_remote_lines(url)
                for ln in lines:
                    raw = _sanitize_config_text(ln)
                    if not raw or raw in seen_lines:
                        continue
                    seen_lines.add(raw)
                    link = _extract_config_link_from_line(raw)
                    if not link or link in seen_links:
                        continue
                    if _is_internal_status(link):
                        continue
                    seen_links.add(link)
                    out.append(link)
        return out


async def collect_all_direct_configs_from_api(svc: dict) -> List[str]:
    """پشتیبان: دریافت کانفیگ‌ها از API پنل برای همه نودها"""
    out: List[str] = []
    seen: set = set()
    for srv, uuid, marzban_un in get_service_panel_targets(svc):
        try:
            configs = await multi_panel.get_user_configs(srv, uuid, marzban_username=marzban_un)
            for item in configs or []:
                link = str(item.get("link") or "").strip()
                if link and link not in seen:
                    seen.add(link)
                    out.append(link)
        except Exception as e:
            logger.warning("API config fallback failed svc=%s node=%s: %s", svc.get("id"), uuid[:8], e)
    return out


# ---------- لینک اشتراک هوشمند (managed sub link) ----------

def _resolve_sub_service_base_url(svc: Optional[dict] = None) -> str:
    try:
        from Shared import userbot_db
        custom_base = str(userbot_db.get_managed_sub_base_url() or "").strip().rstrip("/")
    except Exception:
        custom_base = ""
    if custom_base:
        return custom_base

    env_base = str(os.getenv("SUB_SERVICE_BASE_URL", "") or "").strip().rstrip("/")
    if env_base:
        return env_base

    explicit = str(os.getenv("SUB_SERVER_PUBLIC_HOST", "") or "").strip().rstrip("/")
    if explicit:
        try:
            parsed = urlparse(explicit if "://" in explicit else f"//{explicit}")
            host = (parsed.hostname or "").strip()
            scheme = (parsed.scheme or os.getenv("SUB_SERVER_PUBLIC_SCHEME", "https") or "https").strip().lower()
            port = parsed.port if parsed.port is not None else int(os.getenv("SUB_SERVER_PUBLIC_PORT", "8787") or "8787")
            if host:
                default_port = (scheme == "https" and int(port) == 443) or (scheme == "http" and int(port) == 80)
                if default_port:
                    return f"{scheme}://{host}"
                return f"{scheme}://{host}:{int(port)}"
        except Exception:
            pass

    if svc:
        base_urls = get_service_node_base_urls(svc)
        if base_urls:
            try:
                p = urlparse(base_urls[0])
                host = (p.hostname or "").strip()
                if host:
                    scheme = (os.getenv("SUB_SERVER_PUBLIC_SCHEME", "https") or "https").strip().lower()
                    port = int(os.getenv("SUB_SERVER_PUBLIC_PORT", "8787") or "8787")
                    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
                    if default_port:
                        return f"{scheme}://{host}"
                    return f"{scheme}://{host}:{int(port)}"
            except Exception:
                pass
    return ""


def get_or_create_bot_sub_links(svc: dict) -> Tuple[str, str]:
    """(لینک اشتراک هوشمند، لینک b64) مشابه AdminBot/نمایندگی.

    برای سرویس‌های نمایندگی/مشتری token را به فرم ``panel-srv-{server_id}``
    می‌سازد تا ساب‌سرور بتواند بدون نیاز به ردیف محلی سرویس، کانفیگ‌ها را
    مستقیم از پنل سرور با uuid تحویل دهد؛ دقیقاً همان فرمتی که Shared.sub_links
    در نمایندگی و AdminBot تولید می‌کنند تا هر دو لینک یکسان و قابل استفاده باشند.
    """
    base = _resolve_sub_service_base_url(svc)
    if not base:
        base_urls = get_service_node_base_urls(svc)
        if base_urls:
            base = base_urls[0].rstrip("/")
    if not base:
        return "", ""
    service_uuid = str(svc.get("panel_user_uuid") or "").strip()
    try:
        server_id = int(svc.get("server_id") or 0)
    except (TypeError, ValueError):
        server_id = 0
    if service_uuid and server_id > 0:
        token = f"panel-srv-{server_id}"
        return (
            f"{base}/sub/{token}/{service_uuid}/all.txt",
            f"{base}/sub/{token}/{service_uuid}/all.txt?base64=1",
        )
    if service_uuid:
        return (
            f"{base}/sub/{service_uuid}/all.txt",
            f"{base}/sub/{service_uuid}/all.txt?base64=1",
        )
    token = str(svc.get("id") or "").strip()
    return f"{base}/sub/{token}/all.txt", f"{base}/sub/{token}/all.txt?base64=1"


# ---------- بروزرسانی اطلاعات از همه پنل‌ها ----------

async def sync_service_status_from_panels(service_id: int) -> Dict[str, Any]:
    """سینک مصرف و روز باقیمانده از همه نودها + سرور اصلی (مثل UserBot)"""
    svc = agent_db.get_service_by_id(service_id)
    if not svc:
        return {"ok": False, "error": "service_not_found"}
    targets = get_service_panel_targets(svc)
    if not targets:
        return {"ok": False, "error": "no_targets"}

    total_usage = 0.0
    max_limit = 0.0
    min_days_left: Optional[int] = None
    found_any = False
    missing_any = False
    for srv, uuid, marzban_un in targets:
        try:
            user_data = await hiddify_api.get_user_by_uuid(srv, uuid)
            usage = _to_float(user_data.get("current_usage_GB"), 0.0)
            total_usage += usage
            found_any = True
            limit = _to_float(user_data.get("usage_limit_GB"), 0.0)
            if limit > max_limit:
                max_limit = limit
            derived_days = _extract_days_left(user_data, None)
            if derived_days is not None:
                min_days_left = derived_days if min_days_left is None else min(min_days_left, derived_days)
        except Exception as e:
            if _is_user_missing_error(e):
                missing_any = True
            logger.warning("sync svc=%s node=%s failed: %s", service_id, uuid[:8], e)

    if found_any:
        try:
            agent_db.mark_service_seen(service_id)
        except Exception:
            pass
    elif missing_any:
        try:
            agent_db.mark_service_missing(service_id)
            agent_db.cleanup_stale_agent_services(7)
        except Exception:
            pass
        return {"ok": False, "error": "panel_user_not_found"}

    if not found_any:
        return {"ok": False, "error": "no_reachable_panel"}

    updates = {"usage_current": total_usage, "is_active": 1}
    if max_limit > 0:
        updates["usage_limit"] = max_limit
    if min_days_left is not None:
        updates["days_left"] = int(min_days_left)
    agent_db.update_service(service_id, updates)
    return {"ok": True, "service": agent_db.get_service_by_id(service_id)}


# ---------- تغییر نام روی همه پنل‌ها ----------

async def rename_service_on_panels(svc: dict, new_name: str) -> Tuple[bool, str]:
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
            errors.append(f"{srv.get('title') or srv.get('id')}: {str(e)[:60]}")
    if ok_count == 0:
        preview = "\n".join(errors[:3])
        extra = f"\n... و {len(errors) - 3} خطای دیگر" if len(errors) > 3 else ""
        return False, "❌ تغییر نام روی همه سرورها انجام نشد.\n" + preview + extra
    ok = agent_db.update_service(int(svc.get("id") or 0), {"name": new_name})
    if not ok:
        return False, "❌ بروزرسانی نام در دیتابیس انجام نشد."
    margin = ""
    if errors:
        margin = ("\n\n⚠️ نام روی همه نودها اعمال شد اما " + str(len(errors)) +
                  " نود در دسترس نبود (تا برگشتنشان بعداً همگام می‌شود):\n- " +
                  "\n- ".join(errors[:3]))
    return True, "✅ نام اشتراک با موفقیت بروزرسانی شد." + margin


# ---------- تغییر لینک اشتراک (بازسازی UUID روی همه پنل‌ها) ----------

async def regenerate_service_uuid(svc: dict) -> Tuple[bool, str, Optional[str]]:
    service_id = int(svc.get("id") or 0)
    if service_id <= 0:
        return False, "❌ سرویس نامعتبر است.", None
    current_uuid = str(svc.get("panel_user_uuid") or "").strip()
    if not current_uuid:
        return False, "❌ UUID فعلی اشتراک تعیین نشده است.", None
    targets = get_service_panel_targets(svc)
    if not targets:
        return False, "❌ مسیرهای پنل این اشتراک پیدا نشد.", None

    desired_uuid = str(uuid4())
    final_uuid: Optional[str] = None
    updated_targets: List[Tuple[dict, str, str]] = []  # (srv, old_uuid, new_uuid)

    for srv, old_uuid, _marzban_un in targets:
        if not old_uuid:
            continue
        try:
            patched = await hiddify_api.patch_user(srv, old_uuid, {"uuid": desired_uuid})
        except Exception as e:
            rollback_ok = True
            for srv2, old_uuid2, new_uuid2 in updated_targets:
                try:
                    await hiddify_api.patch_user(srv2, new_uuid2, {"uuid": old_uuid2})
                except Exception:
                    rollback_ok = False
            where = str(srv.get("title") or f"سرور #{srv.get('id')}")
            extra = "" if rollback_ok else "\n⚠️ برگرداندن برخی نودها به UUID قبلی ممکن نشد؛ لطفاً با پشتیبانی هماهنگ کنید."
            return False, f"❌ بازسازی UUID روی «{where}» انجام نشد.\nجزئیات: {str(e)[:120]}{extra}", None

        returned_uuid = str(patched.get("uuid") or patched.get("id") or "").strip()
        if not returned_uuid:
            returned_uuid = desired_uuid
        if final_uuid is None:
            final_uuid = returned_uuid
        elif returned_uuid != final_uuid:
            for srv2, old_uuid2, new_uuid2 in updated_targets:
                try:
                    await hiddify_api.patch_user(srv2, new_uuid2, {"uuid": old_uuid2})
                except Exception:
                    pass
            return False, "❌ UUID جدید روی همه سرورها همگن نشد. لطفاً مجدداً تلاش کنید.", None
        updated_targets.append((srv, old_uuid, returned_uuid))

    if not final_uuid:
        return False, "❌ UUID جدید تهیه نشد.", None

    agent_db.update_service(service_id, {"panel_user_uuid": final_uuid})
    for srv, old_uuid, new_uuid in updated_targets:
        try:
            agent_db.update_service_node_uuid(service_id, int(srv.get("id") or 0), old_uuid, new_uuid)
        except Exception:
            pass
    return True, "✅ لینک اشتراک با موفقیت تغییر یافت.", final_uuid
