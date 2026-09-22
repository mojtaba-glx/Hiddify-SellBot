"""Panel integrations.

Each panel implementation is isolated behind the adapter contract. Importing this
package must never initialize a remote panel or change existing Hiddify behavior.
"""
from .base import PanelAdapter, PanelCapabilities, PanelError, PanelUnavailable

__all__ = ["PanelAdapter", "PanelCapabilities", "PanelError", "PanelUnavailable"]
