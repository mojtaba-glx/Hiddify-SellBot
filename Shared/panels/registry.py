from __future__ import annotations

from typing import Dict, Type

from .base import PanelAdapter, PanelError

_ADAPTERS: Dict[str, Type[PanelAdapter]] = {}


def register_adapter(panel_type: str, adapter_cls: Type[PanelAdapter]) -> None:
    key = panel_type.strip().lower()
    if not key:
        raise ValueError("panel_type is required")
    _ADAPTERS[key] = adapter_cls


def get_adapter(panel_type: str, config) -> PanelAdapter:
    key = panel_type.strip().lower()
    adapter_cls = _ADAPTERS.get(key)
    if adapter_cls is None:
        raise PanelError(f"Unsupported panel type: {panel_type}")
    return adapter_cls(config)


def registered_panel_types():
    return tuple(sorted(_ADAPTERS))
