from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping


class PanelError(RuntimeError):
    """Base error for an optional panel integration."""


class PanelUnavailable(PanelError):
    """Raised when an optional panel is disabled or unreachable."""


@dataclass(frozen=True)
class PanelCapabilities:
    create_user: bool = False
    update_user: bool = False
    delete_user: bool = False
    subscription: bool = False
    traffic_limit: bool = False
    expiry: bool = False
    concurrent_limit: bool = False


class PanelAdapter(ABC):
    """Small stable boundary between SellBot and any panel implementation."""

    panel_type: str = "unknown"
    capabilities = PanelCapabilities()

    def __init__(self, config: Mapping[str, Any]):
        self.config = dict(config)

    @abstractmethod
    def healthcheck(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def create_user(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update_user(self, user_id: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def delete_user(self, user_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_user(self, user_id: str) -> Mapping[str, Any]:
        raise NotImplementedError
