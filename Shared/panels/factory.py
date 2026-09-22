from __future__ import annotations

from .base import PanelError


def build_optional_panel(panel_type: str, config):
    """Lazy factory: optional panels never import during normal Hiddify startup."""
    key = panel_type.strip().lower()
    if key == "xnet":
        from .xnet.adapter import XNetAdapter
        return XNetAdapter(config)
    raise PanelError(f"Unsupported optional panel: {panel_type}")
