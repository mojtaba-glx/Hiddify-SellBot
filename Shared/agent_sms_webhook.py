"""
Shared/agent_sms_webhook.py
===========================
Per-agent SMS webhook configuration and URL building (shared logic).

Every agent gets their own random secret, enable flag and webhook URL —
stored per agent_id in the agent database (agent_sms_webhook table). The
central admin webhook (.env SMS_WEBHOOK_SECRET / SMS_WEBHOOK_ENABLED) is
completely separate: agent UI code must never read or write those keys.

Webhook routes (handled by Shared/sub_http_server.py):
- central (admin):  POST /payment/sms-webhook   (or /sms-webhook)
- per agent:        POST /payment/agent/{agent_id}/sms-webhook

Auth rule: the agent_id inside the URL only *routes* the request; the
provided secret must belong to that same agent. A valid agent secret can
never authorize the central route, another agent's route, or a wallet
self-top-up.
"""

from __future__ import annotations

import secrets as _secrets
from typing import Any, Dict, Optional

CENTRAL_WEBHOOK_PATH = "/payment/sms-webhook"
CENTRAL_WEBHOOK_PATH_ALT = "/sms-webhook"
AGENT_WEBHOOK_PATH_TEMPLATE = "/payment/agent/{agent_id}/sms-webhook"

SECRET_BYTES = 32  # 64 hex chars


def agent_webhook_path(agent_id: int) -> str:
    """Build the per-agent webhook route for the shared HTTP server."""
    return AGENT_WEBHOOK_PATH_TEMPLATE.format(agent_id=int(agent_id or 0))


def agent_webhook_url(agent_id: int, base_url: str) -> str:
    """Build the full per-agent webhook URL from a managed domain base URL."""
    base = str(base_url or "").strip().rstrip("/")
    path = agent_webhook_path(agent_id)
    if not base:
        return path
    return f"{base}{path}"


def _agentbot_db():
    # Imported lazily to avoid circular imports; the agent database module
    # owns the agent_sms_webhook table.
    from AgentBot import database as _adb
    return _adb


def get_agent_sms_settings(agent_id: int) -> Dict[str, Any]:
    """Read one agent's SMS webhook settings: {enabled, secret, webhook_path}."""
    return _agentbot_db().get_sms_webhook_settings(int(agent_id or 0))


def ensure_agent_sms_settings(agent_id: int) -> Dict[str, Any]:
    """Return the agent's settings, creating a strong random secret on first
    use (migration-compatible: safe to call repeatedly)."""
    return _agentbot_db().ensure_sms_webhook_settings(int(agent_id or 0))


def set_agent_sms_enabled(agent_id: int, enabled: bool) -> Dict[str, Any]:
    """Toggle one agent's SMS auto-approval. Turning it on for the first
    time provisions the agent's own secret (never the central one)."""
    return _agentbot_db().set_sms_webhook_enabled(int(agent_id or 0), bool(enabled))


def regenerate_agent_secret(agent_id: int) -> Dict[str, Any]:
    """Rotate one agent's personal secret. Only that agent's route changes."""
    return _agentbot_db().rotate_sms_webhook_secret(int(agent_id or 0))


def agent_owns_secret(agent_id: int, provided_secret: str) -> bool:
    """Constant-time check that `provided_secret` is the secret of this
    exact agent and that the agent exists and is active."""
    return _agentbot_db().agent_sms_secret_matches(int(agent_id or 0), str(provided_secret or ""))


def is_agent_sms_enabled(agent_id: int) -> bool:
    """Whether SMS auto-approval is enabled for this exact agent."""
    return _agentbot_db().agent_sms_enabled(int(agent_id or 0))


def is_agent_usable(agent_id: int) -> bool:
    """An agent must exist and be active to use the webhook at all."""
    return _agentbot_db().agent_exists_and_active(int(agent_id or 0))


def new_secret() -> str:
    return _secrets.token_hex(SECRET_BYTES)
