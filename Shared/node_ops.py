import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from Shared import database, hiddify_api

logger = logging.getLogger(__name__)


class NodeOpsError(RuntimeError):
    pass


def _slugify(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"[^a-z0-9\-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "node"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _build_cloud_init(domain: str, admin_uuid: str) -> str:
    """
    Bootstrap script.
    باید template را در env تنظیم کنید تا نصب واقعی انجام شود.
    """
    template = _env("NODE_INSTALL_COMMAND_TEMPLATE", "")
    if not template:
        raise NodeOpsError(
            "NODE_INSTALL_COMMAND_TEMPLATE تنظیم نشده است. "
            "برای نصب خودکار هیدیفای این env باید ست شود."
        )

    cmd = template.format(
        domain=domain,
        admin_uuid=admin_uuid,
    )
    return (
        "#cloud-config\n"
        "package_update: true\n"
        "runcmd:\n"
        "  - [bash, -lc, \"" + cmd.replace("\"", "\\\"") + "\"]\n"
    )


async def _hetzner_create_server(
    *,
    name: str,
    cloud_init: str,
) -> Dict[str, Any]:
    token = _env("HETZNER_API_TOKEN")
    if not token:
        raise NodeOpsError("HETZNER_API_TOKEN تنظیم نشده است.")

    server_type = _env("HETZNER_SERVER_TYPE", "cx22")
    location = _env("HETZNER_LOCATION", "fsn1")
    image = _env("HETZNER_IMAGE", "ubuntu-22.04")
    ssh_keys_raw = _env("HETZNER_SSH_KEYS", "")
    ssh_keys: List[str] = [x.strip() for x in ssh_keys_raw.split(",") if x.strip()]

    payload: Dict[str, Any] = {
        "name": name,
        "server_type": server_type,
        "location": location,
        "image": image,
        "user_data": cloud_init,
        "labels": {"managed_by": "hiddify-sellbot"},
    }
    if ssh_keys:
        payload["ssh_keys"] = ssh_keys

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=40.0) as client:
        resp = await client.post("https://api.hetzner.cloud/v1/servers", json=payload, headers=headers)
    if resp.status_code >= 300:
        raise NodeOpsError(f"Hetzner create server failed: HTTP {resp.status_code} {resp.text}")

    data = resp.json()
    server = data.get("server") or {}
    public_net = server.get("public_net") or {}
    ipv4_block = public_net.get("ipv4") or {}
    ip = ipv4_block.get("ip")
    if not ip:
        raise NodeOpsError("Hetzner server created but IPv4 not found.")
    return {
        "provider": "hetzner",
        "provider_server_id": str(server.get("id") or ""),
        "ip": str(ip),
        "raw": data,
    }


async def _cloudflare_upsert_a_record(*, fqdn: str, ip: str) -> Dict[str, Any]:
    token = _env("CLOUDFLARE_API_TOKEN")
    zone_id = _env("CLOUDFLARE_ZONE_ID")
    if not token or not zone_id:
        raise NodeOpsError("CLOUDFLARE_API_TOKEN یا CLOUDFLARE_ZONE_ID تنظیم نشده است.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    proxied = _env("CLOUDFLARE_PROXIED", "false").lower() in {"1", "true", "yes", "on"}
    ttl = int(_env("CLOUDFLARE_TTL", "120") or "120")

    async with httpx.AsyncClient(timeout=30.0) as client:
        find_resp = await client.get(
            f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
            params={"type": "A", "name": fqdn},
            headers=headers,
        )
        if find_resp.status_code >= 300:
            raise NodeOpsError(f"Cloudflare find record failed: HTTP {find_resp.status_code} {find_resp.text}")
        find_data = find_resp.json()
        if not find_data.get("success"):
            raise NodeOpsError(f"Cloudflare find record error: {find_data}")

        records = find_data.get("result") or []
        payload = {"type": "A", "name": fqdn, "content": ip, "ttl": ttl, "proxied": proxied}
        if records:
            rec_id = records[0].get("id")
            save_resp = await client.put(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{rec_id}",
                json=payload,
                headers=headers,
            )
        else:
            save_resp = await client.post(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
                json=payload,
                headers=headers,
            )

    if save_resp.status_code >= 300:
        raise NodeOpsError(f"Cloudflare upsert record failed: HTTP {save_resp.status_code} {save_resp.text}")
    save_data = save_resp.json()
    if not save_data.get("success"):
        raise NodeOpsError(f"Cloudflare upsert record error: {save_data}")
    return save_data


async def provision_and_register_node(
    *,
    parent_server_id: int,
    node_title: str,
    users_limit: int,
) -> Dict[str, Any]:
    """
    ساخت VPS -> DNS -> ثبت به عنوان سرور جدید -> اتصال به لیست نودهای سرور مبدا
    """
    parent = database.get_server_by_id(parent_server_id)
    if not parent:
        raise NodeOpsError("سرور مبدا پیدا نشد.")

    provider = _env("NODE_PROVIDER", "hetzner").lower()
    base_domain = _env("NODE_BASE_DOMAIN")
    if not base_domain:
        raise NodeOpsError("NODE_BASE_DOMAIN تنظیم نشده است.")

    slug = _slugify(node_title)
    fqdn = f"{slug}.{base_domain}"
    admin_uuid = str(uuid.uuid4())
    cloud_init = _build_cloud_init(domain=fqdn, admin_uuid=admin_uuid)

    if provider != "hetzner":
        raise NodeOpsError(f"Provider پشتیبانی‌نشده: {provider}")

    infra = await _hetzner_create_server(name=f"sellbot-{slug}", cloud_init=cloud_init)
    await _cloudflare_upsert_a_record(fqdn=fqdn, ip=infra["ip"])

    panel_url = f"https://{fqdn}"
    admin_proxy = _env("NODE_DEFAULT_ADMIN_PROXY", "admin")
    user_proxy = _env("NODE_DEFAULT_USER_PROXY", "sub")

    new_server_payload = {
        "title": node_title,
        "panel_url": panel_url,
        "admin_proxy_path": admin_proxy,
        "admin_uuid": admin_uuid,
        "user_proxy_path": user_proxy,
        "users_limit": int(users_limit),
        "priority": 0,
        "version": 11,
        "users": [],
        "plans": [],
        "domains": [{"id": 1, "title": fqdn, "domain": fqdn}],
        "nodes": [],
        "infra": {
            "provider": infra["provider"],
            "provider_server_id": infra["provider_server_id"],
            "ip": infra["ip"],
            "status": "provisioned",
            "created_at": _now_str(),
            "fqdn": fqdn,
        },
    }

    created_server = database.add_server(new_server_payload)
    child_server_id = int(created_server.get("id") or 0)

    parent_nodes = list(parent.get("nodes") or [])
    next_id = max([int((n or {}).get("id") or 0) for n in parent_nodes] or [0]) + 1
    parent_nodes.append(
        {
            "id": next_id,
            "title": node_title,
            "host": fqdn,
            "target_server_id": child_server_id,
            "provider": infra["provider"],
            "provider_server_id": infra["provider_server_id"],
            "status": "provisioned",
            "last_check": None,
            "fail_count": 0,
            "last_recovery": None,
        }
    )
    database.update_server(parent_server_id, {"nodes": parent_nodes})

    return {
        "parent_server_id": parent_server_id,
        "child_server_id": child_server_id,
        "title": node_title,
        "fqdn": fqdn,
        "ip": infra["ip"],
        "provider": infra["provider"],
        "provider_server_id": infra["provider_server_id"],
        "panel_url": panel_url,
        "admin_uuid": admin_uuid,
        "admin_proxy_path": admin_proxy,
        "user_proxy_path": user_proxy,
    }


async def _hetzner_reboot(provider_server_id: str) -> None:
    token = _env("HETZNER_API_TOKEN")
    if not token:
        raise NodeOpsError("HETZNER_API_TOKEN تنظیم نشده است.")
    if not provider_server_id:
        raise NodeOpsError("provider_server_id خالی است.")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://api.hetzner.cloud/v1/servers/{provider_server_id}/actions/reboot",
            headers=headers,
        )
    if resp.status_code >= 300:
        raise NodeOpsError(f"Hetzner reboot failed: HTTP {resp.status_code} {resp.text}")


async def monitor_and_recover_nodes() -> Dict[str, int]:
    """
    مانیتورینگ نودها و recovery خودکار نود Down.
    """
    summary = {"nodes_scanned": 0, "nodes_up": 0, "nodes_down": 0, "recoveries": 0, "errors": 0}
    auto_recover = _env("NODE_AUTO_RECOVER_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
    recover_fail_threshold = int(_env("NODE_RECOVER_FAIL_THRESHOLD", "3") or "3")

    for parent in database.get_servers():
        sid = int(parent.get("id") or 0)
        if sid <= 0:
            continue
        nodes = list(parent.get("nodes") or [])
        changed = False

        for node in nodes:
            if not isinstance(node, dict):
                continue
            target_sid = int(node.get("target_server_id") or 0)
            if target_sid <= 0:
                continue
            summary["nodes_scanned"] += 1

            child = database.get_server_by_id(target_sid)
            if not child:
                node["status"] = "missing_target"
                node["last_check"] = _now_str()
                node["fail_count"] = int(node.get("fail_count") or 0) + 1
                changed = True
                summary["nodes_down"] += 1
                continue

            try:
                await hiddify_api.list_users(child)
                node["status"] = "up"
                node["last_check"] = _now_str()
                node["fail_count"] = 0
                changed = True
                summary["nodes_up"] += 1
            except Exception:
                node["status"] = "down"
                node["last_check"] = _now_str()
                node["fail_count"] = int(node.get("fail_count") or 0) + 1
                changed = True
                summary["nodes_down"] += 1

                if auto_recover and int(node.get("fail_count") or 0) >= recover_fail_threshold:
                    last_recovery = node.get("last_recovery", "")
                    if last_recovery:
                        try:
                            last_recovery_dt = datetime.strptime(last_recovery, "%Y-%m-%d %H:%M:%S")
                            if (datetime.now() - last_recovery_dt).total_seconds() < 300:
                                continue
                        except (ValueError, TypeError):
                            pass
                    try:
                        provider = (node.get("provider") or (child.get("infra") or {}).get("provider") or "").lower()
                        provider_server_id = str(
                            node.get("provider_server_id")
                            or (child.get("infra") or {}).get("provider_server_id")
                            or ""
                        )
                        if provider == "hetzner" and provider_server_id:
                            await _hetzner_reboot(provider_server_id)
                            node["last_recovery"] = _now_str()
                            node["fail_count"] = 0
                            summary["recoveries"] += 1
                    except Exception as e:
                        summary["errors"] += 1
                        logger.warning("Node auto-recovery failed (parent=%s target=%s): %s", sid, target_sid, e)

        if changed:
            database.update_server(sid, {"nodes": nodes})

    return summary
