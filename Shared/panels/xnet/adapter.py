from __future__ import annotations

from typing import Any, Mapping

from ..base import PanelAdapter, PanelCapabilities, PanelUnavailable
from .client import XNetClient
from .config import XNetConfig


class XNetAdapter(PanelAdapter):
    """X-NET adapter with a verified transport and intentionally unbound routes.

    X-NET publicly documents a Bearer-token API and an in-panel API playground,
    but does not publish the concrete route/schema table in its public repo.
    Route binding therefore waits for the installed panel's official docs.
    """

    panel_type = "xnet"
    capabilities = PanelCapabilities()

    def __init__(self, config: Mapping[str, Any]):
        super().__init__(config)
        cfg = XNetConfig(
            enabled=bool(config.get("enabled", False)),
            base_url=str(config.get("base_url", "")),
            token=str(config.get("token", "")),
            timeout=float(config.get("timeout", 10.0)),
            verify_tls=bool(config.get("verify_tls", True)),
        )
        cfg.validate()
        self.settings = cfg
        self.client = (
            XNetClient(cfg.base_url, cfg.token, cfg.timeout, cfg.verify_tls)
            if cfg.enabled else None
        )

    def _unbound(self):
        if not self.settings.enabled:
            raise PanelUnavailable("X-NET integration is disabled")
        raise PanelUnavailable(
            "X-NET transport is ready; bind official API routes from the installed "
            "panel API playground before enabling production operations"
        )

    def healthcheck(self) -> bool:
        # We deliberately do not guess a health endpoint.
        self._unbound()

    def create_user(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self._unbound()

    def update_user(self, user_id: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self._unbound()

    def delete_user(self, user_id: str) -> bool:
        self._unbound()

    def get_user(self, user_id: str) -> Mapping[str, Any]:
        self._unbound()
