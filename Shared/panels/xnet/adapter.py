from __future__ import annotations

from typing import Any, Mapping

from ..base import PanelAdapter, PanelCapabilities, PanelUnavailable


class XNetAdapter(PanelAdapter):
    """Isolated X-NET adapter skeleton.

    Remote API calls are deliberately not implemented until a test X-NET panel
    is available and its current API contract is verified end-to-end.
    """

    panel_type = "xnet"
    capabilities = PanelCapabilities()

    def _not_ready(self):
        raise PanelUnavailable(
            "X-NET integration is experimental and has no production API binding yet"
        )

    def healthcheck(self) -> bool:
        return False

    def create_user(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self._not_ready()

    def update_user(self, user_id: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self._not_ready()

    def delete_user(self, user_id: str) -> bool:
        self._not_ready()

    def get_user(self, user_id: str) -> Mapping[str, Any]:
        self._not_ready()
